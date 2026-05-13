# Reference Match Audit Status

Generated from `output_dir/validation/reference_match_audit/target_coverage_summary.json`.

Audit generated at: `2026-05-13T05:43:20.142198+00:00`

Reference catalog:

| Reference Chains | Deduped Branded Products | Match Threshold |
|---|---:|---:|
| `shufersal`, `tivtaam` | 24,382 | 0.66 |

Target coverage:

| Chain | Target Products | Matched | Missing | Coverage |
|---|---:|---:|---:|---:|
| `ramilevi` | 18,504 | 4,077 | 20,305 | 16.72% |
| `yochananof` | 8,533 | 3,910 | 20,472 | 16.04% |
| `keshet` | 12,281 | 3,417 | 20,965 | 14.01% |
| `carrefour` | 4,754 | 3,233 | 21,149 | 13.26% |
| `machsanei_hashook` | 6,739 | 3,013 | 21,369 | 12.36% |
| `victory` | 6,093 | 2,725 | 21,657 | 11.18% |
| `quik` | 4,294 | 2,128 | 22,254 | 8.73% |
| `ybitan` | 4,560 | 2,107 | 22,275 | 8.64% |

Current investigation notes:

| Chain | Issue | Next Action |
|---|---|---|
| `keshet` | Original audit used branch `1570`, which currently returns near-empty category totals. Browser default branch `2585` returns 12,281 products with the current scraper. | Keep audit target branch on `2585`; document how to refresh branch/category constants for future agents. |
| `ramilevi` | Original audit used store `125`, which currently returns only `692` products. Store `1314` hits an Elasticsearch-style `10,000` result-window cap, so the audit now combines stores `1314` and `1389`. | Keep the audit target on stores `1314` and `1389`; use low concurrency because larger multi-store runs trigger transient `403` throttling. |

Resolved findings:

| Chain | Finding | Fix |
|---|---|---|
| `keshet` | `branch=1570` produced only `107` products and `0.10%` coverage. Playwright showed the live site defaults to `branch=2585`, and endpoint probes showed much larger totals there. | Updated `tools/reference_brand_match_audit.py` target selection from `1570` to `2585` and regenerated the audit cache `keshet_target_20260513T052417Z.json`. |
| `ramilevi` | `store=125` produced only `692` products and `1.00%` coverage. Live catalog probes showed most online stores return 6k-10k products. Store `1314` reports `hits.total.relation="gte"` at `10,000`, and requests where `from + size` crosses `10,000` return `Internal Server Error`. | Updated `tools/reference_brand_match_audit.py` target selection from `125` to stores `1314` and `1389`, regenerated the audit cache `ramilevi_target_20260513T053747Z.json`, fixed blank `net_content` parsing warnings, and made the scraper robust to non-dict API error responses. |

Primary output files:

| File | Purpose |
|---|---|
| `output_dir/validation/reference_match_audit/reference_products_with_brands.json` | Deduped branded reference product list. |
| `output_dir/validation/reference_match_audit/target_coverage_summary.json` | Per-target coverage summary and reason counts. |
| `output_dir/validation/reference_match_audit/reference_match_matrix.csv` | Per-reference match flags, scores, and reasons by target chain. |
