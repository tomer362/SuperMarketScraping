"""
Yenot Bitan scraper  (יינות ביתן)
====================================
Platform : ZuZ (AngularJS) — retailer ID 1131
Base URL : https://www.ybitan.co.il

Key endpoints
-------------
1. Branch list:
     GET /v2/retailers/1131/branches?appId=2&languageId=1

2. Full product catalogue (offset pagination):
     GET /v2/retailers/1131/products?appId=2&from={offset}&size={size}&languageId=1

3. Search:
     GET /v2/retailers/1131/products?appId=2&q={query}&from=0&size={size}&languageId=1
"""

from __future__ import annotations

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

logger = get_module_logger("ybitan")

CHAIN = "ybitan"
RETAILER_ID = 1131
BASE_URL = "https://www.ybitan.co.il"


class Branch(TypedDict):
    id: int
    name: str
    city: str
    location: str


ONLINE_BRANCHES: List[Branch] = [
    {"id": 960, "name": "אור יהודה - Online", "city": "אור יהודה", "location": ""},
    {"id": 958, "name": "אור עקיבא - Online", "city": "אור עקיבא", "location": ""},
    {"id": 1985, "name": "אילת - ביתן מרקט+online", "city": "אילת", "location": ""},
    {"id": 1855, "name": "אשדוד- Online", "city": "אשדוד", "location": ""},
    {"id": 964, "name": "אשקלון - Online", "city": "אשקלון", "location": ""},
    {"id": 2892, "name": "בית שמש -online", "city": "בית שמש", "location": ""},
    {"id": 1684, "name": "גבעתיים- Online", "city": "גבעתיים", "location": ""},
    {"id": 2777, "name": "גני העיר רחובות - Online", "city": "רחובות", "location": ""},
    {
        "id": 3477,
        "name": "דליית אל כרמל- Online",
        "city": "דליית אל כרמל",
        "location": "",
    },
    {"id": 1973, "name": "הרצליה- online", "city": "הרצליה", "location": ""},
    {"id": 2960, "name": "חיפה- אודיטוריום Online", "city": "חיפה", "location": ""},
    {"id": 1369, "name": "ירושלים - Online", "city": "ירושלים", "location": ""},
    {"id": 1975, "name": "כפר סבא- online", "city": "כפר סבא", "location": ""},
    {"id": 1943, "name": "לוד- Online", "city": "לוד", "location": ""},
    {"id": 1015, "name": "נתניה - Online", "city": "נתניה", "location": ""},
    {"id": 1685, "name": "פתח תקווה- Online", "city": "פתח תקווה", "location": ""},
    {"id": 2177, "name": "קרית אתא- Online", "city": "קרית אתא", "location": ""},
    {"id": 2649, "name": "תל אביב -online", "city": "תל אביב", "location": ""},
    {"id": 987, "name": "אלפי מנשה - ביתן מרקט", "city": "אלפי מנשה", "location": ""},
    {"id": 2325, "name": "אשדוד ח'- ביתן מרקט", "city": "אשדוד", "location": ""},
    {
        "id": 2080,
        "name": "הרצליה- לב הרצליה- ביתן מרקט",
        "city": "הרצליה",
        "location": "",
    },
    {
        "id": 985,
        "name": "מעלה אדומים- ביתן מרקט",
        "city": "מעלה אדומים",
        "location": "",
    },
]


def _products_url() -> str:
    return f"{BASE_URL}/v2/retailers/{RETAILER_ID}/products"


def _branches_url() -> str:
    return f"{BASE_URL}/v2/retailers/{RETAILER_ID}/branches"


async def _fetch_page(
    session: aiohttp.ClientSession,
    params: str,
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    label: str = "",
) -> Dict[str, Any]:
    url = f"{_products_url()}?{params}"
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


async def fetch_branches(session: aiohttp.ClientSession) -> List[Branch]:
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


def _extract_deal(
    branch_info: Dict[str, Any],
    regular_price: float,
    sale_price: Optional[float],
    qty_si: Optional[float],
    dimension: Optional[str],
    is_weighable: bool,
) -> Optional[DealInfo]:
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


def _to_unified(
    item: Dict[str, Any],
    branch: Branch,
    scraped_at: str,
) -> Optional[UnifiedProduct]:
    branch_id_str = str(branch["id"])
    branches_map: Dict[str, Any] = item.get("branches") or {}
    branch_info: Dict[str, Any] = branches_map.get(branch_id_str) or {}

    if not (branch_info.get("isActive") and branch_info.get("isVisible")):
        return None

    regular_price_raw = branch_info.get("regularPrice")
    if regular_price_raw is None or float(regular_price_raw) <= 0:
        return None

    regular_price = float(regular_price_raw)

    names = item.get("names") or {}
    name = (
        (names.get("1") or {}).get("long")
        or (names.get("1") or {}).get("short")
        or item.get("localName", "")
    )
    if not name:
        return None

    barcode: Optional[str] = item.get("barcode") or item.get("localBarcode") or None
    if barcode:
        barcode = str(barcode).strip() or None

    image_url: Optional[str] = (item.get("image") or {}).get("url") or None

    sale_price_raw = branch_info.get("salePrice")
    sale_price: Optional[float] = (
        float(sale_price_raw) if sale_price_raw is not None else None
    )
    effective_price = sale_price if sale_price is not None else regular_price

    discount_pct: Optional[float] = None
    if sale_price is not None and regular_price > 0:
        discount_pct = round((1 - sale_price / regular_price) * 100, 2)

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

    department = item.get("department") or {}
    dept_id = department.get("id")
    category_ids: List[str] = [str(dept_id)] if dept_id is not None else []

    deal = _extract_deal(
        branch_info, regular_price, sale_price, qty_si, dimension, is_weighable
    )

    return UnifiedProduct(
        chain=CHAIN,
        store_id=branch_id_str,
        store_name=branch.get("name", ""),
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
        brand=None,
        manufacturer=None,
        scraped_at=scraped_at,
    )


async def _fetch_all_products(
    session: aiohttp.ClientSession,
    *,
    name_query: Optional[str] = None,
    batch_size: int = 100,
    max_concurrent: int = 15,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> List[Dict[str, Any]]:
    base_params = "appId=2&languageId=1"
    if name_query:
        base_params += f"&q={quote(name_query)}"

    probe = await _fetch_page(
        session,
        f"{base_params}&from=0&size=1",
        max_retries=max_retries,
        base_delay=base_delay,
        label="probe",
    )
    total = probe.get("total", 0)
    if total == 0:
        logger.info("ybitan: 0 products found (probe).")
        return []

    logger.info("ybitan: %d total products to fetch.", total)

    offsets = list(range(0, total, batch_size))

    async def _fetch_offset(offset: int) -> List[Dict[str, Any]]:
        data = await _fetch_page(
            session,
            f"{base_params}&from={offset}&size={batch_size}",
            max_retries=max_retries,
            base_delay=base_delay,
            label=f"offset={offset}",
        )
        return data.get("products", [])

    task_fns = [lambda off=off: _fetch_offset(off) for off in offsets]
    results = await run_concurrently(task_fns, max_concurrent=max_concurrent)

    all_products: List[Dict[str, Any]] = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Page fetch error: %s", r)
        elif r:
            all_products.extend(r)

    return all_products


def _filter_and_map_branch(
    raw_products: List[Dict[str, Any]],
    branch: Branch,
    flt: ScrapeFilter,
    scraped_at: str,
) -> List[UnifiedProduct]:
    filter_barcode = flt.get("barcode")
    filter_cats = flt.get("category_ids")

    products: List[UnifiedProduct] = []
    seen_ids: set = set()

    for item in raw_products:
        p = _to_unified(item, branch, scraped_at)
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
        "branch=%s (%s) — %d unique active products",
        branch["id"],
        branch.get("name", ""),
        len(products),
    )
    return products


async def scrape(
    branches: Optional[List[Branch]] = None,
    flt: Optional[ScrapeFilter] = None,
    batch_size: int = 100,
    max_concurrent: int = 15,
    max_retries: int = 3,
    base_retry_delay: float = 1.0,
) -> ScrapeResult:
    """Scrape Yenot Bitan and return a unified ScrapeResult."""
    if branches is None:
        branches = ONLINE_BRANCHES
    if flt is None:
        flt = {}

    scraped_at = utc_now_iso()
    t0 = time.monotonic()
    errors: List[str] = []

    name_query = flt.get("name_query") or None

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

        products_by_store: Dict[str, List[UnifiedProduct]] = {}
        for branch in branches:
            try:
                prods = _filter_and_map_branch(raw_products, branch, flt, scraped_at)
                products_by_store[str(branch["id"])] = prods
            except Exception as exc:
                msg = f"branch={branch['id']} mapping failed: {exc}"
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


async def update_branches() -> List[Branch]:
    """Fetch the live branch list from the ZuZ API for Yenot Bitan (retailer 1131)."""
    connector = aiohttp.TCPConnector(ssl=make_ssl_context())
    async with aiohttp.ClientSession(connector=connector) as session:
        branches = await fetch_branches(session)
    logger.info("update_branches: found %d Yenot Bitan branches", len(branches))
    return branches
