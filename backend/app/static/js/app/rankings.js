import { queueExport } from './export-tasks.js';
// PR ranking filters and presentation.
import { api } from './api.js';
import { beginViewRequest } from './context.js';
import { actingNoteMarkup, esc, fmt, fmtDeduction, monthStart, portalPath, today } from './format.js';
import { opt } from './records.js';
import { app, state } from './state.js';
import { toast } from './toast.js';

const prCategoryNames={overall:'综合分',recognition:'加分类型',deduction:'扣分类型',absence:'缺勤',leader:'主管发放排行',gsm_leader:'管理层认可次数'};

function prRankBadge(rank){return `<span class="pr-rank-badge ${rank<=3?`top-${rank}`:''}">${rank}</span>`;}

function prEmployeeCell(row,label='员工'){return `<strong>${esc(row.employee_name)}</strong><small>${esc(row.employee_no)} · ${esc(row.role_name||label)}</small>${actingNoteMarkup(row)}`;}

function prRecognitionCount(row,data){return `${esc(row.count)}${data.uncapped_ranking&&Number(row.score)>=5?` <small>（未封顶${fmt(row.uncapped_score)}分）</small>`:''}`;}

function prRankingCards(data){
  const field=(label,value,className='')=>`<div><dt>${label}</dt><dd class="${className}">${value}</dd></div>`;
  const cards=data.rows.map(row=>{
    let fields=[];
    if(data.category==='overall')fields=[field('角色',esc(row.role_name)),field('小组',esc(row.group_label||'未分组')),field('加分',`+${fmt(row.recognition_score)}`,'score-positive'),field('扣分',fmtDeduction(row.deduction_score),'score-negative'),field('全勤分',fmt(row.attendance_score)),field('综合分',fmt(row.total_score),'pr-total-score')];
    else if(data.category==='absence')fields=[field('角色',esc(row.role_name)),field('小组',esc(row.group_label||'未分组')),field('登记次数',esc(row.count)),field('缺勤天数',Number(row.leave_days||0).toFixed(1)),field('扣减全勤分',fmtDeduction(row.score),'score-negative'),field('最近缺勤',esc(row.recent_date||'—'))];
    else if(['leader','gsm_leader'].includes(data.category))fields=[field('角色',esc(row.role_name)),field('认可次数（总数）',esc(row.count)),...(data.venue_columns||[]).map(c=>field(esc(c.name),esc(row.venue_counts?.[c.key]||0))),field('累计加分',`+${fmt(row.score)}`,'score-positive'),field('最近一次认可',esc(row.recent_date||'—'))];
    else {const recognition=data.category==='recognition';fields=[field('角色',esc(row.role_name)),field('小组',esc(row.group_label||'未分组')),field('登记次数',prRecognitionCount(row,data)),field(recognition?'累计加分':'累计扣分',recognition?`+${fmt(row.score)}`:fmtDeduction(row.score),recognition?'score-positive':'score-negative'),field(recognition?'最近加分':'最近扣分',esc(row.recent_date||'—'))]}
    return `<article class="pr-ranking-card ${row.rank<=3?'pr-top-card':''}" role="listitem"><header><div>${prRankBadge(row.rank)}<span>第 ${row.rank} 名</span></div><div class="pr-card-employee">${prEmployeeCell(row,data.category==='leader'?'主管':'员工')}</div></header><dl>${fields.join('')}</dl></article>`;
  }).join('');
  return `<div class="pr-ranking-cards" role="list">${cards||'<div class="empty">当前条件下暂无排名数据</div>'}</div>`;
}

function prRankingTable(data){
  const commonHead='<th>排名</th><th>员工</th><th>角色</th>';
  let head='',rows='';
  if(data.category==='overall'){
    head=`${commonHead}<th>小组</th><th>加分</th><th>扣分</th><th>全勤分</th><th>综合分</th>`;
    rows=data.rows.map(row=>`<tr class="${row.rank<=3?'pr-top-row':''}"><td>${prRankBadge(row.rank)}</td><td>${prEmployeeCell(row)}</td><td>${esc(row.role_name)}</td><td>${esc(row.group_label||'未分组')}</td><td class="score-positive">+${fmt(row.recognition_score)}</td><td class="score-negative">${fmtDeduction(row.deduction_score)}</td><td>${fmt(row.attendance_score)}</td><td class="pr-total-score">${fmt(row.total_score)}</td></tr>`).join('');
  }else if(data.category==='absence'){
    head=`${commonHead}<th>小组</th><th>登记次数</th><th>缺勤天数</th><th>扣减全勤分</th><th>最近一次缺勤</th>`;
    rows=data.rows.map(row=>`<tr class="${row.rank<=3?'pr-top-row':''}"><td>${prRankBadge(row.rank)}</td><td>${prEmployeeCell(row)}</td><td>${esc(row.role_name)}</td><td>${esc(row.group_label||'未分组')}</td><td>${row.count}</td><td>${Number(row.leave_days||0).toFixed(1)}</td><td class="score-negative">${fmtDeduction(row.score)}</td><td>${esc(row.recent_date||'—')}</td></tr>`).join('');
  }else if(['leader','gsm_leader'].includes(data.category)){
    const personLabel=data.category==='gsm_leader'?'GSM / TA GSM / AM / OM':'LEAD / TA LEAD',venues=data.venue_columns||[];
    head=`<th>排名</th><th>${personLabel}</th><th>角色</th><th>认可次数（总数）</th>${venues.map(c=>`<th>${esc(c.name)}</th>`).join('')}<th>累计加分</th><th>最近一次认可</th>`;
    rows=data.rows.map(row=>`<tr class="${row.rank<=3?'pr-top-row':''}"><td>${prRankBadge(row.rank)}</td><td>${prEmployeeCell(row,personLabel)}</td><td>${esc(row.role_name)}</td><td>${row.count}</td>${venues.map(c=>`<td>${esc(row.venue_counts?.[c.key]||0)}</td>`).join('')}<td class="score-positive">+${fmt(row.score)}</td><td>${esc(row.recent_date||'—')}</td></tr>`).join('');
  }else{
    const scoreLabel=data.category==='recognition'?'累计加分':'累计扣分',dateLabel=data.category==='recognition'?'最近一次加分':'最近一次扣分';
    head=`${commonHead}<th>小组</th><th>登记次数</th><th>${scoreLabel}</th><th>${dateLabel}</th>`;
    rows=data.rows.map(row=>`<tr class="${row.rank<=3?'pr-top-row':''}"><td>${prRankBadge(row.rank)}</td><td>${prEmployeeCell(row)}</td><td>${esc(row.role_name)}</td><td>${esc(row.group_label||'未分组')}</td><td>${prRecognitionCount(row,data)}</td><td class="${data.category==='recognition'?'score-positive':'score-negative'}">${data.category==='recognition'?'+':''}${data.category==='recognition'?fmt(row.score):fmtDeduction(row.score)}</td><td>${esc(row.recent_date||'—')}</td></tr>`).join('');
  }
  const pageButtons=[];for(let page=1;page<=data.pages;page++){if(page===1||page===data.pages||Math.abs(page-data.page)<=1)pageButtons.push(`<button type="button" class="${page===data.page?'active':''}" data-pr-page="${page}">${page}</button>`);else if(pageButtons[pageButtons.length-1]!=='<span>…</span>')pageButtons.push('<span>…</span>')}
  return `<div class="pr-result-meta"><span>共 <strong>${data.total}</strong> 人 · ${data.circle_scope==='issuer'?'签卡人所属圈：':''}${esc(data.attraction_name)}${data.population_name?' · 被认可人群：'+esc(data.population_name):''} · ${esc(data.start_date)} 至 ${esc(data.end_date)}</span>${data.subtype_name?`<span>当前类型：<strong>${esc(data.subtype_name)}</strong></span>`:''}${data.uncapped_ranking?'<span>未封顶分数按已生效记录原始分值合计；累计分值排名使用此分数，不改变实际绩效计分。</span>':''}</div>${prRankingCards(data)}<div class="table-wrap pr-ranking-wrap"><table class="pr-ranking-table"><thead><tr>${head}</tr></thead><tbody>${rows||`<tr><td colspan="${(head.match(/<th>/g)||[]).length}" class="empty">当前条件下暂无排名数据</td></tr>`}</tbody></table></div><div class="pr-pagination"><button type="button" data-pr-page="${Math.max(1,data.page-1)}" ${data.page<=1?'disabled':''}>上一页</button>${pageButtons.join('')}<button type="button" data-pr-page="${Math.min(data.pages,data.page+1)}" ${data.page>=data.pages?'disabled':''}>下一页</button><span>${data.page_size}条/页</span></div>`;
}

async function renderPrRankings(){
  const request=beginViewRequest();
  let category='overall',sortBy='score',page=1,requestSequence=0,exportBusy=false;
  const scopedHr=state.me.role_code==='HR_CIRCLE',selectedCircle=String(state.me.attraction_id||''),canExport=['GSM','AM','OM','HR_CIRCLE'].includes(state.me.role_code);
  const circles=(state.options.employee_circles||state.options.attractions||[]).filter(row=>!scopedHr||String(row.id)===selectedCircle);
  if(scopedHr&&!circles.length){app.innerHTML='<section class="panel"><h2>PR排名数据</h2><p>当前HR账号未配置有效的所属景点圈，请联系管理员核对。</p></section>';return;}
  const circleOptions=`${scopedHr?'':'<option value="">全部景点圈</option>'}${circles.map(row=>`<option value="${esc(row.id)}" ${String(row.id)===selectedCircle?'selected':''}>${esc(row.name)}</option>`).join('')}`;
  app.innerHTML=`<div class="section-gap pr-ranking-page"><section class="panel"><h2>PR排名数据</h2><p>${scopedHr?'景点圈HR仅可查询及导出本圈CM/TR绩效与发放统计，不开放主管个人绩效排名。':'TA GSM、GSM、AM和OM均可查询三个景点圈或全部景点圈；各榜单按需加载，不会一次读取全部排名。'}</p><form id="prFilter" class="pr-ranking-filter"><label><span data-pr-circle-label>景点圈</span><select name="attraction_id">${circleOptions}</select></label><label>开始日期<input name="start_date" type="date" value="${monthStart()}" max="${today()}" required></label><label>结束日期<input name="end_date" type="date" value="${today()}" max="${today()}" required></label><label>排名人群<select name="population"><option value="frontline">CM/TR</option>${scopedHr?'':'<option value="supervisor">LEAD</option>'}</select></label><label><span data-pr-search-label>员工搜索</span><input name="keyword" placeholder="搜索姓名或工号"></label><div class="pr-ranking-actions"><button class="primary">查询</button>${canExport?'<button type="button" id="prExport" class="secondary">导出</button>':'<span class="field-hint">TA GSM仅支持查询</span>'}</div></form></section><section class="panel pr-ranking-data"><div id="prCategoryTabs" class="pr-category-tabs">${Object.entries(prCategoryNames).map(([key,name])=>`<button type="button" data-pr-category="${key}" class="${key===category?'active':''}">${name}</button>`).join('')}</div><div id="prSubfilters" class="pr-subfilters"></div><div id="prResults"><div class="empty">正在加载排名…</div></div></section></div>`;
  const form=document.getElementById('prFilter'),subfilters=document.getElementById('prSubfilters'),results=document.getElementById('prResults');
  const recognitionTypes=state.options.recognition_types||[],deductionTypes=state.options.deduction_types||[];
  let scorePopulation='frontline',gsmPopulation='frontline';
  const isIssuer=()=>['leader','gsm_leader'].includes(category);
  const configurePopulation=()=>{
    const population=form.elements.population,fixed=scopedHr||category==='leader';
    population.innerHTML=(category==='gsm_leader'&&!scopedHr?'<option value="all">全部人群</option>':'')+'<option value="frontline">CM/TR</option>'+(fixed?'':'<option value="supervisor">LEAD</option>');
    population.value=fixed?'frontline':category==='gsm_leader'?gsmPopulation:scorePopulation;
    population.disabled=fixed;
    form.querySelector('[data-pr-circle-label]').textContent=isIssuer()&&!scopedHr?'签卡人所属圈':'景点圈';
    form.querySelector('[data-pr-search-label]').textContent=isIssuer()?'签卡人搜索':'员工搜索';
  };
  const renderSubfilters=()=>{
    let selector='';
    if(category==='recognition')selector=`<label>认可类型<select name="subtype_id"><option value="">全部加分类别</option>${opt(recognitionTypes)}</select></label>`;
    if(category==='deduction')selector=`<label>扣分类型<select name="subtype_id"><option value="">全部扣分类型</option>${opt(deductionTypes)}</select></label>`;
    if(category==='absence')selector='<label>缺勤类型<select name="subtype_id"><option value="">全部缺勤类型</option><option value="1">病假</option><option value="2">违规病假</option></select></label>';
    if(category==='leader'||category==='gsm_leader')selector=`<label>加分类别<select name="subtype_id"><option value="">全部加分类别</option>${opt(recognitionTypes)}</select></label>`;
    const sortControl=['recognition','deduction','absence'].includes(category)?`<div class="pr-sort-control"><span>排名依据</span><button type="button" data-pr-sort="score" class="${sortBy==='score'?'active':''}">累计分值</button><button type="button" data-pr-sort="count" class="${sortBy==='count'?'active':''}">登记次数</button></div>`:'';
    const hint=isIssuer()?(scopedHr?'仅统计本圈CM/TR收到的已确认认可，次数只归签卡人；景点明细按发生景点归圈。':'仅统计签卡人的已确认认可，代录不计；按签卡人所属圈筛选，无所属圈的AM/OM按接收人所属圈筛选；明细按发生景点归圈。'):'切换类型后仅加载当前榜单';
    subfilters.innerHTML=`${selector}${sortControl}<span class="field-hint">${hint}</span>`;
    subfilters.querySelector('select')?.addEventListener('change',()=>{page=1;load()});
    subfilters.querySelectorAll('[data-pr-sort]').forEach(button=>button.onclick=()=>{const subtype=subfilters.querySelector('[name=subtype_id]')?.value;sortBy=button.dataset.prSort;page=1;renderSubfilters();const selector=subfilters.querySelector('[name=subtype_id]');if(selector&&subtype)selector.value=subtype;load()});
  };
  const params=()=>{const qs=new URLSearchParams([...new FormData(form)].filter(([,value])=>value));qs.set('category',category);qs.set('population',form.elements.population.value);qs.set('sort_by',sortBy);qs.set('page',String(page));qs.set('page_size','20');const subtype=subfilters.querySelector('[name=subtype_id]')?.value;if(subtype)qs.set('subtype_id',subtype);return qs};
  const updateExportState=()=>{const button=document.getElementById('prExport');if(!button)return;button.disabled=exportBusy;button.title='';};
  const load=async()=>{if(!request.isCurrent())return;const requestId=++requestSequence;results.innerHTML='<div class="empty">正在加载排名…</div>';try{const data=await api('/api/pr-rankings?'+params());if(requestId!==requestSequence||!request.isCurrent())return;results.innerHTML=prRankingTable(data);results.querySelectorAll('[data-pr-page]').forEach(button=>button.onclick=()=>{page=Number(button.dataset.prPage);load()})}catch(error){if(requestId===requestSequence&&request.isCurrent())results.innerHTML=`<div class="error">${esc(error.message)}</div>`}};
  document.getElementById('prCategoryTabs').querySelectorAll('[data-pr-category]').forEach(button=>button.onclick=()=>{category=button.dataset.prCategory;sortBy=['leader','gsm_leader'].includes(category)?'count':'score';page=1;document.querySelectorAll('[data-pr-category]').forEach(item=>item.classList.toggle('active',item===button));configurePopulation();renderSubfilters();updateExportState();load()});
  form.onsubmit=event=>{event.preventDefault();page=1;load()};
  form.elements.population.onchange=()=>{if(category==='gsm_leader')gsmPopulation=form.elements.population.value;else if(category!=='leader')scorePopulation=form.elements.population.value;page=1;load()};
  if(canExport)document.getElementById('prExport').onclick=async()=>{if(exportBusy)return;exportBusy=true;updateExportState();try{await queueExport('/api/pr-rankings/export?'+params());if(request.isCurrent())toast('已加入导出任务');}catch(error){if(request.isCurrent())toast(error.message,true);}finally{exportBusy=false;if(request.isCurrent())updateExportState();}};
  configurePopulation();renderSubfilters();updateExportState();await load();
}

export { renderPrRankings };
