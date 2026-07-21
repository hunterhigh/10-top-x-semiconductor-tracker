#!/usr/bin/env python3
"""
prices.py — multi-blogger tracker, fill price_series (NO LLM)

Reads the per-ticker files built by build_db.py and fills in the price fields
that build_db deliberately left empty:
    price_series  (daily closes, required history start -> today) <- chart/return data
    price_status  (ok / partial / unavailable / unverified_symbol)
    price_history_52w (independent coverage status for the rolling 52-week return)

Ticker-level, not blogger-level: unchanged by the move to multi-blogger
tracking. data/db/stocks/*.json is a single shared, ticker-keyed store (every
tracked blogger's mentions of e.g. $NVDA live in the same file — see
build_db.py), and price data is likewise fetched ONCE per ticker regardless of
how many bloggers mention it. Do NOT loop this per blogger — that would burn
the EODHD daily call budget N times over for the same symbols, since finance
influencers heavily overlap on which tickers they cover.

NOTE: prices.py does NOT bake any return numbers. Per the locked architecture,
render owns ALL derived figures: it computes "gain since first mention"
(series[0].close -> latest close) and any deep-dive horizons straight from
price_series. prices.py is a pure fetch layer.

Design (confirmed with project owner):
  - TWO-STEP separation preserved: build_db owns facts/aggregates; prices.py
    ONLY touches the three price fields, writing every other field back as-is.
  - Provider-pluggable. US/USD (the _default) -> akshare; the mapped non-US
    listings (SIVE.ST/SOI.PA/IQE.L/XFAB.PA/4092.T/6451.TW) -> EODHD.
  - FULL-SERIES CACHE (data/prices_cache/{price_symbol}.json): normal runs
    back-fill from first_mention; 52-week runs extend the cache backwards to
    the exact report horizon. Later runs fetch ONLY missing prefix/suffix data. The series
    written into the stock file is always the COMPLETE line (for the chart).
    The cache also makes us robust to build_db re-runs wiping price_series:
    we just refill from cache, no API calls.
  - Native currency kept as-fetched. NO FX normalization. (Display must label
    the currency; for London GBp we keep price_unit="GBp" = pence.)
  - Raw (unadjusted) close, on purpose: it must line up with the prices HE
    quotes in his own tweets (e.g. SIVE $4 -> $71 in SEK), which are raw quotes.
  - Scope: prices are fetched only for tickers we actually display =
    (mentioned in the last 30 days)  UNION  (total_mentions >= --min-mentions).
    Not all ~860 tail symbols.

Secrets: EODHD key is read from env EODHD_API_KEY. Never hard-coded.

Run:
    python prices.py --provider-test          # one symbol per provider, check connectivity
    python prices.py --ticker SIVE            # one ticker (test)
    python prices.py                          # all in-scope tickers (incremental)
    python prices.py --force                  # ignore cache, full re-fetch
    python prices.py --min-mentions 30        # widen/narrow the core set
    python prices.py --asof 2026-06-02        # pin "now" for the 30d window
    python prices.py --history-weeks 52 --history-scope recent-28d
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = SCRIPT_DIR.parent / "data"
DB_DIR = DATA_DIR / "db"
STOCKS_DIR = DB_DIR / "stocks"
INDEX_PATH = DB_DIR / "index.json"
MANIFEST_PATH = DB_DIR / "manifest.json"
TMAP_PATH = DATA_DIR / "ticker_map.json"
CACHE_DIR = DATA_DIR / "prices_cache"
BLOGGERS_PATH = PROJECT_DIR / "config" / "bloggers.json"

# Keep the installed Skill and repository pipeline on one eligibility rule.
sys.path.insert(0, str(PROJECT_DIR / "skill" / "scripts"))
from report_scope import is_monthly_report_instrument  # noqa: E402

DEFAULT_MIN_MENTIONS = 50      # core set ~= the 41 deep-divable tickers; tune with --min-mentions
RECENT_WINDOW_DAYS = 30        # matches the initial backfill and 28-day Month view
HISTORY_START_TOLERANCE_DAYS = 7
RETRY = 3
PACING_SEC = 0.4               # polite pause between symbols
EODHD_DAILY_CALL_BUDGET = 20   # free tier; we self-limit and stop cleanly when hit


# --------------------------------------------------------------------------- EODHD symbol suffixes
# ticker_map uses Yahoo-style suffixes. EODHD uses its OWN exchange codes for
# some markets. THESE MUST BE VERIFIED once the EODHD key exists (see README in
# the summary). Stockholm/Paris are likely identical; London/Tokyo/Taiwan are
# the high-risk ones. Edit this map after verifying against EODHD's symbol list.
EODHD_SUFFIX_MAP = {
    # CONFIRMED against EODHD's official exchange list (Code / OperatingMIC):
    ".ST": ".ST",     # Stockholm = ST (XSTO)            CONFIRMED
    ".PA": ".PA",     # Euronext Paris = PA (XPAR)       CONFIRMED
    ".L":  ".LSE",    # London = LSE (XLON)              CONFIRMED  (NOT .L)
    ".DE": ".XETRA",  # Frankfurt Xetra = XETRA (XETR)   CONFIRMED  (.F = Frankfurt floor, different)
    ".TO": ".TO",     # Toronto = TO (XTSE)              CONFIRMED
    # NOT in the list we checked — still verify with the key:
    ".T":  ".TSE",    # Tokyo — VERIFY EODHD's Japan code
    ".TW": ".TW",     # Taiwan — VERIFY (6451 is verified:false anyway)
}


def to_eodhd_symbol(price_symbol: str) -> str:
    # match longest suffix first so ".TW" wins over ".T"
    for ysuf in sorted(EODHD_SUFFIX_MAP, key=len, reverse=True):
        if price_symbol.endswith(ysuf):
            return price_symbol[: -len(ysuf)] + EODHD_SUFFIX_MAP[ysuf]
    return price_symbol


# --------------------------------------------------------------------------- io helpers
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')   # Windows console defaults to GBK, which can't print → etc.


def log(m): print(m, flush=True)


def today_et() -> str:
    """Use the same trading-day boundary as the extracted X-post dates."""
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def manifest_report_date() -> str | None:
    """Return the verified ET data cutoff rather than guessing from wall-clock time."""
    manifest = load_json(MANIFEST_PATH, default={}) or {}
    date_range = manifest.get("date_range")
    if isinstance(date_range, list) and len(date_range) >= 2 and date_range[1]:
        return str(date_range[1])[:10]
    if isinstance(date_range, dict) and date_range.get("end"):
        return str(date_range["end"])[:10]
    return None


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


# --------------------------------------------------------------------------- pure logic (unit-tested)
def merge_series(old, new):
    """Combine two [{date, close}] lists; newest fetch wins on date collision; sorted asc."""
    by_date = {p["date"]: {"date": p["date"], "close": p["close"]} for p in (old or [])}
    for p in (new or []):
        by_date[p["date"]] = {"date": p["date"], "close": p["close"]}
    return sorted(by_date.values(), key=lambda p: p["date"])


def in_scope(row, min_mentions, asof_date):
    """row = an index.json stock row. Scope = core OR recently-active."""
    if (row.get("total_mentions") or 0) >= min_mentions:
        return True
    lm = row.get("last_mention")
    if lm and lm >= (asof_date - timedelta(days=RECENT_WINDOW_DAYS)).isoformat():
        return True
    return False


def opinion_account_ids():
    """Load the seven accounts allowed to contribute to consensus."""
    config = load_json(BLOGGERS_PATH, default={}) or {}
    account_ids = {
        str(row["id"])
        for row in config.get("bloggers", [])
        if row.get("id") and row.get("signal_type") == "opinion"
    }
    if len(account_ids) != 7:
        raise RuntimeError(f"Expected 7 opinion accounts, found {len(account_ids)}")
    return account_ids


def in_history_scope(stock_doc, asof_date, opinion_ids):
    """52-week history is maintained only for monthly consensus instruments."""
    return is_monthly_report_instrument(stock_doc.get("mentions") or [], opinion_ids, asof_date)


def history_coverage(series, requested_start, asof, attempted_at, failure=None):
    """Describe 52-week coverage independently from latest-price freshness."""
    if not requested_start:
        return None
    points = sorted(
        [p for p in (series or []) if requested_start <= str(p.get("date", ""))[:10] <= asof],
        key=lambda p: p["date"],
    )
    first_date = points[0]["date"] if points else None
    last_date = points[-1]["date"] if points else None
    if failure:
        status, reason = "error", failure
    elif not points:
        status, reason = "unavailable", "no_daily_closes_returned_for_52w_window"
    elif date.fromisoformat(first_date) <= date.fromisoformat(requested_start) + timedelta(days=HISTORY_START_TOLERANCE_DAYS):
        status, reason = "ok", None
    else:
        status, reason = "insufficient_history", f"first_available_close={first_date}"
    return {
        "requested_start": requested_start,
        "asof": asof,
        "attempted_at": attempted_at,
        "status": status,
        "first_available_date": first_date,
        "last_available_date": last_date,
        "reason": reason,
    }


def terminal_history_coverage(status, requested_start, asof, attempted_at, reason):
    if not requested_start:
        return None
    return {
        "requested_start": requested_start,
        "asof": asof,
        "attempted_at": attempted_at,
        "status": status,
        "first_available_date": None,
        "last_available_date": None,
        "reason": reason,
    }


def pick_provider(exchange, currency, ticker_mapped):
    """Return a provider only for a reviewed instrument identity.

    An unmapped symbol must not silently become a US/USD quote just because
    AkShare has a convenient default endpoint.
    """
    if not ticker_mapped:
        return None
    if ticker_mapped and (currency or "USD") != "USD":
        return "eodhd"
    return "akshare_us"


# --------------------------------------------------------------------------- providers
class ProviderError(Exception):
    pass


class AkshareUSProvider:
    name = "akshare_us"

    def __init__(self):
        try:
            import akshare  # noqa
        except ImportError:
            raise ProviderError("akshare not installed (pip install akshare)")
        self._ak = __import__("akshare")

    @staticmethod
    def _symbol(price_symbol):
        # akshare stock_us_daily takes the PLAIN ticker (e.g. 'NVDA'), no prefix
        # (source: https://finance.sina.com.cn/staticdata/us/{symbol}). Identity is correct.
        return price_symbol

    def _fetch_df(self, price_symbol):
        # akshare's Sina parser throws IndexError/SyntaxError/etc when the symbol isn't a
        # US ticker on Sina (foreign/private/unknown names that defaulted to US). Treat that
        # as "no data for this symbol" -> caller marks unavailable, NO retry, NO scary log.
        # Genuine network errors (requests ConnectionError/Timeout) are NOT swallowed -> they
        # propagate so the caller can retry.
        try:
            return self._ak.stock_us_daily(symbol=self._symbol(price_symbol))
        except (IndexError, SyntaxError, KeyError, ValueError, AttributeError, TypeError):
            return None

    def fetch_daily(self, price_symbol, start, end):
        # adjust='' (default) => UNADJUSTED close, to match the raw prices he quotes.
        # The date may be a 'date' COLUMN (newer akshare) or the DatetimeIndex (older
        # akshare deleted the column and put date in the index). reset_index() normalizes
        # both: a named DatetimeIndex becomes a 'date' column; a RangeIndex becomes 'index'
        # while the real 'date' column stays.
        df = self._fetch_df(price_symbol)
        if df is None or len(df) == 0:
            return []
        df = df.reset_index()
        cols = {str(c).lower(): c for c in df.columns}
        ccol = cols.get("close")
        dcol = cols.get("date") or cols.get("index")
        if not ccol or not dcol:
            raise ProviderError(f"akshare: cannot find date/close in {list(df.columns)}")
        out = []
        for _, r in df.iterrows():
            d = str(r[dcol])[:10]                  # 'YYYY-MM-DD' (string/Timestamp/date all OK)
            if start <= d <= end:
                try:
                    out.append({"date": d, "close": float(r[ccol])})
                except (TypeError, ValueError):
                    continue
        return out


class EODHDProvider:
    name = "eodhd"

    def __init__(self, api_key, call_counter):
        try:
            import requests  # noqa
        except ImportError:
            raise ProviderError("requests not installed (pip install requests)")
        self._requests = __import__("requests")
        if not api_key:
            raise ProviderError("EODHD_API_KEY not set in environment")
        self._key = api_key
        self._calls = call_counter   # dict {"n": int} shared across symbols this run

    def fetch_daily(self, price_symbol, start, end):
        if self._calls["n"] >= EODHD_DAILY_CALL_BUDGET:
            raise ProviderError(f"EODHD daily call budget ({EODHD_DAILY_CALL_BUDGET}) exhausted")
        sym = to_eodhd_symbol(price_symbol)
        url = f"https://eodhd.com/api/eod/{sym}"
        params = {"api_token": self._key, "fmt": "json", "from": start, "to": end, "period": "d"}
        self._calls["n"] += 1
        resp = self._requests.get(url, params=params, timeout=30)
        if resp.status_code == 401:
            raise ProviderError("EODHD 401 (bad/expired api_token)")
        if resp.status_code == 404:
            raise ProviderError(f"EODHD 404 (symbol not found: {sym})")
        if resp.status_code == 429:
            raise ProviderError("EODHD 429 (rate limited)")
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            raise ProviderError(f"EODHD unexpected payload for {sym}: {str(data)[:120]}")
        out = []
        for row in data:
            d = str(row.get("date", ""))[:10]
            c = row.get("close")
            if d and c is not None:
                out.append({"date": d, "close": float(c)})
        return out


# --------------------------------------------------------------------------- cache
def cache_path(price_symbol):
    safe = price_symbol.replace("/", "_").replace("\\", "_")
    return CACHE_DIR / f"{safe}.json"


def load_cache(price_symbol):
    return load_json(cache_path(price_symbol), default=None)


def save_cache(price_symbol, currency, price_unit, series, history_52w=None):
    payload = {
        "price_symbol": price_symbol,
        "currency": currency,
        "price_unit": price_unit,
        "series": series,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    if history_52w:
        payload["price_history_52w"] = history_52w
    save_json(cache_path(price_symbol), payload)


# --------------------------------------------------------------------------- fetch one ticker
def fetch_one(stock_doc, tmap, providers, call_counter, force, today_iso, history_start=None):
    """Return (series, freshness status, unit, reason, 52w coverage metadata)."""
    sym = stock_doc["ticker"]
    price_symbol = stock_doc.get("price_symbol")
    currency = stock_doc.get("currency")
    exchange = stock_doc.get("exchange")
    ticker_mapped = bool(stock_doc.get("ticker_mapped"))
    first_mention = stock_doc.get("first_mention") or today_iso
    price_unit = "GBp" if currency == "GBp" else currency
    attempted_at = datetime.now(timezone.utc).isoformat()
    previous_history = stock_doc.get("price_history_52w")

    tmap_entry = tmap.get(sym) if isinstance(tmap.get(sym), dict) else None
    verified = tmap_entry.get("verified", True) if tmap_entry else True

    if not ticker_mapped or not price_symbol or not exchange or not currency:
        reason = "unverified_instrument_identity"
        history = terminal_history_coverage("unverified_symbol", history_start, today_iso, attempted_at, reason) or previous_history
        return [], "unverified_symbol", (currency or ""), reason, history

    if tmap_entry and tmap_entry.get("no_price"):
        # known to have no fetchable source (e.g. Tokyo: EODHD doesn't cover it, akshare no Japan).
        # Skip the doomed API call; board still shows mentions/stance, just no price line.
        reason = "configured_no_price_source"
        history = terminal_history_coverage("unavailable", history_start, today_iso, attempted_at, reason) or previous_history
        return [], "unavailable", price_unit, reason, history

    prov_key = pick_provider(exchange, currency, ticker_mapped)
    if prov_key is None:
        reason = "unverified_instrument_identity"
        history = terminal_history_coverage("unverified_symbol", history_start, today_iso, attempted_at, reason) or previous_history
        return [], "unverified_symbol", price_unit, reason, history
    provider = providers.get(prov_key)
    if provider is None:
        reason = f"provider_unavailable:{prov_key}"
        history_status = "error" if history_start and verified else "unavailable"
        history = terminal_history_coverage(history_status, history_start, today_iso, attempted_at, reason) or previous_history
        return [], "unavailable", price_unit, reason, history

    cache = None if force else load_cache(price_symbol)
    cached_series = (cache or {}).get("series", []) if cache else []
    previous_history = (cache or {}).get("price_history_52w") or previous_history

    # If the cached prefix is too short, request the complete target interval in
    # one provider call. This also fills any missing suffix without spending a
    # second EODHD call. Otherwise append only the normal latest-day gap.
    if history_start and (not cached_series or cached_series[0]["date"] > history_start):
        start = history_start
    elif cached_series:
        last_cached = cached_series[-1]["date"]
        start = (date.fromisoformat(last_cached) + timedelta(days=1)).isoformat()
    else:
        start = first_mention
    end = today_iso

    new = []
    if start <= end:
        last_err = None
        for attempt in range(1, RETRY + 1):
            try:
                new = provider.fetch_daily(price_symbol, start, end)
                last_err = None
                break
            except ProviderError as e:
                last_err = str(e)
                if "budget" in last_err or "401" in last_err:
                    break  # no point retrying these
                time.sleep(2 * attempt)
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                time.sleep(2 * attempt)
        if last_err:
            log(f"    {sym} ({price_symbol} via {prov_key}) fetch failed: {last_err}")
            history = previous_history
            if history_start:
                cached_coverage = history_coverage(cached_series, history_start, today_iso, attempted_at)
                history = cached_coverage if cached_coverage["status"] == "ok" else history_coverage(
                    cached_series, history_start, today_iso, attempted_at, failure=last_err
                )
            if cached_series:
                # keep serving cached series; mark partial (couldn't extend to today)
                return cached_series, "partial", price_unit, last_err, history
            status = "unverified_symbol" if not verified else "unavailable"
            return [], status, price_unit, last_err, history

    series = merge_series(cached_series, new)
    if not series:
        status = "unverified_symbol" if not verified else "unavailable"
        reason = "unverified_ticker_mapping" if not verified else "no_daily_closes_returned"
        history_status = "unverified_symbol" if not verified else "unavailable"
        history = terminal_history_coverage(history_status, history_start, today_iso, attempted_at, reason) or previous_history
        return [], status, price_unit, reason, history

    # freshness: ok if the latest close is within ~5 calendar days of today
    latest = date.fromisoformat(series[-1]["date"])
    status = "ok" if (date.fromisoformat(today_iso) - latest).days <= 5 else "partial"
    reason = None if status == "ok" else f"stale_series_latest_close={latest.isoformat()}"
    history = history_coverage(series, history_start, today_iso, attempted_at) if history_start else previous_history

    save_cache(price_symbol, currency, price_unit, series, history)
    return series, status, price_unit, reason, history


def annotate_existing(scope, tmap):
    """Give legacy non-ok price records a durable, machine-readable reason.

    This is intentionally network-free so the first backfill can be made fully
    auditable without spending another provider call on symbols already marked.
    """
    counts = {"annotated": 0, "already_explained": 0}
    for row in scope:
        stock_path = STOCKS_DIR / f"{row['ticker']}.json"
        doc = load_json(stock_path, default=None)
        if not doc:
            continue
        status = doc.get("price_status", "pending")
        if status == "ok":
            if doc.pop("price_reason", None) is not None:
                save_json(stock_path, doc)
            continue
        if doc.get("price_reason"):
            counts["already_explained"] += 1
            continue
        entry = tmap.get(doc.get("ticker")) if isinstance(tmap.get(doc.get("ticker")), dict) else {}
        series = doc.get("price_series") or []
        if entry.get("no_price"):
            reason = "configured_no_price_source"
        elif status == "unverified_symbol":
            reason = "unverified_ticker_mapping"
        elif status == "partial" and series:
            reason = f"stale_series_latest_close={series[-1].get('date', 'unknown')}"
        elif status == "unavailable":
            reason = "no_daily_closes_returned"
        else:
            reason = f"status_requires_review:{status}"
        doc["price_reason"] = reason
        save_json(stock_path, doc)
        counts["annotated"] += 1
    return counts


# --------------------------------------------------------------------------- main
def build_providers(args):
    """Lazily build only the providers we can. Missing deps/keys -> provider absent."""
    providers = {}
    call_counter = {"n": 0}
    try:
        providers["akshare_us"] = AkshareUSProvider()
    except ProviderError as e:
        log(f"NOTE: akshare provider unavailable -> US tickers will be 'unavailable'. ({e})")
    try:
        providers["eodhd"] = EODHDProvider(os.environ.get("EODHD_API_KEY", "").strip(), call_counter)
    except ProviderError as e:
        log(f"NOTE: EODHD provider unavailable -> non-US tickers will be 'unavailable'. ({e})")
    return providers, call_counter


def provider_test(args):
    """One run validates connectivity AND every mapped symbol's exchange code:
    US sanity via akshare, and EVERY non-USD ticker_map entry via EODHD."""
    providers, _ = build_providers(args)
    today = (args.asof or today_et())
    start = (date.fromisoformat(today) - timedelta(days=21)).isoformat()
    tmap = load_json(TMAP_PATH, default={}) or {}

    # US sanity (akshare — free, doesn't touch EODHD quota)
    us_checks = ["NVDA", "AAPL"]
    # non-US mapped symbols (eodhd). By DEFAULT only the UNCERTAIN ones — verified:false,
    # or an unconfirmed exchange suffix — to save the free-tier 20-calls/day budget.
    # Pass --all-codes to hit every non-US code.
    UNCONFIRMED_SUFFIXES = (".T",)   # Tokyo never got a working EODHD code (.TW Taiwan confirmed via 6451)
    eodhd_checks = []
    for sym, e in tmap.items():
        if sym.startswith("_") or not isinstance(e, dict):
            continue
        if e.get("no_price"):
            continue                      # no fetchable source — nothing to test
        ps = e.get("price_symbol")
        cur = e.get("currency", "USD")
        if not (ps and cur != "USD"):
            continue
        uncertain = (not e.get("verified")) or ps.endswith(UNCONFIRMED_SUFFIXES)
        if getattr(args, "all_codes", False) or uncertain:
            eodhd_checks.append((ps, cur, sym, e.get("verified")))
    eodhd_checks.sort()

    log(f"as-of {today}; window from {start}\n")
    log("--- akshare (US) ---")
    p = providers.get("akshare_us")
    for sym in us_checks:
        if not p:
            log(f"  [akshare] {sym}: provider unavailable"); continue
        try:
            rows = p.fetch_daily(sym, start, today)
            log(f"  [akshare] {sym:6} {len(rows):>3} rows; last={rows[-1] if rows else 'NONE'}")
        except Exception as e:
            log(f"  [akshare] {sym:6} FAILED — {type(e).__name__}: {e}")

    log(f"\n--- EODHD (non-US) — testing {len(eodhd_checks)} code(s) "
        f"[default: only unverified; use --all-codes for all] ---")
    p = providers.get("eodhd")
    for ps, cur, sym, verified in eodhd_checks:
        eod = to_eodhd_symbol(ps)
        flag = "" if verified else "  (verified:false — confirm here)"
        if not p:
            log(f"  [eodhd] {sym:8} {ps:10}->{eod:12} provider unavailable (no key?)"); continue
        try:
            rows = p.fetch_daily(eod, start, today)
            last = rows[-1] if rows else "NONE"
            log(f"  [eodhd] {sym:8} {ps:10}->{eod:12} {len(rows):>3} rows; last={last} [{cur}]{flag}")
        except Exception as e:
            log(f"  [eodhd] {sym:8} {ps:10}->{eod:12} FAILED — {type(e).__name__}: {e}{flag}")
    log("\nReminder: LSE may report in pence (GBp). RPI should be ~hundreds; if single digits, prices are in GBP and need x100.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="", help="only this ticker (test)")
    ap.add_argument("--min-mentions", type=int, default=DEFAULT_MIN_MENTIONS)
    ap.add_argument("--asof", default="", help="pin 'today' for the 30d window + freshness (YYYY-MM-DD)")
    ap.add_argument("--history-weeks", type=int, choices=(52,), default=0,
                    help="extend report-scope caches backwards for a rolling 52-week return")
    ap.add_argument("--history-scope", choices=("recent-28d",), default="recent-28d",
                    help="scope for --history-weeks (only current 28-day report rows)")
    ap.add_argument("--force", action="store_true", help="ignore cache; full re-fetch from first_mention")
    ap.add_argument("--provider-test", action="store_true", help="just check provider connectivity")
    ap.add_argument("--all-codes", action="store_true", help="provider-test: hit ALL non-US codes, not just unverified")
    ap.add_argument("--annotate-existing", action="store_true",
                    help="add price_reason to existing non-ok in-scope records without provider calls")
    args = ap.parse_args()

    if args.provider_test:
        provider_test(args)
        return

    today_iso = args.asof or manifest_report_date() or today_et()
    asof_date = date.fromisoformat(today_iso)
    history_start = (asof_date - timedelta(weeks=args.history_weeks)).isoformat() if args.history_weeks else None

    index = load_json(INDEX_PATH, default=None)
    if not index:
        log(f"No {INDEX_PATH}. Run build_db.py first.")
        sys.exit(1)
    tmap = load_json(TMAP_PATH, default={}) or {}
    rows = index.get("stocks", [])

    # ---- scope
    if args.ticker:
        scope = [r for r in rows if r["ticker"].upper() == args.ticker.upper()]
        if not scope:
            log(f"{args.ticker} not in index."); sys.exit(1)
    else:
        scope = [r for r in rows if in_scope(r, args.min_mentions, asof_date)]

    docs_by_ticker = {}
    history_tickers = set()
    if history_start:
        opinions = opinion_account_ids()
        for row in scope:
            stock_doc = load_json(STOCKS_DIR / f"{row['ticker']}.json", default=None)
            if not stock_doc:
                continue
            docs_by_ticker[row["ticker"]] = stock_doc
            if in_history_scope(stock_doc, asof_date, opinions):
                history_tickers.add(row["ticker"])

        # Complete the report-visible history before refreshing the broader
        # price universe.  Otherwise unrelated non-US symbols can consume the
        # entire EODHD budget before a report ticker is reached.
        scope.sort(key=lambda row: (
            0 if row["ticker"] in history_tickers else 1,
            0 if (docs_by_ticker.get(row["ticker"], {}).get("currency") or "USD") == "USD" else 1,
            str(row["ticker"]).casefold(),
        ))
    log(f"In scope: {len(scope)} tickers "
        f"(min_mentions={args.min_mentions}, recent<= {RECENT_WINDOW_DAYS}d, asof={today_iso}).")
    if history_start:
        log(f"52-week history scope: {len(history_tickers)} monthly consensus ticker(s); target start={history_start}.")

    if args.annotate_existing:
        counts = annotate_existing(scope, tmap)
        log(f"Annotated {counts['annotated']} existing non-ok price record(s); "
            f"{counts['already_explained']} already had a reason.")
        return

    providers, call_counter = build_providers(args)

    counts = {"ok": 0, "partial": 0, "unavailable": 0, "unverified_symbol": 0}
    history_counts = {"ok": 0, "insufficient_history": 0, "pending": 0, "error": 0,
                      "unavailable": 0, "unverified_symbol": 0}
    for i, row in enumerate(scope, 1):
        sym = row["ticker"]
        stock_path = STOCKS_DIR / f"{sym}.json"
        doc = docs_by_ticker.get(sym) or load_json(stock_path, default=None)
        if not doc:
            log(f"    {sym}: no stock file, skipping."); continue

        ticker_history_start = history_start if sym in history_tickers else None
        series, status, price_unit, reason, history = fetch_one(
            doc, tmap, providers, call_counter, args.force, today_iso, ticker_history_start
        )
        # write back ONLY the price fields (no baked returns; render derives those)
        doc["price_series"] = series
        doc["price_status"] = status
        doc["price_unit"] = price_unit
        if reason:
            doc["price_reason"] = reason
        else:
            doc.pop("price_reason", None)
        if history:
            doc["price_history_52w"] = history
        doc["price_updated_at"] = datetime.now(timezone.utc).isoformat()
        save_json(stock_path, doc)

        counts[status] = counts.get(status, 0) + 1
        if ticker_history_start:
            history_status = (history or {}).get("status", "pending")
            history_counts[history_status] = history_counts.get(history_status, 0) + 1
        if i % 10 == 0 or i == len(scope):
            log(f"  {i}/{len(scope)} done | EODHD calls used: {call_counter['n']}")

    # mirror status into index rows so the board knows without opening each file
    by_ticker = {r["ticker"] for r in scope}
    for r in rows:
        if r["ticker"] in by_ticker:
            sp = STOCKS_DIR / f"{r['ticker']}.json"
            d = load_json(sp, default={})
            r["price_status"] = d.get("price_status", "pending")
            if d.get("price_history_52w"):
                r["price_history_52w_status"] = d["price_history_52w"].get("status", "pending")
    index["meta"]["prices_updated_at"] = datetime.now(timezone.utc).isoformat()
    save_json(INDEX_PATH, index)

    log("")
    log("===== prices summary =====")
    for k in ("ok", "partial", "unverified_symbol", "unavailable"):
        log(f"  {k:<18}: {counts.get(k, 0)}")
    log(f"  EODHD calls used  : {call_counter['n']} / {EODHD_DAILY_CALL_BUDGET}")
    if history_start:
        log("  52-week coverage  : " + ", ".join(f"{k}={v}" for k, v in history_counts.items() if v))
    log("==========================")
    if counts.get("unverified_symbol") or counts.get("unavailable"):
        log("Non-ok symbols stay on the demo's B-plan (his self-quoted prices). "
            "Check the EODHD suffix map / akshare symbol format for any non-US misses.")


if __name__ == "__main__":
    main()
