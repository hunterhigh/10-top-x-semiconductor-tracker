import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from migrate_storage_layout import migrate  # noqa: E402
from storage_layout import (  # noqa: E402
    SHARDED_LAYOUT_VERSION,
    StorageLayoutError,
    detect_storage_layout,
    price_cache_relative,
    safe_resolve,
    stock_document_relative,
    stock_document_path,
    validate_snapshot_layout,
)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class StorageLayoutTests(unittest.TestCase):
    def make_v1(self, root: Path) -> Path:
        data = root / "data"
        stock = {
            "ticker": "奇怪/代码",
            "instrument": {"instrument_id": "東京:奇怪/代码", "display_code": "奇怪/代码"},
            "mentions": [{"tweet_id": "1", "url": "https://x.com/example/status/1"}],
            "price_series": [{"date": "2026-07-22", "close": 12.5}],
        }
        # v1 filenames are kept safe even when the semantic identity is not.
        write_json(data / "db" / "stocks" / "SAFE.json", {**stock, "ticker": "SAFE"})
        row = {
            "ticker": "SAFE",
            "instrument": stock["instrument"],
            "total_mentions": 1,
        }
        write_json(data / "db" / "index.json", {"meta": {"total_tickers": 1}, "stocks": [row]})
        write_json(data / "db" / "manifest.json", {"generated_at": "2026-07-23T00:00:00+00:00", "tickers": 1})
        write_json(
            data / "prices_cache" / "SAFE_X.json",
            {"price_symbol": "SAFE/X", "series": stock["price_series"], "last_updated": "unchanged"},
        )
        return data

    def test_hash_paths_are_deterministic_and_filesystem_safe(self):
        first = stock_document_relative("東京:奇怪/代码")
        second = stock_document_relative("東京:奇怪/代码")
        self.assertEqual(first, second)
        self.assertEqual(len(first.parts), 3)
        self.assertEqual(len(first.stem), 64)
        self.assertNotIn("奇怪", first.as_posix())
        cache = price_cache_relative("BRK/B:US")
        self.assertEqual(len(cache.parts), 2)
        self.assertEqual(len(cache.stem), 64)

    def test_safe_resolve_rejects_traversal_and_absolute_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for value in ("../secret.json", "/absolute.json", "stocks/../../secret.json"):
                with self.assertRaises(StorageLayoutError):
                    safe_resolve(root, value)

    def test_dry_run_does_not_change_v1(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self.make_v1(Path(tmp))
            before = sorted(path.relative_to(data).as_posix() for path in data.rglob("*"))
            result = migrate(data, dry_run=True)
            after = sorted(path.relative_to(data).as_posix() for path in data.rglob("*"))
            self.assertEqual(before, after)
            self.assertFalse(result["changed"])
            self.assertEqual(result["provider_calls"], 0)

    def test_migration_preserves_content_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self.make_v1(Path(tmp))
            old_stock = json.loads((data / "db" / "stocks" / "SAFE.json").read_text(encoding="utf-8"))
            old_cache = json.loads((data / "prices_cache" / "SAFE_X.json").read_text(encoding="utf-8"))
            result = migrate(data)
            self.assertTrue(result["changed"])
            self.assertEqual(result["provider_calls"], 0)
            self.assertFalse(list((data / "db" / "stocks").glob("*.json")))
            self.assertFalse(list((data / "prices_cache").glob("*.json")))
            index = json.loads((data / "db" / "index.json").read_text(encoding="utf-8"))
            row = index["stocks"][0]
            self.assertIn("document_path", row)
            new_stock_path = stock_document_path(
                data / "db", row, version=SHARDED_LAYOUT_VERSION, must_exist=True
            )
            self.assertEqual(json.loads(new_stock_path.read_text(encoding="utf-8")), old_stock)
            new_cache_path = data / "prices_cache" / price_cache_relative("SAFE/X")
            self.assertEqual(json.loads(new_cache_path.read_text(encoding="utf-8")), old_cache)
            self.assertEqual(detect_storage_layout(data / "db"), SHARDED_LAYOUT_VERSION)
            self.assertEqual(validate_snapshot_layout(data / "db")["stock_documents"]["files"], 1)
            second = migrate(data)
            self.assertFalse(second["changed"])

    def test_v2_missing_or_tampered_document_path_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = self.make_v1(Path(tmp))
            migrate(data)
            index_path = data / "db" / "index.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index["stocks"][0].pop("document_path")
            write_json(index_path, index)
            with self.assertRaises(StorageLayoutError):
                validate_snapshot_layout(data / "db")


if __name__ == "__main__":
    unittest.main()
