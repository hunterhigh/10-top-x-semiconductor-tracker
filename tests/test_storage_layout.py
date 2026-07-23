import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skill" / "scripts"))

from storage_layout import (  # noqa: E402
    StorageLayoutError,
    file_sha256,
    make_stock_index,
    price_cache_relative,
    stock_document_relative,
    validate_snapshot_layout,
    write_price_cache_index,
)
from stock_store import StockStore, StockStoreError  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class StorageLayoutV2Tests(unittest.TestCase):
    def make_snapshot(self, root: Path) -> tuple[Path, Path]:
        db = root / "data" / "db"
        cache = root / "data" / "prices_cache"
        instrument_id = "US:SAFE/X:测试"
        row = {
            "ticker": "SAFE/X",
            "cashtag": "$SAFE/X",
            "company": "Safe Example",
            "total_mentions": 1,
            "instrument": {
                "instrument_id": instrument_id,
                "display_code": "SAFE/X",
                "display_name": "Safe Example",
                "aliases": ["SAFE"],
            },
        }
        stock_path = db.joinpath(*stock_document_relative(instrument_id).parts)
        write_json(
            stock_path,
            {
                "ticker": "SAFE/X",
                "instrument": row["instrument"],
                "mentions": [],
                "price_series": [],
            },
        )
        index = make_stock_index([row], generated_at="2026-07-23T00:00:00+00:00")
        write_json(db / "index.json", index)
        symbol = "SAFE/X.US"
        price_path = cache.joinpath(*price_cache_relative(symbol).parts)
        write_json(
            price_path,
            {
                "price_symbol": symbol,
                "currency": "USD",
                "price_unit": "USD",
                "series": [],
                "last_updated": "2026-07-23T00:00:00+00:00",
            },
        )
        price_index = write_price_cache_index(cache)
        write_json(
            db / "manifest.json",
            {
                "generated_at": "2026-07-23T00:00:00+00:00",
                "schema_version": 2,
                "storage_layout": "hash-sharded-v1",
                "date_range": ["2026-07-23", "2026-07-23"],
                "stock_count": 1,
                "index_sha256": file_sha256(db / "index.json"),
                "stocks_root": "stocks",
                "price_cache_count": len(price_index["prices"]),
                "price_cache_index_sha256": file_sha256(cache / "index.json"),
                "price_cache_root": "prices_cache",
            },
        )
        return db, cache

    def test_hash_paths_are_stable_and_filename_safe(self):
        instrument_id = "US:SAFE/X:测试"
        digest = hashlib.sha256(instrument_id.encode()).hexdigest()
        self.assertEqual(
            stock_document_relative(instrument_id).as_posix(),
            f"stocks/{digest[:2]}/{digest}.json",
        )
        self.assertNotIn("SAFE", price_cache_relative("SAFE/X.US").as_posix())

    def test_complete_snapshot_and_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, cache = self.make_snapshot(Path(tmp))
            result = validate_snapshot_layout(db, cache)
            self.assertEqual(result["stock_documents"]["files"], 1)
            self.assertEqual(result["price_cache"]["files"], 1)
            store = StockStore(db)
            row, path = store.resolve_stock("Safe Example")
            self.assertEqual(row["instrument_id"], "US:SAFE/X:测试")
            self.assertTrue(path.is_file())
            self.assertEqual(len(list(store.iter_stock_paths())), 1)

    def test_semantically_identical_crlf_price_index_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, cache = self.make_snapshot(Path(tmp))
            index_path = cache / "index.json"
            original = json.loads(index_path.read_text(encoding="utf-8"))
            crlf_bytes = (
                json.dumps(original, indent=2, ensure_ascii=False) + "\n"
            ).replace("\n", "\r\n").encode("utf-8")
            index_path.write_bytes(crlf_bytes)

            manifest_path = db / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["price_cache_index_sha256"] = hashlib.sha256(crlf_bytes).hexdigest()
            write_json(manifest_path, manifest)

            rebuilt = write_price_cache_index(cache)

            self.assertEqual(rebuilt, original)
            self.assertEqual(index_path.read_bytes(), crlf_bytes)
            result = validate_snapshot_layout(db, cache)
            self.assertEqual(result["price_cache"]["files"], 1)

    def test_unknown_schema_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, _ = self.make_snapshot(Path(tmp))
            manifest = json.loads((db / "manifest.json").read_text(encoding="utf-8"))
            manifest["schema_version"] = 3
            write_json(db / "manifest.json", manifest)
            with self.assertRaises((StorageLayoutError, StockStoreError)):
                StockStore(db)

    def test_missing_path_and_traversal_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, _ = self.make_snapshot(Path(tmp))
            index = json.loads((db / "index.json").read_text(encoding="utf-8"))
            instrument_id = next(iter(index["stocks"]))
            index["stocks"][instrument_id]["path"] = "../secret.json"
            index["all_paths"] = ["../secret.json"]
            write_json(db / "index.json", index)
            manifest = json.loads((db / "manifest.json").read_text(encoding="utf-8"))
            manifest["index_sha256"] = file_sha256(db / "index.json")
            write_json(db / "manifest.json", manifest)
            with self.assertRaises(StockStoreError):
                list(StockStore(db).iter_stock_paths())

    def test_ambiguous_lookup_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, _ = self.make_snapshot(Path(tmp))
            index = json.loads((db / "index.json").read_text(encoding="utf-8"))
            instrument_id = next(iter(index["stocks"]))
            index["lookup"]["SAFE"] = [instrument_id, "CA:SAFE"]
            write_json(db / "index.json", index)
            manifest = json.loads((db / "manifest.json").read_text(encoding="utf-8"))
            manifest["index_sha256"] = file_sha256(db / "index.json")
            write_json(db / "manifest.json", manifest)
            with self.assertRaises(StockStoreError):
                StockStore(db).resolve_stock("SAFE")


if __name__ == "__main__":
    unittest.main()
