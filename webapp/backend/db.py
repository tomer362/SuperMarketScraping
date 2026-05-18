from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy import event
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from models import Base
from settings import get_settings


settings = get_settings()
database_url = settings.database_url
database_backend = make_url(database_url).get_backend_name()

engine_options: dict[str, object] = {"echo": False, "future": True}
if database_backend == "sqlite":
    engine_options["connect_args"] = {
        "check_same_thread": False,
        "timeout": 30,
    }
elif settings.is_vercel:
    engine_options["poolclass"] = NullPool
else:
    engine_options["pool_size"] = 5
    engine_options["max_overflow"] = 10

engine: AsyncEngine = create_async_engine(database_url, **engine_options)

if database_backend == "sqlite":
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

async_session_factory = async_sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


async def create_tables(*, drop_existing: bool = False) -> None:
    async with engine.begin() as conn:
        if database_backend == "sqlite":
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA synchronous=NORMAL"))
            await conn.execute(text("PRAGMA busy_timeout=30000"))

        if drop_existing:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

        if not drop_existing:
            if database_backend == "sqlite":
                columns = (await conn.execute(text("PRAGMA table_info(catalog_refresh_runs)"))).mappings().all()
                column_names = {column["name"] for column in columns}
                if "refresh_kind" not in column_names:
                    await conn.execute(
                        text(
                            "ALTER TABLE catalog_refresh_runs "
                            "ADD COLUMN refresh_kind VARCHAR(16) NOT NULL DEFAULT 'prices'"
                        )
                    )
            elif database_backend == "postgresql":
                await conn.execute(
                    text(
                        "ALTER TABLE catalog_refresh_runs "
                        "ADD COLUMN IF NOT EXISTS refresh_kind VARCHAR(16) NOT NULL DEFAULT 'prices'"
                    )
                )

        if database_backend == "postgresql":
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_canonical_products_search_text_trgm "
                    "ON canonical_products USING GIN (search_text gin_trgm_ops)"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_canonical_products_normalized_name_trgm "
                    "ON canonical_products USING GIN (normalized_name gin_trgm_ops)"
                )
            )


async def dispose_engine() -> None:
    await engine.dispose()
