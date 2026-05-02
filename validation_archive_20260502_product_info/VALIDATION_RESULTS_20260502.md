# Validation Results: 2026-05-02

## Product Info Completeness

Command:

```bash
./venv/bin/python validation_archive_20260502_product_info/validate_product_info_completeness.py
```

Output report:

```text
output_dir/validation/product_info_completeness.json
```

Summary:

| Chain | Status | Products | Required Issues |
| --- | --- | ---: | ---: |
| tivtaam | PASS | 17,896 | 0 |
| shufersal | FAIL | 24,866 | 938 missing/empty `category_ids` |
| yochananof | SKIP | 0 | skipped during maintenance |
| carrefour | PASS | 4,866 | 0 |
| machsanei_hashook | PASS | 7,018 | 0 |
| ramilevi | FAIL | 690 | 1 missing/empty `category_ids` |
| keshet | PASS | 102 | 0 |
| quik | PASS | 4,531 | 0 |
| victory | PASS | 6,087 | 0 |
| ybitan | PASS | 4,706 | 0 |

Key findings:

- Required unified product fields are valid for every non-skipped scraper except
  Shufersal and Rami Levy category coverage.
- Shufersal is still the broadest reference catalogue, but 938 products do not
  expose category IDs through current mapping/API data.
- Rami Levy has one product without category IDs.
- Manufacturer coverage is mostly absent outside Shufersal/Rami Levy and should
  not be treated as a hard failure until the source APIs are inspected.
- Yochananof is skipped until its site/API exits maintenance.

## Reference Coverage Sanity Run

Command:

```bash
./venv/bin/python validation_archive_20260502_product_info/validate_reference_catalog_coverage.py --brand תנובה --use-cache-only --targets quik keshet carrefour machsanei_hashook
```

Output report:

```text
output_dir/validation/reference_catalog_coverage.json
```

This was a script sanity check using older cached files, not a final product
availability conclusion. It showed low coverage for `תנובה`, which should be
manually rechecked with fresh live catalogues per target supermarket. See
`REFERENCE_COVERAGE_TASKS.md` for the full workflow.
