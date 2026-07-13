#!/usr/bin/env python3
"""
prices.py — multi-blogger tracker, fill price_series (NO LLM)

Reads the per-ticker files built by build_db.py and fills in the price fields
that build_db deliberately left empty:
    price_series  (full daily closes, first_mention -> today)  <- the chart data
    price_status  (ok / partial / unavailable / unverified_symbol)

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
  - FULL-SERIES CACHE (data/prices_cache/{price_symbol}.json): first run
    back-fills the complete series from first_mention -> today; later daily
    runs fetch ONLY the gap (last cached day -> today) and append. The series
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
DATA_DIR = SCRIPT_DIR.parent / "data"
DB_DIR = DATA_DIR / "db"
STOCKS_DIR = DB_DIR / "stocks"
INDEX_PATH = DB_DIR / "index.json"
TMAP_PATH = DATA_DIR / "ticker_map.json"
CACHE_DIR = DATA_DIR / "prices_cache"

DEFAULT_MIN_MENTIONS = 50      # core set ~= the 41 deep-divable tickers; tune with --min-mentions
RECENT_WINDOW_DAYS = 30        # matches the initial backfill and 28-day Month view
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


def pick_provider(exchange, currency, ticker_mapped):
    """Return provider key. _default (US/USD) -> akshare; mapped non-US -> eodhd."""
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


def save_cache(price_symbol, currency, price_unit, series):
    save_json(cache_path(price_symbol), {
        "price_symbol": price_symbol,
        "currency": currency,
        "price_unit": price_unit,
        "series": series,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    })


# --------------------------------------------------------------------------- fetch one ticker
def fetch_one(stock_doc, tmap, providers, call_counter, force, today_iso):
    """Returns (series, status, price_unit). Mutates nothing on disk here."""
    sym = stock_doc["ticker"]
    price_symbol = stock_doc.get("price_symbol") or sym
    currency = stock_doc.get("currency") or "USD"
    exchange = stock_doc.get("exchange") or "US"
    ticker_mapped = bool(stock_doc.get("ticker_mapped"))
    first_mention = stock_doc.get("first_mention") or today_iso
    price_unit = "GBp" if currency == "GBp" else currency

    tmap_entry = tmap.get(sym) if isinstance(tmap.get(sym), dict) else None
    verified = tmap_entry.get("verified", True) if tmap_entry else True

    if tmap_entry and tmap_entry.get("no_price"):
        # known to have no fetchable source (e.g. Tokyo: EODHD doesn't cover it, akshare no Japan).
        # Skip the doomed API call; board still shows mentions/stance, just no price line.
        return [], "unavailable", price_unit

    prov_key = pick_provider(exchange, currency, ticker_mapped)
    provider = providers.get(prov_key)
    if provider is None:
        return [], "unavailable", price_unit   # provider unusable (e.g. no akshare / no key)

    cache = None if force else load_cache(price_symbol)
    cached_series = (cache or {}).get("series", []) if cache else []

    if cached_series:
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
            if cached_series:
                # keep serving cached series; mark partial (couldn't extend to today)
                return cached_series, "partial", price_unit
            status = "unverified_symbol" if not verified else "unavailable"
            return [], status, price_unit

    series = merge_series(cached_series, new)
    if not series:
        return [], ("unverified_symbol" if not verified else "unavailable"), price_unit

    # freshness: ok if the latest close is within ~5 calendar days of today
    latest = date.fromisoformat(series[-1]["date"])
    status = "ok" if (date.fromisoformat(today_iso) - latest).days <= 5 else "partial"

    save_cache(price_symbol, currency, price_unit, series)
    return series, status, price_unit


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
    ap.add_argument("--force", action="store_true", help="ignore cache; full re-fetch from first_mention")
    ap.add_argument("--provider-test", action="store_true", help="just check provider connectivity")
    ap.add_argument("--all-codes", action="store_true", help="provider-test: hit ALL non-US codes, not just unverified")
    args = ap.parse_args()

    if args.provider_test:
        provider_test(args)
        return

    today_iso = args.asof or today_et()
    asof_date = date.fromisoformat(today_iso)

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
    log(f"In scope: {len(scope)} tickers "
        f"(min_mentions={args.min_mentions}, recent<= {RECENT_WINDOW_DAYS}d, asof={today_iso}).")

    providers, call_counter = build_providers(args)

    counts = {"ok": 0, "partial": 0, "unavailable": 0, "unverified_symbol": 0}
    for i, row in enumerate(scope, 1):
        sym = row["ticker"]
        stock_path = STOCKS_DIR / f"{sym}.json"
        doc = load_json(stock_path, default=None)
        if not doc:
            log(f"    {sym}: no stock file, skipping."); continue

        series, status, price_unit = fetch_one(doc, tmap, providers, call_counter,
                                                args.force, today_iso)
        # write back ONLY the price fields (no baked returns; render derives those)
        doc["price_series"] = series
        doc["price_status"] = status
        doc["price_unit"] = price_unit
        doc["price_updated_at"] = datetime.now(timezone.utc).isoformat()
        save_json(stock_path, doc)

        counts[status] = counts.get(status, 0) + 1
        if i % 10 == 0 or i == len(scope):
            log(f"  {i}/{len(scope)} done | EODHD calls used: {call_counter['n']}")

    # mirror status into index rows so the board knows without opening each file
    by_ticker = {r["ticker"] for r in scope}
    for r in rows:
        if r["ticker"] in by_ticker:
            sp = STOCKS_DIR / f"{r['ticker']}.json"
            d = load_json(sp, default={})
            r["price_status"] = d.get("price_status", "pending")
    index["meta"]["prices_updated_at"] = datetime.now(timezone.utc).isoformat()
    save_json(INDEX_PATH, index)

    log("")
    log("===== prices summary =====")
    for k in ("ok", "partial", "unverified_symbol", "unavailable"):
        log(f"  {k:<18}: {counts.get(k, 0)}")
    log(f"  EODHD calls used  : {call_counter['n']} / {EODHD_DAILY_CALL_BUDGET}")
    log("==========================")
    if counts.get("unverified_symbol") or counts.get("unavailable"):
        log("Non-ok symbols stay on the demo's B-plan (his self-quoted prices). "
            "Check the EODHD suffix map / akshare symbol format for any non-US misses.")


if __name__ == "__main__":
    main()
