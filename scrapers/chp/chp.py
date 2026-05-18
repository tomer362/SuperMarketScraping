"""
scrapers/chp/chp.py
===================
Scraper for chp.co.il — Israeli supermarket price-comparison site.

chp.co.il aggregates prices from both physical supermarket branches
and online stores. This scraper fetches and parses both comparison tables,
including rich per-row price/deal/store details.

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
from dataclasses import dataclass
import logging
import random
import re
from typing import Any, AsyncIterator, Dict, List, Literal, Optional, Tuple
from urllib.parse import quote, unquote

import aiohttp
from bs4 import BeautifulSoup, NavigableString, Tag

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
_ZERO_WIDTH_ENTITY_MARKERS = (
    "&#8203;",
    "&#8204;",
    "&#8205;",
    "&#8206;",
    "&#8207;",
    "&#65279;",
)

_CSS_RULE_BLOCK_RE = re.compile(r"([^{]+)\{([^}]+)\}")
_CSS_SELECTOR_ID_DATA_RE = re.compile(
    r'^#([A-Za-z0-9_-]+)\[data-([A-Za-z0-9_-]+)="([^"]+)"\]$',
    re.I,
)
_CSS_SELECTOR_TAG_DATA_RE = re.compile(
    r'^([A-Za-z0-9_-]+)\[data-([A-Za-z0-9_-]+)="([^"]+)"\]$',
    re.I,
)
_CSS_SELECTOR_DATA_RE = re.compile(
    r'^\[data-([A-Za-z0-9_-]+)="([^"]+)"\]$',
    re.I,
)


def _strip_zero_width(text: str) -> str:
    """Remove server-injected zero-width characters from extracted text."""
    return _ZERO_WIDTH_RE.sub("", text or "")


def _css_selector_to_visibility_rule(
    selector: str, props: str
) -> Optional[Dict[str, Any]]:
    """Convert a tiny subset of CSS selectors into a visibility rule.

    CHP obfuscation styles mainly look like:
      [data-foo="bar"]{display:none}
      span[data-foo="bar"]{display:inline}
      #abc[data-foo="bar"]{display:none}
    """
    selector = selector.strip()
    props = props.strip()
    compact = props.replace(" ", "").lower()

    m = _CSS_SELECTOR_ID_DATA_RE.match(selector)
    if m:
        return {
            "tag": None,
            "attr_name": f"data-{m.group(2).lower()}",
            "attr_value": m.group(3),
            "has_id": m.group(1),
            "display_none": "display:none" in compact,
            "specificity": 3,
        }

    m = _CSS_SELECTOR_TAG_DATA_RE.match(selector)
    if m:
        return {
            "tag": m.group(1).lower(),
            "attr_name": f"data-{m.group(2).lower()}",
            "attr_value": m.group(3),
            "has_id": None,
            "display_none": "display:none" in compact,
            "specificity": 2,
        }

    m = _CSS_SELECTOR_DATA_RE.match(selector)
    if m:
        return {
            "tag": None,
            "attr_name": f"data-{m.group(1).lower()}",
            "attr_value": m.group(2),
            "has_id": None,
            "display_none": "display:none" in compact,
            "specificity": 1,
        }

    return None


def _parse_visibility_rules_from_soup(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """Extract relevant inline CSS visibility rules from <style> blocks."""
    rules: List[Dict[str, Any]] = []
    for style in soup.find_all("style"):
        css_text = style.get_text(" ", strip=False)
        for m in _CSS_RULE_BLOCK_RE.finditer(css_text):
            rule = _css_selector_to_visibility_rule(m.group(1), m.group(2))
            if rule:
                rules.append(rule)
    return rules


def _is_tag_visible(tag_name: str, attrs: Dict[str, str], el_id: str, rules: List[Dict[str, Any]]) -> bool:
    """Return visibility for a tag based on the highest-specificity matched rule."""
    tag_name = tag_name.lower()
    matched: List[Dict[str, Any]] = []
    for rule in rules:
        if attrs.get(rule["attr_name"]) != rule["attr_value"]:
            continue
        if rule["has_id"] is not None and rule["has_id"] != el_id:
            continue
        if rule["tag"] is not None and rule["tag"] != tag_name:
            continue
        matched.append(rule)

    if not matched:
        return True

    best = max(matched, key=lambda r: int(r["specificity"]))
    return not bool(best["display_none"])


def _collect_visible_text(node: Any, rules: List[Dict[str, Any]], out: List[str]) -> None:
    """Recursively collect text that is visible according to parsed CSS rules."""
    if isinstance(node, NavigableString):
        text = _strip_zero_width(str(node))
        if text:
            out.append(text)
        return

    if not isinstance(node, Tag):
        return

    if node.name and node.name.lower() == "br":
        out.append(" ")
        return

    attrs = {
        k.lower(): str(v)
        for k, v in node.attrs.items()
        if isinstance(k, str) and k.lower().startswith("data-")
    }
    el_id = str(node.get("id") or "")
    if not _is_tag_visible(node.name or "", attrs, el_id, rules):
        return

    for child in node.children:
        _collect_visible_text(child, rules, out)


def _extract_visible_text(element: Any, rules: List[Dict[str, Any]]) -> str:
    """Extract text as-rendered (best effort) from a BeautifulSoup element."""
    if element is None:
        return ""
    parts: List[str] = []
    _collect_visible_text(element, rules, parts)
    text = "".join(parts)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_price_from_text(text: str, deal_price_hint: str = "") -> Optional[float]:
    """Parse price from text while tolerating minor CHP formatting artifacts."""
    max_reasonable_price = 500.0
    cleaned = _strip_zero_width(text).replace("\xa0", " ").replace("₪", "")
    cleaned = cleaned.replace(",", ".")
    cleaned = re.sub(r"\s+", "", cleaned)
    if not cleaned:
        return None

    # Fast path for clean values.
    try:
        value = float(cleaned)
        if 0 < value < max_reasonable_price:
            return value
    except ValueError:
        pass

    # Collapse repeated decimal separators (e.g. "24..9900").
    if ".." in cleaned:
        collapsed = re.sub(r"\.{2,}", ".", cleaned)
        try:
            value = float(collapsed)
            if 0 < value < max_reasonable_price:
                return value
        except ValueError:
            cleaned = collapsed

    # Keep only numeric punctuation for candidate extraction.
    compact = re.sub(r"[^0-9.]", "", cleaned)
    if not compact:
        return None

    candidates: List[float] = []
    for m in re.finditer(r"(?=(\d{1,5}\.\d{1,2}))", compact):
        try:
            value = float(m.group(1))
        except ValueError:
            continue
        if 0 < value < max_reasonable_price:
            candidates.append(value)

    if not candidates:
        for tok in re.findall(r"\d+\.?\d*", compact):
            try:
                value = float(tok)
            except ValueError:
                continue
            if 0 < value < max_reasonable_price:
                candidates.append(value)

    if not candidates:
        return None

    # De-duplicate while preserving order.
    seen: set = set()
    uniq_candidates: List[float] = []
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        uniq_candidates.append(c)

    hint_m = re.search(r"(\d+(?:\.\d+)?)", deal_price_hint.replace(",", "."))
    if hint_m:
        try:
            hint = float(hint_m.group(1))
        except ValueError:
            hint = None
        if hint is not None:
            return min(uniq_candidates, key=lambda c: abs(c - hint))

    return uniq_candidates[0]


def _is_obfuscated_html(html: str) -> bool:
    """Return True if the compare_results response is obfuscated by the server.

    The server's bot-detection injects thousands of zero-width Unicode
    characters (U+200B … U+200F, U+FEFF) throughout obfuscated HTML, producing
    pages that are 500 KB – 2 MB instead of the expected 5–150 KB.

    Detection strategy (two independent signals, either alone is sufficient):

    1. **Zero-width character count** — a legitimate compare_results page has at
       most a handful; an obfuscated one has thousands.  Threshold: > 200.
    2. **DOM structure absent** — if the HTML is suspiciously large (> 150 KB)
       *and* contains no ``<table class="results-table">`` elements, the
       page is obfuscated (a real response always has at least the surrounding
       page structure even when no stores carry the product).

    Using these two signals rather than a raw byte-size threshold avoids both
    false positives (a legitimate page that happens to be large) and false
    negatives (a cleverly-sized obfuscated page).
    """
    # Signal 1: zero-width character saturation (actual code-points in payload)
    zwc_count = len(_ZERO_WIDTH_RE.findall(html))
    if zwc_count > 200:
        logger.debug("Obfuscation signal: %d zero-width chars detected", zwc_count)
        return True

    # Signal 1b: zero-width entities encoded as HTML numeric entities.
    # Obfuscated responses often contain tens of thousands of these markers.
    zwc_entity_count = sum(html.count(marker) for marker in _ZERO_WIDTH_ENTITY_MARKERS)
    if zwc_entity_count > 200:
        logger.debug(
            "Obfuscation signal: %d zero-width HTML entities detected",
            zwc_entity_count,
        )
        return True

    # Signal 1c: style-mask signatures used by CHP obfuscation script.
    if html.count("user-select:none") > 20 and html.count("data-") > 2000:
        logger.debug(
            "Obfuscation signal: heavy style/data-* masking detected "
            "(user-select:none=%d, data-*=%d)",
            html.count("user-select:none"),
            html.count("data-"),
        )
        return True

    # Signal 2: large response with no expected DOM structure
    if len(html) > 150_000:
        from bs4 import BeautifulSoup as _BS

        soup = _BS(html, "html.parser")
        if not soup.find("table", class_="results-table"):
            logger.debug(
                "Obfuscation signal: %d bytes, no results-table found", len(html)
            )
            return True

    return False


# Headers for autocomplete/JSON API calls (used by shopping_address and
# product_extended endpoints). Mirrors browser XHR request shape.
_HEADERS_XHR = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://chp.co.il/",
}

# Keep historical name for internal helpers.
_HEADERS = _HEADERS_XHR

# XHR variant for compare_results, as seen in browser fetch captures.
_HEADERS_XHR_COMPARE = {
    **_HEADERS_XHR,
    "Accept": "*/*",
}

# Headers that mimic a real browser navigating to the homepage.
# chp.co.il sets an httpOnly `us` cookie on every page response; we must
# visit the homepage first so the session carries `us` when compare_results
# is fetched.  Sending X-Requested-With or XHR Accept headers when the
# `us` cookie is already present triggers server-side obfuscation.
_HEADERS_NAV_HOME = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

# Headers that mimic a real browser navigating from the homepage to an inner
# page (compare_results). Critically: no X-Requested-With (not XHR).
_HEADERS_NAV_COMPARE = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://chp.co.il/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

_PAGE_SIZE = 10  # items per autocomplete page (excluding "prev" sentinel)


def _validate_autocomplete_payload(data: Any, endpoint_label: str) -> List[Dict[str, Any]]:
    """Validate that an autocomplete endpoint returns a list[dict].

    CHP currently responds with JSON arrays but sometimes reports
    ``Content-Type: text/html``. We validate the parsed payload shape instead of
    relying on the content-type header.
    """
    if not isinstance(data, list):
        raise ValueError(
            f"{endpoint_label}: expected JSON list, got {type(data).__name__}"
        )
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(
                f"{endpoint_label}: item #{idx} is {type(item).__name__}, expected object"
            )
    return data


def _split_autocomplete_items(
    items: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], bool, bool]:
    """Split real autocomplete rows from prev/next sentinel rows."""
    has_prev = any(str(item.get("id", "")) == "prev" for item in items)
    has_next = any(str(item.get("id", "")) == "next" for item in items)
    real_items = [
        item for item in items if str(item.get("id", "")) not in ("prev", "next")
    ]
    return real_items, has_prev, has_next


@dataclass
class ShoppingAddressResult:
    """One shopping_address response page plus parsed city matches."""

    term: str
    from_offset: int
    raw_items: List[Dict[str, Any]]
    matches: List["CityInfo"]


@dataclass
class ProductAutocompletePage:
    """One product_extended response page with sentinel metadata."""

    term: str
    from_offset: int
    raw_items: List[Dict[str, Any]]
    real_items: List[Dict[str, Any]]
    has_prev: bool
    has_next: bool


@dataclass
class CompareResultsResult:
    """Parsed compare_results response for one query/id and one location."""

    product: "ChpProduct"
    physical_rows: List["OnlineStorePrice"]
    online_rows: List["OnlineStorePrice"]
    physical_row_details: List[Dict[str, Any]]
    online_row_details: List[Dict[str, Any]]
    html: str
    from_offset: int
    num_results: int


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
    """A single price row from compare_results HTML.

    Despite the historical name, this object is used for both online and
    physical CHP result rows.  For online rows ``website``/``store_url`` carry
    the destination; for physical rows ``address`` carries the branch address.
    """

    def __init__(
        self,
        chain_name: str,
        store_name: str,
        website: str,
        deal_text: str,
        price: float,
        store_url: Optional[str],
        deal_price_text: str = "",
        address: str = "",
        is_online: bool = True,
    ) -> None:
        self.chain_name = chain_name
        self.store_name = store_name
        self.website = website
        self.deal_text = deal_text.strip()
        self.deal_price_text = deal_price_text.strip()
        self.price = price
        self.store_url = store_url
        self.address = address.strip()
        self.is_online = is_online


# ---------------------------------------------------------------------------
# Session / u-param management
# ---------------------------------------------------------------------------


def _new_u() -> float:
    """Generate a fresh ``u`` value (random float in [0, 1))."""
    return random.random()


def _extract_price_from_cell(
    cell: Any,
    deal_price_hint: str = "",
    visibility_rules: Optional[List[Dict[str, Any]]] = None,
) -> float:
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
    # Preferred path: extract visible text using parsed CSS rules when available.
    if visibility_rules:
        visible_text = _extract_visible_text(cell, visibility_rules)
        visible_value = _extract_price_from_text(visible_text, deal_price_hint)
        if visible_value is not None:
            return visible_value

    # Fallbacks for clean/simple HTML or partial obfuscation.
    direct_value = _extract_price_from_text(cell.get_text("", strip=False), deal_price_hint)
    if direct_value is not None:
        return direct_value

    collapsed_value = _extract_price_from_text(cell.get_text(" ", strip=True), deal_price_hint)
    if collapsed_value is not None:
        return collapsed_value

    raise ValueError(f"Cannot parse price from cell: {cell.get_text(strip=True)!r}")


# ---------------------------------------------------------------------------
# API call helpers
# ---------------------------------------------------------------------------


async def _get_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
) -> Any:
    """GET JSON from URL with shared XHR headers."""
    request_headers = headers or _HEADERS_XHR
    async with session.get(
        url, headers=request_headers, ssl=make_ssl_context()
    ) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)


async def _get_text(
    session: aiohttp.ClientSession,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
) -> str:
    """GET text/HTML from URL with shared headers."""
    request_headers = headers or _HEADERS_XHR
    async with session.get(
        url, headers=request_headers, ssl=make_ssl_context()
    ) as resp:
        resp.raise_for_status()
        return await resp.text()


async def _get_compare_html(
    session: aiohttp.ClientSession,
    url: str,
    ssl_ctx,
    *,
    headers: Optional[Dict[str, str]] = None,
) -> str:
    """GET compare_results HTML with caller-selected header mode.

    By default this uses browser navigation headers (safe mode).
    """
    request_headers = headers or _HEADERS_NAV_COMPARE
    async with session.get(url, headers=request_headers, ssl=ssl_ctx) as resp:
        resp.raise_for_status()
        return await resp.text()


def _build_shopping_address_url(term: str, from_offset: int, u: float) -> str:
    return f"{BASE_URL}/autocompletion/shopping_address?term={quote(term)}&from={from_offset}&u={u}"


def _build_product_extended_url(
    term: str,
    from_offset: int,
    city: Optional[CityInfo],
    u: float,
) -> str:
    city_label = quote(city.label) if city else ""
    city_id = city.city_id if city else "0"
    street_id = city.street_id if city else "0"
    return (
        f"{BASE_URL}/autocompletion/product_extended"
        f"?term={quote(term)}"
        f"&from={from_offset}"
        f"&u={u}"
        f"&shopping_address={city_label}"
        f"&shopping_address_city_id={city_id}"
        f"&shopping_address_street_id={street_id}"
    )


def _build_compare_results_url(
    city: CityInfo,
    *,
    product_name_or_barcode: str,
    product_barcode: str,
    from_offset: int,
    num_results: int,
) -> str:
    return (
        f"{BASE_URL}/main_page/compare_results"
        f"?shopping_address={quote(city.label)}"
        f"&shopping_address_street_id={city.street_id}"
        f"&shopping_address_city_id={city.city_id}"
        f"&product_name_or_barcode={quote(product_name_or_barcode)}"
        f"&product_barcode={quote(product_barcode)}"
        f"&from={from_offset}"
        f"&num_results={num_results}"
    )


def _identifier_to_chp_product(identifier: str) -> ChpProduct:
    item: Dict[str, Any] = {
        "id": identifier,
        "value": identifier,
        "label": identifier,
        "parts": {},
    }
    return ChpProduct(item)


# ---------------------------------------------------------------------------
# City lookup
# ---------------------------------------------------------------------------


async def fetch_shopping_address_page(
    session: aiohttp.ClientSession,
    term: str,
    u: float,
    *,
    from_offset: int = 0,
    headers: Optional[Dict[str, str]] = None,
) -> ShoppingAddressResult:
    """Fetch one `shopping_address` autocomplete page and validate shape.

    Server-returned payload shape (observed):
    - JSON array
    - each object usually contains: `value`, `label`, `id`
    - `id` format: `<city_id>_<street_id>`
    """
    url = _build_shopping_address_url(term, from_offset, u)
    payload = await with_retry(
        lambda: _get_json(session, url, headers=headers or _HEADERS_XHR),
        label=f"shopping_address:{term}:{from_offset}",
    )
    raw_items = _validate_autocomplete_payload(payload, "shopping_address")
    real_items, _, _ = _split_autocomplete_items(raw_items)

    matches: List[CityInfo] = []
    for item in real_items:
        try:
            matches.append(CityInfo.from_autocomplete_item(item))
        except Exception as exc:
            logger.warning("Skipping malformed shopping_address item: %s", exc)

    return ShoppingAddressResult(
        term=term,
        from_offset=from_offset,
        raw_items=raw_items,
        matches=matches,
    )


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
    page = await fetch_shopping_address_page(session, term, u, from_offset=0)
    return page.matches


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
    """Fetch one page of product autocomplete results (raw list)."""
    page = await fetch_product_autocomplete_page(
        session,
        term,
        city,
        u,
        from_offset=from_offset,
    )
    return page.raw_items


async def fetch_product_autocomplete_page(
    session: aiohttp.ClientSession,
    term: str,
    city: Optional[CityInfo],
    u: float,
    *,
    from_offset: int = 0,
    headers: Optional[Dict[str, str]] = None,
) -> ProductAutocompletePage:
    """Fetch one `product_extended` page and return real + sentinel metadata.

    Server-returned payload shape (observed):
    - JSON array
    - sentinel rows may appear with `id` equal to `prev`/`next`
    - real rows usually contain keys: `id`, `value`, `label`, `parts`
    - `parts` may include: `name_and_contents`, `manufacturer_and_barcode`,
      `pack_size`, `small_image`, `chainnames`, `price_range`
    """
    url = _build_product_extended_url(term, from_offset, city, u)
    payload = await with_retry(
        lambda: _get_json(session, url, headers=headers or _HEADERS_XHR),
        label=f"product_extended:{term}:{from_offset}",
    )
    raw_items = _validate_autocomplete_payload(payload, "product_extended")
    real_items, has_prev, has_next = _split_autocomplete_items(raw_items)
    return ProductAutocompletePage(
        term=term,
        from_offset=from_offset,
        raw_items=raw_items,
        real_items=real_items,
        has_prev=has_prev,
        has_next=has_next,
    )


async def iter_product_autocomplete_pages(
    session: aiohttp.ClientSession,
    term: str,
    city: Optional[CityInfo],
    u: float,
    *,
    start_from: int = 0,
    max_pages: int = 0,
    max_results: int = 0,
    headers: Optional[Dict[str, str]] = None,
) -> AsyncIterator[ProductAutocompletePage]:
    """Iterate over product autocomplete pages asynchronously.

    Stops when:
    - no real rows are returned,
    - no new product IDs are discovered,
    - last-page signal is reached (`has_next=False` and < full page),
    - `max_pages` or `max_results` limits are reached.
    """
    offset = start_from
    pages_seen = 0
    emitted_results = 0
    seen_ids: set[str] = set()

    while True:
        if max_pages and pages_seen >= max_pages:
            break

        page = await fetch_product_autocomplete_page(
            session,
            term,
            city,
            u,
            from_offset=offset,
            headers=headers,
        )
        yield page

        pages_seen += 1
        emitted_results += len(page.real_items)
        if max_results and emitted_results >= max_results:
            break
        if not page.real_items:
            break

        new_count = 0
        for item in page.real_items:
            pid = str(item.get("id", ""))
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                new_count += 1
        if new_count == 0:
            break

        if len(page.real_items) < _PAGE_SIZE and not page.has_next:
            break

        offset += _PAGE_SIZE


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

    async for page in iter_product_autocomplete_pages(
        session,
        term,
        city,
        u,
        start_from=0,
        max_results=max_results,
    ):
        for item in page.real_items:
            if len(products) >= max_results:
                break
            try:
                products.append(ChpProduct(item))
            except Exception as exc:
                logger.warning("Skipping malformed product item: %s", exc)
        if len(products) >= max_results:
            break

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


def _apply_compare_product_metadata(soup: BeautifulSoup, product: ChpProduct) -> None:
    """Hydrate a product from compare_results HTML.

    Sitemap enumeration gives the durable CHP identifier but not the full
    product label, brand, barcode, or unit.  The comparison page includes those
    details in hidden inputs and in the product header, so update the same
    ChpProduct instance before building UnifiedProduct rows.
    """
    code_input = soup.find("input", id="displayed_product_code")
    if code_input and code_input.get("value"):
        product.product_id = str(code_input["value"])

    name_input = soup.find("input", id="displayed_product_name_and_contents")
    if name_input and name_input.get("value"):
        product.name_and_contents = str(name_input["value"]).strip()
        product.label = product.name_and_contents

    header_text = ""
    product_table = soup.find("table")
    if product_table is not None:
        heading = product_table.find(["h1", "h2", "h3", "h4"])
        if heading is not None:
            header_text = heading.get_text(" ", strip=True)

    if header_text:
        brand_m = re.search(r"יצרן/מותג[:：]\s*([^,()]+)", header_text)
        if brand_m:
            product.brand = brand_m.group(1).strip()
        bc_m = re.search(r"ברקוד[:：]\s*(\d+)", header_text)
        if bc_m:
            product.barcode = bc_m.group(1)

    if product.barcode is None and product.product_id.startswith("temp_"):
        product.barcode = product.product_id[len("temp_") :]
    elif product.barcode is None and "_" in product.product_id:
        suffix = product.product_id.split("_", 1)[1]
        if suffix.isdigit():
            product.barcode = suffix

    product._unit_label = None
    product._unit_qty = None
    product._unit_qty_si = None
    product._unit_dimension = None
    product._parse_unit()


def parse_compare_results(
    html: str,
    product: ChpProduct,
) -> Tuple[List[OnlineStorePrice], List[OnlineStorePrice]]:
    """Parse the compare_results HTML fragment.

    Returns:
        (physical_prices, online_prices) — each is a list of OnlineStorePrice.
    """
    soup = BeautifulSoup(html, "html.parser")
    _apply_compare_product_metadata(soup, product)
    visibility_rules = _parse_visibility_rules_from_soup(soup)
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
        malformed_rows = 0
        for row in main_rows:
            cells = row.find_all("td")
            if not cells:
                continue
            # Online table: [chain, store_name, website, deal, price]
            # Physical table: [chain, store_name, address, deal, price]
            if len(cells) < 5:
                continue
            try:
                chain_name = _extract_visible_text(cells[0], visibility_rules)
                store_name = _extract_visible_text(cells[1], visibility_rules)
                # For online: cells[2] is website URL
                # For physical: cells[2] is address (has dont_display_when_narrow class)
                website = _extract_visible_text(cells[2], visibility_rules) if is_online else ""
                address = "" if is_online else _extract_visible_text(cells[2], visibility_rules)
                # Deal cell: look for <button class="btn-discount"> with data attributes
                deal_cell = cells[3]
                btn = deal_cell.find("button", class_="btn-discount")
                if btn is not None:
                    deal_desc = str(btn.get("data-discount-desc", "") or "")
                    # Keep deal value from the button visible text.
                    deal_price_text = _extract_visible_text(btn, visibility_rules) or btn.get_text(strip=True)
                else:
                    deal_desc = ""
                    deal_price_text = ""
                # Price: use robust obfuscation-aware extractor.
                price = _extract_price_from_cell(
                    cells[4],
                    deal_price_text,
                    visibility_rules=visibility_rules,
                )
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
                        address=address,
                        is_online=is_online,
                    )
                )
            except (ValueError, IndexError) as exc:
                malformed_rows += 1
                logger.debug("Skipping malformed table row: %s", exc)
        if malformed_rows:
            table_kind = "online" if is_online else "physical"
            logger.warning(
                "Skipped %d malformed table row(s) in %s compare table while parsing HTML",
                malformed_rows,
                table_kind,
            )
        return results

    physical: List[OnlineStorePrice] = []
    online: List[OnlineStorePrice] = []

    if len(tables) >= 1:
        physical = _parse_table(tables[0], is_online=False)
    if len(tables) >= 2:
        online = _parse_table(tables[1], is_online=True)

    return physical, online


def _store_price_to_detail(
    store_price: OnlineStorePrice,
    product: ChpProduct,
    scraped_at: str,
) -> Dict[str, Any]:
    """Convert one parsed compare row into a rich serializable detail record.

    Includes the raw row fields and normalized price/deal interpretation from
    UnifiedProduct mapping so callers can inspect one comparison row deeply.
    """
    unified = build_unified_product(store_price, product, scraped_at)
    detail: Dict[str, Any] = {
        "store": {
            "chain_name": store_price.chain_name,
            "store_name": store_price.store_name,
            "store_url": store_price.store_url,
            "website": store_price.website,
            "address": store_price.address,
            "store_type": "online" if store_price.is_online else "physical",
            "store_id": unified["store_id"],
        },
        "pricing": {
            "price": unified["price"],
            "regular_price": unified["regular_price"],
            "sale_price": unified["sale_price"],
            "discount_percent": unified["discount_percent"],
            "price_per_base_unit": unified["price_per_base_unit"],
        },
        "deal": unified["deal"],
        "raw": {
            "deal_text": store_price.deal_text,
            "deal_price_text": store_price.deal_price_text,
            "row_price": store_price.price,
        },
        "product": {
            "product_id": product.product_id,
            "name": product.name_and_contents or product.label,
            "barcode": product.barcode,
            "brand": product.brand,
            "unit_of_measure": product._unit_label,
            "unit_qty": product._unit_qty,
            "unit_qty_si": product._unit_qty_si,
            "unit_dimension": product._unit_dimension,
        },
    }
    return detail


def build_compare_result_details(
    physical_rows: List[OnlineStorePrice],
    online_rows: List[OnlineStorePrice],
    product: ChpProduct,
    scraped_at: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build rich detail dicts for each compare row.

    Returns:
        (physical_row_details, online_row_details)
    """
    physical_details = [
        _store_price_to_detail(row, product, scraped_at) for row in physical_rows
    ]
    online_details = [
        _store_price_to_detail(row, product, scraped_at) for row in online_rows
    ]
    return physical_details, online_details


# ---------------------------------------------------------------------------
# Fetch compare results for one product
# ---------------------------------------------------------------------------


async def fetch_compare_results_page(
    session: aiohttp.ClientSession,
    *,
    city: CityInfo,
    product_name_or_barcode: str,
    product_barcode: str = "0",
    from_offset: int = 0,
    num_results: int = 20,
    product: Optional[ChpProduct] = None,
    header_mode: Literal["safe_nav", "xhr"] = "safe_nav",
    max_retries: int = 3,
    retry_delay: float = 5.0,
) -> CompareResultsResult:
    """Fetch one compare_results page with explicit `from`/`num_results` control.

    Args:
        session:                  Outer session (kept for API symmetry).
        city:                     Location IDs and label from shopping_address.
        product_name_or_barcode:  Product query passed to endpoint. For best
                                  reliability, use the full CHP product ID.
        product_barcode:          Optional endpoint parameter observed in browser
                                  requests. Default ``"0"`` matches historical
                                  scraper behavior.
        from_offset:              `from` query parameter for endpoint paging.
        num_results:              `num_results` query parameter.
        product:                  Optional ChpProduct to hydrate in-place.
        header_mode:              ``safe_nav`` (recommended) or ``xhr``.
        max_retries:              Request retries.
        retry_delay:              Delay between retries.

    Returns:
        CompareResultsResult with parsed physical/online rows and raw HTML.
    """
    if from_offset < 0:
        raise ValueError("from_offset must be >= 0")
    if num_results <= 0:
        raise ValueError("num_results must be > 0")
    if header_mode not in ("safe_nav", "xhr"):
        raise ValueError("header_mode must be 'safe_nav' or 'xhr'")

    compare_url = _build_compare_results_url(
        city,
        product_name_or_barcode=product_name_or_barcode,
        product_barcode=product_barcode,
        from_offset=from_offset,
        num_results=num_results,
    )
    parse_product = product or _identifier_to_chp_product(product_name_or_barcode)
    ssl_ctx = make_ssl_context()

    mode_sequence: List[Literal["safe_nav", "xhr"]]
    mode_sequence = [header_mode]
    if header_mode == "safe_nav":
        mode_sequence.append("xhr")
    else:
        mode_sequence.append("safe_nav")

    for attempt in range(max_retries):
        for mode in mode_sequence:
            connector = aiohttp.TCPConnector(ssl=ssl_ctx, limit=3)
            async with aiohttp.ClientSession(connector=connector) as fresh_session:
                if mode == "safe_nav":
                    try:
                        async with fresh_session.get(
                            BASE_URL,
                            headers=_HEADERS_NAV_HOME,
                            ssl=ssl_ctx,
                        ) as resp:
                            await resp.read()
                    except Exception as exc:
                        logger.debug("Homepage warmup failed (non-fatal): %s", exc)

                compare_headers = (
                    _HEADERS_NAV_COMPARE if mode == "safe_nav" else _HEADERS_XHR_COMPARE
                )
                try:
                    html = await with_retry(
                        lambda: _get_compare_html(
                            fresh_session,
                            compare_url,
                            ssl_ctx,
                            headers=compare_headers,
                        ),
                        label=f"compare:{product_name_or_barcode}:{from_offset}:{num_results}:{mode}",
                    )
                except Exception as exc:
                    logger.warning(
                        "compare_results network error for %s (attempt %d/%d, mode=%s): %s",
                        product_name_or_barcode,
                        attempt + 1,
                        max_retries,
                        mode,
                        exc,
                    )
                    continue

            if _is_obfuscated_html(html):
                logger.warning(
                    "Obfuscated compare_results for %s (%d bytes, mode=%s), attempt %d/%d",
                    product_name_or_barcode,
                    len(html),
                    mode,
                    attempt + 1,
                    max_retries,
                )
                continue

            physical_rows, online_rows = parse_compare_results(html, parse_product)
            scraped_at = utc_now_iso()
            physical_row_details, online_row_details = build_compare_result_details(
                physical_rows,
                online_rows,
                parse_product,
                scraped_at,
            )
            return CompareResultsResult(
                product=parse_product,
                physical_rows=physical_rows,
                online_rows=online_rows,
                physical_row_details=physical_row_details,
                online_row_details=online_row_details,
                html=html,
                from_offset=from_offset,
                num_results=num_results,
            )

        if attempt < max_retries - 1:
            await asyncio.sleep(retry_delay + random.uniform(0, 2.0))

    raise RuntimeError(
        f"All {max_retries} retries failed for {product_name_or_barcode}: "
        "compare_results remained obfuscated or unavailable."
    )


async def fetch_compare_results(
    session: aiohttp.ClientSession,
    product: ChpProduct,
    city: CityInfo,
    u: float,
    max_retries: int = 3,
    retry_delay: float = 5.0,
) -> Tuple[List[OnlineStorePrice], List[OnlineStorePrice]]:
    """Fetch and parse compare results for a single product + city.

    **Bot-detection mitigation** — chp.co.il uses two interlocking signals to
    decide whether to obfuscate the response HTML:

    1. **The ``us`` cookie** — an httpOnly cookie set by the server on every
       response.  Once a session carries ``us``, making additional requests in
       that session triggers obfuscation if the requests look automated.

    2. **XHR headers** (``X-Requested-With: XMLHttpRequest``) — if the session
       has a ``us`` cookie and the request headers signal an XHR call rather than
       a real browser page-navigation, the server obfuscates the response.

    **Solution** — for every attempt we create a fresh ``aiohttp.ClientSession``
    (fresh cookie jar), then:

    a. ``GET /`` with browser *navigation* headers → server sets ``us`` cookie.
    b. ``GET /main_page/compare_results`` with browser *navigation* headers and
       the ``Referer: https://chp.co.il/`` header → server sees a normal
       same-origin page navigation from a legitimate browser session and returns
       clean HTML.

    If the response is still obfuscated (>200 KB), we wait ``retry_delay``
    seconds and retry with a completely new session.  Raises ``RuntimeError``
    when all retries are exhausted so the caller can record the failure
    (zero silent data loss).

    Args:
        session:     Outer session (used by the caller for autocomplete calls).
                     **Not used here** — we always create our own fresh session.
        product:     Product to look up.
        city:        City for location-aware pricing.
        u:           Session ``u`` float (not sent to compare_results; kept for
                     API compatibility).
        max_retries: Maximum attempts before raising.
        retry_delay: Base delay in seconds between retries (jitter added).

    Returns:
        (physical_prices, online_prices) — both lists may be empty if the
        product is not carried by any store.
    """
    result = await fetch_compare_results_page(
        session,
        city=city,
        product_name_or_barcode=product.label,
        product_barcode="0",
        from_offset=0,
        num_results=20,
        product=product,
        header_mode="safe_nav",
        max_retries=max_retries,
        retry_delay=retry_delay,
    )
    return result.physical_rows, result.online_rows


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

    # store_id: use website domain for online stores; include branch/address for
    # physical rows so different branches of the same chain do not collapse.
    if store_price.is_online:
        website = store_price.website or store_price.store_url or store_price.chain_name
        store_id = re.sub(r"https?://(?:www\.)?", "", website).rstrip("/")
    else:
        store_id = ":".join(
            p
            for p in (store_price.chain_name, store_price.store_name, store_price.address)
            if p
        )

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
    include_physical: bool = True,
    include_compare_row_details: bool = True,
    include_compare_html: bool = False,
) -> ScrapeResult:
    """Scrape compare-result prices from chp.co.il.

    For each product matching the filter, fetches compare results and
    returns UnifiedProduct records for every parsed comparison row.

    Results are grouped **by product** (keyed by ``product_id``).  The
    ``products_by_store`` field of ``ScrapeResult`` is reused as
    ``products_by_product`` for schema compatibility — each key is a
    ``product_id``; each value is a list of ``UnifiedProduct`` records,
    one per parsed store row, sorted cheapest first.

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
        include_physical: Include nearby physical branch rows in addition to online
                          store rows. Defaults to True.
        include_compare_row_details:
                          Include a top-level ``compare_row_details_by_product`` map
                          with per-row parsed detail dicts from compare HTML.
        include_compare_html:
                          When compare row details are included, attach raw compare
                          HTML for each product as ``html`` (large payload).

    Returns:
        ScrapeResult with ``products_by_store`` keyed by ``product_id``.
        ``stores_scraped`` is the number of distinct parsed stores seen in the
        selected scope (online-only or online+physical).
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
        async def _fetch_one(prod: ChpProduct):
            try:
                result = await fetch_compare_results_page(
                    session,
                    city=city_info,
                    product_name_or_barcode=prod.product_id,
                    product_barcode=prod.product_id or "0",
                    from_offset=0,
                    num_results=20,
                    product=prod,
                    header_mode="safe_nav",
                    max_retries=max_retries,
                    retry_delay=base_delay,
                )
                return (prod, result)
            except Exception as exc:
                errors.append(f"compare_results failed for {prod.product_id}: {exc}")
                return (prod, None)

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
        compare_row_details_by_product: Dict[str, Dict[str, Any]] = {}
        store_ids: set = set()
        total = 0
        scraped_at = utc_now_iso()

        for result in pair_results:
            if isinstance(result, Exception):
                errors.append(str(result))
                continue
            prod, compare_result = result
            if compare_result is None:
                continue
            selected_rows = (
                [*compare_result.physical_rows, *compare_result.online_rows]
                if include_physical
                else list(compare_result.online_rows)
            )
            selected_physical_details = (
                list(compare_result.physical_row_details) if include_physical else []
            )
            selected_online_details = list(compare_result.online_row_details)
            selected_all_details = [
                *selected_physical_details,
                *selected_online_details,
            ]
            selected_all_details_sorted = sorted(
                selected_all_details,
                key=lambda row: float(row.get("pricing", {}).get("price", 0.0) or 0.0),
            )

            pid = compare_result.product.product_id
            if include_compare_row_details:
                product_details: Dict[str, Any] = {
                    "physical_row_details": selected_physical_details,
                    "online_row_details": selected_online_details,
                    "all_row_details_sorted_by_price": selected_all_details_sorted,
                    "rows_total": len(selected_all_details),
                    "cheapest_row": selected_all_details_sorted[0]
                    if selected_all_details_sorted
                    else None,
                    "highest_row": selected_all_details_sorted[-1]
                    if selected_all_details_sorted
                    else None,
                }
                if include_compare_html:
                    product_details["html"] = compare_result.html
                compare_row_details_by_product[pid] = product_details

            if not selected_rows:
                continue
            if pid not in products_by_product:
                products_by_product[pid] = []
            for sp in selected_rows:
                up = build_unified_product(sp, compare_result.product, scraped_at)
                products_by_product[pid].append(up)
                store_ids.add(up["store_id"])
                total += 1

        # Sort each product's store list cheapest first
        for pid in products_by_product:
            products_by_product[pid].sort(key=lambda u: u["price"])

    scrape_result: ScrapeResult = ScrapeResult(
        chain=CHAIN,
        stores_scraped=len(store_ids),
        products_total=total,
        products_by_store=products_by_product,  # keyed by product_id
        scraped_at=started_at,
        duration_seconds=round(time.monotonic() - t0, 2),
        errors=errors,
    )
    if include_compare_row_details:
        scrape_result["compare_row_details_by_product"] = compare_row_details_by_product
    return scrape_result


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
