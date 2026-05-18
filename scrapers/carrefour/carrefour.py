"""
Carrefour Israel scraper
========================
Platform: Stor.ai — retailer ID 1540
Base URL: https://www.carrefour.co.il

Key endpoints
-------------
1. Branch list:
     GET /v2/retailers/1540/branches?appId=4&languageId=1

2. Category products (offset pagination):
     GET /v2/retailers/1540/branches/{branch_id}/categories/{category_id}/products
         ?appId=4&from={offset}&size={size}&languageId=1

3. Name search (autocomplete / full-text):
     GET /v2/retailers/1540/branches/{branch_id}/products/autocomplete
         ?appId=4&isSearch=true&languageId=1&size={size}&from={offset}&q={query}
   Response shape is identical to the category products endpoint.

Category discovery
------------------
There is no standalone categories endpoint.  Categories are discovered
dynamically from ``family.categories[]`` on products returned by the API.
A seed scan of 15 known top-level category IDs collects all sub-category IDs
concurrently (typically ~90 categories in ~0.9 s per branch).

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
from typing import Any, Dict, List, Optional, TypedDict
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

logger = get_module_logger("carrefour")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHAIN = "carrefour"
RETAILER_ID = 1540
BASE_URL = "https://www.carrefour.co.il"

# ---------------------------------------------------------------------------
# Branch list
# ---------------------------------------------------------------------------


class Branch(TypedDict):
    id: int
    name: str
    city: str
    location: str


ONLINE_BRANCHES: List[Branch] = [
    {"id": 3019, "name": "אור יהודה", "city": "אור יהודה", "location": ""},
    {"id": 2996, "name": "אור עקיבא", "city": "אור עקיבא", "location": ""},
    {"id": 3005, "name": "אילת", "city": "אילת", "location": ""},
    {"id": 2998, "name": "אשדוד", "city": "אשדוד", "location": ""},
    {"id": 2992, "name": "אשקלון", "city": "אשקלון", "location": ""},
    {"id": 3466, "name": "באר שבע", "city": "באר שבע", "location": ""},
    {"id": 3010, "name": "בית שמש", "city": "בית שמש", "location": ""},
    {"id": 3012, "name": "גבעתיים", "city": "גבעתיים", "location": ""},
    {"id": 3476, "name": "דליית אל כרמל", "city": "דליית אל כרמל", "location": ""},
    {"id": 3212, "name": "הרצליה", "city": "הרצליה", "location": ""},
    {"id": 3008, "name": "חיפה", "city": "חיפה", "location": ""},
    {"id": 3013, "name": "ירושלים", "city": "ירושלים", "location": ""},
    {"id": 3003, "name": "כפר סבא", "city": "כפר סבא", "location": ""},
    {"id": 3020, "name": "לוד", "city": "לוד", "location": ""},
    {"id": 3360, "name": "נווה אילן", "city": "נווה אילן", "location": ""},
    {"id": 2995, "name": "נתניה", "city": "נתניה", "location": ""},
    {"id": 3458, "name": "עפולה", "city": "עפולה", "location": ""},
    {"id": 2997, "name": "פתח תקווה", "city": "פתח תקווה", "location": ""},
    {"id": 3017, "name": "קריית אתא", "city": "קריית אתא", "location": ""},
    {"id": 3018, "name": "רחובות", "city": "רחובות", "location": ""},
    {"id": 3007, "name": "רמלה", "city": "רמלה", "location": ""},
    {"id": 3361, "name": "שדרות", "city": "שדרות", "location": ""},
    {"id": 3014, "name": "תל אביב", "city": "תל אביב", "location": ""},
]

# Known top-level category seeds (discovered via API research 2026-03)
_SEED_CATEGORIES: List[str] = [
    "79704",
    "79718",
    "79591",
    "79619",
    "79653",
    "79667",
    "79687",
    "79764",
    "79807",
    "79571",
    "79740",
    "79835",
    "79603",
    "79821",
    "95010",
]

# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------


def _products_url(branch_id: int, category_id: str) -> str:
    return (
        f"{BASE_URL}/v2/retailers/{RETAILER_ID}/branches/{branch_id}"
        f"/categories/{category_id}/products"
    )


def _branches_url() -> str:
    return f"{BASE_URL}/v2/retailers/{RETAILER_ID}/branches"


# ---------------------------------------------------------------------------
# Barcode extraction
# ---------------------------------------------------------------------------


def _extract_barcode(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"/gs1-products/\d+/[^/]+/(\d{8,14})-\d+", url)
    return m.group(1) if m else None


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
        logger.debug("GET %s", label or full_url)
        async with session.get(full_url, headers=headers) as resp:
            if resp.status != 200:
                raise aiohttp.ClientResponseError(
                    resp.request_info,
                    resp.history,
                    status=resp.status,
                    message=f"HTTP {resp.status}",
                )
            data = await resp.json()
            total = data.get("total", "?")
            n = len(data.get("products", []))
            logger.debug("  → %s: %s total, %d items in page", label or "?", total, n)
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
        logger.error("Failed %s: %s", label or full_url, exc)
        return {}


async def fetch_branches(session: aiohttp.ClientSession) -> List[Branch]:
    """Fetch the live list of online branches from the API."""
    url = f"{_branches_url()}?appId=4&languageId=1"
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
# Category discovery
# ---------------------------------------------------------------------------


def _collect_category_ids(data: Dict[str, Any]) -> List[str]:
    cat_ids: List[str] = []
    for item in data.get("products", []):
        family = item.get("family") or {}
        for cat in family.get("categories") or []:
            cid = cat.get("id")
            if cid is not None:
                cat_ids.append(str(cid))
    return cat_ids


async def discover_categories(
    session: aiohttp.ClientSession,
    branch_id: int,
    seed_size: int = 50,
    max_concurrent: int = 15,
) -> List[str]:
    """Discover all category IDs by concurrently sampling from seed categories."""
    task_fns = [
        (
            lambda seed=seed: _fetch_page(
                session,
                _products_url(branch_id, seed),
                f"appId=4&from=0&languageId=1&size={seed_size}",
                label=f"branch={branch_id} seed={seed}",
            )
        )
        for seed in _SEED_CATEGORIES
    ]
    results = await run_concurrently(task_fns, max_concurrent=max_concurrent)

    all_cat_ids: List[str] = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Seed fetch error: %s", r)
        elif r:
            all_cat_ids.extend(_collect_category_ids(r))

    unique = list(dict.fromkeys(all_cat_ids))
    logger.info(
        "Discovered %d categories from branch=%s (%d seeds)",
        len(unique),
        branch_id,
        len(_SEED_CATEGORIES),
    )
    return unique


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
        # Carrefour (Stor.ai 1540): image template has {{size}} ONLY, no {{extension}}
        image_url = raw_image["url"].replace("{{size}}", "medium")

    barcode = _extract_barcode(image_url)

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

    canonical_uom, qty_si, dimension, _si_per = normalize_unit(raw_uom, unit_qty_raw)

    unit_description: Optional[str] = None
    if unit_qty_raw is not None and canonical_uom:
        unit_description = f"{unit_qty_raw:g} {canonical_uom}"

    ppbu = compute_price_per_base_unit(effective_price, qty_si, dimension, is_weighable)

    family_cats: List[str] = []
    for cat in (item.get("family") or {}).get("categories") or []:
        cid = cat.get("id")
        if cid is not None:
            family_cats.append(str(cid))
    if not family_cats:
        family_cats = [str(category_id)]

    # Deal extraction (reuse same Stor.ai specials logic as Tiv Taam)
    specials = branch_info.get("specials") or []
    deal: Optional[DealInfo] = None
    if sale_price is not None and sale_price < regular_price_f:
        ppbu_reg = compute_price_per_base_unit(
            regular_price_f, qty_si, dimension, is_weighable
        )
        ppbu_deal = compute_price_per_base_unit(
            sale_price, qty_si, dimension, is_weighable
        )
        deal = DealInfo(
            has_deal=True,
            deal_type="price_reduction",
            deal_description=f"מחיר מבצע: ₪{sale_price:.2f} (במקום ₪{regular_price_f:.2f})",
            deal_price=sale_price,
            deal_min_qty=1,
            deal_price_per_unit=sale_price,
            price_per_base_unit=ppbu_reg,
            price_per_base_unit_deal=ppbu_deal,
        )
    elif specials:
        for special in specials:
            fl = special.get("firstLevel") or {}
            stype = fl.get("type")
            desc_names = special.get("names") or {}
            heb_name = (desc_names.get("1") or {}).get("name") or special.get(
                "description", ""
            )
            ppbu_reg = compute_price_per_base_unit(
                regular_price_f, qty_si, dimension, is_weighable
            )
            if stype == 2:
                qty_req = fl.get("firstPurchaseTotal")
                deal_total = (fl.get("firstGift") or {}).get("total")
                if qty_req and deal_total:
                    qty_req = int(qty_req)
                    deal_total = float(deal_total)
                    if qty_req <= 0:
                        logger.debug(
                            "branch=%s: skipping multi_buy special with qty_req=%s (rounds to 0)",
                            branch["id"],
                            fl.get("firstPurchaseTotal"),
                        )
                        continue
                    per_unit = round(deal_total / qty_req, 4)
                    ppbu_deal = compute_price_per_base_unit(
                        per_unit, qty_si, dimension, is_weighable
                    )
                    deal = DealInfo(
                        has_deal=True,
                        deal_type="multi_buy",
                        deal_description=heb_name or special.get("description", ""),
                        deal_price=deal_total,
                        deal_min_qty=qty_req,
                        deal_price_per_unit=per_unit,
                        price_per_base_unit=ppbu_reg,
                        price_per_base_unit_deal=ppbu_deal,
                    )
                    break
            elif stype == 3:
                deal = DealInfo(
                    has_deal=True,
                    deal_type="cart_total",
                    deal_description=heb_name or special.get("description", ""),
                    deal_price=None,
                    deal_min_qty=None,
                    deal_price_per_unit=None,
                    price_per_base_unit=ppbu_reg,
                    price_per_base_unit_deal=None,
                )
                break

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
# Category scraper
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
    url = _products_url(branch_id, category_id)

    probe = await _fetch_page(
        session,
        url,
        "appId=4&from=0&languageId=1&size=1",
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
        before = len(products)
        for item in items:
            p = _to_unified(item, branch, category_id, scraped_at)
            if p:
                products.append(p)
        logger.debug(
            "branch=%s cat=%s offset=%d — parsed %d/%d products (running total: %d)",
            branch_id,
            category_id,
            offset,
            len(products) - before,
            len(items),
            len(products),
        )
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
    does not support pagination.  The category products endpoint supports q= with
    proper total + pagination, so we fan out across all categories concurrently
    and deduplicate.
    """
    branch_id = branch["id"]
    encoded_q = quote(name_query)

    logger.info(
        "branch=%s: searching '%s' across %d categories (max_concurrent=%d)…",
        branch_id,
        name_query,
        len(categories),
        max_concurrent,
    )

    async def _search_category(cat: str) -> List[UnifiedProduct]:
        url = _products_url(branch_id, cat)
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
            logger.debug(
                "branch=%s cat=%s search='%s': 0 hits — skip",
                branch_id,
                cat,
                name_query,
            )
            return []

        logger.debug(
            "branch=%s cat=%s search='%s': %d hits", branch_id, cat, name_query, total
        )

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
            before = len(cat_products)
            for item in items:
                p = _to_unified(item, branch, cat, scraped_at)
                if p:
                    cat_products.append(p)
            logger.debug(
                "branch=%s cat=%s search offset=%d — parsed %d/%d products",
                branch_id,
                cat,
                offset,
                len(cat_products) - before,
                len(items),
            )
            offset += batch_size
        return cat_products

    task_fns = [lambda c=cat: _search_category(c) for cat in categories]
    results = await run_concurrently(task_fns, max_concurrent=max_concurrent)

    all_products: List[UnifiedProduct] = []
    cats_with_hits = 0
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Search category error branch=%s: %s", branch_id, r)
        elif r:
            all_products.extend(r)
            cats_with_hits += 1

    logger.info(
        "branch=%s search='%s' — %d raw hits across %d/%d categories",
        branch_id,
        name_query,
        len(all_products),
        cats_with_hits,
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
    shared_categories: Optional[List[str]],
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
        # Discover categories if not already provided
        cats = (
            filter_cats
            if filter_cats
            else (
                shared_categories
                if shared_categories is not None
                else await discover_categories(
                    session, branch["id"], max_concurrent=max_concurrent
                )
            )
        )
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
        if shared_categories is not None:
            cats = filter_cats if filter_cats else shared_categories
        else:
            cats = (
                filter_cats
                if filter_cats
                else await discover_categories(
                    session, branch["id"], max_concurrent=max_concurrent
                )
            )

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
                logger.error("Category error branch=%s: %s", branch["id"], r)
            elif r:
                products.extend(r)

    # Post-filters
    if filter_barcode:
        products = [p for p in products if p.get("barcode") == filter_barcode]
    if name_query and filter_cats:
        products = [
            p
            for p in products
            if any(c in filter_cats for c in p.get("category_ids", []))
        ]

    # De-duplicate by product_id
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
    batch_size: int = 300,
    max_concurrent: int = 15,
    branch_concurrent: int = 4,
    max_retries: int = 3,
    base_retry_delay: float = 1.0,
) -> ScrapeResult:
    """Scrape Carrefour Israel and return a unified ScrapeResult.

    Args:
        branches:         Branches to scrape (default: live API branches).
        flt:              Optional filters (name_query, category_ids, barcode).
        batch_size:       Products per paginated request.
        max_concurrent:   Max concurrent category requests per branch.
        max_retries:      Max retry attempts per request.
        base_retry_delay: Base delay (seconds) for exponential backoff.

    Returns:
        ScrapeResult with products_by_store keyed by str(branch_id).
    """
    if flt is None:
        flt = {}

    scraped_at = utc_now_iso()
    t0 = time.monotonic()
    errors: List[str] = []

    connector = aiohttp.TCPConnector(ssl=make_ssl_context())
    async with aiohttp.ClientSession(connector=connector) as session:
        if branches is None:
            live_branches = await fetch_branches(session)
            if live_branches:
                branches = live_branches
                logger.info("carrefour: using %d live branches from API", len(branches))
            else:
                branches = ONLINE_BRANCHES
                logger.warning(
                    "carrefour: live branch discovery failed; falling back to %d static branch(es)",
                    len(branches),
                )

        # Discover categories once from the first branch (chain-wide)
        shared_categories: Optional[List[str]] = None
        if not flt.get("name_query") and not flt.get("category_ids") and branches:
            logger.info(
                "Discovering Carrefour categories from branch %s…", branches[0]["id"]
            )
            shared_categories = await discover_categories(
                session, branches[0]["id"], max_concurrent=max_concurrent
            )

        async def _scrape_one_branch(branch: Branch) -> tuple[str, List[UnifiedProduct], Optional[str]]:
            try:
                prods = await _scrape_branch(
                    session,
                    branch,
                    flt,
                    shared_categories,
                    batch_size,
                    max_concurrent,
                    max_retries,
                    base_retry_delay,
                    scraped_at,
                )
                return str(branch["id"]), prods, None
            except Exception as exc:
                msg = f"branch={branch['id']} failed: {exc}"
                logger.error(msg)
                return str(branch["id"]), [], msg

        products_by_store: Dict[str, List[UnifiedProduct]] = {}
        task_fns = [lambda branch=branch: _scrape_one_branch(branch) for branch in branches]
        results = await run_concurrently(task_fns, max_concurrent=branch_concurrent)
        for result in results:
            if isinstance(result, Exception):
                msg = f"branch task failed: {result}"
                logger.error(msg)
                errors.append(msg)
                continue
            branch_id, products, error = result
            if error:
                errors.append(error)
            products_by_store[branch_id] = products

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
    """Fetch the live branch list from the Stor.ai API for Carrefour (retailer 1540).

    Returns a list of :class:`Branch` objects that can be used to refresh
    :data:`ONLINE_BRANCHES`.
    """
    connector = aiohttp.TCPConnector(ssl=make_ssl_context())
    async with aiohttp.ClientSession(connector=connector) as session:
        branches = await fetch_branches(session)
    logger.info("update_branches: found %d Carrefour branches", len(branches))
    return branches


# ---------------------------------------------------------------------------
# Legacy convenience wrappers (backward-compat)
# ---------------------------------------------------------------------------


async def scrape_all_branches(
    branches: List[Branch] = ONLINE_BRANCHES,
    category_ids: Optional[List[str]] = None,
    batch_size: int = 100,
    max_concurrent: int = 15,
) -> Dict[int, List[UnifiedProduct]]:
    """Legacy wrapper — returns branch_id (int) -> list of UnifiedProduct."""
    flt: ScrapeFilter = {}
    if category_ids:
        flt["category_ids"] = category_ids
    result = await scrape(
        branches=branches, flt=flt, batch_size=batch_size, max_concurrent=max_concurrent
    )
    return {int(k): v for k, v in result["products_by_store"].items()}
