from __future__ import annotations

import asyncio
import importlib
import logging
import os
import re
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
    replace_active_deals_from_staging,
    replace_active_generic_groups_from_staging,
    replace_active_offers_from_staging,
    upsert_catalog_products,
)
from chains import ChainDefinition, iter_active_chains
from location_service import normalize_branch_payload, upsert_store_branches
from models import CatalogOffer, CatalogRefreshRun


logger = logging.getLogger("webapp.scraper_runner")
CHAIN_SCRAPE_TIMEOUT_SECONDS = int(os.environ.get("CHAIN_SCRAPE_TIMEOUT_SECONDS", "480"))
CHAIN_REFRESH_CONCURRENCY = int(os.environ.get("CHAIN_REFRESH_CONCURRENCY", "3"))
_ACTIVE_PROGRESS: dict[int, dict[str, Any]] = {}
CHAIN_START_PRIORITY: dict[str, int] = {
    "ramilevi": 0,
    "ybitan": 1,
    "keshet": 2,
    "machsanei": 3,
    "quik": 4,
    "victory": 5,
    "tivtaam": 6,
    "carrefour": 7,
    "shufersal": 8,
    "yochananof": 9,
}
_PRODUCT_LOG_PATTERN = re.compile(r"—\s*(\d+)\s+products\b")
_CHAIN_LOGGER_ALIASES: dict[str, set[str]] = {
    "machsanei": {"machsanei_hashook", "machsanei"},
    "yochananof": {"yochananof"},
    "shufersal": {"shufersal"},
    "tivtaam": {"tivtaam"},
    "carrefour": {"carrefour"},
    "ramilevi": {"ramilevi"},
    "keshet": {"keshet"},
    "quik": {"quik"},
    "victory": {"victory"},
    "ybitan": {"ybitan"},
}


def _chain_key_from_logger_name(logger_name: str) -> str | None:
    for chain_key, aliases in _CHAIN_LOGGER_ALIASES.items():
        if logger_name == chain_key or logger_name in aliases:
            return chain_key
    for chain_key, aliases in _CHAIN_LOGGER_ALIASES.items():
        if any(alias in logger_name for alias in aliases):
            return chain_key
    return None


def _prioritize_chains(chains: list[ChainDefinition]) -> list[ChainDefinition]:
    return sorted(
        chains,
        key=lambda chain: (CHAIN_START_PRIORITY.get(chain.key, 100), chain.key),
    )


class _LiveProductLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        match = _PRODUCT_LOG_PATTERN.search(message)
        if not match:
            return
        chain_key = _chain_key_from_logger_name(record.name)
        if not chain_key:
            return
        product_count = int(match.group(1))
        for progress in _ACTIVE_PROGRESS.values():
            started = set(progress.get("chains_started") or [])
            if chain_key not in started:
                continue
            by_chain = dict(progress.get("products_reported_by_chain") or {})
            by_chain[chain_key] = int(by_chain.get(chain_key, 0)) + product_count
            progress["products_reported_by_chain"] = by_chain
            progress["products_reported"] = sum(by_chain.values())


logging.getLogger().addHandler(_LiveProductLogHandler())


def get_active_refresh_progress(run_id: int | None) -> dict[str, Any] | None:
    if run_id is None:
        return None
    progress = _ACTIVE_PROGRESS.get(run_id)
    return dict(progress) if progress else None


def clear_active_refresh_progress(run_id: int) -> None:
    _ACTIVE_PROGRESS.pop(run_id, None)


def _set_active_progress(run_id: int, **updates: Any) -> None:
    progress = _ACTIVE_PROGRESS.setdefault(
        run_id,
        {"chains_started": [], "chains_running": [], "current_chain": None},
    )
    progress.update(updates)


def _mark_chain_started(run_id: int, chain_key: str) -> None:
    progress = _ACTIVE_PROGRESS.setdefault(
        run_id,
        {"chains_started": [], "chains_running": [], "current_chain": None},
    )
    chains_started = list(progress.get("chains_started") or [])
    chains_running = list(progress.get("chains_running") or [])
    if chain_key not in chains_started:
        chains_started.append(chain_key)
    if chain_key not in chains_running:
        chains_running.append(chain_key)
    progress["chains_started"] = chains_started
    progress["chains_running"] = chains_running
    progress["current_chain"] = chain_key


def _mark_chain_finished(run_id: int, chain_key: str) -> None:
    progress = _ACTIVE_PROGRESS.get(run_id)
    if not progress:
        return
    chains_running = [key for key in progress.get("chains_running") or [] if key != chain_key]
    progress["chains_running"] = chains_running
    progress["current_chain"] = chains_running[-1] if chains_running else None


def _validate_products_deal_contract(products: list[dict[str, Any]]) -> list[str]:
    try:
        from scrapers.deal_validation import validate_products_deal_contract
    except ModuleNotFoundError as exc:
        logger.warning("Skipping deal contract validation: %s", exc)
        return []

    return validate_products_deal_contract(products, max_errors=20)


def _extract_branch_metadata(
    chain_key: str,
    module: Any,
    result: dict[str, Any],
    products: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    branches: list[dict[str, Any]] = []
    for raw_branch in result.get("store_branches") or []:
        branch = normalize_branch_payload(chain_key, raw_branch)
        if branch:
            branches.append(branch)

    for attr_name in ("ONLINE_BRANCHES", "ONLINE_STORES"):
        for raw_branch in getattr(module, attr_name, []) or []:
            branch = normalize_branch_payload(chain_key, raw_branch)
            if branch:
                branches.append(branch)

    seen_keys = {(branch["chain"], branch["store_id"]) for branch in branches}
    for product in products:
        key = (str(product.get("chain", chain_key)), str(product.get("store_id", "")))
        if not key[1] or key in seen_keys:
            continue
        branch = normalize_branch_payload(
            key[0],
            {
                "store_id": key[1],
                "store_name": product.get("store_name") or key[1],
            },
        )
        if branch:
            branches.append(branch)
            seen_keys.add(key)

    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for branch in branches:
        unique[(branch["chain"], branch["store_id"])] = branch
    return list(unique.values())


async def _run_chain(
    chain: ChainDefinition,
    *,
    refresh_kind: str = "prices",
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    try:
        module = importlib.import_module(chain.scraper_module)
    except Exception as exc:
        logger.error("Failed to import %s: %s", chain.scraper_module, exc)
        return chain.key, [], [], [f"import_error: {exc}"]

    try:
        scrape_func = getattr(module, "scrape_deals", None) if refresh_kind == "deals" else None
        if scrape_func is None:
            scrape_func = module.scrape
        result = await scrape_func()
    except Exception as exc:
        logger.error("Scrape failed for %s: %s", chain.key, exc, exc_info=True)
        return chain.key, [], [], [f"scrape_error: {exc}"]

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
    branches = _extract_branch_metadata(chain.key, module, result, products)
    return chain.key, products, branches, errors


async def _run_chain_with_timeout(
    chain: ChainDefinition,
    *,
    refresh_kind: str,
    run_id: int,
    semaphore: asyncio.Semaphore,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    async with semaphore:
        _mark_chain_started(run_id, chain.key)
        try:
            return await asyncio.wait_for(
                _run_chain(chain, refresh_kind=refresh_kind),
                timeout=CHAIN_SCRAPE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.error(
                "Scrape timed out for %s after %d seconds",
                chain.key,
                CHAIN_SCRAPE_TIMEOUT_SECONDS,
            )
            return chain.key, [], [], [f"scrape_timeout: exceeded {CHAIN_SCRAPE_TIMEOUT_SECONDS} seconds"]
        finally:
            _mark_chain_finished(run_id, chain.key)


async def run_full_refresh(
    session: AsyncSession,
    *,
    source: str,
) -> dict[str, Any]:
    chains = _prioritize_chains(iter_active_chains())
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
    _set_active_progress(
        run.id,
        refresh_kind="prices",
        total_chains=len(chains),
        chains_fetched=[],
        products_fetched=0,
        products_reported=0,
        products_reported_by_chain={},
        products_upserted=0,
        current_status_hint=None,
    )

    await clear_staging_offers(session)
    await clear_staging_generic_groups(session)
    await session.commit()

    chains_scraped: list[str] = []
    chains_failed: list[str] = []
    all_errors: list[str] = []
    total_upserted = 0
    tasks: list[asyncio.Task] = []

    try:
        semaphore = asyncio.Semaphore(max(1, CHAIN_REFRESH_CONCURRENCY))
        tasks = [
            asyncio.create_task(
                _run_chain_with_timeout(
                    chain,
                    refresh_kind="prices",
                    run_id=run.id,
                    semaphore=semaphore,
                ),
                name=chain.key,
            )
            for chain in chains
        ]
        chains_fetched: list[str] = []
        total_fetched = 0
        for completed in asyncio.as_completed(tasks):
            result = await completed
            chain_key, products, branches, errors = result
            if chain_key not in chains_fetched:
                chains_fetched.append(chain_key)
            total_fetched += len(products)
            _set_active_progress(
                run.id,
                chains_fetched=list(chains_fetched),
                products_fetched=total_fetched,
            )
            if errors:
                all_errors.extend(f"{chain_key}: {error}" for error in errors)
            if branches:
                await upsert_store_branches(session, branches)
            if products:
                _set_active_progress(
                    run.id,
                    current_status_hint=f"persisting:{chain_key}:{len(products)}",
                )
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
                _set_active_progress(
                    run.id,
                    products_upserted=total_upserted,
                    current_status_hint=None,
                )
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
        _set_active_progress(run.id, products_upserted=total_upserted)

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
    except asyncio.CancelledError:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await clear_staging_offers(session)
        await clear_staging_generic_groups(session)
        run.status = "cancelled"
        run.finished_at = datetime.now(timezone.utc)
        run.chains_scraped = list(chains_scraped)
        run.chains_failed = sorted(set(chains_failed))
        run.products_upserted = total_upserted
        run.errors = list(all_errors) + ["refresh_cancelled: cancelled by user request"]
    finally:
        await session.commit()
        clear_active_refresh_progress(run.id)

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
    chains = _prioritize_chains(iter_active_chains())
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
    _set_active_progress(
        run.id,
        refresh_kind="deals",
        total_chains=len(chains),
        chains_fetched=[],
        products_fetched=0,
        products_reported=0,
        products_reported_by_chain={},
        products_upserted=0,
        current_status_hint=None,
    )

    await clear_staging_offers(session)
    await clear_staging_generic_groups(session)
    await session.commit()

    chains_scraped: list[str] = []
    chains_failed: list[str] = []
    all_errors: list[str] = []
    total_updated = 0
    tasks: list[asyncio.Task] = []

    try:
        semaphore = asyncio.Semaphore(max(1, CHAIN_REFRESH_CONCURRENCY))
        tasks = [
            asyncio.create_task(
                _run_chain_with_timeout(
                    chain,
                    refresh_kind="deals",
                    run_id=run.id,
                    semaphore=semaphore,
                ),
                name=chain.key,
            )
            for chain in chains
        ]
        chains_fetched: list[str] = []
        total_fetched = 0
        for completed in asyncio.as_completed(tasks):
            result = await completed
            chain_key, products, branches, errors = result
            if chain_key not in chains_fetched:
                chains_fetched.append(chain_key)
            total_fetched += len(products)
            _set_active_progress(
                run.id,
                chains_fetched=list(chains_fetched),
                products_fetched=total_fetched,
            )
            if errors:
                chains_failed.append(chain_key)
                all_errors.extend(f"{chain_key}: {error}" for error in errors)
                run.chains_failed = sorted(set(chains_failed))
                run.errors = list(all_errors)
                await session.commit()
                continue
            if branches:
                await upsert_store_branches(session, branches)
            if not products:
                chains_failed.append(chain_key)
                all_errors.append(f"{chain_key}: no products returned")
                run.chains_failed = sorted(set(chains_failed))
                run.errors = list(all_errors)
                await session.commit()
                continue

            _set_active_progress(
                run.id,
                current_status_hint=f"persisting:{chain_key}:{len(products)}",
            )
            staged = await upsert_catalog_products(
                session,
                products,
                run.id,
                target_table="staging",
            )
            chains_scraped.append(chain_key)
            run.chains_scraped = list(chains_scraped)
            run.errors = list(all_errors)
            await session.commit()
            _set_active_progress(run.id, current_status_hint=None)
            logger.info("%s: staged %d deal rows", chain_key, staged)

        if chains_scraped:
            total_updated = await replace_active_deals_from_staging(session, chains=chains_scraped)
            await build_active_generic_groups(session)
            run.status = "done"
        else:
            run.status = "failed"
            if not all_errors:
                all_errors.append("deal_replace_skipped: no deal payloads were scraped")

        await clear_staging_offers(session)
        await clear_staging_generic_groups(session)
        run.chains_scraped = chains_scraped
        run.chains_failed = sorted(set(chains_failed))
        run.products_upserted = total_updated
        run.errors = all_errors
        run.finished_at = datetime.now(timezone.utc)
        _set_active_progress(run.id, products_upserted=total_updated)
    except asyncio.CancelledError:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await clear_staging_offers(session)
        await clear_staging_generic_groups(session)
        run.status = "cancelled"
        run.finished_at = datetime.now(timezone.utc)
        run.chains_scraped = list(chains_scraped)
        run.chains_failed = sorted(set(chains_failed))
        run.products_upserted = total_updated
        run.errors = list(all_errors) + ["refresh_cancelled: cancelled by user request"]
    finally:
        await session.commit()
        clear_active_refresh_progress(run.id)

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
