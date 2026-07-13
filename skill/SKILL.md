---
name: x-traders-consensus
description: "Tracks the public X (Twitter) posts of 10 stock-tracking accounts: 7 independent opinion accounts whose stance, stated reasons, and mention frequency per ticker are compared for consensus, plus 3 parallel independent-signal accounts for options flow, breaking news, and Trump's disclosed trades. Generates interactive daily, weekly, and monthly HTML dashboards with a 7-account opinion consensus, original-post links, and separate signal activity. Use for stock/company reports, thesis history, latest stance, agreement or disagreement, signal activity, mention tracking, or dashboards about these accounts. Never use for investment advice."
---

# Top 10 X Stock Traders Tracker

---

## ⚠️ COMPLIANCE — READ BEFORE ANYTHING ELSE

This SKILL is a **blogger-tracking tool**, NOT a stock-analysis or investment-advice tool. It reports what tracked accounts said, never an independent market view.

**Mandatory rules for every response:**

1. Every output (HTML dashboard or text answer) MUST include this disclaimer, or a faithful translation in the user's language:
   > This is an aggregation of public posts from tracked X accounts, summarized automatically by AI. It may contain errors or omissions and is not guaranteed accurate — always refer to the original posts and verify independently. This does not constitute investment advice of any kind.

2. NEVER say "you should buy/sell", "this stock will go up/down", or anything that constitutes investment advice. Frame everything as: "{blogger} expressed…", "{blogger} mentioned…", "{blogger}'s stance is…". When reporting consensus, frame as "N of 7 tracked analysts are net-bullish on {ticker}", never as a recommendation.

3. NEVER use analyst/trader role labels for a blogger. Use "stance" (bullish/bearish/neutral), not "recommendation" or "rating".

4. A blogger's gender is not assumed. In Chinese, use the gender-neutral pronoun (其/qi) instead of gendered pronouns unless the blogger has stated otherwise. In English use "the blogger" or "they".

5. Metaphors, quotes of third-party views, and hypothetical discussions do NOT count as stance. If `mention_type` is `background`, `comparison`, or `quote_or_other`, do not report it as the blogger's own opinion.

6. Preserve the original language of tweets, reasons, company names, industry labels, dates, numbers, and currency symbols — never translate these.

7. Every claim about "who said what" must be traceable to a specific `blogger_id` and `url`. Never blend multiple bloggers' reasoning into one unattributed statement — when discussing one blogger's thesis, filter to their mentions only (see Mode B, `--blogger` flag); when discussing consensus, always show the per-blogger breakdown, not just an aggregate number.

8. **Signal-type separation (roster mix rule).** Only the 7 accounts with `signal_type == "opinion"` in `config/bloggers.json` express a personal stance — their mentions feed the bullish/bearish consensus count ("N of 7"). The other 3 accounts are independent-signal sources, NOT opinion-havers:
   - `@unusual_whales` (`signal_type: "flow"`) reports options-flow/dark-pool activity — a market data signal, not the account's personal view.
   - `@StockMKTNewz` (`signal_type: "news"`) reports breaking news headlines — factual events, not the account's personal view.
   - `@DJTRadar` (`signal_type: "disclosure"`) reports **Trump's** disclosed trades — attribute any trade mentioned to "Trump", never to DJTRadar's own opinion.
   Never fold these 3 accounts' mentions into the "N of 7" opinion consensus, and never use language implying their activity *validates* or *confirms* the analyst consensus (banned words: "confirms", "validates", "reinforces", 中文如"共振"/"确认"/"印证"). When presenting them together with the analyst consensus (e.g. a ticker detail view), state them as separate, parallel facts only — e.g. "4 of 7 analysts net-bullish · unusual options flow detected (bullish-leaning) · breaking news today · no Trump trade on record" — and let the user draw their own conclusion. This applies to both dashboard rendering and any Q&A synthesis.

---

## Environment Variables

| Variable | Required | Purpose |
|---|---|---|
| `GITHUB_TOKEN` | No | Optional GitHub token for higher API rate limits; the data repository is public. |

**Script location**: All scripts (`serenity_render.py`, `analyze_stock.py`) are in the `scripts/` subdirectory of this SKILL's installation path. Determine this path at the start of execution:

```python
import os
SKILL_SCRIPTS_DIR = os.path.join(os.path.dirname(SKILL_MD_PATH), "scripts")
```

Do NOT guess this path — list the SKILL directory to confirm `scripts/serenity_render.py` exists before calling it.

**Maintainer:** `hunterhigh`. The package is ready for marketplace-specific pricing decisions, but this Skill does not publish or charge users itself.

## Codex and GPT Compatibility

This is a standard `SKILL.md` package with `agents/openai.yaml`, so Codex can discover and invoke it directly after installation. It also works with GPT-based agents that can read local files and run the bundled Python scripts; a chat-only GPT without file and shell tools cannot generate the dashboard itself. No model-specific instruction format is required for reports or dashboards.

The background extractor is provider-selectable: retain `ANTHROPIC_API_KEY` for the existing Sonnet path, or set `OPENAI_API_KEY` and use `--provider openai` with a Responses API model such as `gpt-5.6-luna`. A ChatGPT/Codex subscription is not an API credential; scheduled refreshes require the corresponding provider API key.

---

## Data Access (Shared by All Modes)

Every user message begins by checking the remote manifest before using any local files. Do this even if data was downloaded earlier in the same conversation a few minutes ago. If the remote manifest changed, download fresh data before answering. All modes then read from the same verified local copy for the rest of that single request.

Cache rule:
- New user message = fetch remote manifest again.
- Same request, multiple internal steps = reuse the verified local copy.
- Never rely on local file age, previous conversation state, or a prior answer as proof that data is current.

### Step 1 — Check freshness and decide whether to download

```python
import requests, json, os, tempfile

TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO = "hunterhigh/10-top-x-semiconductor-tracker"
RAW = f"https://raw.githubusercontent.com/{REPO}/main"
HEADERS = {"Authorization": f"token {TOKEN}"} if TOKEN else {}

WORK = os.path.join(tempfile.gettempdir(), "x-traders-work")
DB = os.path.join(WORK, "db")
CONFIG_PATH = os.path.join(WORK, "bloggers.json")
LOCAL_MANIFEST = os.path.join(DB, "manifest.json")

response = requests.get(f"{RAW}/data/db/manifest.json", headers=HEADERS, timeout=30)
response.raise_for_status()
manifest = response.json()
remote_gen = manifest["generated_at"]

need_download = True
if os.path.exists(LOCAL_MANIFEST):
    local = json.loads(open(LOCAL_MANIFEST, encoding="utf-8").read())
    if local.get("generated_at") == remote_gen:
        need_download = False
```

Tell the user (when relevant): data covers `manifest["tickers"]` stocks, `manifest["tracked_bloggers"]` tracked accounts, latest data through `manifest["date_range"][1]`, last updated `manifest["generated_at"]`. `manifest["mentions_by_blogger"]` gives a per-account mention breakdown if useful context.

### Step 2 — Download data (only if needed)

Skip this step only if `need_download` is False after the remote manifest check for the current user message.

```python
if need_download:
    import zipfile, io, shutil

    if os.path.exists(WORK):
        shutil.rmtree(WORK)
    os.makedirs(os.path.join(DB, "stocks"), exist_ok=True)

    r = requests.get(
        f"https://api.github.com/repos/{REPO}/zipball/main",
        headers={"Authorization": f"token {TOKEN}"},
    )
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))

    # Extract data/db/ AND config/bloggers.json (skip raw_tweets, extracted, per-blogger scripts)
    prefix = z.namelist()[0].split("/")[0]
    for name in z.namelist():
        rel = name[len(prefix) + 1:]
        if rel == "config/bloggers.json":
            target = CONFIG_PATH
        elif rel.startswith("data/db/"):
            target = os.path.join(DB, rel[len("data/db/"):])
        else:
            continue
        if name.endswith("/"):
            os.makedirs(target, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(z.read(name))
```

After this step: stock data is at `{DB}/stocks/*.json` (SHARED across all 10 bloggers — see Stock JSON Schema below), and the tracked-blogger roster is at `{CONFIG_PATH}`.

**Note**: The LLM does NOT need to read the stock JSON files into context. `serenity_render.py` and `analyze_stock.py` read them directly via Python — the data flows through their process, not through the LLM's context window.

---

## Current Date and "Today" Rules

For every user message, determine the current real date/time from the active runtime environment, not from prior conversation history.

```python
from datetime import datetime
from zoneinfo import ZoneInfo

NOW_ET = datetime.now(ZoneInfo("America/New_York"))
TODAY_ET = NOW_ET.date().isoformat()
```

Then compare `TODAY_ET` with `manifest["date_range"][1]`:
- If `TODAY_ET` is within the available data range, use `TODAY_ET`.
- If newer than the latest available date, tell the user the latest available ET date and use that.
- Always state the exact ET date used when answering time-sensitive requests.

---

## Intent Detection

**Step 1 — Blogger selection.** Before choosing a mode, determine WHO the user is asking about:

- User names a specific tracked account (handle, display name, or unambiguous nickname — e.g. "Serenity", "@zephyr_z9", "Jason's Chips") → `BLOGGER_ID` = that account's `id` from `config/bloggers.json`.
- User asks a general/comparative question without naming one account ("who's bullish on NVDA", "dashboard", "today's report", "consensus", "which traders agree on...") → `BLOGGER_ID = "all"` (cross-tracker mode).
- User names a blogger ambiguously (partial match, or a name matching >1 tracked account) → ask which one they mean; list the matching display names.
- If the user's request never mentions or implies a specific blogger, default to `BLOGGER_ID = "all"`.

**Step 2 — Mode.**

**→ Dashboard mode** (default): user asks for a report, dashboard, overview, or "run the tracker".
Examples: "show me the dashboard", "latest report", "what's new today", "Serenity's dashboard", "consensus view".

**→ Q&A mode**: user asks about a specific stock or company.
Examples: "what does Serenity think about NVDA?", "SIVE opinion history", "who's bullish on AAOI", "any bearish calls on IREN?".

**→ General analysis mode**: broad questions about focus areas, cross-stock comparisons, industry breakdown, or anything not about one specific stock.
Examples: "what industries does Zephyr focus on?", "which stocks turned bearish recently across trackers?", "compare SIVE and AXTI".
Write and execute Python code against `{DB}/stocks/*.json` directly for these. Always include the compliance disclaimer.

If ambiguous, default to Q&A if a stock name/ticker is mentioned, otherwise Dashboard.

---

## Mode A: Dashboard Generation

Generates a self-contained interactive HTML file (Daily / Weekly / Monthly views + per-stock detail pages — no Quarterly view; deliberately cut to keep the backfill window to 30 days). When `BLOGGER_ID == "all"`, the dashboard also gets a "Consensus" tab showing today's and this week's most agreed-upon tickers **among the 7 opinion-type analysts only** (`signal_type == "opinion"` in `config/bloggers.json`) — the 3 independent-signal accounts (unusual_whales/StockMKTNewz/DJTRadar) never count toward this tab's numbers, per the signal-type separation rule in Compliance. Single-blogger dashboards (`BLOGGER_ID` = a specific account) do NOT show this tab — a consensus-surfaced ticker that blogger never mentioned would have no detail page to open, so it's intentionally scoped to `all` mode only. This is why Intent Detection defaults ambiguous/general "dashboard" requests to `BLOGGER_ID = "all"`: that's what makes consensus auto-surfaced by default.

### A1 — Determine language

- Detect user's language from conversation.
- Built-in dashboard languages: `en`, `zh` (Simplified Chinese), `zh-Hant` (Traditional Chinese).
- Traditional Chinese request (繁体/繁體/Traditional Chinese/zh-TW/zh-HK) → `LANG = "zh-Hant"`.
- Simplified Chinese request → `LANG = "zh"`.
- English → `LANG = "en"`.
- Other language → create a temporary dashboard language JSON from the English strings (see A2.1), set `LANG` to that code, and set `SERENITY_LANG_FILE`.
- Preserve placeholders such as `{pfx}`, `{n}`, `{date}`, `{link}`, `{handle}`, HTML tags, arrows, and ticker symbols exactly when translating UI strings.

### A2 — Run render

Set environment variables (do NOT use CLI flags with values that could be misparsed as positional args):

```python
import subprocess
os.environ["SERENITY_DB"] = DB
os.environ["SERENITY_CONFIG"] = CONFIG_PATH
# For non-built-in languages, also set:
# os.environ["SERENITY_LANG_FILE"] = LANG_FILE
result = subprocess.run(
    ["python", os.path.join(SKILL_SCRIPTS_DIR, "serenity_render.py"), DATE,
     "--lang", LANG, "--blogger", BLOGGER_ID],
    cwd=WORK, capture_output=True, text=True
)
```

- `DATE`: target date `YYYY-MM-DD`. For "today" requests use `TODAY_ET`, compared against `manifest["date_range"][1]` as above. For "latest report" without a specific date, use the latest available date.
- `BLOGGER_ID`: from Intent Detection Step 1 — either a specific tracked account's `id`, or `"all"`.
- Output: `{BLOGGER_ID or 'consensus'}-tracker-{DATE}[-{LANG}].html` in the WORK directory (`--blogger all` produces a `consensus-tracker-...` file).

### A2.1 — Temporary language JSON for non-built-in languages

Same as before: read `STR["en"]` keys from `scripts/serenity_render.py`, translate values, write a UTF-8 JSON to `{WORK}/x-traders-lang-{code}.json`, set `SERENITY_LANG_FILE`, run with `--lang {code}`. RTL languages (ar/fa/he/ur) get their direction set automatically by the render script.

### A3 — Deliver

Return the HTML file. Brief note:
- What date the report covers, and whose view (a specific blogger's name, or "all 10 tracked accounts / 7-analyst consensus view")
- How many stocks are tracked, and (for `all` mode) how many tickers have cross-tracker agreement today
- Users can click any stock for a detail view (price chart + stance history + per-tracker breakdown when relevant + original tweet links)
- Data update schedule note based on `manifest["generated_at"]`
- **Include compliance disclaimer**

### A4 — AI Daily Analysis

After delivering the HTML, provide an AI-synthesized analysis, scoped to `BLOGGER_ID`:

1. Write and execute Python to extract mentions where `date == DAY` (and separately `date == DAY-1` for context) from `{DB}/stocks/*.json`. If `BLOGGER_ID != "all"`, filter to `m["blogger_id"] == BLOGGER_ID` first.
2. **Single-blogger mode**: synthesize that account's DAY mentions — key events/catalysts, stance positions/changes, new stocks, one-sentence conclusion, brief DAY-1 continuity note if relevant.
3. **All/consensus mode**: synthesize across the 7 opinion-type analysts only — which tickers multiple analysts converged on today (bullish or bearish), any notable divergence (one bullish, one bearish, same ticker, same day — call this out explicitly, it's the most interesting signal this feature offers), one-sentence conclusion. If any of the 3 independent-signal accounts (flow/news/disclosure) also posted about the same ticker that day, mention it as a separate parallel fact (see Compliance rule 8) — never as something that "confirms" the analyst consensus.
4. **Exception — low data**: if DAY has fewer than 3 explicit_stance mentions in scope, expand to cover DAY-1 + DAY together and say so.
5. State at the top: "All dates use US Eastern Time (ET, UTC-4). This analysis covers [DAY] ET[, {blogger display name}'s posts | across the 7 tracked analysts]."
6. End with the compliance disclaimer.

---

## Mode B: Stock Q&A

The user asks about a specific stock, optionally scoped to one blogger. Run the analysis script, then synthesize a narrative.

### B1 — Identify the ticker (and blogger scope)

- Ticker symbol given directly → use it.
- Company name given → check `index.json`:

```python
index = json.loads(open(os.path.join(DB, "index.json"), encoding="utf-8").read())
matches = [k for k, v in index.items() if query.lower() in (v.get("company") or "").lower()]
```

- Do not decide public/private status from model memory — check the tracked database only.
- Not found: "This stock or company is not currently tracked. The tracker covers {N} tickers discussed across the 10 tracked accounts."
- Multiple ticker matches: ask user to clarify.
- **Blogger scope** comes from Intent Detection Step 1. If a specific blogger was named ("what does Serenity think..."), scope the analysis to them (B2 below). If not scoped (e.g. "who's bullish on NVDA", "opinions on SIVE"), run the cross-tracker view (Mode D) instead of Mode B, or run Mode B unscoped (all bloggers blended) ONLY when the user explicitly wants one combined narrative rather than a per-tracker breakdown — prefer Mode D whenever the question is inherently comparative.

### B2 — Run analysis script (scoped to one blogger)

```python
import subprocess
cmd = ["python", os.path.join(SKILL_SCRIPTS_DIR, "analyze_stock.py"),
       os.path.join(DB, "stocks", f"{TICKER}.json")]
if BLOGGER_ID != "all":
    cmd += ["--blogger", BLOGGER_ID]
result = subprocess.run(cmd, capture_output=True, text=True)
analysis = json.loads(result.stdout)
```

**Important**: `stocks/{TICKER}.json` is a file SHARED across all 10 bloggers — every mention carries `blogger_id`. Always pass `--blogger` when the user is asking about one specific account's view; omitting it blends every tracked account's reasoning into one narrative, which misattributes views to no one in particular. The script outputs structured JSON to stdout (~2-3 KB) — this is the ONLY data the LLM needs in context. Do NOT read the raw stock JSON directly.

### B3 — LLM narrative synthesis

Same three-module structure as before, now explicitly attributed to the scoped blogger:

---

#### MODULE 1: Latest Opinion (1 item)

Use `latest_stance` from the script output. Present: date + stance, original tweet text (untranslated), link, and **which tracked account** said it.

```
━━ Latest Opinion ({blogger display name}) ━━
[{date}] {stance}: "{text}" → [Original post](url)
```

---

#### MODULE 2: Narrative Arc (core value — LLM synthesis)

3–5 sentence macro-level narrative of how **this one blogger** is constructing their thesis and how it evolved. Read `arc_phases`, `transitions`, `bull_reasons`, `bear_reasons`, `frequency_trend`, `conviction_trend` from the (blogger-scoped) script output. Same synthesis method as before: identify discovery/conviction-building/expansion/wavering phases, spot pivot points, write as one coherent paragraph with inline quotes, state the tracking period and stance count.

---

#### MODULE 3: Key Opinions (3 items)

Same selection method as before (thesis origin/transition, expansion/catalyst, high-conviction/recent) — all attributed to the one scoped blogger, with date + text + link + one-sentence "why it matters".

---

#### RISKS (if any)

Same as before, from `risk_reasons`/`bear_reasons` in the scoped output. Omit the section entirely if empty — never say "no risks".

---

#### DISCLAIMER (mandatory, always last)

```
━━━━━━━━━
⚠️ The above is an aggregation of {blogger}'s public posts,
summarized automatically by AI. It may contain errors or omissions.
Always refer to the original posts and verify independently.
This does not constitute investment advice of any kind.
```

### B4 — Follow-up handling

Same manifest re-check discipline as before for every new user message. Additional follow-ups specific to multi-blogger:
- "What about {other blogger}?" → re-run B1/B2 with the new `BLOGGER_ID`, same ticker.
- "Show me everyone's view" / "compare trackers on this" → switch to Mode D.

---

## Mode D: Cross-Tracker Consensus Q&A

The user asks a comparative question about one ticker across the tracked accounts ("who's bullish on NVDA", "any disagreement on SIVE", "consensus on IREN this week").

### D1 — Run analysis unscoped

```python
result = subprocess.run(
    ["python", os.path.join(SKILL_SCRIPTS_DIR, "analyze_stock.py"),
     os.path.join(DB, "stocks", f"{TICKER}.json")],
    capture_output=True, text=True)
analysis = json.loads(result.stdout)
```

Then, separately, group `mentions[]` from the raw stock JSON (or re-derive from `analysis`) by `blogger_id`. Look up each `blogger_id`'s `signal_type` in `config/bloggers.json`:
- For the 7 accounts with `signal_type == "opinion"`: compute each one's own net stance (bullish/bearish/neutral, by count of `explicit_stance` mentions) within the relevant window (default: all-time, or the window the user asked about). This is the consensus math.
- For the 3 independent-signal accounts (`flow`/`news`/`disclosure`): do NOT compute a "stance" for them. Just note whether they posted about this ticker in the window, and briefly what (e.g. "unusual_whales flagged options flow on {date}", "DJTRadar recorded a Trump trade on {date}"). Report these as a separate line, not folded into the bull/bear counts.

### D2 — Present the breakdown

```
━━ Cross-Tracker Consensus: {TICKER} ━━
{n_bull} of {n_scored} tracked analysts net-bullish, {n_bear} net-bearish, {n_neutral_or_untracked} no clear stance.

Bullish: {display names}
Bearish: {display names}
No stance / not covered: {display names, if relevant}

Notable divergence: {one sentence if analysts actively disagree — e.g. "Serenity and Zephyr are bullish on the CPO demand thesis, while Jukan flagged valuation risk on the same day."}

Other signals (informational, not part of the consensus count): {e.g. "unusual_whales flagged bullish-leaning options flow on {date} · StockMKTNewz posted related news on {date} · no Trump trade on record" — omit any that had no activity in the window}
```

Never resolve a disagreement into a single verdict ("the real answer is X") — the point of this mode is to surface disagreement between analysts, not adjudicate it. Never phrase the "Other signals" line as confirming or reinforcing the analyst consensus (see Compliance rule 8) — it is parallel information, presented so the user can judge for themselves.

### D3 — Disclaimer

Same mandatory disclaimer as Mode B, generalized to "tracked accounts" rather than one blogger's name.

---

## Stock JSON Schema Reference

Each file in `data/db/stocks/{TICKER}.json` — SHARED across all 10 tracked bloggers:

```
ticker, company, industry, exchange, currency
first_mention, last_mention, total_mentions              — across ALL tracked bloggers
total_mentions_by_blogger                                — {blogger_id: count}, window-independent
price_series[]     — {date, close}   (blogger-agnostic, one shared price history)
price_status       — "ok" | "partial" | "unavailable" | "unverified_symbol"
price_reason       — required when price_status is not "ok"; provider, mapping, no-data, or freshness explanation
mentions[]         — each has:
    .tweet_id
    .blogger_id     — which tracked account this mention is from (see config/bloggers.json)
    .date           — YYYY-MM-DD (US Eastern)
    .stance         — "bullish" | "bearish" | "neutral" | null
    .mention_type   — "explicit_stance" | "background" | "comparison" | "quote_or_other"
    .reasons[]      — the blogger's stated reasons (original language)
    .is_risk        — boolean
    .text           — tweet text (original language, do NOT translate)
    .url            — link to original tweet
    .engagement     — {likes, reposts, replies, views}
```

Only `explicit_stance` counts as a blogger expressing their own opinion, and only for `blogger_id`s whose `signal_type` is `"opinion"` — see Compliance rule 8. All other mention_types are informational context only. `config/bloggers.json` maps `blogger_id` → `{handle, display_name, x_url, avatar_letter, color, signal_type}`.

---

## Language Rules

- **Dashboard built-ins**: `en`, `zh` (Simplified), `zh-Hant` (Traditional).
- **Other languages**: temporary language JSON from English UI strings + `SERENITY_LANG_FILE`, matching language code.
- **Fallback**: English if detection fails or translated strings are incomplete/invalid.
- **Never translate**: tweet text, reasons, company names, industry labels, dates, numbers, currency symbols, blogger handles/display names.
- **Q&A responses**: respond in the user's language, but keep the above items in original language, noting the original is preserved.
- **count_unit**: Chinese (`zh`/`zh-Hant`) = "次"; English = empty string. Other languages: translate naturally or omit.

---

## Error Handling

- **GitHub API fails**: tell user data source is temporarily unavailable, suggest trying later. Do not expose token or repo URL.
- **Stock not found**: "This stock is not currently tracked. The tracker follows 7 independent analysts plus 3 independent-signal accounts (options flow / news / political disclosure) across a range of sectors — {list a few tracked names/directions if helpful}, not limited to any one niche."
- **Named blogger not found / ambiguous**: list the tracked accounts (from `config/bloggers.json` display names) and ask the user to pick.
- **Render script fails**: verify data was downloaded correctly (`stocks/` should contain JSON files, `config/bloggers.json` should exist). If issue persists, offer Q&A mode as fallback.
- **Stale data**: if `manifest["generated_at"]` is >48 hours old, warn user data may not reflect the most recent posts.
- **Few mentions**: if a stock (optionally scoped to one blogger) has <3 explicit stances, `analyze_stock.py` returns minimal/`no_explicit_stance` data. Tell the user: "This stock has only brief or background mentions — not enough data for a thesis analysis. Here's what's available: [present raw latest_mention]."
- **One blogger has no data yet** (e.g. mid-backfill): if `total_mentions_by_blogger` is missing or 0 for the requested `BLOGGER_ID`, say so plainly rather than presenting an empty dashboard as if it were complete.
