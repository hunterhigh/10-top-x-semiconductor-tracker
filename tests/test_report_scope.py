import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skill" / "scripts"))

from report_scope import (
    is_monthly_report_instrument,
    monthly_history_scope,
    monthly_top_pick_candidates,
    report_window,
)


def row(account, day, stance="bullish", tweet="1"):
    return {
        "blogger_id": account,
        "date": day,
        "created_at": f"{day}T12:00:00-04:00",
        "stance": stance,
        "mention_type": "explicit_stance",
        "tweet_id": tweet,
        "url": f"https://x.com/{account}/status/{tweet}",
    }


def doc(ticker, rows):
    return {
        "ticker": ticker,
        "instrument": {
            "instrument_id": f"US:{ticker}", "display_code": ticker,
            "display_name": ticker, "verification_status": "verified",
            "price_symbol": ticker, "asset_type": "equity",
        },
        "mentions": rows,
    }


class ReportScopeTests(unittest.TestCase):
    def setUp(self):
        self.accounts = {"op1", "op2", "news", "flow"}
        self.asof = date(2026, 7, 20)

    def test_window_is_closed_d_minus_27_through_d_and_scores_all_accounts(self):
        self.assertEqual(report_window(self.asof), ("2026-06-23", "2026-07-20"))
        rows = [row("op1", "2026-06-23"), row("news", "2026-07-20"), row("flow", "2026-07-20", "bearish")]
        self.assertTrue(is_monthly_report_instrument(rows, self.accounts, self.asof))
        rows[0]["date"] = "2026-06-22"
        self.assertFalse(is_monthly_report_instrument(rows, self.accounts, self.asof))

    def test_top_pick_uses_count_then_all_explicit_then_recency_then_ticker(self):
        # AAA and BBB tie on bullish posts; BBB wins on total explicit posts.
        aaa = doc("AAA", [row("op1", "2026-07-18", tweet="1")])
        bbb = doc("BBB", [row("op1", "2026-07-17", tweet="2"), row("op1", "2026-07-16", "bearish", "3")])
        selected = monthly_top_pick_candidates([aaa, bbb], ["op1", "op2"], self.asof)
        self.assertEqual(selected["op1"]["doc"]["ticker"], "BBB")
        self.assertIsNone(selected["op2"]["doc"])

        # Equal counts use the latest bullish timestamp, then alphabetical ticker.
        ccc = doc("CCC", [row("op1", "2026-07-19", tweet="4")])
        ddd = doc("DDD", [row("op1", "2026-07-19", tweet="5")])
        selected = monthly_top_pick_candidates([ddd, ccc], ["op1"], self.asof)
        self.assertEqual(selected["op1"]["doc"]["ticker"], "CCC")

    def test_history_scope_is_monthly_rows_union_unique_top_picks(self):
        monthly = doc("MONTH", [row("op1", "2026-07-20", tweet="1"), row("op2", "2026-07-20", "bearish", "2"), row("news", "2026-07-20", tweet="3")])
        pick = doc("PICK", [row("flow", "2026-07-20", tweet="4")])
        scope = monthly_history_scope([monthly, pick], self.accounts, self.asof)
        self.assertEqual({item["ticker"] for item in scope["docs"]}, {"MONTH", "PICK"})
        self.assertEqual(scope["monthly_instrument_ids"], {"US:MONTH"})
        self.assertEqual(scope["top_pick_instrument_ids"], {"US:MONTH", "US:PICK"})
        self.assertEqual(scope["overlap_instrument_ids"], {"US:MONTH"})


if __name__ == "__main__":
    unittest.main()
