#!/usr/bin/env python3
"""
Validate unified product-info completeness for every supermarket scraper.

This is an archived QA script, not runtime scraper code. It runs each configured
scraper for one representative branch/store, validates every returned product
against the unified schema, and reports enrichment-field coverage percentages.

CHP is intentionally excluded because it is a separate price-comparison scraper.
Yochananof is skipped while its site/API is under maintenance.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import math
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.common import ScrapeFilter  # noqa: E402
from validation_archive_20260502_product_info.validate_product_basket import (  # noqa: E402
    SCRAPER_CONFIG,
)


REQUIRED_FIELDS = [
    "chain",
    "store_id",
    "store_name",
    "product_id",
    "name",
    "price",
    "regular_price",
    "category_ids",
    "is_weighable",
    "scraped_at",
]

OPTIONAL_INFO_FIELDS = [
    "sale_price",
    "discount_percent",
    "barcode",
    "image_url",
    "unit_description",
    "unit_of_measure",
    "unit_qty",
    "unit_qty_si",
    "unit_dimension",
    "price_per_base_unit",
    "deal",
    "brand",
    "manufacturer",
]

VALID_UNIT_DIMENSIONS = {"mass", "volume", "count", None}


@dataclass
class ChainCompleteness:
    chain: str
    ok: bool
    skipped: bool
    products_total: int
    stores: list[str]
    duration_seconds: float
    errors: list[str] = field(default_factory=list)
    invalid_required_counts: dict[str, int] = field(default_factory=dict)
    optional_coverage_percent: dict[str, float] = field(default_factory=dict)
    optional_present_counts: dict[str, int] = field(default_factory=dict)
    sample_invalid_products: list[dict[str, Any]] = field(default_factory=list)
    scraper_errors: list[str] = field(default_factory=list)
    skip_reason: str | None = None


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, list) and not value:
        return True
    return False


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _validate_required(product: dict[str, Any]) -> list[str]:
    missing: list[str] = []

    for field_name in REQUIRED_FIELDS:
        if field_name not in product or _is_blank(product.get(field_name)):
            missing.append(field_name)

    for field_name in ("price", "regular_price"):
        value = product.get(field_name)
        if not _is_number(value) or float(value) < 0:
            missing.append(f"{field_name}:invalid_number")

    if not isinstance(product.get("category_ids"), list):
        missing.append("category_ids:not_list")

    if not isinstance(product.get("is_weighable"), bool):
        missing.append("is_weighable:not_bool")

    if product.get("unit_dimension") not in VALID_UNIT_DIMENSIONS:
        missing.append("unit_dimension:invalid")

    return missing


def _has_optional_info(product: dict[str, Any], field_name: str) -> bool:
    value = product.get(field_name)
    if field_name == "deal":
        return value is not None
    if field_name in {"sale_price", "discount_percent"}:
        return value is not None
    return not _is_blank(value)


def validate_products(
    chain: str,
    result: dict[str, Any],
    duration_seconds: float,
    sample_limit: int = 20,
) -> ChainCompleteness:
    products_by_store = result.get("products_by_store") or {}
    products = [
        product
        for store_products in products_by_store.values()
        for product in (store_products or [])
    ]

    invalid_required_counts: dict[str, int] = {}
    sample_invalid_products: list[dict[str, Any]] = []

    for product in products:
        invalid_fields = _validate_required(product)
        for field_name in invalid_fields:
            invalid_required_counts[field_name] = (
                invalid_required_counts.get(field_name, 0) + 1
            )
        if invalid_fields and len(sample_invalid_products) < sample_limit:
            sample_invalid_products.append(
                {
                    "product_id": product.get("product_id"),
                    "name": product.get("name"),
                    "store_id": product.get("store_id"),
                    "invalid_fields": invalid_fields,
                }
            )

    optional_present_counts: dict[str, int] = {}
    optional_coverage_percent: dict[str, float] = {}
    for field_name in OPTIONAL_INFO_FIELDS:
        present = sum(1 for product in products if _has_optional_info(product, field_name))
        optional_present_counts[field_name] = present
        optional_coverage_percent[field_name] = (
            round((present / len(products)) * 100, 2) if products else 0.0
        )

    errors: list[str] = []
    if not products:
        errors.append("No products returned")
    for field_name, count in sorted(invalid_required_counts.items()):
        errors.append(f"{field_name}: invalid/missing in {count} product(s)")

    scraper_errors = list(result.get("errors") or [])
    errors.extend(scraper_errors)

    return ChainCompleteness(
        chain=chain,
        ok=not errors,
        skipped=False,
        products_total=len(products),
        stores=list(products_by_store.keys()),
        duration_seconds=round(duration_seconds, 2),
        errors=errors,
        invalid_required_counts=invalid_required_counts,
        optional_coverage_percent=optional_coverage_percent,
        optional_present_counts=optional_present_counts,
        sample_invalid_products=sample_invalid_products,
        scraper_errors=scraper_errors,
    )


async def scrape_chain(chain: str) -> ChainCompleteness:
    config = SCRAPER_CONFIG[chain]
    start = time.monotonic()

    if config.get("skip"):
        return ChainCompleteness(
            chain=chain,
            ok=True,
            skipped=True,
            products_total=0,
            stores=[],
            duration_seconds=0.0,
            skip_reason=str(config.get("skip_reason") or "Skipped by config"),
        )

    module = importlib.import_module(f"scrapers.{chain}.{chain}")
    kwargs: dict[str, Any] = {
        "flt": ScrapeFilter(),
        "batch_size": 100,
        "max_concurrent": 3,
        "max_retries": 2,
        "base_retry_delay": 0.5,
    }

    if chain in {"shufersal", "machsanei_hashook"}:
        pass
    elif chain == "ramilevi":
        kwargs["stores"] = config.get("stores")
    else:
        kwargs["branches"] = config.get("branches")

    try:
        result = await module.scrape(**kwargs)
    except Exception as exc:
        return ChainCompleteness(
            chain=chain,
            ok=False,
            skipped=False,
            products_total=0,
            stores=[],
            duration_seconds=round(time.monotonic() - start, 2),
            errors=[f"scrape_exception: {exc}"],
        )

    return validate_products(chain, result, time.monotonic() - start)


def print_summary(results: list[ChainCompleteness]) -> None:
    print("=" * 96)
    print("SUPERMARKET PRODUCT INFO COMPLETENESS")
    print("=" * 96)
    print(f"Testing time: {datetime.now().isoformat()}")
    print()
    print(f"{'Chain':<22} {'Status':<8} {'Products':<10} {'Stores':<8} {'Duration':<10} {'Required issues'}")
    print("-" * 96)
    for result in results:
        status = "SKIP" if result.skipped else ("PASS" if result.ok else "FAIL")
        issue_count = sum(result.invalid_required_counts.values())
        print(
            f"{result.chain:<22} {status:<8} {result.products_total:<10} "
            f"{len(result.stores):<8} {result.duration_seconds:>8.2f}s {issue_count}"
        )

    print()
    for result in results:
        if result.skipped:
            print(f"{result.chain}: skipped - {result.skip_reason}")
            continue

        print()
        print(f"{result.chain.upper()}")
        print(f"  Required schema: {'ok' if not result.invalid_required_counts else 'issues'}")
        if result.errors:
            for error in result.errors[:20]:
                print(f"  - {error}")
            if len(result.errors) > 20:
                print(f"  - ... {len(result.errors) - 20} more")

        coverage = result.optional_coverage_percent
        print(
            "  Optional coverage: "
            f"barcode={coverage.get('barcode', 0):.1f}% "
            f"image={coverage.get('image_url', 0):.1f}% "
            f"unit={coverage.get('unit_qty_si', 0):.1f}% "
            f"price_per_base={coverage.get('price_per_base_unit', 0):.1f}% "
            f"brand={coverage.get('brand', 0):.1f}% "
            f"manufacturer={coverage.get('manufacturer', 0):.1f}% "
            f"deal={coverage.get('deal', 0):.1f}%"
        )

        if result.sample_invalid_products:
            print("  Sample invalid products:")
            for sample in result.sample_invalid_products[:5]:
                print(f"  - {sample}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--chains",
        nargs="*",
        default=list(SCRAPER_CONFIG.keys()),
        help="Specific chains to validate. Defaults to all configured chains.",
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "output_dir/validation/product_info_completeness.json"),
        help="Path for JSON report.",
    )
    args = parser.parse_args()

    unknown = [chain for chain in args.chains if chain not in SCRAPER_CONFIG]
    if unknown:
        raise SystemExit(f"Unknown chain(s): {', '.join(unknown)}")

    results = await asyncio.gather(*(scrape_chain(chain) for chain in args.chains))
    print_summary(results)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print()
    print(f"Wrote {output_path}")

    failed = [result for result in results if not result.ok and not result.skipped]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
