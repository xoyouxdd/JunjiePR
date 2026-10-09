// Member scores, attachments and expandable score details.
import { api } from './api.js';
import { beginViewRequest, captureViewContext } from './context.js';
import { attachmentControl, bindFilePreviews } from './files.js';
import { esc, fmt, fmtDeduction, monthNow, prefersReducedMotion } from './format.js';
import { coveredSickDisclosure, splitCoveredSickRecords } from './records.js';
import { app } from './state.js';

function memberScoreRow(row){
  const button=(kind,label,value,scoreClass='')=>`<button type="button" class="member-score-link ${scoreClass}" data-member-detail="${kind}" data-employee-id="${row.employee_id}" aria-expanded="false" title="查看${label}明细">${value}</button>`;
  return `<tr class="member-score-row" data-member-row="${row.employee_id}"><th><strong>${esc(row.employee_name)}</strong><small>${esc(row.employee_no)} · ${esc(row.role_name)}</small></th><td>${button('recognition','加分','+'+fmt(row.recognition_score),'score-positive')}</td><td>${button('deduction','扣分',fmtDeduction(row.deduction_score),'score-negative')}</td><td>${button('attendance','全勤分',fmt(row.attendance_score))}</td><td>${button('all','综合分',fmt(row.total_score),'member-total-link')}</td></tr>`;
}

function memberAttachment(record){
  return attachmentControl(record.attachment_url,record.attachment_name||'查看材料',record.attachment_preview_kind);
}

function memberRecordCard(record){
  const scoreClass=record.record_type==='recognition'?'score-positive':record.record_type==='deduction'?'score-negative':'';
  const includedClass=record.included?'included':'excluded';
  const stateClass=record.status==='rejected'?'is-rejected':record.status==='void'?'is-void':record.status==='pending'?'is-pending':'';
  const score=record.record_type==='sick_leave'?'':`<strong class="member-record-score ${scoreClass} ${!record.included?'not-included':''}">${esc(record.score_text)}</strong>`;
  const reason=record.status==='rejected'&&record.reason?`<div class="member-reject-reason"><strong>不通过原因：</strong>${esc(record.reason)}</div>`:'';
  const employee=record.employee_name?`<span class="member-record-employee">${esc(record.employee_name)}${record.employee_no?` · ${esc(record.employee_no)}`:''}</span>`:'';
  return `<article class="member-detail-card ${stateClass} ${!record.included?'is-excluded':''}"><div class="member-detail-top"><div><span class="member-record-type type-${record.record_type}">${esc(record.record_type_name)}</span>${employee}<strong>${esc(record.business_date)} · ${esc(record.title)}</strong></div>${score}</div><p>${esc(record.content)}</p>${reason}<div class="member-detail-meta"><span class="member-record-status status-${esc(record.status)}">${esc(record.status_name)}</span><span>登记人：${esc(record.operator_name)}</span><span class="member-included ${includedClass}">${record.included?'已计入综合分':'未计入综合分'}</span>${memberAttachment(record)}</div></article>`;
}

function memberScoreDetailHtml(row,kind){
  const titles={recognition:'加分明细',deduction:'扣分明细',attendance:'全勤分明细',all:'全部记录'};
  const all=row.details.all_records||[];
  const records=kind==='all'?all:kind==='attendance'?all.filter(record=>['attendance','sick_leave'].includes(record.record_type)):all.filter(record=>record.record_type===kind);
  const {visible,covered}=splitCoveredSickRecords(records);
  const summary=`<div class="member-detail-summary"><span>加分 <strong class="score-positive">+${fmt(row.recognition_score)}</strong></span><span>扣分 <strong class="score-negative">${fmtDeduction(row.deduction_score)}</strong></span><span>全勤 <strong>${fmt(row.attendance_score)}</strong></span><span>综合 <strong>${fmt(row.total_score)}</strong></span></div>`;
  const attendance=row.details.attendance;
  const calculation=kind==='attendance'&&attendance?`<div class="statistics-attendance-summary"><span>基础分 <strong>${fmt(attendance.base_score)}</strong></span><span>全勤奖励 <strong>${fmt(attendance.perfect_bonus)}</strong></span><span>病假扣减 <strong>${fmtDeduction(attendance.sick_deduction)}</strong></span><span>实际 / 计费病假 <strong>${Number(attendance.actual_sick_days).toFixed(1)} / ${attendance.charged_sick_days}天</strong></span></div>`:'';
  return `<section class="panel member-detail-panel"><div class="statistics-detail-heading"><div><h2>${esc(row.employee_name)} · ${titles[kind]}</h2><span>${esc(row.employee_no)} · ${esc(row.role_name)}</span></div><button type="button" class="secondary" data-close-member-detail>关闭</button></div>${summary}${calculation}<div class="member-detail-list">${visible.map(memberRecordCard).join('')||(!covered.length?'<div class="empty">本月暂无相关记录</div>':'')}</div>${coveredSickDisclosure(covered,`<div class="member-detail-list">${covered.map(memberRecordCard).join('')}</div>`)}</section>`;
}

function bindMemberScoreDetails(data){
  const host=document.getElementById('memberDetail'),rowMap=new Map(data.rows.map(row=>[String(row.employee_id),row]));let activeButton=null;
  const close=()=>{if(activeButton){activeButton.setAttribute('aria-expanded','false');activeButton.closest('tr').classList.remove('is-selected')}activeButton=null;host.innerHTML='';};
  document.querySelectorAll('[data-member-detail]').forEach(button=>button.onclick=()=>{const wasActive=activeButton===button;close();if(wasActive)return;const row=rowMap.get(button.dataset.employeeId);if(!row)return;activeButton=button;button.setAttribute('aria-expanded','true');button.closest('tr').classList.add('is-selected');host.innerHTML=memberScoreDetailHtml(row,button.dataset.memberDetail);bindFilePreviews(host);host.querySelector('[data-close-member-detail]').onclick=close;host.scrollIntoView({behavior:prefersReducedMotion()?'auto':'smooth',block:'nearest'});});
}

async function renderMembers(){
  const request=beginViewRequest();
  app.innerHTML=`<div class="section-gap"><section class="panel"><h2>组员记录</h2><p>每名直属CM/TR一行汇总，按综合分从高到低排列，同分按工号排序。点击加分、扣分、全勤分查看分类明细；点击综合分查看当月所有记录。</p><form id="memberFilter" class="entry-filter member-score-filter"><label>月份<input name="month" type="month" value="${monthNow()}" required></label><label>员工搜索<input name="keyword" placeholder="姓名/员工号"></label><button class="primary">查询</button></form></section><section class="panel"><div id="memberResults"></div></section><div id="memberDetail"></div></div>`;
  const form=document.getElementById('memberFilter');
  const page=captureViewContext(form,{latest:false});
  const load=async()=>{if(!page.isCurrent())return;const request=beginViewRequest();const qs=new URLSearchParams([...new FormData(form)].filter(([,value])=>value));const data=await api('/api/member-score-summary?'+qs);if(!request.isCurrent())return;document.getElementById('memberResults').innerHTML=`<div class="table-wrap member-score-wrap"><table class="member-score-table"><thead><tr><th>员工</th><th>加分</th><th>扣分</th><th>全勤分</th><th>综合分</th></tr></thead><tbody>${data.rows.map(memberScoreRow).join('')||'<tr><td colspan="5" class="empty">无直属CM/TR或无匹配员工</td></tr>'}</tbody></table></div>`;document.getElementById('memberDetail').innerHTML='';bindMemberScoreDetails(data)};
  form.onsubmit=e=>{e.preventDefault();load()};
  await load();
}

export { memberRecordCard, renderMembers };
