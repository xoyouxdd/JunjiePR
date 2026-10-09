// Sick-leave entry, overlap checks, imports, single-entry fallback and LOA records.
import { api, json } from './api.js';
import { beginViewRequest, captureViewContext } from './context.js';
import { bindDeductionMaterialPicker, deductionMaterialPickerMarkup } from './deduction-materials.js';
import { confirmModal } from './dialogs.js';
import { bindEmployeeSearches, employeePicker, requireEmployeeSelection } from './employee-picker.js';
import { esc, fmt, monthNow, portalPath, today } from './format.js';
import { coveredSickDisclosure, splitCoveredSickRecords, statusBadge } from './records.js';
import { app, has } from './state.js';
import { clearSubmissionKey, submissionData, withSubmitLock } from './submission.js';
import { toast } from './toast.js';

function sickForm(){return `<section class="panel"><h2>缺勤登记</h2><p>登记CM/TR病假缺勤；支持0.5天，半天扣0.25分。同一员工已有日期交集的缺勤记录时，不能再次提交。</p><form id="sickForm" class="form-stack" enctype="multipart/form-data">${employeePicker('缺勤员工（必选）','sickEmployee','attendance')}<div class="grid two"><label>开始日期<input name="leave_start_date" type="date" value="${today()}" required></label><label>结束日期<input name="leave_end_date" type="date" value="${today()}" required></label></div><p id="sickDateError" class="field-error" aria-live="polite" hidden></p><label>缺勤天数<input name="leave_days" type="number" step="0.5" min="0.5" max="1" value="1" required><span class="field-hint">可以按0.5天调整，但不能超过所选日期范围。</span></label><label>缺勤证明（图片/PDF）<input name="proof" type="file" accept="image/*,.pdf" required><span class="field-hint">必传；支持 JPG/JPEG、PNG、HEIC、WebP、PDF，单个文件不超过100MB。</span></label><details class="form-more"><summary>更多选项</summary><label>备注<input name="note"></label></details><div class="form-sticky-actions"><button class="warn">提交缺勤登记</button></div></form></section>`;}

function inclusiveDays(start,end){if(!start||!end)return 0;const a=Date.UTC(...start.split('-').map((x,i)=>Number(x)-(i===1?1:0)));const b=Date.UTC(...end.split('-').map((x,i)=>Number(x)-(i===1?1:0)));return b<a?0:Math.floor((b-a)/86400000)+1;}

function bindSick(){
  const page=captureViewContext();
  const form=document.getElementById('sickForm');
  if(!form)return;
  const employee=form.querySelector('[name=employee_id]'),start=form.querySelector('[name=leave_start_date]'),end=form.querySelector('[name=leave_end_date]'),days=form.querySelector('[name=leave_days]'),proof=form.querySelector('[name=proof]'),note=form.querySelector('[name=note]'),violation=form.querySelector('[name=is_violation]');
  const allowedExtensions=new Set(['jpg','jpeg','png','heic','webp','pdf']),maxProofBytes=100*1024*1024;
  const syncDays=()=>{const total=inclusiveDays(start.value,end.value);if(total<1){days.value='';days.max='0.5';return;}days.max=String(total);days.value=String(total);};
  const proofError=()=>{const file=proof.files?.[0],extension=(file?.name.split('.').pop()||'').toLowerCase();if(!file)return '请先选择缺勤证明（图片或PDF）。';if(!allowedExtensions.has(extension))return '缺勤证明仅支持 JPG/JPEG、PNG、HEIC、WebP 或 PDF。';if(file.size<=0)return '缺勤证明未成功读取，请重新选择文件后提交。';if(file.size>maxProofBytes)return `缺勤证明“${file.name}”超过100MB，请重新选择。`;return '';};
  const appendViolationMaterial=data=>{const picker=form._violationMaterial;if(!picker?.hasMaterial())return '';const scratch=new FormData(),mode=picker.appendTo(scratch);for(const [key,value] of scratch.entries())data.append(key==='document'?'violation_document':'violation_document_images',value,value.name);return mode;};
  start.addEventListener('change',syncDays);end.addEventListener('change',syncDays);proof.addEventListener('change',()=>{const message=proofError();if(message)toast(message,true);});syncDays();
  form.onsubmit=async event=>{event.preventDefault();if(!requireEmployeeSelection(form))return;const proofMessage=proofError();if(proofMessage){toast(proofMessage,true);proof.focus();return;}if(!start.value||!end.value){toast('请选择完整的开始日期和结束日期。',true);return;}if(inclusiveDays(start.value,end.value)<1){toast('结束日期不能早于开始日期。',true);end.focus();return;}const enteredDays=Number(days.value);if(!Number.isFinite(enteredDays)||enteredDays<=0||Math.round(enteredDays*2)!==enteredDays*2){toast('缺勤天数必须按0.5天递增。',true);days.focus();return;}if(enteredDays>inclusiveDays(start.value,end.value)){toast('缺勤天数不能超过所选日期范围。',true);days.focus();return;}if(end.value>start.value&&!await confirmModal('病假日期确认','<p>请确认您所提交的病假日期中不含演职人员本休。</p>','已确认，继续提交'))return;
    await withSubmitLock(form,async()=>{try{const data=submissionData(form),file=proof.files?.[0];data.set('employee_id',employee.value);data.set('leave_start_date',start.value);data.set('leave_end_date',end.value);data.set('leave_days',days.value);data.set('note',note.value);data.set('proof',file,file.name);if(end.value>start.value)data.set('rest_day_confirmed','true');let materialMode='';if(violation?.checked){data.set('is_violation','true');const reviewer=form.querySelector('[name=violation_reviewer_id]');if(reviewer&&!reviewer.closest('[hidden]')&&!reviewer.value){toast('请先选择GSM或TA GSM审核人。',true);reviewer.focus();return;}if(reviewer?.value)data.set('violation_reviewer_id',reviewer.value);materialMode=appendViolationMaterial(data);}const result=await api('/api/sick-leaves',{method:'POST',body:data});clearSubmissionKey(form);if(!page.isCurrent()){toast('缺勤登记已完成');return;}proof.value='';if(!violation?.checked){toast(`缺勤登记成功，当月全勤分 ${fmt(result.attendance_score)}`);return;}form._violationMaterial?.reset();violation.checked=false;form._refreshViolation?.();if(!materialMode){await confirmModal('缺勤与违规病假已登记','<p>缺勤已生效；违规病假声明暂未上传材料，已进入“待补充材料”。</p><p><strong>暂不扣分。</strong>请在待办或主管登记记录中补充材料。</p>','我知道了');}else if(result.violation?.material_status==='processing'){toast('缺勤已登记；违规病假声明材料正在后台生成PDF，完成后将自动进入扣分或升级流程。');}else if(result.violation_upgrade){toast('缺勤与违规病假声明已登记，已提交GSM/TA GSM审核。');}else{toast('缺勤与违规病假声明已登记，声明扣分已生效。');}}catch(error){toast(error.message||'缺勤登记失败，请检查所填内容后重试。',true);}});
  };
}

function bindSickLeaveOverlapGuard(form){
  const employee=form.querySelector('[name=employee_id]'),start=form.querySelector('[name=leave_start_date]'),end=form.querySelector('[name=leave_end_date]'),error=document.getElementById('sickDateError');
  let lastSignature='',lastResult=null,sequence=0;
  const reset=()=>{lastSignature='';lastResult=null;};
  const signature=()=>employee.value&&start.value&&end.value&&inclusiveDays(start.value,end.value)>0?`${employee.value}:${start.value}:${end.value}`:'';
  const check=async({showInline=false}={})=>{
    const value=signature();
    if(!value){reset();return null;}
    if(value===lastSignature&&lastResult)return lastResult;
    const requestId=++sequence;
    const result=await api('/api/sick-leaves/overlap-check?'+new URLSearchParams({employee_id:employee.value,leave_start_date:start.value,leave_end_date:end.value}));
    if(requestId!==sequence)return null;
    lastSignature=value;lastResult=result;
    if(showInline&&result.conflict){error.textContent=result.detail.message;error.hidden=false;start.setCustomValidity(result.detail.message);end.setCustomValidity(result.detail.message);}
    return result;
  };
  const clearInline=()=>{if(!error.textContent.includes('已有')&&!error.textContent.includes('日期有交集'))return;error.hidden=true;error.textContent='';start.setCustomValidity('');end.setCustomValidity('');};
  const schedule=()=>{reset();clearInline();check({showInline:true}).catch(()=>{});};
  employee.addEventListener('change',schedule);start.addEventListener('change',schedule);end.addEventListener('change',schedule);
  form.addEventListener('submit',async event=>{
    if(form.dataset.overlapGuardBypass==='1'){delete form.dataset.overlapGuardBypass;return;}
    event.preventDefault();event.stopImmediatePropagation();
    if(!employee.value||!start.value||!end.value||inclusiveDays(start.value,end.value)<1){form.onsubmit?.(event);return;}
    try{
      const result=await check({showInline:true});
      if(result?.conflict){const rows=(result.detail.records||[]).map(row=>`<li>${esc(row.leave_start_date)} 至 ${esc(row.leave_end_date)}（${Number(row.leave_days).toFixed(1)}天）</li>`).join('');await confirmModal('已有缺勤登记',`<p>${esc(result.detail.message)}</p><ul>${rows}</ul><p>请先作废或更正已有记录后再提交。</p>`,'返回修改');return;}
    }catch(requestError){toast(requestError.message||'无法检查已有缺勤登记，请稍后重试。',true);return;}
    form.dataset.overlapGuardBypass='1';form.onsubmit?.(event);
  },true);
}

async function renderAbsence(){
  const request=beginViewRequest();
  if(!has('SICK_REGISTER')){app.innerHTML='<section class="panel"><div class="error">当前账号无缺勤登记权限</div></section>';return;}
  app.innerHTML=`<div class="section-gap">${sickForm()}</div>`;
  const sickFormNode=document.getElementById('sickForm');
  if(sickFormNode&&has('DEDUCTION_DIRECT')||sickFormNode&&has('DEDUCTION_ALL')){const toggle=document.createElement('label');toggle.className='checkbox-row';toggle.innerHTML='<input name="is_violation" type="checkbox" value="true"> 是否为违规病假 <span class="field-hint">在本页同步登记一条独立的违规病假声明；事件日期取开始日期，不按缺勤天数重复扣分。</span>';const detail=document.createElement('section');detail.className='inline-material-panel';detail.hidden=true;detail.innerHTML=`<p class="field-hint">缺勤证明与违规病假声明材料分别保存。声明材料可直接上传PDF，或拍照/从相册选择；未上传时可先登记，后续补齐材料才扣分。</p>${deductionMaterialPickerMarkup()}<div class="repeat-warning notice" data-violation-upgrade hidden></div>`;const sticky=sickFormNode.querySelector('.form-sticky-actions')||sickFormNode.querySelector('button.warn');sticky?.before(toggle);sticky?.before(detail);sickFormNode._violationMaterial=bindDeductionMaterialPicker(detail);const employee=sickFormNode.querySelector('[name=employee_id]'),start=sickFormNode.querySelector('[name=leave_start_date]'),warning=detail.querySelector('[data-violation-upgrade]');const refresh=async()=>{detail.hidden=!toggle.querySelector('input').checked;if(detail.hidden){warning.hidden=true;warning.innerHTML='';return;}if(!employee.value||!start.value){warning.hidden=true;return;}try{const out=await api('/api/sick-leaves/violation-upgrade-preview?'+new URLSearchParams({employee_id:employee.value,leave_start_date:start.value}));if(!request.isCurrent())return;if(!out.eligible){warning.hidden=true;warning.innerHTML='';return;}warning.hidden=false;warning.innerHTML=`<strong>3个月内已有未升级的违规病假声明</strong><p>${esc(out.first_record.occurred_on)}：${esc(out.first_record.description)}。本次将进入升级工单，请选择审核人。</p><label>GSM / TA GSM 审核人<select name="violation_reviewer_id"><option value="">请选择审核人</option>${out.reviewers.map(item=>`<option value="${item.id}">${esc(item.name)} · ${esc(item.role_name)}</option>`).join('')}</select></label>`;}catch(error){warning.hidden=false;warning.className='repeat-warning blocked';warning.innerHTML='<strong>暂时无法检查违规病假升级条件，请稍后重试。</strong>';}};sickFormNode._refreshViolation=refresh;toggle.querySelector('input').addEventListener('change',refresh);employee.addEventListener('change',refresh);start.addEventListener('change',refresh);}
  bindEmployeeSearches(app);
  bindSick();
  const form=document.getElementById('sickForm'),start=form.querySelector('[name=leave_start_date]'),end=form.querySelector('[name=leave_end_date]'),error=document.getElementById('sickDateError');
  const validateDates=()=>{let message='';if(!start.value||!end.value)message='请选择完整的开始日期和结束日期。';else if(inclusiveDays(start.value,end.value)<1)message='结束日期不能早于开始日期。';error.hidden=!message;error.textContent=message;start.setCustomValidity(message);end.setCustomValidity(message);};
  [start,end].forEach(input=>['input','change','blur'].forEach(event=>input.addEventListener(event,validateDates)));
  validateDates();
  bindSickLeaveOverlapGuard(form);
}

function singleSickLeaveForm(){
  return `<form id="singleSickLeaveForm" class="form-stack">
    ${employeePicker('员工（必选）','singleSickEmployee','absence_backup','输入姓名或工号查找全部在职员工','/api/sick-leave-imports/employee-targets')}
    <div class="grid two"><label>病假类型<select name="leave_type" required><option value="法定病假">法定病假</option><option value="全薪病假">全薪病假</option><option value="无薪病假">无薪病假</option></select></label><label>所属月份<input id="singleSickMonth" readonly aria-readonly="true"><span class="field-hint">根据病假日期自动确定</span></label></div>
    <div class="grid two"><label>开始日期<input name="leave_start_date" type="date" value="${today()}" required></label><label>结束日期<input name="leave_end_date" type="date" value="${today()}" required></label></div>
    <div class="grid two"><label>实际病假天数<input name="leave_days" type="number" min="0.5" step="0.5" value="1" required><span class="field-hint">支持0.5天；不含本休，跨月请分开登记</span></label><label>备注（可选）<textarea name="note" rows="2" maxlength="300" placeholder="填写补登记说明"></textarea></label></div>
    <div class="notice">本条记录提交后生效；后续导入该月完整缺勤文件时，将以文件为准覆盖。</div>
    <div id="singleSickResult" aria-live="polite"></div><div class="actions form-sticky-actions"><button type="button" class="secondary" id="clearSingleSick">清空</button><button type="submit" class="primary">登记病假</button></div>
  </form>`;
}

function bindSingleSickLeave(form,onDone){
  const page=captureViewContext(form);
  const start=form.elements.leave_start_date,end=form.elements.leave_end_date,days=form.elements.leave_days,month=document.getElementById('singleSickMonth'),result=document.getElementById('singleSickResult');
  const syncDates=()=>{
    const valid=Boolean(start.value&&end.value&&end.value>=start.value&&start.value.slice(0,7)===end.value.slice(0,7));
    month.value=start.value?`${start.value.slice(0,4)}年${start.value.slice(5,7)}月`:'';
    end.setCustomValidity(valid?'':'病假日期无效，跨月请分开登记');
    days.max=String(Math.max(0.5,inclusiveDays(start.value,end.value)||0.5));
  };
  [start,end].forEach(input=>input.addEventListener('change',syncDates));
  const clearKey=()=>{if(form.dataset.submitting!=='true')clearSubmissionKey(form);};
  form.addEventListener('input',clearKey);form.addEventListener('change',clearKey);
  document.getElementById('clearSingleSick').onclick=()=>{
    if(form.dataset.submitting==='true')return;
    form.reset();form.querySelector('[data-employee-clear]')?.click();result.innerHTML='';clearSubmissionKey(form);syncDates();
  };
  form.onsubmit=async event=>{
    event.preventDefault();if(!requireEmployeeSelection(form))return;syncDates();if(!form.reportValidity())return;
    const enteredDays=Number(days.value);
    if(!Number.isFinite(enteredDays)||enteredDays<=0||enteredDays*2%1!==0||enteredDays>inclusiveDays(start.value,end.value)){toast('病假天数须按0.5天递增，且不能超过日期范围',true);return;}
    await withSubmitLock(form,async()=>{
      try{
        const employeeText=form.querySelector('[data-employee-search]').value;
        const accepted=await confirmModal('确认登记病假',`<p>${esc(employeeText)}</p><p>${esc(start.value)} 至 ${esc(end.value)} · ${esc(form.elements.leave_type.value)} · ${enteredDays}天</p>${end.value>start.value?'<p>请确认实际病假天数中不含演职人员本休。</p>':''}<p>后续导入该月完整缺勤文件时，将以文件为准覆盖。</p>`,'确认登记');
        if(!accepted)return;
        const data=submissionData(form);data.set('rest_day_confirmed','true');
        const out=await api('/api/sick-leave-imports/single',{method:'POST',body:data});clearSubmissionKey(form);
        result.innerHTML=`<div class="notice"><strong>${out.duplicate?'该条病假已登记，无需重复提交':'病假登记成功'}</strong><br>${esc(out.record.leave_start_date)} 至 ${esc(out.record.leave_end_date)} · ${esc(out.record.leave_type)} · ${Number(out.record.leave_days).toFixed(1)}天 · 单人备用登记${out.score_eligible?`<br>当月全勤分：${fmt(out.attendance_score)}`:''}</div>`;
        form.elements.note.value='';toast('病假已登记');await page.refresh(onDone,out.record.attendance_month);
      }catch(error){result.innerHTML=`<div class="error" role="alert">${esc(error.message||'病假登记失败，请稍后重试')}</div>`;toast(error.message||'病假登记失败',true);}
    });
  };
  syncDates();
}

function sickLeaveSourceLabel(row){return row.import_source==='single_backup'?'单人备用登记':row.import_source==='monthly_transaction_import'?'月度文件导入':'历史人工登记';}

async function renderSickLeaveImport(){
  const request=beginViewRequest();
  if(!has('SICK_LEAVE_IMPORT')){app.innerHTML='<section class="panel"><div class="error">当前账号没有缺勤登记权限</div></section>';return;}
  app.innerHTML=`<div class="section-gap sick-import-page"><section class="panel"><h2>缺勤登记</h2><p>按月导入完整缺勤文件，或通过备用入口补登记单个员工的病假。</p><div class="actions" role="tablist" aria-label="缺勤登记方式"><button type="button" class="primary" data-sick-entry-mode="file" id="sickFileTab" role="tab" aria-selected="true" aria-controls="sickFilePanel">月度文件导入</button><button type="button" class="secondary" data-sick-entry-mode="single" id="sickSingleTab" role="tab" aria-selected="false" aria-controls="sickSinglePanel">单人病假登记（备用）</button></div><div id="sickFilePanel" role="tabpanel" aria-labelledby="sickFileTab"><p class="field-hint">选择月份后上传该月完整缺勤文件。未列出的本月员工视为无缺勤，旧缺勤标记“已覆盖”并退出计分。员工匹配失败须由登记人复核后跳过；月份不符或文件对账错误禁止覆盖。景点圈HR的文件导入仅更新本圈。</p><form id="sickLeaveImportForm" class="form-stack" enctype="multipart/form-data"><label>覆盖月份<input name="month" type="month" value="${monthNow()}" required></label><label>员工事务文件<input name="workbook" type="file" accept=".xls" required><span class="field-hint">仅支持不超过 10 MB 的 .xls；源文件 ID 去掉第一位后与系统 7 位员工号匹配。预检最多有效 20 分钟。</span></label><button type="submit" class="primary">预检文件</button></form><div id="sickLeaveImportResult"></div></div><div id="sickSinglePanel" role="tabpanel" aria-labelledby="sickSingleTab" hidden>${singleSickLeaveForm()}</div></section><section class="panel"><h2>月度缺勤登记记录</h2><p class="field-hint">展示月度文件及单人备用登记；已覆盖记录可展开查看，不再参与计天。历史人工登记仍在“我的登记记录”查看。</p><label>查看月份<input id="sickRecordsMonth" type="month" value="${monthNow()}"></label><div id="sickLeaveImportRecords" class="empty">正在加载…</div></section></div>`;
  const form=document.getElementById('sickLeaveImportForm'),result=document.getElementById('sickLeaveImportResult'),records=document.getElementById('sickLeaveImportRecords');
  const recordMonth=document.getElementById('sickRecordsMonth');let recordSequence=0;
  const importTable=rows=>`<div class="desktop-only table-wrap"><table><thead><tr><th>登记时间 / 来源</th><th>员工 / 景点圈</th><th>病假日期</th><th>类型 / 天数</th><th>登记人</th><th>状态</th></tr></thead><tbody>${rows.map(row=>`<tr class="${row.status==='covered'?'void-row':''}"><td>${esc(row.submitted_at)}<br><small>${esc(sickLeaveSourceLabel(row))}</small></td><td>${esc(row.employee_name)}<br><small>${esc(row.employee_no)} · ${esc(row.attraction_name||'未分配景点圈')}</small></td><td>${esc(row.leave_start_date)} 至 ${esc(row.leave_end_date)}</td><td>${esc(row.leave_type)} · ${Number(row.leave_days).toFixed(1)}天${row.note?`<br><small>${esc(row.note)}</small>`:''}</td><td>${esc(row.submitter_name)}</td><td>${statusBadge(row)}${row.status==='covered'?`<br><small>${esc(row.void_reason)}</small>`:''}</td></tr>`).join('')}</tbody></table></div><div class="mobile-only mobile-records">${rows.map(row=>`<article class="record-card"><div class="record-line"><strong>${esc(row.employee_name)} · ${esc(row.employee_no)}</strong>${statusBadge(row)}</div><p>${esc(row.attraction_name||'未分配景点圈')} · ${esc(row.leave_type)} · ${Number(row.leave_days).toFixed(1)}天</p><p>${esc(row.leave_start_date)} 至 ${esc(row.leave_end_date)}</p><small>${esc(sickLeaveSourceLabel(row))} · ${esc(row.submitter_name)} · ${esc(row.submitted_at)}</small>${row.note?`<p>${esc(row.note)}</p>`:''}${row.status==='covered'?`<p>${esc(row.void_reason)}</p>`:''}</article>`).join('')}</div>`;
  const loadRecords=async()=>{if(!request.isCurrent())return;const sequence=++recordSequence;records.classList.remove('empty');const out=await api('/api/sick-leave-imports/records?'+new URLSearchParams({month:recordMonth.value}));if(sequence!==recordSequence||!request.isCurrent())return;const rows=out.items||[],{visible,covered}=splitCoveredSickRecords(rows);records.innerHTML=rows.length?`${visible.length?importTable(visible):''}${coveredSickDisclosure(covered,importTable(covered))}`:'<div class="empty">所选月份暂无缺勤登记记录</div>';};
  recordMonth.addEventListener('change',()=>loadRecords().catch(error=>toast(error.message,true)));
  form.elements.month.addEventListener('change',()=>{recordMonth.value=form.elements.month.value;result.innerHTML='';loadRecords().catch(error=>toast(error.message,true));});
  app.querySelectorAll('[data-sick-entry-mode]').forEach(button=>button.onclick=()=>{app.querySelectorAll('[data-sick-entry-mode]').forEach(tab=>{const active=tab===button;tab.setAttribute('aria-selected',String(active));tab.className=active?'primary':'secondary';});document.getElementById('sickFilePanel').hidden=button.dataset.sickEntryMode!=='file';document.getElementById('sickSinglePanel').hidden=button.dataset.sickEntryMode!=='single';});
  bindEmployeeSearches(app);
  bindSingleSickLeave(document.getElementById('singleSickLeaveForm'),async month=>{recordMonth.value=month;await loadRecords();});
  form.onsubmit=async event=>{
    event.preventDefault();
    const month=form.elements.month.value,file=form.elements.workbook.files?.[0];
    if(!month||!file){toast('请选择月份和 .xls 文件',true);return;}
    const data=new FormData();data.set('month',month);data.set('workbook',file,file.name);
    try{
      const out=await api('/api/sick-leave-imports/preview',{method:'POST',body:data});
      if(!request.isCurrent())return;
      const unmatched=out.unmatched||[],protectedRows=out.loa_protected||[],notImported=[...unmatched,...protectedRows],blocked=out.blocking_errors||[];
      const errors=blocked.length?`<div class="error"><strong>预检未通过</strong><ul>${blocked.map(item=>`<li>${esc(item)}</li>`).join('')}</ul></div>`:'';
      const rows=notImported.length?`<div class="table-wrap"><table><thead><tr><th>行号</th><th>姓名</th><th>源ID</th><th>系统员工号</th><th>日期</th><th>原因</th></tr></thead><tbody>${notImported.map(item=>`<tr><td>${item.row}</td><td>${esc(item.name)}</td><td>${esc(item.source_id)}</td><td>${esc(item.employee_no)}</td><td>${esc(item.date)}</td><td>${esc(item.reason)}</td></tr>`).join('')}</tbody></table></div>`:'<p class="field-hint">文件明细均已匹配。</p>';
      const protectedNote=protectedRows.length?`<div class="notice"><strong>LOA保护</strong><br>${protectedRows.length} 条文件明细落在LOA日期内，已跳过。</div>`:'';
      const unmatchedNote=unmatched.length?`<div class="notice"><strong>需登记人复核：${unmatched.length} 条员工未匹配</strong><br>以下行将跳过，不会写入缺勤；其原因已逐行列明。整月旧缺勤仍会按所选月份覆盖，请核对这些员工是否受影响。</div>`:'';
      const review=out.can_commit&&unmatched.length?`<label class="checkbox-row"><input id="reviewSickUnmatched" type="checkbox">我已逐行复核未匹配员工及原因，确认跳过这些行并继续整月覆盖</label>`:'';
      result.innerHTML=`${errors}<div class="notice"><strong>预检结果</strong><br>月份：${esc(out.month)}；文件匹配 ${out.matched_employee_count} 名员工、${out.matched_record_count} 条明细；当月旧记录 ${out.replaced_record_count} 条将被覆盖，其中 ${out.absent_employee_count} 名员工没有可匹配的文件缺勤，旧缺勤将退出计分。</div>${unmatchedNote}${protectedNote}${rows}${notImported.length?`<a class="secondary download-link" href="${portalPath('/api/sick-leave-imports/'+encodeURIComponent(out.token)+'/unmatched-file')}">下载未导入数据标红文件</a>`:''}${review}${out.can_commit?`<div class="actions"><button id="commitSickLeaveImport" class="warn" ${unmatched.length?'disabled':''}>确认覆盖所选月份全部缺勤</button></div>`:''}`;
      document.getElementById('reviewSickUnmatched')?.addEventListener('change',event=>{document.getElementById('commitSickLeaveImport').disabled=!event.target.checked;});
      document.getElementById('commitSickLeaveImport')?.addEventListener('click',async()=>{
        const confirmed=await confirmModal('确认覆盖整月缺勤',`<p>将以文件替换 ${esc(out.month)} 的全部旧缺勤，共 ${out.replaced_record_count} 条；其中 ${out.absent_employee_count} 名员工没有可匹配的文件缺勤，旧缺勤将退出计分。</p>${unmatched.length?`<p>已复核的 ${unmatched.length} 条未匹配员工行将跳过，不写入缺勤；旧记录仍按整月覆盖。</p>`:''}<p>旧记录保留“已覆盖”审计状态，不再参与计天；LOA不变。</p>`,'确认覆盖整月');
        if(!confirmed)return;
        try{const committed=await api('/api/sick-leave-imports/commit',json('POST',{token:out.token,month:out.month,reviewed_unmatched:unmatched.length>0}));toast('所选月份缺勤已覆盖并重算全勤分');if(!request.isCurrent())return;result.innerHTML=`<div class="notice"><strong>覆盖完成</strong><br>${esc(committed.month)} 写入 ${committed.covered_record_count} 条文件明细；旧记录 ${committed.replaced_record_count} 条已覆盖，${committed.absent_employee_count} 名无可匹配文件缺勤的员工已退出本月缺勤计分；未匹配跳过 ${committed.unmatched_count} 条，LOA保护跳过 ${committed.loa_protected_count} 条。</div>`;recordMonth.value=committed.month;await loadRecords();}catch(error){toast(error.message,true);}
      });
    }catch(error){if(!request.isCurrent())return;const message=error.message||'预检失败';result.innerHTML=`<div class="error" role="alert"><strong>无法完成预检</strong><p>${esc(message)}</p></div>`;toast(message,true);}
  };
  await loadRecords();
}

async function renderLoa(){
  const request=beginViewRequest();
  if(!has('LOA_REGISTER')){app.innerHTML='<section class="panel"><div class="error">当前账号没有LOA登记权限</div></section>';return;}
  app.innerHTML=`<div class="section-gap loa-page"><section class="panel loa-workflow"><div class="loa-heading"><div><h2>LOA（长期病假）登记</h2><p>先搜索员工，再根据当前状态登记进入或结束。LOA 涉及的每个自然月均不参与任何计分。</p></div><span class="badge warn">两步登记</span></div><form id="loaForm" class="form-stack"><section class="loa-step"><strong>1. 搜索并选择员工</strong>${employeePicker('员工（必选）','loaEmployee','loa','输入姓名或工号查找全部在职员工')}</section><section id="loaState" class="loa-state notice"><strong>2. 确认 LOA 状态</strong><p>请先搜索并选择员工。</p></section><section id="loaDateStep" class="loa-step" hidden><strong id="loaDateTitle">3. 登记进入日期</strong><div class="grid two"><label id="loaStartField">LOA进入日期<input name="starts_on" type="date" value="${today()}" required></label><label id="loaEndField" hidden>LOA结束日期（必填）<input name="ends_on" type="date" value="${today()}"><span class="field-hint">默认当前日期，可手动选择其他日期；不可早于进入日期。</span></label></div><p id="loaMonthHint" class="field-hint" aria-live="polite"></p></section><label>备注（可选）<textarea name="note" maxlength="300" placeholder="例如：长期病假登记说明"></textarea></label><div class="form-sticky-actions"><button class="warn" id="loaSubmit" disabled>请先选择员工</button></div></form></section><section class="panel"><h2>近期 LOA 记录</h2><p class="field-hint">未填写结束日期的记录，可在下方直接补登记结束日期。</p><div id="loaRecords" class="empty">正在加载…</div></section></div>`;
  bindEmployeeSearches(app);
  const form=document.getElementById('loaForm'),start=form.elements.starts_on,end=form.elements.ends_on,hint=document.getElementById('loaMonthHint'),employee=form.querySelector('[name=employee_id]'),submit=document.getElementById('loaSubmit'),stateBox=document.getElementById('loaState'),dateStep=document.getElementById('loaDateStep'),dateTitle=document.getElementById('loaDateTitle'),startField=document.getElementById('loaStartField'),endField=document.getElementById('loaEndField');
  const monthsBetweenDates=()=>{if(!start.value||!end.value||end.value<start.value)return [];const months=[];let cursor=new Date(start.value+'T00:00:00'),last=new Date(end.value+'T00:00:00');cursor.setDate(1);last.setDate(1);while(cursor<=last){months.push(`${cursor.getFullYear()}-${String(cursor.getMonth()+1).padStart(2,'0')}`);cursor.setMonth(cursor.getMonth()+1);}return months;};
  const updateMode=()=>{if(!employee.value){dateStep.hidden=true;submit.disabled=true;submit.textContent='请先选择员工';stateBox.innerHTML='<strong>2. 确认 LOA 状态</strong><p>请先搜索并选择员工。</p>';return;}const active=employee.dataset.loaActive==='1',endsOn=employee.dataset.loaEndsOn||'';dateStep.hidden=false;if(active){start.value=employee.dataset.loaStartsOn;start.readOnly=true;startField.classList.add('loa-date-locked');endField.hidden=false;end.min=start.value;if(endsOn){start.disabled=true;end.disabled=true;end.required=false;end.value=endsOn;dateTitle.textContent='3. LOA期间（只读）';submit.disabled=true;submit.textContent='LOA进行中';stateBox.innerHTML=`<strong>2. 当前处于 LOA</strong><p>该员工的 LOA 时间为 <b>${esc(start.value)}</b> 至 <b>${esc(endsOn)}</b>。当前仍在 LOA 期间，不能重复登记。</p>`;hint.textContent='开始日期和结束日期均已登记，当前仅供查看。';}else{start.disabled=false;end.disabled=false;end.required=true;end.value=today();dateTitle.textContent='3. 登记 LOA 结束';submit.disabled=false;submit.textContent='登记LOA结束';stateBox.innerHTML=`<strong>2. 当前处于 LOA</strong><p>该员工于 <b>${esc(start.value)}</b> 进入 LOA。开始日期已锁定；请补登记结束日期。</p>`;updateHint();}}else{start.disabled=false;start.readOnly=false;startField.classList.remove('loa-date-locked');end.required=false;end.value='';end.disabled=true;end.min='';endField.hidden=true;dateTitle.textContent='3. 登记进入 LOA';submit.disabled=false;submit.textContent='登记进入LOA';stateBox.innerHTML='<strong>2. 当前未处于 LOA</strong><p>请选择实际进入日期后登记；结束日期以后再补登记。</p>';hint.textContent='';}};
  const updateHint=()=>{if(employee.dataset.loaActive!=='1'){hint.textContent='';return;}if(end.value&&end.value<start.value){hint.textContent='结束日期不能早于进入日期。';return;}const months=monthsBetweenDates();hint.textContent=months.length?`涉及月份：${months.join('、')}。这些月份均不参与计分。`:'';};
  let loadSequence=0;
  const load=async()=>{
    if(!request.isCurrent())return;const sequence=++loadSequence;
    const out=await api('/api/loa-periods'),rows=out.items||[],records=document.getElementById('loaRecords');
    if(!request.isCurrent()||sequence!==loadSequence)return;
    records.classList.remove('empty');
    records.innerHTML=rows.length?`<div class="table-wrap"><table class="loa-record-table"><thead><tr><th>员工</th><th>进入日期</th><th>结束日期</th><th>状态</th><th>备注</th><th>登记人</th><th>操作</th></tr></thead><tbody>${rows.map(row=>{
      const ongoing=!row.ends_on||row.ends_on>=today(),endControl=row.ends_on?esc(row.ends_on):`<input type="date" value="${today()}" min="${esc(row.starts_on)}" aria-label="${esc(row.employee_name)}的LOA结束日期" data-loa-end-date="${row.id}">`,completeAction=row.ends_on?'':`<button type="button" class="secondary" data-loa-complete="${row.id}" data-employee-id="${row.employee_id}" data-starts-on="${esc(row.starts_on)}" data-employee-name="${esc(row.employee_name)}">登记结束</button>`;
      return `<tr><td>${esc(row.employee_name)}<br><small>${esc(row.employee_no)}</small></td><td>${esc(row.starts_on)}</td><td>${endControl}</td><td><span class="badge ${ongoing?'warn':'ok'}">${ongoing?'LOA进行中':'已结束'}</span></td><td>${esc(row.note||'—')}</td><td>${esc(row.created_by_name)}</td><td><div class="loa-record-actions">${completeAction}<button type="button" class="secondary" data-loa-cancel="${row.id}">撤销</button></div></td></tr>`;
    }).join('')}</tbody></table></div>`:'<div class="empty">暂无LOA记录</div>';
    records.querySelectorAll('[data-loa-complete]').forEach(button=>button.onclick=async()=>{
      const endDate=records.querySelector(`[data-loa-end-date="${button.dataset.loaComplete}"]`),startsOn=button.dataset.startsOn;
      if(!endDate?.value){toast('请选择LOA结束日期',true);endDate?.focus();return;}
      if(endDate.value<startsOn){toast('结束日期不能早于进入日期',true);endDate.focus();return;}
      const months=[];let cursor=new Date(startsOn+'T00:00:00'),last=new Date(endDate.value+'T00:00:00');cursor.setDate(1);last.setDate(1);while(cursor<=last){months.push(`${cursor.getFullYear()}-${String(cursor.getMonth()+1).padStart(2,'0')}`);cursor.setMonth(cursor.getMonth()+1);}
      if(!(await confirmModal('确认登记LOA结束',`<p>${esc(button.dataset.employeeName)} 的结束日期为 ${esc(endDate.value)}。</p><p>涉及月份：${esc(months.join('、'))}</p><p>这些月份只保留记录，不参与计分。</p>`,'确认登记')))return;
      try{const result=await api('/api/loa-periods',json('POST',{employee_id:Number(button.dataset.employeeId),starts_on:startsOn,ends_on:endDate.value}));toast(`LOA结束已登记，已排除 ${result.excluded_months.join('、')} 的计分`);await load();}catch(error){toast(error.message,true);}
    });
    records.querySelectorAll('[data-loa-cancel]').forEach(button=>button.onclick=async()=>{const reason=window.prompt('请填写撤销原因');if(!reason?.trim())return;try{await api('/api/loa-periods/'+button.dataset.loaCancel,{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({reason})});toast('LOA已撤销并重新计算涉及月份');await load();}catch(error){toast(error.message,true)}});
  };
  employee.addEventListener('change',updateMode);[start,end].forEach(input=>input.addEventListener('change',updateHint));updateMode();await load();
  form.onsubmit=async event=>{event.preventDefault();if(!requireEmployeeSelection(form))return;const ending=employee.dataset.loaActive==='1';if(ending&&!end.value){toast('请手动选择LOA结束日期',true);end.focus();return;}if(ending&&end.value<start.value){toast('结束日期不能早于进入日期',true);return;}const months=monthsBetweenDates();const content=ending?`<p>涉及月份：${esc(months.join('、'))}</p><p>这些月份的全勤、绩效加扣分和各类汇总均只保留记录，不参与计分。</p>`:'<p>将登记该员工进入 LOA。结束日期未登记前，记录状态为“LOA进行中”。</p>';if(!(await confirmModal(ending?'确认登记LOA结束':'确认登记进入LOA',content,'确认登记')))return;try{const out=await api('/api/loa-periods',json('POST',Object.fromEntries(new FormData(form))));toast(out.completed?`LOA结束已登记，已排除 ${out.excluded_months.join('、')} 的计分`:'已登记进入LOA，状态为进行中');if(!request.isCurrent())return;form.reset();form.querySelector('[name=employee_id]').value='';form.querySelector('.employee-selected').hidden=true;form.querySelector('[data-employee-search]').value='';delete employee.dataset.loaActive;delete employee.dataset.loaStartsOn;delete employee.dataset.loaEndsOn;updateMode();await load();}catch(error){toast(error.message,true)}};
}

export { renderAbsence, renderLoa, renderSickLeaveImport };
