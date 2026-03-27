# Shufersal API Documentation

> Platform: **Shufersal custom backend**
> Base URL: `https://www.shufersal.co.il/online/he`
> Research date: 2026-03

---

## Overview

Shufersal's online store exposes a JSON search API at the same origin as the website.  The API requires no authentication.  Setting `Accept: application/json` in the request headers makes the server return JSON instead of HTML.  Prices and stock are **chain-wide** — there is no per-branch filtering in the public search API.

---

## Endpoints

### 1. Search / Catalogue

```
GET /online/he/search/results?q={query}&page={page_number}
```

**Headers required:**
```
Accept: application/json, text/plain, */*
```

**Query parameters:**

| Parameter | Type   | Description                                                         |
|-----------|--------|---------------------------------------------------------------------|
| `q`       | string | Search keyword (Hebrew or transliterated). Pass empty string `""` for full catalogue. |
| `page`    | int    | Zero-based page number.                                             |

**Notes:**
- Page size is **fixed at 20** — the server ignores any `pageSize` parameter.
- Total pages and total products are returned in every response's `pagination` object.
- No session cookie or CSRF token is required.

**Response shape:**
```json
{
  "results": [ <Product>, ... ],
  "pagination": {
    "pageSize": 20,
    "currentPage": 0,
    "numberOfPages": 1230,
    "totalNumberOfResults": 24591
  },
  "facets": [ ... ]
}
```

---

## Product Object

```json
{
  "code": "P_22",
  "sku": "7296073440314",
  "name": "חלב תנובה מלא 3% שומן",
  "price": { "value": 6.90, "currencyIso": "ILS" },
  "categoryPrice": { "value": 6.90, "currencyIso": "ILS" },
  "allCategoryCodes": ["A04", "A0410", "A041001"],
  "images": [
    {
      "imageType": "PRIMARY",
      "format": "medium",
      "url": "https://a.fsimg.co.il/product/retail/fan/image/medium/7296073440314.png"
    }
  ],
  "sellingMethod": { "code": "BY_PACKAGE" },
  "food": true,
  "brand": { "name": "תנובה" },
  "ean": null,
  "manufacturer": "תנובה",
  "unitDescription": "1 ליטר",
  "unitForComparison": "מ\"ל",
  "valueForComparison": 1000.0,
  "numberContentUnits": 1.0,
  "weightIncrement": null,
  "minWeight": null,
  "maxWeight": null
}
```

### Key fields

| Field                  | Type            | Notes                                                          |
|------------------------|-----------------|----------------------------------------------------------------|
| `code`                 | string          | Internal product code (e.g. `"P_22"`)                         |
| `sku`                  | string          | Numeric string — often the EAN-13, but NOT always a valid barcode |
| `name`                 | string          | Hebrew product name                                            |
| `categoryPrice.value`  | float           | Current shelf price (preferred over `price.value`)             |
| `price.value`          | float           | Fallback price                                                 |
| `allCategoryCodes`     | list[string]    | Shufersal-internal category hierarchy codes                    |
| `images`               | list            | `imageType="PRIMARY"` images in formats: `thumbnail`, `small`, `medium`, `large`, `product`, `zoom` |
| `sellingMethod.code`   | string          | `"BY_WEIGHT"` \| `"BY_PACKAGE"` \| `"BY_UNIT"`                |
| `food`                 | bool            | Whether the product is a food item                             |
| `brand.name`           | string\|null    | Brand name                                                     |
| `ean`                  | string\|null    | EAN barcode — **often null**; prefer `sku` with caution        |
| `manufacturer`         | string\|null    | Manufacturer name                                              |
| `unitDescription`      | string\|null    | Human-readable size e.g. `"1 ליטר"`, `"500 מ\"ל"`             |
| `unitForComparison`    | string\|null    | Unit label e.g. `"מ\"ל"`, `"גרם"`, `"יח'"`                   |
| `valueForComparison`   | float\|null     | Numeric quantity for the unit above                            |
| `numberContentUnits`   | float\|null     | Number of units per package                                    |
| `weightIncrement`      | float\|null     | Weight step for BY_WEIGHT products                             |

---

## Barcode Notes

Shufersal's barcode situation is nuanced:

| Field   | Reliability | Recommendation                                                 |
|---------|-------------|----------------------------------------------------------------|
| `ean`   | Low (often `null`) | Use when non-null                                     |
| `sku`   | Medium      | Often IS the EAN-13, but can be an internal numeric code      |

The scraper stores `ean` as `barcode` when available, and leaves it `None` otherwise.

---

## Image Selection

Images come in multiple formats for each `imageType`.  The recommended priority order for `PRIMARY` images:

```
large > medium > product > small > thumbnail
```

Skip images whose URL contains `"default"` (placeholder images).

---

## Pagination Strategy

1. Fetch `page=0` to read `pagination.numberOfPages` and `pagination.totalNumberOfResults`.
2. Fetch pages `1, 2, …, numberOfPages-1` concurrently (respect `max_concurrent`).
3. Page size is always 20 regardless of any parameter — plan accordingly (~24,600 products ÷ 20 = ~1,230 pages for the full catalogue).

---

## Filtering

| Filter type  | Method                                        |
|--------------|-----------------------------------------------|
| Name/keyword | Native — pass `q={keyword}` in request        |
| Category     | Post-fetch (match against `allCategoryCodes`) |
| Barcode      | Post-fetch (match `ean` or `sku`)             |

---

## No Per-Branch Pricing

The search API returns a single chain-wide price per product.  Shufersal does not expose per-branch price differences through this endpoint.

---

## Rate Limiting / Politeness

- Observed: up to ~20 concurrent requests before occasional 429 responses.
- Recommended: `max_concurrent ≤ 15` with a small inter-chunk delay (0.3–1.0 s).
- Retry with exponential backoff (base 1 s, max 30 s) on any non-200 response.
