# Machsanei HaShook API Documentation

> Platform: **ZuZ** (AngularJS) — Retailer ID `1107`
> Base URL: `https://www.mck.co.il`
> Research date: 2026-03

---

## Overview

Machsanei HaShook's online store runs on the **ZuZ** platform, a distinct SaaS platform from Stor.ai (used by Tiv Taam / Carrefour).  All data is fetched via a REST/JSON API at the same origin as the website.  No authentication is required for read operations.

The key architectural difference from Stor.ai: the products endpoint returns data for **all branches simultaneously** in a single response.  Branch-specific availability is embedded inside each product under `product["branches"][str(branch_id)]`.  Client-side filtering per branch is required.

---

## Endpoints

### 1. Branch List

```
GET /v2/retailers/1107/branches?appId=2&languageId=1
```

Returns all Machsanei HaShook branches.

**Response shape:**
```json
{
  "branches": [
    {
      "id": 836,
      "name": "באר שבע",
      "city": "באר שבע",
      "location": ""
    }
  ]
}
```

**Known online branch IDs (as of 2026-03):**

| ID   | City        |
|------|-------------|
| 3474 | אופקים      |
| 1650 | אילת        |
| 836  | באר שבע     |
| 2039 | להבים       |
| 1701 | מיתרים      |
| 2933 | נוף הגליל   |
| 1587 | נשר         |
| 2983 | עין יהב     |
| 1370 | קרית גת     |

---

### 2. Full Product Catalogue (offset pagination)

```
GET /v2/retailers/1107/products
    ?appId=2&from={offset}&size={page_size}&languageId=1
```

Returns all products across all branches in a single paginated response.

**Query parameters:**

| Parameter    | Type    | Description                         |
|--------------|---------|-------------------------------------|
| `appId`      | int     | Always `2`                          |
| `from`       | int     | Zero-based offset for pagination    |
| `size`       | int     | Page size (100 recommended)         |
| `languageId` | int     | `1` = Hebrew                        |

**Response shape:**
```json
{
  "total": 3500,
  "products": [ <Product>, ... ]
}
```

---

### 3. Name Search

```
GET /v2/retailers/1107/products
    ?appId=2&q={url_encoded_query}&from={offset}&size={page_size}&languageId=1
```

Same endpoint as the catalogue, with the addition of the `q=` parameter.  Returns a subset of products matching the query, with the same `total` + `products` structure.

**Example:**
```
GET /v2/retailers/1107/products?appId=2&q=%D7%97%D7%9C%D7%91&from=0&size=100&languageId=1
```

---

## Product Object

```json
{
  "id": 20164375,
  "productId": 6080688,
  "barcode": "7290000066882",
  "localBarcode": "7290000066882",
  "localName": "חלב תנובה 3% שומן 1 ליטר",
  "names": {
    "1": {
      "short": "חלב תנובה 3%",
      "long": "חלב תנובה 3% שומן 1 ליטר"
    }
  },
  "image": {
    "url": "https://cdn.mck.co.il/upload/images/product-images/12345/img.jpg"
  },
  "isWeighable": false,
  "weight": 1000,
  "unitOfMeasure": { "names": { "1": "מ\"ל" } },
  "numberOfItems": 1,
  "department": { "id": 13292, "name": "מוצרי חלב" },
  "branches": {
    "836": {
      "id": 836,
      "isActive": true,
      "isVisible": true,
      "regularPrice": 6.90,
      "salePrice": null,
      "isOutOfStock": false,
      "specials": []
    },
    "1587": {
      "id": 1587,
      "isActive": true,
      "isVisible": true,
      "regularPrice": 7.20,
      "salePrice": null,
      "isOutOfStock": false,
      "specials": []
    }
  }
}
```

### Key fields

| Field                           | Type            | Notes                                                  |
|---------------------------------|-----------------|--------------------------------------------------------|
| `id`                            | int             | Internal record ID                                     |
| `productId`                     | int             | Global product ID (use this as canonical ID)           |
| `barcode`                       | string\|null    | EAN barcode (top-level; not extracted from image URL)  |
| `localBarcode`                  | string\|null    | Alternative barcode field (same value, fallback)       |
| `names.1.long`                  | string          | Hebrew long name (preferred)                           |
| `names.1.short`                 | string          | Hebrew short name (fallback)                           |
| `localName`                     | string          | Final fallback name                                    |
| `image.url`                     | string          | Direct image URL — no template substitution needed     |
| `isWeighable`                   | bool            | True for products sold by weight                       |
| `weight`                        | float\|null     | Numeric weight/volume in native unit                   |
| `unitOfMeasure.names.1`         | string\|null    | Hebrew unit label e.g. `גרם`, `מ"ל`                  |
| `department.id`                 | int             | Category ID (flat, single level — not hierarchical)    |
| `department.name`               | string          | Category name (Hebrew)                                 |
| `branches`                      | object          | Map of `str(branch_id)` → branch data                 |
| `branches.{id}.isActive`        | bool            | Product is active in this branch                       |
| `branches.{id}.isVisible`       | bool            | Product is visible in this branch                      |
| `branches.{id}.regularPrice`    | float\|null     | Shelf price (skip if null or ≤ 0)                     |
| `branches.{id}.salePrice`       | float\|null     | Promotional price (null when no active promotion)      |
| `branches.{id}.isOutOfStock`    | bool            | Stock availability                                     |
| `branches.{id}.specials`        | array           | Active promotions (see Specials section)               |

---

## Barcode

Unlike Stor.ai (Tiv Taam / Carrefour), the barcode is a **top-level field** — no image URL parsing is needed:

```python
barcode = item.get("barcode") or item.get("localBarcode") or None
```

---

## Image URL

The image URL is a direct URL with no template variables:

```python
image_url = item.get("image", {}).get("url")
# No {{size}} or {{extension}} substitution needed
```

---

## Category / Department

ZuZ uses a flat single-level `department` object instead of the hierarchical `family.categories` used by Stor.ai:

```python
dept_id = item.get("department", {}).get("id")
category_ids = [str(dept_id)] if dept_id is not None else []
```

---

## Client-Side Branch Filtering

Since all branches are returned in one response, filter per branch after fetching:

```python
branch_id_str = str(branch["id"])
branch_info = item.get("branches", {}).get(branch_id_str, {})

if not (branch_info.get("isActive") and branch_info.get("isVisible")):
    return None  # skip

regular_price = branch_info.get("regularPrice")
if regular_price is None or float(regular_price) <= 0:
    return None  # skip
```

---

## Specials (Promotions)

The `branches.{id}.specials` array may contain promotion objects.  Structure is the same as Stor.ai:

```json
{
  "names": { "1": { "name": "3 יח' ב-18 ₪" } },
  "firstLevel": {
    "type": 2,
    "firstPurchaseTotal": 3,
    "firstGift": { "total": 18.0 }
  }
}
```

**Special types:**

| `firstLevel.type` | Meaning                                      |
|-------------------|----------------------------------------------|
| `1`               | Simple price reduction (covered by salePrice)|
| `2`               | Multi-buy: buy N items for a total price     |
| `3`               | Cart threshold: spend ≥ X to unlock discount |

**Multi-buy parsing (type 2):**
```python
qty_req = int(fl["firstPurchaseTotal"])
deal_total = float(fl["firstGift"]["total"])
per_unit = round(deal_total / qty_req, 4)
```

---

## Pagination Strategy

1. Fetch `size=1&from=0` to read `total`.
2. Loop: `from = 0, batch_size, 2*batch_size, …` until `from >= total`.
3. All pages return products for all branches — filter client-side after fetching all pages.

---

## SSL / TLS Notes

Use certifi for the SSL context:

```python
import certifi, ssl
ctx = ssl.create_default_context(cafile=certifi.where())
connector = aiohttp.TCPConnector(ssl=ctx)
```

---

## Rate Limiting / Politeness

No documented rate limits.  Observed behaviour:
- Up to ~15 concurrent requests handled without throttling.
- Recommended: keep `max_concurrent ≤ 15`.
- Exponential backoff (base 1 s, max 30 s) handles intermittent failures.

---

## Differences from Stor.ai (Tiv Taam / Carrefour)

| Aspect                | Stor.ai (Tiv Taam / Carrefour)         | ZuZ (Machsanei HaShook)                    |
|-----------------------|----------------------------------------|--------------------------------------------|
| Retailer param        | `appId=4`                              | `appId=2`                                  |
| Branch data           | Single `branch` object per response   | Map `branches[str(id)]` embedded in product|
| Barcode               | Parsed from GS1 image URL             | Top-level `barcode` / `localBarcode` field |
| Image URL             | Template with `{{size}}` / `{{extension}}` | Direct URL, no substitution           |
| Categories            | `family.categories[]` (hierarchical)  | `department` (flat single level)           |
| Search                | `/categories/{id}/products?q=` (fan-out across categories) | `/products?q=` (single request) |
