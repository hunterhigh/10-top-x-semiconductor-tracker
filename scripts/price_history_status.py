"""Pure status-resolution rules for 52-week price coverage verification."""
from __future__ import annotations

from datetime import datetime


KNOWN_COVERAGE_STATUSES = {
    "ok",
    "insufficient_history",
    "pending",
    "error",
    "unavailable",
    "unverified_symbol",
}


def effective_history_status(
    verification_status: str | None,
    price_status: str | None,
    coverage_status: str | None,
) -> str:
    """Resolve coverage without masking a real fetch error as unavailable."""
    if verification_status != "verified":
        return "unverified_symbol"
    if coverage_status in KNOWN_COVERAGE_STATUSES:
        return str(coverage_status)
    if price_status in {"unavailable", "unverified_symbol", "error"}:
        return str(price_status)
    return "pending"


def valid_degraded_state(coverage_status: str, source_state: dict | None, reason: str | None) -> bool:
    """Allow an honest, scheduled per-symbol degradation without weakening global gates."""
    source_state = source_state if isinstance(source_state, dict) else {}
    source_status = source_state.get("status")
    if not reason:
        return False
    retry_at = source_state.get("next_retry_at")
    try:
        retry_scheduled = bool(retry_at) and bool(datetime.fromisoformat(str(retry_at)))
    except ValueError:
        retry_scheduled = False
    if coverage_status == "pending":
        return source_status == "deferred" and retry_scheduled
    if coverage_status == "error":
        return source_status == "retryable_error" and retry_scheduled
    return coverage_status in {"unavailable", "unverified_symbol", "insufficient_history"}
