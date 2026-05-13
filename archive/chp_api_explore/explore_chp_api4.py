"""
Find if חלב pagination ever ends, and explore if there's a pattern to reach all products.
Test larger offsets and also check if multiple search terms collectively cover all products.
"""

import asyncio
import random
from urllib.parse import quote

import aiohttp

BASE_URL = "https://chp.co.il"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://chp.co.il/",
}


def _new_u():
    return round(random.random(), 15)


async def find_true_end(session, u, term="חלב"):
    """Jump to large offsets to find where it actually ends."""
    print(f"\n=== Finding true end for term={term!r} ===")
    test_offsets = [1000, 2000, 5000, 10000, 20000, 50000]

    for offset in test_offsets:
        url = (
            f"{BASE_URL}/autocompletion/product_extended"
            f"?term={quote(term)}&from={offset}&u={u}"
            f"&shopping_address=%D7%AA%D7%9C+%D7%90%D7%91%D7%99%D7%91"
            f"&shopping_address_city_id=5000"
            f"&shopping_address_street_id=9000"
        )
        async with session.get(url, headers=HEADERS) as resp:
            data = await resp.json(content_type=None)
            real = [
                x for x in (data or []) if str(x.get("id", "")) not in ("prev", "next")
            ]
            print(f"  offset={offset}: {len(real)} products")
            if real:
                print(f"    First ID: {real[0].get('id', '')}")
        await asyncio.sleep(0.3)


async def check_barcode_search(session, u):
    """Check if barcodes can be searched to get all products."""
    print("\n=== Barcode range search ===")
    # Israeli products commonly have barcodes starting with 729
    for barcode_prefix in ["7290000", "7290001", "7290002", "7290100", "7290200"]:
        url = (
            f"{BASE_URL}/autocompletion/product_extended"
            f"?term={barcode_prefix}&from=0&u={u}"
        )
        async with session.get(url, headers=HEADERS) as resp:
            data = await resp.json(content_type=None)
            real = [
                x for x in (data or []) if str(x.get("id", "")) not in ("prev", "next")
            ]
            print(f"  barcode_prefix={barcode_prefix!r}: {len(real)} products")
            if real:
                for p in real[:3]:
                    print(f"    ID={p.get('id', '')} label={p.get('label', '')[:50]}")
        await asyncio.sleep(0.3)


async def check_main_page_for_categories(session, u):
    """Look at the main page HTML more carefully for category or browse links."""
    print("\n=== Main page deep inspection ===")
    url = BASE_URL + "/"
    async with session.get(
        url,
        headers={
            "User-Agent": HEADERS["User-Agent"],
            "Accept": "text/html",
            "Accept-Language": HEADERS["Accept-Language"],
        },
    ) as resp:
        body = await resp.text()

    import re

    # Find all API calls
    api_calls = re.findall(r"'(/[^']+)'|\"(/[^\"]+)\"", body)
    paths = set()
    for g1, g2 in api_calls:
        p = g1 or g2
        if p.startswith("/") and not p.startswith("//") and len(p) > 3:
            paths.add(p)

    print("  Unique paths found in main page:")
    for p in sorted(paths):
        print(f"    {p}")

    # Find any AJAX/fetch calls
    ajax = re.findall(r'(?:url|href)\s*:\s*["\']([^"\']+)["\']', body)
    print("\n  AJAX url references:")
    for a in sorted(set(ajax)):
        print(f"    {a}")


async def main():
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        u = _new_u()

        await find_true_end(session, u, "חלב")
        await asyncio.sleep(1)

        await check_barcode_search(session, u)
        await asyncio.sleep(1)

        await check_main_page_for_categories(session, u)


if __name__ == "__main__":
    asyncio.run(main())
