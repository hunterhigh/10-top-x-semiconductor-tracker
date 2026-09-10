import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skill" / "scripts"))

from analyze_stock import active_blogger_ids, analyze


class AnalyzeStockRosterTests(unittest.TestCase):
    def test_cross_account_analysis_filters_inactive_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            roster = root / "bloggers.json"
            stock = root / "stock.json"
            roster.write_text(json.dumps({"bloggers": [
                {"id": "active_one"},
                {"id": "inactive_old", "active": False},
            ]}), encoding="utf-8")
            stock.write_text(json.dumps({
                "ticker": "ABC",
                "mentions": [
                    {"blogger_id": "active_one", "date": "2026-09-10", "mention_type": "explicit_stance", "stance": "bullish", "reasons": ["active"], "text": "active", "url": "https://x.com/a/status/1"},
                    {"blogger_id": "inactive_old", "date": "2026-09-10", "mention_type": "explicit_stance", "stance": "bearish", "reasons": ["historical"], "text": "historical", "url": "https://x.com/b/status/2"},
                ],
            }), encoding="utf-8")
            result = analyze(stock, allowed_bloggers=active_blogger_ids(roster))
            self.assertEqual(result["stats"]["total_mentions"], 1)
            self.assertEqual(result["stats"]["bull"], 1)
            self.assertEqual(result["stats"]["bear"], 0)


if __name__ == "__main__":
    unittest.main()
