from __future__ import annotations

import re
from typing import Any


_NON_TEXT_RE = re.compile(r"[^0-9A-Za-z\u0590-\u05FF]+")
_SPACE_RE = re.compile(r"\s+")
_APOSTROPHE_LIKE_CHARS = "'\"`´’‘ʼ\u05f3\u05f4"
_APOSTROPHE_TRANSLATION = str.maketrans("", "", _APOSTROPHE_LIKE_CHARS)
_GENERIC_NAME_TOKENS = {
    "עם",
    "ללא",
    "של",
    "טרי",
    "ארוז",
    "מבצע",
    "מחיר",
    "חדש",
    "יח",
    "יחידה",
    "יחידות",
    "שומן",
    "טעם",
    "בטעם",
    "קג",
    "מל",
    "גרם",
    "ליטר",
}


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    collapsed = value.casefold().translate(_APOSTROPHE_TRANSLATION)
    cleaned = _NON_TEXT_RE.sub(" ", collapsed)
    return _SPACE_RE.sub(" ", cleaned).strip()


def normalize_barcode(value: str | None) -> str:
    if not value:
        return ""
    return "".join(ch for ch in value if ch.isdigit())


def build_search_text(*parts: str | None) -> str:
    return " ".join(part for part in (normalize_text(p) for p in parts) if part)


def format_qty_for_match(value: float | None) -> str:
    if value is None:
        return ""
    try:
        rounded = round(float(value))
    except (TypeError, ValueError):
        return ""
    if rounded <= 0:
        return ""
    if rounded < 100:
        bucketed = rounded
    elif rounded < 1500:
        bucketed = int(round(rounded / 10.0) * 10)
    else:
        bucketed = int(round(rounded / 50.0) * 50)
    return str(bucketed)


def _name_signature(value: str | None, *, max_tokens: int = 2, stem_len: int = 4) -> str:
    normalized = normalize_text(value)
    if not normalized:
        return ""

    deduped: list[str] = []
    seen: set[str] = set()
    for token in normalized.split():
        if len(token) < 2 or token in _GENERIC_NAME_TOKENS:
            continue
        if token in seen:
            continue
        seen.add(token)
        deduped.append(token)

    if not deduped:
        deduped = normalized.split()[:max_tokens]

    strongest = sorted(deduped, key=lambda token: (-len(token), token))[:max_tokens]
    stems = sorted(token[:stem_len] for token in strongest)
    return " ".join(stems)


def build_match_key(product: dict[str, Any]) -> str:
    barcode = normalize_barcode(product.get("barcode"))
    if barcode:
        return f"barcode:{barcode}"

    brand = normalize_text(product.get("brand") or product.get("manufacturer"))
    name = _name_signature(product.get("name"))
    dimension = normalize_text(product.get("unit_dimension"))
    qty = format_qty_for_match(product.get("unit_qty_si"))
    unit = normalize_text(product.get("unit_of_measure"))
    return f"text:{brand}|{name}|{dimension}|{qty}|{unit}"
