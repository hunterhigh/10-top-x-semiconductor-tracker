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
QUEUE_PATH = DATA_DIR / "price_enrichment_queue.json"
BLOGGERS_PATH = PROJECT_DIR / "config" / "bloggers.json"

# Keep the installed Skill and repository pipeline on one eligibility rule.
sys.path.insert(0, str(PROJECT_DIR / "skill" / "scripts"))
from report_scope import monthly_history_scope  # noqa: E402

DEFAULT_MIN_MENTIONS = 50      # core set ~= the 41 deep-divable tickers; tune with --min-mentions
RECENT_WINDOW_DAYS = 30        # matches the initial backfill and 28-day Month view
HISTORY_START_TOLERANCE_DAYS = 7
RETRY = 3
PACING_SEC = 0.4               # polite pause between symbols
EODHD_DAILY_CALL_BUDGET = 20   # free tier; we self-limit and stop cleanly when hit
DEFAULT_MAINTENANCE_LIMIT = 100


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


def tracked_account_ids():
    """Load the ten accounts scored by the supplied dashboard contract."""
    config = load_json(BLOGGERS_PATH, default={}) or {}
    account_ids = [
        str(row["id"])
        for row in config.get("bloggers", [])
        if row.get("id")
    ]
    if len(account_ids) != 10 or len(set(account_ids)) != 10:
        raise RuntimeError(f"Expected 10 unique tracked accounts, found {len(set(account_ids))}")
    return account_ids


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


def provider_state(previous, provider, provider_symbol, status, reason=None, *,
                   checked_at=None, retry_after_hours=None, http_status=None):
    """Build durable per-provider attempt metadata without ticker-specific rules."""
    previous = previous if isinstance(previous, dict) else {}
    attempts = int(previous.get("attempts") or 0)
    if status != "supported":
        attempts += 1
    checked = checked_at or datetime.now(timezone.utc).isoformat()
    next_retry_at = None
    if retry_after_hours is not None:
        next_retry_at = (datetime.fromisoformat(checked) + timedelta(hours=retry_after_hours)).isoformat()
    return {
        "provider": provider,
        "provider_symbol": provider_symbol,
        "status": status,
        "reason": reason,
        "checked_at": checked,
        "attempts": attempts,
        "next_retry_at": next_retry_at,
        "http_status": http_status,
    }


def retry_is_due(state, now=None):
    if not isinstance(state, dict) or not state.get("next_retry_at"):
        return True
    now = now or datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(state["next_retry_at"]) <= now
    except (TypeError, ValueError):
        return True


def queue_key(doc):
    identity = doc.get("instrument") or {}
    return str(identity.get("instrument_id") or f"UNVERIFIED:{doc.get('ticker', 'UNKNOWN')}")


def update_enrichment_queue(queue, doc, requested_start):
    """Persist report-scope work so a later run resumes instead of starting over."""
    key = queue_key(doc)
    coverage = doc.get("price_history_52w") or {}
    source = doc.get("price_source_state") or {}
    if coverage.get("status") in {"ok", "insufficient_history"}:
        queue.get("items", {}).pop(key, None)
        return
    if coverage.get("status") == "unverified_symbol":
        queue.get("items", {}).pop(key, None)
        return
    if source.get("status") == "unsupported" and not source.get("next_retry_at"):
        queue.get("items", {}).pop(key, None)
        return
    queue.setdefault("items", {})[key] = {
        "instrument_id": key,
        "ticker": doc.get("ticker"),
        "provider": source.get("provider"),
        "provider_symbol": source.get("provider_symbol"),
        "requested_start": requested_start,
        "coverage_status": coverage.get("status") or "pending",
        "source_status": source.get("status") or "symbol_unresolved",
        "reason": coverage.get("reason") or source.get("reason"),
        "attempts": int(source.get("attempts") or 0),
        "next_retry_at": source.get("next_retry_at"),
        "priority": "monthly_report",
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


class ProviderAuthError(ProviderError):
    """A global configuration failure. Publishing must stop."""


class ProviderDeferred(ProviderError):
    """Provider capacity/rate limit; retry in a later run."""


class ProviderSymbolNotFound(ProviderError):
    """The selected provider does not currently recognize this symbol."""


class UnavailableProvider:
    """Delay a missing-provider failure until that provider is actually needed."""

    def __init__(self, error):
        self.error = error

    def fetch_daily(self, price_symbol, start, end):
        raise self.error


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
            raise ProviderAuthError("EODHD_API_KEY not set in environment")
        self._key = api_key
        self._calls = call_counter   # dict {"n": int} shared across symbols this run

    def fetch_daily(self, price_symbol, start, end):
        if self._calls["n"] >= EODHD_DAILY_CALL_BUDGET:
            raise ProviderDeferred(f"EODHD daily call budget ({EODHD_DAILY_CALL_BUDGET}) exhausted")
        sym = to_eodhd_symbol(price_symbol)
        url = f"https://eodhd.com/api/eod/{sym}"
        params = {"api_token": self._key, "fmt": "json", "from": start, "to": end, "period": "d"}
        self._calls["n"] += 1
        resp = self._requests.get(url, params=params, timeout=30)
        if resp.status_code in {401, 403}:
            raise ProviderAuthError("EODHD 401 (bad/expired api_token)")
        if resp.status_code == 402:
            raise ProviderDeferred("EODHD 402 (account quota or subscription limit)")
        if resp.status_code == 404:
            raise ProviderSymbolNotFound(f"EODHD 404 (symbol not found: {sym})")
        if resp.status_code == 429:
            raise ProviderDeferred("EODHD 429 (rate limited)")
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

    def resolve_symbol(self, price_symbol, currency=None):
        """Return one unambiguous exact EODHD symbol, or None.

        EODHD documents the Search API as the supported way to look up a code
        across exchanges. We only auto-apply an exact base-code match and, when
        present, an exact currency match; ambiguity remains queued for review.
        """
        if self._calls["n"] >= EODHD_DAILY_CALL_BUDGET:
            raise ProviderDeferred(f"EODHD daily call budget ({EODHD_DAILY_CALL_BUDGET}) exhausted")
        base = str(price_symbol).split(".", 1)[0]
        url = f"https://eodhd.com/api/search/{base}"
        params = {"api_token": self._key, "fmt": "json", "type": "stock"}
        self._calls["n"] += 1
        resp = self._requests.get(url, params=params, timeout=30)
        if resp.status_code in {401, 403}:
            raise ProviderAuthError("EODHD 401 (bad/expired api_token)")
        if resp.status_code in {402, 429}:
            raise ProviderDeferred(f"EODHD {resp.status_code} (search capacity unavailable)")
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            raise ProviderError(f"EODHD unexpected search payload for {base}: {str(data)[:120]}")
        candidates = []
        for row in data:
            code = str(row.get("Code") or row.get("code") or "")
            exchange = str(row.get("Exchange") or row.get("exchange") or "")
            row_currency = str(row.get("Currency") or row.get("currency") or "")
            if code.casefold() != base.casefold() or not exchange:
                continue
            if currency and row_currency and row_currency.casefold() != str(currency).casefold():
                continue
            candidates.append(f"{code}.{exchange}")
        unique = sorted(set(candidates))
        return unique[0] if len(unique) == 1 else None


# --------------------------------------------------------------------------- cache
def cache_path(price_symbol):
    safe = price_symbol.replace("/", "_").replace("\\", "_")
    return CACHE_DIR / f"{safe}.json"


def load_cache(price_symbol):
    return load_json(cache_path(price_symbol), default=None)


def save_cache(price_symbol, currency, price_unit, series, history_52w=None, source_state=None):
    payload = {
        "price_symbol": price_symbol,
        "currency": currency,
        "price_unit": price_unit,
        "series": series,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    if history_52w:
        payload["price_history_52w"] = history_52w
    if source_state:
        payload["price_source_state"] = source_state
    save_json(cache_path(price_symbol), payload)


# --------------------------------------------------------------------------- fetch one ticker
def fetch_one(stock_doc, tmap, providers, call_counter, force, today_iso, history_start=None):
    """Return series, display status, unit, reason, coverage and provider state."""
    sym = stock_doc["ticker"]
    price_symbol = stock_doc.get("price_symbol")
    currency = stock_doc.get("currency")
    exchange = stock_doc.get("exchange")
    ticker_mapped = bool(stock_doc.get("ticker_mapped"))
    first_mention = stock_doc.get("first_mention") or today_iso
    price_unit = "GBp" if currency == "GBp" else currency
    attempted_at = datetime.now(timezone.utc).isoformat()
    previous_history = stock_doc.get("price_history_52w")
    previous_source = stock_doc.get("price_source_state") or {}

    tmap_entry = tmap.get(sym) if isinstance(tmap.get(sym), dict) else None
    verified = tmap_entry.get("verified", True) if tmap_entry else True

    if not ticker_mapped or not price_symbol or not exchange or not currency:
        reason = "unverified_instrument_identity"
        history = terminal_history_coverage("unverified_symbol", history_start, today_iso, attempted_at, reason) or previous_history
        source = provider_state(previous_source, None, price_symbol, "symbol_unresolved", reason, checked_at=attempted_at)
        return [], "unverified_symbol", (currency or ""), reason, history, source

    if tmap_entry and tmap_entry.get("no_price"):
        # known to have no fetchable source (e.g. Tokyo: EODHD doesn't cover it, akshare no Japan).
        # Skip the doomed API call; board still shows mentions/stance, just no price line.
        reason = "configured_no_price_source"
        history = terminal_history_coverage("unavailable", history_start, today_iso, attempted_at, reason) or previous_history
        source = provider_state(previous_source, None, price_symbol, "unsupported", reason, checked_at=attempted_at)
        return [], "unavailable", price_unit, reason, history, source

    prov_key = pick_provider(exchange, currency, ticker_mapped)
    if prov_key is None:
        reason = "unverified_instrument_identity"
        history = terminal_history_coverage("unverified_symbol", history_start, today_iso, attempted_at, reason) or previous_history
        source = provider_state(previous_source, None, price_symbol, "symbol_unresolved", reason, checked_at=attempted_at)
        return [], "unverified_symbol", price_unit, reason, history, source
    provider = providers.get(prov_key)
    if provider is None:
        reason = f"provider_unavailable:{prov_key}"
        history_status = "error" if history_start and verified else "unavailable"
        history = terminal_history_coverage(history_status, history_start, today_iso, attempted_at, reason) or previous_history
        source = provider_state(previous_source, prov_key, price_symbol, "retryable_error", reason,
                                checked_at=attempted_at, retry_after_hours=3)
        return [], "unavailable", price_unit, reason, history, source

    cache = None if force else load_cache(price_symbol)
    cached_series = (cache or {}).get("series", []) if cache else []
    previous_history = (cache or {}).get("price_history_52w") or previous_history
    previous_source = (cache or {}).get("price_source_state") or previous_source

    # A known provider-local miss is re-probed on a cooldown, not every three hours.
    if previous_source.get("status") == "unsupported" and not retry_is_due(previous_source):
        reason = previous_source.get("reason") or "provider_symbol_unsupported"
        history = previous_history or terminal_history_coverage(
            "unavailable", history_start, today_iso, attempted_at, reason
        )
        status = "partial" if cached_series else "unavailable"
        return cached_series, status, price_unit, reason, history, previous_source

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
    provider_symbol_used = price_symbol
    source = None
    if start <= end:
        last_err = None
        for attempt in range(1, RETRY + 1):
            try:
                new = provider.fetch_daily(provider_symbol_used, start, end)
                last_err = None
                break
            except ProviderAuthError:
                raise
            except ProviderSymbolNotFound as e:
                last_err = str(e)
                try:
                    resolved = provider.resolve_symbol(price_symbol, currency) if hasattr(provider, "resolve_symbol") else None
                    if resolved:
                        provider_symbol_used = resolved
                        new = provider.fetch_daily(provider_symbol_used, start, end)
                        last_err = None
                        log(f"    {sym}: provider symbol resolved to {provider_symbol_used}")
                        break
                except ProviderAuthError:
                    raise
                except ProviderDeferred as deferred:
                    last_err = str(deferred)
                    source = provider_state(previous_source, prov_key, provider_symbol_used, "deferred",
                                            last_err, checked_at=attempted_at, retry_after_hours=3)
                    break
                except Exception as resolve_error:
                    last_err = f"symbol_resolution_failed:{type(resolve_error).__name__}:{resolve_error}"
                source = provider_state(previous_source, prov_key, to_eodhd_symbol(price_symbol), "unsupported",
                                        "provider_symbol_not_found_after_search", checked_at=attempted_at,
                                        retry_after_hours=24 * 30, http_status=404)
                break
            except ProviderDeferred as e:
                last_err = str(e)
                source = provider_state(previous_source, prov_key, to_eodhd_symbol(price_symbol), "deferred",
                                        last_err, checked_at=attempted_at, retry_after_hours=3,
                                        http_status=429 if "429" in last_err else None)
                break
            except ProviderError as e:
                last_err = str(e)
                time.sleep(2 * attempt)
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                time.sleep(2 * attempt)
        if last_err:
            if source is None:
                source = provider_state(previous_source, prov_key, price_symbol, "retryable_error", last_err,
                                        checked_at=attempted_at, retry_after_hours=3)
            log(f"    {sym} ({price_symbol} via {prov_key}) fetch failed: {last_err}")
            history = previous_history
            if history_start:
                cached_coverage = history_coverage(cached_series, history_start, today_iso, attempted_at)
                if cached_coverage["status"] == "ok":
                    history = cached_coverage
                elif source["status"] == "unsupported":
                    history = terminal_history_coverage("unavailable", history_start, today_iso, attempted_at, source["reason"])
                elif source["status"] == "deferred":
                    history = terminal_history_coverage("pending", history_start, today_iso, attempted_at, last_err)
                else:
                    history = history_coverage(cached_series, history_start, today_iso, attempted_at, failure=last_err)
            if cached_series:
                # keep serving cached series; mark partial (couldn't extend to today)
                return cached_series, "partial", price_unit, last_err, history, source
            status = "unverified_symbol" if not verified else "unavailable"
            return [], status, price_unit, last_err, history, source

    series = merge_series(cached_series, new)
    if not series:
        status = "unverified_symbol" if not verified else "unavailable"
        reason = "unverified_ticker_mapping" if not verified else "no_daily_closes_returned"
        history_status = "unverified_symbol" if not verified else "unavailable"
        history = terminal_history_coverage(history_status, history_start, today_iso, attempted_at, reason) or previous_history
        source = provider_state(previous_source, prov_key, price_symbol, "unsupported", reason,
                                checked_at=attempted_at, retry_after_hours=24 * 30)
        return [], status, price_unit, reason, history, source

    # freshness: ok if the latest close is within ~5 calendar days of today
    latest = date.fromisoformat(series[-1]["date"])
    status = "ok" if (date.fromisoformat(today_iso) - latest).days <= 5 else "partial"
    reason = None if status == "ok" else f"stale_series_latest_close={latest.isoformat()}"
    history = history_coverage(series, history_start, today_iso, attempted_at) if history_start else previous_history

    source = provider_state(previous_source, prov_key, provider_symbol_used, "supported", None, checked_at=attempted_at)
    save_cache(price_symbol, currency, price_unit, series, history, source)
    return series, status, price_unit, reason, history, source


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
    except ProviderAuthError as e:
        providers["eodhd"] = UnavailableProvider(e)
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
                    help="scope for --history-weeks (monthly rows union all ten monthly top picks)")
    ap.add_argument("--force", action="store_true", help="ignore cache; full re-fetch from first_mention")
    ap.add_argument("--provider-test", action="store_true", help="just check provider connectivity")
    ap.add_argument("--all-codes", action="store_true", help="provider-test: hit ALL non-US codes, not just unverified")
    ap.add_argument("--annotate-existing", action="store_true",
                    help="add price_reason to existing non-ok in-scope records without provider calls")
    ap.add_argument("--maintenance-limit", type=int, default=DEFAULT_MAINTENANCE_LIMIT,
                    help="maximum non-report symbols refreshed per run; 0 means no limit")
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
    history_scope_summary = None
    tracked_ids = tracked_account_ids() if history_start else []
    for row in scope:
        stock_doc = load_json(STOCKS_DIR / f"{row['ticker']}.json", default=None)
        if not stock_doc:
            continue
        docs_by_ticker[row["ticker"]] = stock_doc
    if history_start:
        history_scope_summary = monthly_history_scope(docs_by_ticker.values(), tracked_ids, asof_date)
        history_tickers = {
            str(doc.get("ticker")) for doc in history_scope_summary["docs"] if doc.get("ticker")
        }

    # Complete report-visible work first, then rotate a bounded maintenance
    # batch ordered by oldest successful update. This keeps three-hour runs
    # bounded and prevents unrelated symbols from consuming the provider quota.
    if not args.ticker and not args.annotate_existing and args.maintenance_limit > 0:
        report_rows = [row for row in scope if row["ticker"] in history_tickers]
        maintenance_rows = [row for row in scope if row["ticker"] not in history_tickers]
        maintenance_rows.sort(key=lambda row: (
            str(docs_by_ticker.get(row["ticker"], {}).get("price_updated_at") or ""),
            str(row["ticker"]).casefold(),
        ))
        scope = report_rows + maintenance_rows[:args.maintenance_limit]
    scope.sort(key=lambda row: (
        0 if row["ticker"] in history_tickers else 1,
        0 if (docs_by_ticker.get(row["ticker"], {}).get("currency") or "USD") == "USD" else 1,
        str(row["ticker"]).casefold(),
    ))
    log(f"In scope: {len(scope)} tickers "
        f"(min_mentions={args.min_mentions}, recent<= {RECENT_WINDOW_DAYS}d, asof={today_iso}).")
    if history_start:
        monthly_count = len(history_scope_summary["monthly_instrument_ids"])
        pick_count = len(history_scope_summary["top_pick_instrument_ids"])
        overlap_count = len(history_scope_summary["overlap_instrument_ids"])
        log(f"52-week history scope: {len(history_tickers)} unique ticker(s) "
            f"(monthly={monthly_count}, top-picks={pick_count}, overlap={overlap_count}); "
            f"target start={history_start}.")

    if args.annotate_existing:
        counts = annotate_existing(scope, tmap)
        log(f"Annotated {counts['annotated']} existing non-ok price record(s); "
            f"{counts['already_explained']} already had a reason.")
        return

    providers, call_counter = build_providers(args)
    queue = load_json(QUEUE_PATH, default={}) or {}
    queue.setdefault("version", 1)
    queue.setdefault("items", {})
    if history_start:
        current_keys = {queue_key(docs_by_ticker[ticker]) for ticker in history_tickers}
        queue["items"] = {
            key: item for key, item in queue["items"].items()
            if item.get("priority") != "monthly_report" or key in current_keys
        }

    counts = {"ok": 0, "partial": 0, "unavailable": 0, "unverified_symbol": 0}
    source_counts = {}
    history_counts = {"ok": 0, "insufficient_history": 0, "pending": 0, "error": 0,
                      "unavailable": 0, "unverified_symbol": 0}
    for i, row in enumerate(scope, 1):
        sym = row["ticker"]
        stock_path = STOCKS_DIR / f"{sym}.json"
        doc = docs_by_ticker.get(sym) or load_json(stock_path, default=None)
        if not doc:
            log(f"    {sym}: no stock file, skipping."); continue

        ticker_history_start = history_start if sym in history_tickers else None
        series, status, price_unit, reason, history, source = fetch_one(
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
        if source:
            doc["price_source_state"] = source
        doc["price_updated_at"] = datetime.now(timezone.utc).isoformat()
        if ticker_history_start:
            update_enrichment_queue(queue, doc, ticker_history_start)
        save_json(stock_path, doc)

        counts[status] = counts.get(status, 0) + 1
        source_status = (source or {}).get("status", "unknown")
        source_counts[source_status] = source_counts.get(source_status, 0) + 1
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
    queue["updated_at"] = datetime.now(timezone.utc).isoformat()
    queue["report_asof"] = today_iso
    save_json(QUEUE_PATH, queue)

    log("")
    log("===== prices summary =====")
    for k in ("ok", "partial", "unverified_symbol", "unavailable"):
        log(f"  {k:<18}: {counts.get(k, 0)}")
    log(f"  EODHD calls used  : {call_counter['n']} / {EODHD_DAILY_CALL_BUDGET}")
    if history_start:
        log("  52-week coverage  : " + ", ".join(f"{k}={v}" for k, v in history_counts.items() if v))
        log("  provider states   : " + ", ".join(f"{k}={v}" for k, v in sorted(source_counts.items())))
        log(f"  retry queue       : {len(queue.get('items', {}))}")
    log("==========================")
    if counts.get("unverified_symbol") or counts.get("unavailable"):
        log("Non-ok symbols stay on the demo's B-plan (his self-quoted prices). "
            "Check the EODHD suffix map / akshare symbol format for any non-US misses.")


if __name__ == "__main__":
    main()
