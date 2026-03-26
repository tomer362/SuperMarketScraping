"""
update_stores.py — refresh the Carrefour branch list from the live API.

Usage:
    python3 -m scrapers.carrefour.update_stores
    # or
    python3 scrapers/carrefour/update_stores.py

Prints the updated ONLINE_BRANCHES list to stdout as Python source so you can
paste it back into carrefour.py, and also saves a JSON snapshot to
scrapers/carrefour/branches.json for reference.
"""

import asyncio
import json
import ssl
import sys
from pathlib import Path

import aiohttp

HERE = Path(__file__).parent


def _make_ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


async def main() -> None:
    from scrapers.carrefour.carrefour import fetch_branches, _make_ssl_context

    connector = aiohttp.TCPConnector(ssl=_make_ssl_context())
    async with aiohttp.ClientSession(connector=connector) as session:
        branches = await fetch_branches(session)

    if not branches:
        print("ERROR: no branches returned — check network / API", file=sys.stderr)
        sys.exit(1)

    # Save JSON snapshot
    json_path = HERE / "branches.json"
    json_path.write_text(
        json.dumps(branches, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Saved {len(branches)} branches to {json_path}", file=sys.stderr)

    # Print Python source for copy-paste into carrefour.py
    print("\nONLINE_BRANCHES: List[Branch] = [")
    for b in branches:
        name = b["name"].replace('"', '\\"')
        city = b["city"].replace('"', '\\"')
        location = b["location"].replace('"', '\\"')
        print(
            f'    {{"id": {b["id"]}, "name": "{name}", '
            f'"city": "{city}", "location": "{location}"}},'
        )
    print("]")


if __name__ == "__main__":
    asyncio.run(main())
