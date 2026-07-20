#!/usr/bin/env python3
"""Validate a generated tracker dashboard at the DOM and rendered-browser levels."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path


def fail(errors: list[str], message: str) -> None:
    if message not in errors:
        errors.append(message)


def structural_checks(path: Path, expected_avatars: int) -> tuple[list[str], dict[str, object]]:
    html = path.read_text(encoding="utf-8")
    errors: list[str] = []

    # The 2026-07-17 final UI is an intentionally interactive one-file report.
    # Keep the legacy static-report checks below for historical artifacts, but
    # validate the deterministic embedded payload and routing contract here.
    if "const PAYLOAD=" in html and "final-ui-sha256:" in html:
        payload_start = html.find("const PAYLOAD=") + len("const PAYLOAD=")
        marker = html.find("const esc=", payload_start)
        payload_end = html.rfind(";", payload_start, marker)
        match = None if payload_start < len("const PAYLOAD=") or marker < 0 or payload_end < 0 else html[payload_start:payload_end]
        payload = None
        if match is None:
            fail(errors, "v2 dashboard has no embedded deterministic payload")
        else:
            try:
                payload = json.loads(match.replace("<\\/", "</"))
            except json.JSONDecodeError as exc:
                fail(errors, f"v2 dashboard payload is not valid JSON: {exc.msg} at {exc.pos}")
        people = payload.get("people", []) if isinstance(payload, dict) else []
        drills = payload.get("stock_drilldowns", {}) if isinstance(payload, dict) else {}
        if len(people) != expected_avatars:
            fail(errors, f"expected {expected_avatars} people in payload, found {len(people)}")
        avatars = [p.get("avatar_data_uri", "") for p in people]
        if len(avatars) != expected_avatars or any(not x.startswith("data:image/") for x in avatars):
            fail(errors, "v2 payload has a missing or non-embedded avatar")
        if not drills:
            fail(errors, "v2 payload has no stock drilldowns")
        for symbol, drill in drills.items():
            for key in ("today", "days_7", "days_28"):
                if len(drill.get("person_windows", {}).get(key, [])) != expected_avatars:
                    fail(errors, f"{symbol} {key} does not contain {expected_avatars} person states")
                    break
        if "function showStock" not in html or "#stock=" not in html or "function showPerson" not in html:
            fail(errors, "v2 report lacks required stock/person routing")
        required_runtime_markers = {
            'class="voice-name"': "five-column consensus person names",
            "data-quarter-sort": "monthly sortable headers",
            "quarter-account-popover": "monthly participant popovers",
            "data-instrument-more": "account evidence folding",
            "IntersectionObserver": "scroll-synchronized report navigation",
            "consistency_percentage": "canonical backend consistency values",
            "此前 7 天未达到门槛，本窗口形成至少 3 个明确看多账号": "canonical weekly-change threshold",
        }
        for runtime_marker, label in required_runtime_markers.items():
            if runtime_marker not in html:
                fail(errors, f"v2 report lacks {label}")
        monthly_rows = len(payload.get("monthly", {}).get("rows", [])) if isinstance(payload, dict) else 0
        return errors, {
            "embedded_avatars": len(avatars), "avatar_identities": len(avatars),
            "stock_profiles": len(drills), "account_profiles": len(people),
            "browse_rows_checked": monthly_rows, "v2_payload": True,
        }

    fail(errors, "legacy dashboard format is unsupported; render with render_dashboard.py")
    return errors, {"embedded_avatars": 0, "avatar_identities": 0, "stock_profiles": 0, "account_profiles": 0, "browse_rows_checked": 0, "v2_payload": False}

    # Historical static-dashboard checks intentionally remain unreachable while
    # the old source is retained for migration reference only.
    embedded = len(re.findall(r"data:image/", html))
    used_avatar_ids = set(re.findall(r"portrait-photo avatar-photo-([\w-]+)", html))
    rule_avatar_ids = set(re.findall(r"\.portrait>\.avatar-photo-([\w-]+)\{background-image:url\(\"data:image/", html))
    stock_targets = set(re.findall(r'id="stock-([^"]+)" class="route-panel stock-profile"', html))
    stock_links = set(re.findall(r'href="#stock-([^"]+)"', html))
    account_targets = set(re.findall(r'id="account-([^"]+)" class="route-panel account-profile"', html))

    if "<script" in html.lower():
        fail(errors, "report contains JavaScript; static body must not depend on scripts")
    if re.search(r'<details\s+id="stock-', html):
        fail(errors, "legacy stock details targets remain")
    if not stock_targets:
        fail(errors, "no stock Profile targets found")
    missing_targets = sorted(stock_links - stock_targets)
    if missing_targets:
        fail(errors, f"stock links without Profile targets: {missing_targets[:5]}")
    if len(account_targets) != expected_avatars:
        fail(errors, f"expected {expected_avatars} account Profiles, found {len(account_targets)}")
    required_profile_classes = {
        'class="profile-overview"': "investor overview",
        'class="profile-section profile-intro"': "stable profile introduction",
        'class="archive-root"': "tracked-post archive",
    }
    for marker, label in required_profile_classes.items():
        count = html.count(marker)
        if count != expected_avatars:
            fail(errors, f"expected {expected_avatars} account Profiles with {label}, found {count}")
    if re.search(r'<details class="archive-root"\s+open', html):
        fail(errors, "an account tracked-post archive is not collapsed by default")
    if embedded != expected_avatars:
        fail(errors, f"expected {expected_avatars} uniquely embedded avatars, found {embedded}")
    if len(used_avatar_ids) != expected_avatars:
        fail(errors, f"expected {expected_avatars} used avatar identities, found {len(used_avatar_ids)}")
    if rule_avatar_ids != used_avatar_ids:
        fail(errors, "avatar identities and high-specificity image rules do not match")

    if 'class="table-performance' not in html or 'class="time-context"' not in html:
        fail(errors, "browse table lacks separated performance and time-context fields")
    rows = re.findall(r"<tr>(.*?)</tr>", html, flags=re.S)
    checked_rows = 0
    for row in rows:
        cells = re.findall(r"<td(?:\s[^>]*)?>(.*?)</td>", row, flags=re.S)
        if len(cells) != 4:
            continue
        checked_rows += 1
        if re.search(r"\b\d{4}-\d{2}-\d{2}\b", cells[2]):
            fail(errors, "a performance cell contains a date instead of only value and currency")
            break
        if "time-context" not in row or "time-field" not in cells[3]:
            fail(errors, "a browse row lacks labeled time context")
            break
    if not checked_rows:
        fail(errors, "no four-column browse rows found")

    return errors, {
        "embedded_avatars": embedded,
        "avatar_identities": len(used_avatar_ids),
        "stock_profiles": len(stock_targets),
        "account_profiles": len(account_targets),
        "browse_rows_checked": checked_rows,
    }


def browser_executable() -> str | None:
    candidates = [
        shutil.which("msedge"),
        shutil.which("chrome"),
        shutil.which("chromium"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    return next((item for item in candidates if item and Path(item).exists()), None)


def browser_checks(path: Path, mode: str, expected_avatars: int) -> tuple[list[str], dict[str, object]]:
    errors: list[str] = []
    result: dict[str, object] = {"browser_checked": False}
    html = path.read_text(encoding="utf-8")
    if "const PAYLOAD=" not in html or "final-ui-sha256:" not in html:
        fail(errors, "legacy dashboard format is unsupported; render with render_dashboard.py")
        return errors, result
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        if mode == "required":
            fail(errors, "Playwright is required for visual validation but is unavailable")
        return errors, result

    executable = browser_executable()
    if not executable and mode == "required":
        fail(errors, "a Chromium-compatible browser is required for visual validation")
        return errors, result
    if not executable:
        return errors, result

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=executable)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(path.resolve().as_uri())
        if "const PAYLOAD=" in html and "final-ui-sha256:" in html:
            # The frozen final UI uses its original ``.voice`` cards; the
            # renderer only replaces their demo data.
            avatars = page.locator("#grid .voice .avatar")
            avatar_count = avatars.count()
            if avatar_count != expected_avatars:
                fail(errors, f"browser found {avatar_count} v2 avatars, expected {expected_avatars}")
            if avatar_count:
                source = avatars.first.get_attribute("src") or ""
                if not source.startswith("data:image/"):
                    fail(errors, "v2 visible avatar is not embedded")
            symbol_link = page.locator(".stock-symbol a").first
            if symbol_link.count():
                symbol_style = symbol_link.evaluate(
                    "e => ({color:getComputedStyle(e).color, parent:getComputedStyle(e.parentElement).color, decoration:getComputedStyle(e).textDecorationLine})"
                )
                if symbol_style["color"] != symbol_style["parent"] or symbol_style["decoration"] != "none":
                    fail(errors, "v2 stock-symbol route link no longer retains the frozen card typography")
            voice_lines = page.locator(".voice-line")
            if voice_lines.count():
                malformed_lines = voice_lines.evaluate_all(
                    "els => els.filter(e => !e.querySelector('.mini-avatar') || !e.querySelector('.voice-name') || !e.querySelector('.stance-word') || !e.querySelector('.voice-reason') || !e.querySelector('.source-post')).length"
                )
                if malformed_lines:
                    fail(errors, f"v2 has {malformed_lines} consensus rows that do not retain the approved five-column structure")
            else:
                fail(errors, "v2 report has no rendered consensus evidence rows")
            month_sort = page.locator('[data-quarter-sort="posts"]')
            if month_sort.count() != 1:
                fail(errors, "v2 monthly table does not retain its sortable header controls")
            else:
                if "active desc" not in (month_sort.get_attribute("class") or ""):
                    fail(errors, "v2 monthly post sort is not initially descending")
                month_sort.click()
                month_sort = page.locator('[data-quarter-sort="posts"]')
                if "active asc" not in (month_sort.get_attribute("class") or ""):
                    fail(errors, "v2 monthly post sort does not toggle to ascending order")
            participant_trigger = page.locator(".quarter-account-trigger").first
            if participant_trigger.count():
                participant_trigger.focus()
                if not participant_trigger.locator(".quarter-account-popover").count():
                    fail(errors, "v2 monthly participant trigger has no approved account popover")
            unavailable_returns = page.locator(".period-return.unavailable")
            if unavailable_returns.count() and unavailable_returns.first.evaluate("e => e.classList.contains('up') || e.classList.contains('down')"):
                fail(errors, "v2 missing price is still styled as a real gain or loss")
            stock_href = page.locator('a[href^="#stock="]').first.get_attribute("href")
            if stock_href:
                page.goto(path.resolve().as_uri() + stock_href)
                stock_frame = page.locator(".single-stock-view.open iframe")
                if stock_frame.count():
                    frame = stock_frame.content_frame
                    if not frame or not frame.locator("#chartLine").count() or not frame.locator("#kolGrid").count():
                        fail(errors, "v2 stock drilldown lacks the frozen stock-detail components")
                    elif frame.locator(".window-tab").count() != 3 or frame.locator(".kol-person-block").count() != expected_avatars:
                        fail(errors, "v2 stock drilldown does not retain its three windows and 10-person detail grid")
                    else:
                        frame.locator('.window-tab[data-window="week"]').click()
                        event_rows = frame.locator("#chartEvents .event-row")
                        if not event_rows.count():
                            fail(errors, "v2 stock drilldown has no chart evidence rows in the seven-day window")
                        elif frame.locator("#chartEvents .event-person img").count() != event_rows.count():
                            fail(errors, "v2 chart evidence rows do not retain the approved author avatars")
                        if "暂无结构化理由" in frame.locator("body").inner_text():
                            fail(errors, "v2 stock drilldown still renders the obsolete empty-reason fallback")
                        chart_triggers = frame.locator("#chartEvents .daily-trigger")
                        if chart_triggers.count():
                            chart_trigger = chart_triggers.first
                            chart_trigger.click()
                            active_event = frame.locator("#chartEvents .daily-event.active")
                            if active_event.count() != 1:
                                fail(errors, "v2 chart evidence click does not retain exactly one active popover")
                            elif active_event.evaluate("e => getComputedStyle(e).zIndex") != "40":
                                fail(errors, "v2 active chart popover is not elevated above sibling chart events")
                            chart_trigger.press("Escape")
                            if frame.locator("#chartEvents .daily-event.active").count():
                                fail(errors, "v2 chart evidence popover does not close on Escape")
                        bull_sort = frame.locator('[data-kol-sort="bull"]')
                        if bull_sort.count() != 1:
                            fail(errors, "v2 stock drilldown does not retain the frozen KOL sort controls")
                        else:
                            bull_sort.click()
                            if "active desc" not in (bull_sort.get_attribute("class") or ""):
                                fail(errors, "v2 bullish KOL sort does not start in descending order")
                            bull_sort.click()
                            if "active asc" not in (bull_sort.get_attribute("class") or ""):
                                fail(errors, "v2 bullish KOL sort does not toggle to ascending order")
                elif not page.locator("#drawer.open #detail").is_visible():
                    fail(errors, "v2 stock drilldown is not visible")
            else:
                fail(errors, "v2 report has no stock drilldown link")
            overflow: dict[str, int] = {}
            for width in (320, 768, 1440):
                page.set_viewport_size({"width": width, "height": 900})
                page.goto(path.resolve().as_uri())
                excess = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                overflow[str(width)] = excess
                if excess > 1:
                    fail(errors, f"page-level horizontal overflow at {width}px: {excess}px")
            page.goto(path.resolve().as_uri())
            page.locator("#grid .voice").first.click()
            if not page.locator("#drawer.open .big").count() or not page.locator("#drawer.open .instrument-list").count():
                fail(errors, "v2 account drawer does not retain the frozen counters and instrument list")
            else:
                extra_posts = page.locator("#drawer.open .extra-post")
                if extra_posts.count() and extra_posts.first.is_visible():
                    fail(errors, "v2 account drawer does not collapse excess evidence by default")
                more = page.locator("#drawer.open [data-instrument-more]").first
                if more.count():
                    more.click()
                    if page.locator("#drawer.open .instrument-group.expanded").count() != 1:
                        fail(errors, "v2 account evidence expansion control does not work")
                page.keyboard.press("Escape")
                if page.locator("#drawer.open").count():
                    fail(errors, "v2 account drawer does not close on Escape")
            browser.close()
            return errors, {"browser_checked": True, "avatar_identities_rendered": avatar_count, "overflow_px": overflow, "v2_payload": True}
        avatar_ids = page.locator(".portrait-photo").evaluate_all(
            "els => [...new Set(els.map(e => [...e.classList].find(c => c.startsWith('avatar-photo-'))))].filter(Boolean)"
        )
        if len(avatar_ids) != expected_avatars:
            fail(errors, f"browser found {len(avatar_ids)} avatar identities, expected {expected_avatars}")
        for avatar_class in avatar_ids:
            state = page.locator(f".{avatar_class}").first.evaluate(
                "e => ({background: getComputedStyle(e).backgroundImage})"
            )
            if not state["background"] or state["background"] == "none":
                fail(errors, f"computed avatar image is blank for {avatar_class}")

        account_href = page.locator('a[href^="#account-"]').first.get_attribute("href")
        if account_href:
            page.goto(path.resolve().as_uri() + account_href)
            visible = page.locator(f"{account_href} .source-profile-hero .portrait-photo").first.evaluate(
                "e => ({width: e.offsetWidth, height: e.offsetHeight, background: getComputedStyle(e).backgroundImage})"
            )
            if visible["width"] <= 0 or visible["height"] <= 0 or visible["background"] == "none":
                fail(errors, "the visible account Profile avatar has no rendered image or dimensions")
            if not page.locator(f"{account_href} .profile-overview").is_visible():
                fail(errors, "account Profile investor overview is not visible")
            if page.locator(f"{account_href} .archive-root").get_attribute("open") is not None:
                fail(errors, "account Profile archive renders open by default")
        else:
            fail(errors, "no account Profile link is available for visible-avatar validation")

        overflow: dict[str, int] = {}
        for width in (320, 768, 1440):
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(path.resolve().as_uri() + "#month")
            excess = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            overflow[str(width)] = excess
            if excess > 1:
                fail(errors, f"page-level horizontal overflow at {width}px: {excess}px")
        browser.close()
        result = {"browser_checked": True, "avatar_identities_rendered": len(avatar_ids), "overflow_px": overflow}
    return errors, result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("html", type=Path)
    parser.add_argument("--browser", choices=("required", "auto", "skip"), default="required")
    parser.add_argument("--expected-avatars", type=int, default=10)
    args = parser.parse_args()
    if not args.html.is_file():
        parser.error(f"report not found: {args.html}")

    errors, summary = structural_checks(args.html, args.expected_avatars)
    browser_summary: dict[str, object] = {"browser_checked": False}
    if args.browser != "skip":
        browser_errors, browser_summary = browser_checks(args.html, args.browser, args.expected_avatars)
        errors.extend(browser_errors)
    summary.update(browser_summary)
    summary["ok"] = not errors
    summary["errors"] = errors
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
