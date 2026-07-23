#!/usr/bin/env python3
"""Build the deterministic 10V Dashboard render payload.

This is the only place where dashboard aggregation is performed. It reads a
local ``data/db`` snapshot and never downloads data or infers stance from text.
The 10 tracked account ids and approved aggregation rules are embedded; person
identity fields come from database profile metadata and the avatar cache.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker

from report_scope import is_rankable_equity, monthly_top_pick_candidates
from stock_store import StockStore


SCRIPT_DIR = Path(__file__).resolve().parent
PACKAGE_DIR = SCRIPT_DIR.parent
REPOSITORY_DIR = SCRIPT_DIR.parents[1]
PROJECT_DIR = REPOSITORY_DIR if (REPOSITORY_DIR / "skill").is_dir() else PACKAGE_DIR
SCHEMA_PATH = PACKAGE_DIR / "references" / "dashboard-render-contract.schema.json"

LEGACY_ROSTER = [
    {"id": "aleabitoreddit", "handle": "@aleabitoreddit", "display_name": "Serenity", "x_url": "https://x.com/aleabitoreddit", "avatar_letter": "S", "color": "#1F5C4D", "signal_type": "opinion"},
    {"id": "zephyr_z9", "handle": "@zephyr_z9", "display_name": "Zephyr", "x_url": "https://x.com/zephyr_z9", "avatar_letter": "Z", "color": "#4E79A7", "signal_type": "opinion"},
    {"id": "jukan05", "handle": "@jukan05", "display_name": "Jukan", "x_url": "https://x.com/jukan05", "avatar_letter": "J", "color": "#F28E2B", "signal_type": "opinion"},
    {"id": "KawzInvests", "handle": "@KawzInvests", "display_name": "KawzInvests", "x_url": "https://x.com/KawzInvests", "avatar_letter": "K", "color": "#76B7B2", "signal_type": "opinion"},
    {"id": "michaelsikand", "handle": "@michaelsikand", "display_name": "Michael Sikand", "x_url": "https://x.com/michaelsikand", "avatar_letter": "M", "color": "#17BECF", "signal_type": "opinion"},
    {"id": "ren_stocks", "handle": "@ren_stocks", "display_name": "Ren", "x_url": "https://x.com/ren_stocks", "avatar_letter": "R", "color": "#FF9DA7", "signal_type": "opinion"},
    {"id": "octopusycc", "handle": "@octopusycc", "display_name": "大老师", "x_url": "https://x.com/octopusycc", "avatar_letter": "大", "color": "#9C755F", "signal_type": "opinion"},
    {"id": "unusual_whales", "handle": "@unusual_whales", "display_name": "Unusual Whales", "x_url": "https://x.com/unusual_whales", "avatar_letter": "U", "color": "#E15759", "signal_type": "flow"},
    {"id": "StockMKTNewz", "handle": "@StockMKTNewz", "display_name": "Evan", "x_url": "https://x.com/StockMKTNewz", "avatar_letter": "E", "color": "#B07AA1", "signal_type": "news"},
    {"id": "DJTRadar", "handle": "@DJTRadar", "display_name": "DJT Radar", "x_url": "https://x.com/DJTRadar", "avatar_letter": "D", "color": "#EDC948", "signal_type": "disclosure"},
]
TRACKED_ACCOUNT_IDS = tuple(item["id"] for item in LEGACY_ROSTER)


class _Rules:
    """Approved deterministic UI aggregation rules, embedded for portability."""

    @staticmethod
    def classify_main_section(bullish_accounts, bearish_accounts, mentioned_accounts, unique_posts_by_account):
        del mentioned_accounts, unique_posts_by_account
        bulls, bears = set(filter(None, bullish_accounts)), set(filter(None, bearish_accounts))
        if bulls and bears:
            return {"key": "disagreement", "label": "存在多空分歧", "display_mode": "both"}
        if len(bulls) >= 2:
            return {"key": "shared_bullish", "label": "明确共同看多", "display_mode": "bull"}
        if len(bears) >= 2:
            return {"key": "shared_bearish", "label": "明确共同看空", "display_mode": "bear"}
        return None

    @staticmethod
    def classify_weekly_change(previous_bullish, current_bullish, previous_bearish, current_bearish):
        pb, cb, pr, cr = map(set, (previous_bullish, current_bullish, previous_bearish, current_bearish))
        primary = lambda bull, bear: "bull" if len(bull) > len(bear) else "bear" if len(bear) > len(bull) else None
        before, now = primary(pb, pr), primary(cb, cr)
        if len(pb | pr) >= 2 and len(cb | cr) >= 2 and before and now and before != now:
            return {"group": "reversal_or_disagreement", "label": "主方向反转", "display_mode": "both", "focus_direction": None}
        if cb and cr and not (pb and pr):
            return {"group": "reversal_or_disagreement", "label": "新出现分歧", "display_mode": "both", "focus_direction": None}
        if len(pb) < 3 <= len(cb):
            return {"group": "new_multi_bullish", "label": "新形成多人看多", "display_mode": "bull_with_current_bear_warning", "focus_direction": "bull"}
        changes = []
        for direction, previous, current in (("bull", pb, cb), ("bear", pr, cr)):
            delta = len(current) - len(previous)
            if delta and max(len(previous), len(current)) >= 2:
                changes.append((abs(delta), max(len(previous), len(current)), len(current), direction == "bull", direction, delta))
        if not changes:
            return None
        *_, focus, delta = max(changes)
        word = "看多" if focus == "bull" else "看空"
        return {"group": "consensus_strength", "label": f"{word}共识增强" if delta > 0 else f"{word}信号人数减少", "display_mode": "single_direction", "focus_direction": focus}

    @staticmethod
    def account_change_states(previous, current):
        previous, current = set(previous), set(current)
        ordered = sorted(current, key=str.lower) + sorted(previous - current, key=str.lower)
        return [{"account": account, "state": "added" if account not in previous else "removed" if account not in current else "retained"} for account in ordered]

    @staticmethod
    def person_window_state(has_bull, has_bear, has_no_direction, has_mentions):
        if has_bull and has_bear: return "both"
        if has_bull: return "bull_only"
        if has_bear: return "bear_only"
        if has_mentions or has_no_direction: return "no_direction"
        return "not_mentioned"

    @staticmethod
    def person_window_statistics(records):
        normalized, latest_key, latest_direction = [], None, None
        for index, record in enumerate(records):
            raw = str(record.get("direction") or record.get("stance") or "").lower()
            direction = "bullish" if raw in {"bull", "bullish"} else "bearish" if raw in {"bear", "bearish"} else "neutral"
            normalized.append(direction)
            key = (str(record.get("created_at") or record.get("date") or ""), index)
            if latest_key is None or key > latest_key: latest_key, latest_direction = key, direction
        bull, bear, neutral = normalized.count("bullish"), normalized.count("bearish"), normalized.count("neutral")
        directional = bull + bear
        percentage = round(max(bull, bear) / directional * 100) if directional else None
        label = "无方向信号" if percentage is None else "稳定" if percentage >= 80 else "较稳定" if percentage >= 60 else "多空反复"
        return {"mention_count": len(normalized), "bullish_count": bull, "neutral_count": neutral, "bearish_count": bear, "state": _Rules.person_window_state(bool(bull), bool(bear), bool(neutral), bool(normalized)), "latest_direction": latest_direction, "consistency": {"percentage": percentage, "label": label}}

    @staticmethod
    def tracked_person_stock_lists(records):
        grouped = defaultdict(set)
        for record in records:
            stock = str(record.get("stock") or record.get("ticker") or "").strip()
            if stock: grouped[stock].add(str(record.get("direction") or "").strip())
        result = {"bullish": [], "bearish": [], "neutral": []}
        for stock, directions in grouped.items():
            if directions & {"bull", "bullish"}: result["bullish"].append(stock)
            if directions & {"bear", "bearish"}: result["bearish"].append(stock)
            if not directions & {"bull", "bullish", "bear", "bearish"}: result["neutral"].append(stock)
        return {key: sorted(value) for key, value in result.items()}


RULES = _Rules()


def load(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _profile_rows(document: Any) -> list[dict[str, Any]]:
    if isinstance(document, list):
        return [row for row in document if isinstance(row, dict)]
    if isinstance(document, dict):
        for key in ("accounts", "profiles", "people"):
            rows = document.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def load_roster(db: Path, avatar_cache: Path | None) -> list[dict[str, Any]]:
    """Merge fixed account ids with database-owned identity fields."""
    legacy = {item["id"]: dict(item) for item in LEGACY_ROSTER}
    profiles: dict[str, dict[str, Any]] = {}
    for path in (db / "blogger_profiles.json", db / "blogger_identities.json"):
        for row in _profile_rows(load(path, {})):
            blogger_id = str(row.get("blogger_id") or row.get("id") or "")
            if blogger_id in legacy:
                profiles[blogger_id] = {**profiles.get(blogger_id, {}), **row}

    cache = load(avatar_cache, {}) if avatar_cache else {}
    if not isinstance(cache, dict):
        cache = {}

    roster: list[dict[str, Any]] = []
    for blogger_id in TRACKED_ACCOUNT_IDS:
        fallback = legacy[blogger_id]
        source = dict(profiles.get(blogger_id, {}))
        cached = cache.get(blogger_id)
        if isinstance(cached, dict):
            source = {**source, **cached}
            cached_avatar = cached.get("avatar_data_uri") or cached.get("avatar")
        else:
            cached_avatar = cached

        display_name = display_text(source.get("display_name") or source.get("name")) or fallback["display_name"]
        handle = display_text(source.get("handle") or source.get("username")) or fallback["handle"]
        if not handle.startswith("@"):
            handle = f"@{handle}"
        x_url = display_text(source.get("x_url") or source.get("profile_url")) or f"https://x.com/{handle[1:]}"
        avatar = source.get("avatar_data_uri") or cached_avatar or fallback.get("avatar_data_uri")
        if not isinstance(avatar, str) or not avatar.startswith("data:image/"):
            avatar = svg_avatar(display_name, str(source.get("color") or fallback.get("color") or "#52616b"))
        roster.append({
            "id": blogger_id,
            "display_name": display_name,
            "handle": handle,
            "x_url": x_url,
            "signal_type": str(source.get("signal_type") or fallback.get("signal_type") or "opinion"),
            "profile_summary": display_text(source.get("profile_summary") or source.get("bio") or source.get("description")),
            "avatar_data_uri": avatar,
            "avatar_letter": fallback.get("avatar_letter", display_name[:1]),
            "color": str(source.get("color") or fallback.get("color") or "#52616b"),
        })
    return roster


def snapshot_id(manifest_path: Path, db: Path, avatar_cache: Path | None) -> str | None:
    paths = [
        manifest_path,
        db / "index.json",
        db / "blogger_profiles.json",
        db / "blogger_identities.json",
    ]
    if avatar_cache:
        paths.append(avatar_cache)
    existing = [path for path in paths if path.is_file()]
    if not existing:
        return None
    digest = hashlib.sha256()
    for path in existing:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def display_text(value: Any, language: str = "zh") -> str:
    """Profile copy is stored as localized maps; payload values are strings."""
    if isinstance(value, dict):
        return str(value.get(language) or value.get("zh") or value.get("en") or value.get("zh-Hant") or "")
    return str(value or "")


def iso_day(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def window(end: date, calendar_days: int) -> dict[str, Any]:
    return {"start": (end - timedelta(days=calendar_days - 1)).isoformat(), "end": end.isoformat(), "calendar_days": calendar_days}


def in_window(rows: Iterable[dict[str, Any]], item_window: dict[str, Any]) -> list[dict[str, Any]]:
    start, end = item_window["start"], item_window["end"]
    return [row for row in rows if start <= str(row.get("date", ""))[:10] <= end]


def normal_stance(value: Any) -> str:
    return value if value in {"bullish", "bearish", "neutral"} else "neutral"


def svg_avatar(label: str, color: str) -> str:
    """Visible offline fallback; real avatar data URIs take precedence."""
    safe = html.escape((label or "?")[:1].upper())
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="{html.escape(color or "#52616b")}"/><text x="32" y="42" text-anchor="middle" font-family="Arial" font-size="32" fill="white">{safe}</text></svg>'
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def evidence(row: dict[str, Any]) -> dict[str, Any]:
    created = row.get("created_at")
    if created:
        try:
            datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        except ValueError:
            created = None
    return {
        "tweet_id": str(row.get("tweet_id", "")), "blogger_id": str(row.get("blogger_id", "")),
        "date": str(row.get("date", ""))[:10], "created_at": created,
        "stance": normal_stance(row.get("stance")), "mention_type": str(row.get("mention_type", "")),
        "reasons": [str(x) for x in (row.get("reasons") or [])], "text": str(row.get("text") or ""),
        "url": str(row.get("url") or "https://x.com/i/web/status/" + str(row.get("tweet_id", ""))),
    }


def instrument(doc: dict[str, Any]) -> dict[str, Any]:
    source = doc.get("instrument") or {}
    symbol = str(source.get("display_code") or doc.get("ticker") or "UNKNOWN")
    status = source.get("verification_status") or doc.get("verification_status") or "unverified"
    if status not in {"verified", "identified", "unverified"}:
        status = "unverified"
    return {
        "instrument_id": str(source.get("instrument_id") or f"UNVERIFIED:{symbol}"),
        "display_code": symbol,
        "display_name": str(source.get("display_name") or doc.get("company") or "Name unverified"),
        "display_market": source.get("display_market") or source.get("market") or doc.get("exchange") or "Market unverified",
        "currency": source.get("currency") or doc.get("currency"),
        "price_symbol": source.get("price_symbol") or doc.get("price_symbol"),
        "verification_status": status,
    }


def is_consensus_equity(doc: dict[str, Any]) -> bool:
    """Compatibility alias for the shared listed-equity gate."""
    return is_rankable_equity(doc)


def price_change(doc: dict[str, Any], item_window: dict[str, Any]) -> dict[str, Any]:
    status = str(doc.get("price_status") or "pending")
    all_series = [p for p in (doc.get("price_series") or []) if str(p.get("date", ""))[:10] <= item_window["end"] and isinstance(p.get("close"), (int, float))]
    all_series.sort(key=lambda p: p["date"])
    # One-day reports still need two closes. Weekends and market holidays use
    # the latest two valid trading sessions available before the report date.
    if item_window["start"] == item_window["end"] and len(all_series) >= 2 and all_series[-2]["close"]:
        start_point, end_point = all_series[-2], all_series[-1]
        return {
            "status": "ok",
            "percentage": round((end_point["close"] / start_point["close"] - 1) * 100, 2),
            "start_date": start_point["date"],
            "end_date": end_point["date"],
            "basis": "latest_two_trading_days",
            "used_previous_trading_day": str(end_point["date"])[:10] < item_window["end"],
        }
    series = [p for p in all_series if item_window["start"] <= str(p.get("date", ""))[:10]]
    series.sort(key=lambda p: p["date"])
    if len(series) >= 2 and series[0]["close"]:
        return {"status": "ok", "percentage": round((series[-1]["close"] / series[0]["close"] - 1) * 100, 2), "start_date": series[0]["date"], "end_date": series[-1]["date"]}
    if status == "partial" and series:
        status = "pending"
    if status not in {"pending", "unavailable", "unverified_symbol", "error"}:
        status = "unavailable"
    return {"status": status, "percentage": None, "start_date": None, "end_date": None}


def price_change_52_weeks(doc: dict[str, Any], end: date) -> dict[str, Any]:
    """Return 52-week change, falling back to the full available stored range."""
    item_window = window(end, 365)
    change = price_change(doc, item_window)
    if change["status"] != "ok":
        return change
    target_start = iso_day(item_window["start"])
    actual_start = iso_day(change["start_date"])
    if (actual_start - target_start).days > 7:
        return {
            "status": "ok",
            "percentage": change["percentage"],
            "start_date": change["start_date"],
            "end_date": change["end_date"],
            "basis": "available_history_fallback",
            "history_status": "insufficient_history",
            "target_start_date": item_window["start"],
        }
    return change


def explicit_directional(rows: Iterable[dict[str, Any]], scored_accounts: set[str]) -> list[dict[str, Any]]:
    """Return directional records from every tracked account, including signal accounts."""
    return [r for r in rows if r.get("blogger_id") in scored_accounts and r.get("mention_type") == "explicit_stance"]


def directions(rows: Iterable[dict[str, Any]], scored_accounts: set[str]) -> tuple[set[str], set[str]]:
    scored = explicit_directional(rows, scored_accounts)
    return ({r["blogger_id"] for r in scored if normal_stance(r.get("stance")) == "bullish"}, {r["blogger_id"] for r in scored if normal_stance(r.get("stance")) == "bearish"})


def account_summaries(rows: list[dict[str, Any]], ids: set[str], stance: str) -> list[dict[str, Any]]:
    output = []
    for blogger_id in sorted(ids, key=str.lower):
        candidates = [r for r in rows if r.get("blogger_id") == blogger_id and normal_stance(r.get("stance")) == stance]
        newest = max(candidates, key=lambda r: (str(r.get("created_at") or r.get("date")), str(r.get("tweet_id"))), default={})
        output.append({
            "blogger_id": blogger_id,
            "stance": normal_stance(newest.get("stance")),
            "reasons": [str(x) for x in newest.get("reasons", [])],
            "text": str(newest.get("text") or ""),
            "evidence_url": str(newest.get("url") or "https://x.com"),
        })
    return output


def unique_posts(rows: Iterable[dict[str, Any]]) -> int:
    return len({r.get("url") or r.get("tweet_id") for r in rows})


def monthly_top_picks(
    docs: list[dict[str, Any]],
    roster: list[dict[str, Any]],
    item_window: dict[str, Any],
    end: date,
) -> list[dict[str, Any]]:
    """Choose one deterministic 28-day bullish favorite for every tracked account."""
    selected_by_account = monthly_top_pick_candidates(docs, [b["id"] for b in roster], end)
    picks = []
    for blogger in roster:
        blogger_id = blogger["id"]
        choice = selected_by_account[blogger_id]
        selected = choice.get("doc")
        if not selected:
            picks.append({"blogger_id": blogger_id, "instrument": None, "bullish_mention_count": 0,
                          "mention_count": 0, "latest_bullish_at": None, "price_change_52w": None})
            continue
        picks.append({
            "blogger_id": blogger_id,
            "instrument": instrument(selected),
            "bullish_mention_count": choice["bullish_mention_count"],
            "mention_count": choice["mention_count"],
            "latest_bullish_at": choice["latest_bullish_at"],
            "price_change_52w": price_change_52_weeks(selected, end),
        })
    return picks


def stock_card(doc: dict[str, Any], rows: list[dict[str, Any]], item_window: dict[str, Any], scored_accounts: set[str], classification: str) -> dict[str, Any]:
    bulls, bears = directions(rows, scored_accounts)
    neutral = {r["blogger_id"] for r in explicit_directional(rows, scored_accounts) if normal_stance(r.get("stance")) == "neutral"}
    return {"instrument": instrument(doc), "classification": classification, "price_change": price_change(doc, item_window),
            "bullish_accounts": account_summaries(rows, bulls, "bullish"), "bearish_accounts": account_summaries(rows, bears, "bearish"),
            "neutral_accounts": account_summaries(rows, neutral, "neutral"), "unique_post_count": unique_posts(rows)}


def main_report(docs: list[dict[str, Any]], item_window: dict[str, Any], opinions: set[str]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {"shared_bullish": [], "shared_bearish": [], "disagreement": []}
    for doc in docs:
        rows = in_window(doc.get("mentions") or [], item_window)
        bulls, bears = directions(rows, opinions)
        verdict = RULES.classify_main_section(bulls, bears, {r.get("blogger_id") for r in rows}, Counter(r.get("blogger_id") for r in rows))
        if verdict:
            groups[verdict["key"]].append(stock_card(doc, rows, item_window, opinions, verdict["key"]))
    for cards in groups.values():
        cards.sort(key=lambda c: (-max(len(c["bullish_accounts"]), len(c["bearish_accounts"])), -c["unique_post_count"], c["instrument"]["display_code"]))
    return {"window": item_window, **groups}


def account_changes(previous: set[str], current: set[str]) -> dict[str, Any]:
    states = RULES.account_change_states(previous, current)
    return {"previous": sorted(previous), "current": sorted(current), "states": [{"blogger_id": x["account"], "state": x["state"]} for x in states]}


def weekly_changes(docs: list[dict[str, Any]], current: dict[str, Any], previous: dict[str, Any], opinions: set[str]) -> dict[str, list[dict[str, Any]]]:
    groups = {"new_multi_bullish": [], "consensus_strength": [], "reversal_or_disagreement": []}
    for doc in docs:
        cur_rows, prev_rows = in_window(doc.get("mentions") or [], current), in_window(doc.get("mentions") or [], previous)
        cur_bull, cur_bear = directions(cur_rows, opinions)
        pre_bull, pre_bear = directions(prev_rows, opinions)
        change = RULES.classify_weekly_change(pre_bull, cur_bull, pre_bear, cur_bear)
        if not change:
            continue
        item = {"instrument": instrument(doc), "group": change["group"], "label": change["label"], "focus_direction": change["focus_direction"],
                "price_change": price_change(doc, current), "bullish": account_changes(pre_bull, cur_bull), "bearish": account_changes(pre_bear, cur_bear)}
        groups[change["group"]].append(item)
    for values in groups.values():
        values.sort(key=lambda x: x["instrument"]["display_code"])
    return groups


def person_stats(rows: list[dict[str, Any]], blogger_id: str) -> dict[str, Any]:
    mine = sorted([r for r in rows if r.get("blogger_id") == blogger_id], key=lambda r: (str(r.get("created_at") or r.get("date")), str(r.get("tweet_id"))))
    stats = RULES.person_window_statistics(mine)
    directional = [normal_stance(r.get("stance")) for r in mine]
    reversal = any(a != b for a, b in zip([x for x in directional if x != "neutral"], [x for x in directional if x != "neutral"][1:]))
    latest = evidence(mine[-1]) if mine else None
    return {"blogger_id": blogger_id, "mention_count": stats["mention_count"], "bullish_count": stats["bullish_count"], "neutral_count": stats["neutral_count"],
            "bearish_count": stats["bearish_count"], "consistency_percentage": stats["consistency"]["percentage"], "consistency_label": stats["consistency"]["label"],
            "latest_direction": stats["latest_direction"], "latest": latest, "has_reversal": reversal, "evidence": [evidence(r) for r in mine]}


def drilldown(doc: dict[str, Any], end: date, roster: list[dict[str, Any]]) -> dict[str, Any]:
    all_rows = doc.get("mentions") or []
    windows = {"today": window(end, 1), "days_7": window(end, 7), "days_28": window(end, 28)}
    people_by_window, person_windows, summaries = {}, {}, {}
    for key, item_window in windows.items():
        rows = in_window(all_rows, item_window)
        stats = [person_stats(rows, b["id"]) for b in roster]
        people_by_window[key] = stats
        person_windows[key] = [{"blogger_id": x["blogger_id"], "state": x["state"] if "state" in x else RULES.person_window_state(bool(x["bullish_count"]), bool(x["bearish_count"]), bool(x["neutral_count"]), bool(x["mention_count"]))} for x in stats]
        summaries[key] = {"window": item_window, "participant_count": len({r.get("blogger_id") for r in rows}), "mention_count": len(rows),
                          "bullish_count": sum(normal_stance(r.get("stance")) == "bullish" for r in rows), "bearish_count": sum(normal_stance(r.get("stance")) == "bearish" for r in rows),
                          "price_change": price_change(doc, item_window)}
    month_rows = in_window(all_rows, windows["days_28"])
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in month_rows: by_day[str(row.get("date"))[:10]].append(evidence(row))
    series = [{k: p.get(k) for k in ("date", "open", "high", "low", "close") if k in p} for p in (doc.get("price_series") or []) if windows["days_28"]["start"] <= str(p.get("date", ""))[:10] <= windows["days_28"]["end"] and isinstance(p.get("close"), (int, float))]
    status = "ok" if series else str(doc.get("price_status") or "pending")
    if status not in {"ok", "pending", "unavailable", "unverified_symbol", "error"}: status = "unavailable"
    return {"instrument": instrument(doc), "window": windows["days_28"], "default_person_window": "days_7", "price_status": status, "price_series": series,
            "mention_days": [{"date": k, "evidence": v} for k, v in sorted(by_day.items())], "window_summaries": summaries,
            "person_windows": person_windows, "people_by_window": people_by_window}


def build_payload(db: Path, report_day: str, avatar_cache: Path | None = None) -> dict[str, Any]:
    end = iso_day(report_day)
    manifest_path = db / "manifest.json"
    manifest = load(manifest_path, {}) if manifest_path.exists() else {}
    roster = load_roster(db, avatar_cache)
    if len(roster) != 10:
        raise ValueError(f"Expected exactly 10 tracked accounts, found {len(roster)}")
    scored_accounts = {b["id"] for b in roster}
    if len(scored_accounts) != 10: raise ValueError(f"Expected exactly 10 scored accounts, found {len(scored_accounts)}")
    store = StockStore(db)
    docs = [load(path, {}) for path in store.iter_stock_paths()]
    docs = [doc for doc in docs if doc.get("ticker")]
    consensus_docs = [doc for doc in docs if is_consensus_equity(doc)]
    today = window(end, 1); week = window(end, 7); month = window(end, 28); previous_week = window(end - timedelta(days=7), 7)
    people = []
    for blogger in roster:
        bid = blogger["id"]
        daily_records = []
        for doc in docs:
            for row in in_window(doc.get("mentions") or [], today):
                if row.get("blogger_id") == bid: daily_records.append((doc, row))
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        docs_by_symbol = {instrument(d)["display_code"]: d for d in docs}
        for doc, row in daily_records: grouped[instrument(doc)["display_code"]].append(row)
        personal = []
        for symbol, rows in sorted(grouped.items()):
            counts = Counter(normal_stance(x.get("stance")) for x in rows)
            state = "both" if counts["bullish"] and counts["bearish"] else "bull_only" if counts["bullish"] else "bear_only" if counts["bearish"] else "no_direction"
            personal.append({"instrument": instrument(docs_by_symbol[symbol]), "state": state, "bullish_count": counts["bullish"], "bearish_count": counts["bearish"], "neutral_count": counts["neutral"], "evidence": [evidence(x) for x in rows]})
        daily_lists = RULES.tracked_person_stock_lists([{"stock": symbol, "direction": normal_stance(row.get("stance"))} for symbol, rows in grouped.items() for row in rows])
        avatar = blogger.get("avatar_data_uri") or svg_avatar(blogger.get("display_name") or bid, blogger.get("color") or "#52616b")
        person = {"blogger_id": bid, "display_name": blogger.get("display_name", bid), "handle": blogger.get("handle", ""), "x_url": blogger.get("x_url", f"https://x.com/{bid}"),
                  "signal_type": blogger.get("signal_type", "opinion"), "profile_summary": blogger.get("profile_summary", ""),
                  "avatar_data_uri": avatar, "daily_stock_lists": daily_lists, "personal_view": personal}
        people.append(person)
    weekly = main_report(consensus_docs, week, scored_accounts); weekly["changes"] = weekly_changes(consensus_docs, week, previous_week, scored_accounts)
    payload = {"meta": {"report_date_et": end.isoformat(), "timezone": "America/New_York", "generated_at": datetime.now(timezone.utc).isoformat(), "tracked_account_count": 10, "scored_account_count": 10,
                        "data_cutoff_at": manifest.get("generated_at"),
                        "data_snapshot_id": snapshot_id(manifest_path, db, avatar_cache)},
               "people": people, "daily": main_report(consensus_docs, today, scored_accounts), "weekly": weekly,
               "monthly": {"window": month, "rows": [], "top_picks": monthly_top_picks(consensus_docs, roster, month, end)},
               "stock_drilldowns": {}}
    for doc in docs:
        rows = in_window(doc.get("mentions") or [], month)
        # The 28-day table is a coverage view, not an instrument catalogue.
        # Never render no-post rows as empty "stocks" in the approved UI.
        if unique_posts(rows) == 0:
            continue
        payload["stock_drilldowns"][instrument(doc)["display_code"]] = drilldown(doc, end, roster)
        if not is_consensus_equity(doc):
            continue
        scored_rows = explicit_directional(rows, scored_accounts)
        counts = Counter(normal_stance(r.get("stance")) for r in scored_rows)
        directional = counts["bullish"] + counts["bearish"]
        bulls, bears = directions(rows, scored_accounts)
        directional_account_ids = sorted(bulls | bears)
        if len(directional_account_ids) < 3:
            continue
        payload["monthly"]["rows"].append({"instrument": instrument(doc), "bull_share": round(counts["bullish"] / directional, 6) if directional else None,
            "bear_share": round(counts["bearish"] / directional, 6) if directional else None,
            "price_change": price_change(doc, month), "price_change_28d": price_change(doc, month),
            "price_change_52w": price_change_52_weeks(doc, end), "unique_post_count": unique_posts(rows),
            "bullish_count": counts["bullish"], "bearish_count": counts["bearish"], "neutral_count": counts["neutral"], "participant_ids": sorted({r.get("blogger_id") for r in rows}),
            "directional_account_ids": directional_account_ids,
            "bullish_account_ids": sorted(bulls), "bearish_account_ids": sorted(bears)})
    payload["monthly"]["rows"].sort(key=lambda x: (-x["unique_post_count"], x["instrument"]["display_code"]))
    validate_invariants(payload)
    validate_schema(payload)
    return payload


def validate_invariants(payload: dict[str, Any]) -> None:
    if len(payload.get("people", [])) != 10: raise ValueError("Payload must contain 10 people")
    monthly = payload.get("monthly", {})
    for row in monthly.get("rows", []):
        if len(set(row.get("directional_account_ids", []))) < 3:
            raise ValueError("Monthly consensus rows require at least 3 directional accounts")
    top_picks = monthly.get("top_picks", [])
    if len(top_picks) != 10 or len({x.get("blogger_id") for x in top_picks}) != 10:
        raise ValueError("Monthly top picks must contain exactly 10 unique tracked accounts")
    for drill in payload.get("stock_drilldowns", {}).values():
        for key in ("today", "days_7", "days_28"):
            states = drill["person_windows"][key]
            stats = drill["people_by_window"][key]
            if len(states) != 10 or len(stats) != 10: raise ValueError(f"{key} must contain exactly 10 people")
            if len({x["blogger_id"] for x in states}) != 10: raise ValueError(f"{key} person states are not unique")
            for stat in stats:
                for item in stat["evidence"]:
                    if not item["url"] or item["tweet_id"] == "": raise ValueError("Evidence must retain tweet id and URL")


def validate_schema(payload: dict[str, Any]) -> None:
    schema = load(SCHEMA_PATH, {})
    if not schema:
        raise ValueError(f"Dashboard schema is unavailable: {SCHEMA_PATH}")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.absolute_path))
    if errors:
        rendered = []
        for error in errors[:20]:
            path = ".".join(str(part) for part in error.absolute_path) or "$"
            rendered.append(f"{path}: {error.message}")
        raise ValueError("Dashboard payload failed schema validation:\n" + "\n".join(rendered))


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate a local x-traders database into the 10V dashboard payload")
    parser.add_argument("date", help="Report cutoff date in America/New_York (YYYY-MM-DD)")
    parser.add_argument("--db", type=Path, default=PROJECT_DIR / "data" / "db")
    parser.add_argument("--avatar-cache", type=Path, default=PROJECT_DIR / "data" / "avatar_cache.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.db.is_dir():
        try:
            from snapshot_sync import sync
            cache, _, _ = sync()
            args.db = cache / "data" / "db"
            args.avatar_cache = cache / "data" / "avatar_cache.json"
        except Exception as exc:
            parser.error(f"database directory does not exist and cloud sync failed: {exc}")
    if not (args.db / "stocks").is_dir():
        parser.error(f"database is missing stocks/: {args.db}")
    payload = build_payload(args.db.resolve(), args.date, args.avatar_cache.resolve() if args.avatar_cache.exists() else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
