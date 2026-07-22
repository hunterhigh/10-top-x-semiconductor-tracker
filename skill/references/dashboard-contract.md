# Dashboard v2 production contract

The 2026-07-17 handoff package is the authoritative dashboard contract. Read its schema, aggregation rules, executable rules, UI reference, and change log before any renderer or presentation change.

## Fixed rules

- The only renderer input is a validated payload built by `dashboard_payload.py` from `data/db/stocks/*.json` and the account/profile configuration.
- The only production renderer is `render_dashboard.py`; it emits one self-contained interactive HTML file based on the final UI. JavaScript and `#stock=<display_code>` routing are required capabilities, not failures.
- Never use the reference HTML's demo data. Never infer a stance, reason, evidence link, or price while rendering.
- Use ET closed windows: daily `[D,D]`, weekly `[D-6,D]`, monthly `[D-27,D]`. Weekly comparisons and stock classifications must use the handoff `report_rules.py`.
- Count `explicit_stance` records from all ten tracked accounts in daily, weekly, monthly, and monthly-top-pick calculations. Preserve `signal_type` as source context.
- An evidence card's reasons, original text, and URL must come from the same mention. Empty reasons are valid.
- Display explicit price availability. Missing/pending/partial data is never `0%`.
- Monthly rows expose `price_change` as the 28-day compatibility value plus explicit
  `price_change_28d` and `price_change_52w` objects. The 52-week object uses the
  first close on or within seven calendar days after `D-52 weeks`. Shorter listed
  histories use the available stored range with `status=ok`,
  `basis=available_history_fallback`, and `history_status=insufficient_history`;
  missing data is never `0%`.
- `monthly.top_picks` contains exactly ten unique account cards. Rank eligible
  listed equities by unique bullish posts, all explicit posts, latest bullish
  timestamp, then ticker alphabetically. Empty favorites remain explicit cards.
- Maintain 52-week history for the union of monthly rows and unique top-pick
  instruments. Payload, price fetching, and verification must import the same
  `report_scope.py` implementation.
- Every visible account avatar must be a nonblank embedded resource or an explicit visible fallback after a documented fetch failure.

## Required release validation

1. Handoff aggregation tests pass.
2. ET/DST, data-schema, evidence/source-binding, URL-deduplication, and 10-person-state invariants pass.
3. The full payload passes Draft 2020-12 Schema validation.
4. `validate_dashboard.py <html> --browser required --expected-avatars 10` passes at 320, 768, and 1440 px.
5. Upload the HTML, payload, validation results, and SHA-256 as a 30-day Actions Artifact. Do not commit generated HTML.

`serenity_render.py` and its static-page requirements are historical compatibility only and are not part of this contract.
