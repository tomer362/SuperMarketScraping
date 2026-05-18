# Yenot Bitan API Documentation (יינות ביתן)

> Platform: **ZuZ** (AngularJS) — Retailer ID `1131`
> Base URL: `https://www.ybitan.co.il`
> Research date: 2026-03 (updated 2026-05-16)

---

## Overview

Yenot Bitan's online store runs on the **ZuZ** platform with the `appId=4` per-branch/per-category endpoint pattern — identical in structure to Keshet Teamim, Quik, and Victory.

The global `/v2/retailers/1131/products` endpoint (appId=2) is **capped**.  Use the per-branch/per-category approach for complete data.

Yenot Bitan operates both "Yenot Bitan Online" and "Bitan Market" branded branches — both are scraped.

Coverage update (2026-05-16):
- Live API branch audit found an additional active branch `1033` with product-bearing
  categories.
- Scraper default flow now uses live branch discovery (`/branches`) when no explicit
  branch list is provided.

---

## Endpoints

### 1. Branch List

```
GET /v2/retailers/1131/branches?appId=2&languageId=1
```

**Response shape:**
```json
{
  "branches": [
    { "id": 960, "name": "אור יהודה - Online", "city": "אור יהודה", "location": "" }
  ]
}
```

**Known online branch IDs (as of 2026-05):**

| ID   | Name                             | City            |
|------|----------------------------------|-----------------|
| 960  | אור יהודה - Online               | אור יהודה      |
| 958  | אור עקיבא - Online               | אור עקיבא      |
| 1985 | אילת - ביתן מרקט+online          | אילת            |
| 1855 | אשדוד - Online                   | אשדוד          |
| 964  | אשקלון - Online                  | אשקלון         |
| 2892 | בית שמש - online                 | בית שמש        |
| 1684 | גבעתיים - Online                 | גבעתיים        |
| 2777 | גני העיר רחובות - Online         | רחובות         |
| 3477 | דליית אל כרמל - Online           | דליית אל כרמל  |
| 1973 | הרצליה - online                  | הרצליה         |
| 2960 | חיפה - אודיטוריום Online         | חיפה            |
| 1369 | ירושלים - Online                 | ירושלים        |
| 1975 | כפר סבא - online                 | כפר סבא        |
| 1943 | לוד - Online                     | לוד             |
| 1015 | נתניה - Online                   | נתניה          |
| 1685 | פתח תקווה - Online               | פתח תקווה      |
| 2177 | קרית אתא - Online                | קרית אתא       |
| 2649 | תל אביב - online                 | תל אביב        |
| 987  | אלפי מנשה - ביתן מרקט            | אלפי מנשה      |
| 2325 | אשדוד ח' - ביתן מרקט             | אשדוד          |
| 2080 | הרצליה לב הרצליה - ביתן מרקט    | הרצליה         |
| 985  | מעלה אדומים - ביתן מרקט          | מעלה אדומים    |
| 1033 | רמלה - ביתן מרקט +Online         | רמלה            |

---

### 2. Per-Branch, Per-Category Product Catalogue

```
GET /v2/retailers/1131/branches/{branch_id}/categories/{cat_id}/products
    ?appId=4&from={offset}&size={page_size}&languageId=1
    &categorySort={"sortType":1}
    &filters={"mustNot":{"term":{"branch.isOutOfStock":true}}}
```

**Response shape:**
```json
{
  "total": 312,
  "products": [ <Product>, ... ]
}
```

---

### 3. Name Search

Add `q={url_encoded_query}` and fan out across `MAIN_CATEGORIES`:

```
GET /v2/retailers/1131/branches/{branch_id}/categories/{cat_id}/products
    ?appId=4&from=0&size=100&languageId=1&...&q=%D7%91%D7%99%D7%A6%D7%99%D7%9D
```

---

## Top-Level Categories (MAIN_CATEGORIES)

| Category ID | Name (Hebrew)                                       |
|-------------|-----------------------------------------------------|
| 120357      | cat_120357                                          |
| 95840       | cat_95840                                           |
| 97314       | cat_97314                                           |
| 96505       | cat_96505                                           |
| 93755       | cat_93755                                           |
| 94523       | cat_94523                                           |
| 96764       | cat_96764                                           |
| 94246       | cat_94246                                           |
| 96794       | cat_96794                                           |
| 94600       | cat_94600                                           |
| 95103       | שימורים, כלי בית                                   |
| 99065       | פארם                                               |
| 79821       | קצביה                                               |
| 79704       | פירות וירקות                                        |
| 79718       | חלב                                                 |
| 79687       | לחם + תחליפים                                      |
| 79619       | תבלינים, שימורים, ייסוד וקיטניות                   |
| 79731       | דגנים, שוקולדים וממתקים                             |
| 79603       | מעדנייה                                             |
| 79591       | קפואים א': צ'יפס, דגים, בצקים וגלידות              |
| 79667       | בירות, יין ומשקאות אנרגיה                           |
| 79835       | שתייה חמה, שוקולד, עוגות ועוגיות                   |
| 79653       | חטיפים ובייגלה                                      |
| 79740       | חומרי ניקיון א' וכביסה                              |
| 79571       | קוסמטיקה, היגיינה נשית וטיפוח הפרט                 |
| 122886      | cat_122886                                          |
| 79764       | כלי בית ופחמים                                      |

---

## Product Object (appId=4 schema)

Same schema as Keshet Teamim, Quik, and Victory — see `keshet_api.md` for the full annotated example.

Key points:
- `product["branch"]` — singular object (not a map by branch ID)
- Barcode extracted from image URL via regex `/(\d{7,14})-`
- Image URL has `{{size}}` and `{{extension||'jpg'}}` templates
- Categories in `product["family"]["categories"]`
- Brand in `product["brand"]["names"]["1"]`

---

## Pricing & Deals

Same deal structure as all other ZuZ appId=4 chains:

| Condition                          | Deal type        |
|------------------------------------|------------------|
| `salePrice < regularPrice`         | `price_reduction`|
| `specials[].firstLevel.type == 2`  | `multi_buy`      |
| `specials[].firstLevel.type == 3`  | `cart_total`     |

---

## SSL / TLS

```python
import certifi, ssl
ctx = ssl.create_default_context(cafile=certifi.where())
connector = aiohttp.TCPConnector(ssl=ctx)
```

---

## Instrumentation via Browser DevTools (F12)

1. Open `https://www.ybitan.co.il` in Chrome.
2. Open DevTools → Network tab → filter by `Fetch/XHR`.
3. Browse to any category in the store.
4. Look for requests to `/v2/retailers/1131/branches/{bid}/categories/{cat_id}/products`.
5. Inspect query parameters and response JSON for schema changes.

**curl probe:**
```bash
curl "https://www.ybitan.co.il/v2/retailers/1131/branches/1015/categories/79718/products?appId=4&from=0&size=5&languageId=1&categorySort=%7B%22sortType%22%3A1%7D" \
  -H "Accept: application/json"
```

**Check if global endpoint is capped:**
```bash
curl "https://www.ybitan.co.il/v2/retailers/1131/products?appId=2&from=0&size=1&languageId=1" \
  -H "Accept: application/json"
# Compare "total" vs. what per-branch/per-category returns
```
