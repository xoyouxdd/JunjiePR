// Deduction entry and upgrade type selection.
import { api } from './api.js';
import { captureViewContext, registerPageCleanup, render } from './context.js';
import { bindDeductionMaterialPicker, deductionMaterialPickerMarkup, showDeductionMaterialState } from './deduction-materials.js';
import { confirmModal, selectModal } from './dialogs.js';
import { employeePicker, requireEmployeeSelection } from './employee-picker.js';
import { showPerformanceRegistrationFeedback } from './feedback.js';
import { esc, fmt, today } from './format.js';
import { renderTabs } from './navigation.js';
import { opt } from './records.js';
import { has, state } from './state.js';
import { clearSubmissionKey, requireSubmittedDate, submissionData } from './submission.js';
import { toast } from './toast.js';

async function handlePendingMaterialConflict(error,page=captureViewContext()){const detail=error?.detail,record=detail?.code==='PENDING_MATERIAL_EXISTS'?detail.record:null;if(!record?.id)return false;if(!page.isCurrent()){toast(detail.message||error.message,true);return true;}const go=await confirmModal('已有待补材料记录',`<p>${esc(detail.message||'请先补充已有记录的材料。')}</p><p><strong>已有记录：</strong>${esc(record.employee_name)} · ${esc(record.occurred_on)} · ${esc(record.deduction_type)} · ${esc(record.deduction_level)}</p><p>补齐材料并处理完成后，才可继续登记下一条。</p>`,'去补充材料');if(go&&page.isCurrent()){state.pendingMaterialFocusId=Number(record.id);state.tab='entries';renderTabs();await render();}return true;}

function deductionUpgradeGroup(row){if(!row?.repeat_check)return null;if(['ATT_EARLY_CLOCK','ATT_LATE_CLOCK'].includes(row.code))return {category:'考勤异常',items:'早打卡、晚打卡'};if(['ATT_LATE_WITHIN_30','ATT_EARLY_LEAVE_WITHIN_30'].includes(row.code))return {category:'迟到早退',items:'迟到30分钟内、早退30分钟内'};return {category:'同类型',items:row.name};}

function deductionUpgradeOptionName(row){const group=deductionUpgradeGroup(row);return group?`${row.name}（${group.category}3个月内第2次触发升级）`:row.name;}

function deductionUpgradeOptionMarkup(row){const group=deductionUpgradeGroup(row);return `${esc(row.name)}${group?`<small class="deduction-upgrade-note">（${esc(group.category)}3个月内第2次触发升级）</small>`:''}`;}

function bindDeductionTypePicker(select){
  if(select.closest('[data-deduction-type-picker]'))return;
  const rows=state.options.deduction_types||[],picker=document.createElement('div');
  picker.className='recognizer-picker deduction-type-picker';picker.dataset.deductionTypePicker='';
  select.before(picker);picker.append(select);select.classList.add('recognizer-native-select');select.setAttribute('aria-hidden','true');select.tabIndex=-1;
  picker.insertAdjacentHTML('beforeend',`<button type="button" class="recognizer-trigger" aria-label="扣分类型" aria-describedby="deductionUpgradeHint" aria-haspopup="listbox" aria-expanded="false" aria-controls="deductionTypeMenu"><span data-deduction-type-value></span><span aria-hidden="true" class="recognizer-trigger-icon">▾</span></button><div id="deductionTypeMenu" class="recognizer-menu" role="listbox" aria-label="扣分类型" hidden>${rows.map(row=>`<button type="button" class="recognizer-person" role="option" tabindex="-1" data-deduction-type-option="${esc(row.id)}">${deductionUpgradeOptionMarkup(row)}</button>`).join('')}</div>`);
  const trigger=picker.querySelector('.recognizer-trigger'),menu=picker.querySelector('.recognizer-menu'),value=picker.querySelector('[data-deduction-type-value]'),buttons=[...menu.querySelectorAll('[data-deduction-type-option]')];
  const close=()=>{menu.hidden=true;trigger.setAttribute('aria-expanded','false');};
  const sync=()=>{const row=rows.find(item=>String(item.id)===select.value);value.innerHTML=row?deductionUpgradeOptionMarkup(row):'请选择';buttons.forEach(button=>button.setAttribute('aria-selected',String(button.dataset.deductionTypeOption===select.value)));};
  const focusOption=index=>{const button=buttons[Math.max(0,Math.min(index,buttons.length-1))];button?.focus();};
  const open=()=>{menu.hidden=false;trigger.setAttribute('aria-expanded','true');focusOption(buttons.findIndex(button=>button.dataset.deductionTypeOption===select.value));};
  trigger.onclick=event=>{event.preventDefault();if(menu.hidden)open();else close();};
  trigger.onkeydown=event=>{if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();open();}else if(event.key==='Escape'){event.preventDefault();close();}};
  buttons.forEach((button,index)=>{
    button.onclick=event=>{event.preventDefault();select.value=button.dataset.deductionTypeOption;select.dispatchEvent(new Event('change',{bubbles:true}));close();trigger.focus();};
    button.onkeydown=event=>{
      if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();focusOption(index+(event.key==='ArrowDown'?1:-1));}
      else if(event.key==='Home'||event.key==='End'){event.preventDefault();focusOption(event.key==='Home'?0:buttons.length-1);}
      else if(event.key==='Escape'){event.preventDefault();close();trigger.focus();}
      else if(event.key==='Tab'){close();}
    };
  });
  const outside=event=>{if(!picker.contains(event.target))close();};document.addEventListener('click',outside);registerPageCleanup(()=>document.removeEventListener('click',outside));
  picker.addEventListener('focusout',event=>{if(!picker.contains(event.relatedTarget))close();});
  select.addEventListener('change',sync);select.form.addEventListener('reset',()=>queueMicrotask(()=>{close();sync();}));sync();
}

function deductionForm(targetScope='frontline'){const supervisorScope=targetScope==='supervisor';const levels=has('DEDUCTION_ALL')||supervisorScope?state.options.deduction_levels:state.options.deduction_levels.filter(x=>x.code==='STATEMENT'),types=state.options.deduction_types||[];return `<section class="panel"><h2>${supervisorScope?'主管扣分登记':'扣分登记'}</h2><p>${supervisorScope?'按姓名或员工号搜索主管；可登记全部等级。':'按姓名或员工号搜索CM/TR'}；未上传材料时可先登记为待补充，材料就绪后才扣分。</p><form id="deductionForm" class="form-stack" enctype="multipart/form-data">${employeePicker('被扣分员工（必选）','deductionEmployee','deduction','输入姓名或工号查找全部在职CM/TR','',supervisorScope?'supervisor':'')}<div class="grid two"><label>扣分类型<select name="deduction_type_id" id="deductionTypeSelect" aria-describedby="deductionUpgradeHint">${opt(types,'id',deductionUpgradeOptionName)}</select><span id="deductionUpgradeHint" class="field-hint" aria-live="polite"></span></label><label>扣分等级<select name="deduction_level_id" id="deductionLevelSelect">${levels.map(x=>`<option value="${x.id}" data-code="${esc(x.code)}">${esc(`${x.name} · ${fmt(x.points)}分`)}</option>`).join('')}</select></label></div><label>事件日期<input name="occurred_on" id="deductionOccurredOn" type="date" value="${today()}" required></label><div id="deductionRepeatWarning" class="repeat-warning" hidden></div><details class="form-more"><summary>更多选项</summary><label>事件说明<textarea name="description" required></textarea></label>${deductionMaterialPickerMarkup()}</details><div id="deductionMaterialState" aria-live="polite"></div><div class="form-sticky-actions"><button id="deductionSubmit" class="danger">提交扣分登记</button></div></form></section>`;}

function bindDeductionUpgradeHint(){const form=document.getElementById('deductionForm'),select=document.getElementById('deductionTypeSelect'),hint=document.getElementById('deductionUpgradeHint');if(!form||!select||!hint)return;bindDeductionTypePicker(select);const sync=()=>{const row=(state.options.deduction_types||[]).find(item=>String(item.id)===select.value),group=deductionUpgradeGroup(row);hint.textContent=group?`对应升级项：${group.items}。仅提示归类；选定员工及事件日期后，按该事件日期前后各3个月内已登记的有效声明判断是否触发升级。`:'该类型不参与此处的声明升级判断。';};select.addEventListener('change',sync);form.addEventListener('reset',()=>queueMicrotask(sync));sync();}

function bindDeductionV2244(){
  const page=captureViewContext();
  const form=document.getElementById('deductionForm'),submit=document.getElementById('deductionSubmit'),material=bindDeductionMaterialPicker(form),stateBox=document.getElementById('deductionMaterialState');
  form.onsubmit=async event=>{
    event.preventDefault();
    if(!requireEmployeeSelection(form)||form.dataset.submitting==='true')return;
    try{
      const data=submissionData(form),occurredOn=form.querySelector('[name=occurred_on]'),direct=has('DEDUCTION_DIRECT')&&!has('DEDUCTION_ALL'),materialMode=material.appendTo(data);
      requireSubmittedDate(data,occurredOn,'事件日期');
      if(direct&&materialMode){
        const preview=await api('/api/deduction-upgrades/preview?'+new URLSearchParams({employee_id:data.get('employee_id'),deduction_type_id:data.get('deduction_type_id'),occurred_on:data.get('occurred_on')}));
        if(!page.isCurrent())return;
        if(preview.eligible){
          const first=preview.first_record;
          const reviewerId=await selectModal('可升级声明工单',`<p>该员工三个月内已有同类声明，将进入升级审核。</p><p><strong>历史声明：</strong>${esc(first.occurred_on)} · ${esc(first.deduction_type)} · ${esc(first.description)}</p><p>提交后本次声明不可撤回、不可作废；本次不计分，等待审核结果。</p><label>审核 MOD（GSM / TA GSM）</label>`,(preview.reviewers||[]).map(x=>({id:x.id,label:`${x.name} · ${x.employee_no} · ${x.role_name}`})),'提交工单');
          if(!reviewerId)return;
          data.set('reviewer_id',reviewerId);
          form.dataset.submitting='true';submit.disabled=true;
          const out=await api('/api/deduction-upgrades',{method:'POST',body:data});
          clearSubmissionKey(form);form.reset();material.reset();if(!page.isCurrent()){toast('声明已登记');return;}
          if(out.processing)showDeductionMaterialState(stateBox,out.record);
          if(!showPerformanceRegistrationFeedback('upgrade',out))toast(out.processing?'扣分登记已提交，材料正在生成PDF。生成结果请到“我的登记记录”查看。':'声明升级工单已提交，等待审核');
          return;
        }
      }
      if(!confirm(materialMode==='photos'?'确认提交照片材料？PDF生成成功后才会正式扣分。':materialMode==='pdf'?'确认提交PDF材料？系统会后台优化后生效。':'未上传材料：将创建待补充记录，TA主管、主管、TA GSM和GSM均可补充；在材料就绪前不扣分。是否继续？'))return;
      form.dataset.submitting='true';submit.disabled=true;
      const out=await api('/api/deductions',{method:'POST',body:data});clearSubmissionKey(form);form.reset();material.reset();if(!page.isCurrent()){toast('声明已登记');return;}
      if(out.record?.material_status==='processing')showDeductionMaterialState(stateBox,out.record);
      if(!showPerformanceRegistrationFeedback('deduction',out)){if(out.record?.material_status==='processing')toast('扣分登记已提交，材料正在后台处理。');else if(out.record?.material_status==='missing'){await confirmModal('声明已登记', '<p>该声明尚未上传材料，已登记为“待补充材料”。</p><p><strong>暂不扣分。</strong>请在“待办”或“我的登记记录”中点击“补充材料”完成上传。</p>', '我知道了');}else toast('扣分已生效');}
    }catch(error){if(!(await handlePendingMaterialConflict(error,page)))toast(error.message,true)}finally{delete form.dataset.submitting;submit.disabled=false;}
  };
}

export { bindDeductionUpgradeHint, bindDeductionV2244, deductionForm };
