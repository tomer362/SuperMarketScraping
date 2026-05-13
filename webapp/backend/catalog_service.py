from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from math import inf
from typing import Any, Iterable, Sequence

from sqlalchemy import case, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from chains import get_chain_definition, iter_active_chains, iter_public_chains
from db import database_backend
from models import (
    CanonicalProduct,
    CatalogOffer,
    CatalogRefreshRun,
    ShoppingList,
    ShoppingListItem,
)
from text_utils import build_match_key, build_search_text, normalize_barcode, normalize_text


ACTIVE_CHAIN_KEYS = tuple(chain.key for chain in iter_active_chains())


def _chunks(values: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


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


async def upsert_catalog_products(
    session: AsyncSession,
    products: list[dict[str, Any]],
    refresh_run_id: int,
) -> int:
    if not products:
        return 0

    canonical_rows = [_canonical_payload(product) for product in products]
    canonical_by_key = {row["match_key"]: row for row in canonical_rows}
    existing = await _load_existing_canonical_products(
        session, list(canonical_by_key.keys())
    )

    created: dict[str, CanonicalProduct] = {}
    for match_key, row in canonical_by_key.items():
        canonical = existing.get(match_key)
        if canonical is None:
            canonical = CanonicalProduct(**row)
            session.add(canonical)
            created[match_key] = canonical
        else:
            canonical.display_name = row["display_name"]
            canonical.normalized_name = row["normalized_name"]
            canonical.brand = row["brand"]
            canonical.normalized_brand = row["normalized_brand"]
            canonical.barcode = row["barcode"]
            canonical.manufacturer = row["manufacturer"]
            canonical.image_url = row["image_url"] or canonical.image_url
            canonical.unit_description = row["unit_description"]
            canonical.unit_of_measure = row["unit_of_measure"]
            canonical.unit_qty = row["unit_qty"]
            canonical.unit_qty_si = row["unit_qty_si"]
            canonical.unit_dimension = row["unit_dimension"]
            canonical.search_text = row["search_text"]

    await session.flush()
    canonical_id_by_key = {
        **{key: row.id for key, row in existing.items()},
        **{key: row.id for key, row in created.items()},
    }

    rows = [
        _offer_payload(product, canonical_id_by_key[build_match_key(product)], refresh_run_id)
        for product in products
    ]

    if database_backend == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    elif database_backend == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    else:
        raise RuntimeError(f"Unsupported database backend for upsert: {database_backend}")

    for batch in _chunks(rows, 500):
        stmt = dialect_insert(CatalogOffer).values(list(batch))
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


async def deactivate_missing_offers_for_chain(
    session: AsyncSession,
    chain: str,
    refresh_run_id: int,
    store_ids: set[str],
) -> None:
    await session.execute(
        update(CatalogOffer)
        .where(CatalogOffer.chain == chain)
        .where(CatalogOffer.store_id.in_(sorted(store_ids)))
        .where(CatalogOffer.refresh_run_id != refresh_run_id)
        .values(is_active=False, updated_at=_now_utc())
    )


async def latest_refresh_run(
    session: AsyncSession,
    *,
    successful_only: bool = False,
) -> CatalogRefreshRun | None:
    stmt = select(CatalogRefreshRun).order_by(CatalogRefreshRun.started_at.desc())
    if successful_only:
        stmt = stmt.where(CatalogRefreshRun.status == "done")
    return (await session.execute(stmt.limit(1))).scalar_one_or_none()


async def catalog_is_fresh(session: AsyncSession, max_age_hours: float) -> bool:
    refresh = await latest_refresh_run(session, successful_only=True)
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
    if database_backend == "postgresql":
        similarity = func.similarity(CanonicalProduct.search_text, normalized)
        condition = or_(CanonicalProduct.search_text.contains(normalized), similarity > 0.05)
        ordering = [rank, similarity.desc(), CanonicalProduct.id.asc()]
    else:
        condition = CanonicalProduct.search_text.contains(normalized)
        ordering = [rank, CanonicalProduct.id.asc()]
    return normalized, ordering, condition


def _active_offer_exists() -> Any:
    return exists(
        select(1)
        .select_from(CatalogOffer)
        .where(CatalogOffer.canonical_product_id == CanonicalProduct.id)
        .where(CatalogOffer.is_active.is_(True))
        .where(CatalogOffer.chain.in_(ACTIVE_CHAIN_KEYS))
    )


async def _preview_offer_map(
    session: AsyncSession,
    product_ids: list[int],
) -> tuple[dict[int, CatalogOffer], dict[int, int]]:
    if not product_ids:
        return {}, {}
    offers = (
        await session.execute(
            select(CatalogOffer)
            .where(CatalogOffer.canonical_product_id.in_(product_ids))
            .where(CatalogOffer.is_active.is_(True))
            .where(CatalogOffer.chain.in_(ACTIVE_CHAIN_KEYS))
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
        "cheapest_price": round(float(offer.price), 2),
        "cheapest_chain": offer.chain,
        "cheapest_chain_label": chain.label,
        "cheapest_store_name": offer.store_name,
        "chain_count": chain_count,
        "has_deal": bool((offer.deal or {}).get("has_deal")),
    }


async def suggest_products(
    session: AsyncSession,
    query: str,
    limit: int = 8,
) -> dict[str, Any]:
    normalized, ordering, condition = _search_filters(query)
    if condition is None:
        return {"query": query, "total": 0, "items": []}

    stmt = (
        select(CanonicalProduct)
        .where(_active_offer_exists())
        .where(condition)
        .order_by(*ordering)
        .limit(limit)
    )
    products = list((await session.execute(stmt)).scalars().all())
    best_offers, chain_counts = await _preview_offer_map(
        session, [product.id for product in products]
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
) -> dict[str, Any]:
    normalized, ordering, condition = _search_filters(query)
    if condition is None:
        return {"query": query, "total": 0, "products": []}

    stmt = select(CanonicalProduct).where(_active_offer_exists()).where(condition)
    count = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    products = list(
        (
            await session.execute(
                stmt.order_by(*ordering).offset(offset).limit(limit)
            )
        ).scalars()
    )
    best_offers, chain_counts = await _preview_offer_map(
        session, [product.id for product in products]
    )
    serialized = [
        serialize_product_preview(product, best_offers[product.id], chain_counts.get(product.id, 0))
        for product in products
        if product.id in best_offers
    ]
    return {"query": query, "total": int(count or 0), "products": serialized}


async def load_product_detail(session: AsyncSession, product_id: int) -> dict[str, Any] | None:
    product = await session.get(CanonicalProduct, product_id)
    if not product:
        return None

    offers = list(
        (
            await session.execute(
                select(CatalogOffer)
                .where(CatalogOffer.canonical_product_id == product_id)
                .where(CatalogOffer.is_active.is_(True))
                .where(CatalogOffer.chain.in_(ACTIVE_CHAIN_KEYS))
                .order_by(CatalogOffer.chain.asc(), CatalogOffer.price.asc())
            )
        ).scalars()
    )
    if not offers:
        return None

    best_offer_by_chain: dict[str, CatalogOffer] = {}
    for offer in offers:
        if offer.chain not in best_offer_by_chain:
            best_offer_by_chain[offer.chain] = offer

    chain_offers = sorted(best_offer_by_chain.values(), key=lambda offer: offer.price)
    detail_offers = [
        {
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
            "price_per_base_unit": offer.price_per_base_unit,
            "brand": offer.brand,
            "image_url": offer.image_url,
            "deal": offer.deal,
            "scraped_at": offer.scraped_at,
        }
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
        "cheapest_price": round(float(cheapest_offer.price), 2),
        "chain_count": len(chain_offers),
        "offers": detail_offers,
    }


async def load_product_chain_offers(session: AsyncSession, product_id: int) -> list[dict[str, Any]]:
    detail = await load_product_detail(session, product_id)
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
        "total_quantity": sum(item.quantity for item in items),
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
        await session.refresh(item, attribute_names=["canonical_product"])

    previews = await _preview_offer_map(
        session, [item.canonical_product_id for item in items]
    )
    best_offers, chain_counts = previews

    serialized_items = []
    for item in items:
        product = item.canonical_product
        offer = best_offers.get(product.id)
        if not offer:
            continue
        serialized_items.append(
            {
                "id": item.id,
                "quantity": item.quantity,
                "product": serialize_product_preview(
                    product,
                    offer,
                    chain_counts.get(product.id, 0),
                ),
            }
        )

    return {
        "id": shopping_list.id,
        "name": shopping_list.name,
        "item_count": len(serialized_items),
        "total_quantity": sum(item["quantity"] for item in serialized_items),
        "updated_at": shopping_list.updated_at,
        "items": serialized_items,
    }


def _line_totals_for_offer(offer: CatalogOffer, quantity: int) -> dict[str, Any]:
    deal = offer.deal or {}
    unit_price = float(offer.price)
    regular_unit_price = float(offer.regular_price)
    total = unit_price * quantity
    regular_total = regular_unit_price * quantity
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
            bundles = quantity // minimum_qty
            remainder = quantity % minimum_qty
            total = bundles * deal_total + remainder * unit_price
            deal_applied = bundles > 0 or unit_price < regular_unit_price
            if deal_applied:
                deal_description = deal.get("deal_description")

    return {
        "unit_price": round(unit_price, 2),
        "regular_unit_price": round(regular_unit_price, 2),
        "line_total": round(total, 2),
        "regular_line_total": round(regular_total, 2),
        "deal_applied": deal_applied,
        "deal_description": deal_description,
    }


def _choose_best_offer_for_quantity(
    offers: list[CatalogOffer],
    quantity: int,
) -> tuple[CatalogOffer, dict[str, Any]]:
    best_offer = offers[0]
    best_meta = _line_totals_for_offer(best_offer, quantity)
    best_key = (best_meta["line_total"], best_meta["unit_price"], best_offer.id)
    for offer in offers[1:]:
        meta = _line_totals_for_offer(offer, quantity)
        key = (meta["line_total"], meta["unit_price"], offer.id)
        if key < best_key:
            best_offer = offer
            best_meta = meta
            best_key = key
    return best_offer, best_meta


async def compare_shopping_list(
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
        await session.refresh(item, attribute_names=["canonical_product"])

    if not items:
        return {
            "list_id": shopping_list.id,
            "list_name": shopping_list.name,
            "item_count": 0,
            "total_quantity": 0,
            "chains": [],
        }

    product_ids = [item.canonical_product_id for item in items]
    offers = list(
        (
            await session.execute(
                select(CatalogOffer)
                .where(CatalogOffer.canonical_product_id.in_(product_ids))
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
    stores_by_chain: dict[str, dict[str, str]] = defaultdict(dict)
    for offer in offers:
        offers_by_store_product[(offer.chain, offer.store_id, offer.canonical_product_id)].append(offer)
        stores_by_chain[offer.chain][offer.store_id] = offer.store_name

    chain_results: list[dict[str, Any]] = []
    for chain_key in ACTIVE_CHAIN_KEYS:
        stores = stores_by_chain.get(chain_key)
        if not stores:
            continue
        best_store_result: dict[str, Any] | None = None
        best_store_key = (inf, inf)
        chain_label = get_chain_definition(chain_key).label

        for store_id, store_name in stores.items():
            total_price = 0.0
            regular_total_price = 0.0
            missing_products: list[str] = []
            applied_deals_count = 0
            line_items: list[dict[str, Any]] = []

            for item in items:
                product = item.canonical_product
                candidates = offers_by_store_product.get(
                    (chain_key, store_id, item.canonical_product_id),
                    [],
                )
                if not candidates:
                    missing_products.append(product.display_name)
                    line_items.append(
                        {
                            "list_item_id": item.id,
                            "canonical_product_id": product.id,
                            "product_name": product.display_name,
                            "quantity": item.quantity,
                            "matched_name": None,
                            "unit_price": None,
                            "regular_unit_price": None,
                            "line_total": None,
                            "regular_line_total": None,
                            "deal_applied": False,
                            "deal_description": None,
                            "image_url": product.image_url,
                            "found": False,
                        }
                    )
                    continue

                offer, totals = _choose_best_offer_for_quantity(candidates, item.quantity)
                total_price += totals["line_total"]
                regular_total_price += totals["regular_line_total"]
                if totals["deal_applied"]:
                    applied_deals_count += 1
                line_items.append(
                    {
                        "list_item_id": item.id,
                        "canonical_product_id": product.id,
                        "product_name": product.display_name,
                        "quantity": item.quantity,
                        "matched_name": offer.name,
                        "unit_price": totals["unit_price"],
                        "regular_unit_price": totals["regular_unit_price"],
                        "line_total": totals["line_total"],
                        "regular_line_total": totals["regular_line_total"],
                        "deal_applied": totals["deal_applied"],
                        "deal_description": totals["deal_description"],
                        "image_url": offer.image_url or product.image_url,
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
                "items": line_items,
            }
            ranking_key = (len(missing_products), store_result["total_price"])
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
        )
    )
    return {
        "list_id": shopping_list.id,
        "list_name": shopping_list.name,
        "item_count": len(items),
        "total_quantity": sum(item.quantity for item in items),
        "chains": chain_results,
    }
