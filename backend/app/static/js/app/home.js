// Member home-month selection, score summary and recognition history.
import { api } from './api.js';
import { beginViewRequest } from './context.js';
import { bindImagePreviews } from './files.js';
import { esc, fmt, fmtDeduction, memberHomeMonthLabel, memberHomeMonths } from './format.js';
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

async function renderHome(){
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
  const rank=d.month_rank||{status:'pending',message:'月结后展示本景点圈的个人段位。'};
  const homeReadOnly=isHistoricalMonth||Boolean(rank.month_closed);
  const ranked=rank.status==='ranked'&&rank.month===selectedMonth&&rank.month_closed===true&&Number.isInteger(rank.tier)&&[0,10,20,30,40,50,60,70,80,90].includes(rank.tier);
  const waitingRank=['pending','waiting_global','reopened'].includes(rank.status);
  const notice=rank.status==='ranked'&&!ranked?'所选月份暂无已月结段位，请在月份选择中切换查看。':rank.message;
  const monthStatus=rank.status==='waiting_global'?'本圈已月结 · 段位待汇总':rank.status==='reopened'?'重新核算中':homeMonthOptions.find(option=>option.month===selectedMonth)?.status_label||(rank.month_closed?'已月结':'待月结');
  const rankScope=rank.comparison_label||rank.attraction_name||'本景点圈';
  const rankBand=rank.tier===0?'未超过10%':`超过${rank.tier}%`;
  const summary=d.recognition_summary||{
    credited:(d.records||[]).filter(r=>r.status==='confirmed'&&Number(r.credited_fraction??r.fraction)>0&&rank.status!=='excluded').length,
    pending:(d.records||[]).filter(r=>r.status==='pending').length,
    uncredited:(d.records||[]).filter(r=>r.status==='confirmed'&&(Number(r.credited_fraction??r.fraction)<=0||rank.status==='excluded')).length,
    rejected:(d.records||[]).filter(r=>r.status==='rejected').length,
  };
  const summaryMarkup=`<p class="home-recognition-summary" aria-label="${esc(selectedMonthLabel)}签卡汇总"><span>已计分<strong>${esc(summary.credited)}</strong>张</span>${summary.pending?`<span class="home-recognition-pending">待复核<strong>${esc(summary.pending)}</strong>张</span>`:''}${summary.uncredited?`<span>已确认未计分<strong>${esc(summary.uncredited)}</strong>张</span>`:''}${summary.rejected?`<span>不通过<strong>${esc(summary.rejected)}</strong>张</span>`:''}</p>`;
  const rankMarkup=ranked?`<div class="member-rank-hero" aria-label="${esc(rank.label)}月结段位"><span class="member-rank-month">${esc(memberHomeMonthLabel(rank.month))} · 月结荣誉</span><div class="member-rank-emblem" data-emblem-tier="${rank.tier}" aria-hidden="true"></div><div class="member-rank-copy"><h3>${esc(rank.label)}</h3><p class="member-rank-band">你的分数${rankBand}的${esc(rank.population_label)}</p><section class="member-rank-review" aria-label="本月评语"><h4>${homeScoreIcon('演出')}本月评语</h4><p class="member-rank-message">${esc(rank.message)}</p></section></div></div>`:waitingRank?`<div class="member-rank-awaiting-hero"><div class="member-rank-lock" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/></svg></div><h3>${rank.status==='reopened'?'段位重新核算中':rank.status==='waiting_global'?'等待全圈月结':'月结后解锁'}</h3><p class="member-rank-empty">${esc(notice)}</p></div>`:`<p class="member-rank-empty">${esc(notice||'该月暂无可用的月结段位数据。')}</p>`;
  if(!request.write(`<div class="section-gap">
    <section class="panel member-home-panel${ranked?' member-rank-panel':waitingRank?' member-rank-awaiting':''}" data-season="${season}"${ranked?` data-rank-tier="${rank.tier}"`:''}><div class="home-info-heading"><h2>我的信息</h2>${homeMonthControl(selectedMonth)}<span class="badge${rank.month_closed?' member-month-closed':''}">${esc(monthStatus)}</span>${isHistoricalMonth?'<span class="badge warn home-read-only">仅可查看</span>':''}</div>${rankMarkup}<div class="member-info-grid">
      <div><span>姓名 / ${organization?'月结角色':'当前角色'}</span><strong>${esc(state.me.name)} · ${esc(organization?.role_label||state.me.role_label||state.me.role_name)}</strong></div>
      <div><span>${organization?'月结景点圈':'当前景点圈'}</span><strong>${esc(organization?.attraction_name||state.me.attraction_name||'未分配')}</strong></div>
      ${homeGroupInfo(organization)}
      <div class="member-score-summary"><div class="member-score-items">
        ${cats.map(x=>`<div class="member-score-item"><span>${homeScoreIcon(x)}${x}</span><strong>${fmt(d.category_scores?.[x])}</strong></div>`).join('')}
        <div class="member-score-item"><span>${homeScoreIcon('全勤分')}全勤分</span><strong>${fmt(d.attendance_score)}</strong></div>
        <div class="member-score-item"><span>${homeScoreIcon('扣分')}扣分</span><strong>${fmtDeduction(d.deduction_score)}</strong></div>
        <div class="member-score-item member-score-total"><span>${homeScoreIcon('总分')}${isHistoricalMonth?'当月总分':'本月总分'}</span><strong>${fmt(d.total_score)}</strong></div>
      </div></div>
    </div>${ranked?`<p class="member-rank-scope">比较范围：${esc(memberHomeMonthLabel(selectedMonth))} · ${esc(rankScope)} · ${esc(rank.population_label)}</p>`:''}${showScoreExplainer?`<details class="score-explainer"><summary>${isHistoricalMonth?`${selectedMonthLabel}绩效分怎么算的？`:'本月总分怎么算的？'}</summary><ul><li>加分：认可人签卡确认后，按认可人当日角色分值计入对应类别（安全/礼仪/包容/效率/演出等）。</li><li>全勤分：基础分 10 分；当月无病假满勤再加 2 分；病假按天扣减（0.5 天扣 0.25 分），最低 0 分。</li><li>扣分：声明/备忘录/警告等扣分记录按等级计分，作废后不再计入。</li><li>本月总分 = 加分 + 全勤分 − 扣分（仅统计已确认的有效记录）。</li><li>段位：CM/TR及代理TA主管按本景点圈比较；主管及代理TA GSM从2026年10月起，待参与主管计分的各圈全部月结后统一比较。同分不算超过，本人不进入比较分母。重开月结期间重新核算。</li></ul></details>`:''}</section>
    <section class="panel"><h2>我的签卡${isHistoricalMonth?` · ${selectedMonthLabel}`:''}</h2>${summaryMarkup}<div class="mobile-records">${(d.records||[]).length?d.records.map((r,i)=>recognitionCard(r,i,homeReadOnly)).join(''):`<div class="empty">${isHistoricalMonth?`${selectedMonthLabel}暂无签卡`:'本月暂无签卡'}</div>`}</div></section>
  </div>`))return;
  bindHomeMonth();
  bindImagePreviews(app);
  bindWithdraw(app, ()=>renderHome());
  }catch(error){
    if(!request.isCurrent()||error?.name==='AbortError')return;
    homeQueryState(request,selectedMonth,error.message||'请求失败，请重试。');
  }
}

export { renderHome };
