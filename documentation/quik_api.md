# Quik API Documentation (קוויק)

> Platform: **ZuZ** (AngularJS) — Retailer ID `1541`
> Base URL: `https://www.quik.co.il`
> Research date: 2026-03

---

## Overview

Quik's online store runs on the **ZuZ** platform with the `appId=4` per-branch/per-category endpoint pattern — identical in structure to Keshet Teamim, Victory, and Yenot Bitan.

The global `/v2/retailers/1541/products` endpoint (appId=2) is **capped** and misses many products.  The per-branch/per-category approach avoids the cap entirely.

---

## Endpoints

### 1. Branch List

```
GET /v2/retailers/1541/branches?appId=2&languageId=1
```

**Response shape:**
```json
{
  "branches": [
    { "id": 3264, "name": "אור יהודה - Online", "city": "אור יהודה", "location": "" }
  ]
}
```

**Known online branch IDs (as of 2026-03):**

| ID   | Name                       | City             |
|------|----------------------------|------------------|
| 3264 | אור יהודה - Online         | אור יהודה       |
| 3085 | אור עקיבא - Online         | אור עקיבא       |
| 3187 | אילת - Online              | אילת             |
| 3086 | אשדוד - Online             | אשדוד           |
| 3087 | אשקלון - Online            | אשקלון          |
| 3100 | גבעתיים - Online           | גבעתיים         |
| 3478 | דליית אל כרמל - Online     | דליית אל כרמל   |
| 3211 | הרצליה - Online            | הרצליה          |
| 3096 | חיפה - Online              | חיפה             |
| 3101 | ירושלים - Online           | ירושלים         |
| 3091 | כפר סבא - Online           | כפר סבא         |
| 3106 | לוד - Online               | לוד              |
| 2993 | נתניה - Online             | נתניה           |
| 3089 | פתח תקווה - Online         | פתח תקווה       |
| 3104 | רחובות - Online            | רחובות          |
| 3095 | רמלה - Online              | רמלה            |
| 3102 | תל אביב - Online           | תל אביב         |

---

### 2. Per-Branch, Per-Category Product Catalogue

```
GET /v2/retailers/1541/branches/{branch_id}/categories/{cat_id}/products
    ?appId=4&from={offset}&size={page_size}&languageId=1
    &categorySort={"sortType":1}
    &filters={"mustNot":{"term":{"branch.isOutOfStock":true}}}
```

**Response shape:**
```json
{
  "total": 143,
  "products": [ <Product>, ... ]
}
```

**Pagination strategy:** same as Keshet — probe with `size=1`, then paginate in steps of `size` (100 recommended).

---

### 3. Name Search

Add `q={url_encoded_query}` to the products endpoint and fan out across all `MAIN_CATEGORIES`:

```
GET /v2/retailers/1541/branches/{branch_id}/categories/{cat_id}/products
    ?appId=4&from=0&size=100&languageId=1&...&q=%D7%91%D7%99%D7%A2%D7%99%D7%9D
```

---

## Top-Level Categories (MAIN_CATEGORIES)

Categories discovered from the site's `data.js` bundle (2026-03):

| Category ID | Notes                          |
|-------------|--------------------------------|
| 120357      | cat_120357                     |
| 95840       | cat_95840                      |
| 97314       | cat_97314                      |
| 96505       | cat_96505                      |
| 93755       | cat_93755                      |
| 94523       | cat_94523                      |
| 96764       | cat_96764                      |
| 94246       | cat_94246                      |
| 99065       | cat_99065                      |
| 96794       | cat_96794                      |
| 94600       | cat_94600                      |
| 79704       | פירות וירקות                   |
| 79718       | מוצרי חלב וביצים               |
| 79687       | לחמים / מאפיה                  |
| 79821       | עוף / בשר / דגים               |
| 79619       | שימורים / בישול                |
| 79731       | דגנים                          |
| 79603       | מעדניה                         |
| 79591       | קפואים                         |
| 79667       | שתייה                          |
| 79835       | בריאות ותזונה                  |
| 79653       | חטיפים                         |
| 79740       | ניקיון                         |
| 79571       | היגיינה / פארם                 |
| 79807       | טואלטיקה                       |
| 79764       | כלי בית                        |

---

## Product Object (appId=4 schema)

Same schema as Keshet Teamim:

```json
{
  "productId": 6080688,
  "id": 20164375,
  "localName": "חלב תנובה 3% שומן 1 ליטר",
  "names": { "1": { "short": "חלב תנובה 3%", "long": "חלב תנובה 3% שומן 1 ליטר" } },
  "image": {
    "url": "https://cdn.quik.co.il/upload/images/product-images/7290000066882-{{size}}.{{extension||'jpg'}}"
  },
  "isWeighable": false,
  "weight": 1000,
  "unitOfMeasure": { "names": { "1": "מ\"ל" } },
  "brand": { "names": { "1": "תנובה" } },
  "family": {
    "categories": [{ "id": 79718 }]
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

See `keshet_api.md` for full field description — the product schema is identical across all ZuZ appId=4 retailers.

---

## Barcode Extraction

Barcode is **not** a top-level field.  Extract from image URL:

```python
import re
_BARCODE_RE = re.compile(r"/(\d{7,14})-")
barcode = _BARCODE_RE.search(image_url)
```

---

## Image URL Expansion

```python
import re
url = raw.replace("{{size}}", "large")
url = re.sub(r"\{\{extension(?:\|\|'[^']*')?\}\}", "jpg", url)
```

---

## Pricing & Deals

Same as Keshet Teamim — `branch.regularPrice`, `branch.salePrice`, `branch.specials`.  See `keshet_api.md` for full deal parsing logic.

---

## SSL / TLS

```python
import certifi, ssl
ctx = ssl.create_default_context(cafile=certifi.where())
connector = aiohttp.TCPConnector(ssl=ctx)
```

---

## Instrumentation via Browser DevTools (F12)

1. Open `https://www.quik.co.il` in Chrome.
2. Open DevTools → Network tab → filter by `Fetch/XHR`.
3. Browse to a category.
4. Look for requests to `/v2/retailers/1541/branches/{bid}/categories/{cat_id}/products`.
5. Check query parameters for any changes (appId, filters, sort).

**curl probe:**
```bash
curl "https://www.quik.co.il/v2/retailers/1541/branches/3102/categories/79718/products?appId=4&from=0&size=5&languageId=1&categorySort=%7B%22sortType%22%3A1%7D" \
  -H "Accept: application/json"
```
