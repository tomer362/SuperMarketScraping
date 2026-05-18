from __future__ import annotations

import asyncio
import inspect
import unittest
from unittest.mock import AsyncMock, patch

from scrapers.chp.chp import (
    ChpProduct,
    CityInfo,
    CompareResultsResult,
    OnlineStorePrice,
    _is_obfuscated_html,
    _extract_price_from_cell,
    build_compare_result_details,
    parse_compare_results,
    scrape,
)
from scrapers.chp.compare_validation import (
    compare_browser_rows_to_parser_details,
    expected_deal_type_from_browser_row,
)


def _make_product() -> ChpProduct:
    return ChpProduct(
        {
            "id": "temp_7290004131074",
            "value": "חלב תנובה טרי 3% בקרטון, 1 ליטר",
            "label": "חלב תנובה טרי 3% בקרטון, 1 ליטר",
            "parts": {
                "name_and_contents": "חלב תנובה טרי 3% בקרטון, 1 ליטר",
                "manufacturer_and_barcode": "יצרן/מותג: תנובה, ברקוד: 7290004131074",
                "pack_size": "",
                "small_image": "",
                "chainnames": "",
            },
        }
    )


def _make_compare_result() -> CompareResultsResult:
    product = _make_product()
    physical_row = OnlineStorePrice(
        chain_name="אושר עד",
        store_name="תל אביב",
        website="",
        deal_text="",
        deal_price_text="",
        price=6.80,
        store_url=None,
        address="רחוב 1, תל אביב",
        is_online=False,
    )
    online_row = OnlineStorePrice(
        chain_name="שופרסל",
        store_name="שופרסל אונליין",
        website="https://www.shufersal.co.il",
        deal_text='6.40 ש"ח ליחידה <BR>בתוקף עד 31/12/2026',
        deal_price_text="6.40 *",
        price=7.20,
        store_url="https://www.shufersal.co.il",
        is_online=True,
    )
    physical_details, online_details = build_compare_result_details(
        [physical_row],
        [online_row],
        product,
        "2026-05-12T00:00:00+00:00",
    )
    return CompareResultsResult(
        product=product,
        physical_rows=[physical_row],
        online_rows=[online_row],
        physical_row_details=physical_details,
        online_row_details=online_details,
        html="<html><body>fixture</body></html>",
        from_offset=0,
        num_results=20,
    )


class TestChpScrapeEnhancements(unittest.TestCase):
    def test_scrape_signature_defaults(self):
        sig = inspect.signature(scrape)
        self.assertTrue(sig.parameters["include_physical"].default)
        self.assertTrue(sig.parameters["include_compare_row_details"].default)
        self.assertFalse(sig.parameters["include_compare_html"].default)

    @patch("scrapers.chp.chp.fetch_compare_results_page", new_callable=AsyncMock)
    @patch("scrapers.chp.chp.search_products", new_callable=AsyncMock)
    @patch("scrapers.chp.chp.get_city", new_callable=AsyncMock)
    def test_scrape_returns_row_details_and_excludes_html_by_default(
        self,
        mock_get_city: AsyncMock,
        mock_search_products: AsyncMock,
        mock_fetch_compare_results_page: AsyncMock,
    ):
        mock_get_city.return_value = CityInfo("תל אביב", "5000", "9000")
        mock_search_products.return_value = [_make_product()]
        mock_fetch_compare_results_page.return_value = _make_compare_result()

        result = asyncio.run(scrape({"name_query": "חלב"}, max_products=1))
        self.assertEqual(result["products_total"], 2)  # physical + online default
        self.assertIn("compare_row_details_by_product", result)
        details = result["compare_row_details_by_product"]["temp_7290004131074"]
        self.assertEqual(details["rows_total"], 2)
        self.assertIsNotNone(details["cheapest_row"])
        self.assertIsNotNone(details["highest_row"])
        self.assertNotIn("html", details)

    @patch("scrapers.chp.chp.fetch_compare_results_page", new_callable=AsyncMock)
    @patch("scrapers.chp.chp.search_products", new_callable=AsyncMock)
    @patch("scrapers.chp.chp.get_city", new_callable=AsyncMock)
    def test_scrape_online_only_excludes_physical_rows(
        self,
        mock_get_city: AsyncMock,
        mock_search_products: AsyncMock,
        mock_fetch_compare_results_page: AsyncMock,
    ):
        mock_get_city.return_value = CityInfo("תל אביב", "5000", "9000")
        mock_search_products.return_value = [_make_product()]
        mock_fetch_compare_results_page.return_value = _make_compare_result()

        result = asyncio.run(
            scrape(
                {"name_query": "חלב"},
                max_products=1,
                include_physical=False,
            )
        )
        self.assertEqual(result["products_total"], 1)
        details = result["compare_row_details_by_product"]["temp_7290004131074"]
        self.assertEqual(len(details["physical_row_details"]), 0)
        self.assertEqual(len(details["online_row_details"]), 1)

    @patch("scrapers.chp.chp.fetch_compare_results_page", new_callable=AsyncMock)
    @patch("scrapers.chp.chp.search_products", new_callable=AsyncMock)
    @patch("scrapers.chp.chp.get_city", new_callable=AsyncMock)
    def test_scrape_compare_html_opt_in(
        self,
        mock_get_city: AsyncMock,
        mock_search_products: AsyncMock,
        mock_fetch_compare_results_page: AsyncMock,
    ):
        mock_get_city.return_value = CityInfo("תל אביב", "5000", "9000")
        mock_search_products.return_value = [_make_product()]
        mock_fetch_compare_results_page.return_value = _make_compare_result()

        result = asyncio.run(
            scrape(
                {"name_query": "חלב"},
                max_products=1,
                include_compare_html=True,
            )
        )
        details = result["compare_row_details_by_product"]["temp_7290004131074"]
        self.assertEqual(details["html"], "<html><body>fixture</body></html>")

    @patch("scrapers.chp.chp.fetch_compare_results_page", new_callable=AsyncMock)
    @patch("scrapers.chp.chp.search_products", new_callable=AsyncMock)
    @patch("scrapers.chp.chp.get_city", new_callable=AsyncMock)
    def test_scrape_can_disable_compare_row_details(
        self,
        mock_get_city: AsyncMock,
        mock_search_products: AsyncMock,
        mock_fetch_compare_results_page: AsyncMock,
    ):
        mock_get_city.return_value = CityInfo("תל אביב", "5000", "9000")
        mock_search_products.return_value = [_make_product()]
        mock_fetch_compare_results_page.return_value = _make_compare_result()

        result = asyncio.run(
            scrape(
                {"name_query": "חלב"},
                max_products=1,
                include_compare_row_details=False,
            )
        )
        self.assertNotIn("compare_row_details_by_product", result)


class TestChpCompareValidationHelpers(unittest.TestCase):
    def test_extract_price_handles_concatenated_obfuscation_pattern(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup("<td>4.510.50</td>", "html.parser")
        cell = soup.find("td")
        self.assertIsNotNone(cell)
        price = _extract_price_from_cell(cell, deal_price_hint="4.50 *")
        # Ambiguous concatenation should resolve to the value closest to deal hint.
        self.assertAlmostEqual(price, 4.51, places=2)

    def test_extract_price_handles_double_dot_pattern(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup("<td>24..9900</td>", "html.parser")
        cell = soup.find("td")
        self.assertIsNotNone(cell)
        price = _extract_price_from_cell(cell)
        self.assertAlmostEqual(price, 24.99, places=2)

    def test_obfuscated_html_detected_by_zero_width_entities(self):
        html = (
            '<table class="results-table"></table>'
            + ("&#8203;&#8204;&#8205;" * 120)
            + ("data-x" * 5)
        )
        self.assertTrue(_is_obfuscated_html(html))

    def test_parse_compare_uses_button_text_and_discount_desc(self):
        product = _make_product()
        html = """
        <table class="table results-table"><tbody></tbody></table>
        <table class="table results-table"><tbody>
          <tr>
            <td>שופרסל</td>
            <td><a href="https://www.shufersal.co.il">שופרסל אונליין</a></td>
            <td>https://www.shufersal.co.il</td>
            <td><button class="btn btn-danger btn-xs btn-discount"
                data-discount-desc='2 ב-15.00<BR>בתוקף עד 01/06/2026'>7.50 *</button></td>
            <td>8.00</td>
          </tr>
        </tbody></table>
        """
        _physical, online = parse_compare_results(html, product)
        self.assertEqual(len(online), 1)
        self.assertEqual(online[0].deal_text, "2 ב-15.00<BR>בתוקף עד 01/06/2026")
        self.assertEqual(online[0].deal_price_text, "7.50 *")

    def test_expected_deal_type_none(self):
        row = {"deal_text": "", "deal_price_text": "", "row_price": 7.2}
        self.assertEqual(expected_deal_type_from_browser_row(row), "none")

    def test_expected_deal_type_price_reduction(self):
        row = {
            "deal_text": '6.40 ש"ח ליחידה<BR>בתוקף עד 31/12/2026',
            "deal_price_text": "6.40 *",
            "row_price": 7.2,
        }
        self.assertEqual(expected_deal_type_from_browser_row(row), "price_reduction")

    def test_expected_deal_type_multi_buy(self):
        row = {"deal_text": "2 ב-12.00", "deal_price_text": "6.00 *", "row_price": 7.2}
        self.assertEqual(expected_deal_type_from_browser_row(row), "multi_buy")

    def test_compare_rows_no_mismatch(self):
        browser_rows = [
            {
                "chain_name": "שופרסל",
                "store_name": "שופרסל אונליין",
                "store_url": "https://www.shufersal.co.il",
                "website": "https://www.shufersal.co.il",
                "deal_text": '6.40 ש"ח ליחידה<BR>בתוקף עד 31/12/2026',
                "deal_price_text": "6.40 *",
                "row_price": 7.2,
                "row_price_raw": "7.20",
            }
        ]
        parser_details = [
            {
                "store": {
                    "chain_name": "שופרסל",
                    "store_name": "שופרסל אונליין",
                    "store_url": "https://www.shufersal.co.il",
                    "website": "https://www.shufersal.co.il",
                },
                "raw": {
                    "deal_text": '6.40 ש"ח ליחידה<BR>בתוקף עד 31/12/2026',
                    "deal_price_text": "6.40 *",
                    "row_price": 7.2,
                },
                "deal": {"deal_type": "price_reduction"},
            }
        ]
        mismatches = compare_browser_rows_to_parser_details(
            browser_rows,
            parser_details,
            row_kind="online",
        )
        self.assertEqual(mismatches, [])

    def test_compare_rows_detects_mismatch(self):
        browser_rows = [
            {
                "chain_name": "שופרסל",
                "store_name": "שופרסל אונליין",
                "store_url": "https://www.shufersal.co.il",
                "website": "https://www.shufersal.co.il",
                "deal_text": "",
                "deal_price_text": "",
                "row_price": 7.2,
                "row_price_raw": "7.20",
            }
        ]
        parser_details = [
            {
                "store": {
                    "chain_name": "שופרסל",
                    "store_name": "שופרסל אונליין",
                    "store_url": "https://www.shufersal.co.il",
                    "website": "https://www.shufersal.co.il",
                },
                "raw": {"deal_text": "", "deal_price_text": "", "row_price": 8.2},
                "deal": None,
            }
        ]
        mismatches = compare_browser_rows_to_parser_details(
            browser_rows,
            parser_details,
            row_kind="online",
        )
        self.assertTrue(any(m["type"] == "price_mismatch" for m in mismatches))


if __name__ == "__main__":
    unittest.main(verbosity=2)
