#!/usr/bin/env python3
"""Run deterministic stock Q&A pre-processing against the verified cloud cache."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from analyze_stock import analyze
from snapshot_sync import sync


def main() -> int:
    # Windows PowerShell may default stdout to GBK while source posts contain
    # emoji and non-Latin instrument names.
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker", help="Exact display code from the tracked dataset")
    parser.add_argument("--as-of")
    parser.add_argument("--blogger")
    args = parser.parse_args()
    cache, manifest, _ = sync()
    index = json.loads((cache / "data" / "db" / "index.json").read_text(encoding="utf-8"))
    code = args.ticker.upper()
    candidates = [row for row in index.get("stocks", []) if str((row.get("instrument") or {}).get("display_code") or row.get("ticker") or "").upper() == code]
    if len(candidates) != 1:
        raise SystemExit(f"Ticker is not uniquely tracked: {args.ticker}")
    stock = cache / "data" / "db" / "stocks" / f"{candidates[0]['ticker']}.json"
    if not stock.is_file():
        raise SystemExit(f"Tracked stock document is unavailable: {args.ticker}")
    import datetime
    result = analyze(stock, datetime.date.fromisoformat(args.as_of) if args.as_of else None, args.blogger)
    print(json.dumps({"manifest": manifest, "analysis": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
