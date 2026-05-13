"""
Examine the sitemap and understand how deep barcode scanning goes.
"""

import asyncio
import json
import random
import re
from urllib.parse import quote

import aiohttp

BASE_URL = "https://chp.co.il"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://chp.co.il/",
}
JSON_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": HEADERS["Accept-Language"],
    "X-Requested-With": "XMLHttpRequest",
    "Referer": BASE_URL + "/",
}


def _new_u():
    return round(random.random(), 15)


async def inspect_sitemap(session):
    """Inspect the sitemap for product URLs."""
    print("=== Sitemap inspection ===")
    url = BASE_URL + "/sitemap"
    async with session.get(
        url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=30)
    ) as resp:
        body = await resp.text()

    print(f"Sitemap length: {len(body)} bytes")

    # Find all unique paths
    paths = re.findall(r'href=["\']([^"\']+)["\']', body)
    paths += re.findall(r"<loc>([^<]+)</loc>", body)

    unique_paths = sorted(set(paths))
    print(f"Total paths found: {len(unique_paths)}")

    # Categorize them
    categories = {}
    for p in unique_paths:
        if p.startswith("http"):
            p = p.replace(BASE_URL, "")
        parts = p.strip("/").split("/")
        prefix = "/" + parts[0] if parts else "/"
        categories.setdefault(prefix, []).append(p)

    for prefix, paths_list in sorted(categories.items()):
        print(f"\n  {prefix}: {len(paths_list)} paths")
        for p in paths_list[:5]:
            print(f"    {p}")
        if len(paths_list) > 5:
            print(f"    ... and {len(paths_list) - 5} more")


async def test_barcode_pagination(session, u, prefix="7290000"):
    """How many unique products does a 7-digit barcode prefix return with pagination?"""
    print(f"\n=== Barcode prefix pagination for {prefix!r} ===")
    seen = set()
    for offset in range(0, 200, 10):
        url = (
            f"{BASE_URL}/autocompletion/product_extended"
            f"?term={prefix}&from={offset}&u={u}"
        )
        async with session.get(url, headers=JSON_HEADERS) as resp:
            data = await resp.json(content_type=None)
            real = [
                x for x in (data or []) if str(x.get("id", "")) not in ("prev", "next")
            ]
            new = [x for x in real if x.get("id") not in seen]
            seen.update(x.get("id") for x in real)
            print(
                f"  offset={offset}: {len(real)} items, {len(new)} new, total={len(seen)}"
            )
            if len(new) == 0:
                print(f"  -> Done! {len(seen)} unique products for prefix {prefix!r}")
                return seen
        await asyncio.sleep(0.2)
    return seen


async def estimate_total_products(session, u):
    """Estimate total products by testing a sample of barcode prefixes."""
    print("\n=== Estimating total product count via barcode sampling ===")
    # Test 20 different 7-digit prefixes and average their counts
    total_unique = set()

    # Try different first 7 digits: 7290xxx, 7291xxx, 7292xxx, ...
    test_prefixes = [
        "7290",
        "7291",
        "7292",
        "7293",
        "7294",
        "7295",
        "7296",
        "7297",
        "7298",
        "7299",
    ]

    for prefix4 in test_prefixes:
        # Test first two 7-digit combos of this prefix
        for suffix in ["000", "050", "100"]:
            prefix = prefix4 + suffix
            url = (
                f"{BASE_URL}/autocompletion/product_extended?term={prefix}&from=0&u={u}"
            )
            async with session.get(url, headers=JSON_HEADERS) as resp:
                data = await resp.json(content_type=None)
                real = [
                    x
                    for x in (data or [])
                    if str(x.get("id", "")) not in ("prev", "next")
                ]
                new = [x for x in real if x.get("id") not in total_unique]
                total_unique.update(x.get("id") for x in real)
                print(
                    f"  prefix={prefix}: {len(real)} products, {len(new)} new, total_unique={len(total_unique)}"
                )
            await asyncio.sleep(0.15)


async def main():
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        u = _new_u()

        await inspect_sitemap(session)
        await asyncio.sleep(1)

        await test_barcode_pagination(session, u, "7290000")
        await asyncio.sleep(1)

        await estimate_total_products(session, u)


if __name__ == "__main__":
    asyncio.run(main())
