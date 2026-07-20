"""Regression tests for fail-closed EODHD instrument resolution."""

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from resolve_tickers_eodhd import company_names_match, resolve, select_verified_us_equity  # noqa: E402


AAPL = {
    "Code": "AAPL", "Exchange": "US", "Name": "Apple Inc", "Type": "Common Stock",
    "Country": "USA", "Currency": "USD", "ISIN": "US0378331005", "isPrimary": True,
}


class FakeClient:
    def __init__(self, rows):
        self.rows = rows

    def search(self, symbol):
        return self.rows


class EodhdResolverTests(unittest.TestCase):
    def test_exact_primary_us_equity_is_verified(self):
        match, reason = select_verified_us_equity("AAPL", "Apple", [AAPL])
        self.assertEqual(match, AAPL)
        self.assertIsNone(reason)
        self.assertTrue(company_names_match("Apple", "Apple Inc"))
        self.assertTrue(company_names_match("AMD", "Advanced Micro Devices Inc"))

    def test_foreign_or_name_mismatch_stays_unverified(self):
        foreign = {**AAPL, "Exchange": "TO", "Currency": "CAD", "isPrimary": False}
        match, reason = select_verified_us_equity("AAPL", "Another Company", [AAPL, foreign])
        self.assertIsNone(match)
        self.assertEqual(reason, "no_unique_primary_us_equity_match")

    def test_resolve_builds_auditable_registry_entry(self):
        review = {"unverified": [{"symbol": "AAPL", "company_name_hint": "Apple"}]}
        resolved, audit = resolve(review, {}, FakeClient([AAPL]))
        self.assertEqual(resolved["AAPL"]["instrument_id"], "US:AAPL")
        self.assertEqual(resolved["AAPL"]["price_symbol"], "AAPL")
        self.assertEqual(audit[0]["status"], "verified")

    def test_incomplete_existing_entry_is_still_resolved(self):
        review = {"unverified": [{"symbol": "AAPL", "company_name_hint": "Apple", "mentions": 3}]}
        resolved, _ = resolve(review, {"AAPL": {"company": "Apple"}}, FakeClient([AAPL]))
        self.assertIn("AAPL", resolved)


if __name__ == "__main__":
    unittest.main()
