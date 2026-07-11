#!/usr/bin/env python3
"""
build_db.py — multi-blogger tracker, data layer (NO LLM, NO pre-aggregation)

Transposes the per-tweet extraction of ALL tracked bloggers (config/bloggers.json)
into a by-ticker FACTUAL RECORD: every mention preserved and traceable to its
source tweet + its blogger. That's it.

Multi-blogger / shared-schema design (per project plan): a ticker mentioned by
several bloggers gets ONE stocks/{TICKER}.json file with a merged mentions[]
list, each mention tagged "blogger_id". This is what makes the cross-blogger
consensus view possible ("7 of 10 trackers are bullish on $NVDA") without
re-processing raw data later. The actual windowed consensus math (N/10 bullish
today, this week, etc.) is NOT computed here — same rationale as before: it's
render's job because report windows are dynamic/as-of. What build_db adds for
consensus is only a window-independent fact per ticker: which bloggers have
ever mentioned it and how many times each (total_mentions_by_blogger).

This is the *data layer*, not the stats layer. All windowed/aggregated figures
(bull/bear/neutral buckets, 7d/30d frequency, current stance, flips, rankings)
are computed at RENDER time by render.py — because the report windows
(day/week/month/quarter) are dynamic/as-of, and the stance buckets must be
explicit_stance-only. Baking them here would be brittle (tied to one "now") and
wrong-口径. So build_db deliberately does NOT pre-compute them.

What build_db DOES keep:
  - mentions[]  : every mention, with ET date / stance / mention_type / reasons
                  / url / verbatim text / engagement / tweet_id / blogger_id.
                  (Superset of what render needs; render filters/windows from this.)
  - resolution  : price_symbol / exchange / currency / ticker_mapped (ticker_map)
  - editorial    : company / industry / thesis_summary — folded in from ticker_map.json
                  (merged file; render reads these from the per-stock JSON, not meta.json)
  - first_mention / last_mention  (window-independent static facts; prices.py
                  uses first_mention as the price-series start)
  - total_mentions : raw count of ALL mentions across ALL bloggers (all types).
                  Window-independent, filter-independent fact ("how much this
                  gets talked about"); also used by prices.py to pick the core
                  set. NOT a stance metric.
  - total_mentions_by_blogger : same, broken out per blogger_id. Window-
                  independent fact that enables the consensus view.
  - price_series / price_status : left empty; prices.py fills them.

Dates: tweet created_at is UTC; we convert to US Eastern before taking the
date, to MATCH render/pipeline.py exactly (otherwise ~15% of mentions land on
the wrong day at the UTC/ET boundary). ET is hardcoded UTC-4 to match pipeline.py
(technically EDT; slightly off in the EST months, but it MUST equal render —
if we ever want true DST-aware ET, change both files together).

Inputs:
  ../config/bloggers.json                     tracked blogger roster
  ../data/bloggers/{id}/extracted.json         per blogger, from extract.py
  ../data/bloggers/{id}/raw_tweets.json        per blogger, from fetch_tweets.py
  ../data/ticker_map.json                      shared across all bloggers
Outputs:
  ../data/db/index.json            lean manifest (one row per ticker) — NO stats
  ../data/db/stocks/{TICKER}.json  per-ticker mentions[] + facts, prices empty
  ../data/ticker_review.json       symbols not in ticker_map (to verify)

Run:
  python build_db.py
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data"
CONFIG_PATH = SCRIPT_DIR.parent / "config" / "bloggers.json"
BLOGGERS_DIR = DATA_DIR / "bloggers"
TMAP = DATA_DIR / "ticker_map.json"

DB_DIR = DATA_DIR / "db"
STOCKS_DIR = DB_DIR / "stocks"
INDEX_PATH = DB_DIR / "index.json"
REVIEW_PATH = DATA_DIR / "ticker_review.json"

ET = timezone(timedelta(hours=-4))   # match pipeline.py (render's canonical clock)

NON_TICKER = {
    "AWS", "CPO", "INP", "ETH", "BTC", "LTC", "SOL", "XRP", "USDC", "USDT", "ETORO",
    "TPU", "HBM", "AI", "ARR", "EPS", "CEO", "ETF", "DYOR", "NFI", "IPO",
}
YEAR_RE = re.compile(r"^(19|20)\d{2}$")


if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')   # Windows console defaults to GBK, which can't print → etc.


def log(m): print(m, flush=True)


def load_json(path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log(f"WARNING: {path.name} is corrupt.")
    return default


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def parse_date(s):
    """Twitter created_at ('Mon Jun 01 02:52:08 +0000 2026') -> (ET date iso, ET-aware datetime).
    Converts to US Eastern before taking the date, to match render/pipeline.py."""
    if not s:
        return None, None
    try:
        dt = parsedate_to_datetime(s).astimezone(ET)
        return dt.date().isoformat(), dt
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z",):
        try:
            dt = datetime.strptime(s, fmt).astimezone(ET)
            return dt.date().isoformat(), dt
        except ValueError:
            continue
    try:                                  # bare date, no time/tz: take as-is
        d = datetime.strptime(s, "%Y-%m-%d")
        return d.date().isoformat(), d.replace(tzinfo=ET)
    except ValueError:
        return None, None


def is_real_ticker(sym):
    if not sym:
        return False
    s = sym.strip().upper()
    if s in NON_TICKER or YEAR_RE.match(s) or len(s) > 12:
        return False
    return True


def resolve(sym, tmap):
    """map a symbol to price_symbol/exchange/currency; default US/USD if unknown."""
    entry = tmap.get(sym)
    if entry and isinstance(entry, dict) and "price_symbol" in entry:
        return {
            "price_symbol": entry["price_symbol"],
            "exchange": entry.get("exchange", "US"),
            "currency": entry.get("currency", "USD"),
            "mapped": True,
        }
    return {"price_symbol": sym, "exchange": "US", "currency": "USD", "mapped": False}


def editorial(sym, tmap):
    """Pull editorial fields (company / industry / thesis_summary) from the merged
    ticker_map entry. Absent -> None. (meta.json is retired; this folds its content in.)"""
    e = tmap.get(sym)
    if not isinstance(e, dict):
        e = {}
    return {
        "company": e.get("company"),
        "industry": e.get("industry"),
        "thesis_summary": e.get("thesis_summary"),
    }


def load_bloggers():
    cfg = load_json(CONFIG_PATH, {}) or {}
    return cfg.get("bloggers", [])


def load_all_extracted_and_raw(bloggers):
    """Merge every tracked blogger's extracted.json + raw_tweets.json into one
    (extracted, raw) pair. Tweet ids are globally unique across bloggers (real
    Twitter snowflake ids), so a flat merge by tweet_id is safe. Every extracted
    record already carries blogger_id (tagged by extract.py); we backfill it
    from the directory name if an older record is missing it."""
    extracted, raw = {}, {}
    per_blogger_counts = {}
    for b in bloggers:
        bid = b["id"]
        bdir = BLOGGERS_DIR / bid
        b_extracted = load_json(bdir / "extracted.json", {}) or {}
        b_raw_list = load_json(bdir / "raw_tweets.json", []) or []
        for tid, rec in b_extracted.items():
            rec.setdefault("blogger_id", bid)
            extracted[tid] = rec
        for t in b_raw_list:
            if t.get("tweet_id"):
                raw[t["tweet_id"]] = t
        per_blogger_counts[bid] = len(b_extracted)
        if not b_extracted:
            log(f"  NOTE: no extracted data yet for @{bid} ({b.get('display_name')}) — skipping, run fetch+extract first.")
    return extracted, raw, per_blogger_counts


def main():
    argparse.ArgumentParser().parse_args()   # no flags needed anymore (no windows to anchor)

    bloggers = load_bloggers()
    if not bloggers:
        log(f"No bloggers configured in {CONFIG_PATH}. Nothing to build.")
        sys.exit(1)
    # signal_type: "opinion" bloggers express personal stance (feeds consensus);
    # "flow"/"news"/"disclosure" bloggers (unusual_whales/StockMKTNewz/DJTRadar)
    # report options flow / news / third-party trades, not their own opinion —
    # threaded into each stock_doc below so downstream consumers (render.py's
    # consensus math, future triangulation features) don't need to re-derive it.
    signal_type_by_blogger = {b["id"]: b.get("signal_type", "opinion") for b in bloggers}
    tmap = load_json(TMAP, {}) or {}

    extracted, raw, per_blogger_counts = load_all_extracted_and_raw(bloggers)
    if not extracted or not raw:
        log("No extracted/raw data found for any tracked blogger. Run fetch_tweets.py + extract.py first.")
        sys.exit(1)
    aliases = {k: v for k, v in (tmap.get("_aliases") or {}).items() if not k.startswith("_")}
    log(f"Loaded {len(extracted)} extracted (across {len(bloggers)} bloggers), {len(raw)} raw, "
        f"{sum(1 for k in tmap if not k.startswith('_'))} mapped tickers, {len(aliases)} aliases.")
    for bid, n in per_blogger_counts.items():
        log(f"  @{bid}: {n} extracted records")

    # ---- gather mentions per ticker (faithful, no aggregation)
    per = defaultdict(list)
    unmapped = defaultdict(int)
    unmapped_company_hints = defaultdict(lambda: defaultdict(int))  # sym -> {company_name: count}
    skipped = defaultdict(int)
    excluded = defaultdict(int)

    for tid, rec in extracted.items():
        rt = raw.get(tid, {})
        date_iso, dt = parse_date(rt.get("created_at"))
        if date_iso is None:           # no parseable date -> skip (matches render's `if d is None`)
            continue                   # NOTE: we deliberately do NOT filter on has_investment_content
        for tk in rec.get("tickers", []):   #       or error here, to stay faithful to render's counting.
            sym = (tk.get("symbol") or "").strip().upper()
            sym = aliases.get(sym, sym)         # SIVEF->SIVE, SLOIF/SOITEC->SOI, etc. (merge same company)
            if not is_real_ticker(sym):
                skipped[sym] += 1
                continue
            ent = tmap.get(sym)
            if isinstance(ent, dict) and ent.get("exclude"):
                excluded[sym] += 1          # e.g. ETFs (EWY/XLU): not single-stock opinions
                continue
            res = resolve(sym, tmap)
            if not res["mapped"] and sym not in tmap:
                unmapped[sym] += 1
                cn = tk.get("company_name")
                if cn:
                    unmapped_company_hints[sym][cn] += 1
            per[sym].append({
                "tweet_id": tid,
                "blogger_id": rec.get("blogger_id"),
                "date": date_iso,                    # ET date
                "_dt": dt,                           # internal, stripped before save
                "_company_name": tk.get("company_name"),  # internal, for fallback company resolution
                "stance": tk.get("stance"),
                "mention_type": tk.get("mention_type"),
                "reasons": tk.get("reasons") or [],
                "is_risk": bool(tk.get("is_risk")),
                "conviction": tk.get("conviction_signal"),
                "raw_mention": tk.get("raw_mention"),
                "text": rt.get("text"),
                "url": rt.get("url"),
                "kind": rt.get("kind"),
                "engagement": {
                    "views": rt.get("view_count"),
                    "likes": rt.get("like_count"),
                    "reposts": rt.get("retweet_count"),
                    "replies": rt.get("reply_count"),
                },
                "extractor_model": rec.get("extractor_model"),
                "prompt_version": rec.get("prompt_version"),
            })

    # ---- write per-ticker files (data layer only)
    STOCKS_DIR.mkdir(parents=True, exist_ok=True)
    index_rows = []
    for sym, ms in sorted(per.items()):
        ms.sort(key=lambda m: (m["_dt"] or datetime.min.replace(tzinfo=timezone.utc)))
        res = resolve(sym, tmap)
        ed = editorial(sym, tmap)

        # fallback: if ticker_map has no company name, use LLM-extracted company_name
        if not ed["company"]:
            for m in reversed(ms):
                cn = m.get("_company_name")
                if cn:
                    ed["company"] = cn
                    break
        dates = [m["_dt"] for m in ms if m["_dt"]]
        first_dt = min(dates) if dates else None
        last_dt = max(dates) if dates else None
        clean_mentions = [{k: v for k, v in m.items() if k not in ("_dt", "_company_name")} for m in ms]

        by_blogger = defaultdict(int)
        by_signal_type = defaultdict(int)
        for m in ms:
            bid = m.get("blogger_id") or "unknown"
            by_blogger[bid] += 1
            by_signal_type[signal_type_by_blogger.get(bid, "opinion")] += 1
        total_mentions_by_blogger = dict(sorted(by_blogger.items(), key=lambda x: -x[1]))
        total_mentions_by_signal_type = dict(by_signal_type)  # e.g. {"opinion": 12, "flow": 2, "news": 1}

        # ---- preserve existing price data (prices.py fills these; don't wipe on rebuild)
        existing_file = STOCKS_DIR / f"{sym}.json"
        prev_prices, prev_price_status = [], "pending"
        if existing_file.exists():
            try:
                prev = json.loads(existing_file.read_text(encoding="utf-8"))
                if prev.get("price_series"):
                    prev_prices = prev["price_series"]
                    prev_price_status = prev.get("price_status", "pending")
            except (json.JSONDecodeError, KeyError):
                pass

        stock_doc = {
            "ticker": sym,
            "cashtag": ms[-1].get("raw_mention") or f"${sym}",
            "price_symbol": res["price_symbol"],
            "exchange": res["exchange"],
            "currency": res["currency"],
            "ticker_mapped": res["mapped"],
            "company": ed["company"],            # editorial (folded from ticker_map; was meta.json)
            "industry": ed["industry"],
            "thesis_summary": ed["thesis_summary"],
            "first_mention": first_dt.date().isoformat() if first_dt else None,
            "last_mention": last_dt.date().isoformat() if last_dt else None,
            "total_mentions": len(ms),           # raw count across ALL bloggers, window-independent; NOT a stance metric
            "total_mentions_by_blogger": total_mentions_by_blogger,  # window-independent fact; enables consensus view
            "total_mentions_by_signal_type": total_mentions_by_signal_type,  # opinion/flow/news/disclosure breakdown; see signal_type_by_blogger
            "mentions": clean_mentions,          # EVERY mention (all bloggers), ET dates, fully traceable
            # prices: preserve from previous build if available; prices.py overwrites on its run
            "price_series": prev_prices,
            "price_status": prev_price_status,
        }
        save_json(STOCKS_DIR / f"{sym}.json", stock_doc)

        index_rows.append({
            "ticker": sym,
            "cashtag": stock_doc["cashtag"],
            "price_symbol": res["price_symbol"],
            "exchange": res["exchange"],
            "currency": res["currency"],
            "ticker_mapped": res["mapped"],
            "company": ed["company"],            # for list view; thesis_summary stays detail-only
            "industry": ed["industry"],
            "first_mention": stock_doc["first_mention"],
            "last_mention": stock_doc["last_mention"],
            "total_mentions": stock_doc["total_mentions"],
            "blogger_count": len(total_mentions_by_blogger),  # how many of the tracked bloggers ever covered this ticker
            "total_mentions_by_blogger": total_mentions_by_blogger,
            "price_status": prev_price_status,
        })

    index_rows.sort(key=lambda r: -r["total_mentions"])
    index_doc = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "tracked_bloggers": [b["id"] for b in bloggers],
            "total_tickers": len(index_rows),
            "total_mentions": sum(r["total_mentions"] for r in index_rows),
            "dates": "ET (US Eastern); matches render/pipeline.py",
            "note": "Data layer only — facts + raw mentions from ALL tracked bloggers, "
                    "merged per ticker with blogger_id attribution. All windowed/stance "
                    "aggregation + rankings (incl. cross-blogger consensus) are computed "
                    "by render at runtime. Prices pending.",
        },
        "stocks": index_rows,        # sorted by raw total_mentions (manifest ordering only)
    }
    save_json(INDEX_PATH, index_doc)

    def top_hint(sym):
        hints = unmapped_company_hints.get(sym)
        if not hints:
            return None
        return max(hints.items(), key=lambda x: x[1])[0]   # most-agreed-upon company_name across extractors

    review = {
        "_note": "Symbols seen in data but NOT in ticker_map.json. Default-treated as US/USD. "
                 "Verify any non-US (add to ticker_map.json). Sorted by mention count desc. "
                 "Run ticker_map_suggest.py to auto-draft company/exchange/currency suggestions "
                 "for human review (writes to ticker_map_suggestions.json; never auto-applied).",
        "unmapped": [{"symbol": s, "mentions": n, "company_name_hint": top_hint(s)}
                     for s, n in sorted(unmapped.items(), key=lambda x: -x[1])],
        "skipped_non_tickers": [{"symbol": s, "count": n} for s, n in sorted(skipped.items(), key=lambda x: -x[1])],
        "excluded": [{"symbol": s, "mentions": n} for s, n in sorted(excluded.items(), key=lambda x: -x[1])],
    }
    save_json(REVIEW_PATH, review)

    log("")
    log("===== build_db summary (data layer) =====")
    log(f"  tickers written : {len(index_rows)}  -> {STOCKS_DIR}")
    log(f"  index (manifest): {INDEX_PATH}")
    log(f"  unmapped symbols: {len(unmapped)} (see {REVIEW_PATH.name})")
    log(f"  skipped non-tix : {len(skipped)}")
    log(f"  excluded (ETF…) : {len(excluded)} {dict(excluded) if excluded else ''}")
    if index_rows:
        log("  top by raw mentions (informational only; stance computed by render):")
        for r in index_rows[:10]:
            cur = "" if r["currency"] == "USD" else f" [{r['currency']}]"
            log(f"     {r['ticker']:<7} {r['total_mentions']:>4} mentions  "
                f"({r['first_mention']} → {r['last_mention']}){cur}")
    log("=========================================")
    log("Next: prices.py fills price_series; render computes all windowed/stance stats.")


if __name__ == "__main__":
    main()
