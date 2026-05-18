from __future__ import annotations

from generic_groups import classify_generic_offer


def _milk_offer(name: str) -> dict:
    return {
        "name": name,
        "unit_dimension": "volume",
        "unit_qty_si": 1000,
        "is_weighable": False,
    }


def test_enriched_milk_is_grouped_separately_from_regular_milk() -> None:
    regular = classify_generic_offer(_milk_offer("חלב תנובה 3% 1 ליטר"))
    enriched = classify_generic_offer(_milk_offer("חלב מועשר 3% בקבוק 1 ליטר"))

    assert regular is not None
    assert enriched is not None
    assert regular.key != enriched.key
    assert "enriched" not in regular.key
    assert "enriched" in enriched.key
    assert enriched.label == "חלב 3% 1 ליטר מועשר"


def _mass_offer(name: str, qty: float = 1000, *, is_weighable: bool = False) -> dict:
    return {
        "name": name,
        "unit_dimension": "mass",
        "unit_qty_si": qty,
        "is_weighable": is_weighable,
    }


def _group_key(name: str, qty: float = 1000) -> str:
    group = classify_generic_offer(_mass_offer(name, qty))
    assert group is not None
    return group.key


def test_dairy_fat_percent_splits_cottage_and_white_cheese() -> None:
    assert _group_key("קוטג תנובה 5% 250 גרם", 250) != _group_key("קוטג תנובה 9% 250 גרם", 250)
    assert "fat:5" in _group_key("גבינה לבנה 5% 250 גרם", 250)
    assert "fat:9" in _group_key("גבינה לבנה 9% 250 גרם", 250)


def test_tuna_in_oil_and_water_are_not_combined() -> None:
    oil = _group_key("טונה בשמן 160 גרם", 160)
    water = _group_key("טונה במים 160 גרם", 160)

    assert oil != water
    assert "oil" in oil
    assert "water" in water


def test_rice_flour_sugar_and_salt_variants_are_not_flattened() -> None:
    assert _group_key("אורז בסמטי 1 קג") != _group_key("אורז יסמין 1 קג")
    assert _group_key("קמח לבן 1 קג") != _group_key("קמח כוסמין 1 קג")
    assert _group_key("סוכר לבן 1 קג") != _group_key("סוכר חום 1 קג")
    assert _group_key("מלח דק 1 קג") != _group_key("מלח גס 1 קג")


def test_chicken_cuts_are_grouped_separately() -> None:
    breast = classify_generic_offer(_mass_offer("חזה עוף טרי", is_weighable=True))
    wings = classify_generic_offer(_mass_offer("כנפיים עוף טרי", is_weighable=True))

    assert breast is not None
    assert wings is not None
    assert breast.key != wings.key
    assert "cut:breast" in breast.key
    assert "cut:wing" in wings.key
