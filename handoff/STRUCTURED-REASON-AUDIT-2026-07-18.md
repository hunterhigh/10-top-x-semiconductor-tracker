# Structured-reason audit — 2026-07-18

## Finding

An empty `reasons[]` is not, by itself, an extraction failure. The extraction
contract classifies the mention type first and says that non-`explicit_stance`
tickers normally have an empty reason list. This preserves the distinction
between an author's own investment view and a background mention, comparison,
or quotation.

The previous stock-detail adapter ignored that contract and rendered every
empty value as `暂无结构化理由`. It also omitted the approved author avatar/name
link from chart event rows. Both are renderer defects, not evidence that the
history was broadly unstructured.

## Current baseline measurement

Measured from `data/db/stocks/*.json` on the 2026-07-18 baseline. Counts below
are instrument-mention rows; source-post counts are deduplicated by tweet ID.

| Category | Missing-reason rows | Missing-reason source posts | Interpretation |
| --- | ---: | ---: | --- |
| `neutral/background` | 8,212 | 3,433 | expected normal context mentions |
| `neutral/comparison` | 1,108 | 741 | expected non-position comparisons |
| `neutral/quote_or_other` | 538 | 364 | expected quotation/other mentions |
| `bullish/background or comparison` | 72 | 32 | non-position taxonomy rows; do not infer a thesis |
| `bearish/background or comparison` | 5 | 4 | non-position taxonomy rows; do not infer a thesis |
| `explicit_stance`, all stances | 143 | 68 | genuine, narrow structured-reason repair candidate |

For MU on 2026-07-01, for example, the entries previously shown as blank are
stored as `background` with the original X text intact. The same day also has
explicit bullish content with several structured reasons. This demonstrates
that the old blank-card behaviour is not a missing raw-post problem.

## Rendering rule

1. Show `reasons[0]` if present.
2. For an `explicit_stance` with no reason, show `未提取结构化理由` so the real
   quality defect remains visible.
3. For `background`, `comparison`, and `quote_or_other` with no reason, show
   a truncated original-post excerpt and a matching mention-type label. Never
   synthesize a reason.

## Backfill decision

Do **not** run a historical crawl or a blanket re-extraction. Raw source posts
and their evidence URLs already exist, and broad re-extraction would risk
changing established classification history without solving the presentation
defect.

After this renderer repair is accepted, the only recommended data action is a
separate, auditable targeted re-extraction of the 68 unique source posts whose
`mention_type` is `explicit_stance` and whose `reasons[]` is empty. It should
preserve tweet ID, URL, timestamp, ticker mapping, and mention type; only a
validated reason may be filled. If a new extraction still finds no stated
rationale, the empty list remains correct and the UI continues to disclose it.
