#!/usr/bin/env python3
"""Fetch a verified read-only production snapshot for an installed Skill.

This intentionally has no credentials for X, GitHub Actions, or production
publish.  It reads the public ``main`` manifest first, then atomically replaces
the local cache with the matching repository archive when the manifest changes.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


REPOSITORY = "hunterhigh/10-top-x-semiconductor-tracker"
RAW_ROOT = f"https://raw.githubusercontent.com/{REPOSITORY}/main"
ARCHIVE_URL = f"https://github.com/{REPOSITORY}/archive/refs/heads/main.zip"
REQUIRED = {
    "data/db/",
    "data/avatar_cache.json",
    "config/bloggers.json",
    "config/blogger_profiles.json",
}


def default_cache_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    return Path(root) / "x-traders-consensus" / "snapshot" if root else Path.home() / ".cache" / "x-traders-consensus" / "snapshot"


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "x-traders-consensus-skill/2"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status} for {url}")
            return response.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Unable to retrieve production snapshot: {exc.reason}") from exc


def remote_manifest() -> dict:
    try:
        payload = json.loads(fetch(f"{RAW_ROOT}/data/db/manifest.json"))
    except (json.JSONDecodeError, RuntimeError) as exc:
        raise RuntimeError("Production manifest is unavailable; refusing to use an unverified old snapshot") from exc
    if not payload.get("generated_at") or not payload.get("date_range"):
        raise RuntimeError("Production manifest is invalid; refusing to use an unverified old snapshot")
    return payload


def local_manifest(cache: Path) -> dict | None:
    try:
        return json.loads((cache / "data" / "db" / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def extract_archive(content: bytes, staging: Path) -> None:
    archive_path = staging / "main.zip"
    archive_path.write_bytes(content)
    extracted = 0
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            # GitHub zipballs contain one root directory.  Reject malformed
            # paths before extraction rather than trusting archive members.
            if path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
                continue
            relative = PurePosixPath(*path.parts[1:]).as_posix()
            if not (relative.startswith("data/db/") or relative in REQUIRED):
                continue
            target = staging / relative
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)
            extracted += 1
    archive_path.unlink(missing_ok=True)
    if extracted == 0 or not (staging / "data" / "db" / "manifest.json").is_file():
        raise RuntimeError("Downloaded archive did not contain the required production snapshot")


def sync(cache: Path | None = None) -> tuple[Path, dict, bool]:
    cache = (cache or default_cache_dir()).resolve()
    remote = remote_manifest()
    existing = local_manifest(cache)
    if existing and existing.get("generated_at") == remote.get("generated_at"):
        return cache, remote, False
    cache.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="snapshot-", dir=cache.parent))
    try:
        extract_archive(fetch(ARCHIVE_URL), staging)
        downloaded = local_manifest(staging)
        if not downloaded or downloaded.get("generated_at") != remote.get("generated_at"):
            raise RuntimeError("Snapshot archive does not match the manifest read at start; retry later")
        for required in ("config/bloggers.json", "config/blogger_profiles.json", "data/avatar_cache.json"):
            if not (staging / required).is_file():
                raise RuntimeError(f"Snapshot is missing required file: {required}")
        previous = cache.with_name(cache.name + ".previous")
        if previous.exists():
            shutil.rmtree(previous)
        if cache.exists():
            cache.replace(previous)
        staging.replace(cache)
        if previous.exists():
            shutil.rmtree(previous)
        return cache, remote, True
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronize the read-only x-traders production snapshot")
    parser.add_argument("--cache-dir", type=Path, default=None)
    args = parser.parse_args()
    cache, manifest, changed = sync(args.cache_dir)
    print(json.dumps({"cache_dir": str(cache), "changed": changed, "manifest": manifest}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
