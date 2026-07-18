"""Regression checks for static account-profile presentation."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "skill" / "scripts" / "serenity_render.py"


class SourceProfileTests(unittest.TestCase):
    def test_profiles_are_static_and_linked_from_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td); db = work / "db" / "stocks"; db.mkdir(parents=True)
            bloggers = {"bloggers": [
                {"id": "op", "display_name": "Opinion", "handle": "@op", "x_url": "https://x.com/op", "signal_type": "opinion"},
                {"id": "flow", "display_name": "Flow", "handle": "@flow", "x_url": "https://x.com/flow", "signal_type": "flow"},
            ]}
            profiles = {"profiles": [{"blogger_id": bid, "reviewed_at": "2026-07-13", "bio": {"en": f"{bid} bio", "zh": f"{bid} bio"}}
                                      for bid in ("op", "flow")]}
            config = work / "bloggers.json"; profile_file = work / "blogger_profiles.json"
            config.write_text(json.dumps(bloggers), encoding="utf-8")
            profile_file.write_text(json.dumps(profiles), encoding="utf-8")
            doc = {"ticker": "000660", "company": "SK Hynix", "price_status": "unverified_symbol",
                   "instrument": {"display_code": "000660", "display_name": "SK Hynix", "display_market": "KRX", "aliases": ["000660", "000660.KS"], "verification_status": "verified"},
                   "mentions": [
                       {"date": "2026-07-12", "blogger_id": "op", "mention_type": "explicit_stance", "stance": "bullish", "text": "original opinion", "url": "https://x.com/op/status/1", "reasons": ["fixture reason"]},
                       {"date": "2026-07-12", "blogger_id": "flow", "mention_type": "background", "text": "original flow", "url": "https://x.com/flow/status/2", "reasons": []},
                   ]}
            (db / "000660.json").write_text(json.dumps(doc), encoding="utf-8")
            env = os.environ | {"SERENITY_DB": str(work / "db"), "SERENITY_CONFIG": str(config), "SERENITY_PROFILES": str(profile_file)}
            result = subprocess.run([sys.executable, str(RENDERER), "2026-07-12", "--lang", "zh", "--blogger", "all"], cwd=work, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            html = (work / "consensus-tracker-2026-07-12-zh.html").read_text(encoding="utf-8")
            self.assertNotIn("<script", html)
            self.assertIn('id="account-op" class="route-panel account-profile"', html)
            self.assertIn('id="account-flow" class="route-panel account-profile"', html)
            self.assertIn('href="#account-op"', html)
            self.assertIn("op bio", html)
            self.assertIn("flow bio", html)
            self.assertIn("近 28 天标的榜", html)
            self.assertIn("活动最多的标的", html)
            self.assertIn("近 28 天摘要", html)
            self.assertIn("本报告如何定位该来源", html)
            self.assertIn("标志性看法", html)
            self.assertIn("全部追踪帖子 · 近 28 天", html)
            self.assertIn('class="archive-root"', html)
            self.assertNotIn('class="archive-root" open', html)
            self.assertIn("original opinion", html)
            self.assertIn("original flow", html)
            flow_profile = html.split('id="account-flow"', 1)[1].split('</article>', 1)[0]
            self.assertNotIn("标志性看法", flow_profile)
            self.assertNotIn("明确分歧", flow_profile)


if __name__ == "__main__":
    unittest.main()
