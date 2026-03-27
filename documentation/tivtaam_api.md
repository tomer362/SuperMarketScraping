# Tiv Taam API Documentation

> Platform: **Stor.ai** — Retailer ID `1062`
> Base URL: `https://www.tivtaam.co.il`
> Research date: 2026-03

---

## Overview

Tiv Taam's online store runs on the **Stor.ai** SaaS platform (shared with Carrefour Israel).  All data is fetched via a REST/JSON API at the same origin as the website.  No authentication is required for read operations.  SSL certificates must be validated using **certifi** (system trust store may not include the CDN's CA on some platforms).

---

## Endpoints

### 1. Branch List

```
GET /v2/retailers/1062/branches?appId=4&languageId=1
```

Returns all Tiv Taam branches (online and offline).

**Response shape:**
```json
{
  "branches": [
    {
      "id": 924,
      "name": "רמת החייל",
      "city": "תל אביב יפו",
      "location": "דבורה הנביאה 122",
      "isActive": true,
      "isOnline": true
    }
  ]
}
```

**Known online branch IDs (as of 2026-03):**

| ID   | Name                    | City          |
|------|-------------------------|---------------|
| 924  | רמת החייל               | תל אביב יפו   |
| 929  | ראשל"צ מזרח             | ראשון לציון   |
| 937  | אשדוד                   | אשדוד         |
| 939  | באר שבע                 | באר שבע       |
| 943  | נתניה                   | נתניה         |
| 1489 | קיסריה                  | קיסריה        |
| 1841 | חוצות המפרץ             | חיפה          |
| 1980 | נובל אנרג'י             | אשדוד         |
| 3463 | ראשון לציון - רובוטי    | —             |

---

### 2. Category Products (offset pagination)

```
GET /v2/retailers/1062/branches/{branch_id}/categories/{category_id}/products
    ?appId=4&from={offset}&size={page_size}&languageId=1
```

**Path parameters:**
- `branch_id` — integer branch ID (e.g. `924`)
- `category_id` — string category ID (e.g. `90176`)

**Query parameters:**

| Parameter    | Type    | Description                         |
|--------------|---------|-------------------------------------|
| `appId`      | int     | Always `4`                          |
| `from`       | int     | Zero-based offset for pagination    |
| `size`       | int     | Page size (up to ~200 observed)     |
| `languageId` | int     | `1` = Hebrew                        |

**Response shape:**
```json
{
  "total": 342,
  "products": [ <Product>, ... ]
}
```

---

### 3. Name Search

```
GET /v2/retailers/1062/branches/{branch_id}/categories/{category_id}/products
    ?appId=4&from={offset}&size={page_size}&languageId=1&q={url_encoded_query}
```

The category products endpoint accepts an optional `q=` parameter for keyword filtering.  This is the **recommended search approach** — it correctly returns `total` and paginated `products`, unlike the `/autocomplete` endpoint which is capped at 10 results and returns a different response structure.

The scraper fans out concurrently across all known category IDs and deduplicates results by `productId`.

**Example:**
```
GET /v2/retailers/1062/branches/924/categories/90176/products
    ?appId=4&languageId=1&size=100&from=0&q=%D7%97%D7%9C%D7%91
```

**Note on `/autocomplete`:** The autocomplete endpoint exists but returns a different structure (`suggestions.suggestProducts.items[]`) and is capped at 10 results regardless of `size` — do not use it for full search.

---

## Product Object

```json
{
  "id": 12345,
  "productId": 67890,
  "gs1ProductId": null,
  "localName": "חלב תנובה 3% שומן 1 ליטר",
  "names": {
    "1": { "short": "חלב תנובה 3%", "long": "חלב תנובה 3% שומן 1 ליטר" }
  },
  "image": {
    "url": "https://d2e5ushqwiltxm.cloudfront.net/upload/images/product-images/...{{size}}.{{extension||'jpg'}}"
  },
  "isWeighable": false,
  "weight": 1000,
  "unitOfMeasure": { "names": { "1": "מ\"ל" } },
  "unitResolution": null,
  "numberOfItems": 1,
  "family": {
    "id": 111,
    "categories": [
      { "id": 90176, "name": "מוצרי חלב" }
    ]
  },
  "branch": {
    "id": 924,
    "regularPrice": 6.90,
    "salePrice": 5.50,
    "isOutOfStock": false,
    "isActive": true,
    "isVisible": true
  }
}
```

### Key fields

| Field                        | Type            | Notes                                              |
|------------------------------|-----------------|----------------------------------------------------|
| `id`                         | int             | Branch-scoped product ID                           |
| `productId`                  | int             | Global product ID (use this as canonical ID)       |
| `names.1.long`               | string          | Hebrew long name (preferred)                       |
| `names.1.short`              | string          | Hebrew short name (fallback)                       |
| `localName`                  | string          | Final fallback name                                |
| `image.url`                  | string template | Replace `{{size}}` → `medium`; `{{extension\|\|'jpg'}}` → `jpg` |
| `isWeighable`                | bool            | True for products sold by weight                   |
| `weight`                     | float\|null     | Numeric weight/volume in base unit (grams or ml)   |
| `unitOfMeasure.names.1`      | string\|null    | Hebrew unit label e.g. `גרם`, `מ"ל`               |
| `unitResolution`             | float\|null     | Measurement increment                              |
| `numberOfItems`              | int\|null       | Items per package                                  |
| `branch.regularPrice`        | float           | Shelf price (always present; skip if `null`)       |
| `branch.salePrice`           | float\|null     | Promotional price (null when no active promotion)  |
| `branch.isOutOfStock`        | bool            | Stock availability                                 |
| `family.categories[].id`     | int             | Category IDs the product belongs to                |

---

## Barcode Extraction

Tiv Taam does not expose a `barcode` field directly.  EAN-13 (or EAN-8) barcodes are embedded in GS1 CDN image URLs:

```
https://d2e5ushqwiltxm.cloudfront.net/upload/images/gs1-products/1062/medium/7290000066882-12345/...
```

**Regex pattern:**
```python
re.search(r"/gs1-products/\d+/[^/]+/(\d{8,14})-\d+", image_url)
```

The first capture group is the barcode.  Only GS1-linked products have barcodes; private-label or non-barcoded items return `None`.

---

## Category IDs

Tiv Taam has ~87 top-level categories.  A representative subset:

| ID      | Category                     |
|---------|------------------------------|
| 90066   | ירקות                        |
| 90069   | פירות                        |
| 90176   | מוצרי חלב                   |
| 90261   | שוקולד וממתקים               |
| 90285   | מיצים ונקטרים                |
| 90288   | משקאות מוגזים                |
| 90294   | מים                          |
| 90309   | יין                          |
| 90410   | מוצרי ניקוי                  |

Full list in `scrapers/tivtaam/tivtaam.py → CATEGORIES`.

---

## Pagination Strategy

1. Fetch `size=1&from=0` to read `total`.
2. Loop: `from = 0, batch_size, 2*batch_size, …` until `from >= total`.
3. Stop early if an empty `products` array is returned.

---

## SSL / TLS Notes

The Stor.ai CDN uses a CA that may not be in the system trust store on some macOS/Linux setups.  Always create the SSL context with:

```python
import certifi, ssl
ctx = ssl.create_default_context(cafile=certifi.where())
connector = aiohttp.TCPConnector(ssl=ctx)
```

---

## Rate Limiting / Politeness

No documented rate limits.  Observed behaviour:
- Up to ~50 concurrent requests handled without throttling.
- Recommended: keep `max_concurrent ≤ 20` to avoid transient 429/503 responses.
- Exponential backoff (base 1 s, max 30 s) handles intermittent failures.
