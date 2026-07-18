#!/usr/bin/env python3
"""Build the deterministic 10V Dashboard render payload.

This is the only place where dashboard aggregation is performed.  It consumes
the factual stock documents produced by ``build_db.py`` and never infers a
stance from post text.  The emitted object conforms to the 2026-07-17 handoff
schema and is suitable for any single-file renderer.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import importlib.util
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent.parent
HANDOFF_DIR = PROJECT_DIR / "handoff" / "10V-dashboard-backend-handoff-final-2026-07-17"
SCHEMA_PATH = HANDOFF_DIR / "02-backend-contract" / "dashboard-render-contract.schema.json"
RULES_PATH = HANDOFF_DIR / "03-rules-and-tests" / "report_rules.py"


def _rules():
    spec = importlib.util.spec_from_file_location("dashboard_handoff_rules", RULES_PATH)
    if not spec or not spec.loader:
        raise RuntimeError(f"Cannot load approved aggregation rules: {RULES_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RULES = _rules()


def load(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


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


def price_change(doc: dict[str, Any], item_window: dict[str, Any]) -> dict[str, Any]:
    status = str(doc.get("price_status") or "pending")
    series = [p for p in (doc.get("price_series") or []) if item_window["start"] <= str(p.get("date", ""))[:10] <= item_window["end"] and isinstance(p.get("close"), (int, float))]
    series.sort(key=lambda p: p["date"])
    if len(series) >= 2 and series[0]["close"]:
        return {"status": "ok", "percentage": round((series[-1]["close"] / series[0]["close"] - 1) * 100, 2), "start_date": series[0]["date"], "end_date": series[-1]["date"]}
    if status == "partial" and series:
        status = "pending"
    if status not in {"pending", "unavailable", "unverified_symbol", "error"}:
        status = "unavailable"
    return {"status": status, "percentage": None, "start_date": None, "end_date": None}


def explicit_opinion(rows: Iterable[dict[str, Any]], opinions: set[str]) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("blogger_id") in opinions and r.get("mention_type") == "explicit_stance"]


def directions(rows: Iterable[dict[str, Any]], opinions: set[str]) -> tuple[set[str], set[str]]:
    scored = explicit_opinion(rows, opinions)
    return ({r["blogger_id"] for r in scored if normal_stance(r.get("stance")) == "bullish"}, {r["blogger_id"] for r in scored if normal_stance(r.get("stance")) == "bearish"})


def account_summaries(rows: list[dict[str, Any]], ids: set[str], stance: str) -> list[dict[str, Any]]:
    output = []
    for blogger_id in sorted(ids, key=str.lower):
        candidates = [r for r in rows if r.get("blogger_id") == blogger_id and normal_stance(r.get("stance")) == stance]
        newest = max(candidates, key=lambda r: (str(r.get("created_at") or r.get("date")), str(r.get("tweet_id"))), default={})
        output.append({"blogger_id": blogger_id, "reasons": [str(x) for x in newest.get("reasons", [])], "evidence_url": str(newest.get("url") or "https://x.com")})
    return output


def unique_posts(rows: Iterable[dict[str, Any]]) -> int:
    return len({r.get("url") or r.get("tweet_id") for r in rows})


def stock_card(doc: dict[str, Any], rows: list[dict[str, Any]], item_window: dict[str, Any], opinions: set[str], classification: str) -> dict[str, Any]:
    bulls, bears = directions(rows, opinions)
    neutral = {r["blogger_id"] for r in explicit_opinion(rows, opinions) if normal_stance(r.get("stance")) == "neutral"}
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
    return {"instrument": instrument(doc), "window": windows["days_28"], "default_person_window": "today", "price_status": status, "price_series": series,
            "mention_days": [{"date": k, "evidence": v} for k, v in sorted(by_day.items())], "window_summaries": summaries,
            "person_windows": person_windows, "people_by_window": people_by_window}


def build_payload(db: Path, config: Path, profiles: Path, report_day: str, avatar_cache: Path | None = None) -> dict[str, Any]:
    end = iso_day(report_day)
    roster = load(config, {}).get("bloggers", [])
    if len(roster) != 10:
        raise ValueError(f"Expected exactly 10 tracked accounts, found {len(roster)}")
    opinions = {b["id"] for b in roster if b.get("signal_type") == "opinion"}
    if len(opinions) != 7: raise ValueError(f"Expected exactly 7 opinion accounts, found {len(opinions)}")
    profile_by_id = {p.get("blogger_id"): p for p in load(profiles, {}).get("profiles", [])}
    cache = load(avatar_cache, {}) if avatar_cache else {}
    docs = [load(path, {}) for path in sorted((db / "stocks").glob("*.json"))]
    docs = [doc for doc in docs if doc.get("ticker")]
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
        profile = profile_by_id.get(bid, {})
        avatar = cache.get(bid) or blogger.get("avatar_data_uri") or svg_avatar(blogger.get("display_name") or bid, blogger.get("color") or "#52616b")
        person = {"blogger_id": bid, "display_name": blogger.get("display_name", bid), "handle": blogger.get("handle", ""), "x_url": blogger.get("x_url", f"https://x.com/{bid}"),
                  "signal_type": blogger.get("signal_type", "opinion"), "avatar_data_uri": avatar, "daily_stock_lists": daily_lists, "personal_view": personal}
        if profile.get("bio"): person["bio"] = display_text(profile["bio"])
        people.append(person)
    weekly = main_report(docs, week, opinions); weekly["changes"] = weekly_changes(docs, week, previous_week, opinions)
    payload = {"meta": {"report_date_et": end.isoformat(), "timezone": "America/New_York", "generated_at": datetime.now(timezone.utc).isoformat(), "tracked_account_count": 10, "opinion_account_count": 7,
                        "data_snapshot_id": hashlib.sha256((db / "manifest.json").read_bytes()).hexdigest() if (db / "manifest.json").exists() else None},
               "people": people, "daily": main_report(docs, today, opinions), "weekly": weekly,
               "monthly": {"window": month, "rows": []}, "stock_drilldowns": {}}
    for doc in docs:
        rows = in_window(doc.get("mentions") or [], month)
        counts = Counter(normal_stance(r.get("stance")) for r in rows)
        directional = counts["bullish"] + counts["bearish"]
        bulls, bears = directions(rows, opinions)
        payload["monthly"]["rows"].append({"instrument": instrument(doc), "bull_share": round(counts["bullish"] / directional, 6) if directional else None,
            "bear_share": round(counts["bearish"] / directional, 6) if directional else None, "price_change": price_change(doc, month), "unique_post_count": unique_posts(rows),
            "bullish_count": counts["bullish"], "bearish_count": counts["bearish"], "neutral_count": counts["neutral"], "participant_ids": sorted({r.get("blogger_id") for r in rows}),
            "bullish_account_ids": sorted(bulls), "bearish_account_ids": sorted(bears)})
        payload["stock_drilldowns"][instrument(doc)["display_code"]] = drilldown(doc, end, roster)
    payload["monthly"]["rows"].sort(key=lambda x: (-x["unique_post_count"], x["instrument"]["display_code"]))
    validate_invariants(payload)
    return payload


def validate_invariants(payload: dict[str, Any]) -> None:
    if len(payload.get("people", [])) != 10: raise ValueError("Payload must contain 10 people")
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
    try:
        import jsonschema
    except ImportError as exc:
        raise RuntimeError("jsonschema is required for production payload validation; install requirements.txt") from exc
    schema = load(SCHEMA_PATH, None)
    if not schema: raise RuntimeError(f"Missing handoff schema: {SCHEMA_PATH}")
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER).validate(payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("date", help="Report cutoff date in America/New_York (YYYY-MM-DD)")
    parser.add_argument("--db", type=Path, default=PROJECT_DIR / "data" / "db")
    parser.add_argument("--config", type=Path, default=PROJECT_DIR / "config" / "bloggers.json")
    parser.add_argument("--profiles", type=Path, default=PROJECT_DIR / "config" / "blogger_profiles.json")
    parser.add_argument("--avatar-cache", type=Path, default=PROJECT_DIR / "data" / "avatar_cache.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-schema", action="store_true", help="Only for local dependency bootstrap; CI and production must validate schema")
    args = parser.parse_args()
    payload = build_payload(args.db, args.config, args.profiles, args.date, args.avatar_cache)
    if not args.no_schema: validate_schema(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
