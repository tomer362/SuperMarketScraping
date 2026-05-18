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
