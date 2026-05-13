#!/usr/bin/env python3
"""
Build a large brand-aware reference catalog and measure cross-chain match coverage.

Reference catalog:
- Shufersal
- Tiv Taam

Targets:
- Carrefour
- Machsanei HaShook
- Rami Levy
- Keshet Teamim
- Quik
- Victory
- Yenot Bitan
- Yochananof

Matching strategy (order-insensitive, brand-aware):
1. exact barcode
2. exact canonical key (brand + sorted name tokens + quantity signature)
3. fuzzy token overlap + char similarity on narrowed candidates

Outputs:
- reference_products_with_brands.json
- target_coverage_summary.json
- reference_match_matrix.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import importlib
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scrapers.common import ScrapeFilter


REFERENCE_CHAINS = ["shufersal", "tivtaam"]
TARGET_CHAINS = [
    "carrefour",
    "machsanei_hashook",
    "ramilevi",
    "keshet",
    "quik",
    "victory",
    "ybitan",
    "yochananof",
]

# Representative target branch/store per chain for cross-chain coverage checks.
TARGET_SELECTION: dict[str, dict[str, Any]] = {
    "carrefour": {"branches": [3019]},
    "machsanei_hashook": {},  # fixed branch inside scraper
    "ramilevi": {"stores": [1314, 1389]},
    "keshet": {"branches": [2585]},
    "quik": {"branches": [3264]},
    "victory": {"branches": [2527]},
    "ybitan": {"branches": [960]},
    "yochananof": {"stores": "first_live"},
}

_PUNCT_RE = re.compile(r"[\"'״׳`´,.:;!?()\[\]{}<>|\\/+*=~^%$#@_-]")
_WS_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[0-9]+(?:[.,][0-9]+)?|[a-zA-Z\u0590-\u05FF]+")
_NUM_UNIT_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(kg|g|ml|l)")

_UNIT_TOKEN_MAP = {
    "קג": "kg",
    "ק""ג": "kg",
    "קילו": "kg",
    "קילוגרם": "kg",
    "kg": "kg",
    "גרם": "g",
    "גר": "g",
    "g": "g",
    "מל": "ml",
    "מ""ל": "ml",
    "ml": "ml",
    "ליטר": "l",
    "ל": "l",
    "l": "l",
}

_NOISE_TOKENS = {
    "מחיר",
    "לפי",
    "משקל",
    "ארוז",
    "מארז",
    "חדש",
    "יח",
    "יחידה",
    "יחידות",
}


@dataclass
class MatchOutcome:
    matched: bool
    score: float
    reason: str
    target_product_id: str | None


def normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("־", " ")
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def normalize_brand(value: Any) -> str:
    brand = normalize_text(value)
    return brand


def normalize_barcode(value: Any) -> str:
    raw = str(value or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if 7 <= len(digits) <= 14:
        return digits
    return ""


def canonical_token(token: str) -> str:
    if not token:
        return ""
    t = token.replace(",", ".")
    return _UNIT_TOKEN_MAP.get(t, t)


def tokenize_name(value: Any) -> list[str]:
    text = normalize_text(value)
    tokens = [canonical_token(tok) for tok in _TOKEN_RE.findall(text)]
    cleaned: list[str] = []
    for tok in tokens:
        if not tok:
            continue
        if tok in _NOISE_TOKENS:
            continue
        if len(tok) == 1 and not tok.isdigit():
            continue
        cleaned.append(tok)
    return cleaned


def quantity_signature(product: dict[str, Any]) -> str:
    dim = str(product.get("unit_dimension") or "").strip()
    qty_si = product.get("unit_qty_si")
    if dim and isinstance(qty_si, (int, float)) and math.isfinite(float(qty_si)):
        q = int(round(float(qty_si)))
        if q > 0:
            return f"{dim}:{q}"

    if product.get("is_weighable"):
        return "weighable"

    unit_desc = normalize_text(product.get("unit_description"))
    if unit_desc:
        m = _NUM_UNIT_RE.search(unit_desc)
        if m:
            qty = float(m.group(1))
            unit = m.group(2)
            if unit == "kg":
                return f"mass:{int(round(qty * 1000))}"
            if unit == "g":
                return f"mass:{int(round(qty))}"
            if unit == "l":
                return f"volume:{int(round(qty * 1000))}"
            if unit == "ml":
                return f"volume:{int(round(qty))}"
    return ""


def product_brand(product: dict[str, Any]) -> str:
    return normalize_brand(product.get("brand") or product.get("manufacturer"))


def make_exact_key(brand_norm: str, sorted_tokens_key: str, qty_sig: str) -> str:
    return f"{brand_norm}|{sorted_tokens_key}|{qty_sig}"


def preprocess_product(product: dict[str, Any]) -> dict[str, Any]:
    name = str(product.get("name") or "").strip()
    brand = product.get("brand")
    manufacturer = product.get("manufacturer")
    barcode = normalize_barcode(product.get("barcode") or product.get("product_id"))
    brand_norm = normalize_brand(brand or manufacturer)
    name_norm = normalize_text(name)
    name_tokens = tokenize_name(name)
    token_set = set(name_tokens)
    sorted_tokens_key = " ".join(sorted(token_set))
    qty_sig = quantity_signature(product)

    return {
        "product": product,
        "product_id": str(product.get("product_id") or ""),
        "name": name,
        "brand": brand,
        "manufacturer": manufacturer,
        "barcode": barcode,
        "brand_norm": brand_norm,
        "name_norm": name_norm,
        "token_set": token_set,
        "sorted_tokens_key": sorted_tokens_key,
        "qty_sig": qty_sig,
    }


def reference_key(pre: dict[str, Any]) -> str:
    if pre["barcode"]:
        return f"barcode:{pre['barcode']}"
    return f"name:{make_exact_key(pre['brand_norm'], pre['sorted_tokens_key'], pre['qty_sig'])}"


def merge_reference(existing: dict[str, Any], incoming: dict[str, Any], source_chain: str) -> None:
    existing["source_chains"] = sorted(set(existing["source_chains"] + [source_chain]))

    if not existing.get("brand") and incoming.get("brand"):
        existing["brand"] = incoming["brand"]
    if not existing.get("manufacturer") and incoming.get("manufacturer"):
        existing["manufacturer"] = incoming["manufacturer"]
    if not existing.get("barcode") and incoming.get("barcode"):
        existing["barcode"] = incoming["barcode"]
    if len(str(incoming.get("name") or "")) > len(str(existing.get("name") or "")):
        existing["name"] = incoming.get("name")
    if not existing.get("unit_description") and incoming.get("unit_description"):
        existing["unit_description"] = incoming.get("unit_description")
    if not existing.get("unit_dimension") and incoming.get("unit_dimension"):
        existing["unit_dimension"] = incoming.get("unit_dimension")
    if existing.get("unit_qty_si") is None and incoming.get("unit_qty_si") is not None:
        existing["unit_qty_si"] = incoming.get("unit_qty_si")


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a | b)


def containment(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / len(a)


def brands_compatible(ref_brand: str, tgt_brand: str) -> bool:
    if not ref_brand:
        return True
    if not tgt_brand:
        return True
    if ref_brand == tgt_brand:
        return True
    if len(ref_brand) >= 3 and ref_brand in tgt_brand:
        return True
    if len(tgt_brand) >= 3 and tgt_brand in ref_brand:
        return True
    return False


def score_match(ref: dict[str, Any], tgt: dict[str, Any]) -> float:
    ref_tokens = ref["token_set"]
    tgt_tokens = tgt["token_set"]
    tok_jaccard = jaccard(ref_tokens, tgt_tokens)
    tok_containment = containment(ref_tokens, tgt_tokens)
    char_score = SequenceMatcher(None, ref["name_norm"], tgt["name_norm"]).ratio()

    qty_score = 0.0
    if ref["qty_sig"] and tgt["qty_sig"]:
        qty_score = 0.12 if ref["qty_sig"] == tgt["qty_sig"] else -0.08

    brand_score = 0.0
    ref_brand = ref["brand_norm"]
    tgt_brand = tgt["brand_norm"]
    if ref_brand:
        if ref_brand == tgt_brand:
            brand_score = 0.18
        elif brands_compatible(ref_brand, tgt_brand):
            brand_score = 0.10
        elif tgt_brand:
            brand_score = -0.18
        else:
            brand_score = 0.04
    else:
        brand_score = 0.03

    return 0.45 * tok_jaccard + 0.22 * tok_containment + 0.20 * char_score + qty_score + brand_score


def build_target_index(products: list[dict[str, Any]]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    by_barcode: dict[str, list[int]] = defaultdict(list)
    by_exact_key: dict[str, list[int]] = defaultdict(list)
    by_brand: dict[str, set[int]] = defaultdict(set)
    by_brand_token: dict[str, set[int]] = defaultdict(set)
    by_token: dict[str, set[int]] = defaultdict(set)

    for idx, raw in enumerate(products):
        pre = preprocess_product(raw)
        records.append(pre)

        barcode = pre["barcode"]
        if barcode:
            by_barcode[barcode].append(idx)

        exact_key = make_exact_key(pre["brand_norm"], pre["sorted_tokens_key"], pre["qty_sig"])
        by_exact_key[exact_key].append(idx)

        if pre["brand_norm"]:
            by_brand[pre["brand_norm"]].add(idx)
            for tok in pre["brand_norm"].split():
                by_brand_token[tok].add(idx)

        for tok in pre["token_set"]:
            by_token[tok].add(idx)

    token_freq = {tok: len(ids) for tok, ids in by_token.items()}
    return {
        "records": records,
        "by_barcode": by_barcode,
        "by_exact_key": by_exact_key,
        "by_brand": by_brand,
        "by_brand_token": by_brand_token,
        "by_token": by_token,
        "token_freq": token_freq,
    }


def candidate_ids(ref: dict[str, Any], index: dict[str, Any]) -> set[int]:
    candidates: set[int] = set()

    ref_brand = ref["brand_norm"]
    if ref_brand and ref_brand in index["by_brand"]:
        candidates = set(index["by_brand"][ref_brand])
    elif ref_brand:
        for tok in ref_brand.split():
            candidates.update(index["by_brand_token"].get(tok, set()))

    token_index = index["by_token"]
    token_freq = index["token_freq"]
    rare_tokens = sorted(
        [tok for tok in ref["token_set"] if tok in token_index],
        key=lambda tok: token_freq.get(tok, 10**9),
    )

    if rare_tokens:
        token_sets = [token_index[tok] for tok in rare_tokens[:4]]
        if candidates:
            narrowed = set(candidates)
            for tok_set in token_sets:
                inter = narrowed & tok_set
                if inter:
                    narrowed = inter
            candidates = narrowed
        else:
            narrowed = set(token_sets[0])
            for tok_set in token_sets[1:3]:
                inter = narrowed & tok_set
                if inter:
                    narrowed = inter
            if len(narrowed) <= 2:
                widened: set[int] = set()
                for tok_set in token_sets[:3]:
                    widened.update(tok_set)
                narrowed = widened
            candidates = narrowed

    return candidates


def match_reference_to_target(
    ref: dict[str, Any],
    index: dict[str, Any],
    *,
    threshold: float,
) -> MatchOutcome:
    barcode = ref["barcode"]
    if barcode:
        barcode_hits = index["by_barcode"].get(barcode)
        if barcode_hits:
            rec = index["records"][barcode_hits[0]]
            return MatchOutcome(True, 1.0, "barcode", rec["product_id"])

    exact_key = make_exact_key(ref["brand_norm"], ref["sorted_tokens_key"], ref["qty_sig"])
    exact_hits = index["by_exact_key"].get(exact_key)
    if exact_hits:
        rec = index["records"][exact_hits[0]]
        return MatchOutcome(True, 0.99, "exact_key", rec["product_id"])

    candidates = candidate_ids(ref, index)
    if not candidates:
        return MatchOutcome(False, 0.0, "no_candidate", None)

    if len(candidates) > 2000:
        ref_tokens = ref["token_set"]
        ranked = sorted(
            candidates,
            key=lambda cid: containment(ref_tokens, index["records"][cid]["token_set"]),
            reverse=True,
        )
        candidates = set(ranked[:2000])

    best_score = -10.0
    best_id: int | None = None
    best_reason = "low_score"

    for cid in candidates:
        tgt = index["records"][cid]
        if ref["brand_norm"] and tgt["brand_norm"] and not brands_compatible(
            ref["brand_norm"], tgt["brand_norm"]
        ):
            continue
        score = score_match(ref, tgt)
        if score > best_score:
            best_score = score
            best_id = cid
            best_reason = "fuzzy"

    if best_id is not None and best_score >= threshold:
        rec = index["records"][best_id]
        return MatchOutcome(True, best_score, best_reason, rec["product_id"])

    if best_id is None:
        return MatchOutcome(False, 0.0, "brand_mismatch", None)

    return MatchOutcome(False, best_score, "low_score", None)


def _find_cache_file(cache_dir: Path, chain: str, role: str) -> Path | None:
    if not cache_dir.exists():
        return None
    files = sorted(cache_dir.glob(f"{chain}_{role}_*.json"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _save_cache(cache_dir: Path, chain: str, role: str, products: list[dict[str, Any]], errors: list[str]) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = cache_dir / f"{chain}_{role}_{ts}.json"
    payload = {
        "chain": chain,
        "role": role,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "products": products,
        "errors": errors,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _load_cache(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [], [f"cache_read_error: {exc}"]
    products = payload.get("products") or []
    errors = payload.get("errors") or []
    return [p for p in products if isinstance(p, dict)], [str(e) for e in errors]


def _flatten_products(result: dict[str, Any]) -> list[dict[str, Any]]:
    products_by_store = result.get("products_by_store") or {}
    return [
        product
        for store_products in products_by_store.values()
        for product in (store_products or [])
        if isinstance(product, dict)
    ]


def _first_branch_by_ids(module: Any, ids: list[int]) -> list[dict[str, Any]]:
    branches = getattr(module, "ONLINE_BRANCHES", [])
    by_id = {int(branch.get("id")): branch for branch in branches if branch.get("id") is not None}
    selected: list[dict[str, Any]] = []
    for bid in ids:
        if bid in by_id:
            selected.append(by_id[bid])
    return selected


def _first_store_by_ids(module: Any, ids: list[int]) -> list[dict[str, Any]]:
    stores = getattr(module, "ONLINE_STORES", [])
    by_id = {int(store.get("id")): store for store in stores if store.get("id") is not None}
    selected: list[dict[str, Any]] = []
    for sid in ids:
        if sid in by_id:
            selected.append(by_id[sid])
    return selected


async def _resolve_yochananof_first_store(module: Any) -> list[dict[str, Any]]:
    try:
        stores = await module.update_branches()
    except Exception:
        return []
    if not stores:
        return []
    return stores[:1]


async def scrape_chain(
    chain: str,
    role: str,
    *,
    batch_size: int,
    max_concurrent: int,
    max_retries: int,
    base_retry_delay: float,
) -> tuple[list[dict[str, Any]], list[str]]:
    module = importlib.import_module(f"scrapers.{chain}.{chain}")

    kwargs: dict[str, Any] = {
        "flt": ScrapeFilter(),
        "batch_size": batch_size,
        "max_concurrent": max_concurrent,
        "max_retries": max_retries,
        "base_retry_delay": base_retry_delay,
    }

    if role == "target":
        selection = TARGET_SELECTION.get(chain, {})
        if chain == "ramilevi" and selection.get("stores"):
            stores = _first_store_by_ids(module, selection["stores"])
            if stores:
                kwargs["stores"] = stores
        elif chain == "yochananof":
            stores_cfg = selection.get("stores")
            if stores_cfg == "first_live":
                stores = await _resolve_yochananof_first_store(module)
                if stores:
                    kwargs["stores"] = stores
        elif selection.get("branches"):
            branches = _first_branch_by_ids(module, selection["branches"])
            if branches:
                kwargs["branches"] = branches

    if chain == "machsanei_hashook":
        kwargs.pop("branches", None)

    result = await module.scrape(**kwargs)
    products = _flatten_products(result)
    errors = [str(e) for e in (result.get("errors") or [])]
    return products, errors


async def load_chain_products(
    chain: str,
    role: str,
    *,
    cache_dir: Path,
    use_cache_only: bool,
    force_live: bool,
    batch_size: int,
    max_concurrent: int,
    max_retries: int,
    base_retry_delay: float,
) -> tuple[list[dict[str, Any]], list[str], str]:
    cache_path = _find_cache_file(cache_dir, chain, role)
    if cache_path and not force_live:
        products, errors = _load_cache(cache_path)
        return products, errors, str(cache_path)

    if use_cache_only:
        return [], ["cache_missing"], "missing_cache"

    products, errors = await scrape_chain(
        chain,
        role,
        batch_size=batch_size,
        max_concurrent=max_concurrent,
        max_retries=max_retries,
        base_retry_delay=base_retry_delay,
    )
    saved = _save_cache(cache_dir, chain, role, products, errors)
    return products, errors, str(saved)


def build_reference_catalog(reference_raw: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    shufersal_barcode_brand: dict[str, str] = {}
    for product in reference_raw.get("shufersal", []):
        barcode = normalize_barcode(product.get("barcode") or product.get("product_id"))
        brand = normalize_brand(product.get("brand") or product.get("manufacturer"))
        if barcode and brand and barcode not in shufersal_barcode_brand:
            shufersal_barcode_brand[barcode] = brand

    dedup: dict[str, dict[str, Any]] = {}

    for chain, products in reference_raw.items():
        for raw in products:
            product = dict(raw)

            inferred_brand = False
            barcode = normalize_barcode(product.get("barcode") or product.get("product_id"))
            brand = product.get("brand") or product.get("manufacturer")
            brand_norm = normalize_brand(brand)

            if not brand_norm and chain == "tivtaam" and barcode in shufersal_barcode_brand:
                product["brand"] = shufersal_barcode_brand[barcode]
                brand_norm = shufersal_barcode_brand[barcode]
                inferred_brand = True

            # Keep only products that have a brand signal (native or inferred).
            if not brand_norm:
                continue

            pre = preprocess_product(product)
            key = reference_key(pre)

            record = {
                "name": pre["name"],
                "brand": product.get("brand"),
                "manufacturer": product.get("manufacturer"),
                "barcode": pre["barcode"],
                "product_id": pre["product_id"],
                "unit_description": product.get("unit_description"),
                "unit_dimension": product.get("unit_dimension"),
                "unit_qty_si": product.get("unit_qty_si"),
                "source_chains": [chain],
                "brand_inferred_from_shufersal": inferred_brand,
                "brand_norm": pre["brand_norm"],
                "name_norm": pre["name_norm"],
                "token_set": pre["token_set"],
                "sorted_tokens_key": pre["sorted_tokens_key"],
                "qty_sig": pre["qty_sig"],
            }

            if key in dedup:
                merge_reference(dedup[key], record, chain)
            else:
                dedup[key] = record

    refs = list(dedup.values())
    refs.sort(key=lambda r: (r["brand_norm"], r["name_norm"]))

    for idx, ref in enumerate(refs, start=1):
        ref["reference_id"] = f"R{idx:06d}"
        ref["token_set"] = sorted(ref["token_set"])

    return refs


def evaluate_target(
    target_chain: str,
    target_products: list[dict[str, Any]],
    references: list[dict[str, Any]],
    *,
    threshold: float,
    sample_limit: int,
) -> tuple[dict[str, Any], list[int], list[float], list[str], list[dict[str, Any]]]:
    index = build_target_index(target_products)

    flags: list[int] = []
    scores: list[float] = []
    reasons: list[str] = []
    reason_counts: Counter[str] = Counter()
    matched = 0
    sample_missing: list[dict[str, Any]] = []

    start = time.monotonic()
    for ref in references:
        ref_for_match = {
            "barcode": ref.get("barcode") or "",
            "brand_norm": ref.get("brand_norm") or "",
            "name_norm": ref.get("name_norm") or "",
            "token_set": set(ref.get("token_set") or []),
            "sorted_tokens_key": ref.get("sorted_tokens_key") or "",
            "qty_sig": ref.get("qty_sig") or "",
        }

        outcome = match_reference_to_target(ref_for_match, index, threshold=threshold)
        flag = 1 if outcome.matched else 0
        flags.append(flag)
        scores.append(round(outcome.score, 4))
        reasons.append(outcome.reason)
        reason_counts[outcome.reason] += 1

        if outcome.matched:
            matched += 1
        elif len(sample_missing) < sample_limit:
            sample_missing.append(
                {
                    "reference_id": ref["reference_id"],
                    "name": ref.get("name"),
                    "brand": ref.get("brand") or ref.get("manufacturer"),
                    "barcode": ref.get("barcode"),
                    "unit_description": ref.get("unit_description"),
                    "best_score": round(outcome.score, 4),
                    "reason": outcome.reason,
                }
            )

    duration = time.monotonic() - start
    total = len(references)
    coverage = round((matched / total) * 100, 2) if total else 0.0

    summary = {
        "target_chain": target_chain,
        "reference_total": total,
        "target_products": len(target_products),
        "matched": matched,
        "missing": total - matched,
        "coverage_percent": coverage,
        "duration_seconds": round(duration, 2),
        "reason_counts": dict(reason_counts),
    }

    return summary, flags, scores, reasons, sample_missing


def write_reference_json(path: Path, references: list[dict[str, Any]]) -> None:
    payload = []
    for ref in references:
        payload.append(
            {
                "reference_id": ref["reference_id"],
                "name": ref.get("name"),
                "brand": ref.get("brand"),
                "manufacturer": ref.get("manufacturer"),
                "barcode": ref.get("barcode"),
                "product_id": ref.get("product_id"),
                "unit_description": ref.get("unit_description"),
                "unit_dimension": ref.get("unit_dimension"),
                "unit_qty_si": ref.get("unit_qty_si"),
                "source_chains": ref.get("source_chains"),
                "brand_inferred_from_shufersal": ref.get("brand_inferred_from_shufersal", False),
            }
        )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_matrix_csv(
    path: Path,
    references: list[dict[str, Any]],
    targets: list[str],
    per_target_flags: dict[str, list[int]],
    per_target_scores: dict[str, list[float]],
    per_target_reasons: dict[str, list[str]],
) -> None:
    fieldnames = [
        "reference_id",
        "name",
        "brand",
        "manufacturer",
        "barcode",
        "unit_description",
        "source_chains",
    ]
    for target in targets:
        fieldnames.extend(
            [
                f"match_{target}",
                f"score_{target}",
                f"reason_{target}",
            ]
        )

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for idx, ref in enumerate(references):
            row = {
                "reference_id": ref["reference_id"],
                "name": ref.get("name"),
                "brand": ref.get("brand"),
                "manufacturer": ref.get("manufacturer"),
                "barcode": ref.get("barcode"),
                "unit_description": ref.get("unit_description"),
                "source_chains": ",".join(ref.get("source_chains") or []),
            }
            for target in targets:
                row[f"match_{target}"] = per_target_flags[target][idx]
                row[f"score_{target}"] = per_target_scores[target][idx]
                row[f"reason_{target}"] = per_target_reasons[target][idx]
            writer.writerow(row)


async def async_main(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache_dir) if args.cache_dir else (output_dir / "cache")

    print("Loading reference chains...")
    reference_raw: dict[str, list[dict[str, Any]]] = {}
    data_sources: dict[str, str] = {}
    load_errors: dict[str, list[str]] = {}

    for chain in args.references:
        products, errors, source = await load_chain_products(
            chain,
            "reference",
            cache_dir=cache_dir,
            use_cache_only=args.use_cache_only,
            force_live=args.force_live_references,
            batch_size=args.batch_size,
            max_concurrent=args.max_concurrent,
            max_retries=args.max_retries,
            base_retry_delay=args.base_retry_delay,
        )
        reference_raw[chain] = products
        data_sources[chain] = source
        load_errors[chain] = errors
        print(f"  {chain:<16} products={len(products):>7} source={source}")
        if errors:
            print(f"    errors: {errors[:3]}")

    references = build_reference_catalog(reference_raw)
    if not references:
        print("No reference products with brand signals were built.")
        return 1

    print(f"Reference catalog size (deduped, branded): {len(references):,}")

    reference_json = output_dir / "reference_products_with_brands.json"
    write_reference_json(reference_json, references)

    print("Loading target chains...")
    target_products: dict[str, list[dict[str, Any]]] = {}
    for chain in args.targets:
        products, errors, source = await load_chain_products(
            chain,
            "target",
            cache_dir=cache_dir,
            use_cache_only=args.use_cache_only,
            force_live=args.force_live_targets,
            batch_size=args.batch_size,
            max_concurrent=args.max_concurrent,
            max_retries=args.max_retries,
            base_retry_delay=args.base_retry_delay,
        )
        target_products[chain] = products
        data_sources[chain] = source
        load_errors[chain] = errors
        print(f"  {chain:<16} products={len(products):>7} source={source}")
        if errors:
            print(f"    errors: {errors[:3]}")

    summaries: dict[str, Any] = {}
    sample_missing_by_chain: dict[str, list[dict[str, Any]]] = {}
    per_target_flags: dict[str, list[int]] = {}
    per_target_scores: dict[str, list[float]] = {}
    per_target_reasons: dict[str, list[str]] = {}

    print("Computing match coverage...")
    for chain in args.targets:
        products = target_products.get(chain, [])
        summary, flags, scores, reasons, sample_missing = evaluate_target(
            chain,
            products,
            references,
            threshold=args.threshold,
            sample_limit=args.sample_limit,
        )
        summaries[chain] = summary
        sample_missing_by_chain[chain] = sample_missing
        per_target_flags[chain] = flags
        per_target_scores[chain] = scores
        per_target_reasons[chain] = reasons
        print(
            f"  {chain:<16} coverage={summary['coverage_percent']:>7.2f}% "
            f"matched={summary['matched']:>7} missing={summary['missing']:>7}"
        )

    matrix_csv = output_dir / "reference_match_matrix.csv"
    write_matrix_csv(
        matrix_csv,
        references,
        args.targets,
        per_target_flags,
        per_target_scores,
        per_target_reasons,
    )

    summary_json = output_dir / "target_coverage_summary.json"
    summary_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "references": args.references,
        "targets": args.targets,
        "reference_count": len(references),
        "threshold": args.threshold,
        "sample_limit": args.sample_limit,
        "data_sources": data_sources,
        "load_errors": load_errors,
        "summaries": summaries,
        "sample_missing": sample_missing_by_chain,
    }
    summary_json.write_text(json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nDone.")
    print(f"- Reference list: {reference_json}")
    print(f"- Coverage summary: {summary_json}")
    print(f"- Match matrix CSV: {matrix_csv}")

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a big branded reference list and cross-chain match coverage audit"
    )
    parser.add_argument("--references", nargs="*", default=REFERENCE_CHAINS)
    parser.add_argument("--targets", nargs="*", default=TARGET_CHAINS)
    parser.add_argument(
        "--output-dir",
        default="output_dir/validation/reference_match_audit",
        help="Directory for audit outputs",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="Cache directory (default: <output-dir>/cache)",
    )
    parser.add_argument("--use-cache-only", action="store_true")
    parser.add_argument("--force-live-references", action="store_true")
    parser.add_argument("--force-live-targets", action="store_true")
    parser.add_argument("--threshold", type=float, default=0.66)
    parser.add_argument("--sample-limit", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-concurrent", type=int, default=3)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--base-retry-delay", type=float, default=0.5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
