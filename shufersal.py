import aiohttp
import asyncio
import logging
import random
from typing import TypedDict, List
from config import CHUNK_SIZE, RETRY_LIMIT, RETRY_DELAY
from utils import get_browser_headers, get_module_logger, setup_logging


# Shufersal-specific constants
SHUFERSAL_BASE_URL: str = 'https://www.shufersal.co.il/online/he'
# URL endpoint for product searches
SHUFERSAL_SEARCH_URL: str = f'{SHUFERSAL_BASE_URL}/search/results?q='


# Setup Types
class ProductPrice(TypedDict):
    value: float


class ExtractedProduct(TypedDict):
    name: str
    price: float
    image_url: str | None


class ProductImage(TypedDict):
    url: str


class Product(TypedDict):
    name: str
    price: ProductPrice
    image: ProductImage


class SearchResponse(TypedDict):
    results: list[Product]


# Initialize logging
setup_logging()
logger = get_module_logger('shufersal')
logger.info("Shufersal module initialized.")


async def fetch_search_results(query: str = '') -> SearchResponse:
    """Fetch search results JSON from Shufersal for the given raw query string.

    Args:
        query: The raw query string (e.g., 'חלב' or 'milk').
              Empty string returns all products.

    Returns:
        JSON response from Shufersal API containing product data.

    Raises:
        ValueError: If the API request fails.
    """
    url = f"{SHUFERSAL_SEARCH_URL}{query}"
    headers = get_browser_headers(SHUFERSAL_BASE_URL)

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status != 200:
                msg = f"Failed to fetch data: HTTP {response.status}"
                raise ValueError(msg)
            return await response.json()


def extract_products(data: SearchResponse) -> list[ExtractedProduct]:
    result: list[ExtractedProduct] = []
    for product in data.get("results", []):
        try:
            name = str(product.get("name", ""))
            price_data = product.get("price", {"value": 0.0})
            price = float(price_data["value"])

            # Find primary image in order: large, medium, small, product
            image_url = None
            formats_to_try = ["large", "medium", "small", "product"]

            for format_type in formats_to_try:
                for img in product.get("images", []):
                    if (
                        img.get("imageType") == "PRIMARY"
                        and img.get("format") == format_type
                    ):
                        image_url = img.get("url")
                        break
                if image_url:
                    break

            if name and price > 0:
                result.append({
                    "name": name,
                    "price": price,
                    "image_url": image_url
                })
        except (KeyError, TypeError, ValueError):
            logger.warning(f'Invalid product data: {product}')
            continue
    return result


# Breaking down long lines for PEP8 compliance
async def fetch_page_with_retry(
    page_num: int, expected_page_size: int
) -> List[Product]:
    retries = 0
    while retries < RETRY_LIMIT:
        try:
            data = await fetch_search_results(f'&page={page_num}')
            if "results" not in data:
                raise ValueError(
                    f"Unexpected API response format for page {page_num}"
                )

            results_count = len(data["results"])
            if results_count > expected_page_size:
                logger.warning(
                    f"Unexpected count on page {page_num}: "
                    f"got {results_count} results, "
                    f"which exceeds expected {expected_page_size}"
                )
            else:
                logger.info(
                    f"Fetched page {page_num}: got {results_count} products"
                )
            return data["results"]

        except Exception as e:
            retries += 1
            logger.error(
                f"Failed to fetch page {page_num} (attempt {retries}): {e}"
            )
            await asyncio.sleep(RETRY_DELAY)

    logger.error(
        f"Failed to fetch page {page_num} after {RETRY_LIMIT} attempts."
    )
    return []  # Return an empty list if all retries fail


async def get_all_products() -> list[ExtractedProduct]:
    """Fetch and process all products from Shufersal,
    handling pagination async in chunks.

    Returns:
        List of products with name, price, and image URL.

    Raises:
        ValueError: If the API request fails or response format is unexpected.
    """
    try:
        # Fetch first page to get pagination info only
        metadata: SearchResponse = await fetch_search_results('')
        if "results" not in metadata or "pagination" not in metadata:
            logger.error(
                "Unexpected API response format: "
                "missing 'results' or 'pagination'"
            )
            raise ValueError("Unexpected API response format")

        pagination = metadata["pagination"]
        # Ensure required pagination fields are present
        if (
            "pageSize" not in pagination
            or "numberOfPages" not in pagination
            or "totalNumberOfResults" not in pagination
        ):
            logger.error(
                "Unexpected API response format: missing pagination fields"
            )
            raise ValueError(
                "Unexpected API response format: missing pagination fields"
            )

        # Direct access to validated fields
        page_size = int(pagination["pageSize"])
        number_of_pages = int(pagination["numberOfPages"])
        total_results = int(pagination["totalNumberOfResults"])

        logger.info(
            "Fetch and process all products from Shufersal, "
            "handling pagination async in chunks."
        )
        logger.info(
            f"Pagination: pageSize={page_size}, "
            f"numberOfPages={number_of_pages}, "
            f"totalResults={total_results}"
        )

        products: list[ExtractedProduct] = []

        # Now fetch page 0 like any other page
        first_page = await fetch_page_with_retry(0, page_size)
        products.extend(extract_products({"results": first_page}))

        # Fetch remaining pages in async chunks
        page_numbers = list(range(1, number_of_pages))
        for i in range(0, len(page_numbers), CHUNK_SIZE):
            chunk = page_numbers[i:i+CHUNK_SIZE]
            tasks = [
                fetch_page_with_retry(page_num, page_size)
                for page_num in chunk
            ]
            chunk_results = await asyncio.gather(*tasks)
            for page_products in chunk_results:
                # Create a SearchResponse-like structure for extract_products
                products.extend(
                    extract_products({"results": page_products})
                )

            # Add a random delay between 0.5 and 2 seconds between chunks
            await asyncio.sleep(random.uniform(0.5, 2.0))

        total_found = len(products)
        if total_found < total_results:
            logger.warning(
                f"Found fewer products than expected: {total_found} out of "
                f"{total_results} products"
            )
        elif total_found > total_results:
            logger.warning(
                f"Found more products than expected: {total_found} out of "
                f"{total_results} products"
            )
        else:
            logger.info(
                f"Successfully fetched all {total_found} products"
            )
        return products

    except aiohttp.ClientError as err:
        logger.error(f"Network error: {err}")
        raise ValueError(f"Network error: {err}") from err
