# SuperMarketScraping

Async Python library and CLI for scraping Israeli online supermarket product catalogues.

Supported chains:

| Chain | Key | Platform | Branches |
|---|---|---|---|
| Tiv Taam (טיב טעם) | `tivtaam` | Stor.ai | 15 |
| Shufersal (שופרסל) | `shufersal` | Shufersal API | 1 (global) |
| Yochananof (יוחננוף) | `yochananof` | Magento GraphQL | ~20 |
| Carrefour (קארפור) | `carrefour` | Stor.ai | 18 |
| Machsanei HaShook (מחסני השוק) | `machsanei` | ZuZ | 9 |

All scrapers return a unified `UnifiedProduct` TypedDict so results can be compared across chains.

---

## Requirements

- Python 3.11+
- `aiohttp`

```
pip install aiohttp
```

---

## CLI usage

```
python3 main.py [options]
```

### Quick examples

```bash
# Scrape all supermarkets, all branches
python3 main.py

# Scrape only Tiv Taam
python3 main.py --supermarkets tivtaam

# Scrape Shufersal and Yochananof in parallel
python3 main.py --supermarkets shufersal yochananof

# Search for a product by name across all chains
python3 main.py --filter-name "חלב"

# Filter by category ID
python3 main.py --supermarkets tivtaam --filter-category 90176

# Filter by exact EAN barcode
python3 main.py --filter-barcode 7290000066882

# Save results to JSON files
python3 main.py --output-dir ./results

# List all known branch IDs and exit
python3 main.py --list-branches
```

### Scraping specific branches

```bash
python3 main.py --supermarkets tivtaam    --tivtaam-branches 924 929
python3 main.py --supermarkets carrefour  --carrefour-branches 3003 3014
python3 main.py --supermarkets yochananof --yochananof-stores s82 s63
python3 main.py --supermarkets machsanei  --machsanei-branches 836 1587
```

### All CLI flags

| Flag | Default | Description |
|---|---|---|
| `--supermarkets NAME...` | all | Which chains to scrape (`tivtaam shufersal yochananof carrefour machsanei`) |
| `--list-branches` | — | Print branch IDs for every chain and exit |
| `--filter-name TEXT` | — | Search products by keyword (passed to each chain's native search) |
| `--filter-category ID` | — | Restrict to a single category ID |
| `--filter-barcode EAN` | — | Return only products matching this EAN barcode |
| `--tivtaam-branches N...` | all | Tiv Taam branch IDs |
| `--carrefour-branches N...` | all | Carrefour branch IDs |
| `--machsanei-branches N...` | all | Machsanei HaShook branch IDs |
| `--yochananof-stores CODE...` | all | Yochananof store codes (e.g. `s82`) |
| `--batch-size N` | 100 | Products per paginated API request |
| `--max-concurrent N` | 15 | Max concurrent API requests per branch |
| `--retry-limit N` | 3 | Max retry attempts per failed request |
| `--base-retry-delay SECS` | 1.0 | Base delay for exponential backoff (delay = base × 2^attempt, capped at 30s) |
| `--output-dir DIR` | — | Directory for JSON result files (subdirs per chain created automatically) |
| `--log-file FILE` | — | Log file path (always written at DEBUG level) |
| `--log-level LEVEL` | INFO | Console log level (`DEBUG INFO WARNING ERROR`) |
| `--quiet` | — | Suppress console output |

### Output format

When `--output-dir` is set, results are saved as:

```
results/
  tivtaam/
    branch_924_tel-aviv_20260325T120000Z.json
    summary_20260325T120000Z.json
  shufersal/
    branch_global_20260325T120000Z.json
    summary_20260325T120000Z.json
  machsanei/
    branch_836_beer-sheva_20260325T120000Z.json
    summary_20260325T120000Z.json
  ...
```

Each branch file is a JSON array of `UnifiedProduct` objects. The summary file contains a `ScrapeResult` with total counts and timing.

---

## Library usage

All scrapers expose the same `scrape()` async function and return a `ScrapeResult`.

### UnifiedProduct

```python
from scrapers.common import UnifiedProduct
```

Every scraper returns products as `UnifiedProduct` TypedDicts with these keys:

| Key | Type | Description |
|---|---|---|
| `chain` | `str` | Chain identifier (`"tivtaam"`, `"shufersal"`, etc.) |
| `store_id` | `str` | Branch/store ID |
| `store_name` | `str` | Branch/store display name |
| `product_id` | `str` | Chain-internal product ID |
| `name` | `str` | Product name (Hebrew) |
| `price` | `float` | Effective price (sale price if active, otherwise regular price) |
| `regular_price` | `float` | Regular (non-sale) shelf price |
| `sale_price` | `float \| None` | Sale price when a promotion is active |
| `discount_percent` | `float \| None` | Discount percentage |
| `barcode` | `str \| None` | EAN barcode |
| `image_url` | `str \| None` | Product image URL |
| `category_ids` | `list[str]` | Category identifiers from the chain's taxonomy |
| `is_weighable` | `bool` | True for products sold by weight |
| `unit_description` | `str \| None` | Human-readable unit (e.g. `"1 ליטר"`) |
| `unit_of_measure` | `str \| None` | Canonical unit string (e.g. `'מ"ל'`, `'גרם'`) |
| `unit_qty` | `float \| None` | Numeric quantity in the product's native unit |
| `unit_qty_si` | `float \| None` | Quantity in SI base unit (ml or g) |
| `unit_dimension` | `str \| None` | `"volume"`, `"mass"`, or `"count"` |
| `price_per_base_unit` | `float \| None` | Price per 100ml or 100g (for comparison); `None` for count items |
| `deal` | `DealInfo \| None` | Deal information when a promotion is active |
| `brand` | `str \| None` | Brand name |
| `manufacturer` | `str \| None` | Manufacturer name |
| `scraped_at` | `str` | ISO 8601 UTC timestamp |

### DealInfo

```python
from scrapers.common import DealInfo
```

`DealInfo` is a `TypedDict(total=False)` — all keys are optional:

| Key | Type | Description |
|---|---|---|
| `has_deal` | `bool` | Always `True` when a `DealInfo` is present |
| `deal_type` | `str` | `"price_reduction"`, `"multi_buy"`, or `"cart_total"` |
| `deal_description` | `str` | Human-readable deal description (Hebrew) |
| `deal_price` | `float \| None` | Total deal price (for multi_buy: price for all N items) |
| `deal_min_qty` | `int \| None` | Minimum quantity required to unlock the deal |
| `deal_price_per_unit` | `float \| None` | Per-item price under the deal |
| `price_per_base_unit` | `float \| None` | Regular price per 100ml/100g |
| `price_per_base_unit_deal` | `float \| None` | Deal price per 100ml/100g |

### ScrapeFilter

```python
from scrapers.common import ScrapeFilter
```

All keys optional:

| Key | Type | Description |
|---|---|---|
| `name_query` | `str` | Keyword search (passed to chain's native search API) |
| `category_ids` | `list[str]` | Restrict to these category IDs |
| `barcode` | `str` | Return only products with this exact EAN barcode |

### ScrapeResult

```python
from scrapers.common import ScrapeResult
```

| Key | Type | Description |
|---|---|---|
| `chain` | `str` | Chain identifier |
| `stores_scraped` | `int` | Number of stores/branches scraped |
| `products_total` | `int` | Total products across all stores |
| `products_by_store` | `dict[str, list[UnifiedProduct]]` | Products keyed by store ID |
| `scraped_at` | `str` | ISO 8601 UTC timestamp |
| `duration_seconds` | `float` | Wall-clock time taken |
| `errors` | `list[str]` | Non-fatal errors encountered during scraping |

---

### Tiv Taam

```python
import asyncio
from scrapers.tivtaam.tivtaam import scrape, ONLINE_BRANCHES

result = asyncio.run(scrape(
    branches=ONLINE_BRANCHES[:2],          # first 2 branches only
    flt={"name_query": "חלב"},             # optional filter
    batch_size=100,
    max_concurrent=15,
    max_retries=3,
    base_retry_delay=1.0,
))
print(result["products_total"])
```

### Shufersal

```python
import asyncio
from scrapers.shufersal.shufersal import scrape

result = asyncio.run(scrape(
    flt={"name_query": "לחם"},
    max_concurrent=10,
    max_retries=3,
    base_retry_delay=1.0,
))
```

Shufersal aggregates all branches into a single `"global"` store.

### Yochananof

```python
import asyncio
from scrapers.yochananof.yochananof import scrape, STORES

result = asyncio.run(scrape(
    stores=STORES[:3],
    flt={"name_query": "גבינה"},
    max_concurrent=8,
    max_retries=3,
    base_retry_delay=1.0,
))
```

### Carrefour

```python
import asyncio
from scrapers.carrefour.carrefour import scrape, ONLINE_BRANCHES

result = asyncio.run(scrape(
    branches=ONLINE_BRANCHES[:1],
    flt={"barcode": "7290000066882"},
    batch_size=100,
    max_concurrent=15,
    max_retries=3,
    base_retry_delay=1.0,
))
```

### Machsanei HaShook

```python
import asyncio
from scrapers.machsanei_hashook.machsanei_hashook import scrape, ONLINE_BRANCHES

result = asyncio.run(scrape(
    branches=ONLINE_BRANCHES,             # all 9 branches
    flt={"name_query": "חלב"},
    batch_size=100,
    max_concurrent=15,
    max_retries=3,
    base_retry_delay=1.0,
))

for store_id, products in result["products_by_store"].items():
    print(f"Branch {store_id}: {len(products)} products")
    for p in products[:3]:
        print(f"  {p['name']} — ₪{p['price']:.2f}")
```

#### Machsanei HaShook branches

| ID | City |
|---|---|
| 3474 | אופקים |
| 1650 | אילת |
| 836 | באר שבע |
| 2039 | להבים |
| 1701 | מיתרים |
| 2933 | נוף הגליל |
| 1587 | נשר |
| 2983 | עין יהב |
| 1370 | קרית גת |

---

## Running tests

```bash
python3 tests/test_smoke.py
```

or with pytest:

```bash
python3 -m pytest tests/test_smoke.py -v
```

Tests are unit tests only — no network calls are made.

---

## Project structure

```
SuperMarketScraping/
├── main.py                              # CLI orchestrator (asyncio.TaskGroup parallelism)
├── utils.py                             # Browser headers, logging helpers
├── config.py                            # Shared configuration constants
├── scrapers/
│   ├── common.py                        # UnifiedProduct, ScrapeResult, ScrapeFilter,
│   │                                    # DealInfo, normalize_unit, with_retry, ...
│   ├── tivtaam/tivtaam.py              # Tiv Taam scraper (Stor.ai platform)
│   ├── shufersal/shufersal.py          # Shufersal scraper
│   ├── yochananof/yochananof.py        # Yochananof scraper (Magento GraphQL)
│   ├── carrefour/carrefour.py          # Carrefour scraper (Stor.ai platform)
│   └── machsanei_hashook/
│       └── machsanei_hashook.py        # Machsanei HaShook scraper (ZuZ platform)
├── tests/
│   └── test_smoke.py                   # 140 unit tests
└── documentation/
    ├── tivtaam_api.md
    ├── carrefour_api.md
    ├── shufersal_api.md
    ├── yochananof_api.md
    └── machsanei_hashook_api.md
```
