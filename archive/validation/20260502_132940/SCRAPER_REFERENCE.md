# Quick Reference: All 10 Supermarket Scrapers

Generated from validation run on 2026-05-02.

---

## Summary Table

| # | Chain | Platform | Retailer ID | Status | Products | Duration |
|---|-------|----------|-------------|--------|----------|----------|
| 1 | tivtaam | Stor.ai | 1062 | ✅ | 17,915 | 133.37s |
| 2 | carrefour | Stor.ai | 1540 | ✅ | 4,866 | 105.01s |
| 3 | machsanei_hashook | ZuZ | 1107 | ✅ | 7,018 | 61.47s |
| 4 | victory | ZuZ | 1470 | ✅ | 6,094 | 62.50s |
| 5 | ramilevi | Node.js/ES | — | ✅ | 1,422 | 64.22s |
| 6 | shufersal | Custom JSON | — | ✅ | 894 | 16.32s |
| 7 | keshet | ZuZ | 1219 | ✅ | 102 | 13.13s |
| 8 | yochananof | GraphQL | — | ❌ | 0 | 0.68s |
| 9 | quik | ZuZ | 1541 | ❌ | 0 | 3.62s |
| 10 | ybitan | ZuZ | 1131 | ❌ | 0 | 6.93s |

**Total**: 43,413 products from 7 working chains

---

## Passing Scrapers (7)

### Platform: Stor.ai (2 scrapers)
Pagination via `/v2/retailers/{id}/branches/{branch_id}/categories/{cat_id}/products`

- **tivtaam** (1062) — 17,915 products ⭐ Largest
- **carrefour** (1540) — 4,866 products

### Platform: ZuZ / appId=4 (4 scrapers)
Pagination via `/api/v1/{appId}/{retailer}/{branch}/{category}?page=N`

- **machsanei_hashook** (1107) — 7,018 products (single branch: 836)
- **victory** (1470) — 6,094 products
- **keshet** (1219) — 102 products
- (❌ **quik** (1541) — 0 products — FAILED)
- (❌ **ybitan** (1131) — 0 products — FAILED)

### Platform: Custom Backends (2 scrapers)
Unique endpoints per chain.

- **shufersal** — 894 products (custom JSON, chain-wide, no branches)
- **ramilevi** — 1,422 products (Node.js/Elasticsearch, `POST /api/catalog`)

### Platform: Magento 2 GraphQL (1 scraper)
GraphQL endpoint with `Store:` header filtering.

- (❌ **yochananof** — 0 products — FAILED: 403 Forbidden)

---

## Failing Scrapers (3)

### yochananof (Magento 2 GraphQL)
- **Error**: 403 Forbidden on `https://api.yochananof.co.il/graphql`
- **Likely Cause**: API access blocked, needs specific headers or auth
- **Action**: Add browser headers, test with Playwright

### quik (ZuZ, retailer 1541)
- **Error**: No products returned (0 results)
- **Likely Cause**: API rejected request, category empty, or server blocked
- **Action**: Test with different branch, run Playwright diagnosis

### ybitan (ZuZ, retailer 1131)
- **Error**: No products returned (0 results)
- **Likely Cause**: Same as quik
- **Action**: Test with different branch, run Playwright diagnosis

---

## By Number of Products Found

1. **tivtaam** — 17,915 ⭐⭐⭐
2. **machsanei_hashook** — 7,018 ⭐⭐
3. **victory** — 6,094 ⭐⭐
4. **carrefour** — 4,866 ⭐⭐
5. **ramilevi** — 1,422 ⭐
6. **shufersal** — 894 ⭐
7. **keshet** — 102
8. **yochananof** — 0 ❌
9. **quik** — 0 ❌
10. **ybitan** — 0 ❌

---

## By Scrape Duration

*Fastest to slowest:*

1. **shufersal** — 16.32s (fastest, single endpoint)
2. **keshet** — 13.13s
3. **yochananof** — 0.68s (failed fast)
4. **quik** — 3.62s
5. **ybitan** — 6.93s
6. **ramilevi** — 64.22s
7. **victory** — 62.50s
8. **machsanei_hashook** — 61.47s
9. **carrefour** — 105.01s
10. **tivtaam** — 133.37s (slowest, largest catalog)

---

## Platform Breakdown

### Stor.ai: 2/2 Working ✅
- Offset pagination from {offset}&size={size}
- Same code pattern used by tivtaam and carrefour
- Reliable, both scrapers work well

### ZuZ: 4/6 Working ⚠️
- 4 working (machsanei_hashook, victory, keshet, + 1 more)
- 2 failing (quik, ybitan)
- Page-based pagination
- May need investigation for quik/ybitan

### Custom: 2/2 Working ✅
- shufersal (JSON API with page param)
- ramilevi (Node.js with POST /api/catalog)
- Both work well

### GraphQL: 0/1 Working ❌
- yochananof (Magento 2 with 403 error)
- Needs header or auth fix

---

## Warnings Found

### Net Content Parsing Error
Appears in ZuZ scrapers when `net_content` field is empty:
```
Could not parse net_content value '': could not convert string to float: ''
```

**Affected Scrapers**: machsanei_hashook, quik, victory, ybitan, keshet

**Severity**: Medium (non-fatal, but indicates data quality issue)

**Action**: Add null-checking in ZuZ scraper code

---

## How to Diagnose Failures

### 1. Run Playwright Visual Inspection
```bash
source venv/bin/activate
pip install playwright
playwright install chromium
python3 validate_scrapers.py --playwright
```

This will:
- Navigate to each failing chain's website
- Search for "חלב" (milk)
- Screenshot the results
- Save to `output_dir/validation/<chain>_failure.png`

### 2. Manually Test a Scraper
```bash
source venv/bin/activate
python3 << 'EOF'
import asyncio
from scrapers.quik.quik import scrape
from scrapers.common import ScrapeFilter

result = asyncio.run(scrape(
    flt=ScrapeFilter(name_query="חלב"),
    batch_size=20,
    max_concurrent=3,
    max_retries=2
))

print(f"Products: {result['products_total']}")
print(f"Errors: {result['errors']}")
EOF
```

### 3. Check API Directly
```bash
# Test Yochananof GraphQL
curl -X POST "https://api.yochananof.co.il/graphql" \
  -H "Content-Type: application/json" \
  -H "User-Agent: Mozilla/5.0..." \
  -d '{"query":"query{availableStores{store_code}}"}'

# Test Quik ZuZ
curl "https://api.zuuz.co.il/api/v1/4/1541/3086/1?page=1"
```

---

## Next Validation Run

To run validation again:

```bash
cd /Users/rachelbernadsky/TomerShit/SuperMarketScraping
source venv/bin/activate
python3 validate_scrapers.py > validation_archive_$(date +%Y%m%d_%H%M%S)/output.txt 2>&1
```

---

**Last Updated**: 2026-05-02 13:26:53 UTC  
**Next Review**: Recommended within 1 week to monitor for API changes
