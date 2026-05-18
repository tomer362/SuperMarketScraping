"""
Yochananof Online scraper
=========================
Platform: Magento 2 + GraphQL
Endpoint: https://api.yochananof.co.il/graphql

Key endpoints
-------------
1. Available stores (branches):
     GET /graphql?query=query AvailableStores { availableStores { ... } }

2. Categories (menu tree):
     GET /graphql?query=query Categories { amMegaMenuAll { items { ... } } }

3. Products per category / search (paginated):
     POST /graphql
     Body: { "query": "...", "variables": { "search": "...", "filter": {...},
                                             "pageSize": 100, "currentPage": 1 } }
     Header: Store: <store_code>

Branch-level filtering via ``Store`` HTTP header (e.g. ``Store: s82``).

Unified API
-----------
Call ``scrape()`` to get a ``ScrapeResult`` TypedDict compatible with all
other chain scrapers.  Pass a ``ScrapeFilter`` to restrict by name, category,
or barcode.  ``name_query`` uses the native GraphQL ``search`` argument.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional, Set, TypedDict
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
from utils import get_module_logger

logger = get_module_logger("yochananof")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHAIN = "yochananof"
GRAPHQL_URL = "https://api.yochananof.co.il/graphql"
PAGE_SIZE = 100  # server supports up to 100

# ---------------------------------------------------------------------------
# Internal TypedDicts (not part of public unified API)
# ---------------------------------------------------------------------------


class Store(TypedDict):
    store_code: str
    store_name: str
    is_default_store: bool


class Category(TypedDict):
    id: str
    name: str


# ---------------------------------------------------------------------------
# GraphQL query strings
# ---------------------------------------------------------------------------

_STORES_QUERY = """
query AvailableStores {
  availableStores {
    store_code
    store_name
    is_default_store
    locale
    base_url
  }
}
"""

_CATEGORIES_QUERY = """
query Categories {
  amMegaMenuAll {
    items {
      id name is_category level status
      children {
        id name is_category level status
        children {
          id name is_category level status
          children {
            id name is_category level status
          }
        }
      }
    }
  }
}
"""

# Supports both category filter and keyword search
_PRODUCTS_QUERY = """
query Products($search: String, $filter: ProductAttributeFilterInput, $pageSize: Int!, $currentPage: Int!) {
  products(search: $search, filter: $filter, pageSize: $pageSize, currentPage: $currentPage) {
    total_count
    page_info { page_size current_page total_pages }
    items {
      id sku name short_name brand url_key
      stock_status by_kilo item_unit
      price_range {
        minimum_price {
          regular_price { value currency }
          final_price { value currency }
          discount { amount_off percent_off }
        }
      }
      price_tiers {
        quantity
        final_price { value currency }
        discount { amount_off percent_off }
      }
      small_image { url label }
      categories { id name }
    }
  }
}
"""

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _base_headers(store_code: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if store_code:
        headers["Store"] = store_code
    return headers


async def _gql_get(
    session: aiohttp.ClientSession,
    query: str,
    store_code: Optional[str] = None,
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> Dict:
    url = f"{GRAPHQL_URL}?query={quote(query)}"

    async def _do() -> Dict:
        async with session.get(url, headers=_base_headers(store_code)) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    return await with_retry(
        _do,
        max_retries=max_retries,
        base_delay=base_delay,
        attempt_timeout=25.0,
        label=f"gql_get store={store_code}",
    )


async def _gql_post(
    session: aiohttp.ClientSession,
    query: str,
    variables: Dict,
    store_code: Optional[str] = None,
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    label: str = "",
) -> Dict:
    payload = json.dumps({"query": query, "variables": variables}).encode()

    async def _do() -> Dict:
        async with session.post(
            GRAPHQL_URL, data=payload, headers=_base_headers(store_code)
        ) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    return await with_retry(
        _do,
        max_retries=max_retries,
        base_delay=base_delay,
        attempt_timeout=25.0,
        label=label,
    )


# ---------------------------------------------------------------------------
# Stores (branches)
# ---------------------------------------------------------------------------


async def fetch_stores(
    session: aiohttp.ClientSession,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> List[Store]:
    data = await _gql_get(
        session, _STORES_QUERY, max_retries=max_retries, base_delay=base_delay
    )
    raw = data.get("data", {}).get("availableStores", [])
    stores: List[Store] = [
        Store(
            store_code=s["store_code"],
            store_name=s["store_name"],
            is_default_store=bool(s.get("is_default_store")),
        )
        for s in raw
    ]
    logger.info("Fetched %d Yochananof stores", len(stores))
    return stores


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


def _extract_category_ids(
    items: List[Dict], result: Optional[List[str]] = None
) -> List[str]:
    if result is None:
        result = []
    for item in items:
        item_id: str = item.get("id", "")
        is_cat: bool = bool(item.get("is_category"))
        status: int = item.get("status", 0)
        children: List[Dict] = [
            c
            for c in (item.get("children") or [])
            if c.get("is_category") and c.get("id", "").startswith("category-node-")
        ]
        if not is_cat or not item_id.startswith("category-node-"):
            _extract_category_ids(item.get("children") or [], result)
            continue
        if status == 0:
            _extract_category_ids(item.get("children") or [], result)
            continue
        numeric_id = item_id.replace("category-node-", "")
        if not children:
            result.append(numeric_id)
        else:
            _extract_category_ids(item.get("children") or [], result)
    return result


async def fetch_categories(
    session: aiohttp.ClientSession,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> List[str]:
    data = await _gql_get(
        session, _CATEGORIES_QUERY, max_retries=max_retries, base_delay=base_delay
    )
    items = data.get("data", {}).get("amMegaMenuAll", {}).get("items", [])
    cat_ids = _extract_category_ids(items)
    seen: Set[str] = set()
    unique: List[str] = []
    for cid in cat_ids:
        if cid not in seen:
            seen.add(cid)
            unique.append(cid)
    logger.info("Discovered %d leaf categories", len(unique))
    return unique


# ---------------------------------------------------------------------------
# Product mapping → UnifiedProduct
# ---------------------------------------------------------------------------


def _to_unified(
    item: Dict[str, Any], store: Store, scraped_at: str
) -> Optional[UnifiedProduct]:
    try:
        name = str(item.get("name") or "").strip()
        if not name:
            return None

        price_range = item.get("price_range", {}).get("minimum_price", {})
        regular_price = (price_range.get("regular_price") or {}).get("value")
        final_price = (price_range.get("final_price") or {}).get("value")
        discount_info = price_range.get("discount") or {}
        discount_pct_raw = discount_info.get("percent_off", 0)

        if final_price is None and regular_price is None:
            return None

        regular_price_f = float(regular_price or final_price or 0)
        price = float(final_price or regular_price or 0)
        if price <= 0:
            return None

        sale_price: Optional[float] = (
            float(final_price)
            if final_price is not None and float(final_price) < regular_price_f
            else None
        )
        effective_price = sale_price if sale_price is not None else regular_price_f
        discount_pct: Optional[float] = (
            float(discount_pct_raw) if discount_pct_raw else None
        )

        image_url: Optional[str] = (item.get("small_image") or {}).get("url") or None
        cat_ids = [str(c["id"]) for c in (item.get("categories") or []) if c.get("id")]
        sku = str(item.get("sku") or "")
        is_weighable = bool(item.get("by_kilo"))

        # Unit normalisation — item_unit field (e.g. "ק\"ג", "מ\"ל", "גרם")
        raw_uom: Optional[str] = item.get("item_unit") or None
        canonical_uom, qty_si, dimension, _si_per = normalize_unit(raw_uom, None)

        ppbu = compute_price_per_base_unit(
            effective_price, qty_si, dimension, is_weighable
        )

        # Deal extraction — price_tiers gives tiered pricing (multi-buy)
        deal: Optional[DealInfo] = None
        price_tiers = item.get("price_tiers") or []
        if sale_price is not None:
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
        elif price_tiers:
            # Use the best (lowest per-unit) tier
            best_tier = min(
                price_tiers,
                key=lambda t: (t.get("final_price") or {}).get("value", 999999),
            )
            tier_qty = int(best_tier.get("quantity", 1))
            tier_price_per_unit = (best_tier.get("final_price") or {}).get("value")
            if tier_price_per_unit:
                tier_total = float(tier_price_per_unit) * tier_qty
                ppbu_reg = compute_price_per_base_unit(
                    regular_price_f, qty_si, dimension, is_weighable
                )
                ppbu_deal = compute_price_per_base_unit(
                    float(tier_price_per_unit), qty_si, dimension, is_weighable
                )
                deal = DealInfo(
                    has_deal=True,
                    deal_type="multi_buy",
                    deal_description=f"קנו {tier_qty} יח' במחיר ₪{tier_price_per_unit:.2f} ליחידה",
                    deal_price=tier_total,
                    deal_min_qty=tier_qty,
                    deal_price_per_unit=float(tier_price_per_unit),
                    price_per_base_unit=ppbu_reg,
                    price_per_base_unit_deal=ppbu_deal,
                )

        return UnifiedProduct(
            chain=CHAIN,
            store_id=store["store_code"],
            store_name=store["store_name"],
            product_id=str(item.get("id") or sku),
            name=name,
            price=effective_price,
            regular_price=regular_price_f,
            sale_price=sale_price,
            discount_percent=discount_pct,
            barcode=sku if sku else None,
            image_url=image_url,
            category_ids=cat_ids,
            is_weighable=is_weighable,
            unit_description=None,
            unit_of_measure=canonical_uom,
            unit_qty=None,
            unit_qty_si=qty_si,
            unit_dimension=dimension,
            price_per_base_unit=ppbu,
            deal=deal,
            brand=item.get("brand") or None,
            manufacturer=None,
            scraped_at=scraped_at,
        )
    except Exception as exc:
        logger.warning("Failed to extract product id=%s: %s", item.get("id"), exc)
        return None


# ---------------------------------------------------------------------------
# Per-category / search scraper
# ---------------------------------------------------------------------------


async def _fetch_products_page(
    session: aiohttp.ClientSession,
    store_code: str,
    category_id: Optional[str],
    page: int,
    name_query: Optional[str],
    max_retries: int,
    base_delay: float,
) -> Dict:
    variables: Dict[str, Any] = {"pageSize": PAGE_SIZE, "currentPage": page}
    if category_id:
        variables["filter"] = {"category_id": {"eq": category_id}}
    if name_query:
        variables["search"] = name_query
    label = f"store={store_code} cat={category_id} page={page} q='{name_query}'"
    try:
        return await _gql_post(
            session,
            _PRODUCTS_QUERY,
            variables,
            store_code,
            max_retries=max_retries,
            base_delay=base_delay,
            label=label,
        )
    except Exception as exc:
        logger.error("GQL error %s: %s", label, exc)
        return {}


async def _scrape_category(
    session: aiohttp.ClientSession,
    store: Store,
    category_id: Optional[str],
    name_query: Optional[str],
    max_retries: int,
    base_delay: float,
    max_concurrent: int,
    scraped_at: str,
) -> List[UnifiedProduct]:
    store_code = store["store_code"]
    # Fetch page 1 to discover total_pages
    data = await _fetch_products_page(
        session, store_code, category_id, 1, name_query, max_retries, base_delay
    )
    gql_products = data.get("data", {}).get("products", {})
    if not gql_products:
        errors = data.get("errors")
        if errors:
            logger.error(
                "store=%s category=%s GQL errors: %s", store_code, category_id, errors
            )
        return []

    total_count: int = gql_products.get("total_count", 0)
    page_info = gql_products.get("page_info", {})
    total_pages: int = page_info.get("total_pages", 1)

    logger.debug(
        "store=%s category=%s search='%s': %d products, %d pages",
        store_code,
        category_id,
        name_query or "",
        total_count,
        total_pages,
    )

    products: List[UnifiedProduct] = []
    for item in gql_products.get("items") or []:
        p = _to_unified(item, store, scraped_at)
        if p:
            products.append(p)

    if total_pages > 1:
        task_fns = [
            (
                lambda pg=page: _fetch_products_page(
                    session,
                    store_code,
                    category_id,
                    pg,
                    name_query,
                    max_retries,
                    base_delay,
                )
            )
            for page in range(2, total_pages + 1)
        ]
        results = await run_concurrently(task_fns, max_concurrent=max_concurrent)
        for r in results:
            if isinstance(r, Exception):
                logger.error(
                    "Page fetch error store=%s cat=%s: %s", store_code, category_id, r
                )
                continue
            for item in r.get("data", {}).get("products", {}).get("items") or []:
                p = _to_unified(item, store, scraped_at)
                if p:
                    products.append(p)

    return products


# ---------------------------------------------------------------------------
# Per-store scraper
# ---------------------------------------------------------------------------


async def _scrape_store(
    session: aiohttp.ClientSession,
    store: Store,
    category_ids: List[str],
    flt: ScrapeFilter,
    max_concurrent: int,
    max_retries: int,
    base_delay: float,
    scraped_at: str,
) -> List[UnifiedProduct]:
    store_code = store["store_code"]
    name_query = flt.get("name_query") or None
    filter_cats = flt.get("category_ids")
    filter_barcode = flt.get("barcode")

    all_products: List[UnifiedProduct] = []
    seen_skus: Set[str] = set()

    if name_query:
        # Single global search, no category loop needed
        prods = await _scrape_category(
            session,
            store,
            None,
            name_query,
            max_retries,
            base_delay,
            max_concurrent,
            scraped_at,
        )
        all_products.extend(prods)
    else:
        cats = filter_cats if filter_cats else category_ids
        task_fns = [
            (
                lambda cat=cat: _scrape_category(
                    session,
                    store,
                    cat,
                    None,
                    max_retries,
                    base_delay,
                    max_concurrent,
                    scraped_at,
                )
            )
            for cat in cats
        ]
        results = await run_concurrently(task_fns, max_concurrent=max_concurrent)
        for r in results:
            if isinstance(r, Exception):
                logger.error("Category error store=%s: %s", store_code, r)
            elif r:
                all_products.extend(r)

    # De-duplicate by SKU
    unique: List[UnifiedProduct] = []
    for p in all_products:
        key = p.get("barcode") or p["product_id"]
        if key not in seen_skus:
            seen_skus.add(key)
            unique.append(p)

    # Post-filters
    if filter_barcode:
        unique = [p for p in unique if p.get("barcode") == filter_barcode]
    if name_query and filter_cats:
        unique = [
            p
            for p in unique
            if any(c in filter_cats for c in p.get("category_ids", []))
        ]

    logger.info(
        "store=%s (%s): %d unique products",
        store_code,
        store["store_name"],
        len(unique),
    )
    return unique


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def scrape(
    stores: Optional[List[Store]] = None,
    flt: Optional[ScrapeFilter] = None,
    batch_size: int = 100,  # PAGE_SIZE; kept for API parity
    max_concurrent: int = 6,
    store_concurrent: int = 2,
    max_retries: int = 3,
    base_retry_delay: float = 1.0,
) -> ScrapeResult:
    """Scrape Yochananof and return a unified ScrapeResult.

    Args:
        stores:           Stores to scrape (default: all available stores).
        flt:              Optional filters (name_query, category_ids, barcode).
        batch_size:       Products per page (server max ~100); kept for API parity.
        max_concurrent:   Max concurrent category requests per store.
        max_retries:      Max retry attempts per request.
        base_retry_delay: Base delay (seconds) for exponential backoff.

    Returns:
        ScrapeResult with products_by_store keyed by store_code.
    """
    if flt is None:
        flt = {}

    scraped_at = utc_now_iso()
    t0 = time.monotonic()
    errors: List[str] = []

    connector = aiohttp.TCPConnector(ssl=make_ssl_context())
    async with aiohttp.ClientSession(connector=connector) as session:
        if stores is None:
            stores = await fetch_stores(
                session, max_retries=max_retries, base_delay=base_retry_delay
            )

        category_ids = flt.get("category_ids") or await fetch_categories(
            session, max_retries=max_retries, base_delay=base_retry_delay
        )

        async def _scrape_one_store(store: Store) -> tuple[str, List[UnifiedProduct], Optional[str]]:
            try:
                prods = await _scrape_store(
                    session,
                    store,
                    category_ids,
                    flt,
                    max_concurrent,
                    max_retries,
                    base_retry_delay,
                    scraped_at,
                )
                return store["store_code"], prods, None
            except Exception as exc:
                msg = f"store={store['store_code']} failed: {exc}"
                logger.error(msg)
                return store["store_code"], [], msg

        products_by_store: Dict[str, List[UnifiedProduct]] = {}
        task_fns = [lambda store=store: _scrape_one_store(store) for store in stores]
        results = await run_concurrently(task_fns, max_concurrent=store_concurrent)
        for result in results:
            if isinstance(result, Exception):
                msg = f"store task failed: {result}"
                logger.error(msg)
                errors.append(msg)
                continue
            store_code, products, error = result
            if error:
                errors.append(error)
            products_by_store[store_code] = products

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


# ---------------------------------------------------------------------------
# update_branches — hit the live API to refresh the hardcoded store list
# ---------------------------------------------------------------------------


async def update_branches() -> List[Store]:
    """Fetch the live Yochananof store list from the GraphQL API.

    Returns a list of :class:`Store` objects that can be used to refresh
    the available stores.
    """
    connector = aiohttp.TCPConnector(ssl=make_ssl_context())
    async with aiohttp.ClientSession(connector=connector) as session:
        stores = await fetch_stores(session)
    logger.info("update_branches: found %d Yochananof stores", len(stores))
    return stores


# ---------------------------------------------------------------------------
# Legacy convenience wrappers (backward-compat)
# ---------------------------------------------------------------------------


async def scrape_store(
    session: aiohttp.ClientSession,
    store: Store,
    category_ids: List[str],
) -> List[UnifiedProduct]:
    """Legacy per-store wrapper used by main.py."""
    return await _scrape_store(
        session,
        store,
        category_ids,
        {},
        max_concurrent=15,
        max_retries=3,
        base_delay=1.0,
        scraped_at=utc_now_iso(),
    )


async def scrape_all_stores(
    stores: Optional[List[Store]] = None,
    category_ids: Optional[List[str]] = None,
) -> Dict[str, List[UnifiedProduct]]:
    """Legacy wrapper — returns store_code -> list of UnifiedProduct."""
    result = await scrape(stores=stores)
    return result["products_by_store"]
