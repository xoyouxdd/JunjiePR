import { api, json } from './api.js';
import { confirmModal, formModal } from './dialogs.js';
import { esc, fmt } from './format.js';
import { gotoTab } from './navigation.js';
import { has, state } from './state.js';

const eventStatuses={
  active:'已生效', pending_material:'待补材料', material_processing:'材料生成中',
  material_failed:'材料生成失败', pending_upgrade:'审核中', void:'已作废',
};

function openOriginalRecord(row){
  state.declarationRecordFocus={id:Number(row.id),month:row.month};
  gotoTab('declarationStatistics');
}

function eventRecordList(rows, withLinks=false){
  return `<ul class="deduction-event-matches">${rows.map(row=>`<li>
    <div class="deduction-event-match-heading">
      <strong>原单 #${esc(row.id)}</strong>
      <time datetime="${esc(row.incident_on)}">${esc(row.incident_on)}</time>
    </div>
    <div class="deduction-event-match-meta">
      <span>${esc(row.type_name)} · ${esc(row.level_name)}</span>
      <span class="badge ${row.status==='active'?'ok':row.status==='void'?'danger':'warn'}">${esc(eventStatuses[row.status]||row.status)}</span>
    </div>
    <p>${esc(row.description)}</p>
    ${withLinks?`<button type="button" class="primary deduction-event-view" aria-label="查看原单 #${esc(row.id)}" data-open-deduction-event="${esc(row.id)}">查看原单 <span aria-hidden="true">›</span></button>`:''}
  </li>`).join('')}</ul>`;
}

export async function showExistingDeductionEvent(row,page){
  const open=await confirmModal('同一原单已登记',`
    <p class="deduction-event-blocked">本次登记已拦截。请打开原单处理。</p>
    ${eventRecordList([row])}
  `,'查看原单');
  if(open&&page.isCurrent())openOriginalRecord(row);
}

export async function checkDeductionEvent(data,page){
  const reference=String(data.get('original_statement_id')||'').trim().replace(/^#/,'');
  if(reference)data.set('original_statement_id',reference);
  else data.delete('original_statement_id');
  if(!has('DECLARATION_STATS_VIEW'))return true;
  const query=new URLSearchParams({
    employee_id:data.get('employee_id'), occurred_on:data.get('occurred_on'),
  });
  if(reference)query.set('original_statement_id',reference);
  const found=await api('/api/deductions/event-check?'+query);
  if(!page.isCurrent())return false;
  if(found.same_original){
    await showExistingDeductionEvent(found.records[0],page);
    return false;
  }
  if(found.confirmation)data.set('event_confirmation',found.confirmation);
  if(!found.records.length)return true;
  return formModal('可能是同一起事件','该员工当天已有登记，请先查看原单。',`
    ${eventRecordList(found.records,true)}
    <details class="deduction-event-override">
      <summary>确认为不同事件</summary>
      <label>不同事件的说明<textarea name="different_event_reason" required maxlength="500" rows="2" disabled></textarea></label>
    </details>
  `,'确认是不同事件，继续登记',{
    onReady:(form,close)=>{
      const override=form.querySelector('.deduction-event-override');
      const submit=form.querySelector('[type=submit]');
      const setOverrideVisibility=()=>{
        submit.hidden=!override.open;
        form.elements.different_event_reason.disabled=!override.open;
      };
      setOverrideVisibility();
      override.addEventListener('toggle',setOverrideVisibility);
      form.querySelectorAll('[data-open-deduction-event]').forEach(button=>button.onclick=()=>{
        const row=found.records.find(item=>item.id===Number(button.dataset.openDeductionEvent));
        close();
        if(row&&page.isCurrent())openOriginalRecord(row);
      });
    },
    onSubmit:form=>{
      const reason=form.elements.different_event_reason.value.trim();
      if(!form.querySelector('.deduction-event-override').open||!reason)throw new Error('请填写不同事件的说明');
      data.set('different_event_reason',reason);
    },
  });
}

export async function openOriginalUpgrade(recordId,isCurrent){
  const path='/api/deductions/'+recordId+'/original-upgrade';
  const preview=await api(path);
  if(!isCurrent())return false;
  const options=preview.levels.map(level=>`<option value="${level.id}">${esc(level.name)}</option>`).join('');
  if(!options)throw new Error('当前没有可选的高级处分等级');
  const previous=preview.record.month;
  return formModal(`原单升级 · #${recordId}`,'MOD 线下确认后，请按实际开具的处分填写。',`
    <div class="original-upgrade-dates">
      <span>原事件日 <time datetime="${esc(preview.record.incident_on)}">${esc(preview.record.incident_on)}</time></span>
      <span>生效日 <time datetime="${esc(preview.effective_on)}">${esc(preview.effective_on)}</time></span>
    </div>
    <label>实际处分级别<select name="level_id" required><option value="">请选择</option>${options}</select></label>
    <div class="original-upgrade-summary">
      <div class="original-upgrade-comparison">
        <div>
          <span>当前 · ${esc(previous)}</span>
          <strong>${esc(preview.record.level_name)}</strong>
          <span>实际扣分 <b>${fmt(preview.original_points)}</b> 分</span>
        </div>
        <div class="original-upgrade-result">
          <span>升级后 · ${esc(preview.effective_month)}</span>
          <strong data-upgrade-level>待选择等级</strong>
          <span>实际扣分 <b data-upgrade-points>—</b> 分</span>
        </div>
      </div>
      <p data-original-upgrade-score aria-live="polite">选择等级后查看扣分变化。</p>
    </div>
    <div class="grid two original-upgrade-confirmation">
      <label>MOD 确认人<input name="mod_name" maxlength="100" required></label>
      <label>MOD 确认日期<input name="mod_confirmed_on" type="date" min="${esc(preview.record.incident_on)}" max="${esc(preview.effective_on)}" required></label>
    </div>
    <label>处理说明<textarea name="reason" maxlength="500" required rows="2"></textarea></label>
  `,'确认原单升级',{
    lockWhileSubmitting:true,
    onReady:form=>{
      const select=form.elements.level_id,score=form.querySelector('[data-original-upgrade-score]');
      const level=form.querySelector('[data-upgrade-level]'),points=form.querySelector('[data-upgrade-points]');
      select.onchange=()=>{
        const option=preview.levels.find(level=>level.id===Number(select.value));
        level.textContent=option?.name||'待选择等级';
        points.textContent=option?fmt(option.actual_points):'—';
        if(!option){score.textContent='选择等级后查看扣分变化。';return;}
        const result=preview.loa_excluded
          ?`${preview.effective_month} 按 LOA 规则不计分。`
          :`${preview.effective_month} 按升级后的处分计一次分。`;
        score.textContent=previous===preview.effective_month?result:`${previous} 留档、不计分，${result}`;
      };
    },
    onSubmit:async form=>{
      if(!isCurrent())throw new Error('页面已变化，请重新打开原单');
      const fields=form.elements;
      if(!form.reportValidity()||!fields.mod_name.value.trim()||!fields.reason.value.trim())throw new Error('请完整填写处分级别、MOD 确认信息和说明');
      await api(path,json('POST',{
        level_id:Number(fields.level_id.value),confirmation:preview.confirmation,
        mod_name:fields.mod_name.value.trim(),mod_confirmed_on:fields.mod_confirmed_on.value,reason:fields.reason.value.trim(),
      }));
    },
  });
}
