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

function currentDeclarationUpgrade(row){
  return row.upgrade_links?.find(link=>['awaiting_material','pending','approved'].includes(link.status));
}

function declarationWorkLabel(link){
  return link.status_name||({rejected:'审核不通过',approved:'已升级',pending:'待审核',awaiting_material:'待材料就绪'})[link.status]||link.status;
}

function declarationAssociationButtons(link,rowId){
  const labels={source_first:'首次声明',source_second:'第二次声明'};
  return link.records.filter(item=>item.id!==rowId).map(item=>
    `<button type="button" class="declaration-related-link" data-declaration-related="${item.id}">${esc(labels[item.role]||'对应'+item.level_name)} #${item.id} ↗</button>`
  ).join('');
}

function declarationRecordCard(row,levels=[],types=[],readOnly=false){
  const material=row=>row.document_url?`<button type="button" class="secondary declaration-material" data-file-preview="${esc(row.document_url)}" data-file-kind="pdf" data-file-title="${esc(row.employee_name)}的处分材料">查看材料</button> <a class="secondary declaration-material" href="${esc(portalPath(row.document_url))}" download>下载材料</a>`:'<span class="field-hint">暂无可查看材料</span>';
  const standalone=row=>!row.upgrade_request_id&&!row.upgrade_role&&!row.upgrade_links?.length;
  const admin=()=>!readOnly&&has('SYSTEM_ADMIN');
  const editable=row=>admin()&&standalone(row)&&['active','pending_material','material_failed'].includes(row.raw_status);
  const type=row=>admin()&&standalone(row)&&['active','pending_material','material_failed','void'].includes(row.raw_status)?`<select class="declaration-type-select" aria-label="修改${esc(row.employee_name)}的处分类型" data-correct-type="${row.id}">${types.filter(x=>x.active!==false||x.id===row.type_id).map(x=>`<option value="${x.id}" ${x.id===row.type_id?'selected':''}>${esc(x.name)}</option>`).join('')}</select>`:esc(row.type_name);
  const actions=row=>admin()&&standalone(row)&&(editable(row)||row.raw_status==='void')?`<button type="button" class="secondary declaration-status-action" data-declaration-action="${row.raw_status==='void'?'restore':'void'}" data-record-id="${row.id}">${row.raw_status==='void'?'还原':'作废'}</button>`:'';
  const level=row=>editable(row)?`<select class="declaration-level-select" aria-label="修改${esc(row.employee_name)}的处分等级" data-correct-level="${row.id}" data-original-level="${row.level_id}">${levels.filter(x=>Object.hasOwn(declarationLevelTones,x.code)).map(x=>`<option value="${x.id}" ${x.id===row.level_id?'selected':''}>${esc(x.name)}</option>`).join('')}</select>`:`<span class="badge declaration-level tone-${declarationLevelTone(row.level_code)}">${esc(row.level_name)}</span>`;
  const classification=row=>`<div class="declaration-classification"><label><span>类型</span>${type(row)}</label><label><span>等级</span>${level(row)}</label></div>`;
  const history=(row.upgrade_links||[]).filter(link=>link.status==='rejected').map(link=>
    `<div class="declaration-link-row"><span>工单 #${link.request_id} · ${esc(declarationWorkLabel(link))}</span>${declarationAssociationButtons(link,row.id)}</div>`
  ).join('');
  let reason=row.raw_status==='void'?'本条已作废，不计入绩效扣分，原记录和材料留档。':row.raw_status!=='active'?'本条尚未生效，当前不扣分。':row.upgrade_role==='source_second'?'本条是触发升级的第二次声明，不单独扣分；请查看对应备忘录或警告。':row.upgrade_role==='source_first'?(row.actual_points>0?'首次声明的扣分保留；对应备忘录或警告的扣分单独列示。':'首次声明单独计分，是否扣分按本条所属月份判断。'):row.upgrade_role==='result'?'由两张原声明升级生成，实际扣分按本条所属月份判断。':'实际扣分按本条记录所属月份及业务状态判断。';
  if(row.loa_excluded)reason+=' 本条所属月份为 LOA，不计入该月绩效，原登记分数保留。';
  const points=Number.isFinite(row.actual_points)?(row.actual_points?'−'+row.actual_points:'0'):'—';
  return `<article class="declaration-record ${row.upgrade_role==='result'?'declaration-upgrade-result':''}" data-declaration-record="${row.id}"><header class="declaration-record-heading"><div class="declaration-card-identity"><strong>${esc(row.employee_name)}</strong><small>${esc(row.employee_no)}</small><span class="field-hint">${esc(row.level_name)} #${row.id}</span>${declarationStatusBadge(row.business_status)}</div></header>
    <div class="declaration-record-context"><time>事件日期 ${esc(row.occurred_on)}</time>${row.deduction_month?`<span>绩效月份 ${esc(row.deduction_month)}</span>`:''}</div>${classification(row)}
    <p class="declaration-description">${esc(row.description)}</p>
    <section class="declaration-score" aria-label="本条记录计分说明"><span class="declaration-score-status">${esc(row.score_status||row.business_status)}</span><span class="declaration-performance-score">实际扣分 <strong>${points}</strong> 分</span></section>
    <div class="declaration-operations"><div class="declaration-material-actions">${material(row)}</div>${actions(row)}</div>
    <details class="declaration-card-details"><summary>详情${history?'及审核历史':''}</summary><div class="declaration-record-context"><span>${esc(row.attraction_name)}</span><span>${esc(row.submitter_name)} · ${esc(row.submitted_at)}</span><span>${esc(row.material_status)}</span></div>
    <p class="declaration-score-reason">${esc(reason)}</p><p class="declaration-registered-score">原登记分数 ${Number.isFinite(row.registered_points)?esc(row.registered_points):'—'} 分</p>
    ${row.void_reason?`<p class="declaration-void-note">作废原因：${esc(row.void_reason)}</p>`:''}${history}</details></article>`;
}

function declarationRecordDetails(row,levels,types,related,group){
  const work=currentDeclarationUpgrade(row);
  const member=role=>related.get(work?.records.find(item=>item.role===role)?.id);
  const left=member('source_first')||row;
  const right=work?`<header class="declaration-upgrade-heading"><strong>工单 #${work.request_id}</strong><span class="badge warn">${esc(declarationWorkLabel(work))}</span></header>
    <div class="declaration-upgrade-records" data-declaration-upgrade-records>${['source_second','result'].map(role=>{
      const record=member(role);
      if(!record||record.id===left.id)return '';
      const label=role==='source_second'?'第二张声明':record.level_name;
      return `<details class="declaration-upgrade-member" name="${esc(group)}-links-${row.id}" data-declaration-member="${record.id}"${record.id===row.id?' open':''}>
        <summary class="declaration-summary"><strong>${esc(label)} #${record.id}</strong><time datetime="${esc(record.occurred_on)}">${esc(record.occurred_on)}</time></summary>
        ${declarationRecordCard(record,levels,types,true)}</details>`;
    }).join('')}</div>`:left.upgrade_slot_open?`<div class="declaration-upgrade-placeholder"><strong>等待第二张声明</strong><p>三个月内如有符合规则的第二张声明，可发起升级。</p></div>`:'';
  return `<section class="declaration-pair" data-declaration-pair="${left.id}"><div class="declaration-original">${declarationRecordCard(left,levels,types,left.id!==row.id)}</div><aside class="declaration-upgrade-side" data-declaration-side${right?' data-default-side':' hidden'}>${right}<div data-declaration-relation-view class="declaration-relation-view" aria-live="polite" hidden></div></aside></section>`;
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
      const triggers=new WeakMap();
      detail.onclick=event=>{
        const summary=event.target.closest('[data-declaration-member] > summary');
        const member=summary?.parentElement;
        if(member&&!member.open&&matchMedia('(max-width: 760px)').matches){
          // The native accordion closes its previous member above this summary.
          requestAnimationFrame(()=>{
            if(!member.open||!member.closest('[data-declaration-list-row][open]')||!summary.getClientRects().length)return;
            const top=Math.max(0,document.querySelector('.app-topbar')?.getBoundingClientRect().bottom??0);
            const tabs=document.getElementById('tabs');
            const bottom=tabs?.getClientRects().length?tabs.getBoundingClientRect().top:innerHeight;
            const summaryTop=summary.getBoundingClientRect().top;
            const headingBottom=member.querySelector('.declaration-record-heading').getBoundingClientRect().bottom;
            if(summaryTop<top||headingBottom>bottom)window.scrollBy({top:summaryTop-top-8,behavior:'instant'});
          });
        }
        const button=event.target.closest('[data-declaration-related],[data-declaration-relation-close]');
        if(!button)return;
        const pair=button.closest('[data-declaration-pair]');
        const side=pair.querySelector('[data-declaration-side]');
        const area=side.querySelector('[data-declaration-relation-view]');
        const records=side.querySelector('[data-declaration-upgrade-records]');
        if(button.hasAttribute('data-declaration-relation-close')){
          area.hidden=true;
          area.replaceChildren();
          if(records)records.hidden=false;
          side.hidden=!side.hasAttribute('data-default-side');
          triggers.get(area)?.focus({preventScroll:true});
          return;
        }
        const row=related.get(Number(button.dataset.declarationRelated));
        if(!row)return;
        if(!area.contains(button))triggers.set(area,button);
        const links=(row.upgrade_links||[]).filter(link=>link.status!=='rejected').map(link=>declarationAssociationButtons(link,row.id)).join('');
        side.hidden=false;
        area.hidden=false;
        if(records)records.hidden=true;
        area.innerHTML=`<div class="declaration-relation-heading"><h3>关联记录 · ${esc(row.level_name)} #${row.id}</h3><button type="button" class="secondary" data-declaration-relation-close>收起关联</button></div>${declarationRecordCard(row,data.levels,data.types,true)}${links?`<div class="declaration-link-row declaration-upgrade-links">${links}</div>`:''}`;
        bindFilePreviews(area);
        area.querySelector('[data-declaration-relation-close]').focus({preventScroll:true});
        if(matchMedia('(max-width: 760px)').matches)area.scrollIntoView({block:'nearest'});
      };
    };
    const openDetails=(detail,rows)=>{
      const group=`declaration-list-${detail.dataset.declarationCircleDetail??detail.dataset.declarationLevelDetail}`;
      detail.innerHTML=rows.length?rows.map(row=>`<details class="declaration-list-row" data-declaration-list-row="${row.id}" name="${esc(group)}">
        <summary class="declaration-summary"><span class="declaration-summary-person"><strong>${esc(row.employee_name)}</strong><small>${esc(row.employee_no)}</small><time datetime="${esc(row.occurred_on)}">${esc(row.occurred_on)}</time></span><span class="declaration-summary-action"><span>查看内容 ›</span><span>收起内容 ⌃</span></span></summary>
        <div class="declaration-cards declaration-list-content"></div></details>`).join(''):'<div class="empty">当前范围没有符合条件的记录</div>';
      detail.querySelectorAll('[data-declaration-list-row]').forEach((item,index)=>{
        const content=item.lastElementChild;
        item.addEventListener('toggle',()=>{
          if(!item.open||content.hasChildNodes())return;
          content.innerHTML=declarationRecordDetails(rows[index],data.levels,data.types,related,group);
          bindFilePreviews(content);bindLevelCorrections(content,data);bindRelations(content);
        });
      });
    };
    const cards=`<div class="declaration-kpis"><div class="trend-card declaration-total"><span class="trend-card-label">总记录数</span><strong>${data.total_records}</strong></div><div class="trend-card declaration-total"><span class="trend-card-label">涉及人数</span><strong>${data.employee_count}</strong></div>${data.levels.map(row=>`<div class="trend-card declaration-kpi tone-${declarationLevelTone(row.code)}"><span class="trend-card-label">${esc(row.name)}</span><strong>${row.count}</strong></div>`).join('')}</div>`;
    const statuses=`<div class="declaration-statuses">${Object.entries(data.statuses).map(([name,count])=>`<span class="declaration-status-count">${declarationStatusBadge(name)}<strong>${count} 条</strong></span>`).join('')}</div>`;
    const displayCircles=[...data.circles];
    for(const row of details)if(!displayCircles.some(c=>c.attraction_id===row.attraction_id))displayCircles.push({attraction_id:row.attraction_id,attraction_name:row.attraction_name,record_count:0,employee_count:0,levels:[]});
    const circles=displayCircles.map(circle=>{
      const key=circle.attraction_id===null?'none':String(circle.attraction_id);
      const rows=details.filter(row=>(row.attraction_id===null?'none':String(row.attraction_id))===key);
      const displayLevels=data.levels.filter(level=>rows.some(row=>row.level_id===level.id)).map(level=>({...level,count:circle.levels.find(item=>item.id===level.id)?.count||0,details:rows.filter(row=>row.level_id===level.id).length}));
      return `<section class="declaration-circle"><button type="button" class="declaration-circle-toggle" data-declaration-circle="${esc(key)}" aria-expanded="false"><strong>${esc(circle.attraction_name)}</strong><span>${circle.record_count} 条计入汇总 · ${rows.length} 条明细</span><span aria-hidden="true">⌄</span></button><div class="declaration-inline" data-declaration-circle-detail="${esc(key)}" hidden></div><div class="declaration-levels">${displayLevels.map(level=>`<div class="declaration-level-item"><button type="button" class="declaration-level-toggle tone-${declarationLevelTone(level.code)}" data-declaration-level="${esc(key)}:${level.id}" aria-expanded="false"><span>${esc(level.name)}</span><strong>${level.count} 条计入汇总 · ${level.details} 条明细</strong><span aria-hidden="true">⌄</span></button><div class="declaration-inline" data-declaration-level-detail="${esc(key)}:${level.id}" hidden></div></div>`).join('')}</div></section>`;
    }).join('')||'<div class="empty">当前筛选范围暂无声明登记记录</div>';
    host.innerHTML=`<section class="panel"><h2>月度汇总</h2><p class="statistics-scope">${esc(data.month)} · ${data.total_records} 条记录；展开景点圈或处分等级查看列表，点击单条查看内容。</p>${cards}${statuses}<div class="declaration-circle-list">${circles}</div></section>`;
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
