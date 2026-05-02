#!/usr/bin/env python3
"""
Live scraper validation script
==============================
Runs each of the 10 supermarket scrapers against one branch/store and verifies
that a required grocery basket exists in each catalogue.

CHP is intentionally excluded: it is a separate price-comparison scraper, not
part of this unified supermarket scraper validation path.

Run with:
  python3 validate_scrapers.py
"""

import asyncio
import importlib
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import aiohttp

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.common import make_ssl_context
from utils import get_browser_headers


@dataclass
class ValidationResult:
    scraper: str
    ok: bool
    skipped: bool
    product_count: int
    store_ids_found: list
    errors: list
    duration: float
    failure_kind: Optional[str] = None
    diagnosis: Optional[str] = None
    coverage_found: Optional[dict] = None
    coverage_missing: Optional[list] = None


SCRAPER_CONFIG = {
    "tivtaam": {
        "url": "https://www.tivtaam.co.il",
        "branches": [{"id": 924, "name": "רמת החייל", "city": "תל אביב יפו", "location": "דבורה הנביאה 122"}],
    },
    "shufersal": {
        "url": "https://www.shufersal.co.il",
        "branches": None,  # No branch parameter needed
    },
    "yochananof": {
        "url": "https://www.yochananof.co.il",
        "stores": None,  # Fetches live from GraphQL
        "skip": True,
        "skip_reason": "Under maintenance / Cloudflare API challenge as of 2026-05-02",
    },
    "carrefour": {
        "url": "https://www.carrefour.co.il",
        "branches": [{"id": 3019, "name": "אור יהודה", "city": "אור יהודה", "location": ""}],
    },
    "machsanei_hashook": {
        "url": "https://www.mck.co.il",
        "branches": None,  # Always branch 836
    },
    "ramilevi": {
        "url": "https://www.rami-levy.co.il",
        "stores": [{"id": 125, "name": "ראשון לציון", "city": "ראשון לציון"}],
    },
    "keshet": {
        "url": "https://www.keshet-teamim.co.il",
        "branches": [{"id": 1570, "name": "סניף אשדוד", "city": "אשדוד", "location": ""}],
    },
    "quik": {
        "url": "https://www.quik.co.il",
        "branches": [{"id": 3264, "name": "אור יהודה - Online", "city": "אור יהודה", "location": ""}],
    },
    "victory": {
        "url": "https://www.victoryonline.co.il",
        "branches": [{"id": 2527, "name": "ויקטורי אשדוד - Victory Online", "city": "אשדוד", "location": ""}],
    },
    "ybitan": {
        "url": "https://www.ybitan.co.il",
        "branches": [{"id": 960, "name": "אור יהודה - Online", "city": "אור יהודה", "location": ""}],
    },
}


ProductMatcher = Callable[[dict], bool]


def _norm_text(value: Any) -> str:
    text = str(value or "").lower()
    return (
        text.replace('"', "")
        .replace("'", "")
        .replace("״", "")
        .replace("׳", "")
        .replace("-", " ")
        .replace("_", " ")
    )


def _product_text(product: dict) -> str:
    parts = [
        product.get("name"),
        product.get("brand"),
        product.get("manufacturer"),
        product.get("unit_description"),
        product.get("unit_of_measure"),
    ]
    return " ".join(_norm_text(part) for part in parts if part)


def _contains_all(*terms: str) -> ProductMatcher:
    normalized_terms = [_norm_text(term) for term in terms]

    def _match(product: dict) -> bool:
        text = _product_text(product)
        return all(term in text for term in normalized_terms)

    return _match


def _contains_any(*terms: str) -> ProductMatcher:
    normalized_terms = [_norm_text(term) for term in terms]

    def _match(product: dict) -> bool:
        text = _product_text(product)
        return any(term in text for term in normalized_terms)

    return _match


def _and(*matchers: ProductMatcher) -> ProductMatcher:
    return lambda product: all(matcher(product) for matcher in matchers)


def _milk_percent(percent: str) -> ProductMatcher:
    return _and(_contains_all("חלב"), _contains_any(f"{percent}%", f"{percent} %"))


def _milk_volume_liters(liters: int) -> ProductMatcher:
    expected_ml = liters * 1000
    text_markers = (
        f"{liters} ליטר",
        f"ליטר {liters}",
        f"{expected_ml} מל",
        f"{expected_ml} מ ל",
        f"{expected_ml} מ\"ל",
    )

    def _match(product: dict) -> bool:
        if "חלב" not in _product_text(product):
            return False
        qty_si = product.get("unit_qty_si")
        if isinstance(qty_si, (int, float)) and abs(float(qty_si) - expected_ml) <= 1:
            return True
        return _contains_any(*text_markers)(product)

    return _match


def _egg_size(size: str, hebrew: str) -> ProductMatcher:
    size_norm = _norm_text(size)
    hebrew_norm = _norm_text(hebrew)

    def _match(product: dict) -> bool:
        text = f" {_product_text(product)} "
        if "ביצ" not in text:
            return False
        return (
            f" {size_norm} " in text
            or f"מידה {size_norm}" in text
            or hebrew_norm in text
        )

    return _match


PRODUCT_COVERAGE_CHECKS: list[tuple[str, ProductMatcher]] = [
    ("חלב 3%", _milk_percent("3")),
    ("חלב 1%", _milk_percent("1")),
    ("חלב 1 ליטר", _milk_volume_liters(1)),
    ("חלב 2 ליטר", _milk_volume_liters(2)),
    ("ביצים L / large", _egg_size("l", "גדול")),
    ("ביצים M / medium", _egg_size("m", "בינוני")),
    ("חזה עוף", _contains_all("חזה", "עוף")),
    ("שווארמה עוף", _contains_all("שווארמה", "עוף")),
    ("כרעיים", _contains_any("כרעיים", "כרעים")),
    ("בשר טחון 20%", _and(_contains_all("בשר", "טחון"), _contains_any("20%", "20 %"))),
    ("שמן זית כתית", _contains_all("שמן", "זית", "כתית")),
    ("יוגורט יווני", _contains_all("יוגורט", "יווני")),
]


def validate_product_coverage(products_by_store: dict) -> tuple[dict, list]:
    products: list[dict] = []
    for store_products in products_by_store.values():
        products.extend(store_products or [])

    found: dict[str, str] = {}
    missing: list[str] = []
    for label, matcher in PRODUCT_COVERAGE_CHECKS:
        match = next((product for product in products if matcher(product)), None)
        if match:
            found[label] = str(match.get("name") or match.get("product_id") or "")
        else:
            missing.append(label)
    return found, missing


def _looks_like_cloudflare_challenge(status: int, body: str) -> bool:
    body_lower = body.lower()
    return (
        status == 403
        and "cloudflare" in body_lower
        and (
            "just a moment" in body_lower
            or "cf-browser-verification" in body_lower
            or "challenge-platform" in body_lower
        )
    )


async def diagnose_yochananof_cloudflare() -> tuple[Optional[str], Optional[str]]:
    """Check whether Yochananof GraphQL is returning a Cloudflare challenge."""
    from scrapers.yochananof.yochananof import GRAPHQL_URL, _STORES_QUERY

    url = f"{GRAPHQL_URL}?query={_STORES_QUERY}"
    connector = aiohttp.TCPConnector(ssl=make_ssl_context())
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            async with session.get(
                url,
                headers={
                    "User-Agent": get_browser_headers("https://www.yochananof.co.il")[
                        "User-Agent"
                    ],
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            ) as resp:
                body = await resp.text()
        except Exception as exc:
            return None, f"Yochananof Cloudflare probe failed: {exc}"

    if _looks_like_cloudflare_challenge(resp.status, body):
        excerpt = " ".join(body.split())[:220]
        return (
            "blocked_by_cloudflare",
            f"Yochananof GraphQL returned HTTP 403 Cloudflare challenge HTML: {excerpt}",
        )
    return None, f"Yochananof GraphQL probe returned HTTP {resp.status}"


async def probe_zuz_product_bearing_branch(scraper_name: str) -> tuple[bool, str]:
    """Verify a configured ZuZ branch has at least one product-bearing category."""
    config = SCRAPER_CONFIG[scraper_name]
    branch = config["branches"][0]
    module = importlib.import_module(f"scrapers.{scraper_name}.{scraper_name}")

    common_params = (
        "appId=4&languageId=1"
        '&categorySort={"sortType":1}'
        '&filters={"mustNot":{"term":{"branch.isOutOfStock":true}}}'
    )
    headers = get_browser_headers(module.BASE_URL)
    connector = aiohttp.TCPConnector(ssl=make_ssl_context())

    async with aiohttp.ClientSession(connector=connector) as session:
        totals: list[tuple[int, int]] = []
        for cat_id, _cat_name in module.MAIN_CATEGORIES:
            url = (
                f"{module.BASE_URL}/v2/retailers/{module.RETAILER_ID}"
                f"/branches/{branch['id']}/categories/{cat_id}/products"
                f"?{common_params}&from=0&size=1"
            )
            try:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        return False, f"branch_probe_http_{resp.status}"
                    data: dict[str, Any] = await resp.json()
            except Exception as exc:
                return False, f"branch_probe_error: {exc}"

            total = int(data.get("total") or 0)
            totals.append((cat_id, total))
            if total > 0:
                return True, f"branch_probe_ok: category={cat_id} total={total}"

    nonzero = [f"{cat_id}:{total}" for cat_id, total in totals if total > 0]
    return False, f"empty_branch: no product-bearing categories found ({', '.join(nonzero) or 'all zero'})"


async def run_scraper(scraper_name: str) -> ValidationResult:
    """Run a single scraper with milk filter on one branch."""
    config = SCRAPER_CONFIG[scraper_name]
    start_time = time.time()

    try:
        if config.get("skip"):
            return ValidationResult(
                scraper=scraper_name,
                ok=True,
                skipped=True,
                product_count=0,
                store_ids_found=[],
                errors=[],
                duration=time.time() - start_time,
                failure_kind="skipped",
                diagnosis=str(config.get("skip_reason") or "Skipped by config"),
                coverage_found={},
                coverage_missing=[],
            )

        # Import scraper module
        module = importlib.import_module(f"scrapers.{scraper_name}.{scraper_name}")
        scrape_fn = module.scrape

        # Import ScrapeFilter
        from scrapers.common import ScrapeFilter

        branch_probe_product_bearing: Optional[bool] = None
        branch_probe_diagnosis: Optional[str] = None
        if scraper_name in {"quik", "ybitan"}:
            branch_probe_product_bearing, branch_probe_diagnosis = (
                await probe_zuz_product_bearing_branch(scraper_name)
            )

        # Build kwargs based on scraper type. Product coverage validation needs
        # a catalogue sample for the configured branch, not a single query.
        kwargs = {
            "flt": ScrapeFilter(),
            "batch_size": 100,
            "max_concurrent": 3,
            "max_retries": 2,
            "base_retry_delay": 0.5,
        }

        # Add branch/store parameters
        if scraper_name == "shufersal" or scraper_name == "machsanei_hashook":
            # No branch/store parameter needed
            pass
        elif scraper_name == "yochananof":
            # Will fetch stores live; no parameter needed
            pass
        elif scraper_name == "ramilevi":
            kwargs["stores"] = config.get("stores")
        else:
            # Stor.ai or ZuZ platforms
            kwargs["branches"] = config.get("branches")

        # Run the scraper
        result = await scrape_fn(**kwargs)

        # Validate result
        duration = time.time() - start_time
        products_total = result.get("products_total", 0)
        errors = result.get("errors", [])
        products_by_store = result.get("products_by_store", {})
        store_ids = list(products_by_store.keys())
        coverage_found, coverage_missing = validate_product_coverage(products_by_store)

        ok = False
        error_msgs = []
        failure_kind = None
        diagnosis = branch_probe_diagnosis

        if products_total < 1:
            if branch_probe_product_bearing is False:
                failure_kind = "empty_branch"
                error_msgs.append("Configured branch returned zero products across all category probes")
            else:
                failure_kind = "empty_result"
                error_msgs.append("No products returned (expected >=1)")
        else:
            if coverage_missing:
                failure_kind = "missing_products"
                error_msgs.append(
                    "Missing required product coverage: "
                    + ", ".join(coverage_missing)
                )

            # Check that sampled products have required fields
            sample_products = []
            for store_id, products in products_by_store.items():
                if products:
                    sample_products.extend(products[:2])

            for prod in sample_products[:3]:
                if not prod.get("name"):
                    error_msgs.append("Missing 'name' field in product")
                if not prod.get("price"):
                    error_msgs.append("Missing 'price' field in product")
                if not prod.get("store_id"):
                    error_msgs.append("Missing 'store_id' field in product")

        if not error_msgs and products_total >= 1:
            ok = True

        if errors:
            error_msgs.extend(errors)

        return ValidationResult(
            scraper=scraper_name,
            ok=ok,
            skipped=False,
            product_count=products_total,
            store_ids_found=store_ids,
            errors=error_msgs,
            duration=duration,
            failure_kind=failure_kind,
            diagnosis=diagnosis,
            coverage_found=coverage_found,
            coverage_missing=coverage_missing,
        )

    except Exception as e:
        duration = time.time() - start_time
        failure_kind = None
        diagnosis = None
        if scraper_name == "yochananof":
            failure_kind, diagnosis = await diagnose_yochananof_cloudflare()
        return ValidationResult(
            scraper=scraper_name,
            ok=False,
            skipped=False,
            product_count=0,
            store_ids_found=[],
            errors=[str(e)],
            duration=duration,
            failure_kind=failure_kind,
            diagnosis=diagnosis,
            coverage_found={},
            coverage_missing=[label for label, _matcher in PRODUCT_COVERAGE_CHECKS],
        )


async def main():
    """Run all scrapers and report results."""
    print("=" * 80)
    print("SUPERMARKET SCRAPER VALIDATION")
    print("=" * 80)
    print(f"Testing time: {datetime.now().isoformat()}")
    print()

    # Run all scrapers concurrently
    results = await asyncio.gather(*[
        run_scraper(name) for name in SCRAPER_CONFIG.keys()
    ])

    # Print summary table
    print(f"{'Scraper':<20} {'Status':<8} {'Products':<12} {'Duration':<10}")
    print("-" * 50)
    for result in results:
        if result.skipped:
            status = "↷ SKIP"
        else:
            status = "✓ PASS" if result.ok else "✗ FAIL"
        print(f"{result.scraper:<20} {status:<8} {result.product_count:<12} {result.duration:>8.2f}s")

    print()
    print("=" * 80)
    print("DETAILED RESULTS")
    print("=" * 80)

    # Show details for failed scrapers
    failed_count = 0
    skipped_count = 0
    for result in results:
        if result.skipped:
            skipped_count += 1
            print()
            print(f"↷ {result.scraper.upper()} SKIPPED")
            print(f"   Reason: {result.diagnosis}")
            continue
        if not result.ok:
            failed_count += 1
            print()
            print(f"❌ {result.scraper.upper()}")
            print(f"   Products: {result.product_count}")
            print(f"   Stores: {result.store_ids_found if result.store_ids_found else 'none'}")
            if result.failure_kind:
                print(f"   Classification: {result.failure_kind}")
            if result.diagnosis:
                print(f"   Diagnosis: {result.diagnosis}")
            if result.coverage_missing:
                print(f"   Missing product checks: {', '.join(result.coverage_missing)}")
            print(f"   Errors:")
            for error in result.errors:
                print(f"     - {error}")

    if failed_count == 0:
        print("✅ All scrapers passed validation!")
    else:
        print()
        print(f"⚠️  {failed_count} scraper(s) failed validation")
    if skipped_count:
        print(f"↷ {skipped_count} scraper(s) skipped")

    print()
    print("=" * 80)

    # Exit with failure code if any scrapers failed
    return 1 if failed_count > 0 else 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
