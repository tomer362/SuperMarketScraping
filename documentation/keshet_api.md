# Keshet Teamim API Documentation (קשת טעמים)

> Platform: **ZuZ** (AngularJS) — Retailer ID `1219`
> Base URL: `https://www.keshet-teamim.co.il`
> Research date: 2026-03; refreshed with Playwright on 2026-05-13

---

## Overview

Keshet Teamim's online store runs on the **ZuZ** platform using the `appId=4` per-branch/per-category endpoint pattern.  This is different from Machsanei HaShook (also ZuZ but `appId=2`) which returns all branches in one response.

With `appId=4`, each request is scoped to a **specific branch + category**, and branch data is embedded in each product as a single `product["branch"]` object (not a map keyed by branch ID).

The global `/v2/retailers/1219/products` endpoint (appId=2) is **capped** and misses many products (eggs, fresh chicken, etc.).  The per-branch/per-category endpoint is the correct approach.

Current operational note: branch choice matters a lot. On 2026-05-13, branch `1570` returned only `107` deduped products in the scraper, while the browser-selected branch `2585` returned `12,281` products with the same scraper. Use `2585` as the default representative branch unless a fresh branch probe shows a better live branch.

---

## Endpoints

### 1. Branch List

```
GET /v2/retailers/1219/branches?appId=2&languageId=1
```

**Response shape:**
```json
{
  "branches": [
    {
      "id": 1570,
      "name": "סניף אשדוד",
      "city": "אשדוד",
      "location": ""
    }
  ]
}
```

**Known online branch IDs (as of 2026-03):**

| ID   | Name                              | City               |
|------|-----------------------------------|--------------------|
| 2725 | מרכז משלוחים רובוטי מרכז         | פתח תקווה         |
| 2585 | מרכז משלוחים רובוטי צפון         | חיפה               |
| 1570 | סניף אשדוד                       | אשדוד             |
| 1572 | סניף באר שבע                     | באר שבע           |
| 1571 | סניף גבעת ברנר                   | גבעת ברנר         |
| 3403 | סניף חדרה                        | חדרה              |
| 1563 | סניף חיפה הדר                    | חיפה-הדר          |
| 1564 | סניף חיפה קריית אליעזר           | חיפה-קריית אליעזר |
| 1556 | סניף יקנעם                       | יוקנעם            |
| 1668 | סניף כפר סבא                     | כפר סבא           |
| 1567 | סניף כרמיאל                      | כרמיאל            |
| 1569 | סניף נהריה                       | נהריה             |
| 1568 | סניף נוף הגליל (נצרת עילית)      | נוף הגליל         |
| 1559 | סניף נשר                         | נשר               |
| 1437 | סניף נתניה                       | נתניה             |
| 2291 | סניף עכו                         | עכו               |
| 2656 | סניף עפולה                       | עפולה             |
| 1840 | סניף פתח תקווה                   | פתח תקווה         |
| 1566 | סניף קריית חיים                  | קריית חיים        |
| 1547 | סניף ראשון לציון                 | ראשון לציון       |
| 3266 | סניף ראשון לציון מערב            | ראשון לציון       |

---

### Representative Branch Selection

Use branch `2585` for one-store catalog comparison/audits. Playwright showed the live site using this branch by default, and quick endpoint probes showed large category totals for it.

Example branch-total contrast from 2026-05-13:

| Branch | Category `79718` | Category `95113` | Category `79619` | Scraper Deduped Total |
|---:|---:|---:|---:|---:|
| `1570` | 1 | 1 | 26 | 107 |
| `2585` | 1,064 | 371 | 3,471 | 12,281 |

When refreshing this decision, probe multiple high-volume categories before changing the representative branch. Do not trust branch presence alone; some valid branch IDs can have sparse online inventory.

---

### 2. Per-Branch, Per-Category Product Catalogue

```
GET /v2/retailers/1219/branches/{branch_id}/categories/{cat_id}/products
    ?appId=4&from={offset}&size={page_size}&languageId=1
    &categorySort={"sortType":1}
    &filters={"mustNot":{"term":{"branch.isOutOfStock":true}}}
```

**Response shape:**
```json
{
  "total": 87,
  "products": [ <Product>, ... ]
}
```

**Recommended page size:** `100`

**Pagination strategy:**
1. Probe with `size=1&from=0` to read `total`.
2. Loop: `from = 0, 100, 200, …` until `from >= total`.
3. Deduplicate by `productId` across categories (products can appear in multiple categories).

---

### 3. Name Search (per branch+category)

Add `q={url_encoded_query}` to the products endpoint:

```
GET /v2/retailers/1219/branches/{branch_id}/categories/{cat_id}/products
    ?appId=4&from=0&size=100&languageId=1
    &categorySort={"sortType":1}
    &filters={"mustNot":{"term":{"branch.isOutOfStock":true}}}
    &q=%D7%91%D7%99%D7%A6%D7%99%D7%9D
```

To search across all products, fan out across all `MAIN_CATEGORIES` with the same `q=` parameter and deduplicate.

---

## Top-Level Categories (MAIN_CATEGORIES)

Categories were discovered from the site's `data.js` bundle (2026-03).  The scraper probes each category per branch and skips categories returning 0 products.

The rendered site currently exposes many more leaf category links than `MAIN_CATEGORIES`. On 2026-05-13, Playwright found `434` unique `/categories/{id}/products` links in the DOM. The current scraper still returns a large catalog from the parent categories because parent category endpoints include descendants, but future agents should re-scan the leaf/category constants if coverage drops or a category family disappears.

| Category ID | Name (Hebrew)                          |
|-------------|----------------------------------------|
| 95113       | מעדניית נקניקים                        |
| 94252       | מעדניית גבינות/דגים מלוחים/סלטים וחמוצים |
| 95167       | קצביה                                  |
| 79704       | ירקות ופירות                           |
| 79687       | לחמים פרכיות וצנימים                   |
| 79653       | חטיפים וממתקים                         |
| 95103       | אוכל מוכן                              |
| 79619       | שמנים בישול ואפיה                      |
| 97333       | דגים קפואים ופירות ים                  |
| 79591       | שימורים וקפואים                        |
| 79718       | מוצרי חלב וביצים                       |
| 79667       | שתייה וחטיפים                          |
| 79571       | פארם ותינוקות                          |
| 79740       | ניקיון וחד פעמי                        |
| 79764       | כלי בית ומזון לחיות                    |
| 99065       | טואלטיקה ומוצרי תינוקות               |
| 79821       | עוף בשר ודגים                          |
| 79603       | מעדניה וסלטים                          |
| 79731       | דגנים                                  |
| 79835       | בריאות ותזונה                          |
| 96764       | חג                                     |
| 79807       | טואלטיקה                               |
| 95135, 124113, 119993, 94523, 112550, 96794, 96505, 94600, 120357, 94246, 97314, 95840, 112855, 93755 | (unnamed categories — scrape anyway) |

---

## Product Object (appId=4 schema)

```json
{
  "productId": 6080688,
  "id": 20164375,
  "localName": "חלב תנובה 3% שומן 1 ליטר",
  "names": {
    "1": {
      "short": "חלב תנובה 3%",
      "long": "חלב תנובה 3% שומן 1 ליטר"
    }
  },
  "image": {
    "url": "https://cdn.keshet-teamim.co.il/upload/images/product-images/7290000066882-{{size}}.{{extension||'jpg'}}"
  },
  "isWeighable": false,
  "weight": 1000,
  "unitOfMeasure": { "names": { "1": "מ\"ל" } },
  "brand": { "names": { "1": "תנובה" } },
  "family": {
    "categories": [
      { "id": 79718, "names": { "1": { "name": "מוצרי חלב וביצים" } } }
    ]
  },
  "branch": {
    "isActive": true,
    "isVisible": true,
    "regularPrice": 6.90,
    "salePrice": null,
    "isOutOfStock": false,
    "specials": []
  }
}
```

### Key field differences vs. appId=2 (Machsanei HaShook)

| Field                | appId=2 (Machsanei HaShook)             | appId=4 (Keshet, Quik, Victory, Ybitan) |
|----------------------|-----------------------------------------|-----------------------------------------|
| Branch data location | `product["branches"][str(branch_id)]`  | `product["branch"]` (singular object)  |
| Barcode              | Top-level `barcode` / `localBarcode`   | Not present — extract from image URL   |
| Image URL            | Direct URL, no templates               | Contains `{{size}}` / `{{extension}}`  |
| Categories           | `product["department"]` (flat)         | `product["family"]["categories"]` (list)|
| Brand                | Not available                          | `product["brand"]["names"]["1"]`       |

---

## Barcode Extraction

The barcode is **not** a top-level field.  Extract it from the image URL using regex:

```python
import re
_BARCODE_RE = re.compile(r"/(\d{7,14})-")

def extract_barcode(image_url: str) -> str | None:
    m = _BARCODE_RE.search(image_url)
    return m.group(1) if m else None
```

Example image URL:
```
https://cdn.keshet-teamim.co.il/upload/images/product-images/7290000066882-large.jpg
```
→ barcode: `7290000066882`

---

## Image URL Expansion

Replace template placeholders before use:

```python
import re

def expand_image_url(raw: str) -> str:
    url = raw.replace("{{size}}", "large")
    url = re.sub(r"\{\{extension(?:\|\|'[^']*')?\}\}", "jpg", url)
    return url
```

---

## Pricing & Deals

Each product's `branch` object contains:

| Field          | Type         | Notes                                       |
|----------------|--------------|---------------------------------------------|
| `regularPrice` | float\|null  | Shelf price; skip if null or ≤ 0            |
| `salePrice`    | float\|null  | Promotional price; null when no active promo|
| `isOutOfStock` | bool         | Filtered out by the `filters=` param        |
| `specials`     | array        | Active promotions (same structure as appId=2)|

**Deal parsing:**
- If `salePrice < regularPrice` → `price_reduction` deal.
- If `specials[].firstLevel.type == 2` → multi-buy: N units for total price.
- If `specials[].firstLevel.type == 3` → cart threshold deal.

**Multi-buy calculation:**
```python
qty_req = int(fl["firstPurchaseTotal"])
deal_total = float(fl["firstGift"]["total"])
per_unit = round(deal_total / qty_req, 4)
```

---

## SSL / TLS Notes

```python
import certifi, ssl
ctx = ssl.create_default_context(cafile=certifi.where())
connector = aiohttp.TCPConnector(ssl=ctx)
```

---

## Rate Limiting / Politeness

No documented rate limits.  Observed behaviour:
- Up to ~15 concurrent requests handled without throttling.
- Recommended: `max_concurrent ≤ 15`.
- Exponential backoff (base 1 s, max 30 s) handles transient failures.

---

## Instrumentation via Browser DevTools (F12)

To inspect the API when the site changes:

1. Open `https://www.keshet-teamim.co.il` in Chrome.
2. Open DevTools → Network tab → filter by `Fetch/XHR`.
3. Browse to any category in the store.
4. Look for requests to `/v2/retailers/1219/branches/{bid}/categories/{cat_id}/products`.
5. Inspect the request URL, query parameters, and response JSON shape.
6. If the category IDs change, look for requests to `/data.js` or `/v2/retailers/1219/categories` to find the updated list.

---

## Refreshing Branch and Category Constants

Future agents should refresh these constants when product counts drop, coverage drops, or Playwright shows categories that the scraper never probes.

Branch refresh checklist:

1. Fetch the live branch list from `GET /v2/retailers/1219/branches?appId=2&languageId=1`.
2. Open `https://www.keshet-teamim.co.il` in Playwright and inspect product requests; the branch ID in `/branches/{bid}/...` is the browser's current default.
3. Probe representative high-volume categories for candidate branches using `size=1` and compare `total`.
4. Prefer a branch with broad inventory, not just a valid branch ID. As of 2026-05-13, use `2585`.

Category refresh checklist:

1. Open the site in Playwright after the page fully loads.
2. Extract category links from the DOM with this snippet:

```js
() => {
  const categories = new Map();
  for (const a of document.querySelectorAll('a[href*="/categories/"][href$="/products"]')) {
    const match = a.getAttribute('href')?.match(/\/categories\/(\d+)\/products/);
    if (match) {
      categories.set(Number(match[1]), (a.textContent || '').trim().replace(/\s+/g, ' '));
    }
  }
  return [...categories.entries()].sort((a, b) => a[0] - b[0]);
}
```

3. Compare the extracted IDs against `MAIN_CATEGORIES` in `scrapers/keshet/keshet.py`.
4. Probe whether parent categories still include descendants before replacing `MAIN_CATEGORIES` with all leaf categories. Parent categories reduce request count and dedupe cleanly, but leaf categories may become necessary if the platform changes.
5. After changing constants, run a one-branch scrape against branch `2585` and expect roughly five-digit product count, not hundreds.

Quick Playwright/fetch probe:

```js
async () => {
  const ids = [79718, 95113, 79619, 79591, 95816];
  const branch = 2585;
  const rows = [];
  for (const id of ids) {
    const res = await fetch(`/v2/retailers/1219/branches/${branch}/categories/${id}/products?appId=4&languageId=1&from=0&size=1`);
    const json = await res.json();
    rows.push({ id, total: json.total ?? null, first: json.products?.[0]?.localName || null });
  }
  return rows;
}
```

**Checking for capped global endpoint:**
```bash
curl "https://www.keshet-teamim.co.il/v2/retailers/1219/products?appId=2&from=0&size=1&languageId=1" \
  -H "Accept: application/json"
# Check "total" — if it's suspiciously low, the global endpoint is capped.
```

**Checking per-branch endpoint:**
```bash
curl "https://www.keshet-teamim.co.il/v2/retailers/1219/branches/2585/categories/79718/products?appId=4&from=0&size=5&languageId=1&categorySort=%7B%22sortType%22%3A1%7D" \
  -H "Accept: application/json"
```
