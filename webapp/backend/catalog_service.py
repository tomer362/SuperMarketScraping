from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from math import ceil, inf
from typing import Any, Iterable, Sequence

from sqlalchemy import and_, case, delete, exists, func, insert, or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from chains import get_chain_definition, iter_active_chains, iter_public_chains
from db import database_backend
from models import (
    CanonicalProduct,
    CatalogOffer,
    CatalogOfferStaging,
    CatalogRefreshRun,
    GenericProductGroup,
    GenericProductGroupMember,
    GenericProductGroupMemberStaging,
    GenericProductGroupStaging,
    ShoppingList,
    ShoppingListItem,
    StoreBranch,
    User,
)
from generic_groups import classify_generic_offer
from location_service import haversine_km
from product_links import build_product_url
from settings import get_settings
from text_utils import build_match_key, build_search_text, normalize_barcode, normalize_text


logger = logging.getLogger("webapp.catalog")
ACTIVE_CHAIN_KEYS = tuple(chain.key for chain in iter_active_chains())
_FUZZY_SEARCH_THRESHOLD = 0.72
_FUZZY_CANDIDATE_LIMIT = 1200

_OFFER_COPY_COLUMNS = (
    "canonical_product_id",
    "refresh_run_id",
    "chain",
    "store_id",
    "store_name",
    "product_id",
    "name",
    "barcode",
    "price",
    "regular_price",
    "sale_price",
    "discount_percent",
    "is_weighable",
    "unit_description",
    "unit_of_measure",
    "unit_qty",
    "unit_qty_si",
    "unit_dimension",
    "price_per_base_unit",
    "image_url",
    "brand",
    "manufacturer",
    "category_ids",
    "deal",
    "scraped_at",
    "is_active",
    "created_at",
    "updated_at",
)
_GENERIC_GROUP_COPY_COLUMNS = (
    "key",
    "family",
    "label",
    "search_text",
    "offer_count",
    "chain_count",
    "cheapest_price",
    "created_at",
    "updated_at",
)
_GENERIC_MEMBER_COPY_COLUMNS = ("group_key", "chain", "store_id", "product_id")
_DEAL_MERGE_COLUMNS = (
    "price",
    "regular_price",
    "sale_price",
    "discount_percent",
    "price_per_base_unit",
    "deal",
    "scraped_at",
    "refresh_run_id",
)


def _offer_model(target_table: str):
    if target_table == "active":
        return CatalogOffer
    if target_table == "staging":
        return CatalogOfferStaging
    raise ValueError(f"Unsupported offer target table: {target_table}")


def _resolve_chain_keys(chain_filter: Sequence[str] | None) -> tuple[str, ...]:
    if chain_filter is None:
        return ACTIVE_CHAIN_KEYS
    requested = {chain.strip() for chain in chain_filter if chain and chain.strip()}
    if not requested:
        return tuple()
    return tuple(chain for chain in ACTIVE_CHAIN_KEYS if chain in requested)


def _token_conditions(tokens: Sequence[str]) -> list[Any]:
    conditions: list[Any] = []
    for token in tokens:
        if len(token) < 2:
            continue
        conditions.append(CanonicalProduct.search_text.contains(token))
    return conditions


def _generic_token_conditions(tokens: Sequence[str]) -> list[Any]:
    conditions: list[Any] = []
    for token in tokens:
        if len(token) < 2:
            continue
        conditions.append(GenericProductGroup.search_text.contains(token))
    return conditions


def _score_fuzzy_candidate(query_normalized: str, search_text: str) -> float:
    if not query_normalized or not search_text:
        return 0.0
    if query_normalized in search_text:
        return 1.0

    query_tokens = [token for token in query_normalized.split() if token]
    tokens = [token for token in search_text.split() if token]
    if not query_tokens or not tokens:
        return SequenceMatcher(None, query_normalized, search_text).ratio()

    token_scores: list[float] = []
    for query_token in query_tokens:
        best_score = 0.0
        for token in tokens:
            if token[0] != query_token[0]:
                continue
            ratio = SequenceMatcher(None, query_token, token).ratio()
            if len(query_token) >= 3 and token.startswith(query_token[:2]):
                ratio += 0.08
            if len(query_token) >= 4 and token.startswith(query_token[:3]):
                ratio += 0.08
            best_score = max(best_score, ratio)

        if best_score == 0.0:
            best_score = max(SequenceMatcher(None, query_token, token).ratio() for token in tokens)
        token_scores.append(min(1.0, best_score))

    return sum(token_scores) / len(token_scores)


def _chunks(values: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _catalog_debug_enabled() -> bool:
    return get_settings().catalog_debug


def _canonical_payload(product: dict[str, Any]) -> dict[str, Any]:
    normalized_name = normalize_text(product.get("name"))
    normalized_brand = normalize_text(product.get("brand"))
    return {
        "match_key": build_match_key(product),
        "display_name": product.get("name") or "",
        "normalized_name": normalized_name,
        "brand": product.get("brand"),
        "normalized_brand": normalized_brand,
        "barcode": normalize_barcode(product.get("barcode")) or None,
        "manufacturer": product.get("manufacturer"),
        "image_url": product.get("image_url"),
        "unit_description": product.get("unit_description"),
        "unit_of_measure": product.get("unit_of_measure"),
        "unit_qty": product.get("unit_qty"),
        "unit_qty_si": product.get("unit_qty_si"),
        "unit_dimension": product.get("unit_dimension"),
        "search_text": build_search_text(
            product.get("name"),
            product.get("brand"),
            product.get("manufacturer"),
            product.get("unit_description"),
        ),
    }


def _offer_payload(
    product: dict[str, Any],
    canonical_product_id: int,
    refresh_run_id: int,
) -> dict[str, Any]:
    return {
        "canonical_product_id": canonical_product_id,
        "refresh_run_id": refresh_run_id,
        "chain": product.get("chain", ""),
        "store_id": str(product.get("store_id", "")),
        "store_name": product.get("store_name", ""),
        "product_id": str(product.get("product_id", "")),
        "name": product.get("name", ""),
        "barcode": normalize_barcode(product.get("barcode")) or None,
        "price": float(product.get("price", 0.0) or 0.0),
        "regular_price": float(product.get("regular_price", 0.0) or 0.0),
        "sale_price": product.get("sale_price"),
        "discount_percent": product.get("discount_percent"),
        "is_weighable": bool(product.get("is_weighable", False)),
        "unit_description": product.get("unit_description"),
        "unit_of_measure": product.get("unit_of_measure"),
        "unit_qty": product.get("unit_qty"),
        "unit_qty_si": product.get("unit_qty_si"),
        "unit_dimension": product.get("unit_dimension"),
        "price_per_base_unit": product.get("price_per_base_unit"),
        "image_url": product.get("image_url"),
        "brand": product.get("brand"),
        "manufacturer": product.get("manufacturer"),
        "category_ids": product.get("category_ids") or [],
        "deal": product.get("deal"),
        "scraped_at": product.get("scraped_at", ""),
        "is_active": True,
        "updated_at": _now_utc(),
    }


async def _load_existing_canonical_products(
    session: AsyncSession,
    match_keys: list[str],
) -> dict[str, CanonicalProduct]:
    existing: dict[str, CanonicalProduct] = {}
    for batch in _chunks(match_keys, 1000):
        rows = (
            await session.execute(
                select(CanonicalProduct).where(CanonicalProduct.match_key.in_(batch))
            )
        ).scalars()
        existing.update({row.match_key: row for row in rows})
    return existing


async def _load_canonical_ids(
    session: AsyncSession,
    match_keys: list[str],
) -> dict[str, int]:
    ids: dict[str, int] = {}
    for batch in _chunks(match_keys, 1000):
        rows = await session.execute(
            select(CanonicalProduct.match_key, CanonicalProduct.id).where(
                CanonicalProduct.match_key.in_(batch)
            )
        )
        ids.update({match_key: row_id for match_key, row_id in rows})
    return ids


async def upsert_catalog_products(
    session: AsyncSession,
    products: list[dict[str, Any]],
    refresh_run_id: int,
    *,
    target_table: str = "active",
) -> int:
    if not products:
        return 0

    canonical_rows = [_canonical_payload(product) for product in products]
    canonical_by_key = {row["match_key"]: row for row in canonical_rows}
    if database_backend == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    elif database_backend == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    else:
        raise RuntimeError(f"Unsupported database backend for upsert: {database_backend}")

    for batch in _chunks(list(canonical_by_key.values()), 1000):
        stmt = dialect_insert(CanonicalProduct).values(list(batch))
        stmt = stmt.on_conflict_do_update(
            index_elements=["match_key"],
            set_={
                "display_name": stmt.excluded.display_name,
                "normalized_name": stmt.excluded.normalized_name,
                "brand": stmt.excluded.brand,
                "normalized_brand": stmt.excluded.normalized_brand,
                "barcode": stmt.excluded.barcode,
                "manufacturer": stmt.excluded.manufacturer,
                "image_url": stmt.excluded.image_url,
                "unit_description": stmt.excluded.unit_description,
                "unit_of_measure": stmt.excluded.unit_of_measure,
                "unit_qty": stmt.excluded.unit_qty,
                "unit_qty_si": stmt.excluded.unit_qty_si,
                "unit_dimension": stmt.excluded.unit_dimension,
                "search_text": stmt.excluded.search_text,
                "updated_at": _now_utc(),
            },
        )
        await session.execute(stmt)

    await session.flush()
    canonical_id_by_key = await _load_canonical_ids(
        session, list(canonical_by_key.keys())
    )

    rows = [
        _offer_payload(product, canonical_id_by_key[build_match_key(product)], refresh_run_id)
        for product in products
    ]

    offer_model = _offer_model(target_table)

    for batch in _chunks(rows, 500):
        stmt = dialect_insert(offer_model).values(list(batch))
        stmt = stmt.on_conflict_do_update(
            index_elements=["chain", "store_id", "product_id"],
            set_={
                "canonical_product_id": stmt.excluded.canonical_product_id,
                "refresh_run_id": stmt.excluded.refresh_run_id,
                "store_name": stmt.excluded.store_name,
                "name": stmt.excluded.name,
                "barcode": stmt.excluded.barcode,
                "price": stmt.excluded.price,
                "regular_price": stmt.excluded.regular_price,
                "sale_price": stmt.excluded.sale_price,
                "discount_percent": stmt.excluded.discount_percent,
                "is_weighable": stmt.excluded.is_weighable,
                "unit_description": stmt.excluded.unit_description,
                "unit_of_measure": stmt.excluded.unit_of_measure,
                "unit_qty": stmt.excluded.unit_qty,
                "unit_qty_si": stmt.excluded.unit_qty_si,
                "unit_dimension": stmt.excluded.unit_dimension,
                "price_per_base_unit": stmt.excluded.price_per_base_unit,
                "image_url": stmt.excluded.image_url,
                "brand": stmt.excluded.brand,
                "manufacturer": stmt.excluded.manufacturer,
                "category_ids": stmt.excluded.category_ids,
                "deal": stmt.excluded.deal,
                "scraped_at": stmt.excluded.scraped_at,
                "is_active": True,
                "updated_at": _now_utc(),
            },
        )
        await session.execute(stmt)

    return len(rows)


async def stage_existing_offer_deals(
    session: AsyncSession,
    products: list[dict[str, Any]],
    refresh_run_id: int,
) -> int:
    if not products:
        return 0

    if database_backend == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    elif database_backend == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    else:
        raise RuntimeError(f"Unsupported database backend for upsert: {database_backend}")

    offer_keys = {
        (
            str(product.get("chain", "")),
            str(product.get("store_id", "")),
            str(product.get("product_id", "")),
        )
        for product in products
    }
    active_ids: dict[tuple[str, str, str], int] = {}
    for batch in _chunks(list(offer_keys), 500):
        rows = await session.execute(
            select(
                CatalogOffer.chain,
                CatalogOffer.store_id,
                CatalogOffer.product_id,
                CatalogOffer.canonical_product_id,
            )
            .where(CatalogOffer.is_active.is_(True))
            .where(tuple_(CatalogOffer.chain, CatalogOffer.store_id, CatalogOffer.product_id).in_(batch))
        )
        active_ids.update(
            {
                (chain, store_id, product_id): canonical_id
                for chain, store_id, product_id, canonical_id in rows
            }
        )

    rows = []
    for product in products:
        key = (
            str(product.get("chain", "")),
            str(product.get("store_id", "")),
            str(product.get("product_id", "")),
        )
        canonical_product_id = active_ids.get(key)
        if canonical_product_id is None:
            continue
        rows.append(_offer_payload(product, canonical_product_id, refresh_run_id))

    await clear_staging_offers(session)
    if not rows:
        return 0

    for batch in _chunks(rows, 500):
        stmt = dialect_insert(CatalogOfferStaging).values(list(batch))
        stmt = stmt.on_conflict_do_update(
            index_elements=["chain", "store_id", "product_id"],
            set_={
                "price": stmt.excluded.price,
                "regular_price": stmt.excluded.regular_price,
                "sale_price": stmt.excluded.sale_price,
                "discount_percent": stmt.excluded.discount_percent,
                "price_per_base_unit": stmt.excluded.price_per_base_unit,
                "deal": stmt.excluded.deal,
                "scraped_at": stmt.excluded.scraped_at,
                "refresh_run_id": stmt.excluded.refresh_run_id,
                "updated_at": _now_utc(),
            },
        )
        await session.execute(stmt)
    return len(rows)


async def merge_deal_staging_into_active(session: AsyncSession) -> int:
    staged_rows = list((await session.execute(select(CatalogOfferStaging))).scalars())
    updated = 0
    now = _now_utc()
    for row in staged_rows:
        values = {column: getattr(row, column) for column in _DEAL_MERGE_COLUMNS}
        values["updated_at"] = now
        result = await session.execute(
            update(CatalogOffer)
            .where(CatalogOffer.is_active.is_(True))
            .where(CatalogOffer.chain == row.chain)
            .where(CatalogOffer.store_id == row.store_id)
            .where(CatalogOffer.product_id == row.product_id)
            .values(**values)
        )
        updated += int(result.rowcount or 0)
    return updated


async def deactivate_missing_offers_for_chain(
    session: AsyncSession,
    chain: str,
    refresh_run_id: int,
    store_ids: set[str],
    *,
    target_table: str = "active",
) -> None:
    offer_model = _offer_model(target_table)
    await session.execute(
        update(offer_model)
        .where(offer_model.chain == chain)
        .where(offer_model.store_id.in_(sorted(store_ids)))
        .where(offer_model.refresh_run_id != refresh_run_id)
        .values(is_active=False, updated_at=_now_utc())
    )


async def clear_staging_offers(session: AsyncSession) -> None:
    await session.execute(delete(CatalogOfferStaging))


async def clear_staging_generic_groups(session: AsyncSession) -> None:
    await session.execute(delete(GenericProductGroupMemberStaging))
    await session.execute(delete(GenericProductGroupStaging))


async def replace_active_offers_from_staging(session: AsyncSession) -> None:
    await session.execute(delete(CatalogOffer))
    projection = select(
        *(getattr(CatalogOfferStaging, column) for column in _OFFER_COPY_COLUMNS)
    )
    await session.execute(
        insert(CatalogOffer).from_select(_OFFER_COPY_COLUMNS, projection)
    )


async def replace_active_generic_groups_from_staging(session: AsyncSession) -> None:
    await session.execute(delete(GenericProductGroupMember))
    await session.execute(delete(GenericProductGroup))
    group_projection = select(
        *(getattr(GenericProductGroupStaging, column) for column in _GENERIC_GROUP_COPY_COLUMNS)
    )
    await session.execute(
        insert(GenericProductGroup).from_select(_GENERIC_GROUP_COPY_COLUMNS, group_projection)
    )
    member_projection = select(
        *(getattr(GenericProductGroupMemberStaging, column) for column in _GENERIC_MEMBER_COPY_COLUMNS)
    )
    await session.execute(
        insert(GenericProductGroupMember).from_select(_GENERIC_MEMBER_COPY_COLUMNS, member_projection)
    )


async def _build_generic_groups(
    session: AsyncSession,
    *,
    offer_model: type[CatalogOffer] | type[CatalogOfferStaging],
    group_model: type[GenericProductGroup] | type[GenericProductGroupStaging],
    member_model: type[GenericProductGroupMember] | type[GenericProductGroupMemberStaging],
) -> None:
    offers = list((await session.execute(select(offer_model))).scalars())
    group_meta: dict[str, dict[str, Any]] = {}
    members: list[dict[str, Any]] = []
    chain_sets: dict[str, set[str]] = defaultdict(set)
    offer_counts: dict[str, int] = defaultdict(int)

    for offer in offers:
        group = classify_generic_offer(offer)
        if not group:
            continue
        meta = group_meta.setdefault(
            group.key,
            {
                "key": group.key,
                "family": group.family,
                "label": group.label,
                "search_text": build_search_text(group.label, group.family),
                "cheapest_price": float(offer.price),
            },
        )
        meta["cheapest_price"] = min(float(meta["cheapest_price"]), float(offer.price))
        chain_sets[group.key].add(offer.chain)
        offer_counts[group.key] += 1
        members.append(
            {
                "group_key": group.key,
                "chain": offer.chain,
                "store_id": offer.store_id,
                "product_id": offer.product_id,
            }
        )

    if not group_meta:
        return
    group_rows = []
    for key, row in group_meta.items():
        group_rows.append(
            {
                **row,
                "offer_count": offer_counts[key],
                "chain_count": len(chain_sets[key]),
                "updated_at": _now_utc(),
            }
        )
    session.add_all(group_model(**row) for row in group_rows)
    await session.flush()

    if members:
        if database_backend == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dialect_insert
        elif database_backend == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as dialect_insert
        else:
            raise RuntimeError(f"Unsupported database backend: {database_backend}")
        for batch in _chunks(members, 1000):
            stmt = dialect_insert(member_model).values(list(batch))
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["group_key", "chain", "store_id", "product_id"]
            )
            await session.execute(stmt)


async def build_staging_generic_groups(session: AsyncSession) -> None:
    await clear_staging_generic_groups(session)
    await _build_generic_groups(
        session,
        offer_model=CatalogOfferStaging,
        group_model=GenericProductGroupStaging,
        member_model=GenericProductGroupMemberStaging,
    )


async def build_active_generic_groups(session: AsyncSession) -> None:
    await session.execute(delete(GenericProductGroupMember))
    await session.execute(delete(GenericProductGroup))
    await _build_generic_groups(
        session,
        offer_model=CatalogOffer,
        group_model=GenericProductGroup,
        member_model=GenericProductGroupMember,
    )


async def latest_refresh_run(
    session: AsyncSession,
    *,
    successful_only: bool = False,
    refresh_kind: str | None = None,
) -> CatalogRefreshRun | None:
    stmt = select(CatalogRefreshRun).order_by(CatalogRefreshRun.started_at.desc())
    if successful_only:
        stmt = stmt.where(CatalogRefreshRun.status == "done")
    if refresh_kind:
        stmt = stmt.where(CatalogRefreshRun.refresh_kind == refresh_kind)
    return (await session.execute(stmt.limit(1))).scalar_one_or_none()


async def catalog_is_fresh(
    session: AsyncSession,
    max_age_hours: float,
    *,
    refresh_kind: str | None = None,
) -> bool:
    refresh = await latest_refresh_run(
        session,
        successful_only=True,
        refresh_kind=refresh_kind,
    )
    if not refresh or not refresh.finished_at:
        return False
    finished_at = refresh.finished_at
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=timezone.utc)
    return finished_at >= _now_utc() - timedelta(hours=max_age_hours)


def serialize_refresh_run(run: CatalogRefreshRun | None) -> dict[str, Any] | None:
    if not run:
        return None
    return {
        "run_id": run.id,
        "source": run.source,
        "refresh_kind": run.refresh_kind,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "chains_scraped": list(run.chains_scraped or []),
        "chains_failed": list(run.chains_failed or []),
        "products_upserted": run.products_upserted or 0,
        "errors": list(run.errors or []),
    }


async def chain_product_counts(session: AsyncSession) -> dict[str, int]:
    rows = (
        await session.execute(
            select(CatalogOffer.chain, func.count(CatalogOffer.id))
            .where(CatalogOffer.is_active.is_(True))
            .where(CatalogOffer.chain.in_(ACTIVE_CHAIN_KEYS))
            .group_by(CatalogOffer.chain)
        )
    ).all()
    return {chain: count for chain, count in rows}


async def public_chain_statuses(session: AsyncSession) -> list[dict[str, Any]]:
    counts = await chain_product_counts(session)
    return [
        {
            "chain": chain.key,
            "label": chain.label,
            "enabled": chain.enabled,
            "status": chain.status,
            "unavailable_reason": chain.unavailable_reason,
            "accent": chain.accent,
            "product_count": counts.get(chain.key, 0) if chain.enabled else 0,
        }
        for chain in iter_public_chains()
    ]


def _search_filters(query: str) -> tuple[str, list[Any], Any]:
    normalized = normalize_text(query)
    if len(normalized) < 3:
        return normalized, [], None

    rank = case(
        (CanonicalProduct.normalized_name.like(f"{normalized}%"), 0),
        (CanonicalProduct.search_text.like(f"{normalized}%"), 1),
        else_=2,
    )
    tokens = [token for token in normalized.split(" ") if token]
    token_conditions = _token_conditions(tokens)

    if database_backend == "postgresql":
        similarity = func.similarity(CanonicalProduct.search_text, normalized)
        conditions = [CanonicalProduct.search_text.contains(normalized), similarity > 0.05]
        if token_conditions:
            conditions.append(and_(*token_conditions))
        condition = or_(*conditions)
        ordering = [rank, similarity.desc(), CanonicalProduct.id.asc()]
    else:
        conditions = [CanonicalProduct.search_text.contains(normalized)]
        if token_conditions:
            conditions.append(and_(*token_conditions))
        condition = or_(*conditions)
        ordering = [rank, CanonicalProduct.id.asc()]
    return normalized, ordering, condition


async def _fuzzy_search_products(
    session: AsyncSession,
    query: str,
    normalized: str,
    limit: int,
    offset: int,
    chain_keys: Sequence[str],
) -> dict[str, Any]:
    token_fragments: list[str] = []
    for token in (part for part in normalized.split(" ") if part):
        if len(token) >= 3:
            token_fragments.append(token[:3])
            token_fragments.append(token[:-1])
        if len(token) >= 2:
            token_fragments.append(token[:2])

    seen: set[str] = set()
    ordered_fragments: list[str] = []
    for fragment in token_fragments:
        if fragment and fragment not in seen:
            seen.add(fragment)
            ordered_fragments.append(fragment)

    candidate_filter = None
    if ordered_fragments:
        candidate_filter = or_(
            *[CanonicalProduct.search_text.contains(fragment) for fragment in ordered_fragments]
        )

    base_stmt = (
        select(CanonicalProduct)
        .where(_active_offer_exists(chain_keys))
        .where(func.length(CanonicalProduct.normalized_name) > 0)
        .where(candidate_filter if candidate_filter is not None else True)
        .order_by(CanonicalProduct.id.asc())
        .limit(_FUZZY_CANDIDATE_LIMIT)
    )
    candidates = list((await session.execute(base_stmt)).scalars().all())
    if not candidates:
        return {"query": query, "total": 0, "products": []}

    ranked: list[tuple[float, CanonicalProduct]] = []
    for candidate in candidates:
        score = _score_fuzzy_candidate(normalized, candidate.search_text)
        if score >= _FUZZY_SEARCH_THRESHOLD:
            ranked.append((score, candidate))

    if not ranked:
        return {"query": query, "total": 0, "products": []}

    ranked.sort(key=lambda item: (-item[0], item[1].id))
    total = len(ranked)
    window = ranked[offset : offset + limit]
    products = [product for _, product in window]
    best_offers, chain_counts = await _preview_offer_map(
        session,
        [product.id for product in products],
        chain_keys,
    )
    serialized = [
        serialize_product_preview(product, best_offers[product.id], chain_counts.get(product.id, 0))
        for product in products
        if product.id in best_offers
    ]
    return {"query": query, "total": total, "products": serialized}


def _active_offer_exists(chain_keys: Sequence[str] | None = None) -> Any:
    effective_chains = _resolve_chain_keys(chain_keys)
    if not effective_chains:
        return exists(select(1).where(False))
    return exists(
        select(1)
        .select_from(CatalogOffer)
        .where(CatalogOffer.canonical_product_id == CanonicalProduct.id)
        .where(CatalogOffer.is_active.is_(True))
        .where(CatalogOffer.chain.in_(effective_chains))
    )


async def _preview_offer_map(
    session: AsyncSession,
    product_ids: list[int],
    chain_keys: Sequence[str] | None = None,
) -> tuple[dict[int, CatalogOffer], dict[int, int]]:
    if not product_ids:
        return {}, {}
    effective_chains = _resolve_chain_keys(chain_keys)
    if not effective_chains:
        return {}, {}
    offers = (
        await session.execute(
            select(CatalogOffer)
            .where(CatalogOffer.canonical_product_id.in_(product_ids))
            .where(CatalogOffer.is_active.is_(True))
            .where(CatalogOffer.chain.in_(effective_chains))
            .order_by(CatalogOffer.canonical_product_id.asc(), CatalogOffer.price.asc())
        )
    ).scalars()

    best_offer_by_product: dict[int, CatalogOffer] = {}
    chain_sets: dict[int, set[str]] = defaultdict(set)
    for offer in offers:
        chain_sets[offer.canonical_product_id].add(offer.chain)
        if offer.canonical_product_id not in best_offer_by_product:
            best_offer_by_product[offer.canonical_product_id] = offer
    return best_offer_by_product, {
        product_id: len(chains) for product_id, chains in chain_sets.items()
    }


def _same_product_condition(product: CanonicalProduct) -> Any:
    conditions: list[Any] = [CanonicalProduct.id == product.id]
    if product.barcode:
        conditions.append(CanonicalProduct.barcode == product.barcode)
    if product.match_key:
        conditions.append(CanonicalProduct.match_key == product.match_key)
    if product.normalized_name:
        unit_qty_condition = (
            CanonicalProduct.unit_qty_si == product.unit_qty_si
            if product.unit_qty_si is not None
            else CanonicalProduct.unit_qty_si.is_(None)
        )
        unit_dimension_condition = (
            CanonicalProduct.unit_dimension == product.unit_dimension
            if product.unit_dimension is not None
            else CanonicalProduct.unit_dimension.is_(None)
        )
        conditions.append(
            and_(
                CanonicalProduct.normalized_name == product.normalized_name,
                CanonicalProduct.normalized_brand == product.normalized_brand,
                unit_qty_condition,
                unit_dimension_condition,
            )
        )
    return or_(*conditions)


async def _equivalent_product_ids(
    session: AsyncSession,
    products: Sequence[CanonicalProduct],
) -> dict[int, set[int]]:
    equivalents: dict[int, set[int]] = {}
    for product in products:
        ids = set(
            (
                await session.execute(
                    select(CanonicalProduct.id).where(_same_product_condition(product))
                )
            ).scalars()
        )
        ids.add(product.id)
        equivalents[product.id] = ids
        if _catalog_debug_enabled():
            logger.debug(
                "equivalent_products source_id=%s name=%r barcode=%r match_key=%r equivalent_ids=%s",
                product.id,
                product.display_name,
                product.barcode,
                product.match_key,
                sorted(ids),
            )
    return equivalents


def serialize_product_preview(
    product: CanonicalProduct,
    offer: CatalogOffer,
    chain_count: int,
) -> dict[str, Any]:
    chain = get_chain_definition(offer.chain)
    return {
        "id": product.id,
        "name": product.display_name,
        "brand": product.brand,
        "manufacturer": product.manufacturer,
        "barcode": product.barcode,
        "image_url": product.image_url or offer.image_url,
        "unit_description": product.unit_description,
        "unit_of_measure": product.unit_of_measure,
        "unit_qty": product.unit_qty,
        "unit_qty_si": product.unit_qty_si,
        "unit_dimension": product.unit_dimension,
        "is_weighable": bool(offer.is_weighable),
        "cheapest_price": round(float(offer.price), 2),
        "cheapest_chain": offer.chain,
        "cheapest_chain_label": chain.label,
        "cheapest_store_name": offer.store_name,
        "chain_count": chain_count,
        "has_deal": bool((offer.deal or {}).get("has_deal")),
    }


def serialize_generic_group(group: GenericProductGroup | GenericProductGroupStaging) -> dict[str, Any]:
    return {
        "key": group.key,
        "label": group.label,
        "family": group.family,
        "offer_count": group.offer_count,
        "chain_count": group.chain_count,
        "cheapest_price": round(float(group.cheapest_price), 2) if group.cheapest_price is not None else None,
    }


def serialize_chain_offer(offer: CatalogOffer, distance_km: float | None = None) -> dict[str, Any]:
    return {
        "id": offer.id,
        "chain": offer.chain,
        "chain_label": get_chain_definition(offer.chain).label,
        "store_id": offer.store_id,
        "store_name": offer.store_name,
        "product_id": offer.product_id,
        "name": offer.name,
        "price": round(float(offer.price), 2),
        "regular_price": round(float(offer.regular_price), 2),
        "sale_price": offer.sale_price,
        "discount_percent": offer.discount_percent,
        "is_weighable": bool(offer.is_weighable),
        "unit_description": offer.unit_description,
        "unit_of_measure": offer.unit_of_measure,
        "unit_qty": offer.unit_qty,
        "unit_qty_si": offer.unit_qty_si,
        "unit_dimension": offer.unit_dimension,
        "price_per_base_unit": offer.price_per_base_unit,
        "brand": offer.brand,
        "image_url": offer.image_url,
        "product_url": build_product_url(offer.chain, offer.product_id, offer.barcode, offer.name),
        "deal": offer.deal,
        "scraped_at": offer.scraped_at,
        "distance_km": round(distance_km, 1) if distance_km is not None else None,
    }


def _user_location(user: User | None) -> tuple[float, float] | None:
    if user is None or user.location_lat is None or user.location_lng is None:
        return None
    return (float(user.location_lat), float(user.location_lng))


def _distance_sort_value(distance_km: float | None) -> float:
    return distance_km if distance_km is not None else inf


async def _store_distances(
    session: AsyncSession,
    user: User | None,
    store_keys: Iterable[tuple[str, str]],
) -> dict[tuple[str, str], float | None]:
    location = _user_location(user)
    keys = sorted({(chain, store_id) for chain, store_id in store_keys})
    if location is None or not keys:
        return {key: None for key in keys}
    rows = (
        await session.execute(
            select(StoreBranch)
            .where(tuple_(StoreBranch.chain, StoreBranch.store_id).in_(keys))
        )
    ).scalars()
    branch_by_key = {(branch.chain, branch.store_id): branch for branch in rows}
    distances: dict[tuple[str, str], float | None] = {}
    for key in keys:
        branch = branch_by_key.get(key)
        distances[key] = haversine_km(
            location[0],
            location[1],
            branch.lat if branch else None,
            branch.lng if branch else None,
        )
    return distances


async def search_generic_groups(
    session: AsyncSession,
    query: str,
    limit: int = 6,
    chain_filter: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    normalized = normalize_text(query)
    if len(normalized) < 3:
        return []
    tokens = [token for token in normalized.split() if len(token) >= 2]
    if not tokens:
        return []
    chain_keys = _resolve_chain_keys(chain_filter)
    if not chain_keys:
        return []
    conditions = _generic_token_conditions(tokens)
    active_chain_count = func.count(func.distinct(GenericProductGroupMember.chain))
    active_offer_count = func.count(GenericProductGroupMember.id)
    cheapest_price = func.min(CatalogOffer.price)
    stmt = (
        select(
            GenericProductGroup,
            active_chain_count.label("active_chain_count"),
            active_offer_count.label("active_offer_count"),
            cheapest_price.label("active_cheapest_price"),
        )
        .join(
            GenericProductGroupMember,
            GenericProductGroupMember.group_key == GenericProductGroup.key,
        )
        .join(
            CatalogOffer,
            and_(
                CatalogOffer.chain == GenericProductGroupMember.chain,
                CatalogOffer.store_id == GenericProductGroupMember.store_id,
                CatalogOffer.product_id == GenericProductGroupMember.product_id,
            ),
        )
        .where(and_(*conditions))
        .where(CatalogOffer.is_active.is_(True))
        .where(CatalogOffer.chain.in_(chain_keys))
        .group_by(
            GenericProductGroup.key,
            GenericProductGroup.family,
            GenericProductGroup.label,
            GenericProductGroup.search_text,
            GenericProductGroup.offer_count,
            GenericProductGroup.chain_count,
            GenericProductGroup.cheapest_price,
            GenericProductGroup.created_at,
            GenericProductGroup.updated_at,
        )
        .having(active_chain_count >= 2)
        .order_by(
            active_chain_count.desc(),
            active_offer_count.desc(),
            cheapest_price.asc(),
            GenericProductGroup.key.asc(),
        )
        .limit(limit)
    )
    groups = []
    for group, chain_count, offer_count, price in (await session.execute(stmt)).all():
        row = serialize_generic_group(group)
        row["chain_count"] = int(chain_count or 0)
        row["offer_count"] = int(offer_count or 0)
        row["cheapest_price"] = round(float(price), 2) if price is not None else None
        groups.append(row)
    return groups


async def load_generic_group_detail(
    session: AsyncSession,
    group_key: str,
    chain_filter: Sequence[str] | None = None,
    user: User | None = None,
) -> dict[str, Any] | None:
    group = await session.get(GenericProductGroup, group_key)
    if not group:
        return None
    chain_keys = _resolve_chain_keys(chain_filter)
    if not chain_keys:
        return None

    rows = list(
        (
            await session.execute(
                select(CatalogOffer)
                .join(
                    GenericProductGroupMember,
                    and_(
                        GenericProductGroupMember.chain == CatalogOffer.chain,
                        GenericProductGroupMember.store_id == CatalogOffer.store_id,
                        GenericProductGroupMember.product_id == CatalogOffer.product_id,
                    ),
                )
                .where(GenericProductGroupMember.group_key == group_key)
                .where(CatalogOffer.is_active.is_(True))
                .where(CatalogOffer.chain.in_(chain_keys))
                .order_by(CatalogOffer.chain.asc(), CatalogOffer.price.asc(), CatalogOffer.id.asc())
            )
        ).scalars()
    )

    distances = await _store_distances(session, user, ((offer.chain, offer.store_id) for offer in rows))
    best_offer_by_chain: dict[str, CatalogOffer] = {}
    for offer in rows:
        current = best_offer_by_chain.get(offer.chain)
        offer_key = (offer.price, _distance_sort_value(distances.get((offer.chain, offer.store_id))), offer.id)
        current_key = (
            current.price,
            _distance_sort_value(distances.get((current.chain, current.store_id))),
            current.id,
        ) if current else None
        if current is None or offer_key < current_key:
            best_offer_by_chain[offer.chain] = offer

    chain_offers = sorted(
        best_offer_by_chain.values(),
        key=lambda offer: (
            offer.price,
            _distance_sort_value(distances.get((offer.chain, offer.store_id))),
            offer.id,
        ),
    )
    if not chain_offers:
        return None

    detail = serialize_generic_group(group)
    detail["chain_count"] = len(chain_offers)
    detail["offer_count"] = len(rows)
    detail["cheapest_price"] = round(float(chain_offers[0].price), 2)
    detail["offers"] = [
        serialize_chain_offer(offer, distances.get((offer.chain, offer.store_id)))
        for offer in chain_offers
    ]
    return detail


async def suggest_products(
    session: AsyncSession,
    query: str,
    limit: int = 8,
    chain_filter: Sequence[str] | None = None,
) -> dict[str, Any]:
    normalized, ordering, condition = _search_filters(query)
    if condition is None:
        return {"query": query, "total": 0, "items": []}

    chain_keys = _resolve_chain_keys(chain_filter)
    if not chain_keys:
        return {"query": query, "total": 0, "items": []}

    stmt = (
        select(CanonicalProduct)
        .where(_active_offer_exists(chain_keys))
        .where(condition)
        .order_by(*ordering)
        .limit(limit)
    )
    products = list((await session.execute(stmt)).scalars().all())
    best_offers, chain_counts = await _preview_offer_map(
        session,
        [product.id for product in products],
        chain_keys,
    )

    items = []
    for product in products:
        offer = best_offers.get(product.id)
        if not offer:
            continue
        preview = serialize_product_preview(product, offer, chain_counts.get(product.id, 0))
        items.append(
            {
                "id": preview["id"],
                "name": preview["name"],
                "brand": preview["brand"],
                "unit_description": preview["unit_description"],
                "image_url": preview["image_url"],
                "cheapest_price": preview["cheapest_price"],
                "cheapest_chain": preview["cheapest_chain"],
                "cheapest_chain_label": preview["cheapest_chain_label"],
            }
        )
    return {"query": query, "total": len(items), "items": items}


async def search_products(
    session: AsyncSession,
    query: str,
    limit: int = 20,
    offset: int = 0,
    chain_filter: Sequence[str] | None = None,
) -> dict[str, Any]:
    normalized, ordering, condition = _search_filters(query)
    if condition is None:
        return {"query": query, "total": 0, "products": [], "generic_groups": []}

    chain_keys = _resolve_chain_keys(chain_filter)
    if not chain_keys:
        return {"query": query, "total": 0, "products": [], "generic_groups": []}

    if _catalog_debug_enabled():
        logger.debug(
            "search_start query=%r normalized=%r limit=%s offset=%s chains=%s",
            query,
            normalized,
            limit,
            offset,
            list(chain_keys),
        )

    generic_groups = await search_generic_groups(session, query, chain_filter=chain_keys)

    stmt = select(CanonicalProduct).where(_active_offer_exists(chain_keys)).where(condition)
    count = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()

    if _catalog_debug_enabled():
        logger.debug(
            "search_exact_count query=%r count=%s generic_groups=%s",
            query,
            int(count or 0),
            [group["key"] for group in generic_groups],
        )

    if not count:
        result = await _fuzzy_search_products(
            session,
            query,
            normalized,
            limit,
            offset,
            chain_keys,
        )
        result["generic_groups"] = generic_groups
        if _catalog_debug_enabled():
            logger.debug(
                "search_fuzzy_result query=%r total=%s product_ids=%s",
                query,
                result.get("total"),
                [product["id"] for product in result.get("products", [])],
            )
        return result

    products = list(
        (
            await session.execute(
                stmt.order_by(*ordering).offset(offset).limit(limit)
            )
        ).scalars()
    )
    best_offers, chain_counts = await _preview_offer_map(
        session,
        [product.id for product in products],
        chain_keys,
    )
    serialized = [
        serialize_product_preview(product, best_offers[product.id], chain_counts.get(product.id, 0))
        for product in products
        if product.id in best_offers
    ]
    if _catalog_debug_enabled():
        logger.debug(
            "search_result query=%r total=%s returned=%s product_ids=%s chain_counts=%s",
            query,
            int(count or 0),
            len(serialized),
            [product["id"] for product in serialized],
            {product.id: chain_counts.get(product.id, 0) for product in products},
        )
    return {"query": query, "total": int(count or 0), "products": serialized, "generic_groups": generic_groups}


async def load_product_detail(
    session: AsyncSession,
    product_id: int,
    user: User | None = None,
) -> dict[str, Any] | None:
    product = await session.get(CanonicalProduct, product_id)
    if not product:
        return None

    equivalent_ids = (await _equivalent_product_ids(session, [product]))[product.id]
    if _catalog_debug_enabled():
        logger.debug(
            "product_detail_start product_id=%s name=%r equivalent_ids=%s",
            product_id,
            product.display_name,
            sorted(equivalent_ids),
        )
    offers = list(
        (
            await session.execute(
                select(CatalogOffer)
                .where(CatalogOffer.canonical_product_id.in_(equivalent_ids))
                .where(CatalogOffer.is_active.is_(True))
                .where(CatalogOffer.chain.in_(ACTIVE_CHAIN_KEYS))
                .order_by(CatalogOffer.chain.asc(), CatalogOffer.price.asc())
            )
        ).scalars()
    )
    if not offers:
        if _catalog_debug_enabled():
            logger.debug("product_detail_no_offers product_id=%s", product_id)
        return None

    if _catalog_debug_enabled():
        logger.debug(
            "product_detail_offers product_id=%s raw_offers=%s chains=%s stores=%s",
            product_id,
            len(offers),
            sorted({offer.chain for offer in offers}),
            sorted({f"{offer.chain}:{offer.store_id}" for offer in offers}),
        )

    distances = await _store_distances(session, user, ((offer.chain, offer.store_id) for offer in offers))
    best_offer_by_chain: dict[str, CatalogOffer] = {}
    for offer in offers:
        current = best_offer_by_chain.get(offer.chain)
        offer_key = (
            offer.price,
            _distance_sort_value(distances.get((offer.chain, offer.store_id))),
            offer.id,
        )
        current_key = (
            current.price,
            _distance_sort_value(distances.get((current.chain, current.store_id))),
            current.id,
        ) if current else None
        if current is None or offer_key < current_key:
            best_offer_by_chain[offer.chain] = offer

    chain_offers = sorted(
        best_offer_by_chain.values(),
        key=lambda offer: (
            offer.price,
            _distance_sort_value(distances.get((offer.chain, offer.store_id))),
            offer.id,
        ),
    )
    detail_offers = [
        serialize_chain_offer(offer, distances.get((offer.chain, offer.store_id)))
        for offer in chain_offers
    ]
    cheapest_offer = chain_offers[0]
    return {
        "id": product.id,
        "name": product.display_name,
        "brand": product.brand,
        "manufacturer": product.manufacturer,
        "barcode": product.barcode,
        "image_url": product.image_url or cheapest_offer.image_url,
        "unit_description": product.unit_description,
        "unit_of_measure": product.unit_of_measure,
        "unit_qty": product.unit_qty,
        "unit_qty_si": product.unit_qty_si,
        "unit_dimension": product.unit_dimension,
        "is_weighable": any(bool(offer.is_weighable) for offer in chain_offers),
        "cheapest_price": round(float(cheapest_offer.price), 2),
        "chain_count": len(chain_offers),
        "offers": detail_offers,
    }


async def load_product_chain_offers(
    session: AsyncSession,
    product_id: int,
    user: User | None = None,
) -> list[dict[str, Any]]:
    detail = await load_product_detail(session, product_id, user)
    if not detail:
        return []
    return detail["offers"]


async def get_user_lists(session: AsyncSession, user_id: int) -> list[ShoppingList]:
    return list(
        (
            await session.execute(
                select(ShoppingList)
                .where(ShoppingList.user_id == user_id)
                .order_by(ShoppingList.updated_at.desc())
            )
        ).scalars()
    )


async def get_user_list(
    session: AsyncSession,
    user_id: int,
    shopping_list_id: int,
) -> ShoppingList | None:
    stmt = select(ShoppingList).where(
        ShoppingList.id == shopping_list_id,
        ShoppingList.user_id == user_id,
    )
    shopping_list = (await session.execute(stmt)).scalar_one_or_none()
    return shopping_list


async def get_user_list_with_items(
    session: AsyncSession,
    user_id: int,
    shopping_list_id: int,
) -> ShoppingList | None:
    stmt = (
        select(ShoppingList)
        .where(ShoppingList.id == shopping_list_id, ShoppingList.user_id == user_id)
        .options(
            selectinload(ShoppingList.items).selectinload(ShoppingListItem.canonical_product)
        )
    )
    shopping_list = (await session.execute(stmt)).scalar_one_or_none()
    return shopping_list


async def serialize_shopping_list_summary(
    session: AsyncSession,
    shopping_list: ShoppingList,
) -> dict[str, Any]:
    items = list(
        (
            await session.execute(
                select(ShoppingListItem).where(ShoppingListItem.shopping_list_id == shopping_list.id)
            )
        ).scalars()
    )
    return {
        "id": shopping_list.id,
        "name": shopping_list.name,
        "item_count": len(items),
        "total_quantity": round(sum(float(item.quantity) for item in items), 3),
        "updated_at": shopping_list.updated_at,
    }


async def serialize_shopping_list_detail(
    session: AsyncSession,
    shopping_list: ShoppingList,
) -> dict[str, Any]:
    items = list(
        (
            await session.execute(
                select(ShoppingListItem)
                .where(ShoppingListItem.shopping_list_id == shopping_list.id)
                .order_by(ShoppingListItem.created_at.asc())
            )
        ).scalars()
    )
    for item in items:
        if item.canonical_product_id:
            await session.refresh(item, attribute_names=["canonical_product"])

    exact_ids = [item.canonical_product_id for item in items if item.canonical_product_id]
    generic_keys = [item.generic_group_key for item in items if item.generic_group_key]
    previews = await _preview_offer_map(session, exact_ids)
    best_offers, chain_counts = previews
    generic_groups = {}
    if generic_keys:
        rows = (await session.execute(select(GenericProductGroup).where(GenericProductGroup.key.in_(generic_keys)))).scalars()
        generic_groups = {group.key: group for group in rows}

    serialized_items = []
    for item in items:
        if item.generic_group_key:
            group = generic_groups.get(item.generic_group_key)
            if not group:
                continue
            serialized_items.append(
                {
                    "id": item.id,
                    "quantity": round(float(item.quantity), 3),
                    "product": None,
                    "generic_group": serialize_generic_group(group),
                }
            )
            continue
        product = item.canonical_product
        offer = best_offers.get(product.id)
        if not offer:
            continue
        serialized_items.append(
            {
                "id": item.id,
                "quantity": round(float(item.quantity), 3),
                "product": serialize_product_preview(
                    product,
                    offer,
                    chain_counts.get(product.id, 0),
                ),
                "generic_group": None,
            }
        )

    return {
        "id": shopping_list.id,
        "name": shopping_list.name,
        "item_count": len(serialized_items),
        "total_quantity": round(sum(item["quantity"] for item in serialized_items), 3),
        "updated_at": shopping_list.updated_at,
        "items": serialized_items,
    }


_AMOUNT_FULFILLMENT_FAMILIES = {"salmon", "chicken", "ground_beef"}


def _compatible_amount_group_condition(group: GenericProductGroup) -> Any:
    if group.family not in _AMOUNT_FULFILLMENT_FAMILIES:
        return GenericProductGroup.key == group.key
    freshness = "frozen" if "frozen" in group.key else "fresh" if "fresh" in group.key else None
    conditions: list[Any] = [GenericProductGroup.family == group.family]
    if freshness:
        conditions.append(GenericProductGroup.key.contains(freshness))
    return and_(*conditions)


def _format_si_size_label(qty_si: float | None, dimension: str | None) -> str | None:
    if not qty_si or qty_si <= 0:
        return None
    value = float(qty_si)
    if dimension == "mass":
        if abs(value % 1000.0) < 1e-6:
            return f"{value / 1000.0:g} ק״ג"
        return f"{value:g} גרם"
    if dimension == "volume":
        if abs(value % 1000.0) < 1e-6:
            return f"{value / 1000.0:g} ל׳"
        return f"{value:g} מ״ל"
    if dimension == "count":
        return f"{value:g} יח׳"
    return None


def _amount_request_si(family: str | None, quantity: float) -> float | None:
    if family not in _AMOUNT_FULFILLMENT_FAMILIES:
        return None
    return float(quantity) * 1000.0


def _offer_total_for_package_count(offer: CatalogOffer, package_count: int) -> tuple[float, bool, str | None]:
    deal = offer.deal or {}
    unit_price = float(offer.price)
    regular_unit_price = float(offer.regular_price)
    total = unit_price * package_count
    deal_applied = unit_price < regular_unit_price
    deal_description = deal.get("deal_description") if deal_applied else None

    if (
        deal.get("has_deal")
        and deal.get("deal_type") == "multi_buy"
        and deal.get("deal_min_qty")
        and deal.get("deal_price") is not None
    ):
        minimum_qty = int(deal["deal_min_qty"])
        deal_total = float(deal["deal_price"])
        if minimum_qty > 0:
            bundles = package_count // minimum_qty
            remainder = package_count % minimum_qty
            deal_candidate = bundles * deal_total + remainder * unit_price
            if bundles > 0:
                total = deal_candidate
                deal_applied = True
                deal_description = deal.get("deal_description")

    return total, deal_applied, deal_description


def _line_totals_for_offer(
    offer: CatalogOffer,
    quantity: float,
    *,
    fulfillment_family: str | None = None,
) -> dict[str, Any]:
    quantity_value = float(quantity)
    unit_price = float(offer.price)
    regular_unit_price = float(offer.regular_price)
    request_qty_si = _amount_request_si(fulfillment_family, quantity_value)
    if request_qty_si is None and offer.is_weighable and offer.unit_dimension in {"mass", "volume"}:
        request_qty_si = quantity_value * 1000.0

    if request_qty_si is not None and offer.unit_dimension == "mass" and offer.unit_qty_si and offer.unit_qty_si > 0:
        if offer.is_weighable:
            ratio = request_qty_si / float(offer.unit_qty_si)
            total = unit_price * ratio
            regular_total = regular_unit_price * ratio
            deal_applied = unit_price < regular_unit_price
            deal_description = (offer.deal or {}).get("deal_description") if deal_applied else None
            purchased_qty_si = request_qty_si
            package_count = None
        else:
            package_count = max(1, ceil(request_qty_si / float(offer.unit_qty_si)))
            purchased_qty_si = package_count * float(offer.unit_qty_si)
            total, deal_applied, deal_description = _offer_total_for_package_count(offer, package_count)
            regular_total = regular_unit_price * package_count

        purchased_quantity = purchased_qty_si / 1000.0
        unit_price_effective = total / quantity_value if quantity_value > 0 else unit_price
        regular_unit_price_effective = (
            regular_total / quantity_value if quantity_value > 0 else regular_unit_price
        )
        package_size_label = _format_si_size_label(offer.unit_qty_si, offer.unit_dimension)
        if offer.is_weighable:
            fulfillment_description = f"{quantity_value:g} ק״ג במשקל"
        elif package_count is not None and package_size_label:
            fulfillment_description = f"{package_count} × {package_size_label}"
        else:
            fulfillment_description = None

        return {
            "unit_price": round(unit_price_effective, 2),
            "regular_unit_price": round(regular_unit_price_effective, 2),
            "line_total": round(total, 2),
            "regular_line_total": round(regular_total, 2),
            "purchased_quantity": round(purchased_quantity, 3),
            "purchased_quantity_si": round(purchased_qty_si, 3),
            "package_count": package_count,
            "package_size_label": package_size_label,
            "fulfillment_description": fulfillment_description,
            "overbuy_si": max(0.0, purchased_qty_si - request_qty_si),
            "deal_applied": deal_applied,
            "deal_description": deal_description,
        }

    quantity_is_integer = abs(quantity_value - int(quantity_value)) < 1e-6
    if quantity_is_integer:
        total, deal_applied, deal_description = _offer_total_for_package_count(
            offer,
            max(1, int(quantity_value)),
        )
    else:
        total = unit_price * quantity_value
        deal_applied = unit_price < regular_unit_price
        deal_description = None
    regular_total = regular_unit_price * quantity_value
    if deal_applied and deal_description is None:
        deal_description = (offer.deal or {}).get("deal_description")

    return {
        "unit_price": round(unit_price, 2),
        "regular_unit_price": round(regular_unit_price, 2),
        "line_total": round(total, 2),
        "regular_line_total": round(regular_total, 2),
        "purchased_quantity": round(quantity_value, 3),
        "purchased_quantity_si": offer.unit_qty_si,
        "package_count": int(quantity_value) if quantity_is_integer else None,
        "package_size_label": _format_si_size_label(offer.unit_qty_si, offer.unit_dimension),
        "fulfillment_description": None,
        "overbuy_si": 0.0,
        "deal_applied": deal_applied,
        "deal_description": deal_description,
    }


def _choose_best_offer_for_quantity(
    offers: list[CatalogOffer],
    quantity: float,
    *,
    fulfillment_family: str | None = None,
) -> tuple[CatalogOffer, dict[str, Any]]:
    best_offer = offers[0]
    best_meta = _line_totals_for_offer(best_offer, quantity, fulfillment_family=fulfillment_family)
    best_key = (
        best_meta["line_total"],
        best_meta.get("overbuy_si") or 0,
        best_meta["unit_price"],
        best_offer.id,
    )
    for offer in offers[1:]:
        meta = _line_totals_for_offer(offer, quantity, fulfillment_family=fulfillment_family)
        key = (
            meta["line_total"],
            meta.get("overbuy_si") or 0,
            meta["unit_price"],
            offer.id,
        )
        if key < best_key:
            best_offer = offer
            best_meta = meta
            best_key = key
    return best_offer, best_meta


async def compare_shopping_list(
    session: AsyncSession,
    shopping_list: ShoppingList,
    user: User | None = None,
) -> dict[str, Any]:
    items = list(
        (
            await session.execute(
                select(ShoppingListItem)
                .where(ShoppingListItem.shopping_list_id == shopping_list.id)
                .order_by(ShoppingListItem.created_at.asc())
            )
        ).scalars()
    )
    for item in items:
        if item.canonical_product_id:
            await session.refresh(item, attribute_names=["canonical_product"])

    if not items:
        return {
            "list_id": shopping_list.id,
            "list_name": shopping_list.name,
            "item_count": 0,
            "total_quantity": 0,
            "chains": [],
        }

    exact_items = [item for item in items if item.canonical_product_id]
    generic_keys = [item.generic_group_key for item in items if item.generic_group_key]
    products_by_id = {
        item.canonical_product_id: item.canonical_product
        for item in exact_items
        if item.canonical_product_id and item.canonical_product
    }
    equivalent_ids_by_product = await _equivalent_product_ids(
        session,
        list(products_by_id.values()),
    )
    source_ids_by_equivalent_id: dict[int, set[int]] = defaultdict(set)
    for source_id, equivalent_ids in equivalent_ids_by_product.items():
        for equivalent_id in equivalent_ids:
            source_ids_by_equivalent_id[equivalent_id].add(source_id)
    offer_product_ids = sorted(source_ids_by_equivalent_id)
    if _catalog_debug_enabled():
        logger.debug(
            "compare_start list_id=%s item_count=%s exact_item_ids=%s generic_keys=%s equivalent_offer_ids=%s",
            shopping_list.id,
            len(items),
            [item.canonical_product_id for item in exact_items],
            generic_keys,
            offer_product_ids,
        )
    offers = list(
        (
            await session.execute(
                select(CatalogOffer)
                .where(
                    CatalogOffer.canonical_product_id.in_(offer_product_ids)
                    if offer_product_ids
                    else False
                )
                .where(CatalogOffer.is_active.is_(True))
                .where(CatalogOffer.chain.in_(ACTIVE_CHAIN_KEYS))
                .order_by(
                    CatalogOffer.chain.asc(),
                    CatalogOffer.store_id.asc(),
                    CatalogOffer.canonical_product_id.asc(),
                    CatalogOffer.price.asc(),
                )
            )
        ).scalars()
    )

    offers_by_store_product: dict[tuple[str, str, int], list[CatalogOffer]] = defaultdict(list)
    offers_by_store_group: dict[tuple[str, str, str], list[CatalogOffer]] = defaultdict(list)
    stores_by_chain: dict[str, dict[str, str]] = defaultdict(dict)
    for offer in offers:
        source_ids = source_ids_by_equivalent_id.get(
            offer.canonical_product_id,
            {offer.canonical_product_id},
        )
        for source_id in source_ids:
            offers_by_store_product[(offer.chain, offer.store_id, source_id)].append(offer)
        stores_by_chain[offer.chain][offer.store_id] = offer.store_name

    if _catalog_debug_enabled():
        logger.debug(
            "compare_exact_offers list_id=%s offers=%s candidate_stores=%s chains=%s",
            shopping_list.id,
            len(offers),
            sum(len(stores) for stores in stores_by_chain.values()),
            {chain: sorted(stores) for chain, stores in stores_by_chain.items()},
        )

    generic_groups: dict[str, GenericProductGroup] = {}
    compatible_group_keys_by_item_key: dict[str, set[str]] = defaultdict(set)
    if generic_keys:
        groups = (await session.execute(select(GenericProductGroup).where(GenericProductGroup.key.in_(generic_keys)))).scalars()
        generic_groups = {group.key: group for group in groups}
        compatible_conditions = [
            _compatible_amount_group_condition(group)
            for group in generic_groups.values()
        ]
        compatible_groups = list(
            (
                await session.execute(
                    select(GenericProductGroup).where(or_(*compatible_conditions))
                    if compatible_conditions
                    else select(GenericProductGroup).where(False)
                )
            ).scalars()
        )
        for item_key, group in generic_groups.items():
            if group.family not in _AMOUNT_FULFILLMENT_FAMILIES:
                compatible_group_keys_by_item_key[item_key].add(item_key)
                continue
            for candidate_group in compatible_groups:
                freshness_matches = (
                    ("frozen" in group.key and "frozen" in candidate_group.key)
                    or ("fresh" in group.key and "fresh" in candidate_group.key)
                    or ("frozen" not in group.key and "fresh" not in group.key)
                )
                if group.family == candidate_group.family and freshness_matches:
                    compatible_group_keys_by_item_key[item_key].add(candidate_group.key)
            compatible_group_keys_by_item_key[item_key].add(item_key)
        candidate_group_keys = sorted(
            {key for keys in compatible_group_keys_by_item_key.values() for key in keys}
        )
        rows = (
            await session.execute(
                select(GenericProductGroupMember.group_key, CatalogOffer)
                .join(
                    CatalogOffer,
                    and_(
                        CatalogOffer.chain == GenericProductGroupMember.chain,
                        CatalogOffer.store_id == GenericProductGroupMember.store_id,
                        CatalogOffer.product_id == GenericProductGroupMember.product_id,
                    ),
                )
                .where(GenericProductGroupMember.group_key.in_(candidate_group_keys))
                .where(CatalogOffer.is_active.is_(True))
                .where(CatalogOffer.chain.in_(ACTIVE_CHAIN_KEYS))
            )
        ).all()
        for group_key, offer in rows:
            for item_key, compatible_keys in compatible_group_keys_by_item_key.items():
                if group_key in compatible_keys:
                    offers_by_store_group[(offer.chain, offer.store_id, item_key)].append(offer)
            stores_by_chain[offer.chain][offer.store_id] = offer.store_name

        if _catalog_debug_enabled():
            logger.debug(
                "compare_generic_offers list_id=%s generic_keys=%s rows=%s candidate_stores=%s",
                shopping_list.id,
                generic_keys,
                len(rows),
                sum(len(stores) for stores in stores_by_chain.values()),
            )

    store_distances = await _store_distances(
        session,
        user,
        (
            (chain_key, store_id)
            for chain_key, stores in stores_by_chain.items()
            for store_id in stores
        ),
    )
    chain_results: list[dict[str, Any]] = []
    for chain_key in ACTIVE_CHAIN_KEYS:
        stores = stores_by_chain.get(chain_key)
        if not stores:
            continue
        best_store_result: dict[str, Any] | None = None
        best_store_key = (inf, inf, inf, "")
        chain_label = get_chain_definition(chain_key).label

        for store_id, store_name in stores.items():
            distance_km = store_distances.get((chain_key, store_id))
            total_price = 0.0
            regular_total_price = 0.0
            missing_products: list[str] = []
            applied_deals_count = 0
            line_items: list[dict[str, Any]] = []

            for item in items:
                if item.generic_group_key:
                    group = generic_groups.get(item.generic_group_key)
                    product_name = group.label if group else item.generic_group_key
                    product_id = None
                    image_url = None
                    candidates = offers_by_store_group.get(
                        (chain_key, store_id, item.generic_group_key),
                        [],
                    )
                else:
                    product = item.canonical_product
                    product_name = product.display_name
                    product_id = product.id
                    image_url = product.image_url
                    candidates = offers_by_store_product.get(
                        (chain_key, store_id, item.canonical_product_id),
                        [],
                    )
                if not candidates:
                    missing_products.append(product_name)
                    line_items.append(
                        {
                            "list_item_id": item.id,
                            "canonical_product_id": product_id,
                            "generic_group_key": item.generic_group_key,
                            "product_name": product_name,
                            "quantity": round(float(item.quantity), 3),
                            "matched_name": None,
                            "unit_price": None,
                            "regular_unit_price": None,
                            "line_total": None,
                            "regular_line_total": None,
                            "purchased_quantity": None,
                            "purchased_quantity_si": None,
                            "package_count": None,
                            "package_size_label": None,
                            "fulfillment_description": None,
                            "deal_applied": False,
                            "deal_description": None,
                            "image_url": image_url,
                            "product_url": None,
                            "found": False,
                        }
                    )
                    continue

                fulfillment_family = (
                    generic_groups.get(item.generic_group_key).family
                    if item.generic_group_key and generic_groups.get(item.generic_group_key)
                    else None
                )
                offer, totals = _choose_best_offer_for_quantity(
                    candidates,
                    item.quantity,
                    fulfillment_family=fulfillment_family,
                )
                total_price += totals["line_total"]
                regular_total_price += totals["regular_line_total"]
                if totals["deal_applied"]:
                    applied_deals_count += 1
                line_items.append(
                    {
                        "list_item_id": item.id,
                        "canonical_product_id": product_id,
                        "generic_group_key": item.generic_group_key,
                        "product_name": product_name,
                        "quantity": round(float(item.quantity), 3),
                        "matched_name": offer.name,
                        "unit_price": totals["unit_price"],
                        "regular_unit_price": totals["regular_unit_price"],
                        "line_total": totals["line_total"],
                        "regular_line_total": totals["regular_line_total"],
                        "purchased_quantity": totals["purchased_quantity"],
                        "purchased_quantity_si": totals["purchased_quantity_si"],
                        "package_count": totals["package_count"],
                        "package_size_label": totals["package_size_label"],
                        "fulfillment_description": totals["fulfillment_description"],
                        "deal_applied": totals["deal_applied"],
                        "deal_description": totals["deal_description"],
                        "image_url": offer.image_url or image_url,
                        "product_url": build_product_url(offer.chain, offer.product_id, offer.barcode, offer.name),
                        "found": True,
                    }
                )

            store_result = {
                "chain": chain_key,
                "chain_label": chain_label,
                "store_id": store_id,
                "store_name": store_name,
                "total_price": round(total_price, 2),
                "regular_total_price": round(regular_total_price, 2),
                "complete": len(missing_products) == 0,
                "missing_count": len(missing_products),
                "missing_products": missing_products,
                "applied_deals_count": applied_deals_count,
                "distance_km": round(distance_km, 1) if distance_km is not None else None,
                "items": line_items,
            }
            if _catalog_debug_enabled():
                logger.debug(
                    "compare_store list_id=%s chain=%s store_id=%s store_name=%r total=%.2f regular_total=%.2f complete=%s missing=%s missing_products=%s found_items=%s",
                    shopping_list.id,
                    chain_key,
                    store_id,
                    store_name,
                    store_result["total_price"],
                    store_result["regular_total_price"],
                    store_result["complete"],
                    store_result["missing_count"],
                    missing_products,
                    sum(1 for line_item in line_items if line_item["found"]),
                )
            ranking_key = (
                len(missing_products),
                store_result["total_price"],
                _distance_sort_value(distance_km),
                store_id,
            )
            if ranking_key < best_store_key:
                best_store_result = store_result
                best_store_key = ranking_key

        if best_store_result is not None:
            chain_results.append(best_store_result)

    chain_results.sort(
        key=lambda result: (
            0 if result["complete"] else 1,
            result["missing_count"],
            result["total_price"],
            _distance_sort_value(result["distance_km"]),
            result["store_id"],
        )
    )
    if _catalog_debug_enabled():
        logger.debug(
            "compare_done list_id=%s results=%s best=%s",
            shopping_list.id,
            len(chain_results),
            {
                key: chain_results[0].get(key)
                for key in ("chain", "store_id", "total_price", "complete", "missing_count")
            }
            if chain_results
            else None,
        )
    return {
        "list_id": shopping_list.id,
        "list_name": shopping_list.name,
        "item_count": len(items),
        "total_quantity": round(sum(float(item.quantity) for item in items), 3),
        "chains": chain_results,
    }
