#!/usr/bin/env python3
"""Validate CHP compare parser row-by-row against browser DOM truth.

This script:
1. Discovers at least three product scenarios (no deal, price reduction, multi-buy).
2. Loads real compare pages in a browser (Playwright via Node script).
3. Compares browser DOM rows to parser output row-by-row.
4. Writes per-scenario artifacts (browser rows, parser rows, mismatch report).

Usage:
    ./venv/bin/python tools/chp_browser_compare_validation.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence

import aiohttp

# Ensure project root is on sys.path when executed directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers.chp.chp import (
    ChpProduct,
    CityInfo,
    _build_compare_results_url,
    _new_u,
    build_compare_result_details,
    fetch_compare_results_page,
    get_city,
    iter_product_autocomplete_pages,
    make_ssl_context,
    parse_compare_results,
    utc_now_iso,
)
from scrapers.chp.compare_validation import compare_browser_rows_to_parser_details

TARGET_DEAL_TYPES = ("none", "price_reduction", "multi_buy")
# Seed IDs from known CHP compare fixtures to accelerate scenario discovery.
SEED_PRODUCT_IDS = (
    "7290027600007_7290010429554",
)
FIXTURE_COMPARE_HTML_PATH = Path("documentation/chp_documentation/comparison_result_example.html")
DEFAULT_PRODUCT_TERMS = (
    "חלב",
    "יוגורט",
    "קפה",
    "שמן זית",
    "שוקולד",
    "קוקה קולה",
    "חטיף",
    "נייר טואלט",
    "פסטה",
    "ביצים",
    "לחם",
    "גבינה",
)
_SAFE_ID_RE = re.compile(r"[^0-9A-Za-z._-]+")


@dataclass
class ScenarioChoice:
    deal_type: str
    source_term: str
    product: ChpProduct
    city: CityInfo
    compare_url: str
    compare_result: Any
    has_physical: bool
    has_online: bool


def _safe_slug(value: str) -> str:
    value = value.strip().replace(" ", "_")
    value = _SAFE_ID_RE.sub("_", value)
    return value.strip("_") or "scenario"


def _row_deal_types(compare_result: Any) -> set[str]:
    deal_types: set[str] = set()
    all_rows = [
        *compare_result.physical_row_details,
        *compare_result.online_row_details,
    ]
    for row in all_rows:
        deal = row.get("deal")
        if not deal:
            deal_types.add("none")
            continue
        deal_type = str(deal.get("deal_type") or "other")
        if deal_type in TARGET_DEAL_TYPES:
            deal_types.add(deal_type)
    return deal_types


def _is_better_candidate(current: Optional[ScenarioChoice], candidate: ScenarioChoice) -> bool:
    if current is None:
        return True
    # Prefer products that include both physical and online sections.
    current_score = int(current.has_physical and current.has_online)
    candidate_score = int(candidate.has_physical and candidate.has_online)
    if candidate_score > current_score:
        return True
    if candidate_score < current_score:
        return False
    # Prefer the scenario with more total rows as it validates more table lines.
    current_rows = (
        len(current.compare_result.physical_row_details)
        + len(current.compare_result.online_row_details)
    )
    candidate_rows = (
        len(candidate.compare_result.physical_row_details)
        + len(candidate.compare_result.online_row_details)
    )
    return candidate_rows > current_rows


def _load_fixture_compare_result() -> Optional[Any]:
    if not FIXTURE_COMPARE_HTML_PATH.exists():
        return None
    html = FIXTURE_COMPARE_HTML_PATH.read_text(encoding="utf-8")
    product = ChpProduct(
        {
            "id": "7290027600007_7290010429554",
            "value": "7290027600007_7290010429554",
            "label": "7290027600007_7290010429554",
            "parts": {},
        }
    )
    physical_rows, online_rows = parse_compare_results(html, product)
    physical_details, online_details = build_compare_result_details(
        physical_rows,
        online_rows,
        product,
        utc_now_iso(),
    )
    return SimpleNamespace(
        product=product,
        physical_rows=physical_rows,
        online_rows=online_rows,
        physical_row_details=physical_details,
        online_row_details=online_details,
        html=html,
    )


async def _discover_scenarios(
    *,
    city: str,
    terms: Sequence[str],
    max_products_per_term: int,
    product_pages: int,
    num_results: int,
    max_retries: int,
    retry_delay: float,
) -> Dict[str, ScenarioChoice]:
    u = _new_u()
    ssl_ctx = make_ssl_context()
    connector = aiohttp.TCPConnector(ssl=ssl_ctx, limit=4)
    timeout = aiohttp.ClientTimeout(total=45, connect=15, sock_read=30)

    selected: Dict[str, ScenarioChoice] = {}
    seen_product_ids: set[str] = set()

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        city_info = await get_city(session, city, u)
        if city_info is None:
            raise RuntimeError(f"City not found via CHP shopping_address: {city!r}")

        async def _evaluate_product(product: ChpProduct, source_term: str) -> None:
            compare_result = await fetch_compare_results_page(
                session,
                city=city_info,
                product_name_or_barcode=product.product_id,
                product_barcode=product.product_id or "0",
                from_offset=0,
                num_results=num_results,
                product=product,
                header_mode="safe_nav",
                max_retries=max_retries,
                retry_delay=retry_delay,
            )
            row_types = _row_deal_types(compare_result)
            if not row_types:
                return

            compare_url = _build_compare_results_url(
                city_info,
                product_name_or_barcode=product.product_id,
                product_barcode=product.product_id or "0",
                from_offset=0,
                num_results=num_results,
            )
            candidate = ScenarioChoice(
                deal_type="",
                source_term=source_term,
                product=compare_result.product,
                city=city_info,
                compare_url=compare_url,
                compare_result=compare_result,
                has_physical=bool(compare_result.physical_row_details),
                has_online=bool(compare_result.online_row_details),
            )

            for deal_type in row_types:
                if deal_type not in TARGET_DEAL_TYPES:
                    continue
                existing = selected.get(deal_type)
                if _is_better_candidate(existing, candidate):
                    selected[deal_type] = ScenarioChoice(
                        deal_type=deal_type,
                        source_term=source_term,
                        product=compare_result.product,
                        city=city_info,
                        compare_url=compare_url,
                        compare_result=compare_result,
                        has_physical=candidate.has_physical,
                        has_online=candidate.has_online,
                    )

        # Fast path: evaluate known stable product IDs first.
        for seed_pid in SEED_PRODUCT_IDS:
            if len(selected) == len(TARGET_DEAL_TYPES):
                break
            try:
                seed_product = ChpProduct(
                    {
                        "id": seed_pid,
                        "value": seed_pid,
                        "label": seed_pid,
                        "parts": {},
                    }
                )
                await _evaluate_product(seed_product, "seed_product_id")
            except Exception:
                continue

        for term in terms:
            if len(selected) == len(TARGET_DEAL_TYPES):
                break

            products: List[ChpProduct] = []
            async for page in iter_product_autocomplete_pages(
                session,
                term,
                city_info,
                u,
                max_pages=product_pages,
                max_results=max_products_per_term,
            ):
                for item in page.real_items:
                    pid = str(item.get("id", ""))
                    if not pid or pid in seen_product_ids:
                        continue
                    seen_product_ids.add(pid)
                    products.append(ChpProduct(item))
                    if len(products) >= max_products_per_term:
                        break
                if len(products) >= max_products_per_term:
                    break

            for product in products:
                if len(selected) == len(TARGET_DEAL_TYPES):
                    break

                try:
                    await _evaluate_product(product, term)
                except Exception:
                    continue

        if len(selected) < len(TARGET_DEAL_TYPES):
            fixture_result = _load_fixture_compare_result()
            if fixture_result is not None:
                fixture_types = _row_deal_types(fixture_result)
                for deal_type in TARGET_DEAL_TYPES:
                    if deal_type in selected or deal_type not in fixture_types:
                        continue
                    selected[deal_type] = ScenarioChoice(
                        deal_type=deal_type,
                        source_term="fixture_html",
                        product=fixture_result.product,
                        city=CityInfo("fixture", "0", "9000"),
                        compare_url=FIXTURE_COMPARE_HTML_PATH.resolve().as_uri(),
                        compare_result=fixture_result,
                        has_physical=bool(fixture_result.physical_row_details),
                        has_online=bool(fixture_result.online_row_details),
                    )

    return selected


def _run_browser_capture(
    *,
    compare_url: str,
    out_json_path: Path,
    screenshot_path: Path,
    headless: bool,
) -> Dict[str, Any]:
    cmd = [
        "node",
        "tools/chp_dom_extract_rows.js",
        "--url",
        compare_url,
        "--out",
        str(out_json_path),
        "--screenshot",
        str(screenshot_path),
        "--headless",
        "true" if headless else "false",
    ]
    subprocess.run(cmd, check=True)
    return json.loads(out_json_path.read_text(encoding="utf-8"))


def _parser_details_from_html(html: str, product_code_hint: str) -> Dict[str, Any]:
    product = ChpProduct(
        {
            "id": product_code_hint,
            "value": product_code_hint,
            "label": product_code_hint,
            "parts": {},
        }
    )
    physical_rows, online_rows = parse_compare_results(html, product)
    physical_details, online_details = build_compare_result_details(
        physical_rows,
        online_rows,
        product,
        utc_now_iso(),
    )
    return {
        "product_id": product.product_id,
        "product_name": product.name_and_contents,
        "product_barcode": product.barcode,
        "physical_row_details": physical_details,
        "online_row_details": online_details,
    }


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _validate_one_scenario(
    *,
    scenario: ScenarioChoice,
    out_dir: Path,
    num_results: int,
    headless: bool,
) -> Dict[str, Any]:
    slug = _safe_slug(f"{scenario.deal_type}_{scenario.product.product_id}")
    scenario_dir = out_dir / slug
    scenario_dir.mkdir(parents=True, exist_ok=True)

    browser_capture_path = scenario_dir / "browser_capture.json"
    screenshot_path = scenario_dir / "browser_page.png"
    browser_capture = _run_browser_capture(
        compare_url=scenario.compare_url,
        out_json_path=browser_capture_path,
        screenshot_path=screenshot_path,
        headless=headless,
    )

    browser_rows_payload = {
        "scenario_type": scenario.deal_type,
        "source_term": scenario.source_term,
        "compare_url": scenario.compare_url,
        "product": browser_capture.get("product", {}),
        "physical_rows": browser_capture.get("physical_rows", []),
        "online_rows": browser_capture.get("online_rows", []),
    }
    _write_json(scenario_dir / "browser_rows.json", browser_rows_payload)

    product_code_hint = (
        str(browser_capture.get("product", {}).get("displayed_product_code", "")).strip()
        or scenario.product.product_id
    )
    parser_payload = _parser_details_from_html(
        browser_capture.get("html", ""),
        product_code_hint=product_code_hint,
    )
    _write_json(scenario_dir / "parser_rows.json", parser_payload)

    physical_mismatches = compare_browser_rows_to_parser_details(
        browser_rows_payload["physical_rows"],
        parser_payload["physical_row_details"],
        row_kind="physical",
    )
    online_mismatches = compare_browser_rows_to_parser_details(
        browser_rows_payload["online_rows"],
        parser_payload["online_row_details"],
        row_kind="online",
    )
    mismatches = [*physical_mismatches, *online_mismatches]

    mismatch_report = {
        "scenario_type": scenario.deal_type,
        "source_term": scenario.source_term,
        "product_id": scenario.product.product_id,
        "compare_url": scenario.compare_url,
        "num_results": num_results,
        "browser_counts": {
            "physical_rows": len(browser_rows_payload["physical_rows"]),
            "online_rows": len(browser_rows_payload["online_rows"]),
        },
        "parser_counts": {
            "physical_rows": len(parser_payload["physical_row_details"]),
            "online_rows": len(parser_payload["online_row_details"]),
        },
        "mismatches": mismatches,
        "passed": len(mismatches) == 0,
    }
    _write_json(scenario_dir / "mismatch_report.json", mismatch_report)

    if mismatch_report["passed"] and screenshot_path.exists():
        screenshot_path.unlink()

    return mismatch_report


async def _async_main(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    terms = [t.strip() for t in args.product_terms if t.strip()]
    selected = await _discover_scenarios(
        city=args.city,
        terms=terms,
        max_products_per_term=args.max_products_per_term,
        product_pages=args.product_pages,
        num_results=args.num_results,
        max_retries=args.max_retries,
        retry_delay=args.retry_delay,
    )
    missing = [dt for dt in TARGET_DEAL_TYPES if dt not in selected]
    if missing:
        print(f"Missing required deal scenarios: {', '.join(missing)}")
        print("Try widening terms or max-products-per-term.")
        return 1

    reports: List[Dict[str, Any]] = []
    for deal_type in TARGET_DEAL_TYPES:
        scenario = selected[deal_type]
        report = _validate_one_scenario(
            scenario=scenario,
            out_dir=out_dir,
            num_results=args.num_results,
            headless=args.headless,
        )
        reports.append(report)

    summary = {
        "city": args.city,
        "product_terms": terms,
        "num_results": args.num_results,
        "scenarios": reports,
        "all_passed": all(r.get("passed") for r in reports),
    }
    _write_json(out_dir / "summary.json", summary)

    for report in reports:
        status = "PASS" if report["passed"] else "FAIL"
        print(
            f"[{status}] {report['scenario_type']}: "
            f"{report['product_id']} mismatches={len(report['mismatches'])}"
        )
    print(f"Wrote artifacts to: {out_dir}")

    return 0 if summary["all_passed"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate CHP parser output against browser DOM rows."
    )
    parser.add_argument(
        "--city",
        default="תל אביב",
        help="Shopping city term for CHP shopping_address lookup (default: תל אביב)",
    )
    parser.add_argument(
        "--product-terms",
        nargs="+",
        default=list(DEFAULT_PRODUCT_TERMS),
        help="Product search terms used to discover no-deal/price-reduction/multi-buy scenarios",
    )
    parser.add_argument(
        "--product-pages",
        type=int,
        default=2,
        help="Max autocomplete pages per term during scenario discovery",
    )
    parser.add_argument(
        "--max-products-per-term",
        type=int,
        default=20,
        help="Max unique products to evaluate per product term",
    )
    parser.add_argument(
        "--num-results",
        type=int,
        default=40,
        help="compare_results num_results parameter used in validation",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=1,
        help="Max retries per compare_results request",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=0.5,
        help="Base retry delay in seconds for compare_results fetches",
    )
    parser.add_argument(
        "--out-dir",
        default="output_dir/chp/browser_validation",
        help="Output directory for validation artifacts",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="Run browser capture headlessly (default: True)",
    )
    parser.add_argument(
        "--headed",
        action="store_false",
        dest="headless",
        help="Run browser capture with visible window",
    )
    return parser


def main() -> int:
    logging.getLogger("scrapers.chp").setLevel(logging.ERROR)
    args = build_parser().parse_args()
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
