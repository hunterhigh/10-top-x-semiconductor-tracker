"""Shared deterministic scope rules for the rolling 28-day report.

The payload builder, price backfill, and verification gate must select the
same listed-equity documents.  The July 22 renderer contract scores all ten
tracked accounts and adds one deterministic bullish favorite per account.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Iterable


REPORT_WINDOW_DAYS = 28
MIN_MONTHLY_DIRECTIONAL_ACCOUNTS = 3
DIRECTIONAL_STANCES = frozenset({"bullish", "bearish"})
EQUITY_TYPES = frozenset({
    "equity", "stock", "common stock", "common_stock", "ordinary share",
    "ordinary_share", "adr", "gdr", "reit", "preferred stock", "preferred_stock",
})
NON_EQUITY_NAME = re.compile(
    r"\b(?:ETF|ETN|INDEX|COMPOSITE|FUND|TRUST|TREASURY|BOND|FUTURES?|2X|3X)\b|"
    r"\b(?:ISHARES|SPDR|INVESCO|PROSHARES|DIREXION|VANECK|WISDOMTREE|GLOBAL X|ARK)\b",
    re.IGNORECASE,
)


def report_window(asof: date) -> tuple[str, str]:
    """Return the inclusive 28-day ET window as ISO dates."""
    return ((asof - timedelta(days=REPORT_WINDOW_DAYS - 1)).isoformat(), asof.isoformat())


def in_report_window(rows: Iterable[dict[str, Any]], asof: date) -> list[dict[str, Any]]:
    start, end = report_window(asof)
    return [row for row in rows if start <= str(row.get("date") or "")[:10] <= end]


def is_rankable_equity(doc: dict[str, Any]) -> bool:
    """Match the supplied payload script's listed-equity eligibility gate."""
    source = doc.get("instrument") or {}
    explicit_type = next((str(value).strip().lower() for value in (
        source.get("asset_type"), source.get("instrument_type"), source.get("security_type"),
        doc.get("asset_type"), doc.get("instrument_type"), doc.get("security_type"),
    ) if value), "")
    if explicit_type:
        return explicit_type in EQUITY_TYPES
    status = str(source.get("verification_status") or doc.get("verification_status") or "")
    price_symbol = source.get("price_symbol") or doc.get("price_symbol")
    instrument_id = str(source.get("instrument_id") or "")
    name = str(source.get("display_name") or doc.get("company") or "")
    return (
        status == "verified"
        and bool(price_symbol)
        and not instrument_id.startswith("UNVERIFIED:")
        and not NON_EQUITY_NAME.search(name)
    )


def unique_posts(rows: Iterable[dict[str, Any]]) -> int:
    return len({row.get("url") or row.get("tweet_id") for row in rows})


def explicit_directional_rows(
    rows: Iterable[dict[str, Any]],
    scored_account_ids: set[str],
    asof: date,
) -> list[dict[str, Any]]:
    """Return all ten-account explicit directional records in the 28-day window."""
    return [
        row for row in in_report_window(rows, asof)
        if row.get("blogger_id") in scored_account_ids
        and row.get("mention_type") == "explicit_stance"
        and row.get("stance") in DIRECTIONAL_STANCES
    ]


def monthly_directional_accounts(
    rows: Iterable[dict[str, Any]],
    scored_account_ids: set[str],
    asof: date,
) -> set[str]:
    return {str(row["blogger_id"]) for row in explicit_directional_rows(rows, scored_account_ids, asof)}


def is_monthly_report_instrument(
    rows: Iterable[dict[str, Any]],
    scored_account_ids: set[str],
    asof: date,
) -> bool:
    return len(monthly_directional_accounts(rows, scored_account_ids, asof)) >= MIN_MONTHLY_DIRECTIONAL_ACCOUNTS


def monthly_top_pick_candidates(
    docs: Iterable[dict[str, Any]],
    tracked_account_ids: Iterable[str],
    asof: date,
) -> dict[str, dict[str, Any]]:
    """Apply the supplied four-level favorite ranking for every tracked account."""
    eligible = [doc for doc in docs if is_rankable_equity(doc)]
    result: dict[str, dict[str, Any]] = {}
    for blogger_id in tracked_account_ids:
        candidates: list[tuple[int, int, str, str, dict[str, Any]]] = []
        for doc in eligible:
            mine = [
                row for row in in_report_window(doc.get("mentions") or [], asof)
                if row.get("blogger_id") == blogger_id
                and row.get("mention_type") == "explicit_stance"
            ]
            bullish = [row for row in mine if row.get("stance") == "bullish"]
            if not bullish:
                continue
            latest = max((str(row.get("created_at") or row.get("date") or "") for row in bullish), default="")
            source = doc.get("instrument") or {}
            display_code = str(source.get("display_code") or doc.get("ticker") or "")
            candidates.append((unique_posts(bullish), unique_posts(mine), latest, display_code, doc))
        candidates.sort(key=lambda item: item[3])
        candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        if not candidates:
            result[str(blogger_id)] = {
                "doc": None, "bullish_mention_count": 0, "mention_count": 0,
                "latest_bullish_at": None,
            }
            continue
        bullish_count, mention_count, latest, _, doc = candidates[0]
        result[str(blogger_id)] = {
            "doc": doc, "bullish_mention_count": bullish_count,
            "mention_count": mention_count, "latest_bullish_at": latest,
        }
    return result


def monthly_history_scope(
    docs: Iterable[dict[str, Any]],
    tracked_account_ids: Iterable[str],
    asof: date,
) -> dict[str, Any]:
    """Return the union of monthly rows and ten-account favorite instruments."""
    docs = list(docs)
    account_ids = {str(value) for value in tracked_account_ids}
    monthly_docs = [
        doc for doc in docs
        if is_rankable_equity(doc)
        and is_monthly_report_instrument(doc.get("mentions") or [], account_ids, asof)
    ]
    picks = monthly_top_pick_candidates(docs, tracked_account_ids, asof)
    pick_docs = [item["doc"] for item in picks.values() if item.get("doc")]

    def identity(doc: dict[str, Any]) -> str:
        source = doc.get("instrument") or {}
        return str(source.get("instrument_id") or doc.get("ticker") or "")

    union: dict[str, dict[str, Any]] = {}
    for doc in monthly_docs + pick_docs:
        union.setdefault(identity(doc), doc)
    monthly_ids = {identity(doc) for doc in monthly_docs}
    pick_ids = {identity(doc) for doc in pick_docs}
    return {
        "docs": list(union.values()),
        "monthly_instrument_ids": monthly_ids,
        "top_pick_instrument_ids": pick_ids,
        "overlap_instrument_ids": monthly_ids & pick_ids,
        "top_picks": picks,
    }
