'use strict';
const personnelHistory={query:'',no:'',end:'',span:7,record:null,error:'',loading:false,generation:0,expanded:new Set()};
function historyRememberDay(detail){
  if(!detail.isConnected)return;
  const key=detail.dataset.historyDay,H=personnelHistory;
  if(detail.open){H.expanded.add(key);H.expanded.delete('closed:'+key);}else{H.expanded.delete(key);H.expanded.add('closed:'+key);}
}
function historyRange(){
  const end=personnelHistory.end||RT.data.clock.date;
  const start=new Date(end+'T12:00:00Z');start.setUTCDate(start.getUTCDate()-personnelHistory.span+1);
  return {start:start.toISOString().slice(0,10),end};
}
function historyDuration(minutes){
  if(!Number.isFinite(minutes))return '待核实';
  const seconds=Math.max(0,Math.round(minutes*60)),h=Math.floor(seconds/3600),m=Math.floor(seconds%3600/60),s=seconds%60;
  return (h?h+'时':'')+(m||!h?m+'分':'')+(s?s+'秒':'');
}
function historyTime(minutes){
  const s=Math.round(minutes*60),day=Math.floor(s/86400),n=s%86400;
  return (day?`+${day}日 `:'')+[Math.floor(n/3600),Math.floor(n%3600/60),n%60].map(v=>String(v).padStart(2,'0')).join(':');
}
function historyView(){
  // A live refresh can arrive before the browser dispatches its async toggle event.
  document.querySelectorAll('[data-history-day]').forEach(historyRememberDay);
  const H=personnelHistory,rec=H.record,range=historyRange();
  const controls=`<div class="rt-history-heading"><div><h2>人员轮岗</h2><p class="field-hint">岗位 · 休息 · 线路耗时</p></div><div class="rt-history-range"><label>截至<input type="date" data-history-end value="${esc(range.end)}"></label><label>范围<select data-history-span>${[1,7,30].map(n=>`<option value="${n}" ${H.span===n?'selected':''}>${n===1?'当日':'近'+n+'天'}</option>`).join('')}</select></label></div></div><form id="recordForm" class="rt-history-search"><label for="personHistoryQuery">姓名 / 工号</label><div class="rt-history-search-row"><input id="personHistoryQuery" name="no" autocomplete="off" value="${esc(H.query)}" maxlength="100" required aria-controls="personHistoryChoices"><button type="submit" class="primary">${H.loading?'查询中…':'查询'}</button></div><div id="personHistoryChoices" class="rt-history-choices" aria-live="polite"></div></form>`;
  let content=H.loading?'<p role="status">读取记录…</p>':H.error?`<p role="alert">${esc(H.error)}</p>`:'';
  if(rec&&!H.loading&&!H.error){
    const metric=(label,key,samples)=>{
      const value=rec.summary[key],old=rec.priorSummary[key],delta=value!==null&&old!==null?value-old:null;
      return `<div class="rt-history-metric"><span>${label}</span><strong>${value===null?'—':historyDuration(value)}</strong><small>${rec.summary[samples]}次${delta===null?' · 上期无可比记录':` · 较上期 ${delta>=0?'+':'−'}${historyDuration(Math.abs(delta))}`}</small></div>`;
    };
    const daily=new Map(rec.days.map(d=>[d.date,d]));
    const chartDays=[];for(let d=new Date(range.start+'T12:00:00Z');d.toISOString().slice(0,10)<=range.end;d.setUTCDate(d.getUTCDate()+1)){
      const date=d.toISOString().slice(0,10),day=daily.get(date),work=(day?.rows||[]).filter(r=>r.kind!=='post'&&r.beforeWork!==null&&r.beforeWork!==undefined).map(r=>r.beforeWork);
      chartDays.push({date,value:work.length?work.reduce((a,b)=>a+b,0)/work.length:null});
    }
    const max=Math.max(1,...chartDays.map(d=>d.value||0));
    const chart=`<div class="rt-history-trend-heading">每日餐休前在岗 <small>无完整记录显示短线</small></div><div class="rt-history-trend" role="img" aria-label="每日餐休前平均连续在岗时长，空白表示无完整记录">${chartDays.map(d=>`<div class="rt-history-bar" title="${d.date} ${d.value===null?'无完整记录':historyDuration(d.value)}"><i data-history-height="${d.value===null?2:Math.max(3,d.value/max*64)}" class="${d.value===null?'is-missing':''}"></i><small>${esc(d.date.slice(5))}</small></div>`).join('')}</div>`;
    const lines=rec.lines.map(l=>`<div class="rt-history-line"><strong>${esc(l.line)}</strong><span>已记 ${historyDuration(l.minutes)}</span><span>单次 ${l.average===null?'—':historyDuration(l.average)}</span><small>${l.count}段完成${l.average!==null&&l.previousAverage!==null?' · 较上期 '+(l.average-l.previousAverage>=0?'+':'−')+historyDuration(Math.abs(l.average-l.previousAverage)):''}</small></div>`).join('');
    const days=[...rec.days].reverse().filter(d=>d.rows.length||d.fallback.length||d.damaged).map((d,i)=>{
      const key=rec.employee_no+':'+d.date,opened=H.expanded.has(key)||(i===0&&!H.expanded.has('closed:'+key));
      const rows=d.rows.filter(r=>r.end>r.start||r.ongoing).map(r=>`<div class="rt-history-row ${r.kind==='post'?'':'break'}"><strong>${r.kind==='post'?esc(r.line)+' · '+esc(r.post):r.kind==='meal'?'吃饭':'休息'}</strong><time>${r.completeStart?'':'记录起 '}${historyTime(r.start)}–${r.ongoing?'现在':historyTime(r.end)}</time><span>${r.kind==='post'?'本岗':'实休'} ${historyDuration(r.minutes)}${r.ongoing?' · 进行中':''}${r.exceptionWait?' · 含异常等岗':''}</span>${r.kind!=='post'?`<span class="rt-history-before">休息前 ${r.beforeWork===null||r.beforeWork===undefined?'—':historyDuration(r.beforeWork)}</span>`:''}</div>`).join('');
      const visits=d.visits.map(v=>`<span>${esc(v.line)} ${historyTime(v.start)}–${v.ongoing?'现在':historyTime(v.end)} · ${historyDuration(v.minutes)}</span>`).join('');
      const legacy=d.fallback.map(v=>`<div class="rt-history-row"><strong>${esc(v.line)} · 在线记录</strong><time>${historyTime(v.start)}–${historyTime(v.end)}</time><span>${historyDuration(v.minutes)}</span><span>岗位 / 休息未记录</span></div>`).join('');
      return `<details class="rt-history-day" data-history-day="${esc(key)}" ${opened?'open':''}><summary><strong>${esc(d.date)}</strong><span>${d.live?'更新至 '+historyTime(d.asOf):'岗位与餐休'}</span>${d.rewound||d.damaged?'<em>记录待核实</em>':''}</summary><div class="rt-history-visits"><small>单次在线</small>${visits||'<span>—</span>'}</div>${rows}${legacy}</details>`;
    }).join('');
    content=`<div class="rt-history-identity"><strong>${esc(rec.name)}</strong><span>${esc(rec.employee_no)}</span><small>${esc(rec.start)}—${esc(rec.end)} · 已记 ${rec.summary.days} 天</small></div><div class="rt-history-metrics">${metric('餐休前平均在岗','avgWork','workSamples')}${metric('平均实休','avgRest','restSamples')}</div>${chart}<div class="rt-history-lines">${lines}</div><details class="rt-history-basis"><summary>统计口径${rec.summary.uncertain?' · '+rec.summary.uncertain+'条待核实':''}</summary><p class="field-hint">按系统确认计时；实休含等岗，吃饭单列。单次在线遇休息或换线分段。缺失不补算，上期为前一等长周期。</p></details>${days||'<p class="field-hint">此时段暂无轮岗记录</p>'}`;
  }
  return `<section class="panel rt-person-history">${controls}<div data-history-result>${content}</div></section>`;
}
async function historyLoad(no){
  const H=personnelHistory,range=historyRange(),generation=++H.generation;
  H.no=no;H.record=null;H.error='';H.loading=true;
  document.activeElement?.blur();RT.render();
  try{
    const data=await rtApi(`/api/rotation-board/person-insights?employee_no=${encodeURIComponent(no)}&start=${range.start}&end=${range.end}`);
    if(generation!==H.generation)return;
    H.record=data;H.query=data.name;
  }catch(error){if(generation!==H.generation)return;H.error=error.message;}
  finally{if(generation===H.generation){H.loading=false;RT.render();}}
}
function historyBind(){
  const H=personnelHistory,input=document.getElementById('personHistoryQuery'),choices=document.getElementById('personHistoryChoices');
  document.querySelectorAll('[data-history-height]').forEach(bar=>{bar.style.height=Number(bar.dataset.historyHeight)+'px';});
  const chart=document.querySelector('.rt-history-trend');if(chart)chart.scrollLeft=chart.scrollWidth;
  let timer,generation=0;
  const search=async()=>{
    const request=++generation,range=historyRange(),q=input.value.trim();
    if(!q){choices.replaceChildren();return [];}
    try{
      const data=await rtApi(`/api/rotation-board/record-people?q=${encodeURIComponent(q)}&start=${range.start}&end=${range.end}`);
      if(!input.isConnected||request!==generation)return [];
      choices.innerHTML=data.items.map(p=>`<button type="button" data-history-person="${esc(p.pid)}"><strong>${esc(p.name)}</strong><span>${esc(p.pid)}</span></button>`).join('')||'<p class="field-hint">未找到，可输入完整工号查询</p>';
      choices.querySelectorAll('[data-history-person]').forEach(button=>button.onclick=()=>historyLoad(button.dataset.historyPerson));
      return data.items;
    }catch(error){if(input.isConnected&&request===generation)choices.textContent=error.message;return [];}
  };
  input.oninput=()=>{H.query=input.value;H.no='';H.record=null;H.loading=false;++H.generation;document.querySelector('[data-history-result]').replaceChildren();document.querySelector('#recordForm [type=submit]').textContent='查询';++generation;clearTimeout(timer);timer=setTimeout(search,200);};
  document.getElementById('recordForm').onsubmit=async event=>{
    event.preventDefault();clearTimeout(timer);
    const q=input.value.trim();if(!q)return;
    if(H.no){historyLoad(H.no);return;}
    const matches=await search();
    if(!input.isConnected)return;
    if(matches.length===1)historyLoad(matches[0].pid);
    else if(/^\d+$/.test(q))historyLoad(q);
    else if(matches.length>1)choices.insertAdjacentHTML('afterbegin','<p class="field-hint">请选择对应人员</p>');
  };
  document.querySelector('[data-history-end]').onchange=event=>{H.end=event.target.value;if(H.no)historyLoad(H.no);else{H.record=null;document.activeElement?.blur();RT.render();}};
  document.querySelector('[data-history-span]').onchange=event=>{H.span=Number(event.target.value);if(H.no)historyLoad(H.no);else{H.record=null;document.activeElement?.blur();RT.render();}};
  document.querySelectorAll('[data-history-day]').forEach(detail=>detail.ontoggle=()=>historyRememberDay(detail));
}
