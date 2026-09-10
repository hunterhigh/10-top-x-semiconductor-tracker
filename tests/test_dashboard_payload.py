import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skill" / "scripts"))
sys.path.insert(0, str(ROOT / "scripts"))

from dashboard_payload import build_payload
from storage_layout import file_sha256, make_stock_index, stock_document_relative


ROSTER_PATH = ROOT / "config" / "bloggers.json"
ROSTER = [row for row in json.loads(ROSTER_PATH.read_text(encoding="utf-8"))["bloggers"] if row.get("active", True)]
ACTIVE_ACCOUNT_IDS = tuple(row["id"] for row in ROSTER)


def mention(tweet_id, blogger_id, day, stance, ticker="ABC"):
    return {
        "tweet_id": str(tweet_id),
        "blogger_id": blogger_id,
        "date": day,
        "created_at": f"{day}T12:00:00-04:00",
        "stance": stance,
        "mention_type": "explicit_stance",
        "reasons": [f"{ticker} reason"],
        "text": f"{ticker} {stance}",
        "url": f"https://x.com/{blogger_id}/status/{tweet_id}",
    }


def stock(ticker, mentions, asset_type="equity", short_history=False):
    start = "2026-01-05" if short_history else "2025-07-21"
    return {
        "ticker": ticker,
        "instrument": {
            "instrument_id": f"US:{ticker}",
            "display_code": ticker,
            "display_name": f"{ticker} Corp",
            "display_market": "US",
            "currency": "USD",
            "price_symbol": ticker,
            "verification_status": "verified",
            "asset_type": asset_type,
        },
        "price_status": "ok",
        "price_history_52w": {
            "status": "insufficient_history" if short_history else "ok",
            "first_available_date": start,
            "last_available_date": "2026-07-20",
        },
        "price_series": [
            {"date": start, "close": 50.0},
            {"date": "2026-07-20", "close": 100.0},
        ],
        "mentions": mentions,
    }


class DashboardPayloadTests(unittest.TestCase):
    def test_packaged_contract_resources_match_the_final_handoff(self):
        handoff = ROOT / "handoff" / "10V-dashboard-backend-handoff-final-2026-07-17"
        pairs = [
            (ROOT / "skill/references/final-ui/10-market-voices-complete.html", handoff / "01-final-ui/10-market-voices-complete.html"),
            (ROOT / "skill/references/dashboard-render-contract.schema.json", handoff / "02-backend-contract/dashboard-render-contract.schema.json"),
            (ROOT / "skill/references/report_rules.py", handoff / "03-rules-and-tests/report_rules.py"),
        ]
        for packaged, original in pairs:
            self.assertTrue(packaged.is_file())
            self.assertEqual(hashlib.sha256(packaged.read_bytes()).hexdigest(), hashlib.sha256(original.read_bytes()).hexdigest())

    def test_payload_cardinality_tracks_registry_for_add_and_remove(self):
        for count in (8, 12):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                db = root / "data" / "db"
                (db / "stocks").mkdir(parents=True)
                roster_path = root / "config" / "bloggers.json"
                roster_path.parent.mkdir(parents=True)
                roster_path.write_text(json.dumps({"bloggers": [
                    {
                        "id": f"account_{index}",
                        "display_name": f"Account {index}",
                        "handle": f"@account_{index}",
                        "x_url": f"https://x.com/account_{index}",
                        "signal_type": "opinion",
                    }
                    for index in range(count)
                ]}), encoding="utf-8")
                index = make_stock_index([], generated_at="2026-07-21T01:00:00Z")
                (db / "index.json").write_text(json.dumps(index), encoding="utf-8")
                (db / "manifest.json").write_text(json.dumps({
                    "generated_at": "2026-07-21T01:00:00Z",
                    "schema_version": 2,
                    "storage_layout": "hash-sharded-v1",
                    "date_range": ["2026-07-20", "2026-07-20"],
                    "stock_count": 0,
                    "index_sha256": file_sha256(db / "index.json"),
                }), encoding="utf-8")
                payload = build_payload(db, "2026-07-20", roster_path=roster_path)
                self.assertEqual(payload["meta"]["tracked_account_count"], count)
                self.assertEqual(len(payload["people"]), count)
                self.assertEqual(len(payload["monthly"]["top_picks"]), count)

    def test_dynamic_roster_scores_only_opinion_accounts_and_keeps_deterministic_cards(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "db"
            (db / "stocks").mkdir(parents=True)

            rows = [
                mention(1, "aleabitoreddit", "2026-07-20", "bullish"),
                mention(2, "frank_trading", "2026-07-20", "bullish"),
                mention(3, "jukan05", "2026-07-20", "bearish"),
                mention(4, "asklivermore", "2026-07-20", "bullish"),
                mention(5, "StockMKTNewz", "2026-07-20", "bullish"),
                mention(6, "DJTRadar", "2026-07-20", "bearish"),
                mention(7, "unusual_whales", "2026-07-20", "bullish"),
                mention(8, "zephyr_z9", "2026-07-20", "bearish"),
            ]
            abc = stock("ABC", rows, short_history=True)
            abc_path = db.joinpath(*stock_document_relative("US:ABC").parts)
            abc_path.parent.mkdir(parents=True, exist_ok=True)
            abc_path.write_text(json.dumps(abc), encoding="utf-8")
            # A bullish ETF must not enter monthly rows or favorite rankings.
            etf_rows = [mention(20 + i, account, "2026-07-20", "bullish", "ETF1") for i, account in enumerate(ACTIVE_ACCOUNT_IDS)]
            etf = stock("ETF1", etf_rows, asset_type="etf")
            etf_path = db.joinpath(*stock_document_relative("US:ETF1").parts)
            etf_path.parent.mkdir(parents=True, exist_ok=True)
            etf_path.write_text(json.dumps(etf), encoding="utf-8")
            index_rows = [
                {
                    "ticker": doc["ticker"],
                    "instrument": doc["instrument"],
                    "total_mentions": len(doc["mentions"]),
                }
                for doc in (abc, etf)
            ]
            index = make_stock_index(
                index_rows, generated_at="2026-07-21T01:00:00Z"
            )
            (db / "index.json").write_text(
                json.dumps(index, indent=2), encoding="utf-8"
            )
            (db / "manifest.json").write_text(
                json.dumps({
                    "generated_at": "2026-07-21T01:00:00Z",
                    "schema_version": 2,
                    "storage_layout": "hash-sharded-v1",
                    "date_range": ["2026-07-20", "2026-07-20"],
                    "stock_count": 2,
                    "index_sha256": file_sha256(db / "index.json"),
                }),
                encoding="utf-8",
            )

            payload = build_payload(db, "2026-07-20", roster_path=ROSTER_PATH)
            self.assertEqual(payload["meta"]["scored_account_count"], 9)
            self.assertEqual(payload["meta"]["tracked_account_count"], 11)
            self.assertEqual(len(payload["people"]), 11)
            self.assertEqual(len(payload["monthly"]["top_picks"]), 11)
            self.assertNotIn("zephyr_z9", {person["blogger_id"] for person in payload["people"]})
            self.assertNotIn("unusual_whales", {person["blogger_id"] for person in payload["people"]})

            disagreement = payload["daily"]["disagreement"][0]
            self.assertEqual(
                {row["blogger_id"] for row in disagreement["bullish_accounts"]},
                {"aleabitoreddit", "frank_trading", "asklivermore"},
            )
            self.assertEqual(
                {row["blogger_id"] for row in disagreement["bearish_accounts"]},
                {"jukan05"},
            )

            monthly = payload["monthly"]["rows"]
            self.assertEqual([row["instrument"]["display_code"] for row in monthly], ["ABC"])
            self.assertEqual(set(monthly[0]["directional_account_ids"]), {
                "aleabitoreddit", "frank_trading", "asklivermore", "jukan05"
            })
            self.assertEqual(monthly[0]["price_change"], monthly[0]["price_change_28d"])
            self.assertEqual(monthly[0]["price_change_52w"]["status"], "ok")
            self.assertEqual(monthly[0]["price_change_52w"]["basis"], "available_history_fallback")
            self.assertEqual(monthly[0]["price_change_52w"]["history_status"], "insufficient_history")

            picks = {row["blogger_id"]: row for row in payload["monthly"]["top_picks"]}
            self.assertEqual(picks["frank_trading"]["instrument"]["display_code"], "ABC")
            self.assertIsNone(picks["StockMKTNewz"]["instrument"])
            self.assertIsNone(picks["DJTRadar"]["instrument"])
            self.assertNotIn("unusual_whales", picks)
            self.assertIsNone(picks["michaelsikand"]["instrument"])
            self.assertIn("ETF1", payload["stock_drilldowns"])
            self.assertNotIn("ETF1", [row["instrument"]["display_code"] for row in payload["monthly"]["rows"]])
            self.assertNotIn("ETF1", {
                row["instrument"]["display_code"]
                for row in payload["monthly"]["top_picks"] if row["instrument"]
            })


if __name__ == "__main__":
    unittest.main()
