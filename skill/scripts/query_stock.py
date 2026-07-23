#!/usr/bin/env python3
"""Run deterministic stock Q&A pre-processing against the verified cloud cache."""
from __future__ import annotations

import argparse
import sys

from analyze_stock import analyze
from snapshot_sync import sync
from stock_store import StockStore, StockStoreError


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
    try:
        _, stock = StockStore(cache / "data" / "db").resolve_stock(args.ticker)
    except StockStoreError as exc:
        raise SystemExit(str(exc)) from exc
    import datetime
    result = analyze(stock, datetime.date.fromisoformat(args.as_of) if args.as_of else None, args.blogger)
    import json
    print(json.dumps({"manifest": manifest, "analysis": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
