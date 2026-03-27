# Victory API Documentation (ויקטורי)

> Platform: **ZuZ** (AngularJS) — Retailer ID `1470`
> Base URL: `https://www.victoryonline.co.il`
> Research date: 2026-03

---

## Overview

Victory's online store runs on the **ZuZ** platform with the `appId=4` per-branch/per-category endpoint pattern — identical in structure to Keshet Teamim, Quik, and Yenot Bitan.

The global `/v2/retailers/1470/products` endpoint (appId=2) is **capped** and misses many products.  Use the per-branch/per-category approach.

Victory has the largest branch list among the ZuZ chains (~55 branches including both online and physical stores).

---

## Endpoints

### 1. Branch List

```
GET /v2/retailers/1470/branches?appId=2&languageId=1
```

**Response shape:**
```json
{
  "branches": [
    { "id": 2930, "name": "אינטרנט", "city": "לוד", "location": "" }
  ]
}
```

**Selected online/active branch IDs (as of 2026-03):**

| ID   | Name                                        | City           |
|------|---------------------------------------------|----------------|
| 2930 | אינטרנט                                    | לוד            |
| 2439 | ויקטורי אופקים - Victory Online            | אופקים        |
| 2527 | ויקטורי אשדוד - Victory Online             | אשדוד         |
| 2530 | ויקטורי אשקלון - Victory Online            | אשקלון        |
| 2435 | ויקטורי בית שמש - Victory Online           | בית שמש       |
| 2331 | ויקטורי גן יבנה - Victory Online           | גן יבנה       |
| 2539 | ויקטורי טירת הכרמל - Victory Online        | טירת הכרמל    |
| 2448 | ויקטורי ירושלים קניון מלחה - Victory Online | ירושלים      |
| 2427 | ויקטורי לוד נתב"ג - Victory Online         | לוד            |
| 2451 | ויקטורי נתיבות - Victory Online            | נתיבות        |
| 2449 | ויקטורי נתניה - Victory Online             | נתניה         |
| 3433 | ויקטורי עמק חפר - Victory Online           | עמק חפר       |
| 2550 | ויקטורי עפולה                              | עפולה         |
| 2552 | ויקטורי צור יצחק                           | צור יצחק      |
| 2447 | ויקטורי קניון אילון - Victory Online       | רמת גן        |
| 2438 | ויקטורי קריית מוצקין - Victory Online      | קרית מוצקין   |
| 2444 | ויקטורי ראש העין פארק אפק - Victory Online | ראש העין      |
| 2442 | ויקטורי ראשון לציון מזרח - Victory Online  | ראשון לציון   |
| 2446 | ויקטורי רחובות - Victory Online            | רחובות        |
| 2547 | ויקטורי רמת גן - Victory Online            | רמת גן        |
| 2440 | ויקטורי רעננה פארק - Victory Online        | רעננה         |
| 2568 | ויקטורי תל אביב - Victory Online           | תל אביב       |

*(Plus ~35 additional physical branches — full list in `scrapers/victory/victory.py`)*

---

### 2. Per-Branch, Per-Category Product Catalogue

```
GET /v2/retailers/1470/branches/{branch_id}/categories/{cat_id}/products
    ?appId=4&from={offset}&size={page_size}&languageId=1
    &categorySort={"sortType":1}
    &filters={"mustNot":{"term":{"branch.isOutOfStock":true}}}
```

**Response shape:**
```json
{
  "total": 215,
  "products": [ <Product>, ... ]
}
```

---

### 3. Name Search

Add `q={url_encoded_query}` and fan out across all `MAIN_CATEGORIES`:

```
GET /v2/retailers/1470/branches/{branch_id}/categories/{cat_id}/products
    ?appId=4&from=0&size=100&languageId=1&...&q=%D7%91%D7%99%D7%A6%D7%99%D7%9D
```

---

## Top-Level Categories (MAIN_CATEGORIES)

| Category ID | Name (Hebrew)                               |
|-------------|---------------------------------------------|
| 95840       | cat_95840                                   |
| 120357      | cat_120357                                  |
| 97314       | cat_97314                                   |
| 94600       | cat_94600                                   |
| 96505       | טקסטיל, כלי בית, מוצרי חשמל                |
| 93755       | cat_93755                                   |
| 94523       | cat_94523                                   |
| 96764       | cat_96764                                   |
| 94246       | cat_94246                                   |
| 96794       | cat_96794                                   |
| 99065       | היגיינת הפה והגוף, דאורדורנט, שמפו, סבון גוף |
| 79704       | פירות וירקות                                |
| 79718       | חלבי                                        |
| 79687       | מאפיה                                       |
| 79821       | קצבייה טרי                                  |
| 79731       | דגני בוקר                                   |
| 79619       | שימורים                                     |
| 79603       | מעדניה נקניקים                              |
| 79591       | קפואים                                      |
| 79667       | משקאות קלים                                 |
| 79835       | ללא גלוטן                                   |
| 79653       | חטיפים מלוחים                               |
| 79740       | אביזרי ניקיון                               |
| 79571       | היגיינה                                     |
| 79764       | חד פעמי ושקיות                              |

---

## Product Object (appId=4 schema)

Same schema as Keshet Teamim and Quik — see `keshet_api.md` for the full annotated example.

Key points:
- `product["branch"]` — singular object (not a map by branch ID)
- Barcode extracted from image URL via regex `/(\d{7,14})-`
- Image URL has `{{size}}` and `{{extension||'jpg'}}` templates
- Categories in `product["family"]["categories"]`
- Brand in `product["brand"]["names"]["1"]`

---

## Pricing & Deals

Same deal structure as other ZuZ appId=4 chains:

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

1. Open `https://www.victoryonline.co.il` in Chrome.
2. Open DevTools → Network tab → filter by `Fetch/XHR`.
3. Browse to any category.
4. Look for requests to `/v2/retailers/1470/branches/{bid}/categories/{cat_id}/products`.
5. Check response JSON for schema changes.

**curl probe:**
```bash
curl "https://www.victoryonline.co.il/v2/retailers/1470/branches/2449/categories/79718/products?appId=4&from=0&size=5&languageId=1&categorySort=%7B%22sortType%22%3A1%7D" \
  -H "Accept: application/json"
```

**Check if global endpoint is capped:**
```bash
curl "https://www.victoryonline.co.il/v2/retailers/1470/products?appId=2&from=0&size=1&languageId=1" \
  -H "Accept: application/json"
# Compare "total" with what you get from per-branch/per-category scraping
```
