#!/usr/bin/env python3
"""Publish the compact trihourly sync health snapshot to ops-health."""
from __future__ import annotations

import argparse
import base64
import binascii
import copy
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


SCHEMA_VERSION = 1
MAX_FILE_BYTES = 4096
MAX_SUMMARY_CHARS = 240
DEFAULT_BRANCH = "ops-health"
DEFAULT_PATH = "sync_health.json"
TERMINAL_STATUSES = {"healthy", "degraded", "failed"}
SEVERITIES = {"none", "P0", "P1", "P2", "P3"}
FORBIDDEN_KEY_PARTS = ("token", "secret", "header", "response_body", "stack_trace")
OUTCOME_CHECKS = (
    ("checkout", "CHECKOUT_OUTCOME", "P1", "UNKNOWN_PIPELINE_ERROR", "Production checkout failed"),
    ("setup", "SETUP_OUTCOME", "P1", "UNKNOWN_PIPELINE_ERROR", "Python setup failed"),
    ("dependencies", "INSTALL_OUTCOME", "P1", "UNKNOWN_PIPELINE_ERROR", "Dependency installation failed"),
    ("fetch-extract", "FETCH_RESULT", "P1", "FETCH_ACCOUNT_FAILED", "One or more account refreshes failed"),
    ("account-gate", "GATE_OUTCOME", "P1", "FETCH_ACCOUNT_FAILED", "Account refresh gate failed"),
    ("artifacts", "DOWNLOAD_OUTCOME", "P1", "ACCOUNT_ARTIFACT_MISSING", "Account artifact download failed"),
    ("artifacts", "ARTIFACTS_OUTCOME", "P1", "ACCOUNT_ARTIFACT_MISSING", "One or more account artifacts are missing"),
    ("artifacts", "FLATTEN_OUTCOME", "P1", "ACCOUNT_ARTIFACT_MISSING", "Account artifacts could not be prepared"),
    ("database", "DATABASE_OUTCOME", "P1", "DATABASE_BUILD_FAILED", "Database rebuild failed"),
    ("data-validation", "DATA_VALIDATION_OUTCOME", "P0", "DATA_VALIDATION_FAILED", "Rebuilt data failed validation"),
    ("prices", "PRICES_OUTCOME", "P1", "PRICE_REFRESH_FAILED", "Price refresh failed"),
    ("avatars", "AVATARS_OUTCOME", "P1", "AVATAR_CACHE_MISSING", "No valid fresh or cached avatar is available"),
    ("data-validation", "ENRICHMENT_VALIDATION_OUTCOME", "P0", "DATA_VALIDATION_FAILED", "Enriched data failed validation"),
    ("data-cutoff", "CUTOFF_OUTCOME", "P0", "DATA_VALIDATION_FAILED", "Data cutoff could not be determined"),
    ("tests", "TESTS_OUTCOME", "P0", "DATA_VALIDATION_FAILED", "Production rules or pipeline tests failed"),
    ("dashboard", "DASHBOARD_OUTCOME", "P1", "DASHBOARD_VALIDATION_FAILED", "Dashboard build or validation failed"),
    ("metadata", "METADATA_OUTCOME", "P1", "UNKNOWN_PIPELINE_ERROR", "Build metadata generation failed"),
    ("publish", "PUBLISH_OUTCOME", "P1", "PUBLISH_FAILED", "Production data publication failed"),
)


class HealthStateError(ValueError):
    pass


class GitHubApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def compact_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def clean_summary(value: str) -> str:
    return " ".join(value.split())[:MAX_SUMMARY_CHARS]


def empty_failure_state() -> dict[str, Any]:
    return {
        "consecutive_failures": 0,
        "consecutive_degraded_runs": 0,
        "incident_id": None,
        "last_error_at": None,
        "last_error_code": None,
        "last_error_severity": None,
        "last_error_run_url": None,
        "last_actionable_incident_id": None,
        "last_actionable_error_at": None,
        "last_actionable_error_code": None,
        "last_actionable_error_severity": None,
        "last_actionable_error_run_url": None,
    }


def initial_state(now: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "healthy",
        "severity": "none",
        "stage": "initialization",
        "error_code": None,
        "summary": "Health reporting initialized; awaiting the next sync",
        "run": {
            "id": None,
            "url": None,
            "event": "initialization",
            "started_at": now,
            "observed_at": now,
            "source_sha": None,
        },
        "freshness": {
            "last_success_at": None,
            "data_cutoff_et": None,
            "published_sha": None,
        },
        "metrics": {
            "accounts_complete": None,
            "tickers": None,
            "mentions": None,
            "priced_tickers": None,
            "valid_avatars": None,
        },
        "failure_state": empty_failure_state(),
    }


def run_metadata(now: str, *, source_sha: str | None = None) -> dict[str, Any]:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    run_id_raw = os.environ.get("GITHUB_RUN_ID")
    run_id = int(run_id_raw) if run_id_raw and run_id_raw.isdigit() else None
    return {
        "id": run_id,
        "url": f"{server}/{repository}/actions/runs/{run_id}" if repository and run_id else None,
        "event": os.environ.get("GITHUB_EVENT_NAME", "unknown"),
        "started_at": now,
        "observed_at": now,
        "source_sha": source_sha or os.environ.get("GITHUB_SHA"),
    }


def validate_state(state: dict[str, Any]) -> None:
    required = {
        "schema_version", "status", "severity", "stage", "error_code",
        "summary", "run", "freshness", "metrics", "failure_state",
    }
    if set(state) != required:
        raise HealthStateError(f"health state keys do not match schema: {sorted(set(state) ^ required)}")
    if state["schema_version"] != SCHEMA_VERSION:
        raise HealthStateError(f"unsupported schema_version: {state['schema_version']}")
    if state["status"] not in {"running", *TERMINAL_STATUSES}:
        raise HealthStateError(f"invalid status: {state['status']}")
    if state["severity"] not in SEVERITIES:
        raise HealthStateError(f"invalid severity: {state['severity']}")
    if state["status"] in {"running", "healthy"} and state["severity"] != "none":
        raise HealthStateError("running and healthy states require severity=none")
    if state["status"] in {"running", "healthy"} and state["error_code"] is not None:
        raise HealthStateError("running and healthy states require error_code=null")
    if state["status"] == "failed" and state["severity"] not in {"P0", "P1"}:
        raise HealthStateError("failed state requires P0 or P1")
    if state["status"] == "degraded" and state["severity"] not in {"P2", "P3"}:
        raise HealthStateError("degraded state requires P2 or P3")
    if len(state["summary"]) > MAX_SUMMARY_CHARS:
        raise HealthStateError("summary exceeds maximum length")
    if not isinstance(state["run"], dict) or not isinstance(state["freshness"], dict):
        raise HealthStateError("run and freshness must be objects")
    if not isinstance(state["metrics"], dict) or not isinstance(state["failure_state"], dict):
        raise HealthStateError("metrics and failure_state must be objects")

    def scan(value: object) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                lowered = key.lower()
                if any(part in lowered for part in FORBIDDEN_KEY_PARTS):
                    raise HealthStateError(f"prohibited field: {key}")
                scan(nested)
        elif isinstance(value, list):
            for nested in value:
                scan(nested)

    scan(state)
    if len(compact_json(state)) > MAX_FILE_BYTES:
        raise HealthStateError(f"health state exceeds {MAX_FILE_BYTES} bytes")


def build_running(previous: dict[str, Any], *, now: str, source_sha: str | None = None) -> dict[str, Any]:
    state = copy.deepcopy(previous)
    state.update({
        "schema_version": SCHEMA_VERSION,
        "status": "running",
        "severity": "none",
        "stage": "starting",
        "error_code": None,
        "summary": "Trihourly sync is running",
        "run": run_metadata(now, source_sha=source_sha),
    })
    validate_state(state)
    return state


def _same_incident(previous: dict[str, Any], error_code: str) -> bool:
    failure = previous.get("failure_state", {})
    remains_open = bool(
        failure.get("consecutive_failures") or failure.get("consecutive_degraded_runs")
    )
    return remains_open and failure.get("last_error_code") == error_code


def build_terminal(
    previous: dict[str, Any],
    *,
    now: str,
    status: str,
    severity: str,
    stage: str,
    error_code: str | None,
    summary: str,
    source_sha: str | None = None,
    published_sha: str | None = None,
    data_cutoff_et: str | None = None,
    metrics: dict[str, int | None] | None = None,
) -> dict[str, Any]:
    if status not in TERMINAL_STATUSES:
        raise HealthStateError(f"invalid terminal status: {status}")
    state = copy.deepcopy(previous)
    prior_run = previous.get("run", {})
    current_run = run_metadata(now, source_sha=source_sha)
    if prior_run.get("id") == current_run.get("id") and prior_run.get("started_at"):
        current_run["started_at"] = prior_run["started_at"]
    current_run["observed_at"] = now
    state.update({
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "severity": severity,
        "stage": stage,
        "error_code": error_code,
        "summary": clean_summary(summary),
        "run": current_run,
    })
    failure = state["failure_state"]
    if status == "healthy":
        failure["consecutive_failures"] = 0
        failure["consecutive_degraded_runs"] = 0
    else:
        if not error_code:
            raise HealthStateError("non-healthy terminal state requires error_code")
        incident_id = (
            failure.get("incident_id")
            if _same_incident(previous, error_code) and failure.get("incident_id")
            else f"{current_run.get('id') or 'unknown'}:{error_code}"
        )
        failure.update({
            "incident_id": incident_id,
            "last_error_at": now,
            "last_error_code": error_code,
            "last_error_severity": severity,
            "last_error_run_url": current_run.get("url"),
        })
        if status == "failed":
            failure["consecutive_failures"] = int(failure.get("consecutive_failures") or 0) + 1
            failure["consecutive_degraded_runs"] = 0
        else:
            failure["consecutive_failures"] = 0
            failure["consecutive_degraded_runs"] = int(failure.get("consecutive_degraded_runs") or 0) + 1
        if severity in {"P0", "P1"}:
            failure.update({
                "last_actionable_incident_id": incident_id,
                "last_actionable_error_at": now,
                "last_actionable_error_code": error_code,
                "last_actionable_error_severity": severity,
                "last_actionable_error_run_url": current_run.get("url"),
            })

    if status in {"healthy", "degraded"}:
        state["freshness"].update({
            "last_success_at": now,
            "data_cutoff_et": data_cutoff_et,
            "published_sha": published_sha,
        })
        if metrics is not None:
            state["metrics"] = metrics
    validate_state(state)
    return state


def valid_avatar(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("data:image/") or ";base64," not in value:
        return False
    try:
        return bool(base64.b64decode(value.split(",", 1)[1], validate=True))
    except (ValueError, binascii.Error):
        return False


def read_metrics(manifest_path: Path, avatar_cache_path: Path) -> tuple[str | None, dict[str, int | None]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    avatars = json.loads(avatar_cache_path.read_text(encoding="utf-8"))
    date_range = manifest.get("date_range") or []
    cutoff = date_range[-1] if date_range else None
    bloggers = manifest.get("mentions_by_blogger")
    return cutoff, {
        "accounts_complete": len(bloggers) if isinstance(bloggers, dict) else None,
        "tickers": manifest.get("tickers"),
        "mentions": manifest.get("total_mentions"),
        "priced_tickers": manifest.get("priced_tickers"),
        "valid_avatars": sum(valid_avatar(value) for value in avatars.values()) if isinstance(avatars, dict) else None,
    }


def classify_outcome(
    outcomes: dict[str, str],
    *,
    avatar_report: dict[str, Any] | None,
    publish_error_code: str | None = None,
) -> dict[str, str]:
    for stage, variable, severity, code, summary in OUTCOME_CHECKS:
        if outcomes.get(variable) in {"failure", "cancelled"}:
            if stage == "publish" and publish_error_code == "PUBLISH_CONFLICT":
                code = "PUBLISH_CONFLICT"
                summary = "Main changed while the generated snapshot was building"
            return {
                "status": "failed",
                "severity": severity,
                "stage": stage,
                "error_code": code,
                "summary": summary,
            }
    if avatar_report is None:
        return {
            "status": "failed",
            "severity": "P1",
            "stage": "health-collection",
            "error_code": "UNKNOWN_PIPELINE_ERROR",
            "summary": "Avatar health result is missing after a successful avatar step",
        }
    stale_cache = avatar_report.get("stale_cache") or []
    if stale_cache:
        return {
            "status": "degraded",
            "severity": "P2",
            "stage": "avatars",
            "error_code": "AVATAR_REFRESH_DEGRADED",
            "summary": f"Avatar refresh failed for {len(stale_cache)} account(s); valid cache retained",
        }
    if outcomes.get("DASHBOARD_ARTIFACT_OUTCOME") == "failure":
        return {
            "status": "degraded",
            "severity": "P2",
            "stage": "dashboard-artifact",
            "error_code": "UNKNOWN_PIPELINE_ERROR",
            "summary": "Validated dashboard artifact upload failed after data publication",
        }
    return {
        "status": "healthy",
        "severity": "none",
        "stage": "complete",
        "error_code": None,
        "summary": "Sync and publication completed",
    }


class GitHubHealthStore:
    def __init__(self, repository: str, token: str, *, branch: str = DEFAULT_BRANCH, path: str = DEFAULT_PATH):
        self.repository = repository
        self.token = token
        self.branch = branch
        self.path = path
        api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
        self.base_url = f"{api_url}/repos/{repository}"

    def _request(self, method: str, endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = compact_json(payload) if payload is not None else None
        request = Request(
            f"{self.base_url}/{endpoint}",
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "sync-health-reporter/1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read(1000).decode("utf-8", "replace")
            raise GitHubApiError(exc.code, detail) from exc

    def read(self) -> tuple[dict[str, Any], str]:
        endpoint = f"contents/{quote(self.path)}?ref={quote(self.branch)}"
        response = self._request("GET", endpoint)
        state = json.loads(base64.b64decode(response["content"]).decode("utf-8"))
        validate_state(state)
        return state, response["sha"]

    def update(self, build: Callable[[dict[str, Any]], dict[str, Any]], message: str) -> dict[str, Any]:
        for attempt in range(2):
            previous, blob_sha = self.read()
            state = build(previous)
            payload = {
                "message": message,
                "content": base64.b64encode(compact_json(state)).decode("ascii"),
                "branch": self.branch,
                "sha": blob_sha,
            }
            try:
                self._request("PUT", f"contents/{quote(self.path)}", payload)
                return state
            except GitHubApiError as exc:
                if attempt == 0 and exc.status in {409, 422}:
                    continue
                raise
        raise AssertionError("unreachable")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    result.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    result.add_argument("--branch", default=DEFAULT_BRANCH)
    result.add_argument("--path", default=DEFAULT_PATH)
    subparsers = result.add_subparsers(dest="command", required=True)
    start = subparsers.add_parser("start")
    start.add_argument("--source-sha")
    finish = subparsers.add_parser("finish")
    finish.add_argument("--status", choices=sorted(TERMINAL_STATUSES), required=True)
    finish.add_argument("--severity", choices=sorted(SEVERITIES), required=True)
    finish.add_argument("--stage", required=True)
    finish.add_argument("--error-code")
    finish.add_argument("--summary", required=True)
    finish.add_argument("--source-sha")
    finish.add_argument("--published-sha")
    finish.add_argument("--manifest", type=Path)
    finish.add_argument("--avatar-cache", type=Path)
    classify = subparsers.add_parser("classify")
    classify.add_argument("--avatar-status", type=Path, required=True)
    classify.add_argument("--publish-error-code", type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    if args.command == "classify":
        avatar_report = None
        if args.avatar_status.exists():
            try:
                avatar_report = json.loads(args.avatar_status.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        publish_error_code = None
        if args.publish_error_code and args.publish_error_code.exists():
            publish_error_code = args.publish_error_code.read_text(encoding="utf-8").strip()
        result = classify_outcome(dict(os.environ), avatar_report=avatar_report, publish_error_code=publish_error_code)
        output_path = os.environ.get("GITHUB_OUTPUT")
        if output_path:
            with open(output_path, "a", encoding="utf-8") as output:
                for key, value in result.items():
                    output.write(f"{key}={value}\n")
        print(json.dumps(result, separators=(",", ":")))
        return 0
    if not args.repository or not args.token:
        print("::warning::Sync health reporter requires GITHUB_REPOSITORY and GITHUB_TOKEN.")
        return 2
    store = GitHubHealthStore(args.repository, args.token, branch=args.branch, path=args.path)
    now = utc_now()
    try:
        if args.command == "start":
            state = store.update(
                lambda previous: build_running(previous, now=now, source_sha=args.source_sha),
                "Record sync start",
            )
        else:
            cutoff = None
            metrics = None
            if args.status in {"healthy", "degraded"}:
                if not args.manifest or not args.avatar_cache:
                    raise HealthStateError("successful states require --manifest and --avatar-cache")
                cutoff, metrics = read_metrics(args.manifest, args.avatar_cache)
            state = store.update(
                lambda previous: build_terminal(
                    previous,
                    now=now,
                    status=args.status,
                    severity=args.severity,
                    stage=args.stage,
                    error_code=args.error_code,
                    summary=args.summary,
                    source_sha=args.source_sha,
                    published_sha=args.published_sha,
                    data_cutoff_et=cutoff,
                    metrics=metrics,
                ),
                f"Record sync {args.status}",
            )
        print(json.dumps({
            "status": state["status"],
            "severity": state["severity"],
            "error_code": state["error_code"],
            "incident_id": state["failure_state"]["incident_id"],
        }, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(f"::warning::Unable to update sync health state: {type(exc).__name__}: {clean_summary(str(exc))}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
