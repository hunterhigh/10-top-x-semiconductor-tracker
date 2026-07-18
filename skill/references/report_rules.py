"""Deterministic aggregation rules shared by the dashboard renderer.

This module never reads post text and never infers direction. Callers must pass
normalized, traceable account directions produced by the data pipeline.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping


MIN_SHARED_DIRECTION_ACCOUNTS = 2
MIN_NEW_MULTI_BULL_ACCOUNTS = 3
MIN_DIRECTIONAL_ACCOUNTS_FOR_REVERSAL = 2


def _account_set(values: Iterable[str]) -> set[str]:
    return {str(value) for value in values if value}


def classify_main_section(
    bullish_accounts: Iterable[str],
    bearish_accounts: Iterable[str],
    mentioned_accounts: Iterable[str],
    unique_posts_by_account: Mapping[str, int],
) -> dict[str, object] | None:
    """Assign one mutually exclusive daily/weekly main section."""
    bulls = _account_set(bullish_accounts)
    bears = _account_set(bearish_accounts)
    # Kept in the signature for renderer compatibility; neither field creates
    # a primary section after removal of the legacy other-notable bucket.
    del mentioned_accounts, unique_posts_by_account

    if bulls and bears:
        return {"key": "disagreement", "label": "存在多空分歧", "display_mode": "both"}
    if len(bulls) >= MIN_SHARED_DIRECTION_ACCOUNTS:
        return {"key": "shared_bullish", "label": "明确共同看多", "display_mode": "bull"}
    if len(bears) >= MIN_SHARED_DIRECTION_ACCOUNTS:
        return {"key": "shared_bearish", "label": "明确共同看空", "display_mode": "bear"}
    return None


def _primary_direction(bulls: set[str], bears: set[str]) -> str | None:
    if len(bulls) > len(bears):
        return "bull"
    if len(bears) > len(bulls):
        return "bear"
    return None


def classify_weekly_change(
    previous_bullish: Iterable[str],
    current_bullish: Iterable[str],
    previous_bearish: Iterable[str],
    current_bearish: Iterable[str],
) -> dict[str, object] | None:
    """Classify a stock into exactly one rolling-seven-day change list.

    Priority: reversal/new disagreement, newly formed multi-bullish consensus,
    then single-direction strengthening or weakening.
    """
    prev_bull, bull = _account_set(previous_bullish), _account_set(current_bullish)
    prev_bear, bear = _account_set(previous_bearish), _account_set(current_bearish)
    prev_primary = _primary_direction(prev_bull, prev_bear)
    current_primary = _primary_direction(bull, bear)
    enough_for_reversal = (
        len(prev_bull | prev_bear) >= MIN_DIRECTIONAL_ACCOUNTS_FOR_REVERSAL
        and len(bull | bear) >= MIN_DIRECTIONAL_ACCOUNTS_FOR_REVERSAL
    )

    if enough_for_reversal and prev_primary and current_primary and prev_primary != current_primary:
        return {
            "group": "reversal_or_disagreement",
            "label": "主方向反转",
            "display_mode": "both",
            "focus_direction": None,
        }
    if bull and bear and not (prev_bull and prev_bear):
        return {
            "group": "reversal_or_disagreement",
            "label": "新出现分歧",
            "display_mode": "both",
            "focus_direction": None,
        }
    if len(prev_bull) < MIN_NEW_MULTI_BULL_ACCOUNTS <= len(bull):
        return {
            "group": "new_multi_bullish",
            "label": "新形成多人看多",
            "display_mode": "bull_with_current_bear_warning",
            "focus_direction": "bull",
        }

    changes = []
    for direction, previous, current in (("bull", prev_bull, bull), ("bear", prev_bear, bear)):
        delta = len(current) - len(previous)
        if delta and max(len(previous), len(current)) >= MIN_SHARED_DIRECTION_ACCOUNTS:
            changes.append((abs(delta), max(len(previous), len(current)), len(current), direction == "bull", direction, delta))
    if not changes:
        return None
    _, _, _, _, focus, delta = max(changes)
    word = "看多" if focus == "bull" else "看空"
    label = f"{word}共识增强" if delta > 0 else f"{word}信号人数减少"
    return {
        "group": "consensus_strength",
        "label": label,
        "display_mode": "single_direction",
        "focus_direction": focus,
    }


def account_change_states(previous: Iterable[str], current: Iterable[str]) -> list[dict[str, str]]:
    """Return stable visual states: retained, added, removed."""
    previous_set, current_set = _account_set(previous), _account_set(current)
    ordered = sorted(current_set, key=str.lower) + sorted(previous_set - current_set, key=str.lower)
    return [
        {
            "account": account,
            "state": "added" if account not in previous_set else "removed" if account not in current_set else "retained",
        }
        for account in ordered
    ]


def monthly_consensus_label(bullish_signals: int, bearish_signals: int) -> str:
    """Create the approved 28-day table tag from effective directional signals."""
    bull, bear = max(0, int(bullish_signals or 0)), max(0, int(bearish_signals or 0))
    directional = bull + bear
    if not directional:
        return "无明确方向"
    share = bull / directional
    if share >= 0.60:
        return "偏多"
    if share <= 0.40:
        return "偏空"
    return "多空分歧"


def person_stance_consistency(bullish_records: int, bearish_records: int) -> dict[str, int | str | None]:
    """Return deterministic consistency percentage and the approved display label."""
    bull = max(0, int(bullish_records or 0))
    bear = max(0, int(bearish_records or 0))
    directional = bull + bear
    if not directional:
        return {"percentage": None, "label": "无方向信号"}
    percentage = round(max(bull, bear) / directional * 100)
    if percentage >= 80:
        label = "稳定"
    elif percentage >= 60:
        label = "较稳定"
    else:
        label = "多空反复"
    return {"percentage": percentage, "label": label}


def person_window_statistics(records: Iterable[Mapping[str, object]]) -> dict[str, object]:
    """Aggregate one person's stock records for the currently selected window."""
    normalized = []
    latest_key: tuple[str, int] | None = None
    latest_direction: str | None = None
    for index, record in enumerate(records):
        direction = str(record.get("direction") or record.get("stance") or "").strip().lower()
        if direction in {"bull", "bullish"}:
            normalized_direction = "bullish"
        elif direction in {"bear", "bearish"}:
            normalized_direction = "bearish"
        else:
            normalized_direction = "neutral"
        normalized.append(normalized_direction)
        key = (str(record.get("created_at") or record.get("date") or ""), index)
        if latest_key is None or key > latest_key:
            latest_key, latest_direction = key, normalized_direction
    bull = normalized.count("bullish")
    bear = normalized.count("bearish")
    neutral = normalized.count("neutral")
    return {
        "mention_count": len(normalized),
        "bullish_count": bull,
        "neutral_count": neutral,
        "bearish_count": bear,
        "state": person_window_state(bool(bull), bool(bear), bool(neutral), bool(normalized)),
        "latest_direction": latest_direction,
        "consistency": person_stance_consistency(bull, bear),
    }


def person_window_state(has_bull: bool, has_bear: bool, has_no_direction: bool, has_mentions: bool) -> str:
    """Assign one mutually exclusive person stance state inside a window."""
    if has_bull and has_bear:
        return "both"
    if has_bull:
        return "bull_only"
    if has_bear:
        return "bear_only"
    if has_mentions or has_no_direction:
        return "no_direction"
    return "not_mentioned"


def tracked_person_stock_lists(records: Iterable[Mapping[str, object]]) -> dict[str, list[str]]:
    """Build deduplicated bullish, bearish and neutral stock lists for a person card."""
    grouped: dict[str, set[str]] = {}
    for record in records:
        stock = str(record.get("stock") or record.get("ticker") or "").strip()
        direction = str(record.get("direction") or "").strip()
        if stock:
            grouped.setdefault(stock, set()).add(direction)
    bullish, bearish, neutral = [], [], []
    for stock, directions in grouped.items():
        if directions & {"bull", "bullish"}:
            bullish.append(stock)
        if directions & {"bear", "bearish"}:
            bearish.append(stock)
        if not directions & {"bull", "bullish", "bear", "bearish"}:
            neutral.append(stock)
    return {"bullish": sorted(bullish), "bearish": sorted(bearish), "neutral": sorted(neutral)}
