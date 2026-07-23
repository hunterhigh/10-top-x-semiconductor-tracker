"""verify_data.py — 推送 GitHub 前的数据健康检查 + 写 manifest.json（多博主版）
Usage: python verify_data.py [--db <path>] [--require-price-scope]
默认 db 路径: 脚本上级/data/db (和 render/build_db 一致，全博主共享)
"""
import json, os, sys, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from storage_layout import (
    SHARDED_LAYOUT_VERSION,
    StorageLayoutError,
    detect_storage_layout,
    iter_stock_documents,
    price_cache_relative,
    shard_stats,
    stock_document_path,
    storage_contract,
    validate_snapshot_layout,
)

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')   # Windows console defaults to GBK, which can't print ✅/⚠️

def _argval(flag,default=None):
    for i,x in enumerate(sys.argv):
        if x==flag and i+1<len(sys.argv): return sys.argv[i+1]
        if x.startswith(flag+'='): return x.split('=',1)[1]
    return default

def _intarg(flag, default):
    try:
        return int(_argval(flag, default))
    except (TypeError, ValueError):
        print(f"ERROR: {flag} must be an integer.")
        sys.exit(2)

SCRIPT_DIR = Path(__file__).resolve().parent
_db_override = _argval('--db') or os.environ.get('SERENITY_DB')
DB = Path(_db_override).resolve() if _db_override else SCRIPT_DIR.parent / 'data' / 'db'
STOCKS_DIR = DB / 'stocks'
DATA_DIR = DB.parent
CONFIG_PATH = SCRIPT_DIR.parent / 'config' / 'bloggers.json'
PROFILE_CONFIG_PATH = SCRIPT_DIR.parent / 'config' / 'blogger_profiles.json'
PROFILE_DB_PATH = DB / 'blogger_profiles.json'

sys.path.insert(0, str(SCRIPT_DIR.parent / 'skill' / 'scripts'))
from report_scope import monthly_history_scope  # noqa: E402
from price_history_status import effective_history_status, valid_degraded_state  # noqa: E402
PRICE_WINDOW_DAYS = _intarg('--price-window-days', 30)
PRICE_MIN_MENTIONS = _intarg('--price-min-mentions', 50)
REQUIRE_PRICE_SCOPE = '--require-price-scope' in sys.argv
REQUIRE_PRICE_HISTORY_52W = '--require-price-history-52w' in sys.argv

print(f"DB path: {DB}")
print(f"Stocks dir: {STOCKS_DIR}")
print()

INDEX_PATH = DB / 'index.json'
try:
    index_preview = json.load(open(INDEX_PATH, encoding='utf-8')) if INDEX_PATH.exists() else None
    storage_version = detect_storage_layout(DB, index=index_preview)
    files = [str(path) for path in iter_stock_documents(DB, index=index_preview, version=storage_version)]
except (OSError, json.JSONDecodeError, StorageLayoutError) as exc:
    print(f"ERROR: invalid storage layout: {exc}"); sys.exit(1)
if not files:
    print("ERROR: no stock JSON files found!"); sys.exit(1)

total_mentions = 0
total_priced = 0
earliest = None
latest = None
errors = []
tickers = []
mentions_by_blogger = {}   # blogger_id -> mention count, tallied across ALL stock files
instrument_counts = {"verified": 0, "identified": 0, "unverified": 0}
market_missing = 0
price_unavailable = 0

for f in files:
    fname = os.path.basename(f)
    try:
        d = json.load(open(f, encoding='utf-8'))
    except Exception as e:
        errors.append(f"{fname}: invalid JSON — {e}"); continue
    tk = d.get('ticker')
    if not tk:
        errors.append(f"{fname}: missing 'ticker'"); continue
    tickers.append(tk)
    instrument = d.get("instrument") or {}
    status = instrument.get("verification_status") or d.get("verification_status") or "unverified"
    if status not in instrument_counts:
        errors.append(f"{fname}: invalid instrument verification_status {status!r}")
        status = "unverified"
    instrument_counts[status] += 1
    if not instrument:
        errors.append(f"{fname}: missing instrument identity object")
    elif not instrument.get("display_code") or not instrument.get("display_name") or not instrument.get("display_market"):
        errors.append(f"{fname}: incomplete instrument display identity")
    if status == "verified":
        if not (instrument.get("market") and instrument.get("currency") and instrument.get("price_symbol")):
            errors.append(f"{fname}: verified instrument missing market/currency/price_symbol")
        if d.get("ticker_mapped") is not True:
            errors.append(f"{fname}: verified instrument must set ticker_mapped=true")
    elif d.get("ticker_mapped"):
        errors.append(f"{fname}: unverified instrument must not be price-mapped")
    if not instrument.get("market"):
        market_missing += 1
    mentions = d.get('mentions') or []
    total_mentions += len(mentions)
    ps = d.get('price_series') or []
    if ps:
        total_priced += 1
    if d.get("price_status") in {"unavailable", "unverified_symbol"}:
        price_unavailable += 1
    for m in mentions:
        dt = m.get('date')
        # Every published mention must retain enough source data for a v2
        # evidence card.  The renderer never manufactures a date, reason,
        # source link, or timestamp fallback.
        required = ('tweet_id', 'blogger_id', 'created_at', 'date', 'text', 'url')
        absent = [key for key in required if m.get(key) in (None, '')]
        if absent:
            errors.append(f"{fname}: mention missing {', '.join(absent)}")
        if not isinstance(m.get('reasons'), list):
            errors.append(f"{fname}: mention reasons must be a list")
        if dt:
            if earliest is None or dt < earliest: earliest = dt
            if latest is None or dt > latest: latest = dt
        bid = m.get('blogger_id') or 'unknown'
        mentions_by_blogger[bid] = mentions_by_blogger.get(bid, 0) + 1

size_bytes = sum(os.path.getsize(f) for f in files)
size_mb = size_bytes / (1024*1024)

print(f"Tickers: {len(tickers)}")
print(f"Total mentions: {total_mentions}")
print(f"Priced tickers: {total_priced}")
print(f"Date range: {earliest} — {latest}")
print(f"Total size: {size_mb:.1f} MB ({len(files)} files)")
print(f"Instrument identities: {instrument_counts}; market pending review: {market_missing}")

# index.json check + the price coverage summary used by dashboards and release checks
idx = DB / 'index.json'
index_data = None
if idx.exists():
    print(f"index.json: present ({idx.stat().st_size / 1024:.0f} KB)")
    try:
        index_data = json.load(open(idx, encoding='utf-8'))
    except Exception as e:
        errors.append(f"index.json: invalid JSON — {e}")
else:
    print("index.json: NOT found (optional, render doesn't need it)")

storage_summary = storage_contract(storage_version)
try:
    stock_layout = validate_snapshot_layout(DB)
    stock_stats = stock_layout["stock_documents"]
    cache_root = DATA_DIR / "prices_cache"
    if storage_version == SHARDED_LAYOUT_VERSION:
        flat_cache = list(cache_root.glob("*.json"))
        if flat_cache:
            raise StorageLayoutError(f"v2 price cache still contains {len(flat_cache)} flat JSON file(s)")
        cache_files = sorted(cache_root.rglob("*.json"))
        seen_symbols = set()
        for cache_file in cache_files:
            cache_doc = json.load(open(cache_file, encoding='utf-8'))
            symbol = str(cache_doc.get('price_symbol') or '')
            if not symbol or symbol in seen_symbols:
                raise StorageLayoutError(f"missing or duplicate price_symbol in {cache_file}")
            seen_symbols.add(symbol)
            expected = cache_root.joinpath(*price_cache_relative(symbol).parts).resolve()
            if cache_file.resolve() != expected:
                raise StorageLayoutError(f"price cache path does not match price_symbol: {cache_file}")
    else:
        cache_files = sorted(cache_root.glob("*.json"))
    cache_stats = shard_stats(cache_files, cache_root)
    if storage_version == SHARDED_LAYOUT_VERSION and not cache_stats['within_limit']:
        raise StorageLayoutError(
            f"A price-cache shard has {cache_stats['max_shard_width']} files; publish is blocked"
        )
    declared = ((index_data or {}).get('meta') or {}).get('storage_layout') or {}
    storage_summary = dict(declared or storage_contract(storage_version))
    storage_summary['stock_documents'] = {**storage_summary.get('stock_documents', {}), **stock_stats}
    storage_summary['price_cache'] = {**storage_summary.get('price_cache', {}), **cache_stats}
    print(f"Storage layout: v{storage_version}; stocks={stock_stats}; price_cache={cache_stats}")
    if stock_stats.get('warning') or cache_stats.get('warning'):
        print("WARNING: storage shard width is at or above 900 files")
except (OSError, json.JSONDecodeError, StorageLayoutError) as exc:
    errors.append(f"storage layout invalid: {exc}")

price_scope = {
    "window_days": PRICE_WINDOW_DAYS,
    "min_mentions": PRICE_MIN_MENTIONS,
    "tickers": 0,
    "status_counts": {},
    "pending": 0,
    "reason_counts": {},
    "missing_reason": 0,
}
if index_data:
    today_et = datetime.datetime.now(ZoneInfo('America/New_York')).date()
    cutoff = (today_et - datetime.timedelta(days=PRICE_WINDOW_DAYS)).isoformat()
    scope_rows = [
        row for row in index_data.get('stocks', [])
        if (row.get('total_mentions') or 0) >= PRICE_MIN_MENTIONS
        or (row.get('last_mention') and row['last_mention'] >= cutoff)
    ]
    status_counts = {}
    reason_counts = {}
    missing_reason = []
    for row in scope_rows:
        status = row.get('price_status') or 'pending'
        status_counts[status] = status_counts.get(status, 0) + 1
        if status in {'partial', 'unavailable', 'unverified_symbol'}:
            stock_file = stock_document_path(DB, row, version=storage_version, must_exist=True)
            stock_doc = json.load(open(stock_file, encoding='utf-8')) if stock_file.exists() else {}
            reason = stock_doc.get('price_reason')
            if reason:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
            else:
                missing_reason.append(row['ticker'])
    price_scope.update({
        "tickers": len(scope_rows),
        "status_counts": status_counts,
        "pending": status_counts.get('pending', 0),
        "reason_counts": reason_counts,
        "missing_reason": len(missing_reason),
    })
    print(f"Price scope: {len(scope_rows)} tickers (last {PRICE_WINDOW_DAYS}d or >= {PRICE_MIN_MENTIONS} mentions)")
    print(f"Price statuses: {status_counts or {'pending': len(scope_rows)}}")
    if REQUIRE_PRICE_SCOPE and price_scope['pending']:
        errors.append(f"price scope has {price_scope['pending']} pending ticker(s)")
    if REQUIRE_PRICE_SCOPE and missing_reason:
        errors.append(f"price scope has {len(missing_reason)} non-ok ticker(s) without price_reason: {', '.join(missing_reason[:20])}")
elif REQUIRE_PRICE_SCOPE:
    errors.append("cannot verify price scope without index.json")

price_history_52w = {
    "report_window_days": 28,
    "weeks": 52,
    "asof": latest,
    "target_start": None,
    "tickers": 0,
    "status_counts": {},
    "missing_reason": 0,
    "provider_state_counts": {},
    "retry_queue": 0,
    "scope_counts": {
        "monthly_consensus": 0,
        "top_pick_cards": 10,
        "top_pick_unique": 0,
        "overlap": 0,
    },
}
if index_data and latest:
    history_asof = datetime.date.fromisoformat(latest)
    target_start = history_asof - datetime.timedelta(weeks=52)
    try:
        blogger_config = json.load(open(CONFIG_PATH, encoding='utf-8'))
        tracked_ids = [str(row['id']) for row in blogger_config.get('bloggers', []) if row.get('id')]
    except Exception as exc:
        tracked_ids = []
        errors.append(f"cannot load tracked account roster for 52-week scope: {exc}")
    if len(tracked_ids) != 10 or len(set(tracked_ids)) != 10:
        errors.append(f"52-week scope expected 10 unique tracked accounts, found {len(set(tracked_ids))}")

    all_stock_rows = []
    for row in index_data.get('stocks', []):
        stock_file = stock_document_path(DB, row, version=storage_version, must_exist=True)
        stock_doc = json.load(open(stock_file, encoding='utf-8')) if stock_file.exists() else {}
        if stock_doc:
            all_stock_rows.append((row, stock_doc))
    scope = monthly_history_scope((stock_doc for _, stock_doc in all_stock_rows), tracked_ids, history_asof)
    scope_ids = {
        str((doc.get('instrument') or {}).get('instrument_id') or doc.get('ticker') or '')
        for doc in scope['docs']
    }
    history_rows = [
        (row, stock_doc) for row, stock_doc in all_stock_rows
        if str((stock_doc.get('instrument') or {}).get('instrument_id') or stock_doc.get('ticker') or '') in scope_ids
    ]
    status_counts = {}
    provider_state_counts = {}
    missing_history_reason = []
    blocking = []
    for row, stock_doc in history_rows:
        identity = stock_doc.get('instrument') or {}
        coverage = stock_doc.get('price_history_52w') or {}
        status = effective_history_status(
            identity.get('verification_status'),
            stock_doc.get('price_status'),
            coverage.get('status'),
        )
        status_counts[status] = status_counts.get(status, 0) + 1
        source_state = stock_doc.get('price_source_state') or {}
        source_status = source_state.get('status') or 'unknown'
        provider_state_counts[source_status] = provider_state_counts.get(source_status, 0) + 1

        reason = coverage.get('reason') or stock_doc.get('price_reason')
        if status in {'insufficient_history', 'pending', 'unavailable', 'unverified_symbol', 'error'} and not reason:
            missing_history_reason.append(row['ticker'])
        if status in {'pending', 'error'} and not valid_degraded_state(status, source_state, reason):
            blocking.append(f"{row['ticker']}={status}")
        if status == 'ok':
            first = coverage.get('first_available_date')
            requested = coverage.get('requested_start')
            if not first or not requested:
                blocking.append(f"{row['ticker']}=ok_without_coverage_dates")
            elif datetime.date.fromisoformat(first) > datetime.date.fromisoformat(requested) + datetime.timedelta(days=7):
                blocking.append(f"{row['ticker']}=ok_but_late_start:{first}")
        if status == 'insufficient_history' and not coverage.get('attempted_at'):
            blocking.append(f"{row['ticker']}=insufficient_without_attempt")

    queue_path = DATA_DIR / 'price_enrichment_queue.json'
    try:
        retry_queue = len((json.load(open(queue_path, encoding='utf-8')).get('items') or {})) if queue_path.exists() else 0
    except Exception as exc:
        retry_queue = 0
        errors.append(f"cannot read price enrichment queue: {exc}")
    price_history_52w.update({
        "asof": history_asof.isoformat(),
        "target_start": target_start.isoformat(),
        "tickers": len(history_rows),
        "status_counts": status_counts,
        "missing_reason": len(missing_history_reason),
        "provider_state_counts": provider_state_counts,
        "retry_queue": retry_queue,
        "scope_counts": {
            "monthly_consensus": len(scope['monthly_instrument_ids']),
            "top_pick_cards": len(scope['top_picks']),
            "top_pick_unique": len(scope['top_pick_instrument_ids']),
            "overlap": len(scope['overlap_instrument_ids']),
        },
    })
    print(f"52-week price history: {len(history_rows)} union ticker(s); target={target_start.isoformat()}")
    print(f"52-week scope counts: {price_history_52w['scope_counts']}")
    print(f"52-week statuses: {status_counts or {'pending': len(history_rows)}}")
    if REQUIRE_PRICE_HISTORY_52W and blocking:
        errors.append(f"52-week price history has {len(blocking)} blocking ticker(s): {', '.join(blocking[:20])}")
    if REQUIRE_PRICE_HISTORY_52W and missing_history_reason:
        errors.append(f"52-week price history has {len(missing_history_reason)} non-ok ticker(s) without reason: {', '.join(missing_history_reason[:20])}")
elif REQUIRE_PRICE_HISTORY_52W:
    errors.append("cannot verify 52-week price history without index.json and a data cutoff")

# per-blogger state file check (needed for daily automation) + mention breakdown
bloggers = []
if CONFIG_PATH.exists():
    try:
        bloggers = json.load(open(CONFIG_PATH, encoding='utf-8')).get('bloggers', [])
    except Exception as e:
        errors.append(f"config/bloggers.json: invalid JSON — {e}")
else:
    print(f"\nWARNING: {CONFIG_PATH} not found — cannot check per-blogger state files.")

# Source profiles are a first-class public interface.  Validate the editorial
# copy separately from factual coverage stats, and never silently publish a
# directory where a tracked account lacks a profile or a valid X destination.
profile_copy = {}
if not PROFILE_CONFIG_PATH.exists():
    errors.append(f"missing profile configuration: {PROFILE_CONFIG_PATH}")
else:
    try:
        profile_copy = {p.get('blogger_id'): p for p in json.load(open(PROFILE_CONFIG_PATH, encoding='utf-8')).get('profiles', []) if p.get('blogger_id')}
    except Exception as e:
        errors.append(f"blogger_profiles.json: invalid JSON — {e}")
expected_ids = {b.get('id') for b in bloggers}
if bloggers and set(profile_copy) != expected_ids:
    errors.append(f"profile roster mismatch: config={sorted(profile_copy)} tracked={sorted(expected_ids)}")
for b in bloggers:
    bid = b['id']; p = profile_copy.get(bid, {})
    if not str(b.get('x_url') or '').startswith('https://x.com/'):
        errors.append(f"{bid}: invalid or missing X URL")
    if not p.get('reviewed_at'):
        errors.append(f"{bid}: profile copy missing reviewed_at")
    # Profile configuration intentionally stores only stable, reviewed bio
    # copy plus source links.  Coverage and activity facts are rebuilt in the
    # database, not hand-maintained as presentation tags.
    for field in ('bio',):
        value = p.get(field)
        if not isinstance(value, dict) or any(not value.get(locale) for locale in ('en', 'zh', 'zh-Hant')):
            errors.append(f"{bid}: profile {field} must provide en/zh/zh-Hant")

profile_stats = {}
if not PROFILE_DB_PATH.exists():
    errors.append(f"missing profile statistics: {PROFILE_DB_PATH}")
else:
    try:
        profile_stats = {p.get('blogger_id'): p for p in json.load(open(PROFILE_DB_PATH, encoding='utf-8')).get('profiles', []) if p.get('blogger_id')}
    except Exception as e:
        errors.append(f"blogger_profiles.json in db: invalid JSON — {e}")
if bloggers and set(profile_stats) != expected_ids:
    errors.append(f"profile statistics roster mismatch: stats={sorted(profile_stats)} tracked={sorted(expected_ids)}")
for b in bloggers:
    stat = profile_stats.get(b['id'], {})
    sample = stat.get('sample') or {}
    if sample.get('mentions') != mentions_by_blogger.get(b['id'], 0):
        errors.append(f"{b['id']}: profile sample mention count disagrees with DB")
    rate = sample.get('original_link_rate')
    if not isinstance(rate, (int, float)) or not 0 <= rate <= 1:
        errors.append(f"{b['id']}: profile original_link_rate must be in [0,1]")
    if b.get('signal_type', 'opinion') != 'opinion' and sample.get('explicit_stance_counts') is not None:
        errors.append(f"{b['id']}: independent signal profile must not expose stance statistics")

print(f"\nTracked bloggers: {len(bloggers)}")
missing_state = []
for b in bloggers:
    bid = b['id']
    bdir = DATA_DIR / 'bloggers' / bid
    raw_p = bdir / 'raw_tweets.json'
    ext_p = bdir / 'extracted.json'
    n_mentions = mentions_by_blogger.get(bid, 0)
    raw_ok = raw_p.exists()
    ext_ok = ext_p.exists()
    status = "ok" if (raw_ok and ext_ok) else "MISSING FILES"
    print(f"  @{bid:<16} raw={'y' if raw_ok else 'N'} extracted={'y' if ext_ok else 'N'}  "
          f"mentions_in_db={n_mentions}  [{status}]")
    if not (raw_ok and ext_ok):
        missing_state.append(bid)

unaccounted = set(mentions_by_blogger) - {b['id'] for b in bloggers}
if unaccounted:
    print(f"\nWARNING: mentions found for blogger_id(s) not in config/bloggers.json: {sorted(unaccounted)}")

if errors:
    print(f"\n⚠️  {len(errors)} ERRORS:")
    for e in errors: print(f"  {e}")
    sys.exit(1)
else:
    print("\n✅ All files valid.")

# write manifest
manifest = {
    "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "tickers": len(tickers),
    "total_mentions": total_mentions,
    "priced_tickers": total_priced,
    "date_range": [earliest, latest],
    "size_mb": round(size_mb, 1),
    "tracked_bloggers": len(bloggers),
    "mentions_by_blogger": mentions_by_blogger,
    "bloggers_missing_state_files": missing_state,
    "instrument_coverage": {
        **instrument_counts,
        "market_pending_review": market_missing,
        "price_unavailable_or_unverified": price_unavailable,
    },
    "storage_layout": storage_summary,
    "price_scope": price_scope,
    "price_history_52w": price_history_52w,
    "profile_coverage": {
        "editorial_profiles": len(profile_copy),
        "statistical_profiles": len(profile_stats),
        "profile_errors": len([e for e in errors if 'profile' in e.lower()]),
    },
}
mf = DB / 'manifest.json'
if index_data is not None:
    index_data.setdefault('meta', {})['storage_layout'] = storage_summary
    json.dump(index_data, open(idx, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
json.dump(manifest, open(mf, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
print(f"\nManifest written: {mf}")
print(json.dumps(manifest, indent=2))
