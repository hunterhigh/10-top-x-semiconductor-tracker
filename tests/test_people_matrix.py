"""Presentation-level checks for the static investor report."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "skill" / "scripts" / "serenity_render.py"


class PeopleMatrixTests(unittest.TestCase):
    def test_static_report_keeps_evidence_and_separates_signal_records(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td); (work / "db" / "stocks").mkdir(parents=True)
            opinions = [{"id": f"op{i}", "display_name": f"Opinion {i}", "handle": f"@op{i}", "signal_type": "opinion", "color": "#4f46e5"} for i in range(1, 8)]
            signals = [{"id": "flow", "display_name": "Flow", "handle": "@flow", "signal_type": "flow"}, {"id": "news", "display_name": "News", "handle": "@news", "signal_type": "news"}, {"id": "disclosure", "display_name": "Disclosure", "handle": "@disc", "signal_type": "disclosure"}]
            (work / "bloggers.json").write_text(json.dumps({"bloggers": opinions + signals}), encoding="utf-8")
            mentions = [
                {"date": "2026-07-10", "blogger_id": "op1", "mention_type": "explicit_stance", "stance": "bearish", "text": "old bear", "url": "https://x.test/0", "reasons": ["old risk"]},
                {"date": "2026-07-12", "blogger_id": "op1", "mention_type": "explicit_stance", "stance": "bullish", "text": "bull", "url": "https://x.test/1"},
                {"date": "2026-07-12", "blogger_id": "op2", "mention_type": "explicit_stance", "stance": "bearish", "text": "bear", "url": "https://x.test/2"},
                {"date": "2026-07-12", "blogger_id": "op3", "mention_type": "background", "text": "context", "url": "https://x.test/3"},
                {"date": "2026-07-12", "blogger_id": "flow", "mention_type": "background", "text": "flow", "url": "https://x.test/4"},
            ]
            doc = {"ticker": "000660", "company": "SK Hynix", "currency": "KRW", "price_status": "ok", "price_series": [{"date": "2026-06-15", "close": 100}, {"date": "2026-07-12", "close": 110}], "instrument": {"display_code": "000660", "display_name": "SK Hynix", "display_market": "KRX", "aliases": ["000660", "000660.KS"], "verification_status": "verified"}, "mentions": mentions}
            (work / "db" / "stocks" / "000660.json").write_text(json.dumps(doc), encoding="utf-8")
            env = os.environ | {"SERENITY_DB": str(work / "db"), "SERENITY_CONFIG": str(work / "bloggers.json")}
            result = subprocess.run([sys.executable, str(RENDERER), "2026-07-12", "--lang", "en"], cwd=work, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            html = (work / "consensus-tracker-2026-07-12.html").read_text(encoding="utf-8")
            self.assertNotIn('class="rail"', html)
            self.assertNotIn("<script", html)
            self.assertIn("000660 · SK Hynix · KRX", html)
            self.assertIn("Period performance", html)
            self.assertIn("Time context", html)
            self.assertIn("Price window", html)
            self.assertIn("Latest mention", html)
            performance_cell = html.split('class="table-performance', 1)[1].split('class="time-context"', 1)[0]
            self.assertIn("+10.00%", performance_cell)
            self.assertNotIn("2026-06-15", performance_cell)
            self.assertIn('id="stock-000660" class="route-panel stock-profile"', html)
            self.assertNotIn('<details id="stock-000660"', html)
            profile_start = html.index('id="stock-000660"')
            profile_end = html.find('id="stock-', profile_start + 20)
            if profile_end == -1:
                profile_end = html.index('<details class="glossary"', profile_start)
            profile = html[profile_start:profile_end]
            default_evidence = profile.split('<details class="post-fold"', 1)[0]
            self.assertEqual(default_evidence.count('class="evidence-card'), 2)
            self.assertIn("bull", default_evidence)
            self.assertIn("bear", default_evidence)
            self.assertNotIn("old bear", default_evidence)
            self.assertNotIn("context", default_evidence)
            self.assertNotIn("flow", default_evidence)
            self.assertIn("old bear", profile)
            self.assertIn("context", profile)
            self.assertIn("flow", profile)
            self.assertIn("Recent view changes", profile)
            self.assertIn("Turned", profile)
            self.assertIn("2/7", profile)
            self.assertIn("@op1", profile)
            self.assertIn("https://x.test/1", profile)


if __name__ == "__main__":
    unittest.main()
