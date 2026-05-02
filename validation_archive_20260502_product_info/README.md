# Product Info Validation Archive: 2026-05-02

Archived validation scripts for supermarket scraper QA. These are not runtime
dependencies for the scrapers or web app.

## Scripts

- `validate_product_info_completeness.py` validates every returned product for
  required `UnifiedProduct` fields and reports optional enrichment coverage per
  supermarket.
- `validate_product_basket.py` validates a concrete basket of user-requested
  products, including milk variants, egg sizes, chicken, meat, olive oil, and
  Greek yogurt.
- `yochananof_browser_probe.py` is a Chrome DevTools network-capture helper for
  retrying Yochananof after maintenance.
- `validate_reference_catalog_coverage.py` compares target supermarkets against
  Shufersal/Tiv Taam reference catalogues by barcode and normalized brand/name.
- `REFERENCE_COVERAGE_TASKS.md` contains the manual validation task list for
  brand-by-brand product coverage work.

## Run

```bash
./venv/bin/python validation_archive_20260502_product_info/validate_product_info_completeness.py
./venv/bin/python validation_archive_20260502_product_info/validate_product_basket.py
./venv/bin/python validation_archive_20260502_product_info/validate_reference_catalog_coverage.py --brand תנובה
```

Yochananof is intentionally skipped until its site/API is out of maintenance.
CHP is intentionally excluded because it is a separate price-comparison scraper.
