# Machsanei HaShook API Documentation

> Platform: **ZuZ** (AngularJS)  
> Retailer ID: `1107`  
> Base URL: `https://www.mck.co.il`  
> Updated: 2026-05-16

## Overview

Machsanei HaShook uses the same per-branch/per-category `appId=4` API pattern used
by other ZuZ chains in this repository.

Important coverage note:
- The branch list is live and changes over time.
- Scraping only one branch causes real catalog misses.

## Endpoints

### 1) Branch list

`GET /v2/retailers/1107/branches?appId=2&languageId=1`

Returns branch metadata (`id`, `name`, `city`, `location`, etc.).

### 2) Per-branch category products

`GET /v2/retailers/1107/branches/{branch_id}/categories/{category_id}/products`

Typical query params:
- `appId=4`
- `from={offset}`
- `size={page_size}`
- `languageId=1`
- `categorySort={"sortType":1}`
- `filters={"mustNot":{"term":{"branch.isOutOfStock":true}}}`

Response shape:

```json
{
  "total": 186,
  "products": [ ... ]
}
```

## Product Schema Notes

- Branch data is in `product["branch"]` (single object), not in a branch map.
- Product ID should use `productId` first, fallback to `id`.
- Barcode is commonly extracted from image URL (GS1 pattern) in current scraper.
- Categories appear under `product["family"]["categories"]`.

## Search Behavior Caveat

For Machsanei category endpoint, `q=` is not reliable in current observations:
- category responses often ignore the query and return full category totals.
- scraper should not depend on `q` for correctness of full-catalog ingestion.

## Branch Coverage Findings (2026-05-16)

- Live branches from API: `9`
- Previous scraper default: `1` branch (`836`) only
- Missing products were confirmed in additional branches (including salmon SKUs)

Examples observed in non-836 branches:
- `פילה סלמון טרי ארוז` (`productId=240707`)
- `פילה דג סלמון מעושן פרוס קפוא` (`2810118`)
- `חצי פילה סלמון קפוא ללא עור` (`6382644`)

## Scraper Guidance

Preferred strategy:
1. Fetch live branches from `/branches`.
2. For each branch, iterate known top-level categories.
3. Paginate by `from/size` until coverage complete.
4. Deduplicate by `productId` per branch.
5. Keep a fallback static branch list only if live branch discovery fails.

This is now the behavior implemented in `scrapers/machsanei_hashook/machsanei_hashook.py`.
