"""
Extract product IDs from the chp.co.il sitemap.
The sitemap has ~1473 sub-pages.
"""

import asyncio
import random
import re
from urllib.parse import unquote

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


def extract_product_ids_from_html(html: str) -> set:
    """Extract product IDs from sitemap page HTML."""
    ids = set()
    # Pattern: URLs like /שמן/9000/31/מ/7290027600007_7290000935461/1
    # Product ID is before the /1 at the end
    # Patterns we care about: 7290027600007_xxxx, temp_xxxx, our_xxxx, F_xxxx

    # Find href patterns containing product IDs
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html)
    for href in hrefs:
        decoded = unquote(href)
        # Look for the product ID pattern in the path
        # It appears as the second-to-last path segment before /1
        parts = decoded.rstrip("/").split("/")
        for part in parts:
            if (
                re.match(r"7290027600007_\d+", part)
                or re.match(r"temp_\d+", part)
                or re.match(r"our_\d+", part)
                or re.match(r"F_\w+", part)
                or re.match(r"Q_\w+", part)
            ):
                ids.add(part)
    return ids


async def get_sitemap_page(session, page_num):
    """Fetch one sitemap page."""
    url = f"{BASE_URL}/sitemap/{page_num}"
    async with session.get(
        url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=30)
    ) as resp:
        if resp.status != 200:
            return set()
        html = await resp.text()
        return extract_product_ids_from_html(html)


async def sample_sitemap_pages(session, page_nums):
    """Sample a few sitemap pages to understand the format."""
    print("=== Sampling sitemap pages ===")
    all_ids = set()
    for page in page_nums:
        ids = await get_sitemap_page(session, page)
        print(f"  Page {page}: {len(ids)} product IDs")
        if ids:
            sample = list(ids)[:5]
            for s in sample:
                print(f"    {s}")
        all_ids.update(ids)
        await asyncio.sleep(0.3)
    return all_ids


async def estimate_total_from_sitemap(session):
    """Estimate total unique product IDs in the sitemap."""
    print("\n=== Estimating total products in sitemap ===")
    # Sample pages: 1, 100, 500, 1000, 1200, 1400, 1473
    sample_pages = [1, 2, 3, 50, 100, 200, 500, 750, 1000, 1200, 1400, 1473]
    all_ids = set()
    ids_per_page = []

    for page in sample_pages:
        ids = await get_sitemap_page(session, page)
        new_ids = ids - all_ids
        all_ids.update(ids)
        ids_per_page.append(len(ids))
        print(
            f"  Page {page}: {len(ids)} products, {len(new_ids)} new, total unique so far: {len(all_ids)}"
        )
        await asyncio.sleep(0.3)

    avg_per_page = sum(ids_per_page) / len(ids_per_page) if ids_per_page else 0
    estimated_total = int(avg_per_page * 1473)
    print(f"\n  Average per page: {avg_per_page:.1f}")
    print(f"  Estimated total across 1473 pages: {estimated_total}")
    return all_ids, estimated_total


async def main():
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        # Sample first few pages to understand format
        await sample_sitemap_pages(session, [1, 2, 3])
        await asyncio.sleep(1)

        # Estimate total
        ids, estimated = await estimate_total_from_sitemap(session)
        print(f"\nConclusion: ~{estimated} unique products accessible via sitemap")
        print(f"Unique IDs found in sample: {len(ids)}")


if __name__ == "__main__":
    asyncio.run(main())
