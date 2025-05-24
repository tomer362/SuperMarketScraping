import asyncio
from shufersal import get_all_products, fetch_search_results, extract_products
from utils import get_module_logger, setup_logging

# Initialize logging
setup_logging()
logger = get_module_logger('main')


async def test_page(page_num: int):
    """Test fetching and extracting products from a specific page.
    
    Args:
        page_num: The page number to fetch (0-based index)
    """    
    data = await fetch_search_results(f'&page={page_num}')
    products = extract_products(data)
    logger.info(f"Page {page_num}: {len(products)} products found")


async def main():
    data = await get_all_products()
    # Save as JSON for better structure preservation
    import json
    with open('results.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"Saved {len(data)} products to 'results.json'.")


if __name__ == "__main__":
    asyncio.run(main())
