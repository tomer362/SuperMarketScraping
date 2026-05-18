#!/usr/bin/env python3.13
"""
chp_main.py
===========
Standalone CLI for the chp.co.il scraper.

chp.co.il is an Israeli price-comparison aggregator. This tool searches
for products by name or barcode and reports parsed comparison-table prices.

Usage
-----
    python chp_main.py --query "חלב תנובה"
    python chp_main.py --barcode 7290004131074
    python chp_main.py --query "גבינה לבנה" --city "ירושלים"
    python chp_main.py --query "ביצים" --city "חיפה" --max-products 50
    python chp_main.py --query "קוטג" --output results.json
    python chp_main.py --list-cities

Output
------
Without --output: prints a human-readable price table to stdout.
With    --output: writes a JSON file with full UnifiedProduct records.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import List, Optional

import aiohttp

# Ensure the project root is on sys.path when running directly
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/../..")

from scrapers.chp.chp import (
    CHAIN,
    CityInfo,
    ChpProduct,
    OnlineStorePrice,
    _new_u,
    get_city,
    make_ssl_context,
    scrape,
    search_cities,
    search_products,
    update_cities,
)
from scrapers.common import ScrapeFilter


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------


class _HelpOnErrorParser(argparse.ArgumentParser):
    """ArgumentParser that prints the full help message before any error."""

    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_help(sys.stderr)
        self.exit(2, f"\nerror: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    p = _HelpOnErrorParser(
        prog="chp_main.py",
        description="Scrape product prices from chp.co.il (Israeli price comparison)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # Query — mutually exclusive: --query / --barcode / --all
    qgroup = p.add_mutually_exclusive_group()
    qgroup.add_argument(
        "--query",
        "-q",
        metavar="TERM",
        help="Hebrew product name to search (e.g. 'חלב תנובה'). "
        "All products returned by the server for this term are fetched "
        "(no client-side word filtering).",
    )
    qgroup.add_argument(
        "--barcode",
        "-b",
        metavar="BARCODE",
        help="EAN barcode to look up (e.g. 7290004131074)",
    )
    qgroup.add_argument(
        "--all",
        action="store_true",
        dest="browse_all",
        help=(
            "Enumerate ALL products from chp.co.il via the sitemap (~100K+ products). "
            "Use --max-products N to cap results; 0 means no limit. "
            "Combines with --city for location-aware pricing."
        ),
    )

    # City
    p.add_argument(
        "--city",
        "-c",
        default="תל אביב",
        metavar="CITY",
        help="Hebrew city name for location context (default: תל אביב)",
    )

    # Limits
    p.add_argument(
        "--max-products",
        type=int,
        default=50,
        metavar="N",
        help=(
            "Max products to fetch compare results for (default: 50). "
            "With --all, set to 0 for no limit (fetches all ~100K+ sitemap products)."
        ),
    )
    p.add_argument(
        "--max-concurrent",
        type=int,
        default=1,
        metavar="N",
        help="Concurrent compare_results requests (default: 1; keep at 1 — parallel requests trigger server bot-detection)",
    )
    row_scope = p.add_mutually_exclusive_group()
    row_scope.add_argument(
        "--online-only",
        action="store_false",
        dest="include_physical",
        help="Return only online-store rows (exclude nearby physical branches)",
    )
    row_scope.add_argument(
        "--include-physical",
        action="store_true",
        dest="include_physical",
        help="Include nearby physical branch prices as well as online-store prices (default)",
    )
    p.set_defaults(include_physical=True)
    p.add_argument(
        "--no-compare-row-details",
        action="store_false",
        dest="include_compare_row_details",
        help="Do not include compare_row_details_by_product in the JSON payload",
    )
    p.add_argument(
        "--include-compare-html",
        action="store_true",
        help="Include raw compare HTML per product inside compare_row_details_by_product",
    )
    p.set_defaults(include_compare_row_details=True)

    # Output
    p.add_argument(
        "--output",
        "-o",
        metavar="FILE",
        help="Write full JSON results to FILE (default: pretty table to stdout)",
    )

    # Utility actions
    p.add_argument(
        "--list-cities", action="store_true", help="Print known cities and exit"
    )
    p.add_argument(
        "--search-cities", metavar="TERM", help="Search cities by name and exit"
    )

    # Verbosity
    p.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    p.add_argument("--quiet", action="store_true", help="Suppress all non-error output")

    return p


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _print_product_table(product_id: str, rows: list) -> None:
    """Print a price-comparison table for one product across all stores."""
    # Use the first row's name (they're all the same product)
    name = (rows[0]["name"] or product_id)[:70] if rows else product_id
    print(f"\n{'─' * 70}")
    print(f"  {name}")
    print(f"{'─' * 70}")
    print(f"  {'Store':<40} {'Price':>8}  {'Deal'}")
    print(f"  {'-' * 40} {'-' * 8}  {'-' * 20}")
    # Sort by price ascending so cheapest is first
    for up in sorted(rows, key=lambda r: r["price"]):
        store = (up["store_id"] or up["store_name"] or "")[:39]
        price = f"₪{up['price']:.2f}"
        deal = ""
        if up.get("deal") and up["deal"].get("has_deal"):
            deal = up["deal"].get("deal_description", "")[:30]
        print(f"  {store:<40} {price:>8}  {deal}")


def _print_summary(result: dict) -> None:
    print(f"\n{'=' * 70}")
    print(f"  chp.co.il scrape summary")
    print(f"{'=' * 70}")
    n_products = len(result["products_by_store"])  # key is product_id in chp
    print(f"  Unique products : {n_products}")
    print(f"  Price records   : {result['products_total']}")
    print(f"  Stores seen     : {result['stores_scraped']}")
    print(f"  Duration        : {result['duration_seconds']:.1f}s")
    if result["errors"]:
        print(f"  Errors          : {len(result['errors'])}")
        for e in result["errors"][:5]:
            print(f"    - {e}")
    print()


# ---------------------------------------------------------------------------
# async main
# ---------------------------------------------------------------------------


async def async_main(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    # Configure logging
    level = (
        logging.DEBUG
        if args.verbose
        else (logging.ERROR if args.quiet else logging.INFO)
    )
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # --- list-cities ---
    if args.list_cities:
        cities = await update_cities()
        print(f"{'Name':<20} {'city_id':<10} {'street_id'}")
        print(f"{'-' * 20} {'-' * 10} {'-' * 10}")
        for c in cities:
            print(f"{c['name']:<20} {c['city_id']:<10} {c['street_id']}")
        return 0

    # --- search-cities ---
    if args.search_cities:
        u = _new_u()
        ssl_ctx = make_ssl_context()
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=ssl_ctx)
        ) as session:
            cities = await search_cities(session, args.search_cities, u)
        if not cities:
            print(f"No cities found for {args.search_cities!r}", file=sys.stderr)
            return 1
        print(f"{'Name':<25} {'city_id':<10} {'street_id'}")
        for c in cities:
            print(f"{c.label:<25} {c.city_id:<10} {c.street_id}")
        return 0

    # --- scrape ---
    query = args.query or ""
    barcode = args.barcode or ""
    browse_all = getattr(args, "browse_all", False)

    if not query and not barcode and not browse_all:
        parser.error("provide --query, --barcode, or --all (unrestricted browse)")

    sf: ScrapeFilter = {}
    if query:
        sf["name_query"] = query
    if barcode:
        sf["barcode"] = barcode

    if not args.quiet:
        if browse_all:
            print(
                f"Browsing chp.co.il (all products via sitemap, up to {args.max_products or 'unlimited'}) "
                f"in {args.city!r} …"
            )
        else:
            print(f"Searching chp.co.il for: {query or barcode!r} in {args.city!r} …")

    result = await scrape(
        sf,
        city=args.city,
        max_products=args.max_products,
        max_concurrent=args.max_concurrent,
        browse_all=browse_all,
        include_physical=args.include_physical,
        include_compare_row_details=args.include_compare_row_details,
        include_compare_html=args.include_compare_html,
    )

    # Output
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        if not args.quiet:
            print(f"Written to {args.output}")
    else:
        # Pretty-print to stdout — one table per product, sorted by product name
        for product_id, rows in sorted(
            result["products_by_store"].items(),
            key=lambda kv: (kv[1][0]["name"] if kv[1] else kv[0]),
        ):
            _print_product_table(product_id, rows)
        _print_summary(result)

    return 0 if not result["errors"] else 1


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(asyncio.run(async_main(args, parser)))


if __name__ == "__main__":
    main()
