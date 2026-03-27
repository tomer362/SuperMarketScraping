"""
webapp/backend/scraper_runner.py
================================
Runs all scrapers (except CHP) and upserts results into PostgreSQL.
Called by the background scheduler.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make sure the project root is on sys.path so scrapers can be imported
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db import ScrapeRun, async_session_factory

logger = logging.getLogger("scraper_runner")

# ---------------------------------------------------------------------------
# Chain registry (exclude CHP)
# ---------------------------------------------------------------------------

_CHAINS: List[Dict[str, Any]] = [
    {"name": "shufersal", "module": "scrapers.shufersal.shufersal"},
    {"name": "tivtaam", "module": "scrapers.tivtaam.tivtaam"},
    {"name": "carrefour", "module": "scrapers.carrefour.carrefour"},
    {"name": "machsanei", "module": "scrapers.machsanei_hashook.machsanei_hashook"},
    {"name": "ramilevi", "module": "scrapers.ramilevi.ramilevi"},
    {"name": "yochananof", "module": "scrapers.yochananof.yochananof"},
    {"name": "keshet", "module": "scrapers.keshet.keshet"},
    {"name": "quik", "module": "scrapers.quik.quik"},
    {"name": "victory", "module": "scrapers.victory.victory"},
    {"name": "ybitan", "module": "scrapers.ybitan.ybitan"},
]


async def _run_chain(chain_cfg: Dict[str, Any]) -> tuple[str, list, list[str]]:
    """Run a single chain scraper and return (chain_name, products, errors)."""
    name = chain_cfg["name"]
    mod_path = chain_cfg["module"]

    try:
        mod = importlib.import_module(mod_path)
    except Exception as exc:
        logger.error("Failed to import %s: %s", mod_path, exc)
        return name, [], [str(exc)]

    try:
        # All scrapers support scrape() with no arguments to scrape everything
        result = await mod.scrape()
    except Exception as exc:
        logger.error("Scrape failed for %s: %s", name, exc, exc_info=True)
        return name, [], [str(exc)]

    products: list = []
    for store_products in result.get("products_by_store", {}).values():
        products.extend(store_products)

    errors = result.get("errors", [])
    logger.info(
        "%s: collected %d products (%d errors)", name, len(products), len(errors)
    )
    return name, products, errors


def _product_to_row(p: dict) -> dict:
    """Convert a UnifiedProduct dict to a DB row dict."""
    return {
        "chain": p.get("chain", ""),
        "store_id": str(p.get("store_id", "")),
        "store_name": p.get("store_name", ""),
        "product_id": str(p.get("product_id", "")),
        "name": p.get("name", ""),
        "barcode": p.get("barcode"),
        "price": p.get("price", 0.0),
        "regular_price": p.get("regular_price", 0.0),
        "sale_price": p.get("sale_price"),
        "discount_percent": p.get("discount_percent"),
        "is_weighable": bool(p.get("is_weighable", False)),
        "unit_description": p.get("unit_description"),
        "unit_of_measure": p.get("unit_of_measure"),
        "unit_qty": p.get("unit_qty"),
        "unit_qty_si": p.get("unit_qty_si"),
        "unit_dimension": p.get("unit_dimension"),
        "price_per_base_unit": p.get("price_per_base_unit"),
        "image_url": p.get("image_url"),
        "brand": p.get("brand"),
        "manufacturer": p.get("manufacturer"),
        "category_ids": p.get("category_ids") or [],
        "deal": p.get("deal"),
        "scraped_at": p.get("scraped_at", ""),
        "updated_at": datetime.utcnow(),
    }


async def _upsert_products(products: list) -> int:
    """Bulk upsert products into PostgreSQL. Returns count upserted."""
    if not products:
        return 0

    from db import Product

    rows = [_product_to_row(p) for p in products]

    BATCH = 500
    total = 0
    async with async_session_factory() as session:
        for i in range(0, len(rows), BATCH):
            batch = rows[i : i + BATCH]
            stmt = pg_insert(Product).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["chain", "store_id", "product_id"],
                set_={
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
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            await session.execute(stmt)
            await session.commit()
            total += len(batch)
            logger.debug("Upserted batch %d/%d", i + BATCH, len(rows))

    return total


async def run_all_scrapers() -> dict:
    """Run all chain scrapers concurrently and upsert results into DB.

    Returns a summary dict with counts and errors.
    """
    logger.info("=== Starting full scrape run ===")

    # Record scrape run start
    async with async_session_factory() as session:
        run = ScrapeRun(started_at=datetime.utcnow(), status="running")
        session.add(run)
        await session.commit()
        run_id = run.id

    all_errors: list[str] = []
    chains_done: list[str] = []
    total_upserted = 0

    # Run all chains concurrently
    tasks = [asyncio.create_task(_run_chain(cfg), name=cfg["name"]) for cfg in _CHAINS]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for cfg, res in zip(_CHAINS, results):
        name = cfg["name"]
        if isinstance(res, Exception):
            logger.error("Chain %s raised exception: %s", name, res)
            all_errors.append(f"{name}: {res}")
            continue
        chain_name, products, errors = res
        all_errors.extend([f"{name}: {e}" for e in errors])
        chains_done.append(chain_name)
        upserted = await _upsert_products(products)
        total_upserted += upserted

    # Update scrape run record
    async with async_session_factory() as session:
        run = await session.get(ScrapeRun, run_id)
        if run:
            run.finished_at = datetime.utcnow()
            run.chains_scraped = chains_done
            run.products_upserted = total_upserted
            run.errors = all_errors
            run.status = "failed" if all_errors and not chains_done else "done"
            await session.commit()

    logger.info(
        "=== Scrape run complete: %d products upserted, %d errors ===",
        total_upserted,
        len(all_errors),
    )
    return {
        "run_id": run_id,
        "chains_scraped": chains_done,
        "products_upserted": total_upserted,
        "errors": all_errors,
    }
