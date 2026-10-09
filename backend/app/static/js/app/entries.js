// entries
import { api, json } from './api.js';
import { beginViewRequest, captureViewContext } from './context.js';
import { openDeductionMaterialRetry } from './deduction-materials.js';
import { confirmModal, promptModal } from './dialogs.js';
import { bindFilePreviews } from './files.js';
import { esc, monthStart, today } from './format.js';
import { coveredSickDisclosure, entryCard, entryRow, splitCoveredSickRecords, statusBadge } from './records.js';
import { app, state } from './state.js';
import { toast } from './toast.js';

async function renderEntries(){
  const request=beginViewRequest();
  const isSupervisor=['TA_SUPERVISOR','SUPERVISOR'].includes(state.me.role_code);
  const canCollaborate=['TA_SUPERVISOR','SUPERVISOR','TA_GSM','GSM'].includes(state.me.role_code);
  const title=isSupervisor?'主管登记记录':'我的登记记录';
  const description=isSupervisor?'默认展示本人登记的扣分和缺勤记录；可切换查看全部主管登记记录。其他主管的记录仅供查询，本人记录继续按原权限操作。':'展示本人登记的数据和重复处分跟进状态。';
  const scopeField=isSupervisor?'<label>查看范围<select name="scope"><option value="mine">我的登记记录</option><option value="supervisors">全部主管登记记录</option></select></label>':'';
  const recordTypes=isSupervisor?'<option value="all">全部</option><option value="deduction">扣分</option><option value="sick_leave">缺勤</option>':'<option value="all">全部</option><option value="recognition">加分</option><option value="deduction">扣分</option><option value="sick_leave">缺勤</option><option value="follow_up">处分跟进</option>';
  app.innerHTML=`<div class="section-gap"><section class="panel"><h2>${title}</h2><p>${description}</p><form id="entryFilter" class="entry-filter">${scopeField}<label>开始日期<input name="start_date" type="date" value="${monthStart()}"></label><label>结束日期<input name="end_date" type="date" value="${today()}"></label><label>记录类型<select name="record_type">${recordTypes}</select></label><label>状态<select name="status"><option value="">全部</option><option value="confirmed">已确认</option><option value="pending">待复核 / 待经理跟进</option><option value="issued">已开具</option><option value="rejected">不通过</option><option value="pending_material">待补充材料</option><option value="material_processing">材料生成中</option><option value="material_failed">材料生成失败</option><option value="active">已生效</option><option value="covered">已覆盖</option><option value="void">已作废</option></select></label><label>员工搜索<input name="keyword" placeholder="姓名/员工号"></label><button class="primary">查询</button></form><div class="actions"><button id="allHistoryBtn" class="secondary">查看全部历史</button></div></section><div id="entryResults"></div></div>`;
  const form=document.getElementById('entryFilter');let currentPage=1;
  const page=captureViewContext(form,{latest:false});
  const load=async()=>{if(!page.isCurrent())return;
    const request=beginViewRequest();
    const qs=new URLSearchParams([...new FormData(form)].filter(([,value])=>value));qs.set('page',String(currentPage));qs.set('page_size','100');
    const materialScope=state.pendingMaterialScope||'mine';
    const [data,pendingMaterials]=await Promise.all([api('/api/my-entries?'+qs),canCollaborate?api('/api/deductions/pending-materials?'+new URLSearchParams({scope:materialScope})):Promise.resolve({items:[]})]);
    if(!request.isCurrent())return;
    const collaboration=(pendingMaterials.items||[]);
    const collaborationPanel=canCollaborate?`<section class="panel"><h3>待补充声明材料</h3><p class="field-hint">TA主管、主管、TA GSM和GSM均可协作补充；补齐并处理成功后才会正式扣分。本人提交且尚未计分的记录可作废。</p><label class="material-scope-filter">景点圈范围<select data-pending-material-scope><option value="mine" ${materialScope==='mine'?'selected':''}>我的景点圈</option><option value="all" ${materialScope==='all'?'selected':''}>全部景点圈</option></select></label>${collaboration.length?`<div class="mobile-only work-card-list">${collaboration.map(row=>{const own=Number(row.submitter_id)===Number(state.me.id);const origin=own?'<small class="material-inline-state">我提交</small>':'<small class="material-inline-state">协作补充</small>';const ownVoid=own?` <button data-entry-void="${row.id}" class="secondary">作废</button>`:'';return `<article class="work-card"><div class="work-card-head"><strong>${esc(row.employee_name)}</strong><small>${esc(row.employee_no)}</small>${statusBadge(row)}</div><p>${esc(row.occurred_on)} · ${esc(row.deduction_type)}</p><p>${origin}</p><div class="work-card-actions"><button data-entry-material-retry="${row.id}" class="secondary">补充材料</button>${ownVoid}</div></article>`;}).join('')}</div><div class="desktop-only table-wrap sticky-col"><table><thead><tr><th>登记时间</th><th>员工</th><th>事件日期</th><th>内容</th><th>状态</th><th>操作</th></tr></thead><tbody>${collaboration.map(row=>{const own=Number(row.submitter_id)===Number(state.me.id);const origin=own?'<br><small class="material-inline-state">我提交</small>':'<br><small class="material-inline-state">协作补充</small>';const ownVoid=own?` <button data-entry-void="${row.id}" class="secondary">作废</button>`:'';return `<tr><td>${esc(row.submitted_at)}</td><td>${esc(row.employee_name)}<br><small>${esc(row.employee_no)}</small></td><td>${esc(row.occurred_on)}</td><td>${esc(row.deduction_type)} · ${esc(row.deduction_level)}${origin}<br>${esc(row.description)}</td><td>${statusBadge(row)}</td><td><button data-entry-material-retry="${row.id}" class="secondary">补充材料</button>${ownVoid}</td></tr>`;}).join('')}</tbody></table></div>`:'<div class="empty">当前范围暂无待补充声明材料</div>'}</section>`:'';
    const {visible,covered}=splitCoveredSickRecords(data.items);
    const table=items=>`<div class="desktop-only table-wrap sticky-col"><table><thead><tr><th>登记时间</th><th>类别</th><th>员工</th><th>业务日期</th><th>内容</th><th>分值/天数</th><th>状态</th><th>操作</th></tr></thead><tbody>${items.map(entryRow).join('')}</tbody></table></div>`;
    const coveredContent=`<div class="mobile-only work-card-list">${covered.map(entryCard).join('')}</div>${table(covered)}`;
    document.getElementById('entryResults').innerHTML=`${collaborationPanel}<section class="panel"><div class="mobile-only work-card-list">${visible.map(entryCard).join('')||(!covered.length?'<div class="empty">无匹配记录</div>':'')}</div>${visible.length?table(visible):!covered.length?'<div class="desktop-only empty">无匹配记录</div>':''}${coveredSickDisclosure(covered,coveredContent)}<div class="pagination"><button id="entryPrev" class="secondary" ${data.page<=1?'disabled':''}>上一页</button><span>第${data.page}页，共${data.total}条</span><button id="entryNext" class="secondary" ${data.page*data.page_size>=data.total?'disabled':''}>下一页</button></div></section>`;
    bindFilePreviews(document.getElementById('entryResults'));
    bindEntryActions(load);
    document.querySelector('[data-pending-material-scope]')?.addEventListener('change',event=>{state.pendingMaterialScope=event.target.value;load();});
    const focusId=Number(state.pendingMaterialFocusId||0);
    if(focusId){state.pendingMaterialFocusId=null;const button=document.querySelector(`[data-entry-material-retry="${focusId}"]`);if(button)setTimeout(()=>button.click(),0);else toast('该待补材料记录已变化，请刷新后确认。',true);}
    document.getElementById('entryPrev').onclick=()=>{if(currentPage>1){currentPage--;load()}};
    document.getElementById('entryNext').onclick=()=>{if(currentPage*data.page_size<data.total){currentPage++;load()}};
  };
  form.onsubmit=e=>{e.preventDefault();currentPage=1;load()};
  document.getElementById('allHistoryBtn').onclick=()=>{form.querySelector('[name=start_date]').value='';form.querySelector('[name=end_date]').value='';currentPage=1;load()};
  await load();
}

function bindEntryActions(done){const page=captureViewContext();
  document.querySelectorAll('[data-entry-withdraw]').forEach(button=>button.onclick=async()=>{if(!await confirmModal('撤回签卡','<p>撤回后该条记录不再计分，操作记录仅供高级别导出复查，是否继续？</p>','确认撤回'))return;try{await api('/api/recognitions/'+button.dataset.entryWithdraw,{method:'DELETE'});toast('加分记录已撤回');page.refresh(done)}catch(error){toast(error.message,true)}});
  document.querySelectorAll('[data-entry-void]').forEach(button=>button.onclick=async()=>{const reason=await promptModal('作废扣分记录','请输入作废原因（必填）：','作废原因');if(!reason)return;try{await api('/api/deductions/'+button.dataset.entryVoid+'/void',json('POST',{reason}));toast('扣分记录已作废');page.refresh(done)}catch(error){toast(error.message,true)}});
  document.querySelectorAll('[data-entry-material-retry]').forEach(button=>button.onclick=async()=>{try{const current=(await api('/api/deductions/'+button.dataset.entryMaterialRetry+'/material-status')).record;if(!page.isCurrent())return;openDeductionMaterialRetry(current.id,()=>page.refresh(done));}catch(error){toast(error.message,true)}});
  document.querySelectorAll('[data-entry-sick-void]').forEach(button=>button.onclick=async()=>{const reason=await promptModal('作废缺勤记录','请输入作废原因（必填）：','作废原因');if(!reason)return;try{await api('/api/sick-leaves/'+button.dataset.entrySickVoid+'/void',json('POST',{reason}));toast('缺勤记录已作废，全勤分已重新计算');page.refresh(done)}catch(error){toast(error.message,true)}});
}

export { renderEntries };
