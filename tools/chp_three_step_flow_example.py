#!/usr/bin/env python3
"""Example: CHP three-step endpoint flow with async helpers.

This script demonstrates:
1) shopping_address lookup (location IDs)
2) product_extended pagination (product candidates)
3) compare_results fetch with caller-controlled `from` and `num_results`

Run:
    venv/bin/python tools/chp_three_step_flow_example.py \
      --shopping-term "תל אביב" \
      --product-term "יוגורט עזים" \
      --from-offset 0 \
      --num-results 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any, Dict

import aiohttp

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.chp.chp import (
    CityInfo,
    _new_u,
    fetch_compare_results_page,
    fetch_shopping_address_page,
    iter_product_autocomplete_pages,
    make_ssl_context,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Demonstrate CHP three-step async flow."
    )
    parser.add_argument(
        "--shopping-term",
        default="תל אביב",
        help="Shopping address search term (step 1)",
    )
    parser.add_argument(
        "--city-index",
        type=int,
        default=0,
        help="Index of selected shopping address match (step 1)",
    )
    parser.add_argument(
        "--product-term",
        default="יוגורט עזים",
        help="Product search term for product_extended (step 2)",
    )
    parser.add_argument(
        "--product-pages",
        type=int,
        default=3,
        help="Maximum product autocomplete pages to fetch (step 2)",
    )
    parser.add_argument(
        "--max-product-results",
        type=int,
        default=100,
        help="Maximum product rows to gather in step 2",
    )
    parser.add_argument(
        "--product-id",
        default="",
        help="Explicit product ID to use for compare_results (step 3)",
    )
    parser.add_argument(
        "--product-index",
        type=int,
        default=0,
        help="Fallback selected product index from collected products",
    )
    parser.add_argument(
        "--from-offset",
        type=int,
        default=0,
        help="compare_results `from` parameter",
    )
    parser.add_argument(
        "--num-results",
        type=int,
        default=20,
        help="compare_results `num_results` parameter",
    )
    parser.add_argument(
        "--product-barcode-param",
        default="0",
        help=(
            "compare_results `product_barcode` param. Use '0', 'auto', or explicit value."
        ),
    )
    parser.add_argument(
        "--header-mode",
        choices=("safe_nav", "xhr"),
        default="safe_nav",
        help="Request mode for compare_results",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional output JSON path for captured artifacts",
    )
    return parser


async def run_flow(args: argparse.Namespace) -> Dict[str, Any]:
    u = _new_u()
    ssl_ctx = make_ssl_context()
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)

    async with aiohttp.ClientSession(connector=connector) as session:
        # Step 1: shopping_address
        shopping_page = await fetch_shopping_address_page(session, args.shopping_term, u)
        if not shopping_page.matches:
            raise RuntimeError(
                f"No shopping_address matches found for term: {args.shopping_term!r}"
            )
        if not (0 <= args.city_index < len(shopping_page.matches)):
            raise ValueError(
                f"city-index {args.city_index} out of range (0..{len(shopping_page.matches)-1})"
            )
        selected_city: CityInfo = shopping_page.matches[args.city_index]

        # Step 2: product_extended pagination
        products_by_id: Dict[str, Dict[str, Any]] = {}
        product_pages = []
        async for page in iter_product_autocomplete_pages(
            session,
            args.product_term,
            selected_city,
            u,
            max_pages=args.product_pages,
            max_results=args.max_product_results,
        ):
            product_pages.append(
                {
                    "from_offset": page.from_offset,
                    "raw_count": len(page.raw_items),
                    "real_count": len(page.real_items),
                    "has_prev": page.has_prev,
                    "has_next": page.has_next,
                }
            )
            for item in page.real_items:
                pid = str(item.get("id", ""))
                if pid and pid not in products_by_id:
                    products_by_id[pid] = item

        if not products_by_id:
            raise RuntimeError(
                f"No product_extended matches found for term: {args.product_term!r}"
            )

        ordered_products = list(products_by_id.values())
        if args.product_id:
            selected_item = products_by_id.get(args.product_id)
            if selected_item is None:
                raise RuntimeError(
                    f"Requested --product-id {args.product_id!r} was not found in collected step-2 results"
                )
        else:
            if not (0 <= args.product_index < len(ordered_products)):
                raise ValueError(
                    f"product-index {args.product_index} out of range (0..{len(ordered_products)-1})"
                )
            selected_item = ordered_products[args.product_index]

        selected_product_id = str(selected_item.get("id", ""))
        selected_product_label = str(selected_item.get("value", ""))
        if not selected_product_id:
            raise RuntimeError("Selected product has empty id")

        # Step 3: compare_results with caller-controlled from/num_results
        product_barcode_param = args.product_barcode_param
        if product_barcode_param == "auto":
            product_barcode_param = selected_product_id

        compare_result = await fetch_compare_results_page(
            session,
            city=selected_city,
            product_name_or_barcode=selected_product_id,
            product_barcode=product_barcode_param,
            from_offset=args.from_offset,
            num_results=args.num_results,
            header_mode=args.header_mode,
        )

        all_row_details = [
            *compare_result.physical_row_details,
            *compare_result.online_row_details,
        ]
        all_row_details_sorted = sorted(
            all_row_details,
            key=lambda row: float(row.get("pricing", {}).get("price", 0.0) or 0.0),
        )
        cheapest_row = all_row_details_sorted[0] if all_row_details_sorted else None
        highest_row = all_row_details_sorted[-1] if all_row_details_sorted else None

        return {
            "u": u,
            "shopping": {
                "term": args.shopping_term,
                "matches_count": len(shopping_page.matches),
                "selected_city": {
                    "label": selected_city.label,
                    "city_id": selected_city.city_id,
                    "street_id": selected_city.street_id,
                },
                "matches_preview": [
                    {
                        "label": c.label,
                        "city_id": c.city_id,
                        "street_id": c.street_id,
                    }
                    for c in shopping_page.matches[:10]
                ],
            },
            "products": {
                "term": args.product_term,
                "pages": product_pages,
                "unique_products": len(ordered_products),
                "selected_product": {
                    "id": selected_product_id,
                    "label": selected_product_label,
                },
                "preview": [
                    {
                        "id": str(p.get("id", "")),
                        "label": str(p.get("value", "")),
                    }
                    for p in ordered_products[:10]
                ],
            },
            "compare": {
                "from_offset": args.from_offset,
                "num_results": args.num_results,
                "header_mode": args.header_mode,
                "product_barcode_param": product_barcode_param,
                "hydrated_product_id": compare_result.product.product_id,
                "hydrated_product_name": compare_result.product.name_and_contents,
                "hydrated_product_barcode": compare_result.product.barcode,
                "physical_rows": len(compare_result.physical_row_details),
                "online_rows": len(compare_result.online_row_details),
                "rows_total": len(all_row_details),
                "cheapest_row": cheapest_row,
                "highest_row": highest_row,
                "physical_row_details": compare_result.physical_row_details,
                "online_row_details": compare_result.online_row_details,
                "all_row_details_sorted_by_price": all_row_details_sorted,
            },
        }


def print_summary(payload: Dict[str, Any]) -> None:
    shopping = payload["shopping"]
    products = payload["products"]
    compare = payload["compare"]

    print("\n=== CHP Three-Step Flow Summary ===")
    print(
        f"Step 1 (shopping_address): {shopping['matches_count']} matches; selected {shopping['selected_city']['label']}"
    )
    print(
        f"Step 2 (product_extended): {products['unique_products']} unique products; selected {products['selected_product']['id']}"
    )
    print(
        "Step 3 (compare_results): "
        f"from={compare['from_offset']} num_results={compare['num_results']} "
        f"physical={compare['physical_rows']} online={compare['online_rows']} total={compare['rows_total']}"
    )
    if compare.get("cheapest_row"):
        cheapest = compare["cheapest_row"]
        print(
            "Cheapest row: "
            f"{cheapest['store']['chain_name']} / {cheapest['store']['store_name']} "
            f"-> {cheapest['pricing']['price']}"
        )


def main() -> int:
    args = build_parser().parse_args()
    payload = asyncio.run(run_flow(args))
    print_summary(payload)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Wrote output: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
