#!/usr/bin/env python3
"""
Compare target supermarket catalogues against Shufersal/Tiv Taam references.

This archived QA script helps validate whether lower-count supermarket scrapers
are missing products that appear in the known broad catalogues. Matching uses:
1. exact barcode
2. normalized product name + normalized brand/manufacturer
3. token overlap fallback for same-brand products

Use `--brand` to keep the manual review focused.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.common import ScrapeFilter  # noqa: E402
from validation_archive_20260502_product_info.validate_product_basket import (  # noqa: E402
    SCRAPER_CONFIG,
)


DEFAULT_REFERENCES = ["shufersal", "tivtaam"]
DEFAULT_TARGETS = [
    "carrefour",
    "machsanei_hashook",
    "ramilevi",
    "keshet",
    "quik",
    "victory",
    "ybitan",
]

CHAIN_OUTPUT_DIRS = {
    "machsanei_hashook": "machsanei",
}


@dataclass
class CoverageResult:
    target: str
    ok: bool
    skipped: bool
    reference_products: int
    target_products: int
    matched: int
    missing: int
    coverage_percent: float
    duration_seconds: float
    sample_missing: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    data_sources: dict[str, str] = field(default_factory=dict)


def _norm(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[\"'״׳`´]", "", text)
    text = re.sub(r"[-_/.,:;()[\]{}+]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _brand(product: dict[str, Any]) -> str:
    return _norm(product.get("brand") or product.get("manufacturer"))


def _barcode(product: dict[str, Any]) -> str:
    barcode = str(product.get("barcode") or "").strip()
    if barcode:
        return barcode
    product_id = str(product.get("product_id") or "").strip()
    if product_id.isdigit() and 8 <= len(product_id) <= 14:
        return product_id
    return ""


def _identity_key(product: dict[str, Any]) -> str:
    barcode = _barcode(product)
    if barcode:
        return f"barcode:{barcode}"
    brand = _brand(product)
    return f"name_brand:{_norm(product.get('name'))}|{brand}"


def _name_unit_key(product: dict[str, Any]) -> str:
    return f"name_unit:{_norm(product.get('name'))}|{_norm(product.get('unit_description'))}"


def _tokens(product: dict[str, Any]) -> set[str]:
    text = " ".join(
        _norm(product.get(field))
        for field in ("name", "brand", "manufacturer", "unit_description")
        if product.get(field)
    )
    return {token for token in text.split() if len(token) > 1}


def _brand_matches(product: dict[str, Any], brands: list[str]) -> bool:
    if not brands:
        return True
    haystack = " ".join(
        _norm(product.get(field))
        for field in ("name", "brand", "manufacturer")
        if product.get(field)
    )
    return any(_norm(brand) in haystack for brand in brands)


def latest_cached_products(chain: str, output_dir: Path) -> tuple[list[dict[str, Any]], str | None]:
    chain_dir = output_dir / CHAIN_OUTPUT_DIRS.get(chain, chain)
    patterns = ["branch_*.json", "store_*.json", "products_*.json"]
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(chain_dir.glob(pattern))
    candidates = [
        path
        for path in candidates
        if path.is_file() and not path.name.startswith("summary_")
    ]
    if not candidates:
        return [], None

    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
    except Exception:
        return [], str(latest)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)], str(latest)
    return [], str(latest)


async def scrape_products(chain: str) -> tuple[list[dict[str, Any]], list[str]]:
    config = SCRAPER_CONFIG[chain]
    if config.get("skip"):
        return [], [str(config.get("skip_reason") or "Skipped by config")]

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

    result = await module.scrape(**kwargs)
    products_by_store = result.get("products_by_store") or {}
    products = [
        product
        for store_products in products_by_store.values()
        for product in (store_products or [])
    ]
    return products, list(result.get("errors") or [])


async def load_products(
    chain: str,
    *,
    output_dir: Path,
    use_cache_only: bool,
) -> tuple[list[dict[str, Any]], str, list[str]]:
    cached, cached_source = latest_cached_products(chain, output_dir)
    if cached:
        return cached, cached_source or "cache", []
    if use_cache_only:
        return [], "missing_cache", ["No cached output JSON found"]

    products, errors = await scrape_products(chain)
    return products, "live_scrape", errors


def dedupe_reference(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for product in products:
        key = _identity_key(product)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(product)
    return deduped


def build_target_indexes(
    products: list[dict[str, Any]],
) -> tuple[set[str], set[str], dict[str, list[dict[str, Any]]]]:
    exact_keys = {_identity_key(product) for product in products}
    name_unit_keys = {_name_unit_key(product) for product in products}
    by_brand: dict[str, list[dict[str, Any]]] = {}
    for product in products:
        by_brand.setdefault(_brand(product), []).append(product)
    return exact_keys, name_unit_keys, by_brand


def token_overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def matches_target(
    reference: dict[str, Any],
    exact_keys: set[str],
    name_unit_keys: set[str],
    by_brand: dict[str, list[dict[str, Any]]],
    threshold: float,
    allow_name_unit_fallback: bool,
) -> bool:
    if _identity_key(reference) in exact_keys:
        return True
    if allow_name_unit_fallback and _name_unit_key(reference) in name_unit_keys:
        return True

    brand = _brand(reference)
    if not brand and not allow_name_unit_fallback:
        return False

    reference_tokens = _tokens(reference)
    candidates = by_brand.get(brand, [])
    if allow_name_unit_fallback and not candidates:
        candidates = [candidate for bucket in by_brand.values() for candidate in bucket]
    for candidate in candidates:
        if token_overlap(reference_tokens, _tokens(candidate)) >= threshold:
            return True
    return False


async def compare_target(
    target: str,
    reference_products: list[dict[str, Any]],
    *,
    output_dir: Path,
    use_cache_only: bool,
    sample_limit: int,
    threshold: float,
    allow_name_unit_fallback: bool,
) -> CoverageResult:
    start = time.monotonic()
    products, source, errors = await load_products(
        target, output_dir=output_dir, use_cache_only=use_cache_only
    )
    if not products:
        return CoverageResult(
            target=target,
            ok=False,
            skipped=False,
            reference_products=len(reference_products),
            target_products=0,
            matched=0,
            missing=len(reference_products),
            coverage_percent=0.0,
            duration_seconds=round(time.monotonic() - start, 2),
            errors=errors or ["No target products loaded"],
            data_sources={target: source},
        )

    exact_keys, name_unit_keys, by_brand = build_target_indexes(products)
    matched = 0
    sample_missing: list[dict[str, Any]] = []

    for product in reference_products:
        if matches_target(
            product,
            exact_keys,
            name_unit_keys,
            by_brand,
            threshold,
            allow_name_unit_fallback,
        ):
            matched += 1
        elif len(sample_missing) < sample_limit:
            sample_missing.append(
                {
                    "reference_chain": product.get("chain"),
                    "product_id": product.get("product_id"),
                    "barcode": _barcode(product),
                    "brand": product.get("brand"),
                    "manufacturer": product.get("manufacturer"),
                    "name": product.get("name"),
                    "unit_description": product.get("unit_description"),
                }
            )

    missing = len(reference_products) - matched
    coverage = round((matched / len(reference_products)) * 100, 2) if reference_products else 0.0

    return CoverageResult(
        target=target,
        ok=missing == 0 and not errors,
        skipped=False,
        reference_products=len(reference_products),
        target_products=len(products),
        matched=matched,
        missing=missing,
        coverage_percent=coverage,
        duration_seconds=round(time.monotonic() - start, 2),
        sample_missing=sample_missing,
        errors=errors,
        data_sources={target: source},
    )


def print_results(results: list[CoverageResult], brands: list[str], references: list[str]) -> None:
    brand_text = ", ".join(brands) if brands else "all brands"
    print("=" * 96)
    print("REFERENCE CATALOGUE COVERAGE")
    print("=" * 96)
    print(f"References: {', '.join(references)}")
    print(f"Brand filter: {brand_text}")
    print()
    print(f"{'Target':<22} {'Coverage':<10} {'Matched':<10} {'Missing':<10} {'Target products':<16} {'Duration'}")
    print("-" * 96)
    for result in results:
        print(
            f"{result.target:<22} {result.coverage_percent:>7.2f}%  "
            f"{result.matched:<10} {result.missing:<10} "
            f"{result.target_products:<16} {result.duration_seconds:>8.2f}s"
        )
    print()
    for result in results:
        if result.errors:
            print(f"{result.target} errors: {result.errors}")
        if result.sample_missing:
            print(f"{result.target} sample missing:")
            for item in result.sample_missing[:10]:
                print(f"  - {item}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--brand", action="append", default=[], help="Brand/manufacturer/name filter. Repeatable.")
    parser.add_argument("--references", nargs="*", default=DEFAULT_REFERENCES)
    parser.add_argument("--targets", nargs="*", default=DEFAULT_TARGETS)
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "output_dir"))
    parser.add_argument("--output", default=str(PROJECT_ROOT / "output_dir/validation/reference_catalog_coverage.json"))
    parser.add_argument("--use-cache-only", action="store_true")
    parser.add_argument("--sample-limit", type=int, default=50)
    parser.add_argument("--threshold", type=float, default=0.75)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    data_sources: dict[str, str] = {}
    errors: list[str] = []
    reference_products: list[dict[str, Any]] = []

    for reference in args.references:
        products, source, load_errors = await load_products(
            reference, output_dir=output_dir, use_cache_only=args.use_cache_only
        )
        data_sources[reference] = source
        errors.extend(f"{reference}: {error}" for error in load_errors)
        reference_products.extend(product for product in products if _brand_matches(product, args.brand))

    reference_products = dedupe_reference(reference_products)
    if not reference_products:
        raise SystemExit(f"No reference products loaded. Sources={data_sources} Errors={errors}")

    results = await asyncio.gather(
        *(
            compare_target(
                target,
                reference_products,
                output_dir=output_dir,
                use_cache_only=args.use_cache_only,
                sample_limit=args.sample_limit,
                threshold=args.threshold,
                allow_name_unit_fallback=bool(args.brand),
            )
            for target in args.targets
        )
    )

    for result in results:
        result.data_sources.update(data_sources)

    print_results(results, args.brand, args.references)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print()
    print(f"Wrote {output_path}")

    return 1 if any(result.missing for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
