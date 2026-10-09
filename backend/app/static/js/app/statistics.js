// Statistics hierarchy, detail navigation and filtered queries.
import { api } from './api.js';
import { beginViewRequest, pageTimeout, render } from './context.js';
import { confirmModal } from './dialogs.js';
import { bindFilePreviews } from './files.js';
import { actingNoteMarkup, esc, fmt, fmtDeduction, monthNow, portalPath } from './format.js';
import { memberRecordCard } from './members.js';
import { renderTabs } from './navigation.js';
import { opt } from './records.js';
import { app, has, state } from './state.js';
import { toast } from './toast.js';
import { TREND_SPANS, trendResultsHtml } from './trends.js';

const statisticsDetailContext = () => new URLSearchParams(location.search);

function statisticsHierarchyRows(rows,emptyMessage='当前条件下暂无数据'){
  if(!rows.length)return `<tr><td colspan="6" class="empty">${esc(emptyMessage)}</td></tr>`;
  const scoreButton=(row,kind,value,scoreClass='')=>`<button type="button" class="statistics-score-link ${scoreClass}" data-stats-detail="${kind}" data-employee-ids="${esc((row.employee_ids||[row.employee_id]).join(','))}" data-employee-name="${esc(row.name||row.employee_name)}" data-score="${esc(value)}" aria-expanded="false">${esc(value)}</button>`;
  return rows.map(row=>{
    if(row.node_type!=='employee'){
      const meta=row.node_type==='attraction'?'景点圈':row.node_type==='gsm'?(row.role_name||'GSM'):row.node_type==='gsm_team'?`共同承接 · ${row.supervisor_count||0}个主管 · CM ${row.cm_count||0}人 · TR ${row.tr_count||0}人（共${row.member_count||0}人）`:`${row.role_name||'主管'} · ${row.member_count||0}人`;
      const scores=row.node_type==='supervisor'?`<td>${scoreButton(row,'recognitions','+'+fmt(row.recognition_score),'score-positive')}</td><td>${scoreButton(row,'deductions',fmtDeduction(row.deduction_score),'score-negative')}</td><td>${scoreButton(row,'attendance',fmt(row.attendance_score))}</td><td>${scoreButton(row,'all',fmt(row.total_score),'member-total-link')}</td><td><strong>${fmt(row.average_score)}</strong><small class="average-calculation">${fmt(row.total_score)} ÷ ${row.member_count||0}人</small></td>`:'<td></td><td></td><td></td><td></td><td></td>';
      const expanded=row.node_type!=='supervisor';
      return `<tr class="statistics-org-row ${row.node_type==='supervisor'?'statistics-score-row statistics-supervisor-row':''}" data-stats-node="${esc(row.node_id)}" data-stats-parent="${esc(row.parent_id)}" data-level="${row.level}"><th><button type="button" class="statistics-tree-toggle" data-stats-toggle="${esc(row.node_id)}" aria-expanded="${expanded}"><span class="statistics-tree-arrow">${expanded?'⌄':'›'}</span><strong>${esc(row.name)}</strong><small>${esc(meta)}</small></button></th>${scores}</tr>`;
    }
    return `<tr class="statistics-score-row statistics-employee-row" data-stats-node="${esc(row.node_id)}" data-stats-parent="${esc(row.parent_id)}" data-level="3"><th><strong>${esc(row.employee_name)}</strong><small>${esc(row.employee_no)} · ${esc(row.role_name||row.role_code)}</small>${actingNoteMarkup(row)}</th><td>${scoreButton(row,'recognitions','+'+fmt(row.recognition_score),'score-positive')}</td><td>${scoreButton(row,'deductions',fmtDeduction(row.deduction_score),'score-negative')}</td><td>${scoreButton(row,'attendance',fmt(row.attendance_score))}</td><td>${scoreButton(row,'all',fmt(row.total_score),'member-total-link')}</td><td></td></tr>`;
  }).join('');
}

function statisticsDetailHtml(data,button){
  const ids=(button.dataset.employeeIds||'').split(',').filter(Boolean),kind=button.dataset.statsDetail;
  const employeeRows=new Map(data.hierarchy.filter(row=>row.node_type==='employee').map(row=>[String(row.employee_id),row]));
  let records=[];
  ids.forEach(id=>{const detail=data.details[id]||{all_records:[]},employee=employeeRows.get(id)||{};records.push(...(detail.all_records||[]).map(record=>({...record,employee_name:employee.employee_name||'',employee_no:employee.employee_no||''}))) });
  if(kind==='recognitions')records=records.filter(record=>record.record_type==='recognition');
  else if(kind==='deductions')records=records.filter(record=>record.record_type==='deduction');
  else if(kind==='attendance')records=records.filter(record=>['attendance','sick_leave'].includes(record.record_type));
  records.sort((a,b)=>`${b.business_date}|${b.submitted_at}`.localeCompare(`${a.business_date}|${a.submitted_at}`));
  const titles={recognitions:'加分明细',deductions:'扣分明细',attendance:'全勤分明细',all:'全部记录'};
  const scopeName=ids.length>1?`${button.dataset.employeeName} · ${ids.length}名直属员工`:button.dataset.employeeName;
  return `<section class="panel statistics-detail-panel"><div class="statistics-detail-heading"><div><h2>${esc(scopeName)} · ${titles[kind]}</h2><span>合计 ${esc(button.dataset.score)}；未计入或不通过记录仍会显示</span></div><button type="button" class="secondary" id="closeStatsDetail">关闭</button></div><div class="member-detail-list">${records.map(memberRecordCard).join('')||'<div class="empty">本月暂无相关记录</div>'}</div></section>`;
}

function statisticsDetailKinds(kind){
  if(kind==='recognitions')return ['recognition'];
  if(kind==='deductions')return ['deduction'];
  if(kind==='attendance')return ['attendance','sick_leave'];
  return ['recognition','deduction','attendance','sick_leave'];
}

function statisticsDetailTitle(kind){
  return {recognitions:'加分有效明细',deductions:'扣分有效明细',attendance:'全勤分有效明细',all:'综合分有效明细'}[kind]||'员工有效绩效明细';
}

function statisticsDetailUrl({month,employeeIds,kind,returnFilters={}}){
  const params=new URLSearchParams({month:String(month),employee_ids:(employeeIds||[]).join(','),kind:String(kind||'all')});
  ['attraction_id','title','keyword'].forEach(key=>{const value=returnFilters[key];if(value)params.set(`return_${key}`,String(value));});
  return `${location.pathname}?${params.toString()}`;
}

function openStatisticsDetail(context){
  state.tab='statisticsDetail';
  history.pushState({statisticsDetail:true},'',statisticsDetailUrl(context));
  renderTabs();
  render();
}

async function renderStatisticsDetail(){
  const request=beginViewRequest();
  const context=statisticsDetailContext(),month=context.get('month')||monthNow(),employeeIds=(context.get('employee_ids')||'').split(',').filter(value=>/^\d+$/.test(value)),kind=context.get('kind')||'all';
  if(!employeeIds.length){app.innerHTML='<section class="panel"><div class="error">缺少员工明细参数</div></section>';return;}
  const returnFilters={attraction_id:context.get('return_attraction_id')||'',title:context.get('return_title')||'',keyword:context.get('return_keyword')||''};
  app.innerHTML='<section class="panel"><div class="empty">正在加载有效绩效明细…</div></section>';
  try{
    const payload=await api(`/api/statistics/details?month=${encodeURIComponent(month)}&employee_ids=${encodeURIComponent(employeeIds.join(','))}&effective_only=true`),details=payload.details||{},rows=Object.values(details),allowedTypes=new Set(statisticsDetailKinds(kind));
    if(!request.isCurrent())return;
    const records=rows.flatMap(row=>(row.all_records||[]).filter(record=>allowedTypes.has(record.record_type)).map(record=>({...record,employee_name:row.employee_name,employee_no:row.employee_no,role_name:row.role_name}))).sort((a,b)=>`${b.business_date}|${b.submitted_at}`.localeCompare(`${a.business_date}|${a.submitted_at}`));
    const employeeLabel=rows.length===1&&rows[0]?`${rows[0].employee_name} · ${rows[0].employee_no}`:`${rows.length}名员工`;
    app.innerHTML=`<div class="section-gap"><section class="panel statistics-detail-page"><div class="statistics-detail-heading"><div><h2>${esc(employeeLabel)} · ${statisticsDetailTitle(kind)}</h2><span>${esc(month)} · 仅展示已计入综合分的有效数据</span></div><button type="button" class="secondary" id="backToStatistics">返回组织树</button></div><div class="member-detail-list">${records.map(memberRecordCard).join('')||'<div class="empty">当前月份暂无有效记录</div>'}</div></section></div>`;
    bindFilePreviews(app);
    document.getElementById('backToStatistics').onclick=()=>{
      state.tab='statistics';history.pushState({statistics:true},'',location.pathname);
      try{localStorage.setItem(`recognition-v2:statistics-filters:${state.me.employee_no}`,JSON.stringify({month,attraction_id:returnFilters.attraction_id,title:returnFilters.title,keyword:returnFilters.keyword}));}catch(_error){}
      renderTabs();render();
    };
  }catch(error){if(!request.isCurrent()||error?.name==='AbortError')return;app.innerHTML=`<section class="panel"><div class="error">${esc(error.message)}</div></section>`;}
}

function bindStatisticsHierarchy(data,{expandEmployees=false}={}){
  const rows=[...document.querySelectorAll('[data-stats-node]')],collapsed=new Set(expandEmployees?[]:rows.filter(row=>row.classList.contains('statistics-supervisor-row')).map(row=>row.dataset.statsNode));
  const rowMap=new Map(rows.map(row=>[row.dataset.statsNode,row]));
  const refresh=()=>rows.forEach(row=>{let parent=row.dataset.statsParent,hidden=false;while(parent){if(collapsed.has(parent)){hidden=true;break}parent=rowMap.get(parent)?.dataset.statsParent||''}row.hidden=hidden});
  document.querySelectorAll('[data-stats-toggle]').forEach(button=>button.onclick=()=>{const id=button.dataset.statsToggle,willCollapse=!collapsed.has(id);if(willCollapse)collapsed.add(id);else collapsed.delete(id);button.setAttribute('aria-expanded',String(!willCollapse));button.querySelector('.statistics-tree-arrow').textContent=willCollapse?'›':'⌄';refresh()});
  document.querySelectorAll('[data-stats-detail]').forEach(button=>button.onclick=()=>openStatisticsDetail({month:data.month,employeeIds:(button.dataset.employeeIds||'').split(',').filter(Boolean),kind:button.dataset.statsDetail,returnFilters:data.filters||{}}));
  refresh();
}

async function renderStatistics(){
  const request=beginViewRequest();
  const canExport=has('DATA_EXPORT');
  app.innerHTML=`<div class="section-gap"><section class="panel"><div class="statistics-page-heading"><h2>${canExport?'景点数据统计与月结导出':'景点数据统计'}</h2><span class="access-mode ${canExport?'can-export':'read-only'}">${canExport?'可导出':'只读'}</span></div><form id="statsFilter" class="statistics-filter"><label>月份<input name="month" type="month" value="${monthNow()}" required></label><label>景点圈<select name="attraction_id"><option value="">全部</option>${opt(state.options.employee_circles||state.options.attractions)}</select></label><label>Title<select name="title"><option value="">CM/TR全部</option><option value="CM">CM</option><option value="TR">TR</option><option value="SUPERVISOR">主管</option></select></label><label>员工搜索<input name="keyword" placeholder="姓名/员工号"></label><div class="statistics-actions"><button class="primary">查询统计</button>${canExport?' <button type="button" id="exportBtn" class="secondary">导出 Excel</button>':''}</div></form>${canExport?'<div id="recentExports" class="recent-exports"><span class="field-hint">正在加载最近导出记录…</span></div>':''}</section><section class="panel statistics-views"><div class="statistics-view-tabs" role="tablist"><button type="button" class="primary" data-stats-view="month" role="tab" aria-selected="true">当月统计</button><button type="button" class="secondary" data-stats-view="trend" role="tab" aria-selected="false">月度趋势</button></div><label class="trend-span" hidden>回溯月数<select id="trendSpan">${TREND_SPANS.map(n=>`<option value="${n}"${n===6?' selected':''}>${n} 个月</option>`).join('')}</select></label></section><div id="statsResults"></div></div>`;
  const form=document.getElementById('statsFilter');
  const preferenceKey=`recognition-v2:statistics-filters:${state.me.employee_no}`,queryButton=form.querySelector('button[type="submit"],button:not([type])'),resultsHost=document.getElementById('statsResults');
  try{const saved=JSON.parse(localStorage.getItem(preferenceKey)||'{}');['month','attraction_id','title','keyword'].forEach(name=>{if(!Object.prototype.hasOwnProperty.call(saved,name))return;const field=form.elements[name],value=String(saved[name]??'');if(name==='month'&&!value)return;if(field&&(!field.options||[...field.options].some(option=>option.value===value)))field.value=value})}catch(_error){}
  let lastStatistics=null,loadSequence=0;
  const saveFilters=()=>{try{localStorage.setItem(preferenceKey,JSON.stringify({month:form.elements.month.value,attraction_id:form.elements.attraction_id.value,title:form.elements.title.value,keyword:form.elements.keyword.value}))}catch(_error){}};
  const emptyMessage=formData=>{const values=Object.fromEntries(formData);if(String(values.keyword||'').trim())return '没有匹配当前姓名或员工号的参与计分人员';if(values.attraction_id)return '该景点圈本月没有参与计分人员';if(values.title)return `本月没有符合 ${values.title} 条件的参与计分人员`;return '本月尚无业务数据或参与计分人员'};
  const load=async()=>{if(!request.isCurrent())return;const requestId=++loadSequence,formData=[...new FormData(form)],qs=new URLSearchParams(formData.filter(([,v])=>v)),expandEmployees=Boolean(String(formData.find(([key])=>key==='keyword')?.[1]||'').trim()),originalText=queryButton.textContent;saveFilters();queryButton.disabled=true;queryButton.textContent='查询中…';resultsHost.innerHTML='<section class="panel"><div class="empty">正在加载统计数据…</div></section>';try{const d=await api('/api/statistics?'+qs);if(requestId!==loadSequence||!request.isCurrent())return;lastStatistics=d;const selectedTitle=d.title||'',averageLabel=selectedTitle?`${selectedTitle}小组平均分`:'CM/TR小组平均分',attractionSelect=form.elements.attraction_id,attractionLabel=attractionSelect.selectedOptions[0]?.textContent||'全部',updatedLabel=d.data_updated_at||'暂无业务更新',organizationBasis=d.organization_basis||'当前组织归属';resultsHost.innerHTML=`<section class="panel"><h2>员工综合分</h2><p class="statistics-scope">统计范围：${esc(d.month)} · ${esc(attractionLabel)} · ${esc(selectedTitle==='SUPERVISOR'?'主管':(selectedTitle||'CM/TR全部'))}；数据更新至 ${esc(updatedLabel)}；组织口径：${esc(organizationBasis)}；平均分按筛选后小组综合分 ÷ 筛选后人数计算。</p><div class="table-wrap"><table class="statistics-hierarchy-table"><thead><tr><th>组织 / 员工</th><th>加分</th><th>扣分</th><th>全勤分</th><th>小组综合分</th><th>${esc(averageLabel)}</th></tr></thead><tbody>${statisticsHierarchyRows(d.hierarchy,emptyMessage(formData))}</tbody></table></div></section><div id="statisticsDetail"></div>`;bindStatisticsHierarchy(d,{expandEmployees})}catch(error){if(requestId===loadSequence&&request.isCurrent())resultsHost.innerHTML=`<section class="panel"><div class="error">${esc(error.message)}</div></section>`}finally{if(requestId===loadSequence&&request.isCurrent()){queryButton.disabled=false;queryButton.textContent=originalText}}};
  const loadRecentExports=async()=>{if(!canExport)return;const host=document.getElementById('recentExports');if(!request.isCurrent())return;try{const data=await api('/api/statistics/my-exports');if(!request.isCurrent())return;host.innerHTML=data.items.length?`<h3>本人最近景点数据导出</h3><div class="table-wrap"><table class="recent-export-table"><thead><tr><th>导出时间</th><th>月份</th><th>景点圈</th><th>Title</th><th>员工搜索</th><th>员工数</th></tr></thead><tbody>${data.items.map(row=>`<tr><td>${esc(row.exported_at)}</td><td>${esc(row.month)}</td><td>${esc(row.attraction_name)}</td><td>${esc(row.title)}</td><td>${esc(row.keyword||'无')}</td><td>${row.employee_count}</td></tr>`).join('')}</tbody></table></div>`:'<span class="field-hint">当前账号暂无景点数据导出记录</span>'}catch(error){if(!request.isCurrent()||error?.name==='AbortError')return;host.innerHTML=`<span class="field-hint">最近导出记录暂时无法加载：${esc(error.message)}</span>`}};
  const spanLabel=app.querySelector('.trend-span'),spanSelect=document.getElementById('trendSpan'),viewButtons=[...app.querySelectorAll('[data-stats-view]')];let statsView='month';
  const loadTrend=async()=>{if(!request.isCurrent())return;const requestId=++loadSequence,qs=new URLSearchParams();qs.set('month',form.elements.month.value||monthNow());if(form.elements.attraction_id.value)qs.set('attraction_id',form.elements.attraction_id.value);if(form.elements.title.value)qs.set('title',form.elements.title.value);qs.set('months',spanSelect.value);resultsHost.innerHTML='<section class="panel"><div class="empty">正在加载趋势数据…</div></section>';try{const d=await api('/api/statistics/trend?'+qs);if(requestId!==loadSequence||!request.isCurrent())return;resultsHost.innerHTML=trendResultsHtml(d);}catch(error){if(requestId!==loadSequence||!request.isCurrent())return;resultsHost.innerHTML=`<section class="panel"><div class="empty">${esc(error.message)}</div></section>`;toast(error.message,true);}};
  const loadCurrent=()=>statsView==='trend'?loadTrend():load();
  viewButtons.forEach(button=>button.onclick=()=>{if(statsView===button.dataset.statsView)return;statsView=button.dataset.statsView;viewButtons.forEach(other=>{const on=other.dataset.statsView===statsView;other.className=on?'primary':'secondary';other.setAttribute('aria-selected',on?'true':'false');});spanLabel.hidden=statsView!=='trend';const exportButton=document.getElementById('exportBtn');if(exportButton)exportButton.hidden=statsView==='trend';loadCurrent();});
  spanSelect.onchange=()=>{if(statsView==='trend')loadCurrent();};
  form.onsubmit=e=>{e.preventDefault();if(!queryButton.disabled)loadCurrent()};
  if(canExport){const exportButton=document.getElementById('exportBtn');exportButton.onclick=async()=>{if(exportButton.disabled)return;const formData=[...new FormData(form)],values=Object.fromEntries(formData),attractionLabel=form.elements.attraction_id.selectedOptions[0]?.textContent||'全部',titleLabel=values.title==='SUPERVISOR'?'主管':(values.title||'CM/TR全部'),keywordLabel=String(values.keyword||'').trim(),count=lastStatistics?.summary?.employee_count??0,loaCount=lastStatistics?.loa_rows?.length??0,confirmed=await confirmModal('确认导出景点数据',`<p><strong>${esc(values.month)}</strong> · ${esc(attractionLabel)} · ${esc(titleLabel)}</p>${keywordLabel?`<p>员工搜索：${esc(keywordLabel)}</p>`:''}<p>预计包含 ${count} 名参与计分人员${loaCount?`，另有 ${loaCount} 名整月LOA员工`:''}。</p>`,'导出 Excel');if(!confirmed)return;exportButton.disabled=true;exportButton.textContent='准备导出…';const qs=new URLSearchParams(formData.filter(([,v])=>v));location.href=portalPath('/api/statistics/export?'+qs);pageTimeout(()=>{if(!request.isCurrent())return;exportButton.disabled=false;exportButton.textContent='导出 Excel';loadRecentExports()},1400)}}
  await Promise.all([loadCurrent(),loadRecentExports()]);
}

export { renderStatistics, renderStatisticsDetail, statisticsDetailContext };
