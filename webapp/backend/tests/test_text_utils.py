from __future__ import annotations

from text_utils import build_match_key


def test_build_match_key_prefers_barcode() -> None:
    key = build_match_key(
        {
            "barcode": "729-0000-066882",
            "brand": "תנובה",
            "name": "חלב 3%",
        }
    )
    assert key == "barcode:7290000066882"


def test_build_match_key_handles_apostrophe_variants() -> None:
    base = {
        "brand": "תנובה",
        "unit_dimension": "mass",
        "unit_qty_si": 250,
        "unit_of_measure": "גרם",
    }
    key_one = build_match_key({**base, "name": "קוטג׳ 5%"})
    key_two = build_match_key({**base, "name": "קוטג 5%"})
    assert key_one == key_two


def test_build_match_key_falls_back_to_manufacturer() -> None:
    key = build_match_key(
        {
            "manufacturer": "מחלבת הגולן",
            "name": "יוגורט יווני טבעי",
            "unit_dimension": "mass",
            "unit_qty_si": 200,
            "unit_of_measure": "גרם",
        }
    )
    assert key.startswith("text:מחלבת הגולן|")


def test_build_match_key_uses_coarse_quantity_buckets() -> None:
    product = {
        "brand": "טרה",
        "name": "חלב 3%",
        "unit_dimension": "volume",
        "unit_of_measure": 'מ"ל',
    }
    key_one = build_match_key({**product, "unit_qty_si": 1000})
    key_two = build_match_key({**product, "unit_qty_si": 1003})
    assert key_one == key_two
