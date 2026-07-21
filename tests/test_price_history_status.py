import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from price_history_status import effective_history_status, valid_degraded_state  # noqa: E402


class PriceHistoryStatusTests(unittest.TestCase):
    def test_coverage_error_is_not_masked_by_unavailable_price_status(self):
        self.assertEqual(
            effective_history_status("verified", "unavailable", "error"),
            "error",
        )

    def test_known_terminal_and_identity_statuses_are_preserved(self):
        self.assertEqual(effective_history_status("verified", "unavailable", "unavailable"), "unavailable")
        self.assertEqual(effective_history_status("unverified", "pending", "error"), "unverified_symbol")
        self.assertEqual(effective_history_status("verified", "ok", None), "pending")

    def test_only_a_reasoned_scheduled_degradation_can_publish(self):
        self.assertTrue(valid_degraded_state(
            "pending",
            {"status": "deferred", "next_retry_at": "2026-07-21T06:00:00+00:00"},
            "provider budget exhausted",
        ))
        self.assertTrue(valid_degraded_state(
            "error",
            {"status": "retryable_error", "next_retry_at": "2026-07-21T06:00:00+00:00"},
            "network timeout",
        ))
        self.assertFalse(valid_degraded_state("error", {"status": "retryable_error"}, "network timeout"))
        self.assertFalse(valid_degraded_state("error", {"status": "retryable_error", "next_retry_at": "later"}, "network timeout"))
        self.assertFalse(valid_degraded_state("pending", {"status": "deferred"}, None))


if __name__ == "__main__":
    unittest.main()
