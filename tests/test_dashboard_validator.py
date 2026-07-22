import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "skill" / "scripts" / "validate_dashboard.py"
SPEC = importlib.util.spec_from_file_location("dashboard_validator", VALIDATOR_PATH)
VALIDATOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(VALIDATOR)


def report_html(missing_avatar=False):
    account_ids = [f"account-{index}" for index in range(10)]
    people = [
        {
            "blogger_id": account,
            "avatar_data_uri": "" if missing_avatar and index == 0 else "data:image/svg+xml;base64,AA==",
        }
        for index, account in enumerate(account_ids)
    ]
    states = [{"blogger_id": account, "state": "not_mentioned"} for account in account_ids]
    payload = {
        "people": people,
        "monthly": {
            "rows": [],
            "top_picks": [{"blogger_id": account, "instrument": None} for account in account_ids],
        },
        "stock_drilldowns": {
            "NVDA": {"person_windows": {key: states for key in ("today", "days_7", "days_28")}}
        },
    }
    return (
        "<!doctype html>"
        f'<script id="dashboardPayload" type="application/json">{json.dumps(payload)}</script>'
        '<div class="monthly-favorite-card"></div><div class="quarter-account-popover"></div>'
        '<button id="ret52Sort"></button><script>const consistency_percentage=1;'
        'function openStock(){location.hash="#stock="}</script>'
    )


class DashboardValidatorTests(unittest.TestCase):
    def validate(self, html):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.html"
            path.write_text(html, encoding="utf-8")
            return VALIDATOR.structural_checks(path, 10)

    def test_accepts_new_embedded_payload_and_routes(self):
        errors, summary = self.validate(report_html())
        self.assertEqual(errors, [])
        self.assertTrue(summary["v2_payload"])
        self.assertEqual(summary["embedded_avatars"], 10)
        self.assertEqual(summary["monthly_top_picks"], 10)

    def test_rejects_missing_embedded_avatar(self):
        errors, _ = self.validate(report_html(missing_avatar=True))
        self.assertTrue(any("missing or non-embedded avatar" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
