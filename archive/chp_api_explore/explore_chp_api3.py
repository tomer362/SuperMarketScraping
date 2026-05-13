"""
Test if חלב pagination has a real end, and find the minimum term length.
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


async def find_pagination_end(session, u, term, max_pages=100):
    """Find where pagination actually ends for a given term."""
    seen_ids = set()
    last_new_count = -1

    for i in range(max_pages):
        offset = i * 10
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
            ids = {str(x.get("id", "")) for x in real}
            new = ids - seen_ids
            seen_ids.update(ids)
            print(
                f"  offset={offset:5d}: {len(real)} items, {len(new)} new, total unique={len(seen_ids)}"
            )
            if len(new) == 0:
                print(
                    f"  -> DONE! Last unique product at offset {offset - 10}. Total: {len(seen_ids)}"
                )
                return seen_ids
        await asyncio.sleep(0.2)

    print(f"  -> Hit max_pages={max_pages}. Total so far: {len(seen_ids)}")
    return seen_ids


async def test_min_term_length(session, u):
    """What is the minimum number of characters needed?"""
    print("\n=== Minimum term length test ===")
    for term in ["א", "אב", "אבי", "שמן", "חל", "חלב", "00", "000", "0000"]:
        url = (
            f"{BASE_URL}/autocompletion/product_extended"
            f"?term={quote(term)}&from=0&u={u}"
        )
        async with session.get(url, headers=HEADERS) as resp:
            data = await resp.json(content_type=None)
            real = [
                x for x in (data or []) if str(x.get("id", "")) not in ("prev", "next")
            ]
            print(f"  term={term!r} (len={len(term)}): {len(real)} products")
        await asyncio.sleep(0.2)


async def test_very_common_terms(session, u):
    """Test broad common terms that might cover many products."""
    print("\n=== Very common terms ===")
    terms = [
        # Very common 3-letter Hebrew words
        "מוצ",  # motz - product
        "טבע",  # nature
        "פרי",  # fruit
        "ירק",  # vegetable
        "בשר",  # meat
        "דגן",  # grain
        "סלט",  # salad
        "לחם",  # bread
        "שמן",  # oil
        "חלב",  # milk (already tested)
        "מים",  # water
        "קמח",  # flour
        "ביצ",  # eggs
        "גבי",  # cheese
        "יוג",  # yogurt
        "תפו",  # apple/potato
        "עוג",  # cake
        "שוק",  # chocolate
        "קפה",  # coffee
        "תה",  # tea (2 chars!)
        "תה ",  # tea with space
    ]
    for term in terms:
        url = (
            f"{BASE_URL}/autocompletion/product_extended"
            f"?term={quote(term)}&from=0&u={u}"
            f"&shopping_address=%D7%AA%D7%9C+%D7%90%D7%91%D7%99%D7%91"
            f"&shopping_address_city_id=5000"
            f"&shopping_address_street_id=9000"
        )
        async with session.get(url, headers=HEADERS) as resp:
            data = await resp.json(content_type=None)
            real = [
                x for x in (data or []) if str(x.get("id", "")) not in ("prev", "next")
            ]
            print(f"  term={term!r}: {len(real)} products")
        await asyncio.sleep(0.2)


async def main():
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        u = _new_u()

        await test_min_term_length(session, u)
        await asyncio.sleep(1)

        await test_very_common_terms(session, u)
        await asyncio.sleep(1)

        # Test how far חלב actually goes
        print("\n=== Pagination end for 'חלב' ===")
        ids = await find_pagination_end(session, u, "חלב", max_pages=60)
        print(f"Total unique products for 'חלב': {len(ids)}")


if __name__ == "__main__":
    asyncio.run(main())
