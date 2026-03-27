# Yochananof API Documentation

> Platform: **Magento 2 + GraphQL**
> Endpoint: `https://api.yochananof.co.il/graphql`
> Research date: 2026-03

---

## Overview

Yochananof's online store is built on Magento 2 and exposes a standard Magento GraphQL API.  No authentication is required for product browsing.  Branch-level price and stock filtering is achieved via the `Store` HTTP header.

---

## Authentication

None required.  All GraphQL queries are unauthenticated.

---

## Endpoints

All requests go to a single GraphQL endpoint:

```
https://api.yochananof.co.il/graphql
```

Simple, parameter-free queries can be sent via `GET` with a URL-encoded `query` param.  Queries with variables must use `POST` with a JSON body.

**Required headers for POST:**
```
Content-Type: application/json
Accept: application/json
Store: <store_code>          # branch-specific pricing
```

---

## Queries

### 1. Available Stores (branches)

```graphql
query AvailableStores {
  availableStores {
    store_code
    store_name
    is_default_store
    locale
    base_url
  }
}
```

Returns all ~23 online store branches.  Each branch has a `store_code` (e.g. `"s82"`) used as the `Store` HTTP header value.

**Example store object:**
```json
{
  "store_code": "s82",
  "store_name": "תל אביב - שינקין",
  "is_default_store": false,
  "locale": "he_IL",
  "base_url": "https://yochananof.co.il/s82/"
}
```

---

### 2. Category Tree (menu)

```graphql
query Categories {
  amMegaMenuAll {
    items {
      id name is_category level status
      children { id name is_category level status
        children { id name is_category level status
          children { id name is_category level status }
        }
      }
    }
  }
}
```

Returns the full Amasty Mega Menu tree.

**ID conventions:**
- `"category-node-{N}"` — real Magento category (`is_category = true`)
- `"custom-node-{N}"` — promotional link, not a real category (`is_category = false`)

**Extracting numeric IDs:**
Strip the `"category-node-"` prefix.  Only collect **leaf** categories (no real-category children) to avoid fetching the same products through parent categories.

**Example item:**
```json
{
  "id": "category-node-423",
  "name": "מוצרי חלב",
  "is_category": true,
  "level": 3,
  "status": 1,
  "children": []
}
```

---

### 3. Products (by category or keyword search)

```graphql
query Products(
  $search: String
  $filter: ProductAttributeFilterInput
  $pageSize: Int!
  $currentPage: Int!
) {
  products(search: $search, filter: $filter, pageSize: $pageSize, currentPage: $currentPage) {
    total_count
    page_info { page_size current_page total_pages }
    items {
      id sku name short_name brand url_key
      stock_status by_kilo item_unit
      price_range {
        minimum_price {
          regular_price { value currency }
          final_price   { value currency }
          discount      { amount_off percent_off }
        }
      }
      small_image { url label }
      categories { id name }
    }
  }
}
```

**Variables for category filter:**
```json
{
  "filter": { "category_id": { "eq": "423" } },
  "pageSize": 100,
  "currentPage": 1
}
```

**Variables for keyword search:**
```json
{
  "search": "חלב",
  "pageSize": 100,
  "currentPage": 1
}
```

**Combined (search + category):**
```json
{
  "search": "חלב",
  "filter": { "category_id": { "eq": "423" } },
  "pageSize": 100,
  "currentPage": 1
}
```

**Notes:**
- Maximum `pageSize` observed: 100 (server enforces this cap).
- Pages are 1-indexed (`currentPage: 1` is the first page).
- `total_pages` is returned in `page_info` — fetch all remaining pages concurrently.
- Prices and `stock_status` are **branch-specific** when the `Store` header is set.

---

## Product Object (GraphQL)

| Field                                            | Type         | Notes                                          |
|--------------------------------------------------|--------------|------------------------------------------------|
| `id`                                             | int          | Magento product entity ID                      |
| `sku`                                            | string       | **EAN-13 barcode** (verified for Yochananof)   |
| `name`                                           | string       | Hebrew product name                            |
| `short_name`                                     | string\|null | Short Hebrew name                              |
| `brand`                                          | string\|null | Brand name                                     |
| `stock_status`                                   | enum         | `"IN_STOCK"` or `"OUT_OF_STOCK"`               |
| `by_kilo`                                        | int          | `1` if sold by weight                          |
| `item_unit`                                      | string\|null | Unit label e.g. `"יח'"`, `"ק\"ג"`             |
| `price_range.minimum_price.regular_price.value`  | float        | Shelf price (before discount)                  |
| `price_range.minimum_price.final_price.value`    | float        | Effective price (after active promotions)      |
| `price_range.minimum_price.discount.percent_off` | float        | Discount percentage (0 when no promotion)      |
| `small_image.url`                                | string\|null | Product image URL                              |
| `categories[].id`                                | int          | Category IDs the product belongs to            |
| `categories[].name`                              | string       | Category name                                  |

---

## Barcode

For Yochananof, the `sku` field **is** the EAN-13 barcode.  This has been verified empirically — the numeric SKU matches the physical barcode on the product packaging.

---

## Branch Filtering

Pass the `Store` HTTP header to scope prices and stock to a specific branch:

```
Store: s82
```

Without this header the server returns the default store's data.  Different store codes return different `total_count` values for the same category, confirming branch-level inventory differences.

---

## Pagination Strategy

1. Fetch `currentPage: 1` to read `total_pages` from `page_info`.
2. Fetch pages `2, 3, …, total_pages` concurrently (respect `max_concurrent` semaphore).
3. Pages are 1-indexed — there is no page 0.

---

## De-duplication

Products appear in multiple categories.  De-duplicate within a store by `sku` (barcode) before returning results.

---

## Rate Limiting / Politeness

- No documented rate limits.
- Observed: handles ~15–20 concurrent POST requests reliably.
- Recommended: `max_concurrent ≤ 15`.
- Retry with exponential backoff on network errors or 5xx responses.
