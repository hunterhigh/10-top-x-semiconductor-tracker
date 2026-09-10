#!/usr/bin/env python3
"""Validate and expose the configured X-account roster.

The ``bloggers`` array is the single account registry. Entries with
``active: false`` retain historical identity metadata but are excluded from
fetching, current dashboard people, and opinion-consensus membership.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "bloggers.json"
VALID_SIGNAL_TYPES = frozenset({"opinion", "flow", "news", "disclosure"})
BLOGGER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
HANDLE_RE = re.compile(r"^@([A-Za-z0-9_]{1,15})$")


class RosterError(ValueError):
    """The account registry cannot safely drive a production run."""


def _x_username(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() not in {"x.com", "www.x.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    return parts[0] if len(parts) == 1 else None


def load_bloggers(
    path: Path = DEFAULT_CONFIG,
    *,
    active_only: bool = True,
) -> list[dict]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RosterError(f"cannot read roster {path}: {exc}") from exc
    rows = document.get("bloggers") if isinstance(document, dict) else None
    if not isinstance(rows, list) or not rows:
        raise RosterError("roster bloggers must be a non-empty array")

    seen_ids: set[str] = set()
    seen_handles: set[str] = set()
    seen_urls: set[str] = set()
    normalized: list[dict] = []
    for index, source in enumerate(rows):
        if not isinstance(source, dict):
            raise RosterError(f"roster entry {index} must be an object")
        row = dict(source)
        blogger_id = row.get("id")
        display_name = row.get("display_name")
        handle = row.get("handle")
        x_url = row.get("x_url")
        signal_type = row.get("signal_type")
        active = row.get("active", True)
        if not isinstance(blogger_id, str) or not BLOGGER_ID_RE.fullmatch(blogger_id):
            raise RosterError(f"roster entry {index} has an invalid id")
        if not isinstance(display_name, str) or not display_name:
            raise RosterError(f"{blogger_id}: display_name must be a non-empty string")
        handle_match = HANDLE_RE.fullmatch(handle) if isinstance(handle, str) else None
        if not handle_match:
            raise RosterError(f"{blogger_id}: handle must be an X handle beginning with @")
        url_username = _x_username(x_url) if isinstance(x_url, str) else None
        if not url_username or url_username.casefold() != handle_match.group(1).casefold():
            raise RosterError(f"{blogger_id}: x_url must match handle")
        if signal_type not in VALID_SIGNAL_TYPES:
            raise RosterError(f"{blogger_id}: invalid signal_type {signal_type!r}")
        if not isinstance(active, bool):
            raise RosterError(f"{blogger_id}: active must be a boolean")

        folded_id = blogger_id.casefold()
        folded_handle = handle.casefold()
        canonical_url = f"https://x.com/{url_username.casefold()}"
        if folded_id in seen_ids:
            raise RosterError(f"duplicate blogger id: {blogger_id}")
        if folded_handle in seen_handles:
            raise RosterError(f"duplicate X handle: {handle}")
        if canonical_url in seen_urls:
            raise RosterError(f"duplicate X URL: {x_url}")
        seen_ids.add(folded_id)
        seen_handles.add(folded_handle)
        seen_urls.add(canonical_url)
        row["active"] = active
        row["username"] = handle_match.group(1)
        normalized.append(row)

    selected = [row for row in normalized if row["active"]] if active_only else normalized
    if active_only and not selected:
        raise RosterError("active roster must contain at least one account")
    return selected


def select_bloggers(rows: list[dict], ids: str | None) -> list[dict]:
    if not ids or not ids.strip():
        return rows
    requested = [part.strip() for part in ids.split(",") if part.strip()]
    if len(requested) != len({item.casefold() for item in requested}):
        raise RosterError("requested blogger ids must be unique")
    by_id = {row["id"].casefold(): row for row in rows}
    unknown = [item for item in requested if item.casefold() not in by_id]
    if unknown:
        raise RosterError(f"requested blogger ids are not active: {', '.join(unknown)}")
    return [by_id[item.casefold()] for item in requested]


def github_matrix(rows: list[dict]) -> dict:
    return {
        "include": [
            {"blogger_id": row["id"], "username": row["username"]}
            for row in rows
        ]
    }


def verify_artifacts(root: Path, rows: list[dict]) -> None:
    expected = {f"blogger-data-{row['id']}" for row in rows}
    found = {path.name for path in root.glob("blogger-data-*") if path.is_dir()}
    missing = sorted(expected - found)
    unexpected = sorted(found - expected)
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing={','.join(missing)}")
        if unexpected:
            details.append(f"unexpected={','.join(unexpected)}")
        raise RosterError("account artifact set mismatch: " + " ".join(details))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and query the active X account roster")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--ids", default="", help="optional comma-separated active blogger ids")
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--matrix", action="store_true", help="print a GitHub Actions matrix")
    output.add_argument("--count", action="store_true", help="print the selected active count")
    output.add_argument("--ids-json", action="store_true", help="print the selected stable ids")
    output.add_argument("--verify-artifacts", type=Path, metavar="DIR")
    args = parser.parse_args()
    try:
        rows = select_bloggers(load_bloggers(args.config), args.ids)
        if args.matrix:
            print(json.dumps(github_matrix(rows), separators=(",", ":")))
        elif args.count:
            print(len(rows))
        elif args.ids_json:
            print(json.dumps([row["id"] for row in rows], separators=(",", ":")))
        else:
            verify_artifacts(args.verify_artifacts, rows)
            print(len(rows))
    except RosterError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
