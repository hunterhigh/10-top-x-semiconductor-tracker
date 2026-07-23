#!/usr/bin/env python3
"""Read the v2 stock snapshot through its manifest and deterministic index."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Iterator


SCHEMA_VERSION = 2
STORAGE_LAYOUT = "hash-sharded-v1"


class StockStoreError(RuntimeError):
    pass


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StockStoreError(f"Cannot read snapshot document {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise StockStoreError(f"Expected a JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").strip().upper().split())


class StockStore:
    def __init__(self, db_dir: Path):
        self.db_dir = Path(db_dir).resolve()
        self.manifest = _read_object(self.db_dir / "manifest.json")
        self.index = _read_object(self.db_dir / "index.json")
        for label, document in (("manifest", self.manifest), ("index", self.index)):
            if document.get("schema_version") != SCHEMA_VERSION:
                raise StockStoreError(
                    f"Unsupported {label} schema_version: {document.get('schema_version')!r}"
                )
            if document.get("storage_layout") != STORAGE_LAYOUT:
                raise StockStoreError(
                    f"Unsupported {label} storage_layout: {document.get('storage_layout')!r}"
                )
        if self.manifest.get("index_sha256") != _sha256(self.db_dir / "index.json"):
            raise StockStoreError("manifest.index_sha256 does not match index.json")
        self.stocks = self.index.get("stocks")
        self.lookup = self.index.get("lookup")
        self.all_paths = self.index.get("all_paths")
        if not isinstance(self.stocks, dict) or not isinstance(self.lookup, dict):
            raise StockStoreError("Invalid v2 stocks/lookup mapping")
        if not isinstance(self.all_paths, list):
            raise StockStoreError("Invalid v2 all_paths list")
        indexed_paths = {entry.get("path") for entry in self.stocks.values() if isinstance(entry, dict)}
        if (
            len(self.all_paths) != len(set(self.all_paths))
            or set(self.all_paths) != indexed_paths
            or self.manifest.get("stock_count") != len(self.stocks)
        ):
            raise StockStoreError("manifest/index stock counts or paths disagree")

    def _safe_path(self, relative: Any) -> Path:
        rel = PurePosixPath(str(relative or "").replace("\\", "/"))
        if (
            rel.is_absolute()
            or len(rel.parts) != 3
            or rel.parts[0] != "stocks"
            or ".." in rel.parts
            or rel.suffix != ".json"
        ):
            raise StockStoreError(f"Unsafe stock path: {relative!r}")
        candidate = self.db_dir.joinpath(*rel.parts).resolve()
        try:
            candidate.relative_to(self.db_dir)
        except ValueError as exc:
            raise StockStoreError(f"Stock path escapes snapshot: {relative!r}") from exc
        if not candidate.is_file():
            raise StockStoreError(f"Indexed stock document is missing: {relative!r}")
        return candidate

    def iter_stock_paths(self) -> Iterator[Path]:
        for relative in self.all_paths:
            yield self._safe_path(relative)

    def resolve_ids(self, query: str) -> list[str]:
        key = _normalize(query)
        if key in self.stocks:
            return [key]
        values = self.lookup.get(key)
        if values is None and key.startswith("$"):
            values = self.lookup.get(key[1:])
        if not isinstance(values, list):
            return []
        unknown = [value for value in values if value not in self.stocks]
        if unknown:
            raise StockStoreError(f"lookup references unknown instrument_id(s): {unknown}")
        return values

    def resolve_stock(self, query: str) -> tuple[dict[str, Any], Path]:
        matches = self.resolve_ids(query)
        if not matches:
            raise StockStoreError(f"No stock matches {query!r}")
        if len(matches) > 1:
            raise StockStoreError(
                f"Ambiguous stock query {query!r}; choose an instrument_id: {', '.join(matches)}"
            )
        row = self.stocks[matches[0]]
        path = self._safe_path(row.get("path"))
        document = _read_object(path)
        actual = ((document.get("instrument") or {}).get("instrument_id"))
        if actual != matches[0]:
            raise StockStoreError(
                f"Stock document identity mismatch: {actual!r} != {matches[0]!r}"
            )
        return row, path
