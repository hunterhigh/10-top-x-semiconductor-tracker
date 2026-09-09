# Sync Health Observability Design

Date: 2026-09-09

## Objective

Provide a low-cost, reliable health signal for the trihourly production sync. A Codex scheduled task must be able to decide whether human investigation is required by reading one small JSON file, without scanning workflow logs, repository data, or source code during healthy operation.

## Context

The September 2026 avatar incident exposed two independent weaknesses:

1. A non-critical X avatar refresh failure stopped publication even though ten valid cached avatars existed.
2. The avatar code discarded exception details, and the pipeline exposed only a binary success/failure result. Determining the cause required manually comparing multiple workflow runs and timings.

The avatar publication issue is now mitigated. This design addresses the broader observability gap: explicit error classification, data-health metrics, failure persistence, stale-run detection, and a stable low-token monitoring interface.

## Design Decision

Maintain a single `sync_health.json` file on a dedicated orphan-style `ops-health` branch.

This is preferred over writing health state to `main` because the production workflow also commits generated data to `main`. Separating the writers prevents health reporting from conflicting with or blocking production publication. It is preferred over GitHub Issues and workflow artifacts because a monitor can read one fixed resource without listing, filtering, or downloading run data.

The file is a current-state snapshot, not an append-only log. Detailed evidence remains in the linked GitHub Actions run.

## Architecture

The system has three bounded components.

### Production pipeline

The existing `trihourly-sync` workflow remains responsible for account fetching, extraction, database construction, validation, price and avatar enrichment, dashboard validation, and publication. Critical steps receive stable step identifiers so their outcomes can be summarized without parsing human-readable logs.

### Health reporter

A focused script, `scripts/write_sync_health.py`, builds, validates, and publishes health state. It does not fetch business data or alter production outputs.

When a workflow obtains the existing production concurrency lock, the reporter writes a `running` state. At the end of the workflow, an `if: always()` step evaluates job and step outcomes, reads metrics from the generated manifest when available, and writes the terminal `healthy`, `degraded`, or `failed` state.

The reporter reads the previous health snapshot before each update. This preserves the last successful publication and most recent incident across transitions.

### Codex monitor

The scheduled Codex task reads only `sync_health.json` from `ops-health`. Healthy or acceptable degraded states end silently. Actionable states generate a short notification containing the severity, stable error code, stage, time, and GitHub Actions URL. Full diagnosis begins only when the user opens a task manually.

## Repository Layout

Production branch additions:

```text
scripts/write_sync_health.py
tests/test_write_sync_health.py
docs/superpowers/specs/2026-09-09-sync-health-observability-design.md
.github/workflows/trihourly-sync.yml
```

Health branch contents:

```text
sync_health.json
```

The health branch contains no production data and does not trigger the production or smoke-test workflows.

## State Schema

The canonical file has this logical structure:

```json
{
  "schema_version": 1,
  "status": "healthy",
  "severity": "none",
  "stage": "complete",
  "error_code": null,
  "summary": "Sync and publication completed",
  "run": {
    "id": 34305745690,
    "url": "https://github.com/hunterhigh/10-top-x-semiconductor-tracker/actions/runs/34305745690",
    "event": "repository_dispatch",
    "started_at": "2026-09-09T03:05:17Z",
    "observed_at": "2026-09-09T03:33:18Z"
  },
  "freshness": {
    "last_success_at": "2026-09-09T03:33:18Z",
    "data_cutoff_et": "2026-09-08",
    "published_sha": "c6b93ac0a438d09a4b45bc3bcae70695ac7d36f3"
  },
  "metrics": {
    "accounts_complete": 10,
    "tickers": 2248,
    "mentions": 27187,
    "priced_tickers": 726,
    "valid_avatars": 10
  },
  "failure_state": {
    "consecutive_failures": 0,
    "consecutive_degraded_runs": 1,
    "incident_id": "34305745690:AVATAR_REFRESH_DEGRADED",
    "last_error_at": "2026-09-09T03:32:03Z",
    "last_error_code": "AVATAR_REFRESH_DEGRADED",
    "last_error_severity": "P2",
    "last_error_run_url": "https://github.com/hunterhigh/10-top-x-semiconductor-tracker/actions/runs/34305745690",
    "last_actionable_incident_id": null,
    "last_actionable_error_at": null,
    "last_actionable_error_code": null,
    "last_actionable_error_severity": null,
    "last_actionable_error_run_url": null
  }
}
```

All timestamps use UTC ISO 8601 with a trailing `Z`. `data_cutoff_et` remains an ET calendar date because the production dataset is defined that way. Unknown metrics are represented as `null`, never invented as zero.

The serialized file must remain below 4 KiB, with a target below 2 KiB. Error summaries are capped at 240 characters. Secrets, request headers, response bodies, stack traces, and user content are prohibited.

## State Transitions

### Start

After the workflow owns the production concurrency slot, write `status=running`, `severity=none`, and the current run identity. Preserve prior `freshness`, `metrics`, and `failure_state` values until terminal reporting.

### Healthy completion

Set `status=healthy`, `severity=none`, and `stage=complete`. Update `last_success_at`, data cutoff, published commit, and metrics. Reset `consecutive_failures` and `consecutive_degraded_runs` to zero. Preserve both the most recent error and the most recent actionable P0/P1 incident.

### Degraded completion

Use `status=degraded` when all core data and publication gates pass but a permitted non-core dependency degrades. Publication continues. Update successful freshness and metrics, reset `consecutive_failures`, increment `consecutive_degraded_runs`, and update the latest error without replacing a retained P0/P1 actionable incident.

### Failed completion

Use `status=failed` when a core gate or publication fails. Preserve the last known successful freshness and production metrics, increment `consecutive_failures`, reset `consecutive_degraded_runs`, and update both the latest error and actionable-incident fields.

### Interrupted or absent run

The reporter cannot guarantee a terminal write after runner termination, workflow cancellation, or a platform-wide outage. A `running` state older than 60 minutes is therefore interpreted by the monitor as `WORKFLOW_STUCK/P1`. A `last_success_at` older than six hours is interpreted as `SYNC_MISSED/P1`, even if no new run ever wrote a failure.

Data whose ET cutoff is more than two calendar days behind the current date is interpreted as `DATA_STALE/P1`. Weekend and market-holiday exemptions are intentionally excluded from the first version; the two-day tolerance is the approved simple rule.

## Severity Model

| Severity | Meaning | Publication behavior | Monitor behavior |
| --- | --- | --- | --- |
| P0 | Data integrity risk or incorrect publication | Stop publication | Notify immediately |
| P1 | Core sync unavailable or stale | Stop publication | Notify immediately |
| P2 | Non-core degradation with safe fallback | Continue publication | Notify after three consecutive degraded runs |
| P3 | Transient issue recovered automatically | Continue publication | Record only |
| none | Healthy | Continue publication | Silent |

Core failure always outranks non-core degradation. The terminal report selects the highest-severity observed condition, so an avatar warning cannot hide a database validation failure.

## Stable Error Codes

The first version supports these codes:

- `FETCH_ACCOUNT_FAILED`
- `EXTRACTION_FAILED`
- `ACCOUNT_ARTIFACT_MISSING`
- `DATABASE_BUILD_FAILED`
- `DATA_VALIDATION_FAILED`
- `PRICE_REFRESH_DEGRADED`
- `AVATAR_REFRESH_DEGRADED`
- `AVATAR_CACHE_MISSING`
- `DASHBOARD_VALIDATION_FAILED`
- `PUBLISH_CONFLICT`
- `PUBLISH_FAILED`
- `WORKFLOW_STUCK`
- `SYNC_MISSED`
- `DATA_STALE`
- `UNKNOWN_PIPELINE_ERROR`

Error codes are an external interface consumed by the Codex monitor. Renaming or changing their meaning requires a schema-version change or backwards-compatible monitor update.

## Workflow Integration

Critical workflow steps receive IDs for account gate, artifact validation, database build, enrichment, rules tests, dashboard validation, metadata generation, and publication. The terminal health step receives their `outcome` values through environment variables and does not scrape console text.

The reporter derives the first failed core stage using a fixed precedence table. Permitted degradation details, such as avatar HTTP failures and cached-avatar count, are passed as a compact JSON result written by the relevant script. Manifest metrics are read only after schema validation.

The `resolve-base` job requires narrowly scoped `contents: write` permission to create the initial state. The `publish` job already has content-write permission and writes the terminal state. The reporter updates only `refs/heads/ops-health`.

Each health update:

1. Reads the current branch ref and `sync_health.json` blob.
2. Merges the new event with retained state.
3. Validates the schema and size locally.
4. Creates a new blob, tree, and commit on `ops-health`.
5. Advances the branch with a non-force compare-and-swap update.
6. On ref conflict, rereads current state and retries once.

The existing workflow-level concurrency group makes conflicting production health writers unlikely. The compare-and-swap behavior protects manual and scheduled overlap without overwriting a newer result.

Health-write failure does not roll back or corrupt production data. It produces a visible workflow warning. If reporting remains broken, the Codex monitor detects the old `observed_at` or `last_success_at` after the approved timeout.

## Codex Monitor Contract

The scheduled task retrieves the raw `sync_health.json` from `ops-health`, parses JSON, validates `schema_version=1`, and applies these rules in order:

1. Missing, invalid, or unsupported file: notify as monitor-contract failure.
2. `status=running` for more than 60 minutes: notify `WORKFLOW_STUCK/P1`.
3. `last_success_at` older than six hours: notify `SYNC_MISSED/P1`.
4. Data cutoff more than two calendar days old: notify `DATA_STALE/P1`.
5. Current `status=failed`: notify the recorded P0/P1 condition.
6. A P0/P1 `last_actionable_error_at` within the previous 24 hours: notify even if the current run recovered.
7. `status=degraded` with `consecutive_degraded_runs >= 3`: notify the P2 condition.
8. Otherwise: finish silently.

A notification contains only status, severity, error code, stage, summary, timestamps, and run URL. The monitor does not inspect linked logs automatically.

Each incident receives the stable identifier `first_failed_run_id:error_code`. The Codex task must run as a persistent thread heartbeat and compare this identifier with incident IDs already reported in that thread. An already reported incident stays silent unless its severity increases. A standalone new-task-per-run schedule is outside the first version because it cannot deduplicate without another state store.

## Testing

Unit tests cover:

- Initial state creation without a prior health file.
- `running` state preserving last successful information.
- Healthy, degraded, and failed terminal transitions.
- Failure and degradation counter behavior.
- Preservation of the most recent actionable P0/P1 incident across later healthy and P2 runs.
- Stable incident identifiers across repeated observations of the same failure.
- Highest-severity condition selection.
- Missing and malformed manifest metrics producing `null` rather than zero.
- Error-summary truncation and prohibited-field rejection.
- Schema and file-size enforcement.
- Compare-and-swap conflict followed by one successful retry.
- Permanent health-write failure producing a warning without modifying production data.

Workflow-level tests cover:

- A normal run ending healthy.
- Ten avatar HTTP 403 responses with valid cache ending degraded while publishing data.
- A missing valid avatar cache ending failed.
- Account artifact failure reporting the account stage.
- Database and dashboard validation failures receiving the correct code and severity.
- Publication conflict preserving the prior successful freshness values.
- Simulated terminal-step omission leaving an old running state detectable by the monitor.

Acceptance requires two consecutive real repository-dispatch runs whose health snapshots agree with their GitHub Actions conclusions and published commits.

## Rollout

1. Add and test the health reporter and schema.
2. Initialize `ops-health` with a valid snapshot based on the latest successful production run.
3. Add start and terminal reporting to `trihourly-sync`.
4. Run smoke tests and one manual production sync.
5. Observe two consecutive repository-dispatch runs.
6. Create the Codex scheduled monitor against the stable raw-file path.

During rollout, the existing production gates remain authoritative. Health reporting is observational and must not weaken data validation or publication controls.

## Non-Goals

- Replacing GitHub Actions logs or building a full logging platform.
- Storing append-only incident history in the repository.
- Automatically launching deep Codex diagnosis.
- Automatically repairing failed business data.
- Adding Slack, email, or pager integrations in the first version.
- Implementing exchange-calendar-aware freshness in the first version.
