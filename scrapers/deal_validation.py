from __future__ import annotations

from math import isfinite
from typing import Any, Iterable

from scrapers.common import compute_price_per_base_unit


ALLOWED_DEAL_TYPES = {"price_reduction", "multi_buy", "cart_total", "coupon", "other"}


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _close(left: float | None, right: float | None, tolerance: float = 0.01) -> bool:
    if left is None or right is None:
        return left is right
    return abs(left - right) <= tolerance


def _path(chain: str, store_id: Any, product_id: Any) -> str:
    return f"{chain or 'unknown'} store={store_id or '?'} product={product_id or '?'}"


def validate_product_deal_contract(product: dict[str, Any]) -> list[str]:
    """Validate the cross-scraper promotion contract for a UnifiedProduct.

    This is intentionally data-shape focused and hermetic: it checks the unified
    product emitted by a scraper without making network calls.
    """
    errors: list[str] = []
    chain = str(product.get("chain") or "")
    location = _path(chain, product.get("store_id"), product.get("product_id"))

    if "deal" not in product:
        return [f"{location}: missing deal field"]

    regular_price = _as_float(product.get("regular_price"))
    sale_price = _as_float(product.get("sale_price"))
    discount_percent = _as_float(product.get("discount_percent"))
    deal = product.get("deal")

    if deal is None:
        if sale_price is not None and regular_price is not None and sale_price < regular_price:
            errors.append(f"{location}: sale_price is discounted but deal is None")
        if discount_percent is not None and discount_percent > 0:
            errors.append(f"{location}: discount_percent is set but deal is None")
        return errors

    if not isinstance(deal, dict):
        return [f"{location}: deal must be a mapping or None"]

    if deal.get("has_deal") is not True:
        errors.append(f"{location}: deal.has_deal must be true when deal exists")

    deal_type = deal.get("deal_type")
    if deal_type not in ALLOWED_DEAL_TYPES:
        errors.append(f"{location}: unsupported deal_type={deal_type!r}")

    description = str(deal.get("deal_description") or "").strip()
    if not description:
        errors.append(f"{location}: deal_description is required when deal exists")

    deal_price = _as_float(deal.get("deal_price"))
    min_qty = _as_int(deal.get("deal_min_qty"))
    price_per_unit = _as_float(deal.get("deal_price_per_unit"))

    if deal_type == "price_reduction":
        if min_qty != 1:
            errors.append(f"{location}: price_reduction deal_min_qty must be 1")
        if deal_price is None or deal_price <= 0:
            errors.append(f"{location}: price_reduction deal_price must be positive")
        if deal_price is not None and not _close(price_per_unit, deal_price):
            errors.append(f"{location}: price_reduction price_per_unit must equal deal_price")

    if deal_type == "multi_buy":
        if min_qty is None or min_qty < 2:
            errors.append(f"{location}: multi_buy deal_min_qty must be at least 2")
        if deal_price is None or deal_price <= 0:
            errors.append(f"{location}: multi_buy deal_price must be positive")
        if deal_price is not None and min_qty and min_qty > 0:
            expected_ppu = round(deal_price / min_qty, 4)
            if not _close(price_per_unit, expected_ppu):
                errors.append(f"{location}: multi_buy price_per_unit must equal deal_price/deal_min_qty")

    if deal_type in {"cart_total", "coupon", "other"}:
        if deal_price is not None and min_qty and min_qty > 0:
            expected_ppu = round(deal_price / min_qty, 4)
            if not _close(price_per_unit, expected_ppu):
                errors.append(f"{location}: deal_price_per_unit must equal deal_price/deal_min_qty")

    if price_per_unit is not None:
        qty_si = _as_float(product.get("unit_qty_si"))
        expected_ppbu = compute_price_per_base_unit(
            price_per_unit,
            qty_si,
            product.get("unit_dimension"),
            bool(product.get("is_weighable", False)),
        )
        actual_ppbu = _as_float(deal.get("price_per_base_unit_deal"))
        if expected_ppbu is not None and not _close(actual_ppbu, expected_ppbu):
            errors.append(f"{location}: price_per_base_unit_deal does not match deal_price_per_unit")

    regular_ppbu = _as_float(deal.get("price_per_base_unit"))
    expected_regular_ppbu = compute_price_per_base_unit(
        regular_price or 0,
        _as_float(product.get("unit_qty_si")),
        product.get("unit_dimension"),
        bool(product.get("is_weighable", False)),
    )
    if regular_price is not None and expected_regular_ppbu is not None and not _close(regular_ppbu, expected_regular_ppbu):
        errors.append(f"{location}: deal.price_per_base_unit does not match regular_price")

    if sale_price is not None and regular_price is not None and sale_price < regular_price:
        if price_per_unit is not None and not _close(sale_price, price_per_unit):
            errors.append(f"{location}: sale_price does not match deal_price_per_unit")

    return errors


def validate_products_deal_contract(
    products: Iterable[dict[str, Any]],
    *,
    max_errors: int | None = None,
) -> list[str]:
    errors: list[str] = []
    for product in products:
        errors.extend(validate_product_deal_contract(product))
        if max_errors is not None and len(errors) >= max_errors:
            return errors[:max_errors]
    return errors


def validate_scrape_result_deal_contract(
    result: dict[str, Any],
    *,
    max_errors: int | None = None,
) -> list[str]:
    products: list[dict[str, Any]] = []
    for store_products in (result.get("products_by_store") or {}).values():
        products.extend(store_products or [])
    return validate_products_deal_contract(products, max_errors=max_errors)
