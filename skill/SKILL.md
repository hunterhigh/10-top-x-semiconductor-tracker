---
name: x-traders-consensus
description: "Track public X posts from 10 market accounts: compare the seven opinion accounts' stated bullish, bearish, or neutral views; present three separate flow, news, and disclosure signals; answer source-linked company questions; and build the validated v2 dashboard. Never use for investment advice."
---

# X Traders Consensus

This Skill reports structured, source-linked public posts. It is not a stock-analysis, trading, or investment-advice tool.

## Non-negotiable rules

Every answer and dashboard must include this disclaimer, or a faithful translation:

> This is an aggregation of public posts from tracked X accounts, summarized automatically by AI. It may contain errors or omissions and is not guaranteed accurate — always refer to the original posts and verify independently. This does not constitute investment advice of any kind.

- Describe only what an account expressed or posted. Never recommend buying, selling, holding, or predict a security's price.
- Retain original post text, company names, reasons, dates, figures, and URLs. Do not translate them.
- Each claim about an account must remain traceable to its `blogger_id` and original-post URL.
- Only records whose `mention_type` is `explicit_stance` and whose account `signal_type` is `opinion` contribute to a stance count.
- The seven `opinion` accounts form the only consensus cohort. The `flow`, `news`, and `disclosure` accounts are parallel facts, never opinions and never confirmation of an opinion consensus. Attribute disclosure trades to Trump, not to `DJTRadar`.
- Use gender-neutral account references unless the account self-identifies otherwise.
- All report dates and windows use `America/New_York` (ET), including daylight-saving transitions.
- Preserve entity identity from each record's `instrument` object. Display `code · original company name · market`; do not assume an unknown code is US/USD.
- A missing, pending, partial, or unavailable price must be shown as its real status, never as `0%`.

## Production source and freshness

Cloud `main` in `hunterhigh/10-top-x-semiconductor-tracker` is the only production Skill and data source. Local files are for development, dry runs, and diagnosis only.

At the start of every new user request, retrieve `data/db/manifest.json` from remote `main`. If its `generated_at` differs from the local working copy, download a fresh copy of `data/db/`, `config/bloggers.json`, and `config/blogger_profiles.json` before analysis. Reuse that verified copy only within the same request.

```python
import json, os, requests

repo = "hunterhigh/10-top-x-semiconductor-tracker"
token = os.environ.get("GITHUB_TOKEN", "")
headers = {"Authorization": f"token {token}"} if token else {}
manifest = requests.get(
    f"https://raw.githubusercontent.com/{repo}/main/data/db/manifest.json",
    headers=headers, timeout=30,
).json()
```

State the actual ET cutoff (`manifest["date_range"][1]`) when a request is time-sensitive. If it is older than the current ET date, say so; never present stale data as current.

The canonical data tree is:

```text
data/bloggers/<account>/{raw_tweets.json,extracted.json,state.json}
data/db/{manifest.json,index.json,stocks/*.json,blogger_profiles.json}
config/{bloggers.json,blogger_profiles.json}
```

`raw_tweets.json`, `extracted.json`, and `state.json` make the baseline recoverable. `stocks/*.json` is the query and rendering database. Every final mention must retain `created_at`, ET `date`, `tweet_id`, `blogger_id`, `mention_type`, `stance`, `reasons`, `text`, and `url`; reasons, evidence text, and URL must be from the same structured mention.

## Dashboard v2: sole production path

Before building or changing the dashboard, read these required materials in order:

1. `handoff/10V-dashboard-backend-handoff-final-2026-07-17/README-START-HERE.md`
2. `02-backend-contract/dashboard-render-contract.schema.json`
3. `03-rules-and-tests/report-aggregation-rules.md` and `report_rules.py`
4. `04-reference/CHANGE-HANDOFF-FULL.md`

The final UI reference is `01-final-ui/10-market-voices-complete.html`. It supplies layout and interaction only: its demonstration arrays, people, reasons, prices, and stock data are never production input.

The only production workflow is:

```text
refresh → extract → build_db → verify_data → prices → refresh_avatars
→ payload → Schema and invariant validation → v2 render → browser validation → Artifact delivery
```

- Daily, 7-day, and 28-day windows are ET closed intervals: `[D,D]`, `[D-6,D]`, `[D-27,D]`.
- Reuse the handoff's `report_rules.py` for aggregation and weekly changes. Do not reimplement or infer those rules in a renderer.
- `skill/scripts/dashboard_payload.py` builds the sole render input and validates the Draft 2020-12 Schema by default.
- `skill/scripts/render_dashboard.py` accepts only the validated deterministic payload; it does not infer stance, reasons, or database fields.
- The delivered page is one self-contained HTML file, embeds each visible avatar, supports `#stock=<display_code>` drilldowns and the final sidebar interactions, and may use JavaScript as required by the approved final UI.
- `skill/scripts/validate_dashboard.py` must pass with `--browser required --expected-avatars 10` at 320, 768, and 1440 px before delivery.
- Generated HTML is an Actions Artifact, never a Git-tracked production file. Retain the HTML, payload, validation JSON, and SHA-256 for 30 days.

`serenity_render.py` is historical compatibility material only. It is not a production entry point, is not a release criterion, and must not be called by workflows or this Skill.

### Dashboard commands

Run from the repository root, after the data source is confirmed current:

```powershell
python scripts/incremental_refresh.py --report-date <YYYY-MM-DD> # planning only
python scripts/incremental_refresh.py --execute --report-date <YYYY-MM-DD>
python scripts/build_db.py
python scripts/verify_data.py
python scripts/prices.py --asof <YYYY-MM-DD>
python scripts/refresh_avatars.py
python skill/scripts/dashboard_payload.py <YYYY-MM-DD> --output payload.json
python skill/scripts/render_dashboard.py <YYYY-MM-DD> --output dashboard.html
python skill/scripts/validate_dashboard.py dashboard.html --browser required --expected-avatars 10
```

For production, GitHub Actions is the only publisher. It uses one atomic three-hour workflow (`0 */3 * * *` UTC), a shared concurrency group, all ten API fetches, and a single remote rebase-and-publish job. A failure in any account stops extraction, database rebuild, prices, rendering, artifact upload, and data commit. While the TwitterAPI.io key has no usable balance, this workflow remains disabled; do not use an old cache to claim a successful refresh.

## Answering account and company questions

Resolve an account from `config/bloggers.json`. For a company, first resolve through `data/db/index.json`; ambiguity requires clarification. Do not use model memory to assign a ticker, exchange, or listing.

For a single-account question, run:

```powershell
python skill/scripts/analyze_stock.py <db>/stocks/<TICKER>.json --blogger <BLOGGER_ID>
```

For a cross-account question, omit `--blogger`, then show the seven opinion accounts individually: bullish, bearish, neutral/no clear direction, or not covered in the requested ET window. Report the three signal accounts in a separate “Other signals” section, with no stance math and no wording that they confirm the consensus.

For every narrative, include the report date/window, original links, account attribution, and the required disclaimer. If a ticker has fewer than three explicit stances, say that there is insufficient structured opinion history rather than inventing a thesis. If there are no records for an account or date, say so plainly.

## Skill package and release checks

The package retains `agents/openai.yaml`, this `SKILL.md`, `scripts/analyze_stock.py`, `scripts/dashboard_payload.py`, `scripts/render_dashboard.py`, `scripts/validate_dashboard.py`, and `references/dashboard-contract.md`.

CI runs the handoff aggregation tests, ET/DST tests, payload Schema and invariants, avatar and evidence checks, and browser validation. Record production changes and verification results in `handoff/IMPLEMENTATION-LOG.md`; retain the original handoff changelog without rewriting it.

## Failure behavior

- Remote manifest unavailable: say data is temporarily unavailable; do not silently use an unverified older copy.
- Stale manifest: state the exact ET cutoff and warning before answering.
- Fetch credential, HTTP/API, rate-limit exhaustion, or network failure: fail the run without changing raw data or state watermarks.
- Invalid payload, schema/invariant failure, missing avatar, or browser failure: do not publish an HTML Artifact.
- Unknown ticker or account: say it is not in the tracked dataset and ask for clarification when needed.
