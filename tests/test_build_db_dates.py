import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_db.py"
SPEC = importlib.util.spec_from_file_location("build_db_dates", MODULE_PATH)
BUILD_DB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD_DB)


class BuildDbDateTests(unittest.TestCase):
    def test_new_york_conversion_observes_standard_and_daylight_time(self):
        self.assertEqual(BUILD_DB.parse_date("Mon Jan 05 02:30:00 +0000 2026")[0], "2026-01-04")
        self.assertEqual(BUILD_DB.parse_date("Mon Jul 06 02:30:00 +0000 2026")[0], "2026-07-05")


if __name__ == "__main__":
    unittest.main()
