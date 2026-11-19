// Formal employee rotation entry and personal action-center tasks.
import { api, json } from './api.js';
import { beginViewRequest, registerPageCleanup } from './context.js';
import { confirmModal } from './dialogs.js';
import { refreshActionBadge } from './navigation.js';
import { esc, portalPath } from './format.js';
import { state } from './state.js';
import { toast } from './toast.js';

// Existing navigation ID remains stable; real employee identity authorizes the formal board.
async function renderRotationTest(){
  const request=beginViewRequest();
  if(!request.isCurrent())return;
  const codes=state.me.role_codes||[state.me.role_code,...(state.me.duty_role_codes||[])];
  if(!codes.some(code=>['CM','TR','SUPERVISOR','TA_SUPERVISOR','GSM','TA_GSM'].includes(code))){location.href=portalPath('/?view=actionCenter');return;}
  location.href=portalPath('/rotation-board');
}

const hm=value=>{
  if(value===null||value===undefined)return '--:--';
  const minute=((Math.floor(value)%1440)+1440)%1440;
  return `${String(Math.floor(minute/60)).padStart(2,'0')}:${String(minute%60).padStart(2,'0')}`;
};
const taskTime=value=>value===null||value===undefined?'--:--':hm(value)+(Math.floor((value-Math.floor(value))*60+1e-6)?':'+String(Math.floor((value-Math.floor(value))*60+1e-6)).padStart(2,'0'):'');
const personalTaskPaused=p=>!!(p.timeoutCase||p.timeoutPending||p.task?.status==='exception_hold');
function personalAssignment(p,data){
  if(personalTaskPaused(p)||p.entryHold)return null;
  if(p.task)return ['assigned','forecast'].includes(p.task.status)?p.assign:null;
  return data.taskWorkflow===false?p.assign:null;
}
function personalTiming(p,now){
  const start=p.continuousStart??p.lineStart;
  if(p.state==='onpost'&&start!==null&&start!==undefined)return `<p role="status">${p.timingUnverified?'历史时间待核实 · 核实后已记录':'连续在岗'} ${Math.max(0,Math.floor(now-start))} 分钟</p>`;
  return p.timingUnverified?'<p role="status">历史时间待核实</p>':'';
}
function personalReceipt(result){
  if(result.action==='batch_check_in')return '已签到';
  return result.action==='depart'?'已确认上岗安排，请前往 '+esc(result.line)+'线 · '+esc(result.post)+' 执行':'已到休息室';
}
function entryRoutes(routes){return (routes||[]).map(r=>`<li><strong>${esc(r.name)}</strong>：${esc(r.from)} → ${esc(r.to)}</li>`).join('');}
function taskLine(line,pid){
  if(!line)return '';
  return `<div class="rt-line"><div class="rt-line-head"><strong>${esc(line.id)}线 · 岗位详情</strong></div>${line.posts.map(post=>`<div class="rt-post ${post.occ===pid?'me':''}"><span class="rt-pn">${esc(post.name)}</span><span class="rt-who-name">${post.occ===pid?`<strong class="rt-own-post-name">${esc(post.occName||'空岗')}</strong>`:esc(post.occName||'空岗')}${post.missedCheckIn?'<small class="rt-badge rt-missed-attendance">未答到</small>':''}${post.hold?'<span class="rt-badge post-hold">定岗</span>':''}</span><span class="rt-mins">${post.open?'':'未开岗'}</span></div>`).join('')}</div>`;
}

function personalTaskComparison(p){
  if(!p.task)return '';
  const task=p.task,batch=task.status==='batch_result',finished=batch||task.status==='executed';
  const originalWorkers=new Set(task.beforePosts.map(post=>post.occ));
  const shiftEndPosts=new Set(task.shiftEndPosts);
  const openingPosts=new Set((task.openingPosts||(task.opening?[task.opening]:[])).map(post=>post.key));
  const push7Posts=new Set(task.handoffChain?.filter(step=>step.kind==='push7').map(step=>step.postKey));
  const table=(posts,after)=>`<div class="rt-task-line"><header>${esc(task.line)}</header>${posts.map((post,i)=>{
    const original=task.beforePosts[i];
    const shiftEndRow=shiftEndPosts.has(original?.key);
    const shiftEnd=!after&&shiftEndRow;
    const opening=openingPosts.has(post.key);
    const originalSeven=original?.seven??push7Posts.has(original?.key);
    const seven=after?post.seven:originalSeven;
    const sevenRow=originalSeven||task.afterPosts[i]?.seven;
    const missed=post.missedCheckIn;
    const attendanceRow=original?.missedCheckIn||task.afterPosts[i]?.missedCheckIn;
    const changed=after&&post.occ!==original?.occ;
    const newWorker=changed&&post.occ&&!originalWorkers.has(post.occ);
    const classes=(shiftEndRow?' shift-end-row':'')+(sevenRow?' seven-row':'')+(attendanceRow?' attendance-row':'')+(opening?' opening-row':'')+(shiftEnd?' shift-end':'')+(changed?' changed':'')+(newWorker?' new-worker':'');
    const name=`<strong>${esc(post.occName||(opening&&!after?'—':post.open===false?'未开岗':'空岗'))}</strong>`;
    const sevenBadge=seven?'<small class="rt-badge p7">7点</small>':'';
    const missedBadge=missed?'<small class="rt-badge rt-missed-attendance">未答到</small>':'';
    const person=seven||shiftEnd||missed?`<div class="rt-task-person${shiftEnd?' rt-shift-end-person':''}">${name}${sevenBadge}${missedBadge}${shiftEnd?'<small>下班</small>':''}</div>`:name;
    const label=opening?`<span class="rt-task-post-label">${esc(post.name)}<small>${after?'加岗':'待加岗'}</small></span>`:`<span>${esc(post.name)}</span>`;
    return `<div class="rt-task-slot${classes}">${label}${person}</div>`;
  }).join('')}</div>`;
  return `<section class="rt-task-comparison rt-personal-comparison"><h4>${batch?'整批排岗前后':'执行本任务前后'}</h4><div class="rt-task-columns"><div><p>执行前 · ${task.status==='executed'?'原站位':'当前站位'}</p>${table(task.beforePosts,false)}</div><div><p>执行后 · ${finished?'最终站位':'预计站位'}</p>${table(task.afterPosts,true)}</div></div>${finished?'':`<small>只标出本任务变化。${taskTime(task.allowedAt)} 可出发，${taskTime(task.deadlineAt)} 前确认。</small>`}${task.transferredFromName?`<p>转接自 ${esc(task.transferredFromName)}</p>`:''}</section>`;
}

function taskInstruction(task,suffix=''){
  if(!task.instruction)return '';
  const content=task.instructionParts?.map(part=>part.emphasis?
    `<strong class="rt-special-instruction">${esc(part.text)}</strong>`:esc(part.text)).join('')??esc(task.instruction);
  return `<p>${content}${suffix}</p>`;
}

const taskOpeningBadge=post=>`<span class="rt-task-opening">加岗（${esc(post.name)}）</span>`;

function singleTaskSummary(task){
  if(!task?.kind)return '';
  const heading=`${task.line}线 · ${task.entryPost}${task.pushes?` · ${task.pushes}推`:''}`;
  const shiftEnd=task.shiftEndPosts?.length?'<span class="rt-batch-shift-end">推下班</span>':'';
  const opening=task.opening?taskOpeningBadge(task.opening):'';
  const special=['push7','sick','out'].includes(task.kind)?`<span class="rt-confirm-tag">${esc(task.reason)}</span>`:'';
  return `<div class="rt-single-task-summary"><div class="rt-batch-result-heading"><strong>${esc(heading)}</strong>${shiftEnd}${opening}${special}</div>${taskInstruction(task,'。')}</div>`;
}
function personalLineChange(out,previous){
  if(out.line===previous)return '<p class="rt-route-change" role="alert">本线接替路线已更新。</p>';
  const names=(out.waiting_predecessors||[]).map(p=>esc(p.name)).join('、');
  return `<p class="rt-route-change" role="alert">原预计 ${esc(previous||'')}，现调整为 ${esc(out.line)}。${names?`<br>${names}还未上岗，请确认先上。`:''}</p>`;
}

function personalShift(p){
  return hm(p.end);
}
function personalConfirmation(p,action,data,pending,result){
  if(personalTaskPaused(p))return '';
  const arrived=!action&&['rest','meal'].includes(p.state)&&result?.action==='arrive';
  const accepted=!action&&p.state==='onpost'&&result?.action==='depart';
  if(!['arrive','depart'].includes(action)&&!arrived&&!accepted)return '';
  const room=action==='arrive'||arrived,a=personalAssignment(p,data),t=['assigned','forecast'].includes(p.task?.status)?p.task:null;
  const completed=accepted&&p.line===result.line&&p.post===result.post&&p.confirmationSequence===result.sequence?result.task:null;
  const destination=accepted?`${esc(p.line)} · ${esc(p.post)}`:`${esc(a?.line||'')} · ${esc(a?.entryPost||'入口岗位')}`;
  const selected=p.state==='meal'?'meal':'rest';
  const choice=(value,title,hint,checked,disabled=false)=>`<label class="rt-confirm-choice"><input type="radio" name="formalRotationMeal" value="${value}" ${checked?'checked':''} ${disabled?'disabled':''}><span><strong>${title}</strong><small>${hint}</small></span></label>`;
  const choices=p.mealOptions?.forced?choice('meal','吃饭','本次按原规则安排吃饭',true,true):p.mealOptions?.canChoose?choice('rest','休息','确认后开始休息计时',true)+choice('meal','吃饭','确认后开始吃饭计时',false):choice('rest','休息','本次按原规则安排休息',true);
  const source=p.lastDeparture,ending=room&&p.arrivalOutcome;
  const outcome=ending==='done'?'本次确认后结束当日轮岗。':ending==='away'?'本次确认后按原安排转为圈外状态。':'';
  const receipt=result?`<p class="field-hint rt-confirm-receipt">最近安排生效：${hm(result.minute)} · ${personalReceipt(result)}</p>`:'';
  return `<section class="panel rt-personal-task rt-confirm-page ${p.task?.status==='forecast'&&p.canDepart?'rt-can-board':''}" data-confirm-page="${room?'room':'depart'}">
    <header class="rt-confirm-brand"><strong>FZ工作台</strong><span>轮岗</span><a href="${portalPath('/rotation-board')}">当日看板</a></header>
    <div class="rt-personal-identity"><strong>${esc(p.name)}</strong><span>${personalShift(p)}</span><small class="rt-confirm-tag">${arrived?(selected==='meal'?'吃饭中':'休息中'):accepted?'已确认安排':room?'待确认':'待出发'}</small></div>
    <h3>${room?'回休息室确认':'上岗前确认'}</h3>
    ${room?`<p class="rt-confirm-lead">实际到达休息室后，再确认。</p>${source?`<div class="rt-confirm-origin"><span>本次下岗</span><strong>${esc(source.line)} · ${esc(source.post)}</strong></div>`:''}
    ${ending?`<p class="rt-confirm-rule">${outcome}</p>`:arrived?`<div class="rt-confirm-result" role="status"><strong>${selected==='meal'?'吃饭':'休息'}已开始</strong><p>至 ${hm(p.readyAt)} · 剩 ${Math.max(0,Math.ceil((p.readyAt??data.clock.minute)-data.clock.minute))} 分钟。</p></div>`:`<fieldset class="rt-confirm-choices"><legend>本次安排<small>${p.mealOptions?.canChoose?'当前可选择休息或吃饭':'按当前餐休规则安排'}</small></legend>${choices}</fieldset><p class="rt-confirm-rule">从本次确认开始计时，<br>以系统显示的餐休倒计时为准。</p>`}`:
    `<div class="rt-confirm-destination"><span>本次任务</span>${singleTaskSummary(t||completed)||`<strong>${destination}</strong>`}</div>${t?`<div class="rt-confirm-times"><span>允许出发<strong>${taskTime(t.allowedAt)}</strong></span><span>确认截止<strong>${taskTime(t.deadlineAt)}</strong></span></div><p class="field-hint">${t.status==='forecast'?'当前为预计安排，确认时重新计算。':'本轮任务已派发。'}${data.clock.minute<t.allowedAt?`餐休剩 ${Math.max(0,Math.ceil((p.readyAt??data.clock.minute)-data.clock.minute))} 分钟 · 距允许出发 ${Math.max(0,Math.ceil(t.allowedAt-data.clock.minute))} 分钟；到时才可确认。`:'已到允许出发时间，请按任务前往。'}</p>${t.transferredFromName?`<p class="rt-transfer">任务转接自 ${esc(t.transferredFromName)}</p>`:''}`:''}${completed?personalTaskComparison({task:completed}):accepted?`<div class="rt-confirm-result" role="status"><strong>已确认上岗安排</strong><p>现在前往 ${destination}，按接替路线通知并接替。</p></div>`:''}`}
    ${arrived&&a?`<div class="rt-confirm-destination"><span>${p.task?.status==='forecast'?'下一次预计安排':'下一次上岗任务已派发'}</span><strong>${destination}</strong><small>${taskTime(a.departAt)} 允许出发，餐休结束后才可确认。</small></div>`:''}
    ${personalTiming(p,data.clock.minute)}
    ${p.postHold?`<p class="field-hint">持续定岗 · ${hm(p.postHold.since)}起 · ${esc(p.postHold.reason)}</p>`:''}
    ${!room&&!accepted?'<p class="rt-confirm-notice">看清任务后确认，前往指定岗位通知并接替。</p>':''}
    ${pending?'<p role="status">上次提交结果待核对，可刷新或使用原步骤重试。</p><button type="button" class="primary" data-rotation-retry>重试本次确认</button>':action?`<button type="button" class="primary" data-rotation-task="${action}" ${action==='depart'&&!p.canDepart?'disabled':''}>${room?'确认已到休息室':p.task?.status==='forecast'?'可上岗':'确认去上岗'}</button>`:`<button type="button" class="primary" disabled>${room?'已确认':'已确认上岗安排'}</button>`}
    ${receipt}${accepted&&!completed&&data.line?`<details class="rt-confirm-line"><summary>当前线路岗位详情</summary>${taskLine(data.line,p.pid)}</details>`:''}
  </section>`;
}

function mountRotationTasks(host,request,onComplete){
  if(!host||!state.me.rotation_entry)return;
  if(!document.getElementById('formalRotationStyles')){const css=document.createElement('link');css.id='formalRotationStyles';css.rel='stylesheet';css.href=portalPath('/static/css/rotation.css')+new URL(import.meta.url).search;document.head.appendChild(css);}
  let busy=false,loading=false,closed=false,data=null,selectedCircle=null,queuedScope=false;
  const key='formalRotation.todo.'+state.me.id;
  let pending=null;try{pending=JSON.parse(sessionStorage.getItem(key)||'null');}catch{}
  selectedCircle=pending?.body?.attraction_id||null;
  const save=value=>{pending=value;try{if(value)sessionStorage.setItem(key,JSON.stringify(value));else sessionStorage.removeItem(key);}catch{}};
  const current=()=>!closed&&request.isCurrent()&&host.isConnected;
  const confirmScope=(title,message,options)=>confirmModal(title,`<p>当前景点圈：${esc(data?.identity?.attraction_name||state.me.attraction_name||'')}</p>${message}`,'确认',options);
  const arrivalFormState=payload=>{
    const p=payload?.person;
    return JSON.stringify([payload?.clock?.date,payload?.attraction_id,p?.state,p?.confirmationTokens,
      p?.canDepart,p?.arrivalOutcome,p?.mealEligible,p?.ate,p?.mealOptions?.canChoose,p?.mealOptions?.forced,
      p?.mealOptions?.supply,p?.mealOptions?.required,p?.mealOptions?.reserveWithin,personalTaskPaused(p||{})]);
  };
  async function load(force=false){
    if(loading){if(force)queuedScope=true;return;}
    if(!current()||busy)return;
    loading=true;
    try{
      const requestedCircle=selectedCircle;
      const next=await api('/api/rotation-board/me'+(requestedCircle?'?attraction_id='+requestedCircle:''));
      if(!current()||busy||requestedCircle!==selectedCircle)return;
      const active=document.activeElement;
      const sameArrival=data?.clock.date===next.clock.date&&data?.attraction_id===next.attraction_id
        &&data?.person?.confirmationTokens?.arrive===next.person?.confirmationTokens?.arrive;
      const keepControl=!force&&!pending&&host.contains(active)&&(
        active?.matches?.('[name=formalRotationMeal]')&&arrivalFormState(data)===arrivalFormState(next)
        ||active?.matches?.('[data-personal-circle]')&&JSON.stringify(data?.rotation_circles)===JSON.stringify(next.rotation_circles)
          &&arrivalFormState(data)===arrivalFormState(next));
      data=next;selectedCircle=next.attraction_id||selectedCircle;
      if(keepControl&&!data.readOnly)return;
      if(data.readOnly&&pending)save(null);
      if(pending){
        if(pending.date!==data.clock.date){save(null);toast('此前轮岗确认已跨日，请联系主管核对当日记录。',true);}
        else{
          const result=await api('/api/rotation-board/confirmation?date='+encodeURIComponent(pending.date)+'&token='+encodeURIComponent(pending.body.confirmation_token)+(pending.body.attraction_id?'&attraction_id='+pending.body.attraction_id:''));
          // A successful receipt lookup is authoritative even when the old
          // step was not committed or was revoked. Transport errors keep it.
          save(null);
          if(result.confirmed&&onComplete){
            toast('已核实轮岗确认成功');
            onComplete();return;
          }
          if(!result.confirmed)toast('旧步骤未生效或已撤销，请按最新任务操作。');
        }
      }
      if(!current())return;
      const circles=data.rotation_circles||[];
      const scopeHtml=circles.length?`<div class="panel rt-personal-scope"><label>当前轮岗景点圈${circles.length>1?`<select data-personal-circle ${pending?'disabled':''}>${circles.map(c=>`<option value="${c.id}" ${c.id===data.attraction_id?'selected':''}>${esc(c.name)}</option>`).join('')}</select>`:`<strong>${esc(circles[0].name)}</strong>`}</label>${pending?'<small>此前确认结果待核对，核对完成后可切换。</small>':''}</div>`:'';
      host.dataset.rotationCircle=data.attraction_id||'';
      delete host.dataset.scopeLoading;
      const bindScope=()=>host.querySelector('[data-personal-circle]')?.addEventListener('change',event=>{
        if(busy||pending)return;
        selectedCircle=Number(event.target.value);host.dataset.scopeLoading='true';
        host.querySelectorAll('button,select').forEach(el=>el.disabled=true);void load(true);
      });
      const previousMeal=sameArrival?host.querySelector('[name=formalRotationMeal]:checked')?.value:null;
      const p=data.person;
      const empty=host.parentElement.querySelector('.action-center-empty');if(empty)empty.hidden=!!p;
      const personalEmpty=document.getElementById('rotationPersonalEmpty');
      if(personalEmpty){personalEmpty.hidden=!!p;personalEmpty.textContent='当前没有已发布的本人轮岗安排，请等待主管更新班表。';}
      if(!p){host.innerHTML=scopeHtml;bindScope();return;}
      if(data.readOnly){
        host.innerHTML=`${scopeHtml}<section class="panel rt-personal-task"><h3>我的轮岗</h3><div class="rt-personal-identity"><strong>${esc(p.name)}</strong><span>${personalShift(p)}</span></div><p role="status">旧版轮岗记录仅供查看。</p>${taskLine(data.line,p.pid)}</section>`;
        bindScope();return;
      }
      if(personalTaskPaused(p)){
        host.innerHTML=`${scopeHtml}<section class="panel rt-personal-task"><h3>关联任务待处理</h3><div class="rt-personal-identity"><strong>${esc(p.name)}</strong><span>${personalShift(p)}</span></div><p role="status">${p.timeoutPending?'等待前序完成后更新任务，暂不计确认超时':'同线关联任务待负责人核实，暂不计确认超时'}</p><p>旧详情暂停执行，请等待更新后的安排。</p>${personalTiming(p,data.clock.minute)}<a href="${portalPath('/rotation-board')}">当日看板</a></section>`;
        bindScope();return;
      }
      const a=personalAssignment(p,data),autoMorning=p.morningRest&&p.batchCheckedIn;
      const needsArrival=['walkback','pending','op'].includes(p.state);
      const action=p.canBatchCheckIn?'batch_check_in':needsArrival?'arrive':!autoMorning&&a&&((['assigned','forecast'].includes(p.task?.status)&&['rest','meal','ready'].includes(p.state))||(p.state==='ready'&&p.canDepart))?'depart':null;
      const waiting=p.scheduledRestStart!==null&&p.scheduledRestStart!==undefined;
      const status=p.timeoutCase?'同线关联任务待负责人核实，暂不计确认超时':p.timeoutPending?'等待前序完成后更新任务，暂不计确认超时':waiting?`班次未开始 · ${hm(p.scheduledRestStart)}开始休息`:['rest','meal'].includes(p.state)?`${p.state==='meal'?'吃饭':'休息'}至 ${hm(p.readyAt)} · 剩 ${Math.max(0,Math.ceil((p.readyAt??data.clock.minute)-data.clock.minute))} 分钟`:p.state==='heading'?`前往 ${esc(a?.line||'')} 线，预计${hm(p.arriveAt)}到岗`:p.state==='onpost'?`已确认安排 · ${esc(p.line)}线 · ${esc(p.post)}`:p.state==='ready'&&!action?'等待上岗时间':action==='arrive'?'待确认休息／吃饭':p.state==='done'?'当日轮岗已结束':p.state==='exception'?'上岗超时，等待大屏、主管、TR或经理人工安排':'请留意当日轮岗安排';
      const result=data.confirmations?.find(r=>!['reversed','superseded'].includes(r.status)
        &&(r.action!=='batch_check_in'||(p.upcomingShift&&p.batchCheckedIn)));
      const confirmation=personalConfirmation(p,action,data,pending,result);
      host.innerHTML=confirmation?scopeHtml+confirmation:`${scopeHtml}<section class="panel rt-personal-task"><div class="record-line"><h3>${action==='arrive'?'回休息室确认':action==='depart'?'上岗前确认':'我的轮岗'}</h3><a class="secondary" href="${portalPath('/rotation-board')}">当日看板</a></div><div class="rt-personal-identity"><strong>${esc(p.name)}</strong><span>${personalShift(p)}</span></div><p>${status}</p>${action==='arrive'?'<p class="rt-confirm-reminder">到达休息室后再确认，餐休从确认时开始计时。</p>':action==='depart'?'<p class="rt-confirm-reminder">看清本次任务，确认后前往指定岗位执行。</p>':''}${p.postHold?`<p role="status">持续定岗 · ${hm(p.postHold.since)}起 · ${esc(p.postHold.reason)}，手动取消或在当天轮岗结束时自动解除。</p>`:''}${a?`<p>安排：${esc(a.line)}线 · ${esc(a.entryPost||'入口岗位')}，${hm(a.departAt)}出发${a.targetName?' · 替换'+esc(a.targetName):''}</p>`:''}${a?`<p class="field-hint">${a.blocked?'安排失效，待主管核对':p.assignmentLocked?('本轮任务已派发'):'预计安排 · 整轮正式派发前可能调整'}；餐休结束后才可确认，以出发前最新任务为准。</p>`:''}${action==='arrive'&&p.mealOptions?.canChoose?'<fieldset><legend>本次选择</legend><label class="checkbox-row"><input type="radio" name="formalRotationMeal" value="rest" checked>休息</label><label class="checkbox-row"><input type="radio" name="formalRotationMeal" value="meal">吃饭</label></fieldset>':''}${action==='arrive'&&p.mealOptions?.forced?'<p>已到饭点，本次按原规则安排吃饭。</p>':''}${pending?'<p role="status">上次提交结果待核对，可刷新或使用原步骤重试。</p><button type="button" class="primary" data-rotation-retry>重试本次确认</button>':action?`<button type="button" class="primary" data-rotation-task="${action}">${action==='batch_check_in'?'已到':action==='depart'?'确认去上岗':'确认已到休息室'}</button>`:''}${result?`<p class="field-hint">最近安排生效：${hm(result.minute)} · ${personalReceipt(result)}</p>`:''}${taskLine(a||p.state==='onpost'?data.line:null,p.pid)}</section>`;
      if(state.me.rotation_temporary||data.attraction_id&&data.attraction_id!==state.me.attraction_id)host.querySelector('a[href]')?.remove();
      if(state.me.rotation_temporary&&data.notices?.some(n=>n.status==='open'))host.querySelector('.rt-personal-task').insertAdjacentHTML('beforeend',`<div role="status">${data.notices.filter(n=>n.status==='open').map(n=>`<p><strong>${esc(n.title)}</strong><br>${esc(n.body)}</p>`).join('')}</div>`);
      if(previousMeal==='meal'&&action==='arrive'&&p.mealOptions?.canChoose){const option=host.querySelector('[name=formalRotationMeal][value=meal]');if(option)option.checked=true;}
      host.querySelector('[data-rotation-task]')?.addEventListener('click',()=>submit(action));
      bindScope();
      const panel=host.querySelector('.rt-personal-task');
      if(p.morningRest)panel.insertAdjacentHTML('afterbegin',`<p><strong>07:30 上岗</strong> · ${autoMorning?'系统将在 07:27 安排岗位':'休息结束后请本人确认上岗'}</p>`);
      if(p.mealEligible&&!p.ate)panel.querySelector('.rt-personal-identity')?.insertAdjacentHTML('beforeend','<span class="rt-badge meal">未休饭</span>');
      if(action==='arrive'&&p.mealOptions?.reserveText)panel.querySelector('.rt-personal-identity')?.insertAdjacentHTML('afterend',`<p class="field-hint">${esc(p.mealOptions.reserveText)}。基础用餐条件及强制饭点沿用现有规则。</p>`);
      if(p.state==='onpost'&&p.preparing==='推下班')panel.querySelector('.rt-personal-identity')?.insertAdjacentHTML('beforeend','<span class="rt-badge prep">准备下班</span>');
      if(!confirmation)panel.insertAdjacentHTML('beforeend',personalTiming(p,data.clock.minute));
      if(p.departureBlocked)panel.insertAdjacentHTML('beforeend',`<p role="alert">${esc(p.departureBlocked)}</p>`);
      if(a&&p.departureRoutes?.length){
        const html=`<section class="rt-entry-group"><h4>到现场后，按这条路线接替</h4><div class="rt-confirm-routes">${p.departureRoutes.map(r=>`<div><strong>${esc(r.name)}</strong><span>${esc(r.from)} →</span><b>${esc(r.to)}</b></div>`).join('')}</div><p>前往指定岗位，按以上路线通知并接替。</p></section>`;
        if(p.task){const target=panel.querySelector('[data-rotation-task],button.primary,.rt-line');target?.insertAdjacentHTML('beforebegin',personalTaskComparison(p));panel.querySelector('.rt-line')?.remove();}
        else panel.insertAdjacentHTML('beforeend',html);
      }
      if(p.batchResult){
        const result=p.batchResult;
        const directShiftEnd=result.heading?.endsWith(' · 推下班');
        const shiftEnd=directShiftEnd||result.handoffChain?.some(step=>step.outcome==="done");
        const heading=directShiftEnd?result.heading.slice(0,-' · 推下班'.length):result.heading;
        const openings=(result.ownOpeningPosts||[]).map(taskOpeningBadge).join('');
        const special=(result.specialTasks||[]).map(label=>`<span class="rt-confirm-tag">${esc(label)}</span>`).join('');
        const summary=result.line?`<div class="rt-batch-result-heading"><strong>${esc(heading)}</strong>${shiftEnd?'<span class="rt-batch-shift-end">推下班</span>':''}${openings}${special}</div>`
          :`<strong>本批岗位已排满 · 休息至 ${taskTime(result.restUntil)}</strong>`;
        const instruction=taskInstruction(result);
        panel.insertAdjacentHTML('afterbegin',`<section class="rt-confirm-result" role="status"><h4>${result.reportAt!=null?hm(result.reportAt)+' 上岗安排':'批量上岗结果'}</h4>${summary}${instruction}${result.line?personalTaskComparison({task:{...result,status:'batch_result'}}):''}</section>`);
      }
      if(p.awaitingShiftEntry){
        panel.insertAdjacentHTML('afterbegin',p.canDepart?'<p>准备上班 · 已到班</p>':'<p>准备上班 · 已到班，等待安排</p>');
      }else if(p.upcomingShift){
        const preview=p.previewAssign;
        panel.insertAdjacentHTML('afterbegin',`<p>准备上班 · 距到班 ${Math.max(0,Math.ceil(p.start-data.clock.minute))} 分钟${preview?` · 预计 ${esc(preview.line)}线${preview.entryPost?` · ${esc(preview.entryPost)}`:''}`:''}</p>`);
      }
      host.querySelector('[data-rotation-retry]')?.addEventListener('click',()=>submit(pending.body.action,true));
    }catch(error){if(current())host.innerHTML=`<section class="panel"><p role="alert">轮岗待办暂未同步：${esc(error.message)}</p><button type="button" class="secondary" data-rotation-refresh>重新同步</button></section>`;host.querySelector('[data-rotation-refresh]')?.addEventListener('click',load);}
    finally{loading=false;if(queuedScope&&current()){queuedScope=false;void load(true);}}
  }
  async function submit(action,retry=false){
    if(busy||host.dataset.scopeLoading||data?.readOnly||!data?.person)return;
    if(!retry&&action==='depart'&&!data.person.canDepart){toast('餐休未结束或尚未到允许出发时间',true);return;}
    busy=true;
    const p=data.person,meal=host.querySelector('[name=formalRotationMeal]:checked')?.value||'rest';
    let completed=false;
    const body=retry?{...pending.body}:{action,pid:p.pid,confirmation_token:p.confirmationTokens?.[action],meal_choice:meal,...(data.attraction_id?{attraction_id:data.attraction_id}:{})};
    if(!retry&&p.task?.status==='forecast'&&action==='depart'){
      body.expected_line=p.assign?.line;
      body.expected_push7=p.task.push7Route??null;
      body.expected_opening=p.task.openingRoute??null;
      body.expected_kind=p.task.kind;
    }
    const date=retry?pending.date:data.clock.date;
    async function refreshConfirmation(){
      const latest=await api('/api/rotation-board/me'+(body.attraction_id?'?attraction_id='+body.attraction_id:''));
      const next=latest.person;
      if(!current()||latest.clock.date!==date||(body.attraction_id&&latest.attraction_id!==body.attraction_id)
          ||next?.pid!==body.pid||next.confirmationTokens?.[action]!==body.confirmation_token)return null;
      return next;
    }
    const watchConfirmation=(update=()=>{})=>({onOpen:(container,close)=>{
      let open=true, checking=false;
      const timer=setInterval(async()=>{
        if(!current()){clearInterval(timer);return;}
        if(checking||document.hidden)return;
        checking=true;
        try{
          const next=await refreshConfirmation();
          if(open&&current()){
            if(next&&!personalTaskPaused(next))update(container,next,close);
            else{toast('轮岗安排已更新，请查看当前状态');close();}
          }
        }catch{}finally{checking=false;}
      },5000);
      return ()=>{open=false;clearInterval(timer);};
    }});
    try{
      const a=p.assign;
      if(action==='depart'){
        const watch=p.task&&watchConfirmation((container,next)=>{
          const comparison=container.querySelector('[data-departure-comparison]');
          if(comparison){const html=singleTaskSummary(next.task)+personalTaskComparison(next);if(comparison.innerHTML!==html)comparison.innerHTML=html;return;}
          const list=container.querySelector('[data-departure-routes]');
          const html=entryRoutes(next.departureRoutes);
          if(list&&list.innerHTML!==html)list.innerHTML=html;
        });
        const preview=p.task?`<div data-departure-comparison>${singleTaskSummary(p.task)}${personalTaskComparison(p)}</div>`:`进入 ${esc(a?.line||'')}线 · ${esc(a?.entryPost||'入口岗位')}。${a?.targetName?'按原推岗规则替换 '+esc(a.targetName)+'。':''}<ul data-departure-routes aria-live="polite">${entryRoutes(p.departureRoutes)}</ul>`;
        if(!await confirmScope('确认去上岗',`${preview}<p>确认后按以上路线通知并接替。</p>`,watch||undefined))return;
      }
      let arriving=p, updated=false, out;
      while(current()){
        if(action==='arrive'){
          const message=()=>{
            const outcome=arriving.arrivalOutcome==='done'?'本次确认后结束当日轮岗。':arriving.arrivalOutcome==='away'?'本次确认后按原安排转为圈外状态。':arriving.mealOptions?.forced?'本次安排吃饭。':body.meal_choice==='meal'?'本次选择吃饭。':'本次选择休息。';
            return (updated?'到达后的安排已更新，请核对后确认。':'请确认本人已到达休息室。')+outcome;
          };
          if(!await confirmScope('确认已到休息室',`<p data-arrival-message aria-live="polite">${message()}</p>`,watchConfirmation((container,next,close)=>{
            if(body.meal_choice==='meal'&&!next.mealOptions?.canChoose&&!next.mealOptions?.forced){toast('餐休条件已更新，请重新选择休息或吃饭');close();return;}
            if((next.arrivalOutcome??null)===(arriving.arrivalOutcome??null)&&next.mealOptions?.forced===arriving.mealOptions?.forced)return;
            arriving=next;updated=true;
            container.querySelector('[data-arrival-message]').textContent=message();
          }))||!current())return;
          body.expected_arrival_outcome=arriving.arrivalOutcome??null;
        }
        save({date,body});
        try{
          out=await api('/api/rotation-board/me/act',json('POST',body));
          if(out.line_changed){
            save(null);
            const title=out.line===body.expected_line?'接替路线已改变，请重新确认':'线路已改变，请重新确认';
            if(!await confirmScope(title,personalLineChange(out,body.expected_line)+singleTaskSummary(out.task)+personalTaskComparison({task:out.task}),watchConfirmation()))return;
            body.expected_line=out.line;
            body.expected_push7=out.task?.push7Route??null;
            body.expected_opening=out.task?.openingRoute??null;
            body.expected_kind=out.task?.kind;
            continue;
          }
          break;
        }catch(error){
          if(action!=='arrive'||error.status!==409||!current())throw error;
          save(null);
          const next=await refreshConfirmation();
          if(!current())return;
          if(!next||(next.arrivalOutcome??null)===body.expected_arrival_outcome)throw error;
          arriving=next;
          updated=true;
        }
      }
      if(!out)return;
      save(null);completed=true;
      toast(action==='batch_check_in'?'已签到':out.duplicate?'本次已确认，无需重复操作':'轮岗已确认，正在同步看板');
    }catch(error){if(error.status&&error.status<500)save(null);toast(error.message||'提交结果待核对，请勿重复确认',true);}
    finally{busy=false;if(current()){await load();await refreshActionBadge();if(completed&&current())onComplete?.();}}
  }
  const timer=setInterval(()=>{if(!document.hidden)void load();},5000);
  const visible=()=>{if(!document.hidden)void load();};document.addEventListener('visibilitychange',visible);
  registerPageCleanup(()=>{closed=true;clearInterval(timer);document.removeEventListener('visibilitychange',visible);});
  void load();
}

export { renderRotationTest, mountRotationTasks };
