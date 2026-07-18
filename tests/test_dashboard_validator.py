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


def report_html(avatar: str = "data:image/svg+xml;base64,AA==") -> str:
    windows = {key: [{}] for key in ("today", "days_7", "days_28")}
    payload = {
        "people": [{"avatar_data_uri": avatar}],
        "stock_drilldowns": {"NVDA": {"person_windows": windows}},
    }
    return (
        "<!doctype html><!-- final-ui-sha256: fixture -->"
        f"<script>const PAYLOAD={json.dumps(payload)};const esc=1;"
        "function showStock(){} function showPerson(){} // #stock=</script>"
    )


class DashboardValidatorTests(unittest.TestCase):
    def validate(self, html: str):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.html"
            path.write_text(html, encoding="utf-8")
            return VALIDATOR.structural_checks(path, 1)

    def test_accepts_v2_embedded_payload_and_routes(self):
        errors, summary = self.validate(report_html())
        self.assertEqual(errors, [])
        self.assertTrue(summary["v2_payload"])
        self.assertEqual(summary["embedded_avatars"], 1)

    def test_rejects_missing_embedded_avatar(self):
        errors, _ = self.validate(report_html(""))
        self.assertTrue(any("missing or non-embedded avatar" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
