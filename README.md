# X Traders Consensus data pipeline

This repository supplies the public data used by the `x-traders-consensus` Skill. It aggregates public X posts; it does not provide investment advice or make independent market claims.

## What is tracked

- All ten tracked accounts contribute their structured `explicit_stance` records to the production dashboard's deterministic daily, weekly, monthly, and monthly-top-pick calculations.
- Account roles remain visible source metadata: options-flow, market-news, and Trump-trade-disclosure records must still be described in their actual context rather than as independent analyst recommendations.
- The dashboard provides daily, weekly, monthly, stock-drilldown, and one monthly favorite card per tracked account. Quarterly reporting is intentionally out of scope.
- The **Information sources** directory gives every tracked account a multilingual Profile with its X link, dashboard role, tracking coverage, activity, and original-post traceability. These metrics describe the collected sample only; they do not rate source quality, reliability, or investment performance.

## Instrument identity

Every stock document carries an `instrument` object with its canonical ID,
display code, original company name, market, aliases, currency, provider
symbol, and verification status. Dashboards display `code · company · market`
at every first user-facing mention. Unknown codes are publishable but explicitly
marked as pending review; they never default to a US/USD quote or price source.

The reviewed registry is `data/ticker_map.json`; `data/ticker_review.json`
contains unresolved entities and extractor-provided company hints. Listing
aliases are merged before aggregation while the original post text and raw
cashtag remain preserved for traceability.

## Data windows

The nine newly added accounts were initially backfilled for 30 days. Serenity retains its migrated historical archive. Price collection covers tickers mentioned in the last 30 days or with at least 50 total mentions; older low-frequency symbols remain in the historical database without forcing a price fetch.

## Operations

GitHub Actions is the only production refresh and publishing entry point. It
keeps manual controls; Cloudflare Cron is the sole automatic trigger:

- `cloudflare/trihourly-dispatcher` dispatches `trihourly-sync` every three
  hours at minute 05 UTC. GitHub's own `schedule` is deliberately absent, so
  two schedulers cannot create duplicate production runs.
- `trihourly-sync` retains its manual dispatch entry and runs the complete
  atomic pipeline: incremental fetch, extraction, database rebuild, prices,
  avatar cache, validated Dashboard payload, browser-checked HTML artifact,
  and one data commit.
- `manual-price-repair` is manual-only for exceptional price remediation. It never races the scheduled pipeline.
- Each successful scheduled run uploads the validated Dashboard, payload, validation report, and SHA-256 as a 30-day Actions Artifact; generated HTML is never committed to Git history.

The three-hour pipeline maintains rolling 52-week history for the union of current monthly rows and all ten monthly favorites. Run the initial price backfill manually with the `require_price_scope` input enabled. It fails if any in-scope ticker remains `pending`; `unavailable` and `unverified_symbol` are explicit, reviewable outcomes rather than silent omissions.

For local checks:

```bash
pip install -r requirements.txt
python scripts/prices.py --provider-test --all-codes
python scripts/verify_data.py
python skill/scripts/dashboard_payload.py <YYYY-MM-DD> --output payload.json
python skill/scripts/render_dashboard.py --input payload.json --output dashboard.html
python skill/scripts/validate_dashboard.py dashboard.html --browser required --expected-avatars 10
```

The data repository is public. Consumers may read its manifest and snapshot without a GitHub token; a token is optional for higher GitHub API rate limits. Extraction supports the existing Anthropic path and OpenAI's Responses API: configure `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`, then select the provider in the manual workflow when needed.
