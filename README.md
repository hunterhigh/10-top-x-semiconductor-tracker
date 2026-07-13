# X Traders Consensus data pipeline

This repository supplies the public data used by the `x-traders-consensus` Skill. It aggregates public X posts; it does not provide investment advice or make independent market claims.

## What is tracked

- Seven `opinion` accounts contribute only their own explicit bullish, bearish, or neutral stances to the `N of 7` consensus.
- Three parallel sources provide options-flow, market-news, or Trump-trade-disclosure activity. They are never counted as an opinion and never presented as validating consensus.
- The dashboard provides daily, weekly, monthly, and consensus views. Quarterly reporting is intentionally out of scope.

## Data windows

The nine newly added accounts were initially backfilled for 30 days. Serenity retains its migrated historical archive. Price collection covers tickers mentioned in the last 30 days or with at least 50 total mentions; older low-frequency symbols remain in the historical database without forcing a price fetch.

## Operations

GitHub Actions keeps manual controls and also schedules:

- `bihourly-sync` every two hours at minute 00 for incremental fetch and extraction.
- `bihourly-prices` every two hours at minute 45 for price refresh. Both share a concurrency lock, so a delayed sync finishes before prices write data.

Run the initial price backfill manually with the `require_price_scope` input enabled. It fails if any in-scope ticker remains `pending`; `unavailable` and `unverified_symbol` are explicit, reviewable outcomes rather than silent omissions.

For local checks:

```bash
pip install -r requirements.txt
python scripts/prices.py --provider-test --all-codes
python scripts/verify_data.py
python skill/scripts/serenity_render.py --db data/db --config config/bloggers.json --blogger all
```

The data repository is public. Consumers may read its manifest and snapshot without a GitHub token; a token is optional for higher GitHub API rate limits. Extraction supports the existing Anthropic path and OpenAI's Responses API: configure `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`, then select the provider in the manual workflow when needed.
