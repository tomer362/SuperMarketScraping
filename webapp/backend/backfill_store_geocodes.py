from __future__ import annotations

import argparse
import asyncio

from db import async_session_factory, create_tables, dispose_engine
from location_service import geocode_missing_store_branches


async def _main(limit: int) -> None:
    await create_tables()
    async with async_session_factory() as session:
        resolved = await geocode_missing_store_branches(session, limit=limit)
        await session.commit()
        print(f"Resolved {resolved} store branch geocode(s).")
    await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill missing store branch coordinates.")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    asyncio.run(_main(args.limit))


if __name__ == "__main__":
    main()
