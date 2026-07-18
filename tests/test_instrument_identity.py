"""Regression checks for the entity-first instrument contract."""
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_db import instrument_identity, resolve  # noqa: E402
from prices import pick_provider  # noqa: E402


class InstrumentIdentityTests(unittest.TestCase):
    def test_verified_krx_entry_keeps_market_and_aliases(self):
        entry = {
            "instrument_id": "KRX:000660", "company": "SK Hynix", "exchange": "KRX",
            "market": "KRX", "country": "KR", "currency": "KRW",
            "price_symbol": "000660.KS", "verified": True,
        }
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


if __name__ == "__main__":
    unittest.main()
