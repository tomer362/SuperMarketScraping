from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from text_utils import normalize_text


@dataclass(frozen=True)
class GenericGroup:
    key: str
    family: str
    label: str


_FAT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_COUNT_RE = re.compile(r"(?:^|\s)(\d{1,2})\s*(?:יח|יחידות|ביצים)?(?:\s|$)")
_EGG_SIZE_RE = re.compile(r"(?:^|\s)(xl|l|m|s)(?:\s|$)", re.IGNORECASE)


def _qty_bucket(value: Any) -> str | None:
    try:
        qty = float(value or 0)
    except (TypeError, ValueError):
        return None
    if qty <= 0:
        return None
    if qty == 100:
        return None
    if qty < 100:
        rounded = round(qty)
    elif qty < 1500:
        rounded = round(qty / 10) * 10
    else:
        rounded = round(qty / 50) * 50
    return str(int(rounded))


def _fat(text: str) -> str | None:
    match = _FAT_RE.search(text)
    return match.group(1) if match else None


def _size_label(unit_dimension: str | None, qty: str | None) -> str | None:
    if not qty:
        return None
    value = int(qty)
    if unit_dimension == "volume":
        if value % 1000 == 0:
            liters = value // 1000
            return f"{liters} ליטר"
        return f"{value} מ״ל"
    if unit_dimension == "mass":
        if value % 1000 == 0:
            kg = value // 1000
            return f"{kg} ק״ג"
        return f"{value} גרם"
    if unit_dimension == "count":
        return f"{value} יחידות"
    return None


def _contains_any(text: str, tokens: set[str]) -> bool:
    words = set(text.split())
    return any(token in words or token in text for token in tokens)


def classify_generic_offer(offer: Any) -> GenericGroup | None:
    raw_name = getattr(offer, "name", None) if not isinstance(offer, dict) else offer.get("name")
    name = normalize_text(raw_name)
    unit_dimension = getattr(offer, "unit_dimension", None) if not isinstance(offer, dict) else offer.get("unit_dimension")
    qty = _qty_bucket(getattr(offer, "unit_qty_si", None) if not isinstance(offer, dict) else offer.get("unit_qty_si"))
    is_weighable = bool(getattr(offer, "is_weighable", False) if not isinstance(offer, dict) else offer.get("is_weighable"))

    if not name:
        return None

    organic = "אורגני" in name
    lactose_free = "נטול לקטוז" in name or "ללא לקטוז" in name
    gluten_free = "ללא גלוטן" in name or "נטול גלוטן" in name
    kosher = "מהדרין" in name or "בדצ" in name or "בדץ" in name or "עדה חרדית" in name

    if "חלב" in name and not _contains_any(name, {"משקה", "מעדן", "חטיף", "עוגיות", "קרם"}):
        fat = _fat(raw_name or "")
        if not fat or unit_dimension != "volume" or not qty:
            return None
        animal = "goat" if "עיזים" in name else "cow"
        flags = [animal, f"fat:{fat}", f"qty:{qty}"]
        if lactose_free:
            flags.append("lactose_free")
        if organic:
            flags.append("organic")
        if kosher:
            flags.append("kosher")
        label = f"חלב {fat}% {_size_label(unit_dimension, qty)}"
        if lactose_free:
            label += " נטול לקטוז"
        if animal == "goat":
            label += " עיזים"
        if organic:
            label += " אורגני"
        if kosher:
            label += " מהדרין"
        key = "milk|" + "|".join(flags)
        return GenericGroup(key=key, family="milk", label=label)

    if "ביצ" in name:
        count_match = _COUNT_RE.search(name)
        size_match = _EGG_SIZE_RE.search(name)
        if not count_match or not size_match:
            return None
        count = count_match.group(1)
        size = size_match.group(1).upper()
        flags = [f"size:{size}", f"count:{count}"]
        if organic:
            flags.append("organic")
        if _contains_any(name, {"חופש", "חופשיות"}):
            flags.append("free_range")
        label = f"ביצים {size} {count} יחידות"
        if organic:
            label += " אורגני"
        key = "eggs|" + "|".join(flags)
        return GenericGroup(key=key, family="eggs", label=label)

    simple_families: list[tuple[str, str, str, set[str]]] = [
        ("sugar", "סוכר", "סוכר", {"סוכר"}),
        ("flour", "קמח", "קמח", {"קמח"}),
        ("rice", "אורז", "אורז", {"אורז"}),
        ("pasta", "פסטה", "פסטה", {"פסטה", "ספגטי"}),
        ("salt", "מלח", "מלח", {"מלח"}),
        ("tuna", "טונה", "טונה", {"טונה"}),
        ("tomato_paste", "רסק עגבניות", "רסק עגבניות", {"רסק עגבניות"}),
        ("cottage", "קוטג׳", "קוטג׳", {"קוטג"}),
        ("white_cheese", "גבינה לבנה", "גבינה לבנה", {"גבינה לבנה"}),
    ]
    for family, label_base, key_base, needles in simple_families:
        if not _contains_any(name, needles) or not qty:
            continue
        if family in {"sugar", "flour", "rice", "pasta", "salt", "tuna", "tomato_paste"} and unit_dimension != "mass":
            continue
        if family in {"cottage", "white_cheese"} and unit_dimension != "mass":
            continue
        flags = [f"qty:{qty}"]
        if organic:
            flags.append("organic")
        if gluten_free:
            flags.append("gluten_free")
        if kosher:
            flags.append("kosher")
        label = f"{label_base} {_size_label(unit_dimension, qty)}"
        if organic:
            label += " אורגני"
        if gluten_free:
            label += " ללא גלוטן"
        key = f"{family}|" + "|".join(flags)
        return GenericGroup(key=key, family=family, label=label)

    if is_weighable and unit_dimension == "mass":
        for family, label in (("chicken", "עוף"), ("salmon", "סלמון"), ("ground_beef", "בשר טחון")):
            if family == "chicken" and "עוף" not in name:
                continue
            if family == "salmon" and "סלמון" not in name:
                continue
            if family == "ground_beef" and not ("בשר טחון" in name or "טחון בקר" in name):
                continue
            frozen = "קפוא" in name
            key = f"{family}|weight|{'frozen' if frozen else 'fresh'}"
            return GenericGroup(key=key, family=family, label=f"{label} במשקל {'קפוא' if frozen else 'טרי'}")

    return None
