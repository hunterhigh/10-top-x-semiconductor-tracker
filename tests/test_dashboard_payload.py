import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skill" / "scripts"))
from dashboard_payload import build_payload, validate_invariants


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

    def test_monthly_excludes_instruments_without_28_day_posts(self):
        # The 28-day table is a coverage view, not an instrument catalogue.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); db = root / "db"; (db / "stocks").mkdir(parents=True)
            roster = [{"id": f"op{i}", "display_name": str(i), "handle": "@x", "signal_type": "opinion"} for i in range(7)]
            roster += [{"id": "flow", "display_name": "f", "handle": "@f", "signal_type": "flow"}, {"id": "news", "display_name": "n", "handle": "@n", "signal_type": "news"}, {"id": "disc", "display_name": "d", "handle": "@d", "signal_type": "disclosure"}]
            (root / "bloggers.json").write_text(json.dumps({"bloggers": roster}), encoding="utf-8")
            (root / "profiles.json").write_text('{"profiles": []}', encoding="utf-8")
            old = {"ticker": "OLD", "instrument": {"instrument_id": "x:old", "display_code": "OLD", "display_name": "old", "display_market": "US"}, "mentions": [{"tweet_id": "1", "blogger_id": "op0", "date": "2026-06-01", "stance": "bullish", "mention_type": "explicit_stance", "url": "https://x.com/1"}]}
            new = {"ticker": "NEW", "instrument": {"instrument_id": "x:new", "display_code": "NEW", "display_name": "new", "display_market": "US"}, "mentions": [{"tweet_id": "2", "blogger_id": "op0", "date": "2026-07-18", "stance": "bullish", "mention_type": "explicit_stance", "url": "https://x.com/2"}]}
            (db / "stocks/OLD.json").write_text(json.dumps(old), encoding="utf-8")
            (db / "stocks/NEW.json").write_text(json.dumps(new), encoding="utf-8")
            payload = build_payload(db, root / "bloggers.json", root / "profiles.json", "2026-07-18")
            self.assertEqual([row["instrument"]["display_code"] for row in payload["monthly"]["rows"]], ["NEW"])


if __name__ == "__main__":
    unittest.main()
