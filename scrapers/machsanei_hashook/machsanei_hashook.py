"""
Machsanei HaShook scraper  (מחסני השוק)
========================================
Platform : ZuZ (AngularJS) — retailer ID 1107
Base URL : https://www.mck.co.il

Key endpoints
-------------
1. Branch list:
     GET /v2/retailers/1107/branches?appId=2&languageId=1
   Response: { "branches": [ { "id": 3474, "name": "אופקים", "city": "אופקים", ... } ] }

2. Per-branch, per-category product catalogue (appId=4, offset pagination):
     GET /v2/retailers/1107/branches/836/categories/{catId}/products
         ?appId=4&from={offset}&size={size}&languageId=1
         &categorySort={"sortType":1}
         &filters={"mustNot":{"term":{"branch.isOutOfStock":true}}}
   Response: { "total": N, "products": [ ... ] }
   Branch data lives in product["branch"] (singular dict, not keyed by branch ID).

3. Specials/deals endpoint (deals also embedded in product["branch"]["specials"]):
     GET /v2/retailers/1107/branches/836/specials
         ?appId=4&from=0&size=N
         &filters={"must":{"lessThan":{"startDate":"<ISO>"},"greaterThan":{"endDate":"<ISO>"},"term":{"displayOnWeb":true}}}
         &sort={"priority":"desc"}

Key differences from old appId=2 global endpoint
-------------------------------------------------
- Only branch 836 (Beer Sheva) is a real physical store; the others in the API
  are delivery zones, not separate branches.  We always scrape only branch 836.
- Branch data is in product["branch"] (singular), NOT product["branches"]["836"].
- Barcode is NOT a top-level field; it must be extracted from the image URL.
- Image URL is a template with {{size}} and {{extension||'jpg'}} placeholders.
- Categories are in product["family"]["categories"] (list), not product["department"].
- Brand is in product["brand"]["names"]["1"].
- Deals/specials share the same firstLevel structure as before.

Unified API
-----------
Call ``scrape()`` to get a ``ScrapeResult`` TypedDict compatible with all
other chain scrapers.  Pass a ``ScrapeFilter`` to restrict by name, category,
or barcode.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, List, Optional, Tuple, TypedDict
from urllib.parse import quote

import aiohttp

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
from utils import get_browser_headers, get_module_logger

logger = get_module_logger("machsanei_hashook")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHAIN = "machsanei"
RETAILER_ID = 1107
BASE_URL = "https://www.mck.co.il"

# The only real physical branch.  All other IDs in the API are delivery zones.
BRANCH_ID = 836

# Regex to extract a barcode (7–14 digits) from the image URL.
# Image URLs look like: .../gs1-products/1107/{size}/8700216965705-9...
_BARCODE_RE = re.compile(r"/(\d{7,14})-")

# ---------------------------------------------------------------------------
# Branch list  (single real branch)
# ---------------------------------------------------------------------------


class Branch(TypedDict):
    id: int
    name: str
    city: str
    location: str


ONLINE_BRANCHES: List[Branch] = [
    {"id": 836, "name": "מחסני השוק", "city": "באר שבע", "location": ""},
]

# ---------------------------------------------------------------------------
# Top-level visible categories for branch 836 (discovered 2026-03)
# Each tuple is (category_id, hebrew_name).
# ---------------------------------------------------------------------------

MAIN_CATEGORIES: List[Tuple[int, str]] = [
    (79704, "ירקות ופירות"),
    (79718, "מוצרי קירור וביצים"),
    (79687, "לחמים עוגות ועוגיות"),
    (79821, "עוף בשר ודגים"),
    (79731, "דגנים"),
    (79619, "שימורים בישול ואפיה"),
    (79603, "מעדניה סלטים ונקניקים"),
    (79591, "קפואים"),
    (79653, "חטיפים וממתקים"),
    (79667, "משקאות ויין"),
    (79835, "בריאות ותזונה"),
    (79740, "ניקיון"),
    (79571, "פארם ותינוקות"),
    (79764, "כלי בית ופנאי"),
    (122168, "טבק ומוצרי עישון"),
    (96764, "חג הפסח"),
]

# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------


def _category_products_url(cat_id: int) -> str:
    return (
        f"{BASE_URL}/v2/retailers/{RETAILER_ID}"
        f"/branches/{BRANCH_ID}/categories/{cat_id}/products"
    )


def _branches_url() -> str:
    return f"{BASE_URL}/v2/retailers/{RETAILER_ID}/branches"


# ---------------------------------------------------------------------------
# Barcode + image helpers
# ---------------------------------------------------------------------------


def _extract_barcode(image_url: Optional[str]) -> Optional[str]:
    """Extract barcode from an image URL.

    Image URLs embed the barcode before a dash, e.g.:
      .../gs1-products/1107/large/8700216965705-9...
    Returns the barcode string, or None if not found.
    """
    if not image_url:
        return None
    m = _BARCODE_RE.search(image_url)
    return m.group(1) if m else None


def _expand_image_url(raw: Optional[str]) -> Optional[str]:
    """Expand ZuZ image URL template placeholders.

    Replaces ``{{size}}`` with ``large`` and
    ``{{extension||'jpg'}}`` (or ``{{extension}}``) with ``jpg``.
    """
    if not raw:
        return None
    url = raw.replace("{{size}}", "large")
    # Handle both {{extension||'jpg'}} and {{extension}}
    url = re.sub(r"\{\{extension(?:\|\|'[^']*')?\}\}", "jpg", url)
    return url


# ---------------------------------------------------------------------------
# Low-level fetch helper
# ---------------------------------------------------------------------------


async def _fetch_page(
    session: aiohttp.ClientSession,
    url: str,
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    label: str = "",
) -> Dict[str, Any]:
    """Fetch a single paginated API page and return the parsed JSON."""
    headers = get_browser_headers(BASE_URL)

    async def _do() -> Dict[str, Any]:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                raise aiohttp.ClientResponseError(
                    resp.request_info,
                    resp.history,
                    status=resp.status,
                    message=f"HTTP {resp.status}",
                )
            return await resp.json()

    try:
        return await with_retry(
            _do, max_retries=max_retries, base_delay=base_delay, label=label
        )
    except Exception as exc:
        logger.error("Failed %s: %s", label or url, exc)
        return {}


# ---------------------------------------------------------------------------
# Branch list fetch (live API)
# ---------------------------------------------------------------------------


async def fetch_branches(session: aiohttp.ClientSession) -> List[Branch]:
    """Fetch the live branch list from the API."""
    url = f"{_branches_url()}?appId=2&languageId=1"
    headers = get_browser_headers(BASE_URL)
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                logger.error("HTTP %s fetching branches", resp.status)
                return []
            data = await resp.json()
    except Exception as exc:
        logger.error("Error fetching branches: %s", exc)
        return []

    branches: List[Branch] = []
    for b in data.get("branches", []):
        branches.append(
            Branch(
                id=int(b["id"]),
                name=str(b.get("name") or b.get("localName") or ""),
                city=str(b.get("city") or ""),
                location=str(b.get("location") or ""),
            )
        )
    logger.info("Fetched %d branches from API", len(branches))
    return branches


# ---------------------------------------------------------------------------
# Deal extraction (ZuZ specials — same firstLevel structure as Stor.ai)
# ---------------------------------------------------------------------------


def _extract_deal(
    branch_info: Dict[str, Any],
    regular_price: float,
    sale_price: Optional[float],
    qty_si: Optional[float],
    dimension: Optional[str],
    is_weighable: bool,
) -> Optional[DealInfo]:
    """Parse ZuZ ``specials`` list into a unified DealInfo.

    Special types:
      type 2 — multi-buy: buy N items for a total price
      type 3 — cart threshold: spend ≥ X to unlock a discount/gift
      type 1 — simple price reduction (covered by salePrice)
    """
    specials = branch_info.get("specials") or []

    if sale_price is not None and sale_price < regular_price:
        ppbu = compute_price_per_base_unit(sale_price, qty_si, dimension, is_weighable)
        ppbu_reg = compute_price_per_base_unit(
            regular_price, qty_si, dimension, is_weighable
        )
        return DealInfo(
            has_deal=True,
            deal_type="price_reduction",
            deal_description=f"מחיר מבצע: ₪{sale_price:.2f} (במקום ₪{regular_price:.2f})",
            deal_price=sale_price,
            deal_min_qty=1,
            deal_price_per_unit=sale_price,
            price_per_base_unit=ppbu_reg,
            price_per_base_unit_deal=ppbu,
        )

    if not specials:
        return None

    ppbu_reg = compute_price_per_base_unit(
        regular_price, qty_si, dimension, is_weighable
    )

    for special in specials:
        fl = special.get("firstLevel") or {}
        stype = fl.get("type")
        desc_names = special.get("names") or {}
        heb_name = (desc_names.get("1") or {}).get("name") or special.get(
            "description", ""
        )

        if stype == 2:
            qty_req = fl.get("firstPurchaseTotal")
            deal_total = (fl.get("firstGift") or {}).get("total")
            if qty_req and deal_total:
                qty_req = int(qty_req)
                if qty_req == 0:
                    continue
                deal_total = float(deal_total)
                per_unit = round(deal_total / qty_req, 4)
                ppbu_deal = compute_price_per_base_unit(
                    per_unit, qty_si, dimension, is_weighable
                )
                return DealInfo(
                    has_deal=True,
                    deal_type="multi_buy",
                    deal_description=heb_name or "",
                    deal_price=deal_total,
                    deal_min_qty=qty_req,
                    deal_price_per_unit=per_unit,
                    price_per_base_unit=ppbu_reg,
                    price_per_base_unit_deal=ppbu_deal,
                )

        elif stype == 3:
            return DealInfo(
                has_deal=True,
                deal_type="cart_total",
                deal_description=heb_name or "",
                deal_price=None,
                deal_min_qty=None,
                deal_price_per_unit=None,
                price_per_base_unit=ppbu_reg,
                price_per_base_unit_deal=None,
            )

    return None


# ---------------------------------------------------------------------------
# Product mapping → UnifiedProduct
# ---------------------------------------------------------------------------


def _to_unified(
    item: Dict[str, Any],
    scraped_at: str,
) -> Optional[UnifiedProduct]:
    """Convert a ZuZ appId=4 product dict to a UnifiedProduct for branch 836.

    Returns None if the product is inactive / invisible / has no price.
    """
    branch_info: Dict[str, Any] = item.get("branch") or {}

    # Skip products not active in this branch
    if not (branch_info.get("isActive") and branch_info.get("isVisible")):
        return None

    regular_price_raw = branch_info.get("regularPrice")
    if regular_price_raw is None or float(regular_price_raw) <= 0:
        return None

    regular_price = float(regular_price_raw)

    # Name (prefer names.1.long → names.1.short → localName)
    names = item.get("names") or {}
    name = (
        (names.get("1") or {}).get("long")
        or (names.get("1") or {}).get("short")
        or item.get("localName", "")
    )
    if not name:
        return None

    # Image URL: expand template placeholders then extract barcode from it
    raw_image_url: Optional[str] = (item.get("image") or {}).get("url") or None
    image_url = _expand_image_url(raw_image_url)
    barcode = _extract_barcode(image_url)

    # Sale price
    sale_price_raw = branch_info.get("salePrice")
    sale_price: Optional[float] = (
        float(sale_price_raw) if sale_price_raw is not None else None
    )
    effective_price = sale_price if sale_price is not None else regular_price

    discount_pct: Optional[float] = None
    if sale_price is not None and regular_price > 0:
        discount_pct = round((1 - sale_price / regular_price) * 100, 2)

    # Unit / weight
    unit_weight_raw = item.get("weight")
    unit_qty_raw: Optional[float] = (
        float(unit_weight_raw) if unit_weight_raw is not None else None
    )
    uom = item.get("unitOfMeasure") or {}
    raw_uom: Optional[str] = (uom.get("names") or {}).get("1") or None
    is_weighable = bool(item.get("isWeighable", False))

    canonical_uom, qty_si, dimension, _si_per = normalize_unit(raw_uom, unit_qty_raw)

    unit_description: Optional[str] = None
    if unit_qty_raw is not None and canonical_uom:
        unit_description = f"{unit_qty_raw:g} {canonical_uom}"

    ppbu = compute_price_per_base_unit(effective_price, qty_si, dimension, is_weighable)

    # Category IDs: come from family.categories (list of {id, names})
    family = item.get("family") or {}
    family_cats = family.get("categories") or []
    category_ids: List[str] = [
        str(c["id"]) for c in family_cats if c.get("id") is not None
    ]

    # Brand
    brand_raw = (item.get("brand") or {}).get("names") or {}
    brand: Optional[str] = (brand_raw.get("1") or None) or None
    if brand:
        brand = str(brand).strip() or None

    # Deal extraction
    deal = _extract_deal(
        branch_info, regular_price, sale_price, qty_si, dimension, is_weighable
    )

    return UnifiedProduct(
        chain=CHAIN,
        store_id=str(BRANCH_ID),
        store_name=ONLINE_BRANCHES[0]["name"],
        product_id=str(item.get("productId") or item.get("id") or ""),
        name=str(name),
        price=effective_price,
        regular_price=regular_price,
        sale_price=sale_price,
        discount_percent=discount_pct,
        barcode=barcode,
        image_url=image_url,
        category_ids=category_ids,
        is_weighable=is_weighable,
        unit_description=unit_description,
        unit_of_measure=canonical_uom,
        unit_qty=unit_qty_raw,
        unit_qty_si=qty_si,
        unit_dimension=dimension,
        price_per_base_unit=ppbu,
        deal=deal,
        brand=brand,
        manufacturer=None,
        scraped_at=scraped_at,
    )


# ---------------------------------------------------------------------------
# Paginated fetch across all categories
# ---------------------------------------------------------------------------


async def _fetch_all_products(
    session: aiohttp.ClientSession,
    *,
    name_query: Optional[str] = None,
    batch_size: int = 100,
    max_concurrent: int = 15,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> List[Dict[str, Any]]:
    """Fetch all products for branch 836 by iterating MAIN_CATEGORIES.

    Each category is paginated independently with the appId=4 per-branch
    endpoint (no global 10K cap).  Products are deduplicated by productId.

    If ``name_query`` is supplied it is appended as a ``q=`` parameter.
    """
    all_products: Dict[str, Dict[str, Any]] = {}  # keyed by productId for dedup

    common_params = (
        "appId=4&languageId=1"
        '&categorySort={"sortType":1}'
        '&filters={"mustNot":{"term":{"branch.isOutOfStock":true}}}'
    )
    if name_query:
        common_params += f"&q={quote(name_query)}"

    for cat_id, cat_name in MAIN_CATEGORIES:
        base_url = _category_products_url(cat_id)

        # Probe to get total for this category
        probe_url = f"{base_url}?{common_params}&from=0&size=1"
        probe = await _fetch_page(
            session,
            probe_url,
            max_retries=max_retries,
            base_delay=base_delay,
            label=f"probe cat={cat_id}",
        )
        total = probe.get("total", 0)
        if total == 0:
            logger.debug("machsanei: category %s (%s) — 0 products", cat_id, cat_name)
            continue

        logger.info(
            "machsanei: category %s (%s) — %d products", cat_id, cat_name, total
        )

        offsets = list(range(0, total, batch_size))

        async def _fetch_offset(
            offset: int, cat_id: int = cat_id, cat_name: str = cat_name
        ) -> List[Dict[str, Any]]:
            url = f"{_category_products_url(cat_id)}?{common_params}&from={offset}&size={batch_size}"
            data = await _fetch_page(
                session,
                url,
                max_retries=max_retries,
                base_delay=base_delay,
                label=f"cat={cat_id} offset={offset}",
            )
            return data.get("products", [])

        task_fns = [lambda off=off: _fetch_offset(off) for off in offsets]
        results = await run_concurrently(task_fns, max_concurrent=max_concurrent)

        for r in results:
            if isinstance(r, Exception):
                logger.warning("Page fetch error in cat=%s: %s", cat_id, r)
            elif r:
                for product in r:
                    pid = str(product.get("productId") or product.get("id") or "")
                    if pid and pid not in all_products:
                        all_products[pid] = product

    logger.info(
        "machsanei: %d unique products across all categories", len(all_products)
    )
    return list(all_products.values())


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def scrape(
    branches: Optional[List[Branch]] = None,
    flt: Optional[ScrapeFilter] = None,
    batch_size: int = 100,
    max_concurrent: int = 15,
    max_retries: int = 3,
    base_retry_delay: float = 1.0,
) -> ScrapeResult:
    """Scrape Machsanei HaShook and return a unified ScrapeResult.

    Args:
        branches:         Ignored — always scrapes branch 836 only.
        flt:              Optional filters (name_query, category_ids, barcode).
        batch_size:       Products per paginated request (default 100).
        max_concurrent:   Max concurrent page requests (default 15).
        max_retries:      Max retry attempts per request (default 3).
        base_retry_delay: Base exponential-backoff delay in seconds (default 1.0).

    Returns:
        ScrapeResult with products_by_store keyed by "836".
    """
    if flt is None:
        flt = {}

    scraped_at = utc_now_iso()
    t0 = time.monotonic()
    errors: List[str] = []

    name_query = flt.get("name_query") or None
    filter_barcode = flt.get("barcode")
    filter_cats = flt.get("category_ids")

    connector = aiohttp.TCPConnector(ssl=make_ssl_context())
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            raw_products = await _fetch_all_products(
                session,
                name_query=name_query,
                batch_size=batch_size,
                max_concurrent=max_concurrent,
                max_retries=max_retries,
                base_delay=base_retry_delay,
            )
        except Exception as exc:
            msg = f"Failed to fetch products: {exc}"
            logger.error(msg)
            errors.append(msg)
            raw_products = []

    # Map raw products → UnifiedProduct, apply post-filters
    products: List[UnifiedProduct] = []
    seen_ids: set = set()

    for item in raw_products:
        p = _to_unified(item, scraped_at)
        if p is None:
            continue

        if filter_barcode and p.get("barcode") != filter_barcode:
            continue
        if filter_cats and not any(c in filter_cats for c in p.get("category_ids", [])):
            continue

        pid = p["product_id"]
        if pid not in seen_ids:
            seen_ids.add(pid)
            products.append(p)

    logger.info(
        "machsanei: branch=%s — %d unique active products", BRANCH_ID, len(products)
    )

    products_by_store: Dict[str, List[UnifiedProduct]] = {str(BRANCH_ID): products}

    duration = time.monotonic() - t0
    total = len(products)

    return ScrapeResult(
        chain=CHAIN,
        stores_scraped=1,
        products_total=total,
        products_by_store=products_by_store,
        scraped_at=scraped_at,
        duration_seconds=round(duration, 2),
        errors=errors,
    )


# ---------------------------------------------------------------------------
# update_branches — hit the live API to refresh the hardcoded branch list
# ---------------------------------------------------------------------------


async def update_branches() -> List[Branch]:
    """Fetch the live branch list from the ZuZ API for Machsanei HaShook (retailer 1107).

    Returns a list of :class:`Branch` objects that can be used to refresh
    :data:`ONLINE_BRANCHES`.
    """
    connector = aiohttp.TCPConnector(ssl=make_ssl_context())
    async with aiohttp.ClientSession(connector=connector) as session:
        branches = await fetch_branches(session)
    logger.info("update_branches: found %d Machsanei branches", len(branches))
    return branches
