# Reference Match Audit Status

Generated from `output_dir/validation/reference_match_audit/target_coverage_summary.json`.

Audit generated at: `2026-05-13T05:25:31.492043+00:00`

Reference catalog:

| Reference Chains | Deduped Branded Products | Match Threshold |
|---|---:|---:|
| `shufersal`, `tivtaam` | 24,382 | 0.66 |

Target coverage:

| Chain | Target Products | Matched | Missing | Coverage |
|---|---:|---:|---:|---:|
| `yochananof` | 8,533 | 3,910 | 20,472 | 16.04% |
| `keshet` | 12,281 | 3,421 | 20,961 | 14.03% |
| `carrefour` | 4,754 | 3,233 | 21,149 | 13.26% |
| `machsanei_hashook` | 6,739 | 3,012 | 21,370 | 12.35% |
| `victory` | 6,093 | 2,725 | 21,657 | 11.18% |
| `quik` | 4,294 | 2,130 | 22,252 | 8.74% |
| `ybitan` | 4,560 | 2,109 | 22,273 | 8.65% |
| `ramilevi` | 692 | 243 | 24,139 | 1.00% |

Current investigation notes:

| Chain | Issue | Next Action |
|---|---|---|
| `keshet` | Original audit used branch `1570`, which currently returns near-empty category totals. Browser default branch `2585` returns 12,281 products with the current scraper. | Keep audit target branch on `2585`; document how to refresh branch/category constants for future agents. |
| `ramilevi` | Target scrape returned only 692 products, also too low for a full supermarket catalog. | Investigate after Keshet; confirm branch selection, API pagination, and category/search strategy. |

Resolved findings:

| Chain | Finding | Fix |
|---|---|---|
| `keshet` | `branch=1570` produced only `107` products and `0.10%` coverage. Playwright showed the live site defaults to `branch=2585`, and endpoint probes showed much larger totals there. | Updated `tools/reference_brand_match_audit.py` target selection from `1570` to `2585` and regenerated the audit cache `keshet_target_20260513T052417Z.json`. |

Primary output files:

| File | Purpose |
|---|---|
| `output_dir/validation/reference_match_audit/reference_products_with_brands.json` | Deduped branded reference product list. |
| `output_dir/validation/reference_match_audit/target_coverage_summary.json` | Per-target coverage summary and reason counts. |
| `output_dir/validation/reference_match_audit/reference_match_matrix.csv` | Per-reference match flags, scores, and reasons by target chain. |
