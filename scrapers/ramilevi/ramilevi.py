"""
Rami Levy scraper
=================
Platform: Custom Node.js / Elasticsearch backend
Base URL: https://www.rami-levy.co.il

Key endpoints
-------------
1. Stores list:
     GET https://www.rami-levy.co.il/api/stores
   Returns all physical and online stores.  Online stores have a numeric
   ``internet_store_id`` (used as the ``store`` param in catalog calls).

2. Catalog (full browse, paginated):
     POST https://www.rami-levy.co.il/api/catalog?
     Content-Type: application/json
     Body: {"store": <internet_store_id>, "q": "", "from": <offset>, "size": <n>}
   Returns ``{"status": 200, "total": N, "data": [...]}`` where each item
   contains price, deal (``sale``), category, brand, and GS1 metadata.

3. Search (full-text, paginated):
     GET https://www.rami-levy.co.il/api/search
         ?storeid=<internet_store_id>&q=<query>&from=<offset>&size=<n>
   Works identically to the POST catalog for name-based searches.

Pagination
----------
Both endpoints use offset-based pagination via the ``from`` parameter.
The ``page`` parameter is NOT used — it does not advance the result window.

Deals / promotions
------------------
``sale`` is a list of promotion objects.  Each has:
  - ``type``  — currently always 1 (simple price reduction)
  - ``scm``   — the deal price (₪)
  - ``name``  — Hebrew description string
  - ``from``  / ``to``  — validity window

Products sold by weight (``prop.by_kilo == 1``) have ``price.price`` per kg.

Unified API
-----------
Call ``scrape()`` to get a ``ScrapeResult`` TypedDict compatible with all
other chain scrapers.  Pass a ``ScrapeFilter`` to restrict by name or barcode.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, TypedDict

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

logger = get_module_logger("ramilevi")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHAIN = "ramilevi"
BASE_URL = "https://www.rami-levy.co.il"
IMAGE_BASE = "https://img.rami-levy.co.il"

# ---------------------------------------------------------------------------
# Store list (hardcoded — stores with internet_store_id from /api/stores)
# ---------------------------------------------------------------------------


class Store(TypedDict):
    id: int  # internet_store_id (numeric)
    name: str  # store name / branch name
    city: str


# Stores that have an internet_store_id (i.e. support online ordering / catalog)
ONLINE_STORES: List[Store] = [
    {"id": 8, "name": "שער בנימין", "city": "מתחם שער בנימין"},
    {"id": 82, "name": "ירושלים - מלחה", "city": "ירושלים"},
    {"id": 125, "name": "ראשון לציון", "city": "ראשון לציון"},
    {"id": 130, "name": "גוש עציון", "city": "גוש עציון"},
    {"id": 179, "name": "נתניה", "city": "נתניה"},
    {"id": 279, "name": "חיפה", "city": "חיפה"},
    {"id": 290, "name": "אשדוד", "city": "אשדוד"},
    {"id": 306, "name": "ראשון לציון - רבין", "city": "ראשון לציון"},
    {"id": 331, "name": "כפר סבא", "city": "כפר סבא"},
    {"id": 411, "name": "מישור אדומים", "city": "מישור אדומים"},
    {"id": 412, "name": "ירושלים - גילה", "city": "ירושלים"},
    {"id": 1197, "name": "באר שבע", "city": "באר שבע"},
    {"id": 1198, "name": "טבריה", "city": "טבריה"},
    {"id": 1214, "name": "חולון", "city": "חולון"},
    {"id": 1220, "name": "בית שאן", "city": "בית שאן"},
    {"id": 1221, "name": "ירושלים - הר חומה", "city": "ירושלים"},
    {"id": 1225, "name": "פתח תקווה", "city": "פתח תקווה"},
    {"id": 1226, "name": "חדרה", "city": "חדרה"},
    {"id": 1306, "name": "גבעת שמואל", "city": "גבעת שמואל"},
    {"id": 1307, "name": "ירושלים - אג'מי", "city": "ירושלים"},
    {"id": 1314, "name": "אילת", "city": "אילת"},
    {"id": 1323, "name": "נשר", "city": "נשר"},
    {"id": 1329, "name": "רחובות", "city": "רחובות"},
    {"id": 1330, "name": "קרית גת", "city": "קרית גת"},
    {"id": 1332, "name": "מודיעין", "city": "מודיעין"},
    {"id": 1333, "name": "חיפה - נווה שאנן", "city": "חיפה"},
    {"id": 1357, "name": "כרמיאל", "city": "כרמיאל"},
    {"id": 1378, "name": "ירושלים - בית וגן", "city": "ירושלים"},
    {"id": 1389, "name": "איילון בני ברק", "city": "בני ברק"},
    {"id": 1401, "name": "ירושלים - רמות", "city": "ירושלים"},
]

# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------

_CATALOG_URL = f"{BASE_URL}/api/catalog?"
_SEARCH_URL = f"{BASE_URL}/api/search"
_STORES_URL = f"{BASE_URL}/api/stores"


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _headers() -> Dict[str, str]:
    h = get_browser_headers(BASE_URL)
    h["Content-Type"] = "application/json"
    return h


async def _fetch_catalog_page(
    session: aiohttp.ClientSession,
    store_id: int,
    offset: int,
    size: int,
    name_query: str = "",
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    label: str = "",
) -> Dict[str, Any]:
    """POST to /api/catalog? with offset pagination."""
    payload = {
        "store": store_id,
        "q": name_query,
        "from": offset,
        "size": size,
    }
    headers = _headers()

    async def _do() -> Dict[str, Any]:
        async with session.post(_CATALOG_URL, json=payload, headers=headers) as resp:
            if resp.status != 200:
                raise aiohttp.ClientResponseError(
                    resp.request_info,
                    resp.history,
                    status=resp.status,
                    message=f"HTTP {resp.status}",
                )
            data = await resp.json(content_type=None)
            if not isinstance(data, dict):
                logger.warning(
                    "Unexpected catalog response type store=%s offset=%s size=%s: %r",
                    store_id,
                    offset,
                    size,
                    data,
                )
                return {}
            return data

    try:
        return await with_retry(
            _do,
            max_retries=max_retries,
            base_delay=base_delay,
            attempt_timeout=25.0,
            label=label,
        )
    except Exception as exc:
        logger.error("Failed catalog page %s: %s", label, exc)
        return {}


async def _fetch_search_page(
    session: aiohttp.ClientSession,
    store_id: int,
    name_query: str,
    offset: int,
    size: int,
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    label: str = "",
) -> Dict[str, Any]:
    """GET /api/search with offset pagination."""
    from urllib.parse import quote

    params = f"storeid={store_id}&q={quote(name_query)}&from={offset}&size={size}"
    url = f"{_SEARCH_URL}?{params}"
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
            data = await resp.json(content_type=None)
            if not isinstance(data, dict):
                logger.warning(
                    "Unexpected search response type store=%s offset=%s size=%s: %r",
                    store_id,
                    offset,
                    size,
                    data,
                )
                return {}
            return data

    try:
        return await with_retry(
            _do,
            max_retries=max_retries,
            base_delay=base_delay,
            attempt_timeout=25.0,
            label=label,
        )
    except Exception as exc:
        logger.error("Failed search page %s: %s", label, exc)
        return {}


# ---------------------------------------------------------------------------
# Product mapping → UnifiedProduct
# ---------------------------------------------------------------------------


def _image_url(item: Dict[str, Any]) -> Optional[str]:
    images = item.get("images") or {}
    barcode = item.get("barcode")
    # The small image path starts with /product/…
    small = images.get("small") or ""
    if small:
        # Construct full URL; the site serves images from the same origin
        return f"{BASE_URL}{small}" if small.startswith("/") else small
    # Fallback: construct from barcode
    if barcode:
        return f"{BASE_URL}/product/{barcode}/small.jpg"
    return None


def _extract_deal(
    item: Dict[str, Any],
    regular_price: float,
    qty_si: Optional[float],
    dimension: Optional[str],
    is_weighable: bool,
) -> Optional[DealInfo]:
    """Parse Rami Levy ``sale`` list into DealInfo."""
    sales = item.get("sale") or []
    if not sales:
        return None

    sale = sales[0]  # use first active sale
    sale_price = sale.get("scm")
    if sale_price is None:
        return None

    sale_price_f = float(sale_price)
    if sale_price_f >= regular_price:
        return None

    ppbu_reg = compute_price_per_base_unit(
        regular_price, qty_si, dimension, is_weighable
    )
    ppbu_deal = compute_price_per_base_unit(
        sale_price_f, qty_si, dimension, is_weighable
    )

    name = sale.get("name") or sale.get("label") or ""
    return DealInfo(
        has_deal=True,
        deal_type="price_reduction",
        deal_description=name,
        deal_price=sale_price_f,
        deal_min_qty=1,
        deal_price_per_unit=sale_price_f,
        price_per_base_unit=ppbu_reg,
        price_per_base_unit_deal=ppbu_deal,
    )


def _to_unified(
    item: Dict[str, Any],
    store: Store,
    scraped_at: str,
) -> Optional[UnifiedProduct]:
    name = item.get("name")
    if not name:
        return None

    price_obj = item.get("price") or {}
    regular_price_raw = price_obj.get("price")
    if regular_price_raw is None:
        return None
    regular_price = float(regular_price_raw)

    # Unit / weight fields
    prop = item.get("prop") or {}
    by_kilo = bool(prop.get("by_kilo"))  # price is per kg
    is_weighable = by_kilo

    # GS1 data carries canonical net content (weight/volume)
    gs = item.get("gs") or {}
    net_content = gs.get("Net_Content") or {}
    qty_raw: Optional[float] = None
    raw_uom: Optional[str] = None
    if net_content:
        value_str = str(net_content.get("value") or "").strip()
        if value_str:
            try:
                if "*" in value_str:
                    # Handle multiplicative notation like '4*140' (4 packs x 140 g each)
                    parts = value_str.split("*", 1)
                    qty_raw = float(parts[0].strip()) * float(parts[1].strip()) or None
                    logger.debug(
                        "ramilevi: product id=%s name=%r parsed multiplicative net_content %r -> %s",
                        item.get("id"),
                        item.get("name"),
                        value_str,
                        qty_raw,
                    )
                else:
                    qty_raw = float(value_str) or None
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "Could not parse net_content value %r: %s",
                    value_str,
                    exc,
                )
                logger.debug(
                    "ramilevi: net_content parse failure product id=%s name=%r full net_content=%r",
                    item.get("id"),
                    item.get("name"),
                    net_content,
                )
                qty_raw = None
        raw_uom = net_content.get("UOM") or None

    canonical_uom, qty_si, dimension, _si_per = normalize_unit(raw_uom, qty_raw)

    # For by_kilo items the quantity from net_content is net weight in grams,
    # but the comparison price should be per kg (the API already gives ₪/kg).
    if by_kilo:
        # Override dimension to mass so ppbu uses the kg price directly
        dimension = "mass"
        # qty_si may not be reliable for weighable items — treat price as per-kg
        qty_si = None

    unit_description: Optional[str] = None
    if qty_raw is not None and canonical_uom:
        unit_description = f"{qty_raw:g} {canonical_uom}"

    ppbu = compute_price_per_base_unit(regular_price, qty_si, dimension, is_weighable)

    # Deal
    deal = _extract_deal(item, regular_price, qty_si, dimension, is_weighable)
    sale_price: Optional[float] = None
    effective_price = regular_price
    discount_pct: Optional[float] = None
    if deal:
        sale_price = deal.get("deal_price")
        if sale_price is not None:
            effective_price = sale_price
            if regular_price > 0:
                discount_pct = round((1 - sale_price / regular_price) * 100, 2)

    # Category IDs — Rami Levy uses department_id / group_id / sub_group_id
    dept = item.get("department") or {}
    group = item.get("group") or {}
    sub_group = item.get("subGroup") or {}
    cat_ids: List[str] = []
    for field in (dept, group, sub_group):
        cid = field.get("id")
        if cid is not None:
            cat_ids.append(str(cid))
    # Also pick up plain department_id / group_id fields (search API returns them)
    for key in ("department_id", "group_id", "sub_group_id"):
        val = item.get(key)
        if val is not None:
            s = str(val)
            if s not in cat_ids:
                cat_ids.append(s)
    if not cat_ids:
        cat_ids = []

    barcode_raw = item.get("barcode")
    barcode: Optional[str] = str(barcode_raw) if barcode_raw else None

    brand = (gs.get("BrandName") or "").strip() or None
    manufacturer: Optional[str] = None
    if isinstance(gs.get("Manufacturer_Name"), str):
        manufacturer = gs["Manufacturer_Name"].strip() or None

    image = _image_url(item)

    return UnifiedProduct(
        chain=CHAIN,
        store_id=str(store["id"]),
        store_name=store.get("name", ""),
        product_id=str(item.get("id", "")),
        name=str(name),
        price=effective_price,
        regular_price=regular_price,
        sale_price=sale_price,
        discount_percent=discount_pct,
        barcode=barcode,
        image_url=image,
        category_ids=cat_ids,
        is_weighable=is_weighable,
        unit_description=unit_description,
        unit_of_measure=canonical_uom,
        unit_qty=qty_raw,
        unit_qty_si=qty_si,
        unit_dimension=dimension,
        price_per_base_unit=ppbu,
        deal=deal,
        brand=brand,
        manufacturer=manufacturer,
        scraped_at=scraped_at,
    )


# ---------------------------------------------------------------------------
# Per-store scraper
# ---------------------------------------------------------------------------


async def _scrape_store(
    session: aiohttp.ClientSession,
    store: Store,
    flt: ScrapeFilter,
    batch_size: int,
    max_concurrent: int,
    max_retries: int,
    base_delay: float,
    scraped_at: str,
) -> List[UnifiedProduct]:
    store_id = store["id"]
    name_query = flt.get("name_query", "")
    filter_barcode = flt.get("barcode")

    # Probe for total
    if name_query:
        probe = await _fetch_search_page(
            session,
            store_id,
            name_query,
            offset=0,
            size=1,
            max_retries=max_retries,
            base_delay=base_delay,
            label=f"store={store_id} search probe",
        )
    else:
        probe = await _fetch_catalog_page(
            session,
            store_id,
            offset=0,
            size=1,
            max_retries=max_retries,
            base_delay=base_delay,
            label=f"store={store_id} catalog probe",
        )

    total = probe.get("total", 0)
    if total == 0:
        logger.info("store=%s — no products", store_id)
        return []

    logger.info("store=%s (%s) — %d products to fetch", store_id, store["name"], total)

    # Build page fetch tasks
    offsets = list(range(0, total, batch_size))

    async def _fetch(offset: int) -> List[UnifiedProduct]:
        if name_query:
            data = await _fetch_search_page(
                session,
                store_id,
                name_query,
                offset=offset,
                size=min(batch_size, total - offset),
                max_retries=max_retries,
                base_delay=base_delay,
                label=f"store={store_id} search offset={offset}",
            )
        else:
            data = await _fetch_catalog_page(
                session,
                store_id,
                offset=offset,
                size=min(batch_size, total - offset),
                max_retries=max_retries,
                base_delay=base_delay,
                label=f"store={store_id} catalog offset={offset}",
            )
        results: List[UnifiedProduct] = []
        for raw in data.get("data", []):
            p = _to_unified(raw, store, scraped_at)
            if p:
                results.append(p)
        return results

    task_fns = [lambda o=off: _fetch(o) for off in offsets]
    page_results = await run_concurrently(task_fns, max_concurrent=max_concurrent)

    products: List[UnifiedProduct] = []
    for r in page_results:
        if isinstance(r, Exception):
            logger.warning("store=%s page error: %s", store_id, r)
        elif r:
            products.extend(r)

    # Post-filter by barcode
    if filter_barcode:
        products = [p for p in products if p.get("barcode") == filter_barcode]

    # Deduplicate by product_id
    seen: set = set()
    unique: List[UnifiedProduct] = []
    for p in products:
        pid = p["product_id"]
        if pid not in seen:
            seen.add(pid)
            unique.append(p)

    logger.info(
        "store=%s (%s) — %d unique products",
        store_id,
        store.get("name", ""),
        len(unique),
    )
    return unique


# ---------------------------------------------------------------------------
# update_branches — hit the live API to refresh the hardcoded store list
# ---------------------------------------------------------------------------


async def update_stores() -> List[Store]:
    """Fetch the live store list from /api/stores and return online-capable stores.

    These are stores that have a numeric ``internet_store_id`` set.  The list
    can be used to refresh :data:`ONLINE_STORES`.
    """
    connector = aiohttp.TCPConnector(ssl=make_ssl_context())
    async with aiohttp.ClientSession(connector=connector) as session:
        headers = get_browser_headers(BASE_URL)
        try:
            async with session.get(_STORES_URL, headers=headers) as resp:
                data = await resp.json(content_type=None)
        except Exception as exc:
            logger.error("Failed to fetch stores: %s", exc)
            return []

    stores: List[Store] = []
    for s in (data.get("stores") or {}).get("data") or []:
        internet_id = s.get("internet_store_id")
        if internet_id is None:
            continue
        try:
            iid = int(internet_id)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "Skipping store with non-integer internet_store_id %r: %s",
                internet_id,
                exc,
            )
            continue
        stores.append(
            Store(
                id=iid,
                name=str(s.get("name") or s.get("id") or ""),
                city=str(s.get("city") or ""),
            )
        )
    logger.info("update_stores: found %d online-capable stores", len(stores))
    return stores


# Alias for consistency with other scrapers
update_branches = update_stores


def _merge_stores(live_stores: List[Store], fallback_stores: List[Store]) -> List[Store]:
    merged: List[Store] = []
    seen: set[int] = set()
    for store in [*live_stores, *fallback_stores]:
        store_id = int(store["id"])
        if store_id in seen:
            continue
        seen.add(store_id)
        merged.append(store)
    return merged


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def scrape(
    stores: Optional[List[Store]] = None,
    flt: Optional[ScrapeFilter] = None,
    batch_size: int = 300,
    max_concurrent: int = 15,
    store_concurrent: int = 6,
    max_retries: int = 3,
    base_retry_delay: float = 1.0,
) -> ScrapeResult:
    """Scrape Rami Levy and return a unified ScrapeResult.

    Args:
        stores:           Stores to scrape (default: all ONLINE_STORES).
        flt:              Optional filters (name_query, barcode).
        batch_size:       Products per paginated request (max ~200).
        max_concurrent:   Max concurrent page fetches per store.
        max_retries:      Max retry attempts per request.
        base_retry_delay: Base delay (seconds) for exponential backoff.

    Returns:
        ScrapeResult with products_by_store keyed by str(internet_store_id).
    """
    if flt is None:
        flt = {}

    scraped_at = utc_now_iso()
    t0 = time.monotonic()
    errors: List[str] = []

    connector = aiohttp.TCPConnector(ssl=make_ssl_context())
    async with aiohttp.ClientSession(connector=connector) as session:
        if stores is None:
            live_stores = await update_stores()
            if live_stores:
                stores = _merge_stores(live_stores, ONLINE_STORES)
                logger.info(
                    "ramilevi: using %d live/static merged store(s)", len(stores)
                )
            else:
                stores = ONLINE_STORES
                logger.warning(
                    "ramilevi: live store discovery failed; falling back to %d static store(s)",
                    len(stores),
                )
        async def _scrape_one_store(store: Store) -> tuple[str, List[UnifiedProduct], Optional[str]]:
            try:
                prods = await _scrape_store(
                    session,
                    store,
                    flt,
                    batch_size,
                    max_concurrent,
                    max_retries,
                    base_retry_delay,
                    scraped_at,
                )
                return str(store["id"]), prods, None
            except Exception as exc:
                msg = f"store={store['id']} failed: {exc}"
                logger.error(msg)
                return str(store["id"]), [], msg

        products_by_store: Dict[str, List[UnifiedProduct]] = {}
        task_fns = [lambda store=store: _scrape_one_store(store) for store in stores]
        results = await run_concurrently(task_fns, max_concurrent=store_concurrent)
        for result in results:
            if isinstance(result, Exception):
                msg = f"store task failed: {result}"
                logger.error(msg)
                errors.append(msg)
                continue
            store_id, products, error = result
            if error:
                errors.append(error)
            products_by_store[store_id] = products

    duration = time.monotonic() - t0
    total = sum(len(v) for v in products_by_store.values())

    return ScrapeResult(
        chain=CHAIN,
        stores_scraped=len(stores),
        products_total=total,
        products_by_store=products_by_store,
        scraped_at=scraped_at,
        duration_seconds=round(duration, 2),
        errors=errors,
    )
