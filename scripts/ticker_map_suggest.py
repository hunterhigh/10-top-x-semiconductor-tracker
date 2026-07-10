#!/usr/bin/env python3
"""
ticker_map_suggest.py — LLM-assisted ticker_map.json maintenance.

With 10 generalist trading bloggers instead of one niche photonics account,
the unmapped-ticker pool grows far past what hand-curation (the original
89-entry ticker_map.json) can keep up with. This script drafts suggestions
for a human to confirm — it NEVER writes directly to ticker_map.json.

Input : ../data/ticker_review.json          (written by build_db.py; each
                                              unmapped symbol includes a
                                              company_name_hint pulled from
                                              extract.py's per-tweet guesses)
Output: ../data/ticker_map_suggestions.json  (pending review queue)

Flow:
  1. build_db.py runs, writes ticker_review.json (unmapped symbols + hints)
  2. python ticker_map_suggest.py     -> drafts suggestions via Claude
  3. A human skims ticker_map_suggestions.json, fixes anything wrong, and
     copies confirmed entries into ticker_map.json (or use --apply-confirmed
     after manually setting "confirmed": true on entries you trust)
  4. Re-run build_db.py so the newly mapped symbols pick up exchange/currency

The model is given the mention's company_name_hint (already extracted from
actual tweet text, not invented) plus the symbol, and asked to identify the
real listing (exchange/currency/price_symbol) — this DOES require the model's
general knowledge (which exchange a company trades on), so every suggestion
carries a confidence field and low-confidence ones are flagged for mandatory
review rather than silently trusted.

Run:
    export ANTHROPIC_API_KEY="your_key_here"
    python ticker_map_suggest.py                 # draft suggestions for all unmapped symbols
    python ticker_map_suggest.py --limit 20       # test on the top 20 by mention count
    python ticker_map_suggest.py --apply-confirmed  # fold entries marked confirmed:true into ticker_map.json
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data"
REVIEW_PATH = DATA_DIR / "ticker_review.json"
SUGGESTIONS_PATH = DATA_DIR / "ticker_map_suggestions.json"
TMAP_PATH = DATA_DIR / "ticker_map.json"

DEFAULT_MODEL = "claude-opus-4-6"
BATCH_SIZE = 15   # symbols per LLM call

SYSTEM_PROMPT = """You help maintain a stock ticker mapping table for a financial data pipeline. \
For each symbol you are given (plus an optional company-name hint pulled from real tweet text), \
identify the actual company and its primary listing.

For each symbol, output:
- company: the company's full commonly-known name
- exchange: one of "US" (NYSE/Nasdaq), or a specific non-US venue name (e.g. "Tokyo", "Stockholm", "Taiwan", "Hong Kong", "London", "Shenzhen")
- currency: the ISO currency code of that listing (e.g. "USD", "JPY", "SEK", "TWD", "HKD", "GBP")
- price_symbol: the ticker suffix format typically used by price data providers for that listing \
(e.g. US -> same as symbol; Tokyo -> "1234.T"; Stockholm -> "TICK.ST"; Taiwan -> "1234.TW"; Hong Kong -> "1234.HK"; London -> "TICK.L")
- confidence: "high" | "medium" | "low" — how sure you are this symbol maps to a real, correctly-identified listing
- notes: one short sentence if there's ambiguity (e.g. "could also refer to a different company with the same ticker on another exchange")

If you do not recognize the symbol/company at all, or multiple unrelated companies plausibly share this \
ticker, set confidence to "low" and explain in notes rather than guessing a company.

Output ONLY valid JSON, no prose, no markdown fences:
{"results": [{"symbol": "SIVE", "company": "Sivers Semiconductors", "exchange": "Stockholm", "currency": "SEK", "price_symbol": "SIVE.ST", "confidence": "high", "notes": ""}]}
Every input symbol MUST appear exactly once in results."""


if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')   # Windows console defaults to GBK, which can't print → etc.


def log(m):
    print(m, flush=True)


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


def get_client():
    try:
        import anthropic
    except ImportError:
        log("ERROR: pip install anthropic")
        sys.exit(1)
    import os
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        log("ERROR: ANTHROPIC_API_KEY not set.")
        sys.exit(1)
    return anthropic.Anthropic(api_key=api_key)


def strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s.rsplit("```", 1)[0]
        if s.lstrip().startswith("json"):
            s = s.lstrip()[4:]
    return s.strip()


def call_model(client, model, batch):
    lines = ["Symbols to identify:\n"]
    for item in batch:
        hint = f' (hint from tweet text: "{item["company_name_hint"]}")' if item.get("company_name_hint") else ""
        lines.append(f'- {item["symbol"]}{hint}  [{item["mentions"]} mentions across tracked bloggers]')
    user_msg = "\n".join(lines)
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        return json.loads(strip_fences(raw)).get("results", [])
    except Exception as e:
        log(f"  !! batch failed: {e}")
        return []


def cmd_apply_confirmed():
    suggestions = load_json(SUGGESTIONS_PATH, {})
    tmap = load_json(TMAP_PATH, {})
    applied = 0
    for sym, entry in suggestions.get("pending", {}).items():
        if not entry.get("confirmed"):
            continue
        tmap[sym] = {
            "company": entry["company"],
            "industry": entry.get("industry"),
            "exchange": entry["exchange"],
            "currency": entry["currency"],
            "price_symbol": entry["price_symbol"],
            "verified": True,
        }
        applied += 1
    save_json(TMAP_PATH, tmap)
    log(f"Applied {applied} human-confirmed entries into {TMAP_PATH}. Re-run build_db.py next.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="only draft suggestions for top-N unmapped symbols by mention count")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--apply-confirmed", action="store_true",
                     help="fold entries with confirmed:true from ticker_map_suggestions.json into ticker_map.json")
    args = ap.parse_args()

    if args.apply_confirmed:
        cmd_apply_confirmed()
        return

    review = load_json(REVIEW_PATH, {})
    unmapped = review.get("unmapped", [])
    if not unmapped:
        log(f"No unmapped symbols in {REVIEW_PATH}. Run build_db.py first (or nothing to do).")
        return

    existing = load_json(SUGGESTIONS_PATH, {"pending": {}})
    already_drafted = set(existing["pending"].keys())
    work = [u for u in unmapped if u["symbol"] not in already_drafted]
    if args.limit:
        work = work[: args.limit]
    if not work:
        log("Nothing new to draft (all unmapped symbols already have pending suggestions).")
        return
    log(f"Drafting suggestions for {len(work)} symbols (of {len(unmapped)} unmapped total)...")

    client = get_client()
    for i in range(0, len(work), BATCH_SIZE):
        batch = work[i:i + BATCH_SIZE]
        results = call_model(client, args.model, batch)
        by_sym = {r.get("symbol", "").upper(): r for r in results}
        for item in batch:
            sym = item["symbol"]
            r = by_sym.get(sym)
            if not r:
                continue
            existing["pending"][sym] = {
                "company": r.get("company"),
                "exchange": r.get("exchange"),
                "currency": r.get("currency"),
                "price_symbol": r.get("price_symbol"),
                "confidence": r.get("confidence", "low"),
                "notes": r.get("notes", ""),
                "mentions": item["mentions"],
                "company_name_hint": item.get("company_name_hint"),
                "confirmed": False,   # human sets this to true before --apply-confirmed will use it
            }
        save_json(SUGGESTIONS_PATH, existing)
        log(f"  drafted {min(i + BATCH_SIZE, len(work))}/{len(work)}")

    low_conf = sum(1 for v in existing["pending"].values() if v.get("confidence") == "low")
    log("")
    log("===== ticker_map_suggest summary =====")
    log(f"  total pending      : {len(existing['pending'])}  -> {SUGGESTIONS_PATH}")
    log(f"  low confidence     : {low_conf} (need extra scrutiny before confirming)")
    log("  Next: review the file, set confirmed:true on entries you trust,")
    log("        then run:  python ticker_map_suggest.py --apply-confirmed")
    log("=======================================")


if __name__ == "__main__":
    main()
