"""
Shufersal Online scraper
========================
Platform: Shufersal custom backend
Base URL: https://www.shufersal.co.il/online/he

Key endpoints
-------------
1. Search / catalogue (paginated by page number):
     GET /online/he/search/results?q={query}&page={N}
   - Response is JSON when ``Accept: application/json`` header is present.
   - Page size is fixed at 20 (server ignores pageSize param).
   - Pass ``q=`` (empty string) for the full catalogue; pass a query string
     for keyword search (native server-side search).

2. No per-branch filtering — prices are chain-wide.

Unified API
-----------
Call ``scrape()`` to get a ``ScrapeResult`` TypedDict compatible with all
other chain scrapers.  Pass a ``ScrapeFilter`` to restrict by name or barcode.
Category filtering is post-fetch (Shufersal uses its own category codes not
comparable to other chains).
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import re

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

logger = get_module_logger("shufersal")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHAIN = "shufersal"
SHUFERSAL_BASE_URL = "https://www.shufersal.co.il/online/he"
SHUFERSAL_SEARCH_URL = f"{SHUFERSAL_BASE_URL}/search/results"

# Shufersal has a single "virtual store" for the global catalogue
_GLOBAL_STORE_ID = "global"
_GLOBAL_STORE_NAME = "Shufersal Online"

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _build_headers() -> Dict[str, str]:
    headers = get_browser_headers(SHUFERSAL_BASE_URL)
    headers["Accept"] = "application/json, text/plain, */*"
    return headers


async def _fetch_page(
    session: aiohttp.ClientSession,
    page: int,
    query: str = "",
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> Dict[str, Any]:
    params = {"q": query, "page": page}

    async def _do() -> Dict[str, Any]:
        logger.debug("GET shufersal page=%d q='%s'", page, query)
        async with session.get(
            SHUFERSAL_SEARCH_URL, params=params, headers=_build_headers()
        ) as resp:
            if resp.status != 200:
                raise aiohttp.ClientResponseError(
                    resp.request_info,
                    resp.history,
                    status=resp.status,
                    message=f"HTTP {resp.status}",
                )
            data = await resp.json(content_type=None)
            n = len(data.get("results", []))
            total = (data.get("pagination") or {}).get("totalNumberOfResults", "?")
            logger.debug("  → shufersal page=%d: %d items (total=%s)", page, n, total)
            return data

    try:
        return await with_retry(
            _do,
            max_retries=max_retries,
            base_delay=base_delay,
            label=f"shufersal page={page} q='{query}'",
        )
    except Exception as exc:
        logger.error("Failed to fetch shufersal page=%d: %s", page, exc)
        return {}


# ---------------------------------------------------------------------------
# Image helper
# ---------------------------------------------------------------------------


def _pick_image(images: List[Dict[str, Any]]) -> Optional[str]:
    primary = [img for img in images if img.get("imageType") == "PRIMARY"]
    for fmt in ("large", "medium", "product", "small", "thumbnail"):
        for img in primary:
            if img.get("format") == fmt:
                url = img.get("url")
                if url and "default" not in url:
                    return url
    for img in primary:
        url = img.get("url")
        if url and "default" not in url:
            return url
    return None


# ---------------------------------------------------------------------------
# Deal / Promotion extraction
# ---------------------------------------------------------------------------

# Patterns seen in Shufersal promotionMsg field:
#   "2 יח'  ב- 22 ₪"        → multi-buy: 2 units for 22 ILS
#   " ב- 45.90 ₪"            → single unit at 45.90 (price reduction)
#   "3 ב- 10 ₪"              → 3 for 10
_MULTI_BUY_RE = re.compile(
    r"(\d+)\s*(?:יח['\u05f4\u2019]?)?\s*ב[-–]\s*([\d.]+)\s*₪",
    re.UNICODE,
)
_SINGLE_PRICE_RE = re.compile(
    r"ב[-–]\s*([\d.]+)\s*₪",
    re.UNICODE,
)


def _parse_deal(
    item: Dict[str, Any],
    regular_price: float,
    promo_price: Optional[float],
    qty_si: Optional[float],
    dimension: Optional[str],
    is_weighable: bool,
) -> Optional[DealInfo]:
    """Parse Shufersal promotion fields into a unified DealInfo."""
    promotion_msg: Optional[str] = item.get("promotionMsg") or None
    promotion_codes: List[str] = item.get("promotionCodes") or []
    has_any_promo = bool(promotion_msg or promotion_codes)

    ppbu_reg = compute_price_per_base_unit(
        regular_price, qty_si, dimension, is_weighable
    )

    if not has_any_promo and promo_price is None:
        return None

    # Simple price reduction (categoryPrice < price)
    if promo_price is not None and promo_price < regular_price and not promotion_msg:
        ppbu_deal = compute_price_per_base_unit(
            promo_price, qty_si, dimension, is_weighable
        )
        return DealInfo(
            has_deal=True,
            deal_type="price_reduction",
            deal_description=f"מחיר מבצע: ₪{promo_price:.2f}",
            deal_price=promo_price,
            deal_min_qty=1,
            deal_price_per_unit=promo_price,
            price_per_base_unit=ppbu_reg,
            price_per_base_unit_deal=ppbu_deal,
        )

    if promotion_msg:
        # Try multi-buy pattern first: "2 יח' ב- 22 ₪"
        m = _MULTI_BUY_RE.search(promotion_msg)
        if m:
            qty_required = int(m.group(1))
            deal_total = float(m.group(2))
            per_unit = round(deal_total / qty_required, 4)
            ppbu_deal = compute_price_per_base_unit(
                per_unit, qty_si, dimension, is_weighable
            )
            return DealInfo(
                has_deal=True,
                deal_type="multi_buy",
                deal_description=promotion_msg.strip(),
                deal_price=deal_total,
                deal_min_qty=qty_required,
                deal_price_per_unit=per_unit,
                price_per_base_unit=ppbu_reg,
                price_per_base_unit_deal=ppbu_deal,
            )

        # Single-unit price reduction from promotionMsg: " ב- 45.90 ₪"
        m2 = _SINGLE_PRICE_RE.search(promotion_msg)
        if m2:
            deal_price = float(m2.group(1))
            ppbu_deal = compute_price_per_base_unit(
                deal_price, qty_si, dimension, is_weighable
            )
            return DealInfo(
                has_deal=True,
                deal_type="price_reduction",
                deal_description=promotion_msg.strip(),
                deal_price=deal_price,
                deal_min_qty=1,
                deal_price_per_unit=deal_price,
                price_per_base_unit=ppbu_reg,
                price_per_base_unit_deal=ppbu_deal,
            )

        # Unknown format — record as 'other'
        return DealInfo(
            has_deal=True,
            deal_type="other",
            deal_description=promotion_msg.strip(),
            deal_price=None,
            deal_min_qty=None,
            deal_price_per_unit=None,
            price_per_base_unit=ppbu_reg,
            price_per_base_unit_deal=None,
        )

    # Has promotion code but no message
    if promotion_codes:
        return DealInfo(
            has_deal=True,
            deal_type="other",
            deal_description=f"מבצע קוד: {', '.join(promotion_codes)}",
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


def _to_unified(item: Dict[str, Any], scraped_at: str) -> Optional[UnifiedProduct]:
    name = str(item.get("name", "")).strip()
    if not name:
        return None

    # Shufersal: categoryPrice is the promo price; price is the regular shelf price
    category_price_obj = item.get("categoryPrice") or {}
    shelf_price_obj = item.get("price") or {}
    category_price = category_price_obj.get("value")
    shelf_price = shelf_price_obj.get("value")

    # Determine regular price and effective price
    if shelf_price is not None and shelf_price > 0:
        regular_price_f = float(shelf_price)
    elif category_price is not None and category_price > 0:
        regular_price_f = float(category_price)
    else:
        return None

    promo_price: Optional[float] = None
    if (
        category_price is not None
        and category_price > 0
        and shelf_price is not None
        and shelf_price > 0
        and float(category_price) < float(shelf_price)
    ):
        promo_price = float(category_price)

    effective_price = promo_price if promo_price is not None else regular_price_f

    discount_pct: Optional[float] = None
    if promo_price is not None and regular_price_f > 0:
        discount_pct = round((1 - promo_price / regular_price_f) * 100, 2)

    sku = str(item.get("sku") or item.get("code") or "")
    category_codes: List[str] = item.get("allCategoryCodes") or []
    selling_method = (item.get("sellingMethod") or {}).get("code") or "UNKNOWN"
    brand_obj = item.get("brand") or {}
    brand: Optional[str] = (
        brand_obj.get("name") if isinstance(brand_obj, dict) else None
    )
    manufacturer: Optional[str] = item.get("manufacturer") or None
    is_weighable = selling_method == "BY_WEIGHT"
    image_url = _pick_image(item.get("images") or [])

    ean = item.get("ean")
    barcode: Optional[str] = str(ean) if ean else None

    unit_description: Optional[str] = item.get("unitDescription") or None
    raw_uom: Optional[str] = item.get("unitForComparison") or None
    vfc = item.get("valueForComparison")
    unit_qty_raw: Optional[float] = float(vfc) if vfc is not None else None

    # Normalize unit
    canonical_uom, qty_si, dimension, _si_per = normalize_unit(
        raw_uom, unit_qty_raw, unit_description
    )

    ppbu = compute_price_per_base_unit(effective_price, qty_si, dimension, is_weighable)

    deal = _parse_deal(
        item, regular_price_f, promo_price, qty_si, dimension, is_weighable
    )

    return UnifiedProduct(
        chain=CHAIN,
        store_id=_GLOBAL_STORE_ID,
        store_name=_GLOBAL_STORE_NAME,
        product_id=sku,
        name=name,
        price=effective_price,
        regular_price=regular_price_f,
        sale_price=promo_price,
        discount_percent=discount_pct,
        barcode=barcode,
        image_url=image_url,
        category_ids=category_codes,
        is_weighable=is_weighable,
        unit_description=unit_description,
        unit_of_measure=canonical_uom,
        unit_qty=unit_qty_raw,
        unit_qty_si=qty_si,
        unit_dimension=dimension,
        price_per_base_unit=ppbu,
        deal=deal,
        brand=brand,
        manufacturer=manufacturer,
        scraped_at=scraped_at,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def scrape(
    flt: Optional[ScrapeFilter] = None,
    batch_size: int = 20,  # Shufersal page size is fixed at 20; param kept for API consistency
    max_concurrent: int = 15,
    max_retries: int = 3,
    base_retry_delay: float = 1.0,
) -> ScrapeResult:
    """Scrape Shufersal and return a unified ScrapeResult.

    Args:
        flt:              Optional filters (name_query, category_ids, barcode).
                          ``name_query`` uses the native ``q=`` search parameter.
                          ``category_ids`` and ``barcode`` are applied post-fetch.
        batch_size:       Ignored (Shufersal fixes page size at 20); kept for API parity.
        max_concurrent:   Max concurrent page requests.
        max_retries:      Max retry attempts per page request.
        base_retry_delay: Base delay (seconds) for exponential backoff.

    Returns:
        ScrapeResult with products_by_store keyed by ``"global"``.
    """
    if flt is None:
        flt = {}

    name_query = flt.get("name_query", "")
    filter_cats = flt.get("category_ids")
    filter_barcode = flt.get("barcode")

    scraped_at = utc_now_iso()
    t0 = time.monotonic()
    errors: List[str] = []

    connector = aiohttp.TCPConnector(ssl=make_ssl_context())
    async with aiohttp.ClientSession(connector=connector) as session:
        # --- Discover pagination ---
        logger.info("Shufersal: fetching pagination (q='%s')…", name_query)
        probe = await _fetch_page(
            session, 0, name_query, max_retries=max_retries, base_delay=base_retry_delay
        )
        pagination = probe.get("pagination", {})
        if not pagination:
            msg = "Could not retrieve Shufersal pagination info"
            logger.error(msg)
            errors.append(msg)
            return ScrapeResult(
                chain=CHAIN,
                stores_scraped=0,
                products_total=0,
                products_by_store={},
                scraped_at=scraped_at,
                duration_seconds=round(time.monotonic() - t0, 2),
                errors=errors,
            )

        page_size: int = pagination.get("pageSize", 20)
        total_pages: int = pagination.get("numberOfPages", 1)
        total_expected: int = pagination.get("totalNumberOfResults", 0)
        logger.info(
            "Shufersal: %d products across %d pages", total_expected, total_pages
        )

        all_products: List[UnifiedProduct] = []

        # Page 0 already fetched
        for item in probe.get("results", []):
            p = _to_unified(item, scraped_at)
            if p:
                all_products.append(p)

        if total_pages > 1:
            remaining_pages = list(range(1, total_pages))

            async def _fetch_and_parse(pg: int) -> List[UnifiedProduct]:
                data = await _fetch_page(
                    session,
                    pg,
                    name_query,
                    max_retries=max_retries,
                    base_delay=base_retry_delay,
                )
                return [
                    p
                    for item in data.get("results", [])
                    if (p := _to_unified(item, scraped_at)) is not None
                ]

            task_fns = [
                (lambda pg=page: _fetch_and_parse(pg)) for page in remaining_pages
            ]

            # Fetch in concurrent chunks with a small polite delay between chunks
            chunk_size = max_concurrent
            for chunk_start in range(0, len(task_fns), chunk_size):
                chunk = task_fns[chunk_start : chunk_start + chunk_size]
                pages_in_chunk = remaining_pages[chunk_start : chunk_start + chunk_size]
                logger.debug(
                    "Shufersal: fetching pages %d–%d (chunk %d/%d)…",
                    pages_in_chunk[0],
                    pages_in_chunk[-1],
                    chunk_start // chunk_size + 1,
                    (len(task_fns) + chunk_size - 1) // chunk_size,
                )
                results = await run_concurrently(chunk, max_concurrent=chunk_size)
                for r in results:
                    if isinstance(r, Exception):
                        errors.append(str(r))
                        logger.error("Page fetch error: %s", r)
                    elif r:
                        all_products.extend(r)
                logger.debug(
                    "Shufersal: chunk done — running total %d products",
                    len(all_products),
                )
                if chunk_start + chunk_size < len(task_fns):
                    await asyncio.sleep(random.uniform(0.3, 1.0))

        # Post-filters
        if filter_barcode:
            all_products = [
                p for p in all_products if p.get("barcode") == filter_barcode
            ]

        if filter_cats:
            all_products = [
                p
                for p in all_products
                if any(c in filter_cats for c in p.get("category_ids", []))
            ]

        products_by_store = {_GLOBAL_STORE_ID: all_products}

    duration = time.monotonic() - t0
    total = len(all_products)

    if (
        total < total_expected
        and not filter_barcode
        and not filter_cats
        and not name_query
    ):
        logger.warning(
            "Shufersal: fetched %d products, expected %d", total, total_expected
        )

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
# Legacy convenience wrapper (backward-compat)
# ---------------------------------------------------------------------------


async def update_branches() -> list:
    """Shufersal is a single nationwide online store — no branch list to refresh.

    Returns an empty list for API consistency with other scrapers.
    """
    logger.info("update_branches: Shufersal has no branch list (nationwide store)")
    return []


async def get_all_products(
    max_concurrent: int = 15,
) -> List[UnifiedProduct]:
    """Fetch all Shufersal products as a flat list (legacy wrapper)."""
    result = await scrape(max_concurrent=max_concurrent)
    return result["products_by_store"].get(_GLOBAL_STORE_ID, [])
