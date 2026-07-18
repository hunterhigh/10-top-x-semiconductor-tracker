import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skill" / "scripts"))
from dashboard_payload import build_payload, validate_invariants


class DashboardPayloadTests(unittest.TestCase):
    def test_payload_uses_traceable_windows_and_separates_signal_sources(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); db = root / "db"; (db / "stocks").mkdir(parents=True)
            roster = [{"id": f"op{i}", "display_name": f"Opinion {i}", "handle": f"@op{i}", "x_url": f"https://x.com/op{i}", "signal_type": "opinion", "color": "#123456"} for i in range(1, 8)]
            roster += [{"id": "flow", "display_name": "Flow", "handle": "@flow", "x_url": "https://x.com/flow", "signal_type": "flow"}, {"id": "news", "display_name": "News", "handle": "@news", "x_url": "https://x.com/news", "signal_type": "news"}, {"id": "disc", "display_name": "Disclosure", "handle": "@disc", "x_url": "https://x.com/disc", "signal_type": "disclosure"}]
            (root / "bloggers.json").write_text(json.dumps({"bloggers": roster}), encoding="utf-8")
            (root / "profiles.json").write_text(json.dumps({"profiles": []}), encoding="utf-8")
            mentions = [
                {"tweet_id": "1", "blogger_id": "op1", "date": "2026-07-11", "created_at": "2026-07-11T12:00:00-04:00", "stance": "bullish", "mention_type": "explicit_stance", "reasons": ["demand"], "text": "bull", "url": "https://x.com/op1/status/1"},
                {"tweet_id": "2", "blogger_id": "op2", "date": "2026-07-11", "created_at": "2026-07-11T13:00:00-04:00", "stance": "bullish", "mention_type": "explicit_stance", "reasons": [], "text": "bull", "url": "https://x.com/op2/status/2"},
                {"tweet_id": "3", "blogger_id": "op3", "date": "2026-07-11", "created_at": "2026-07-11T14:00:00-04:00", "stance": "bearish", "mention_type": "explicit_stance", "reasons": ["valuation"], "text": "bear", "url": "https://x.com/op3/status/3"},
                {"tweet_id": "4", "blogger_id": "flow", "date": "2026-07-11", "stance": "bullish", "mention_type": "explicit_stance", "reasons": ["flow"], "text": "flow", "url": "https://x.com/flow/status/4"},
            ]
            doc = {"ticker": "ABC", "instrument": {"instrument_id": "US:ABC", "display_code": "ABC", "display_name": "ABC Corp", "display_market": "US", "currency": "USD", "price_symbol": "ABC", "verification_status": "verified"}, "price_status": "ok", "price_series": [{"date": "2026-07-10", "close": 100}, {"date": "2026-07-11", "close": 110}], "mentions": mentions}
            (db / "stocks" / "ABC.json").write_text(json.dumps(doc), encoding="utf-8")
            payload = build_payload(db, root / "bloggers.json", root / "profiles.json", "2026-07-11", root / "avatars.json")
            validate_invariants(payload)
            self.assertEqual(payload["daily"]["disagreement"][0]["instrument"]["display_code"], "ABC")
            self.assertEqual([x["blogger_id"] for x in payload["daily"]["disagreement"][0]["bullish_accounts"]], ["op1", "op2"])
            self.assertNotIn("flow", [x["blogger_id"] for x in payload["daily"]["disagreement"][0]["bullish_accounts"]])
            drill = payload["stock_drilldowns"]["ABC"]
            self.assertEqual(len(drill["person_windows"]["today"]), 10)
            self.assertEqual(drill["people_by_window"]["today"][0]["latest"]["created_at"], "2026-07-11T12:00:00-04:00")


if __name__ == "__main__":
    unittest.main()
