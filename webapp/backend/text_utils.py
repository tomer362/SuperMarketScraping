from __future__ import annotations

import re
from typing import Any


_NON_TEXT_RE = re.compile(r"[^0-9A-Za-z\u0590-\u05FF]+")
_SPACE_RE = re.compile(r"\s+")
_APOSTROPHE_LIKE_CHARS = "'\"`´’‘ʼ\u05f3\u05f4"
_APOSTROPHE_TRANSLATION = str.maketrans("", "", _APOSTROPHE_LIKE_CHARS)


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
    return f"{value:.3f}".rstrip("0").rstrip(".")


def build_match_key(product: dict[str, Any]) -> str:
    barcode = normalize_barcode(product.get("barcode"))
    if barcode:
        return f"barcode:{barcode}"

    brand = normalize_text(product.get("brand"))
    name = normalize_text(product.get("name"))
    dimension = normalize_text(product.get("unit_dimension"))
    qty = format_qty_for_match(product.get("unit_qty_si"))
    unit = normalize_text(product.get("unit_of_measure"))
    return f"text:{brand}|{name}|{dimension}|{qty}|{unit}"
