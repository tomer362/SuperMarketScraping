"""Helpers for validating CHP compare parsing against browser DOM rows."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff\u200e\u200f]")
_MULTI_BUY_RE = re.compile(r"(\d+)\s+ב[-–]?\s*([\d.]+)")
_PRICE_IN_TEXT_RE = re.compile(r"([\d.]+)\s*ש")


def normalize_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\xa0", " ")
    text = _ZERO_WIDTH_RE.sub("", text)
    text = text.strip()
    return re.sub(r"\s+", " ", text)


def normalize_deal_text(value: Any) -> str:
    text = normalize_text(value)
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "<BR>", text)
    text = re.sub(r"\s*<BR>\s*", "<BR>", text)
    return text


def parse_price(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = normalize_text(value).replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def expected_deal_type_from_browser_row(row: Dict[str, Any]) -> str:
    deal_desc = normalize_deal_text(row.get("deal_text"))
    deal_price_text = normalize_text(row.get("deal_price_text"))
    row_price = parse_price(row.get("row_price"))
    if row_price is None:
        row_price = parse_price(row.get("row_price_raw"))

    if not deal_desc and not deal_price_text:
        return "none"

    if _MULTI_BUY_RE.search(deal_desc):
        return "multi_buy"

    dp = parse_price(deal_price_text.replace("*", "").strip())
    if dp is not None and row_price is not None and dp < row_price - 1e-9:
        return "price_reduction"

    desc_price_match = _PRICE_IN_TEXT_RE.search(deal_desc)
    if desc_price_match and row_price is not None:
        try:
            desc_price = float(desc_price_match.group(1))
            if desc_price < row_price - 1e-9:
                return "price_reduction"
        except ValueError:
            pass

    return "other"


def compare_browser_rows_to_parser_details(
    browser_rows: List[Dict[str, Any]],
    parser_details: List[Dict[str, Any]],
    *,
    row_kind: str,
    price_tolerance: float = 0.01,
) -> List[Dict[str, Any]]:
    """Compare browser DOM rows to parser row details and return mismatches."""
    mismatches: List[Dict[str, Any]] = []

    if len(browser_rows) != len(parser_details):
        mismatches.append(
            {
                "type": "row_count",
                "row_kind": row_kind,
                "browser_count": len(browser_rows),
                "parser_count": len(parser_details),
            }
        )

    for idx in range(min(len(browser_rows), len(parser_details))):
        browser_row = browser_rows[idx]
        parser_row = parser_details[idx]
        parser_store = parser_row.get("store", {})
        parser_raw = parser_row.get("raw", {})
        parser_deal = parser_row.get("deal")

        checks = [
            ("chain_name", normalize_text(browser_row.get("chain_name")), normalize_text(parser_store.get("chain_name"))),
            ("store_name", normalize_text(browser_row.get("store_name")), normalize_text(parser_store.get("store_name"))),
            ("store_url", normalize_text(browser_row.get("store_url")), normalize_text(parser_store.get("store_url"))),
            ("deal_text", normalize_deal_text(browser_row.get("deal_text")), normalize_deal_text(parser_raw.get("deal_text"))),
            ("deal_price_text", normalize_text(browser_row.get("deal_price_text")), normalize_text(parser_raw.get("deal_price_text"))),
        ]

        if row_kind == "physical":
            checks.append(
                (
                    "address",
                    normalize_text(browser_row.get("address")),
                    normalize_text(parser_store.get("address")),
                )
            )
        else:
            checks.append(
                (
                    "website",
                    normalize_text(browser_row.get("website")),
                    normalize_text(parser_store.get("website")),
                )
            )

        for field, browser_value, parser_value in checks:
            if browser_value != parser_value:
                mismatches.append(
                    {
                        "type": "field_mismatch",
                        "row_kind": row_kind,
                        "row_index": idx,
                        "field": field,
                        "browser": browser_value,
                        "parser": parser_value,
                    }
                )

        browser_price = parse_price(browser_row.get("row_price"))
        if browser_price is None:
            browser_price = parse_price(browser_row.get("row_price_raw"))
        parser_price = parse_price(parser_raw.get("row_price"))
        if browser_price is None or parser_price is None:
            mismatches.append(
                {
                    "type": "price_unparseable",
                    "row_kind": row_kind,
                    "row_index": idx,
                    "browser_price": browser_row.get("row_price"),
                    "browser_price_raw": browser_row.get("row_price_raw"),
                    "parser_price": parser_raw.get("row_price"),
                }
            )
        elif abs(browser_price - parser_price) > price_tolerance:
            mismatches.append(
                {
                    "type": "price_mismatch",
                    "row_kind": row_kind,
                    "row_index": idx,
                    "browser": browser_price,
                    "parser": parser_price,
                    "tolerance": price_tolerance,
                }
            )

        expected_deal_type = expected_deal_type_from_browser_row(browser_row)
        parser_deal_type = parser_deal.get("deal_type") if parser_deal else "none"
        if expected_deal_type != parser_deal_type:
            mismatches.append(
                {
                    "type": "deal_type_mismatch",
                    "row_kind": row_kind,
                    "row_index": idx,
                    "expected_from_browser": expected_deal_type,
                    "parser": parser_deal_type,
                }
            )

    return mismatches
