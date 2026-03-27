# AGENT_TARGET_DOCUMENTATION.md

> **This file is a skill guide for an AI agent.**
> It explains how to explore, instrument, and debug the APIs of all supermarket chains that have scrapers in this project.  Use this guide when you need to update a scraper, validate that an API still works, or discover new endpoints after the site has been updated.

---

## Table of Contents

1. [Overview of Scraped Chains](#1-overview-of-scraped-chains)
2. [General Instrumentation Strategy (F12 / DevTools)](#2-general-instrumentation-strategy-f12--devtools)
3. [Playwright Exploration Workflow](#3-playwright-exploration-workflow)
4. [Chain-by-Chain Guide](#4-chain-by-chain-guide)
   - [Shufersal](#41-shufersal-שופרסל)
   - [Rami Levy](#42-rami-levy-רמי-לוי)
   - [Tiv Taam](#43-tiv-taam-טיב-טעם)
   - [Carrefour](#44-carrefour-קרפור)
   - [Machsanei HaShook](#45-machsanei-hashook-מחסני-השוק)
   - [Keshet Teamim](#46-keshet-teamim-קשת-טעמים)
   - [Quik](#47-quik-קוויק)
   - [Victory](#48-victory-ויקטורי)
   - [Yenot Bitan](#49-yenot-bitan-יינות-ביתן)
   - [Yochananof](#410-yochananof-יוחננוף)
5. [Platform Reference Summary](#5-platform-reference-summary)
6. [Common Gotchas](#6-common-gotchas)

---

## 1. Overview of Scraped Chains

| Chain           | Platform               | Retailer/Store ID | Base URL                            | Scraper file                              |
|-----------------|------------------------|-------------------|-------------------------------------|-------------------------------------------|
| Shufersal       | Shufersal custom       | —                 | shufersal.co.il                     | scrapers/shufersal/shufersal.py           |
| Rami Levy       | Custom Node.js/ES      | varies per store  | rami-levy.co.il                     | scrapers/ramilevi/ramilevi.py             |
| Tiv Taam        | Stor.ai                | 1062              | www.tivtaam.co.il                   | scrapers/tivtaam/tivtaam.py               |
| Carrefour       | Stor.ai                | 1540              | www.carrefour.co.il                 | scrapers/carrefour/carrefour.py           |
| Machsanei HaShook | ZuZ (appId=2)        | 1107              | www.mck.co.il                       | scrapers/machsanei_hashook/machsanei_hashook.py |
| Keshet Teamim   | ZuZ (appId=4)          | 1219              | www.keshet-teamim.co.il             | scrapers/keshet/keshet.py                 |
| Quik            | ZuZ (appId=4)          | 1541              | www.quik.co.il                      | scrapers/quik/quik.py                     |
| Victory         | ZuZ (appId=4)          | 1470              | www.victoryonline.co.il             | scrapers/victory/victory.py               |
| Yenot Bitan     | ZuZ (appId=4)          | 1131              | www.ybitan.co.il                    | scrapers/ybitan/ybitan.py                 |
| Yochananof      | Magento 2 + GraphQL    | —                 | api.yochananof.co.il/graphql        | scrapers/yochananof/yochananof.py         |

**Note: CHP (חצי חינם) is excluded from the web application** but has a scraper at `scrapers/chp/`.

---

## 2. General Instrumentation Strategy (F12 / DevTools)

When a scraper breaks or you need to discover how an API works, use browser DevTools:

### Step-by-step

1. Open the chain's website in **Chrome** (not a headless browser — you need DevTools).
2. Open **DevTools** → **Network** tab.
3. Filter by **`Fetch/XHR`** to see only API requests (ignore HTML, CSS, images).
4. Browse the site normally (load the homepage, navigate to a category, search for something).
5. Watch for JSON API requests:
   - Click each request to see **Headers** (URL, method, request headers) and **Response** (JSON body).
   - Copy the **Request URL** and replicate it with `curl` or `aiohttp`.
6. Look for pagination: find `total`, `from`/`offset`, `size`/`limit`, `page`/`numberOfPages` in responses.
7. Check if there is an authentication token in request headers — look for `Authorization`, `X-Auth-Token`, `Cookie`, etc.

### curl replication template

```bash
curl -s "<URL>" \
  -H "Accept: application/json" \
  -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
  | python3 -m json.tool | head -80
```

---

## 3. Playwright Exploration Workflow

Use Playwright when:
- The site uses JavaScript rendering and the API isn't visible in raw curl.
- There is bot detection or CSRF token required.
- You want to intercept all network requests programmatically.

### Installation

```bash
pip install playwright
playwright install chromium
```

### Intercept all API requests

```python
import asyncio
from playwright.async_api import async_playwright

async def explore(url: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # headless=True for automation
        page = await browser.new_page()

        # Log all API-like requests
        def on_request(request):
            if "api" in request.url or ".json" in request.url or "v2/retailers" in request.url:
                print(f"→ {request.method} {request.url}")

        def on_response(response):
            if response.url.endswith(".json") or "v2/retailers" in response.url:
                print(f"← {response.status} {response.url}")

        page.on("request", on_request)
        page.on("response", on_response)

        await page.goto(url)
        await page.wait_for_timeout(5000)  # wait for JS to load
        await browser.close()

asyncio.run(explore("https://www.keshet-teamim.co.il"))
```

### Capture full response body

```python
import asyncio, json
from playwright.async_api import async_playwright

TARGET = "v2/retailers"

async def capture_responses(url: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        captured = []

        async def handle_response(response):
            if TARGET in response.url:
                try:
                    body = await response.json()
                    captured.append({"url": response.url, "body": body})
                except Exception:
                    pass

        page.on("response", handle_response)
        await page.goto(url)
        await page.wait_for_timeout(6000)
        await browser.close()

    for item in captured:
        print(item["url"])
        print(json.dumps(item["body"], ensure_ascii=False, indent=2)[:2000])
        print("---")

asyncio.run(capture_responses("https://www.keshet-teamim.co.il"))
```

### aiohttp direct call (for non-bot-protected APIs)

```python
import asyncio, aiohttp, ssl, certifi, json

async def fetch(url: str):
    ctx = ssl.create_default_context(cafile=certifi.where())
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ctx)) as session:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json(content_type=None)
            print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])

asyncio.run(fetch(
    "https://www.keshet-teamim.co.il/v2/retailers/1219/branches/1437/categories/79718/products"
    "?appId=4&from=0&size=5&languageId=1&categorySort={\"sortType\":1}"
))
```

---

## 4. Chain-by-Chain Guide

### 4.1 Shufersal (שופרסל)

- **Website:** `https://www.shufersal.co.il`
- **Scraper:** `scrapers/shufersal/shufersal.py`
- **Full docs:** `documentation/shufersal_api.md`

**API type:** Custom JSON REST — the same HTML pages return JSON when `Accept: application/json` is set.

**Key endpoint:**
```
GET https://www.shufersal.co.il/online/he/search/results?q=&page=0
Header: Accept: application/json, text/plain, */*
```

**Pagination:** `pagination.numberOfPages`, `pagination.pageSize` (fixed at 20), `pagination.totalNumberOfResults`.

**F12 tip:** Navigate to any product category or search result page and look for requests returning `{"results": [...], "pagination": {...}}`.

**curl probe:**
```bash
curl -s "https://www.shufersal.co.il/online/he/search/results?q=&page=0" \
  -H "Accept: application/json" \
  -H "User-Agent: Mozilla/5.0" | python3 -m json.tool | head -50
```

**No branch filtering** — prices are chain-wide. No authentication needed.

---

### 4.2 Rami Levy (רמי לוי)

- **Website:** `https://www.rami-levy.co.il`
- **Scraper:** `scrapers/ramilevi/ramilevi.py`
- **Full docs:** `documentation/ramilevi_api.md`

**API type:** Custom Node.js/Elasticsearch backend (Nuxt.js SPA).

**Key endpoints:**
```
GET /api/stores                                         # list of stores
GET /api/catalog?store={store_id}&q=&page=1&size=50     # paginated catalogue
GET /api/catalog?store={store_id}&q={query}&page=1      # search
```

**Pagination:** `data.total`, request page number in `page` param.

**Note:** Store IDs used in `/api/catalog` are `internet_store_id` from `/api/stores`, **not** the branch `id`.  Some stores have `internet_store_id: null` — skip them.

**F12 tip:** Look for requests to `/api/catalog` — the `store` param is the key.

**curl probe:**
```bash
curl -s "https://www.rami-levy.co.il/api/stores" | python3 -m json.tool | head -50
curl -s "https://www.rami-levy.co.il/api/catalog?store=331&q=&page=1&size=5" \
  -H "Accept: application/json" | python3 -m json.tool | head -80
```

---

### 4.3 Tiv Taam (טיב טעם)

- **Website:** `https://www.tivtaam.co.il`
- **Scraper:** `scrapers/tivtaam/tivtaam.py`
- **Full docs:** `documentation/tivtaam_api.md`

**API type:** Stor.ai platform — retailer ID `1062`.

**Key endpoints:**
```
GET /v2/retailers/1062/branches?appId=2&languageId=1
GET /v2/retailers/1062/branches/{bid}/categories/{cat_id}/products
    ?appId=4&from=0&size=100&languageId=1
```

**Product schema:** Same ZuZ appId=4 schema as Keshet/Quik/Victory/Ybitan — `product["branch"]` (singular), barcode from image URL.

**F12 tip:** Filter requests to `/v2/retailers/1062/`.

**curl probe:**
```bash
curl -s "https://www.tivtaam.co.il/v2/retailers/1062/branches?appId=2&languageId=1" | python3 -m json.tool | head -40
```

---

### 4.4 Carrefour (קרפור)

- **Website:** `https://www.carrefour.co.il`
- **Scraper:** `scrapers/carrefour/carrefour.py`
- **Full docs:** `documentation/carrefour_api.md`

**API type:** Stor.ai platform — retailer ID `1540`.

**Key endpoints:**
```
GET /v2/retailers/1540/branches?appId=2&languageId=1
GET /v2/retailers/1540/branches/{bid}/categories/{cat_id}/products
    ?appId=4&from=0&size=100&languageId=1
```

**Identical schema** to Tiv Taam — same Stor.ai platform, different retailer ID.

**curl probe:**
```bash
curl -s "https://www.carrefour.co.il/v2/retailers/1540/branches?appId=2&languageId=1" | python3 -m json.tool | head -40
```

---

### 4.5 Machsanei HaShook (מחסני השוק)

- **Website:** `https://www.mck.co.il`
- **Scraper:** `scrapers/machsanei_hashook/machsanei_hashook.py`
- **Full docs:** `documentation/machsanei_hashook_api.md`

**API type:** ZuZ platform — retailer ID `1107`, **appId=2** (global multi-branch endpoint).

**Key difference from other ZuZ chains:** Branch data is a **map** `product["branches"][str(branch_id)]`, not a single `product["branch"]` object.  All branches are returned in a single response; filtering is done client-side.

**Key endpoints:**
```
GET /v2/retailers/1107/branches?appId=2&languageId=1
GET /v2/retailers/1107/products?appId=2&from=0&size=100&languageId=1
GET /v2/retailers/1107/products?appId=2&q={query}&from=0&size=100&languageId=1
```

**Barcode:** Top-level `barcode` / `localBarcode` field — no image URL parsing needed.

**F12 tip:** Filter requests to `/v2/retailers/1107/`.

**curl probe:**
```bash
curl -s "https://www.mck.co.il/v2/retailers/1107/products?appId=2&from=0&size=5&languageId=1" | python3 -m json.tool | head -80
```

---

### 4.6 Keshet Teamim (קשת טעמים)

- **Website:** `https://www.keshet-teamim.co.il`
- **Scraper:** `scrapers/keshet/keshet.py`
- **Full docs:** `documentation/keshet_api.md`

**API type:** ZuZ platform — retailer ID `1219`, **appId=4** (per-branch, per-category).

**Key endpoints:**
```
GET /v2/retailers/1219/branches?appId=2&languageId=1
GET /v2/retailers/1219/branches/{bid}/categories/{cat_id}/products
    ?appId=4&from=0&size=100&languageId=1
    &categorySort={"sortType":1}
    &filters={"mustNot":{"term":{"branch.isOutOfStock":true}}}
```

**Critical:** The global `/v2/retailers/1219/products` endpoint is **capped** — it misses products like eggs and fresh meat.  Always use per-branch/per-category.

**Category discovery:** Categories are in the site's `data.js` JS bundle.  In DevTools, search for `/data.js` in the Network tab and look for category ID arrays.

**F12 tip:** Filter requests to `/v2/retailers/1219/branches/`.

**curl probe:**
```bash
curl -s "https://www.keshet-teamim.co.il/v2/retailers/1219/branches/1437/categories/79718/products?appId=4&from=0&size=3&languageId=1" | python3 -m json.tool | head -100
```

---

### 4.7 Quik (קוויק)

- **Website:** `https://www.quik.co.il`
- **Scraper:** `scrapers/quik/quik.py`
- **Full docs:** `documentation/quik_api.md`

**API type:** ZuZ platform — retailer ID `1541`, **appId=4**.

Identical endpoint pattern to Keshet Teamim — only the retailer ID and branch IDs differ.

**curl probe:**
```bash
curl -s "https://www.quik.co.il/v2/retailers/1541/branches/3102/categories/79718/products?appId=4&from=0&size=3&languageId=1" | python3 -m json.tool | head -100
```

---

### 4.8 Victory (ויקטורי)

- **Website:** `https://www.victoryonline.co.il`
- **Scraper:** `scrapers/victory/victory.py`
- **Full docs:** `documentation/victory_api.md`

**API type:** ZuZ platform — retailer ID `1470`, **appId=4**.

Identical endpoint pattern to Keshet Teamim — only the retailer ID and branch IDs differ.  Victory has ~55 branches (largest ZuZ chain).

**curl probe:**
```bash
curl -s "https://www.victoryonline.co.il/v2/retailers/1470/branches/2449/categories/79718/products?appId=4&from=0&size=3&languageId=1" | python3 -m json.tool | head -100
```

---

### 4.9 Yenot Bitan (יינות ביתן)

- **Website:** `https://www.ybitan.co.il`
- **Scraper:** `scrapers/ybitan/ybitan.py`
- **Full docs:** `documentation/ybitan_api.md`

**API type:** ZuZ platform — retailer ID `1131`, **appId=4**.

Identical endpoint pattern to Keshet Teamim — covers both "Yenot Bitan Online" and "Bitan Market" branded branches.

**curl probe:**
```bash
curl -s "https://www.ybitan.co.il/v2/retailers/1131/branches/1015/categories/79718/products?appId=4&from=0&size=3&languageId=1" | python3 -m json.tool | head -100
```

---

### 4.10 Yochananof (יוחננוף)

- **Website:** `https://www.yochananof.co.il`
- **API endpoint:** `https://api.yochananof.co.il/graphql`
- **Scraper:** `scrapers/yochananof/yochananof.py`
- **Full docs:** `documentation/yochananof_api.md`

**API type:** Magento 2 + GraphQL.

**Key difference:** This is a GraphQL API, not REST.  All queries go to one endpoint.  Branch-level pricing is controlled via the `Store` HTTP header (the `store_code` of the branch).

**Key queries:**
```graphql
# Get available branches (store codes)
query { availableStores { store_code store_name } }

# Get product categories
query { categoryList { id name children { id name } } }

# Get products from a category
query {
  products(filter: { category_id: { eq: "123" } }, pageSize: 50, currentPage: 1) {
    total_count
    items { sku name price_range { minimum_price { regular_price { value } } } }
  }
}
```

**Required headers:**
```
Content-Type: application/json
Accept: application/json
Store: <store_code>    # e.g. "default", "haifa_1", etc.
```

**F12 tip:** Filter requests to `api.yochananof.co.il/graphql` — all requests hit the same URL but with different query bodies.

**curl probe:**
```bash
curl -s -X POST "https://api.yochananof.co.il/graphql" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{"query":"{ availableStores { store_code store_name } }"}' | python3 -m json.tool | head -60
```

---

## 5. Platform Reference Summary

### ZuZ Platform (appId=4) — Keshet, Quik, Victory, Ybitan, Tiv Taam, Carrefour

All share the same URL structure and product schema:

```
Branch list:  GET /v2/retailers/{ID}/branches?appId=2&languageId=1
Products:     GET /v2/retailers/{ID}/branches/{bid}/categories/{cat_id}/products
              ?appId=4&from={offset}&size={size}&languageId=1
              &categorySort={"sortType":1}
              &filters={"mustNot":{"term":{"branch.isOutOfStock":true}}}
```

Product schema differences from appId=2:

| Field       | appId=4                              | appId=2 (Machsanei HaShook)          |
|-------------|--------------------------------------|--------------------------------------|
| Branch data | `product["branch"]` (singular)       | `product["branches"][str(id)]` (map) |
| Barcode     | Parsed from image URL via regex      | Top-level `barcode` field            |
| Image URL   | Has `{{size}}` / `{{extension}}`     | Direct URL, no templates             |
| Categories  | `product["family"]["categories"]`    | `product["department"]` (flat)       |
| Brand       | `product["brand"]["names"]["1"]`     | Not available                        |

### ZuZ Platform — Retailer IDs

| Chain            | Retailer ID | appId |
|------------------|-------------|-------|
| Machsanei HaShook | 1107       | 2     |
| Tiv Taam         | 1062        | 4     |
| Carrefour        | 1540        | 4     |
| Keshet Teamim    | 1219        | 4     |
| Quik             | 1541        | 4     |
| Victory          | 1470        | 4     |
| Yenot Bitan      | 1131        | 4     |

---

## 6. Common Gotchas

### The global products endpoint is capped (ZuZ appId=4 chains)

`GET /v2/retailers/{ID}/products?appId=2` returns a **capped** total (often ~2,000 items) and silently drops staple products like eggs, fresh meat, and bread.  **Always use the per-branch/per-category endpoint.**

To verify if a chain is capped:
```bash
# Compare totals
curl "https://www.keshet-teamim.co.il/v2/retailers/1219/products?appId=2&from=0&size=1&languageId=1" | python3 -c "import sys,json; d=json.load(sys.stdin); print('global total:', d['total'])"
# Then scrape a single branch across all categories and count — you'll get more products.
```

### Barcode extraction from image URLs

For all ZuZ appId=4 chains, the barcode is embedded in the image URL:
```
https://cdn.chain.co.il/upload/images/product-images/7290000066882-large.jpg
                                                       ↑ barcode
```
Use regex: `r"/(\d{7,14})-"`.  Expand the image URL first (replace `{{size}}` with `large`).

### Image URL template expansion

Raw image URLs for appId=4 chains contain template placeholders:
```
https://cdn.chain.co.il/.../7290000066882-{{size}}.{{extension||'jpg'}}
```
Expand before use:
```python
url = url.replace("{{size}}", "large")
url = re.sub(r"\{\{extension(?:\|\|'[^']*')?\}\}", "jpg", url)
```

### SSL certificate issues

Use `certifi` to avoid SSL errors:
```python
import certifi, ssl
ctx = ssl.create_default_context(cafile=certifi.where())
connector = aiohttp.TCPConnector(ssl=ctx)
```

### Category deduplication

A product can appear in multiple categories.  Deduplicate by `productId` after collecting from all categories:
```python
seen: dict[str, product] = {}
for product in all_fetched:
    pid = str(product.get("productId") or product.get("id") or "")
    if pid and pid not in seen:
        seen[pid] = product
```

### Deal calculation for multi-buy promotions (Shufersal)

Shufersal's `promotionMsg` field (e.g. `"2 יח' ב- 22 ₪"`) encodes the **total** price for the bundle, not the per-unit price.  Parse with regex and divide:
```python
import re
m = re.search(r"(\d+)\s*(?:יח['\u05f4\u2019]?)?\s*ב[-–]\s*([\d.]+)\s*₪", msg)
if m:
    qty = int(m.group(1))
    total = float(m.group(2))
    per_unit = round(total / qty, 4)
```

### Rate limiting

None of the chains have documented rate limits, but all should be treated politely:
- Max 15 concurrent requests.
- Exponential backoff (base 1 s) on HTTP 429 or 5xx responses.
- The `with_retry` helper in `scrapers/common.py` handles this automatically.
