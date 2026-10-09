// Declaration statistics and record detail presentation.
import { api } from './api.js';
import { beginViewRequest } from './context.js';
import { confirmModal } from './dialogs.js';
import { bindFilePreviews } from './files.js';
import { esc, monthNow, portalPath } from './format.js';
import { app, has } from './state.js';

const declarationLevelTones={STATEMENT:0,MEMO:1,WARNING_1:2,WARNING_2:3};

function declarationLevelTone(code){if(Object.prototype.hasOwnProperty.call(declarationLevelTones,code))return declarationLevelTones[code];return [...String(code||'')].reduce((n,ch)=>n+ch.charCodeAt(0),0)%6;}

function declarationStatusBadge(value){const tone=value==='已生效'?'ok':value==='已作废'?'danger':'warn';return `<span class="badge status-chip ${tone}">${esc(value)}</span>`;}

function declarationRecordDetails(rows){
  if(!rows.length)return '<div class="empty">当前范围没有符合条件的记录</div>';
  const material=row=>row.document_url?`<button type="button" class="secondary declaration-material" data-file-preview="${esc(row.document_url)}" data-file-kind="pdf" data-file-title="${esc(row.employee_name)}的处分材料">查看材料</button> <a class="secondary declaration-material" href="${esc(portalPath(row.document_url))}" download>下载材料</a>`:'<span class="field-hint">暂无可查看材料</span>';
  const level=row=>`<span class="badge declaration-level tone-${declarationLevelTone(row.level_code)}">${esc(row.level_name)}</span>`;
  return `<div class="table-wrap declaration-desktop"><table><thead><tr><th>员工 / 工号</th><th>原景点圈</th><th>事件日期</th><th>处分类型 / 等级</th><th>内容</th><th>登记人 / 时间</th><th>材料状态</th><th>审核或升级状态</th><th>业务状态</th><th>材料</th></tr></thead><tbody>${rows.map(row=>`<tr><td><strong>${esc(row.employee_name)}</strong><br><small>${esc(row.employee_no)}</small></td><td>${esc(row.attraction_name)}</td><td>${esc(row.occurred_on)}</td><td>${esc(row.type_name)}<br>${level(row)}</td><td>${esc(row.description)}</td><td>${esc(row.submitter_name)}<br><small>${esc(row.submitted_at)}</small></td><td>${esc(row.material_status)}</td><td>${esc(row.upgrade_status)}</td><td>${declarationStatusBadge(row.business_status)}</td><td>${material(row)}</td></tr>`).join('')}</tbody></table></div><div class="declaration-mobile">${rows.map(row=>`<article class="declaration-record"><div class="record-line"><strong>${esc(row.employee_name)} · ${esc(row.employee_no)}</strong>${declarationStatusBadge(row.business_status)}</div><p>${esc(row.attraction_name)} · ${esc(row.occurred_on)}</p><div class="record-line"><span>${esc(row.type_name)}</span>${level(row)}</div><p>${esc(row.description)}</p><small>登记：${esc(row.submitter_name)} · ${esc(row.submitted_at)}</small><small>材料：${esc(row.material_status)} · 审核/升级：${esc(row.upgrade_status)}</small>${material(row)}</article>`).join('')}</div>`;
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
  if(!request.write(`<div class="section-gap declaration-statistics-page"><section class="panel"><div class="statistics-page-heading"><h2>声明登记统计</h2><span class="access-mode ${canExport?'can-export':'read-only'}">${canExport?'可导出':'只读'}</span></div><p>汇总全部处分等级的登记事件；景点圈按登记时归属统计，升级关联记录不重复计数。</p><form id="declarationFilter" class="statistics-filter declaration-filter"><label>月份<input name="month" type="month" value="${monthNow()}" required></label><label>景点圈<select name="attraction_id"><option value="">全部景点圈</option>${circleOptions}</select></label><label>处分等级<select name="level_id"><option value="">全部等级</option>${levelOptions}</select></label><label>处分类型<select name="type_id"><option value="">全部类型</option>${typeOptions}</select></label><label>业务状态<select name="status"><option value="">全部状态</option>${['已生效','待补材料','审核中','已作废'].map(name=>`<option value="${name}">${name}</option>`).join('')}</select></label><label>员工搜索<input name="keyword" placeholder="姓名或工号"></label><div class="statistics-actions"><button type="submit" class="primary">查询</button>${canExport?'<button type="button" class="secondary" id="declarationExport">导出 Excel</button>':''}</div></form></section><div id="declarationResults"></div></div>`))return;
  const form=document.getElementById('declarationFilter'),host=document.getElementById('declarationResults');
  let sequence=0;
  const show=data=>{
    const cards=`<div class="declaration-kpis"><div class="trend-card"><span class="trend-card-label">总记录数</span><strong>${data.total_records}</strong></div><div class="trend-card"><span class="trend-card-label">涉及人数</span><strong>${data.employee_count}</strong></div>${data.levels.map(row=>`<div class="trend-card declaration-kpi tone-${declarationLevelTone(row.code)}"><span class="trend-card-label">${esc(row.name)}</span><strong>${row.count}</strong></div>`).join('')}</div>`;
    const statuses=`<div class="declaration-statuses">${Object.entries(data.statuses).map(([name,count])=>`<span class="declaration-status-count">${declarationStatusBadge(name)}<strong>${count} 条</strong></span>`).join('')}</div>`;
    const circles=data.circles.map(circle=>{
      const key=circle.attraction_id===null?'none':String(circle.attraction_id);
      const rows=data.records.filter(row=>(row.attraction_id===null?'none':String(row.attraction_id))===key);
      return `<section class="declaration-circle"><button type="button" class="declaration-circle-toggle" data-declaration-circle="${esc(key)}" aria-expanded="false"><strong>${esc(circle.attraction_name)}</strong><span>${circle.record_count} 条 · ${circle.employee_count} 人</span><span aria-hidden="true">⌄</span></button><div class="declaration-inline" data-declaration-circle-detail="${esc(key)}" hidden></div><div class="declaration-levels">${circle.levels.map(level=>`<div class="declaration-level-item"><button type="button" class="declaration-level-toggle tone-${declarationLevelTone(level.code)}" data-declaration-level="${esc(key)}:${level.id}" aria-expanded="false"><span>${esc(level.name)}</span><strong>${level.count} 条</strong><span aria-hidden="true">⌄</span></button><div class="declaration-inline" data-declaration-level-detail="${esc(key)}:${level.id}" hidden></div></div>`).join('')}</div></section>`;
    }).join('')||'<div class="empty">当前筛选范围暂无声明登记记录</div>';
    host.innerHTML=`<section class="panel"><h2>月度汇总</h2><p class="statistics-scope">${esc(data.month)} · ${data.total_records} 条记录；点击景点圈或处分等级可展开明细，再次点击收起。</p>${cards}${statuses}<div class="declaration-circle-list">${circles}</div></section>`;
    host.querySelectorAll('[data-declaration-circle]').forEach(button=>button.onclick=()=>{const key=button.dataset.declarationCircle,detail=host.querySelector(`[data-declaration-circle-detail="${key}"]`),open=detail.hidden;detail.hidden=!open;detail.innerHTML=open?declarationRecordDetails(data.records.filter(row=>(row.attraction_id===null?'none':String(row.attraction_id))===key)):'';button.setAttribute('aria-expanded',String(open));if(open)bindFilePreviews(detail);});
    host.querySelectorAll('[data-declaration-level]').forEach(button=>button.onclick=()=>{const key=button.dataset.declarationLevel,[circleId,levelId]=key.split(':'),detail=host.querySelector(`[data-declaration-level-detail="${key}"]`),open=detail.hidden;detail.hidden=!open;detail.innerHTML=open?declarationRecordDetails(data.records.filter(row=>(row.attraction_id===null?'none':String(row.attraction_id))===circleId&&String(row.level_id)===levelId)):'';button.setAttribute('aria-expanded',String(open));if(open)bindFilePreviews(detail);});
  };
  const params=()=>new URLSearchParams([...new FormData(form)].filter(([,value])=>value));
  const load=async()=>{const id=++sequence,button=form.querySelector('button[type="submit"]');button.disabled=true;host.innerHTML='<section class="panel"><div class="empty">正在加载统计数据…</div></section>';try{const data=await api('/api/declaration-statistics?'+params());if(id===sequence&&app.contains(form))show(data);}catch(error){if(id===sequence&&app.contains(form))host.innerHTML=`<section class="panel"><div class="error">${esc(error.message)}</div></section>`;}finally{if(id===sequence)button.disabled=false;}};
  form.onsubmit=event=>{event.preventDefault();void load();};
  if(canExport)document.getElementById('declarationExport').onclick=async()=>{const confirmed=await confirmModal('导出声明登记统计',`<p>将按当前筛选条件导出 ${esc(form.elements.month.value)} 的景点圈、等级汇总和逐条明细。</p>`,'导出 Excel');if(confirmed)location.href=portalPath('/api/declaration-statistics/export?'+params());};
  show(initial);
}

export { renderDeclarationStatistics };
