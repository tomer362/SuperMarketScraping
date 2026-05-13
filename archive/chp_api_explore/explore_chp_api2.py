"""
Deeper CHP API exploration — understanding pagination and if "all products" is possible.
"""

import asyncio
import json
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


async def check_if_pagination_cycles(session, u):
    """
    For term='חלב', collect all product IDs across many pages.
    Do they cycle, or are they unique?
    """
    print("\n=== Checking if pagination returns unique products ===")
    term = "חלב"
    seen_ids = set()
    new_ids_per_page = []

    for offset in range(0, 150, 10):
        url = (
            f"{BASE_URL}/autocompletion/product_extended"
            f"?term={quote(term)}"
            f"&from={offset}"
            f"&u={u}"
            f"&shopping_address=%D7%AA%D7%9C+%D7%90%D7%91%D7%99%D7%91"
            f"&shopping_address_city_id=5000"
            f"&shopping_address_street_id=9000"
        )
        async with session.get(url, headers=HEADERS) as resp:
            data = await resp.json(content_type=None)
            real = [
                x for x in (data or []) if str(x.get("id", "")) not in ("prev", "next")
            ]
            ids_this_page = {str(x.get("id", "")) for x in real}
            new_this_page = ids_this_page - seen_ids
            seen_ids.update(ids_this_page)
            new_ids_per_page.append(len(new_this_page))
            print(
                f"  offset={offset:4d}: {len(real)} items, {len(new_this_page)} NEW, total unique={len(seen_ids)}"
            )
            if len(new_this_page) == 0 and offset > 0:
                print("  -> NO NEW PRODUCTS — pagination cycles!")
                break
        await asyncio.sleep(0.3)

    print(f"\nTotal unique product IDs found: {len(seen_ids)}")
    return seen_ids


async def try_two_letter_terms(session, u):
    """Try two-letter Hebrew combinations to get unique sets."""
    print("\n=== Two-letter Hebrew terms ===")
    # Common Hebrew two-letter prefixes for food products
    prefixes = [
        "חל",
        "ית",
        "שמ",
        "גב",
        "בש",
        "לח",
        "קמ",
        "שמן",
        "דג",
        "ביצ",
        "עוג",
        "מים",
    ]
    total_unique = set()
    for prefix in prefixes:
        url = (
            f"{BASE_URL}/autocompletion/product_extended"
            f"?term={quote(prefix)}"
            f"&from=0"
            f"&u={u}"
            f"&shopping_address=%D7%AA%D7%9C+%D7%90%D7%91%D7%99%D7%91"
            f"&shopping_address_city_id=5000"
            f"&shopping_address_street_id=9000"
        )
        async with session.get(url, headers=HEADERS) as resp:
            data = await resp.json(content_type=None)
            real = [
                x for x in (data or []) if str(x.get("id", "")) not in ("prev", "next")
            ]
            ids = {str(x.get("id", "")) for x in real}
            new = ids - total_unique
            total_unique.update(ids)
            print(f"  prefix={prefix!r}: {len(real)} items, {len(new)} new unique")
        await asyncio.sleep(0.3)
    print(f"  Total unique across prefixes: {len(total_unique)}")


async def inspect_main_page(session, u):
    """Inspect the main page HTML to find any catalog/category links."""
    print("\n=== Inspecting main page for catalog structure ===")
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
        print(f"  Main page: {len(body)} bytes")
        # Look for interesting endpoints
        import re

        endpoints = re.findall(
            r'(?:href|src|action|url)["\s]*[:=]["\s]*["\']([/][^"\'<>]+)["\']', body
        )
        for ep in sorted(set(endpoints)):
            if any(
                k in ep
                for k in [
                    "api",
                    "product",
                    "category",
                    "catalog",
                    "browse",
                    "search",
                    "all",
                ]
            ):
                print(f"    {ep}")

        # Look for JavaScript variables with data
        js_vars = re.findall(r"var\s+(\w+)\s*=\s*({[^;]+}|\[[^\]]+\])", body[:5000])
        for name, val in js_vars[:10]:
            print(f"    JS var: {name} = {val[:100]}")


async def try_product_search_api(session, u):
    """Try alternative search API paths."""
    print("\n=== Testing alternative search API paths ===")
    test_cases = [
        # longer min-chars
        ("חל", "chol 2-letter"),
        ("חלב", "chalav 3-letter"),
        ("חל ת", "partial space"),
        # numeric / barcode
        ("729", "barcode prefix"),
        ("7290", "barcode prefix 4"),
    ]
    for term, desc in test_cases:
        url = (
            f"{BASE_URL}/autocompletion/product_extended"
            f"?term={quote(term)}"
            f"&from=0"
            f"&u={u}"
        )
        async with session.get(url, headers=HEADERS) as resp:
            data = await resp.json(content_type=None)
            real = [
                x for x in (data or []) if str(x.get("id", "")) not in ("prev", "next")
            ]
            print(f"  {desc!r}: {len(real)} products")
            if real:
                print(f"    First: {real[0].get('label', '')[:80]}")
        await asyncio.sleep(0.3)


async def main():
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        u = _new_u()
        print(f"u={u}")

        seen_ids = await check_if_pagination_cycles(session, u)
        await asyncio.sleep(1)

        await try_two_letter_terms(session, u)
        await asyncio.sleep(1)

        await try_product_search_api(session, u)
        await asyncio.sleep(1)

        await inspect_main_page(session, u)


if __name__ == "__main__":
    asyncio.run(main())
