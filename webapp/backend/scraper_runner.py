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

from sqlalchemy.ext.asyncio import AsyncSession

from catalog_service import (
    build_staging_generic_groups,
    clear_staging_generic_groups,
    clear_staging_offers,
    replace_active_generic_groups_from_staging,
    replace_active_offers_from_staging,
    upsert_catalog_products,
)
from chains import ChainDefinition, iter_active_chains
from models import CatalogRefreshRun
from scrapers.deal_validation import validate_products_deal_contract


logger = logging.getLogger("webapp.scraper_runner")


async def _run_chain(chain: ChainDefinition) -> tuple[str, list[dict[str, Any]], list[str]]:
    try:
        module = importlib.import_module(chain.scraper_module)
    except Exception as exc:
        logger.error("Failed to import %s: %s", chain.scraper_module, exc)
        return chain.key, [], [f"import_error: {exc}"]

    try:
        result = await module.scrape()
    except Exception as exc:
        logger.error("Scrape failed for %s: %s", chain.key, exc, exc_info=True)
        return chain.key, [], [f"scrape_error: {exc}"]

    products: list[dict[str, Any]] = []
    for store_products in (result.get("products_by_store") or {}).values():
        products.extend(store_products)
    errors = list(result.get("errors") or [])
    deal_contract_errors = validate_products_deal_contract(products, max_errors=20)
    if deal_contract_errors:
        logger.warning(
            "%s: found %d deal contract errors",
            chain.key,
            len(deal_contract_errors),
        )
        errors.extend(f"deal_contract: {error}" for error in deal_contract_errors)
    logger.info(
        "%s: collected %d products (%d errors)",
        chain.key,
        len(products),
        len(errors),
    )
    return chain.key, products, errors


async def run_full_refresh(
    session: AsyncSession,
    *,
    source: str,
) -> dict[str, Any]:
    chains = iter_active_chains()
    run = CatalogRefreshRun(
        source=source,
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

    tasks = [asyncio.create_task(_run_chain(chain), name=chain.key) for chain in chains]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    chains_scraped: list[str] = []
    chains_failed: list[str] = []
    all_errors: list[str] = []
    total_upserted = 0

    for chain, result in zip(chains, results):
        if isinstance(result, Exception):
            chains_failed.append(chain.key)
            all_errors.append(f"{chain.key}: {result}")
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
            await session.commit()
        else:
            chains_failed.append(chain_key)
            if not errors:
                all_errors.append(f"{chain_key}: no products returned")

    run.chains_scraped = chains_scraped
    run.chains_failed = sorted(set(chains_failed))
    run.products_upserted = total_upserted
    run.errors = all_errors
    run.finished_at = datetime.now(timezone.utc)

    all_chains_succeeded = len(run.chains_failed) == 0 and len(chains_scraped) == len(chains)
    if all_chains_succeeded:
        await build_staging_generic_groups(session)
        await replace_active_offers_from_staging(session)
        await replace_active_generic_groups_from_staging(session)
        await clear_staging_offers(session)
        await clear_staging_generic_groups(session)
        run.status = "done"
    else:
        await clear_staging_offers(session)
        await clear_staging_generic_groups(session)
        run.status = "failed"
        run.errors.append("swap_skipped: staged catalog not activated because one or more chains failed")

    return {
        "run_id": run.id,
        "source": run.source,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "chains_scraped": run.chains_scraped,
        "chains_failed": run.chains_failed,
        "products_upserted": run.products_upserted,
        "errors": run.errors,
    }
