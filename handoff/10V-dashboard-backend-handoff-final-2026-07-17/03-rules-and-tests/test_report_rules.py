import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from report_rules import (  # noqa: E402
    account_change_states,
    classify_main_section,
    classify_weekly_change,
    monthly_consensus_label,
    person_stance_consistency,
    person_window_statistics,
    person_window_state,
    tracked_person_stock_lists,
)


class ReportRuleTests(unittest.TestCase):
    def test_main_sections_are_mutually_exclusive_by_priority(self):
        result = classify_main_section({"a", "b"}, {"c"}, {"a", "b", "c", "d"}, {"a": 4})
        self.assertEqual(result["key"], "disagreement")
        result = classify_main_section({"a", "b"}, set(), {"a", "b", "c"}, {"a": 4})
        self.assertEqual(result["key"], "shared_bullish")
        result = classify_main_section(set(), {"c", "d"}, {"a", "b", "c", "d"}, {"a": 4})
        self.assertEqual(result["key"], "shared_bearish")
        result = classify_main_section({"a"}, set(), {"a", "b", "c"}, {"a": 4})
        self.assertIsNone(result)

    def test_change_priority_and_display_modes(self):
        reversal = classify_weekly_change({"a", "b", "c"}, {"a"}, {"d"}, {"b", "c", "d"})
        self.assertEqual(reversal["label"], "主方向反转")
        self.assertEqual(reversal["display_mode"], "both")
        split = classify_weekly_change({"a", "b"}, {"a", "b"}, set(), {"c"})
        self.assertEqual(split["label"], "新出现分歧")
        new_bull = classify_weekly_change({"a"}, {"a", "b", "c"}, set(), set())
        self.assertEqual(new_bull["group"], "new_multi_bullish")
        weaker = classify_weekly_change({"a", "b", "c"}, {"a"}, set(), set())
        self.assertEqual(weaker["label"], "看多信号人数减少")
        self.assertEqual(weaker["focus_direction"], "bull")

    def test_account_visual_states_do_not_imply_reversal(self):
        states = account_change_states({"a", "b"}, {"b", "c"})
        self.assertEqual(states, [
            {"account": "b", "state": "retained"},
            {"account": "c", "state": "added"},
            {"account": "a", "state": "removed"},
        ])

    def test_monthly_and_person_labels(self):
        self.assertEqual(monthly_consensus_label(6, 4), "偏多")
        self.assertEqual(monthly_consensus_label(4, 6), "偏空")
        self.assertEqual(monthly_consensus_label(5, 5), "多空分歧")
        self.assertEqual(monthly_consensus_label(0, 0), "无明确方向")
        self.assertEqual(person_window_state(True, True, False, True), "both")
        self.assertEqual(person_window_state(False, False, True, True), "no_direction")
        self.assertEqual(person_window_state(False, False, False, False), "not_mentioned")

    def test_person_stance_consistency_thresholds(self):
        self.assertEqual(person_stance_consistency(8, 2), {"percentage": 80, "label": "稳定"})
        self.assertEqual(person_stance_consistency(3, 2), {"percentage": 60, "label": "较稳定"})
        self.assertEqual(person_stance_consistency(1, 1), {"percentage": 50, "label": "多空反复"})
        self.assertEqual(person_stance_consistency(0, 0), {"percentage": None, "label": "无方向信号"})

    def test_person_statistics_follow_selected_window_records(self):
        stats = person_window_statistics([
            {"stance": "bullish"},
            {"direction": "bull"},
            {"stance": "bearish"},
            {"stance": "neutral"},
        ])
        self.assertEqual(stats, {
            "mention_count": 4,
            "bullish_count": 2,
            "neutral_count": 1,
            "bearish_count": 1,
            "state": "both",
            "latest_direction": "neutral",
            "consistency": {"percentage": 67, "label": "较稳定"},
        })
        self.assertEqual(person_window_statistics([])["state"], "not_mentioned")
        self.assertIsNone(person_window_statistics([])["latest_direction"])

    def test_person_card_counts_distinct_stocks_and_exposes_lists(self):
        lists = tracked_person_stock_lists([
            {"stock": "MU", "direction": "bullish"},
            {"stock": "MU", "direction": "bullish"},
            {"stock": "MU", "direction": "bearish"},
            {"stock": "NVDA", "direction": "bullish"},
            {"stock": "META", "direction": "neutral"},
        ])
        self.assertEqual(lists, {"bullish": ["MU", "NVDA"], "bearish": ["MU"], "neutral": ["META"]})


if __name__ == "__main__":
    unittest.main()
