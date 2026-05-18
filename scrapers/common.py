"""
scrapers/common.py
==================
Shared types, helpers, and retry logic used by all scrapers.

Exports
-------
- UnifiedProduct   — chain-agnostic product TypedDict (includes deal + unit-price fields)
- DealInfo         — structured promotion/deal description
- ScrapeResult     — wrapper returned by every scraper's ``scrape()`` function
- ScrapeFilter     — optional filter parameters accepted by every scraper
- with_retry       — async exponential-backoff retry decorator
- run_concurrently — bounded-concurrency async fan-out (TaskGroup on 3.11+)
- make_ssl_context — certifi-aware SSL context factory
- normalize_unit   — Hebrew/metric unit → (canonical_label, grams_or_ml_per_unit)
- compute_unit_price — compute price per 100 g/ml or per kg for weight products
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import ssl
import sys
from datetime import datetime, timezone
from typing import (
    Any,
    Callable,
    Coroutine,
    Dict,
    List,
    Optional,
    Tuple,
    TypedDict,
)

try:
    from typing import NotRequired
except ImportError:  # Python 3.10 compatibility
    from typing_extensions import NotRequired

logger = logging.getLogger("scrapers.common")

# ---------------------------------------------------------------------------
# Python version guard
# ---------------------------------------------------------------------------

_HAS_TASK_GROUP = sys.version_info >= (3, 11)


# ---------------------------------------------------------------------------
# Hebrew / metric unit normalisation
# ---------------------------------------------------------------------------

# Mapping from raw unit strings (as returned by various chain APIs) to a
# canonical label and the number of **grams** (for mass) or **millilitres**
# (for volume) that one unit represents.
#
# "base_unit" is the canonical comparison unit:
#   - mass products  → compare per 100 g  (base_qty = 100)
#   - volume products → compare per 100 ml (base_qty = 100)
#   - count products  → compare per 1 item (base_qty = 1)
#   - weight/kg products → compare per kg   (base_qty = 1000 g)
#
# The multiplier stored here is "how many grams-or-ml is ONE of this unit".

_UNIT_TABLE: Dict[str, Tuple[str, float, str]] = {
    # ── volume ──────────────────────────────────────────────────────────────
    # key              (canonical_label, ml_per_unit, dimension)
    'מ"ל': ('מ"ל', 1.0, "volume"),
    "מ'ל": ('מ"ל', 1.0, "volume"),
    'מ"ל': ('מ"ל', 1.0, "volume"),  # alternate quote
    "מל": ('מ"ל', 1.0, "volume"),
    "ml": ('מ"ל', 1.0, "volume"),
    "ML": ('מ"ל', 1.0, "volume"),
    "ליטר": ("ליטר", 1000.0, "volume"),
    "ל'": ("ליטר", 1000.0, "volume"),
    'ל"ל': ("ליטר", 1000.0, "volume"),
    "l": ("ליטר", 1000.0, "volume"),
    "L": ("ליטר", 1000.0, "volume"),
    "liter": ("ליטר", 1000.0, "volume"),
    "litre": ("ליטר", 1000.0, "volume"),
    # ── mass ────────────────────────────────────────────────────────────────
    "גרם": ("גרם", 1.0, "mass"),
    "גר": ("גרם", 1.0, "mass"),
    "ג'": ("גרם", 1.0, "mass"),
    "gr": ("גרם", 1.0, "mass"),
    "gram": ("גרם", 1.0, "mass"),
    "grams": ("גרם", 1.0, "mass"),
    "g": ("גרם", 1.0, "mass"),
    "G": ("גרם", 1.0, "mass"),
    'ק"ג': ('ק"ג', 1000.0, "mass"),
    "ק'ג": ('ק"ג', 1000.0, "mass"),
    "קג": ('ק"ג', 1000.0, "mass"),
    "kg": ('ק"ג', 1000.0, "mass"),
    "KG": ('ק"ג', 1000.0, "mass"),
    "kilogram": ('ק"ג', 1000.0, "mass"),
    # ── count ────────────────────────────────────────────────────────────────
    "יח'": ("יח'", 1.0, "count"),
    "יחידה": ("יח'", 1.0, "count"),
    "יחידות": ("יח'", 1.0, "count"),
    "unit": ("יח'", 1.0, "count"),
    "units": ("יח'", 1.0, "count"),
    "pcs": ("יח'", 1.0, "count"),
    "pc": ("יח'", 1.0, "count"),
}

# Regex to parse quantity+unit from description strings like
# "1 ל'", "500 מ\"ל", "1.5 ליטר", "700 גרם"
_QTY_UNIT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*"  # quantity (integer or decimal)
    r'(מ["\u05f4\u2019\']?ל|ליטר|ל["\u05f4\u2019\']?|ק["\u05f4\u2019\']?ג|'
    r'גר(?:ם)?|יח["\u05f4\u2019\']?|'
    r"ml|ML|[lLgG]|kg|KG)",
    re.UNICODE,
)


def normalize_unit(
    raw_unit: Optional[str],
    qty: Optional[float] = None,
    description: Optional[str] = None,
) -> Tuple[Optional[str], Optional[float], Optional[str], Optional[float]]:
    """Normalise a unit string and quantity to canonical form.

    Returns ``(canonical_label, qty_in_si, dimension, si_per_raw_unit)`` where:
      - ``canonical_label``  — e.g. ``'מ"ל'``, ``'גרם'``, ``'ק"ג'``, ``"יח'"``
      - ``qty_in_si``        — total quantity expressed in the base SI unit
                               (ml for volume, g for mass, items for count)
      - ``dimension``        — ``'volume'``, ``'mass'``, ``'count'``, or ``None``
      - ``si_per_raw_unit``  — how many SI units equal one raw unit
                               (e.g. 1000 for ליטר)
    """
    # Try to resolve the unit
    entry = None
    if raw_unit:
        key = raw_unit.strip()
        entry = _UNIT_TABLE.get(key)
        if entry is None:
            # Try case-insensitive match
            for k, v in _UNIT_TABLE.items():
                if k.lower() == key.lower():
                    entry = v
                    break

    # If we couldn't identify the unit, try to parse from description
    if entry is None and description:
        m = _QTY_UNIT_RE.search(description)
        if m:
            raw_num = m.group(1).replace(",", ".")
            qty_from_desc = float(raw_num)
            unit_from_desc = m.group(2)
            entry_from_desc = _UNIT_TABLE.get(unit_from_desc)
            if entry_from_desc is None:
                for k, v in _UNIT_TABLE.items():
                    if k == unit_from_desc or k.lower() == unit_from_desc.lower():
                        entry_from_desc = v
                        break
            if entry_from_desc:
                entry = entry_from_desc
                if qty is None:
                    qty = qty_from_desc

    if entry is None:
        return (raw_unit, qty, None, None)

    canonical_label, si_per_unit, dimension = entry
    qty_in_si = (qty * si_per_unit) if qty is not None else None
    return (canonical_label, qty_in_si, dimension, si_per_unit)


def compute_price_per_base_unit(
    price: float,
    qty_in_si: Optional[float],
    dimension: Optional[str],
    is_weighable: bool = False,
) -> Optional[float]:
    """Compute the comparable unit price for a product.

    - **mass / volume** packaged items → price per 100 g/ml
    - **weighable** items               → price per kg (already the input price)
    - **count** items                   → price per single unit (price / qty)

    Args:
        price:       Effective price for the product (after any deal).
        qty_in_si:   Total quantity in SI units (g, ml, or item-count).
        dimension:   ``'mass'``, ``'volume'``, ``'count'``, or ``None``.
        is_weighable: True for by-weight counters (price is per kg).

    Returns:
        Comparable unit price, or ``None`` when the unit is unknown.
    """
    if is_weighable:
        # price is already per kg for weighable items → just return it
        return round(price, 4)
    if dimension in ("mass", "volume") and qty_in_si and qty_in_si > 0:
        return round(price / qty_in_si * 100, 4)
    if dimension == "count" and qty_in_si and qty_in_si > 0:
        return round(price / qty_in_si, 4)
    return None


# ---------------------------------------------------------------------------
# Deal / Promotion types
# ---------------------------------------------------------------------------


class DealInfo(TypedDict, total=False):
    """Structured deal/promotion information for a product.

    ``deal_type`` values:
      - ``'price_reduction'``  — straightforward price drop (salePrice < regularPrice)
      - ``'multi_buy'``        — buy N items for a total price (e.g. "2 for ₪19.90")
      - ``'cart_total'``       — unlocked when cart exceeds a threshold
      - ``'coupon'``           — loyalty club / coupon deal
      - ``'other'``            — any other promotion

    ``price_per_base_unit_deal`` is the effective per-unit comparison price
    when the deal's quantity requirement is met (for ``multi_buy`` this is
    total_deal_price / quantity / qty_in_si * 100).
    """

    has_deal: bool  # True whenever any promotion is active
    deal_type: str  # see above
    deal_description: str  # human-readable Hebrew/English description
    # Simple price-reduction fields (also set for multi_buy)
    deal_price: Optional[float]  # total price under deal
    deal_min_qty: Optional[int]  # min items to qualify
    deal_price_per_unit: Optional[float]  # deal_price / deal_min_qty
    # Comparison price (per 100 g/ml or per kg)
    price_per_base_unit: Optional[float]  # regular — at single-unit price
    price_per_base_unit_deal: Optional[float]  # under the deal terms


# ---------------------------------------------------------------------------
# Unified product schema
# ---------------------------------------------------------------------------


class UnifiedProduct(TypedDict):
    """Chain-agnostic product record.

    Fields present for all chains are non-optional.  Chain-specific fields
    that cannot always be populated use ``Optional``.
    """

    chain: str  # "tivtaam" | "shufersal" | "yochananof" | "carrefour"
    store_id: str  # branch_id (int as str) or Yochananof store_code
    store_name: str
    product_id: str
    name: str
    price: float  # effective price (after any active promotion)
    regular_price: float  # shelf price before discounts
    sale_price: Optional[float]  # explicit sale/promotion price when active
    discount_percent: Optional[float]
    barcode: Optional[str]  # EAN-13 (or EAN-8) when available
    image_url: Optional[str]
    category_ids: List[str]
    is_weighable: bool
    unit_description: Optional[str]  # human-readable e.g. "500 מ\"ל"
    unit_of_measure: Optional[str]  # canonical label e.g. "גרם", "מ\"ל", "יח'"
    unit_qty: Optional[float]  # numeric quantity in raw unit (e.g. 500 for 500ml)
    unit_qty_si: Optional[
        float
    ]  # quantity in SI base (g or ml); e.g. 500 for 500ml, 1000 for 1L
    unit_dimension: Optional[str]  # 'mass', 'volume', 'count', or None
    price_per_base_unit: Optional[float]  # price per 100 g/ml (or per kg for weighable)
    deal: Optional[DealInfo]  # structured promotion info (None = no deal)
    brand: Optional[str]
    manufacturer: Optional[str]
    scraped_at: str  # ISO-8601 UTC timestamp


# ---------------------------------------------------------------------------
# ScrapeResult — wrapper returned by every chain's scrape() function
# ---------------------------------------------------------------------------


class ScrapeResult(TypedDict):
    chain: str
    stores_scraped: int
    products_total: int
    products_by_store: Dict[str, List[UnifiedProduct]]
    compare_row_details_by_product: NotRequired[Dict[str, Dict[str, Any]]]
    scraped_at: str  # ISO-8601 UTC session start
    duration_seconds: float
    errors: List[str]


# ---------------------------------------------------------------------------
# ScrapeFilter — optional filter parameters accepted by every scraper
# ---------------------------------------------------------------------------


class ScrapeFilter(TypedDict, total=False):
    """Filtering options passed to each scraper's ``scrape()`` entry point.

    All fields are optional.  Scrapers use native API search when available
    (Tiv Taam, Carrefour autocomplete; Shufersal q=; Yochananof GQL search),
    and fall back to post-fetch filtering where not.
    """

    name_query: str  # keyword/substring search
    category_ids: List[str]  # restrict to these category IDs
    barcode: str  # exact EAN barcode match


# ---------------------------------------------------------------------------
# SSL helper (shared across all scrapers)
# ---------------------------------------------------------------------------


def make_ssl_context() -> ssl.SSLContext:
    """Return an SSL context that trusts certifi's CA bundle when installed,
    falling back to the system default."""
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


# ---------------------------------------------------------------------------
# Exponential-backoff retry helper
# ---------------------------------------------------------------------------


async def with_retry(
    coro_fn: Callable[[], Coroutine[Any, Any, Any]],
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    attempt_timeout: float | None = 30.0,
    jitter_ratio: float = 0.2,
    label: str = "",
) -> Any:
    """Call ``coro_fn()`` up to ``max_retries`` times with exponential backoff.

    On each failure the coroutine is re-created by calling ``coro_fn()`` again
    (i.e. ``coro_fn`` must be a zero-argument factory, not an already-awaited
    coroutine).

    Delay schedule: ``min(base_delay * 2**attempt, max_delay)``
      attempt 0 → no delay (first try)
      attempt 1 → base_delay   (e.g. 1 s)
      attempt 2 → base_delay*2 (e.g. 2 s)
      attempt 3 → base_delay*4 (e.g. 4 s)
      …

    If ``attempt_timeout`` is set, each try is bounded with ``asyncio.wait_for``.
    A small jitter is added to sleep duration to avoid synchronized retries.

    Raises the last exception when all retries are exhausted.
    """
    last_exc: Exception = RuntimeError("No attempts made")
    for attempt in range(max_retries):
        try:
            if attempt_timeout is not None and attempt_timeout > 0:
                return await asyncio.wait_for(coro_fn(), timeout=attempt_timeout)
            return await coro_fn()
        except Exception as exc:
            last_exc = exc
            if attempt == max_retries - 1:
                raise
            delay = min(base_delay * (2**attempt), max_delay)
            if jitter_ratio > 0:
                jitter = delay * jitter_ratio
                delay = max(0.0, delay + random.uniform(-jitter, jitter))
            tag = f" [{label}]" if label else ""
            logger.warning(
                "Attempt %d/%d failed%s: %s — retrying in %.1fs",
                attempt + 1,
                max_retries,
                tag,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
    raise last_exc  # unreachable, but keeps type checkers happy


# ---------------------------------------------------------------------------
# TaskGroup / gather compatibility shim
# ---------------------------------------------------------------------------


async def run_concurrently(
    tasks: List[Callable[[], Coroutine[Any, Any, Any]]],
    max_concurrent: int = 15,
) -> List[Any]:
    """Run ``tasks`` with bounded concurrency, returning results in order.

    Uses ``asyncio.TaskGroup`` on Python 3.11+ or ``asyncio.gather`` otherwise.
    Failed tasks return the exception object rather than raising (matches the
    ``return_exceptions=True`` behaviour).

    Args:
        tasks: List of zero-argument coroutine factories.
        max_concurrent: Maximum number of simultaneously running coroutines.

    Returns:
        List of results (or Exception objects) in the same order as ``tasks``.
    """
    sem = asyncio.Semaphore(max_concurrent)
    results: List[Any] = [None] * len(tasks)

    if _HAS_TASK_GROUP:

        async def _run(idx: int, fn: Callable[[], Coroutine[Any, Any, Any]]) -> None:
            async with sem:
                try:
                    results[idx] = await fn()
                except Exception as exc:
                    results[idx] = exc

        async with asyncio.TaskGroup() as tg:
            for i, fn in enumerate(tasks):
                tg.create_task(_run(i, fn))
    else:

        async def _run_sem(fn: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
            async with sem:
                return await fn()

        gathered = await asyncio.gather(
            *[_run_sem(fn) for fn in tasks], return_exceptions=True
        )
        for i, val in enumerate(gathered):
            results[i] = val

    return results


# ---------------------------------------------------------------------------
# Timestamp helper
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()
