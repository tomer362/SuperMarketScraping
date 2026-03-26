"""
Tiv Taam scraper
================
Platform: Stor.ai  — retailer ID 1062
Base URL: https://www.tivtaam.co.il

Key endpoints
-------------
1. Category products (offset pagination):
     GET /v2/retailers/1062/branches/{branch_id}/categories/{category_id}/products
         ?appId=4&from={offset}&size={size}&languageId=1

2. Name search (autocomplete / full-text):
     GET /v2/retailers/1062/branches/{branch_id}/products/autocomplete
         ?appId=4&isSearch=true&languageId=1&size={size}&from={offset}
         &filters={json_filters}
         &q={query}
   Response shape is identical to the category products endpoint.

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
from typing import Any, Dict, List, Optional
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

logger = get_module_logger("tivtaam")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHAIN = "tivtaam"
RETAILER_ID = 1062
BASE_URL = "https://www.tivtaam.co.il"

# ---------------------------------------------------------------------------
# Branch list
# ---------------------------------------------------------------------------

from typing import TypedDict


class Branch(TypedDict):
    id: int
    name: str
    city: str
    location: str


ONLINE_BRANCHES: List[Branch] = [
    {
        "id": 924,
        "name": "רמת החייל",
        "city": "תל אביב יפו",
        "location": "דבורה הנביאה 122",
    },
    {"id": 929, "name": 'ראשל"צ מזרח', "city": "ראשון לציון", "location": "המכבים 62"},
    {"id": 937, "name": "אשדוד", "city": "אשדוד", "location": "ז'בוטינסקי 45"},
    {
        "id": 939,
        "name": "באר שבע",
        "city": "באר שבע",
        "location": "יצחק נפחא 25 מתחם מבנה",
    },
    {"id": 943, "name": "נתניה", "city": "נתניה", "location": "בני גאון 2"},
    {"id": 1489, "name": "קיסריה", "city": "קיסריה", "location": "דולב 14"},
    {
        "id": 1841,
        "name": "חוצות המפרץ",
        "city": "חיפה",
        "location": "החרושת 10 מתחם חוצות המפרץ O",
    },
    {"id": 1980, "name": "נובל אנרג'י", "city": "אשדוד", "location": ""},
    {"id": 3463, "name": "ראשון לציון - רובוטי", "city": "", "location": "המכבים 62"},
]

CATEGORIES: List[str] = [
    "90066",
    "90069",
    "90073",
    "90082",
    "90083",
    "90084",
    "90085",
    "90100",
    "90103",
    "90173",
    "90176",
    "90184",
    "95874",
    "90107",
    "90191",
    "90121",
    "90113",
    "90131",
    "90124",
    "92837",
    "90205",
    "90199",
    "90210",
    "90215",
    "90219",
    "90221",
    "90135",
    "90150",
    "90157",
    "90167",
    "90245",
    "90225",
    "90144",
    "90236",
    "92072",
    "90076",
    "96394",
    "90250",
    "90254",
    "90255",
    "90256",
    "90257",
    "90258",
    "90261",
    "90269",
    "90271",
    "90276",
    "90277",
    "90282",
    "90283",
    "90281",
    "119730",
    "90285",
    "90288",
    "90292",
    "90294",
    "90297",
    "90299",
    "90303",
    "90309",
    "90315",
    "90318",
    "90361",
    "90365",
    "90370",
    "90376",
    "90380",
    "90390",
    "90401",
    "90404",
    "94410",
    "90333",
    "90342",
    "90350",
    "90355",
    "96535",
    "92060",
    "90434",
    "123135",
    "90410",
    "90415",
    "90420",
    "90427",
    "90440",
    "92848",
    "92849",
]

# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------


def _category_url(branch_id: int, category_id: str) -> str:
    return (
        f"{BASE_URL}/v2/retailers/{RETAILER_ID}/branches/{branch_id}"
        f"/categories/{category_id}/products"
    )


# ---------------------------------------------------------------------------
# Barcode extraction
# ---------------------------------------------------------------------------


def _extract_barcode(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"/gs1-products/\d+/[^/]+/(\d{8,14})-\d+", url)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Deal extraction
# ---------------------------------------------------------------------------


def _extract_deal(
    branch_info: Dict[str, Any],
    regular_price: float,
    sale_price: Optional[float],
    qty_si: Optional[float],
    dimension: Optional[str],
    is_weighable: bool,
) -> Optional[DealInfo]:
    """Parse Stor.ai ``branch.specials`` list into a unified ``DealInfo``.

    Special types observed in the wild:
      type 2 — multi-buy: buy N items from a group for a total price
                  firstPurchaseTotal = quantity required
                  firstGift.total     = total price for that quantity
      type 3 — cart threshold: spend ≥ X to unlock a gift/discount
      type 1 — simple price reduction (covered already by salePrice)
    """
    specials = branch_info.get("specials") or []

    # Simple price reduction (salePrice already set)
    if sale_price is not None and sale_price < regular_price:
        ppbu = compute_price_per_base_unit(sale_price, qty_si, dimension, is_weighable)
        ppbu_reg = compute_price_per_base_unit(
            regular_price, qty_si, dimension, is_weighable
        )
        deal: DealInfo = {
            "has_deal": True,
            "deal_type": "price_reduction",
            "deal_description": f"מחיר מבצע: ₪{sale_price:.2f} (במקום ₪{regular_price:.2f})",
            "deal_price": sale_price,
            "deal_min_qty": 1,
            "deal_price_per_unit": sale_price,
            "price_per_base_unit": ppbu_reg,
            "price_per_base_unit_deal": ppbu,
        }
        return deal

    if not specials:
        # No deal — still compute regular price_per_base_unit so callers can compare
        return None

    # Parse first actionable special
    for special in specials:
        fl = special.get("firstLevel") or {}
        stype = fl.get("type")
        desc_names = special.get("names") or {}
        # Prefer Hebrew description
        heb_name = (desc_names.get("1") or {}).get("name") or special.get(
            "description", ""
        )

        if stype == 2:
            # Multi-buy: buy firstPurchaseTotal items for firstGift.total price
            qty_required = fl.get("firstPurchaseTotal")
            deal_total = (fl.get("firstGift") or {}).get("total")
            if qty_required and deal_total:
                qty_required = int(qty_required)
                deal_total = float(deal_total)
                per_unit = round(deal_total / qty_required, 4)
                ppbu_deal = compute_price_per_base_unit(
                    per_unit, qty_si, dimension, is_weighable
                )
                ppbu_reg = compute_price_per_base_unit(
                    regular_price, qty_si, dimension, is_weighable
                )
                return DealInfo(
                    has_deal=True,
                    deal_type="multi_buy",
                    deal_description=heb_name or special.get("description", ""),
                    deal_price=deal_total,
                    deal_min_qty=qty_required,
                    deal_price_per_unit=per_unit,
                    price_per_base_unit=ppbu_reg,
                    price_per_base_unit_deal=ppbu_deal,
                )
        elif stype == 3:
            # Cart threshold — cart total must reach firstPurchaseTotal
            threshold = fl.get("firstPurchaseTotal")
            ppbu_reg = compute_price_per_base_unit(
                regular_price, qty_si, dimension, is_weighable
            )
            return DealInfo(
                has_deal=True,
                deal_type="cart_total",
                deal_description=heb_name or special.get("description", ""),
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
    branch: Branch,
    category_id: str,
    scraped_at: str,
) -> Optional[UnifiedProduct]:
    branch_info = item.get("branch", {})
    regular_price = branch_info.get("regularPrice")
    if regular_price is None:
        return None

    names = item.get("names", {})
    name = (
        names.get("1", {}).get("long")
        or names.get("1", {}).get("short")
        or item.get("localName", "")
    )
    if not name:
        return None

    raw_image = item.get("image", {})
    image_url: Optional[str] = None
    if raw_image and raw_image.get("url"):
        image_url = (
            raw_image["url"]
            .replace("{{size}}", "medium")
            .replace("{{extension||'jpg'}}", "jpg")
        )

    barcode = item.get("barcode") or _extract_barcode(image_url)

    sale_price_raw = branch_info.get("salePrice")
    sale_price: Optional[float] = (
        float(sale_price_raw) if sale_price_raw is not None else None
    )

    regular_price_f = float(regular_price)
    effective_price = sale_price if sale_price is not None else regular_price_f

    discount_pct: Optional[float] = None
    if sale_price is not None and regular_price_f > 0:
        discount_pct = round((1 - sale_price / regular_price_f) * 100, 2)

    # Unit / weight fields
    unit_weight_raw = item.get("weight")
    unit_qty_raw: Optional[float] = (
        float(unit_weight_raw) if unit_weight_raw is not None else None
    )
    uom = item.get("unitOfMeasure") or {}
    raw_uom: Optional[str] = (uom.get("names") or {}).get("1") or None

    is_weighable = bool(item.get("isWeighable", False))

    # Resolve unit to canonical form
    canonical_uom, qty_si, dimension, _si_per = normalize_unit(raw_uom, unit_qty_raw)

    unit_description: Optional[str] = None
    if unit_qty_raw is not None and canonical_uom:
        unit_description = f"{unit_qty_raw:g} {canonical_uom}"

    # Price per base unit (regular, single-unit price)
    ppbu = compute_price_per_base_unit(effective_price, qty_si, dimension, is_weighable)

    # Collect all category IDs from family.categories
    family_cats: List[str] = []
    for cat in (item.get("family") or {}).get("categories") or []:
        cid = cat.get("id")
        if cid is not None:
            family_cats.append(str(cid))
    if not family_cats:
        family_cats = [str(category_id)]

    # Extract deal info
    deal = _extract_deal(
        branch_info, regular_price_f, sale_price, qty_si, dimension, is_weighable
    )

    return UnifiedProduct(
        chain=CHAIN,
        store_id=str(branch["id"]),
        store_name=branch.get("name", ""),
        product_id=str(item.get("productId", item.get("id", ""))),
        name=str(name),
        price=effective_price,
        regular_price=regular_price_f,
        sale_price=sale_price,
        discount_percent=discount_pct,
        barcode=barcode,
        image_url=image_url,
        category_ids=family_cats,
        is_weighable=is_weighable,
        unit_description=unit_description,
        unit_of_measure=canonical_uom,
        unit_qty=unit_qty_raw,
        unit_qty_si=qty_si,
        unit_dimension=dimension,
        price_per_base_unit=ppbu,
        deal=deal,
        brand=None,
        manufacturer=None,
        scraped_at=scraped_at,
    )


# ---------------------------------------------------------------------------
# Low-level fetch helpers
# ---------------------------------------------------------------------------


async def _fetch_page(
    session: aiohttp.ClientSession,
    url: str,
    params: str,
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    label: str = "",
) -> Dict[str, Any]:
    full_url = f"{url}?{params}"
    headers = get_browser_headers(BASE_URL)

    async def _do() -> Dict[str, Any]:
        async with session.get(full_url, headers=headers) as resp:
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
        logger.error("Failed to fetch %s: %s", label or full_url, exc)
        return {}


# ---------------------------------------------------------------------------
# Category scraper (uses category products endpoint)
# ---------------------------------------------------------------------------


async def _scrape_category(
    session: aiohttp.ClientSession,
    branch: Branch,
    category_id: str,
    batch_size: int,
    max_retries: int,
    base_delay: float,
    scraped_at: str,
) -> List[UnifiedProduct]:
    branch_id = branch["id"]
    url = _category_url(branch_id, category_id)

    # Discover total
    probe = await _fetch_page(
        session,
        url,
        f"appId=4&from=0&languageId=1&size=1",
        max_retries=max_retries,
        base_delay=base_delay,
        label=f"branch={branch_id} cat={category_id} probe",
    )
    total = probe.get("total", 0)
    if total == 0:
        return []

    logger.info("branch=%s category=%s — %d products", branch_id, category_id, total)

    products: List[UnifiedProduct] = []
    offset = 0
    while offset < total:
        data = await _fetch_page(
            session,
            url,
            f"appId=4&from={offset}&languageId=1&size={batch_size}",
            max_retries=max_retries,
            base_delay=base_delay,
            label=f"branch={branch_id} cat={category_id} offset={offset}",
        )
        items = data.get("products", [])
        if not items:
            break
        for item in items:
            p = _to_unified(item, branch, category_id, scraped_at)
            if p:
                products.append(p)
        offset += batch_size

    return products


# ---------------------------------------------------------------------------
# Search scraper — uses category products endpoint with q= param
# ---------------------------------------------------------------------------


async def _scrape_search(
    session: aiohttp.ClientSession,
    branch: Branch,
    name_query: str,
    categories: List[str],
    batch_size: int,
    max_concurrent: int,
    max_retries: int,
    base_delay: float,
    scraped_at: str,
) -> List[UnifiedProduct]:
    """Search across all categories using q= on the category products endpoint.

    The Stor.ai /products/autocomplete endpoint is capped at 10 suggestions and
    does not support pagination.  The category products endpoint (same URL used
    for full scraping) does support q= with proper total + pagination, so we fan
    out across all categories concurrently and deduplicate.
    """
    branch_id = branch["id"]
    encoded_q = quote(name_query)

    async def _search_category(cat: str) -> List[UnifiedProduct]:
        url = _category_url(branch_id, cat)
        probe = await _fetch_page(
            session,
            url,
            f"appId=4&from=0&languageId=1&size=1&q={encoded_q}",
            max_retries=max_retries,
            base_delay=base_delay,
            label=f"branch={branch_id} cat={cat} search probe",
        )
        total = probe.get("total", 0)
        if total == 0:
            return []

        cat_products: List[UnifiedProduct] = []
        offset = 0
        while offset < total:
            data = await _fetch_page(
                session,
                url,
                f"appId=4&from={offset}&languageId=1&size={batch_size}&q={encoded_q}",
                max_retries=max_retries,
                base_delay=base_delay,
                label=f"branch={branch_id} cat={cat} search offset={offset}",
            )
            items = data.get("products", [])
            if not items:
                break
            for item in items:
                p = _to_unified(item, branch, cat, scraped_at)
                if p:
                    cat_products.append(p)
            offset += batch_size
        return cat_products

    task_fns = [lambda c=cat: _search_category(c) for cat in categories]
    results = await run_concurrently(task_fns, max_concurrent=max_concurrent)

    all_products: List[UnifiedProduct] = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Search category error branch=%s: %s", branch_id, r)
        elif r:
            all_products.extend(r)

    logger.info(
        "branch=%s search='%s' — %d raw hits across %d categories",
        branch_id,
        name_query,
        len(all_products),
        len(categories),
    )
    return all_products


# ---------------------------------------------------------------------------
# Per-branch scraper
# ---------------------------------------------------------------------------


async def _scrape_branch(
    session: aiohttp.ClientSession,
    branch: Branch,
    flt: ScrapeFilter,
    batch_size: int,
    max_concurrent: int,
    max_retries: int,
    base_delay: float,
    scraped_at: str,
) -> List[UnifiedProduct]:
    name_query = flt.get("name_query", "")
    filter_cats = flt.get("category_ids")
    filter_barcode = flt.get("barcode")

    if name_query:
        # Search across all (or filtered) categories using q= param
        cats = filter_cats if filter_cats else CATEGORIES
        products = await _scrape_search(
            session,
            branch,
            name_query,
            cats,
            batch_size,
            max_concurrent,
            max_retries,
            base_delay,
            scraped_at,
        )
    else:
        cats = filter_cats if filter_cats else CATEGORIES
        task_fns = [
            (
                lambda c=cat: _scrape_category(
                    session, branch, c, batch_size, max_retries, base_delay, scraped_at
                )
            )
            for cat in cats
        ]
        results = await run_concurrently(task_fns, max_concurrent=max_concurrent)
        products = []
        for r in results:
            if isinstance(r, Exception):
                logger.error("Category scrape error: %s", r)
            elif r:
                products.extend(r)

    # Post-filter by barcode
    if filter_barcode:
        products = [p for p in products if p.get("barcode") == filter_barcode]

    # Post-filter by category (when name_query was used)
    if name_query and filter_cats:
        products = [
            p
            for p in products
            if any(c in filter_cats for c in p.get("category_ids", []))
        ]

    # De-duplicate by product_id within this branch
    seen: set = set()
    unique: List[UnifiedProduct] = []
    for p in products:
        pid = p["product_id"]
        if pid not in seen:
            seen.add(pid)
            unique.append(p)

    logger.info(
        "branch=%s (%s) — %d unique products",
        branch["id"],
        branch.get("name", ""),
        len(unique),
    )
    return unique


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
    """Scrape Tiv Taam and return a unified ScrapeResult.

    Args:
        branches:         Branches to scrape (default: all ONLINE_BRANCHES).
        flt:              Optional filters (name_query, category_ids, barcode).
        batch_size:       Products per paginated request.
        max_concurrent:   Max concurrent category requests per branch.
        max_retries:      Max retry attempts per request.
        base_retry_delay: Base delay (seconds) for exponential backoff.

    Returns:
        ScrapeResult with products_by_store keyed by str(branch_id).
    """
    if branches is None:
        branches = ONLINE_BRANCHES
    if flt is None:
        flt = {}

    scraped_at = utc_now_iso()
    t0 = time.monotonic()
    errors: List[str] = []

    connector = aiohttp.TCPConnector(ssl=make_ssl_context())
    async with aiohttp.ClientSession(connector=connector) as session:
        products_by_store: Dict[str, List[UnifiedProduct]] = {}

        for branch in branches:
            try:
                prods = await _scrape_branch(
                    session,
                    branch,
                    flt,
                    batch_size,
                    max_concurrent,
                    max_retries,
                    base_retry_delay,
                    scraped_at,
                )
                products_by_store[str(branch["id"])] = prods
            except Exception as exc:
                msg = f"branch={branch['id']} failed: {exc}"
                logger.error(msg)
                errors.append(msg)
                products_by_store[str(branch["id"])] = []

    duration = time.monotonic() - t0
    total = sum(len(v) for v in products_by_store.values())

    return ScrapeResult(
        chain=CHAIN,
        stores_scraped=len(branches),
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
    """Fetch the live branch list from the Stor.ai API for Tiv Taam (retailer 1062).

    Returns a list of :class:`Branch` objects that can be used to refresh
    :data:`ONLINE_BRANCHES`.
    """
    url = f"{BASE_URL}/v2/retailers/{RETAILER_ID}/branches?appId=4&languageId=1"
    headers = get_browser_headers(BASE_URL)
    connector = aiohttp.TCPConnector(ssl=make_ssl_context())
    async with aiohttp.ClientSession(connector=connector) as session:
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
    logger.info("update_branches: found %d Tiv Taam branches", len(branches))
    return branches


# ---------------------------------------------------------------------------
# Legacy convenience wrappers (backward-compat)
# ---------------------------------------------------------------------------


async def scrape_all_branches(
    branches: List[Branch] = ONLINE_BRANCHES,
    categories: List[str] = CATEGORIES,
    batch_size: int = 100,
    max_concurrent: int = 15,
) -> Dict[int, List]:
    """Legacy wrapper — returns branch_id (int) -> list of UnifiedProduct."""
    result = await scrape(
        branches=branches,
        flt=ScrapeFilter(category_ids=categories),
        batch_size=batch_size,
        max_concurrent=max_concurrent,
    )
    return {int(k): v for k, v in result["products_by_store"].items()}


async def scrape_all_categories() -> List[UnifiedProduct]:
    """Scrape all products from all branches (flattened list)."""
    results = await scrape_all_branches()
    return [p for products in results.values() for p in products]
