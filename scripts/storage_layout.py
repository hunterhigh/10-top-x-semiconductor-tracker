#!/usr/bin/env python3
"""Fail-closed v2 paths and indexes for stock documents and price caches."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator


SCHEMA_VERSION = 2
STORAGE_LAYOUT = "hash-sharded-v1"
SHARDED_LAYOUT_VERSION = SCHEMA_VERSION  # retained for existing callers
HASH_ALGORITHM = "sha256"
SHARD_PREFIX_CHARS = 2
SHARD_WARNING_WIDTH = 900
SHARD_MAX_WIDTH = 999
PRICE_INDEX_NAME = "index.json"


class StorageLayoutError(RuntimeError):
    """Raised when a snapshot violates the declared v2 storage contract."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageLayoutError(f"Unable to read JSON storage document: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise StorageLayoutError(f"Expected a JSON object: {path}")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_v2(document: dict[str, Any] | None, label: str) -> None:
    if not isinstance(document, dict):
        raise StorageLayoutError(f"{label} must be a JSON object")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise StorageLayoutError(
            f"Unsupported {label} schema_version: {document.get('schema_version')!r}; "
            f"expected {SCHEMA_VERSION}"
        )
    if document.get("storage_layout") != STORAGE_LAYOUT:
        raise StorageLayoutError(
            f"Unsupported {label} storage_layout: {document.get('storage_layout')!r}; "
            f"expected {STORAGE_LAYOUT!r}"
        )


def detect_storage_layout(
    db_dir: Path,
    *,
    manifest: dict[str, Any] | None = None,
    index: dict[str, Any] | None = None,
) -> int:
    """Require the direct-switch v2 contract and return its schema version."""
    db_dir = Path(db_dir)
    if manifest is None:
        manifest = _load_json(db_dir / "manifest.json")
    if index is None:
        index = _load_json(db_dir / "index.json")
    _require_v2(manifest, "manifest")
    _require_v2(index, "index")
    if manifest.get("schema_version") != index.get("schema_version"):
        raise StorageLayoutError("manifest and index schema_version disagree")
    if manifest.get("storage_layout") != index.get("storage_layout"):
        raise StorageLayoutError("manifest and index storage_layout disagree")
    return SCHEMA_VERSION


def stable_digest(identity: str) -> str:
    normalized = str(identity or "").strip()
    if not normalized:
        raise StorageLayoutError("A non-empty stable identity is required")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def stock_document_relative(instrument_id: str) -> PurePosixPath:
    digest = stable_digest(instrument_id)
    return PurePosixPath("stocks", digest[:SHARD_PREFIX_CHARS], f"{digest}.json")


def price_cache_relative(price_symbol: str) -> PurePosixPath:
    """Hash the normalized provider symbol already used as the cache identity."""
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


def index_stock_rows(index: dict[str, Any]) -> list[dict[str, Any]]:
    _require_v2(index, "index")
    stocks = index.get("stocks")
    if not isinstance(stocks, dict):
        raise StorageLayoutError("v2 index.stocks must be an instrument_id mapping")
    rows: list[dict[str, Any]] = []
    for instrument_id, raw in stocks.items():
        if not isinstance(raw, dict):
            raise StorageLayoutError(f"Invalid index row for {instrument_id}")
        row = raw
        declared = row.get("instrument_id") or (row.get("instrument") or {}).get("instrument_id")
        if declared != instrument_id:
            raise StorageLayoutError(
                f"index key/instrument_id mismatch: {instrument_id!r} != {declared!r}"
            )
        rows.append(row)
    return rows


def index_rows_by_ticker(index: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not index:
        return {}
    return {
        str(row.get("ticker")): row
        for row in index_stock_rows(index)
        if row.get("ticker")
    }


def _row_instrument_id(row: dict[str, Any]) -> str:
    return str(row.get("instrument_id") or (row.get("instrument") or {}).get("instrument_id") or "")


def stock_document_path(
    db_dir: Path,
    row: dict[str, Any],
    *,
    version: int | None = None,
    must_exist: bool = False,
) -> Path:
    if version not in {None, SCHEMA_VERSION}:
        raise StorageLayoutError(f"Unsupported storage layout version: {version}")
    relative = row.get("path")
    instrument_id = _row_instrument_id(row)
    if not relative or not instrument_id:
        raise StorageLayoutError(
            f"v2 index row is missing path/instrument_id: {row.get('ticker') or '<unknown>'}"
        )
    expected = stock_document_relative(instrument_id)
    if PurePosixPath(str(relative).replace("\\", "/")) != expected:
        raise StorageLayoutError(
            f"v2 path does not match instrument_id for {row.get('ticker')}: {relative}"
        )
    if row.get("hash") != stable_digest(instrument_id):
        raise StorageLayoutError(f"v2 hash does not match instrument_id for {row.get('ticker')}")
    return safe_resolve(Path(db_dir), relative, must_exist=must_exist)


def price_cache_path(cache_dir: Path, price_symbol: str, *, version: int | None = None) -> Path:
    if version not in {None, SCHEMA_VERSION}:
        raise StorageLayoutError(f"Unsupported storage layout version: {version}")
    return safe_resolve(Path(cache_dir), price_cache_relative(price_symbol))


def iter_stock_documents(
    db_dir: Path,
    *,
    index: dict[str, Any] | None = None,
    version: int | None = None,
) -> Iterator[Path]:
    db_dir = Path(db_dir)
    index = index or _load_json(db_dir / "index.json")
    _require_v2(index, "index")
    all_paths = index.get("all_paths")
    if not isinstance(all_paths, list):
        raise StorageLayoutError("v2 index.all_paths must be a list")
    rows = index_stock_rows(index)
    row_paths = {str(row.get("path")) for row in rows}
    if len(all_paths) != len(set(all_paths)) or set(all_paths) != row_paths:
        raise StorageLayoutError("index.all_paths must uniquely equal index.stocks[*].path")
    for relative in all_paths:
        yield safe_resolve(db_dir, relative, must_exist=True)


def load_stock_document(
    db_dir: Path,
    row: dict[str, Any],
    *,
    version: int | None = None,
) -> dict[str, Any]:
    path = stock_document_path(db_dir, row, version=version, must_exist=True)
    document = _load_json(path)
    actual = ((document.get("instrument") or {}).get("instrument_id"))
    expected = _row_instrument_id(row)
    if actual != expected:
        raise StorageLayoutError(
            f"Stock document instrument_id mismatch at {path}: {actual!r} != {expected!r}"
        )
    return document


def normalize_lookup(value: Any) -> str:
    return " ".join(str(value or "").strip().upper().split())


def build_stock_lookup(rows: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
    lookup: defaultdict[str, set[str]] = defaultdict(set)
    for row in rows:
        instrument = row.get("instrument") or {}
        instrument_id = _row_instrument_id(row)
        values = [
            instrument_id,
            row.get("ticker"),
            row.get("cashtag"),
            row.get("company"),
            instrument.get("display_code"),
            instrument.get("display_name"),
            *(instrument.get("aliases") or []),
        ]
        for raw in values:
            key = normalize_lookup(raw)
            if not key:
                continue
            lookup[key].add(instrument_id)
            if key.startswith("$"):
                lookup[key[1:]].add(instrument_id)
            elif key == normalize_lookup(row.get("ticker")):
                lookup[f"${key}"].add(instrument_id)
    return {key: sorted(values) for key, values in sorted(lookup.items())}


def make_stock_index(
    rows: Iterable[dict[str, Any]],
    *,
    generated_at: str,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prepared: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        instrument_id = _row_instrument_id(row)
        digest = stable_digest(instrument_id)
        row["instrument_id"] = instrument_id
        row["hash"] = digest
        row["path"] = stock_document_relative(instrument_id).as_posix()
        row.pop("document_path", None)
        prepared.append(row)
    prepared.sort(key=lambda row: (-int(row.get("total_mentions") or 0), _row_instrument_id(row)))
    stocks = {_row_instrument_id(row): row for row in prepared}
    if len(stocks) != len(prepared):
        raise StorageLayoutError("Duplicate instrument_id in stock index")
    return {
        "schema_version": SCHEMA_VERSION,
        "storage_layout": STORAGE_LAYOUT,
        "generated_at": generated_at,
        "meta": dict(meta or {}),
        "stocks": stocks,
        "lookup": build_stock_lookup(prepared),
        "all_paths": [row["path"] for row in prepared],
    }


def build_price_cache_index(cache_dir: Path, price_symbols: Iterable[str]) -> dict[str, Any]:
    cache_dir = Path(cache_dir)
    entries: dict[str, dict[str, Any]] = {}
    for price_symbol in sorted({str(value).strip() for value in price_symbols if str(value).strip()}):
        digest = stable_digest(price_symbol)
        relative = price_cache_relative(price_symbol).as_posix()
        path = safe_resolve(cache_dir, relative, must_exist=True)
        entries[price_symbol] = {
            "price_symbol": price_symbol,
            "hash": digest,
            "path": relative,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "storage_layout": STORAGE_LAYOUT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "prices": entries,
        "lookup": {normalize_lookup(symbol): [symbol] for symbol in sorted(entries)},
        "all_paths": [entry["path"] for entry in entries.values()],
    }


def write_price_cache_index(cache_dir: Path) -> dict[str, Any]:
    """Rebuild the producer-owned price index from validated cache payloads."""
    cache_dir = Path(cache_dir)
    symbols: list[str] = []
    updated_values: list[str] = []
    for path in cache_dir.rglob("*.json"):
        if path.parent == cache_dir and path.name == PRICE_INDEX_NAME:
            continue
        payload = _load_json(path)
        symbol = str(payload.get("price_symbol") or "").strip()
        if not symbol:
            raise StorageLayoutError(f"Price cache is missing price_symbol: {path}")
        if payload.get("last_updated"):
            updated_values.append(str(payload["last_updated"]))
        expected = safe_resolve(cache_dir, price_cache_relative(symbol))
        if path.resolve() != expected.resolve():
            raise StorageLayoutError(f"Price cache is stored at the wrong v2 path: {path}")
        symbols.append(symbol)
    previous = {}
    index_path = cache_dir / PRICE_INDEX_NAME
    if index_path.is_file():
        try:
            previous = _load_json(index_path)
        except StorageLayoutError:
            previous = {}
    index = build_price_cache_index(cache_dir, symbols)
    previous_symbols = set((previous.get("prices") or {}).keys())
    index["generated_at"] = (
        previous.get("generated_at")
        if previous_symbols == set(symbols) and previous.get("generated_at")
        else max(updated_values, default=datetime.now(timezone.utc).isoformat())
    )
    # Preserve the producer-owned index byte-for-byte when its semantic
    # contents have not changed. Otherwise a newline-only conversion between
    # Windows (CRLF) and Linux (LF) would invalidate the manifest's raw SHA-256.
    if previous == index:
        return previous
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return index


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


def storage_contract(version: int = SCHEMA_VERSION, *, activated_at: str | None = None) -> dict[str, Any]:
    if version != SCHEMA_VERSION:
        raise StorageLayoutError(f"Unsupported storage layout version: {version}")
    return {
        "schema_version": SCHEMA_VERSION,
        "storage_layout": STORAGE_LAYOUT,
        "hash_algorithm": HASH_ALGORITHM,
        "shard_prefix_chars": SHARD_PREFIX_CHARS,
        "stock_hash_identity": "instrument_id",
        "price_hash_identity": "price_symbol",
        "activated_at": activated_at,
    }


def validate_price_cache_layout(cache_dir: Path) -> dict[str, Any]:
    cache_dir = Path(cache_dir)
    index = _load_json(cache_dir / PRICE_INDEX_NAME)
    _require_v2(index, "price cache index")
    prices = index.get("prices")
    all_paths = index.get("all_paths")
    if not isinstance(prices, dict) or not isinstance(all_paths, list):
        raise StorageLayoutError("Invalid price cache index mappings")
    declared: set[Path] = set()
    for key, entry in prices.items():
        if not isinstance(entry, dict) or entry.get("price_symbol") != key:
            raise StorageLayoutError(f"Invalid price cache entry: {key}")
        expected = price_cache_relative(key).as_posix()
        if entry.get("path") != expected or entry.get("hash") != stable_digest(key):
            raise StorageLayoutError(f"Price cache path/hash mismatch: {key}")
        path = safe_resolve(cache_dir, expected, must_exist=True)
        declared.add(path.resolve())
    if len(all_paths) != len(set(all_paths)) or set(all_paths) != {
        entry["path"] for entry in prices.values()
    }:
        raise StorageLayoutError("Price index all_paths mismatch")
    actual = {
        path.resolve()
        for path in cache_dir.rglob("*.json")
        if path.name != PRICE_INDEX_NAME
    }
    if actual != declared:
        raise StorageLayoutError(
            f"Price cache tree/index mismatch: extra={len(actual-declared)}, missing={len(declared-actual)}"
        )
    stats = shard_stats(declared, cache_dir)
    if not stats["within_limit"]:
        raise StorageLayoutError(
            f"A price shard has {stats['max_shard_width']} files; limit is {SHARD_MAX_WIDTH}"
        )
    return stats


def validate_snapshot_layout(db_dir: Path, price_cache_dir: Path | None = None) -> dict[str, Any]:
    db_dir = Path(db_dir)
    manifest = _load_json(db_dir / "manifest.json")
    index = _load_json(db_dir / "index.json")
    detect_storage_layout(db_dir, manifest=manifest, index=index)
    if manifest.get("index_sha256") != file_sha256(db_dir / "index.json"):
        raise StorageLayoutError("manifest.index_sha256 does not match index.json")
    rows = index_stock_rows(index)
    paths = list(iter_stock_documents(db_dir, index=index))
    if len(paths) != len(rows) or manifest.get("stock_count") != len(rows):
        raise StorageLayoutError("manifest/index/stock document counts disagree")
    for row in rows:
        load_stock_document(db_dir, row)
    actual = {path.resolve() for path in (db_dir / "stocks").rglob("*.json")}
    declared = {path.resolve() for path in paths}
    if actual != declared:
        raise StorageLayoutError(
            f"Stock tree/index mismatch: extra={len(actual-declared)}, missing={len(declared-actual)}"
        )
    for values in (index.get("lookup") or {}).values():
        if not isinstance(values, list) or any(value not in index["stocks"] for value in values):
            raise StorageLayoutError("index.lookup references an unknown instrument_id")
    stock_stats = shard_stats(paths, db_dir / "stocks")
    if not stock_stats["within_limit"]:
        raise StorageLayoutError(
            f"A stock shard has {stock_stats['max_shard_width']} files; limit is {SHARD_MAX_WIDTH}"
        )
    result = {"version": SCHEMA_VERSION, "stock_documents": stock_stats}
    if price_cache_dir is not None:
        price_stats = validate_price_cache_layout(price_cache_dir)
        price_index = Path(price_cache_dir) / PRICE_INDEX_NAME
        if manifest.get("price_cache_index_sha256") != file_sha256(price_index):
            raise StorageLayoutError("manifest.price_cache_index_sha256 does not match price index")
        if manifest.get("price_cache_count") != price_stats["files"]:
            raise StorageLayoutError("manifest.price_cache_count does not match price index")
        result["price_cache"] = price_stats
    return result
