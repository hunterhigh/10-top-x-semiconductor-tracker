import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "skill" / "scripts" / "render_dashboard.py"
SPEC = importlib.util.spec_from_file_location("render_dashboard_dynamic", PATH)
RENDERER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(RENDERER)


class RenderDashboardRosterTests(unittest.TestCase):
    def test_runtime_replaces_demo_people_with_payload_people(self):
        source = RENDERER._unpack(RENDERER._RUNTIME_B85, RENDERER.RUNTIME_SHA256)
        runtime = RENDERER._enable_dynamic_people(source)
        self.assertIn("voices.splice(0,voices.length,...PAYLOAD.people.map", runtime)
        self.assertIn("p.profile_summary||''", runtime)
        self.assertEqual(runtime.count("const templateVoices=[...voices]"), 1)


if __name__ == "__main__":
    unittest.main()
