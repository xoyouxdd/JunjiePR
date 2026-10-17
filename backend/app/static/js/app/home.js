// Member home-month selection, score summary and recognition history.
import { api } from './api.js';
import { beginViewRequest, registerPageCleanup } from './context.js';
import { bindImagePreviews } from './files.js';
import { esc, fmt, fmtDeduction, memberHomeMonthLabel, memberHomeMonths, portalPath } from './format.js';
import { bindWithdraw, recognitionCard } from './records.js';
import { app, state } from './state.js';

// 首页"我的信息"：组员显示所在小组与主管/代理主管；主管显示所带小组。
function homeGroupInfo(organization){
  if(organization){
    if(organization.base_role_code==='SUPERVISOR')return '<div><span>月结计分人群</span><strong>主管</strong></div>';
    return `<div><span>月结小组</span><strong>${esc(organization.group_name||'未分组')}</strong>${organization.leader_name?`<small class="field-hint">${esc(organization.leader_name)}</small>`:''}</div>`;
  }
  const me=state.me,led=me.led_groups||[];
  if(me.base_role_code==='SUPERVISOR')return `<div><span>带组</span><strong>${led.length?led.map(group=>esc(group.name)).join('、'):'暂未带组'}</strong></div>`;
  const acting=led.filter(group=>group.leader_type==='acting').map(group=>esc(group.name)).join('、');
  return `<div><span>小组</span><strong>${esc(me.group_name||'未分组')}</strong>${me.group_leader_label?`<small class="field-hint">${esc(me.group_leader_label)}</small>`:''}${acting?`<small class="field-hint">代理主管：${acting}</small>`:''}</div>`;
}

function homeScoreIcon(name){
  const paths={
    '安全':'<path d="M12 3l7 3v6c0 5-7 9-7 9s-7-4-7-9V6l7-3z"/><path d="M9 12l2 2 4-4"/>',
    '礼仪':'<path d="M20 5a5 5 0 0 0-8 1 5 5 0 0 0-8-1c-3 4 1 9 8 15 7-6 11-11 8-15z"/>',
    '包容':'<circle cx="9" cy="7" r="3"/><circle cx="17" cy="8" r="2"/><path d="M3 20v-3a6 6 0 0 1 12 0v3M16 13a5 5 0 0 1 5 5v2"/>',
    '效率':'<path d="M5 20V11M12 20V7M19 20V3"/>',
    '演出':'<path d="M12 3l3 6 6 1-4.5 4.5L18 21l-6-3-6 3 1.5-6.5L3 10l6-1 3-6z"/>',
    '全勤分':'<rect x="4" y="5" width="16" height="16" rx="2"/><path d="M8 3v4M16 3v4M4 10h16M8 15l3 3 5-5"/>',
    '扣分':'<circle cx="12" cy="12" r="9"/><path d="M8 12h8"/>',
    '总分':'<path d="M7 3h10v6a5 5 0 0 1-10 0V3zM12 14v6M8 21h8M7 5H4v3a4 4 0 0 0 4 4M17 5h3v3a4 4 0 0 1-4 4"/>',
  };
  return `<svg class="member-score-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]||''}</svg>`;
}

let homeMonthOptions=[];
let homeMonthOwner=null;
const rankStatement='段位根据当月PR积分在同类人员中的相对分布自动生成，仅供个人参考。段位不代表个人综合工作表现，不构成独立的绩效评价结果。';
const rankCeremonies={
  0:{kind:'dawn',name:'星光初升',layers:['dawn-base','dawn-body','dawn-top']},
  10:{kind:'bronze',name:'铜纹铸印',layers:['quarter-base','quarter-low','quarter-high','quarter-top']},
  20:{kind:'silver',name:'银环扣合',layers:['silver-base','silver-body','silver-left','silver-right','silver-crest']},
  30:{kind:'gold',name:'金纹铸成',layers:['gold-base','gold-body','gold-left','gold-right','gold-crest']},
  40:{kind:'platinum',name:'轻落展翼',layers:['platinum-base','platinum-body','platinum-left','platinum-right','platinum-crest']},
  50:{kind:'diamond',name:'晶徽翻转',layers:['diamond-base','diamond-body','diamond-left','diamond-right','diamond-crest']},
  60:{kind:'star',name:'星核唤醒',layers:['star-base','star-body','star-left','star-right','star-crest']},
  70:{kind:'master',name:'秘印解封',layers:['master-base','master-body','master-left','master-right','master-crest']},
  80:{kind:'grand',name:'绯翼蓄势',layers:['grand-base','grand-body','grand-left','grand-right','grand-crest']},
  90:{kind:'royal',name:'王者加冕',layers:['royal-base','royal-body','royal-left','royal-right','royal-crest']},
};
function rankEmblem(tier){
  const ceremony=rankCeremonies[tier];
  const silver='<svg class="rank-silver-ring" viewBox="0 0 100 100"><path class="rank-ring-left" d="M50 10a40 40 0 0 0 0 80"/><path class="rank-ring-right" d="M50 10a40 40 0 0 1 0 80"/></svg>';
  const forge='<svg class="rank-forge" viewBox="0 0 100 100"><path d="M50 10 80 26 73 66 50 91 27 66 20 26Z"/><path d="M50 24v53M34 36l16 15 16-15"/></svg>';
  const seal='<svg class="rank-seal" viewBox="0 0 100 100"><circle cx="50" cy="50" r="46"/><circle cx="50" cy="50" r="37"/><path d="M50 4v18M50 78v18M4 50h18M78 50h18M17 17l13 13M70 70l13 13M17 83l13-13M70 30l13-13M50 16 80 33v34L50 84 20 67V33Z"/></svg>';
  return `<div class="member-rank-stage" data-ceremony="${tier}" data-kind="${ceremony.kind}" aria-hidden="true"><div class="member-rank-scene"></div><div class="member-rank-plinth"></div><div class="member-rank-pressure"></div><span class="member-rank-sound-cue"></span><span class="member-rank-end-cue"></span>${silver}${forge}${seal}<div class="rank-star-core"></div><div class="rank-charge"></div><div class="rank-facet-light"></div>${ceremony.layers.map((shape,index)=>`<i class="member-rank-layer" data-layer-shape="${shape}" data-step="${index}" data-part="${tier>=20?['base','body','left','right','crest'][index]:'layer'}" data-emblem-tier="${tier}"></i>`).join('')}<div class="member-rank-emblem" data-emblem-tier="${tier}"></div><div class="member-rank-sweep"></div></div>`;
}
function bindRankCeremony(host){
  const hero=host.querySelector('.member-rank-hero');
  if(!hero)return;
  const skip=hero.querySelector('[data-rank-skip]'),replay=hero.querySelector('[data-rank-replay]'),sound=hero.querySelector('[data-rank-sound]');
  const sample=hero.querySelector('[data-rank-sample]'),chime=hero.querySelector('[data-rank-audio]'),audioStatus=hero.querySelector('[data-rank-audio-status]');
  const reduced=window.matchMedia('(prefers-reduced-motion: reduce)');
  let dismissed=false,disposed=false,soundEnabled=false,playSequence=0;
  chime.volume=.45;
  const stopSound=()=>{++playSequence;chime.pause();if(chime.readyState)chime.currentTime=0;if(audioStatus.textContent==='正在播放轻柔音效…')audioStatus.textContent='音效已停止';};
  const playSound=()=>{
    if(disposed)return;
    stopSound();
    const sequence=playSequence;
    audioStatus.textContent='正在播放轻柔音效…';
    chime.play().catch(()=>{if(!disposed&&sequence===playSequence)audioStatus.textContent='未能播放，请点“试听音效”重试，并确认本机声音已开启。';});
  };
  chime.onended=()=>{audioStatus.textContent=soundEnabled?'音效已开启，重播时随动作播放':'试听结束；动画仍保持静音';};
  chime.onerror=()=>{audioStatus.textContent='音效文件未能加载，动画仍可正常查看';};
  const finish=()=>{dismissed=true;stopSound();hero.classList.remove('is-preparing','is-assembling');skip.hidden=true;replay.hidden=false;};
  const cleanup=()=>{disposed=true;finish();};
  const play=()=>{
    finish();
    if(disposed)return;
    if(reduced.matches){if(soundEnabled)playSound();return;}
    // Restart the CSS timeline on this result only; no timers or global listeners.
    void hero.offsetWidth;
    hero.classList.add('is-assembling');skip.hidden=false;replay.hidden=true;
  };
  sound.onclick=()=>{
    soundEnabled=!soundEnabled;sound.setAttribute('aria-pressed',String(soundEnabled));sound.textContent=soundEnabled?'音效：开':'音效：关';
    if(soundEnabled)playSound();else{stopSound();audioStatus.textContent='音效已关闭';}
  };
  sample.onclick=playSound;
  hero.addEventListener('animationstart',event=>{if(event.animationName==='rank-sound-cue'&&soundEnabled)playSound();});
  skip.onclick=()=>{finish();replay.focus({preventScroll:true});};
  replay.onclick=()=>{play();if(!skip.hidden)skip.focus({preventScroll:true});};
  hero.addEventListener('animationcancel',event=>{
    if(event.animationName==='rank-ceremony-end'&&reduced.matches)finish();
  });
  hero.addEventListener('animationend',event=>{
    if(event.animationName==='rank-ceremony-end'){
      const restoreFocus=document.activeElement===skip;
      finish();if(restoreFocus)replay.focus({preventScroll:true});
    }
  });
  if(reduced.matches){finish();return cleanup;}
  hero.classList.add('is-preparing');skip.hidden=false;replay.hidden=true;
  const artwork=new Image();
  artwork.src=getComputedStyle(hero.querySelector('.member-rank-emblem')).backgroundImage.slice(5,-2);
  artwork.decode().then(()=>{if(!disposed&&!dismissed&&hero.isConnected&&!host.hidden)play();}).catch(finish);
  return cleanup;
}
function homeRankContent(rank,selectedMonth,d){
  const ranked=rank.status==='ranked'&&rank.month===selectedMonth&&rank.month_closed===true&&Number.isInteger(rank.tier)&&[0,10,20,30,40,50,60,70,80,90].includes(rank.tier);
  const waiting=['pending','waiting_global','reopened'].includes(rank.status);
  const notice=rank.status==='ranked'&&!ranked?'所选月份暂无可用的游戏段位，请重新查看。':rank.message;
  const rules=`<details class="member-rank-rules"><summary>了解月度勋章</summary><p>${rankStatement}</p><p>沿用PR计分人群：CM/TR及代理TA主管按月结景点圈比较；主管及代理TA GSM从2026年10月起按全部参与圈比较，各圈全部月结后生成。同分人员按同分组在积分分布中的中位位置获得相同段位，不强制分配各档人数；比较人数不足10人或全员同分时不生成段位。月结后封存，重开期间暂不展示。</p><p>仅本人主动查看，不公开排行榜，不新增奖惩或绩效评定依据。</p>${rank.rule_version?`<small>计算规则：${esc(rank.rule_version)}</small>`:''}</details>`;
  const content=ranked?`<div class="member-rank-hero" aria-label="${esc(rank.label)}游戏段位"><span class="member-rank-month">${esc(memberHomeMonthLabel(rank.month))} · 月度PR积分</span>${rankEmblem(rank.tier)}<div class="member-rank-copy"><h3>${esc(rank.label)}</h3><p class="member-rank-band">月度PR积分 <strong>${fmt(d.total_score)}</strong></p><section class="member-rank-review" aria-label="本月寄语"><h4>${homeScoreIcon('演出')}本月寄语</h4><p class="member-rank-message">${esc(rank.message)}</p></section></div><div class="member-rank-motion"><button type="button" data-rank-skip hidden>跳过动画</button><button type="button" data-rank-replay>重播动画</button><button type="button" data-rank-sound aria-pressed="false" title="点击开启并试听，重播动画时同步播放">音效：关</button><button type="button" data-rank-sample>试听音效</button></div><p class="member-rank-audio-status" data-rank-audio-status role="status"></p><audio data-rank-audio preload="none" src="${esc(portalPath('/static/audio/medal-soft-seal.wav'))}"></audio></div>`:waiting?`<div class="member-rank-awaiting-hero"><div class="member-rank-lock" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/></svg></div><h3>${rank.status==='reopened'?'段位重新核算中':rank.status==='waiting_global'?'等待全圈月结':'月结后可查看'}</h3><p class="member-rank-empty">${esc(notice)}</p></div>`:`<p class="member-rank-empty">${esc(notice||'本月暂不生成游戏段位。')}</p>`;
  return {ranked,waiting,html:content+`<p class="member-rank-reference">个人积分参考 · 仅本人可见</p>`+rules};
}
function homeMonthControl(selectedMonth){
  const current=memberHomeMonths()[0],years=new Map();
  for(const option of homeMonthOptions){
    const year=option.month.slice(0,4);
    if(!years.has(year))years.set(year,[]);
    years.get(year).push(`<option value="${esc(option.month)}"${option.month===selectedMonth?' selected':''}>${esc(memberHomeMonthLabel(option.month))}${option.month===current?'（本月）':''}${option.status_label?` · ${esc(option.status_label)}`:''}</option>`);
  }
  return `<label class="home-month-control">数据月份<select id="homeMonthSelect" aria-label="查看绩效月份">${[...years].map(([year,options])=>`<optgroup label="${esc(year)}年">${options.join('')}</optgroup>`).join('')}</select></label>`;
}
function bindHomeMonth(){
  document.getElementById('homeMonthSelect').onchange=event=>{state.homeMonth=event.target.value;renderHome();};
}
function homeQueryState(request,selectedMonth,error){
  const message=error?`查询${memberHomeMonthLabel(selectedMonth)}失败：${error}`:`正在查询${memberHomeMonthLabel(selectedMonth)}…`;
  request.write(`<section class="panel member-home-panel"><div class="home-info-heading"><h2>我的信息</h2>${homeMonthControl(selectedMonth)}</div><div class="${error?'error':'empty'}" ${error?'role="alert"':'role="status" aria-busy="true"'}>${esc(message)}</div>${error?'<button class="secondary home-query-retry" type="button" id="homeQueryRetry">重试</button>':''}</section>`);
  bindHomeMonth();
  if(error)document.getElementById('homeQueryRetry').onclick=()=>renderHome();
}

let homeRankCleanup=null;
async function renderHome(){
  homeRankCleanup?.();homeRankCleanup=null;
  const request=beginViewRequest();
  const currentMonth=memberHomeMonths()[0];
  if(homeMonthOwner!==state.me.id){
    homeMonthOwner=state.me.id;
    homeMonthOptions=memberHomeMonths().map(month=>({month,status_label:''}));
  }
  const selectedMonth=homeMonthOptions.some(option=>option.month===state.homeMonth)?state.homeMonth:currentMonth;
  const isHistoricalMonth=selectedMonth!==currentMonth;
  state.homeMonth=selectedMonth;
  const selectedMonthLabel=memberHomeMonthLabel(selectedMonth);
  const monthNumber=Number(selectedMonth.slice(5));
  const season=monthNumber>=3&&monthNumber<=5?'spring':monthNumber>=6&&monthNumber<=8?'summer':monthNumber>=9&&monthNumber<=11?'autumn':'winter';
  homeQueryState(request,selectedMonth);
  try{
  const d=await api('/api/dashboard?month='+encodeURIComponent(selectedMonth));
  if(!request.isCurrent())return;
  if(d.month&&d.month!==selectedMonth)throw new Error('返回的数据月份不一致，请重试。');
  if(Array.isArray(d.query_months)){
    const options=d.query_months.filter(option=>/^\d{4}-\d{2}$/.test(option.month)&&option.month<=currentMonth);
    if(options.some(option=>option.month===selectedMonth))homeMonthOptions=options;
  }
  const organization=d.month_organization;
  const showScoreExplainer=!['CM','TR','TA_SUPERVISOR'].includes(state.me.base_role_code||state.me.role_code);
  const cats=['安全','礼仪','包容','效率','演出'];
  const homeReadOnly=isHistoricalMonth||Boolean(d.month_closed);
  const monthStatus=homeMonthOptions.find(option=>option.month===selectedMonth)?.status_label||(d.month_closed?'已月结':'待月结');
  const summary=d.recognition_summary||{
    credited:(d.records||[]).filter(r=>r.status==='confirmed'&&Number(r.credited_fraction??r.fraction)>0).length,
    pending:(d.records||[]).filter(r=>r.status==='pending').length,
    uncredited:(d.records||[]).filter(r=>r.status==='confirmed'&&Number(r.credited_fraction??r.fraction)<=0).length,
    rejected:(d.records||[]).filter(r=>r.status==='rejected').length,
  };
  const summaryMarkup=`<p class="home-recognition-summary" aria-label="${esc(selectedMonthLabel)}签卡汇总"><span>已计分<strong>${esc(summary.credited)}</strong>张</span>${summary.pending?`<span class="home-recognition-pending">待复核<strong>${esc(summary.pending)}</strong>张</span>`:''}${summary.uncredited?`<span>已确认未计分<strong>${esc(summary.uncredited)}</strong>张</span>`:''}${summary.rejected?`<span>不通过<strong>${esc(summary.rejected)}</strong>张</span>`:''}</p>`;
  const rankMarkup=d.monthly_rank_available===true?'<div class="member-rank-entry"><button type="button" class="secondary" id="homeRankView" aria-expanded="false" aria-controls="homeRankContent">查看我的月度勋章</button><div id="homeRankContent" hidden></div></div>':'';
  if(!request.write(`<div class="section-gap">
    <section class="panel member-home-panel" data-season="${season}"><div class="home-info-heading"><h2>我的信息</h2>${homeMonthControl(selectedMonth)}<span class="badge${d.month_closed?' member-month-closed':''}">${esc(monthStatus)}</span>${isHistoricalMonth?'<span class="badge warn home-read-only">仅可查看</span>':''}</div>${rankMarkup}<div class="member-info-grid">
      <div><span>姓名 / ${organization?'月结角色':'当前角色'}</span><strong>${esc(state.me.name)} · ${esc(organization?.role_label||state.me.role_label||state.me.role_name)}</strong></div>
      <div><span>${organization?'月结景点圈':'当前景点圈'}</span><strong>${esc(organization?.attraction_name||state.me.attraction_name||'未分配')}</strong></div>
      ${homeGroupInfo(organization)}
      <div class="member-score-summary"><div class="member-score-items">
        ${cats.map(x=>`<div class="member-score-item"><span>${homeScoreIcon(x)}${x}</span><strong>${fmt(d.category_scores?.[x])}</strong></div>`).join('')}
        <div class="member-score-item"><span>${homeScoreIcon('全勤分')}全勤分</span><strong>${fmt(d.attendance_score)}</strong></div>
        <div class="member-score-item"><span>${homeScoreIcon('扣分')}扣分</span><strong>${fmtDeduction(d.deduction_score)}</strong></div>
        <div class="member-score-item member-score-total"><span>${homeScoreIcon('总分')}${isHistoricalMonth?'当月总分':'本月总分'}</span><strong>${fmt(d.total_score)}</strong></div>
      </div></div>
    </div>${showScoreExplainer?`<details class="score-explainer"><summary>${isHistoricalMonth?`${selectedMonthLabel}绩效分怎么算的？`:'本月总分怎么算的？'}</summary><ul><li>加分：认可人签卡确认后，按认可人当日角色分值计入对应类别（安全/礼仪/包容/效率/演出等）。</li><li>全勤分：基础分 10 分；当月无病假满勤再加 2 分；病假按天扣减（0.5 天扣 0.25 分），最低 0 分。</li><li>扣分：声明/备忘录/警告等扣分记录按等级计分，作废后不再计入。</li><li>本月总分 = 加分 + 全勤分 − 扣分（仅统计已确认的有效记录）。</li></ul></details>`:''}</section>
    <section class="panel"><h2>我的签卡${isHistoricalMonth?` · ${selectedMonthLabel}`:''}</h2>${summaryMarkup}<div class="mobile-records">${(d.records||[]).length?d.records.map((r,i)=>recognitionCard(r,i,homeReadOnly)).join(''):`<div class="empty">${isHistoricalMonth?`${selectedMonthLabel}暂无签卡`:'本月暂无签卡'}</div>`}</div></section>
  </div>`))return;
  bindHomeMonth();
  const rankButton=document.getElementById('homeRankView');
  if(rankButton)rankButton.onclick=async()=>{
    const host=document.getElementById('homeRankContent'),panel=host.closest('.member-home-panel');
    if(rankButton.getAttribute('aria-expanded')==='true'){
      homeRankCleanup?.();homeRankCleanup=null;
      host.hidden=true;rankButton.setAttribute('aria-expanded','false');rankButton.textContent='查看我的月度勋章';
      host.querySelector('.member-rank-hero')?.classList.remove('is-preparing','is-assembling');
      panel.classList.remove('member-rank-panel','member-rank-awaiting');panel.removeAttribute('data-rank-tier');return;
    }
    rankButton.disabled=true;host.hidden=false;host.innerHTML='<p class="empty" role="status" aria-busy="true">正在查询本人的月度勋章…</p>';
    try{
      const rank=await api('/api/self-month-rank?month='+encodeURIComponent(selectedMonth));
      if(!request.isCurrent())return;
      if(rank.month!==selectedMonth)throw new Error('返回的数据月份不一致，请重试。');
      const view=homeRankContent(rank,selectedMonth,d);
      host.innerHTML=view.html;panel.classList.toggle('member-rank-panel',view.ranked);panel.classList.toggle('member-rank-awaiting',view.waiting);
      if(view.ranked)panel.dataset.rankTier=rank.tier;
      if(view.ranked){homeRankCleanup=bindRankCeremony(host);registerPageCleanup(homeRankCleanup);}
      rankButton.setAttribute('aria-expanded','true');rankButton.textContent='收起我的月度勋章';
    }catch(error){if(request.isCurrent())host.innerHTML=`<p class="error" role="alert">查看失败：${esc(error.message)}，可再次点击重试。</p>`;}
    finally{if(rankButton.isConnected)rankButton.disabled=false;}
  };
  bindImagePreviews(app);
  bindWithdraw(app, ()=>renderHome());
  }catch(error){
    if(!request.isCurrent()||error?.name==='AbortError')return;
    homeQueryState(request,selectedMonth,error.message||'请求失败，请重试。');
  }
}

export { renderHome };
