# Supermarket API Research Notes

Research date: 2026-05-17

## Goal

Validate real-world catalog coverage chain-by-chain against live supermarket APIs,
identify products missing from our local catalog/search, and map concrete scraper
fixes that increase coverage.

## Live Endpoint Pattern Used

Most chains in this project expose the ZuZ/Stor.ai style endpoint:

`GET /v2/retailers/{retailer_id}/branches/{branch_id}/categories/{category_id}/products`

Common query params:
- `appId=4`
- `from={offset}`
- `size={page_size}`
- `languageId=1`
- `categorySort={"sortType":1}`
- `filters={"mustNot":{"term":{"branch.isOutOfStock":true}}}`

## Coverage Audit Summary

Branch list endpoint used for drift check:

`GET /v2/retailers/{retailer_id}/branches?appId=2&languageId=1`

| Chain | API shape | Current coverage result | Gap / action |
|---|---|---|---|
| shufersal | Single global paginated search catalogue | Probe returned `24816` products across `1241` pages | No branch gap found; added per-attempt HTTP timeout |
| ramilevi | Live `/api/stores` plus per-store `/api/catalog` | Live list has `32` internet stores, static list has `30`, merged set has `43` | Live API omits several still product-bearing static IDs; scraper now merges live and static stores |
| tivtaam | ZuZ per-branch/per-category | Live list has `47`, curated static list has `9`; sampled live extras returned zero products, static branch `1980` still has products | Scraper now probes candidates and keeps product-bearing branches, including stale static branch `1980` |
| carrefour | Stor.ai per-branch/per-category with dynamic category discovery | Live list has `17`; stale static extras sampled as zero-product | Existing live branch discovery is correct |
| machsanei | ZuZ per-branch/per-category | Live list has `9`; old hardcoded branch `836` missed many products | Already fixed to discover and scrape all live branches |
| keshet | ZuZ per-branch/per-category | Live list has `21`, static list has `21` | No branch drift found; added per-attempt HTTP timeout |
| quik | ZuZ per-branch/per-category | Live list has `16`; static extra `3096` sampled as zero-product | Existing live branch discovery is correct |
| victory | ZuZ per-branch/per-category | Live list has `60`, static list has `60` | Existing live branch discovery is correct |
| ybitan | ZuZ per-branch/per-category | Live list has `21` in current probe, includes branch `1033`; stale static extras sampled as zero-product | Existing live branch discovery captures branch `1033` |
| yochananof | Magento GraphQL stores/categories/products | Live GraphQL returned `23` stores and `471` categories | Dynamic discovery is correct; added per-attempt GraphQL timeout |

## Missing Product Evidence (Live API vs Local Catalog)

### Machsanei HaShook (major)

Sampling extra branches (not scraped before) across core categories found hundreds
of unique product IDs not present in local active offers.

- Checked rows (sampled): `10190`
- Unique missing product IDs (sampled categories): `463`

Missing examples from live API:
- `פילה סלמון טרי ארוז` (`productId=240707`, branch `1650`)
- `פילה דג סלמון מעושן פרוס קפוא` (`2810118`, branch `1650`)
- `חצי פילה סלמון קפוא ללא עור` (`6382644`, branch `1650`)
- `100% סירופ מייפל` (`1277166`, branch `1650`)
- `פטריות שי-מג'י חומות` (`4264420`, branch `3474`)
- `קמח שקדים` (`3824294`, branch `3474`)

Observed search symptom in local API before refresh:
- exact query often returned `total=0` or only fuzzy substitutes, while exact live
  product was absent from chain results.

### Ybitan (moderate)

Live branch `1033` is present in API but absent in previous static branch list.

- Checked rows (sampled): `1355`
- Unique missing product IDs (sampled categories): `17`

Missing examples from branch `1033`:
- `אצבעות גבינה יורו` (`3242137`)
- `אסאדו עם עצם טרי - מחפוד` (`5326840`)
- `וודקה גריי גוס לימון` (`6618838`)
- `טקילה סיירה רפוסאדו` (`324649`)

### Rami Levy (store-list drift)

The live `/api/stores` response exposes `32` stores with `internet_store_id`, but it omits product-bearing static store IDs that still answer `/api/catalog` with non-zero totals.

Examples of product-bearing static IDs missing from the live store list:
- `290` אשדוד: `7065` products in catalog probe
- `331` כפר סבא: `7271` products in catalog probe
- `1197` באר שבע: `6452` products in catalog probe
- `1220` בית שאן: `7149` products in catalog probe
- `1307` ירושלים - אג'מי: `7590` products in catalog probe
- `1314` אילת: `10000` products in catalog probe
- `1329` רחובות: `8130` products in catalog probe
- `1333` חיפה - נווה שאנן: `6207` products in catalog probe
- `1401` ירושלים - רמות: `7270` products in catalog probe

Action: do not replace Rami Levy static stores with live stores. Merge live and static IDs, deduping by `internet_store_id`.

### Tiv Taam (branch-list drift in both directions)

The live `/branches` endpoint returned `47` branches, but many live extras returned no products in sampled categories. Static branch `1980` is not in the live list but still has catalog data.

Evidence:
- Static branch `1980` נובל אנרג'י returned products in category probes, including `90066` with `83` products.
- Live extra branches outside the curated static set returned no products in the first sampled core categories.

Action: merge live and static branch candidates, then probe a small set of high-signal categories and scrape only branches that actually expose catalog data.

### Victory / Quik / Carrefour / Keshet

- No unique product-ID misses were detected in sampled checks after ID normalization,
  but branch-list drift exists and can cause store/price coverage lag over time.
- Carrefour stale static extras `2998`, `3008`, `2995`, `3017`, `3018`, and `3361` sampled as zero-product.
- Quik stale static extra `3096` sampled as zero-product.
- Keshet live and static branch IDs matched.

### Shufersal / Yochananof

- Shufersal has no branch dimension in our scraper; global catalogue pagination was healthy in the probe.
- Yochananof uses GraphQL dynamic store/category discovery; probe returned `23` stores and `471` categories.

## Root Causes

1. Static branch lists drift from live `/branches` endpoint over time.
2. Machsanei scraper previously hardcoded a single branch (`836`) and ignored all
   other live branches.
3. Product search mismatch can hide misses because fuzzy fallback returns similar
   items even when the exact live product is absent.
4. Some store APIs drift backwards: Rami Levy and Tiv Taam live lists can omit
   product-bearing store IDs that still serve catalog pages.
5. Webapp exact comparison used only `canonical_product_id`; products split into
   separate canonical rows because of missing barcodes were marked missing in other
   stores even when equivalent offers existed.

## Implemented Fixes (Code)

1. `scrapers/machsanei_hashook/machsanei_hashook.py`
   - Refactored from single-branch scrape to true multi-branch scrape.
   - Default behavior now discovers live branches via `/branches` and scrapes all.
   - Added safe fallback to static `ONLINE_BRANCHES` if live discovery fails.
   - Store attribution now uses the current branch (`store_id`, `store_name`).

2. Dynamic branch discovery by default when `branches=None`:
    - `scrapers/ybitan/ybitan.py`
    - `scrapers/victory/victory.py`
    - `scrapers/quik/quik.py`
    - `scrapers/carrefour/carrefour.py`

3. Store-list drift handling:
   - `scrapers/ramilevi/ramilevi.py` now merges live `/api/stores` results with
     the static product-bearing list.
   - `scrapers/tivtaam/tivtaam.py` now merges live/static candidates and keeps
     only branches that return product data in probe categories.

4. HTTP robustness:
   - Added per-attempt request timeouts to Shufersal, Rami Levy, Tiv Taam,
     Keshet, and Yochananof retry calls. The other active scrapers already had
     the timeout patch.

5. Webapp comparison fix:
   - `webapp/backend/catalog_service.py` now treats canonical rows as equivalent
     when they share barcode, match key, or exact normalized name/brand/unit.
   - Product detail and basket comparison now load offers for equivalent canonical
     IDs, so a missing barcode in one chain no longer prevents cross-store
     comparison.

## Validation Performed

- Backend tests: `pytest webapp/backend/tests` (all passing).
- Syntax checks for modified scrapers: `python3 -m py_compile ...` (no errors).
- Machsanei multi-branch sanity run confirmed products from non-836 branches are
  now emitted by scraper output.
- Ybitan branch `1033` sanity run confirmed previously missing IDs are now emitted.
- Rami Levy validation: live `32`, static `30`, merged `43`; merged list preserves
  product-bearing static IDs omitted by the live endpoint.
- Tiv Taam validation: dynamic default scrape over category `90066` kept `9`
  product-bearing branches and included branch `1980`.
- Backend regression validation: `pytest webapp/backend/tests` passed with a new
  test proving a no-barcode equivalent offer appears in product detail and basket
  comparison.

## Operational Next Step

Run a full catalog refresh in the webapp backend so active offers include the new
branch coverage and become visible in `/api/products/search`.
