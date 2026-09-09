import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import refresh_avatars


VALID_AVATAR = "data:image/png;base64,aW1hZ2U="


class RefreshAvatarsTests(unittest.TestCase):
    def run_refresh(self, previous, fetch_result):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "bloggers.json"
            output = root / "avatar_cache.json"
            config.write_text(json.dumps({
                "bloggers": [{"id": "account", "x_url": "https://x.com/account"}],
            }), encoding="utf-8")
            output.write_text(json.dumps(previous), encoding="utf-8")
            stdout = io.StringIO()
            argv = ["refresh_avatars.py", "--config", str(config), "--output", str(output)]
            with (
                patch.object(refresh_avatars, "fetch", return_value=fetch_result),
                patch.object(sys, "argv", argv),
                redirect_stdout(stdout),
            ):
                status = refresh_avatars.main()
            return status, json.loads(output.read_text(encoding="utf-8")), stdout.getvalue()

    def test_retains_valid_cache_when_x_refresh_is_blocked(self):
        status, output, log = self.run_refresh(
            {"account": VALID_AVATAR},
            (None, "HTTPError: 429 Too Many Requests"),
        )

        self.assertEqual(status, 0)
        self.assertEqual(output["account"], VALID_AVATAR)
        self.assertIn('"stale_cache": ["account"]', log)
        self.assertIn("::warning::", log)

    def test_fails_when_neither_fresh_nor_cached_avatar_is_valid(self):
        status, _, log = self.run_refresh(
            {"account": "not-an-image"},
            (None, "profile response did not contain an og:image"),
        )

        self.assertEqual(status, 2)
        self.assertIn('"missing": ["account"]', log)
        self.assertIn("::error::", log)

    def test_replaces_cache_after_successful_refresh(self):
        fresh_avatar = "data:image/jpeg;base64,bmV3LWltYWdl"
        status, output, log = self.run_refresh(
            {"account": VALID_AVATAR},
            (fresh_avatar, None),
        )

        self.assertEqual(status, 0)
        self.assertEqual(output["account"], fresh_avatar)
        self.assertIn('"refreshed": ["account"]', log)


if __name__ == "__main__":
    unittest.main()
