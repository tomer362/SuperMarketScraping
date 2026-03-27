"""
webapp/backend/main.py
======================
FastAPI application: product search + shopping list comparison.

Environment variables (set in .env):
  DATABASE_URL          - PostgreSQL connection string (asyncpg)
  SCRAPE_INTERVAL_HOURS - Hours between automatic scrape runs (default: 6)
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from db import Product, ScrapeRun, create_tables, get_session
from scheduler import scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("webapp")


# ---------------------------------------------------------------------------
# Lifespan — DB init + scheduler start
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up — creating DB tables…")
    await create_tables()
    logger.info("DB ready. Starting scrape scheduler…")
    await scheduler.start()
    yield
    logger.info("Shutting down scrape scheduler…")
    await scheduler.stop()


app = FastAPI(
    title="SuperMarket Price Searcher",
    description="Fuzzy product search and shopping list comparison across Israeli supermarkets.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class DealOut(BaseModel):
    has_deal: bool
    deal_type: Optional[str] = None
    deal_description: Optional[str] = None
    deal_price: Optional[float] = None
    deal_min_qty: Optional[int] = None
    deal_price_per_unit: Optional[float] = None
    price_per_base_unit: Optional[float] = None
    price_per_base_unit_deal: Optional[float] = None

    model_config = {"from_attributes": True}


class ProductOut(BaseModel):
    id: int
    chain: str
    store_id: str
    store_name: str
    product_id: str
    name: str
    barcode: Optional[str] = None
    price: float
    regular_price: float
    sale_price: Optional[float] = None
    discount_percent: Optional[float] = None
    is_weighable: bool
    unit_description: Optional[str] = None
    unit_of_measure: Optional[str] = None
    unit_qty: Optional[float] = None
    unit_qty_si: Optional[float] = None
    unit_dimension: Optional[str] = None
    price_per_base_unit: Optional[float] = None
    image_url: Optional[str] = None
    brand: Optional[str] = None
    manufacturer: Optional[str] = None
    deal: Optional[Dict[str, Any]] = None
    scraped_at: str

    model_config = {"from_attributes": True}


class SearchResult(BaseModel):
    query: str
    total: int
    products: List[ProductOut]


class CartItem(BaseModel):
    product_id: int  # DB primary key


class StoreCartResult(BaseModel):
    chain: str
    store_id: str
    store_name: str
    total_price: float
    items: List[Dict[str, Any]]  # {product_id, name, price, found: bool}
    missing_products: List[str]  # names of products not found at this store
    has_missing: bool


class CartCompareResult(BaseModel):
    cart_items: List[str]  # product names from reference
    stores: List[StoreCartResult]


class ScrapeStatus(BaseModel):
    scheduler_running: bool
    interval_hours: float
    last_run: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Helper: fuzzy product search
# ---------------------------------------------------------------------------


async def _search_products(
    session: AsyncSession,
    query: str,
    limit: int = 50,
    offset: int = 0,
    chain: Optional[str] = None,
) -> tuple[int, list[Product]]:
    """Fuzzy search products by name using pg_trgm similarity."""
    base = select(Product)
    count_base = select(func.count()).select_from(Product)

    if query:
        # Use similarity threshold for fuzzy matching
        condition = text("similarity(name, :q) > 0.1 OR name ILIKE :like").bindparams(
            q=query, like=f"%{query}%"
        )
        base = base.where(condition)
        count_base = count_base.where(condition)

    if chain:
        base = base.where(Product.chain == chain)
        count_base = count_base.where(Product.chain == chain)

    # Order: exact/most-similar first, then cheapest
    if query:
        base = base.order_by(
            text("similarity(name, :q) DESC").bindparams(q=query),
            Product.price.asc(),
        )
    else:
        base = base.order_by(Product.price.asc())

    total_result = await session.execute(count_base)
    total = total_result.scalar() or 0

    base = base.offset(offset).limit(limit)
    result = await session.execute(base)
    products = list(result.scalars().all())

    return total, products


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/api/search", response_model=SearchResult, tags=["Search"])
async def search(
    q: str = Query("", description="Product name (fuzzy search)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    chain: Optional[str] = Query(None, description="Filter by chain name"),
    session: AsyncSession = Depends(get_session),
) -> SearchResult:
    """
    Fuzzy search for products across all supermarkets.

    Results are ordered by similarity to query (most relevant first), then
    by price ascending (cheapest first within each similarity tier).
    """
    if not q and not chain:
        # Return a sampling of cheap products when no query
        stmt = select(Product).order_by(Product.price.asc()).limit(limit).offset(offset)
        count_stmt = select(func.count()).select_from(Product)
        total = (await session.execute(count_stmt)).scalar() or 0
        rows = list((await session.execute(stmt)).scalars().all())
        return SearchResult(
            query=q,
            total=total,
            products=[ProductOut.model_validate(r) for r in rows],
        )

    total, products = await _search_products(
        session, q, limit=limit, offset=offset, chain=chain
    )
    return SearchResult(
        query=q,
        total=total,
        products=[ProductOut.model_validate(p) for p in products],
    )


@app.get("/api/product/{product_db_id}", response_model=ProductOut, tags=["Search"])
async def get_product(
    product_db_id: int,
    session: AsyncSession = Depends(get_session),
) -> ProductOut:
    """Fetch a single product by its database ID."""
    product = await session.get(Product, product_db_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return ProductOut.model_validate(product)


@app.post("/api/cart/compare", response_model=CartCompareResult, tags=["Shopping List"])
async def compare_cart(
    items: List[CartItem],
    session: AsyncSession = Depends(get_session),
) -> CartCompareResult:
    """
    Compare a shopping cart across all supermarkets.

    For each product in the cart, finds its equivalent at every store
    (matched by barcode first, then product_id+chain, then name similarity).
    Returns stores sorted cheapest to most expensive total, with warnings
    when a product is unavailable at a store.
    """
    if not items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    # Load the reference products
    ref_products: list[Product] = []
    for item in items:
        p = await session.get(Product, item.product_id)
        if not p:
            raise HTTPException(
                status_code=404, detail=f"Product {item.product_id} not found"
            )
        ref_products.append(p)

    cart_names = [p.name for p in ref_products]

    # Find all distinct (chain, store_id, store_name) combos in DB
    stores_stmt = select(Product.chain, Product.store_id, Product.store_name).distinct()
    stores_rows = list((await session.execute(stores_stmt)).all())
    # Deduplicate
    seen = set()
    stores = []
    for row in stores_rows:
        key = (row.chain, row.store_id)
        if key not in seen:
            seen.add(key)
            stores.append(
                {
                    "chain": row.chain,
                    "store_id": row.store_id,
                    "store_name": row.store_name,
                }
            )

    store_results: list[StoreCartResult] = []

    for store in stores:
        chain = store["chain"]
        store_id = store["store_id"]
        store_name = store["store_name"]

        total_price = 0.0
        items_out = []
        missing = []

        for ref in ref_products:
            found_product: Optional[Product] = None

            # 1. Match by barcode (most reliable)
            if ref.barcode:
                stmt = (
                    select(Product)
                    .where(
                        Product.chain == chain,
                        Product.store_id == store_id,
                        Product.barcode == ref.barcode,
                    )
                    .limit(1)
                )
                row = (await session.execute(stmt)).scalar_one_or_none()
                if row:
                    found_product = row

            # 2. Match by same chain+product_id
            if not found_product:
                stmt = (
                    select(Product)
                    .where(
                        Product.chain == chain,
                        Product.store_id == store_id,
                        Product.product_id == ref.product_id,
                    )
                    .limit(1)
                )
                row = (await session.execute(stmt)).scalar_one_or_none()
                if row:
                    found_product = row

            # 3. Fuzzy name match within this store
            if not found_product:
                stmt = (
                    select(Product)
                    .where(
                        Product.chain == chain,
                        Product.store_id == store_id,
                        text("similarity(name, :q) > 0.3").bindparams(q=ref.name),
                    )
                    .order_by(text("similarity(name, :q) DESC").bindparams(q=ref.name))
                    .limit(1)
                )
                row = (await session.execute(stmt)).scalar_one_or_none()
                if row:
                    found_product = row

            if found_product:
                total_price += found_product.price
                items_out.append(
                    {
                        "ref_product_id": ref.id,
                        "ref_name": ref.name,
                        "matched_name": found_product.name,
                        "price": found_product.price,
                        "barcode": found_product.barcode,
                        "image_url": found_product.image_url,
                        "found": True,
                    }
                )
            else:
                missing.append(ref.name)
                items_out.append(
                    {
                        "ref_product_id": ref.id,
                        "ref_name": ref.name,
                        "matched_name": None,
                        "price": None,
                        "barcode": None,
                        "image_url": None,
                        "found": False,
                    }
                )

        store_results.append(
            StoreCartResult(
                chain=chain,
                store_id=store_id,
                store_name=store_name,
                total_price=round(total_price, 2),
                items=items_out,
                missing_products=missing,
                has_missing=bool(missing),
            )
        )

    # Sort by total price ascending (missing items get penalised to the bottom)
    store_results.sort(key=lambda s: (s.has_missing, s.total_price))

    return CartCompareResult(cart_items=cart_names, stores=store_results)


@app.get("/api/chains", tags=["Meta"])
async def list_chains(session: AsyncSession = Depends(get_session)) -> Dict[str, Any]:
    """List all chains currently in the database with product counts."""
    stmt = (
        select(Product.chain, func.count(Product.id).label("count"))
        .group_by(Product.chain)
        .order_by(Product.chain)
    )
    rows = list((await session.execute(stmt)).all())
    return {"chains": [{"chain": r.chain, "product_count": r.count} for r in rows]}


@app.get("/api/scrape/status", response_model=ScrapeStatus, tags=["Scraper"])
async def scrape_status(session: AsyncSession = Depends(get_session)) -> ScrapeStatus:
    """Return the status of the scrape scheduler and last run summary."""
    last_run_data = scheduler.last_run

    # Also fetch the latest ScrapeRun from DB if last_run not in memory
    if not last_run_data:
        stmt = select(ScrapeRun).order_by(ScrapeRun.started_at.desc()).limit(1)
        run = (await session.execute(stmt)).scalar_one_or_none()
        if run:
            last_run_data = {
                "run_id": run.id,
                "status": run.status,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "chains_scraped": run.chains_scraped,
                "products_upserted": run.products_upserted,
                "errors": run.errors,
            }

    return ScrapeStatus(
        scheduler_running=scheduler.is_running,
        interval_hours=scheduler.interval_hours,
        last_run=last_run_data,
    )


@app.post("/api/scrape/trigger", tags=["Scraper"])
async def trigger_scrape() -> Dict[str, Any]:
    """Manually trigger an immediate full scrape of all supermarkets."""
    result = await scheduler.trigger_now()
    return result


@app.get("/api/health", tags=["Meta"])
async def health() -> Dict[str, str]:
    return {"status": "ok"}
