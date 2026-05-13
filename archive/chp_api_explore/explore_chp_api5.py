"""
Test /main_page/get_product_by_description and barcode-range enumeration strategy.
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


async def test_get_product_by_description(session, u):
    """Test the /main_page/get_product_by_description endpoint."""
    print("\n=== Testing /main_page/get_product_by_description ===")

    # Try GET with various params
    test_cases = [
        "?description=חלב",
        "?description=חלב+תנובה",
        "?q=חלב",
        "?term=חלב",
        "?product_name=חלב",
        "",  # bare endpoint
    ]
    for params in test_cases:
        url = f"{BASE_URL}/main_page/get_product_by_description{params}&u={u}"
        try:
            async with session.get(
                url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                ct = resp.content_type
                body = await resp.text()
                print(f"  {params!r}: status={resp.status} ct={ct} len={len(body)}")
                if len(body) < 200 and len(body) > 2:
                    print(f"    Body: {body[:200]}")
                elif "application/json" in ct:
                    try:
                        data = json.loads(body)
                        print(f"    JSON: {str(data)[:200]}")
                    except Exception:
                        pass
        except Exception as e:
            print(f"  {params!r}: ERROR {e}")
        await asyncio.sleep(0.3)

    # Try POST
    print("\n  POST attempts:")
    for payload in [{"description": "חלב"}, {"q": "חלב"}, {"term": "חלב"}]:
        try:
            async with session.post(
                f"{BASE_URL}/main_page/get_product_by_description",
                json=payload,
                headers={**HEADERS, "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                ct = resp.content_type
                body = await resp.text()
                print(f"  POST {payload}: status={resp.status} ct={ct} len={len(body)}")
        except Exception as e:
            print(f"  POST {payload}: ERROR {e}")
        await asyncio.sleep(0.3)


async def test_barcode_scan_strategy(session, u):
    """Test scanning barcode ranges to find products."""
    print("\n=== Testing barcode prefix enumeration strategy ===")

    # Israeli product barcodes mostly start with 729
    # Test range of 7290000 to 7290009 to see how many hit
    hits = 0
    total = 0
    seen_ids = set()

    for prefix_suffix in range(0, 10):
        prefix = f"729000{prefix_suffix}"
        url = f"{BASE_URL}/autocompletion/product_extended?term={prefix}&from=0&u={u}"
        async with session.get(url, headers=HEADERS) as resp:
            data = await resp.json(content_type=None)
            real = [
                x for x in (data or []) if str(x.get("id", "")) not in ("prev", "next")
            ]
            new = [x for x in real if x.get("id") not in seen_ids]
            seen_ids.update(x.get("id") for x in real)
            total += 1
            if real:
                hits += 1
            print(f"  prefix={prefix}: {len(real)} products, {len(new)} new")
        await asyncio.sleep(0.2)

    print(f"  {hits}/{total} prefixes returned products")
    print(f"  Total unique so far: {len(seen_ids)}")

    # How many 7-digit barcode prefixes exist? 729xxxx has 1000 combinations (0-999)
    # If ~80% return products and each returns 10 unique → ~8000 products via this method
    print(f"\n  Extrapolation:")
    print(f"  - 729xxxx has 1000 combinations (7290000-7290999)")
    print(
        f"  - If {hits / total * 100:.0f}% return products = {int(hits / total * 1000)} hits"
    )
    print(
        f"  - At 10 products/prefix = ~{int(hits / total * 1000 * 10)} products reachable via 7290xxx"
    )
    print(
        f"  - With pagination (each prefix ~100 unique) = ~{int(hits / total * 1000 * 100)} products"
    )


async def test_sitemap(session):
    """Check if there's a sitemap with product pages."""
    print("\n=== Testing sitemap ===")
    for url_path in ["/sitemap", "/sitemap.xml", "/sitemap.xml.gz"]:
        url = BASE_URL + url_path
        try:
            async with session.get(
                url,
                headers={
                    "User-Agent": HEADERS["User-Agent"],
                    "Accept": "*/*",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                ct = resp.content_type
                body = await resp.text()
                print(f"  {url_path}: status={resp.status} ct={ct} len={len(body)}")
                if len(body) < 500 and len(body) > 2:
                    print(f"    Body: {body[:500]}")
        except Exception as e:
            print(f"  {url_path}: ERROR {e}")
        await asyncio.sleep(0.3)


async def main():
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        u = _new_u()

        await test_get_product_by_description(session, u)
        await asyncio.sleep(1)

        await test_barcode_scan_strategy(session, u)
        await asyncio.sleep(1)

        await test_sitemap(session)


if __name__ == "__main__":
    asyncio.run(main())
