# CHP API Documentation

> Platform: **CHP price-comparison aggregator**
> Base URL: `https://chp.co.il`
> Research date: 2026-05

---

## Overview

CHP is not a normal supermarket online store.  It is a cross-store price-comparison site that aggregates nearby physical branches and online supermarket stores for one selected product.

The user flow is location-first:

1. Enter a shopping area in the first field, for example `קרית ביאליק`.
2. Enter a product name or barcode in the second field, for example `אבקת שום`.
3. Click `בדוק`.
4. CHP lands on a comparison page containing the selected product metadata, nearby physical-store prices, and online-store prices.

Captured browser example:

```text
Shopping area: קרית ביאליק
Product query: אבקת שום
Result title: השוואת מחירים בסופר של אבקת שום, 90 גרם
URL: https://chp.co.il/קרית ביאליק /9000/9500/אבקת שום/0
Product: אבקת שום, 90 גרם
Brand/manufacturer: תבליני טעם וריח
Barcode: 7290000134338
```

The scraper should treat CHP rows as comparison observations.  A product can have many store rows; each row should be normalized separately.

---

## Endpoints

### 1. Shopping Address Autocomplete

```http
GET /autocompletion/shopping_address?term={city_or_street}&from=0&u={session_float}
```

**Observed response shape:**

```json
[
  {
    "value": "קרית ביאליק",
    "label": "קרית ביאליק",
    "id": "9500_9000"
  }
]
```

**Notes:**
- `id` is `{city_id}_{street_id}`.
- `street_id=9000` means a whole-city search.
- The `u` parameter is a random float stored by the site in browser state.  Any stable random float per scraping session is sufficient.

### 2. Product Autocomplete

```http
GET /autocompletion/product_extended
    ?term={query_or_barcode}
    &from={offset}
    &u={session_float}
    &shopping_address={city_label}
    &shopping_address_city_id={city_id}
    &shopping_address_street_id={street_id}
```

**Pagination:**
- Use `from=0,10,20,...`.
- Later pages can include navigation sentinel rows such as `prev`; ignore rows with `id` equal to `prev` or `next`.
- Stop when a page returns no new real products or fewer than a full page of real products.

**Product ID patterns:**

| Pattern | Meaning |
|---------|---------|
| `7290027600007_{barcode}` | Canonical product with image metadata |
| `temp_{barcode}` | Barcode product without canonical image |
| `F_{code}` | Franchise product |
| `our_{number}` | Generic or weighable product |

Product metadata can include name/contents, brand/manufacturer, barcode, pack size, and base64 image data.

### 3. Compare Results

```http
GET /main_page/compare_results
    ?shopping_address={city_label}
    &shopping_address_street_id={street_id}
    &shopping_address_city_id={city_id}
    &product_name_or_barcode={product_label}
    &product_barcode=0
    &from=0
    &num_results=20
```

This returns an HTML page or fragment with product metadata and up to two `<table class="results-table">` tables.

For sitemap-enumerated products, pass the full CHP product identifier as `product_name_or_barcode`, for example `7290027600007_16000423534`.  Passing only the bare barcode is not reliable for this endpoint.

Do not use `bare=true`; it can trigger server-side price obfuscation.

### Endpoint Validation Notes (2026-05)

The scraper now exposes low-level async helpers that map directly to the three request phases:

1. `fetch_shopping_address_page(...)`
2. `fetch_product_autocomplete_page(...)` and `iter_product_autocomplete_pages(...)`
3. `fetch_compare_results_page(...)`

Observed payload validation notes:

- `shopping_address`
  - JSON is a list of objects with `value`, `label`, `id`.
  - `id` is generally `{city_id}_{street_id}`.
  - In practice, server may return JSON while reporting `Content-Type: text/html`; validate parsed structure, not content-type.

- `product_extended`
  - JSON is a list of objects.
  - Real rows usually contain: `id`, `value`, `label`, `parts`.
  - `parts` can contain: `name_and_contents`, `manufacturer_and_barcode`, `pack_size`, `small_image`, `chainnames`, `price_range`.
  - Sentinel rows (`id=prev` / `id=next`) can appear and must be excluded from product parsing.

- `compare_results`
  - Response is HTML (not JSON) and includes hidden product metadata inputs and up to two `results-table` tables.
  - Endpoint accepts caller-controlled `from` and `num_results` parameters; scraper helper exposes both.
  - `product_barcode` can be supplied as observed in browser requests; `0` remains a safe default.
  - Library helper returns both parsed row objects and rich per-row detail dicts (`physical_row_details`, `online_row_details`) containing:
    - store identity (chain/store/address/url/type),
    - pricing (`price`, `regular_price`, `sale_price`, `discount_percent`, `price_per_base_unit`),
    - structured deal info, and
    - raw deal fields from the source row.

Relevant implementation:

- `scrapers/chp/chp.py`:
  - `fetch_shopping_address_page`
  - `fetch_product_autocomplete_page`
  - `iter_product_autocomplete_pages`
  - `fetch_compare_results_page`

### 4. Sitemap Product Identifier Enumeration

```http
GET /sitemap/{page_number}
```

CHP exposes a large sitemap split across numbered pages.  Product links end with a durable product identifier segment, such as:

```text
/קרית ביאליק/9000/9500/.../7290027600007_7290000000336/1
```

Use these IDs for all-products scraping:

1. Fetch sitemap pages.
2. Extract product IDs matching patterns such as `7290027600007_{barcode}`, `temp_{barcode}`, `our_{number}`, `F_{code}`, and `Q_{code}`.
3. De-duplicate IDs globally.
4. For each ID, fetch `compare_results` for the selected shopping area.
5. Hydrate product name, barcode, brand, and unit metadata from the comparison HTML.

---

## Result Tables

### Physical Store Table

The first `results-table` is the nearby physical-store section.

| Column | Meaning |
|--------|---------|
| `רשת` | Chain name |
| `שם החנות` | Branch/store name |
| `כתובת החנות` | Physical address |
| `מבצע` | Deal button or empty cell |
| `מחיר` | Regular shelf price |

Rows can be followed by mobile-only address rows with class `display_when_narrow`.  Ignore those duplicate display rows and parse only main rows.

Captured example rows:

| Chain | Store | Address | Deal | Price |
|-------|-------|---------|------|-------|
| סופר ספיר | קרית ים גאולה | גאולה כהן 4, קרית ים | `9.00 *` | `11.90` |
| סטופמרקט | קריות | שד ההסתדרות 271, חיפה | empty | `10.90` |

### Online Store Table

The second `results-table` is `תוצאות מחנויות באינטרנט`.

| Column | Meaning |
|--------|---------|
| `רשת` | Online chain name |
| `שם החנות` | Store name, usually a link to the online store |
| `אתר אינטרנט` | Website domain or URL |
| `מבצע` | Deal button or empty cell |
| `מחיר` | Regular online price |

Captured example rows:

| Chain | Store URL | Price |
|-------|-----------|-------|
| חצי חינם אונליין | `https://shop.hazi-hinam.co.il/` | `10.00` |
| ויקטורי אונליין | `https://www.victoryonline.co.il` | `11.50` |
| שופרסל אונליין | `https://www.shufersal.co.il/online/he/search?text=134338` | `11.90` |

---

## Deal Extraction

Deals are exposed directly on `button.btn-discount`.  Prefer reading button attributes over clicking the modal.

Captured button example:

```html
<button
  class="btn btn-danger btn-xs btn-discount"
  data-discount-title="סופר ספיר"
  data-discount-desc="3 יחידות ב- 27 ש&quot;ח (מחיר ליחידה 9.00 ש&quot;ח)<BR>בתוקף עד 31/05/2026<BR>"
  title="מחיר מבצע בסופר ספיר קרית ים גאולה: 9.00 ש&quot;ח ליחידה/ק&quot;ג. לחצו על הכפתור לפרטים נוספים.">
  9.00 *
</button>
```

Recommended normalized fields:

| Field | Source |
|-------|--------|
| `regular_price` | Price column |
| `sale_price` | Button text or parsed deal description when lower than regular price |
| `deal.raw_text` | `data-discount-desc` with `<BR>` converted to line breaks or spaces |
| `deal.store_title` | `data-discount-title` |
| `deal.expires_at` | Parse `בתוקף עד DD/MM/YYYY` when present |
| `deal_type` | Infer `multi_buy` from quantity patterns; otherwise `price_reduction` when a lower sale price is present |

Keep raw deal text even when structured parsing fails.  Hebrew promotion text varies, and raw text is valuable for future parser improvements.

---

## Anti-Obfuscation Notes

CHP can obfuscate comparison HTML when requests look automated.  Obfuscated responses can contain many zero-width Unicode characters and malformed-looking price text.

Use this request strategy:

1. Create a fresh HTTP session for comparison-page fetches.
2. Warm the session with `GET /` using browser navigation headers so the server sets its cookies.
3. Fetch `/main_page/compare_results` with navigation-style headers:
   - no `X-Requested-With`,
   - document-style `Accept`,
   - `Referer: https://chp.co.il/`,
   - normal browser `User-Agent`.
4. Keep comparison requests sequential or slow.  Parallel or rapid comparison requests can trigger obfuscation.
5. Detect obfuscation before parsing:
   - more than roughly 200 zero-width characters, or
   - very large response with no `table.results-table`.

Autocomplete endpoints are JSON/XHR endpoints and should still use XHR-style headers.

---

## Generic Scraper Design

CHP should be modelled as a comparison source:

```text
city/search area -> product candidates -> comparison page -> row-level observations
```

For every parsed row, normalize:

| Normalized field | CHP source |
|------------------|------------|
| Product name | Product header or autocomplete payload |
| Barcode | Product header or autocomplete `parts.manufacturer_and_barcode` |
| Brand/manufacturer | Product header or autocomplete payload |
| Chain/store name | Result row `רשת` and `שם החנות` |
| Store type | `physical` for table 1, `online` for table 2 |
| Address | Physical table address column |
| Website/store URL | Online table website/link columns |
| Regular price | Result row price column |
| Sale/deal price | Deal button text or parsed description |
| Raw deal text | `data-discount-desc` |
| Scraped location | Selected city/street identifiers |

The current scraper returns physical + online records by default.  Pass `include_physical=False` in code, or `--online-only` in `chp_main.py`, to keep only online-store rows. Physical rows use a branch-specific store ID composed from chain, branch name, and address so branches do not collapse into one store.

`scrape(...)` also returns an additive top-level map, `compare_row_details_by_product`, by default. For each product ID key, the value contains:

- `physical_row_details`
- `online_row_details`
- `all_row_details_sorted_by_price`
- `rows_total`
- `cheapest_row`
- `highest_row`

Raw compare HTML is excluded by default (`include_compare_html=False`) and can be enabled explicitly when debugging.

---

## Validation Checklist

Use these cases when maintaining or extending the CHP scraper:

- City autocomplete resolves `קרית ביאליק` and `תל אביב`.
- Product autocomplete works for Hebrew text search such as `אבקת שום`.
- Product autocomplete works for barcode search such as `7290000134338`.
- Comparison parsing handles a product with both physical and online rows.
- Comparison parsing handles a product with no online rows.
- Deal extraction captures `data-discount-title`, `data-discount-desc`, visible deal price, and regular price.
- Rows without deal buttons still parse with empty deal fields.
- Obfuscated HTML is detected and retried rather than silently parsed.

Browser truth validation workflow:

```bash
./venv/bin/python tools/chp_browser_compare_validation.py \
  --city "תל אביב" \
  --out-dir output_dir/chp/browser_validation
```

This workflow opens real CHP compare pages in a browser and writes per-scenario artifacts:

- `browser_rows.json` — DOM source-of-truth rows from physical/online tables.
- `parser_rows.json` — parser output rows from `parse_compare_results` + detail builders.
- `mismatch_report.json` — field-by-field diff and acceptance checks.
- `browser_page.png` — kept only when mismatches are found.

Smoke fixture:

```bash
python - <<'PY'
from pathlib import Path
from scrapers.chp.chp import ChpProduct, parse_compare_results

html = Path("documentation/chp_documentation/comparison_result_example.html").read_text()
product = ChpProduct({
    "id": "7290027600007_7290010429554",
    "value": "שמן זית כתית מעולה קלאסי, 750 מ\"ל",
    "parts": {
        "name_and_contents": "שמן זית כתית מעולה קלאסי, 750 מ\"ל",
        "manufacturer_and_barcode": "יצרן/מותג: יד מרדכי, ברקוד: 7290010429554",
    },
})
physical, online = parse_compare_results(html, product)
assert physical
assert online
assert any(row.deal_text for row in physical)
assert any(row.website.startswith("http") for row in online)
print(len(physical), len(online))
PY
```
