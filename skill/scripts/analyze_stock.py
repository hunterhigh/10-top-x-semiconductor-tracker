#!/usr/bin/env python3
"""analyze_stock.py — Deterministic pre-processing for stock Q&A.

Reads a single stock JSON from data/db/stocks/{TICKER}.json,
produces a structured intermediate analysis (JSON to stdout)
for LLM narrative synthesis.

Zero LLM, zero network. Pure aggregation.

Multi-blogger note: stocks/{TICKER}.json is a SHARED file — a ticker mentioned
by several tracked bloggers has ALL of their mentions merged into one
mentions[] list, each tagged "blogger_id" (see build_db.py). When the user
asks about one specific blogger's view ("what does Serenity think about
NVDA?"), pass --blogger <id> so the thesis-arc/key-candidates analysis below
reflects only that person's mentions — without it, phases and reasons would
blend multiple people's narratives into one, which would misattribute views.
Omit --blogger for a deliberate cross-tracker view (all mentions, unfiltered).

Usage:
    python analyze_stock.py <stock.json> [--as-of YYYY-MM-DD] [--blogger BLOGGER_ID]
"""

import json, sys, datetime, os
from collections import defaultdict, Counter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _argval(flag, default=None):
    for i, x in enumerate(sys.argv):
        if x == flag and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if x.startswith(flag + '='):
            return x.split('=', 1)[1]
    return default


def _eng_score(m):
    """Engagement score: likes + 2×retweets + replies."""
    e = m.get('engagement') or {}
    return (e.get('likes') or 0) + (e.get('retweets') or 0) * 2 + (e.get('replies') or 0)


def _mention_summary(m):
    """Extract a compact summary dict from a full mention."""
    if not m:
        return None
    return {
        "date": m['date'],
        "stance": m.get('stance'),
        "reason": (m.get('reasons') or [None])[0],
        "reasons_all": m.get('reasons') or [],
        "text": m.get('text', ''),
        "url": m.get('url', ''),
        "engagement": m.get('engagement') or {},
    }


def _make_phase(from_ym, to_ym, stance, count, reasons_list):
    """Build one phase dict with top reasons by frequency."""
    freq = Counter(r for r in reasons_list if r)
    top = [{"reason": r, "count": c} for r, c in freq.most_common(5)]
    return {
        "from": from_ym,
        "to": to_ym,
        "stance": stance,
        "mention_count": count,
        "top_reasons": top,
    }


def _aggregate_reasons(mentions_subset):
    """Exact-match dedup of reasons with frequency + date range + sample URL."""
    bucket = defaultdict(lambda: {"count": 0, "first": None, "last": None, "sample_url": ""})
    for m in mentions_subset:
        for r in (m.get('reasons') or []):
            if not r:
                continue
            b = bucket[r]
            b["count"] += 1
            d = m['date']
            if b["first"] is None or d < b["first"]:
                b["first"] = d
            if b["last"] is None or d > b["last"]:
                b["last"] = d
            if not b["sample_url"]:
                b["sample_url"] = m.get('url', '')
    out = [{"reason": reason, **b} for reason, b in bucket.items()]
    out.sort(key=lambda x: (-x["count"], x["first"] or ""))
    return out


def _bull_ratio(ms):
    if not ms:
        return None
    b = sum(1 for m in ms if m.get('stance') == 'bullish')
    return b / len(ms)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def analyze(path, as_of=None, blogger_id=None):
    with open(path, encoding='utf-8') as f:
        data = json.load(f)

    ticker = data.get('ticker', '')
    company = data.get('company', '')
    industry = data.get('industry', '')
    currency = data.get('currency', 'USD')

    # ── Filter mentions by as-of date and (optionally) by blogger ──────
    mentions = data.get('mentions', [])
    if as_of:
        mentions = [m for m in mentions if m.get('date') and m['date'] <= as_of.isoformat()]
    if blogger_id:
        mentions = [m for m in mentions if m.get('blogger_id') == blogger_id]

    all_sorted = sorted([m for m in mentions if m.get('date')], key=lambda m: m['date'])
    explicit = [m for m in all_sorted if m.get('mention_type') == 'explicit_stance']

    # ── Early exit: no data ────────────────────────────────────────────
    if not all_sorted:
        return {
            "ticker": ticker, "company": company, "industry": industry,
            "currency": currency, "status": "no_mentions",
            "arc_phases": [], "transitions": [],
            "first_mention": None, "latest_mention": None,
            "key_candidates": [],
            "bull_reasons": [], "bear_reasons": [], "risk_reasons": [],
            "price_context": {"first_px": None, "latest_px": None, "change_pct": None},
            "stats": {"total_mentions": 0, "explicit_stances": 0,
                      "bull": 0, "bear": 0, "neutral": 0,
                      "mentions_7d": 0, "mentions_30d": 0},
            "frequency_trend": "none", "conviction_trend": "none",
        }

    # ── Basic stats ────────────────────────────────────────────────────
    ref_date = as_of or datetime.date.fromisoformat(all_sorted[-1]['date'])
    d7 = (ref_date - datetime.timedelta(days=6)).isoformat()
    d30 = (ref_date - datetime.timedelta(days=29)).isoformat()
    d60 = (ref_date - datetime.timedelta(days=59)).isoformat()

    mentions_7d = sum(1 for m in all_sorted if m['date'] >= d7)
    mentions_30d = sum(1 for m in all_sorted if m['date'] >= d30)
    mentions_prev30 = sum(1 for m in all_sorted if d60 <= m['date'] < d30)

    bull_count = sum(1 for m in explicit if m.get('stance') == 'bullish')
    bear_count = sum(1 for m in explicit if m.get('stance') == 'bearish')
    neu_count = len(explicit) - bull_count - bear_count

    # ── Frequency trend ────────────────────────────────────────────────
    if mentions_prev30 == 0:
        freq_trend = "rising" if mentions_30d > 0 else "none"
    elif mentions_30d > mentions_prev30 * 1.3:
        freq_trend = "rising"
    elif mentions_30d < mentions_prev30 * 0.7:
        freq_trend = "declining"
    else:
        freq_trend = "stable"

    # ── Phase segmentation (by month) ──────────────────────────────────
    monthly = defaultdict(lambda: {"bull": 0, "bear": 0, "neu": 0,
                                    "reasons": [], "mentions": []})
    for m in explicit:
        ym = m['date'][:7]
        s = m.get('stance', '')
        if s == 'bullish':
            monthly[ym]["bull"] += 1
        elif s == 'bearish':
            monthly[ym]["bear"] += 1
        else:
            monthly[ym]["neu"] += 1
        for r in (m.get('reasons') or []):
            if r:
                monthly[ym]["reasons"].append(r)
        monthly[ym]["mentions"].append(m)

    months_sorted = sorted(monthly.keys())
    month_stances = []
    for ym in months_sorted:
        d = monthly[ym]
        if d["bull"] > d["bear"]:
            dom = "bullish"
        elif d["bear"] > d["bull"]:
            dom = "bearish"
        elif d["bull"] == d["bear"] and d["bull"] > 0:
            dom = "mixed"
        else:
            dom = "neutral"
        month_stances.append((ym, dom, d["bull"] + d["bear"] + d["neu"], d["reasons"]))

    # Merge consecutive same-stance months into phases
    phases = []
    if month_stances:
        cur_stance = month_stances[0][1]
        cur_from = month_stances[0][0]
        cur_count = month_stances[0][2]
        cur_reasons = list(month_stances[0][3])

        for i in range(1, len(month_stances)):
            ym, dom, cnt, reasons = month_stances[i]
            if dom == cur_stance:
                cur_count += cnt
                cur_reasons.extend(reasons)
            else:
                phases.append(_make_phase(cur_from, month_stances[i - 1][0],
                                          cur_stance, cur_count, cur_reasons))
                cur_stance = dom
                cur_from = ym
                cur_count = cnt
                cur_reasons = list(reasons)
        phases.append(_make_phase(cur_from, month_stances[-1][0],
                                  cur_stance, cur_count, cur_reasons))

    # ── Transitions ────────────────────────────────────────────────────
    transitions = []
    for i in range(1, len(phases)):
        prev_p = phases[i - 1]
        curr_p = phases[i]
        # Find first mention in the new phase
        first_in = None
        for m in explicit:
            mym = m['date'][:7]
            if mym >= curr_p['from'] and mym <= curr_p['to']:
                first_in = m
                break
        transitions.append({
            "date": first_in['date'] if first_in else curr_p['from'],
            "from_stance": prev_p['stance'],
            "to_stance": curr_p['stance'],
            "reason": (first_in.get('reasons') or [None])[0] if first_in else None,
            "text": (first_in.get('text') or '') if first_in else '',
            "url": (first_in.get('url') or '') if first_in else '',
        })

    # ── Key moment candidates (5-8 for LLM to select 3) ───────────────
    candidates = []
    seen_urls = set()

    def _add_candidate(ctype, label, mention):
        if not mention:
            return
        url = mention.get('url', '')
        if url and url in seen_urls:
            return
        if url:
            seen_urls.add(url)
        candidates.append({"type": ctype, "label": label, **_mention_summary(mention)})

    # a) First mention (thesis origin)
    _add_candidate("first_mention", "Thesis origin — first time discussed",
                   all_sorted[0])

    # b) Transition moments
    for t in transitions:
        # Find the full mention for this transition
        for m in explicit:
            if m['date'] == t['date'] and m.get('url') == t['url']:
                _add_candidate("transition",
                               f"Stance shift: {t['from_stance']} → {t['to_stance']}",
                               m)
                break

    # c) High engagement (top 3)
    for m in sorted(explicit, key=_eng_score, reverse=True)[:3]:
        if _eng_score(m) > 0:
            _add_candidate("high_engagement",
                           f"High engagement (score {_eng_score(m)})", m)

    # d) First occurrence of top-5 most frequent reasons
    reason_freq = Counter()
    reason_first_mention = {}
    for m in explicit:
        for r in (m.get('reasons') or []):
            if r:
                reason_freq[r] += 1
                if r not in reason_first_mention:
                    reason_first_mention[r] = m

    for r, _ in reason_freq.most_common(5):
        m = reason_first_mention[r]
        _add_candidate("reason_origin",
                        f"First mention of thesis element: {r[:60]}", m)

    # e) Latest mention (most recent)
    if len(all_sorted) > 1:
        _add_candidate("latest_mention", "Most recent mention", all_sorted[-1])

    # ── Reason aggregation ─────────────────────────────────────────────
    bull_reasons = _aggregate_reasons(
        [m for m in explicit if m.get('stance') == 'bullish'])
    bear_reasons = _aggregate_reasons(
        [m for m in explicit if m.get('stance') == 'bearish'])
    risk_reasons = _aggregate_reasons(
        [m for m in all_sorted if m.get('is_risk')])

    # ── Conviction trend ───────────────────────────────────────────────
    recent_exp = [m for m in explicit if m['date'] >= d30]
    earlier_exp = [m for m in explicit if m['date'] < d30]
    r_ratio = _bull_ratio(recent_exp)
    e_ratio = _bull_ratio(earlier_exp)

    if r_ratio is None or e_ratio is None:
        conviction_trend = "insufficient_data"
    elif r_ratio > e_ratio + 0.15:
        conviction_trend = "rising"
    elif r_ratio < e_ratio - 0.15:
        conviction_trend = "declining"
    else:
        conviction_trend = "stable"

    if conviction_trend == "stable":
        if bull_count > 0 and bear_count == 0:
            conviction_trend = "stable_high_bull"
        elif bear_count > 0 and bull_count == 0:
            conviction_trend = "stable_high_bear"

    # ── Price context ──────────────────────────────────────────────────
    price_series = data.get('price_series') or []
    first_px = None
    latest_px = None
    change_pct = None

    if price_series and all_sorted:
        first_date = all_sorted[0]['date']
        latest_date = all_sorted[-1]['date']

        for p in price_series:
            if p['date'] >= first_date:
                first_px = {"date": p['date'], "close": p['close']}
                break

        for p in price_series:
            if p['date'] <= latest_date:
                latest_px = {"date": p['date'], "close": p['close']}

        if (first_px and latest_px
                and first_px['close'] and first_px['close'] > 0):
            change_pct = round(
                (latest_px['close'] - first_px['close']) / first_px['close'] * 100, 1)

    # ── Assemble output ───────────────────────────────────────────────
    return {
        "ticker": ticker,
        "company": company,
        "industry": industry,
        "currency": currency,
        "status": "ok" if explicit else "no_explicit_stance",

        "arc_phases": phases,
        "transitions": transitions,
        "first_mention": _mention_summary(all_sorted[0]),
        "latest_mention": _mention_summary(all_sorted[-1]),
        "latest_stance": _mention_summary(explicit[-1]) if explicit else None,
        "key_candidates": candidates,

        "bull_reasons": bull_reasons[:10],
        "bear_reasons": bear_reasons[:10],
        "risk_reasons": risk_reasons[:10],

        "price_context": {
            "first_px": first_px,
            "latest_px": latest_px,
            "change_pct": change_pct,
            "currency": currency,
        },

        "stats": {
            "total_mentions": len(all_sorted),
            "explicit_stances": len(explicit),
            "bull": bull_count,
            "bear": bear_count,
            "neutral": neu_count,
            "mentions_7d": mentions_7d,
            "mentions_30d": mentions_30d,
        },
        "frequency_trend": freq_trend,
        "conviction_trend": conviction_trend,
    }


def main():
    # Force UTF-8 stdout on Windows (default GBK can't encode some Unicode chars)
    sys.stdout.reconfigure(encoding='utf-8')

    if len(sys.argv) < 2 or sys.argv[1].startswith('-'):
        print("Usage: python analyze_stock.py <stock.json> [--as-of YYYY-MM-DD] [--blogger BLOGGER_ID]",
              file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    as_of_str = _argval('--as-of')
    as_of = datetime.date.fromisoformat(as_of_str) if as_of_str else None
    blogger_id = _argval('--blogger')

    result = analyze(path, as_of, blogger_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
