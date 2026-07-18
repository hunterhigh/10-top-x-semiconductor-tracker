#!/usr/bin/env python3
"""Fetch and cache the ten public X profile avatars as data URIs.

Run explicitly during the refresh stage.  The dashboard builder reads the
resulting cache but never performs network I/O itself, keeping rendering
deterministic and offline-safe.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent


def fetch(url: str) -> str | None:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read(600_000).decode("utf-8", "ignore")
        match = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', body, re.I)
        if not match: return None
        image_url = match.group(1).replace("&amp;", "&")
        with urlopen(Request(image_url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as response:
            raw, mime = response.read(), response.headers.get_content_type()
        return f"data:{mime if mime.startswith('image/') else 'image/jpeg'};base64," + base64.b64encode(raw).decode("ascii")
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "bloggers.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "avatar_cache.json")
    args = parser.parse_args()
    roster = json.loads(args.config.read_text(encoding="utf-8")).get("bloggers", [])
    previous = json.loads(args.output.read_text(encoding="utf-8")) if args.output.exists() else {}
    output = dict(previous)
    failures = []
    for blogger in roster:
        image = fetch(blogger["x_url"])
        if image: output[blogger["id"]] = image
        else: failures.append(blogger["id"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"cached": len(output), "failures": failures}, ensure_ascii=False))
    return 0 if not failures else 2


if __name__ == "__main__": raise SystemExit(main())
