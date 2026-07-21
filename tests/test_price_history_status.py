import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from price_history_status import effective_history_status  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
