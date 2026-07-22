#!/usr/bin/env python3
"""Plan or execute a bounded per-account incremental dashboard refresh.

The default is a dry run.  ``--execute`` performs only incremental fetches,
re-extracts the overlapping latest ET day (which also captures raw-only posts),
then rebuilds the factual database.  It never requests a full backfill.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent.parent
ET = ZoneInfo("America/New_York")


def watermark(path: Path) -> str | None:
    if not path.exists(): return None
    rows = json.loads(path.read_text(encoding="utf-8"))
    dates = []
    for row in rows:
        value = row.get("created_at") or row.get("createdAt")
        try: dates.append(parsedate_to_datetime(value).astimezone(ET).date())
        except Exception: pass
    return (max(dates) - timedelta(days=1)).isoformat() if dates else None


def command(*items: str) -> list[str]: return [sys.executable, *items]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="perform network/API work; default only prints the bounded plan")
    parser.add_argument("--skip-prices", action="store_true")
    parser.add_argument("--report-date", help="also render this ET cutoff after a successful rebuild")
    args = parser.parse_args()
    bloggers = json.loads((ROOT / "config" / "bloggers.json").read_text(encoding="utf-8")).get("bloggers", [])
    steps: list[tuple[str, list[str]]] = []
    for blogger in bloggers:
        bid = blogger["id"]
        since = watermark(ROOT / "data" / "bloggers" / bid / "raw_tweets.json")
        if not since: raise RuntimeError(f"No raw-data watermark for {bid}; use the explicit backfill workflow instead")
        steps += [(f"fetch {bid}", command("scripts/fetch_tweets.py", "--user", bid)), (f"extract {bid} since {since}", command("scripts/extract.py", "--user", bid, "--since", since))]
    steps.append(("rebuild database", command("scripts/build_db.py")))
    steps.append(("resolve instrument identities", command("scripts/resolve_tickers_eodhd.py", "--apply")))
    steps.append(("rebuild verified database", command("scripts/build_db.py")))
    if not args.skip_prices:
        price_cmd = command("scripts/prices.py", "--history-weeks", "52", "--history-scope", "recent-28d")
        if args.report_date: price_cmd.extend(["--asof", args.report_date])
        steps.append(("refresh prices", price_cmd))
    steps.append(("refresh avatars", command("scripts/refresh_avatars.py")))
    if args.report_date:
        payload = f"dashboard-payload-{args.report_date}.json"
        steps.append(("build validated dashboard payload", command("skill/scripts/dashboard_payload.py", args.report_date, "--output", payload)))
        steps.append(("render v2 dashboard", command("skill/scripts/render_dashboard.py", "--input", payload, "--output", f"consensus-tracker-{args.report_date}.html")))
    for label, cmd in steps: print(label + ": " + subprocess.list2cmdline(cmd))
    if not args.execute: return 0
    for label, cmd in steps:
        print("RUN " + label, flush=True)
        subprocess.run(cmd, cwd=ROOT, check=True)
    return 0


if __name__ == "__main__": raise SystemExit(main())
