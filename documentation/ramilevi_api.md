# Rami Levy API Documentation

**Base URL:** `https://www.rami-levy.co.il`
**Platform:** Custom Node.js / Elasticsearch backend (Nuxt.js SPA)
**Authentication:** None required for public catalog endpoints
**Last refreshed:** 2026-05-13

---

## 1. Stores List

```
GET /api/stores
```

Returns all physical and online store locations.

### Response

```json
{
  "status": 200,
  "stores": {
    "total": 97,
    "data": [
      {
        "id": "מודיעין",
        "branch_id": "מודיעין",
        "name": "מודיעין",
        "street": "שדרות הספורט",
        "city": "מודיעין-מכבים-רעות",
        "tel": "08-9732970",
        "internet_store_id": 1332,
        "delivery": 0,
        "show_site": 1
      }
    ]
  }
}
```

**Key fields:**
- `internet_store_id` — numeric store ID used in catalog/search calls. `null` for physical-only stores.
- `delivery` — 1 if the store offers home delivery.

**Online-capable stores** (those with a non-null `internet_store_id`) can be browsed via the catalog API.

### Representative Store Selection

Use stores `1314` (`אילת`) and `1389` (`איילון בני ברק`) together for reference coverage audits. The previous audit target store `125` (`ראשון לציון`) is currently sparse and returned only `692` catalog products, which made Ramilevi coverage look falsely broken. Store `1314` is the highest-coverage single store found, but it hits an Elasticsearch-style `10,000` result-window cap, so pairing it with `1389` improves chain-level product coverage without the heavy throttling seen in larger multi-store runs.

Live catalog probe from 2026-05-13:

| Store ID | Name | Catalog Total |
|---:|---|---:|
| `1314` | אילת | 10,000 |
| `1389` | איילון בני ברק | 8,504 |
| `1329` | רחובות | 8,078 |
| `1306` | גבעת שמואל | 7,951 |
| `1221` | ירושלים - הר חומה | 7,794 |
| `125` | ראשון לציון | 692 |
| `1357` | כרמיאל | 403 |
| `130` | גוש עציון | 0 |
| `8` | שער בנימין | 0 |

Refresh rule: probe all `ONLINE_STORES` with `POST /api/catalog?` using `size=1`, then choose stores with broad inventory. Do not assume any online-capable store is suitable for product coverage audits.

---

## 2. Category Menu

```
GET /api/menu
```

Returns Elasticsearch aggregations describing the product catalog hierarchy.

### Response

```json
{
  "aggregations": {
    "department": {
      "buckets": [
        {
          "key": 60,
          "doc_count": 1084,
          "group": {
            "buckets": [
              { "key": 258, "doc_count": 202 },
              { "key": 293, "doc_count": 155 }
            ]
          }
        }
      ]
    }
  }
}
```

**Key fields:**
- `department.buckets[].key` — department ID
- `department.buckets[].group.buckets[].key` — group ID within that department

**Known department IDs:**

| ID   | Approx. products |
|------|-----------------|
| 49   | 151 (פירות וירקות) |
| 50   | 625 (מוצרי חלב) |
| 51   | 234 (בשר ועוף) |
| 52   | 256 (דגים) |
| 53   | 499 (מוצרי מאפה) |
| 54   | 1028 (שתיה) |
| 55   | 442 (חטיפים וממתקים) |
| 56   | 769 (שימורים ומזון יבש) |
| 57   | 771 (ניקיון ובית) |
| 58   | 434 (טיפוח ובריאות) |
| 59   | 959 (תינוקות) |
| 60   | 1084 (קפה, תה ועוגיות) |
| 61   | 71 (אחר) |

---

## 3. Full Catalog Browse (POST)

The primary endpoint for fetching all products in a store's catalog.

```
POST /api/catalog?
Content-Type: application/json
```

### Request Body

```json
{
  "store": 1332,
  "q": "",
  "from": 0,
  "size": 100
}
```

| Field  | Type   | Description |
|--------|--------|-------------|
| `store`| int    | `internet_store_id` from the stores endpoint |
| `q`    | string | Full-text search query. Empty string `""` returns all products |
| `from` | int    | Zero-based offset for pagination |
| `size` | int    | Number of products per page (recommended: 100–200) |

### Response

```json
{
  "status": 200,
  "total": 6634,
  "q": null,
  "data": [
    {
      "id": 2,
      "name": "עגבניה",
      "barcode": 100,
      "price": { "price": 4.9 },
      "prop": {
        "unit": 0,
        "sw_shakil": 1,
        "by_kilo": 1,
        "by_kilo_content": 0,
        "status": 2
      },
      "department": { "id": 49, "name": "פירות וירקות", "slug": "פירות-וירקות" },
      "department_id": 49,
      "group": { "id": 197, "name": "ירקות", "slug": "ירקות" },
      "group_id": 197,
      "subGroup": { "id": 320, "name": "ירקות טריים", "slug": "ירקות-טריים" },
      "sub_group_id": 320,
      "images": {
        "small": "/product/100/2/medium.jpg",
        "original": "/product/100/2/large.jpg",
        "trim": "/product/100/trim.jpg",
        "transparent": "/product/100/transparent.png"
      },
      "gs": {
        "BrandName": "",
        "Net_Content": { "UOM": "גרם", "text": "200 גרם", "value": "200" }
      },
      "sale": [],
      "available_in": [125, 179, 279, 290, 1332],
      "multiplication": 0.5
    }
  ]
}
```

**Key product fields:**

| Field | Description |
|-------|-------------|
| `id` | Rami Levy internal product ID (use as `product_id`) |
| `name` | Hebrew product name |
| `barcode` | EAN barcode (integer) |
| `price.price` | Shelf price in ₪. For `by_kilo=1` items this is **price per kg** |
| `prop.by_kilo` | 1 = price is per kg (weighable product) |
| `prop.sw_shakil` | 1 = sold by weight (scale product) |
| `prop.status` | 2 = active |
| `gs.Net_Content.value` | Numeric quantity (e.g. `"200"`) |
| `gs.Net_Content.UOM` | Unit of measure in Hebrew (e.g. `"גרם"`, `"ליטר"`) |
| `gs.BrandName` | Brand name |
| `sale` | List of active promotions (see §5) |
| `available_in` | List of `internet_store_id` values where this product is available |
| `department_id` | Department numeric ID |
| `group_id` | Group numeric ID |
| `sub_group_id` | Sub-group numeric ID |

### Pagination

Use the `from` field (zero-based offset). The `page` parameter is **not functional** — always use `from`.

Observed note from 2026-05-13: store `1314` reports exactly `10,000` products. This is a real backend cap, not a code constant. `/api/menu?store=1314` returns `hits.total.value=10000` with `hits.total.relation="gte"`, and catalog requests where `from + size` crosses `10,000` return `['Internal Server Error']`. For example, `from=9990&size=10` succeeds, while `from=9999&size=10` and `from=10000&size=10` fail.

The scraper should keep page sizes inside the reported total window and should treat non-object JSON responses as API errors. For reference matching, combine stores `1314` and `1389` with low concurrency rather than trying to scrape many stores in one audit run; a five-store probe improved coverage but triggered many transient `403` responses.

```
Page 1: from=0,   size=100  → items 0–99
Page 2: from=100, size=100  → items 100–199
...
```

---

## 4. Product Search (GET)

Full-text search for products in a specific store.

```
GET /api/search?storeid={id}&q={query}&from={offset}&size={n}
```

### Parameters

| Param    | Type   | Description |
|----------|--------|-------------|
| `storeid`| int    | `internet_store_id` |
| `q`      | string | Search query (URL-encoded Hebrew text) |
| `from`   | int    | Zero-based offset |
| `size`   | int    | Results per page |

### Response

```json
{
  "status": 200,
  "total": 3011,
  "q": "חלב",
  "data": [ ... ]
}
```

Response structure is the same as the POST catalog endpoint, but each item has
an additional `hl` field with highlighted match fragments.

**Notes:**
- An empty or missing `q` causes a `400 Internal Server Error` (unlike the POST catalog which accepts empty string).
- The `page` parameter does **not** work — use `from` for all pagination.

---

## 5. Deals / Promotions

Each product has a `sale` array containing active promotions.

```json
"sale": [
  {
    "id": 781384,
    "type": 1,
    "name": "פטה עיזים מעודנת ב-22.90 ש\"ח",
    "label": "פטה עיזים מעודנת ב-22.90 ש\"ח\n1 ב 22.9 ₪",
    "scm": 22.9,
    "from": "2026-03-01 00:00:00",
    "to": "2026-05-02 23:59:59",
    "active": 1,
    "is_club": 0,
    "is_personal": 0,
    "max_in_doc": 0
  }
]
```

| Field | Description |
|-------|-------------|
| `type` | Always `1` (simple price reduction) |
| `scm` | Sale price in ₪ |
| `name` | Hebrew promotion description |
| `from` / `to` | Promotion validity window |
| `is_club` | 1 if only available to club members |
| `active` | 1 if currently active |

**Deal price:** `sale[0].scm` is the promotional price. The regular price is `price.price`.

---

## 6. Product Images

Images are served from the main site with paths like:
```
/product/{barcode}/{id}/medium.jpg
/product/{barcode}/{id}/large.jpg
/product/{barcode}/trim.jpg
```

Full URLs: `https://www.rami-levy.co.il{path}`

---

## 7. Scraper Strategy

The scraper uses the **POST catalog** endpoint (`/api/catalog?`) to fetch all products for each store:

1. **Probe:** POST with `size=1` to get `total`.
2. **Paginate:** Build offsets `range(0, total, batch_size)`.
3. **Concurrent fetch:** Fetch all pages concurrently using `run_concurrently()`.
4. **Map:** Convert each raw item via `_to_unified()` → `UnifiedProduct`.
5. **Deduplicate:** By `product_id` within each store.

For **name search** (`ScrapeFilter.name_query`), the GET `/api/search` endpoint is used instead, with the same offset-based pagination.

**Note on `available_in`:** Each product includes a list of stores where it's available. The catalog API already filters by store, but this field can be used to verify availability.
