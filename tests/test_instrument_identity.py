"""Regression checks for the entity-first instrument contract."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_db import instrument_identity, resolve  # noqa: E402
from prices import pick_provider  # noqa: E402


class InstrumentIdentityTests(unittest.TestCase):
    def test_verified_krx_entry_keeps_market_and_aliases(self):
        entry = {"instrument_id": "KRX:000660", "company": "SK Hynix", "exchange": "KRX", "market": "KRX", "country": "KR", "currency": "KRW", "price_symbol": "000660.KS", "verified": True}
        res = resolve("000660", {"000660": entry})
        identity = instrument_identity("000660", res, {"company": "SK Hynix"}, {"000660", "000660.KS"})
        self.assertEqual(identity["instrument_id"], "KRX:000660")
        self.assertEqual(identity["display_market"], "KRX")
        self.assertIn("000660.KS", identity["aliases"])
        self.assertEqual(pick_provider(res["exchange"], res["currency"], res["mapped"]), "eodhd")

    def test_unknown_identifier_is_not_routed_to_us_provider(self):
        res = resolve("123456", {})
        identity = instrument_identity("123456", res, {"company": None}, {"123456"})
        self.assertEqual(res["verification_status"], "unverified")
        self.assertIsNone(res["currency"])
        self.assertEqual(identity["display_market"], "Market unverified")
        self.assertIsNone(pick_provider(res["exchange"], res["currency"], res["mapped"]))

    def test_dashboard_renders_entity_labels_without_javascript(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp); db = work / "db" / "stocks"; db.mkdir(parents=True)
            config = work / "bloggers.json"
            config.write_text(json.dumps({"bloggers": [{"id": "writer", "display_name": "Writer", "handle": "@writer", "x_url": "https://x.com/writer", "color": "#000", "signal_type": "opinion"}]}), encoding="utf-8")
            def stock(ticker, name, market, status="verified"):
                return {"ticker": ticker, "company": name, "exchange": market if status == "verified" else None, "currency": "KRW" if market == "KRX" else None, "ticker_mapped": status == "verified", "verification_status": status,
                        "instrument": {"instrument_id": f"{market}:{ticker}", "display_code": ticker, "display_name": name, "display_market": market, "aliases": [ticker], "market": market if status == "verified" else None, "currency": "KRW" if market == "KRX" else None, "price_symbol": f"{ticker}.KS" if status == "verified" else None, "verification_status": status},
                        "price_series": [], "price_status": "unavailable", "mentions": [{"date": "2026-07-12", "blogger_id": "writer", "mention_type": "explicit_stance", "stance": "bullish", "reasons": ["fixture"], "text": f"${ticker}", "url": "https://example.test/post"}]}
            (db / "000660.json").write_text(json.dumps(stock("000660", "SK Hynix", "KRX")), encoding="utf-8")
            (db / "123456.json").write_text(json.dumps(stock("123456", "Name unverified", "Market unverified", "unverified")), encoding="utf-8")
            renderer = ROOT / "skill" / "scripts" / "serenity_render.py"
            env = os.environ | {"SERENITY_DB": str(work / "db"), "SERENITY_CONFIG": str(config)}
            result = subprocess.run([sys.executable, str(renderer), "2026-07-12", "--lang", "zh", "--blogger", "all"], cwd=work, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            html = (work / "consensus-tracker-2026-07-12-zh.html").read_text(encoding="utf-8")
            self.assertNotIn("<script", html)
            self.assertNotIn("PACKED_DATA", html)
            self.assertIn("000660 · SK Hynix · KRX", html)
            self.assertIn("123456 · Name unverified · Market unverified", html)


if __name__ == "__main__":
    unittest.main()
