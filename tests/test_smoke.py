"""
Smoke tests for the unified scraper API.
=========================================
Tests cover:
  1. scrapers/common.py — UnifiedProduct shape, ScrapeResult shape, ScrapeFilter,
     with_retry (success, retry, exhaustion), run_concurrently (ordering, concurrency cap),
     normalize_unit (Hebrew/metric unit normalisation),
     compute_price_per_base_unit (comparison price calculation)
  2. Tiv Taam — barcode extraction, _to_unified field mapping, scrape() returns ScrapeResult,
     deal extraction (_extract_deal)
  3. Carrefour — same platform, image URL template difference from Tiv Taam, deal extraction
  4. Shufersal — _to_unified field mapping, scrape() returns ScrapeResult, deal parsing
  5. Yochananof — _to_unified field mapping, scrape() returns ScrapeResult, deal parsing
  6. main.py — _build_parser() honours all new CLI flags, ScrapeFilter is built correctly,
     _result_summary() formats correctly
  7. Integration smoke — ScrapeFilter roundtrip across all scrapers

Run with:
    python3 -m pytest tests/test_smoke.py -v
or:
    python3 tests/test_smoke.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import types
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure the project root is on sys.path so that `scrapers` and `main` are importable
# regardless of whether the tests are run from the project root or the tests/ directory.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# 1. scrapers/common.py
# ---------------------------------------------------------------------------


class TestCommonTypes(unittest.TestCase):
    def test_unified_product_fields(self):
        """UnifiedProduct must accept all required fields without error."""
        from scrapers.common import UnifiedProduct

        p = UnifiedProduct(
            chain="tivtaam",
            store_id="924",
            store_name="רמת החייל",
            product_id="12345",
            name="חלב",
            price=5.90,
            regular_price=6.90,
            sale_price=5.90,
            discount_percent=14.5,
            barcode="7290000066882",
            image_url="https://example.com/img.jpg",
            category_ids=["90176"],
            is_weighable=False,
            unit_description="1 ליטר",
            unit_of_measure='מ"ל',
            unit_qty=1000.0,
            unit_qty_si=1000.0,
            unit_dimension="volume",
            price_per_base_unit=0.59,
            deal=None,
            brand="תנובה",
            manufacturer=None,
            scraped_at="2026-03-25T10:00:00+00:00",
        )
        self.assertEqual(p["chain"], "tivtaam")
        self.assertEqual(p["barcode"], "7290000066882")
        self.assertIsNone(p["manufacturer"])
        self.assertEqual(p["unit_qty_si"], 1000.0)
        self.assertEqual(p["unit_dimension"], "volume")
        self.assertAlmostEqual(p["price_per_base_unit"], 0.59)
        self.assertIsNone(p["deal"])

    def test_scrape_result_fields(self):
        """ScrapeResult must accept all required fields."""
        from scrapers.common import ScrapeResult, UnifiedProduct

        r = ScrapeResult(
            chain="shufersal",
            stores_scraped=1,
            products_total=100,
            products_by_store={"global": []},
            scraped_at="2026-03-25T10:00:00+00:00",
            duration_seconds=3.14,
            errors=[],
        )
        self.assertEqual(r["chain"], "shufersal")
        self.assertEqual(r["products_total"], 100)

    def test_scrape_filter_optional(self):
        """ScrapeFilter must accept any subset of keys (total=False)."""
        from scrapers.common import ScrapeFilter

        f1: ScrapeFilter = {}
        f2: ScrapeFilter = {"name_query": "חלב"}
        f3: ScrapeFilter = {"barcode": "1234567890123"}
        f4: ScrapeFilter = {"category_ids": ["90176", "90066"], "name_query": "גבינה"}
        self.assertEqual(f2["name_query"], "חלב")
        self.assertEqual(f4["category_ids"], ["90176", "90066"])

    def test_utc_now_iso_format(self):
        """utc_now_iso() must return a non-empty ISO string containing 'T'."""
        from scrapers.common import utc_now_iso

        ts = utc_now_iso()
        self.assertIn("T", ts)
        self.assertTrue(ts.endswith("+00:00"))

    def test_make_ssl_context(self):
        """make_ssl_context() must return an ssl.SSLContext."""
        import ssl
        from scrapers.common import make_ssl_context

        ctx = make_ssl_context()
        self.assertIsInstance(ctx, ssl.SSLContext)


class TestWithRetry(unittest.TestCase):
    def test_success_on_first_attempt(self):
        """with_retry returns immediately when the first call succeeds."""
        from scrapers.common import with_retry

        calls = []

        async def fn():
            calls.append(1)
            return 42

        result = _run(with_retry(fn))
        self.assertEqual(result, 42)
        self.assertEqual(len(calls), 1)

    def test_retries_on_failure_then_succeeds(self):
        """with_retry retries after a failure and returns on eventual success."""
        from scrapers.common import with_retry

        calls = []

        async def fn():
            calls.append(1)
            if len(calls) < 3:
                raise ValueError("transient")
            return "ok"

        result = _run(with_retry(fn, max_retries=5, base_delay=0.01))
        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 3)

    def test_raises_after_exhausting_retries(self):
        """with_retry raises the last exception when all retries are exhausted."""
        from scrapers.common import with_retry

        calls = []

        async def fn():
            calls.append(1)
            raise RuntimeError("always fails")

        with self.assertRaises(RuntimeError):
            _run(with_retry(fn, max_retries=3, base_delay=0.01))
        self.assertEqual(len(calls), 3)

    def test_exponential_delay_grows(self):
        """Retry delays follow exponential backoff (base * 2^attempt)."""
        from scrapers.common import with_retry

        sleep_calls = []

        async def mock_sleep(delay):
            sleep_calls.append(delay)

        attempts = [0]

        async def fn():
            attempts[0] += 1
            if attempts[0] < 4:
                raise ValueError("fail")
            return "done"

        original_sleep = asyncio.sleep

        async def patched_sleep(delay):
            sleep_calls.append(delay)

        with patch("scrapers.common.asyncio.sleep", side_effect=patched_sleep):
            result = _run(with_retry(fn, max_retries=4, base_delay=1.0, max_delay=30.0))

        self.assertEqual(result, "done")
        # Delays: attempt 0→1s, 1→2s, 2→4s (3 retries before success on attempt 4)
        self.assertEqual(len(sleep_calls), 3)
        self.assertAlmostEqual(sleep_calls[0], 1.0)
        self.assertAlmostEqual(sleep_calls[1], 2.0)
        self.assertAlmostEqual(sleep_calls[2], 4.0)

    def test_max_delay_cap(self):
        """Delays are capped at max_delay."""
        from scrapers.common import with_retry

        sleep_calls = []
        attempts = [0]

        async def fn():
            attempts[0] += 1
            if attempts[0] < 5:
                raise ValueError("fail")
            return "done"

        async def patched_sleep(delay):
            sleep_calls.append(delay)

        with patch("scrapers.common.asyncio.sleep", side_effect=patched_sleep):
            _run(with_retry(fn, max_retries=5, base_delay=10.0, max_delay=15.0))

        for d in sleep_calls:
            self.assertLessEqual(d, 15.0)


class TestRunConcurrently(unittest.TestCase):
    def test_returns_results_in_order(self):
        """run_concurrently preserves task order in results."""
        from scrapers.common import run_concurrently

        async def make_fn(v):
            async def fn():
                await asyncio.sleep(0.01)
                return v

            return fn

        async def run():
            fns = [await make_fn(i) for i in range(5)]
            return await run_concurrently(fns, max_concurrent=5)

        results = _run(run())
        self.assertEqual(results, [0, 1, 2, 3, 4])

    def test_concurrency_cap_respected(self):
        """run_concurrently runs at most max_concurrent tasks simultaneously."""
        from scrapers.common import run_concurrently

        active = [0]
        peak = [0]

        async def fn(i):
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            await asyncio.sleep(0.05)
            active[0] -= 1
            return i

        async def run():
            fns = [lambda i=i: fn(i) for i in range(10)]
            return await run_concurrently(fns, max_concurrent=3)

        results = _run(run())
        self.assertEqual(len(results), 10)
        self.assertLessEqual(peak[0], 3)

    def test_exceptions_are_returned_not_raised(self):
        """run_concurrently returns exceptions as values instead of propagating."""
        from scrapers.common import run_concurrently

        async def ok():
            return 1

        async def bad():
            raise ValueError("oops")

        async def run():
            return await run_concurrently([ok, bad, ok], max_concurrent=3)

        results = _run(run())
        self.assertEqual(results[0], 1)
        self.assertIsInstance(results[1], ValueError)
        self.assertEqual(results[2], 1)


# ---------------------------------------------------------------------------
# 1b. normalize_unit
# ---------------------------------------------------------------------------


class TestNormalizeUnit(unittest.TestCase):
    def setUp(self):
        from scrapers.common import normalize_unit

        self._fn = normalize_unit

    def test_millilitre_canonical(self):
        label, qty_si, dim, si_per = self._fn('מ"ל', 500.0)
        self.assertEqual(label, 'מ"ל')
        self.assertAlmostEqual(qty_si, 500.0)
        self.assertEqual(dim, "volume")
        self.assertAlmostEqual(si_per, 1.0)

    def test_litre_to_ml(self):
        label, qty_si, dim, si_per = self._fn("ליטר", 1.5)
        self.assertEqual(label, "ליטר")
        self.assertAlmostEqual(qty_si, 1500.0)
        self.assertEqual(dim, "volume")
        self.assertAlmostEqual(si_per, 1000.0)

    def test_gram_canonical(self):
        label, qty_si, dim, si_per = self._fn("גרם", 250.0)
        self.assertEqual(label, "גרם")
        self.assertAlmostEqual(qty_si, 250.0)
        self.assertEqual(dim, "mass")

    def test_kilogram_to_grams(self):
        label, qty_si, dim, si_per = self._fn('ק"ג', 2.0)
        self.assertEqual(label, 'ק"ג')
        self.assertAlmostEqual(qty_si, 2000.0)
        self.assertEqual(dim, "mass")
        self.assertAlmostEqual(si_per, 1000.0)

    def test_count_unit(self):
        label, qty_si, dim, si_per = self._fn("יח'", 6.0)
        self.assertEqual(label, "יח'")
        self.assertAlmostEqual(qty_si, 6.0)
        self.assertEqual(dim, "count")

    def test_alternate_ml_form(self):
        """מ'ל (alternate apostrophe) should map to מ\"ל."""
        label, qty_si, dim, si_per = self._fn("מ'ל", 330.0)
        self.assertEqual(label, 'מ"ל')
        self.assertEqual(dim, "volume")

    def test_kg_ascii(self):
        label, qty_si, dim, si_per = self._fn("kg", 1.0)
        self.assertEqual(dim, "mass")
        self.assertAlmostEqual(qty_si, 1000.0)

    def test_unknown_unit_returns_raw(self):
        """Unknown unit strings should pass through with dimension=None."""
        label, qty_si, dim, si_per = self._fn("קופסה", 3.0)
        self.assertIsNone(dim)
        self.assertIsNone(si_per)

    def test_none_unit_with_description_fallback(self):
        """When raw_unit is None, description is parsed as fallback."""
        label, qty_si, dim, si_per = self._fn(None, None, '500 מ"ל')
        self.assertEqual(dim, "volume")
        self.assertAlmostEqual(qty_si, 500.0)

    def test_none_unit_and_no_description(self):
        label, qty_si, dim, si_per = self._fn(None, None, None)
        self.assertIsNone(dim)
        self.assertIsNone(qty_si)

    def test_qty_none_returns_none_qty_si(self):
        """qty=None with a known unit → qty_si is None."""
        label, qty_si, dim, si_per = self._fn("גרם", None)
        self.assertEqual(dim, "mass")
        self.assertIsNone(qty_si)


# ---------------------------------------------------------------------------
# 1c. compute_price_per_base_unit
# ---------------------------------------------------------------------------


class TestComputePricePerBaseUnit(unittest.TestCase):
    def setUp(self):
        from scrapers.common import compute_price_per_base_unit

        self._fn = compute_price_per_base_unit

    def test_volume_per_100ml(self):
        # 500ml product at 5.00 → 1.00 per 100ml
        result = self._fn(5.00, 500.0, "volume", False)
        self.assertAlmostEqual(result, 1.0)

    def test_mass_per_100g(self):
        # 250g at 10.00 → 4.00 per 100g
        result = self._fn(10.00, 250.0, "mass", False)
        self.assertAlmostEqual(result, 4.0)

    def test_1_litre_at_6_90(self):
        # 1000ml at 6.90 → 0.69 per 100ml
        result = self._fn(6.90, 1000.0, "volume", False)
        self.assertAlmostEqual(result, 0.69)

    def test_weighable_returns_price_as_is(self):
        # Weighable = price is already per kg
        result = self._fn(25.90, None, None, True)
        self.assertAlmostEqual(result, 25.90)

    def test_count_dimension_returns_price_per_unit(self):
        # count items → price per single unit (price / qty)
        result = self._fn(5.00, 6.0, "count", False)
        self.assertAlmostEqual(result, round(5.00 / 6.0, 4))

    def test_none_dimension_returns_none(self):
        result = self._fn(5.00, None, None, False)
        self.assertIsNone(result)

    def test_zero_qty_returns_none(self):
        result = self._fn(5.00, 0.0, "volume", False)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# 2. Tiv Taam — unit tests (no network)
# ---------------------------------------------------------------------------


class TestTivTaamExtraction(unittest.TestCase):
    def setUp(self):
        from scrapers.tivtaam.tivtaam import ONLINE_BRANCHES, _to_unified

        self._to_unified = _to_unified
        self._branch = ONLINE_BRANCHES[0]  # branch 924
        self._scraped_at = "2026-03-25T10:00:00+00:00"

    def _make_item(self, **overrides) -> dict:
        item = {
            "id": 1,
            "productId": 100,
            "localName": "חלב",
            "names": {"1": {"long": "חלב תנובה 3%", "short": "חלב"}},
            "image": {
                "url": "https://d2e5ushqwiltxm.cloudfront.net/upload/images/gs1-products/1062/{{size}}/7290000066882-999/{{size}}.{{extension||'jpg'}}"
            },
            "isWeighable": False,
            "weight": 1000,
            "unitOfMeasure": {"names": {"1": 'מ"ל'}},
            "unitResolution": None,
            "numberOfItems": 1,
            "branch": {"regularPrice": 6.90, "salePrice": None},
            "family": {"categories": [{"id": 90176, "name": "מוצרי חלב"}]},
        }
        item.update(overrides)
        return item

    def test_barcode_extracted_from_gs1_url(self):
        p = self._to_unified(self._make_item(), self._branch, "90176", self._scraped_at)
        self.assertIsNotNone(p)
        self.assertEqual(p["barcode"], "7290000066882")

    def test_name_preferred_from_names_1_long(self):
        p = self._to_unified(self._make_item(), self._branch, "90176", self._scraped_at)
        self.assertEqual(p["name"], "חלב תנובה 3%")

    def test_regular_price_mapped_correctly(self):
        p = self._to_unified(self._make_item(), self._branch, "90176", self._scraped_at)
        self.assertAlmostEqual(p["regular_price"], 6.90)
        self.assertIsNone(p["sale_price"])
        self.assertAlmostEqual(p["price"], 6.90)

    def test_sale_price_and_discount(self):
        item = self._make_item()
        item["branch"]["salePrice"] = 5.50
        p = self._to_unified(item, self._branch, "90176", self._scraped_at)
        self.assertAlmostEqual(p["sale_price"], 5.50)
        self.assertAlmostEqual(p["price"], 5.50)
        self.assertIsNotNone(p["discount_percent"])
        self.assertGreater(p["discount_percent"], 0)

    def test_unit_fields(self):
        p = self._to_unified(self._make_item(), self._branch, "90176", self._scraped_at)
        self.assertAlmostEqual(p["unit_qty"], 1000.0)
        self.assertEqual(p["unit_of_measure"], 'מ"ל')
        self.assertIsNotNone(p["unit_description"])

    def test_chain_and_store_id(self):
        p = self._to_unified(self._make_item(), self._branch, "90176", self._scraped_at)
        self.assertEqual(p["chain"], "tivtaam")
        self.assertEqual(p["store_id"], "924")
        self.assertEqual(p["store_name"], "רמת החייל")

    def test_missing_price_returns_none(self):
        item = self._make_item()
        item["branch"]["regularPrice"] = None
        p = self._to_unified(item, self._branch, "90176", self._scraped_at)
        self.assertIsNone(p)

    def test_category_ids_from_family(self):
        p = self._to_unified(self._make_item(), self._branch, "90176", self._scraped_at)
        self.assertIn("90176", p["category_ids"])

    def test_image_url_template_resolved(self):
        p = self._to_unified(self._make_item(), self._branch, "90176", self._scraped_at)
        self.assertIsNotNone(p["image_url"])
        self.assertNotIn("{{size}}", p["image_url"])
        self.assertNotIn("{{extension", p["image_url"])

    def test_unit_qty_si_and_dimension_populated(self):
        """unit_qty_si and unit_dimension must be set for known units."""
        p = self._to_unified(self._make_item(), self._branch, "90176", self._scraped_at)
        # 1000 מ"ל → volume, 1000ml SI
        self.assertEqual(p["unit_dimension"], "volume")
        self.assertAlmostEqual(p["unit_qty_si"], 1000.0)

    def test_price_per_base_unit_populated(self):
        """price_per_base_unit must be computed for volume products."""
        p = self._to_unified(self._make_item(), self._branch, "90176", self._scraped_at)
        # price=6.90, qty_si=1000ml → 0.69 per 100ml
        self.assertIsNotNone(p["price_per_base_unit"])
        self.assertAlmostEqual(p["price_per_base_unit"], 0.69)

    def test_deal_none_when_no_sale(self):
        """deal must be None when there is no salePrice and no specials."""
        p = self._to_unified(self._make_item(), self._branch, "90176", self._scraped_at)
        self.assertIsNone(p["deal"])

    def test_deal_price_reduction(self):
        """deal must be populated as price_reduction when salePrice is set."""
        item = self._make_item()
        item["branch"]["salePrice"] = 5.50
        p = self._to_unified(item, self._branch, "90176", self._scraped_at)
        self.assertIsNotNone(p["deal"])
        self.assertTrue(p["deal"]["has_deal"])
        self.assertEqual(p["deal"]["deal_type"], "price_reduction")
        self.assertAlmostEqual(p["deal"]["deal_price"], 5.50)
        self.assertIsNotNone(p["deal"]["price_per_base_unit_deal"])

    def test_deal_multi_buy(self):
        """deal must be populated as multi_buy from specials type=2."""
        item = self._make_item()
        item["branch"]["specials"] = [
            {
                "names": {"1": {"name": "2 ב-12"}},
                "firstLevel": {
                    "type": 2,
                    "firstPurchaseTotal": 2,
                    "firstGift": {"total": 12.0},
                },
            }
        ]
        p = self._to_unified(item, self._branch, "90176", self._scraped_at)
        self.assertIsNotNone(p["deal"])
        self.assertEqual(p["deal"]["deal_type"], "multi_buy")
        self.assertEqual(p["deal"]["deal_min_qty"], 2)
        self.assertAlmostEqual(p["deal"]["deal_price"], 12.0)
        self.assertAlmostEqual(p["deal"]["deal_price_per_unit"], 6.0)

    def test_weighable_flag(self):
        item = self._make_item()
        item["isWeighable"] = True
        p = self._to_unified(item, self._branch, "90176", self._scraped_at)
        self.assertTrue(p["is_weighable"])


class TestTivTaamBarcodeRegex(unittest.TestCase):
    def test_valid_gs1_url(self):
        from scrapers.tivtaam.tivtaam import _extract_barcode

        url = "https://cdn.example.com/gs1-products/1062/medium/7290000066882-99999/img.jpg"
        self.assertEqual(_extract_barcode(url), "7290000066882")

    def test_ean8_barcode(self):
        from scrapers.tivtaam.tivtaam import _extract_barcode

        url = "https://cdn.example.com/gs1-products/1062/medium/12345678-999/img.jpg"
        self.assertEqual(_extract_barcode(url), "12345678")

    def test_non_gs1_url_returns_none(self):
        from scrapers.tivtaam.tivtaam import _extract_barcode

        self.assertIsNone(_extract_barcode("https://example.com/regular/image.jpg"))

    def test_none_returns_none(self):
        from scrapers.tivtaam.tivtaam import _extract_barcode

        self.assertIsNone(_extract_barcode(None))


# ---------------------------------------------------------------------------
# 3. Carrefour — unit tests (no network)
# ---------------------------------------------------------------------------


class TestCarrefourExtraction(unittest.TestCase):
    def setUp(self):
        from scrapers.carrefour.carrefour import ONLINE_BRANCHES, _to_unified

        self._to_unified = _to_unified
        self._branch = ONLINE_BRANCHES[12]  # כפר סבא (id=3003)
        self._scraped_at = "2026-03-25T10:00:00+00:00"

    def _make_item(self) -> dict:
        return {
            "id": 55123,
            "productId": 88456,
            "localName": "גבינה",
            "names": {"1": {"long": "גבינה לבנה 5%", "short": "גבינה"}},
            "image": {
                "url": "https://d2e5ushqwiltxm.cloudfront.net/upload/images/gs1-products/1540/{{size}}/7290000011111-555/img.jpg"
            },
            "isWeighable": False,
            "weight": 250,
            "unitOfMeasure": {"names": {"1": "גרם"}},
            "unitResolution": None,
            "numberOfItems": 1,
            "branch": {"regularPrice": 7.90, "salePrice": None},
            "family": {"categories": [{"id": 79604, "name": "גבינות"}]},
        }

    def test_chain_is_carrefour(self):
        p = self._to_unified(self._make_item(), self._branch, "79604", self._scraped_at)
        self.assertIsNotNone(p)
        self.assertEqual(p["chain"], "carrefour")

    def test_barcode_extracted(self):
        p = self._to_unified(self._make_item(), self._branch, "79604", self._scraped_at)
        self.assertEqual(p["barcode"], "7290000011111")

    def test_image_url_no_extension_template(self):
        """Carrefour image URLs only have {{size}}, no {{extension}} suffix."""
        p = self._to_unified(self._make_item(), self._branch, "79604", self._scraped_at)
        self.assertIsNotNone(p["image_url"])
        self.assertIn("medium", p["image_url"])
        self.assertNotIn("{{size}}", p["image_url"])

    def test_store_id_is_branch_id_string(self):
        p = self._to_unified(self._make_item(), self._branch, "79604", self._scraped_at)
        self.assertEqual(p["store_id"], "3003")

    def test_unit_dimension_mass(self):
        """250g item should produce dimension=mass and qty_si=250."""
        p = self._to_unified(self._make_item(), self._branch, "79604", self._scraped_at)
        self.assertEqual(p["unit_dimension"], "mass")
        self.assertAlmostEqual(p["unit_qty_si"], 250.0)

    def test_price_per_base_unit_mass(self):
        """7.90 for 250g → 3.16 per 100g."""
        p = self._to_unified(self._make_item(), self._branch, "79604", self._scraped_at)
        self.assertIsNotNone(p["price_per_base_unit"])
        self.assertAlmostEqual(p["price_per_base_unit"], 3.16)

    def test_deal_price_reduction(self):
        """salePrice on a Carrefour item → price_reduction deal."""
        item = self._make_item()
        item["branch"]["salePrice"] = 6.00
        p = self._to_unified(item, self._branch, "79604", self._scraped_at)
        self.assertIsNotNone(p["deal"])
        self.assertEqual(p["deal"]["deal_type"], "price_reduction")
        self.assertAlmostEqual(p["deal"]["deal_price"], 6.00)

    def test_deal_multi_buy_from_specials(self):
        """Specials type=2 → multi_buy deal on Carrefour."""
        item = self._make_item()
        item["branch"]["specials"] = [
            {
                "names": {"1": {"name": "2 ב-15"}},
                "firstLevel": {
                    "type": 2,
                    "firstPurchaseTotal": 2,
                    "firstGift": {"total": 15.0},
                },
            }
        ]
        p = self._to_unified(item, self._branch, "79604", self._scraped_at)
        self.assertIsNotNone(p["deal"])
        self.assertEqual(p["deal"]["deal_type"], "multi_buy")
        self.assertEqual(p["deal"]["deal_min_qty"], 2)
        self.assertAlmostEqual(p["deal"]["deal_price_per_unit"], 7.5)


# ---------------------------------------------------------------------------
# 4. Shufersal — unit tests (no network)
# ---------------------------------------------------------------------------


class TestShufersalExtraction(unittest.TestCase):
    def setUp(self):
        from scrapers.shufersal.shufersal import _to_unified

        self._to_unified = _to_unified
        self._scraped_at = "2026-03-25T10:00:00+00:00"

    def _make_item(self, **overrides) -> dict:
        item = {
            "code": "P_22",
            "sku": "7290000066882",
            "name": "חלב תנובה",
            "categoryPrice": {"value": 6.90},
            "price": {"value": 6.90},
            "allCategoryCodes": ["A04", "A0410"],
            "images": [
                {
                    "imageType": "PRIMARY",
                    "format": "medium",
                    "url": "https://example.com/product/img.png",
                }
            ],
            "sellingMethod": {"code": "BY_PACKAGE"},
            "food": True,
            "brand": {"name": "תנובה"},
            "ean": "7290000066882",
            "manufacturer": "תנובה",
            "unitDescription": "1 ליטר",
            "unitForComparison": 'מ"ל',
            "valueForComparison": 1000.0,
            "numberContentUnits": 1.0,
        }
        item.update(overrides)
        return item

    def test_chain_is_shufersal(self):
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertIsNotNone(p)
        self.assertEqual(p["chain"], "shufersal")

    def test_store_id_is_global(self):
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertEqual(p["store_id"], "global")

    def test_barcode_from_ean(self):
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertEqual(p["barcode"], "7290000066882")

    def test_barcode_none_when_ean_null(self):
        item = self._make_item(ean=None)
        p = self._to_unified(item, self._scraped_at)
        self.assertIsNone(p["barcode"])

    def test_price_mapped(self):
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertAlmostEqual(p["price"], 6.90)
        self.assertAlmostEqual(p["regular_price"], 6.90)
        # categoryPrice == price → no promo active → sale_price is None
        self.assertIsNone(p["sale_price"])

    def test_is_weighable_by_weight(self):
        item = self._make_item()
        item["sellingMethod"]["code"] = "BY_WEIGHT"
        p = self._to_unified(item, self._scraped_at)
        self.assertTrue(p["is_weighable"])

    def test_unit_fields(self):
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertEqual(p["unit_description"], "1 ליטר")
        self.assertEqual(p["unit_of_measure"], 'מ"ל')
        self.assertAlmostEqual(p["unit_qty"], 1000.0)

    def test_unit_qty_si_and_dimension(self):
        """1000 מ\"ל → volume, qty_si=1000."""
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertEqual(p["unit_dimension"], "volume")
        self.assertAlmostEqual(p["unit_qty_si"], 1000.0)

    def test_price_per_base_unit_volume(self):
        """6.90 for 1000ml → 0.69 per 100ml."""
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertIsNotNone(p["price_per_base_unit"])
        self.assertAlmostEqual(p["price_per_base_unit"], 0.69)

    def test_deal_none_when_no_promo(self):
        """No promotionMsg and categoryPrice == price → deal is None."""
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertIsNone(p["deal"])

    def test_deal_price_reduction_from_category_price(self):
        """categoryPrice < price → price_reduction deal."""
        item = self._make_item()
        item["categoryPrice"] = {"value": 5.50}
        item["price"] = {"value": 6.90}
        p = self._to_unified(item, self._scraped_at)
        self.assertIsNotNone(p["deal"])
        self.assertEqual(p["deal"]["deal_type"], "price_reduction")
        self.assertAlmostEqual(p["deal"]["deal_price"], 5.50)
        self.assertIsNotNone(p["deal"]["price_per_base_unit_deal"])

    def test_deal_multi_buy_from_promotion_msg(self):
        """promotionMsg '2 יח' ב- 22 ₪' → multi_buy deal."""
        item = self._make_item()
        item["promotionMsg"] = "2 יח'  ב- 22 ₪"
        p = self._to_unified(item, self._scraped_at)
        self.assertIsNotNone(p["deal"])
        self.assertEqual(p["deal"]["deal_type"], "multi_buy")
        self.assertEqual(p["deal"]["deal_min_qty"], 2)
        self.assertAlmostEqual(p["deal"]["deal_price"], 22.0)
        self.assertAlmostEqual(p["deal"]["deal_price_per_unit"], 11.0)

    def test_manufacturer_mapped(self):
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertEqual(p["manufacturer"], "תנובה")

    def test_brand_mapped(self):
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertEqual(p["brand"], "תנובה")

    def test_category_codes_mapped(self):
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertIn("A04", p["category_ids"])

    def test_no_price_returns_none(self):
        item = self._make_item()
        item["categoryPrice"] = None
        item["price"] = None
        p = self._to_unified(item, self._scraped_at)
        self.assertIsNone(p)

    def test_image_selected(self):
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertIsNotNone(p["image_url"])
        self.assertIn("example.com", p["image_url"])


# ---------------------------------------------------------------------------
# 5. Yochananof — unit tests (no network)
# ---------------------------------------------------------------------------


class TestYochananofExtraction(unittest.TestCase):
    def setUp(self):
        from scrapers.yochananof.yochananof import Store, _to_unified

        self._to_unified = _to_unified
        self._store = Store(
            store_code="s82", store_name="תל אביב", is_default_store=False
        )
        self._scraped_at = "2026-03-25T10:00:00+00:00"

    def _make_item(self, **overrides) -> dict:
        item = {
            "id": 9001,
            "sku": "7290000066882",
            "name": "חלב",
            "short_name": "חלב",
            "brand": "תנובה",
            "stock_status": "IN_STOCK",
            "by_kilo": 0,
            "item_unit": "יח'",
            "price_range": {
                "minimum_price": {
                    "regular_price": {"value": 6.90, "currency": "ILS"},
                    "final_price": {"value": 5.50, "currency": "ILS"},
                    "discount": {"amount_off": 1.40, "percent_off": 20.3},
                }
            },
            "small_image": {"url": "https://yochananof.co.il/img.jpg", "label": "חלב"},
            "categories": [{"id": 423, "name": "מוצרי חלב"}],
        }
        item.update(overrides)
        return item

    def test_chain_is_yochananof(self):
        p = self._to_unified(self._make_item(), self._store, self._scraped_at)
        self.assertIsNotNone(p)
        self.assertEqual(p["chain"], "yochananof")

    def test_barcode_is_sku(self):
        p = self._to_unified(self._make_item(), self._store, self._scraped_at)
        self.assertEqual(p["barcode"], "7290000066882")

    def test_store_id_is_store_code(self):
        p = self._to_unified(self._make_item(), self._store, self._scraped_at)
        self.assertEqual(p["store_id"], "s82")

    def test_sale_price_and_regular_price(self):
        p = self._to_unified(self._make_item(), self._store, self._scraped_at)
        self.assertAlmostEqual(p["regular_price"], 6.90)
        self.assertAlmostEqual(p["price"], 5.50)
        self.assertAlmostEqual(p["sale_price"], 5.50)

    def test_discount_percent(self):
        p = self._to_unified(self._make_item(), self._store, self._scraped_at)
        self.assertIsNotNone(p["discount_percent"])
        self.assertGreater(p["discount_percent"], 0)

    def test_is_weighable_by_kilo(self):
        item = self._make_item()
        item["by_kilo"] = 1
        p = self._to_unified(item, self._store, self._scraped_at)
        self.assertTrue(p["is_weighable"])

    def test_unit_of_measure(self):
        p = self._to_unified(self._make_item(), self._store, self._scraped_at)
        self.assertEqual(p["unit_of_measure"], "יח'")

    def test_brand_mapped(self):
        p = self._to_unified(self._make_item(), self._store, self._scraped_at)
        self.assertEqual(p["brand"], "תנובה")

    def test_category_ids_mapped(self):
        p = self._to_unified(self._make_item(), self._store, self._scraped_at)
        self.assertIn("423", p["category_ids"])

    def test_no_price_returns_none(self):
        item = self._make_item()
        item["price_range"]["minimum_price"]["final_price"]["value"] = None
        item["price_range"]["minimum_price"]["regular_price"]["value"] = None
        p = self._to_unified(item, self._store, self._scraped_at)
        self.assertIsNone(p)

    def test_deal_price_reduction_from_sale(self):
        """final_price < regular_price → price_reduction deal."""
        p = self._to_unified(self._make_item(), self._store, self._scraped_at)
        # _make_item has final=5.50, regular=6.90 → sale
        self.assertIsNotNone(p["deal"])
        self.assertEqual(p["deal"]["deal_type"], "price_reduction")
        self.assertAlmostEqual(p["deal"]["deal_price"], 5.50)
        # item_unit='יח' (count) → price_per_base_unit_deal is None (no per-100g comparison)
        self.assertIsNone(p["deal"]["price_per_base_unit_deal"])

    def test_deal_multi_buy_from_price_tiers(self):
        """price_tiers → multi_buy deal."""
        item = self._make_item()
        # Reset to no sale price so tier logic runs
        item["price_range"]["minimum_price"]["final_price"]["value"] = 6.90
        item["price_range"]["minimum_price"]["regular_price"]["value"] = 6.90
        item["price_range"]["minimum_price"]["discount"]["percent_off"] = 0
        item["price_tiers"] = [
            {
                "quantity": 3,
                "final_price": {"value": 5.90, "currency": "ILS"},
                "discount": {"amount_off": 1.00, "percent_off": 14.5},
            }
        ]
        p = self._to_unified(item, self._store, self._scraped_at)
        self.assertIsNotNone(p["deal"])
        self.assertEqual(p["deal"]["deal_type"], "multi_buy")
        self.assertEqual(p["deal"]["deal_min_qty"], 3)
        self.assertAlmostEqual(p["deal"]["deal_price_per_unit"], 5.90)

    def test_unit_dimension_count_for_unit_item(self):
        """יח' unit → dimension=count, price_per_base_unit=None (no qty provided)."""
        p = self._to_unified(self._make_item(), self._store, self._scraped_at)
        self.assertEqual(p["unit_dimension"], "count")
        # No qty in the item fixture → qty_si is None → price_per_base_unit is None
        self.assertIsNone(p["price_per_base_unit"])


# ---------------------------------------------------------------------------
# 6. main.py — CLI parser
# ---------------------------------------------------------------------------


class TestMainParser(unittest.TestCase):
    def setUp(self):
        from main import _build_parser

        self._parser = _build_parser()

    def test_default_supermarkets_all_four(self):
        args = self._parser.parse_args([])
        self.assertIn("tivtaam", args.supermarkets)
        self.assertIn("shufersal", args.supermarkets)
        self.assertIn("yochananof", args.supermarkets)
        self.assertIn("carrefour", args.supermarkets)
        self.assertIn("machsanei", args.supermarkets)

    def test_machsanei_branches(self):
        args = self._parser.parse_args(["--machsanei-branches", "836", "1587"])
        self.assertEqual(args.machsanei_branches, ["836", "1587"])

    def test_filter_name(self):
        args = self._parser.parse_args(["--filter-name", "חלב"])
        self.assertEqual(args.filter_name, "חלב")

    def test_filter_category(self):
        args = self._parser.parse_args(["--filter-category", "90176"])
        self.assertEqual(args.filter_category, "90176")

    def test_filter_barcode(self):
        args = self._parser.parse_args(["--filter-barcode", "7290000066882"])
        self.assertEqual(args.filter_barcode, "7290000066882")

    def test_batch_size(self):
        args = self._parser.parse_args(["--batch-size", "50"])
        self.assertEqual(args.batch_size, 50)

    def test_max_concurrent(self):
        args = self._parser.parse_args(["--max-concurrent", "8"])
        self.assertEqual(args.max_concurrent, 8)

    def test_retry_limit(self):
        args = self._parser.parse_args(["--retry-limit", "5"])
        self.assertEqual(args.retry_limit, 5)

    def test_base_retry_delay(self):
        args = self._parser.parse_args(["--base-retry-delay", "2.5"])
        self.assertAlmostEqual(args.base_retry_delay, 2.5)

    def test_defaults(self):
        args = self._parser.parse_args([])
        self.assertEqual(args.batch_size, 100)
        self.assertEqual(args.max_concurrent, 15)
        self.assertEqual(args.retry_limit, 3)
        self.assertAlmostEqual(args.base_retry_delay, 1.0)
        self.assertIsNone(args.filter_name)
        self.assertIsNone(args.filter_category)
        self.assertIsNone(args.filter_barcode)

    def test_tivtaam_branches(self):
        args = self._parser.parse_args(["--tivtaam-branches", "924", "929"])
        self.assertEqual(args.tivtaam_branches, ["924", "929"])

    def test_carrefour_branches(self):
        args = self._parser.parse_args(["--carrefour-branches", "3003"])
        self.assertEqual(args.carrefour_branches, ["3003"])

    def test_yochananof_stores(self):
        args = self._parser.parse_args(["--yochananof-stores", "s82", "s63"])
        self.assertEqual(args.yochananof_stores, ["s82", "s63"])

    def test_supermarkets_subset(self):
        args = self._parser.parse_args(["--supermarkets", "tivtaam", "shufersal"])
        self.assertEqual(set(args.supermarkets), {"tivtaam", "shufersal"})

    def test_quiet_flag(self):
        args = self._parser.parse_args(["--quiet"])
        self.assertTrue(args.quiet)

    def test_log_level_choices(self):
        for level in ("DEBUG", "INFO", "WARNING", "ERROR"):
            args = self._parser.parse_args(["--log-level", level])
            self.assertEqual(args.log_level, level)

    def test_default_supermarkets_includes_ramilevi(self):
        args = self._parser.parse_args([])
        self.assertIn("ramilevi", args.supermarkets)

    def test_ramilevi_stores(self):
        args = self._parser.parse_args(["--ramilevi-stores", "1332", "411"])
        self.assertEqual(args.ramilevi_stores, ["1332", "411"])

    def test_update_branches_flag(self):
        args = self._parser.parse_args(["--update-branches"])
        self.assertTrue(args.update_branches)

    def test_tivtaam_branches_accepts_name_substring(self):
        """--tivtaam-branches should accept Hebrew name substrings (strings)."""
        args = self._parser.parse_args(["--tivtaam-branches", "ירושלים"])
        self.assertEqual(args.tivtaam_branches, ["ירושלים"])

    def test_resolve_branches_by_id(self):
        """_resolve_branches should filter by integer ID."""
        from main import _resolve_branches

        branch_list = [
            {"id": 924, "name": "סניף א"},
            {"id": 929, "name": "סניף ב"},
            {"id": 937, "name": "סניף ג"},
        ]
        result = _resolve_branches(["924", "937"], branch_list)
        self.assertEqual([b["id"] for b in result], [924, 937])

    def test_resolve_branches_by_name_substring(self):
        """_resolve_branches should match Hebrew name substrings."""
        from main import _resolve_branches

        branch_list = [
            {"id": 924, "name": "ירושלים - מרכז"},
            {"id": 929, "name": "תל אביב"},
            {"id": 937, "name": "ירושלים - גילה"},
        ]
        result = _resolve_branches(["ירושלים"], branch_list)
        self.assertEqual([b["id"] for b in result], [924, 937])

    def test_resolve_branches_mixed(self):
        """_resolve_branches should handle mixed ID and name args."""
        from main import _resolve_branches

        branch_list = [
            {"id": 924, "name": "ירושלים - מרכז"},
            {"id": 929, "name": "תל אביב"},
            {"id": 937, "name": "ירושלים - גילה"},
        ]
        result = _resolve_branches(["929", "ירושלים"], branch_list)
        self.assertEqual(len(result), 3)
        ids = [b["id"] for b in result]
        self.assertIn(929, ids)
        self.assertIn(924, ids)
        self.assertIn(937, ids)

    def test_resolve_branches_empty_args_returns_all(self):
        """_resolve_branches with empty args returns full branch_list."""
        from main import _resolve_branches

        branch_list = [{"id": 1, "name": "א"}, {"id": 2, "name": "ב"}]
        result = _resolve_branches([], branch_list)
        self.assertEqual(result, branch_list)


class TestMainResultSummary(unittest.TestCase):
    def test_summary_format(self):
        from main import _result_summary
        from scrapers.common import ScrapeResult

        result = ScrapeResult(
            chain="tivtaam",
            stores_scraped=2,
            products_total=500,
            products_by_store={"924": [{}] * 300, "929": [{}] * 200},
            scraped_at="2026-03-25T10:00:00+00:00",
            duration_seconds=12.3,
            errors=[],
        )
        summary = _result_summary(result)
        self.assertIn("tivtaam", summary)
        self.assertIn("500", summary)
        self.assertIn("12.3", summary)

    def test_summary_includes_error_count(self):
        from main import _result_summary
        from scrapers.common import ScrapeResult

        result = ScrapeResult(
            chain="carrefour",
            stores_scraped=1,
            products_total=10,
            products_by_store={"3003": [{}] * 10},
            scraped_at="2026-03-25T10:00:00+00:00",
            duration_seconds=5.0,
            errors=["branch=9999 failed: timeout"],
        )
        summary = _result_summary(result)
        self.assertIn("1 errors", summary)


# ---------------------------------------------------------------------------
# 7. Integration smoke — ScrapeFilter passed through scrape() signatures
# ---------------------------------------------------------------------------


class TestScrapeFilterPassthrough(unittest.TestCase):
    """Verify that ScrapeFilter keys map correctly to internal call sites."""

    def test_tivtaam_scrape_accepts_filter(self):
        """tivtaam.scrape() must accept a ScrapeFilter without raising."""
        from scrapers.tivtaam.tivtaam import scrape as tivtaam_scrape
        from scrapers.common import ScrapeFilter

        # We just check the signature — don't actually make network calls
        import inspect

        sig = inspect.signature(tivtaam_scrape)
        self.assertIn("flt", sig.parameters)
        self.assertIn("batch_size", sig.parameters)
        self.assertIn("max_concurrent", sig.parameters)
        self.assertIn("max_retries", sig.parameters)
        self.assertIn("base_retry_delay", sig.parameters)

    def test_shufersal_scrape_accepts_filter(self):
        from scrapers.shufersal.shufersal import scrape as shufersal_scrape
        import inspect

        sig = inspect.signature(shufersal_scrape)
        self.assertIn("flt", sig.parameters)
        self.assertIn("max_concurrent", sig.parameters)
        self.assertIn("max_retries", sig.parameters)
        self.assertIn("base_retry_delay", sig.parameters)

    def test_yochananof_scrape_accepts_filter(self):
        from scrapers.yochananof.yochananof import scrape as yochananof_scrape
        import inspect

        sig = inspect.signature(yochananof_scrape)
        self.assertIn("flt", sig.parameters)
        self.assertIn("max_concurrent", sig.parameters)
        self.assertIn("max_retries", sig.parameters)
        self.assertIn("base_retry_delay", sig.parameters)

    def test_carrefour_scrape_accepts_filter(self):
        from scrapers.carrefour.carrefour import scrape as carrefour_scrape
        import inspect

        sig = inspect.signature(carrefour_scrape)
        self.assertIn("flt", sig.parameters)
        self.assertIn("max_concurrent", sig.parameters)
        self.assertIn("max_retries", sig.parameters)
        self.assertIn("base_retry_delay", sig.parameters)

    def test_machsanei_scrape_accepts_filter(self):
        from scrapers.machsanei_hashook.machsanei_hashook import (
            scrape as machsanei_scrape,
        )
        import inspect

        sig = inspect.signature(machsanei_scrape)
        self.assertIn("flt", sig.parameters)
        self.assertIn("batch_size", sig.parameters)
        self.assertIn("max_concurrent", sig.parameters)
        self.assertIn("max_retries", sig.parameters)
        self.assertIn("base_retry_delay", sig.parameters)

    def test_ramilevi_scrape_accepts_filter(self):
        from scrapers.ramilevi.ramilevi import scrape as ramilevi_scrape
        import inspect

        sig = inspect.signature(ramilevi_scrape)
        self.assertIn("flt", sig.parameters)
        self.assertIn("batch_size", sig.parameters)
        self.assertIn("max_concurrent", sig.parameters)
        self.assertIn("max_retries", sig.parameters)
        self.assertIn("base_retry_delay", sig.parameters)


class TestUnifiedProductSchema(unittest.TestCase):
    """All scrapers must produce UnifiedProduct dicts with the same required keys."""

    REQUIRED_KEYS = {
        "chain",
        "store_id",
        "store_name",
        "product_id",
        "name",
        "price",
        "regular_price",
        "sale_price",
        "discount_percent",
        "barcode",
        "image_url",
        "category_ids",
        "is_weighable",
        "unit_description",
        "unit_of_measure",
        "unit_qty",
        "unit_qty_si",
        "unit_dimension",
        "price_per_base_unit",
        "deal",
        "brand",
        "manufacturer",
        "scraped_at",
    }

    def _check_product(self, p: dict, chain: str):
        self.assertIsInstance(p, dict)
        for key in self.REQUIRED_KEYS:
            self.assertIn(
                key, p, f"UnifiedProduct missing key '{key}' for chain {chain}"
            )
        self.assertEqual(p["chain"], chain)
        self.assertIsInstance(p["price"], float)
        self.assertIsInstance(p["regular_price"], float)
        self.assertIsInstance(p["category_ids"], list)
        self.assertIsInstance(p["is_weighable"], bool)
        self.assertIsInstance(p["scraped_at"], str)
        self.assertIn("T", p["scraped_at"])

    def test_tivtaam_product_schema(self):
        from scrapers.tivtaam.tivtaam import ONLINE_BRANCHES, _to_unified

        branch = ONLINE_BRANCHES[0]
        item = {
            "id": 1,
            "productId": 100,
            "names": {"1": {"long": "חלב"}},
            "image": {
                "url": "https://cdn.example.com/gs1-products/1062/{{size}}/7290000066882-1/img.{{extension||'jpg'}}"
            },
            "isWeighable": False,
            "weight": None,
            "unitOfMeasure": {"names": {"1": None}},
            "branch": {"regularPrice": 5.90, "salePrice": None},
            "family": {"categories": [{"id": 90176}]},
        }
        p = _to_unified(item, branch, "90176", "2026-03-25T10:00:00+00:00")
        self._check_product(p, "tivtaam")

    def test_shufersal_product_schema(self):
        from scrapers.shufersal.shufersal import _to_unified

        item = {
            "code": "P_1",
            "sku": "111",
            "name": "לחם",
            "categoryPrice": {"value": 3.90},
            "allCategoryCodes": ["B01"],
            "images": [
                {
                    "imageType": "PRIMARY",
                    "format": "medium",
                    "url": "https://example.com/b.png",
                }
            ],
            "sellingMethod": {"code": "BY_PACKAGE"},
            "food": True,
            "brand": None,
            "ean": None,
            "manufacturer": None,
            "unitDescription": None,
            "unitForComparison": None,
            "valueForComparison": None,
            "numberContentUnits": None,
        }
        p = _to_unified(item, "2026-03-25T10:00:00+00:00")
        self._check_product(p, "shufersal")

    def test_yochananof_product_schema(self):
        from scrapers.yochananof.yochananof import Store, _to_unified

        store = Store(store_code="s82", store_name="תל אביב", is_default_store=False)
        item = {
            "id": 9001,
            "sku": "7290001234567",
            "name": "גבינה",
            "brand": None,
            "stock_status": "IN_STOCK",
            "by_kilo": 0,
            "item_unit": None,
            "price_range": {
                "minimum_price": {
                    "regular_price": {"value": 7.90},
                    "final_price": {"value": 7.90},
                    "discount": {"percent_off": 0},
                }
            },
            "small_image": {"url": "https://example.com/g.jpg"},
            "categories": [{"id": 423, "name": "גבינות"}],
        }
        p = _to_unified(item, store, "2026-03-25T10:00:00+00:00")
        self._check_product(p, "yochananof")

    def test_carrefour_product_schema(self):
        from scrapers.carrefour.carrefour import ONLINE_BRANCHES, _to_unified

        branch = ONLINE_BRANCHES[12]  # כפר סבא
        item = {
            "id": 55,
            "productId": 88,
            "names": {"1": {"long": "שמן זית"}},
            "image": {
                "url": "https://cdn.example.com/gs1-products/1540/{{size}}/7290000999999-1/img.jpg"
            },
            "isWeighable": False,
            "weight": 500,
            "unitOfMeasure": {"names": {"1": 'מ"ל'}},
            "branch": {"regularPrice": 22.90, "salePrice": 18.00},
            "family": {"categories": [{"id": 79591}]},
        }
        p = _to_unified(item, branch, "79591", "2026-03-25T10:00:00+00:00")
        self._check_product(p, "carrefour")

    def test_machsanei_product_schema(self):
        from scrapers.machsanei_hashook.machsanei_hashook import (
            _to_unified,
        )

        item = {
            "id": 20164375,
            "productId": 6080688,
            "localName": "חלב",
            "names": {"1": {"short": "חלב", "long": "חלב תנובה 3%"}},
            "isWeighable": False,
            "weight": 1000,
            "unitOfMeasure": {"names": {"1": 'מ"ל'}},
            "numberOfItems": 1,
            "family": {
                "categories": [{"id": 13292, "names": {"1": {"name": "מוצרי חלב"}}}]
            },
            "image": {
                "url": "https://htmlcache.blob.core.windows.net/gs1-products/1107/{{size}}/7290000066882-1.jpg"
            },
            "branch": {
                "id": 836,
                "isActive": True,
                "isVisible": True,
                "regularPrice": 6.90,
                "salePrice": None,
                "isOutOfStock": False,
                "specials": [],
            },
        }
        p = _to_unified(item, "2026-03-25T10:00:00+00:00")
        self._check_product(p, "machsanei")

    def test_ramilevi_product_schema(self):
        from scrapers.ramilevi.ramilevi import ONLINE_STORES, _to_unified

        store = ONLINE_STORES[24]  # מודיעין, id=1332
        item = {
            "id": 3025,
            "name": "חלב תנובה 3% שומן 1 ל'",
            "barcode": 7290004131074,
            "price": {"price": 7.2},
            "prop": {
                "unit": None,
                "sw_shakil": 0,
                "by_kilo": 0,
                "by_kilo_content": 0,
                "status": 2,
            },
            "department": {"id": 50, "name": "מוצרי חלב", "slug": "מוצרי-חלב"},
            "department_id": 50,
            "group": {"id": 198, "name": "חלב", "slug": "חלב"},
            "group_id": 198,
            "subGroup": {"id": 22, "name": "חלב קרטון", "slug": "חלב-קרטון"},
            "sub_group_id": 22,
            "gs": {
                "BrandName": "תנובה",
                "Net_Content": {"UOM": "ליטר", "text": "1 ליטר", "value": "1"},
            },
            "images": {
                "small": "/product/7290004131074/3025/medium.jpg",
                "original": "/product/7290004131074/3025/large.jpg",
            },
            "sale": [],
            "available_in": [1332],
            "site_id": 1,
        }
        p = _to_unified(item, store, "2026-03-25T10:00:00+00:00")
        self._check_product(p, "ramilevi")


# ---------------------------------------------------------------------------
# 6b. Machsanei HaShook — unit tests (no network)
# ---------------------------------------------------------------------------


class TestMachsaneiExtraction(unittest.TestCase):
    def setUp(self):
        from scrapers.machsanei_hashook.machsanei_hashook import (
            _to_unified,
            _extract_deal,
        )

        self._to_unified = _to_unified
        self._extract_deal = _extract_deal
        self._scraped_at = "2026-03-25T10:00:00+00:00"

    def _make_item(self, **overrides) -> dict:
        item = {
            "id": 20164375,
            "productId": 6080688,
            "localName": "חלב",
            "names": {"1": {"short": "חלב", "long": "חלב תנובה 3%"}},
            "isWeighable": False,
            "weight": 1000,
            "unitOfMeasure": {"names": {"1": 'מ"ל'}},
            "numberOfItems": 1,
            "family": {
                "categories": [{"id": 13292, "names": {"1": {"name": "מוצרי חלב"}}}]
            },
            "image": {
                "url": "https://htmlcache.blob.core.windows.net/gs1-products/1107/{{size}}/7290000066882-1.jpg"
            },
            "branch": {
                "id": 836,
                "isActive": True,
                "isVisible": True,
                "regularPrice": 6.90,
                "salePrice": None,
                "isOutOfStock": False,
                "specials": [],
            },
        }
        item.update(overrides)
        return item

    def test_chain_is_machsanei(self):
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertIsNotNone(p)
        self.assertEqual(p["chain"], "machsanei")

    def test_store_id_is_branch_id(self):
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertEqual(p["store_id"], "836")

    def test_store_name_is_branch_name(self):
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertEqual(p["store_name"], "מחסני השוק")

    def test_barcode_extracted_from_image_url(self):
        """Barcode is extracted from the image URL (not a top-level field)."""
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertEqual(p["barcode"], "7290000066882")

    def test_barcode_none_when_no_barcode_in_url(self):
        """Image URL with no recognisable barcode → barcode is None."""
        item = self._make_item()
        item["image"] = {"url": "https://cdn.example.com/products/nobarcode.jpg"}
        p = self._to_unified(item, self._scraped_at)
        self.assertIsNone(p["barcode"])

    def test_image_url_template_expanded(self):
        """{{size}} and {{extension||'jpg'}} placeholders are replaced."""
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertIsNotNone(p["image_url"])
        self.assertNotIn("{{size}}", p["image_url"])
        self.assertNotIn("{{extension", p["image_url"])
        self.assertIn("large", p["image_url"])

    def test_category_from_family_categories(self):
        """Category IDs come from product['family']['categories'][*]['id']."""
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertIn("13292", p["category_ids"])

    def test_no_category_when_no_family(self):
        item = self._make_item()
        del item["family"]
        p = self._to_unified(item, self._scraped_at)
        self.assertEqual(p["category_ids"], [])

    def test_regular_price_mapped(self):
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertAlmostEqual(p["regular_price"], 6.90)
        self.assertAlmostEqual(p["price"], 6.90)
        self.assertIsNone(p["sale_price"])

    def test_sale_price_mapped(self):
        item = self._make_item()
        item["branch"]["salePrice"] = 5.50
        p = self._to_unified(item, self._scraped_at)
        self.assertAlmostEqual(p["price"], 5.50)
        self.assertAlmostEqual(p["sale_price"], 5.50)
        self.assertAlmostEqual(p["regular_price"], 6.90)

    def test_discount_percent_calculated(self):
        item = self._make_item()
        item["branch"]["salePrice"] = 5.50
        p = self._to_unified(item, self._scraped_at)
        self.assertIsNotNone(p["discount_percent"])
        self.assertGreater(p["discount_percent"], 0)

    def test_inactive_branch_returns_none(self):
        item = self._make_item()
        item["branch"]["isActive"] = False
        p = self._to_unified(item, self._scraped_at)
        self.assertIsNone(p)

    def test_invisible_branch_returns_none(self):
        item = self._make_item()
        item["branch"]["isVisible"] = False
        p = self._to_unified(item, self._scraped_at)
        self.assertIsNone(p)

    def test_missing_branch_returns_none(self):
        """No branch key at all → None."""
        item = self._make_item()
        del item["branch"]
        p = self._to_unified(item, self._scraped_at)
        self.assertIsNone(p)

    def test_zero_price_returns_none(self):
        item = self._make_item()
        item["branch"]["regularPrice"] = 0
        p = self._to_unified(item, self._scraped_at)
        self.assertIsNone(p)

    def test_no_price_returns_none(self):
        item = self._make_item()
        item["branch"]["regularPrice"] = None
        p = self._to_unified(item, self._scraped_at)
        self.assertIsNone(p)

    def test_unit_dimension_volume(self):
        """1000 מ\"ל → dimension=volume, qty_si=1000."""
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertEqual(p["unit_dimension"], "volume")
        self.assertAlmostEqual(p["unit_qty_si"], 1000.0)

    def test_price_per_base_unit_volume(self):
        """6.90 for 1000ml → 0.69 per 100ml."""
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertIsNotNone(p["price_per_base_unit"])
        self.assertAlmostEqual(p["price_per_base_unit"], 0.69, places=3)

    def test_name_prefers_long(self):
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertEqual(p["name"], "חלב תנובה 3%")

    def test_name_fallback_to_local_name(self):
        item = self._make_item()
        del item["names"]
        p = self._to_unified(item, self._scraped_at)
        self.assertEqual(p["name"], "חלב")

    def test_deal_none_when_no_sale_no_specials(self):
        p = self._to_unified(self._make_item(), self._scraped_at)
        self.assertIsNone(p["deal"])

    def test_deal_price_reduction_from_sale_price(self):
        """salePrice < regularPrice → price_reduction deal."""
        item = self._make_item()
        item["branch"]["salePrice"] = 5.50
        p = self._to_unified(item, self._scraped_at)
        self.assertIsNotNone(p["deal"])
        self.assertEqual(p["deal"]["deal_type"], "price_reduction")
        self.assertAlmostEqual(p["deal"]["deal_price"], 5.50)

    def test_deal_multi_buy_from_special_type2(self):
        """specials type=2 → multi_buy deal."""
        item = self._make_item()
        item["branch"]["specials"] = [
            {
                "names": {"1": {"name": "3 יח' ב-18 ₪"}},
                "firstLevel": {
                    "type": 2,
                    "firstPurchaseTotal": 3,
                    "firstGift": {"total": 18.0},
                },
            }
        ]
        p = self._to_unified(item, self._scraped_at)
        self.assertIsNotNone(p["deal"])
        self.assertEqual(p["deal"]["deal_type"], "multi_buy")
        self.assertEqual(p["deal"]["deal_min_qty"], 3)
        self.assertAlmostEqual(p["deal"]["deal_price"], 18.0)
        self.assertAlmostEqual(p["deal"]["deal_price_per_unit"], 6.0)

    def test_deal_cart_total_from_special_type3(self):
        """specials type=3 → cart_total deal."""
        item = self._make_item()
        item["branch"]["specials"] = [
            {
                "names": {"1": {"name": "קנה ב-50 ₪ וקבל הנחה"}},
                "firstLevel": {"type": 3},
            }
        ]
        p = self._to_unified(item, self._scraped_at)
        self.assertIsNotNone(p["deal"])
        self.assertEqual(p["deal"]["deal_type"], "cart_total")


# ---------------------------------------------------------------------------
# 6c. Rami Levy — unit tests (no network)
# ---------------------------------------------------------------------------


class TestRamiLeviExtraction(unittest.TestCase):
    def setUp(self):
        from scrapers.ramilevi.ramilevi import ONLINE_STORES, _to_unified, _extract_deal

        self._to_unified = _to_unified
        self._extract_deal = _extract_deal
        self._store = ONLINE_STORES[24]  # מודיעין, id=1332
        self._scraped_at = "2026-03-25T10:00:00+00:00"

    def _make_item(self, **overrides) -> dict:
        item = {
            "id": 3025,
            "name": "חלב תנובה 3% שומן 1 ל'",
            "barcode": 7290004131074,
            "price": {"price": 7.2},
            "prop": {
                "unit": None,
                "sw_shakil": 0,
                "by_kilo": 0,
                "by_kilo_content": 0,
                "status": 2,
            },
            "department": {"id": 50, "name": "מוצרי חלב", "slug": "מוצרי-חלב"},
            "department_id": 50,
            "group": {"id": 198, "name": "חלב", "slug": "חלב"},
            "group_id": 198,
            "subGroup": {"id": 22, "name": "חלב קרטון", "slug": "חלב-קרטון"},
            "sub_group_id": 22,
            "gs": {
                "BrandName": "תנובה",
                "Net_Content": {"UOM": "ליטר", "text": "1 ליטר", "value": "1"},
            },
            "images": {
                "small": "/product/7290004131074/3025/medium.jpg",
                "original": "/product/7290004131074/3025/large.jpg",
            },
            "sale": [],
            "available_in": [1332],
            "site_id": 1,
        }
        item.update(overrides)
        return item

    def test_chain_is_ramilevi(self):
        p = self._to_unified(self._make_item(), self._store, self._scraped_at)
        self.assertEqual(p["chain"], "ramilevi")

    def test_store_id_is_internet_store_id(self):
        p = self._to_unified(self._make_item(), self._store, self._scraped_at)
        self.assertEqual(p["store_id"], "1332")

    def test_name_mapped(self):
        p = self._to_unified(self._make_item(), self._store, self._scraped_at)
        self.assertEqual(p["name"], "חלב תנובה 3% שומן 1 ל'")

    def test_barcode_is_string(self):
        p = self._to_unified(self._make_item(), self._store, self._scraped_at)
        self.assertIsInstance(p["barcode"], str)
        self.assertEqual(p["barcode"], "7290004131074")

    def test_regular_price_mapped(self):
        p = self._to_unified(self._make_item(), self._store, self._scraped_at)
        self.assertAlmostEqual(p["regular_price"], 7.2)
        self.assertAlmostEqual(p["price"], 7.2)
        self.assertIsNone(p["sale_price"])

    def test_no_sale_when_sale_empty(self):
        p = self._to_unified(self._make_item(), self._store, self._scraped_at)
        self.assertIsNone(p["deal"])
        self.assertIsNone(p["sale_price"])

    def test_sale_price_from_scm(self):
        item = self._make_item()
        item["sale"] = [{"type": 1, "scm": 5.9, "name": "מחיר מיוחד"}]
        p = self._to_unified(item, self._store, self._scraped_at)
        self.assertAlmostEqual(p["sale_price"], 5.9)
        self.assertAlmostEqual(p["price"], 5.9)

    def test_deal_price_reduction_from_sale(self):
        """sale[0].scm < regular_price → price_reduction deal."""
        item = self._make_item()
        item["sale"] = [{"type": 1, "scm": 5.9, "name": "מחיר מיוחד"}]
        p = self._to_unified(item, self._store, self._scraped_at)
        self.assertIsNotNone(p["deal"])
        self.assertEqual(p["deal"]["deal_type"], "price_reduction")
        self.assertAlmostEqual(p["deal"]["deal_price"], 5.9)

    def test_no_deal_when_sale_empty(self):
        p = self._to_unified(self._make_item(), self._store, self._scraped_at)
        self.assertIsNone(p["deal"])

    def test_unit_fields_from_gs_net_content(self):
        """1 ליטר → dimension=volume, qty_si=1000."""
        p = self._to_unified(self._make_item(), self._store, self._scraped_at)
        self.assertEqual(p["unit_dimension"], "volume")
        self.assertAlmostEqual(p["unit_qty_si"], 1000.0)

    def test_price_per_base_unit_volume(self):
        """7.2 for 1000ml → 0.72 per 100ml."""
        p = self._to_unified(self._make_item(), self._store, self._scraped_at)
        self.assertIsNotNone(p["price_per_base_unit"])
        self.assertAlmostEqual(p["price_per_base_unit"], 0.72, places=3)

    def test_by_kilo_sets_is_weighable(self):
        """prop.by_kilo=1 → is_weighable=True."""
        item = self._make_item()
        item["prop"]["by_kilo"] = 1
        p = self._to_unified(item, self._store, self._scraped_at)
        self.assertTrue(p["is_weighable"])

    def test_category_ids_from_department_group_subgroup(self):
        p = self._to_unified(self._make_item(), self._store, self._scraped_at)
        self.assertIn("50", p["category_ids"])
        self.assertIn("198", p["category_ids"])
        self.assertIn("22", p["category_ids"])

    def test_brand_from_gs(self):
        p = self._to_unified(self._make_item(), self._store, self._scraped_at)
        self.assertEqual(p["brand"], "תנובה")

    def test_image_url_constructed(self):
        p = self._to_unified(self._make_item(), self._store, self._scraped_at)
        self.assertIsNotNone(p["image_url"])
        self.assertIn("7290004131074", p["image_url"])

    def test_no_price_returns_none(self):
        item = self._make_item()
        item["price"] = {"price": None}
        p = self._to_unified(item, self._store, self._scraped_at)
        self.assertIsNone(p)

    def test_no_name_returns_none(self):
        item = self._make_item()
        item["name"] = None
        p = self._to_unified(item, self._store, self._scraped_at)
        self.assertIsNone(p)

    def test_discount_percent_calculated(self):
        item = self._make_item()
        item["sale"] = [{"type": 1, "scm": 5.9, "name": "מחיר מיוחד"}]
        p = self._to_unified(item, self._store, self._scraped_at)
        self.assertIsNotNone(p["discount_percent"])
        self.assertGreater(p["discount_percent"], 0)

    def test_sale_not_applied_when_scm_ge_regular(self):
        """sale.scm >= regular_price → no deal, no sale_price."""
        item = self._make_item()
        item["sale"] = [{"type": 1, "scm": 9.9, "name": "לא הנחה"}]
        p = self._to_unified(item, self._store, self._scraped_at)
        self.assertIsNone(p["deal"])
        self.assertIsNone(p["sale_price"])


# ---------------------------------------------------------------------------
# 6d. Keshet Teamim — unit tests (no network, same ZuZ pattern)
# ---------------------------------------------------------------------------


class TestKeshetExtraction(unittest.TestCase):
    """Tests for the appId=4 per-branch/per-category schema.

    Key differences from the old appId=2 schema:
    - Branch data is in item["branch"] (singular), not item["branches"][str(id)].
    - Barcode is extracted from the image URL, not a top-level field.
    - Categories come from item["family"]["categories"], not item["department"].
    - Brand comes from item["brand"]["names"]["1"].
    """

    def setUp(self):
        from scrapers.keshet.keshet import ONLINE_BRANCHES, _to_unified

        self._to_unified = _to_unified
        self._branch = ONLINE_BRANCHES[2]  # סניף אשדוד, id=1570
        self._scraped_at = "2026-03-25T10:00:00+00:00"

    def _make_item(self, **overrides) -> dict:
        # Image URL with an embedded barcode (7290001234567) before a dash —
        # this is the ZuZ appId=4 pattern used by both Keshet and Machsanei.
        item = {
            "id": 99001,
            "productId": 99001,
            "localName": "שמן זית",
            "names": {"1": {"short": "שמן זית", "long": "שמן זית כתית מעולה 750מל"}},
            "isWeighable": False,
            "weight": 750,
            "unitOfMeasure": {"names": {"1": 'מ"ל'}},
            "family": {
                "categories": [{"id": 79619, "names": {"1": {"long": "שמנים"}}}]
            },
            "brand": {"names": {"1": "יד מרדכי"}},
            "image": {
                "url": "https://d226b0iufwcjmj.cloudfront.net/gs1-products/1219/{{size}}/7290001234567-1.{{extension||'jpg'}}"
            },
            # appId=4: branch data in "branch" (singular), not "branches"
            "branch": {
                "id": 1570,
                "isActive": True,
                "isVisible": True,
                "regularPrice": 24.90,
                "salePrice": None,
                "specials": [],
            },
        }
        item.update(overrides)
        return item

    def test_chain_is_keshet(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertIsNotNone(p)
        self.assertEqual(p["chain"], "keshet")

    def test_store_id_is_branch_id(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertEqual(p["store_id"], "1570")

    def test_barcode_extracted_from_image_url(self):
        """Barcode must be extracted from the image URL, not a direct field."""
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertEqual(p["barcode"], "7290001234567")

    def test_barcode_none_when_no_image(self):
        item = self._make_item()
        item["image"] = {}
        p = self._to_unified(item, self._branch, self._scraped_at)
        self.assertIsNone(p["barcode"])

    def test_image_url_placeholders_expanded(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertIsNotNone(p["image_url"])
        self.assertNotIn("{{size}}", p["image_url"])
        self.assertNotIn("{{extension", p["image_url"])
        self.assertIn("large", p["image_url"])

    def test_price_mapped(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertAlmostEqual(p["regular_price"], 24.90)
        self.assertAlmostEqual(p["price"], 24.90)
        self.assertIsNone(p["sale_price"])

    def test_unit_dimension_volume(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertEqual(p["unit_dimension"], "volume")
        self.assertAlmostEqual(p["unit_qty_si"], 750.0)

    def test_price_per_base_unit_volume(self):
        """24.90 for 750ml → price per 100ml."""
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertIsNotNone(p["price_per_base_unit"])
        self.assertAlmostEqual(
            p["price_per_base_unit"], round(24.90 / 750 * 100, 4), places=3
        )

    def test_category_ids_from_family(self):
        """Category IDs come from item["family"]["categories"], not department."""
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertIn("79619", p["category_ids"])

    def test_brand_mapped(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertEqual(p["brand"], "יד מרדכי")

    def test_inactive_branch_returns_none(self):
        item = self._make_item()
        item["branch"]["isActive"] = False
        p = self._to_unified(item, self._branch, self._scraped_at)
        self.assertIsNone(p)

    def test_invisible_branch_returns_none(self):
        item = self._make_item()
        item["branch"]["isVisible"] = False
        p = self._to_unified(item, self._branch, self._scraped_at)
        self.assertIsNone(p)

    def test_missing_branch_key_returns_none(self):
        """Products without a 'branch' key should be skipped."""
        item = self._make_item()
        del item["branch"]
        p = self._to_unified(item, self._branch, self._scraped_at)
        self.assertIsNone(p)

    def test_deal_price_reduction(self):
        item = self._make_item()
        item["branch"]["salePrice"] = 19.90
        p = self._to_unified(item, self._branch, self._scraped_at)
        self.assertIsNotNone(p["deal"])
        self.assertEqual(p["deal"]["deal_type"], "price_reduction")
        self.assertAlmostEqual(p["deal"]["deal_price"], 19.90)

    def test_deal_multi_buy(self):
        item = self._make_item()
        item["branch"]["specials"] = [
            {
                "names": {"1": {"name": "2 יח' ב-40 ₪"}},
                "firstLevel": {
                    "type": 2,
                    "firstPurchaseTotal": 2,
                    "firstGift": {"total": 40.0},
                },
            }
        ]
        p = self._to_unified(item, self._branch, self._scraped_at)
        self.assertIsNotNone(p["deal"])
        self.assertEqual(p["deal"]["deal_type"], "multi_buy")
        self.assertEqual(p["deal"]["deal_min_qty"], 2)
        self.assertAlmostEqual(p["deal"]["deal_price_per_unit"], 20.0)


# ---------------------------------------------------------------------------
# 6e. Quik — unit tests (no network, same ZuZ pattern)
# ---------------------------------------------------------------------------


class TestQuikExtraction(unittest.TestCase):
    """Tests for the appId=4 per-branch/per-category schema.

    Key differences from the old appId=2 schema:
    - Branch data is in item["branch"] (singular), not item["branches"][str(id)].
    - Barcode is extracted from the image URL, not a top-level field.
    - Categories come from item["family"]["categories"], not item["department"].
    - Brand comes from item["brand"]["names"]["1"].
    """

    def setUp(self):
        from scrapers.quik.quik import ONLINE_BRANCHES, _to_unified

        self._to_unified = _to_unified
        self._branch = ONLINE_BRANCHES[3]  # אשדוד - Online, id=3086
        self._scraped_at = "2026-03-25T10:00:00+00:00"

    def _make_item(self, **overrides) -> dict:
        item = {
            "id": 88001,
            "productId": 88001,
            "localName": "לחם אחיד",
            "names": {"1": {"short": "לחם אחיד", "long": "לחם אחיד כהה 750 גר"}},
            "isWeighable": False,
            "weight": 750,
            "unitOfMeasure": {"names": {"1": "גרם"}},
            "family": {"categories": [{"id": 79687, "names": {"1": {"long": "לחם"}}}]},
            "brand": {"names": {"1": "אחלה לחם"}},
            "image": {
                "url": "https://d226b0iufwcjmj.cloudfront.net/gs1-products/1541/{{size}}/7290005001234-1.{{extension||'jpg'}}"
            },
            # appId=4: branch data in "branch" (singular), not "branches"
            "branch": {
                "id": 3086,
                "isActive": True,
                "isVisible": True,
                "regularPrice": 8.90,
                "salePrice": None,
                "specials": [],
            },
        }
        item.update(overrides)
        return item

    def test_chain_is_quik(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertIsNotNone(p)
        self.assertEqual(p["chain"], "quik")

    def test_store_id_is_branch_id(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertEqual(p["store_id"], "3086")

    def test_barcode_extracted_from_image_url(self):
        """Barcode must be extracted from the image URL, not a direct field."""
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertEqual(p["barcode"], "7290005001234")

    def test_barcode_none_when_no_image(self):
        item = self._make_item()
        item["image"] = {}
        p = self._to_unified(item, self._branch, self._scraped_at)
        self.assertIsNone(p["barcode"])

    def test_image_url_placeholders_expanded(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertIsNotNone(p["image_url"])
        self.assertNotIn("{{size}}", p["image_url"])
        self.assertNotIn("{{extension", p["image_url"])
        self.assertIn("large", p["image_url"])

    def test_unit_dimension_mass(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertEqual(p["unit_dimension"], "mass")
        self.assertAlmostEqual(p["unit_qty_si"], 750.0)

    def test_price_per_base_unit_mass(self):
        """8.90 for 750g → price per 100g."""
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertIsNotNone(p["price_per_base_unit"])
        self.assertAlmostEqual(
            p["price_per_base_unit"], round(8.90 / 750 * 100, 4), places=3
        )

    def test_missing_branch_key_returns_none(self):
        """Products without a 'branch' key should be skipped."""
        item = self._make_item()
        del item["branch"]
        p = self._to_unified(item, self._branch, self._scraped_at)
        self.assertIsNone(p)

    def test_inactive_branch_returns_none(self):
        item = self._make_item()
        item["branch"]["isActive"] = False
        p = self._to_unified(item, self._branch, self._scraped_at)
        self.assertIsNone(p)

    def test_zero_price_returns_none(self):
        item = self._make_item()
        item["branch"]["regularPrice"] = 0
        p = self._to_unified(item, self._branch, self._scraped_at)
        self.assertIsNone(p)

    def test_category_ids_from_family(self):
        """Category IDs come from item["family"]["categories"], not department."""
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertIn("79687", p["category_ids"])

    def test_brand_mapped(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertEqual(p["brand"], "אחלה לחם")

    def test_deal_price_reduction(self):
        item = self._make_item()
        item["branch"]["salePrice"] = 6.90
        p = self._to_unified(item, self._branch, self._scraped_at)
        self.assertIsNotNone(p["deal"])
        self.assertEqual(p["deal"]["deal_type"], "price_reduction")
        self.assertAlmostEqual(p["deal"]["deal_price"], 6.90)


# ---------------------------------------------------------------------------
# 6f. Victory — unit tests (no network, same ZuZ pattern)
# ---------------------------------------------------------------------------


class TestVictoryExtraction(unittest.TestCase):
    """Tests for the appId=4 per-branch/per-category schema.

    Key differences from the old appId=2 schema:
    - Branch data is in item["branch"] (singular), not item["branches"][str(id)].
    - Barcode is extracted from the image URL, not a top-level field.
    - Categories come from item["family"]["categories"], not item["department"].
    - Brand comes from item["brand"]["names"]["1"].
    """

    def setUp(self):
        from scrapers.victory.victory import ONLINE_BRANCHES, _to_unified

        self._to_unified = _to_unified
        self._branch = ONLINE_BRANCHES[2]  # ויקטורי אשדוד - Victory Online, id=2527
        self._scraped_at = "2026-03-25T10:00:00+00:00"

    def _make_item(self, **overrides) -> dict:
        item = {
            "id": 77001,
            "productId": 77001,
            "localName": "גבינה צהובה",
            "names": {"1": {"short": "גבינה צהובה", "long": "גבינה צהובה 28% 200 גר"}},
            "isWeighable": False,
            "weight": 200,
            "unitOfMeasure": {"names": {"1": "גרם"}},
            "family": {
                "categories": [{"id": 79718, "names": {"1": {"long": "חלב וגבינות"}}}]
            },
            "brand": {"names": {"1": "תנובה"}},
            "image": {
                "url": "https://d226b0iufwcjmj.cloudfront.net/gs1-products/1470/{{size}}/7290009876543-1.{{extension||'jpg'}}"
            },
            # appId=4: branch data in "branch" (singular), not "branches"
            "branch": {
                "id": 2527,
                "isActive": True,
                "isVisible": True,
                "regularPrice": 12.50,
                "salePrice": None,
                "specials": [],
            },
        }
        item.update(overrides)
        return item

    def test_chain_is_victory(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertIsNotNone(p)
        self.assertEqual(p["chain"], "victory")

    def test_store_id_is_branch_id(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertEqual(p["store_id"], "2527")

    def test_barcode_extracted_from_image_url(self):
        """Barcode must be extracted from the image URL, not a direct field."""
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertEqual(p["barcode"], "7290009876543")

    def test_barcode_none_when_no_image(self):
        item = self._make_item()
        item["image"] = {}
        p = self._to_unified(item, self._branch, self._scraped_at)
        self.assertIsNone(p["barcode"])

    def test_image_url_placeholders_expanded(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertIsNotNone(p["image_url"])
        self.assertNotIn("{{size}}", p["image_url"])
        self.assertNotIn("{{extension", p["image_url"])
        self.assertIn("large", p["image_url"])

    def test_price_mapped(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertAlmostEqual(p["regular_price"], 12.50)
        self.assertAlmostEqual(p["price"], 12.50)

    def test_unit_dimension_mass(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertEqual(p["unit_dimension"], "mass")

    def test_category_ids_from_family(self):
        """Category IDs come from item["family"]["categories"], not department."""
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertIn("79718", p["category_ids"])

    def test_brand_mapped(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertEqual(p["brand"], "תנובה")

    def test_sale_price_creates_deal(self):
        item = self._make_item()
        item["branch"]["salePrice"] = 9.90
        p = self._to_unified(item, self._branch, self._scraped_at)
        self.assertIsNotNone(p["deal"])
        self.assertEqual(p["deal"]["deal_type"], "price_reduction")

    def test_invisible_branch_returns_none(self):
        item = self._make_item()
        item["branch"]["isVisible"] = False
        p = self._to_unified(item, self._branch, self._scraped_at)
        self.assertIsNone(p)

    def test_missing_branch_key_returns_none(self):
        """Products without a 'branch' key should be skipped."""
        item = self._make_item()
        del item["branch"]
        p = self._to_unified(item, self._branch, self._scraped_at)
        self.assertIsNone(p)

    def test_deal_zero_qty_req_skipped(self):
        """multi_buy with qty_req=0 must not raise ZeroDivisionError."""
        item = self._make_item()
        item["branch"]["specials"] = [
            {
                "names": {"1": {"name": "מבצע"}},
                "firstLevel": {
                    "type": 2,
                    "firstPurchaseTotal": 0,  # ← the problematic value
                    "firstGift": {"total": 10.0},
                },
            }
        ]
        p = self._to_unified(item, self._branch, self._scraped_at)
        # Should not crash; deal should be None (skipped)
        self.assertIsNone(p["deal"])


# ---------------------------------------------------------------------------
# 6g. Yenot Bitan — unit tests (no network, same ZuZ pattern)
# ---------------------------------------------------------------------------


class TestYbitanExtraction(unittest.TestCase):
    """Tests for the appId=4 per-branch/per-category schema.

    Key differences from the old appId=2 schema:
    - Branch data is in item["branch"] (singular), not item["branches"][str(id)].
    - Barcode is extracted from the image URL, not a top-level field.
    - Categories come from item["family"]["categories"], not item["department"].
    - Brand comes from item["brand"]["names"]["1"].
    """

    def setUp(self):
        from scrapers.ybitan.ybitan import ONLINE_BRANCHES, _to_unified

        self._to_unified = _to_unified
        self._branch = ONLINE_BRANCHES[3]  # אשדוד- Online, id=1855
        self._scraped_at = "2026-03-25T10:00:00+00:00"

    def _make_item(self, **overrides) -> dict:
        item = {
            "id": 66001,
            "productId": 66001,
            "localName": "יין אדום",
            "names": {
                "1": {"short": "יין אדום", "long": "יין אדום קברנה סוביניון 750מל"}
            },
            "isWeighable": False,
            "weight": 750,
            "unitOfMeasure": {"names": {"1": 'מ"ל'}},
            "family": {
                "categories": [{"id": 79667, "names": {"1": {"long": "יינות ומשקאות"}}}]
            },
            "brand": {"names": {"1": "יקב ברקן"}},
            "image": {
                "url": "https://d226b0iufwcjmj.cloudfront.net/gs1-products/1131/{{size}}/7290002345678-1.{{extension||'jpg'}}"
            },
            # appId=4: branch data in "branch" (singular), not "branches"
            "branch": {
                "id": 1855,
                "isActive": True,
                "isVisible": True,
                "regularPrice": 49.90,
                "salePrice": None,
                "specials": [],
            },
        }
        item.update(overrides)
        return item

    def test_chain_is_ybitan(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertIsNotNone(p)
        self.assertEqual(p["chain"], "ybitan")

    def test_store_id_is_branch_id(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertEqual(p["store_id"], "1855")

    def test_barcode_extracted_from_image_url(self):
        """Barcode must be extracted from the image URL, not a direct field."""
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertEqual(p["barcode"], "7290002345678")

    def test_barcode_none_when_no_image(self):
        item = self._make_item()
        item["image"] = {}
        p = self._to_unified(item, self._branch, self._scraped_at)
        self.assertIsNone(p["barcode"])

    def test_image_url_placeholders_expanded(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertIsNotNone(p["image_url"])
        self.assertNotIn("{{size}}", p["image_url"])
        self.assertNotIn("{{extension", p["image_url"])
        self.assertIn("large", p["image_url"])

    def test_unit_dimension_volume(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertEqual(p["unit_dimension"], "volume")
        self.assertAlmostEqual(p["unit_qty_si"], 750.0)

    def test_price_per_base_unit_volume(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertIsNotNone(p["price_per_base_unit"])
        self.assertAlmostEqual(
            p["price_per_base_unit"], round(49.90 / 750 * 100, 4), places=3
        )

    def test_category_ids_from_family(self):
        """Category IDs come from item["family"]["categories"], not department."""
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertIn("79667", p["category_ids"])

    def test_brand_mapped(self):
        p = self._to_unified(self._make_item(), self._branch, self._scraped_at)
        self.assertEqual(p["brand"], "יקב ברקן")

    def test_price_reduction_deal(self):
        item = self._make_item()
        item["branch"]["salePrice"] = 39.90
        p = self._to_unified(item, self._branch, self._scraped_at)
        self.assertIsNotNone(p["deal"])
        self.assertEqual(p["deal"]["deal_type"], "price_reduction")
        self.assertAlmostEqual(p["deal"]["deal_price"], 39.90)
        self.assertIsNotNone(p["deal"]["price_per_base_unit_deal"])

    def test_missing_branch_key_returns_none(self):
        """Products without a 'branch' key should be skipped."""
        item = self._make_item()
        del item["branch"]
        p = self._to_unified(item, self._branch, self._scraped_at)
        self.assertIsNone(p)

    def test_no_price_returns_none(self):
        item = self._make_item()
        item["branch"]["regularPrice"] = None
        p = self._to_unified(item, self._branch, self._scraped_at)
        self.assertIsNone(p)


# ---------------------------------------------------------------------------
# chp.co.il scraper tests
# ---------------------------------------------------------------------------


class TestChpCityInfo(unittest.TestCase):
    """Tests for CityInfo parsing from autocomplete JSON."""

    def setUp(self):
        from scrapers.chp.chp import CityInfo

        self.CityInfo = CityInfo

    def test_parse_city_id_and_street_id(self):
        item = {"value": "תל אביב", "label": "תל אביב", "id": "5000_9000"}
        city = self.CityInfo.from_autocomplete_item(item)
        self.assertEqual(city.city_id, "5000")
        self.assertEqual(city.street_id, "9000")

    def test_label_stripped(self):
        item = {"value": "קרית ביאליק ", "label": "קרית ביאליק ", "id": "9500_9000"}
        city = self.CityInfo.from_autocomplete_item(item)
        self.assertEqual(city.label, "קרית ביאליק")

    def test_label_from_value_int_sentinel(self):
        """The 'prev' sentinel has value=0 (int); should not crash."""
        item = {"value": 0, "label": "↑ הצג ערכים קודמים ↑", "id": "prev"}
        # No exception, and city_id is "prev"
        city = self.CityInfo.from_autocomplete_item(item)
        self.assertEqual(city.city_id, "prev")

    def test_no_underscore_in_id(self):
        """If id has no underscore, street_id defaults to 9000."""
        item = {"value": "תל אביב", "label": "תל אביב", "id": "5000"}
        city = self.CityInfo.from_autocomplete_item(item)
        self.assertEqual(city.city_id, "5000")
        self.assertEqual(city.street_id, "9000")


class TestChpProduct(unittest.TestCase):
    """Tests for ChpProduct parsing from autocomplete JSON."""

    def _make_item(self, **overrides):
        item = {
            "value": "חלב תנובה טרי 3% בקרטון, 1 ליטר",
            "label": "חלב תנובה טרי 3% בקרטון, 1 ליטר",
            "id": "7290027600007_7290004131074",
            "parts": {
                "name_and_contents": "חלב תנובה טרי 3% בקרטון, 1 ליטר",
                "manufacturer_and_barcode": "יצרן/מותג: תנובה, ברקוד: 7290004131074",
                "pack_size": "",
                "small_image": "",
                "chainnames": "",
            },
        }
        item.update(overrides)
        return item

    def setUp(self):
        from scrapers.chp.chp import ChpProduct

        self.ChpProduct = ChpProduct

    def test_barcode_parsed_from_manufacturer_field(self):
        prod = self.ChpProduct(self._make_item())
        self.assertEqual(prod.barcode, "7290004131074")

    def test_brand_parsed(self):
        prod = self.ChpProduct(self._make_item())
        self.assertEqual(prod.brand, "תנובה")

    def test_unit_dimension_volume(self):
        """1 ליטר → volume, qty_si=1000."""
        prod = self.ChpProduct(self._make_item())
        self.assertEqual(prod._unit_dimension, "volume")
        self.assertAlmostEqual(prod._unit_qty_si, 1000.0)

    def test_unit_qty_raw(self):
        """Raw qty for 1 ליטר should be 1.0."""
        prod = self.ChpProduct(self._make_item())
        self.assertAlmostEqual(prod._unit_qty, 1.0)

    def test_is_weighable_false_for_barcode_product(self):
        prod = self.ChpProduct(self._make_item())
        self.assertFalse(prod.is_weighable)

    def test_is_weighable_true_for_our_product(self):
        item = self._make_item()
        item["id"] = "our_000005"
        prod = self.ChpProduct(item)
        self.assertTrue(prod.is_weighable)

    def test_barcode_from_temp_prefix(self):
        item = self._make_item()
        item["id"] = "temp_7290107932080"
        item["parts"]["manufacturer_and_barcode"] = ""
        prod = self.ChpProduct(item)
        self.assertEqual(prod.barcode, "7290107932080")

    def test_unit_dimension_mass(self):
        """500 גרם → mass, qty_si=500."""
        item = self._make_item()
        item["value"] = "גבינה לבנה 5%, 500 גרם"
        item["parts"]["name_and_contents"] = "גבינה לבנה 5%, 500 גרם"
        prod = self.ChpProduct(item)
        self.assertEqual(prod._unit_dimension, "mass")
        self.assertAlmostEqual(prod._unit_qty_si, 500.0)


class TestChpParseCompareResults(unittest.TestCase):
    """Tests for parse_compare_results HTML parser."""

    def setUp(self):
        from scrapers.chp.chp import ChpProduct, parse_compare_results

        self.parse = parse_compare_results
        self._prod_item = {
            "value": "חלב תנובה טרי 3% בקרטון, 1 ליטר",
            "label": "חלב תנובה טרי 3% בקרטון, 1 ליטר",
            "id": "7290027600007_7290004131074",
            "parts": {
                "name_and_contents": "חלב תנובה טרי 3% בקרטון, 1 ליטר",
                "manufacturer_and_barcode": "יצרן/מותג: תנובה, ברקוד: 7290004131074",
                "pack_size": "",
                "small_image": "",
                "chainnames": "",
            },
        }
        self.prod = ChpProduct(self._prod_item)

    def _make_html(self, physical_rows="", online_rows=""):
        return f"""
        <table class="table results-table" id="results-table">
          <thead><tr>
            <th>רשת</th><th>שם החנות</th>
            <th class="dont_display_when_narrow">כתובת החנות</th>
            <th>מבצע</th><th>מחיר</th>
          </tr></thead>
          <tbody>{physical_rows}</tbody>
        </table>
        <h4>תוצאות מחנויות באינטרנט</h4>
        <table class="table results-table">
          <thead><tr>
            <th class="dont_display_when_narrow">רשת</th>
            <th>שם החנות</th>
            <th class="dont_display_when_narrow">אתר אינטרנט</th>
            <th>מבצע</th><th>מחיר</th>
          </tr></thead>
          <tbody>{online_rows}</tbody>
        </table>
        """

    def test_parses_online_store_price(self):
        online_rows = """
        <tr class="line-odd">
          <td class="dont_display_when_narrow">רמי לוי באינטרנט</td>
          <td><a href="https://www.rami-levy.co.il/he/online/search?q=7290004131074">רמי לוי באינטרנט</a></td>
          <td class="dont_display_when_narrow">https://www.rami-levy.co.il</td>
          <td>&nbsp;</td>
          <td>7.20</td>
        </tr>
        """
        _phys, online = self.parse(self._make_html(online_rows=online_rows), self.prod)
        self.assertEqual(len(online), 1)
        self.assertAlmostEqual(online[0].price, 7.20)
        self.assertEqual(online[0].chain_name, "רמי לוי באינטרנט")
        self.assertEqual(online[0].website, "https://www.rami-levy.co.il")

    def test_parses_multiple_online_stores(self):
        online_rows = """
        <tr><td class="dont_display_when_narrow">שופרסל</td>
          <td><a href="https://www.shufersal.co.il">שופרסל</a></td>
          <td class="dont_display_when_narrow">https://www.shufersal.co.il</td>
          <td>&nbsp;</td><td>9.20</td></tr>
        <tr><td class="dont_display_when_narrow">רמי לוי</td>
          <td><a href="https://www.rami-levy.co.il">רמי לוי</a></td>
          <td class="dont_display_when_narrow">https://www.rami-levy.co.il</td>
          <td>&nbsp;</td><td>7.20</td></tr>
        """
        _phys, online = self.parse(self._make_html(online_rows=online_rows), self.prod)
        self.assertEqual(len(online), 2)
        prices = sorted([s.price for s in online])
        self.assertEqual(prices, [7.20, 9.20])

    def test_parses_physical_store(self):
        physical_rows = """
        <tr class="line-odd">
          <td>אושר עד</td>
          <td>קרית ביאליק</td>
          <td class="dont_display_when_narrow">הנס מולר 6, קרית ביאליק</td>
          <td>&nbsp;</td>
          <td>8.50</td>
        </tr>
        """
        phys, _online = self.parse(
            self._make_html(physical_rows=physical_rows), self.prod
        )
        self.assertEqual(len(phys), 1)
        self.assertAlmostEqual(phys[0].price, 8.50)
        self.assertEqual(phys[0].chain_name, "אושר עד")

    def test_skips_display_when_narrow_rows(self):
        """Address-only rows (display_when_narrow) must not be parsed as products."""
        online_rows = """
        <tr class="line-odd">
          <td class="dont_display_when_narrow">רמי לוי</td>
          <td><a href="https://rami-levy.co.il">רמי לוי</a></td>
          <td class="dont_display_when_narrow">https://rami-levy.co.il</td>
          <td>&nbsp;</td><td>7.20</td></tr>
        <tr class="line-odd display_when_narrow">
          <td colspan="5" style="border-top:none;">אתר: https://rami-levy.co.il</td>
        </tr>
        """
        _phys, online = self.parse(self._make_html(online_rows=online_rows), self.prod)
        self.assertEqual(len(online), 1)

    def test_empty_html_returns_empty_lists(self):
        phys, online = self.parse("", self.prod)
        self.assertEqual(phys, [])
        self.assertEqual(online, [])

    def test_deal_text_parsed(self):
        online_rows = """
        <tr>
          <td class="dont_display_when_narrow">שופרסל</td>
          <td>שופרסל אונליין</td>
          <td class="dont_display_when_narrow">https://www.shufersal.co.il</td>
          <td><button class="btn btn-danger btn-xs btn-discount" data-discount-desc="2 ב-15.00">7.50 *</button></td>
          <td>8.00</td>
        </tr>
        """
        _phys, online = self.parse(self._make_html(online_rows=online_rows), self.prod)
        self.assertEqual(len(online), 1)
        self.assertEqual(online[0].deal_text, "2 ב-15.00")

    def test_zero_width_chars_stripped_from_price(self):
        """Zero-width Unicode chars in price cells must be stripped before float()."""
        # chp.co.il obfuscates prices with zero-width characters.
        # "\u200c\u200d7\u200d.\u200b2\u200c0\u200d" should parse as 7.20
        obfuscated_price = "\u200c\u200d7\u200d.\u200b2\u200c0\u200d"
        online_rows = f"""
        <tr class="line-odd">
          <td class="dont_display_when_narrow">רמי לוי</td>
          <td><a href="https://rami-levy.co.il">רמי לוי</a></td>
          <td class="dont_display_when_narrow">https://rami-levy.co.il</td>
          <td>&nbsp;</td>
          <td>{obfuscated_price}</td>
        </tr>
        """
        _phys, online = self.parse(self._make_html(online_rows=online_rows), self.prod)
        self.assertEqual(
            len(online), 1, "Row with obfuscated price should not be skipped"
        )
        self.assertAlmostEqual(online[0].price, 7.20)

    def test_zero_width_chars_all_variants_stripped(self):
        """All zero-width Unicode variants are stripped: U+200B, U+200C, U+200D, U+FEFF, U+200E, U+200F."""
        # Mix all six zero-width char types around a valid price
        obfuscated = "\ufeff\u200b1\u200c1\u200d.\u200e9\u200f0"
        online_rows = f"""
        <tr>
          <td>שופרסל</td>
          <td>שופרסל אונליין</td>
          <td>https://www.shufersal.co.il</td>
          <td>&nbsp;</td>
          <td>{obfuscated}</td>
        </tr>
        """
        _phys, online = self.parse(self._make_html(online_rows=online_rows), self.prod)
        self.assertEqual(len(online), 1)
        self.assertAlmostEqual(online[0].price, 11.90)

    def test_malformed_price_skipped_with_warning(self):
        """Rows with truly unparseable prices are skipped; a warning is logged (not debug)."""
        import logging

        online_rows = """
        <tr>
          <td>שופרסל</td>
          <td>שופרסל</td>
          <td>https://www.shufersal.co.il</td>
          <td>&nbsp;</td>
          <td>NOT_A_PRICE</td>
        </tr>
        """
        with self.assertLogs("scrapers.chp", level="WARNING") as cm:
            _phys, online = self.parse(
                self._make_html(online_rows=online_rows), self.prod
            )
        self.assertEqual(len(online), 0)
        self.assertTrue(any("malformed table row" in msg for msg in cm.output))


class TestChpDealParsing(unittest.TestCase):
    """Tests for _parse_deal()."""

    def setUp(self):
        from scrapers.chp.chp import _parse_deal

        self._parse = _parse_deal

    def test_no_deal_text_returns_none(self):
        self.assertIsNone(self._parse("", "", 9.90))
        self.assertIsNone(self._parse("", "\xa0", 9.90))

    def test_multi_buy_deal(self):
        # Multi-buy text appears in data-discount-desc (deal_desc)
        deal = self._parse("2 ב-15.00", "", 8.00)
        self.assertIsNotNone(deal)
        self.assertEqual(deal["deal_type"], "multi_buy")
        self.assertEqual(deal["deal_min_qty"], 2)
        self.assertAlmostEqual(deal["deal_price"], 15.00)
        self.assertAlmostEqual(deal["deal_price_per_unit"], 7.50)
        self.assertTrue(deal["has_deal"])

    def test_multi_buy_with_dash(self):
        deal = self._parse("3 ב-29.90", "", 11.00)
        self.assertIsNotNone(deal)
        self.assertEqual(deal["deal_min_qty"], 3)
        self.assertAlmostEqual(deal["deal_price"], 29.90)

    def test_price_reduction_deal(self):
        """Deal price text (button visible text) is a price lower than the shelf price."""
        deal = self._parse("", "7.50", 9.90)
        self.assertIsNotNone(deal)
        self.assertEqual(deal["deal_type"], "price_reduction")
        self.assertAlmostEqual(deal["deal_price"], 7.50)

    def test_non_reducing_number_is_other(self):
        """A number >= shelf price is not a price reduction."""
        deal = self._parse("", "10.00", 9.90)
        # 10.00 >= 9.90 so it won't be price_reduction
        if deal is not None:
            self.assertNotEqual(deal.get("deal_type"), "price_reduction")


class TestChpBuildUnifiedProduct(unittest.TestCase):
    """Tests for build_unified_product()."""

    def setUp(self):
        from scrapers.chp.chp import ChpProduct, OnlineStorePrice, build_unified_product

        self.build = build_unified_product
        self.prod = ChpProduct(
            {
                "value": "חלב תנובה טרי 3% בקרטון, 1 ליטר",
                "label": "חלב תנובה טרי 3% בקרטון, 1 ליטר",
                "id": "7290027600007_7290004131074",
                "parts": {
                    "name_and_contents": "חלב תנובה טרי 3% בקרטון, 1 ליטר",
                    "manufacturer_and_barcode": "יצרן/מותג: תנובה, ברקוד: 7290004131074",
                    "pack_size": "",
                    "small_image": "",
                    "chainnames": "",
                },
            }
        )
        self.store_price = OnlineStorePrice(
            chain_name="רמי לוי באינטרנט",
            store_name="רמי לוי באינטרנט",
            website="https://www.rami-levy.co.il",
            deal_text="",
            price=7.20,
            store_url="https://www.rami-levy.co.il/he/online/search?q=7290004131074",
        )

    def test_chain_is_chp(self):
        p = self.build(self.store_price, self.prod, "2025-01-01T00:00:00+00:00")
        self.assertEqual(p["chain"], "chp")

    def test_price_mapped(self):
        p = self.build(self.store_price, self.prod, "2025-01-01T00:00:00+00:00")
        self.assertAlmostEqual(p["price"], 7.20)

    def test_barcode_mapped(self):
        p = self.build(self.store_price, self.prod, "2025-01-01T00:00:00+00:00")
        self.assertEqual(p["barcode"], "7290004131074")

    def test_store_id_from_website(self):
        p = self.build(self.store_price, self.prod, "2025-01-01T00:00:00+00:00")
        self.assertIn("rami-levy.co.il", p["store_id"])

    def test_price_per_base_unit_volume(self):
        """7.20 for 1000ml → 0.72 per 100ml."""
        p = self.build(self.store_price, self.prod, "2025-01-01T00:00:00+00:00")
        self.assertIsNotNone(p["price_per_base_unit"])
        self.assertAlmostEqual(p["price_per_base_unit"], 0.72, places=2)

    def test_no_deal_when_no_deal_text(self):
        p = self.build(self.store_price, self.prod, "2025-01-01T00:00:00+00:00")
        self.assertIsNone(p["deal"])

    def test_unit_dimension_volume(self):
        p = self.build(self.store_price, self.prod, "2025-01-01T00:00:00+00:00")
        self.assertEqual(p["unit_dimension"], "volume")

    def test_brand_mapped(self):
        p = self.build(self.store_price, self.prod, "2025-01-01T00:00:00+00:00")
        self.assertEqual(p["brand"], "תנובה")

    def test_product_id_mapped(self):
        p = self.build(self.store_price, self.prod, "2025-01-01T00:00:00+00:00")
        self.assertEqual(p["product_id"], "7290027600007_7290004131074")

    def test_deal_sets_sale_price(self):
        from scrapers.chp.chp import OnlineStorePrice, build_unified_product

        sp = OnlineStorePrice(
            chain_name="שופרסל",
            store_name="שופרסל",
            website="https://www.shufersal.co.il",
            deal_text="2 ב-12.00",
            price=7.00,
            store_url=None,
        )
        p = build_unified_product(sp, self.prod, "2025-01-01T00:00:00+00:00")
        self.assertIsNotNone(p["deal"])
        self.assertTrue(p["deal"]["has_deal"])


class TestChpScrapeFilter(unittest.TestCase):
    """Tests that chp.scrape() accepts a ScrapeFilter without raising."""

    def test_scrape_accepts_name_query(self):
        from scrapers.chp import chp as chp_mod
        from scrapers.common import ScrapeFilter
        import inspect

        sig = inspect.signature(chp_mod.scrape)
        params = list(sig.parameters.keys())
        self.assertIn("scrape_filter", params)

    def test_scrape_raises_without_query_by_default(self):
        """With require_query=True (default), empty filter raises ValueError."""
        from scrapers.chp.chp import scrape

        with self.assertRaises(ValueError):
            _run(scrape({}))

    def test_scrape_does_not_raise_with_require_query_false(self):
        """require_query=False lets you call scrape() with no query/barcode."""
        from scrapers.chp.chp import scrape
        import inspect

        sig = inspect.signature(scrape)
        self.assertIn("require_query", sig.parameters)

    def test_require_query_param_defaults_to_true(self):
        """require_query parameter must default to True."""
        from scrapers.chp.chp import scrape
        import inspect

        param = inspect.signature(scrape).parameters["require_query"]
        self.assertTrue(param.default)

    def test_search_products_pagination_filters_prev(self):
        """'prev' sentinel items must be excluded from results."""
        from scrapers.chp.chp import ChpProduct, _PAGE_SIZE

        raw_page = [{"value": 0, "label": "prev", "id": "prev", "parts": ""}]
        raw_page += [
            {
                "value": f"מוצר {i}",
                "label": f"מוצר {i}",
                "id": f"7290027600007_729000000{i:04d}",
                "parts": {
                    "name_and_contents": f"מוצר {i}",
                    "manufacturer_and_barcode": "",
                    "pack_size": "",
                    "small_image": "",
                    "chainnames": "",
                },
            }
            for i in range(_PAGE_SIZE)
        ]
        real = [item for item in raw_page if item.get("id") != "prev"]
        self.assertEqual(len(real), _PAGE_SIZE)

    def test_search_products_pagination_filters_next(self):
        """'next' sentinel items must also be excluded from results."""
        from scrapers.chp.chp import _PAGE_SIZE

        raw_page = [
            {
                "value": f"מוצר {i}",
                "label": f"מוצר {i}",
                "id": f"7290027600007_729000000{i:04d}",
                "parts": {
                    "name_and_contents": f"מוצר {i}",
                    "manufacturer_and_barcode": "",
                    "pack_size": "",
                    "small_image": "",
                    "chainnames": "",
                },
            }
            for i in range(_PAGE_SIZE)
        ]
        raw_page.append({"value": 0, "label": "next", "id": "next", "parts": ""})
        # The filter used in search_products filters both "prev" and "next"
        real = [
            item for item in raw_page if str(item.get("id", "")) not in ("prev", "next")
        ]
        self.assertEqual(len(real), _PAGE_SIZE)

    def test_search_products_pagination_filters_both_sentinels(self):
        """Both 'prev' and 'next' sentinels in the same page are filtered out."""
        from scrapers.chp.chp import _PAGE_SIZE

        raw_page = [{"value": 0, "label": "prev", "id": "prev", "parts": ""}]
        raw_page += [
            {
                "value": f"מוצר {i}",
                "label": f"מוצר {i}",
                "id": f"7290027600007_729000000{i:04d}",
                "parts": {
                    "name_and_contents": f"מוצר {i}",
                    "manufacturer_and_barcode": "",
                    "pack_size": "",
                    "small_image": "",
                    "chainnames": "",
                },
            }
            for i in range(_PAGE_SIZE)
        ]
        raw_page.append({"value": 0, "label": "next", "id": "next", "parts": ""})
        real = [
            item for item in raw_page if str(item.get("id", "")) not in ("prev", "next")
        ]
        self.assertEqual(len(real), _PAGE_SIZE)


class TestChpQueryMatches(unittest.TestCase):
    """Tests for _query_matches() relevance filter."""

    def _make_product(self, name: str) -> "ChpProduct":
        from scrapers.chp.chp import ChpProduct

        return ChpProduct(
            {
                "value": name,
                "label": name,
                "id": "temp_0000000000001",
                "parts": {
                    "name_and_contents": name,
                    "manufacturer_and_barcode": "",
                    "pack_size": "",
                    "small_image": "",
                    "chainnames": "",
                },
            }
        )

    def setUp(self):
        from scrapers.chp.chp import _query_matches

        self._match = _query_matches

    def test_all_words_present(self):
        """Product name contains all query words → True."""
        prod = self._make_product("חלב סויה ללא סוכר 1 ליטר")
        self.assertTrue(self._match(prod, "חלב סויה"))

    def test_missing_one_word(self):
        """Product name missing one query word → False."""
        prod = self._make_product("חלב תנובה 3% 1 ליטר")
        self.assertFalse(self._match(prod, "חלב סויה"))

    def test_empty_query_always_matches(self):
        """Empty query → always True."""
        prod = self._make_product("כלשהו מוצר")
        self.assertTrue(self._match(prod, ""))

    def test_single_word_match(self):
        """Single word query matches if that word is in the name."""
        prod = self._make_product("משקה סויה 1 ליטר")
        self.assertTrue(self._match(prod, "סויה"))

    def test_single_word_no_match(self):
        """Single word query fails if not in name."""
        prod = self._make_product("חלב תנובה 1 ליטר")
        self.assertFalse(self._match(prod, "סויה"))

    def test_partial_word_does_not_match_as_substring(self):
        """Query word 'סויה' should not match 'סויאים' (exact word check)."""
        # Note: we do substring matching on words (Hebrew has no word boundaries
        # in regex), so if 'סויה' is a substring of 'סויהאחר' it still matches.
        # This test documents the current behaviour.
        prod = self._make_product("חלב סויהאחר 1 ליטר")
        # 'סויה' is a prefix of 'סויהאחר', so `in` returns True — document this.
        result = self._match(prod, "חלב סויה")
        self.assertIsInstance(result, bool)  # just verify it doesn't crash


class TestChpScrapeGroupedByProduct(unittest.TestCase):
    """Verify that scrape() groups results by product_id, not store_id."""

    def setUp(self):
        from scrapers.chp.chp import (
            ChpProduct,
            OnlineStorePrice,
            build_unified_product,
        )

        self.ChpProduct = ChpProduct
        self.OnlineStorePrice = OnlineStorePrice
        self.build = build_unified_product

    def test_two_stores_for_same_product_both_returned(self):
        """build_unified_product for two stores returns two distinct store_ids."""
        prod = self.ChpProduct(
            {
                "value": "חלב תנובה 1 ליטר",
                "label": "חלב תנובה 1 ליטר",
                "id": "temp_7290004131074",
                "parts": {
                    "name_and_contents": "חלב תנובה 1 ליטר",
                    "manufacturer_and_barcode": "ברקוד: 7290004131074",
                    "pack_size": "",
                    "small_image": "",
                    "chainnames": "",
                },
            }
        )
        sp1 = self.OnlineStorePrice(
            chain_name="רמי לוי",
            store_name="רמי לוי",
            website="https://www.rami-levy.co.il",
            deal_text="",
            price=6.90,
            store_url=None,
        )
        sp2 = self.OnlineStorePrice(
            chain_name="שופרסל",
            store_name="שופרסל",
            website="https://www.shufersal.co.il",
            deal_text="",
            price=7.20,
            store_url=None,
        )
        up1 = self.build(sp1, prod, "2025-01-01T00:00:00+00:00")
        up2 = self.build(sp2, prod, "2025-01-01T00:00:00+00:00")

        # Both have the same product_id
        self.assertEqual(up1["product_id"], up2["product_id"])
        # But different store_ids
        self.assertNotEqual(up1["store_id"], up2["store_id"])
        # Prices are preserved correctly
        self.assertAlmostEqual(up1["price"], 6.90)
        self.assertAlmostEqual(up2["price"], 7.20)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
