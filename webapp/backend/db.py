"""
webapp/backend/db.py
====================
Database models and async engine setup.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    JSON,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/supermarket",
)


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # Identity
    chain = Column(String(32), nullable=False, index=True)
    store_id = Column(String(64), nullable=False, index=True)
    store_name = Column(String(255), nullable=False)
    product_id = Column(String(128), nullable=False)
    name = Column(Text, nullable=False)
    barcode = Column(String(32), nullable=True, index=True)

    # Prices
    price = Column(Float, nullable=False)
    regular_price = Column(Float, nullable=False)
    sale_price = Column(Float, nullable=True)
    discount_percent = Column(Float, nullable=True)

    # Units
    is_weighable = Column(Boolean, nullable=False, default=False)
    unit_description = Column(String(64), nullable=True)
    unit_of_measure = Column(String(32), nullable=True)
    unit_qty = Column(Float, nullable=True)
    unit_qty_si = Column(Float, nullable=True)
    unit_dimension = Column(String(16), nullable=True)
    price_per_base_unit = Column(Float, nullable=True)

    # Meta
    image_url = Column(Text, nullable=True)
    brand = Column(String(255), nullable=True)
    manufacturer = Column(String(255), nullable=True)
    category_ids = Column(JSON, nullable=True)  # list of strings
    deal = Column(JSON, nullable=True)  # DealInfo dict or null
    scraped_at = Column(String(40), nullable=False)

    # Updated at (server-side, for cache freshness)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        # Composite unique: one row per (chain, store_id, product_id)
        Index(
            "ix_products_chain_store_pid",
            "chain",
            "store_id",
            "product_id",
            unique=True,
        ),
        # Full-text / trigram index created via raw SQL in create_tables()
    )


class ScrapeRun(Base):
    """Records each scrape run for scheduling / status reporting."""

    __tablename__ = "scrape_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    chains_scraped = Column(JSON, nullable=True)  # list of chain names
    products_upserted = Column(Integer, nullable=True)
    errors = Column(JSON, nullable=True)  # list of error strings
    status = Column(
        String(16), nullable=False, default="running"
    )  # running|done|failed


# ---------------------------------------------------------------------------
# Engine & session factory
# ---------------------------------------------------------------------------

engine = create_async_engine(DATABASE_URL, echo=False, pool_size=5, max_overflow=10)
async_session_factory = async_sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession
)


async def get_session() -> AsyncSession:  # type: ignore[return]
    async with async_session_factory() as session:
        yield session


async def create_tables() -> None:
    """Create all tables and install pg_trgm extension + GIN index."""
    async with engine.begin() as conn:
        # Install pg_trgm for fuzzy search
        await conn.execute(
            __import__("sqlalchemy").text("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        )
        # Create tables
        await conn.run_sync(Base.metadata.create_all)
        # GIN trigram index on name for fast ILIKE/similarity queries
        await conn.execute(
            __import__("sqlalchemy").text(
                "CREATE INDEX IF NOT EXISTS ix_products_name_trgm "
                "ON products USING GIN (name gin_trgm_ops)"
            )
        )
