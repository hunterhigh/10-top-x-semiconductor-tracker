"""verify_data.py — 推送 GitHub 前的数据健康检查 + 写 manifest.json（多博主版）
Usage: python verify_data.py [--db <path>]
默认 db 路径: 脚本上级/data/db (和 render/build_db 一致，全博主共享)
"""
import json, glob, os, sys, datetime
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')   # Windows console defaults to GBK, which can't print ✅/⚠️

def _argval(flag,default=None):
    for i,x in enumerate(sys.argv):
        if x==flag and i+1<len(sys.argv): return sys.argv[i+1]
        if x.startswith(flag+'='): return x.split('=',1)[1]
    return default

SCRIPT_DIR = Path(__file__).resolve().parent
_db_override = _argval('--db') or os.environ.get('SERENITY_DB')
DB = Path(_db_override).resolve() if _db_override else SCRIPT_DIR.parent / 'data' / 'db'
STOCKS_DIR = DB / 'stocks'
DATA_DIR = DB.parent
CONFIG_PATH = SCRIPT_DIR.parent / 'config' / 'bloggers.json'

print(f"DB path: {DB}")
print(f"Stocks dir: {STOCKS_DIR}")
print()

files = sorted(glob.glob(str(STOCKS_DIR / '*.json')))
if not files:
    print("ERROR: no stock JSON files found!"); sys.exit(1)

total_mentions = 0
total_priced = 0
earliest = None
latest = None
errors = []
tickers = []
mentions_by_blogger = {}   # blogger_id -> mention count, tallied across ALL stock files

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
    mentions = d.get('mentions') or []
    total_mentions += len(mentions)
    ps = d.get('price_series') or []
    if ps:
        total_priced += 1
    for m in mentions:
        dt = m.get('date')
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

# index.json check
idx = DB / 'index.json'
if idx.exists():
    print(f"index.json: present ({idx.stat().st_size / 1024:.0f} KB)")
else:
    print("index.json: NOT found (optional, render doesn't need it)")

# per-blogger state file check (needed for daily automation) + mention breakdown
bloggers = []
if CONFIG_PATH.exists():
    try:
        bloggers = json.load(open(CONFIG_PATH, encoding='utf-8')).get('bloggers', [])
    except Exception as e:
        errors.append(f"config/bloggers.json: invalid JSON — {e}")
else:
    print(f"\nWARNING: {CONFIG_PATH} not found — cannot check per-blogger state files.")

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
}
mf = DB / 'manifest.json'
json.dump(manifest, open(mf, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
print(f"\nManifest written: {mf}")
print(json.dumps(manifest, indent=2))
