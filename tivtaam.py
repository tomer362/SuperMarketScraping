import logging
import asyncio
import aiohttp
from typing import List, Dict, Any, TypedDict
from config import RETRY_LIMIT, RETRY_DELAY
from utils import get_browser_headers


class Product(TypedDict):
    name: str
    price: float
    image_url: str | None
    category_id: str
    product_id: str
    unit_type: str
    barcode: str


# Category IDs with their descriptions
CATEGORIES = [
    "90066",   # Vegetables
    "90069",   # Fruits
    "90073",   # Vegetables and Herbs
    "90082",   # Fresh Meat
    "90083",   # Fresh Poultry
    "90084",   # Frozen Meat
    "90085",   # Frozen Chicken
    "90100",   # Fish
    "90103",   # Seafood
    "90173",   # Eggs
    "90176",   # Dairy Products
    "90184",   # Baking and Cooking Products
    "95874",   # Refrigerated Cakes and Desserts
    "90107",   # Sausages and Pastrami
    "90191",   # Packaged Salads
    "90121",   # Spreads and Seasonings
    "90113",   # Deli Cheeses
    "90131",   # Pickles
    "90124",   # Fish and Seafood Delicacies
    "92837",   # Ready-made Food
    "90205",   # Bakery
    "90199",   # Breads and Challahs
    "90210",   # Various Pastries
    "90215",   # Rice Cakes/Crackers/Rusks
    "90219",   # Matzot
    "90221",   # Breadcrumbs and Croutons
    "90135",   # Canned Goods
    "90150",   # Sauces and Vinegars
    "90157",   # Spreads
    "90167",   # Soups and Stews
    "90245",   # Rice Legumes and Grains
    "90225",   # Baking
    "90144",   # Pasta and Noodles
    "90236",   # Basic Products
    "92072",   # Special Cooking
    "90076",   # Nuts and Almonds
    "96394",   # Dried Fruits
    "90250",   # Spices
    "90254",   # Breakfast Cereals
    "90255",   # Granola and Muesli
    "90256",   # Oatmeal
    "90257",   # Health Snacks
    "90258",   # Halva and Turkish Delight
    "90261",   # Chocolate and Candies
    "90269",   # Snacks
    "90271",   # Cakes and Cookies
    "90276",   # Frozen Vegetables
    "90277",   # Doughs
    "90282",   # Frozen Food
    "90283",   # Frozen Concentrates
    "90281",   # Frozen Fruits
    "119730",   # Ice Cream
    "90285",   # Juices and Nectars
    "90288",   # Carbonated Drinks
    "90292",   # Concentrates
    "90294",   # Water
    "90297",   # Iced Tea
    "90299",   # Hot Drinks
    "90303",   # Tea and Infusions
    "90309",   # Wine
    "90315",   # Beers
    "90318",   # Alcohol
    "90361",   # Bath Products
    "90365",   # Oral Hygiene
    "90370",   # Baby Products
    "90376",   # Hair Colors and Care
    "90380",   # Body and Face Care
    "90390",   # Feminine Hygiene
    "90401",   # Protection
    "90404",   # Medical Equipment
    "94410",   # Organic Food
    "90333",   # Gluten-free Food
    "90342",   # Sugar-free Food
    "90350",   # Plant-based Drinks and Delicacies
    "90355",   # Soy, Tofu, Seitan and Cheese Alternatives
    "96535",   # Natural Pharmacy
    "92060",   # Home and Leisure
    "90434",   # Electrical and Lighting Accessories
    "123135",   # Novy God and Christmas Products
    "90410",   # Cleaning Products
    "90415",   # Paper Products
    "90420",   # Disposables
    "90427",   # Pet Products
    "90440",   # Insecticides
    "92848",   # BBQ Materials
    "92849"    # Cigarettes and Matches
]

logger = logging.getLogger(__name__)

BASE_URL = "https://www.tivtaam.co.il"
API_URL = f"{BASE_URL}/v2/retailers/1440/branches/677/categories"
PRODUCT_URL = f"{BASE_URL}/v2/retailers/1440/branches/677/categories"


async def fetch_category_products(
    session: aiohttp.ClientSession,
    category_id: str,
    start_from: int = 0,
    size: int = 1
) -> Dict[str, Any]:
    """Fetch products for a specific category from an index."""
    try:
        headers = get_browser_headers(BASE_URL)
        
        # Build the URL with query parameters
        query_params = (
            f"appId=4"
            f"&categorySort={{%22sortType%22:2}}"
            f"&categorySort={{%22sortType%22:7}}"
            f"&filters={{%22mustNot%22:{{%22term%22:"
            f"{{%22branch.isOutOfStock%22:true}}}}}}"
            f"&from={start_from}"
            f"&languageId=1"
            f"&minScore=0"
            f"&size={size}"
        )
        url = f"{PRODUCT_URL}/{category_id}/products?{query_params}"
        
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                return await response.json()
            logger.error(
                f"Failed to fetch category {category_id} "
                f"from={start_from}, size={size}. Status: {response.status}"
            )
            return {}
    except Exception as e:
        logger.error(
            f"Error fetching category {category_id} "
            f"from={start_from}, size={size}: {str(e)}"
        )
        return {}


async def get_category_total(
    session: aiohttp.ClientSession,
    category_id: str
) -> int:
    """Get the total number of products in a category."""
    data = await fetch_category_products(session, category_id, 0, 1)
    return data.get('total', 0)


async def scrape_category(
    session: aiohttp.ClientSession,
    category_id: str,
    batch_size: int = 100
) -> List[Product]:
    """Scrape all products from a category using from/size pagination."""
    all_products: List[Product] = []
    retries = 0
    
    # First, get the total number of products
    total_products = await get_category_total(session, category_id)
    if total_products == 0:
        logger.warning(f"No products found in category {category_id}")
        return all_products
        
    logger.info(f"Found {total_products} products in category {category_id}")
    
    # Fetch products in batches
    start_from = 0
    while start_from < total_products and retries < RETRY_LIMIT:
        try:
            data = await fetch_category_products(
                session, category_id, start_from, batch_size
            )
            if not data:
                break
                
            products = extract_products(data)
            if not products:
                break
                
            all_products.extend(products)
            logger.info(
                f"Category {category_id}: Fetched {len(products)} products "
                f"({start_from + len(products)}/{total_products})"
            )
            
            start_from += len(products)
            retries = 0  # Reset retries on successful fetch
            
        except Exception as e:
            retries += 1
            logger.error(
                f"Error scraping category {category_id} "
                f"(attempt {retries}): {str(e)}"
            )
            if retries >= RETRY_LIMIT:
                logger.error(f"Max retries reached for category {category_id}")
                break
            
            await asyncio.sleep(RETRY_DELAY)
    
    # Verify we got all products
    if len(all_products) != total_products:
        logger.warning(
            f"Category {category_id}: Expected {total_products} products "
            f"but got {len(all_products)}"
        )
    
    return all_products


def extract_products(data: Dict[str, Any]) -> List[Product]:
    """Extract product information from the API response."""
    products: List[Product] = []
    try:
        items = data.get('data', [])
        for item in items:
            if 'regularPrice' not in item:
                logger.error(
                    f"Missing regularPrice for product {item.get('name', 'Unknown')} "
                    f"ID: {item.get('id', 'Unknown')}"
                )
                raise ValueError("regularPrice is required but missing")

            unit_name = item.get('measurement_unit', {}).get('name', '')
            product: Product = {
                'name': str(item.get('name', '')),
                'price': float(item.get('regularPrice', 0)),
                'image_url': item.get('image', {}).get('url'),
                'category_id': str(item.get('category_id', '')),
                'product_id': str(item.get('id', '')),
                'unit_type': str(unit_name),
                'barcode': str(item.get('barcode', ''))
            }
            products.append(product)
    except Exception as e:
        logger.error(f"Error extracting products: {str(e)}")
        raise  # Re-raise the exception to handle it in the caller
    
    return products


async def scrape_all_categories() -> List[Product]:
    """Scrape products from all categories."""
    all_products: List[Product] = []
    async with aiohttp.ClientSession() as session:
        for category_id in CATEGORIES:
            logger.info(f"Starting to scrape category {category_id}")
            products = await scrape_category(session, category_id)
            all_products.extend(products)
            logger.info(
                f"Completed category {category_id} - "
                f"Total products: {len(products)}"
            )
            
    return all_products