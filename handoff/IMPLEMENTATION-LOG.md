# Dashboard implementation log

## 2026-07-20 - manual instrument-registry remediation

- Reviewed all 1,600 entities in the production review snapshot instead of
  defaulting ticker-shaped strings to US/USD. The SEC official company ticker
  and exchange registry supplied 859 high-confidence US listings; the manual
  registry now contains 901 verified price identities after overseas listings
  and aliases were reviewed.
- Added explicit identities for major Korean, Japanese, Taiwanese, Chinese,
  European, Canadian, and newly listed US instruments, including AAPL, SPCX,
  000660, 005930, 285A, and 2454. Each verified row records market, country,
  currency, provider symbol, review date, and its registry source.
- Preserved ambiguous or reused codes as non-price-routable review records.
  Private companies, indices, commodities, futures roots, and crypto mentions
  are explicitly classified so they cannot silently acquire the price of an
  unrelated same-code equity (for example SPX or GOLD).
- Added canonical aliases for common names and extraction variants such as
  APPL -> AAPL, SK HYNIX -> 000660, SAMSUNG -> 005930, and SPACEX -> SPCX.
  Regression tests cover US, overseas, alias, private-company, and index cases.

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

## 2026-07-18 — Cloudflare external scheduler prepared

- Cloudflare Cron is configured as the sole automatic trigger at minute 05 of
  every third UTC hour. It sends a `repository_dispatch` event to the default
  branch's `trihourly-sync` workflow; GitHub's internal cron is removed.
- The Worker records one 3-hour UTC slot in Cloudflare KV only after GitHub
  accepts the dispatch. The existing GitHub concurrency group remains the
  second protection against duplicate writers.
- The dispatcher uses a Cloudflare secret for a repository-restricted GitHub
  dispatch credential. It never contains tracker API, model, or price keys.
- Deployment remains intentionally manual: create the KV namespace, set its
  ID in `cloudflare/trihourly-dispatcher/wrangler.jsonc`, add the secret, deploy
  the Worker, then inspect the first dispatch and GitHub Step Summary.

## 2026-07-18 - final UI and standalone Skill integration

- Replaced the simplified v2 renderer with an adapter over the frozen 7/17
  final HTML. Its demonstration script is excluded at build time; the approved
  visual shell, sidebar, daily/weekly/28-day modules and `#stock=` interaction
  surface are retained.
- The renderer consumes only the validated payload. It fills the existing
  people, daily, weekly, 28-day and stock-drilldown areas from real data and
  does not create a second weekly-report container.
- The 28-day table filters out instruments without a post in its ET window.
  On the 2026-07-18 snapshot it contains 824 non-empty instruments; the weekly
  output contains 17 shared-bullish and 2 disagreement items.
- Packaged the final UI, Draft 2020-12 Schema and approved aggregation rules
  inside `skill/references/`. CI asserts byte-for-byte equality with the
  immutable handoff originals.
- Added a read-only installed-Skill snapshot synchronizer and Q&A entry point.
  It verifies remote `main`'s manifest before atomically refreshing an external
  cache; it has no API or publishing credentials and never falls back to an
  unverified old snapshot.
- Verification passed: project tests, 7 handoff aggregation tests, real 7/18
  payload Schema/invariants, 10 embedded avatars, stock drilldown routing, and
  320/768/1440px browser checks (zero page-level horizontal overflow).

## 2026-07-20 — EODHD instrument identity resolution

- Added a fail-closed EODHD Search API resolver for unverified conventional US
  ticker symbols. Automatic application requires one exact primary US common
  stock with USD currency and a matching extracted company-name hint.
- Resolution results and failure reasons are retained in
  `data/ticker_resolution_eodhd.json`; verified entries are persisted to the
  existing instrument registry before the database is rebuilt.
- Wired the resolver into trihourly, backfill, and incremental refresh paths,
  reusing the existing `EODHD_API_KEY` GitHub Actions secret.
- Verification passed: Python compilation, 17 project tests, focused identity
  resolver tests, and `git diff --check`.

## 2026-07-21 - rolling 52-week price history contract

- Added backward cache extension for current 28-day report instruments. The
  report cutoff comes from the verified ET manifest and the target start is
  exactly 52 weeks earlier; existing prefix and suffix data are merged by date.
- Separated latest-price freshness from 52-week coverage. Completed, genuinely
  short listing histories, unavailable identities, pending attempts and fetch
  errors retain distinct machine-readable states and reasons.
- Monthly payload rows now expose `price_change_28d` and `price_change_52w`
  while retaining `price_change` as the 28-day compatibility field. Missing
  history always has a null percentage.
- Added a fail-closed manual backfill input and ongoing trihourly maintenance.
  The production backfill and its final coverage counts will run only after the
  implementation PR is merged.
- Verification passed locally: Python compilation, 52-week cache/state tests,
  payload Draft 2020-12 Schema validation, ET/build tests, and all seven handoff
  aggregation tests. The complete repository discovery suite requires the
  non-sparse CI checkout and remains a PR check.

## 2026-07-21 - monthly scope and EODHD budget repair

- Replaced the broad “any mention in 28 days” price-history scope with the
  actual monthly consensus rule: at least three distinct opinion accounts must
  provide an `explicit_stance` bullish or bearish signal in the ET closed
  interval `[D-27, D]`.
- The payload builder, 52-week fetcher, and verification gate now import one
  shared scope function. Flow, news, disclosure, background, and neutral rows
  remain visible as context but cannot expand the price-history queue.
- Report-scope instruments are processed before the broader price universe so
  unrelated non-US symbols cannot consume the 20-call EODHD budget first.
- A provider or budget failure remains `error` in 52-week coverage even when
  the general price layer falls back to `unavailable`; strict verification
  therefore fails visibly without publishing a partial snapshot.
- Verification passed locally: 31 repository tests, all seven handoff rule
  tests, Python compilation, diff checks, and real 2026-07-20 report-scope
  comparisons (40 eligible in the earlier Artifact and 46 in the final report).

## 2026-07-21 - provider-aware price enrichment state machine

- Replaced the single undifferentiated provider error path with durable states
  for supported, provider-unsupported, deferred, retryable and unresolved work.
  Authentication failures remain fatal; an honest per-symbol degradation may
  publish only when it has a reason and a scheduled retry.
- Added generic EODHD Search API recovery after a 404. An automatically selected
  replacement must be the only exact base-code and currency match; ambiguous or
  unsupported results are never guessed and are re-probed on a cooldown.
- Added `data/price_enrichment_queue.json`, keyed by stable instrument identity,
  so report-scope work survives runs and provider budgets. The queue is pruned
  when an instrument leaves the monthly report or reaches complete/verified
  short-history coverage.
- Monthly report instruments run before a rotating, oldest-first maintenance
  batch. This prevents hundreds of unrelated symbols from exhausting the
  non-US provider budget after the report scope is already complete.
- Actions summaries and the manifest now expose provider-state counts and queue
  depth. No ticker-specific no-price exception was added.

## 2026-07-22 - ten-account monthly favorites production contract

- Adopted the supplied payload and compressed single-file renderer as the new
  production baseline. Their received SHA-256 values were
  `0889BEA39560519587B15A1DAB075D24C095F58A0B26C04730F4344CFE6DD951`
  and `46B2FB2BFAA001F8B74241508F1D1F9B991562D2F9F174CEB6E133315C614F8D`.
- Formalized `monthly.top_picks` as exactly one deterministic card for each of
  the ten tracked accounts. All ten accounts now participate in structured
  dashboard direction counts; source roles remain visible metadata.
- Added one shared equity/ranking/scope module used by payload generation,
  price maintenance, and verification. Rolling 52-week maintenance covers the
  union of monthly rows and unique top-pick instruments.
- Short listed histories now render the available-range return with explicit
  `available_history_fallback` / `insufficient_history` metadata. Missing
  prices remain null and never become zero.
- Updated all production workflows to render only a previously validated JSON
  payload. The renderer performs no aggregation or data inference.
- Real 2026-07-21 snapshot regression produced 10 favorite cards, 41 monthly
  rows, and 800 stock drilldowns. Draft 2020-12 Schema/invariants passed; the
  compressed HTML passed Chromium validation at 320, 768, and 1440 px with no
  page-level horizontal overflow.
