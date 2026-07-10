#!/usr/bin/env python3
"""
fetch_all.py — loop fetch_tweets.py over every blogger in config/bloggers.json.

Local/manual convenience wrapper. In GitHub Actions, prefer a matrix strategy
(one job per blogger) so failures/rate limits on one account don't block the
others — see .github/workflows/hourly-sync.yml.

Run:
    export TWITTERAPI_KEY="your_key_here"
    python fetch_all.py                # incremental, all bloggers
    python fetch_all.py --backfill     # full history, all bloggers
    python fetch_all.py --only zephyr_z9 jukan05   # subset
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR.parent / "config" / "bloggers.json"


def load_bloggers() -> list[dict]:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return cfg["bloggers"]


def main():
    ap = argparse.ArgumentParser(description="Fetch tweets for all tracked bloggers")
    ap.add_argument("--backfill", action="store_true", help="ignore saved state, pull full history")
    ap.add_argument("--only", nargs="*", default=None, help="restrict to these blogger ids")
    args = ap.parse_args()

    bloggers = load_bloggers()
    if args.only:
        wanted = set(args.only)
        bloggers = [b for b in bloggers if b["id"] in wanted]

    failures = []
    for b in bloggers:
        bid = b["id"]
        # aleabitoreddit's history was reused from the Serenity Tracker handover,
        # not re-fetched here; incremental runs still pick up new tweets fine.
        print(f"\n===== fetching @{bid} ({b['display_name']}) =====", flush=True)
        cmd = [sys.executable, str(SCRIPT_DIR / "fetch_tweets.py"), "--user", bid]
        if args.backfill:
            cmd.append("--backfill")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            failures.append(bid)

    print("\n===== fetch_all summary =====")
    print(f"  bloggers attempted : {len(bloggers)}")
    print(f"  failures           : {failures or 'none'}")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
