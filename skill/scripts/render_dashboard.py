#!/usr/bin/env python3
"""Render a self-contained interactive dashboard from a validated payload."""
from __future__ import annotations

import argparse
import hashlib
import json
from html import escape
from pathlib import Path

from dashboard_payload import build_payload, validate_schema


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent.parent
FINAL_UI = PROJECT_DIR / "handoff" / "10V-dashboard-backend-handoff-final-2026-07-17" / "01-final-ui" / "10-market-voices-complete.html"


def js_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def render(payload: dict) -> str:
    # The approved UI is the visual reference.  The production body below keeps
    # its single-file routing model while replacing every sample data array with
    # the validated deterministic payload.
    source_hash = hashlib.sha256(FINAL_UI.read_bytes()).hexdigest() if FINAL_UI.exists() else "missing"
    payload_json = js_json(payload)
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>10 Market Voices · {escape(payload["meta"]["report_date_et"])}</title>
<style>
:root{{--ink:#242629;--muted:#6a6b6a;--line:#d7d4cd;--paper:#fffefa;--green:#176747;--red:#a63c37;--blue:#385d8d}}*{{box-sizing:border-box}}body{{margin:0;background:#f7f6f1;color:var(--ink);font:14px/1.5 Arial,"Noto Sans SC",sans-serif}}a{{color:inherit}}.shell{{max-width:1280px;margin:auto;padding:36px 48px 80px}}header{{border-bottom:2px solid var(--ink);padding-bottom:20px}}h1,h2,h3{{font-family:Georgia,"Noto Serif SC",serif}}h1{{margin:0;font-size:38px}}h2{{font-size:26px;margin:46px 0 14px}}.meta,.note{{color:var(--muted)}}.people{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:16px;margin-top:24px}}.person,.card,.detail{{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:16px}}.person{{cursor:pointer}}.person:hover,.card:hover{{border-color:#90908a}}.avatar{{width:42px;height:42px;border-radius:50%;object-fit:cover;vertical-align:middle;margin-right:9px}}.role{{float:right;color:var(--muted);font-size:11px}}.counts{{display:flex;gap:10px;margin-top:13px;font-weight:bold}}.bull{{color:var(--green)}}.bear{{color:var(--red)}}.neutral{{color:var(--muted)}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}}.card h3{{margin:0;font-size:21px}}.pill{{display:inline-block;padding:2px 7px;border-radius:999px;background:#eef1f5;color:var(--blue);font-size:11px}}.voices{{margin:12px 0 0;padding:0;list-style:none}}.voices li{{padding:6px 0;border-top:1px solid #ece9e1}}.price{{font-weight:bold}}.empty{{color:var(--muted);padding:10px 0}}.detail{{margin-top:28px}}.back{{border:1px solid var(--line);background:white;border-radius:7px;padding:7px 10px;cursor:pointer}}.window button{{margin-right:6px;padding:6px 10px;border:1px solid var(--line);background:white;border-radius:7px;cursor:pointer}}.window button.active{{background:#e9eef6;color:var(--blue)}}.evidence{{padding:10px 0;border-top:1px solid var(--line)}}.chart{{display:flex;align-items:end;height:130px;gap:2px;border-bottom:1px solid var(--line);margin:14px 0}}.chart i{{flex:1;background:#8fa6c6;min-width:2px}}@media(max-width:1100px){{.people{{grid-template-columns:repeat(3,1fr)}}}}@media(max-width:700px){{.shell{{padding:22px 16px}}.people,.grid{{grid-template-columns:1fr}}h1{{font-size:30px}}}}
</style></head><body><main class="shell"><header><div class="note">公开观点与市场信号追踪 · 不构成投资建议</div><h1>追踪人物</h1><div class="meta">数据截止 {escape(payload["meta"]["report_date_et"])} · America/New_York · 7 位观点账号与 3 个独立信号源</div></header><section id="people" class="people"></section><section id="reports"></section><section id="detail"></section></main>
<script>const PAYLOAD={payload_json};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const byId=Object.fromEntries(PAYLOAD.people.map(p=>[p.blogger_id,p]));
const price=p=>p.percentage===null?'暂无行情数据':`${{p.percentage>=0?'+':''}}${{p.percentage.toFixed(2)}}%`;
const person=p=>`<article class="person" tabindex="0" data-person="${{esc(p.blogger_id)}}"><span class="role">${{esc(p.signal_type)}}</span><img class="avatar" src="${{p.avatar_data_uri}}" alt="${{esc(p.display_name)}}"><b>${{esc(p.display_name)}}</b><div class="note">${{esc(p.handle)}}</div><div class="counts"><span class="bull">↑ ${{p.daily_stock_lists.bullish.length}}</span><span class="bear">↓ ${{p.daily_stock_lists.bearish.length}}</span><span class="neutral">— ${{p.daily_stock_lists.neutral.length}}</span></div></article>`;
function voices(items,klass){{return items.map(x=>{{let p=byId[x.blogger_id]||{{}};return `<li><b class="${{klass}}">${{esc(p.display_name||x.blogger_id)}}</b> ${{x.reasons[0]?`· ${{esc(x.reasons[0])}}`:''}} <a href="${{esc(x.evidence_url)}}" target="_blank" rel="noopener">原帖 ↗</a></li>`}}).join('')}}
function cards(title,items){{if(!items.length)return `<section><h2>${{title}}</h2><div class="empty">本窗口暂无符合条件的标的。</div></section>`;return `<section><h2>${{title}}</h2><div class="grid">${{items.map(x=>`<article class="card"><a href="#stock=${{encodeURIComponent(x.instrument.display_code)}}"><h3>${{esc(x.instrument.display_code)}}</h3></a><div class="note">${{esc(x.instrument.display_name)}} · <span class="price">${{price(x.price_change)}}</span></div><ul class="voices">${{voices(x.bullish_accounts,'bull')}}${{voices(x.bearish_accounts,'bear')}}</ul></article>`).join('')}}</div></section>`}}
function report(){{let d=PAYLOAD.daily,w=PAYLOAD.weekly,m=PAYLOAD.monthly;document.querySelector('#reports').innerHTML=cards('日报 · 明确共同看多',d.shared_bullish)+cards('日报 · 明确共同看空',d.shared_bearish)+cards('日报 · 多空分歧',d.disagreement)+cards('周报 · 明确共同看多',w.shared_bullish)+cards('周报 · 明确共同看空',w.shared_bearish)+cards('周报 · 多空分歧',w.disagreement)+`<section><h2>近 28 日标的</h2><div class="note">${{m.window.start}} 至 ${{m.window.end}} · 共 ${{m.rows.length}} 个标的</div></section>`}}
function showPerson(id){{let p=byId[id];let rows=p.personal_view.map(x=>`<div class="evidence"><b>${{esc(x.instrument.display_code)}}</b> · ${{esc(x.state)}} · ↑${{x.bullish_count}} ↓${{x.bearish_count}}<br>${{x.evidence.map(e=>`<a href="${{esc(e.url)}}" target="_blank" rel="noopener">${{esc(e.date)}} 原帖</a>`).join(' · ')}}</div>`).join('')||'<div class="empty">当日无相关记录。</div>';document.querySelector('#detail').innerHTML=`<article class="detail"><button class="back" onclick="closeDetail()">返回</button><h2>${{esc(p.display_name)}} · 今日个人视角</h2><p>${{esc(p.bio||'暂无人物简介')}}</p>${{rows}}</article>`;location.hash='person='+encodeURIComponent(id)}}
function showStock(symbol){{let d=PAYLOAD.stock_drilldowns[symbol];if(!d)return;let key='today';const draw=()=>{{let s=d.window_summaries[key],people=d.people_by_window[key],series=d.price_series,lo=Math.min(...series.map(x=>x.close)),hi=Math.max(...series.map(x=>x.close));let bars=series.map(x=>`<i style="height:${{hi===lo?50:20+80*(x.close-lo)/(hi-lo)}}%" title="${{esc(x.date)}}: ${{x.close}}"></i>`).join('');document.querySelector('#detail').innerHTML=`<article class="detail"><button class="back" onclick="closeDetail()">返回</button><h2>${{esc(d.instrument.display_code)}} · ${{esc(d.instrument.display_name)}}</h2><div class="window"><button data-w="today" class="${{key==='today'?'active':''}}">今日</button><button data-w="days_7" class="${{key==='days_7'?'active':''}}">7日</button><button data-w="days_28" class="${{key==='days_28'?'active':''}}">28日</button></div><p class="note">${{s.window.start}} 至 ${{s.window.end}} · ${{price(s.price_change)}} · ${{s.mention_count}} 条记录</p><div class="chart">${{bars}}</div>${{people.map(p=>`<div class="evidence"><b>${{esc(byId[p.blogger_id].display_name)}}</b> · ${{esc(p.latest_direction||'未提及')}} · ↑${{p.bullish_count}} —${{p.neutral_count}} ↓${{p.bearish_count}} ${{p.latest?`<a href="${{esc(p.latest.url)}}" target="_blank" rel="noopener">原帖 ↗</a>`:''}}</div>`).join('')}}</article>`;document.querySelectorAll('[data-w]').forEach(b=>b.onclick=()=>{{key=b.dataset.w;draw()}})}};draw()}}
function closeDetail(){{document.querySelector('#detail').innerHTML='';history.replaceState(null,'','#')}}
function route(){{let h=decodeURIComponent(location.hash.slice(1));if(h.startsWith('stock='))showStock(h.slice(6));else if(h.startsWith('person='))showPerson(h.slice(7));else closeDetail()}}
document.querySelector('#people').innerHTML=PAYLOAD.people.map(person).join('');document.querySelectorAll('[data-person]').forEach(el=>{{el.onclick=()=>showPerson(el.dataset.person);el.onkeydown=e=>{{if(e.key==='Enter'||e.key===' ')showPerson(el.dataset.person)}}}});report();addEventListener('hashchange',route);route();
</script><!-- final-ui-sha256:{source_hash} --></body></html>'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("date")
    parser.add_argument("--db", type=Path, default=PROJECT_DIR / "data" / "db")
    parser.add_argument("--config", type=Path, default=PROJECT_DIR / "config" / "bloggers.json")
    parser.add_argument("--profiles", type=Path, default=PROJECT_DIR / "config" / "blogger_profiles.json")
    parser.add_argument("--avatar-cache", type=Path, default=PROJECT_DIR / "data" / "avatar_cache.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = build_payload(args.db, args.config, args.profiles, args.date, args.avatar_cache)
    validate_schema(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(payload), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__": raise SystemExit(main())
