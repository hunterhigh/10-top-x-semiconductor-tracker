# Final UI parity audit

The 2026-07-17 final HTML is a component and interaction contract, not merely
a stylesheet. The production renderer may replace demonstration data only; it
must retain the following surfaces and states.

| Surface | Frozen component contract | Production binding |
| --- | --- | --- |
| Header | Report cutoff and ET label; no sample-data wording | `meta.report_date_et` |
| Account grid | Ten `voice` cards, embedded avatars, X link, role, bio, daily stock previews, account drawer | `people[]` |
| Account drawer | `big` directional counters, instrument groups, evidence rows, empty state and source links | `people[].personal_view` |
| Daily report | Existing three `daily-group` sections and stock cards | `daily` |
| Weekly consensus | Existing three `weekly-subsection` sections and `weekly-stock-grid` cards | `weekly.shared_bullish`, `shared_bearish`, `disagreement` |
| Seven-day changes | Existing `week-change-title` and three `change-subsection` columns; no added report container | `weekly.changes` |
| 28-day table | Existing `quarter-table`, price-status text, direction bars, account counts and stock route | `monthly.rows` with non-zero posts only |
| Stock route | Existing full-screen `single-stock-view` iframe, not the account drawer | `stock_drilldowns[symbol]` |
| Stock chart | Existing SVG line/area chart and mention event popovers | `price_series`, `mention_days` |
| Stock windows | Existing 1/7/28-day tabs, ten-person composition and metrics | `window_summaries`, `people_by_window` |
| Stock people detail | Existing `kolGrid`, avatar rows, composition, consistency, latest evidence, expandable original posts | `people_by_window[*].evidence` |
| Shared interactions | `#stock=` routing, return to source position, keyboard card activation, reason tooltip, responsive layouts | final UI event contract |

## Prohibited substitutions

- Do not replace the full-screen stock detail page with a drawer or a new
  page type.
- Do not add a weekly-report container; populate the frozen change grid.
- Do not turn a missing price into `0%`, or a lack of same-day mentions into
  a lack of historical drilldown data.
- Do not leave demonstration date, people, price, reason, or sample labels.

## Required release checks

1. Payload Schema and data invariants pass.
2. Browser opens a real `#stock=` route into `single-stock-view`; its iframe
   has chart, three window tabs, and the 10-person detail grid.
3. Browser opens an account drawer with directional counters and an instrument
   list.
4. 320, 768 and 1440px have no page-level horizontal overflow.
5. Candidate and reference are inspected section-by-section at the same
   viewport; real data is allowed to differ, component hierarchy is not.
