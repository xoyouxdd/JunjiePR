// Embedded honor controls. Attendance scores and their pages are independent.
import { api, json } from './api.js';
import { esc, monthNow, portalPath } from './format.js';
import { state } from './state.js';
import { confirmModal, promptModal } from './dialogs.js';
import { toast } from './toast.js';

const previousMonth=()=>{const [y,m]=monthNow().split('-').map(Number);return `${m===1?y-1:y}-${String(m===1?12:m-1).padStart(2,'0')}`;};
const collectionRequests=new WeakMap();
// Immutable, shared static assets. No image generation or extra API calls at runtime.
const MEDAL_ART_ROOT='/static/images/attendance-medals/seasonal-521cf58ca25a';
const MEDAL_THEMES=[['冰蓝 · 钛银','小寒、大寒'],['青玉 · 浅金','立春、雨水'],['翠绿 · 暖金','惊蛰、春分'],
  ['湖青 · 银白','清明、谷雨'],['草木绿 · 鎏金','立夏、小满'],['朱红 · 赤金','芒种、夏至'],
  ['琥珀橙 · 亮金','小暑、大暑'],['古铜 · 深金','立秋、处暑'],['月白 · 香槟金','白露、秋分'],
  ['枫红 · 玫瑰金','寒露、霜降'],['墨蓝 · 冷银','立冬、小雪'],['冰紫 · 铂金','大雪、冬至']];

function medalMonth(month){return /^[0-9]{4}-(0[1-9]|1[0-2])$/.exec(String(month))?.[1]||null;}
function medalMonthLabel(month){const number=medalMonth(month);return number?`${month.slice(0,4)}年${Number(number)}月`:String(month);}
function medalFallback(){return '<svg class="medal-art-fallback" viewBox="0 0 100 120" aria-hidden="true"><path d="M28 3h44l-8 28H36z" fill="currentColor" opacity=".55"/><circle cx="50" cy="69" r="44" fill="#172237" stroke="currentColor" stroke-width="4"/><path d="M23 45 50 35l27 10v31L50 103 23 76z" fill="none" stroke="currentColor" stroke-width="4"/><path d="m35 67 12 12 23-27" fill="none" stroke="currentColor" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/></svg>';}
function medalArt(month,{expand=false,small=false}={}){
  const number=medalMonth(month),thumb=number?portalPath(`${MEDAL_ART_ROOT}/${number}-thumb.webp`):null;
  return `<span class="medal-art${small?' medal-art-small':''}" aria-hidden="true">${medalFallback()}${thumb?`<img data-medal-image src="${esc(thumb)}" data-medal-thumb="${esc(thumb)}"${expand?` data-medal-detail="${esc(portalPath(`${MEDAL_ART_ROOT}/${number}-detail.webp`))}"`:''} alt="" width="240" height="284" loading="lazy" decoding="async" fetchpriority="low">`:''}</span>`;
}

function wireMedalArt(host){
  host.querySelectorAll('img[data-medal-image]').forEach(image=>{
    const ready=()=>image.parentElement?.classList.add('is-ready');
    const failed=()=>{image.hidden=true;image.dataset.failed='true';image.parentElement?.classList.remove('is-ready');};
    image.onload=ready;image.onerror=failed;
    if(image.complete){if(image.naturalWidth)ready();else if(image.currentSrc)failed();}
  });
  host.querySelectorAll('details.medal-item').forEach(item=>item.addEventListener('toggle',event=>{
    if(event.target!==item)return;
    const image=item.querySelector('summary img[data-medal-detail]');
    if(item.open){
      [...item.parentElement.children].forEach(other=>{if(other!==item&&other.matches('details.medal-item[open]'))other.open=false;});
    }
    if(!image||image.dataset.failed)return;
    const source=item.open?image.dataset.medalDetail:image.dataset.medalThumb;
    if(image.getAttribute('src')!==source)image.src=source;
  }));
}

function medalCard(item,data,{compact=false}={}){
  const number=medalMonth(item.month),theme=number?MEDAL_THEMES[Number(number)-1]:null;
  const label=medalMonthLabel(item.month),issued=String(item.issued_at||'').slice(0,10);
  if(compact)return `<details class="medal-item medal-item-compact" data-medal-art-month="${number||'00'}"${theme?` title="${esc(theme[0]+' · '+theme[1])}"`:''}><summary><span class="medal-card-eyebrow">${esc(label)} · 全勤</span>${medalArt(item.month,{expand:true})}<strong>全勤勋章</strong><span class="medal-reveal">累计 ${esc(data.count)} 次 · ${esc(issued)} 发放</span></summary><div class="medal-compact-detail"><span class="medal-count-pill">累计 ${esc(data.count)} 次</span><span>发放于 ${esc(issued)}</span></div><details class="medal-rule-details"><summary>评定规则</summary><p>${esc(data.rule)}</p></details></details>`;
  return `<details class="medal-item" data-medal-art-month="${number||'00'}"${theme?` title="${esc(theme[0]+' · '+theme[1])}"`:''}><summary><span class="medal-card-eyebrow">月度全勤勋章</span>${medalArt(item.month,{expand:true})}<strong>${esc(label)} · 全勤</strong><span class="medal-reveal" aria-hidden="true">查看荣誉详情</span></summary><div class="medal-detail-card">${medalArt(item.month,{small:true})}<div class="medal-detail-copy"><h4>${esc(label)} · 全勤</h4><span class="medal-count-pill">累计 ${esc(data.count)} 次</span><p>发放日期 <time datetime="${esc(issued)}">${esc(issued)}</time></p></div><p class="medal-sentiment">每一份坚持，都值得被看见</p></div><details class="medal-rule-details"><summary>评定规则</summary><p>${esc(data.rule)}</p></details></details>`;
}

function rowHTML(row,issued=false){
  return `<article class="medal-person"><div><strong>${esc(row.name)}</strong> <small>${esc(row.employee_no)}</small><span class="badge">${esc(issued&&row.status==='拟发放'?'已获得':row.status)}</span></div><p>${esc(row.reason)}${row.first_month?` · 首次参与 ${esc(row.first_month)}`:''}</p>${row.leaves.length?`<details><summary>查看请假依据（${row.leaves.length}条）</summary>${row.leaves.map(r=>`<p>${esc(r.date)} · ${esc(r.leave_type)} · ${esc(r.hours)}小时</p>`).join('')}</details>`:''}</article>`;
}

function changeEmployeeLabel(row){
  return row.employee_no ? `${esc(row.name)} · ${esc(row.employee_no)}` : esc(row.name);
}

function confirmationList(title,rows){
  return rows.length ? `<p><strong>${title}</strong></p><ul>${rows.map(row=>
    `<li>${changeEmployeeLabel(row)} · ${esc(row.reason)}</li>`).join('')}</ul>` : '';
}

function issueHTML(issue){
  if(issue.redacted)return `<p>${esc(issue.reason)}</p>`;
  const parts=[issue.row?`第${issue.row}行`:'',issue.source_name,issue.source_no,issue.leave_type].filter(Boolean);
  return `<p>${parts.map(esc).join(' · ')}${parts.length?'：':''}${esc(issue.reason)}</p>`;
}

function codeClassifications(batch,codes=[]){
  const entries=new Map(codes.map(c=>[c.code,c]));
  for(const issue of batch.issues)if(issue.code_type==='unclassified'&&!entries.has(issue.leave_type))entries.set(issue.leave_type,{code:issue.leave_type,kind:'',revision:0});
  if(!entries.size)return '';
  return `<details><summary>核实工资代码分类</summary><p>未识别代码不能自动按无请假处理。确认分类后，需重新替换导入相关月份的原文件；已发勋章不会立即变化。</p>${[...entries.values()].map(entry=>`<form data-medal-code class="form-stack"><label>工资代码<input name="code" value="${esc(entry.code)}" readonly></label><input name="revision" type="hidden" value="${entry.revision}"><label>分类<select name="kind" required><option value="">请选择</option><option value="leave" ${entry.kind==='leave'?'selected':''}>请假（影响全勤）</option><option value="normal" ${entry.kind==='normal'?'selected':''}>正常工作、休息或调休（不影响全勤）</option></select></label><label>核实依据<input name="reason" maxlength="500" required></label><button type="submit" class="secondary">保存分类并重新核对</button></form>`).join('')}</details>`;
}

export function mountMedalManagement(host,{isCurrent=()=>true}={}){
  if(!host)return;
  let sequence=0,busy=false,data=null;
  const valid=()=>host.isConnected&&isCurrent();
  host.innerHTML=`<section class="panel attendance-medals"><h2>月度全勤勋章</h2><p>不绑定加分。月结且请假数据确认完整后，由各圈管理员分别发放。</p><label>评定月份<input data-medal-month type="month" min="2026-11" value="${esc(previousMonth()<'2026-11'?'2026-11':previousMonth())}"></label><div data-medal-body role="status">正在加载…</div></section>`;
  const monthInput=host.querySelector('[data-medal-month]'),body=host.querySelector('[data-medal-body]');
  async function load(){
    const token=++sequence,month=monthInput.value;
    try{
      const result=await api('/api/attendance-medals/manage?'+new URLSearchParams({month}));
      if(!valid()||sequence!==token)return;
      data=result;
      const batch=result.batch;
      body.innerHTML=`<div class="medal-design-preview" data-medal-art-month="${medalMonth(month)||'00'}">${medalArt(month)}<div><span>本月勋章样式</span><strong>${esc(medalMonthLabel(month))}</strong><small>${esc(MEDAL_THEMES[Number(medalMonth(month))-1]?.[0]||'全勤荣誉')}</small></div></div>${month<'2027-01'?'<div class="notice">管理端试用月份：可以核对、发放和调整；不向员工展示或通知，不计正式累计。2027年1月起正式评定，2月起发放。</div>':''}${result.can_import?`<details><summary>全员请假数据导入与确认</summary><p>正常缺勤文件导入成功后会同步全勤勋章数据。下方可单独补导勋章数据，不改变计分或月结。</p><form data-medal-import class="form-stack"><label>处理方式<select name="mode"><option value="append">追加（相同记录不重复）</option><option value="replace">替换该月完整数据</option></select></label><label>全员事务文件<input name="workbook" type="file" accept=".xls" required></label><button type="submit">导入勋章请假数据</button></form>${batch?`<p>${esc(batch.filename)} · ${esc(batch.imported_by)} · ${esc(batch.imported_at)}<br>版本 ${result.revision} · 有效请假 ${batch.leaves} 条 · 异常 ${batch.issues.length} 条 · ${result.data_ready?'已确认完整':'待确认完整'}</p><details><summary>未纳入请假的事务代码（请核对）</summary><p>${batch.ignored_codes.map(esc).join('、')||'无'}</p><p>未知工资代码必须先核实分类，再重新替换导入，不能直接忽略。</p></details>${codeClassifications(batch,result.wage_codes||[])}${batch.issues.length?`<div class="error">异常必须处理后重新替换导入；明确归属的异常只阻断对应圈。</div><details><summary>查看导入异常（${batch.issues.length}条）</summary>${batch.issues.map(issueHTML).join('')}</details>`:''}<button type="button" data-medal-confirm ${result.data_ready?'disabled':''}>确认该月全员数据完整</button>${result.highest?'<button type="button" class="secondary" data-medal-refresh>修正当月人员圈归属快照</button>':''}`:'<p>尚未导入。全月无人请假时，也需导入包含该月正常事务的完整文件以证明月份，并确认完整。</p>'}</details>`:''}${result.highest&&result.data_ready?`<details><summary>修正单个人员的月份归属</summary><p>只修正勋章评定快照，不改变员工当前景点圈。跨圈结果由最高管理员核对相关圈后确认。</p><form data-medal-scope class="form-stack"><label>员工<select name="employee_id" required><option value="">请选择</option>${(result.roster||[]).map(r=>`<option value="${r.id}">${esc(r.name)} · ${esc(r.employee_no)}</option>`).join('')}</select></label><label>评定月份所属圈<select name="attraction_id" required>${result.circles.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select></label><label>核实原因<input name="reason" required maxlength="500"></label><button type="submit" class="secondary">保存月份归属</button></form></details>`:''}${result.unassigned_count?`<p class="notice">${result.unassigned_count} 名人员圈归属未明确，请在现有人员管理中核实。</p>`:''}<div class="medal-circle-list">${result.items.map(item=>{
        const circle=result.circles.find(c=>c.id===item.attraction_id),counts={};item.rows.forEach(r=>{const label=item.status==='已发放'&&r.status==='拟发放'?'已获得':r.status;counts[label]=(counts[label]||0)+1;});
        return `<section class="medal-circle" data-medal-circle="${item.attraction_id}"><h3>${esc(circle?.name)} <span class="badge">${esc(item.status)}</span></h3><p>${Object.entries(counts).map(([k,v])=>`${esc(k)} ${v}人`).join(' · ')||'本圈暂无参与人员'}</p>${item.confirmed_by?`<p>上次确认：${esc(item.confirmed_by)} · ${esc(item.confirmed_at)}</p>`:''}${item.requires_highest&&!result.highest?'<p class="field-hint">历史月份首次补发需最高管理员核对确认。</p>':''}${item.month_closed===false?'<p class="field-hint">月结前可核对数据，完成月结后才能确认发放。</p>':''}${item.issues.length?`<div class="error">待处理请假记录 ${item.issues.length} 条${item.issues.map(issueHTML).join('')}</div>`:''}${item.changes.length?`<details><summary>本次发放变化（${item.changes.length}人）</summary>${item.changes.map(r=>`<p>${changeEmployeeLabel(r)} · ${esc(r.action)} · ${esc(r.reason)}</p>`).join('')}</details>`:''}<label>搜索姓名或工号<input data-medal-search placeholder="只筛选显示，不改变发放名单"></label><div class="medal-person-list">${item.rows.map(row=>rowHTML(row,item.status==='已发放')+(result.highest&&row.needs_identity_date?`<form data-medal-identity="${row.employee_id}" class="form-stack"><label>核实原账号创建日期<input name="first_created_on" type="date" required></label><label>核实原因<input name="reason" required maxlength="500"></label><button type="submit" class="secondary">保存原账号日期</button></form>`:'')).join('')}</div><div class="actions"><button type="button" data-medal-publish="${item.attraction_id}" ${!item.can_publish||item.status==='已发放'?'disabled':''}>${item.status==='待调整'?'确认调整':'确认本圈统一发放'}</button><button type="button" class="secondary" data-medal-history="${item.attraction_id}">查看操作记录</button></div><div data-medal-history-content></div></section>`;
      }).join('')}</div><p class="field-hint">${esc(result.items[0]?.rule||'依据月结后的有效请假和考勤记录评定。')}</p>`;
      wireMedalArt(body);
      bind();
    }catch(error){if(valid()&&token===sequence){body.innerHTML=`<p class="error">${esc(error.message)}</p><button type="button" data-medal-retry>重新读取</button>`;body.querySelector('[data-medal-retry]').onclick=load;}}
  }
  async function perform(operation){
    if(busy)return;busy=true;monthInput.disabled=true;body.querySelectorAll('button').forEach(b=>b.disabled=true);
    try{await operation();await load();}catch(error){toast(error.message,true);await load();}finally{busy=false;monthInput.disabled=false;}
  }
  function bind(){
    body.querySelectorAll('[data-medal-code]').forEach(form=>form.onsubmit=event=>{event.preventDefault();const payload={code:form.elements.code.value,kind:form.elements.kind.value,revision:Number(form.elements.revision.value),reason:form.elements.reason.value};perform(async()=>{if(!await confirmModal('确认工资代码分类',`<p>${esc(payload.code)}：${payload.kind==='leave'?'请假，影响全勤':'正常工作、休息或调休，不影响全勤'}</p><p>相关月份会重新进入待核对，须重新替换导入原文件。已发勋章仍需管理员确认调整。</p>`,'确认分类'))return;await api('/api/attendance-medals/classify-code',json('POST',payload));toast('分类已保存，请重新替换导入原文件');});});
    body.querySelector('[data-medal-import]')?.addEventListener('submit',event=>{
      event.preventDefault();const form=event.currentTarget,formData=new FormData(form);formData.set('month',data.month);
      perform(async()=>{const preview=await api('/api/attendance-medals/import-preview',{method:'POST',body:formData});if(!await confirmModal('导入全勤勋章数据',`<p>${esc(data.month)} · ${formData.get('mode')==='replace'?'替换整月':'追加'}。</p><p>文件请假 ${preview.leaves}条；新增 ${preview.added}条，移除 ${preview.removed}条，异常 ${preview.issues.length}条。</p><p>已发勋章不会立即改变，三个圈需按新数据核对。</p>`,'确认导入'))return;formData.set('expected_revision',String(preview.revision));formData.set('file_digest',preview.file_digest);const out=await api('/api/attendance-medals/import',{method:'POST',body:formData});toast(`导入完成：${out.leaves}条请假，${out.issues.length}条异常；请核对并确认完整。`);});
    });
    for(const [selector,refresh] of [['[data-medal-confirm]',false],['[data-medal-refresh]',true]])body.querySelector(selector)?.addEventListener('click',()=>perform(async()=>{
      const reason=refresh?await promptModal('修正人员归属快照','请填写原因；将按历史组织记录重新核对，可能影响已发放结果。','修正原因'):'';if(refresh&&!reason)return;
      if(!refresh&&!await confirmModal('确认全员请假数据完整',`<p>请确认 ${esc(data.month)} 三个圈的全部有效请假已导入，且未纳入的事务代码不包含请假。该操作不会自动发勋章。</p>`,'确认完整'))return;
      await api('/api/attendance-medals/confirm-data',json('POST',{month:data.month,revision:data.revision,refresh_roster:refresh,reason}));toast('数据完整状态已确认');
    }));
    body.querySelector('[data-medal-scope]')?.addEventListener('submit',event=>{event.preventDefault();const form=event.currentTarget,payload={month:data.month,employee_id:Number(form.elements.employee_id.value),attraction_id:Number(form.elements.attraction_id.value),reason:form.elements.reason.value};perform(async()=>{if(!await confirmModal('修正月份归属',`<p>修正 ${esc(payload.month)} 的人员归属；已发勋章仍需核对相关圈后确认调整。</p>`,'确认保存'))return;await api('/api/attendance-medals/repair-scope',json('POST',payload));toast('月份归属已修正，请核对相关圈');});});
    body.querySelectorAll('[data-medal-identity]').forEach(form=>form.onsubmit=event=>{event.preventDefault();const payload={employee_id:Number(form.dataset.medalIdentity),first_created_on:form.elements.first_created_on.value,reason:form.elements.reason.value};perform(async()=>{if(!await confirmModal('核实原账号日期',`<p>保存核实日期 ${esc(payload.first_created_on)}，将重新计算参与资格并保留审计。</p>`,'确认保存'))return;await api('/api/attendance-medals/repair-identity',json('POST',payload));toast('原账号日期已保存');});});
    body.querySelectorAll('[data-medal-search]').forEach(input=>input.oninput=()=>{const keyword=input.value.trim().toLowerCase();input.closest('[data-medal-circle]').querySelectorAll('.medal-person').forEach(node=>node.hidden=!node.textContent.toLowerCase().includes(keyword));});
    body.querySelectorAll('[data-medal-publish]').forEach(button=>button.onclick=()=>perform(async()=>{
      const item=data.items.find(i=>i.attraction_id===Number(button.dataset.medalPublish)),circle=data.circles.find(c=>c.id===item.attraction_id);
      const adjusting=item.status==='待调整';
      const adjustmentPrompt=item.trial?'请填写本次调整原因。试行月份不会通知员工。':'请填写补发或撤回原因。确认后仅通知结果有变化的员工。';
      const reason=adjusting?await promptModal('调整全勤勋章',adjustmentPrompt,'调整原因'):'';
      if(adjusting&&!reason)return;
      const count=item.rows.filter(r=>r.status==='拟发放').length;
      const additions=item.changes.filter(row=>row.action==='补发');
      const withdrawals=item.changes.filter(row=>row.action==='撤回');
      const confirmationLists=item.trial
        ? confirmationList('待发放名单',additions)+confirmationList('待撤回名单',withdrawals)
          +confirmationList('待移入名单',item.changes.filter(row=>row.action==='移入'))
          +confirmationList('待移出名单',item.changes.filter(row=>row.action==='移出'))
          +confirmationList('仅更新获奖名单',item.changes.filter(row=>row.action==='确认名单'))
        : '';
      if(!await confirmModal(adjusting?'确认调整':'确认统一发放',`<p>${esc(circle.name)} · ${esc(item.month)}</p><p>数据版本 ${item.revision} · 导入时间 ${esc(item.data_imported_at||'待核对')}</p><p>符合全勤 ${count}人；${adjusting?'补发':'本次发放'} ${additions.length}人，撤回 ${withdrawals.length}人。</p>${confirmationLists}<p>请确认该月请假数据完整，名单已核对。${item.trial?'本月为管理端试用，不通知员工、不计正式累计。':''}</p>`,'确认'))return;
      const out=await api('/api/attendance-medals/publish',json('POST',{month:item.month,attraction_id:item.attraction_id,revision:item.revision,fingerprint:item.fingerprint,reason}));
      toast(out.duplicate?'这次确认已处理':`处理完成：发放${out.awarded||0}人，撤回${out.revoked||0}人${out.moved?`，跨圈移动${out.moved}人`:''}`);
    }));
    body.querySelectorAll('[data-medal-history]').forEach(button=>button.onclick=async()=>{
      try{const out=await api('/api/attendance-medals/history?'+new URLSearchParams({month:data.month,attraction_id:button.dataset.medalHistory}));if(!valid())return;button.closest('[data-medal-circle]').querySelector('[data-medal-history-content]').innerHTML=out.items.map(r=>`<p>${esc(r.time)} · ${esc(r.operator)} · ${esc(r.action)}${r.reason?' · '+esc(r.reason):''}</p>`).join('')||'<p>暂无操作记录</p>';}catch(error){toast(error.message,true);}
    });
  }
  monthInput.onchange=load;load();
  return {refresh:load};
}

export function mountTrialMedalResults(host,{isCurrent=()=>true}={}){
  host.innerHTML='<label>试行月份<select data-medal-trial-month><option value="2026-11">2026 年 11 月</option><option value="2026-12">2026 年 12 月</option></select></label><div data-medal-trial-results aria-live="polite"></div>';
  const month=host.querySelector('[data-medal-trial-month]'),body=host.querySelector('[data-medal-trial-results]');
  let sequence=0;
  async function load(){
    const token=++sequence;
    const valid=()=>host.isConnected&&isCurrent()&&token===sequence;
    body.textContent='正在加载…';
    try{
      const data=await api('/api/attendance-medals/manage?'+new URLSearchParams({month:month.value,trial_view:'true'}));
      if(!valid())return;
      body.innerHTML=data.items.map(item=>{
        const circle=data.circles.find(entry=>entry.id===item.attraction_id);
        return `<section class="medal-circle"><h3>${esc(circle?.name)} <span class="badge">${esc(item.status)}</span></h3>${item.status==='已发放'?`<p>${item.rows.length} 人获得</p>${item.rows.map(row=>`<p>${esc(row.name)} · ${esc(row.employee_no)}</p>`).join('')}`:''}</section>`;
      }).join('');
    }catch(error){if(valid())body.textContent=error.message||'读取试行结果失败';}
  }
  month.onchange=load;
  load();
}

export function medalCollectionAvailable(month=null){
  return monthNow()>='2027-02'&&(!month||month>='2027-01');
}

export async function mountMedalCollection(host,employeeId,{isCurrent=()=>true,lookup=false,month=null,compact=false}={}){
  if(!host)return;
  const token=(collectionRequests.get(host)||0)+1;collectionRequests.set(host,token);
  const valid=()=>host.isConnected&&isCurrent()&&collectionRequests.get(host)===token;
  if(!medalCollectionAvailable(month)){host.replaceChildren();return;}
  try{
    let data=await api('/api/attendance-medals/employees/'+encodeURIComponent(employeeId));
    if(!valid())return;
    if(!data.visible){host.innerHTML=month?'<p class="empty">全勤勋章暂未开放。</p>':'';return;}
    if(month)data={...data,items:data.items.filter(item=>item.month===month),assessments:(data.assessments||[]).filter(item=>item.month===month),pending_month:data.pending_month?.month===month?data.pending_month:null};
    const own=Number(employeeId)===Number(state.me.id);
    host.innerHTML=`<section class="attendance-medals medal-collection${compact?' medal-collection-compact':''}"><h3>${own?'我的':'员工'}全勤勋章 <span class="badge">累计 ${data.count}次</span></h3>${data.items.length?`<div class="medal-items">${data.items.map(item=>medalCard(item,data,{compact})).join('')}</div>`:'<p>已获得的月度全勤勋章会保存在这里。</p>'}${own&&data.pending_month?`<p>${esc(data.pending_month.month)} 评定：${esc(data.pending_month.status)}</p>`:''}${own?(data.notices||[]).filter(n=>!n.read).map(n=>`<div class="notice">${esc(n.message)}${n.reason?`<p>${esc(n.reason)}</p>`:''}<button type="button" class="secondary" data-medal-notice="${n.id}">知道了</button></div>`).join(''):''}${own&&data.assessments?.length?`<details><summary>本人评定详情</summary>${data.assessments.map(r=>`<article><strong>${esc(r.month)} · ${esc(r.status==='拟发放'?'已获得':r.status)}</strong><p>${esc(r.reason)}</p>${r.leaves.map(l=>`<p>${esc(l.date)} · ${esc(l.leave_type)} · ${esc(l.hours)}小时</p>`).join('')}</article>`).join('')}<p>如有异议，请联系本圈管理员。</p></details>`:''}${lookup?'<details><summary>查看同事已获勋章</summary><form data-medal-lookup><label>姓名或工号<input name="keyword" required maxlength="50"></label><button type="submit" class="secondary">查询</button></form><div data-medal-people></div><div data-medal-peer></div></details>':''}</section>`;
    wireMedalArt(host);
    host.querySelectorAll('[data-medal-notice]').forEach(button=>button.onclick=async()=>{try{await api('/api/attendance-medals/notices/'+button.dataset.medalNotice+'/read',json('POST',{}));if(valid())button.closest('.notice').remove();}catch(error){toast(error.message,true);}});
    let lookupSequence=0;
    host.querySelector('[data-medal-lookup]')?.addEventListener('submit',async event=>{
      event.preventDefault();const seq=++lookupSequence,people=host.querySelector('[data-medal-people]');
      try{const out=await api('/api/attendance-medals/people?'+new URLSearchParams({keyword:event.currentTarget.elements.keyword.value}));if(!valid()||seq!==lookupSequence)return;people.innerHTML=out.items.map(p=>`<button type="button" class="secondary" data-medal-peer-id="${p.id}">${esc(p.name)} · ${esc(p.employee_no)}</button>`).join('')||'<p>未找到员工</p>';people.querySelectorAll('[data-medal-peer-id]').forEach(button=>button.onclick=()=>mountMedalCollection(host.querySelector('[data-medal-peer]'),button.dataset.medalPeerId,{isCurrent}));}catch(error){if(valid())people.textContent=error.message;}
    });
  }catch(error){if(valid()){host.innerHTML=`<p class="field-hint">全勤勋章读取失败：${esc(error.message)}</p>`;}}
}
