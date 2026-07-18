# Dashboard aggregation rules

This document is the renderer-facing contract. The renderer must consume normalized, traceable direction fields; it must never infer stance from `text`, `reasons[]`, labels, or CSS classes.

## Rolling windows

- Report cutoff: `D`, interpreted as an Eastern Time calendar date.
- Daily: `[D, D]`.
- Weekly: `[D-6, D]`.
- Previous weekly comparison: `[D-13, D-7]`.
- Monthly: `[D-27, D]`, exactly 28 calendar days. It is not a natural month.

All ranges are closed intervals. Price returns display the actual available trading dates selected inside the requested window.

## Deduplication and source binding

- Account count: distinct `blogger_id` inside the window.
- Post count: unique original-post URL; use stable `tweet_id` only when URL is absent.
- Direction and reason must come from the same structured mention.
- Card reasons use `reasons[]`; expanded evidence uses original `text`; both link to the same mention URL.
- A missing signal in the current window means only “no current same-direction signal”. It never means the account reversed.

## Tracked-person daily stock lists

The backend derives three distinct-stock lists for each tracked person in the daily window, not post or signal-record counts:

- bullish stocks: distinct stocks with at least one normalized bullish record from that account today;
- bearish stocks: distinct stocks with at least one normalized bearish record from that account today;
- neutral stocks: distinct stocks mentioned today with neither bullish nor bearish records.

Multiple same-direction posts about one stock count once. A stock with both bullish and bearish records appears in both bullish and bearish lists and does not enter neutral. The primary card does not render the old numeric count blocks and does not render neutral stocks. It directly shows up to three bullish symbols and up to three bearish symbols; when a direction has more than three symbols, append `+N`, where `N = total - 3`. Neutral remains available to the personal view and drilldown. Clicking a symbol opens the in-file stock drilldown, while clicking the rest of the person card opens the corresponding personal-view panel with all symbols and evidence.

## Daily and weekly main sections

Assign each stock to at most one section:

1. `disagreement` / 存在多空分歧: at least one bullish account and at least one bearish account.
2. `shared_bullish` / 明确共同看多: no bearish account and at least two distinct bullish accounts.
3. `shared_bearish` / 明确共同看空: no bullish account and at least two distinct bearish accounts.

Display order is 明确共同看多、明确共同看空、存在多空分歧. No-direction accounts do not block either shared-direction classification. There is no `other_notable` section in the daily or weekly primary view.

## Weekly change lists

Compare the previous and current rolling seven-day windows. Assign a stock to at most one list, in this priority:

1. `reversal_or_disagreement`: main direction reversed with at least two directional accounts in both windows, or current window newly contains both bullish and bearish accounts.
2. `new_multi_bullish`: current bullish account count first reaches at least three while previous count was below three.
3. `consensus_strength`: a bullish or bearish account count changes and either window has at least two accounts in that direction.

When both direction counts change in the third case, select exactly one `focus_direction`: largest absolute account delta, then larger maximum window count, then larger current count, then bullish as the final stable tie-break.

Approved labels:

- 新形成多人看多
- 看多共识增强 / 看多信号人数减少
- 看空共识增强 / 看空信号人数减少
- 新出现分歧 / 主方向反转

Display rules:

- New multi-bullish: show bullish accounts only; if current bearish accounts exist, add `同时有X人看空` with those current accounts.
- Consensus strength: show only `focus_direction`.
- Reversal/disagreement: show both bullish and bearish account groups.
- Current bullish account, whether retained or newly added: green stance style.
- Current bearish account, whether retained or newly added: red stance style.
- A newly added account keeps a small `+`, but its color is determined only by bullish/bearish direction.
- Previous only: light-gray removed style with `−`. This means no current same-direction signal, not a bearish/bullish reversal.

## Monthly consensus tag

Use effective bullish and bearish signal counts only. Let `share = bullish / (bullish + bearish)`:

- no effective direction: 无明确方向
- `share >= 0.60`: 偏多
- `share <= 0.40`: 偏空
- otherwise: 多空分歧

This label is an internal derived field only. The confirmed monthly table does not display a consensus-status column. Its visible column order is: 股票、立场占比、涨跌幅、提及次数、看多次数、看空次数、无方向、参与账号、详情.

## Person stance inside any window

Each tracked person belongs to exactly one state:

- bullish and bearish both occurred: 多空均有
- bullish only: 仅看多
- bearish only: 仅看空
- mentions exist but neither direction occurred: 无方向
- no related mention: 未提及

Counts across these five states must always equal the number of tracked accounts.

## Stock drilldown shared window and person statistics

The overview metrics, person-stance distribution and primary 10-person table share one selected window: `day`, rolling 7 calendar days, or rolling 28 calendar days. The drilldown defaults to `day` / 今日. Switching the window must recompute all three blocks and the prominent window price return together. The approximately-28-day price chart remains fixed and does not shrink when the statistics window changes.

The person table reports public-content statistics only and must never infer a position, purchase, sale, holding period, return, hit rate, or investment performance.

Visible columns: 人物、所选窗口提及、立场构成、立场一致度、最近观点与理由、展开全部.

- 所选窗口提及: count of related structured records inside the selected window.
- 立场构成: bullish, no-direction and bearish record counts for that person and stock, displayed in that exact left-to-right order.
- 一致度: `max(bullish, bearish) / (bullish + bearish)`; no-direction records are excluded. If there is no directional record, render `— / 无方向信号`. Display labels are deterministic: `>=80%` 稳定, `>=60% and <80%` 较稳定, `<60%` 多空反复.
- 最近观点: date and direction of the single latest related structured record. `多空均有` is an aggregate window state and must never be shown as the latest direction. Show a reversal marker separately only when ordered directional records actually change from bullish to bearish or bearish to bullish.
- 最近理由: `reasons[]` from the latest related structured record.
- 展开全部: chronological evidence rows limited to the selected window, with date, structured direction, original post text and the matching `mention.url`.
