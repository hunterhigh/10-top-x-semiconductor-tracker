#!/usr/bin/env python3
"""Atomically migrate the repository database from flat v1 files to v2 shards."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from storage_layout import (
    LEGACY_LAYOUT_VERSION,
    SHARDED_LAYOUT_VERSION,
    StorageLayoutError,
    detect_storage_layout,
    price_cache_relative,
    shard_stats,
    stock_document_relative,
    storage_contract,
    validate_snapshot_layout,
)


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise StorageLayoutError(f"Expected JSON object: {path}")
    return value


def save_object(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def content_digest(rows: list[tuple[str, dict[str, Any]]]) -> str:
    digest = hashlib.sha256()
    for key, value in sorted(rows, key=lambda item: item[0]):
        digest.update(key.encode("utf-8"))
        digest.update(b"\0")
        digest.update(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def migrate(data_dir: Path, *, dry_run: bool = False) -> dict[str, Any]:
    data_dir = data_dir.resolve()
    db_dir = data_dir / "db"
    stocks_dir = db_dir / "stocks"
    cache_dir = data_dir / "prices_cache"
    index_path = db_dir / "index.json"
    manifest_path = db_dir / "manifest.json"
    index = load_object(index_path)
    manifest = load_object(manifest_path)
    version = detect_storage_layout(db_dir, manifest=manifest, index=index)
    if version == SHARDED_LAYOUT_VERSION:
        result = validate_snapshot_layout(db_dir)
        result.update({"changed": False, "price_cache": manifest.get("storage_layout", {}).get("price_cache", {})})
        return result
    if version != LEGACY_LAYOUT_VERSION:
        raise StorageLayoutError(f"Cannot migrate storage layout version {version}")

    flat_stocks = sorted(stocks_dir.glob("*.json"))
    flat_caches = sorted(cache_dir.glob("*.json")) if cache_dir.is_dir() else []
    rows = index.get("stocks") or []
    rows_by_ticker = {str(row.get("ticker")): row for row in rows if row.get("ticker")}
    if len(rows_by_ticker) != len(rows):
        raise StorageLayoutError("index.json contains a missing or duplicate ticker")
    if len(flat_stocks) != len(rows):
        raise StorageLayoutError(
            f"Refusing migration: index has {len(rows)} rows but flat storage has {len(flat_stocks)} files"
        )

    activated_at = datetime.now(timezone.utc).isoformat()
    staging = Path(tempfile.mkdtemp(prefix="storage-layout-v2-", dir=data_dir))
    staged_stocks = staging / "stocks"
    staged_cache = staging / "prices_cache"
    stock_values: list[tuple[str, dict[str, Any]]] = []
    cache_values: list[tuple[str, dict[str, Any]]] = []
    stock_targets: set[Path] = set()
    cache_targets: set[Path] = set()
    try:
        for source in flat_stocks:
            document = load_object(source)
            ticker = str(document.get("ticker") or "")
            row = rows_by_ticker.get(ticker)
            if not row:
                raise StorageLayoutError(f"Stock document is absent from index.json: {ticker or source.name}")
            instrument_id = str((document.get("instrument") or {}).get("instrument_id") or "")
            row_instrument_id = str((row.get("instrument") or {}).get("instrument_id") or "")
            if not instrument_id or instrument_id != row_instrument_id:
                raise StorageLayoutError(f"Instrument identity mismatch for {ticker}")
            relative = stock_document_relative(instrument_id)
            target = staged_stocks.joinpath(*relative.parts[1:])
            if target in stock_targets:
                raise StorageLayoutError(f"Duplicate instrument_id produces one document path: {instrument_id}")
            stock_targets.add(target)
            save_object(target, document)
            row["document_path"] = relative.as_posix()
            stock_values.append((ticker, document))

        for source in flat_caches:
            document = load_object(source)
            price_symbol = str(document.get("price_symbol") or "").strip()
            if not price_symbol:
                raise StorageLayoutError(f"Price cache is missing price_symbol: {source.name}")
            relative = price_cache_relative(price_symbol)
            target = staged_cache.joinpath(*relative.parts)
            if target in cache_targets:
                raise StorageLayoutError(f"Duplicate price_symbol produces one cache path: {price_symbol}")
            cache_targets.add(target)
            save_object(target, document)
            cache_values.append((price_symbol, document))

        stock_result = shard_stats(stock_targets, staged_stocks)
        cache_result = shard_stats(cache_targets, staged_cache)
        if not stock_result["within_limit"] or not cache_result["within_limit"]:
            raise StorageLayoutError(
                f"Migration would exceed shard width: stocks={stock_result['max_shard_width']}, "
                f"prices={cache_result['max_shard_width']}"
            )
        if len(stock_targets) != len(flat_stocks) or len(cache_targets) != len(flat_caches):
            raise StorageLayoutError("Migration lost one or more source documents")

        layout = storage_contract(SHARDED_LAYOUT_VERSION, activated_at=activated_at)
        layout["stock_documents"].update(stock_result)
        layout["price_cache"].update(cache_result)
        index.setdefault("meta", {})["storage_layout"] = layout
        manifest["storage_layout"] = layout
        before = {
            "stock_documents": content_digest(stock_values),
            "price_cache": content_digest(cache_values),
        }
        staged_stock_values = [(load_object(path)["ticker"], load_object(path)) for path in sorted(stock_targets)]
        staged_cache_values = [(load_object(path)["price_symbol"], load_object(path)) for path in sorted(cache_targets)]
        after = {
            "stock_documents": content_digest(staged_stock_values),
            "price_cache": content_digest(staged_cache_values),
        }
        if before != after:
            raise StorageLayoutError("Semantic content digest changed during migration")

        result = {
            "changed": not dry_run,
            "dry_run": dry_run,
            "version": SHARDED_LAYOUT_VERSION,
            "activated_at": activated_at,
            "stock_documents": stock_result,
            "price_cache": cache_result,
            "content_digest": after,
            "provider_calls": 0,
        }
        if dry_run:
            return result

        backup_stocks = staging / "legacy-stocks"
        backup_cache = staging / "legacy-prices-cache"
        original_index = index_path.read_bytes()
        original_manifest = manifest_path.read_bytes()
        replaced_stocks = replaced_cache = False
        try:
            stocks_dir.replace(backup_stocks)
            staged_stocks.replace(stocks_dir)
            replaced_stocks = True
            if cache_dir.exists():
                cache_dir.replace(backup_cache)
            staged_cache.replace(cache_dir)
            replaced_cache = True
            save_object(index_path, index)
            save_object(manifest_path, manifest)
            validate_snapshot_layout(db_dir)
        except Exception:
            if replaced_stocks:
                shutil.rmtree(stocks_dir, ignore_errors=True)
                backup_stocks.replace(stocks_dir)
            if replaced_cache:
                shutil.rmtree(cache_dir, ignore_errors=True)
                if backup_cache.exists():
                    backup_cache.replace(cache_dir)
            index_path.write_bytes(original_index)
            manifest_path.write_bytes(original_manifest)
            raise
        return result
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate flat database files to deterministic SHA-256 shards")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    try:
        result = migrate(args.data_dir, dry_run=args.dry_run)
    except (OSError, ValueError, json.JSONDecodeError, StorageLayoutError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
