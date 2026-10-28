// Formal employee rotation entry and personal action-center tasks.
import { api, json } from './api.js';
import { beginViewRequest, registerPageCleanup } from './context.js';
import { confirmModal, formModal } from './dialogs.js';
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

const hm=value=>`${String(Math.floor(value/60)%24).padStart(2,'0')}:${String(Math.floor(value%60)).padStart(2,'0')}`;
function entryRoutes(routes){return (routes||[]).map(r=>`<li><strong>${esc(r.name)}</strong>：${esc(r.from)} → ${esc(r.to)}</li>`).join('');}
function entryCard(p){
  const g=p.entryGroup;if(!g)return '';
  return `<section class="rt-entry-group"><h4>${esc(g.line)}线 · ${g.members.length}人协同上岗</h4><p role="status">${g.confirmed} / ${g.members.length} 人已确认安排</p><ul>${g.members.map(m=>`<li>${esc(m.name)} · ${esc(m.task||'上岗')} → ${esc(m.entryPost||'待核实岗位')} · ${hm(m.start)}–${hm(m.end)} · ${m.confirmed?'已确认':'待本人确认'}</li>`).join('')}</ul><p>出发前全员确认后一起前往，现场无需再次操作手机</p><h4>现场通知与接替去向</h4><p>向岗上伙伴说明本组来几人、各自任务，并按以下路线接替。下线人员回休息室后确认休息／吃饭。</p><ul>${entryRoutes(g.routes)}</ul>${g.blocked?`<p role="alert">${esc(g.blocked)}</p>`:''}</section>`;
}
function taskLine(line,pid){
  if(!line)return '';
  return `<div class="rt-line"><div class="rt-line-head"><strong>${esc(line.id)}线 · 岗位详情</strong></div>${line.posts.map(post=>`<div class="rt-post ${post.occ===pid?'me':''}"><span class="rt-pn">${esc(post.name)}</span><span class="rt-who-name">${esc(post.occName||'空岗')}${post.hold?'<span class="rt-badge post-hold">定岗</span>':''}</span><span class="rt-mins">${post.open?'':'未开岗'}</span></div>`).join('')}</div>`;
}

function personalTaskComparison(p){
  if(!p.task)return '';
  const task=p.task,table=(posts,after)=>`<div class="rt-task-line"><header>${esc(task.line)}</header>${posts.map((post,i)=>`<div class="rt-task-slot ${after&&post.occ!==task.beforePosts[i]?.occ?'changed':''}"><span>${esc(post.name)}</span><strong>${esc(post.occName||'空岗')}</strong></div>`).join('')}</div>`;
  return `<section class="rt-task-comparison"><h4>执行本任务前后</h4><div class="rt-task-columns"><div><p>执行前 · 当前站位</p>${table(task.beforePosts,false)}</div><div><p>执行后 · 预计站位</p>${table(task.afterPosts,true)}</div></div><small>只标出本任务变化。${hm(task.allowedAt)} 可出发，${hm(task.deadlineAt)} 前确认。</small>${task.transferredFromName?`<p>转接自 ${esc(task.transferredFromName)}</p>`:''}</section>`;
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
  const confirmScope=(title,message)=>confirmModal(title,`<p>当前景点圈：${esc(data?.identity?.attraction_name||state.me.attraction_name||'')}</p>${message}`);
  async function load(force=false){
    if(loading){if(force)queuedScope=true;return;}
    if(!current()||busy||!force&&host.contains(document.activeElement)&&['INPUT','SELECT'].includes(document.activeElement.tagName))return;
    loading=true;
    try{
      const requestedCircle=selectedCircle;
      const next=await api('/api/rotation-board/me'+(requestedCircle?'?attraction_id='+requestedCircle:''));if(!current()||requestedCircle!==selectedCircle)return;data=next;selectedCircle=next.attraction_id||selectedCircle;
      if(pending){
        if(pending.date!==data.clock.date){save(null);toast('此前轮岗确认已跨日，请联系主管核对当日记录。',true);}
        else{const result=await api('/api/rotation-board/confirmation?date='+encodeURIComponent(pending.date)+'&token='+encodeURIComponent(pending.body.confirmation_token)+(pending.body.attraction_id?'&attraction_id='+pending.body.attraction_id:''));if(result.confirmed){save(null);if(onComplete){toast(result.result?.status==='waiting'?'已核实本人安排确认，仍在等待同行人':result.result?.status==='superseded'?'此前同行安排已调整，请查看最新任务':'已核实轮岗确认成功');onComplete();return;}}}
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
      const p=data.person;
      const empty=host.parentElement.querySelector('.action-center-empty');if(empty)empty.hidden=!!p;
      const personalEmpty=document.getElementById('rotationPersonalEmpty');
      if(personalEmpty){personalEmpty.hidden=!!p;personalEmpty.textContent='当前没有已发布的本人轮岗安排，请等待主管更新班表。';}
      if(!p){host.innerHTML=scopeHtml;bindScope();return;}
      const g=p.entryGroup,voted=g?.members.find(m=>m.pid===p.pid)?.confirmed;
      const a=p.assign,action=p.entryHold?'entry_return':(['walkback','pending'].includes(p.state)||(p.role==='op'&&['op','notyet'].includes(p.state)))?'arrive':p.state==='ready'&&p.canDepart&&!voted&&!g?.blocked?'depart':null;
      const waiting=p.scheduledRestStart!==null&&p.scheduledRestStart!==undefined;
      const status=waiting?`班次未开始 · ${hm(p.scheduledRestStart)}开始休息`:['rest','meal'].includes(p.state)?`${p.state==='meal'?'吃饭':'休息'}至 ${hm(p.readyAt)}`:p.state==='heading'?`前往 ${esc(a?.line||'')} 线，预计${hm(p.arriveAt)}到岗`:p.state==='onpost'?`在 ${esc(p.line)} 线 · ${esc(p.post)}`:p.state==='ready'&&!action?'等待上岗时间':action==='arrive'?'待确认休息／吃饭':p.state==='done'?'当日轮岗已结束':'请留意当日轮岗安排';
      const result=data.confirmations?.find(r=>!['reversed','superseded'].includes(r.status));
      host.innerHTML=`${scopeHtml}<section class="panel rt-personal-task"><div class="record-line"><h3>我的轮岗</h3><a class="secondary" href="${portalPath('/rotation-board')}">当日看板</a></div><p>${status}</p>${p.postHold?`<p role="status">持续定岗 · ${hm(p.postHold.since)}起 · ${esc(p.postHold.reason)}，手动取消或零点结束。</p>`:''}${a?`<p>安排：${esc(a.line)}线 · ${esc(a.entryPost||'入口岗位')}，${hm(a.departAt)}出发${a.targetName?' · 替换'+esc(a.targetName):''}</p>`:''}${a?`<p class="field-hint">${a.blocked?'安排失效，待主管核对':p.assignmentLocked?(p.state!=='ready'&&p.readyAt-data.clock.minute>3?'同行组合已固定':'安排已固定'):'预计安排 · 餐休剩余超过3分钟可调整'}；餐休结束后才可确认，以出发前最新任务为准。</p>`:''}${action==='arrive'&&p.mealOptions?.canChoose?'<fieldset><legend>本次选择</legend><label class="checkbox-row"><input type="radio" name="formalRotationMeal" value="rest" checked>休息</label><label class="checkbox-row"><input type="radio" name="formalRotationMeal" value="meal">吃饭</label></fieldset>':''}${action==='arrive'&&p.mealOptions?.forced?'<p>已到饭点，本次按原规则安排吃饭。</p>':''}${pending?'<p role="status">上次提交结果待核对，可刷新或使用原步骤重试。</p><button type="button" class="primary" data-rotation-retry>重试本次确认</button>':action?`<button type="button" class="primary" data-rotation-task="${action}">${action==='depart'?'确认去上岗':'确认已到休息室'}</button>`:''}${result?`<p class="field-hint">最近安排生效：${hm(result.minute)} · ${result.action==='depart'?esc(result.line)+'线 · '+esc(result.post):'已到休息室'}</p>`:''}${taskLine(data.line,p.pid)}</section>`;
      if(state.me.rotation_temporary||data.attraction_id&&data.attraction_id!==state.me.attraction_id)host.querySelector('a[href]')?.remove();
      if(state.me.rotation_temporary&&data.notices?.some(n=>n.status==='open'))host.querySelector('.rt-personal-task').insertAdjacentHTML('beforeend',`<div role="status">${data.notices.filter(n=>n.status==='open').map(n=>`<p><strong>${esc(n.title)}</strong><br>${esc(n.body)}</p>`).join('')}</div>`);
      host.querySelector('[data-rotation-task]')?.addEventListener('click',()=>submit(action));
      bindScope();
      const panel=host.querySelector('.rt-personal-task');
      if(p.departureBlocked)panel.insertAdjacentHTML('beforeend',`<p role="alert">${esc(p.departureBlocked)}</p>`);
      if(!g&&p.departureRoutes?.length){
        const html=`<section class="rt-entry-group"><h4>现场通知与接替去向</h4><ul>${entryRoutes(p.departureRoutes)}</ul><p>前往指定岗位，按以上路线通知并接替。</p></section>`;
        if(p.task){const target=panel.querySelector('[data-rotation-task],.rt-line');target?.insertAdjacentHTML('beforebegin',html+personalTaskComparison(p));panel.querySelector('.rt-line')?.remove();}
        else panel.insertAdjacentHTML('beforeend',html);
      }
      if(g){panel.insertAdjacentHTML('beforeend',entryCard(p));if(p.canDepart&&!g.soloBlocked)panel.insertAdjacentHTML('beforeend','<button type="button" class="secondary" data-entry-single>同行人未到场，调整为单人上岗</button>');}
      if(p.entryHold)panel.insertAdjacentHTML('afterbegin',`<p role="status">${p.taskTimeout?esc(p.taskTimeout.line)+' · '+esc(p.taskTimeout.entryPost)+'任务超时，'+(p.taskTimeout.transferredToName?'已转由 '+esc(p.taskTimeout.transferredToName)+' 执行':'正在等待转接')+'；确认知晓后重新领取。':'本次同行任务已调整，请本人到场后确认，重新领取上岗安排。'}</p>`);
      if(p.upcomingShift)panel.insertAdjacentHTML('afterbegin',`<p>即将上班 · ${hm(p.start)}–${hm(p.end)}${p.previewAssign?` · 预计进入 ${esc(p.previewAssign.line)}线 · ${esc(p.previewAssign.entryPost)}（上岗时再次核对）`:''}</p>${p.previewAssign?.group?`<ul>${entryRoutes(p.previewAssign.group.routes)}</ul>`:''}`);
      if(action==='entry_return')host.querySelector('[data-rotation-task]').textContent=p.taskTimeout?'确认知晓，重新领取':'本人已到场，重新领取安排';
      if(g&&action==='depart')host.querySelector('[data-rotation-task]').textContent='确认上岗安排';
      if(g){
        const groupCard=panel.querySelector('.rt-entry-group'),primary=panel.querySelector('[data-rotation-task]');
        const lineCard=panel.querySelector('.rt-line');
        if(groupCard)panel.insertBefore(groupCard,primary||lineCard);
        const singleButton=panel.querySelector('[data-entry-single]');
        if(singleButton&&lineCard)panel.insertBefore(singleButton,lineCard);
      }
      if(result?.status==='waiting'){const hint=host.querySelector('.field-hint');if(hint)hint.textContent='已接受安排，请先等待同行人确认齐再一起出发；尚未更新岗位。';}
      if(result?.status==='superseded'){const hint=host.querySelector('.field-hint');if(hint)hint.textContent='此前同行安排已调整，旧确认不再执行，请查看最新任务。';}
      if(result?.action==='entry_return'){const hint=host.querySelector('.field-hint');if(hint)hint.textContent='已确认本人到场，已重新领取最新安排。';}
      host.querySelector('[data-entry-single]')?.addEventListener('click',()=>submit('depart',false,true));
      if(g?.members.length>2&&p.canDepart){
        const single=host.querySelector('[data-entry-single]');if(single)single.textContent='其他同行人均未到场，改为单人';
        const button=document.createElement('button');button.type='button';button.className='secondary';button.dataset.entryAdjust='';button.textContent='部分同行人未到场，重新组合';
        panel.insertBefore(button,panel.querySelector('.rt-line'));button.addEventListener('click',regroup);
      }
      if(result?.status==='revised'){const hint=host.querySelector('.field-hint');if(hint)hint.textContent='同行组合已调整，尚未上岗；剩余人员需重新各自确认。';}
      host.querySelector('[data-rotation-retry]')?.addEventListener('click',()=>submit(pending.body.action,true));
    }catch(error){if(current())host.innerHTML=`<section class="panel"><p role="alert">轮岗待办暂未同步：${esc(error.message)}</p><button type="button" class="secondary" data-rotation-refresh>重新同步</button></section>`;host.querySelector('[data-rotation-refresh]')?.addEventListener('click',load);}
    finally{loading=false;if(queuedScope&&current()){queuedScope=false;void load(true);}}
  }
  async function regroup(){
    if(busy||host.dataset.scopeLoading||!data?.person?.entryGroup)return;
    busy=true;let completed=false;
    const p=data.person,peers=p.entryGroup.members.filter(m=>m.pid!==p.pid);let missing=[];
    try{
      const chosen=await formModal('选择暂未到场的同行人','只调整本次任务，不记为缺勤。',peers.map(m=>`<label class="checkbox-row"><input type="checkbox" name="entryMissing" value="${esc(m.pid)}" ${m.confirmed?'disabled':''}>${esc(m.name)}${m.confirmed?' · 已确认安排':''}</label>`).join(''),'预览新组合',{onSubmit:form=>{missing=[...form.querySelectorAll('[name=entryMissing]:checked')].map(el=>el.value);if(!missing.length)throw new Error('请先选择暂未到场的同行人');}});
      if(!chosen||!current())return;
      if(missing.length===peers.length){busy=false;await submit('depart',false,true);return;}
      const token=p.confirmationTokens.entry_adjust;
      const preview=await api('/api/rotation-board/me/entry-preview',json('POST',{confirmation_token:token,missing,...(data.attraction_id?{attraction_id:data.attraction_id}:{})}));
      if(!current()||!await confirmScope('确认新的同行组合',`由 ${preview.members.map(m=>esc(m.name)).join('、')} 重新组合，优先接替原下班／出圈任务：<ul>${entryRoutes(preview.routes)}</ul>本次只调整安排，剩余人员需要重新各自确认上岗。`))return;
      const body={action:'entry_adjust',pid:p.pid,confirmation_token:token,missing,expected_plan:preview.id,...(data.attraction_id?{attraction_id:data.attraction_id}:{})};
      save({date:data.clock.date,body});await api('/api/rotation-board/me/act',json('POST',body));save(null);completed=true;toast('组合已调整，请剩余同行人重新确认');
    }catch(error){if(error.status&&error.status<500)save(null);toast(error.message||'提交结果待核对',true);}
    finally{busy=false;if(current()){await load();await refreshActionBadge();if(completed&&current())onComplete?.();}}
  }
  async function submit(action,retry=false,single=false){
    if(busy||host.dataset.scopeLoading||!data?.person)return;
    busy=true;
    const p=data.person,meal=host.querySelector('[name=formalRotationMeal]:checked')?.value||'rest';
    let completed=false;
    const body=retry?pending.body:{action,pid:p.pid,confirmation_token:p.confirmationTokens?.[action],meal_choice:meal,...(data.attraction_id?{attraction_id:data.attraction_id}:{})};
    if(single){body.single=true;body.missing=p.entryGroup.members.filter(m=>m.pid!==p.pid).map(m=>m.pid);}
    try{
      const a=p.assign;
      if(single&&!await confirmScope('确认其他同行人未到场',`请确认 ${p.entryGroup.members.filter(m=>m.pid!==p.pid).map(m=>esc(m.name)).join('、')} 暂未到场。单人安排将优先接替下班／出圈任务：<ul>${entryRoutes(p.entryGroup?.soloRoutes)}</ul>请核对以上新路径。同行人已确认安排时不能使用此模式。`))return;
      if(action==='entry_adjust'){if(!await confirmScope('重试同行组合调整','沿用上次提交的组合预览；仅调整任务，剩余人员仍需重新确认上岗。'))return;}
      else if(action==='entry_return'){if(!await confirmScope('确认本人已到场','重新领取最新安排，本次操作不会直接上岗。'))return;}
      else if(p.entryGroup&&action==='depart'){if(!single&&!await confirmScope('确认上岗安排',`${entryCard(p)}确认后请在出发前等待同行人，全员确认后一起前往，现场无需再次操作手机。`))return;}
      else
      if(!await confirmScope(action==='depart'?'确认去上岗':'确认已到休息室',action==='depart'?`进入 ${esc(a?.line||'')}线 · ${esc(a?.entryPost||'入口岗位')}。${a?.targetName?'按原推岗规则替换 '+esc(a.targetName)+'。':''}<ul>${entryRoutes(p.departureRoutes)}</ul>确认表示接受安排并前往执行，现场无需再次操作手机。下线人员回休息室后确认休息／吃饭。`:`请确认本人已到达休息室。${p.mealOptions?.forced?'本次安排吃饭。':meal==='meal'?'本次选择吃饭。':'本次选择休息。'}`))return;
      if(!current())return;
      save({date:data.clock.date,body});
      const out=await api('/api/rotation-board/me/act',json('POST',body));save(null);completed=true;
      toast(out.result?.status==='waiting'?'本人已确认，等待同行人确认安排':out.duplicate?'本次已确认，无需重复操作':'轮岗已确认，正在同步看板');
    }catch(error){if(error.status&&error.status<500)save(null);toast(error.message||'提交结果待核对，请勿重复确认',true);}
    finally{busy=false;if(current()){await load();await refreshActionBadge();if(completed&&current())onComplete?.();}}
  }
  const timer=setInterval(()=>{if(!document.hidden)void load();},5000);
  const visible=()=>{if(!document.hidden)void load();};document.addEventListener('visibilitychange',visible);
  registerPageCleanup(()=>{closed=true;clearInterval(timer);document.removeEventListener('visibilitychange',visible);});
  void load();
}

export { renderRotationTest, mountRotationTasks };
