import sys
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skill" / "scripts"))

from report_scope import (  # noqa: E402
    is_monthly_report_instrument,
    monthly_directional_accounts,
    report_window,
)


class ReportScopeTests(unittest.TestCase):
    def setUp(self):
        self.opinions = {f"op{i}" for i in range(1, 8)}
        self.asof = date(2026, 7, 20)

    @staticmethod
    def row(blogger_id, day, stance="bullish", mention_type="explicit_stance"):
        return {
            "blogger_id": blogger_id,
            "date": day,
            "stance": stance,
            "mention_type": mention_type,
        }

    def test_window_is_closed_d_minus_27_through_d(self):
        self.assertEqual(report_window(self.asof), ("2026-06-23", "2026-07-20"))
        rows = [
            self.row("op1", "2026-06-23"),
            self.row("op2", "2026-07-20"),
            self.row("op3", "2026-07-20", "bearish"),
            self.row("op4", "2026-06-22"),
            self.row("op5", "2026-07-21"),
        ]
        self.assertEqual(monthly_directional_accounts(rows, self.opinions, self.asof), {"op1", "op2", "op3"})
        self.assertTrue(is_monthly_report_instrument(rows, self.opinions, self.asof))

    def test_three_distinct_opinion_accounts_are_required(self):
        rows = [
            self.row("op1", "2026-07-20"),
            self.row("op1", "2026-07-19", "bearish"),
            self.row("op2", "2026-07-18"),
        ]
        self.assertFalse(is_monthly_report_instrument(rows, self.opinions, self.asof))
        rows.append(self.row("op3", "2026-07-17"))
        self.assertTrue(is_monthly_report_instrument(rows, self.opinions, self.asof))

    def test_parallel_signal_and_context_records_never_expand_scope(self):
        rows = [
            self.row("op1", "2026-07-20"),
            self.row("op2", "2026-07-20"),
            self.row("news", "2026-07-20"),
            self.row("flow", "2026-07-20", "bearish"),
            self.row("op3", "2026-07-20", "neutral"),
            self.row("op4", "2026-07-20", mention_type="background"),
        ]
        self.assertFalse(is_monthly_report_instrument(rows, self.opinions, self.asof))


if __name__ == "__main__":
    unittest.main()
