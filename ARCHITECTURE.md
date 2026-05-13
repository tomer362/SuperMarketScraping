# SuperMarketScraping Architecture

## Overview

**SuperMarketScraping** is an async Python scraper library for Israeli supermarket chains. It provides a unified interface to browse and search products across 10+ chains, returning normalized product data via a common schema.

- **Language**: Python 3.11+
- **Concurrency**: `asyncio` with bounded parallelism via `TaskGroup`
- **HTTP**: `aiohttp` with exponential backoff retry logic
- **Output**: Unified `UnifiedProduct` TypedDict across all chains
- **Deployment**: CLI (`python3 main.py`), library API, or integrated into web app (FastAPI + React/Vite + PostgreSQL)

---

## Shared Infrastructure

### Types (`scrapers/common.py`)

All scrapers return these types:

```python
UnifiedProduct = TypedDict({
    "name": str,                   # Product name (Hebrew)
    "brand": str | None,           # Brand (if available)
    "barcode": str | None,         # EAN barcode
    "price": float,                # Current price (₪)
    "price_per_unit": float,       # Normalized to base unit (₪/kg, ₪/L, ₪/100ml, etc.)
    "unit": str,                   # Base unit ("kg", "L", "100ml", etc.)
    "store_id": str,               # Store/branch identifier
    "chain": str,                  # Chain name (e.g. "tivtaam", "shufersal")
    "url": str | None,             # Product page URL
    "image_url": str | None,       # Product image CDN URL
    "category": str | None,        # Category ID or path
    "deals": list[DealInfo],       # Active promotions
}, total=False)

ScrapeResult = TypedDict({
    "chain": str,
    "stores_scraped": int,         # Number of branches/stores queried
    "products_total": int,         # Total unique products across all stores
    "products_by_store": dict[str, list[UnifiedProduct]],  # Keyed by store_id
    "scraped_at": str,             # ISO-8601 UTC timestamp
    "duration_seconds": float,
    "errors": list[str],           # Warnings or partial failure messages
})

ScrapeFilter = TypedDict({
    "name_query": str | None,      # Substring search
    "category_ids": list[str],     # Filter by category IDs
    "barcode": str,                # Exact EAN match
}, total=False)

DealInfo = TypedDict({
    "type": str,                   # "discount", "promotion", etc.
    "discount_pct": float | None,  # Percentage off (if applicable)
    "deal_price": float | None,    # Promoted price (₪)
    "valid_from": str,             # ISO date (e.g. "2026-05-01")
    "valid_to": str,               # ISO date
})
```

### Utilities (`scrapers/common.py`)

- **`with_retry(fn, max_retries, base_delay)`** — Exponential backoff decorator. Retries on network errors with jitter. Used by all API calls.
- **`run_concurrently(tasks, max_concurrent)`** — Execute tasks with bounded parallelism via `asyncio.TaskGroup`. Fails fast on first error.
- **`normalize_unit(unit_str)`** — Converts Hebrew/alternate unit names to base units (e.g. "ק"ג" → "kg", "ליטר" → "L").
- **`compute_price_per_base_unit(price, unit, quantity)`** — Normalizes price to per-kilogram or per-liter for comparison.
- **`make_ssl_context()`** — SSL context for aiohttp sessions (handles specific chain requirements).

### Config (`config.py`)

```python
CHUNK_SIZE = 15               # Concurrent requests per batch
RETRY_LIMIT = 3               # Retries per failed request
RETRY_DELAY = 2               # Seconds before retrying
```

---

## Active Scrapers

| Chain | Hebrew | Platform | Retailer ID | Branches | File |
|-------|--------|----------|-------------|----------|------|
| **tivtaam** | טיב טעם | Stor.ai | 1062 | 7 | `scrapers/tivtaam/tivtaam.py` |
| **carrefour** | קארפור | Stor.ai | 1540 | 22+ | `scrapers/carrefour/carrefour.py` |
| **shufersal** | שופרסל | Custom JSON | — | 1 (chain-wide) | `scrapers/shufersal/shufersal.py` |
| **yochananof** | יוחננוף | Magento 2 GraphQL | — | 20+ | `scrapers/yochananof/yochananof.py` |
| **machsanei_hashook** | מחסני השוק | ZuZ | 1107 | 1 | `scrapers/machsanei_hashook/machsanei_hashook.py` |
| **ramilevi** | רמי לוי | Custom Node.js/Elasticsearch | — | 22+ | `scrapers/ramilevi/ramilevi.py` |
| **keshet** | קשת טעמים | ZuZ | 1219 | 30+ | `scrapers/keshet/keshet.py` |
| **quik** | קוויק | ZuZ | 1541 | 17 | `scrapers/quik/quik.py` |
| **victory** | ויקטורי | ZuZ | 1470 | 30+ | `scrapers/victory/victory.py` |
| **ybitan** | יינות ביתן | ZuZ | 1131 | 17+ | `scrapers/ybitan/ybitan.py` |

---

## Platform Patterns

### Stor.ai (tivtaam, carrefour)

**Endpoint**: `GET /v2/retailers/{retailer_id}/branches/{branch_id}/categories/{category_id}/products`

Query params: `appId=4`, `from={offset}`, `size={page_size}`, `languageId=1`

**Pattern**:
1. Fetch category tree from Stor.ai endpoints.
2. For each branch, iterate all categories.
3. Offset-paginate through category products (`from=0,size=100 → from=100,size=100 → ...`).
4. Stop when response contains fewer items than `size`.

**Retry**: Exponential backoff on 5xx or timeout.

**Branch/Store Discovery**: Hardcoded in `ONLINE_BRANCHES` list.

---

### ZuZ (machsanei_hashook, keshet, quik, victory, ybitan)

**Endpoint**: `GET /api/v1/{appId}/{retailer_id}/{branch_id}/{category_id}`

Query params: `page={page_num}`, `itemsPerPage=100`

**Pattern**:
1. Fetch available categories from ZuZ.
2. For each branch, iterate all categories.
3. Page-paginate through category products (`page=1,2,3,...` until empty).
4. Extract deals from product objects (usually `product.branch.specials[]` or similar).

**Barcode Extraction**: Many chains store barcodes in image CDN URLs (e.g. `https://cdn.zuuz.co.il/img/product/.../<EAN>.jpg`). Extract via regex when `barcode` field is null.

**Branch/Store Discovery**: Hardcoded in `ONLINE_BRANCHES` list per scraper.

---

### Shufersal Custom JSON

**Endpoint**: `GET /online/he/search/results`

Query params: `q={query}`, `page={page_num}`, `Accept: application/json`

**Pattern**:
1. Single chain-wide endpoint (no per-branch categories).
2. Full-text search via `q` parameter.
3. Page-paginate from `page=1` onwards. Fixed page size of 20.
4. Continue until response is empty.

**Store ID**: All products mapped to store_id `"global"` (no per-branch data).

---

### Magento 2 GraphQL (yochananof)

**Endpoint**: `POST /graphql`

Headers: `Store: {store_code}` (e.g. `Store: s82`)

**Pattern**:
1. Fetch store list via `availableStores` GraphQL query at runtime.
2. For each store, fetch category tree via `categoryList` query.
3. For each category, fetch products via `products(filter={category_id})` query.
4. Pagination via GraphQL `pageSize` and `currentPage` arguments.

**Store Discovery**: Fetched live from GraphQL API (no hardcoded list).

---

### Custom Node.js / Elasticsearch (ramilevi)

**Endpoints**:
- Catalog (browse): `POST /api/catalog` (Content-Type: application/json)
- Search: `GET /api/search`

Request body: `{"store": <internet_store_id>, "q": "", "from": <offset>, "size": <page_size>}`

**Pattern**:
1. Browse full catalog via `POST /api/catalog` with `q=""` and per-store `store` ID.
2. Offset-paginate via `from` and `size` parameters.
3. Search (keyword) via `GET /api/search` with `storeid=<id>`, `q=<query>`, `from=<offset>`, `size=<size>`.

**Products by Weight**: Products with `prop.by_kilo == 1` have `price.price` per kilogram. Normalize to "kg" unit.

**Store Discovery**: Hardcoded in `ONLINE_STORES` list.

---

## Web App

### Stack
- **Backend**: FastAPI + uvicorn
- **Database**: PostgreSQL with asyncpg + SQLAlchemy async ORM
- **Fuzzy Search**: PostgreSQL `pg_trgm` extension for typo-tolerant product search
- **Frontend**: React + Vite + TypeScript
- **Deployment**: Docker Compose (3 services: api, db, frontend)

### Services
| Service | Port | Directory |
|---------|------|-----------|
| FastAPI backend | 8000 | `webapp/backend/` |
| PostgreSQL | 5432 | (container) |
| React frontend | 5174 | `webapp/frontend/` |

### Features
1. **Product Search** — Search products across all chains with fuzzy matching
2. **Price Comparison** — See the same product across multiple stores
3. **Shopping List** — Create lists and see which chain offers the best price per item
4. **Background Scraping** — Periodic scrapes (every 6 hours by default) refresh product data

### Database Schema
- `products` table: product metadata (name, barcode, image_url, etc.)
- `store_products` table: per-store pricing and deals
- `shopping_lists` table: user-created shopping lists

### Environment
```
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/supermarket
SCRAPE_INTERVAL_HOURS=6
HOST=0.0.0.0
PORT=8000
```

---

## Validation

Run `python3 validate_scrapers.py` to test all 10 scrapers with a live API call.

The validation script:
1. Runs each scraper with a `name_query="חלב"` (milk) filter on **one branch**.
2. Checks that ≥1 products are returned with valid `name`, `price`, `store_id` fields.
3. Prints a pass/fail summary table.
4. Optionally uses Playwright to visually inspect failed websites (requires `pip install playwright`).

---

## Future Scraper Ideas

These Israeli supermarket chains are candidates for future integration:

| Chain | Hebrew | Status | Notes |
|-------|--------|--------|-------|
| **Osher Ad** | אושר עד | Not scraped | Large discount chain. Likely custom backend or ZuZ. ~15 branches. |
| **AM:PM** | AM:PM | Not scraped | Convenience store chain. Likely custom or off-platform backend. |
| **Freshmarket** | Fresh Market | Not scraped | Regional chain, smaller footprint. May have e-commerce API. |
| **Super Yuda** | סופר יודה | Not scraped | Regional chain in Haifa/northern Israel. |
| **Dor Alon** | דור אלון | Not scraped | Gas station + supermarket. May have online catalog. |
| **HaZol** | הזול | Not scraped | Deep-discount chain. Likely simple backend (Stor.ai or ZuZ). |
| **Eden Teva Market** | אדן | Not scraped | Health food / organic chain. Smaller. |
| **Mega** | מגה | Closed | Former major chain, no longer in operation. Skip. |

### How to Add a New Scraper

1. **Research** the target website's product browse/search API using browser DevTools (see `documentation/AGENT_TARGET_DOCUMENTATION.md`).
2. **Create** `scrapers/<name>/<name>.py` with a single async function:
   ```python
   async def scrape(branches=None, flt=None, batch_size=100, max_concurrent=15, max_retries=3, base_retry_delay=1.0) -> ScrapeResult:
       # Return ScrapeResult with products_by_store dict
   ```
3. **Add** the chain to the dispatcher in `main.py` (CLI arguments, import, etc.).
4. **Test** with `python3 validate_scrapers.py`.
5. **Document** the API in `documentation/<name>_api.md`.

---

## CLI Usage

### Run all scrapers
```bash
python3 main.py
```

### Run specific chains
```bash
python3 main.py --supermarkets tivtaam carrefour shufersal
```

### Filter by name
```bash
python3 main.py --filter-name "חלב"
```

### Filter by barcode
```bash
python3 main.py --filter-barcode 7290000000000
```

### Select branches
```bash
python3 main.py --tivtaam-branches 924 929 937
```

### Output to directory
```bash
python3 main.py --output-dir ./my_results
```

### Full options
See `README.md` or `python3 main.py --help`.

---

## Project Structure

```
.
├── main.py                        # CLI entry point (all 10 chains)
├── validate_scrapers.py           # Live scraper validation
├── chp_main.py                    # Standalone CHP scraper (not integrated)
├── config.py                      # Shared config constants
├── utils.py                       # Browser headers, logging
├── README.md                      # Full user guide
├── ARCHITECTURE.md                # This file
├── TODOS.md                       # Project task history (completed)
│
├── scrapers/
│   ├── common.py                  # Shared types and utilities
│   ├── tivtaam/tivtaam.py         # Stor.ai retailer 1062
│   ├── carrefour/carrefour.py     # Stor.ai retailer 1540
│   ├── shufersal/shufersal.py     # Custom JSON backend
│   ├── yochananof/yochananof.py   # Magento 2 GraphQL
│   ├── machsanei_hashook/machsanei_hashook.py  # ZuZ retailer 1107
│   ├── ramilevi/ramilevi.py       # Custom Node.js/Elasticsearch
│   ├── keshet/keshet.py           # ZuZ retailer 1219
│   ├── quik/quik.py               # ZuZ retailer 1541
│   ├── victory/victory.py         # ZuZ retailer 1470
│   └── ybitan/ybitan.py           # ZuZ retailer 1131
│
├── documentation/
│   ├── AGENT_TARGET_DOCUMENTATION.md  # Guide for AI agents to research/debug APIs
│   ├── *_api.md                   # Per-scraper API research notes
│   └── chp_documentation/         # CHP scraper docs
│
├── tests/
│   └── test_smoke.py              # ~140 unit tests (all mocked)
│
├── webapp/
│   ├── docker-compose.yml
│   ├── .env
│   ├── backend/
│   │   ├── main.py                # FastAPI app
│   │   ├── db.py                  # Database layer
│   │   ├── scheduler.py           # Periodic scraper runs
│   │   ├── scraper_runner.py      # Calls main.py internals
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   └── frontend/
│       ├── src/
│       │   ├── App.tsx
│       │   ├── components/        # SearchPage, ShoppingListPage, etc.
│       │   └── api.ts             # Axios client
│       ├── Dockerfile
│       ├── vite.config.ts
│       └── package.json
│
└── archive/
    ├── chp_api_explore/           # Historical CHP exploration scripts (not active)
    └── validation/                # Historical validation snapshots
```

---

## Development Notes

- **Async/await**: All I/O is async. Use `asyncio.run()` or `main.py` for entry points.
- **Concurrency**: Bounded via `run_concurrently(..., max_concurrent=15)`. Set per-scraper as needed.
- **Retry logic**: `with_retry()` handles transient errors. Permanent 404s/5xxs are caught and reported.
- **Encoding**: All text is UTF-8. Hebrew product names are normalized for search.
- **Caching**: No caching layer in the scrapers themselves. Web app uses PostgreSQL for data persistence.
- **Rate limiting**: No explicit rate limiting; chains may have server-side limits. Monitor `errors` in `ScrapeResult` for 429/throttling.
