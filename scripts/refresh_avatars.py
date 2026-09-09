#!/usr/bin/env python3
"""Fetch and cache the ten public X profile avatars as data URIs.

Run explicitly during the refresh stage.  The dashboard builder reads the
resulting cache but never performs network I/O itself, keeping rendering
deterministic and offline-safe.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent


def fetch(url: str) -> tuple[str | None, str | None]:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read(600_000).decode("utf-8", "ignore")
        match = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', body, re.I)
        if not match:
            return None, "profile response did not contain an og:image"
        image_url = match.group(1).replace("&amp;", "&")
        with urlopen(Request(image_url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as response:
            raw, mime = response.read(), response.headers.get_content_type()
        if not raw or not mime.startswith("image/"):
            return None, f"avatar response was not an image ({mime})"
        return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii"), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def valid_cached_avatar(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("data:image/") or ";base64," not in value:
        return False
    try:
        return bool(base64.b64decode(value.split(",", 1)[1], validate=True))
    except (ValueError, binascii.Error):
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "bloggers.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "avatar_cache.json")
    parser.add_argument("--status-output", type=Path)
    args = parser.parse_args()
    roster = json.loads(args.config.read_text(encoding="utf-8")).get("bloggers", [])
    previous = json.loads(args.output.read_text(encoding="utf-8")) if args.output.exists() else {}
    output = dict(previous)
    refreshed = []
    stale_cache = []
    missing = []
    errors = {}
    for blogger in roster:
        blogger_id = blogger["id"]
        image, error = fetch(blogger["x_url"])
        if image:
            output[blogger_id] = image
            refreshed.append(blogger_id)
        else:
            errors[blogger_id] = error
            if valid_cached_avatar(previous.get(blogger_id)):
                stale_cache.append(blogger_id)
            else:
                missing.append(blogger_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    cached = sum(valid_cached_avatar(output.get(blogger["id"])) for blogger in roster)
    summary = {
        "cached": cached,
        "refreshed": refreshed,
        "stale_cache": stale_cache,
        "missing": missing,
        "errors": errors,
    }
    print(json.dumps(summary, ensure_ascii=False))
    if args.status_output:
        args.status_output.parent.mkdir(parents=True, exist_ok=True)
        args.status_output.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    if stale_cache:
        print(f"::warning::Avatar refresh failed for {len(stale_cache)} account(s); retained valid cached avatars.")
    if missing:
        print(f"::error::No valid current or cached avatar for: {', '.join(missing)}")
    return 0 if not missing else 2


if __name__ == "__main__": raise SystemExit(main())
