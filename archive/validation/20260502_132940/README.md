# Validation Archive: 2026-05-02

This archive contains the results and documentation from the first comprehensive validation run of all 10 supermarket scrapers.

## 📊 Quick Stats

- **Date**: 2026-05-02
- **Time**: 13:26:53 UTC
- **Scrapers Tested**: 10
- **Passed**: 7 ✅
- **Failed**: 3 ❌
- **Total Products**: 43,413
- **Total Duration**: ~468 seconds (7.8 minutes)

---

## 📁 Files in This Archive

### Documentation

1. **README.md** (this file)
   - Overview of the validation run and archive contents

2. **VALIDATION_REPORT.md** ⭐ **START HERE**
   - Detailed validation results with per-scraper analysis
   - Root cause analysis for 3 failing scrapers
   - Recommendations for fixes
   - High-priority action items

3. **SCRAPER_REFERENCE.md**
   - Quick reference table of all 10 scrapers
   - Grouped by platform type and status
   - Sorted by product count and duration
   - Links to how to diagnose failures

### Code & Scripts

4. **validate_scrapers.py**
   - Live validation script (executable)
   - Tests all 10 scrapers with milk filter on 1 branch
   - Optional Playwright visual diagnosis
   - See VALIDATION_REPORT.md for how to use

5. **ARCHITECTURE.md**
   - Complete architecture documentation
   - All 10 scrapers with platforms and patterns
   - Web app stack description
   - Future scraper ideas
   - Full CLI reference

---

## 🎯 Key Findings

### ✅ Healthy Scrapers (7)

**Passing with good product counts:**
- **tivtaam** (Stor.ai) — 17,915 products
- **carrefour** (Stor.ai) — 4,866 products  
- **machsanei_hashook** (ZuZ) — 7,018 products
- **victory** (ZuZ) — 6,094 products
- **ramilevi** (Node.js) — 1,422 products
- **shufersal** (Custom JSON) — 894 products
- **keshet** (ZuZ) — 102 products

### ⚠️ Failing Scrapers (3)

**Require investigation/fixes:**
1. **yochananof** (GraphQL) — 403 Forbidden error
2. **quik** (ZuZ) — Returns 0 products
3. **ybitan** (ZuZ) — Returns 0 products

See **VALIDATION_REPORT.md** for detailed diagnosis and remediation steps.

---

## 🚀 How to Use This Archive

### 1. Read the Report
Start with **VALIDATION_REPORT.md** to understand:
- What passed and what failed
- Root causes of failures
- Recommended next steps

### 2. Check the Reference
Use **SCRAPER_REFERENCE.md** to quickly:
- See all scrapers grouped by status
- Find scrapers by product count or duration
- Look up platform details

### 3. Run Validation Again
To re-run the validation:
```bash
cd /Users/rachelbernadsky/TomerShit/SuperMarketScraping
source venv/bin/activate
python3 validate_scrapers.py

# With Playwright diagnosis:
pip install playwright
playwright install chromium
python3 validate_scrapers.py --playwright
```

### 4. Investigate Failures
For each failing scraper:
```bash
# Get visual diagnosis via Playwright
python3 validate_scrapers.py --playwright

# Manual testing (example for quik):
python3 << 'EOF'
import asyncio
from scrapers.quik.quik import scrape
from scrapers.common import ScrapeFilter

result = asyncio.run(scrape(
    flt=ScrapeFilter(name_query="חלב"),
    batch_size=20,
    max_concurrent=3
))
print(f"Products: {result['products_total']}")
print(f"Errors: {result['errors']}")
EOF
```

---

## 📈 Validation Metrics

### By Product Count
```
Largest:  tivtaam (17,915)
          machsanei_hashook (7,018)
          victory (6,094)
          carrefour (4,866)

Smallest: keshet (102)
Failing:  yochananof, quik, ybitan (0)
```

### By Execution Time
```
Fastest:  shufersal (16.32s)
          keshet (13.13s)

Slowest:  tivtaam (133.37s)
          carrefour (105.01s)
```

### By Platform
```
Stor.ai:      2/2 working (100%)
ZuZ:          4/6 working (67%)
Custom:       2/2 working (100%)
GraphQL:      0/1 working (0%)
```

---

## 🔧 Recommended Actions

### High Priority (This Week)
1. **Yochananof 403 Forbidden**
   - Add browser headers (User-Agent, Referer)
   - Test if authentication needed
   - Use Playwright to reverse-engineer requirements

2. **Quik & Ybitan (0 Products)**
   - Check if ZuZ platform changed API format
   - Try with different branches
   - Run Playwright visual inspection

### Medium Priority (Next Week)
3. **Net Content Parsing**
   - Fix ZuZ scrapers to handle empty net_content fields
   - Add defensive null-checking
   - Log as warnings, not errors

4. **Set Up Monitoring**
   - Schedule weekly validation runs
   - Alert on failures
   - Track product count trends

### Low Priority (Nice to Have)
5. **Performance Optimization**
   - Tivtaam takes 133s — consider category filtering
   - Carrefour takes 105s — similar optimization
   - Investigate if batch size can be increased

---

## 📊 Data Summary

### Products by Chain
```
tivtaam          ████████████████ 17,915 (41.2%)
machsanei_hashook ███████ 7,018 (16.2%)
victory          ██████ 6,094 (14.0%)
carrefour        ████ 4,866 (11.2%)
ramilevi         █ 1,422 (3.3%)
shufersal        █ 894 (2.1%)
keshet           ▌ 102 (0.2%)
yochananof       0 (0%)
quik             0 (0%)
ybitan           0 (0%)
─────────────────────────────────
Total            43,413 products
```

### Time Breakdown
```
tivtaam          ████████████████ 133.37s (28.5%)
carrefour        ████████████ 105.01s (22.4%)
machsanei        ███████ 61.47s (13.1%)
victory          ███████ 62.50s (13.4%)
ramilevi         ███████ 64.22s (13.7%)
shufersal        ██ 16.32s (3.5%)
keshet           █ 13.13s (2.8%)
yochananof       ▌ 0.68s (0.1%)
quik             ▌ 3.62s (0.8%)
ybitan           ▌ 6.93s (1.5%)
─────────────────────────────────
Total            468 seconds (7.8 min)
```

---

## 🔍 Platform Patterns Used

### Stor.ai (`/v2/retailers/{id}/branches/...`)
- Used by: tivtaam, carrefour
- Pattern: Category → paginated products
- Status: ✅ Stable

### ZuZ (`/api/v1/{appId}/.../categories`)
- Used by: machsanei_hashook, keshet, victory, quik, ybitan
- Pattern: Page-based pagination
- Status: ⚠️ Mixed (4 working, 2 failing)

### Custom Backends
- shufersal: Custom JSON API with page parameter
- ramilevi: POST /api/catalog with offset pagination
- Status: ✅ Stable

### Magento 2 GraphQL
- Used by: yochananof
- Pattern: GraphQL with Store header filtering
- Status: ❌ Broken (403 Forbidden)

---

## 📋 Version Info

- **Validation Script**: `validate_scrapers.py` v1.0
- **Python**: 3.14+
- **aiohttp**: 3.13.5+
- **Dependencies**: aiohttp, asyncio (stdlib), pathlib (stdlib)
- **Optional**: playwright (for visual diagnostics)

---

## 🎓 Learning Resources

For understanding each scraper:
1. **ARCHITECTURE.md** — High-level patterns
2. **Scraper code** in `scrapers/*/` — Implementation details
3. **API documentation** in `documentation/*_api.md` — API specifics
4. **AGENT_TARGET_DOCUMENTATION.md** — How to research/debug APIs

---

## 📝 Notes for Future Runs

When re-running validation:

1. **Create new archive** with fresh timestamp:
   ```bash
   mkdir validation_archive_$(date +%Y%m%d_%H%M%S)
   ```

2. **Track differences** from previous runs (product counts, durations)

3. **Note any new failures** or patterns

4. **Update remediation notes** based on what you learn

5. **Archive successful results** to track trends over time

---

## 🤝 Contributing

If you discover:
- A fix for one of the failing scrapers → Update the scraper code and re-run validation
- A new issue → Document it here and create remediation steps
- An improvement → Test it and document the results

Each validation archive becomes a checkpoint in time for monitoring scraper health.

---

**Archive Created**: 2026-05-02 13:26:53 UTC  
**Next Validation**: Recommended within 1 week  
**Status**: ✅ 7 working | ⚠️ 3 need fixes
