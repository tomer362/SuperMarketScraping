"""
scrapers/chp/chp.py
===================
Scraper for chp.co.il — Israeli supermarket price-comparison site.

chp.co.il aggregates prices from both physical supermarket branches
and online stores. This scraper fetches the **online stores** section
(תוצאות מחנויות באינטרנט) for each product.

API overview
------------
All endpoints live under https://chp.co.il/

1. **City autocomplete**
   GET /autocompletion/shopping_address?term=<city>&from=0&u=<u>
   Returns: JSON list of {value, label, id}
     id format: "<city_id>_<street_id>"   (street_id=9000 means whole city)

2. **Product autocomplete / search / paginate**
   GET /autocompletion/product_extended
       ?term=<query>
       &from=<offset>          (0, 10, 20, … — 10 results per page, -1 nav item)
       &u=<u>
       &shopping_address=<city label>
       &shopping_address_city_id=<city_id>
       &shopping_address_street_id=<street_id>
   Returns: JSON list; first item on page>0 has id="prev" (navigation sentinel).
   Product ID formats:
     - "7290027600007_<barcode>"  canonical (has product image)
     - "temp_<barcode>"           no canonical image
     - "F_<code>"                 franchise product
     - "our_<N>"                  generic/weighable (no barcode, price per weight)

3. **Compare results** (price data)
   GET /main_page/compare_results
       ?shopping_address=<city label>
       &shopping_address_street_id=<street_id>
       &shopping_address_city_id=<city_id>
       &product_name_or_barcode=<product label>
       &product_barcode=0
       &from=0
       &num_results=20
   Returns: Full HTML page with two <table class="results-table"> tables:
     [0] Physical stores: columns → רשת, שם החנות, כתובת, מבצע, מחיר
     [1] Online stores:   columns → רשת, שם החנות, אתר אינטרנט, מבצע, מחיר
   NOTE: Do NOT use bare=true — that triggers price obfuscation on the server.

The ``u`` parameter
-------------------
A persistent random float in [0, 1) stored in localStorage/cookie under key
"u". Value does not affect responses — any float works. We generate one per
session at startup.

The ``from`` pagination
-----------------------
Results come in pages of 10. ``from=0`` → items 0-9. ``from=10`` → nav "prev"
sentinel + items 10-19. Iterate until fewer than 11 items (no new real items).

Usage
-----
    from scrapers.chp.chp import scrape, update_cities

    # Fetch all products for a search term across all online stores
    result = asyncio.run(scrape({"name_query": "חלב תנובה"}, city="תל אביב"))
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote

import aiohttp
from bs4 import BeautifulSoup

from scrapers.common import (
    DealInfo,
    ScrapeFilter,
    ScrapeResult,
    UnifiedProduct,
    compute_price_per_base_unit,
    make_ssl_context,
    normalize_unit,
    run_concurrently,
    utc_now_iso,
    with_retry,
)

logger = logging.getLogger("scrapers.chp")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://chp.co.il"
CHAIN = "chp"

# Zero-width Unicode characters injected by chp.co.il to obfuscate prices.
# The server injects these when it detects bot-like request patterns (concurrent
# requests from the same session/IP). They appear inside <span>/<div> tags that
# also carry random data-* attribute names to defeat simple CSS selectors.
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff\u200e\u200f]")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://chp.co.il/",
}

_PAGE_SIZE = 10  # items per autocomplete page (excluding "prev" sentinel)


# ---------------------------------------------------------------------------
# City type
# ---------------------------------------------------------------------------


class CityInfo:
    """Represents a city returned by the shopping_address autocomplete."""

    def __init__(self, label: str, city_id: str, street_id: str) -> None:
        self.label = label.strip()
        self.city_id = city_id
        self.street_id = street_id

    @classmethod
    def from_autocomplete_item(cls, item: Dict[str, Any]) -> "CityInfo":
        """Parse a raw autocomplete JSON item into a CityInfo."""
        id_str = str(item["id"])  # format: "<city_id>_<street_id>"
        parts = id_str.split("_", 1)
        city_id = parts[0]
        street_id = parts[1] if len(parts) > 1 else "9000"
        raw_label = item.get("value", item.get("label", ""))
        label = str(raw_label) if raw_label is not None else ""
        return cls(label=label, city_id=city_id, street_id=street_id)

    def __repr__(self) -> str:  # pragma: no cover
        return f"CityInfo(label={self.label!r}, city_id={self.city_id}, street_id={self.street_id})"


# ---------------------------------------------------------------------------
# ChpProduct — intermediate repr before building UnifiedProduct
# ---------------------------------------------------------------------------


class ChpProduct:
    """Intermediate product representation from the product autocomplete."""

    def __init__(self, item: Dict[str, Any]) -> None:
        self.product_id: str = item["id"]
        self.label: str = item.get("value", item.get("label", ""))
        parts: Dict[str, Any] = item.get("parts") or {}
        self.name_and_contents: str = parts.get("name_and_contents", "") or self.label
        self.manufacturer_and_barcode: str = (
            parts.get("manufacturer_and_barcode", "") or ""
        )
        self.pack_size: str = parts.get("pack_size", "") or ""
        self.image_b64: str = parts.get("small_image", "") or ""
        self.chainnames: str = parts.get("chainnames", "") or ""

        # Parse barcode from manufacturer_and_barcode field
        # Format: "יצרן/מותג: תנובה, ברקוד: 7290004131074"
        self.barcode: Optional[str] = None
        self.brand: Optional[str] = None
        if self.manufacturer_and_barcode:
            bc_m = re.search(r"ברקוד[:：]\s*(\d+)", self.manufacturer_and_barcode)
            if bc_m:
                self.barcode = bc_m.group(1)
            brand_m = re.search(
                r"יצרן/מותג[:：]\s*([^,]+)", self.manufacturer_and_barcode
            )
            if brand_m:
                self.brand = brand_m.group(1).strip()

        # Barcode from product_id if temp_ prefix
        if self.barcode is None and self.product_id.startswith("temp_"):
            self.barcode = self.product_id[len("temp_") :]
        elif self.barcode is None and "_" in self.product_id:
            # "7290027600007_7290004131074" → second part is the barcode
            suffix = self.product_id.split("_", 1)[1]
            if suffix.isdigit():
                self.barcode = suffix

        # Parse unit info from name_and_contents (e.g. "חלב תנובה 3%, 1 ליטר")
        self._unit_label: Optional[str] = None
        self._unit_qty: Optional[float] = None
        self._unit_qty_si: Optional[float] = None
        self._unit_dimension: Optional[str] = None
        self._parse_unit()

    def _parse_unit(self) -> None:
        """Parse quantity + unit from the product label."""
        desc = self.name_and_contents or self.label
        _, qty_si, dimension, si_per = normalize_unit(None, None, description=desc)
        if dimension is not None:
            # Re-parse to get the raw qty
            from scrapers.common import _QTY_UNIT_RE, _UNIT_TABLE

            m = _QTY_UNIT_RE.search(desc)
            if m:
                raw_num = m.group(1).replace(",", ".")
                raw_unit = m.group(2)
                qty = float(raw_num)
                canon_label, qty_si2, dimension2, si_per2 = normalize_unit(
                    raw_unit, qty
                )
                self._unit_label = canon_label
                self._unit_qty = qty
                self._unit_qty_si = qty_si2
                self._unit_dimension = dimension2

    @property
    def is_weighable(self) -> bool:
        return self.product_id.startswith("our_")

    def __repr__(self) -> str:  # pragma: no cover
        return f"ChpProduct(id={self.product_id!r}, label={self.label[:40]!r})"


# ---------------------------------------------------------------------------
# OnlineStorePrice — one row from the online stores results table
# ---------------------------------------------------------------------------


class OnlineStorePrice:
    """A single online-store price row from compare_results HTML."""

    def __init__(
        self,
        chain_name: str,
        store_name: str,
        website: str,
        deal_text: str,
        price: float,
        store_url: Optional[str],
        deal_price_text: str = "",
    ) -> None:
        self.chain_name = chain_name
        self.store_name = store_name
        self.website = website
        self.deal_text = deal_text.strip()
        self.deal_price_text = deal_price_text.strip()
        self.price = price
        self.store_url = store_url


# ---------------------------------------------------------------------------
# Session / u-param management
# ---------------------------------------------------------------------------


def _new_u() -> float:
    """Generate a fresh ``u`` value (random float in [0, 1))."""
    return random.random()


def _extract_price_from_cell(cell: Any) -> float:
    """Robustly extract a price float from a compare-results price <td>.

    chp.co.il sometimes returns obfuscated HTML where the price cell contains
    many hidden <span>/<div> elements with random ``data-*`` attributes that
    carry zero-width Unicode chars and interleaved digits from two prices.
    ``get_text(strip=True)`` concatenates all of them into a garbled string.

    This function strips zero-width chars from each element's text, collects
    the remaining printable chars, and then finds the first valid price-looking
    token (e.g. "46.90") among them.

    For clean HTML (no obfuscation), the cell has a single NavigableString
    and this function trivially succeeds on the first pass.
    """
    from bs4 import NavigableString as _NS

    # Fast path: cell has a single NavigableString child (clean HTML)
    children = list(cell.children)
    if len(children) == 1 and isinstance(children[0], _NS):
        text = str(children[0]).strip()
        return float(text)

    # Obfuscated path: collect text from each child element individually,
    # strip zero-width chars, then reassemble and search for a price token.
    # The real price digits are scattered across multiple children.
    # Strategy: strip all zero-width chars from the full get_text, then
    # extract the first valid decimal number — the obfuscation interleaves
    # two prices but the result after stripping zero-widths is still parseable
    # if we pick the right token.
    raw = _ZERO_WIDTH_RE.sub("", cell.get_text())
    # Find all contiguous numeric tokens (digits + at most one dot)
    tokens = re.findall(r"\d+\.?\d*", raw)
    # The real price is typically the shortest valid price among them
    # (the obfuscation concatenates two prices; split them by looking for
    # two decimal-like numbers of roughly equal length)
    for tok in tokens:
        try:
            val = float(tok)
            if 0 < val < 10000:  # sanity check: price must be reasonable
                return val
        except ValueError:
            continue

    raise ValueError(f"Cannot parse price from cell: {cell.get_text(strip=True)!r}")


# ---------------------------------------------------------------------------
# API call helpers
# ---------------------------------------------------------------------------


async def _get_json(
    session: aiohttp.ClientSession,
    url: str,
) -> Any:
    """GET JSON from URL with shared headers."""
    async with session.get(url, headers=_HEADERS, ssl=make_ssl_context()) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)


async def _get_text(
    session: aiohttp.ClientSession,
    url: str,
) -> str:
    """GET text/HTML from URL with shared headers."""
    async with session.get(url, headers=_HEADERS, ssl=make_ssl_context()) as resp:
        resp.raise_for_status()
        return await resp.text()


# ---------------------------------------------------------------------------
# City lookup
# ---------------------------------------------------------------------------


async def search_cities(
    session: aiohttp.ClientSession,
    term: str,
    u: float,
) -> List[CityInfo]:
    """Search for cities matching ``term`` via the shopping_address autocomplete.

    Args:
        session: aiohttp ClientSession.
        term:    Hebrew city name (partial match supported).
        u:       Session random float.

    Returns:
        List of CityInfo objects.
    """
    url = f"{BASE_URL}/autocompletion/shopping_address?term={quote(term)}&from=0&u={u}"
    data = await with_retry(lambda: _get_json(session, url), label=f"city:{term}")
    # Filter out navigation sentinels ("prev", "next")
    real_items = [
        item for item in (data or []) if str(item.get("id", "")) not in ("prev", "next")
    ]
    return [CityInfo.from_autocomplete_item(item) for item in real_items]


async def get_city(
    session: aiohttp.ClientSession,
    city_name: str,
    u: float,
) -> Optional[CityInfo]:
    """Return the best-matching CityInfo for ``city_name``, or None."""
    cities = await search_cities(session, city_name, u)
    return cities[0] if cities else None


# ---------------------------------------------------------------------------
# Product search / pagination
# ---------------------------------------------------------------------------


async def _fetch_product_page(
    session: aiohttp.ClientSession,
    term: str,
    from_offset: int,
    city: Optional[CityInfo],
    u: float,
) -> List[Dict[str, Any]]:
    """Fetch one page of product autocomplete results."""
    city_label = quote(city.label) if city else ""
    city_id = city.city_id if city else "0"
    street_id = city.street_id if city else "0"
    url = (
        f"{BASE_URL}/autocompletion/product_extended"
        f"?term={quote(term)}"
        f"&from={from_offset}"
        f"&u={u}"
        f"&shopping_address={city_label}"
        f"&shopping_address_city_id={city_id}"
        f"&shopping_address_street_id={street_id}"
    )
    data = await with_retry(
        lambda: _get_json(session, url), label=f"products:{term}:{from_offset}"
    )
    return data or []


async def search_products(
    session: aiohttp.ClientSession,
    term: str,
    city: Optional[CityInfo],
    u: float,
    max_results: int = 200,
) -> List[ChpProduct]:
    """Search for products matching ``term``, paginating through all results.

    Args:
        session:     aiohttp ClientSession.
        term:        Hebrew product name or barcode.
        city:        Optional CityInfo for location-scoped search.
        u:           Session random float.
        max_results: Maximum products to return.

    Returns:
        List of ChpProduct objects.
    """
    products: List[ChpProduct] = []
    offset = 0

    while len(products) < max_results:
        page = await _fetch_product_page(session, term, offset, city, u)
        # Filter out nav sentinels ("prev", "next") — they have integer value=0
        real_items = [
            item for item in page if str(item.get("id", "")) not in ("prev", "next")
        ]
        for item in real_items:
            if len(products) >= max_results:
                break
            try:
                products.append(ChpProduct(item))
            except Exception as exc:
                logger.warning("Skipping malformed product item: %s", exc)

        # Pagination: fewer than _PAGE_SIZE real items → last page
        if len(real_items) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE

    return products


# ---------------------------------------------------------------------------
# Compare results HTML parser
# ---------------------------------------------------------------------------


def _parse_deal(
    deal_desc: str, deal_price_text: str, price: float
) -> Optional[DealInfo]:
    """Parse a deal from the discount button's data attributes.

    Args:
        deal_desc:       Content of data-discount-desc attribute, e.g.
                         "29.90 ש\"ח ליחידה<BR>בתוקף עד 11/04/2026"
                         or "2 ב-59.80<BR>בתוקף עד 11/04/2026"
        deal_price_text: Visible button text (stripped), e.g. "29.90 *" or "29.90"
        price:           Regular shelf price (float).

    Returns:
        DealInfo dict or None when there is no deal.
    """
    # Strip non-breaking spaces and regular whitespace from both inputs
    deal_desc = (deal_desc or "").replace("\xa0", " ").strip()
    deal_price_text = (deal_price_text or "").replace("\xa0", " ").strip()

    if not deal_desc and not deal_price_text:
        return None

    # Clean up the description — strip HTML tags and extra whitespace
    clean_desc = re.sub(r"<[^>]+>", " ", deal_desc).strip()
    # Strip trailing " *" from deal_price_text
    deal_price_str = re.sub(r"\s*\*\s*$", "", deal_price_text).strip()

    deal: DealInfo = {
        "has_deal": True,
        "deal_type": "other",
        "deal_description": clean_desc or deal_price_str,
        "deal_price": None,
        "deal_min_qty": None,
        "deal_price_per_unit": None,
        "price_per_base_unit": None,
        "price_per_base_unit_deal": None,
    }

    # Pattern: "N ב-XX.XX" — multi-buy (appears in clean_desc)
    multi_m = re.search(r"(\d+)\s+ב[-–]?\s*([\d.]+)", clean_desc)
    if multi_m:
        qty = int(multi_m.group(1))
        total = float(multi_m.group(2))
        deal["deal_type"] = "multi_buy"
        deal["deal_min_qty"] = qty
        deal["deal_price"] = total
        deal["deal_price_per_unit"] = round(total / qty, 4) if qty else None
        return deal

    # Pattern: sale price from deal_price_str (e.g. "29.90")
    if deal_price_str:
        price_m = re.match(r"^([\d.]+)$", deal_price_str)
        if price_m:
            dp = float(price_m.group(1))
            if dp < price:
                deal["deal_type"] = "price_reduction"
                deal["deal_price"] = dp
                deal["deal_price_per_unit"] = dp
                return deal

    # Fallback: try extracting a price from the description
    price_m = re.search(r"([\d.]+)\s*ש", clean_desc)
    if price_m:
        dp = float(price_m.group(1))
        if dp < price:
            deal["deal_type"] = "price_reduction"
            deal["deal_price"] = dp
            deal["deal_price_per_unit"] = dp
            return deal

    return deal


def parse_compare_results(
    html: str,
    product: ChpProduct,
) -> Tuple[List[OnlineStorePrice], List[OnlineStorePrice]]:
    """Parse the compare_results HTML fragment.

    Returns:
        (physical_prices, online_prices) — each is a list of OnlineStorePrice.
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table", class_="results-table")

    def _parse_table(table: Any, is_online: bool) -> List[OnlineStorePrice]:
        results: List[OnlineStorePrice] = []
        if table is None:
            return results
        tbody = table.find("tbody")
        if tbody is None:
            return results
        all_rows = tbody.find_all("tr")
        # Rows come in pairs: main row + address row (display_when_narrow).
        # Main rows do NOT have class "display_when_narrow".
        main_rows = [
            r for r in all_rows if "display_when_narrow" not in (r.get("class") or [])
        ]
        for row in main_rows:
            cells = row.find_all("td")
            if not cells:
                continue
            # Online table: [chain, store_name, website, deal, price]
            # Physical table: [chain, store_name, address, deal, price]
            if len(cells) < 5:
                continue
            try:
                chain_name = cells[0].get_text(strip=True)
                store_name = cells[1].get_text(strip=True)
                # For online: cells[2] is website URL
                # For physical: cells[2] is address (has dont_display_when_narrow class)
                website = cells[2].get_text(strip=True) if is_online else ""
                # Deal cell: look for <button class="btn-discount"> with data attributes
                deal_cell = cells[3]
                btn = deal_cell.find("button", class_="btn-discount")
                if btn is not None:
                    deal_desc = btn.get("data-discount-desc", "")
                    deal_price_text = btn.get_text(strip=True)
                else:
                    deal_desc = ""
                    deal_price_text = ""
                # Price: clean plain text, strip zero-width chars defensively
                price_raw = cells[4].get_text(strip=True)
                price_text = _ZERO_WIDTH_RE.sub("", price_raw).replace(",", "")
                price = float(price_text)
                # Store link href (for online stores)
                link = cells[1].find("a")
                store_url = link.get("href") if link else None
                results.append(
                    OnlineStorePrice(
                        chain_name=chain_name,
                        store_name=store_name,
                        website=website,
                        deal_text=deal_desc,
                        price=price,
                        store_url=store_url,
                        deal_price_text=deal_price_text,
                    )
                )
            except (ValueError, IndexError) as exc:
                logger.warning("Skipping malformed table row: %s", exc)
        return results

    physical: List[OnlineStorePrice] = []
    online: List[OnlineStorePrice] = []

    if len(tables) >= 1:
        physical = _parse_table(tables[0], is_online=False)
    if len(tables) >= 2:
        online = _parse_table(tables[1], is_online=True)

    return physical, online


# ---------------------------------------------------------------------------
# Fetch compare results for one product
# ---------------------------------------------------------------------------


async def fetch_compare_results(
    session: aiohttp.ClientSession,
    product: ChpProduct,
    city: CityInfo,
    u: float,
    max_retries: int = 3,
    retry_delay: float = 5.0,
) -> Tuple[List[OnlineStorePrice], List[OnlineStorePrice]]:
    """Fetch and parse compare results for a single product + city.

    Uses a fresh ``aiohttp.ClientSession`` per call to avoid bot-detection by
    chp.co.il, which returns obfuscated HTML (with zero-width Unicode and
    interleaved digit spans) when it detects concurrent requests from the same
    TCP connection.  If an obfuscated response is detected (HTML > 200 KB),
    the call is retried after ``retry_delay`` seconds with a new session and
    a fresh ``u`` value.

    Returns:
        (physical_prices, online_prices)
    """
    url = (
        f"{BASE_URL}/main_page/compare_results"
        f"?shopping_address={quote(city.label)}"
        f"&shopping_address_street_id={city.street_id}"
        f"&shopping_address_city_id={city.city_id}"
        f"&product_name_or_barcode={quote(product.label)}"
        f"&product_barcode=0"
        f"&from=0"
        f"&num_results=20"
    )

    ssl_ctx = make_ssl_context()

    for attempt in range(max_retries):
        fresh_u = _new_u()
        connector = aiohttp.TCPConnector(ssl=ssl_ctx, limit=5)
        async with aiohttp.ClientSession(connector=connector) as fresh_session:
            html = await with_retry(
                lambda: _get_text(fresh_session, url),
                label=f"compare:{product.product_id}",
            )

        # Obfuscated responses are ~10-20× larger than clean ones.
        # A clean response for a popular product is typically 40-150 KB;
        # an obfuscated one is 500 KB – 2 MB.
        if len(html) > 200_000:
            logger.warning(
                "Obfuscated response detected for %s (%d bytes); "
                "retrying after %.1fs (attempt %d/%d)",
                product.product_id,
                len(html),
                retry_delay,
                attempt + 1,
                max_retries,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay + random.uniform(0, 2.0))
            continue

        return parse_compare_results(html, product)

    # All retries exhausted — raise so the caller can record the failure
    raise RuntimeError(
        f"All {max_retries} retries failed for {product.product_id}: "
        "server kept returning obfuscated HTML (>200 KB). "
        "Data loss is unacceptable; raising instead of returning empty."
    )


# ---------------------------------------------------------------------------
# Build UnifiedProduct from an online store price row
# ---------------------------------------------------------------------------


def build_unified_product(
    store_price: OnlineStorePrice,
    product: ChpProduct,
    scraped_at: str,
) -> UnifiedProduct:
    """Convert a parsed OnlineStorePrice row + ChpProduct into a UnifiedProduct."""
    price = store_price.price
    deal = _parse_deal(store_price.deal_text, store_price.deal_price_text, price)

    regular_price = price
    sale_price: Optional[float] = None
    discount_percent: Optional[float] = None

    if (
        deal
        and deal.get("deal_type") == "price_reduction"
        and deal.get("deal_price") is not None
    ):
        sale_price = deal["deal_price"]
        price = sale_price
        if regular_price > 0:
            discount_percent = round((1 - price / regular_price) * 100, 2)

    ppbu = compute_price_per_base_unit(
        price,
        product._unit_qty_si,
        product._unit_dimension,
        is_weighable=product.is_weighable,
    )

    # store_id: use website domain as a stable identifier
    website = store_price.website or store_price.store_url or store_price.chain_name
    store_id = re.sub(r"https?://(?:www\.)?", "", website).rstrip("/")

    return UnifiedProduct(
        chain=CHAIN,
        store_id=store_id,
        store_name=store_price.store_name or store_price.chain_name,
        product_id=product.product_id,
        name=product.name_and_contents or product.label,
        price=price,
        regular_price=regular_price,
        sale_price=sale_price,
        discount_percent=discount_percent,
        barcode=product.barcode,
        image_url=None,  # images are base64-embedded; too large to include
        category_ids=[],
        is_weighable=product.is_weighable,
        unit_description=product.label,
        unit_of_measure=product._unit_label,
        unit_qty=product._unit_qty,
        unit_qty_si=product._unit_qty_si,
        unit_dimension=product._unit_dimension,
        price_per_base_unit=ppbu,
        deal=deal,
        brand=product.brand,
        manufacturer=product.brand,
        scraped_at=scraped_at,
    )


# ---------------------------------------------------------------------------
# Main scrape() entry point
# ---------------------------------------------------------------------------


def _query_matches(product: "ChpProduct", query: str) -> bool:
    """Return True if *all* words in ``query`` appear in the product name.

    This is a **loose relevance guard** — it is intentionally not called by
    default in ``scrape()``.  The server's own autocomplete ranking is
    generally good enough; use this only if you want an extra post-filter
    on top of the server results.

    Matching is done as simple substring checks (Hebrew has no word-boundary
    regex), so "חלב" will also match "חלבון".
    """
    if not query:
        return True
    name = (product.name_and_contents or product.label or "").strip()
    words = query.split()
    return all(w in name for w in words)


# ---------------------------------------------------------------------------
# Sitemap-based product ID enumeration (used by --all / browse_all)
# ---------------------------------------------------------------------------

_SITEMAP_TOTAL_PAGES = 1473
_SITEMAP_ID_RE = re.compile(r"(7290027600007_\d+|temp_\d+|our_\d+|F_[\w]+|Q_[\w]+)")


def _extract_product_ids_from_sitemap_html(html: str) -> set:
    """Extract chp.co.il product IDs from one sitemap page's HTML."""
    ids: set = set()
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html)
    for href in hrefs:
        decoded = unquote(href)
        for part in decoded.rstrip("/").split("/"):
            if _SITEMAP_ID_RE.fullmatch(part):
                ids.add(part)
    return ids


async def _fetch_sitemap_page(session: aiohttp.ClientSession, page_num: int) -> set:
    """Fetch one sitemap page and return the set of product IDs found."""
    url = f"{BASE_URL}/sitemap/{page_num}"
    try:
        html = await with_retry(
            lambda: _get_text(session, url),
            label=f"sitemap:{page_num}",
        )
        return _extract_product_ids_from_sitemap_html(html)
    except Exception as exc:
        logger.warning("Sitemap page %d failed: %s", page_num, exc)
        return set()


async def enumerate_all_products_via_sitemap(
    session: aiohttp.ClientSession,
    max_products: int = 0,
    concurrency: int = 5,
    inter_page_delay: float = 0.2,
) -> List[str]:
    """Enumerate product IDs from chp.co.il sitemap pages.

    Fetches sitemap pages 1 through :data:`_SITEMAP_TOTAL_PAGES` in small
    concurrent batches, extracting product IDs from each.  Stops early when
    ``max_products`` unique IDs have been collected (0 = no limit).

    Args:
        session:          aiohttp ClientSession.
        max_products:     Stop after this many unique product IDs (0 = all).
        concurrency:      Concurrent sitemap page fetches per batch.
        inter_page_delay: Seconds to pause between batches.

    Returns:
        Sorted list of unique product ID strings.
    """
    seen: set = set()
    all_page_nums = list(range(1, _SITEMAP_TOTAL_PAGES + 1))

    for batch_start in range(0, len(all_page_nums), concurrency):
        batch = all_page_nums[batch_start : batch_start + concurrency]
        tasks = [lambda p=page: _fetch_sitemap_page(session, p) for page in batch]
        results = await run_concurrently(tasks, max_concurrent=concurrency)
        for result in results:
            if isinstance(result, Exception):
                logger.warning("Sitemap batch error: %s", result)
                continue
            seen.update(result)

        if max_products and len(seen) >= max_products:
            break

        if batch_start + concurrency < len(all_page_nums):
            await asyncio.sleep(inter_page_delay)

    product_ids = sorted(seen)
    if max_products:
        product_ids = product_ids[:max_products]
    logger.info("Sitemap enumeration complete: %d unique product IDs", len(product_ids))
    return product_ids


def _sitemap_id_to_chp_product(product_id: str) -> ChpProduct:
    """Build a minimal ChpProduct from a sitemap product ID.

    The sitemap only gives us the product ID, not the full autocomplete payload.
    We synthesise a minimal item dict so ``ChpProduct`` can be constructed.
    The ``label`` will just be the product_id itself; the compare_results HTML
    is fetched by product label, so this works for the API call.
    """
    item: Dict[str, Any] = {
        "id": product_id,
        "value": product_id,
        "label": product_id,
        "parts": {},
    }
    return ChpProduct(item)


async def scrape(
    scrape_filter: Optional[ScrapeFilter] = None,
    *,
    city: str = "תל אביב",
    max_products: int = 200,
    max_concurrent: int = 1,
    inter_request_delay: float = 3.5,
    max_retries: int = 3,
    base_delay: float = 1.0,
    require_query: bool = True,
    browse_all: bool = False,
) -> ScrapeResult:
    """Scrape online store prices from chp.co.il.

    For each product matching the filter, fetches compare results and
    returns UnifiedProduct records for every online store that lists it.

    Results are grouped **by product** (keyed by ``product_id``).  The
    ``products_by_store`` field of ``ScrapeResult`` is reused as
    ``products_by_product`` for schema compatibility — each key is a
    ``product_id``; each value is a list of ``UnifiedProduct`` records,
    one per online store that carries that product, sorted cheapest first.

    The server's autocomplete already ranks results by relevance — **no
    client-side word filtering is applied**.  All products returned by the
    API for the given term are fetched and included.

    Args:
        scrape_filter:    Optional filter with ``name_query`` and/or ``barcode``.
                          When both are absent and ``browse_all=True``, product IDs
                          are enumerated from the sitemap instead of the search API.
        city:             Hebrew city name for location context (default: תל אביב).
        max_products:     Maximum number of products to fetch compare results for.
        max_concurrent:   Concurrent compare_results requests per batch.
                          chp.co.il activates JavaScript-based price obfuscation
                          when it detects concurrent or rapid requests from the
                          same session/IP. Defaults to 1 (sequential).  Each
                          compare_results call uses its own fresh TCP session.
        inter_request_delay: Seconds to pause between request batches.
                          Must be ≥ 3s to reliably avoid bot-detection.
        max_retries:      Retry attempts per request.
        base_delay:       Base delay for exponential backoff.
        require_query:    If True (default), raise ValueError when no query/barcode
                          is given.  Set False or use ``browse_all=True`` instead.
        browse_all:       When True, enumerate all products via sitemap instead of
                          the search API.  Ignores ``require_query``.

    Returns:
        ScrapeResult with ``products_by_store`` keyed by ``product_id``.
        ``stores_scraped`` is the number of distinct online stores seen.
    """
    scrape_filter = scrape_filter or {}
    name_query: str = scrape_filter.get("name_query", "") or ""
    barcode: str = scrape_filter.get("barcode", "") or ""

    # Use barcode as search term if no name query
    term = barcode if barcode and not name_query else name_query
    if not term and not browse_all and require_query:
        raise ValueError(
            "scrape() requires scrape_filter with 'name_query' or 'barcode', "
            "or pass browse_all=True to enumerate all products via sitemap."
        )

    started_at = utc_now_iso()
    import time

    t0 = time.monotonic()
    errors: List[str] = []

    u = _new_u()
    ssl_ctx = make_ssl_context()
    connector = aiohttp.TCPConnector(ssl=ssl_ctx, limit=max_concurrent + 5)

    async with aiohttp.ClientSession(connector=connector) as session:
        # 1. Resolve city
        city_info = await get_city(session, city, u)
        if city_info is None:
            errors.append(f"City not found: {city!r}")
            city_info = CityInfo(label=city, city_id="0", street_id="9000")
            logger.warning("City %r not found — using city_id=0 (all Israel)", city)

        logger.info(
            "City resolved: %s (city_id=%s)", city_info.label, city_info.city_id
        )

        # 2. Enumerate products — either via search API or sitemap (browse_all).
        if browse_all:
            logger.info(
                "browse_all=True: enumerating product IDs from sitemap "
                "(max_products=%d, 0=all)",
                max_products,
            )
            try:
                product_ids = await enumerate_all_products_via_sitemap(
                    session,
                    max_products=max_products,
                    concurrency=5,
                    inter_page_delay=0.2,
                )
            except Exception as exc:
                errors.append(f"Sitemap enumeration failed: {exc}")
                product_ids = []
            products = [_sitemap_id_to_chp_product(pid) for pid in product_ids]
            logger.info("Sitemap enumerated %d products", len(products))
        else:
            logger.info("Searching products for term=%r", term or "(all)")
            try:
                products = await search_products(
                    session, term, city_info, u, max_results=max_products
                )
            except Exception as exc:
                errors.append(f"Product search failed: {exc}")
                products = []
            logger.info("Found %d products", len(products))

        if not products:
            return ScrapeResult(
                chain=CHAIN,
                stores_scraped=0,
                products_total=0,
                products_by_store={},
                scraped_at=started_at,
                duration_seconds=round(time.monotonic() - t0, 2),
                errors=errors,
            )

        # 3. Fetch compare results sequentially (default max_concurrent=1) with
        #    an inter-batch delay to avoid triggering the server's bot-detection.
        #    fetch_compare_results() uses a fresh TCP session per call internally.
        async def _fetch_one(prod: ChpProduct):
            try:
                _phys, online = await fetch_compare_results(session, prod, city_info, u)
                return (prod, online)
            except Exception as exc:
                errors.append(f"compare_results failed for {prod.product_id}: {exc}")
                return (prod, [])

        pair_results = []
        for batch_start in range(0, len(products), max_concurrent):
            batch = products[batch_start : batch_start + max_concurrent]
            tasks = [lambda p=prod: _fetch_one(p) for prod in batch]
            batch_results = await run_concurrently(tasks, max_concurrent=max_concurrent)
            pair_results.extend(batch_results)
            # Polite inter-batch delay with small jitter
            if batch_start + max_concurrent < len(products):
                jitter = random.uniform(0, inter_request_delay * 0.5)
                await asyncio.sleep(inter_request_delay + jitter)

        # 4. Build UnifiedProducts — group by product_id.
        #    Within each product, sort stores cheapest first.
        products_by_product: Dict[str, List[UnifiedProduct]] = {}
        store_ids: set = set()
        total = 0
        scraped_at = utc_now_iso()

        for result in pair_results:
            if isinstance(result, Exception):
                errors.append(str(result))
                continue
            prod, online_prices = result
            if not online_prices:
                continue
            pid = prod.product_id
            if pid not in products_by_product:
                products_by_product[pid] = []
            for sp in online_prices:
                up = build_unified_product(sp, prod, scraped_at)
                products_by_product[pid].append(up)
                store_ids.add(up["store_id"])
                total += 1

        # Sort each product's store list cheapest first
        for pid in products_by_product:
            products_by_product[pid].sort(key=lambda u: u["price"])

    return ScrapeResult(
        chain=CHAIN,
        stores_scraped=len(store_ids),
        products_total=total,
        products_by_store=products_by_product,  # keyed by product_id
        scraped_at=started_at,
        duration_seconds=round(time.monotonic() - t0, 2),
        errors=errors,
    )


# ---------------------------------------------------------------------------
# update_cities() — enumerate known cities (convention used by main.py)
# ---------------------------------------------------------------------------


async def update_cities(
    session: Optional[aiohttp.ClientSession] = None,
) -> List[Dict[str, str]]:
    """Return a list of well-known Israeli cities supported by chp.co.il.

    This does NOT call a listing API (none exists) — it returns a curated
    list of major cities.  Each entry has keys: ``name``, ``city_id``,
    ``street_id``.
    """
    # These are validated city IDs from the shopping_address autocomplete.
    return [
        {"name": "תל אביב", "city_id": "5000", "street_id": "9000"},
        {"name": "ירושלים", "city_id": "3000", "street_id": "9000"},
        {"name": "חיפה", "city_id": "4000", "street_id": "9000"},
        {"name": "ראשון לציון", "city_id": "7400", "street_id": "9000"},
        {"name": "פתח תקווה", "city_id": "7900", "street_id": "9000"},
        {"name": "אשדוד", "city_id": "7000", "street_id": "9000"},
        {"name": "נתניה", "city_id": "7100", "street_id": "9000"},
        {"name": "באר שבע", "city_id": "9000", "street_id": "9000"},
        {"name": "בני ברק", "city_id": "6200", "street_id": "9000"},
        {"name": "רחובות", "city_id": "8400", "street_id": "9000"},
        {"name": "בת ים", "city_id": "6100", "street_id": "9000"},
        {"name": "הרצליה", "city_id": "6600", "street_id": "9000"},
        {"name": "חולון", "city_id": "6400", "street_id": "9000"},
        {"name": "רמת גן", "city_id": "5200", "street_id": "9000"},
        {"name": "קרית ביאליק", "city_id": "9500", "street_id": "9000"},
    ]
