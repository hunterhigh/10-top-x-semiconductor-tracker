import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests


ROOT = Path(__file__).resolve().parents[1]
FETCH_PATH = ROOT / "scripts" / "fetch_tweets.py"
SPEC = importlib.util.spec_from_file_location("fetch_tweets", FETCH_PATH)
FETCH = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(FETCH)


class Response:
    def __init__(self, status_code, payload=None, text="error"):
        self.status_code = status_code
        self.payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self.payload


class Session:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)

    def get(self, *_args, **_kwargs):
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FetchIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name) / "data"
        self.raw, self.state = self._paths()
        self.raw.parent.mkdir(parents=True)
        self.raw.write_text(json.dumps([{"tweet_id": "10", "created_at": "Thu Jul 10 00:00:00 +0000 2026"}]), encoding="utf-8")
        self.state.write_text(json.dumps({"newest_tweet_id": "10", "last_run_utc": "old"}), encoding="utf-8")
        self.before_raw = self.raw.read_bytes()
        self.before_state = self.state.read_bytes()

    def tearDown(self):
        self.temp.cleanup()

    def _paths(self):
        directory = self.data_dir / "bloggers" / "tester"
        return directory / "raw_tweets.json", directory / "state.json"

    def run_fetch(self, outcomes):
        with (
            patch.object(FETCH, "DATA_DIR", self.data_dir),
            patch.object(FETCH, "get_api_key", return_value="test-key"),
            patch.object(FETCH, "session_with_key", return_value=Session(outcomes)),
            patch.object(FETCH.time, "sleep"),
        ):
            FETCH.fetch("tester", backfill=False)

    def assert_failure_preserves_watermark(self, outcome):
        with self.assertRaises(SystemExit) as exit_code:
            self.run_fetch([Response(200, {"data": {"id": "1"}}), outcome])
        self.assertEqual(exit_code.exception.code, 1)
        self.assertEqual(self.raw.read_bytes(), self.before_raw)
        self.assertEqual(self.state.read_bytes(), self.before_state)

    def test_401_preserves_existing_raw_and_state(self):
        self.assert_failure_preserves_watermark(Response(401, text="bad key"))

    def test_402_preserves_existing_raw_and_state(self):
        self.assert_failure_preserves_watermark(Response(402, text="insufficient credit"))

    def test_network_error_preserves_existing_raw_and_state(self):
        self.assert_failure_preserves_watermark(requests.ConnectionError("offline"))

    def test_exhausted_429_preserves_existing_raw_and_state(self):
        with self.assertRaises(SystemExit) as exit_code:
            self.run_fetch([Response(200, {"data": {"id": "1"}})] + [Response(429, text="rate limit")] * 5)
        self.assertEqual(exit_code.exception.code, 1)
        self.assertEqual(self.raw.read_bytes(), self.before_raw)
        self.assertEqual(self.state.read_bytes(), self.before_state)

    def test_200_with_zero_new_tweets_updates_success_receipt(self):
        self.run_fetch([
            Response(200, {"data": {"id": "1"}}),
            Response(200, {"data": {"tweets": [], "has_next_page": False}}),
        ])
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state["newest_tweet_id"], "10")
        self.assertEqual(state["last_api_tweets_seen"], 0)
        self.assertEqual(state["last_new_tweets_added"], 0)
        self.assertIn("last_successful_fetch_utc", state)


if __name__ == "__main__":
    unittest.main()
