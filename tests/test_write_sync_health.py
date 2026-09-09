import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import write_sync_health as health


NOW = "2026-09-09T03:05:17Z"
LATER = "2026-09-09T03:33:18Z"
AVATAR = "data:image/png;base64,aW1hZ2U="


class SyncHealthStateTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "GITHUB_REPOSITORY": "hunterhigh/10-top-x-semiconductor-tracker",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_RUN_ID": "12345",
            "GITHUB_EVENT_NAME": "repository_dispatch",
            "GITHUB_SHA": "source-sha",
        }, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_running_preserves_last_success_and_metrics(self):
        previous = health.initial_state(NOW)
        previous["freshness"]["last_success_at"] = NOW
        previous["metrics"]["tickers"] = 2248

        state = health.build_running(previous, now=LATER, source_sha="locked-sha")

        self.assertEqual(state["status"], "running")
        self.assertEqual(state["freshness"]["last_success_at"], NOW)
        self.assertEqual(state["metrics"]["tickers"], 2248)
        self.assertEqual(state["run"]["source_sha"], "locked-sha")

    def test_repeated_degradation_keeps_incident_and_reaches_threshold(self):
        previous = health.initial_state(NOW)
        previous = health.build_terminal(
            previous,
            now=NOW,
            status="degraded",
            severity="P2",
            stage="avatars",
            error_code="AVATAR_REFRESH_DEGRADED",
            summary="X returned 403",
            published_sha="first",
            data_cutoff_et="2026-09-08",
            metrics=self.metrics(),
        )
        incident_id = previous["failure_state"]["incident_id"]
        previous["failure_state"]["consecutive_degraded_runs"] = 2
        running = health.build_running(previous, now=LATER)

        state = health.build_terminal(
            running,
            now=LATER,
            status="degraded",
            severity="P2",
            stage="avatars",
            error_code="AVATAR_REFRESH_DEGRADED",
            summary="X returned 403 again",
            published_sha="second",
            data_cutoff_et="2026-09-08",
            metrics=self.metrics(),
        )

        self.assertEqual(state["failure_state"]["incident_id"], incident_id)
        self.assertEqual(state["failure_state"]["consecutive_degraded_runs"], 3)
        self.assertEqual(state["failure_state"]["consecutive_failures"], 0)

    def test_p2_does_not_replace_last_actionable_incident(self):
        previous = health.initial_state(NOW)
        failed = health.build_terminal(
            previous,
            now=NOW,
            status="failed",
            severity="P1",
            stage="database",
            error_code="DATABASE_BUILD_FAILED",
            summary="database failed",
        )
        actionable_id = failed["failure_state"]["last_actionable_incident_id"]
        running = health.build_running(failed, now=LATER)

        degraded = health.build_terminal(
            running,
            now=LATER,
            status="degraded",
            severity="P2",
            stage="avatars",
            error_code="AVATAR_REFRESH_DEGRADED",
            summary="avatar fallback used",
            published_sha="published",
            data_cutoff_et="2026-09-08",
            metrics=self.metrics(),
        )

        self.assertEqual(degraded["failure_state"]["last_actionable_incident_id"], actionable_id)
        self.assertEqual(degraded["failure_state"]["last_error_code"], "AVATAR_REFRESH_DEGRADED")

    def test_healthy_resets_counters_and_preserves_error_history(self):
        previous = health.initial_state(NOW)
        previous["failure_state"].update({
            "consecutive_failures": 2,
            "incident_id": "1:PUBLISH_FAILED",
            "last_error_at": NOW,
            "last_error_code": "PUBLISH_FAILED",
            "last_error_severity": "P1",
            "last_error_run_url": "https://github.com/run/1",
            "last_actionable_incident_id": "1:PUBLISH_FAILED",
            "last_actionable_error_at": NOW,
            "last_actionable_error_code": "PUBLISH_FAILED",
            "last_actionable_error_severity": "P1",
            "last_actionable_error_run_url": "https://github.com/run/1",
        })

        state = health.build_terminal(
            previous,
            now=LATER,
            status="healthy",
            severity="none",
            stage="complete",
            error_code=None,
            summary="recovered",
            published_sha="published",
            data_cutoff_et="2026-09-08",
            metrics=self.metrics(),
        )

        self.assertEqual(state["failure_state"]["consecutive_failures"], 0)
        self.assertEqual(state["failure_state"]["last_actionable_error_code"], "PUBLISH_FAILED")
        self.assertEqual(state["freshness"]["last_success_at"], LATER)

    def test_summary_is_normalized_and_truncated(self):
        state = health.build_terminal(
            health.initial_state(NOW),
            now=LATER,
            status="failed",
            severity="P1",
            stage="unknown",
            error_code="UNKNOWN_PIPELINE_ERROR",
            summary="  repeated\nspace  " + ("x" * 400),
        )

        self.assertEqual(len(state["summary"]), health.MAX_SUMMARY_CHARS)
        self.assertNotIn("\n", state["summary"])

    def test_schema_rejects_prohibited_fields(self):
        state = health.initial_state(NOW)
        state["metrics"]["api_token"] = "unsafe"

        with self.assertRaises(health.HealthStateError):
            health.validate_state(state)

    def test_reads_manifest_metrics_without_inventing_missing_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            avatars = root / "avatars.json"
            manifest.write_text(json.dumps({
                "date_range": ["2025-07-02", "2026-09-08"],
                "tickers": 2248,
                "total_mentions": 27187,
                "mentions_by_blogger": {"one": 1, "two": 2},
            }), encoding="utf-8")
            avatars.write_text(json.dumps({"one": AVATAR, "two": "invalid"}), encoding="utf-8")

            cutoff, metrics = health.read_metrics(manifest, avatars)

        self.assertEqual(cutoff, "2026-09-08")
        self.assertEqual(metrics["accounts_complete"], 2)
        self.assertEqual(metrics["valid_avatars"], 1)
        self.assertIsNone(metrics["priced_tickers"])

    @staticmethod
    def metrics():
        return {
            "accounts_complete": 10,
            "tickers": 2248,
            "mentions": 27187,
            "priced_tickers": 726,
            "valid_avatars": 10,
        }


class GitHubHealthStoreTests(unittest.TestCase):
    def test_compare_and_swap_conflict_rereads_and_rebuilds(self):
        first = health.initial_state(NOW)
        second = health.initial_state(LATER)
        encoded_first = base64.b64encode(health.compact_json(first)).decode("ascii")
        encoded_second = base64.b64encode(health.compact_json(second)).decode("ascii")
        store = health.GitHubHealthStore("owner/repo", "token")
        calls = []

        def request(method, endpoint, payload=None):
            calls.append((method, endpoint, payload))
            if len(calls) == 1:
                return {"content": encoded_first, "sha": "first-sha"}
            if len(calls) == 2:
                raise health.GitHubApiError(409, "conflict")
            if len(calls) == 3:
                return {"content": encoded_second, "sha": "second-sha"}
            return {"commit": {"sha": "updated"}}

        with patch.object(store, "_request", side_effect=request):
            result = store.update(
                lambda previous: health.build_running(previous, now="2026-09-09T04:00:00Z"),
                "update",
            )

        self.assertEqual(result["freshness"], second["freshness"])
        self.assertEqual(calls[-1][2]["sha"], "second-sha")
        self.assertEqual(len(calls), 4)


class OutcomeClassificationTests(unittest.TestCase):
    def test_core_failure_outranks_avatar_degradation(self):
        result = health.classify_outcome(
            {"DATA_VALIDATION_OUTCOME": "failure"},
            avatar_report={"stale_cache": ["one"]},
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["severity"], "P0")
        self.assertEqual(result["error_code"], "DATA_VALIDATION_FAILED")

    def test_avatar_fallback_is_p2_degradation(self):
        result = health.classify_outcome(
            {},
            avatar_report={"stale_cache": ["one", "two"], "missing": []},
        )

        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["error_code"], "AVATAR_REFRESH_DEGRADED")
        self.assertIn("2 account(s)", result["summary"])

    def test_missing_avatar_report_is_actionable(self):
        result = health.classify_outcome({}, avatar_report=None)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stage"], "health-collection")

    def test_publish_conflict_has_specific_code(self):
        result = health.classify_outcome(
            {"PUBLISH_OUTCOME": "failure"},
            avatar_report={"stale_cache": []},
            publish_error_code="PUBLISH_CONFLICT",
        )

        self.assertEqual(result["error_code"], "PUBLISH_CONFLICT")

    def test_all_success_is_healthy(self):
        result = health.classify_outcome({}, avatar_report={"stale_cache": [], "missing": []})

        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["severity"], "none")


if __name__ == "__main__":
    unittest.main()
