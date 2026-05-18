# Web App Backend

## Purpose

This backend powers the mobile-first grocery comparison web app.

It is responsible for:

- user signup/login with cookie sessions
- canonical product search and autocomplete
- saved shopping lists with quantities
- chain-level basket comparison using the cheapest single branch per chain
- scheduled or cron-triggered catalog refresh from the Python supermarket scrapers
- deterministic seeded data mode for automated tests and Playwright

## Runtime Model

- The database is the catalog cache.
- User requests never trigger live scrapes.
- Catalog refresh runs in the background locally, or through a cron-triggered API route on serverless deployments, and updates cached offers.
- Public product APIs only expose active chains and active offers.

## Environment

Key settings are read from `settings.py`:

- `DATABASE_URL`
- `SCRAPE_INTERVAL_HOURS`
- `ENABLE_SCHEDULER`
- `AUTO_REFRESH_ON_START`
- `SECRET_KEY`
- `SESSION_COOKIE_NAME`
- `SESSION_MAX_AGE_DAYS`
- `SESSION_COOKIE_SECURE`
- `CORS_ORIGINS`
- `CATALOG_REFRESH_TOKEN` or `CRON_SECRET`
- `SEED_TEST_DATA`
- `RESET_TEST_DB_ON_START`
- `LOG_LEVEL`
- `CATALOG_DEBUG`

## Debug Logging

Set `CATALOG_DEBUG=1` to enable verbose search and comparison diagnostics.

This logs:
- request method/path/query/status/timing
- normalized search query, chain filters, exact/fuzzy result counts, returned IDs
- product-detail equivalent canonical IDs and chain/store offer coverage
- shopping-list comparison exact IDs, generic groups, equivalent IDs, candidate stores
- per-chain/per-store totals, missing products, and the selected best result

For a full local live-debug session, start with:

```bash
./run-webapp.sh --debug
```

Frontend API request/response logging is enabled by `VITE_API_DEBUG=1`, which `./run-webapp.sh --debug` sets automatically for the Vite dev server.

## Vercel Deployment

The `webapp/` directory can be used as a Vercel project root.

- `api/index.py` exposes the FastAPI app as a Python serverless function.
- `vercel.json` builds the Vite frontend from `frontend/` and rewrites `/api/*` to the FastAPI function.
- In-process scheduling is disabled by default when `VERCEL=1`.
- Vercel Cron calls `GET /api/catalog/refresh/cron` daily to refresh cached supermarket data.
- Without `DATABASE_URL`, Vercel uses ephemeral SQLite in `/tmp` so the app can boot out of the box.
- Set a durable hosted PostgreSQL `DATABASE_URL` for production data; serverless SQLite is not durable.
- If `CATALOG_REFRESH_TOKEN` or `CRON_SECRET` is set, the cron route requires `Authorization: Bearer <token>`.

## Test Mode

For local automated tests and Playwright, the backend can run in a deterministic seed mode:

- scheduler disabled
- no live supermarket network calls
- seeded catalog data inserted on startup
- SQLite supported for fast isolated test runs

This is the preferred mode for CI and frontend E2E.

## Yochananof

`yochananof` is enabled in the active web app chain registry after product-level GraphQL validation showed stable live results.

## Basket Comparison Rule

The comparison API does not mix prices from different branches of the same chain.

For each chain:

1. evaluate every available branch/store for the whole basket
2. apply quantity-aware pricing and multi-buy logic per item
3. pick the cheapest single branch for that chain

This avoids unrealistic chain totals assembled from different branches.

## Testing Strategy

### Backend

- unit tests for auth, matching, and basket math
- API integration tests with deterministic seeded catalog data
- disabled-chain behavior tests for Yochananof

### Frontend

- component tests with Vitest and Testing Library
- Playwright tests for mobile-first end-to-end flows

### Recommended CI Shape

- backend tests
- frontend unit tests
- frontend build
- Playwright on iPhone, Android, and desktop Chromium

Keep live supermarket scraping out of merge-blocking CI.
