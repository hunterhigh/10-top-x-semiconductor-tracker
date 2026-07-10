#!/usr/bin/env python3
"""
extract_all.py — loop extract.py over every blogger in config/bloggers.json.

Local/manual convenience wrapper. In GitHub Actions, prefer a matrix strategy
(one job per blogger) for parallelism and isolated failure — see
.github/workflows/hourly-sync.yml.

Run:
    export ANTHROPIC_API_KEY="your_key_here"
    python extract_all.py --since 2026-04-01     # windowed, all bloggers
    python extract_all.py --only zephyr_z9        # subset
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
    ap = argparse.ArgumentParser(description="Extract stances for all tracked bloggers")
    ap.add_argument("--since", default="", help="only process tweets on/after YYYY-MM-DD")
    ap.add_argument("--limit", type=int, default=0, help="only process first N tweets per blogger (test)")
    ap.add_argument("--model", default="", help="override model (default: extract.py's fixed Opus)")
    ap.add_argument("--only", nargs="*", default=None, help="restrict to these blogger ids")
    args = ap.parse_args()

    bloggers = load_bloggers()
    if args.only:
        wanted = set(args.only)
        bloggers = [b for b in bloggers if b["id"] in wanted]

    failures = []
    for b in bloggers:
        bid = b["id"]
        print(f"\n===== extracting @{bid} ({b['display_name']}) =====", flush=True)
        cmd = [sys.executable, str(SCRIPT_DIR / "extract.py"), "--user", bid]
        if args.since:
            cmd += ["--since", args.since]
        if args.limit:
            cmd += ["--limit", str(args.limit)]
        if args.model:
            cmd += ["--model", args.model]
        result = subprocess.run(cmd)
        if result.returncode != 0:
            failures.append(bid)

    print("\n===== extract_all summary =====")
    print(f"  bloggers attempted : {len(bloggers)}")
    print(f"  failures           : {failures or 'none'}")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
