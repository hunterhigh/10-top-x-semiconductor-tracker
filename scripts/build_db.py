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
  - instrument  : canonical identity used by every reader: display code/name,
                  market/country/currency, aliases, provider symbol and verification state
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

Dates: tweet created_at is UTC; we convert to the DST-aware
``America/New_York`` timezone before taking the date.  The same timezone is
the dashboard window contract, preventing EST/EDT boundary date drift.

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
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from storage_layout import (
    SHARDED_LAYOUT_VERSION,
    detect_storage_layout,
    index_rows_by_ticker,
    safe_resolve,
    shard_stats,
    stock_document_path,
    stock_document_relative,
    storage_contract,
)

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data"
CONFIG_PATH = SCRIPT_DIR.parent / "config" / "bloggers.json"
PROFILE_CONFIG_PATH = SCRIPT_DIR.parent / "config" / "blogger_profiles.json"
BLOGGERS_DIR = DATA_DIR / "bloggers"
TMAP = DATA_DIR / "ticker_map.json"

DB_DIR = DATA_DIR / "db"
STOCKS_DIR = DB_DIR / "stocks"
INDEX_PATH = DB_DIR / "index.json"
REVIEW_PATH = DATA_DIR / "ticker_review.json"

# Calendar windows in the dashboard are defined in New York time.  A fixed
# UTC-4 offset silently assigns posts to the wrong calendar day during EST.
ET = ZoneInfo("America/New_York")

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
    """Resolve a canonical symbol without inventing a US listing for unknown codes.

    ``ticker_map.json`` is the human-reviewed instrument registry.  A company
    hint is useful for display, but only an explicitly verified registry entry
    may drive market, currency, or price-provider selection.
    """
    entry = dict(tmap.get(sym) or {}) if isinstance(tmap.get(sym), dict) else {}
    # Legacy registry versions recorded reviewed US listings in one explicit
    # allow-list rather than repeating the same fields on every entry. Treat
    # that list as a migration source, never as a fallback for arbitrary codes.
    raw_confirmed_us = tmap.get("_us_confirmed") or []
    confirmed_us = set(raw_confirmed_us if isinstance(raw_confirmed_us, list) else str(raw_confirmed_us).split())
    if sym in confirmed_us:
        entry.setdefault("instrument_id", f"US:{sym}")
        entry.setdefault("exchange", "US")
        entry.setdefault("market", "US")
        entry.setdefault("country", "US")
        entry.setdefault("currency", "USD")
        entry.setdefault("price_symbol", sym)
        entry.setdefault("verified", True)
    verified = bool(entry.get("verified"))
    has_market = bool(entry.get("exchange") and entry.get("currency"))
    has_price_symbol = bool(entry.get("price_symbol"))
    status = "verified" if verified and has_market else ("identified" if entry.get("company") else "unverified")
    return {
        "price_symbol": entry.get("price_symbol") if verified and has_price_symbol else None,
        "exchange": entry.get("exchange") if verified and has_market else None,
        "market": entry.get("market") or (entry.get("exchange") if verified and has_market else None),
        "country": entry.get("country") if verified else None,
        "currency": entry.get("currency") if verified and has_market else None,
        "mapped": status == "verified",
        "verification_status": status,
        "entry": entry,
    }


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


def instrument_identity(sym, res, editorial_fields, aliases):
    """Return the single display/identity contract consumed by renderers.

    The ticker remains a backwards-compatible storage key; ``instrument_id``
    is the stable semantic identity and aliases preserve all accepted forms.
    """
    status = res["verification_status"]
    company = editorial_fields.get("company")
    market = res.get("market")
    display_name = company or "Name unverified"
    display_market = market or "Market unverified"
    instrument_id = res["entry"].get("instrument_id") or f"{market or 'UNVERIFIED'}:{sym}"
    return {
        "instrument_id": instrument_id,
        "display_code": sym,
        "display_name": display_name,
        "display_market": display_market,
        "aliases": sorted(set(aliases or [sym])),
        "market": market,
        "country": res.get("country"),
        "currency": res.get("currency"),
        "price_symbol": res.get("price_symbol"),
        "verification_status": status,
    }


def load_bloggers():
    cfg = load_json(CONFIG_PATH, {}) or {}
    return cfg.get("bloggers", [])


def load_profile_copy():
    """Read editorial profile copy without mixing it into the factual stock data."""
    cfg = load_json(PROFILE_CONFIG_PATH, {}) or {}
    return {p.get("blogger_id"): p for p in cfg.get("profiles", []) if p.get("blogger_id")}


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

    previous_index = load_json(INDEX_PATH, {}) or {}
    storage_version = detect_storage_layout(DB_DIR, index=previous_index)
    previous_rows = index_rows_by_ticker(previous_index)

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
    profile_copy = load_profile_copy()
    tmap = load_json(TMAP, {}) or {}

    extracted, raw, per_blogger_counts = load_all_extracted_and_raw(bloggers)
    if not extracted or not raw:
        log("No extracted/raw data found for any tracked blogger. Run fetch_tweets.py + extract.py first.")
        sys.exit(1)
    aliases = {k: v for k, v in (tmap.get("_aliases") or {}).items() if not k.startswith("_")}
    aliases_by_canonical = defaultdict(set)
    for alias, canonical in aliases.items():
        aliases_by_canonical[canonical].add(alias)
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
            source_symbol = (tk.get("symbol") or "").strip().upper()
            sym = aliases.get(source_symbol, source_symbol)  # merge listing aliases before aggregation
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
                "created_at": dt.isoformat() if dt else None,  # ET ISO timestamp; preserves same-day ordering
                "_dt": dt,                           # internal, stripped before save
                "_company_name": tk.get("company_name"),  # internal, for fallback company resolution
                "_source_symbol": source_symbol,
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
    profile_acc = {
        b["id"]: {"mentions": 0, "linked_mentions": 0, "dates": [], "urls": set(), "posts": {},
                  "stances": defaultdict(int), "tickers": defaultdict(int),
                  "industries": defaultdict(int)}
        for b in bloggers
    }
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
        source_aliases = aliases_by_canonical[sym] | {sym} | {m.get("_source_symbol") for m in ms if m.get("_source_symbol")}
        instrument = instrument_identity(sym, res, ed, source_aliases)
        clean_mentions = [{k: v for k, v in m.items() if k not in ("_dt", "_company_name", "_source_symbol")} for m in ms]

        by_blogger = defaultdict(int)
        by_signal_type = defaultdict(int)
        for m in ms:
            bid = m.get("blogger_id") or "unknown"
            by_blogger[bid] += 1
            by_signal_type[signal_type_by_blogger.get(bid, "opinion")] += 1
        total_mentions_by_blogger = dict(sorted(by_blogger.items(), key=lambda x: -x[1]))
        total_mentions_by_signal_type = dict(by_signal_type)  # e.g. {"opinion": 12, "flow": 2, "news": 1}

        # ---- preserve existing price data (prices.py fills these; don't wipe on rebuild)
        previous_row = previous_rows.get(sym)
        existing_file = (
            stock_document_path(DB_DIR, previous_row, version=storage_version, must_exist=True)
            if previous_row else None
        )
        prev_prices, prev_price_status = [], "pending"
        prev_price_meta = {}
        if existing_file and existing_file.exists():
            try:
                prev = json.loads(existing_file.read_text(encoding="utf-8"))
                prev_prices = prev.get("price_series") or []
                prev_price_status = prev.get("price_status", "pending")
                prev_price_meta = {
                    key: prev[key]
                    for key in ("price_unit", "price_updated_at", "price_reason", "price_history_52w", "price_source_state")
                    if prev.get(key) is not None
                }
            except (json.JSONDecodeError, KeyError):
                pass

        stock_doc = {
            "ticker": sym,
            "cashtag": ms[-1].get("raw_mention") or f"${sym}",
            "instrument": instrument,
            "price_symbol": res["price_symbol"],
            "exchange": res["exchange"],
            "currency": res["currency"],
            "ticker_mapped": res["mapped"],
            "verification_status": res["verification_status"],
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
            **prev_price_meta,
        }
        if storage_version == SHARDED_LAYOUT_VERSION:
            document_relative = stock_document_relative(instrument["instrument_id"])
            document_path = safe_resolve(DB_DIR, document_relative)
        else:
            document_relative = None
            document_path = STOCKS_DIR / f"{sym}.json"
        save_json(document_path, stock_doc)

        # Profile metrics are factual summaries of this tracker\'s collected
        # sample, not a claim about the source account\'s quality or performance.
        for m in clean_mentions:
            bid = m.get("blogger_id")
            if bid not in profile_acc:
                continue
            acc = profile_acc[bid]
            acc["mentions"] += 1
            if m.get("date"):
                acc["dates"].append(m["date"])
            url = m.get("url") or ""
            if url:
                acc["linked_mentions"] += 1
                acc["urls"].add(url)
                prev = acc["posts"].get(url)
                if not prev or (m.get("date") or "") > (prev.get("date") or ""):
                    acc["posts"][url] = {"date": m.get("date"), "url": url, "text": m.get("text") or "", "ticker": sym}
            if signal_type_by_blogger.get(bid, "opinion") == "opinion" and m.get("mention_type") == "explicit_stance":
                stance = m.get("stance")
                acc["stances"][stance if stance in {"bullish", "bearish", "neutral"} else "neutral"] += 1
            acc["tickers"][sym] += 1
            if ed.get("industry"):
                acc["industries"][ed["industry"]] += 1

        index_row = {
            "ticker": sym,
            "instrument": instrument,
            "cashtag": stock_doc["cashtag"],
            "price_symbol": res["price_symbol"],
            "exchange": res["exchange"],
            "currency": res["currency"],
            "ticker_mapped": res["mapped"],
            "verification_status": res["verification_status"],
            "company": ed["company"],            # for list view; thesis_summary stays detail-only
            "industry": ed["industry"],
            "first_mention": stock_doc["first_mention"],
            "last_mention": stock_doc["last_mention"],
            "total_mentions": stock_doc["total_mentions"],
            "blogger_count": len(total_mentions_by_blogger),  # how many of the tracked bloggers ever covered this ticker
            "total_mentions_by_blogger": total_mentions_by_blogger,
            "price_status": prev_price_status,
        }
        if document_relative is not None:
            index_row["document_path"] = document_relative.as_posix()
        index_rows.append(index_row)

    # A canonical alias may have existed as a standalone file before the
    # registry learned about it.  Remove only those obsolete alias documents
    # after their canonical replacement has been written; never delete an
    # arbitrary historical symbol merely because it is absent in this run.
    pruned_alias_docs = []
    for alias, canonical in aliases.items():
        if alias == canonical:
            continue
        if storage_version == SHARDED_LAYOUT_VERSION:
            alias_row = previous_rows.get(alias)
            canonical_row = next((row for row in index_rows if row["ticker"] == canonical), None)
            if alias_row and canonical_row:
                legacy = stock_document_path(DB_DIR, alias_row, version=storage_version, must_exist=True)
                canonical_doc = stock_document_path(DB_DIR, canonical_row, version=storage_version, must_exist=True)
                if legacy != canonical_doc:
                    legacy.unlink()
                    pruned_alias_docs.append(alias)
        else:
            legacy = STOCKS_DIR / f"{alias}.json"
            canonical_doc = STOCKS_DIR / f"{canonical}.json"
            if legacy.exists() and canonical_doc.exists():
                legacy.unlink()
                pruned_alias_docs.append(alias)
    index_rows.sort(key=lambda r: -r["total_mentions"])
    activated_at = ((previous_index.get("meta") or {}).get("storage_layout") or {}).get("activated_at")
    layout = storage_contract(storage_version, activated_at=activated_at)
    if storage_version == SHARDED_LAYOUT_VERSION:
        referenced = {
            stock_document_path(DB_DIR, row, version=storage_version, must_exist=True).resolve()
            for row in index_rows
        }
        for path in STOCKS_DIR.rglob("*.json"):
            if path.resolve() not in referenced:
                path.unlink()
        layout["stock_documents"].update(shard_stats(referenced, STOCKS_DIR))
    index_doc = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "tracked_bloggers": [b["id"] for b in bloggers],
            "total_tickers": len(index_rows),
            "total_mentions": sum(r["total_mentions"] for r in index_rows),
            "dates": "ET (US Eastern); matches render/pipeline.py",
            "storage_layout": layout,
            "note": "Data layer only — facts + raw mentions from ALL tracked bloggers, "
                    "merged per ticker with blogger_id attribution. All windowed/stance "
                    "aggregation + rankings (incl. cross-blogger consensus) are computed "
                    "by render at runtime. Prices pending.",
        },
        "stocks": index_rows,        # sorted by raw total_mentions (manifest ordering only)
    }
    save_json(INDEX_PATH, index_doc)

    profile_rows = []
    for blogger in bloggers:
        bid = blogger["id"]
        acc = profile_acc[bid]
        dates = sorted(set(acc["dates"]))
        total = acc["mentions"]
        profile_rows.append({
            "blogger_id": bid,
            "signal_type": signal_type_by_blogger[bid],
            "sample": {
                "first_date": dates[0] if dates else None,
                "last_date": dates[-1] if dates else None,
                "mentions": total,
                "unique_original_posts": len(acc["urls"]),
                "original_link_rate": round(acc["linked_mentions"] / total, 4) if total else 0,
                "explicit_stance_counts": dict(acc["stances"]) if signal_type_by_blogger[bid] == "opinion" else None,
                "top_tickers": [{"ticker": tk, "mentions": n} for tk, n in sorted(acc["tickers"].items(), key=lambda x: (-x[1], x[0]))[:8]],
                "top_industries": [{"industry": name, "mentions": n} for name, n in sorted(acc["industries"].items(), key=lambda x: (-x[1], x[0]))[:6]],
                "recent_posts": sorted(acc["posts"].values(), key=lambda x: (x.get("date") or "", x.get("url") or ""), reverse=True)[:12],
            },
            "editorial_reviewed_at": (profile_copy.get(bid) or {}).get("reviewed_at"),
        })
    save_json(DB_DIR / "blogger_profiles.json", {
        "meta": {"generated_at": datetime.now(timezone.utc).isoformat(), "note": "Automatic coverage statistics for the tracked public-post sample. They are not source-quality or performance scores."},
        "profiles": profile_rows,
    })

    def top_hint(sym):
        hints = unmapped_company_hints.get(sym)
        if not hints:
            return None
        return max(hints.items(), key=lambda x: x[1])[0]   # most-agreed-upon company_name across extractors

    review = {
        "_note": "Symbols without a verified instrument-registry entry are never defaulted to US/USD. "
                 "They remain publishable as explicitly unverified entities and require review. "
                 "Run ticker_map_suggest.py to auto-draft company/exchange/currency suggestions "
                 "for human review (writes to ticker_map_suggestions.json; never auto-applied).",
        "unmapped": [{"symbol": s, "mentions": n, "company_name_hint": top_hint(s)}
                     for s, n in sorted(unmapped.items(), key=lambda x: -x[1])],
        "unverified": [
            {"symbol": row["ticker"], "instrument_id": row["instrument"]["instrument_id"],
             "company_name_hint": row["company"], "mentions": row["total_mentions"],
             "verification_status": row["verification_status"]}
            for row in index_rows if row["verification_status"] != "verified"
        ],
        "skipped_non_tickers": [{"symbol": s, "count": n} for s, n in sorted(skipped.items(), key=lambda x: -x[1])],
        "excluded": [{"symbol": s, "mentions": n} for s, n in sorted(excluded.items(), key=lambda x: -x[1])],
    }
    save_json(REVIEW_PATH, review)

    log("")
    log("===== build_db summary (data layer) =====")
    log(f"  tickers written : {len(index_rows)}  -> {STOCKS_DIR}")
    log(f"  index (manifest): {INDEX_PATH}")
    log(f"  profile stats    : {DB_DIR / 'blogger_profiles.json'}")
    log(f"  unmapped symbols: {len(unmapped)} (see {REVIEW_PATH.name})")
    if pruned_alias_docs:
        log(f"  merged alias docs: {', '.join(sorted(pruned_alias_docs))}")
    log(f"  skipped non-tix : {len(skipped)}")
    log(f"  excluded (ETF…) : {len(excluded)} {dict(excluded) if excluded else ''}")
    if index_rows:
        log("  top by raw mentions (informational only; stance computed by render):")
        for r in index_rows[:10]:
            cur = "" if not r["currency"] or r["currency"] == "USD" else f" [{r['currency']}]"
            log(f"     {r['ticker']:<7} {r['total_mentions']:>4} mentions  "
                f"({r['first_mention']} → {r['last_mention']}){cur}")
    log("=========================================")
    log("Next: prices.py fills price_series; render computes all windowed/stance stats.")


if __name__ == "__main__":
    main()
