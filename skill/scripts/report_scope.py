"""Shared deterministic scope rules for the rolling 28-day report.

The UI, price backfill, and verification gate must all select the same stock
documents.  A stock enters the monthly consensus report only after at least
three distinct opinion accounts publish an explicit bullish or bearish stance
inside the ET closed interval ``[D-27, D]``.

Flow, news, disclosure, background, and neutral records remain available as
source-linked context, but they never make a stock eligible for consensus or
52-week price-history maintenance.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable


REPORT_WINDOW_DAYS = 28
MIN_MONTHLY_DIRECTIONAL_ACCOUNTS = 3
DIRECTIONAL_STANCES = frozenset({"bullish", "bearish"})


def report_window(asof: date) -> tuple[str, str]:
    """Return the inclusive 28-day ET window as ISO dates."""
    return (
        (asof - timedelta(days=REPORT_WINDOW_DAYS - 1)).isoformat(),
        asof.isoformat(),
    )


def explicit_directional_opinion_rows(
    rows: Iterable[dict[str, Any]],
    opinion_account_ids: set[str],
    asof: date,
) -> list[dict[str, Any]]:
    """Return only report-eligible opinion records in the rolling window."""
    start, end = report_window(asof)
    return [
        row
        for row in rows
        if start <= str(row.get("date") or "")[:10] <= end
        and row.get("blogger_id") in opinion_account_ids
        and row.get("mention_type") == "explicit_stance"
        and row.get("stance") in DIRECTIONAL_STANCES
    ]


def monthly_directional_accounts(
    rows: Iterable[dict[str, Any]],
    opinion_account_ids: set[str],
    asof: date,
) -> set[str]:
    """Return distinct opinion accounts that supplied a directional signal."""
    return {
        str(row["blogger_id"])
        for row in explicit_directional_opinion_rows(rows, opinion_account_ids, asof)
    }


def is_monthly_report_instrument(
    rows: Iterable[dict[str, Any]],
    opinion_account_ids: set[str],
    asof: date,
) -> bool:
    """Whether one stock belongs in the monthly report and 52-week scope."""
    return len(monthly_directional_accounts(rows, opinion_account_ids, asof)) >= MIN_MONTHLY_DIRECTIONAL_ACCOUNTS
