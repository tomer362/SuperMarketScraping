# Reference Catalogue Coverage Task List

Goal: use Shufersal and Tiv Taam as high-coverage reference catalogues, then
manually validate whether other supermarket scrapers expose the same products
for specific brands and product families.

## Workflow

1. Generate or refresh full catalogues for Shufersal and Tiv Taam.
2. Generate or refresh full catalogues for each target supermarket branch.
3. Run `validate_reference_catalog_coverage.py` with one brand or product family
   at a time, starting with high-priority brands.
4. Inspect unmatched reference products manually in the target site/API.
5. Classify each miss:
   - product genuinely unavailable in that supermarket
   - scraper category coverage gap
   - scraper pagination/cap issue
   - product mapping problem, e.g. missing barcode/brand/unit
   - matching heuristic false negative
6. Fix scraper/API coverage issues before moving to the next brand.
7. Commit after each meaningful scraper fix or validation milestone.

## Initial Brand/Product Priorities

- Milk and dairy: תנובה, טרה, יטבתה, מולר, דנונה
- Eggs: brands/suppliers visible in Shufersal/Tiv Taam references
- Chicken/meat: עוף טוב, זוגלובק, טירת צבי, אדום אדום where relevant
- Olive oil: יד מרדכי, זיתא, עץ הזית, שמן תעשיות
- Yogurt: תנובה, מולר, דנונה, גד

## Commands

Run broad cached comparison where output JSON exists:

```bash
./venv/bin/python validation_archive_20260502_product_info/validate_reference_catalog_coverage.py --use-cache-only
```

Run focused live/cached validation for one brand:

```bash
./venv/bin/python validation_archive_20260502_product_info/validate_reference_catalog_coverage.py --brand תנובה
```

Run for selected targets:

```bash
./venv/bin/python validation_archive_20260502_product_info/validate_reference_catalog_coverage.py --brand תנובה --targets quik ybitan victory
```

## Current Handoff Notes

- Product-info completeness report was written to
  `output_dir/validation/product_info_completeness.json`.
- Shufersal and Rami Levy have required-schema category gaps:
  Shufersal has 938 products with empty `category_ids`; Rami Levy has 1.
- Many chains have low `manufacturer` coverage by design/current API mapping.
  Do not block reference matching on manufacturer unless barcode/brand/name are
  insufficient.
- Yochananof is skipped until maintenance ends.
