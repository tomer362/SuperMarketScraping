"""
Exploration script for chp.co.il API — understanding what endpoints exist
and whether a "fetch all products" approach is feasible.
"""

import asyncio
import json
import random
import sys
import time
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


async def test_empty_term(session, u):
    """Test what comes back when we search for empty string."""
    print("\n=== Test: empty term search ===")
    for from_offset in [0, 10, 20]:
        url = (
            f"{BASE_URL}/autocompletion/product_extended"
            f"?term="
            f"&from={from_offset}"
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
            print(
                f"  from={from_offset}: {len(data or [])} items total, {len(real)} real products"
            )
            if real:
                print(f"    First: {real[0]}")
            if len(data or []) == 0:
                print("  -> Empty result! No products returned for empty term.")
                break
        await asyncio.sleep(1)


async def test_common_hebrew_letters(session, u):
    """Test what single Hebrew letters or common prefixes return."""
    print("\n=== Test: single Hebrew letters ===")
    letters = ["א", "ב", "ג", "ד", "ח", "מ", "ש", "ת", "ל", "כ"]
    for letter in letters:
        url = (
            f"{BASE_URL}/autocompletion/product_extended"
            f"?term={quote(letter)}"
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
            print(f"  letter={letter!r}: {len(real)} products")
        await asyncio.sleep(0.5)


async def test_browse_endpoint(session, u):
    """Test if there's a browse/catalog endpoint."""
    print("\n=== Test: browse/catalog endpoints ===")
    endpoints = [
        "/main_page/get_products",
        "/main_page/browse",
        "/main_page/catalog",
        "/main_page/all_products",
        "/autocompletion/product_extended?term=%20&from=0",  # space
        "/autocompletion/product_extended?term=*&from=0",  # wildcard
        "/autocompletion/product_extended?term=.&from=0",  # dot
    ]
    for ep in endpoints:
        url = f"{BASE_URL}{ep}&u={u}" if "?" in ep else f"{BASE_URL}{ep}?u={u}"
        try:
            async with session.get(
                url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                ct = resp.content_type
                body = await resp.text()
                print(f"  {ep}: status={resp.status} ct={ct} len={len(body)}")
                if resp.status == 200 and len(body) > 10:
                    try:
                        data = json.loads(body)
                        if isinstance(data, list) and len(data) > 0:
                            real = [
                                x
                                for x in data
                                if str(x.get("id", "")) not in ("prev", "next")
                            ]
                            print(f"    -> {len(real)} real items")
                    except Exception:
                        pass
        except Exception as e:
            print(f"  {ep}: ERROR {e}")
        await asyncio.sleep(0.5)


async def test_categories_endpoint(session, u):
    """Check if categories exist."""
    print("\n=== Test: categories ===")
    cat_endpoints = [
        "/main_page/categories",
        "/main_page/get_categories",
        "/autocompletion/category",
        "/autocompletion/categories",
    ]
    for ep in cat_endpoints:
        url = f"{BASE_URL}{ep}?u={u}"
        try:
            async with session.get(
                url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                body = await resp.text()
                print(f"  {ep}: status={resp.status} len={len(body)}")
        except Exception as e:
            print(f"  {ep}: ERROR {e}")
        await asyncio.sleep(0.3)


async def test_pagination_depth(session, u):
    """How deep can we paginate on a common term?"""
    print("\n=== Test: pagination depth for common term 'חלב' ===")
    term = "חלב"  # milk - very common
    offset = 0
    total_real = 0
    while offset < 200:
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
            total_real += len(real)
            print(
                f"  from={offset}: {len(data or [])} total items, {len(real)} real -> cumulative={total_real}"
            )
            if len(real) == 0:
                print("  -> No more results!")
                break
        offset += 10
        await asyncio.sleep(0.5)


async def test_single_letter_depth(session, u):
    """Paginate through a single letter to see total products available."""
    print("\n=== Test: pagination depth for letter 'א' ===")
    term = "א"
    offset = 0
    total_real = 0
    while offset < 500:
        url = (
            f"{BASE_URL}/autocompletion/product_extended"
            f"?term={quote(term)}"
            f"&from={offset}"
            f"&u={u}"
        )
        async with session.get(url, headers=HEADERS) as resp:
            data = await resp.json(content_type=None)
            real = [
                x for x in (data or []) if str(x.get("id", "")) not in ("prev", "next")
            ]
            total_real += len(real)
            print(
                f"  from={offset}: {len(data or [])} items, {len(real)} real -> cumulative={total_real}"
            )
            if len(real) == 0:
                print("  -> No more results!")
                break
        offset += 10
        await asyncio.sleep(0.5)
    return total_real


async def main():
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        u = _new_u()
        print(f"Session u={u}")

        await test_empty_term(session, u)
        await asyncio.sleep(2)

        await test_browse_endpoint(session, u)
        await asyncio.sleep(2)

        await test_common_hebrew_letters(session, u)
        await asyncio.sleep(2)

        await test_categories_endpoint(session, u)
        await asyncio.sleep(2)

        await test_pagination_depth(session, u)
        await asyncio.sleep(2)

        total = await test_single_letter_depth(session, u)
        print(f"\nTotal products for 'א': {total}")


if __name__ == "__main__":
    asyncio.run(main())
