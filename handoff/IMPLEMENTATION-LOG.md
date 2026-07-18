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

The 2026-07-17 handoff package remains an immutable source snapshot. The
following recoverable baseline supersedes the earlier “not yet rebuilt” note.

## 2026-07-18 — recoverable v2 data baseline

- Rebuilt deterministically from the committed-ready local
  `raw_tweets.json`, `extracted.json`, and `state.json` files for all ten
  tracked accounts. No TwitterAPI.io request, LLM extraction, or price API
  request was made.
- Published source inputs, database, ticker/instrument registry, review queue,
  10-account coverage statistics, and cached public X avatars together.
- Baseline cutoff: **2026-07-17 ET**; 1,628 entities, 19,307 structured
  mentions, and 10/10 accounts with raw/extracted/state files.
- `verify_data.py` passed with source-binding fields (`created_at`, ET date,
  tweet id, account id, text, URL, and list-valued reasons) present on every
  published mention. It also validates the 10 profile records and instrument
  identity/price-status coverage.
- Handoff aggregation tests passed. The rendered local validation artifact had
  10 visible embedded avatars, 1,628 stock drilldowns, 10 account panels, and
  zero page overflow at 320, 768, and 1440px. Local artifact SHA-256:
  `dashboard.html` `E8C8494F1F24D6C3E6A9D9BBF57F3D99953DAE64C7DD90016FBD5F8ABA472F16`;
  `payload.json` `B9455800C565FC179EFE906BC79DA17E037E2ADCF5C253E1ADB4ACD83604438C`.
- Generated HTML is deliberately excluded from Git. The first funded manual
  production run will generate the 30-day Actions Artifact and record its run
  URL, commit SHA, cutoff, and artifact SHA-256 here.

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
