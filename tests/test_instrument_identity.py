"""Regression checks for the entity-first instrument contract."""
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_db import instrument_identity, resolve  # noqa: E402
from prices import pick_provider  # noqa: E402


class InstrumentIdentityTests(unittest.TestCase):
    def test_reviewed_us_ticker_resolves_to_priced_identity(self):
        res = resolve("AAPL", {"_us_confirmed": ["AAPL"], "AAPL": {"company": "Apple"}})

        self.assertTrue(res["mapped"])
        self.assertEqual(res["verification_status"], "verified")
        self.assertEqual(res["price_symbol"], "AAPL")
        self.assertEqual(res["exchange"], "US")
        self.assertEqual(res["currency"], "USD")

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

    def test_reviewed_registry_covers_us_overseas_alias_and_non_priceable_entities(self):
        tmap = json.loads((ROOT / "data" / "ticker_map.json").read_text(encoding="utf-8"))

        for symbol, market, currency, price_symbol in (
            ("AAPL", "NASDAQ", "USD", "AAPL"),
            ("SPCX", "NASDAQ", "USD", "SPCX"),
            ("000660", "KRX", "KRW", "000660.KS"),
            ("005930", "KRX", "KRW", "005930.KS"),
            ("285A", "Tokyo", "JPY", "285A.T"),
            ("2454", "Taiwan", "TWD", "2454.TW"),
        ):
            result = resolve(symbol, tmap)
            self.assertEqual(result["verification_status"], "verified", symbol)
            self.assertEqual(result["market"], market, symbol)
            self.assertEqual(result["currency"], currency, symbol)
            self.assertEqual(result["price_symbol"], price_symbol, symbol)

        self.assertEqual(tmap["_aliases"]["APPL"], "AAPL")
        self.assertEqual(tmap["_aliases"]["SK HYNIX"], "000660")
        self.assertFalse(tmap["OPENAI"]["verified"])
        self.assertEqual(tmap["OPENAI"]["security_type"], "private_company")
        self.assertFalse(tmap["SPX"]["verified"])
        self.assertEqual(tmap["SPX"]["security_type"], "index")


if __name__ == "__main__":
    unittest.main()
