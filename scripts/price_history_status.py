"""Pure status-resolution rules for 52-week price coverage verification."""
from __future__ import annotations


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
