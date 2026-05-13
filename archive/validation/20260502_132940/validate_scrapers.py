#!/usr/bin/env python3
"""
Live scraper validation script
==============================
Runs each of the 10 supermarket scrapers with a test query ("חלב" = milk)
against one branch/store to verify they're working correctly.

If a scraper fails, optionally uses Playwright to visually inspect the website
for diagnosis (requires: pip install playwright && playwright install chromium).

Run with:
  python3 validate_scrapers.py [--playwright]
"""

import asyncio
import importlib
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

# Check if playwright is available
PLAYWRIGHT_AVAILABLE = False
try:
    import importlib.util
    if importlib.util.find_spec("playwright") is not None:
        PLAYWRIGHT_AVAILABLE = True
except ImportError:
    pass


@dataclass
class ValidationResult:
    scraper: str
    ok: bool
    product_count: int
    store_ids_found: list
    errors: list
    duration: float
    diagnosis: Optional[str] = None


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
        "branches": [{"id": 3086, "name": "אשדוד - Online", "city": "אשדוד", "location": ""}],
    },
    "victory": {
        "url": "https://www.victoryonline.co.il",
        "branches": [{"id": 2527, "name": "ויקטורי אשדוד - Victory Online", "city": "אשדוד", "location": ""}],
    },
    "ybitan": {
        "url": "https://www.ybitan.co.il",
        "branches": [{"id": 1855, "name": "אשדוד- Online", "city": "אשדוד", "location": ""}],
    },
}


async def run_scraper(scraper_name: str) -> ValidationResult:
    """Run a single scraper with milk filter on one branch."""
    config = SCRAPER_CONFIG[scraper_name]
    start_time = time.time()

    try:
        # Import scraper module
        module = importlib.import_module(f"scrapers.{scraper_name}.{scraper_name}")
        scrape_fn = module.scrape

        # Import ScrapeFilter
        from scrapers.common import ScrapeFilter

        # Build kwargs based on scraper type
        kwargs = {
            "flt": ScrapeFilter(name_query="חלב"),  # milk filter
            "batch_size": 20,
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

        ok = False
        error_msgs = []

        if products_total < 1:
            error_msgs.append(f"No products returned (expected ≥1)")
        else:
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
            product_count=products_total,
            store_ids_found=store_ids,
            errors=error_msgs,
            duration=duration,
        )

    except Exception as e:
        duration = time.time() - start_time
        return ValidationResult(
            scraper=scraper_name,
            ok=False,
            product_count=0,
            store_ids_found=[],
            errors=[str(e)],
            duration=duration,
        )


async def playwright_diagnose(scraper_name: str) -> str:
    """Use Playwright to inspect the website if scraper fails."""
    if not PLAYWRIGHT_AVAILABLE:
        return "Playwright not installed (pip install playwright && playwright install chromium)"

    config = SCRAPER_CONFIG[scraper_name]
    url = config["url"]

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="networkidle", timeout=10000)

            # Try to search for milk
            try:
                search_inputs = await page.query_selector_all('input[type="search"], input[placeholder*="חיפוש"], input[placeholder*="search"]')
                if search_inputs:
                    await search_inputs[0].fill("חלב")
                    await search_inputs[0].press("Enter")
                    await page.wait_for_load_state("networkidle")
            except:
                pass  # Search might not be available

            # Take screenshot
            output_dir = Path("output_dir") / "validation"
            output_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = output_dir / f"{scraper_name}_failure.png"
            await page.screenshot(path=str(screenshot_path))

            page_title = await page.title()
            await browser.close()

            return f"Screenshot saved to {screenshot_path} (title: {page_title})"
    except Exception as e:
        return f"Playwright diagnosis failed: {str(e)}"


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
        status = "✓ PASS" if result.ok else "✗ FAIL"
        print(f"{result.scraper:<20} {status:<8} {result.product_count:<12} {result.duration:>8.2f}s")

    print()
    print("=" * 80)
    print("DETAILED RESULTS")
    print("=" * 80)

    # Show details for failed scrapers
    failed_count = 0
    for result in results:
        if not result.ok:
            failed_count += 1
            print()
            print(f"❌ {result.scraper.upper()}")
            print(f"   Products: {result.product_count}")
            print(f"   Stores: {result.store_ids_found if result.store_ids_found else 'none'}")
            print(f"   Errors:")
            for error in result.errors:
                print(f"     - {error}")

            # Run playwright diagnosis
            if "--playwright" in sys.argv:
                print(f"   Running Playwright diagnosis...")
                diagnosis = await playwright_diagnose(result.scraper)
                print(f"   {diagnosis}")

    if failed_count == 0:
        print("✅ All scrapers passed validation!")
    else:
        print()
        print(f"⚠️  {failed_count} scraper(s) failed validation")
        print()
        print("To diagnose failures with Playwright, run:")
        print("  python3 validate_scrapers.py --playwright")
        print()
        print("First, install Playwright:")
        print("  pip install playwright")
        print("  playwright install chromium")

    print()
    print("=" * 80)

    # Exit with failure code if any scrapers failed
    return 1 if failed_count > 0 else 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
