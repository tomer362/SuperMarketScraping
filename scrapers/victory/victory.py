"""
Victory scraper  (ויקטורי)
===========================
Platform : ZuZ (AngularJS) — retailer ID 1470
Base URL : https://www.victoryonline.co.il

Key endpoints
-------------
1. Branch list:
     GET /v2/retailers/1470/branches?appId=2&languageId=1

2. Per-branch, per-category product catalogue (appId=4, offset pagination):
     GET /v2/retailers/1470/branches/{bid}/categories/{catId}/products
         ?appId=4&from={offset}&size={size}&languageId=1
         &categorySort={"sortType":1}
         &filters={"mustNot":{"term":{"branch.isOutOfStock":true}}}
   Response: { "total": N, "products": [ ... ] }
   Branch data lives in product["branch"] (singular dict, not keyed by branch ID).

Key differences from old appId=2 global endpoint
-------------------------------------------------
- Branch data is in product["branch"] (singular), NOT product["branches"][str(id)].
- Barcode is NOT a top-level field; extracted from the image URL via regex.
- Image URL contains {{size}} and {{extension||'jpg'}} template placeholders.
- Categories are in product["family"]["categories"] (list), not product["department"].
- Brand is in product["brand"]["names"]["1"].
- The old /v2/retailers/1470/products global endpoint is capped and misses many
  products (e.g. eggs, fresh chicken). Per-branch/per-category avoids the cap.
"""

from __future__ import annotations

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

logger = get_module_logger("victory")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHAIN = "victory"
RETAILER_ID = 1470
BASE_URL = "https://www.victoryonline.co.il"

# Regex to extract a barcode (7–14 digits) from the image URL.
_BARCODE_RE = re.compile(r"/(\d{7,14})-")

# ---------------------------------------------------------------------------
# Branch list (confirmed via API 2026-03)
# ---------------------------------------------------------------------------


class Branch(TypedDict):
    id: int
    name: str
    city: str
    location: str


ONLINE_BRANCHES: List[Branch] = [
    {"id": 2930, "name": "אינטרנט", "city": "לוד", "location": ""},
    {
        "id": 2439,
        "name": "ויקטורי אופקים - Victory Online",
        "city": "אופקים",
        "location": "",
    },
    {
        "id": 2527,
        "name": "ויקטורי אשדוד - Victory Online",
        "city": "אשדוד",
        "location": "",
    },
    {
        "id": 2530,
        "name": "ויקטורי אשקלון-- Victory Online",
        "city": "אשקלון",
        "location": "",
    },
    {
        "id": 2435,
        "name": "ויקטורי בית שמש - Victory Online",
        "city": "בית שמש",
        "location": "",
    },
    {
        "id": 2331,
        "name": "ויקטורי גן יבנה - Victory Online",
        "city": "גן יבנה",
        "location": "",
    },
    {
        "id": 2539,
        "name": "ויקטורי טירת הכרמל - Victory Online",
        "city": "טירת הכרמל",
        "location": "",
    },
    {
        "id": 2448,
        "name": "ויקטורי ירושלים קניון מלחה - Victory Online",
        "city": "ירושלים",
        "location": "",
    },
    {
        "id": 2427,
        "name": 'ויקטורי לוד נתב"ג - Victory Online',
        "city": "לוד",
        "location": "",
    },
    {
        "id": 2451,
        "name": "ויקטורי נתיבות - Victory Online",
        "city": "נתיבות",
        "location": "",
    },
    {
        "id": 2449,
        "name": "ויקטורי נתניה - Victory Online",
        "city": "נתניה",
        "location": "",
    },
    {
        "id": 3433,
        "name": "ויקטורי עמק חפר - Victory Online",
        "city": "עמק חפר",
        "location": "",
    },
    {"id": 2550, "name": "ויקטורי עפולה", "city": "עפולה", "location": ""},
    {"id": 2552, "name": "ויקטורי צור יצחק", "city": "צור יצחק", "location": ""},
    {
        "id": 2447,
        "name": "ויקטורי קניון אילון- Victory Online",
        "city": "רמת גן",
        "location": "",
    },
    {
        "id": 2438,
        "name": "ויקטורי קריית מוצקין - Victory Online",
        "city": "קרית מוצקין",
        "location": "",
    },
    {
        "id": 2444,
        "name": "ויקטורי ראש העין פארק אפק - Victory Online",
        "city": "ראש העין",
        "location": "",
    },
    {
        "id": 2442,
        "name": "ויקטורי ראשון לציון מזרח - Victory Online",
        "city": "ראשון לציון",
        "location": "",
    },
    {
        "id": 2446,
        "name": "ויקטורי רחובות - Victory Online",
        "city": "רחובות",
        "location": "",
    },
    {
        "id": 2547,
        "name": "ויקטורי רמת גן - Victory Online",
        "city": "רמת גן",
        "location": "",
    },
    {
        "id": 2440,
        "name": "ויקטורי רעננה פארק - Victory Online",
        "city": "רעננה",
        "location": "",
    },
    {"id": 3055, "name": "שמוטקין ראשון לציון", "city": "ראשון לציון", "location": ""},
    {"id": 2437, "name": "ויקטורי אורנית", "city": "אורנית", "location": ""},
    {"id": 2528, "name": "ויקטורי אלקנה", "city": "אלקנה", "location": ""},
    {"id": 2529, "name": "ויקטורי אשדוד", "city": "אשדוד", "location": ""},
    {"id": 2531, "name": "ויקטורי אשקלון", "city": "אשקלון", "location": ""},
    {"id": 2532, "name": "ויקטורי אשקלון", "city": "אשקלון", "location": ""},
    {
        "id": 2533,
        "name": "ויקטורי באר שבע - Victory Online",
        "city": "באר שבע",
        "location": "",
    },
    {"id": 2534, "name": "ויקטורי בית שאן", "city": "בית שאן", "location": ""},
    {"id": 2535, "name": "ויקטורי גדרה", "city": "גדרה", "location": ""},
    {"id": 2536, "name": "ויקטורי גני תקווה", "city": "גני תקווה", "location": ""},
    {"id": 2537, "name": "ויקטורי דימונה", "city": "דימונה", "location": ""},
    {"id": 2538, "name": "ויקטורי חדרה", "city": "חדרה", "location": ""},
    {"id": 2540, "name": "ויקטורי יבנה", "city": "יבנה", "location": ""},
    {"id": 2541, "name": "ויקטורי כפר יונה", "city": "כפר יונה", "location": ""},
    {"id": 2542, "name": "ויקטורי כפר סבא", "city": "כפר סבא", "location": ""},
    {"id": 2543, "name": "ויקטורי לוד רכבת", "city": "לוד", "location": ""},
    {"id": 2544, "name": "ויקטורי מבשרת", "city": "מבשרת", "location": ""},
    {"id": 2545, "name": "ויקטורי מודיעין", "city": "מודיעין", "location": ""},
    {"id": 2546, "name": "ויקטורי נתיבות", "city": "נתיבות", "location": ""},
    {"id": 2549, "name": "ויקטורי עכו", "city": "עכו", "location": ""},
    {"id": 2551, "name": "ויקטורי פתח תקווה", "city": "פתח תקווה", "location": ""},
    {"id": 2553, "name": "ויקטורי צמח", "city": "צמח", "location": ""},
    {"id": 2554, "name": "ויקטורי קריית אתא", "city": "קרית אתא", "location": ""},
    {"id": 2555, "name": "ויקטורי קרית גת", "city": "קרית גת", "location": ""},
    {"id": 2556, "name": "ויקטורי קרית גת", "city": "קרית גת", "location": ""},
    {"id": 2557, "name": "ויקטורי קרית טבעון", "city": "קרית טבעון", "location": ""},
    {"id": 2558, "name": "ויקטורי קרית מלאכי", "city": "קרית מלאכי", "location": ""},
    {"id": 2560, "name": "ויקטורי ראש העין", "city": "ראש העין", "location": ""},
    {"id": 2559, "name": "ויקטורי ראשון לציון", "city": "ראשון לציון", "location": ""},
    {"id": 2561, "name": "ויקטורי רמלה", "city": "רמלה", "location": ""},
    {"id": 2562, "name": "ויקטורי רמת ישי", "city": "רמת ישי", "location": ""},
    {"id": 2563, "name": "ויקטורי רעננה", "city": "רעננה", "location": ""},
    {"id": 2564, "name": "ויקטורי שדרות", "city": "שדרות", "location": ""},
    {"id": 2565, "name": "ויקטורי שוהם", "city": "שוהם", "location": ""},
    {"id": 2566, "name": "ויקטורי תל אביב", "city": "תל אביב", "location": ""},
    {"id": 2567, "name": "ויקטורי תל אביב", "city": "תל אביב", "location": ""},
    {"id": 2548, "name": "ויקטורי תל אביב", "city": "תל אביב", "location": ""},
    {
        "id": 2568,
        "name": "ויקטורי תל אביב - Victory Online",
        "city": "תל אביב",
        "location": "",
    },
    {"id": 2569, "name": "ויקטורי תל מונד", "city": "תל מונד", "location": ""},
]

# ---------------------------------------------------------------------------
# Top-level categories for Victory (discovered from data.js 2026-03).
# All tree-level categories are included; the scraper probes each one per
# branch and skips categories that return 0 products for that branch.
# ---------------------------------------------------------------------------

MAIN_CATEGORIES: List[Tuple[int, str]] = [
    (95840, "cat_95840"),
    (120357, "cat_120357"),
    (97314, "cat_97314"),
    (94600, "cat_94600"),
    (96505, "טקסטיל, כלי בית ,מוצרי חשמל"),
    (93755, "cat_93755"),
    (94523, "cat_94523"),
    (96764, "cat_96764"),
    (94246, "cat_94246"),
    (96794, "cat_96794"),
    (99065, "היגיינת הפה והגוף,דאורדורנט,שמפו ,סבון גוף וידיים"),
    (79704, "פירות וירקות"),
    (79718, "חלבי"),
    (79687, "מאפיה"),
    (79821, "קצבייה טרי"),
    (79731, "דגני בוקר"),
    (79619, "שימורים"),
    (79603, "מעדניה נקניקים"),
    (79591, "קפואים"),
    (79667, "משקאות קלים"),
    (79835, "ללא גלוטן"),
    (79653, "חטיפים מלוחים"),
    (79740, "אביזרי ניקיון"),
    (79571, "היגיינה"),
    (79764, "חד פעמי ושקיות"),
]

# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------


def _category_products_url(branch_id: int, cat_id: int) -> str:
    return (
        f"{BASE_URL}/v2/retailers/{RETAILER_ID}"
        f"/branches/{branch_id}/categories/{cat_id}/products"
    )


def _branches_url() -> str:
    return f"{BASE_URL}/v2/retailers/{RETAILER_ID}/branches"


# ---------------------------------------------------------------------------
# Barcode + image helpers
# ---------------------------------------------------------------------------


def _extract_barcode(image_url: Optional[str]) -> Optional[str]:
    """Extract barcode from a ZuZ image URL (digits before a dash)."""
    if not image_url:
        return None
    m = _BARCODE_RE.search(image_url)
    return m.group(1) if m else None


def _expand_image_url(raw: Optional[str]) -> Optional[str]:
    """Expand ZuZ image URL template placeholders."""
    if not raw:
        return None
    url = raw.replace("{{size}}", "large")
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
# Deal extraction (ZuZ specials)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Product mapping → UnifiedProduct (appId=4 schema)
# ---------------------------------------------------------------------------


def _to_unified(
    item: Dict[str, Any],
    branch: Branch,
    scraped_at: str,
) -> Optional[UnifiedProduct]:
    """Convert a ZuZ appId=4 product dict to a UnifiedProduct for the given branch.

    Returns None if the product is inactive / invisible / has no price.
    Branch data is in item["branch"] (singular), NOT item["branches"][str(id)].
    """
    branch_info: Dict[str, Any] = item.get("branch") or {}

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

    # Image URL: expand template, then extract barcode from it
    raw_image_url: Optional[str] = (item.get("image") or {}).get("url") or None
    image_url = _expand_image_url(raw_image_url)
    barcode = _extract_barcode(image_url)

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

    # Category IDs come from family.categories (list of {id, names})
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

    deal = _extract_deal(
        branch_info, regular_price, sale_price, qty_si, dimension, is_weighable
    )

    return UnifiedProduct(
        chain=CHAIN,
        store_id=str(branch["id"]),
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
        brand=brand,
        manufacturer=None,
        scraped_at=scraped_at,
    )


# ---------------------------------------------------------------------------
# Paginated fetch for one branch across all categories
# ---------------------------------------------------------------------------


async def _fetch_branch_products(
    session: aiohttp.ClientSession,
    branch: Branch,
    *,
    name_query: Optional[str] = None,
    batch_size: int = 100,
    max_concurrent: int = 15,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> List[Dict[str, Any]]:
    """Fetch all products for a single branch by iterating MAIN_CATEGORIES.

    Each category is paginated independently with the appId=4 per-branch
    endpoint (no global cap).  Products are deduplicated by productId.
    """
    branch_id = branch["id"]
    all_products: Dict[str, Dict[str, Any]] = {}  # keyed by productId for dedup

    common_params = (
        "appId=4&languageId=1"
        '&categorySort={"sortType":1}'
        '&filters={"mustNot":{"term":{"branch.isOutOfStock":true}}}'
    )
    if name_query:
        common_params += f"&q={quote(name_query)}"

    for cat_id, cat_name in MAIN_CATEGORIES:
        base_url = _category_products_url(branch_id, cat_id)

        probe_url = f"{base_url}?{common_params}&from=0&size=1"
        probe = await _fetch_page(
            session,
            probe_url,
            max_retries=max_retries,
            base_delay=base_delay,
            label=f"probe branch={branch_id} cat={cat_id}",
        )
        total = probe.get("total", 0)
        if total == 0:
            logger.debug(
                "victory: branch=%s category %s (%s) — 0 products",
                branch_id,
                cat_id,
                cat_name,
            )
            continue

        logger.info(
            "victory: branch=%s category %s (%s) — %d products",
            branch_id,
            cat_id,
            cat_name,
            total,
        )

        offsets = list(range(0, total, batch_size))

        async def _fetch_offset(
            offset: int,
            _cat_id: int = cat_id,
            _cat_name: str = cat_name,
        ) -> List[Dict[str, Any]]:
            url = (
                f"{_category_products_url(branch_id, _cat_id)}"
                f"?{common_params}&from={offset}&size={batch_size}"
            )
            data = await _fetch_page(
                session,
                url,
                max_retries=max_retries,
                base_delay=base_delay,
                label=f"branch={branch_id} cat={_cat_id} offset={offset}",
            )
            return data.get("products", [])

        task_fns = [lambda off=off: _fetch_offset(off) for off in offsets]
        results = await run_concurrently(task_fns, max_concurrent=max_concurrent)

        for r in results:
            if isinstance(r, Exception):
                logger.warning(
                    "Page fetch error branch=%s cat=%s: %s", branch_id, cat_id, r
                )
            elif r:
                for product in r:
                    pid = str(product.get("productId") or product.get("id") or "")
                    if pid and pid not in all_products:
                        all_products[pid] = product

    logger.info(
        "victory: branch=%s (%s) — %d unique products across all categories",
        branch_id,
        branch.get("name", ""),
        len(all_products),
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
    """Scrape Victory and return a unified ScrapeResult."""
    if branches is None:
        branches = ONLINE_BRANCHES
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
        products_by_store: Dict[str, List[UnifiedProduct]] = {}

        for branch in branches:
            try:
                raw_products = await _fetch_branch_products(
                    session,
                    branch,
                    name_query=name_query,
                    batch_size=batch_size,
                    max_concurrent=max_concurrent,
                    max_retries=max_retries,
                    base_delay=base_retry_delay,
                )
            except Exception as exc:
                msg = f"branch={branch['id']} fetch failed: {exc}"
                logger.error(msg)
                errors.append(msg)
                products_by_store[str(branch["id"])] = []
                continue

            # Map raw → UnifiedProduct, apply post-filters, deduplicate
            products: List[UnifiedProduct] = []
            seen_ids: set = set()

            for item in raw_products:
                p = _to_unified(item, branch, scraped_at)
                if p is None:
                    continue
                if filter_barcode and p.get("barcode") != filter_barcode:
                    continue
                if filter_cats and not any(
                    c in filter_cats for c in p.get("category_ids", [])
                ):
                    continue
                pid = p["product_id"]
                if pid not in seen_ids:
                    seen_ids.add(pid)
                    products.append(p)

            logger.info(
                "victory: branch=%s (%s) — %d unique active products",
                branch["id"],
                branch.get("name", ""),
                len(products),
            )
            products_by_store[str(branch["id"])] = products

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
# update_branches
# ---------------------------------------------------------------------------


async def update_branches() -> List[Branch]:
    """Fetch the live branch list from the ZuZ API for Victory (retailer 1470)."""
    connector = aiohttp.TCPConnector(ssl=make_ssl_context())
    async with aiohttp.ClientSession(connector=connector) as session:
        branches = await fetch_branches(session)
    logger.info("update_branches: found %d Victory branches", len(branches))
    return branches
