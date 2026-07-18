# Dashboard implementation log

## 2026-07-18 — Production writer collision fix

- Reviewed the failed `bihourly-sync` run supplied from GitHub Actions. All ten
  fetch/extract jobs and aggregation had succeeded; publication alone failed
  because `git push` was rejected as non-fast-forward (`fetch first`).
- Every data writer (`trihourly-sync`, `backfill`, and manual price repair) is
  now in one shared, non-cancelling concurrency group and rebases
  `origin/main` before publishing. A genuine rebase conflict fails visibly and
  publishes nothing; the next scheduled refresh starts from current `main`.
- Backfill is fail-closed for a missing requested account and now uses the
  same price, avatar, payload, rule, browser-validation, SHA-256, and 30-day
  dashboard-artifact chain as the scheduled workflow.

## 2026-07-18 — backend adaptation started

- Canonical source: `10V-dashboard-backend-handoff-final-2026-07-17/`.
- Added deterministic `skill/scripts/dashboard_payload.py`; it creates the
  handoff payload, applies the approved aggregation rules, validates
  invariants, and supports Draft 2020-12 JSON Schema validation.
- Added `skill/scripts/render_dashboard.py`; it emits one interactive HTML
  artifact with embedded payload, avatar data URIs, person panels, and
  `#stock=<display_code>` drilldowns.
- Updated `scripts/build_db.py` to use `America/New_York` and persist an ISO
  `created_at` timestamp in future stock-document mentions.
- Added `scripts/refresh_avatars.py` for explicit build-stage avatar caching.
- Added payload/DST tests and v2 support to the dashboard validator.

## Data refresh status

No external fetch, extraction, price refresh, or database rebuild was run in
this implementation pass because the working tree already contains active
uncommitted database changes.  The next operator must run the incremental
workflow from `skill/SKILL.md`, then record the cutoff date, validation result,
and generated HTML SHA-256 here.  The 2026-07-17 handoff package itself remains
an immutable source snapshot.

## 2026-07-18 — GitHub Actions production workflow prepared

- Replaced the bi-hourly scheduled content and price workflows with
  `trihourly-sync` at `0 */3 * * *` UTC and manual-only price repair.
- The scheduled workflow is fail-closed: all ten account jobs must succeed
  before database rebuild, price refresh, avatar fetch, dashboard rendering,
  artifact upload, or data commit can occur.
- A successful run uploads a 30-day Artifact containing the validated HTML,
  payload, validation report, and SHA-256.  HTML is not committed to Git.
- Remote activation and first-run diagnosis still require GitHub Actions access
  and a manual production run after these changes are reviewed and pushed.
