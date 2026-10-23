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
  location.href=portalPath('/rotation-board');
}

const hm=value=>`${String(Math.floor(value/60)%24).padStart(2,'0')}:${String(Math.floor(value%60)).padStart(2,'0')}`;
function entryRoutes(routes){return (routes||[]).map(r=>`<li><strong>${esc(r.name)}</strong>：${esc(r.from)} → ${esc(r.to)}</li>`).join('');}
function entryCard(p){
  const g=p.entryGroup;if(!g)return '';
  return `<section class="rt-entry-group"><h4>${esc(g.line)}线 · ${g.members.length}人协同上岗</h4><p role="status">${g.confirmed} / ${g.members.length} 人已确认到位</p><ul>${g.members.map(m=>`<li>${esc(m.name)} · ${hm(m.start)}–${hm(m.end)} · ${m.confirmed?'已确认':'待本人确认'}</li>`).join('')}</ul><p>全部本人确认后统一执行</p><ul>${entryRoutes(g.routes)}</ul>${g.blocked?`<p role="alert">${esc(g.blocked)}</p>`:''}</section>`;
}
function taskLine(line,pid){
  if(!line)return '';
  return `<div class="rt-line"><div class="rt-line-head"><strong>${esc(line.id)}线 · 岗位详情</strong></div>${line.posts.map(post=>`<div class="rt-post ${post.occ===pid?'me':''}"><span class="rt-pn">${esc(post.name)}</span><span class="rt-who-name">${esc(post.occName||'空岗')}</span><span class="rt-mins">${post.open?'':'未开岗'}</span></div>`).join('')}</div>`;
}

function mountRotationTasks(host,request,onComplete){
  if(!host||!state.me.rotation_entry)return;
  if(!document.getElementById('formalRotationStyles')){const css=document.createElement('link');css.id='formalRotationStyles';css.rel='stylesheet';css.href=portalPath('/static/css/rotation.css');document.head.appendChild(css);}
  let busy=false,loading=false,closed=false,data=null;
  const key='formalRotation.todo.'+state.me.id;
  let pending=null;try{pending=JSON.parse(sessionStorage.getItem(key)||'null');}catch{}
  const save=value=>{pending=value;try{if(value)sessionStorage.setItem(key,JSON.stringify(value));else sessionStorage.removeItem(key);}catch{}};
  const current=()=>!closed&&request.isCurrent()&&host.isConnected;
  async function load(){
    if(!current()||busy||loading||host.contains(document.activeElement)&&document.activeElement.tagName==='INPUT')return;
    loading=true;
    try{
      const next=await api('/api/rotation-board/me');if(!current())return;data=next;
      if(pending){
        if(pending.date!==data.clock.date){save(null);toast('此前轮岗确认已跨日，请联系主管核对当日记录。',true);}
        else{const result=await api('/api/rotation-board/confirmation?date='+encodeURIComponent(pending.date)+'&token='+encodeURIComponent(pending.body.confirmation_token));if(result.confirmed){save(null);if(onComplete){toast(result.result?.status==='waiting'?'已核实本人到位确认，仍在等待同行人':result.result?.status==='superseded'?'此前同行安排已调整，请查看最新任务':'已核实轮岗确认成功');onComplete();return;}}}
      }
      if(!current())return;
      const p=data.person;
      const empty=host.parentElement.querySelector('.action-center-empty');if(empty)empty.hidden=!!p;
      if(!p){host.innerHTML='';return;}
      const g=p.entryGroup,voted=g?.members.find(m=>m.pid===p.pid)?.confirmed;
      const a=p.assign,action=p.entryHold?'entry_return':['walkback','pending'].includes(p.state)?'arrive':p.state==='ready'&&p.canDepart&&!voted&&!g?.blocked?'depart':null;
      const waiting=p.scheduledRestStart!==null&&p.scheduledRestStart!==undefined;
      const status=waiting?`班次未开始 · ${hm(p.scheduledRestStart)}开始休息`:['rest','meal'].includes(p.state)?`${p.state==='meal'?'吃饭':'休息'}至 ${hm(p.readyAt)}`:p.state==='heading'?`前往 ${esc(a?.line||'')} 线，预计${hm(p.arriveAt)}到岗`:p.state==='onpost'?`在 ${esc(p.line)} 线 · ${esc(p.post)}`:p.state==='ready'&&!action?'等待上岗时间':action==='arrive'?'待确认休息／吃饭':p.state==='done'?'当日轮岗已结束':'请留意当日轮岗安排';
      const result=data.confirmations?.[0];
      host.innerHTML=`<section class="panel"><div class="record-line"><h3>我的轮岗</h3><a class="secondary" href="${portalPath('/rotation-board')}">当日看板</a></div><p>${status}</p>${a?`<p>安排：${esc(a.line)}线 · ${esc(a.entryPost||'入口岗位')}，${hm(a.departAt)}出发${a.targetName?' · 替换'+esc(a.targetName):''}</p>`:''}${action==='arrive'&&p.mealOptions?.canChoose?'<fieldset><legend>本次选择</legend><label class="checkbox-row"><input type="radio" name="formalRotationMeal" value="rest" checked>休息</label><label class="checkbox-row"><input type="radio" name="formalRotationMeal" value="meal">吃饭</label></fieldset>':''}${action==='arrive'&&p.mealOptions?.forced?'<p>已到饭点，本次按原规则安排吃饭。</p>':''}${pending?'<p role="status">上次提交结果待核对，可刷新或使用原步骤重试。</p><button type="button" class="primary" data-rotation-retry>重试本次确认</button>':action?`<button type="button" class="primary" data-rotation-task="${action}">${action==='depart'?'确认去上岗':'确认已到休息室'}</button>`:''}${result?`<p class="field-hint">最近确认：${hm(result.minute)} · ${result.action==='depart'?esc(result.line)+'线 · '+esc(result.post):'已到休息室'}</p>`:''}${taskLine(data.line,p.pid)}</section>`;
      host.querySelector('[data-rotation-task]')?.addEventListener('click',()=>submit(action));
      const panel=host.querySelector('.panel');
      if(g){panel.insertAdjacentHTML('beforeend',entryCard(p));if(p.canDepart&&!g.soloBlocked)panel.insertAdjacentHTML('beforeend','<button type="button" class="secondary" data-entry-single>同行人未到场，调整为单人上岗</button>');}
      if(p.entryHold)panel.insertAdjacentHTML('afterbegin','<p role="status">本次同行任务已调整，请本人到场后确认，重新领取上岗安排。</p>');
      if(p.upcomingShift)panel.insertAdjacentHTML('afterbegin',`<p>即将上班 · ${hm(p.start)}–${hm(p.end)}${p.previewAssign?` · 预计进入 ${esc(p.previewAssign.line)}线 · ${esc(p.previewAssign.entryPost)}（上岗时再次核对）`:''}</p>${p.previewAssign?.group?`<ul>${entryRoutes(p.previewAssign.group.routes)}</ul>`:''}`);
      if(action==='entry_return')host.querySelector('[data-rotation-task]').textContent='本人已到场，重新领取安排';
      if(g&&action==='depart')host.querySelector('[data-rotation-task]').textContent='确认本人已到位';
      if(g){
        const groupCard=panel.querySelector('.rt-entry-group'),primary=panel.querySelector('[data-rotation-task]');
        const lineCard=panel.querySelector('.rt-line');
        if(groupCard)panel.insertBefore(groupCard,primary||lineCard);
        const singleButton=panel.querySelector('[data-entry-single]');
        if(singleButton&&lineCard)panel.insertBefore(singleButton,lineCard);
      }
      if(result?.status==='waiting'){const hint=host.querySelector('.field-hint');if(hint)hint.textContent='已登记本人到位，等待同行人确认；尚未改变实际岗位。';}
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
    finally{loading=false;}
  }
  async function regroup(){
    if(busy||!data?.person?.entryGroup)return;
    busy=true;let completed=false;
    const p=data.person,peers=p.entryGroup.members.filter(m=>m.pid!==p.pid);let missing=[];
    try{
      const chosen=await formModal('选择暂未到场的同行人','只调整本次任务，不记为缺勤。',peers.map(m=>`<label class="checkbox-row"><input type="checkbox" name="entryMissing" value="${esc(m.pid)}" ${m.confirmed?'disabled':''}>${esc(m.name)}${m.confirmed?' · 已确认到位':''}</label>`).join(''),'预览新组合',{onSubmit:form=>{missing=[...form.querySelectorAll('[name=entryMissing]:checked')].map(el=>el.value);if(!missing.length)throw new Error('请先选择暂未到场的同行人');}});
      if(!chosen||!current())return;
      if(missing.length===peers.length){busy=false;await submit('depart',false,true);return;}
      const token=p.confirmationTokens.entry_adjust;
      const preview=await api('/api/rotation-board/me/entry-preview',json('POST',{confirmation_token:token,missing}));
      if(!current()||!await confirmModal('确认新的同行组合',`由 ${preview.members.map(m=>esc(m.name)).join('、')} 重新组合，优先接替原下班／出圈任务：<ul>${entryRoutes(preview.routes)}</ul>本次只调整安排，剩余人员需要重新各自确认上岗。`))return;
      const body={action:'entry_adjust',pid:p.pid,confirmation_token:token,missing,expected_plan:preview.id};
      save({date:data.clock.date,body});await api('/api/rotation-board/me/act',json('POST',body));save(null);completed=true;toast('组合已调整，请剩余同行人重新确认');
    }catch(error){if(error.status&&error.status<500)save(null);toast(error.message||'提交结果待核对',true);}
    finally{busy=false;if(current()){await load();await refreshActionBadge();if(completed&&current())onComplete?.();}}
  }
  async function submit(action,retry=false,single=false){
    if(busy||!data?.person)return;
    busy=true;
    const p=data.person,meal=host.querySelector('[name=formalRotationMeal]:checked')?.value||'rest';
    let completed=false;
    const body=retry?pending.body:{action,pid:p.pid,confirmation_token:p.confirmationTokens?.[action],meal_choice:meal};
    if(single){body.single=true;body.missing=p.entryGroup.members.filter(m=>m.pid!==p.pid).map(m=>m.pid);}
    try{
      const a=p.assign;
      if(single&&!await confirmModal('确认其他同行人未到场',`请确认 ${p.entryGroup.members.filter(m=>m.pid!==p.pid).map(m=>esc(m.name)).join('、')} 暂未到场。单人安排将优先接替下班／出圈任务：<ul>${entryRoutes(p.entryGroup?.soloRoutes)}</ul>请核对以上新路径。同行人已确认到位时不能使用此模式。`))return;
      if(action==='entry_adjust'){if(!await confirmModal('重试同行组合调整','沿用上次提交的组合预览；仅调整任务，剩余人员仍需重新确认上岗。'))return;}
      else if(action==='entry_return'){if(!await confirmModal('确认本人已到场','重新领取最新安排，本次操作不会直接上岗。'))return;}
      else if(p.entryGroup&&action==='depart'){if(!single&&!await confirmModal('确认本人已到位',`${entryCard(p)}确认后等待同行人，全部到位才统一执行。`))return;}
      else
      if(!await confirmModal(action==='depart'?'确认去上岗':'确认已到休息室',action==='depart'?`进入 ${esc(a?.line||'')}线 · ${esc(a?.entryPost||'入口岗位')}。${a?.targetName?'按原推岗规则替换 '+esc(a.targetName)+'。':''}`:`请确认本人已到达休息室。${p.mealOptions?.forced?'本次安排吃饭。':meal==='meal'?'本次选择吃饭。':'本次选择休息。'}`))return;
      if(!current())return;
      save({date:data.clock.date,body});
      const out=await api('/api/rotation-board/me/act',json('POST',body));save(null);completed=true;
      toast(out.result?.status==='waiting'?'本人已确认，等待同行人到位':out.duplicate?'本次已确认，无需重复操作':'轮岗已确认，正在同步看板');
    }catch(error){if(error.status&&error.status<500)save(null);toast(error.message||'提交结果待核对，请勿重复确认',true);}
    finally{busy=false;if(current()){await load();await refreshActionBadge();if(completed&&current())onComplete?.();}}
  }
  const timer=setInterval(()=>{if(!document.hidden)void load();},5000);
  const visible=()=>{if(!document.hidden)void load();};document.addEventListener('visibilitychange',visible);
  registerPageCleanup(()=>{closed=true;clearInterval(timer);document.removeEventListener('visibilitychange',visible);});
  void load();
}

export { renderRotationTest, mountRotationTasks };
