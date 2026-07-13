#!/usr/bin/env python3
"""
extract.py — multi-blogger tracker, P2 step 2

Reads a single blogger's raw_tweets.json and uses a selectable Anthropic or
OpenAI model to turn each tweet
into structured, per-tweet investment signal (which tickers, their stance,
their reasons, their conviction, mention type), with audit fields so every
conclusion traces back to a tweet id.

Multi-blogger support: run once per --user (see config/bloggers.json). Each
blogger's raw/extracted stores live under data/bloggers/{user}/, and every
extracted record is tagged with blogger_id so build_db.py can merge multiple
bloggers' mentions into one shared per-ticker file (needed for the
cross-blogger consensus view) without losing attribution.

The system prompt is templated with the target blogger's handle/display name.
This is NOT role-play — it exists so the model can correctly attribute each
tweet's stance to its actual author (vs. something they're quoting, mocking,
or using as a comparison) and isn't a persona the model adopts.

Architecture (per project plan — TWO-STEP separation):
  fetch_tweets.py -> data/bloggers/{user}/raw_tweets.json
  extract.py      -> data/bloggers/{user}/extracted.json  (THIS: per-tweet signal)
  build_db.py     -> index.json + stocks/*.json   (aggregation across all bloggers, no LLM)

Key design decisions:
  - PER-TWEET output (not aggregated). Aggregation/timeline/frequency is
    build_db's job. We must keep per-tweet results so daily increments can be
    merged and aggregates recomputed without re-calling the LLM.
  - Grouped by conversation_id: all of this blogger's tweets in one
    conversation are sent together so the model can resolve "it" / pronouns
    from context. (We only have their tweets, not the other party's — the
    prompt says so.)
  - Ticker is captured as referred to (raw_mention) + a normalized symbol.
    Exchange/currency resolution ($SIVE -> SIVE.ST / SEK, (4092) -> 4092.T / JPY)
    is NOT done here — that's build_db's mapping-table job. We only identify
    "they are talking about SIVE / 4092 / Sivers".
  - Social chatter is allowed in and the model marks has_investment_content=false.
  - Resumable: writes results incrementally; re-running skips done tweet_ids.
  - Default model: Claude Sonnet (project decision — the API account in use doesn't
    have Opus access; Sonnet is ~5x cheaper). KNOWN RISK: Sonnet is more prone
    to misreading metaphor/irony as a real stance than Opus (this is why the
    original single-blogger project used Opus). Spot-check extracted stances
    per blogger more carefully than you would with Opus, especially for
    accounts with a sarcastic/ironic voice — see extract_all.py's --limit flag
    to sample a blogger's output before running the full backfill.

Auth: reads ANTHROPIC_API_KEY or OPENAI_API_KEY from environment.

Run:
    python extract.py --user aleabitoreddit --limit 50   # test on 50 tweets
    python extract.py --user aleabitoreddit               # full run (resumable)
    python extract.py --user zephyr_z9 --since 2026-04-01  # another blogger, windowed
    python extract.py --user zephyr_z9 --provider openai --model gpt-5.6-luna
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data"
CONFIG_PATH = SCRIPT_DIR.parent / "config" / "bloggers.json"

DEFAULT_USER = "aleabitoreddit"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"
MAX_TWEETS_PER_CALL = 8               # smaller groups -> shorter JSON output -> no truncation on long threads
RETRY = 3
REQUEST_PACING_SEC = 0.5              # polite pause between calls to avoid tripping limits
RATE_LIMIT_WAIT = 30                  # base seconds to wait when a 429 is hit (×attempt)


def blogger_paths(username: str) -> tuple[Path, Path]:
    """Return (raw_tweets_path, extracted_path) for a given blogger username."""
    d = DATA_DIR / "bloggers" / username
    return d / "raw_tweets.json", d / "extracted.json"


def blogger_display_name(username: str) -> str:
    """Look up display_name from config/bloggers.json; falls back to the handle."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        for b in cfg.get("bloggers", []):
            if b.get("id") == username:
                return b.get("display_name") or username
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return username


# --------------------------------------------------------------------------- prompt
# Templated per blogger. The handle/display name are injected ONLY so the
# model can correctly attribute each tweet's stance to its actual author
# (vs. something being quoted, mocked, or used as a comparison) — this is a
# disambiguation aid, not a persona for the model to adopt.
SYSTEM_PROMPT_TEMPLATE = """You analyze the public X (Twitter) posts of a single trader (handle @{handle}, alias "{display_name}"). Your ONLY job is to report what THEY say about stocks — never your own market view, never advice, never price predictions.

You will receive THEIR tweets from ONE conversation thread, in order. Some are their top-level posts; some are their replies. You do NOT see other people's tweets, only theirs — infer context from their own words.

For EACH tweet, extract a structured record. A tweet may mention several tickers, one, or none.

Identify tickers in ALL the forms they use:
- cashtags like $SIVE, $NVDA
- numeric codes like (4092) or 6324 (these are Tokyo-listed; keep the digits as the symbol)
- plain company names like "Sivers", "Soitec", "Lumentum", "Nippon Chemical"
- pronouns ("it", "they") ONLY when context makes the referent unambiguous within their own tweets

Do NOT resolve exchanges or currencies. Just identify which company/ticker they mean (e.g. symbol "SIVE", or "4092"). Normalization happens downstream.

For each ticker in a tweet, determine:
- mention_type: classify FIRST, before stance. One of:
    "explicit_stance" — they take a clear directional position ON THIS TICKER (bullish/bearish on it themselves, in their own voice)
    "background"      — the ticker is only scenery/context/demand-anchor/supply-chain mention; they are not taking a position on IT
    "comparison"      — the ticker is cited as an analogy, example, or yardstick ("like $AMC", "the next $LITE", "trades like $XYZ"), not a position on it
    "quote_or_other"  — the view belongs to someone else (they are quoting/paraphrasing another person, a reply echoing @someone), OR it is a hypothetical/conditional ("if TSLA switches suppliers, then..."), OR the language is non-English and you cannot be confident
- stance: "bullish" | "bearish" | "neutral".
    HARD RULE: stance may be "bullish" or "bearish" ONLY when mention_type == "explicit_stance".
    For "background", "comparison", and "quote_or_other", stance MUST be "neutral" — no exceptions, even if the surrounding sentence sounds positive or negative.
    When in doubt about whether they are really taking a position ON THIS TICKER, choose "neutral". Under-calling stance is much better than over-calling it.
- reasons: array of short phrases capturing THEIR stated rationale (bull reasons OR risks). Empty if none. Each reason is something they assert — must be prefixable with "they ...". For non-explicit_stance tickers, reasons should usually be empty.
- is_risk: true if the reason(s) are risks/cautions THEY name; false if they are bull points. If mixed or none, use false.
- conviction_signal: "high" | "medium" | "low" | null — only from THEIR wording ("highest conviction", "watching", "small position", "speculative"). null if they give no signal.
- company_name: the company's commonly known full name (e.g. "Sivers Semiconductors", "Tower Semiconductor", "Nvidia"). Extract from the post if mentioned; otherwise use your knowledge of the ticker symbol. null only if truly unknown.

WHY THIS MATTERS: a downstream system counts their bullish/bearish calls per ticker. If you label a ticker bullish/bearish when they were only using it as a comparison, as background, or were quoting someone, you create a false record of their position. Be conservative: a clear position in their own voice = explicit_stance; anything else = neutral.

CALIBRATION EXAMPLES (mention_type, stance):
- "I'm long $SIVE, the next $LITE" -> SIVE:(explicit_stance, bullish); LITE:(comparison, neutral)
- "$IREN is doing a $6B ATM, terrible dilution" -> IREN:(explicit_stance, bearish)
- "people stuck in $AMC know this feeling" -> AMC:(comparison, neutral)
- "$NVDA/$MRVL are the end demand for the whole chain" -> both:(background, neutral)
- "@user thinks $POET is great, but I prefer $AAOI" -> POET:(quote_or_other, neutral); AAOI:(explicit_stance, bullish)
- "if $TSLA uses a China supplier my $VPG thesis weakens" -> TSLA:(quote_or_other/hypothetical, neutral); VPG:(explicit_stance, bullish)

Also set per tweet:
- has_investment_content: true if the tweet expresses any view/reasoning/information about a stock or the market; false for pure social chatter (greetings, jokes, anime, travel, thanks) with no investment substance.

STRICT RULES:
- Report only what THEY state. If they don't give a reason, leave reasons empty — do not invent one.
- Never add your own judgment about whether a stock is good/bad.
- Verbatim evidence: for each reason, it must be grounded in their actual words.
- If a tweet is social chatter, set has_investment_content=false and tickers=[] (unless they genuinely name a stock in passing — then include it with mention_type="background").

Output ONLY valid JSON, no prose, no markdown fences. Schema:
{
  "results": [
    {
      "tweet_id": "<the id you were given>",
      "has_investment_content": true,
      "tickers": [
        {
          "raw_mention": "$SIVE",
          "symbol": "SIVE",
          "company_name": "Sivers Semiconductors",
          "stance": "bullish",
          "mention_type": "explicit_stance",
          "reasons": ["InP laser bottleneck play", "supplies POET/Ayar"],
          "is_risk": false,
          "conviction_signal": "high"
        }
      ],
      "confidence": 0.9
    }
  ]
}
Every input tweet_id MUST appear exactly once in results."""


def build_system_prompt(handle: str, display_name: str) -> str:
    # NOTE: deliberately NOT using str.format() — the template embeds a literal
    # JSON schema example full of {...} braces (the expected output format),
    # which .format() would misinterpret as more placeholders and crash on
    # (e.g. KeyError on the JSON's own "results" key). Plain .replace() only
    # touches the two placeholders we actually inject.
    return SYSTEM_PROMPT_TEMPLATE.replace('{handle}', handle).replace('{display_name}', display_name)


def build_user_message(convo_tweets):
    """Render one conversation's tweets (this blogger's only) as the user message."""
    lines = ["Here are their tweets from one conversation, in order:\n"]
    for t in convo_tweets:
        kind = t.get("kind")
        rt = t.get("in_reply_to_username")
        ctx = f" (reply to @{rt})" if kind == "reply" and rt else (" (self-thread)" if kind == "self_thread" else " (post)")
        text = (t.get("text") or "").strip()
        lines.append(f"[tweet_id={t['tweet_id']}{ctx}]\n{text}\n")
    lines.append("\nReturn the JSON described in the system prompt for exactly these tweet_ids.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- io helpers
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')   # Windows console defaults to GBK, which can't print → etc.


def log(m): print(m, flush=True)


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log(f"WARNING: {path.name} corrupt; ignoring.")
    return default


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def resolve_provider(requested: str) -> str:
    if requested != "auto":
        return requested
    # Keep the deployed Anthropic path stable until an OpenAI key is configured.
    return "openai" if os.environ.get("OPENAI_API_KEY", "").strip() else "anthropic"


def get_client(provider: str):
    if provider == "openai":
        try:
            from openai import OpenAI
        except ImportError:
            log("ERROR: the 'openai' package is not installed. Run: pip install openai")
            sys.exit(1)
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            log("ERROR: OPENAI_API_KEY is required when --provider openai.")
            sys.exit(1)
        log("Mode: OpenAI Responses API")
        return OpenAI(api_key=api_key)

    try:
        import anthropic
    except ImportError:
        log("ERROR: the 'anthropic' package is not installed.")
        log("Install it:  pip install anthropic")
        sys.exit(1)

    auth_token = (os.environ.get("ANTHROPIC_AUTH_TOKEN") or "").strip()
    api_key    = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    base_url   = (os.environ.get("ANTHROPIC_BASE_URL") or "").strip()

    # Decide the mode cleanly:
    #  - INTERNAL gateway  : requires BOTH a base_url AND an auth token (Bearer).
    #  - OFFICIAL endpoint : uses api_key (x-api-key), no base_url.
    # This prevents the failure where a leftover AUTH_TOKEN gets sent to the
    # official endpoint (or an api_key gets sent as Bearer) -> 401.
    use_internal = bool(base_url and auth_token)

    if use_internal:
        kwargs = {"auth_token": auth_token, "base_url": base_url}
        log(f"Mode: INTERNAL gateway")
        log(f"  base_url : {base_url}")
        log(f"  auth     : Authorization: Bearer (token starts '{auth_token[:6]}…')")
    else:
        if not api_key:
            log("ERROR: no usable credentials.")
            log("For OFFICIAL Anthropic API set ANTHROPIC_API_KEY (sk-ant-…) and DO NOT set ANTHROPIC_BASE_URL.")
            log("For the INTERNAL gateway set BOTH ANTHROPIC_AUTH_TOKEN and ANTHROPIC_BASE_URL.")
            if auth_token and not base_url:
                log("NOTE: ANTHROPIC_AUTH_TOKEN is set but ANTHROPIC_BASE_URL is empty —")
                log("      that token would be rejected by the official endpoint. Clear it or set the base_url.")
            sys.exit(1)
        kwargs = {"api_key": api_key}
        log("Mode: OFFICIAL Anthropic endpoint")
        log(f"  auth     : x-api-key (key starts '{api_key[:8]}…')")
        if auth_token:
            log("  WARNING: ANTHROPIC_AUTH_TOKEN is still set but ignored (no base_url). "
                "Clear it to avoid confusion: setx ANTHROPIC_AUTH_TOKEN \"\"")
    return anthropic.Anthropic(**kwargs)


def strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s.rsplit("```", 1)[0]
        # also drop a leading 'json'
        if s.lstrip().startswith("json"):
            s = s.lstrip()[4:]
    return s.strip()


def call_model(client, provider, model, system_prompt, convo_tweets):
    user_msg = build_user_message(convo_tweets)
    last_err = None
    for attempt in range(1, RETRY + 1):
        try:
            if provider == "openai":
                resp = client.responses.create(
                    model=model,
                    instructions=system_prompt,
                    input=user_msg,
                    max_output_tokens=8000,
                )
                raw = resp.output_text
            else:
                resp = client.messages.create(
                    model=model,
                    max_tokens=8000,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_msg}],
                )
                raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
            data = json.loads(strip_fences(raw))
            return data.get("results", [])
        except json.JSONDecodeError as e:
            last_err = f"JSON parse error: {e}"
            wait = 2 * attempt
        except Exception as e:
            last_err = f"API error: {e}"
            # 429 / rate limit: wait much longer so we don't re-trip the gateway ban
            is_rate = "429" in str(e) or "rate_limit" in str(e).lower()
            wait = RATE_LIMIT_WAIT * attempt if is_rate else 2 * attempt
            if is_rate:
                log(f"    rate-limited (429); backing off {wait}s before retry {attempt+1}/{RETRY}")
        if attempt < RETRY:
            time.sleep(wait)
    log(f"    !! group failed after {RETRY} tries: {last_err}")
    return None


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default=DEFAULT_USER, help="blogger id (matches config/bloggers.json + fetch_tweets.py --user)")
    ap.add_argument("--limit", type=int, default=0, help="only process first N tweets (test)")
    ap.add_argument("--since", default="", help="only process tweets created on/after this date, YYYY-MM-DD (e.g. 2026-02-01)")
    ap.add_argument("--provider", choices=("auto", "anthropic", "openai"),
                    default=os.environ.get("EXTRACT_PROVIDER", "auto"))
    ap.add_argument("--model", default=os.environ.get("EXTRACT_MODEL", ""),
                    help="provider-specific model override")
    args = ap.parse_args()
    provider = resolve_provider(args.provider)
    model = args.model or (DEFAULT_OPENAI_MODEL if provider == "openai" else DEFAULT_ANTHROPIC_MODEL)

    raw_path, out_path = blogger_paths(args.user)
    display_name = blogger_display_name(args.user)
    system_prompt = build_system_prompt(args.user, display_name)

    raw = load_json(raw_path, [])
    if not raw:
        log(f"No tweets in {raw_path}. Run fetch_tweets.py --user {args.user} first.")
        sys.exit(1)

    # resume: which tweet_ids already done SUCCESSFULLY (error records are retried)
    done = load_json(out_path, {})
    # When --since is used with a newer prompt, force re-extraction of OLD-version
    # records that fall in the window so they get re-done with the new prompt.
    CURRENT_PROMPT_VERSION = "extract-v2"
    def _is_done(tid, r):
        if r.get("error"):
            return False  # always retry errors
        # if this record was made by an older prompt version, it is NOT considered done
        if r.get("prompt_version") and r.get("prompt_version") != CURRENT_PROMPT_VERSION:
            return False  # re-extract with current prompt
        return True
    done_ids = {tid for tid, r in done.items() if _is_done(tid, r)}
    err_ids = {tid for tid, r in done.items() if r.get("error")}
    stale_ids = {tid for tid, r in done.items()
                 if not r.get("error") and r.get("prompt_version") and r.get("prompt_version") != CURRENT_PROMPT_VERSION}
    log(f"Loaded {len(raw)} raw tweets; {len(done_ids)} already extracted (current prompt)"
        + (f"; {len(err_ids)} errors to retry" if err_ids else "")
        + (f"; {len(stale_ids)} old-prompt records eligible for re-extraction (filtered by --since if set)" if stale_ids else "")
        + ".")

    # optional limit (by raw order = newest first)
    work = raw[: args.limit] if args.limit else raw

    # optional --since date filter (only tweets on/after the given YYYY-MM-DD)
    if args.since:
        from datetime import datetime as _dt
        def _tweet_date(t):
            # created_at like "Mon Jun 01 02:52:08 +0000 2026"
            s = t.get("created_at") or ""
            try:
                return _dt.strptime(s, "%a %b %d %H:%M:%S %z %Y").date().isoformat()
            except Exception:
                return ""
        before_n = len(work)
        work = [t for t in work if _tweet_date(t) and _tweet_date(t) >= args.since]
        log(f"--since {args.since}: kept {len(work)} of {before_n} tweets in window.")

    # skip already-done
    work = [t for t in work if t["tweet_id"] not in done_ids]
    if not work:
        log("Nothing to do (all selected tweets already extracted).")
        return
    log(f"To process this run: {len(work)} tweets.")

    # group by conversation_id (so context travels together)
    from collections import defaultdict
    groups = defaultdict(list)
    for t in work:
        groups[t.get("conversation_id") or t["tweet_id"]].append(t)
    # sort each group oldest->newest by id for readable context
    for g in groups.values():
        g.sort(key=lambda t: int(t["tweet_id"]) if str(t["tweet_id"]).isdigit() else 0)

    client = get_client(provider)
    log(f"Extractor: {provider} / {model}")
    now = datetime.now(timezone.utc).isoformat()
    group_list = list(groups.values())
    total_groups = len(group_list)
    processed = 0

    for gi, convo in enumerate(group_list, 1):
        # chunk overly long conversations
        for start in range(0, len(convo), MAX_TWEETS_PER_CALL):
            chunk = convo[start:start + MAX_TWEETS_PER_CALL]
            results = call_model(client, provider, model, system_prompt, chunk)
            if results is None:
                # mark as error so we can find them, but keep going
                for t in chunk:
                    done[t["tweet_id"]] = {
                        "tweet_id": t["tweet_id"], "error": True,
                        "blogger_id": args.user,
                        "extractor_model": model, "prompt_version": "extract-v2",
                        "extracted_at": now,
                    }
                continue
            by_id = {r.get("tweet_id"): r for r in results}
            for t in chunk:
                r = by_id.get(t["tweet_id"]) or {
                    "tweet_id": t["tweet_id"], "has_investment_content": False,
                    "tickers": [], "confidence": 0.0, "missing_from_model": True,
                }
                r["blogger_id"] = args.user
                r["extractor_model"] = model
                r["prompt_version"] = "extract-v2"
                r["extracted_at"] = now
                done[t["tweet_id"]] = r
                processed += 1
            save_json(out_path, done)   # incremental save = resumable
            time.sleep(REQUEST_PACING_SEC)   # polite pace to avoid tripping rate limits
        if gi % 25 == 0 or gi == total_groups:
            log(f"  group {gi}/{total_groups} | tweets done this run: {processed}")

    # summary
    inv = sum(1 for r in done.values() if r.get("has_investment_content"))
    errs = sum(1 for r in done.values() if r.get("error"))
    log("")
    log("===== summary =====")
    log(f"  blogger           : @{args.user} ({display_name})")
    log(f"  extracted total   : {len(done)}  -> {out_path}")
    log(f"  with investment   : {inv}")
    log(f"  errors            : {errs}")
    log("===================")
    if args.limit:
        log(f"This was a --limit test run. Inspect {out_path} for quality,")
        log("then run without --limit for the full set.")


if __name__ == "__main__":
    main()
