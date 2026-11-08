import { queueExport } from './export-tasks.js';
// Declaration statistics and record detail presentation.
import { api, json } from './api.js';
import { toast } from './toast.js';
import { beginViewRequest, registerPageCleanup, unregisterPageCleanup } from './context.js';
import { confirmModal, formModal } from './dialogs.js';
import { bindFilePreviews } from './files.js';
import { esc, monthNow, portalPath } from './format.js';
import { app, has } from './state.js';

const declarationLevelTones={STATEMENT:0,MEMO:1,WARNING_1:2,WARNING_2:3};
let disposeDeclarationSearch=null;

function declarationLevelTone(code){if(Object.prototype.hasOwnProperty.call(declarationLevelTones,code))return declarationLevelTones[code];return [...String(code||'')].reduce((n,ch)=>n+ch.charCodeAt(0),0)%6;}

function declarationStatusBadge(value){const tone=value==='已生效'?'ok':value==='已作废'?'danger':'warn';return `<span class="badge status-chip ${tone}">${esc(value)}</span>`;}

function declarationRecordDetails(rows,levels=[],types=[],readOnly=false){
  if(!rows.length)return '<div class="empty">当前范围没有符合条件的记录</div>';
  const material=row=>row.document_url?`<button type="button" class="secondary declaration-material" data-file-preview="${esc(row.document_url)}" data-file-kind="pdf" data-file-title="${esc(row.employee_name)}的处分材料">查看材料</button> <a class="secondary declaration-material" href="${esc(portalPath(row.document_url))}" download>下载材料</a>`:'<span class="field-hint">暂无可查看材料</span>';
  const standalone=row=>!row.upgrade_request_id&&!row.upgrade_role&&!row.upgrade_links?.length;
  const admin=()=>!readOnly&&has('SYSTEM_ADMIN');
  const editable=row=>admin()&&standalone(row)&&['active','pending_material','material_failed'].includes(row.raw_status);
  const type=row=>admin()&&standalone(row)&&['active','pending_material','material_failed','void'].includes(row.raw_status)?`<select class="declaration-type-select" aria-label="修改${esc(row.employee_name)}的处分类型" data-correct-type="${row.id}">${types.filter(x=>x.active!==false||x.id===row.type_id).map(x=>`<option value="${x.id}" ${x.id===row.type_id?'selected':''}>${esc(x.name)}</option>`).join('')}</select>`:esc(row.type_name);
  const actions=row=>admin()&&standalone(row)&&(editable(row)||row.raw_status==='void')?`<button type="button" class="secondary declaration-status-action" data-declaration-action="${row.raw_status==='void'?'restore':'void'}" data-record-id="${row.id}">${row.raw_status==='void'?'还原':'作废'}</button>`:'';
  const level=row=>editable(row)?`<select class="declaration-level-select" aria-label="修改${esc(row.employee_name)}的处分等级" data-correct-level="${row.id}" data-original-level="${row.level_id}">${levels.filter(x=>Object.hasOwn(declarationLevelTones,x.code)).map(x=>`<option value="${x.id}" ${x.id===row.level_id?'selected':''}>${esc(x.name)}</option>`).join('')}</select>`:`<span class="badge declaration-level tone-${declarationLevelTone(row.level_code)}">${esc(row.level_name)}</span>`;
  const classification=row=>`<div class="declaration-classification"><label><span>类型</span>${type(row)}</label><label><span>等级</span>${level(row)}</label></div>`;
  const workflow=row=>`<dl class="declaration-workflow"><div><dt>材料</dt><dd>${esc(row.material_status)}</dd></div><div><dt>审核 / 升级</dt><dd>${esc(row.upgrade_status)}</dd></div></dl>`;
  const operations=row=>`<div class="declaration-operations"><div class="declaration-material-actions">${material(row)}</div>${actions(row)}</div>`;
  const relation=row=>row.upgrade_links?.length?row.upgrade_links.map(link=>`<div class="declaration-link-row"><span class="field-hint">工单 #${link.request_id} · ${esc(({rejected:'审核不通过',approved:'已升级',pending:'待审核',awaiting_material:'待材料就绪'})[link.status]||link.status)}</span><span>${link.role==='result'?'升级来源':link.role==='source_first'?(link.status==='approved'?'首次声明 · 已参与升级':link.status==='rejected'?'首次声明 · 升级未通过':'首次声明 · 待升级审核'):'第二次声明 · 用于升级'}</span>${link.records.filter(item=>item.id!==row.id).map(item=>`<button type="button" class="declaration-related-link" data-declaration-related="${item.id}">${item.role==='source_first'?'首次声明':item.role==='source_second'?'第二次声明':'对应'+esc(item.level_name||'升级结果')} #${item.id} ↗</button>`).join('')}</div>`).join(''):`<span class="field-hint">${row.upgrade_role||row.upgrade_request_id?'升级关联待核对':row.raw_status==='void'?'已作废 · 不参与有效升级':'无升级关联'}</span>`;
  const score=row=>{
    let reason=row.raw_status==='void'?'本条已作废，不计入绩效扣分，原记录和材料留档。':row.raw_status!=='active'?'本条尚未生效，当前不扣分。':row.upgrade_role==='source_second'?'本条是触发升级的第二次声明，不单独扣分；请查看对应备忘录或警告。':row.upgrade_role==='source_first'?(row.actual_points>0?'首次声明的扣分保留；对应备忘录或警告的扣分单独列示。':'首次声明单独计分，是否扣分按本条所属月份判断。'):row.upgrade_role==='result'?'由两张原声明升级生成，实际扣分按本条所属月份判断。':'实际扣分按本条记录所属月份及业务状态判断。';
    if(row.loa_excluded)reason+=' 本条所属月份为 LOA，不计入该月绩效，原登记分数保留。';
    return `<section class="declaration-score" aria-label="本条记录计分说明"><div class="declaration-score-summary"><strong class="declaration-score-status">${esc(row.score_status||row.business_status)}</strong><p class="declaration-score-reason">${esc(reason)}</p></div><div class="declaration-score-values"><span class="declaration-performance-score">计入绩效扣分 <strong>${Number.isFinite(row.actual_points)?(row.actual_points?'−'+row.actual_points:'0'):'—'}</strong> 分</span><span class="declaration-registered-score">原登记分数 ${Number.isFinite(row.registered_points)?esc(row.registered_points):'—'} 分</span>${row.deduction_month?`<small>所属月份 ${esc(row.deduction_month)}</small>`:''}</div></section>`;
  };
  const content=rows.map(row=>`<article class="declaration-record ${row.upgrade_role==='result'?'declaration-upgrade-result':''}" data-declaration-record="${row.id}"><header class="declaration-record-heading"><div class="declaration-card-identity"><strong>${esc(row.employee_name)}</strong><small>${esc(row.employee_no)}</small><span class="field-hint">${esc(row.level_name)} #${row.id}</span><span class="declaration-record-state">记录状态 ${declarationStatusBadge(row.business_status)}</span></div></header><div class="declaration-record-context"><span>${esc(row.attraction_name)}</span><time>事件日期 ${esc(row.occurred_on)}</time>${row.deduction_month?`<span>绩效月份 ${esc(row.deduction_month)}</span>`:''}</div>${score(row)}${classification(row)}<div class="declaration-record-content"><span class="declaration-field-caption">事件内容</span><p class="declaration-description">${esc(row.description)}</p></div>${workflow(row)}${row.raw_status==='void'&&row.void_reason?`<p class="declaration-void-note">作废原因：${esc(row.void_reason)}</p>`:''}<div class="declaration-card-bottom"><div class="declaration-registration"><span class="declaration-field-caption">登记信息</span><strong>${esc(row.submitter_name)}</strong><time>${esc(row.submitted_at)}</time></div>${operations(row)}</div><footer class="declaration-links">${relation(row)}${row.counted_in_statistics===false?'<span class="field-hint">升级来源留档 · 不单独计入汇总和导出</span>':''}</footer></article>`).join('');
  return `<div class="declaration-desktop declaration-cards">${content}</div><div class="declaration-mobile declaration-cards">${content}</div>`;
}

async function renderDeclarationStatistics(){
  disposeDeclarationSearch?.();
  if(!has('DECLARATION_STATS_VIEW')){app.innerHTML='<section class="panel"><div class="error">当前账号没有声明登记统计查看权限</div></section>';return;}
  const request=beginViewRequest();
  const initial=await api('/api/declaration-statistics?month='+encodeURIComponent(monthNow())+'&status=current');
  if(!request.isCurrent())return;
  const levelOptions=initial.levels.map(row=>`<option value="${row.id}">${esc(row.name)}</option>`).join('');
  const circleOptions=initial.attractions.map(row=>`<option value="${row.id}">${esc(row.name)}</option>`).join('');
  const typeOptions=initial.types.map(row=>`<option value="${row.id}">${esc(row.name)}</option>`).join('');
  const canExport=has('DECLARATION_STATS_EXPORT');
  if(!request.write(`<div class="section-gap declaration-statistics-page"><section class="panel"><div class="statistics-page-heading"><h2>声明登记统计</h2><span class="access-mode ${canExport?'can-export':'read-only'}">${canExport?'可导出':'只读'}</span></div><p>汇总全部处分等级的登记事件；景点圈按登记时归属统计，升级关联记录不重复计数。</p><form id="declarationFilter" class="statistics-filter declaration-filter"><label>月份<input name="month" type="month" value="${monthNow()}" required></label><label>景点圈<select name="attraction_id"><option value="">全部景点圈</option>${circleOptions}</select></label><label>处分等级<select name="level_id"><option value="">全部等级</option>${levelOptions}</select></label><label>处分类型<select name="type_id"><option value="">全部类型</option>${typeOptions}</select></label><label>业务状态<select name="status" title="默认全部状态包含已生效、审核中、待补材料"><option value="current">全部状态</option><option value="">全部含作废</option>${['已生效','待补材料','审核中','已作废'].map(name=>`<option value="${name}">${name}</option>`).join('')}</select></label><div class="declaration-search"><label for="declarationEmployeeSearch">员工搜索</label><div class="declaration-search-control"><input id="declarationEmployeeSearch" name="keyword" placeholder="输入姓名或工号，支持模糊查询" autocomplete="off" inputmode="search" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="declarationEmployeeResults"><div id="declarationEmployeeResults" class="employee-search-results" role="listbox" aria-label="匹配员工" hidden></div></div><small class="field-hint" id="declarationEmployeeHint" aria-live="polite">可输入部分姓名或工号，也可从候选中选择</small></div><div class="statistics-actions"><button type="submit" class="primary">查询</button>${canExport?'<button type="button" class="secondary" id="declarationExport">导出 Excel</button>':''}</div></form></section><div id="declarationResults"></div></div>`))return;
  const form=document.getElementById('declarationFilter'),host=document.getElementById('declarationResults');
  const isCurrent=()=>request.isCurrent()&&app.contains(form);
  const ensureCurrentEdit=()=>{if(!isCurrent()||!has('SYSTEM_ADMIN'))throw new Error('页面或账号已变化，请关闭弹窗后重新查询');};
  let sequence=0;
  const show=data=>{
    const details=data.detail_records||data.records;
    const related=new Map([...details,...(data.related_records||[])].map(row=>[row.id,row]));
    const bindRelations=detail=>{
      const area=detail.querySelector('[data-declaration-relation-view]');
      let trigger=null;
      detail.onclick=event=>{
        const button=event.target.closest('[data-declaration-related],[data-declaration-relation-close]');
        if(!button)return;
        if(button.hasAttribute('data-declaration-relation-close')){area.hidden=true;trigger?.focus({preventScroll:true});return;}
        const row=related.get(Number(button.dataset.declarationRelated));
        if(!row)return;
        if(!area.contains(button))trigger=button;
        area.hidden=false;
        area.innerHTML=`<div class="declaration-relation-heading"><h3>关联记录 · ${esc(row.level_name)} #${row.id}</h3><span class="field-hint">关联查看不受当前月份、等级或类型筛选限制</span><button type="button" class="secondary" data-declaration-relation-close>收起关联</button></div>${declarationRecordDetails([row],data.levels,data.types,true)}`;
        bindFilePreviews(area);
        area.scrollIntoView({block:'nearest'});
        area.querySelector('[data-declaration-relation-close]')?.focus({preventScroll:true});
      };
    };
    const openDetails=(detail,rows)=>{detail.innerHTML='<div data-declaration-relation-view class="declaration-relation-view" aria-live="polite" hidden></div>'+declarationRecordDetails(rows,data.levels,data.types);bindFilePreviews(detail);bindLevelCorrections(detail,data);bindRelations(detail);};
    const cards=`<div class="declaration-kpis"><div class="trend-card declaration-total"><span class="trend-card-label">总记录数</span><strong>${data.total_records}</strong></div><div class="trend-card declaration-total"><span class="trend-card-label">涉及人数</span><strong>${data.employee_count}</strong></div>${data.levels.map(row=>`<div class="trend-card declaration-kpi tone-${declarationLevelTone(row.code)}"><span class="trend-card-label">${esc(row.name)}</span><strong>${row.count}</strong></div>`).join('')}</div>`;
    const statuses=`<div class="declaration-statuses">${Object.entries(data.statuses).map(([name,count])=>`<span class="declaration-status-count">${declarationStatusBadge(name)}<strong>${count} 条</strong></span>`).join('')}</div>`;
    const displayCircles=[...data.circles];
    for(const row of details)if(!displayCircles.some(c=>c.attraction_id===row.attraction_id))displayCircles.push({attraction_id:row.attraction_id,attraction_name:row.attraction_name,record_count:0,employee_count:0,levels:[]});
    const circles=displayCircles.map(circle=>{
      const key=circle.attraction_id===null?'none':String(circle.attraction_id);
      const rows=details.filter(row=>(row.attraction_id===null?'none':String(row.attraction_id))===key);
      const displayLevels=data.levels.filter(level=>rows.some(row=>row.level_id===level.id)).map(level=>({...level,count:circle.levels.find(item=>item.id===level.id)?.count||0,details:rows.filter(row=>row.level_id===level.id).length}));
      return `<section class="declaration-circle"><button type="button" class="declaration-circle-toggle" data-declaration-circle="${esc(key)}" aria-expanded="false"><strong>${esc(circle.attraction_name)}</strong><span>${circle.record_count} 条计入汇总 · ${rows.length} 张卡片</span><span aria-hidden="true">⌄</span></button><div class="declaration-inline" data-declaration-circle-detail="${esc(key)}" hidden></div><div class="declaration-levels">${displayLevels.map(level=>`<div class="declaration-level-item"><button type="button" class="declaration-level-toggle tone-${declarationLevelTone(level.code)}" data-declaration-level="${esc(key)}:${level.id}" aria-expanded="false"><span>${esc(level.name)}</span><strong>${level.count} 条计入汇总 · ${level.details} 张卡片</strong><span aria-hidden="true">⌄</span></button><div class="declaration-inline" data-declaration-level-detail="${esc(key)}:${level.id}" hidden></div></div>`).join('')}</div></section>`;
    }).join('')||'<div class="empty">当前筛选范围暂无声明登记记录</div>';
    host.innerHTML=`<section class="panel"><h2>月度汇总</h2><p class="statistics-scope">${esc(data.month)} · ${data.total_records} 条记录；点击景点圈或处分等级可展开明细，再次点击收起。</p>${cards}${statuses}<div class="declaration-circle-list">${circles}</div></section>`;
    host.querySelectorAll('[data-declaration-circle]').forEach(button=>button.onclick=()=>{const key=button.dataset.declarationCircle,detail=host.querySelector(`[data-declaration-circle-detail="${key}"]`),open=detail.hidden;detail.hidden=!open;button.setAttribute('aria-expanded',String(open));if(open)openDetails(detail,details.filter(row=>(row.attraction_id===null?'none':String(row.attraction_id))===key));else detail.replaceChildren();});
    host.querySelectorAll('[data-declaration-level]').forEach(button=>button.onclick=()=>{const key=button.dataset.declarationLevel,[circleId,levelId]=key.split(':'),detail=host.querySelector(`[data-declaration-level-detail="${key}"]`),open=detail.hidden;detail.hidden=!open;button.setAttribute('aria-expanded',String(open));if(open)openDetails(detail,details.filter(row=>(row.attraction_id===null?'none':String(row.attraction_id))===circleId&&String(row.level_id)===levelId));else detail.replaceChildren();});
  };
  const bindLevelCorrections=(detail,data)=>{
    detail.querySelectorAll('[data-correct-type]').forEach(select=>select.onchange=async()=>{
      const row=(data.detail_records||data.records).find(x=>x.id===Number(select.dataset.correctType)),typeId=Number(select.value);
      select.value=String(row.type_id);if(typeId===row.type_id)return;
      const target=data.types.find(x=>x.id===typeId);
      const saved=await formModal('修改处分类型',`<p>${esc(row.employee_name)} · ${esc(row.employee_no)}</p><p>${esc(row.type_name)} → ${esc(target.name)}</p><p>统计分类将更新，扣分按更正后的类别规则核算；原材料和登记内容保留。</p>`, '<label>修改原因<textarea name="reason" maxlength="500" required></textarea></label>','确认修改',{lockWhileSubmitting:true,onSubmit:async f=>{
        ensureCurrentEdit();
        const reason=f.elements.reason.value.trim();if(!reason)throw new Error('请填写修改原因');
        await api('/api/deductions/'+row.id+'/type',json('PATCH',{type_id:typeId,expected_type_id:row.type_id,expected_level_id:row.level_id,expected_status:row.raw_status,reason}));
      }});if(saved&&request.isCurrent())await load();
    });
    detail.querySelectorAll('[data-declaration-action]').forEach(button=>button.onclick=async()=>{
      const row=(data.detail_records||data.records).find(x=>x.id===Number(button.dataset.recordId)),action=button.dataset.declarationAction,label=action==='restore'?'还原':'作废';
      const saved=await formModal(label+'记录',`<p>${esc(row.employee_name)} · ${esc(row.employee_no)} · ${esc(row.type_name)} · ${esc(row.level_name)}</p><p>${action==='restore'?'恢复作废前业务状态，原已生效记录恢复计分。':'作废后该记录退出计分，保留历史记录和材料。'}已月结及升级工单关联记录不能在此操作。</p>`, '<label>操作原因<textarea name="reason" maxlength="500" required></textarea></label>','确认'+label,{lockWhileSubmitting:true,onSubmit:async f=>{
        ensureCurrentEdit();
        const reason=f.elements.reason.value.trim();if(!reason)throw new Error('请填写操作原因');
        await api('/api/deductions/'+row.id+'/'+action,json('POST',{reason,expected_status:row.raw_status,expected_voided_at:row.voided_at,expected_type_id:row.type_id,expected_level_id:row.level_id}));
      }});if(saved&&request.isCurrent())await load();
    });

    detail.querySelectorAll('[data-correct-level]').forEach(select=>select.onchange=async()=>{
      const row=(data.detail_records||data.records).find(x=>x.id===Number(select.dataset.correctLevel)),levelId=Number(select.value);
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
  const search=form.elements.keyword,choices=form.querySelector('#declarationEmployeeResults'),searchHint=form.querySelector('#declarationEmployeeHint');
  let searchTimer,searchSequence=0,searchController;
  const closeSearch=()=>{clearTimeout(searchTimer);++searchSequence;searchController?.abort();choices.hidden=true;choices.replaceChildren();search.setAttribute('aria-expanded','false');};
  search.addEventListener('input',()=>{
    closeSearch();searchHint.textContent='可输入部分姓名或工号，也可从候选中选择';
    if(!search.value.trim())return;
    const id=searchSequence,filters=params();
    searchTimer=setTimeout(async()=>{
      if(!isCurrent())return;
      searchController=new AbortController();searchHint.textContent='正在搜索…';
      try{
        const data=await api('/api/declaration-statistics?'+filters,{signal:searchController.signal});
        if(id!==searchSequence||!isCurrent())return;
        const rows=[...new Map((data.detail_records||data.records).map(row=>[row.employee_id,row])).values()];
        choices.innerHTML=rows.length?rows.slice(0,30).map((row,index)=>`<button type="button" class="employee-search-result" role="option" aria-selected="false" data-search-result="${index}"><strong>${esc(row.employee_name)}</strong><small>${esc(row.employee_no)}</small></button>`).join(''):'<div class="employee-search-empty">当前月份及筛选条件下没有匹配记录</div>';
        choices.hidden=false;search.setAttribute('aria-expanded','true');
        searchHint.textContent=rows.length>30?`找到 ${rows.length} 名员工，显示前 30 名，请继续输入缩小范围`:`找到 ${rows.length} 名员工，可选择或直接查询`;
        choices.querySelectorAll('[data-search-result]').forEach(button=>button.onclick=()=>{const row=rows[Number(button.dataset.searchResult)];search.value=row.employee_no;closeSearch();searchHint.textContent=`已选择 ${row.employee_name} · ${row.employee_no}`;search.focus();});
      }catch(error){if(error.name!=='AbortError'&&id===searchSequence&&isCurrent())searchHint.textContent='搜索失败，可直接点击查询重试';}
    },120);
  });
  form.addEventListener('change',event=>{if(event.target!==search){closeSearch();searchHint.textContent='可输入部分姓名或工号，也可从候选中选择';}});
  const searchArea=search.closest('.declaration-search');
  // Keep input focus through mouse/synthesized touch mousedown so WebKit's
  // null-relatedTarget blur cannot remove the candidate before its click.
  choices.addEventListener('mousedown',event=>{if(event.target.closest('[data-search-result]'))event.preventDefault();});
  const outsideSearch=event=>{if(!isCurrent()){disposeSearch();return;}if(!searchArea.contains(event.target))closeSearch();};
  const disposeSearch=()=>{closeSearch();document.removeEventListener('pointerdown',outsideSearch,true);unregisterPageCleanup(disposeSearch);if(disposeDeclarationSearch===disposeSearch)disposeDeclarationSearch=null;};
  document.addEventListener('pointerdown',outsideSearch,true);
  disposeDeclarationSearch=registerPageCleanup(disposeSearch);
  searchArea.addEventListener('focusout',event=>{if(!searchArea.contains(event.relatedTarget))closeSearch();});
  searchArea.addEventListener('keydown',event=>{if(event.key==='Escape'){closeSearch();search.focus();}else if(['ArrowDown','ArrowUp'].includes(event.key)){const buttons=[...choices.querySelectorAll('button')];if(!buttons.length)return;event.preventDefault();const index=buttons.indexOf(document.activeElement),next=index<0?(event.key==='ArrowDown'?0:buttons.length-1):(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;buttons[next].focus();}});
  const load=async()=>{if(!isCurrent())return;const id=++sequence,button=form.querySelector('button[type="submit"]');button.disabled=true;host.innerHTML='<section class="panel"><div class="empty">正在加载统计数据…</div></section>';try{const data=await api('/api/declaration-statistics?'+params());if(id===sequence&&isCurrent())show(data);}catch(error){if(id===sequence&&isCurrent())host.innerHTML=`<section class="panel"><div class="error">${esc(error.message)}</div></section>`;}finally{if(id===sequence)button.disabled=false;}};
  form.onsubmit=event=>{event.preventDefault();closeSearch();void load();};
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
