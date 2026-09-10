import base64
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import refresh_avatars


PNG_BYTES = b"\x89PNG\r\n\x1a\nimage"
JPEG_BYTES = b"\xff\xd8\xffimage"
VALID_AVATAR = "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode("ascii")
PROFILE_URL = "https://pbs.twimg.com/profile_images/1/avatar.jpg"


class Headers:
    def __init__(self, content_type="image/png", content_length=None):
        self.content_type = content_type
        self.content_length = content_length

    def get_content_type(self):
        return self.content_type

    def get(self, name):
        if name == "Content-Length" and self.content_length is not None:
            return str(self.content_length)
        return None


class AvatarResponse:
    def __init__(self, raw=PNG_BYTES, url=PROFILE_URL, headers=None):
        self.raw = raw
        self.url = url
        self.headers = headers or Headers()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self.url

    def read(self, _limit):
        return self.raw


class RefreshAvatarsTests(unittest.TestCase):
    def run_refresh(self, previous, fetch_result, *, profile=None, write_profile=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "bloggers.json"
            profiles_root = root / "bloggers"
            output = root / "avatar_cache.json"
            status_output = root / "avatar_status.json"
            config.write_text(json.dumps({
                "bloggers": [{
                    "id": "account", "display_name": "Account", "handle": "@account",
                    "x_url": "https://x.com/account", "signal_type": "opinion",
                    "avatar_letter": "A", "color": "#123456",
                }],
            }), encoding="utf-8")
            if write_profile:
                account_dir = profiles_root / "account"
                account_dir.mkdir(parents=True)
                account_dir.joinpath("profile.json").write_text(json.dumps(profile or {
                    "schema_version": 1,
                    "id": "1",
                    "user_name": "account",
                    "avatar_url": PROFILE_URL,
                    "observed_at": "2026-09-10T03:20:00Z",
                }), encoding="utf-8")
            output.write_text(json.dumps(previous), encoding="utf-8")
            stdout = io.StringIO()
            argv = [
                "refresh_avatars.py", "--config", str(config), "--output", str(output),
                "--profiles-root", str(profiles_root),
                "--status-output", str(status_output),
            ]
            with (
                patch.object(refresh_avatars, "fetch", return_value=fetch_result) as fetch,
                patch.object(sys, "argv", argv),
                redirect_stdout(stdout),
            ):
                status = refresh_avatars.main()
            return (
                status,
                json.loads(output.read_text(encoding="utf-8")),
                json.loads(status_output.read_text(encoding="utf-8")),
                stdout.getvalue(),
                fetch,
            )

    def test_retains_valid_cache_when_cdn_refresh_is_blocked(self):
        status, output, report, log, _ = self.run_refresh(
            {"account": VALID_AVATAR},
            (None, "HTTPError: HTTP Error 403: Forbidden"),
        )

        self.assertEqual(status, 0)
        self.assertEqual(output["account"], VALID_AVATAR)
        self.assertEqual(report["stale_cache"], ["account"])
        self.assertIn('"stale_cache": ["account"]', log)
        self.assertIn("::warning::", log)

    def test_uses_letter_avatar_when_neither_fresh_nor_cached_avatar_is_valid(self):
        status, output, report, log, _ = self.run_refresh(
            {"account": "not-an-image"},
            (None, "AvatarValidationError: unsupported image signature"),
        )

        self.assertEqual(status, 0)
        self.assertEqual(report["fallback"], ["account"])
        self.assertEqual(report["missing"], [])
        self.assertTrue(output["account"].startswith("data:image/svg+xml;base64,"))
        self.assertTrue(refresh_avatars.valid_cached_avatar(output["account"]))
        self.assertIn("::warning::", log)

    def test_replaces_cache_after_successful_refresh(self):
        fresh_avatar = "data:image/jpeg;base64," + base64.b64encode(JPEG_BYTES).decode("ascii")
        status, output, report, log, fetch = self.run_refresh(
            {"account": VALID_AVATAR},
            (fresh_avatar, None),
        )

        self.assertEqual(status, 0)
        self.assertEqual(output["account"], fresh_avatar)
        self.assertEqual(report["refreshed"], ["account"])
        self.assertIn('"refreshed": ["account"]', log)
        fetch.assert_called_once_with(PROFILE_URL)
        self.assertNotIn("x.com", fetch.call_args.args[0])

    def test_missing_profile_file_uses_valid_cache_without_network(self):
        status, output, report, _, fetch = self.run_refresh(
            {"account": VALID_AVATAR},
            (None, None),
            write_profile=False,
        )

        self.assertEqual(status, 0)
        self.assertEqual(output["account"], VALID_AVATAR)
        self.assertEqual(report["stale_cache"], ["account"])
        self.assertIn("FileNotFoundError", report["errors"]["account"])
        fetch.assert_not_called()

    def test_profile_username_must_match_configured_account(self):
        status, _, report, _, fetch = self.run_refresh(
            {"account": VALID_AVATAR},
            (None, None),
            profile={
                "schema_version": 1,
                "id": "1",
                "user_name": "different",
                "avatar_url": PROFILE_URL,
                "observed_at": "2026-09-10T03:20:00Z",
            },
        )

        self.assertEqual(status, 0)
        self.assertIn("does not match", report["errors"]["account"])
        fetch.assert_not_called()

    def test_profile_requires_numeric_id_and_observation_time(self):
        status, _, report, _, fetch = self.run_refresh(
            {"account": VALID_AVATAR},
            (None, None),
            profile={
                "schema_version": 1,
                "id": "not-numeric",
                "user_name": "account",
                "avatar_url": PROFILE_URL,
                "observed_at": "",
            },
        )

        self.assertEqual(status, 0)
        self.assertIn("numeric user id", report["errors"]["account"])
        fetch.assert_not_called()

    def test_direct_x_page_is_rejected_before_network_access(self):
        with patch.object(refresh_avatars.AVATAR_OPENER, "open") as open_url:
            image, error = refresh_avatars.fetch("https://x.com/account")

        self.assertIsNone(image)
        self.assertIn("not allowlisted", error)
        open_url.assert_not_called()

    def test_valid_image_download_uses_detected_mime(self):
        response = AvatarResponse(raw=JPEG_BYTES, headers=Headers("image/jpeg"))
        with patch.object(refresh_avatars.AVATAR_OPENER, "open", return_value=response):
            image, error = refresh_avatars.fetch(PROFILE_URL)

        self.assertIsNone(error)
        self.assertTrue(image.startswith("data:image/jpeg;base64,"))

    def test_non_image_response_is_rejected(self):
        response = AvatarResponse(raw=b"<html>blocked</html>", headers=Headers("text/html"))
        with patch.object(refresh_avatars.AVATAR_OPENER, "open", return_value=response):
            image, error = refresh_avatars.fetch(PROFILE_URL)

        self.assertIsNone(image)
        self.assertIn("was not an image", error)

    def test_oversized_response_is_rejected_before_read(self):
        response = AvatarResponse(headers=Headers("image/png", refresh_avatars.MAX_AVATAR_BYTES + 1))
        with patch.object(refresh_avatars.AVATAR_OPENER, "open", return_value=response):
            image, error = refresh_avatars.fetch(PROFILE_URL)

        self.assertIsNone(image)
        self.assertIn("2 MiB", error)

    def test_redirect_to_non_allowlisted_host_is_rejected(self):
        handler = refresh_avatars.SafeAvatarRedirectHandler()
        with self.assertRaises(refresh_avatars.AvatarValidationError):
            handler.redirect_request(None, None, 302, "Found", {}, "https://example.com/avatar.jpg")


if __name__ == "__main__":
    unittest.main()
