// Shared record presentation and withdrawal behavior.
import { api } from './api.js';
import { captureViewContext } from './context.js';
import { confirmModal } from './dialogs.js';
import { attachmentControl, imagePreviewButton } from './files.js';
import { esc, fmtDeduction, recognitionScoreNote, recognitionScoreText } from './format.js';
import { state } from './state.js';
import { toast } from './toast.js';

const opt = (rows, value='id', label='name') => rows.map(x=>`<option value="${esc(x[value])}">${esc(typeof label==='function'?label(x):x[label])}</option>`).join('');

const statusBadge = row => `<span class="badge status-chip ${row.status==='confirmed'||row.status==='issued'||(row.record_type==='sick_leave'&&row.status==='active')?'ok':row.status==='rejected'||row.status==='material_failed'||(row.record_type==='deduction'&&row.status==='active')?'danger':'warn'}">${esc(row.status_name || row.status)}</span>`;

function splitCoveredSickRecords(rows){
  const visible=[],covered=[];
  for(const row of rows)(row.record_type==='sick_leave'&&row.status==='covered'?covered:visible).push(row);
  return {visible,covered};
}

function coveredSickDisclosure(rows,content){
  return rows.length?`<details class="covered-sick-disclosure"><summary>已覆盖病假（${rows.length} 条）<span aria-hidden="true"></span></summary><div class="covered-sick-content">${content}</div></details>`:'';
}

const sameDayDuplicateBadge = row => row.same_day_duplicate?` <span class="badge warn same-day-duplicate" title="同一认可日期、员工、认可类型和认可人已有其他有效记录">${esc(row.same_day_duplicate_label||'今日已有同类登记')}</span>`:'';

function recognitionCard(r,i,readOnly=false){return `<article class="record-card tone-${i%2}"><div class="record-line"><strong>${esc(r.recognition_date.slice(2).replaceAll('-','/'))} · ${esc(r.recognition_type)}</strong><span>${recognitionScoreText(r)}</span></div><div class="record-line"><span>${esc(r.recognizer_name)}：${esc(r.content)} ${r.entry_label?`<em>${esc(r.entry_label)}</em>`:''} ${sameDayDuplicateBadge(r)} ${r.image_url?imagePreviewButton(r.image_url,'查看图片'):''}</span><span class="status-action">${statusBadge(r)}${readOnly?'':`<button class="withdraw-icon" data-withdraw="${r.id}" aria-label="撤回签卡" title="撤回签卡">撤回</button>`}</span></div>${recognitionScoreNote(r)}${r.review_note?`<small>复核说明：${esc(r.review_note)}</small>`:''}</article>`;}

function bindWithdraw(root, done){const page=captureViewContext();root.querySelectorAll('[data-withdraw]').forEach(b=>b.onclick=async()=>{if(!await confirmModal('撤回签卡','<p>撤回后该条记录不再计分，操作记录仅供高级别导出复查，是否撤回？</p>','确认撤回'))return;try{await api('/api/recognitions/'+b.dataset.withdraw,{method:'DELETE'});toast('已撤回');page.refresh(done);}catch(e){toast(e.message,true)}})}

function entryRow(row){
  if(row.record_type==='follow_up'){
    const issued=row.status==='issued'?`<br><small>${esc(row.issued_by_name)} · ${esc(row.issued_at)}</small>`:'';
    return `<tr><td>${esc(row.submitted_at)}</td><td>处分跟进</td><td>${esc(row.employee_name)}<br><small>${esc(row.employee_no)}</small></td><td>${esc(row.occurred_on)}</td><td>${esc(row.deduction_type)} · 三个月内第二次${issued}</td><td>—</td><td>${statusBadge(row)}</td><td></td></tr>`;
  }
  if(row.record_type==='recognition'){
    const evidence=attachmentControl(row.image_url,'图片',row.image_preview_kind);
    return `<tr><td>${esc(row.submitted_at)}</td><td>加分</td><td>${esc(row.employee_name)}<br><small>${esc(row.employee_no)}</small></td><td>${esc(row.recognition_date)}</td><td>${esc(row.recognition_type)} · ${esc(row.recognizer_name)}${sameDayDuplicateBadge(row)}<br>${esc(row.content)}${evidence}${row.monthly_cap_reason?`<br><small>${esc(row.monthly_cap_reason)}</small>`:''}</td><td class="score-positive">+${recognitionScoreText(row)}</td><td>${statusBadge(row)}</td><td>${row.available_actions.includes('withdraw')?`<button data-entry-withdraw="${row.id}" class="secondary">撤回</button>`:''}</td></tr>`;
  }
  if(row.record_type==='sick_leave'){
    const submitterNote=Number(row.submitter_id)===Number(state.me.id)?'':`<br><small>登记人：${esc(row.submitter_name)}</small>`;
    const sourceNote=row.import_source==='monthly_transaction_import'?'<br><small>来源：HR 月度病假事务导入</small>':'';
    return `<tr class="${row.status==='void'?'void-row':''}"><td>${esc(row.submitted_at)}</td><td>缺勤</td><td>${esc(row.employee_name)}<br><small>${esc(row.employee_no)}</small></td><td>${esc(row.leave_start_date)} 至 ${esc(row.leave_end_date)}</td><td>${esc(row.leave_type||'病假')} · ${esc(row.note||'无备注')} ${attachmentControl(row.proof_url,'证明',row.proof_preview_kind)}${sourceNote}${submitterNote}${row.void_reason?`<br><small>作废原因：${esc(row.void_reason)}</small>`:''}</td><td>${Number(row.leave_days).toFixed(1)}天<br><small>计费${row.charged_days}天</small></td><td>${statusBadge(row)}</td><td>${row.available_actions.includes('void')?`<button data-entry-sick-void="${row.id}" class="secondary">作废</button>`:''}</td></tr>`;
  }
  const materialNote=row.status==='pending_material'?'<br><small class="material-inline-state">待补充声明材料，暂不计分。</small>':row.material_status==='processing'?'<br><small class="material-inline-state">材料正在生成PDF，暂不计分。</small>':row.material_status==='failed'?`<br><small class="material-inline-state failed">${esc(row.material_error||'材料生成失败，请重新提交。')}</small>`:'';
  const supplement=row.available_actions.includes('supplement_material')?`<button data-entry-material-retry="${row.id}" class="secondary">补充材料</button>`:'';
  const actions=`${supplement}${row.available_actions.includes('void')?` <button data-entry-void="${row.id}" class="secondary">作废</button>`:''}`;
  const submitterNote=Number(row.submitter_id)===Number(state.me.id)?'':`<br><small>登记人：${esc(row.submitter_name)}</small>`;
  return `<tr class="${row.status==='void'?'void-row':''}"><td>${esc(row.submitted_at)}</td><td>扣分</td><td>${esc(row.employee_name)}<br><small>${esc(row.employee_no)}</small></td><td>${esc(row.occurred_on)}</td><td>${esc(row.deduction_type)} · ${esc(row.deduction_level)}<br>${esc(row.description)} ${attachmentControl(row.document_url,'声明PDF',row.document_preview_kind)}${submitterNote}${materialNote}</td><td class="score-negative">${row.material_status==='processing'||row.material_status==='failed'?'暂不计分':fmtDeduction(row.points)}</td><td>${statusBadge(row)}</td><td>${actions}</td></tr>`;
}

function entryCard(row){
  const person=`<strong>${esc(row.employee_name)}</strong><small>${esc(row.employee_no)}</small>`;
  const chip=statusBadge(row);
  if(row.record_type==='follow_up'){
    return `<article class="work-card"><div class="work-card-head">${person}${chip}</div><p>${esc(row.occurred_on)} · 处分跟进 · 三个月内第二次</p>${row.issued_by_name?`<p>${esc(row.issued_by_name)} · ${esc(row.issued_at||'')}</p>`:''}</article>`;
  }
  if(row.record_type==='recognition'){
    const actions=row.available_actions.includes('withdraw')?`<button data-entry-withdraw="${row.id}" class="secondary">撤回</button>`:'';
    const evidence=row.image_url?attachmentControl(row.image_url,'查看材料',row.image_preview_kind):'';
    return `<article class="work-card"><div class="work-card-head">${person}${chip}</div><p>${esc(row.recognition_date)} · 加分 · ${esc(row.recognition_type)} · ${recognitionScoreText(row)}</p><p>签卡人：${esc(row.recognizer_name)}</p><p>${esc(row.content)}${sameDayDuplicateBadge(row)}${evidence}</p>${recognitionScoreNote(row)}<div class="work-card-actions">${actions}</div></article>`;
  }
  if(row.record_type==='sick_leave'){
    const actions=row.available_actions.includes('void')?`<button data-entry-sick-void="${row.id}" class="secondary">作废</button>`:'';
    const proof=row.proof_url?attachmentControl(row.proof_url,'查看证明',row.proof_preview_kind):'';
    const sourceNote=row.import_source==='monthly_transaction_import'?' · HR月度导入':'';
    return `<article class="work-card ${row.status==='void'?'void-row':''}"><div class="work-card-head">${person}${chip}</div><p>${esc(row.leave_start_date)} 至 ${esc(row.leave_end_date)} · ${esc(row.leave_type||'病假')} · ${Number(row.leave_days).toFixed(1)}天${sourceNote}</p><p>${esc(row.note||'无备注')} ${proof}</p><div class="work-card-actions">${actions}</div></article>`;
  }
  const supplement=row.available_actions.includes('supplement_material')?`<button data-entry-material-retry="${row.id}" class="secondary">补充材料</button>`:'';
  const actions=`${supplement}${row.available_actions.includes('void')?` <button data-entry-void="${row.id}" class="secondary">作废</button>`:''}`;
  const material=row.document_url?attachmentControl(row.document_url,'查看材料',row.document_preview_kind):'';
  const points=row.material_status==='processing'||row.material_status==='failed'?'暂不计分':fmtDeduction(row.points);
  return `<article class="work-card ${row.status==='void'?'void-row':''}"><div class="work-card-head">${person}${chip}</div><p>${esc(row.occurred_on)} · 扣分 · ${esc(row.deduction_type)} · ${points}</p><p>${esc(row.description)} ${material}</p><div class="work-card-actions">${actions}</div></article>`;
}

export { bindWithdraw, coveredSickDisclosure, entryCard, entryRow, opt, recognitionCard, sameDayDuplicateBadge, splitCoveredSickRecords, statusBadge };
