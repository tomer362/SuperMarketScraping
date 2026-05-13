from __future__ import annotations

from typing import Any

from scrapers.deal_validation import validate_product_deal_contract


def _product(chain: str, deal: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "chain": chain,
        "store_id": "store-1",
        "store_name": "Store",
        "product_id": f"{chain}-1",
        "name": "חלב 1 ליטר",
        "price": 7.9,
        "regular_price": 9.9,
        "sale_price": 7.9 if deal else None,
        "discount_percent": 20.2 if deal else None,
        "barcode": "7290000000000",
        "image_url": None,
        "category_ids": ["1"],
        "is_weighable": False,
        "unit_description": "1 ליטר",
        "unit_of_measure": "ליטר",
        "unit_qty": 1.0,
        "unit_qty_si": 1000.0,
        "unit_dimension": "volume",
        "price_per_base_unit": 0.79,
        "deal": deal,
        "brand": "brand",
        "manufacturer": None,
        "scraped_at": "2026-05-13T00:00:00+00:00",
    }


def _price_reduction() -> dict[str, Any]:
    return {
        "has_deal": True,
        "deal_type": "price_reduction",
        "deal_description": "מחיר מבצע",
        "deal_price": 7.9,
        "deal_min_qty": 1,
        "deal_price_per_unit": 7.9,
        "price_per_base_unit": 0.99,
        "price_per_base_unit_deal": 0.79,
    }


def test_all_supermarket_chains_accept_valid_price_reduction_contract() -> None:
    chains = [
        "shufersal",
        "tivtaam",
        "carrefour",
        "machsanei",
        "ramilevi",
        "keshet",
        "quik",
        "victory",
        "ybitan",
        "yochananof",
    ]

    for chain in chains:
        assert validate_product_deal_contract(_product(chain, _price_reduction())) == []


def test_discounted_product_requires_structured_deal() -> None:
    product = _product("ramilevi", None)
    product["sale_price"] = 7.9
    product["discount_percent"] = 20.2

    errors = validate_product_deal_contract(product)

    assert any("deal is None" in error for error in errors)


def test_multi_buy_contract_checks_per_unit_math() -> None:
    product = _product(
        "shufersal",
        {
            "has_deal": True,
            "deal_type": "multi_buy",
            "deal_description": "2 יח' ב- 12 ₪",
            "deal_price": 12.0,
            "deal_min_qty": 2,
            "deal_price_per_unit": 6.0,
            "price_per_base_unit": 0.99,
            "price_per_base_unit_deal": 0.6,
        },
    )
    product["sale_price"] = None
    product["discount_percent"] = None

    assert validate_product_deal_contract(product) == []


def test_multi_buy_contract_rejects_bad_per_unit_math() -> None:
    product = _product(
        "shufersal",
        {
            "has_deal": True,
            "deal_type": "multi_buy",
            "deal_description": "2 יח' ב- 12 ₪",
            "deal_price": 12.0,
            "deal_min_qty": 2,
            "deal_price_per_unit": 7.0,
            "price_per_base_unit": 0.99,
            "price_per_base_unit_deal": 0.7,
        },
    )
    product["sale_price"] = None
    product["discount_percent"] = None

    errors = validate_product_deal_contract(product)

    assert any("price_per_unit" in error for error in errors)


def test_supermarket_deal_parsers_emit_valid_contracts() -> None:
    from scrapers.keshet import keshet
    from scrapers.machsanei_hashook import machsanei_hashook
    from scrapers.quik import quik
    from scrapers.ramilevi import ramilevi
    from scrapers.shufersal import shufersal
    from scrapers.tivtaam import tivtaam
    from scrapers.victory import victory
    from scrapers.ybitan import ybitan

    multi_special = {
        "specials": [
            {
                "firstLevel": {
                    "type": 2,
                    "firstPurchaseTotal": 2,
                    "firstGift": {"total": 12.0},
                },
                "names": {"1": {"name": "2 יחידות ב-12"}},
            }
        ]
    }
    cases = [
        (
            "shufersal",
            shufersal._parse_deal(
                {"promotionMsg": "2 יח' ב- 12 ₪"}, 9.9, None, 1000.0, "volume", False
            ),
        ),
        (
            "tivtaam",
            tivtaam._extract_deal(multi_special, 9.9, None, 1000.0, "volume", False),
        ),
        (
            "machsanei",
            machsanei_hashook._extract_deal(multi_special, 9.9, None, 1000.0, "volume", False),
        ),
        (
            "ramilevi",
            ramilevi._extract_deal(
                {"sale": [{"scm": 7.9, "name": "מחיר מבצע"}]},
                9.9,
                1000.0,
                "volume",
                False,
            ),
        ),
        (
            "keshet",
            keshet._extract_deal(multi_special, 9.9, None, 1000.0, "volume", False),
        ),
        (
            "quik",
            quik._extract_deal(multi_special, 9.9, None, 1000.0, "volume", False),
        ),
        (
            "victory",
            victory._extract_deal(multi_special, 9.9, None, 1000.0, "volume", False),
        ),
        (
            "ybitan",
            ybitan._extract_deal(multi_special, 9.9, None, 1000.0, "volume", False),
        ),
    ]

    for chain, deal in cases:
        assert deal is not None
        product = _product(chain, deal)
        if deal["deal_type"] == "multi_buy":
            product["sale_price"] = None
            product["discount_percent"] = None
        assert validate_product_deal_contract(product) == []


def test_inline_deal_mappers_emit_valid_contracts() -> None:
    from scrapers.carrefour import carrefour
    from scrapers.yochananof import yochananof

    carrefour_product = carrefour._to_unified(
        {
            "productId": "carrefour-1",
            "names": {"1": {"long": "חלב 1 ליטר"}},
            "branch": {"regularPrice": 9.9, "salePrice": 7.9},
            "weight": 1,
            "unitOfMeasure": {"names": {"1": "ליטר"}},
            "family": {"categories": [{"id": 1}]},
        },
        {"id": 1, "name": "Online", "city": "", "location": ""},
        "1",
        "2026-05-13T00:00:00+00:00",
    )
    yochananof_product = yochananof._to_unified(
        {
            "id": "yo-1",
            "sku": "7290000000000",
            "name": "חלב 1 ליטר",
            "price_range": {
                "minimum_price": {
                    "regular_price": {"value": 9.9},
                    "final_price": {"value": 7.9},
                    "discount": {"percent_off": 20.2},
                }
            },
            "item_unit": "ליטר",
            "categories": [{"id": 1}],
        },
        {"store_code": "1", "store_name": "Online"},
        "2026-05-13T00:00:00+00:00",
    )

    assert carrefour_product is not None
    assert yochananof_product is not None
    assert validate_product_deal_contract(carrefour_product) == []
    assert validate_product_deal_contract(yochananof_product) == []
