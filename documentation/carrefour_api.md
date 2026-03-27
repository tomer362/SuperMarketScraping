# Carrefour Israel API Documentation

> Platform: **Stor.ai** — Retailer ID `1540`
> Base URL: `https://www.carrefour.co.il`
> Research date: 2026-03

---

## Overview

Carrefour Israel's online store runs on the **Stor.ai** SaaS platform — the **same platform as Tiv Taam** (retailer ID 1062), just with a different retailer ID (1540).  The API structure, request format, response shape, and barcode extraction method are identical.  No authentication is required.

---

## Endpoints

### 1. Branch List

```
GET /v2/retailers/1540/branches?appId=4&languageId=1
```

**Response shape:**
```json
{
  "branches": [
    {
      "id": 3003,
      "name": "כפר סבא",
      "city": "כפר סבא",
      "location": "",
      "isActive": true,
      "isOnline": true
    }
  ]
}
```

**Known online branch IDs (as of 2026-03):**

| ID   | Name              | City              |
|------|-------------------|-------------------|
| 2992 | אשקלון            | אשקלון            |
| 2995 | נתניה             | נתניה             |
| 2996 | אור עקיבא         | אור עקיבא         |
| 2997 | פתח תקווה        | פתח תקווה        |
| 2998 | אשדוד             | אשדוד             |
| 3003 | כפר סבא (default) | כפר סבא           |
| 3005 | אילת              | אילת              |
| 3007 | רמלה              | רמלה              |
| 3008 | חיפה              | חיפה              |
| 3010 | בית שמש           | בית שמש           |
| 3012 | גבעתיים           | גבעתיים           |
| 3013 | ירושלים           | ירושלים           |
| 3014 | תל אביב           | תל אביב           |
| 3017 | קריית אתא         | קריית אתא         |
| 3018 | רחובות            | רחובות            |
| 3019 | אור יהודה         | אור יהודה         |
| 3020 | לוד               | לוד               |
| 3212 | הרצליה            | הרצליה            |
| 3360 | נווה אילן         | נווה אילן         |
| 3361 | שדרות             | שדרות             |
| 3458 | עפולה             | עפולה             |
| 3466 | באר שבע           | באר שבע           |
| 3476 | דליית אל כרמל    | דליית אל כרמל    |

---

### 2. Category Products (offset pagination)

```
GET /v2/retailers/1540/branches/{branch_id}/categories/{category_id}/products
    ?appId=4&from={offset}&size={page_size}&languageId=1
```

**Query parameters:**

| Parameter    | Type | Description                         |
|--------------|------|-------------------------------------|
| `appId`      | int  | Always `4`                          |
| `from`       | int  | Zero-based offset                   |
| `size`       | int  | Page size (up to ~200 observed)     |
| `languageId` | int  | `1` = Hebrew                        |

**Response shape:**
```json
{
  "total": 187,
  "products": [ <Product>, ... ]
}
```

---

### 3. Name Search

```
GET /v2/retailers/1540/branches/{branch_id}/categories/{category_id}/products
    ?appId=4&from={offset}&size={page_size}&languageId=1&q={url_encoded_query}
```

Same approach as Tiv Taam — the `q=` parameter is added to the category products endpoint.  The scraper fans out concurrently across all discovered category IDs and deduplicates by `productId`.

**Note on `/autocomplete`:** The Carrefour autocomplete endpoint (`/products/autocomplete`) exists but returns `suggestions.suggestProducts.items[]` and is capped at 10 results — do not use it for full search.

---

## Product Object

```json
{
  "id": 55123,
  "productId": 88456,
  "gs1ProductId": null,
  "localName": "גבינה לבנה 5% שומן",
  "names": {
    "1": { "short": "גבינה לבנה 5%", "long": "גבינה לבנה 5% שומן 250 גרם" }
  },
  "image": {
    "url": "https://d2e5ushqwiltxm.cloudfront.net/upload/images/product-images/.../{{size}}.jpg"
  },
  "isWeighable": false,
  "weight": 250,
  "unitOfMeasure": { "names": { "1": "גרם" } },
  "unitResolution": null,
  "numberOfItems": 1,
  "family": {
    "id": 222,
    "categories": [
      { "id": 79604, "name": "גבינות" }
    ]
  },
  "branch": {
    "id": 3003,
    "regularPrice": 7.90,
    "salePrice": null,
    "isOutOfStock": false,
    "isActive": true,
    "isVisible": true
  }
}
```

### Key fields

| Field                       | Type            | Notes                                                      |
|-----------------------------|-----------------|-------------------------------------------------------------|
| `id`                        | int             | Branch-scoped product ID                                    |
| `productId`                 | int             | Global product ID (canonical)                               |
| `names.1.long`              | string          | Hebrew long name (preferred)                                |
| `names.1.short`             | string          | Hebrew short name (fallback)                                |
| `localName`                 | string          | Final fallback name                                         |
| `image.url`                 | string template | Replace `{{size}}` → `medium` (no `{{extension}}` suffix unlike Tiv Taam) |
| `isWeighable`               | bool            | True for by-weight products                                 |
| `weight`                    | float\|null     | Numeric weight/volume in base unit (grams or ml)            |
| `unitOfMeasure.names.1`     | string\|null    | Hebrew unit label e.g. `גרם`, `מ"ל`                        |
| `unitResolution`            | float\|null     | Measurement increment                                       |
| `numberOfItems`             | int\|null       | Items per package                                           |
| `branch.regularPrice`       | float           | Shelf price (skip product if `null`)                        |
| `branch.salePrice`          | float\|null     | Promotional price (null when no active promotion)           |
| `branch.isOutOfStock`       | bool            | Stock availability                                          |
| `family.categories[].id`    | int             | Category IDs the product belongs to                         |

---

## Barcode Extraction

Same method as Tiv Taam — barcodes are embedded in GS1 CDN image URLs.

```
https://d2e5ushqwiltxm.cloudfront.net/upload/images/gs1-products/1540/medium/7290000066882-12345/...
```

**Regex pattern:**
```python
re.search(r"/gs1-products/\d+/[^/]+/(\d{8,14})-\d+", image_url)
```

**Important difference from Tiv Taam:** Carrefour image URL templates use only `{{size}}` — there is **no** `{{extension||'jpg'}}` suffix.  Simply replace `{{size}}` with `"medium"`.

---

## Category Discovery

Carrefour provides **no standalone categories endpoint**.  Categories must be discovered dynamically by sampling products and reading `family.categories[]`.

**Two-phase discovery strategy:**

**Phase 1 — Seed scan:**  Fetch `size=50&from=0` from 15 known top-level seed category IDs concurrently.  Collect all `family.categories[].id` values from returned products.

**Phase 2 — Full scrape:** Use the discovered category IDs to paginate through all products.

**Known seed category IDs:**
```
79704, 79718, 79591, 79619, 79653, 79667, 79687, 79764,
79807, 79571, 79740, 79835, 79603, 79821, 95010
```

This discovers ~90 categories in ~0.9 seconds (15 concurrent requests).

**Categories are chain-wide** — discovered once from any branch and reused across all branches.

---

## Pagination Strategy

Identical to Tiv Taam:
1. Fetch `size=1&from=0` to read `total`.
2. Loop `from = 0, batch_size, 2*batch_size, …` until `from >= total`.
3. Stop early on empty `products` array.

---

## SSL / TLS Notes

Same as Tiv Taam — use certifi:

```python
import certifi, ssl, aiohttp
ctx = ssl.create_default_context(cafile=certifi.where())
connector = aiohttp.TCPConnector(ssl=ctx)
```

---

## Per-Branch Pricing

Prices and stock availability are **branch-specific** — the `branch_id` in the URL path determines which branch's data is returned.  Always specify the branch ID explicitly; there is no concept of a "global" price for Carrefour.

---

## Rate Limiting / Politeness

- No documented rate limits.
- Recommended: `max_concurrent ≤ 20`.
- Retry with exponential backoff (base 1 s, max 30 s) on any non-200 response.
