#!/usr/bin/env python3
"""Render a validated payload into the frozen 2026-07-17 dashboard UI.

The handoff HTML supplies the complete visual shell, CSS and interaction
language.  Its demonstration script is deliberately removed; this module
embeds only factual payload JSON and a small adapter which fills the existing
containers.  It performs no aggregation or content inference.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path

from dashboard_payload import PROJECT_DIR, build_payload, validate_schema


SCRIPT_DIR = Path(__file__).resolve().parent
FINAL_UI = SCRIPT_DIR.parent / "references" / "final-ui" / "10-market-voices-complete.html"


def _without_demo_script(source: str) -> str:
    """Keep the approved document and styles, dropping only demo runtime data."""
    return re.sub(r"<script\b[^>]*>.*?</script>", "", source, flags=re.IGNORECASE | re.DOTALL)


def _stock_detail_shell(source: str) -> str:
    """Decode the frozen stock-detail document without its demo-data script."""
    match = re.search(r"const singleFileStockDetailBase64='([^']+)'", source)
    if not match:
        raise RuntimeError("The approved final UI does not contain its stock-detail shell")
    detail = base64.b64decode(match.group(1)).decode("utf-8")
    return _without_demo_script(detail)


def _stock_detail_binding() -> str:
    """Runtime bound inside the frozen full-screen stock-detail document."""
    return r'''
(() => {
  const D=window.__STOCK_DETAIL__, P=window.__STOCK_PEOPLE__||{};
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const label={bullish:'看多',bearish:'看空',neutral:'中性'};
  const status=p=>p&&p.percentage!==null?`${p.percentage>=0?'+':''}${Number(p.percentage).toFixed(2)}%`:(p?.status==='pending'?'行情待补齐':'暂无行情');
  const tone=p=>p&&p.percentage!==null?(p.percentage>=0?'up':'down'):'neutral';
  const set=(id,value)=>{const node=document.querySelector(id);if(node)node.textContent=value};
  const windows={day:'today',week:'days_7',month:'days_28'};
  const shellDate=D.window_summaries.days_28.window.end;
  set('#symbol',D.instrument.display_code);set('#company',D.instrument.display_name);set('.report-date',`数据截至 ${shellDate} · 美东时间`);
  const points=(D.price_series||[]).filter(x=>Number.isFinite(x.close));
  function drawChart(){
    const svg=document.querySelector('.chart-svg'), line=document.querySelector('#chartLine'), area=document.querySelector('#chartArea');
    if(!svg||!line||points.length<2){set('#chartRange','近28天 · 暂无完整行情');return}
    const width=Math.max(svg.clientWidth||1000,320),height=220,values=points.map(x=>x.close),lo=Math.min(...values),hi=Math.max(...values),span=hi-lo||1,top=28,bottom=198;
    const xy=values.map((value,i)=>[i/(values.length-1)*width,bottom-(value-lo)/span*(bottom-top)]);
    svg.setAttribute('viewBox',`0 0 ${width} ${height}`);line.setAttribute('d','M '+xy.map(p=>p.join(',')).join(' L '));area.setAttribute('d','M '+xy[0].join(',')+' L '+xy.slice(1).map(p=>p.join(',')).join(' L ')+` L ${width},210 L 0,210 Z`);
    set('#chartRange',`${points[0].date}–${points.at(-1).date} · 收盘价`);set('#chartStart',points[0].date.slice(5));set('#chartMiddle',points[Math.floor(points.length/2)].date.slice(5));set('#chartEnd',points.at(-1).date.slice(5));
    const byDate=Object.fromEntries((D.mention_days||[]).map(x=>[x.date,x.evidence]));
    const events=points.map((point,i)=>({point,i,rows:byDate[point.date]||[]})).filter(x=>x.rows.length).map(event=>{const [x,y]=xy[event.i],rows=event.rows.slice(0,6);return `<div class="daily-event bull" style="left:${x}px;top:${y}px"><button class="daily-trigger" type="button" aria-label="${event.rows.length} 条原帖"></button><div class="daily-popover"><h3>${event.point.date}<span>${event.rows.length} 条记录</span></h3>${rows.map(row=>`<div class="event-row"><span class="event-person">${esc(P[row.blogger_id]?.display_name||row.blogger_id)}</span><span class="event-stance ${row.stance}">${label[row.stance]||'中性'}</span><span class="event-reason">${esc((row.reasons||[])[0]||'暂无结构化理由')}</span><a class="event-source" href="${esc(row.url)}" target="_blank" rel="noopener">原帖 ↗</a></div>`).join('')}</div></div>`}).join('');document.querySelector('#chartEvents').innerHTML=events;
  }
  function peopleFor(key){return D.people_by_window[key]||[]}
  function state(person){if(person.bullish_count&&person.bearish_count)return 'mixed';if(person.bullish_count)return 'bull';if(person.bearish_count)return 'bear';if(person.neutral_count)return 'neutral';return 'none'}
  function chips(key){const grouped={bull:[],bear:[],mixed:[],neutral:[],none:[]};peopleFor(key).forEach(p=>grouped[state(p)].push(p));const names={bull:'看多',bear:'看空',mixed:'多空均有',neutral:'中性',none:'未提及'};return Object.entries(grouped).filter(([,rows])=>rows.length).map(([kind,rows])=>`<span class="stance-people-group ${kind}"><strong>${names[kind]}</strong>${rows.map(p=>{const person=P[p.blogger_id]||{};return `<a class="stance-person-chip ${kind}" href="${esc(person.x_url||'#')}" target="_blank" rel="noopener"><img src="${person.avatar_data_uri||''}" alt="${esc(person.display_name||p.blogger_id)}">${esc(person.display_name||p.blogger_id)}</a>`}).join('')}</span>`).join('')}
  function renderKol(key){const rows=peopleFor(key).slice().sort((a,b)=>(b.mention_count-a.mention_count)||String(P[a.blogger_id]?.display_name||a.blogger_id).localeCompare(String(P[b.blogger_id]?.display_name||b.blogger_id)));document.querySelector('#kolDetailRange').textContent=D.window_summaries[key].window.start+'–'+D.window_summaries[key].window.end;document.querySelector('#kolGrid').innerHTML=`<div class="kol-head"><span>跟踪账号</span><span>提及</span><span class="kol-composition-head"><span>多空构成</span></span><span>一致性</span><span class="kol-latest-summary-head">最新观点摘要</span><span>展开全部</span></div>${rows.map(row=>{const person=P[row.blogger_id]||{},total=row.bullish_count+row.bearish_count+row.neutral_count||1,directional=row.bullish_count+row.bearish_count,consistency=directional?Math.round(Math.max(row.bullish_count,row.bearish_count)/directional*100):null,latest=row.latest;return `<section class="kol-person-block"><div class="kol-row"><div><a class="kol-name" href="${esc(person.x_url||'#')}" target="_blank" rel="noopener"><img src="${person.avatar_data_uri||''}" alt="${esc(person.display_name||row.blogger_id)}">${esc(person.display_name||row.blogger_id)}</a></div><div class="kol-count">${row.mention_count}</div><div><div class="kol-composition"><div class="kol-composition-bar"><span class="bull" style="width:${row.bullish_count/total*100}%"></span><span class="neutral" style="width:${row.neutral_count/total*100}%"></span><span class="bear" style="width:${row.bearish_count/total*100}%"></span></div><div class="kol-composition-counts"><span class="bull">${row.bullish_count} 多</span><span class="neutral">${row.neutral_count} 中</span><span class="bear">${row.bearish_count} 空</span></div></div></div><div class="kol-consistency"><b>${consistency===null?'—':consistency+'%'}</b><small>${row.consistency_label||'无方向'}</small></div><div class="kol-latest-summary"><span class="kol-latest-state"><time>${latest?.date||'—'}</time><span class="kol-current ${latest?.stance||'none'}">${label[latest?.stance]||'未提及'}</span></span><span class="kol-reason">${esc(latest?(latest.reasons||[])[0]||'暂无结构化理由':'该窗口没有结构化记录')}</span></div><div class="kol-action"><button class="kol-expand" type="button">查看全部 ↗</button></div></div><div class="kol-inline-evidence">${(row.evidence||[]).map(ev=>`<div class="kol-evidence-row"><time>${esc(ev.date)}</time><span class="stance ${ev.stance}">${label[ev.stance]||'中性'}</span><div class="post-original">${esc(ev.text||'')}</div><a class="source" href="${esc(ev.url)}" target="_blank" rel="noopener">原帖 ↗</a></div>`).join('')||'<div class="kol-evidence-empty">该窗口没有结构化记录。</div>'}</div></section>`}).join('')}`;document.querySelectorAll('.kol-expand').forEach(button=>button.onclick=()=>{const block=button.closest('.kol-person-block'),open=block.classList.toggle('open');button.textContent=open?'收起 ↗':'查看全部 ↗'})}
  function setWindow(view){const key=windows[view],summary=D.window_summaries[key],rows=peopleFor(key),counts={bull:0,bear:0,mixed:0,neutral:0,none:0};rows.forEach(row=>counts[state(row)]++);set('#accounts',summary.participant_count);set('#mentions',summary.mention_count);set('#bullSignals',summary.bullish_count);set('#bearSignals',summary.bearish_count);set('#windowMove',status(summary.price_change));set('#windowRange',summary.window.start+'–'+summary.window.end);set('#windowStancePeople','');document.querySelector('#windowStancePeople').innerHTML=chips(key);const total=10;['bull','bear','mixed','neutral','none'].forEach(kind=>{const cap=kind[0].toUpperCase()+kind.slice(1),bar=document.querySelector('#window'+cap+'Bar'),text=document.querySelector('#window'+cap+'Label');if(bar)bar.style.width=counts[kind]/total*100+'%';if(text){text.hidden=!counts[kind];text.style.width=counts[kind]/total*100+'%';text.textContent=counts[kind]+({bull:'人看多',bear:'人看空',mixed:'人多空均有',neutral:'人中性',none:'人未提及'}[kind])}});renderKol(key)}
  /* Replace the demo-compatible chart adapter with evidence rows that retain
     the approved event-card structure: avatar, author link, status and source. */
  function displayEvidence(row){
    const reason=(row.reasons||[]).find(value=>String(value||'').trim());
    if(reason)return String(reason);
    if(row.mention_type==='explicit_stance')return '\u672a\u63d0\u53d6\u7ed3\u6784\u5316\u7406\u7531';
    const original=String(row.text||'').replace(/\s+/g,' ').trim();
    return original?original.slice(0,180):'\u539f\u5e16\u672a\u63d0\u4f9b\u53ef\u5c55\u793a\u6587\u5b57';
  }
  function displayLabel(row){
    if(row.mention_type==='background')return '\u80cc\u666f\u63d0\u53ca';
    if(row.mention_type==='comparison')return '\u6bd4\u8f83\u63d0\u53ca';
    if(row.mention_type==='quote_or_other')return '\u8f6c\u5f15/\u5176\u4ed6';
    return row.stance==='bullish'?'\u770b\u591a':row.stance==='bearish'?'\u770b\u7a7a':'\u4e2d\u6027';
  }
  function drawChart(){
    const svg=document.querySelector('.chart-svg'),line=document.querySelector('#chartLine'),area=document.querySelector('#chartArea');
    if(!svg||!line||points.length<2){set('#chartRange','\u8fd1 28 \u5929 \u00b7 \u6682\u65e0\u5b8c\u6574\u884c\u60c5');return}
    const width=Math.max(svg.clientWidth||1000,320),height=220,values=points.map(x=>x.close),lo=Math.min(...values),hi=Math.max(...values),span=hi-lo||1,top=28,bottom=198;
    const xy=values.map((value,i)=>[i/(values.length-1)*width,bottom-(value-lo)/span*(bottom-top)]);
    svg.setAttribute('viewBox',`0 0 ${width} ${height}`);line.setAttribute('d','M '+xy.map(p=>p.join(',')).join(' L '));area.setAttribute('d','M '+xy[0].join(',')+' L '+xy.slice(1).map(p=>p.join(',')).join(' L ')+` L ${width},210 L 0,210 Z`);
    set('#chartRange',`${points[0].date}\u2013${points.at(-1).date} \u00b7 \u6536\u76d8\u4ef7`);set('#chartStart',points[0].date.slice(5));set('#chartMiddle',points[Math.floor(points.length/2)].date.slice(5));set('#chartEnd',points.at(-1).date.slice(5));
    const byDate=Object.fromEntries((D.mention_days||[]).map(x=>[x.date,x.evidence]));
    const eventTone=rows=>rows.some(row=>row.stance==='bearish')&&rows.some(row=>row.stance==='bullish')?'mixed':rows.some(row=>row.stance==='bearish')?'bear':rows.some(row=>row.stance==='bullish')?'bull':'neutral';
    const eventRow=row=>{const person=P[row.blogger_id]||{},name=person.display_name||row.blogger_id,reason=displayEvidence(row);return `<div class="event-row"><a class="event-person" href="${esc(person.x_url||'#')}" target="_blank" rel="noopener"><img src="${esc(person.avatar_data_uri||'')}" alt="${esc(name)}">${esc(name)}</a><span class="event-stance ${row.stance}">${esc(displayLabel(row))}</span><span class="event-reason" title="${esc(reason)}">${esc(reason)}</span><a class="event-source" href="${esc(row.url)}" target="_blank" rel="noopener">\u539f\u5e16 \u2197</a></div>`};
    const events=points.map((point,i)=>({point,i,rows:byDate[point.date]||[]})).filter(event=>event.rows.length).map(event=>{const [x,y]=xy[event.i],rows=event.rows.slice(0,6),peopleCount=new Set(event.rows.map(row=>row.blogger_id)).size;return `<div class="daily-event ${eventTone(rows)}" style="left:${x}px;top:${y}px"><button class="daily-trigger" type="button" aria-label="${event.rows.length} \u6761\u539f\u5e16"></button><div class="daily-popover"><h3>${event.point.date}<span>${peopleCount} \u4f4d\u4eba\u7269\u63d0\u53ca</span></h3>${rows.map(eventRow).join('')}</div></div>`}).join('');
    document.querySelector('#chartEvents').innerHTML=events;
  }
  function renderKol(key){
    const rows=peopleFor(key).slice().sort((a,b)=>(b.mention_count-a.mention_count)||String(P[a.blogger_id]?.display_name||a.blogger_id).localeCompare(String(P[b.blogger_id]?.display_name||b.blogger_id)));
    document.querySelector('#kolDetailRange').textContent=D.window_summaries[key].window.start+'\u2013'+D.window_summaries[key].window.end;
    document.querySelector('#kolGrid').innerHTML=`<div class="kol-head"><span>\u8ddf\u8e2a\u8d26\u53f7</span><span>\u63d0\u53ca</span><span class="kol-composition-head"><span>\u591a\u7a7a\u6784\u6210</span></span><span>\u4e00\u81f4\u6027</span><span class="kol-latest-summary-head">\u6700\u65b0\u89c2\u70b9\u6458\u8981</span><span>\u5c55\u5f00\u5168\u90e8</span></div>${rows.map(row=>{const person=P[row.blogger_id]||{},total=row.bullish_count+row.bearish_count+row.neutral_count||1,directional=row.bullish_count+row.bearish_count,consistency=directional?Math.round(Math.max(row.bullish_count,row.bearish_count)/directional*100):null,latest=row.latest,latestText=latest?displayEvidence(latest):'\u8be5\u7a97\u53e3\u6ca1\u6709\u8bb0\u5f55';return `<section class="kol-person-block"><div class="kol-row"><div><a class="kol-name" href="${esc(person.x_url||'#')}" target="_blank" rel="noopener"><img src="${esc(person.avatar_data_uri||'')}" alt="${esc(person.display_name||row.blogger_id)}">${esc(person.display_name||row.blogger_id)}</a></div><div class="kol-count">${row.mention_count}</div><div><div class="kol-composition"><div class="kol-composition-bar"><span class="bull" style="width:${row.bullish_count/total*100}%"></span><span class="neutral" style="width:${row.neutral_count/total*100}%"></span><span class="bear" style="width:${row.bearish_count/total*100}%"></span></div><div class="kol-composition-counts"><span class="bull">${row.bullish_count} \u591a</span><span class="neutral">${row.neutral_count} \u4e2d</span><span class="bear">${row.bearish_count} \u7a7a</span></div></div></div><div class="kol-consistency"><b>${consistency===null?'\u2014':consistency+'%'}</b><small>${esc(row.consistency_label||'\u65e0\u65b9\u5411')}</small></div><div class="kol-latest-summary"><span class="kol-latest-state"><time>${latest?.date||'\u2014'}</time><span class="kol-current ${latest?.stance||'none'}">${latest?esc(displayLabel(latest)):'\u672a\u63d0\u53ca'}</span></span><span class="kol-reason" title="${esc(latestText)}">${esc(latestText)}</span></div><div class="kol-action"><button class="kol-expand" type="button">\u67e5\u770b\u5168\u90e8 \u2197</button></div></div><div class="kol-inline-evidence">${(row.evidence||[]).map(ev=>`<div class="kol-evidence-row"><time>${esc(ev.date)}</time><span class="stance ${ev.stance}">${esc(displayLabel(ev))}</span><div class="post-original">${esc(ev.text||'')}</div><a class="source" href="${esc(ev.url)}" target="_blank" rel="noopener">\u539f\u5e16 \u2197</a></div>`).join('')||'<div class="kol-evidence-empty">\u8be5\u7a97\u53e3\u6ca1\u6709\u8bb0\u5f55\u3002</div>'}</div></section>`}).join('')}`;
    document.querySelectorAll('.kol-expand').forEach(button=>button.onclick=()=>{const block=button.closest('.kol-person-block'),open=block.classList.toggle('open');button.textContent=open?'\u6536\u8d77 \u2197':'\u67e5\u770b\u5168\u90e8 \u2197'});
  }
  let activeKolWindow='today',kolSortKey='count',kolSortDirection='desc';
  const kolSortValue=(row,key)=>key==='bull'?row.bullish_count:key==='bear'?row.bearish_count:key==='consistency'?((row.bullish_count+row.bearish_count)?Math.max(row.bullish_count,row.bearish_count)/(row.bullish_count+row.bearish_count):-1):row.mention_count;
  const kolSortButton=(key,text,side='')=>`<button class="kol-sort ${side} ${kolSortKey===key?`active ${kolSortDirection}`:''}" type="button" data-kol-sort="${key}" aria-label="${text}">${text}</button>`;
  function renderKol(key){
    activeKolWindow=key;
    const labelsByWindow={today:'\u4eca\u65e5',days_7:'\u6700\u8fd17\u5929',days_28:'\u6700\u8fd128\u5929'},direction=kolSortDirection==='asc'?1:-1;
    const rows=peopleFor(key).slice().sort((a,b)=>(kolSortValue(a,kolSortKey)-kolSortValue(b,kolSortKey))*direction||(b.mention_count-a.mention_count)||String(P[a.blogger_id]?.display_name||a.blogger_id).localeCompare(String(P[b.blogger_id]?.display_name||b.blogger_id)));
    document.querySelector('#kolDetailRange').textContent=D.window_summaries[key].window.start+'\u2013'+D.window_summaries[key].window.end;
    document.querySelector('#kolGrid').innerHTML=`<div class="kol-head"><span>\u8ddf\u8e2a\u4eba\u7269</span><span>${kolSortButton('count',`${labelsByWindow[key]}\u63d0\u53ca`)}</span><span class="kol-composition-head">${kolSortButton('bull','\u770b\u591a','bull')}<span>\u7acb\u573a\u6784\u6210</span>${kolSortButton('bear','\u770b\u7a7a','bear')}</span><span class="kol-stat-head">${kolSortButton('consistency','\u7acb\u573a\u4e00\u81f4\u5ea6')}<span class="kol-stat-help" tabindex="0" title="\u4e3b\u5bfc\u65b9\u5411\u6b21\u6570 \u00f7\uff08\u770b\u591a\u6b21\u6570\uff0b\u770b\u7a7a\u6b21\u6570\uff09\uff1b\u65e0\u65b9\u5411\u4e0d\u8fdb\u5165\u8ba1\u7b97\u3002">!</span></span><span class="kol-latest-summary-head">\u6700\u8fd1\u89c2\u70b9\u4e0e\u7406\u7531</span><span>\u5c55\u5f00\u5168\u90e8</span></div>${rows.map(row=>{const person=P[row.blogger_id]||{},total=row.bullish_count+row.bearish_count+row.neutral_count||1,directional=row.bullish_count+row.bearish_count,consistency=directional?Math.round(Math.max(row.bullish_count,row.bearish_count)/directional*100):null,latest=row.latest,latestText=latest?displayEvidence(latest):'\u8be5\u7a97\u53e3\u6ca1\u6709\u8bb0\u5f55';return `<section class="kol-person-block"><div class="kol-row"><div><a class="kol-name" href="${esc(person.x_url||'#')}" target="_blank" rel="noopener"><img src="${esc(person.avatar_data_uri||'')}" alt="${esc(person.display_name||row.blogger_id)}">${esc(person.display_name||row.blogger_id)}</a></div><div class="kol-count">${row.mention_count}</div><div><div class="kol-composition"><div class="kol-composition-bar"><span class="bull" style="width:${row.bullish_count/total*100}%"></span><span class="neutral" style="width:${row.neutral_count/total*100}%"></span><span class="bear" style="width:${row.bearish_count/total*100}%"></span></div><div class="kol-composition-counts"><span class="bull">${row.bullish_count} \u591a</span><span class="neutral">${row.neutral_count} \u4e2d</span><span class="bear">${row.bearish_count} \u7a7a</span></div></div></div><div class="kol-consistency"><b>${consistency===null?'\u2014':consistency+'%'}</b><small>${esc(row.consistency_label||'\u65e0\u65b9\u5411')}</small></div><div class="kol-latest-summary"><span class="kol-latest-state"><time>${latest?.date||'\u2014'}</time><span class="kol-current ${latest?.stance||'none'}">${latest?esc(displayLabel(latest)):'\u672a\u63d0\u53ca'}</span></span><span class="kol-reason" title="${esc(latestText)}">${esc(latestText)}</span></div><div class="kol-action"><button class="kol-expand" type="button">\u67e5\u770b\u5168\u90e8 \u2197</button></div></div><div class="kol-inline-evidence">${(row.evidence||[]).map(ev=>`<div class="kol-evidence-row"><time>${esc(ev.date)}</time><span class="stance ${ev.stance}">${esc(displayLabel(ev))}</span><div class="post-original">${esc(ev.text||'')}</div><a class="source" href="${esc(ev.url)}" target="_blank" rel="noopener">\u539f\u5e16 \u2197</a></div>`).join('')||'<div class="kol-evidence-empty">\u8be5\u7a97\u53e3\u6ca1\u6709\u8bb0\u5f55\u3002</div>'}</div></section>`}).join('')}`;
    document.querySelectorAll('.kol-expand').forEach(button=>button.onclick=()=>{const block=button.closest('.kol-person-block'),open=block.classList.toggle('open');button.textContent=open?'\u6536\u8d77 \u2197':'\u67e5\u770b\u5168\u90e8 \u2197'});
  }
  document.querySelector('#kolGrid').addEventListener('click',event=>{const button=event.target.closest('[data-kol-sort]');if(!button)return;const key=button.dataset.kolSort;kolSortDirection=kolSortKey===key?(kolSortDirection==='desc'?'asc':'desc'):'desc';kolSortKey=key;renderKol(activeKolWindow)});
  document.querySelectorAll('.window-tab').forEach(button=>button.addEventListener('click',()=>{document.querySelectorAll('.window-tab').forEach(x=>x.classList.toggle('active',x===button));setWindow(button.dataset.window)}));
  document.querySelector('#back').addEventListener('click',event=>{event.preventDefault();parent.postMessage({type:'stockDetailBack'},'*')});
  drawChart();setWindow('day');addEventListener('resize',drawChart);
})();
'''


def render(payload: dict) -> str:
    validate_schema(payload)
    if not FINAL_UI.is_file():
        raise RuntimeError(f"Missing packaged final UI: {FINAL_UI}")
    shell = _without_demo_script(FINAL_UI.read_text(encoding="utf-8"))
    source_hash = hashlib.sha256(FINAL_UI.read_bytes()).hexdigest()
    stock_shell = base64.b64encode(_stock_detail_shell(FINAL_UI.read_text(encoding="utf-8")).encode("utf-8")).decode("ascii")
    stock_binding = base64.b64encode(_stock_detail_binding().encode("utf-8")).decode("ascii")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    # Only responsive containment is added to the frozen CSS: real English
    # bios and the real 9-column table can otherwise widen the document.  The
    # table keeps its approved horizontal scroll behaviour inside its module.
    shell = shell.replace("</head>", "<style>.voice,.disclaimer{min-width:0}.bio,.disclaimer,.disclaimer p,.instrument-post,.weekly-card,.notice-defs,.notice-def,.weekly-date,.quarter-date,.daily-date{white-space:normal!important;overflow-wrap:anywhere}.notice-defs{flex-wrap:wrap}.quarter-grid{max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}</style></head>")
    runtime = r'''
<script>
const PAYLOAD=__PAYLOAD__;
const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const STOCK_DETAIL_SHELL='__STOCK_DETAIL_SHELL__';
const STOCK_DETAIL_BINDING='__STOCK_DETAIL_BINDING__';
const byId=Object.fromEntries(PAYLOAD.people.map(person=>[person.blogger_id,person]));
const labels={bullish:'看多',bearish:'看空',neutral:'中性',opinion:'观点',flow:'资金流',news:'新闻',disclosure:'披露'};
const tone=stance=>stance==='bullish'?'bull':stance==='bearish'?'bear':'neutral';
const sourceLink=url=>url?`<a class="source-post" href="${esc(url)}" target="_blank" rel="noopener">原帖 ↗</a>`:'';
const price=change=>change&&change.percentage!==null?`${change.percentage>=0?'+':''}${Number(change.percentage).toFixed(2)}%`:(change?.status==='pending'?'行情待补齐':change?.status==='partial'?'行情不完整':'暂无行情');
const priceClass=change=>change&&change.percentage!==null?(change.percentage>=0?'positive':'negative'):'unavailable';
const firstReason=item=>item?.reasons?.[0]||'暂无结构化理由';
const personName=id=>byId[id]?.display_name||id;

function setDates(){
 const daily=document.querySelector('.daily-date'),weekly=document.querySelector('.weekly-date'),monthly=document.querySelector('.quarter-date');
 if(daily)daily.textContent=`日报 · ${PAYLOAD.daily.window.end}`;
 if(weekly)weekly.textContent=`周报 · 最近 7 日（${PAYLOAD.weekly.window.start}–${PAYLOAD.weekly.window.end}）`;
 if(monthly)monthly.textContent=`近 28 日（${PAYLOAD.monthly.window.start}–${PAYLOAD.monthly.window.end}）`;
 const peopleNote=document.querySelector('#people')?.querySelector('span');
 if(peopleNote)peopleNote.textContent=`${PAYLOAD.meta.tracked_account_count} 个跟踪账号 · 点击人物卡查看当日视角与同源证据`;
 const reportDate=document.querySelector('.top .date');
 if(reportDate)reportDate.innerHTML=`报告口径<br><b>${esc(PAYLOAD.meta.report_date_et)} · 美东时间</b>`;
 const sample=document.querySelector('.top .sample');
 if(sample)sample.textContent='真实数据';
}

function preview(label,symbols,klass){
 const visible=symbols.slice(0,3),more=symbols.length-visible.length;
 return `<div class="voice-preview-row ${klass}"><span class="voice-preview-label">${label}</span><span class="voice-preview-symbols">${visible.map(symbol=>`<span role="link" tabindex="0" data-card-stock="${esc(symbol)}">${esc(symbol)}</span>`).join('')||'<i>—</i>'}${more?`<b>+${more}</b>`:''}</span></div>`;
}
function renderPeople(){
 const grid=document.querySelector('#grid'); if(!grid)return;
 grid.innerHTML=PAYLOAD.people.map((person,index)=>{
  const lists=person.daily_stock_lists||{bullish:[],bearish:[]};
  return `<button class="voice" data-person="${esc(person.blogger_id)}" aria-label="查看 ${esc(person.display_name)} 的账户视角"><div class="identity"><img class="avatar" src="${person.avatar_data_uri}" alt="${esc(person.display_name)}"><div><div class="identity-name-row"><h3>${esc(person.display_name)}</h3><a class="card-x-link" href="${esc(person.x_url)}" target="_blank" rel="noopener">X 主页 ↗</a></div><div class="handle">${esc(person.handle)}</div></div></div><span class="role ${esc(person.signal_type)}">${esc(labels[person.signal_type]||person.signal_type)}</span><p class="bio">${esc(person.bio||'暂无人物简介')}</p><div class="voice-preview">${preview('看多',lists.bullish||[],'bull')}${preview('看空',lists.bearish||[],'bear')}</div></button>`;
 }).join('');
 grid.onclick=event=>{const target=event.target.closest('[data-card-stock]');if(target){openStock(target.dataset.cardStock);return;}const card=event.target.closest('[data-person]');if(card&&!event.target.closest('a'))showPerson(card.dataset.person)};
 grid.onkeydown=event=>{const target=event.target.closest('[data-card-stock]');if(target&&(event.key==='Enter'||event.key===' ')){event.preventDefault();openStock(target.dataset.cardStock)}};
}

function lines(accounts,stance){return accounts.map(account=>{const person=byId[account.blogger_id]||{};return `<div class="voice-line"><img class="mini-avatar" src="${person.avatar_data_uri||''}" alt="${esc(person.display_name||account.blogger_id)}"><span class="stance-word ${tone(stance)}">${labels[stance]}</span><span class="voice-reason" title="${esc(firstReason(account))}">${esc(firstReason(account))}</span>${sourceLink(account.evidence_url)}</div>`}).join('')}
function card(item){
 const bulls=item.bullish_accounts||[],bears=item.bearish_accounts||[],classification=item.classification||'disagreement';
 const count=Math.max(bulls.length,bears.length);
 const verdict=classification==='shared_bullish'?`${count} 位账户共同看多`:classification==='shared_bearish'?`${count} 位账户共同看空`:'多空分歧';
 const tag=classification==='shared_bullish'?'共同看多':classification==='shared_bearish'?'共同看空':'分歧';
 return `<article class="stock-card ${classification==='shared_bullish'?'bullish':classification==='shared_bearish'?'bearish':'neutral'}"><div class="stock-summary"><div class="stock-symbol"><a href="#stock=${encodeURIComponent(item.instrument.display_code)}">${esc(item.instrument.display_code)}</a></div><div class="stock-name">${esc(item.instrument.display_name)}</div><div class="stock-move ${priceClass(item.price_change)}"><b>${esc(price(item.price_change))}</b><small>${item.price_change?.start_date&&item.price_change?.end_date?`${item.price_change.start_date}–${item.price_change.end_date}`:'真实状态'}</small></div></div><div class="stock-body"><div class="stock-verdict"><strong>${esc(verdict)}</strong><span class="consensus-tag ${classification==='disagreement'?'mixed':classification==='shared_bullish'?'bull':'bear'}">${tag}</span></div><div class="voice-lines">${lines(bulls,'bullish')}${lines(bears,'bearish')}</div><footer class="stock-foot"><span>${item.unique_post_count} 个去重原帖</span><a href="#stock=${encodeURIComponent(item.instrument.display_code)}">查看股票详情 ↗</a></footer></div></article>`;
}
function dailyGroup(title,note,items){return `<section class="daily-group"><header class="daily-group-head"><h3>${title}</h3><p>${note}</p></header><div class="stock-grid">${items.length?items.map(card).join(''):'<div class="quarter-empty">当前窗口暂无符合条件的标的</div>'}</div></section>`}
function renderDaily(){const daily=document.querySelector('#daily');if(!daily)return;const report=PAYLOAD.daily;daily.innerHTML=`<header class="daily-top"><h2 id="daily-title">当日共识</h2><div class="daily-date">日报 · ${report.window.end}</div></header>${dailyGroup('明确共同看多','至少 2 个观点账号给出明确看多信号',report.shared_bullish)}${dailyGroup('明确共同看空','至少 2 个观点账号给出明确看空信号',report.shared_bearish)}${dailyGroup('存在多空分歧','仅展示不同观点账号之间的真实多空信号',report.disagreement)}`}

function weeklyCard(item){
 const bulls=item.bullish_accounts||[], bears=item.bearish_accounts||[];
 const classification=item.classification||'disagreement';
 const verdict=classification==='shared_bullish'?`${bulls.length} 位账户看多`:classification==='shared_bearish'?`${bears.length} 位账户看空`:'多空分歧';
 return `<article class="stock-card"><div class="stock-summary"><div class="stock-symbol"><a href="#stock=${encodeURIComponent(item.instrument.display_code)}">${esc(item.instrument.display_code)}</a></div><div class="stock-name">${esc(item.instrument.display_name)}</div><div class="stock-move ${priceClass(item.price_change)} period-return"><b>${esc(price(item.price_change))}</b><small>本周涨跌幅</small></div></div><div class="stock-body"><div class="stock-verdict"><strong>${esc(verdict)}</strong></div><div class="voice-lines">${lines(bulls,'bullish')}${lines(bears,'bearish')}</div><footer class="stock-foot"><span>${bulls.length+bears.length} 位账号</span><a href="#stock=${encodeURIComponent(item.instrument.display_code)}">查看股票详情 ↗</a></footer></div></article>`;
}
function weeklyGroup(title,note,items){return `<section class="weekly-subsection"><header class="weekly-subhead"><h3>${title}</h3><p>${note}</p></header><div class="weekly-stock-grid">${items.length?items.map(weeklyCard).join(''):'<div class="quarter-empty">当前分类暂无真实数据</div>'}</div></section>`}
function renderWeekly(){
 const target=document.querySelector('#weeklyDetails');if(!target)return;const weekly=PAYLOAD.weekly;
 // These are the three frozen weekly containers.  Changes remain in the
 // payload for downstream use but must not create another weekly module.
 target.innerHTML=weeklyGroup('明确共同看多','滚动最近 7 天内，至少 2 个观点账号给出明确看多信号',weekly.shared_bullish)+weeklyGroup('明确共同看空','滚动最近 7 天内，至少 2 个观点账号给出明确看空信号',weekly.shared_bearish)+weeklyGroup('多空分歧','直接展示观点账号间真实的多空分歧',weekly.disagreement);
 target.querySelectorAll('.stock-symbol').forEach(symbol=>{const length=symbol.textContent.trim().length;if(length>=7)symbol.classList.add('symbol-xlong');else if(length>=5)symbol.classList.add('symbol-long')});
}

function changePeople(previous,current,direction){return `<span class="current-people">${current.length?current.map(id=>{const person=byId[id]||{},added=!previous.includes(id);return `<span class="current-person ${direction}${added?' new':''}"><img src="${person.avatar_data_uri||''}" alt="${esc(person.display_name||id)}"><span>${esc(person.display_name||id)}</span>${added?'<i>+</i>':''}</span>`}).join(''):'<span class="current-person-empty">—</span>'}</span>`}
function changeRow(title,previous,current,direction){return `<div class="current-account-row ${direction}"><span class="current-account-metric"><span>${title}</span><b>${previous.length} → ${current.length}</b></span>${changePeople(previous,current,direction)}</div>`}
function changeItem(item,mode){
 const bull=item.bullish||{},bear=item.bearish||{},previousBull=bull.previous||[],currentBull=bull.current||[],previousBear=bear.previous||[],currentBear=bear.current||[];
 const tag=item.label||'近7天变化';
 let rows=mode==='new_multi_bullish'?changeRow('看多账号',previousBull,currentBull,'bull')+(!currentBear.length?'':changeRow('同时看空',currentBear,currentBear,'bear')):mode==='consensus_strength'?(item.focus_direction==='bear'?changeRow('看空账号',previousBear,currentBear,'bear'):changeRow('看多账号',previousBull,currentBull,'bull')):changeRow('看多账号',previousBull,currentBull,'bull')+changeRow('看空账号',previousBear,currentBear,'bear');
 return `<article class="change-item"><div class="change-item-head"><span class="change-title-group"><a class="change-ticker" href="#stock=${encodeURIComponent(item.instrument.display_code)}">${esc(item.instrument.display_code)}</a><span class="judgement">${esc(tag)}</span></span><span class="change-return ${priceClass(item.price_change)==='negative'?'down':'up'}">${esc(price(item.price_change))}<small>最近7天涨跌</small></span></div><div class="current-accounts">${rows}</div></article>`;
}
function renderChanges(){
 const target=document.querySelector('.change-grid');if(!target)return;const changes=PAYLOAD.weekly.changes||{};
 const groups=[['new_multi_bullish','新形成多人看多','此前 7 天未达到门槛，本窗口形成至少 2 个明确看多观点'],['consensus_strength','原有共识增强或减弱','比较连续两个 7 天窗口内同方向的观点账号数量'],['reversal_or_disagreement','观点反转或出现分歧','仅展示具有明确多空状态变化的真实记录']];
 target.innerHTML=groups.map(([key,title,note])=>`<section class="change-subsection"><header class="change-subhead"><h4>${title}</h4><p>${note}</p></header><div class="change-subgrid">${(changes[key]||[]).map(item=>changeItem(item,key)).join('')||'<div class="quarter-empty">当前分类暂无真实变化</div>'}</div></section>`).join('');
}

function renderMonthly(){
 const target=document.querySelector('#quarterGrid');if(!target)return;
 const rows=PAYLOAD.monthly.rows||[];
 target.innerHTML=`<table class="quarter-table"><thead><tr><th>标的</th><th>多 / 空占比</th><th>涨跌幅</th><th>原帖</th><th>看多</th><th>看空</th><th>中性</th><th>参与账号</th><th>详情</th></tr></thead><tbody>${rows.map(row=>{const directional=row.bullish_count+row.bearish_count,bull=directional?Math.round(row.bullish_count/directional*100):0,bear=directional?100-bull:0;return `<tr><td><div class="quarter-stock"><span><a class="quarter-symbol" href="#stock=${encodeURIComponent(row.instrument.display_code)}">${esc(row.instrument.display_code)}</a><span class="quarter-name">${esc(row.instrument.display_name)}</span></span></div></td><td><div class="quarter-direction"><div class="quarter-direction-bar"><span class="bull" style="width:${bull}%"></span><span class="bear" style="width:${bear}%"></span></div><div class="quarter-direction-text"><span class="bull">${bull}% 多</span><span class="bear">${bear}% 空</span></div></div></td><td><span class="quarter-return period-return ${priceClass(row.price_change)==='negative'?'down':'up'}">${esc(price(row.price_change))}</span></td><td><span class="quarter-num">${row.unique_post_count}</span></td><td><span class="quarter-num bull">${row.bullish_count}</span></td><td><span class="quarter-num bear">${row.bearish_count}</span></td><td><span class="quarter-num">${row.neutral_count}</span></td><td><span class="quarter-num">${row.participant_ids.length}</span></td><td><a class="quarter-detail" href="#stock=${encodeURIComponent(row.instrument.display_code)}">↗</a></td></tr>`}).join('')||'<tr><td class="quarter-empty" colspan="9">近 28 日暂无帖子标的</td></tr>'}</tbody></table>`;
}

function showPerson(id){
 const person=byId[id];if(!person)return;const drawer=document.querySelector('#drawer'),detail=document.querySelector('#detail');
 const views=person.personal_view||[],bull=views.filter(view=>view.state==='bull_only'||view.state==='both').length,bear=views.filter(view=>view.state==='bear_only'||view.state==='both').length;
 const items=views.map(view=>{const state=view.state==='both'?'多空均有':view.state==='bull_only'?'仅看多':view.state==='bear_only'?'仅看空':'中性/无方向';const stateClass=view.state==='both'?'mixed':view.state==='bull_only'?'bullish':view.state==='bear_only'?'bearish':'neutral';return `<article class="instrument-row instrument-group"><div class="instrument-top"><div><a class="instrument-name" href="#stock=${encodeURIComponent(view.instrument.display_code)}">${esc(view.instrument.display_code)}</a><span class="instrument-post-count">${view.evidence.length} 条当日记录</span></div><span class="direction ${stateClass}">${state}</span></div><div class="instrument-posts">${view.evidence.map(ev=>`<div class="instrument-post"><time>${esc(ev.date)}</time><span class="post-direction ${tone(ev.stance)}">${esc(labels[ev.stance])}</span><span class="post-reason">${esc(firstReason(ev))}</span>${sourceLink(ev.url)}</div>`).join('')}</div></article>`}).join('')||'<div class="instrument-empty">当日没有相关记录</div>';
 detail.innerHTML=`<div class="eyebrow">${esc(labels[person.signal_type]||person.signal_type)}账号 · 今日个人视角</div><div class="panel-title"><h2>${esc(person.display_name)}</h2><a class="x-link" href="${esc(person.x_url)}" target="_blank" rel="noopener">X 主页 ↗</a></div><div class="handle">${esc(person.handle)}</div><p>${esc(person.bio||'暂无人物简介')}</p><div class="big"><div><strong class="up-text">↑ ${bull}</strong><span>看多标的</span></div><div><strong class="down-text">↓ ${bear}</strong><span>看空标的</span></div></div><div class="instrument-head"><h3>当日涉及标的 · ${views.length}</h3><span>立场、依据与原帖同源</span></div><div class="instrument-list">${items}</div>`;
 drawer.classList.add('open');drawer.setAttribute('aria-hidden','false');history.replaceState(null,'',`#person=${encodeURIComponent(id)}`);
}
const singleStockView=document.createElement('div');singleStockView.className='single-stock-view';singleStockView.setAttribute('aria-hidden','true');singleStockView.innerHTML='<iframe class="single-stock-frame" title="股票详情"></iframe>';document.body.appendChild(singleStockView);
const singleStockFrame=singleStockView.querySelector('iframe');
function detailShell(){return new TextDecoder('utf-8').decode(Uint8Array.from(atob(STOCK_DETAIL_SHELL),c=>c.charCodeAt(0)))}
function openSingleStock(symbol){
 const drill=PAYLOAD.stock_drilldowns[symbol];if(!drill)return;const data=JSON.stringify(drill).replace(/</g,'\\u003c'),people=JSON.stringify(byId).replace(/</g,'\\u003c');
 const bridge=`<script>window.__STOCK_DETAIL__=${data};window.__STOCK_PEOPLE__=${people};<\/script><script>eval(new TextDecoder('utf-8').decode(Uint8Array.from(atob('${STOCK_DETAIL_BINDING}'),c=>c.charCodeAt(0))))<\/script>`;
 singleStockFrame.srcdoc=detailShell().replace('</body>',bridge+'</body>');singleStockView.dataset.symbol=symbol;singleStockView.classList.add('open');singleStockView.setAttribute('aria-hidden','false');document.body.style.overflow='hidden';
}
function closeSingleStock(){if(!singleStockView.classList.contains('open'))return;singleStockView.classList.remove('open');singleStockView.setAttribute('aria-hidden','true');singleStockFrame.removeAttribute('srcdoc');document.body.style.overflow='';singleStockView.dataset.symbol=''}
function showStock(symbol){openSingleStock(symbol)}
function openStock(symbol){location.hash=`stock=${encodeURIComponent(symbol)}`}
function closeDrawer(){const drawer=document.querySelector('#drawer');drawer?.classList.remove('open');drawer?.setAttribute('aria-hidden','true');history.replaceState(null,'',location.pathname+location.search)}
function route(){const raw=decodeURIComponent(location.hash.slice(1));if(raw.startsWith('stock='))showStock(raw.slice(6));else{closeSingleStock();if(raw.startsWith('person='))showPerson(raw.slice(7));}}
function makeStockCardsInteractive(){document.querySelectorAll('.stock-card').forEach(card=>{const link=card.querySelector('a[href^="#stock="]');if(!link)return;const symbol=decodeURIComponent(link.getAttribute('href').split('=').slice(1).join('='));card.classList.add('clickable');card.tabIndex=0;card.setAttribute('role','link');card.setAttribute('aria-label',`查看 ${symbol} 股票详情页`);const open=event=>{if(event.target.closest('a,button'))return;openStock(symbol)};card.addEventListener('click',open);card.addEventListener('keydown',event=>{if((event.key==='Enter'||event.key===' ')&&!event.target.closest('a,button')){event.preventDefault();openStock(symbol)}})})}
const reasonTooltip=document.querySelector('#reasonTooltip');let activeReason=null;
function showReasonTooltip(target){const text=target.getAttribute('title')||target.textContent.trim();if(!text||!reasonTooltip)return;activeReason=target;reasonTooltip.textContent=text;reasonTooltip.classList.add('show');reasonTooltip.setAttribute('aria-hidden','false');const rect=target.getBoundingClientRect(),tip=reasonTooltip.getBoundingClientRect();reasonTooltip.style.left=`${Math.min(Math.max(14,rect.left),window.innerWidth-tip.width-14)}px`;reasonTooltip.style.top=`${rect.bottom+8}px`}
function hideReasonTooltip(target){if(target&&activeReason!==target)return;activeReason=null;reasonTooltip?.classList.remove('show');reasonTooltip?.setAttribute('aria-hidden','true')}
document.addEventListener('mouseover',event=>{const target=event.target.closest('.stock-card .voice-reason');if(target)showReasonTooltip(target)});document.addEventListener('mouseout',event=>{const target=event.target.closest('.stock-card .voice-reason');if(target&&!target.contains(event.relatedTarget))hideReasonTooltip(target)});document.addEventListener('focusin',event=>{const target=event.target.closest('.stock-card .voice-reason');if(target)showReasonTooltip(target)});document.addEventListener('focusout',event=>{const target=event.target.closest('.stock-card .voice-reason');if(target)hideReasonTooltip(target)});
const reportNavLinks=[...document.querySelectorAll('[data-report-nav]')];reportNavLinks.forEach(link=>link.addEventListener('click',event=>{event.preventDefault();document.getElementById(link.dataset.reportNav)?.scrollIntoView({behavior:'smooth',block:'start'});history.replaceState(null,'',`#${link.dataset.reportNav}`);reportNavLinks.forEach(item=>item.classList.toggle('active',item===link))}));
document.querySelector('#close')?.addEventListener('click',closeDrawer);document.querySelector('#drawer')?.addEventListener('click',event=>{if(event.target.id==='drawer')closeDrawer()});
window.addEventListener('message',event=>{if(event.data?.type==='stockDetailBack'){history.back()}});
renderPeople();renderDaily();renderWeekly();renderChanges();renderMonthly();setDates();makeStockCardsInteractive();window.addEventListener('hashchange',route);route();
</script>'''.replace('__PAYLOAD__', data)
    runtime = runtime.replace("__PAYLOAD__", data).replace("__STOCK_DETAIL_SHELL__", stock_shell).replace("__STOCK_DETAIL_BINDING__", stock_binding)
    return shell.replace("</body>", runtime + f"<!-- final-ui-sha256:{source_hash} --></body>")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("date")
    parser.add_argument("--db", type=Path, default=PROJECT_DIR / "data" / "db")
    parser.add_argument("--config", type=Path, default=PROJECT_DIR / "config" / "bloggers.json")
    parser.add_argument("--profiles", type=Path, default=PROJECT_DIR / "config" / "blogger_profiles.json")
    parser.add_argument("--avatar-cache", type=Path, default=PROJECT_DIR / "data" / "avatar_cache.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.db.is_dir():
        from snapshot_sync import sync
        cache, _, _ = sync()
        args.db = cache / "data" / "db"
        args.config = cache / "config" / "bloggers.json"
        args.profiles = cache / "config" / "blogger_profiles.json"
        args.avatar_cache = cache / "data" / "avatar_cache.json"
    payload = build_payload(args.db, args.config, args.profiles, args.date, args.avatar_cache)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(payload), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
