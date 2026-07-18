# Investor Dashboard Contract

## Job to be done

Help an investor understand which tracked entities are viewed most positively or negatively, what changed, why, and which original posts support that view, without requiring a glossary before the report is useful.

## Stable workflow

1. **Refresh**: verify the remote manifest and select the requested account scope, language, and date window.
2. **Model**: derive entity identity, opinion-account stance, signal-account activity, price availability, and source evidence without changing the underlying consensus rules.
3. **Render**: produce one self-contained offline HTML file whose primary views work without JavaScript.
4. **Validate**: run structural checks and a real browser rendering check. Treat a visually missing asset or an ambiguous information hierarchy as a failed build even when the HTML contains the underlying data.
5. **Deliver**: provide only a validated artifact with its scope, data date, and compliance disclaimer.

## Presentation invariants

### Decision hierarchy

- Lead monthly and weekly views with the most net-bullish and most net-bearish entities among the seven opinion accounts.
- Lead daily views with new or changed explicit views, not a raw activity list.
- Order stock Profiles as: current view, recent changes, attributed reasons and disagreement, account views, original-post evidence.
- Keep signal accounts outside stance counts, reasons, changes, and disagreement. Show their posts only as separately attributed related activity.

### Entity and source identity

- Display an entity as `code · original company name · market` wherever space permits.
- Use the current X Profile avatar for each tracked account. Embed each unique avatar once and reuse it.
- Allow a letter fallback only when avatar retrieval explicitly fails. Never render a blank circle.
- Do not accept an embedded data URI as proof that an avatar works. In a browser, the visible avatar must have non-zero dimensions and a computed image value other than `none`.
- Keep account names, handles, company names, reasons, and original post text untranslated.

### Table semantics

Each browse-table column must answer one question:

1. **Entity** — what is being discussed?
2. **Views** — how many distinct opinion accounts are net-bullish or net-bearish, and who are they?
3. **Period performance** — what is the price change? Show only the percentage and currency here.
4. **Time context** — when was performance measured and when was the entity last mentioned? Label `price window` and `latest mention` separately.

Do not place complete date ranges inside the performance value. Do not use an unlabeled date as a peer metric. When price is unavailable, state that in the performance column and omit only the unavailable price-window row; keep latest mention visible.

### Evidence and interaction

- Stock and account links must open independent `:target` Profile layers and support deep links, browser Back, keyboard access, and 320–1440 px layouts.
- Default stock evidence must show at most the newest explicit view per opinion account. Put older explicit views, context, and signal activity in separately counted disclosure sections.
- Preserve every original-post URL and original text.
- Do not depend on JavaScript to reveal the report body.

### Account Profile hierarchy

Every tracked account has an internal `#account-{blogger_id}` Profile built only from the existing static report records and manually reviewed presentation copy. Data refreshes must not require a crawler, extractor, database-schema, manifest, price, entity, scheduler, or consensus change.

Order every account Profile as follows:

1. **Identity** — real X avatar, display name, handle, report role, and X link.
2. **28-day decision view** — for an opinion account, top three net-bullish and top three net-bearish entities using `bullish explicit records - bearish explicit records`; for a signal account, top three entities by activity count. Put a compact 28-day summary beside the board on desktop.
3. **Stable introduction** — one human-reviewed paragraph below the board, with two to four visible source links and a review date. Original posts, handles, and company names remain untranslated.
4. **Recurring stated views** — opinion accounts only. Group existing original `reasons` by entity and explicit direction. Never generate, merge, or rewrite a viewpoint sentence.
5. **Clear disagreement** — opinion accounts only. Show an entity only when the account's net direction is bullish or bearish and at least one other opinion account has the actual opposite net direction. Absence, neutral, tie, and signal activity are not disagreement.
6. **Tracked-post archive** — last on the page and collapsed by default. Include every record in the rolling 28-day window. Opinion accounts classify posts as explicit bullish, explicit bearish, explicit neutral, or context/comparison/quote/other, then by entity. Signal accounts classify directly by related entity without stance labels.

Ticker cards in an account board must not show reasons. Signal accounts must never receive stance boards, recurring-view sections, or disagreement sections. For `DJTRadar`, transaction activity remains attributed to Trump or the cited disclosure.

## Release validation

Run after every dashboard generation:

```powershell
python scripts/validate_dashboard.py <report.html> --browser required --expected-avatars 10
```

Fail delivery when any of these occur:

- an expected avatar is missing, blank, overridden by CSS, or repeated as embedded data;
- a stock link has no Profile target, a legacy stock `<details>` target remains, or report content depends on JavaScript;
- the performance cell contains a date, or price-window and latest-mention dates are not separated and labeled;
- a 320, 768, or 1440 px viewport has page-level horizontal overflow;
- required source attribution, original-post URLs, or opinion/signal separation is structurally broken.
- an account Profile lacks its board-and-summary overview, stable sourced introduction, or collapsed full-window post archive;
- an account board shows reasons, a signal account receives stance-derived modules, or the archive truncates the current 28-day records.
