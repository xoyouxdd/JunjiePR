import { queueExport } from './export-tasks.js';
// Declaration statistics and record detail presentation.
import { api, json } from './api.js';
import { toast } from './toast.js';
import { beginViewRequest } from './context.js';
import { confirmModal, formModal } from './dialogs.js';
import { bindFilePreviews } from './files.js';
import { esc, monthNow, portalPath } from './format.js';
import { app, has } from './state.js';

const declarationLevelTones={STATEMENT:0,MEMO:1,WARNING_1:2,WARNING_2:3};

function declarationLevelTone(code){if(Object.prototype.hasOwnProperty.call(declarationLevelTones,code))return declarationLevelTones[code];return [...String(code||'')].reduce((n,ch)=>n+ch.charCodeAt(0),0)%6;}

function declarationStatusBadge(value){const tone=value==='已生效'?'ok':value==='已作废'?'danger':'warn';return `<span class="badge status-chip ${tone}">${esc(value)}</span>`;}

function declarationRecordDetails(rows,levels=[],types=[]){
  if(!rows.length)return '<div class="empty">当前范围没有符合条件的记录</div>';
  const material=row=>row.document_url?`<button type="button" class="secondary declaration-material" data-file-preview="${esc(row.document_url)}" data-file-kind="pdf" data-file-title="${esc(row.employee_name)}的处分材料">查看材料</button> <a class="secondary declaration-material" href="${esc(portalPath(row.document_url))}" download>下载材料</a>`:'<span class="field-hint">暂无可查看材料</span>';
  const standalone=row=>!row.upgrade_request_id&&!row.upgrade_role;
  const editable=row=>has('SYSTEM_ADMIN')&&standalone(row)&&['active','pending_material','material_failed'].includes(row.raw_status);
  const type=row=>has('SYSTEM_ADMIN')&&standalone(row)&&['active','pending_material','material_failed','void'].includes(row.raw_status)?`<select class="declaration-type-select" aria-label="修改${esc(row.employee_name)}的处分类型" data-correct-type="${row.id}">${types.filter(x=>x.active!==false||x.id===row.type_id).map(x=>`<option value="${x.id}" ${x.id===row.type_id?'selected':''}>${esc(x.name)}</option>`).join('')}</select>`:esc(row.type_name);
  const actions=row=>has('SYSTEM_ADMIN')&&standalone(row)&&(editable(row)||row.raw_status==='void')?`<button type="button" class="secondary declaration-status-action" data-declaration-action="${row.raw_status==='void'?'restore':'void'}" data-record-id="${row.id}">${row.raw_status==='void'?'还原':'作废'}</button>`:'';
  const level=row=>editable(row)?`<select class="declaration-level-select" aria-label="修改${esc(row.employee_name)}的处分等级" data-correct-level="${row.id}" data-original-level="${row.level_id}">${levels.filter(x=>Object.hasOwn(declarationLevelTones,x.code)).map(x=>`<option value="${x.id}" ${x.id===row.level_id?'selected':''}>${esc(x.name)}</option>`).join('')}</select>`:`<span class="badge declaration-level tone-${declarationLevelTone(row.level_code)}">${esc(row.level_name)}</span>`;
  const classification=row=>`<div class="declaration-classification"><label><span>类型</span>${type(row)}</label><label><span>等级</span>${level(row)}</label></div>`;
  const workflow=row=>`<dl class="declaration-workflow"><div><dt>材料</dt><dd>${esc(row.material_status)}</dd></div><div><dt>审核 / 升级</dt><dd>${esc(row.upgrade_status)}</dd></div></dl>`;
  const operations=row=>`<div class="declaration-operations"><div class="declaration-material-actions">${material(row)}</div>${actions(row)}</div>`;
  return `<div class="table-wrap declaration-desktop"><table><colgroup><col><col><col><col><col><col></colgroup><thead><tr><th>员工与事件</th><th>处分信息</th><th>事件内容</th><th>登记信息</th><th>处理状态</th><th>材料与操作</th></tr></thead><tbody>${rows.map(row=>`<tr><td><div class="declaration-person"><strong>${esc(row.employee_name)}</strong><small class="declaration-employee-no">${esc(row.employee_no)}</small><span>${esc(row.attraction_name)}</span><time>${esc(row.occurred_on)}</time></div></td><td>${classification(row)}</td><td><p class="declaration-description">${esc(row.description)}</p></td><td><div class="declaration-registration"><strong>${esc(row.submitter_name)}</strong><time>${esc(row.submitted_at)}</time></div></td><td><div class="declaration-business-state">${declarationStatusBadge(row.business_status)}</div>${workflow(row)}</td><td>${operations(row)}</td></tr>`).join('')}</tbody></table></div><div class="declaration-mobile">${rows.map(row=>`<article class="declaration-record"><header class="declaration-record-heading"><div><strong>${esc(row.employee_name)}</strong><small>${esc(row.employee_no)}</small></div>${declarationStatusBadge(row.business_status)}</header><div class="declaration-record-context"><span>${esc(row.attraction_name)}</span><time>${esc(row.occurred_on)}</time></div>${classification(row)}<div class="declaration-record-content"><span class="declaration-field-caption">事件内容</span><p class="declaration-description">${esc(row.description)}</p></div>${workflow(row)}<div class="declaration-registration"><span class="declaration-field-caption">登记信息</span><strong>${esc(row.submitter_name)}</strong><time>${esc(row.submitted_at)}</time></div>${operations(row)}</article>`).join('')}</div>`;
}

async function renderDeclarationStatistics(){
  if(!has('DECLARATION_STATS_VIEW')){app.innerHTML='<section class="panel"><div class="error">当前账号没有声明登记统计查看权限</div></section>';return;}
  const request=beginViewRequest();
  const initial=await api('/api/declaration-statistics?month='+encodeURIComponent(monthNow()));
  if(!request.isCurrent())return;
  const levelOptions=initial.levels.map(row=>`<option value="${row.id}">${esc(row.name)}</option>`).join('');
  const circleOptions=initial.attractions.map(row=>`<option value="${row.id}">${esc(row.name)}</option>`).join('');
  const typeOptions=initial.types.map(row=>`<option value="${row.id}">${esc(row.name)}</option>`).join('');
  const canExport=has('DECLARATION_STATS_EXPORT');
  if(!request.write(`<div class="section-gap declaration-statistics-page"><section class="panel"><div class="statistics-page-heading"><h2>声明登记统计</h2><span class="access-mode ${canExport?'can-export':'read-only'}">${canExport?'可导出':'只读'}</span></div><p>汇总全部处分等级的登记事件；景点圈按登记时归属统计，升级关联记录不重复计数。</p><form id="declarationFilter" class="statistics-filter declaration-filter"><label>月份<input name="month" type="month" value="${monthNow()}" required></label><label>景点圈<select name="attraction_id"><option value="">全部景点圈</option>${circleOptions}</select></label><label>处分等级<select name="level_id"><option value="">全部等级</option>${levelOptions}</select></label><label>处分类型<select name="type_id"><option value="">全部类型</option>${typeOptions}</select></label><label>业务状态<select name="status"><option value="">全部状态</option>${['已生效','待补材料','审核中','已作废'].map(name=>`<option value="${name}">${name}</option>`).join('')}</select></label><label class="declaration-search">员工搜索<input name="keyword" placeholder="姓名或工号"></label><div class="statistics-actions"><button type="submit" class="primary">查询</button>${canExport?'<button type="button" class="secondary" id="declarationExport">导出 Excel</button>':''}</div></form></section><div id="declarationResults"></div></div>`))return;
  const form=document.getElementById('declarationFilter'),host=document.getElementById('declarationResults');
  const isCurrent=()=>request.isCurrent()&&app.contains(form);
  const ensureCurrentEdit=()=>{if(!isCurrent()||!has('SYSTEM_ADMIN'))throw new Error('页面或账号已变化，请关闭弹窗后重新查询');};
  let sequence=0;
  const show=data=>{
    const cards=`<div class="declaration-kpis"><div class="trend-card declaration-total"><span class="trend-card-label">总记录数</span><strong>${data.total_records}</strong></div><div class="trend-card declaration-total"><span class="trend-card-label">涉及人数</span><strong>${data.employee_count}</strong></div>${data.levels.map(row=>`<div class="trend-card declaration-kpi tone-${declarationLevelTone(row.code)}"><span class="trend-card-label">${esc(row.name)}</span><strong>${row.count}</strong></div>`).join('')}</div>`;
    const statuses=`<div class="declaration-statuses">${Object.entries(data.statuses).map(([name,count])=>`<span class="declaration-status-count">${declarationStatusBadge(name)}<strong>${count} 条</strong></span>`).join('')}</div>`;
    const circles=data.circles.map(circle=>{
      const key=circle.attraction_id===null?'none':String(circle.attraction_id);
      const rows=data.records.filter(row=>(row.attraction_id===null?'none':String(row.attraction_id))===key);
      return `<section class="declaration-circle"><button type="button" class="declaration-circle-toggle" data-declaration-circle="${esc(key)}" aria-expanded="false"><strong>${esc(circle.attraction_name)}</strong><span>${circle.record_count} 条 · ${circle.employee_count} 人</span><span aria-hidden="true">⌄</span></button><div class="declaration-inline" data-declaration-circle-detail="${esc(key)}" hidden></div><div class="declaration-levels">${circle.levels.map(level=>`<div class="declaration-level-item"><button type="button" class="declaration-level-toggle tone-${declarationLevelTone(level.code)}" data-declaration-level="${esc(key)}:${level.id}" aria-expanded="false"><span>${esc(level.name)}</span><strong>${level.count} 条</strong><span aria-hidden="true">⌄</span></button><div class="declaration-inline" data-declaration-level-detail="${esc(key)}:${level.id}" hidden></div></div>`).join('')}</div></section>`;
    }).join('')||'<div class="empty">当前筛选范围暂无声明登记记录</div>';
    host.innerHTML=`<section class="panel"><h2>月度汇总</h2><p class="statistics-scope">${esc(data.month)} · ${data.total_records} 条记录；点击景点圈或处分等级可展开明细，再次点击收起。</p>${cards}${statuses}<div class="declaration-circle-list">${circles}</div></section>`;
    host.querySelectorAll('[data-declaration-circle]').forEach(button=>button.onclick=()=>{const key=button.dataset.declarationCircle,detail=host.querySelector(`[data-declaration-circle-detail="${key}"]`),open=detail.hidden;detail.hidden=!open;detail.innerHTML=open?declarationRecordDetails(data.records.filter(row=>(row.attraction_id===null?'none':String(row.attraction_id))===key),data.levels,data.types):'';button.setAttribute('aria-expanded',String(open));if(open){bindFilePreviews(detail);bindLevelCorrections(detail,data);}});
    host.querySelectorAll('[data-declaration-level]').forEach(button=>button.onclick=()=>{const key=button.dataset.declarationLevel,[circleId,levelId]=key.split(':'),detail=host.querySelector(`[data-declaration-level-detail="${key}"]`),open=detail.hidden;detail.hidden=!open;detail.innerHTML=open?declarationRecordDetails(data.records.filter(row=>(row.attraction_id===null?'none':String(row.attraction_id))===circleId&&String(row.level_id)===levelId),data.levels,data.types):'';button.setAttribute('aria-expanded',String(open));if(open){bindFilePreviews(detail);bindLevelCorrections(detail,data);}});
  };
  const bindLevelCorrections=(detail,data)=>{
    detail.querySelectorAll('[data-correct-type]').forEach(select=>select.onchange=async()=>{
      const row=data.records.find(x=>x.id===Number(select.dataset.correctType)),typeId=Number(select.value);
      select.value=String(row.type_id);if(typeId===row.type_id)return;
      const target=data.types.find(x=>x.id===typeId);
      const saved=await formModal('修改处分类型',`<p>${esc(row.employee_name)} · ${esc(row.employee_no)}</p><p>${esc(row.type_name)} → ${esc(target.name)}</p><p>统计分类将更新，扣分按更正后的类别规则核算；原材料和登记内容保留。</p>`, '<label>修改原因<textarea name="reason" maxlength="500" required></textarea></label>','确认修改',{lockWhileSubmitting:true,onSubmit:async f=>{
        ensureCurrentEdit();
        const reason=f.elements.reason.value.trim();if(!reason)throw new Error('请填写修改原因');
        await api('/api/deductions/'+row.id+'/type',json('PATCH',{type_id:typeId,expected_type_id:row.type_id,expected_level_id:row.level_id,expected_status:row.raw_status,reason}));
      }});if(saved&&request.isCurrent())await load();
    });
    detail.querySelectorAll('[data-declaration-action]').forEach(button=>button.onclick=async()=>{
      const row=data.records.find(x=>x.id===Number(button.dataset.recordId)),action=button.dataset.declarationAction,label=action==='restore'?'还原':'作废';
      const saved=await formModal(label+'记录',`<p>${esc(row.employee_name)} · ${esc(row.employee_no)} · ${esc(row.type_name)} · ${esc(row.level_name)}</p><p>${action==='restore'?'恢复作废前业务状态，原已生效记录恢复计分。':'作废后该记录退出计分，保留历史记录和材料。'}已月结及升级工单关联记录不能在此操作。</p>`, '<label>操作原因<textarea name="reason" maxlength="500" required></textarea></label>','确认'+label,{lockWhileSubmitting:true,onSubmit:async f=>{
        ensureCurrentEdit();
        const reason=f.elements.reason.value.trim();if(!reason)throw new Error('请填写操作原因');
        await api('/api/deductions/'+row.id+'/'+action,json('POST',{reason,expected_status:row.raw_status,expected_voided_at:row.voided_at,expected_type_id:row.type_id,expected_level_id:row.level_id}));
      }});if(saved&&request.isCurrent())await load();
    });

    detail.querySelectorAll('[data-correct-level]').forEach(select=>select.onchange=async()=>{
      const row=data.records.find(x=>x.id===Number(select.dataset.correctLevel)),levelId=Number(select.value);
      select.value=String(row.level_id);
      if(levelId===row.level_id)return;
      const target=data.levels.find(x=>x.id===levelId);
      const saved=await formModal('修改处分级别',`<p>${esc(row.employee_name)} · ${esc(row.employee_no)} · ${esc(row.occurred_on)}</p><p>${esc(row.level_name)} → ${esc(target.name)}</p><p>该记录的扣分同步按新级别更新，原登记内容与材料保留。</p>`, '<label>修改原因<textarea name="reason" maxlength="500" required></textarea></label>','确认修改',{lockWhileSubmitting:true,onSubmit:async f=>{
        ensureCurrentEdit();
        const reason=f.elements.reason.value.trim();if(!reason)throw new Error('请填写修改原因');
        await api('/api/deductions/'+row.id+'/level',json('PATCH',{level_id:levelId,expected_level_id:row.level_id,expected_type_id:row.type_id,expected_status:row.raw_status,reason}));
      }});
      if(saved&&request.isCurrent())await load();
    });
  };
  const params=()=>new URLSearchParams([...new FormData(form)].filter(([,value])=>value));
  const load=async()=>{if(!isCurrent())return;const id=++sequence,button=form.querySelector('button[type="submit"]');button.disabled=true;host.innerHTML='<section class="panel"><div class="empty">正在加载统计数据…</div></section>';try{const data=await api('/api/declaration-statistics?'+params());if(id===sequence&&isCurrent())show(data);}catch(error){if(id===sequence&&isCurrent())host.innerHTML=`<section class="panel"><div class="error">${esc(error.message)}</div></section>`;}finally{if(id===sequence)button.disabled=false;}};
  form.onsubmit=event=>{event.preventDefault();void load();};
  if(canExport)document.getElementById('declarationExport').onclick=async event=>{
    const button=event.currentTarget,filters=params();
    const confirmed=await confirmModal('导出声明登记统计',`<p>将按当前筛选条件导出 ${esc(filters.get('month'))} 的景点圈、等级汇总和逐条明细。</p>`,'导出 Excel');
    if(!confirmed||!isCurrent()||!has('DECLARATION_STATS_EXPORT')||button.disabled)return;
    button.disabled=true;
    try{await queueExport('/api/declaration-statistics/export?'+filters);if(isCurrent())toast('已加入导出任务');}
    catch(error){if(isCurrent())toast(error.message,true);}
    finally{button.disabled=false;}
  };
  show(initial);
}

export { renderDeclarationStatistics };
