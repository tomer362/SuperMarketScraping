from __future__ import annotations

import asyncio
import importlib
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from catalog_service import (
    build_active_generic_groups,
    build_staging_generic_groups,
    clear_staging_generic_groups,
    clear_staging_offers,
    merge_deal_staging_into_active,
    replace_active_generic_groups_from_staging,
    replace_active_offers_from_staging,
    stage_existing_offer_deals,
    upsert_catalog_products,
)
from chains import ChainDefinition, iter_active_chains
from models import CatalogOffer, CatalogRefreshRun


logger = logging.getLogger("webapp.scraper_runner")


def _validate_products_deal_contract(products: list[dict[str, Any]]) -> list[str]:
    try:
        from scrapers.deal_validation import validate_products_deal_contract
    except ModuleNotFoundError as exc:
        logger.warning("Skipping deal contract validation: %s", exc)
        return []

    return validate_products_deal_contract(products, max_errors=20)


async def _run_chain(
    chain: ChainDefinition,
    *,
    refresh_kind: str = "prices",
) -> tuple[str, list[dict[str, Any]], list[str]]:
    try:
        module = importlib.import_module(chain.scraper_module)
    except Exception as exc:
        logger.error("Failed to import %s: %s", chain.scraper_module, exc)
        return chain.key, [], [f"import_error: {exc}"]

    try:
        scrape_func = getattr(module, "scrape_deals", None) if refresh_kind == "deals" else None
        if scrape_func is None:
            scrape_func = module.scrape
        result = await scrape_func()
    except Exception as exc:
        logger.error("Scrape failed for %s: %s", chain.key, exc, exc_info=True)
        return chain.key, [], [f"scrape_error: {exc}"]

    products: list[dict[str, Any]] = []
    for store_products in (result.get("products_by_store") or {}).values():
        products.extend(store_products)
    errors = list(result.get("errors") or [])
    deal_contract_errors = _validate_products_deal_contract(products)
    if deal_contract_errors:
        logger.warning(
            "%s: found %d deal contract errors",
            chain.key,
            len(deal_contract_errors),
        )
        errors.extend(f"deal_contract: {error}" for error in deal_contract_errors)
    logger.info(
        "%s: collected %d products for %s refresh (%d errors)",
        chain.key,
        len(products),
        refresh_kind,
        len(errors),
    )
    return chain.key, products, errors


async def run_full_refresh(
    session: AsyncSession,
    *,
    source: str,
) -> dict[str, Any]:
    chains = iter_active_chains()
    existing_active_offer_count = (
        await session.execute(
            select(func.count()).select_from(CatalogOffer).where(CatalogOffer.is_active.is_(True))
        )
    ).scalar_one()
    run = CatalogRefreshRun(
        source=source,
        refresh_kind="prices",
        status="running",
        chains_scraped=[],
        chains_failed=[],
        products_upserted=0,
        errors=[],
    )
    session.add(run)
    await session.flush()

    await clear_staging_offers(session)
    await clear_staging_generic_groups(session)
    await session.commit()

    chains_scraped: list[str] = []
    chains_failed: list[str] = []
    all_errors: list[str] = []
    total_upserted = 0

    tasks = [asyncio.create_task(_run_chain(chain, refresh_kind="prices"), name=chain.key) for chain in chains]
    for completed in asyncio.as_completed(tasks):
        chain_key = "unknown"
        result = await completed

        if isinstance(result, Exception):
            all_errors.append(str(result))
            run.errors = all_errors
            await session.commit()
            continue

        chain_key, products, errors = result
        if errors:
            all_errors.extend(f"{chain_key}: {error}" for error in errors)
        if products:
            upserted = await upsert_catalog_products(
                session,
                products,
                run.id,
                target_table="staging",
            )
            total_upserted += upserted
            chains_scraped.append(chain_key)
            run.chains_scraped = list(chains_scraped)
            run.products_upserted = total_upserted
            run.errors = list(all_errors)
            await session.commit()
            logger.info(
                "%s: staged %d products (refresh progress: %d chains, %d products)",
                chain_key,
                upserted,
                len(chains_scraped),
                total_upserted,
            )
        else:
            chains_failed.append(chain_key)
            if not errors:
                all_errors.append(f"{chain_key}: no products returned")
            run.chains_failed = sorted(set(chains_failed))
            run.errors = list(all_errors)
            await session.commit()

    run.chains_scraped = chains_scraped
    run.chains_failed = sorted(set(chains_failed))
    run.products_upserted = total_upserted
    run.errors = all_errors
    run.finished_at = datetime.now(timezone.utc)

    all_chains_succeeded = len(run.chains_failed) == 0 and len(chains_scraped) == len(chains)
    minimum_partial_activation_size = max(500, int(existing_active_offer_count * 0.3))
    partial_activation_allowed = (
        total_upserted >= minimum_partial_activation_size
        or existing_active_offer_count < 1000
    )

    if total_upserted > 0 and (all_chains_succeeded or partial_activation_allowed):
        await build_staging_generic_groups(session)
        await replace_active_offers_from_staging(session)
        await replace_active_generic_groups_from_staging(session)
        await clear_staging_offers(session)
        await clear_staging_generic_groups(session)
        run.status = "done"
        if not all_chains_succeeded:
            run.errors.append(
                "partial_refresh: activated staged catalog despite some failed chains "
                f"(succeeded={len(chains_scraped)}, failed={len(run.chains_failed)}, "
                f"products_upserted={total_upserted})"
            )
    else:
        await clear_staging_offers(session)
        await clear_staging_generic_groups(session)
        run.status = "failed"
        if total_upserted <= 0:
            run.errors.append("swap_skipped: no products were scraped into staging")
        else:
            run.errors.append(
                "swap_skipped: staged catalog too small to replace active data "
                f"(products_upserted={total_upserted}, existing_active_offers={existing_active_offer_count}, "
                f"minimum_required={minimum_partial_activation_size})"
            )

    return {
        "run_id": run.id,
        "source": run.source,
        "refresh_kind": run.refresh_kind,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "chains_scraped": run.chains_scraped,
        "chains_failed": run.chains_failed,
        "products_upserted": run.products_upserted,
        "errors": run.errors,
    }


async def run_deals_refresh(
    session: AsyncSession,
    *,
    source: str,
) -> dict[str, Any]:
    chains = iter_active_chains()
    run = CatalogRefreshRun(
        source=source,
        refresh_kind="deals",
        status="running",
        chains_scraped=[],
        chains_failed=[],
        products_upserted=0,
        errors=[],
    )
    session.add(run)
    await session.flush()

    await clear_staging_offers(session)
    await clear_staging_generic_groups(session)
    await session.commit()

    chains_scraped: list[str] = []
    chains_failed: list[str] = []
    all_errors: list[str] = []
    total_updated = 0

    tasks = [
        asyncio.create_task(_run_chain(chain, refresh_kind="deals"), name=chain.key)
        for chain in chains
    ]
    for completed in asyncio.as_completed(tasks):
        result = await completed
        chain_key, products, errors = result
        if errors:
            chains_failed.append(chain_key)
            all_errors.extend(f"{chain_key}: {error}" for error in errors)
            run.chains_failed = sorted(set(chains_failed))
            run.errors = list(all_errors)
            await session.commit()
            continue
        if not products:
            chains_failed.append(chain_key)
            all_errors.append(f"{chain_key}: no products returned")
            run.chains_failed = sorted(set(chains_failed))
            run.errors = list(all_errors)
            await session.commit()
            continue

        staged = await stage_existing_offer_deals(session, products, run.id)
        updated = await merge_deal_staging_into_active(session)
        await clear_staging_offers(session)
        total_updated += updated
        chains_scraped.append(chain_key)
        run.chains_scraped = list(chains_scraped)
        run.products_upserted = total_updated
        run.errors = list(all_errors)
        await session.commit()
        logger.info(
            "%s: merged deals into %d active offers (%d staged)",
            chain_key,
            updated,
            staged,
        )

    if total_updated > 0:
        await build_active_generic_groups(session)
        run.status = "done"
    else:
        run.status = "failed"
        if not all_errors:
            all_errors.append("deal_merge_skipped: no existing active offers matched scraped deals")

    await clear_staging_offers(session)
    await clear_staging_generic_groups(session)
    run.chains_scraped = chains_scraped
    run.chains_failed = sorted(set(chains_failed))
    run.products_upserted = total_updated
    run.errors = all_errors
    run.finished_at = datetime.now(timezone.utc)

    return {
        "run_id": run.id,
        "source": run.source,
        "refresh_kind": run.refresh_kind,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "chains_scraped": run.chains_scraped,
        "chains_failed": run.chains_failed,
        "products_upserted": run.products_upserted,
        "errors": run.errors,
    }
