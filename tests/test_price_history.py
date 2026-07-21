import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skill" / "scripts"))

import prices  # noqa: E402
from dashboard_payload import price_change_52w  # noqa: E402


class FakeProvider:
    def __init__(self, rows=None, failure=None, failure_type=prices.ProviderError):
        self.rows = rows or []
        self.failure = failure
        self.failure_type = failure_type
        self.calls = []

    def fetch_daily(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        if self.failure:
            raise self.failure_type(self.failure)
        return [row for row in self.rows if start <= row["date"] <= end]


class ResolvingProvider(FakeProvider):
    def __init__(self, rows):
        super().__init__(rows=rows)
        self.resolved = False

    def fetch_daily(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        if not self.resolved:
            raise prices.ProviderSymbolNotFound(f"not found: {symbol}")
        return [row for row in self.rows if start <= row["date"] <= end]

    def resolve_symbol(self, symbol, currency=None):
        self.resolved = True
        return "ABC.KO"


def stock_doc():
    return {
        "ticker": "ABC",
        "price_symbol": "ABC",
        "currency": "USD",
        "exchange": "NASDAQ",
        "ticker_mapped": True,
        "first_mention": "2025-09-25",
    }


class PriceHistoryTests(unittest.TestCase):
    def test_history_scope_uses_three_distinct_explicit_opinion_accounts(self):
        opinions = {f"op{i}" for i in range(1, 8)}
        doc = {"mentions": [
            {"blogger_id": "op1", "date": "2026-07-20", "stance": "bullish", "mention_type": "explicit_stance"},
            {"blogger_id": "op2", "date": "2026-07-19", "stance": "bearish", "mention_type": "explicit_stance"},
            {"blogger_id": "news", "date": "2026-07-18", "stance": "bullish", "mention_type": "explicit_stance"},
        ]}
        self.assertFalse(prices.in_history_scope(doc, date(2026, 7, 20), opinions))
        doc["mentions"].append(
            {"blogger_id": "op3", "date": "2026-07-17", "stance": "bullish", "mention_type": "explicit_stance"}
        )
        self.assertTrue(prices.in_history_scope(doc, date(2026, 7, 20), opinions))

    def test_short_cache_is_extended_backwards_and_forwards_in_one_call(self):
        with tempfile.TemporaryDirectory() as td, patch.object(prices, "CACHE_DIR", Path(td)):
            prices.save_cache("ABC", "USD", "USD", [
                {"date": "2025-09-25", "close": 60.0},
                {"date": "2026-07-19", "close": 90.0},
            ])
            provider = FakeProvider([
                {"date": "2025-07-21", "close": 50.0},
                {"date": "2026-07-20", "close": 100.0},
            ])
            series, status, _, reason, coverage, source = prices.fetch_one(
                stock_doc(), {"ABC": {"verified": True}}, {"akshare_us": provider},
                {"n": 0}, False, "2026-07-20", "2025-07-21",
            )

            self.assertEqual(provider.calls, [("ABC", "2025-07-21", "2026-07-20")])
            self.assertEqual([row["date"] for row in series], [
                "2025-07-21", "2025-09-25", "2026-07-19", "2026-07-20"
            ])
            self.assertEqual(status, "ok")
            self.assertIsNone(reason)
            self.assertEqual(coverage["status"], "ok")
            self.assertEqual(source["status"], "supported")
            cached = json.loads((Path(td) / "ABC.json").read_text(encoding="utf-8"))
            self.assertEqual(cached["price_history_52w"]["requested_start"], "2025-07-21")

    def test_successful_provider_response_can_prove_listing_history_is_short(self):
        with tempfile.TemporaryDirectory() as td, patch.object(prices, "CACHE_DIR", Path(td)):
            provider = FakeProvider([
                {"date": "2026-01-05", "close": 20.0},
                {"date": "2026-07-20", "close": 30.0},
            ])
            series, status, _, _, coverage, _ = prices.fetch_one(
                stock_doc(), {"ABC": {"verified": True}}, {"akshare_us": provider},
                {"n": 0}, False, "2026-07-20", "2025-07-21",
            )

            self.assertEqual(status, "ok")
            self.assertEqual(len(series), 2)
            self.assertEqual(coverage["status"], "insufficient_history")
            self.assertEqual(coverage["first_available_date"], "2026-01-05")
            self.assertIn("first_available_close", coverage["reason"])

    def test_prefix_failure_preserves_cache_and_is_not_mislabeled_as_listing_history(self):
        with tempfile.TemporaryDirectory() as td, patch.object(prices, "CACHE_DIR", Path(td)), patch.object(prices.time, "sleep"):
            cached = [
                {"date": "2025-09-25", "close": 60.0},
                {"date": "2026-07-20", "close": 90.0},
            ]
            prices.save_cache("ABC", "USD", "USD", cached)
            provider = FakeProvider(failure="network unavailable")
            series, status, _, reason, coverage, source = prices.fetch_one(
                stock_doc(), {"ABC": {"verified": True}}, {"akshare_us": provider},
                {"n": 0}, False, "2026-07-20", "2025-07-21",
            )

            self.assertEqual(series, cached)
            self.assertEqual(status, "partial")
            self.assertEqual(reason, "network unavailable")
            self.assertEqual(coverage["status"], "error")
            self.assertEqual(source["status"], "retryable_error")

    def test_fetch_failure_without_cache_remains_a_history_error(self):
        with tempfile.TemporaryDirectory() as td, patch.object(prices, "CACHE_DIR", Path(td)), patch.object(prices.time, "sleep"):
            provider = FakeProvider(
                failure="EODHD daily call budget (20) exhausted",
                failure_type=prices.ProviderDeferred,
            )
            _, price_status, _, reason, coverage, source = prices.fetch_one(
                stock_doc(), {"ABC": {"verified": True}}, {"akshare_us": provider},
                {"n": 20}, False, "2026-07-20", "2025-07-21",
            )

            self.assertEqual(price_status, "unavailable")
            self.assertIn("budget", reason)
            self.assertEqual(coverage["status"], "pending")
            self.assertIn("budget", coverage["reason"])
            self.assertEqual(source["status"], "deferred")
            self.assertTrue(source["next_retry_at"])

    def test_tail_failure_does_not_erase_already_complete_history_coverage(self):
        with tempfile.TemporaryDirectory() as td, patch.object(prices, "CACHE_DIR", Path(td)), patch.object(prices.time, "sleep"):
            cached = [
                {"date": "2025-07-21", "close": 50.0},
                {"date": "2026-07-19", "close": 90.0},
            ]
            prices.save_cache("ABC", "USD", "USD", cached)
            provider = FakeProvider(failure="network unavailable")
            series, status, _, _, coverage, _ = prices.fetch_one(
                stock_doc(), {"ABC": {"verified": True}}, {"akshare_us": provider},
                {"n": 0}, False, "2026-07-20", "2025-07-21",
            )

            self.assertEqual(series, cached)
            self.assertEqual(status, "partial")
            self.assertEqual(coverage["status"], "ok")

    def test_provider_404_becomes_cooldown_not_a_global_history_error(self):
        with tempfile.TemporaryDirectory() as td, patch.object(prices, "CACHE_DIR", Path(td)), patch.object(prices.time, "sleep"):
            provider = FakeProvider(
                failure="EODHD 404 (symbol not found: ABC.X)",
                failure_type=prices.ProviderSymbolNotFound,
            )
            _, price_status, _, reason, coverage, source = prices.fetch_one(
                stock_doc(), {"ABC": {"verified": True}}, {"akshare_us": provider},
                {"n": 0}, False, "2026-07-20", "2025-07-21",
            )
            self.assertEqual(price_status, "unavailable")
            self.assertEqual(reason, "EODHD 404 (symbol not found: ABC.X)")
            self.assertEqual(coverage["status"], "unavailable")
            self.assertEqual(coverage["reason"], "provider_symbol_not_found_after_search")
            self.assertEqual(source["status"], "unsupported")
            self.assertEqual(source["http_status"], 404)
            self.assertTrue(source["next_retry_at"])

    def test_auth_failure_is_fatal(self):
        provider = FakeProvider(
            failure="EODHD 401 (bad/expired api_token)",
            failure_type=prices.ProviderAuthError,
        )
        with self.assertRaises(prices.ProviderAuthError):
            prices.fetch_one(
                stock_doc(), {"ABC": {"verified": True}}, {"akshare_us": provider},
                {"n": 0}, False, "2026-07-20", "2025-07-21",
            )

    def test_404_uses_exact_provider_resolution_before_degrading(self):
        with tempfile.TemporaryDirectory() as td, patch.object(prices, "CACHE_DIR", Path(td)):
            provider = ResolvingProvider([
                {"date": "2025-07-21", "close": 50.0},
                {"date": "2026-07-20", "close": 75.0},
            ])
            series, status, _, reason, coverage, source = prices.fetch_one(
                stock_doc(), {"ABC": {"verified": True}}, {"akshare_us": provider},
                {"n": 0}, False, "2026-07-20", "2025-07-21",
            )
            self.assertEqual(len(series), 2)
            self.assertEqual(status, "ok")
            self.assertIsNone(reason)
            self.assertEqual(coverage["status"], "ok")
            self.assertEqual(source["status"], "supported")
            self.assertEqual(source["provider_symbol"], "ABC.KO")

    def test_payload_uses_exact_52_week_target_and_never_returns_zero_for_missing(self):
        complete = {
            "price_status": "ok",
            "price_series": [
                {"date": "2025-07-21", "close": 50.0},
                {"date": "2026-07-20", "close": 100.0},
            ],
        }
        result = price_change_52w(complete, date(2026, 7, 20))
        self.assertEqual(result, {
            "status": "ok", "percentage": 100.0,
            "start_date": "2025-07-21", "end_date": "2026-07-20",
        })

        short = {
            "price_status": "ok",
            "price_series": [
                {"date": "2026-01-05", "close": 20.0},
                {"date": "2026-07-20", "close": 30.0},
            ],
            "price_history_52w": {
                "status": "insufficient_history",
                "first_available_date": "2026-01-05",
                "last_available_date": "2026-07-20",
            },
        }
        result = price_change_52w(short, date(2026, 7, 20))
        self.assertEqual(result["status"], "insufficient_history")
        self.assertIsNone(result["percentage"])


if __name__ == "__main__":
    unittest.main()
