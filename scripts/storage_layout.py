#!/usr/bin/env python3
"""Versioned, fail-closed paths for the repository database and price cache."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator


LEGACY_LAYOUT_VERSION = 1
SHARDED_LAYOUT_VERSION = 2
HASH_ALGORITHM = "sha256"
SHARD_PREFIX_CHARS = 2
SHARD_WARNING_WIDTH = 900
SHARD_MAX_WIDTH = 999


class StorageLayoutError(RuntimeError):
    """Raised when a snapshot violates its declared storage contract."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageLayoutError(f"Unable to read JSON storage contract: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise StorageLayoutError(f"Expected a JSON object: {path}")
    return value


def _declared_version(document: dict[str, Any] | None) -> int | None:
    if not document:
        return None
    layout = document.get("storage_layout")
    if not isinstance(layout, dict):
        layout = (document.get("meta") or {}).get("storage_layout")
    if not isinstance(layout, dict) or layout.get("version") is None:
        return None
    try:
        version = int(layout["version"])
    except (TypeError, ValueError) as exc:
        raise StorageLayoutError("storage_layout.version must be an integer") from exc
    if version not in {LEGACY_LAYOUT_VERSION, SHARDED_LAYOUT_VERSION}:
        raise StorageLayoutError(f"Unsupported storage layout version: {version}")
    return version


def detect_storage_layout(
    db_dir: Path,
    *,
    manifest: dict[str, Any] | None = None,
    index: dict[str, Any] | None = None,
) -> int:
    """Return the declared layout, treating pre-contract snapshots as v1."""
    db_dir = Path(db_dir)
    if manifest is None and (db_dir / "manifest.json").is_file():
        manifest = _load_json(db_dir / "manifest.json")
    if index is None and (db_dir / "index.json").is_file():
        index = _load_json(db_dir / "index.json")
    manifest_version = _declared_version(manifest)
    index_version = _declared_version(index)
    if manifest_version and index_version and manifest_version != index_version:
        raise StorageLayoutError(
            f"Storage layout disagreement: manifest={manifest_version}, index={index_version}"
        )
    return index_version or manifest_version or LEGACY_LAYOUT_VERSION


def stable_digest(identity: str) -> str:
    normalized = str(identity or "").strip()
    if not normalized:
        raise StorageLayoutError("A non-empty stable identity is required")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def stock_document_relative(instrument_id: str) -> PurePosixPath:
    digest = stable_digest(instrument_id)
    return PurePosixPath("stocks", digest[:SHARD_PREFIX_CHARS], f"{digest}.json")


def price_cache_relative(price_symbol: str) -> PurePosixPath:
    digest = stable_digest(price_symbol)
    return PurePosixPath(digest[:SHARD_PREFIX_CHARS], f"{digest}.json")


def safe_resolve(root: Path, relative: str | PurePosixPath, *, must_exist: bool = False) -> Path:
    root = Path(root).resolve()
    rel = PurePosixPath(str(relative).replace("\\", "/"))
    if rel.is_absolute() or not rel.parts or ".." in rel.parts:
        raise StorageLayoutError(f"Unsafe relative storage path: {relative!s}")
    candidate = root.joinpath(*rel.parts).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise StorageLayoutError(f"Storage path escapes its root: {relative!s}") from exc
    if must_exist and not candidate.is_file():
        raise StorageLayoutError(f"Declared storage document is missing: {relative!s}")
    return candidate


def _legacy_stock_relative(ticker: str) -> PurePosixPath:
    ticker = str(ticker or "").strip()
    if not ticker or "/" in ticker or "\\" in ticker or ticker in {".", ".."}:
        raise StorageLayoutError(f"Unsafe legacy ticker filename: {ticker!r}")
    return PurePosixPath("stocks", f"{ticker}.json")


def stock_document_path(
    db_dir: Path,
    row: dict[str, Any],
    *,
    version: int | None = None,
    must_exist: bool = False,
) -> Path:
    version = version or detect_storage_layout(Path(db_dir))
    if version == SHARDED_LAYOUT_VERSION:
        relative = row.get("document_path")
        if not relative:
            raise StorageLayoutError(
                f"v2 index row is missing document_path: {row.get('ticker') or '<unknown>'}"
            )
        expected = stock_document_relative((row.get("instrument") or {}).get("instrument_id"))
        if PurePosixPath(str(relative).replace("\\", "/")) != expected:
            raise StorageLayoutError(
                f"v2 document_path does not match instrument_id for {row.get('ticker')}: {relative}"
            )
    elif version == LEGACY_LAYOUT_VERSION:
        relative = _legacy_stock_relative(row.get("ticker"))
    else:
        raise StorageLayoutError(f"Unsupported storage layout version: {version}")
    return safe_resolve(Path(db_dir), relative, must_exist=must_exist)


def price_cache_path(cache_dir: Path, price_symbol: str, *, version: int) -> Path:
    if version == SHARDED_LAYOUT_VERSION:
        relative = price_cache_relative(price_symbol)
    elif version == LEGACY_LAYOUT_VERSION:
        safe = str(price_symbol or "").replace("/", "_").replace("\\", "_")
        if not safe or safe in {".", ".."}:
            raise StorageLayoutError(f"Unsafe legacy price symbol: {price_symbol!r}")
        relative = PurePosixPath(f"{safe}.json")
    else:
        raise StorageLayoutError(f"Unsupported storage layout version: {version}")
    return safe_resolve(Path(cache_dir), relative)


def iter_stock_documents(
    db_dir: Path,
    *,
    index: dict[str, Any] | None = None,
    version: int | None = None,
) -> Iterator[Path]:
    db_dir = Path(db_dir)
    if index is None:
        index_path = db_dir / "index.json"
        index = _load_json(index_path) if index_path.is_file() else None
    version = version or detect_storage_layout(db_dir, index=index)
    if version == LEGACY_LAYOUT_VERSION:
        yield from sorted((db_dir / "stocks").glob("*.json"))
        return
    if not index:
        raise StorageLayoutError("v2 storage requires data/db/index.json")
    seen: set[Path] = set()
    for row in index.get("stocks") or []:
        path = stock_document_path(db_dir, row, version=version, must_exist=True)
        if path in seen:
            raise StorageLayoutError(f"Duplicate v2 document_path in index: {row.get('document_path')}")
        seen.add(path)
        yield path


def index_rows_by_ticker(index: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("ticker")): row
        for row in (index or {}).get("stocks") or []
        if row.get("ticker")
    }


def load_stock_document(
    db_dir: Path,
    row: dict[str, Any],
    *,
    version: int | None = None,
) -> dict[str, Any]:
    path = stock_document_path(db_dir, row, version=version, must_exist=True)
    return _load_json(path)


def shard_stats(paths: Iterable[Path], root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    widths: Counter[str] = Counter()
    files = 0
    for raw in paths:
        path = Path(raw).resolve()
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise StorageLayoutError(f"Counted path is outside storage root: {path}") from exc
        files += 1
        shard = relative.parts[0] if len(relative.parts) > 1 else "."
        widths[shard] += 1
    max_width = max(widths.values(), default=0)
    return {
        "files": files,
        "shards": len(widths),
        "max_shard_width": max_width,
        "warning": max_width >= SHARD_WARNING_WIDTH,
        "within_limit": max_width <= SHARD_MAX_WIDTH,
    }


def storage_contract(version: int, *, activated_at: str | None = None) -> dict[str, Any]:
    if version == LEGACY_LAYOUT_VERSION:
        return {
            "version": 1,
            "stock_documents": {"scheme": "ticker-filename"},
            "price_cache": {"scheme": "price-symbol-filename"},
        }
    if version != SHARDED_LAYOUT_VERSION:
        raise StorageLayoutError(f"Unsupported storage layout version: {version}")
    result: dict[str, Any] = {
        "version": 2,
        "hash_algorithm": HASH_ALGORITHM,
        "shard_prefix_chars": SHARD_PREFIX_CHARS,
        "stock_documents": {"scheme": "sha256-instrument-id"},
        "price_cache": {"scheme": "sha256-price-symbol"},
        "migrated_from_version": 1,
    }
    if activated_at:
        result["activated_at"] = activated_at
    return result


def validate_snapshot_layout(db_dir: Path) -> dict[str, Any]:
    db_dir = Path(db_dir)
    index = _load_json(db_dir / "index.json")
    version = detect_storage_layout(db_dir, index=index)
    paths = list(iter_stock_documents(db_dir, index=index, version=version))
    if len(paths) != len(index.get("stocks") or []):
        raise StorageLayoutError(
            f"Index/document count mismatch: index={len(index.get('stocks') or [])}, files={len(paths)}"
        )
    if version == SHARDED_LAYOUT_VERSION and list((db_dir / "stocks").glob("*.json")):
        raise StorageLayoutError("v2 snapshot still contains flat stock JSON files")
    if version == SHARDED_LAYOUT_VERSION:
        actual = {path.resolve() for path in (db_dir / "stocks").rglob("*.json")}
        declared = {path.resolve() for path in paths}
        if actual != declared:
            raise StorageLayoutError(
                f"v2 stock tree/index mismatch: unreferenced={len(actual - declared)}, missing={len(declared - actual)}"
            )
    stats = shard_stats(paths, db_dir / "stocks")
    if version == SHARDED_LAYOUT_VERSION and not stats["within_limit"]:
        raise StorageLayoutError(
            f"A stock shard has {stats['max_shard_width']} files; the publish limit is {SHARD_MAX_WIDTH}"
        )
    return {"version": version, "stock_documents": stats}
