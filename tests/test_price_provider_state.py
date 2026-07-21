import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import prices  # noqa: E402


class FakeResponse:
    def __init__(self, rows, status_code=200):
        self.rows = rows
        self.status_code = status_code

    def json(self):
        return self.rows

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeRequests:
    def __init__(self, response):
        self.response = response

    def get(self, url, params=None, timeout=None):
        return self.response


class PriceProviderStateTests(unittest.TestCase):
    def provider(self, rows):
        counter = {"n": 0}
        provider = prices.EODHDProvider("test-key", counter)
        provider._requests = FakeRequests(FakeResponse(rows))
        return provider, counter

    def test_search_auto_selects_only_one_exact_code_and_currency(self):
        provider, counter = self.provider([
            {"Code": "000660", "Exchange": "KO", "Currency": "KRW"},
            {"Code": "000660", "Exchange": "US", "Currency": "USD"},
            {"Code": "000661", "Exchange": "KO", "Currency": "KRW"},
        ])
        self.assertEqual(provider.resolve_symbol("000660.KS", "KRW"), "000660.KO")
        self.assertEqual(counter["n"], 1)

    def test_search_does_not_guess_when_exact_candidates_are_ambiguous(self):
        provider, _ = self.provider([
            {"Code": "ABC", "Exchange": "X", "Currency": "USD"},
            {"Code": "ABC", "Exchange": "Y", "Currency": "USD"},
        ])
        self.assertIsNone(provider.resolve_symbol("ABC", "USD"))

    def test_queue_is_keyed_by_instrument_and_clears_on_completion(self):
        queue = {"items": {}}
        doc = {
            "ticker": "ABC",
            "instrument": {"instrument_id": "US:ABC"},
            "price_history_52w": {"status": "pending", "reason": "budget"},
            "price_source_state": {
                "provider": "eodhd", "provider_symbol": "ABC.US", "status": "deferred",
                "reason": "budget", "attempts": 1, "next_retry_at": "2026-07-21T06:00:00+00:00",
            },
        }
        prices.update_enrichment_queue(queue, doc, "2025-07-21")
        self.assertIn("US:ABC", queue["items"])
        doc["price_history_52w"] = {"status": "ok"}
        prices.update_enrichment_queue(queue, doc, "2025-07-21")
        self.assertNotIn("US:ABC", queue["items"])


if __name__ == "__main__":
    unittest.main()
