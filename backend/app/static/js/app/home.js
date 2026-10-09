// Member home-month selection, score summary and recognition history.
import { api } from './api.js';
import { beginViewRequest } from './context.js';
import { bindImagePreviews } from './files.js';
import { esc, fmt, fmtDeduction, memberHomeMonthLabel, memberHomeMonths } from './format.js';
import { bindWithdraw, recognitionCard } from './records.js';
import { app, state } from './state.js';

// 首页"我的信息"：组员显示所在小组与主管/代理主管；主管显示所带小组。
function homeGroupInfo(){
  const me=state.me,led=me.led_groups||[];
  if(me.base_role_code==='SUPERVISOR')return `<div><span>带组</span><strong>${led.length?led.map(group=>esc(group.name)).join('、'):'暂未带组'}</strong></div>`;
  const acting=led.filter(group=>group.leader_type==='acting').map(group=>esc(group.name)).join('、');
  return `<div><span>小组</span><strong>${esc(me.group_name||'未分组')}</strong>${me.group_leader_label?`<small class="field-hint">${esc(me.group_leader_label)}</small>`:''}${acting?`<small class="field-hint">代理主管：${acting}</small>`:''}</div>`;
}

async function renderHome(){
  const request=beginViewRequest();
  const visibleMonths=memberHomeMonths();
  const selectedMonth=visibleMonths.includes(state.homeMonth)?state.homeMonth:visibleMonths[0];
  const isHistoricalMonth=selectedMonth!==visibleMonths[0];
  state.homeMonth=selectedMonth;
  const selectedMonthLabel=memberHomeMonthLabel(selectedMonth);
  const d=await api('/api/dashboard?month='+encodeURIComponent(selectedMonth));
  if(!request.isCurrent())return;
  const cats=['安全','礼仪','包容','效率','演出'];
  if(!request.write(`<div class="section-gap">
    <section class="panel"><div class="home-info-heading"><h2>我的信息</h2><label class="home-month-control">数据月份<select id="homeMonthSelect" aria-label="查看绩效月份">${visibleMonths.map((month,index)=>`<option value="${month}" ${month===selectedMonth?'selected':''}>${memberHomeMonthLabel(month)}${index===0?'（本月）':''}</option>`).join('')}</select></label>${isHistoricalMonth?'<span class="badge warn home-read-only">仅可查看</span>':''}</div><div class="member-info-grid">
      <div><span>姓名 / 角色</span><strong>${esc(state.me.name)} · ${esc(state.me.role_label||state.me.role_name)}</strong></div>
      <div><span>景点圈</span><strong>${esc(state.me.attraction_name||'未分配')}</strong></div>
      ${homeGroupInfo()}
      <div class="member-score-summary"><div class="member-score-items">
        ${cats.map(x=>`<div class="member-score-item"><span>${x}</span><strong>${fmt(d.category_scores?.[x])}</strong></div>`).join('')}
        <div class="member-score-item"><span>全勤分</span><strong>${fmt(d.attendance_score)}</strong></div>
        <div class="member-score-item"><span>扣分</span><strong>${fmtDeduction(d.deduction_score)}</strong></div>
        <div class="member-score-item member-score-total"><span>${isHistoricalMonth?'当月总分':'本月总分'}</span><strong>${fmt(d.total_score)}</strong></div>
      </div></div>
    </div><details class="score-explainer"><summary>${isHistoricalMonth?`${selectedMonthLabel}绩效分怎么算的？`:'本月总分怎么算的？'}</summary><ul><li>加分：认可人签卡确认后，按认可人当日角色分值计入对应类别（安全/礼仪/包容/效率/演出等）。</li><li>全勤分：基础分 10 分；当月无病假满勤再加 2 分；病假按天扣减（0.5 天扣 0.25 分），最低 0 分。</li><li>扣分：声明/备忘录/警告等扣分记录按等级计分，作废后不再计入。</li><li>本月总分 = 加分 + 全勤分 − 扣分（仅统计已确认的有效记录）。</li></ul></details></section>
    <section class="panel"><h2>我的签卡${isHistoricalMonth?` · ${selectedMonthLabel}`:''}</h2><div class="mobile-records">${(d.records||[]).length?d.records.map((r,i)=>recognitionCard(r,i,isHistoricalMonth)).join(''):`<div class="empty">${isHistoricalMonth?`${selectedMonthLabel}暂无签卡`:'本月暂无签卡'}</div>`}</div></section>
  </div>`))return;
  document.getElementById('homeMonthSelect').onchange=event=>{state.homeMonth=event.target.value;renderHome();};
  bindImagePreviews(app);
  bindWithdraw(app, ()=>renderHome());
}

export { renderHome };
