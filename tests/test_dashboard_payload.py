import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skill" / "scripts"))

from dashboard_payload import TRACKED_ACCOUNT_IDS, build_payload


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

    def test_all_ten_accounts_are_scored_and_top_picks_are_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "db"
            (db / "stocks").mkdir(parents=True)
            (db / "manifest.json").write_text(json.dumps({"generated_at": "2026-07-21T01:00:00Z"}), encoding="utf-8")

            rows = [
                mention(1, "aleabitoreddit", "2026-07-20", "bullish"),
                mention(2, "zephyr_z9", "2026-07-20", "bullish"),
                mention(3, "jukan05", "2026-07-20", "bearish"),
                mention(4, "unusual_whales", "2026-07-20", "bullish"),
                mention(5, "StockMKTNewz", "2026-07-20", "bullish"),
                mention(6, "DJTRadar", "2026-07-20", "bearish"),
            ]
            (db / "stocks" / "ABC.json").write_text(json.dumps(stock("ABC", rows, short_history=True)), encoding="utf-8")
            # A bullish ETF must not enter monthly rows or favorite rankings.
            etf_rows = [mention(20 + i, account, "2026-07-20", "bullish", "ETF1") for i, account in enumerate(TRACKED_ACCOUNT_IDS)]
            (db / "stocks" / "ETF1.json").write_text(json.dumps(stock("ETF1", etf_rows, asset_type="etf")), encoding="utf-8")

            payload = build_payload(db, "2026-07-20")
            self.assertEqual(payload["meta"]["scored_account_count"], 10)
            self.assertEqual(len(payload["people"]), 10)
            self.assertEqual(len(payload["monthly"]["top_picks"]), 10)

            disagreement = payload["daily"]["disagreement"][0]
            self.assertEqual(
                {row["blogger_id"] for row in disagreement["bullish_accounts"]},
                {"aleabitoreddit", "zephyr_z9", "unusual_whales", "StockMKTNewz"},
            )
            self.assertEqual(
                {row["blogger_id"] for row in disagreement["bearish_accounts"]},
                {"jukan05", "DJTRadar"},
            )

            monthly = payload["monthly"]["rows"]
            self.assertEqual([row["instrument"]["display_code"] for row in monthly], ["ABC"])
            self.assertEqual(set(monthly[0]["directional_account_ids"]), {
                "aleabitoreddit", "zephyr_z9", "jukan05", "unusual_whales", "StockMKTNewz", "DJTRadar"
            })
            self.assertEqual(monthly[0]["price_change"], monthly[0]["price_change_28d"])
            self.assertEqual(monthly[0]["price_change_52w"]["status"], "ok")
            self.assertEqual(monthly[0]["price_change_52w"]["basis"], "available_history_fallback")
            self.assertEqual(monthly[0]["price_change_52w"]["history_status"], "insufficient_history")

            picks = {row["blogger_id"]: row for row in payload["monthly"]["top_picks"]}
            self.assertEqual(picks["unusual_whales"]["instrument"]["display_code"], "ABC")
            self.assertEqual(picks["StockMKTNewz"]["instrument"]["display_code"], "ABC")
            self.assertIsNone(picks["michaelsikand"]["instrument"])
            self.assertIn("ETF1", payload["stock_drilldowns"])
            self.assertNotIn("ETF1", [row["instrument"]["display_code"] for row in payload["monthly"]["rows"]])
            self.assertNotIn("ETF1", {
                row["instrument"]["display_code"]
                for row in payload["monthly"]["top_picks"] if row["instrument"]
            })


if __name__ == "__main__":
    unittest.main()
