'use strict';
// Shared real-data presentation; screen devices never receive management controls.
const workflowExpandedShifts = new Set();
function workflowShiftGroups(list,chip,day,view){
  const groups=new Map();
  for(const p of list){const key=JSON.stringify([p.start,p.end]);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(p);}
  return `<div class="rt-shift-groups">${[...groups.values()].sort((a,b)=>a[0].start-b[0].start||a[0].end-b[0].end).map(people=>{
    const p=people[0],key=JSON.stringify([RT.data.attraction_name||'',day.date,view.daily?'daily':'live',p.start,p.end]);
    return `<details class="rt-shift-group" data-workflow-shift="${esc(key)}" ${workflowExpandedShifts.has(key)?'open':''}><summary><span>${hm(p.start)}–${hm(p.end)}</span><span class="rt-shift-count">${people.length} 人</span></summary><div class="rt-chips">${people.map(chip).join('')}</div></details>`;
  }).join('')}</div>`;
}
function shortRotationShift(p){
  const part=value=>{const h=Math.floor(value/60)%24,m=Math.floor(value%60);return `${pad2(h)}:${pad2(m)}`;};
  return part(p.end);
}
function rotationName(p){return `<span class="rt-name-row"><strong>${esc(p.name)}</strong><small>${shortRotationShift(p)}</small></span>`;}
function workflowTaskSummary(p){
  if(!p.task)return p.assign?assignmentPlanText(p):'去向安排中';
  const rows=p.task.routes.slice(1);
  if(rows.length>2){const last=rows.at(-1);return `顺推 ${rows.length-1} 岗；${esc(last.name)} → ${esc(last.to.replace(/下岗.*休息.*$/,'下岗休息'))}`;}
  return rows.slice(0,2).map(r=>`${esc(r.name)} → ${esc(r.to.replace(/下岗.*休息.*$/,'下岗休息'))}`).join('；')||'按任务接替';
}
function workflowComparison(p){
  if(!p.task)return '';
  const t=p.task;
  const table=(posts,after)=>`<div class="rt-task-line"><header>${esc(t.line)}</header>${posts.map((x,i)=>`<div class="rt-task-slot ${after&&x.occ!==t.beforePosts[i]?.occ?'changed':''}"><span>${esc(x.name)}</span><strong>${esc(x.occName||'空岗')}</strong></div>`).join('')}</div>`;
  return `<section class="rt-task-comparison"><h4>执行本任务前后</h4><div class="rt-task-columns"><div><p>执行前 · 当前站位</p>${table(t.beforePosts,false)}</div><div><p>执行后 · 预计站位</p>${table(t.afterPosts,true)}</div></div><small>蓝色仅标出本任务变化，不代表其他任务已经执行。</small></section>`;
}
function workflowPools(n,ops,view={}){
  const day=view.day||RT.data.day,posted=new Set(day.lines.flatMap(l=>l.posts.map(x=>x.occ).filter(Boolean))),allPeople=day.persons.filter(p=>!posted.has(p.pid)),ps=allPeople.filter(p=>view.daily||!p.timeoutCase&&!p.timeoutPending),seen=new Set();
  const section=(title,list,chip,cls='')=>{list.forEach(p=>seen.add(p.pid));const contents=cls==='rt-others'&&list.length?workflowShiftGroups(list,chip,day,view):`<div class="rt-chips${list.length?'':' empty'}">${list.length?list.map(chip).join(''):'暂无'}</div>`;return `<section class="rt-sect ${cls}${list.length?'':' is-empty'}"><h2>${title}<span class="rt-n">${list.length}</span></h2>${contents}</section>`;};
  const drag=p=>ops&&['rest','meal','ready','notyet'].includes(p.state)?` draggable="true" data-drag-pid="${esc(p.pid)}"`:'';
  const card=(p,action,body,cls='')=>{
    if(view.daily){
      const can=view.editable&&p.role==='rotation';
      return `<${can?'button type="button" draggable="true" data-daily-person="person:'+esc(p.pid)+'"':'div'} class="rt-chip ${cls}">${rotationName(p)}${body}</${can?'button':'div'}>`;
    }
    if(!ops&&!document.body.classList.contains('rotation-screen'))action=null;
    const tag=action?'button':'div',can=action==='depart'?canDepartNow(p,n):action==='arrive'?p.state!=='notyet'||n>=p.start:true;
    return `<${tag}${action?' type="button"':''} class="rt-chip ${cls}"${action?` data-wf-pid="${esc(p.pid)}" data-wf-action="${action}" ${can&&!confirmationBlocked()?'':'aria-disabled="true"'}`:''}${drag(p)}>${rotationName(p)}${body}${badges(p)}</${tag}>`;
  };
  let html='';
  if(!view.daily){const paused=allPeople.filter(p=>p.timeoutCase||p.timeoutPending);if(paused.length)html+=section('关联任务待处理',paused,p=>card(p,null,`<span class="rt-go">${esc(p.timeoutCase?.line||p.timeoutPending?.line||'')} · ${p.timeoutCase?'待负责人核实':'等待前序完成'}</span><span class="rt-sub">旧详情暂停执行 · 暂不计确认超时</span>`,'late'),'rt-related-paused');}
  if(view.daily){
    const initial=p=>p.role==='rotation'&&p.state==='notyet'&&[420,435].includes(p.start);
    const pending=p=>['walkback','pending','op'].includes(p.state)||p.role==='op'&&p.state==='notyet';
    const chip=p=>card(p,null,`<span class="rt-sub">${p.role==='op'?'OP':p.role==='excluded'?'不轮岗':p.state==='notyet'?(view.frozen?'开班预排':'班次未开始'):esc(STATE_NAME[p.state]||'待安排')}</span>`);
    html+=section('待出发',ps.filter(p=>p.state==='ready'),chip,'rt-ready');
    html+=section('待确定休息',ps.filter(pending),chip,'rt-arrivals');
    html+=section('吃饭区',ps.filter(p=>p.state==='meal'),chip,'rt-meals');
    html+=section('休息区',ps.filter(p=>p.state==='rest'||initial(p)),chip,'rt-resting');
    html+=section('圈外人员',ps.filter(p=>p.state==='away'),chip,'rt-outside');
    html+=section('其他人员',ps.filter(p=>!seen.has(p.pid)),chip,'rt-others');
    return html;
  }
  html+=section('待出发',ps.filter(p=>!p.entryHold&&poolState(p,n)==='ready'),p=>card(p,p.assign?'depart':null,`${p.assign?`<span class="rt-go">${goText(p)}</span>`:''}<span class="rt-task-summary">${workflowTaskSummary(p)}</span><span class="rt-sub">${p.task?`${hm(p.task.allowedAt)} 可出发 · ${hm(p.task.deadlineAt)} 前确认`:p.assign?hm(p.assign.departAt)+' 出发':'等待安排'}</span>${p.task&&n<p.task.allowedAt?`<span class="rt-sub">餐休剩 ${Math.max(0,Math.ceil((p.readyAt??n)-n))} 分钟 · 距允许出发 ${Math.max(0,Math.ceil(p.task.allowedAt-n))} 分钟</span>`:''}${p.task?.transferredFromName?`<span class="rt-transfer">任务转接自 ${esc(p.task.transferredFromName)}</span>`:''}`,'wait'),'rt-ready');
  html+=section('待确定休息',ps.filter(p=>p.state==='walkback'||p.state==='pending'||opWaiting(p)),p=>card(p,'arrive',`<span class="rt-sub">${opWaiting(p)?'OP · 待确认休息':p.after==='done'?'轮岗结束，回休息室确认':'已下线，到休息室后确认'}</span>`),'rt-arrivals');
  html+=section('吃饭区',ps.filter(p=>!p.entryHold&&poolState(p,n)==='meal'),p=>card(p,null,`<span class="rt-sub">至 ${hm(p.readyAt)} · 剩 ${Math.max(0,Math.ceil(p.readyAt-n))} 分钟</span>${p.assign?`<span class="rt-go">${goText(p)}</span>`:''}`,'meal'),'rt-meals');
  html+=section('休息区',ps.filter(p=>!p.entryHold&&poolState(p,n)==='rest'),p=>card(p,null,`<span class="rt-sub">${waitingInitialRest(p)?'预排休息 · '+hm(p.start)+' 开始计时':'至 '+hm(restUntil(p))+' · 剩 '+Math.max(0,Math.ceil(restUntil(p)-n))+' 分钟'}</span>${p.assign?`<span class="rt-go">${goText(p)}</span>`:''}`,'rest'),'rt-resting');
  html+=section('圈外人员',ps.filter(outsideKind),p=>card(p,null,`<span class="rt-sub">${esc(p.leaveReason||p.away?.reason||outsideKind(p))}</span>`,'dim'),'rt-outside');
  html+=section('任务超时 · 待本人确认',ps.filter(p=>p.entryHold),p=>card(p,'entry_return',`<span class="rt-sub">${esc(p.taskTimeout?.line||'')} · ${esc(p.taskTimeout?.entryPost||'原上岗')} 任务超时</span><span class="rt-task-summary">${p.taskTimeout?.transferredToName?'已转 '+esc(p.taskTimeout.transferredToName):'等待转接'}</span><span class="rt-sub">确认后立即分配，不新增休息</span>`,'late'),'rt-timeout');
  html+=section('其他人员',ps.filter(p=>!seen.has(p.pid)),p=>card(p,null,`<span class="rt-sub">${esc(p.state==='notyet'?'班次未开始':STATE_NAME[p.state]||'')}</span>`,'dim'),'rt-others');
  return html;
}
function workflowBoardContent(day,n,ops,view={}){
  const context={...view,day};
  return `<div class="rt-board${view.daily?' rt-daily-board':''}"><div class="rt-lines">${day.lines.map(line=>lineCard(line,n,ops,context)).join('')}</div><div class="rt-pool">${workflowPools(n,ops,context)}</div></div>`;
}
function workflowBoardView(n,ops){
  const d=RT.data.day,pre=d.persons.length&&n<Math.min(...d.persons.map(p=>p.start));
  return `<section class="rt-workflow-board"><div class="rt-workflow-heading"><h1>${esc(d.date)} · ${pre?'预排看板':'轮岗看板'}</h1>${ops?`<div class="rt-workflow-tools"><button type="button" class="secondary ${d.hold101Active?'rt-101-on':''}" data-wf-101>${d.hold101Active?'取消 101 定岗':'101 定岗'}</button><button type="button" class="secondary" data-wf-undo>返回上一步</button></div>`:''}</div>${pre?'<p class="field-hint">只保存预排，不提前计时。岗位名双击修改，人员及岗位通过拖拽交换。</p>':''}${workflowTimeoutBanner(d,ops)}${workflowBoardContent(d,n,ops)}</section>`;
}
const workflowTimeoutSeen=new Set();
function workflowTimeoutBanner(day,ops){
  const cases=day.timeoutCases||[];
  if(ops&&cases.length&&!RT.modalOpen&&!RT.submitting&&!RT.editingPost){const c=cases.find(c=>{const key='fz.rotation.timeout.notice:'+c.id;try{return !workflowTimeoutSeen.has(key)&&!sessionStorage.getItem(key);}catch(_){return !workflowTimeoutSeen.has(key);}});if(c)setTimeout(()=>{if(RT.modalOpen||!RT.data.day?.timeoutCases?.some(x=>x.id===c.id))return;const key='fz.rotation.timeout.notice:'+c.id;workflowTimeoutSeen.add(key);try{sessionStorage.setItem(key,'1');}catch(_){}confirmBox('强提醒 · '+c.line+'站位待核实','<p>超时影响同线关联任务，相关任务已暂停。请核实执行情况，补记完成或顺接任务。</p><p>关闭提醒后仍保留在看板及待办中，不会每分钟重复弹出。</p>','立即处理',()=>{setTimeout(()=>workflowHandleTimeout(c.id),0);return true;});},0);}
  return cases.map(c=>`<section class="rt-timeout-banner" role="status"><div><strong>${esc(c.line)} · 关联任务待核实</strong><p>${c.people.length} 人受影响 · ${c.handler?'处理中：'+esc(c.handler):'等待负责人接手'}</p></div>${ops?`<button type="button" class="primary" data-wf-timeout="${esc(c.id)}">${c.handler&&c.handler!==me?.name?'接替处理':'立即处理'}</button>`:''}</section>`).join('')+(day.timeoutWaitingMatch||[]).map(d=>`<p class="rt-timeout-waiting">${esc(d.line)} · 顺接后的需求等待匹配</p>`).join('');
}
async function workflowHandleTimeout(id,takeover=false,reason=''){
  const c=RT.data.day?.timeoutCases?.find(c=>c.id===id);if(!c){toast('异常已变化，请刷新',true);return;}
  if(c.handler&&c.handler!==me?.name&&!takeover){formBox('接替异常处理',`<p>当前处理人：${esc(c.handler)}。请先联系对方。</p><label>交接原因<input name="reason" required maxlength="500"></label>`,values=>{setTimeout(()=>workflowHandleTimeout(id,true,values.reason),0);return true;});return;}
  try{await rtPost('/api/rotation/act',{action:'timeout_claim',case_id:id,takeover,reason});await RT.load();}catch(error){toast(error.message,true);return;}
  const current=RT.data.day?.timeoutCases?.find(c=>c.id===id);if(!current)return;
  const people=RT.data.day.persons;
  openModal(`<h3>${esc(current.line)} · 核实关联任务</h3><p class="field-hint">已接手。只纠正受影响任务，其他异常按原规则执行。</p><form data-timeout-form class="form-stack">${current.roots.map(r=>`<fieldset data-timeout-root="${esc(r.pid)}"><legend>${esc(r.name)} · ${hm(r.allowedAt)} 允许出发</legend><label>实际情况<select name="result"><option value="absent">未执行，顺接任务</option><option value="executed">已执行，代确认完成</option></select></label><label>实际执行时间（无法确认可留空）<input name="actualAt" type="time"></label><label>已执行但现已离岗时的当前去向<select name="offpost"><option value="">仍在现场岗位</option value="walkback">已下岗，待回休息室确认</option><option value="done">已下班</option><option value="away">已出圈</option></select></label><label class="checkbox-row"><input type="checkbox" name="returned">未执行人员现已返回，可以立即分配</label></fieldset>`).join('')}<p class="field-hint">逐岗核对现场当前人员。参考填入不是实际确认；后续已执行的结果应保留。</p><div class="rt-timeout-positions">${current.posts.map(post=>`<label>${esc(post.name)}<select data-timeout-position="${esc(post.key)}" ${post.hold?'disabled':''}><option value="">空岗</option>${people.map(p=>`<option value="${esc(p.pid)}" ${p.pid===post.pid?'selected':''}>${esc(p.name)}</option>`).join('')}</select></label>`).join('')}</div><p class="field-hint">点击预览表示已核实以上站位。已执行时间不确定时标记待核实，从核实时间继续计时，不编造实际时间。</p><div class="modal-actions"><button type="button" class="secondary" data-close>暂不处理</button><button type="submit" class="primary">核对并预览恢复</button></div></form>`,modal=>{
    modal.querySelectorAll('[data-timeout-root]').forEach(field=>{field.querySelector('[name=result]').onchange=()=>{const r=current.roots.find(r=>r.pid===field.dataset.timeoutRoot);const proposed=field.querySelector('[name=result]').value==='executed'?r.suggestedPositions:Object.fromEntries(current.posts.map(p=>[p.key,p.pid]));if(proposed&&current.roots.length===1)modal.querySelectorAll('[data-timeout-position]').forEach(select=>select.value=proposed[select.dataset.timeoutPosition]||'');};});
    modal.querySelector('form').onsubmit=async event=>{event.preventDefault();const button=event.target.querySelector('[type=submit]');button.disabled=true;try{const decisions={};modal.querySelectorAll('[data-timeout-root]').forEach(field=>{const raw=field.querySelector('[name=actualAt]').value;decisions[field.dataset.timeoutRoot]={result:field.querySelector('[name=result]').value,actualAt:raw?Number(raw.slice(0,2))*60+Number(raw.slice(3)):null,offpost:field.querySelector('[name=offpost]').value,returned:field.querySelector('[name=returned]').checked};});const positions=Object.fromEntries([...modal.querySelectorAll('[data-timeout-position]')].map(select=>[select.dataset.timeoutPosition,select.value||null]));const preview=await rtPost('/api/rotation/timeout/preview',{case_id:id,decisions,positions,positions_verified:true});workflowTimeoutPreview(preview);}catch(error){toast(error.message,true);button.disabled=false;}};
  });
}
function workflowTimeoutPreview(preview){
  const line=lines=>lines.filter(l=>l.id===preview.line).map(l=>`<div class="rt-task-line"><header>${esc(l.id)}</header>${l.posts.map(p=>`<div class="rt-task-slot"><span>${esc(p.name)}</span><strong>${esc(p.occName||'空岗')}</strong></div>`).join('')}</div>`).join('');
  confirmBox('核对纠正与任务顺接',`<div class="rt-task-columns"><div><p>原记录</p>${line(preview.before_lines)}</div><div><p>核实后</p>${line(preview.after_lines)}</div></div><h4>更新的任务</h4>${preview.updates.map(u=>u.waiting?`<p>${esc(u.name)}：等待前序完成后刷新路线，暂不计超时。</p>`:`<p><strong>${esc(u.name)}</strong>：${esc(u.task.line)} · ${esc(u.task.entryPost)}，${hm(u.task.allowedAt)} 可出发，${hm(u.task.deadlineAt)} 前确认。</p><ul>${u.task.routes.map(r=>`<li>${esc(r.name)} → ${esc(r.to)}</li>`).join('')}</ul>`).join('')}${preview.waitingMatch?`<p>${preview.waitingMatch} 项末端需求暂无人承接，保留等待匹配。</p>`:''}<label class="checkbox-row"><input type="checkbox" data-timeout-notified>已通知可能在途人员及相关岗位，按更新后的任务执行</label><p class="field-hint">全部同步成功才关闭异常；重复提交不重复推岗。</p>`,'确认纠正并恢复',async()=>{if(!document.querySelector('[data-timeout-notified]')?.checked){toast('请先核实并通知相关人员',true);return false;}const done=await act('/api/rotation/act',{...preview.request,notified:true},'异常已处理，关联任务已更新');if(!done)toast('请重新打开处理页面并预览最新安排',true);return done;});
}
function workflowUndo(){
  const lines=RT.data.day.lines;
  formBox('选择返回上一步的线路',`<label>线路<select name="line">${lines.map(l=>`<option value="${esc(l.id)}">${esc(l.id)}</option>`).join('')}</select></label>`,async values=>{
    try{const p=await rtPost('/api/rotation/undo/preview',{line:values.line});
      const snapshots=(lines)=>lines.filter(l=>p.related_lines.includes(l.id)).map(l=>`<div class="rt-task-line"><header>${esc(l.id)}</header>${l.posts.map(x=>`<div class="rt-task-slot"><span>${esc(x.name)}</span><strong>${esc(x.occName||'空岗')}</strong></div>`).join('')}</div>`).join('');
      const names={depart:'确认去上岗',move:'人员拖拽',post_order:'岗位拖拽',post:'加岗／撤岗',hold101:'101定岗',resume101:'取消101定岗',rename_post:'岗位改名',line:'线路启停',away:'暂离',leave:'离岗'};
      setTimeout(()=>confirmBox('恢复预览',`<p>${hm(p.minute)} · ${esc(names[p.action]||p.action)}</p><ul>${p.changes.map(c=>`<li>${esc(c)}</li>`).join('')}</ul><div class="rt-task-columns"><div><p>当前安排</p>${snapshots(p.before_lines)}</div><div><p>恢复后</p>${snapshots(p.after_lines)}</div></div><p>涉及线路：${p.related_lines.map(esc).join('、')}</p><p>${esc(p.notice)}</p><label class="checkbox-row"><input type="checkbox" data-wf-notified>已核实或可立即通知相关人员</label>`,'确认恢复',()=>{
        if(!document.querySelector('[data-wf-notified]')?.checked){toast('请先核实现场并确认可通知人员',true);return false;}
        return act('/api/rotation/act',{action:'restore_line',line:values.line,checkpoint:p.checkpoint,expected_snapshot:p.expected_snapshot,notified:true},'恢复已同步');
      },{autoCancel:60000}),0);
    }catch(error){toast(error.message,true);return false;}
  },{okText:'查看上一步'});
}
document.addEventListener('DOMContentLoaded',()=>{
  const host=document.getElementById('rtApp');if(!host)return;
  host.addEventListener('toggle',event=>{
    const group=event.target;if(!group.matches?.('[data-workflow-shift]')||!group.isConnected)return;
    if(group.open)workflowExpandedShifts.add(group.dataset.workflowShift);else workflowExpandedShifts.delete(group.dataset.workflowShift);
  },true);
  host.addEventListener('click',event=>{if(event.target.closest('[data-wf-rename],.rt-inline-post-name'))event.stopImmediatePropagation();},true);
  host.addEventListener('click',event=>{
    const timeout=event.target.closest('[data-wf-timeout]');if(timeout){void workflowHandleTimeout(timeout.dataset.wfTimeout);return;}
    const hold=event.target.closest('[data-wf-101]');
    if(hold){void act('/api/rotation/act',{action:'hold101',on:!RT.data.day.hold101Active},'101状态已同步').then(done=>{
      if(!done)return;
      const recent=RT.data.day.hold101Recent||[];
      if(recent.length)confirmBox('近3分钟已执行相关任务',`<ul>${recent.map(r=>`<li>${hm(r.minute)} · ${esc(personOf(r.pid)?.name||r.pid)} · ${esc(r.line||'')} · ${esc(r.post||'')}</li>`).join('')}</ul><p>仅提示，不撤销、不重放已执行任务。</p>`,'知道了',()=>true);
    });return;}
    if(event.target.closest('[data-wf-undo]')){workflowUndo();return;}
    const chip=event.target.closest('[data-wf-pid]');if(!chip)return;
    const p=personOf(chip.dataset.wfPid);if(!p)return;
    if(chip.getAttribute('aria-disabled')==='true'){toast('尚未到允许时间或任务已变化，请查看最新安排',true);return;}
    const screen=document.body.classList.contains('rotation-screen');
    confirmRotation(p,chip.dataset.wfAction,screen?'/api/rotation/screen/act':'/api/rotation/act');
  });
  const edit=target=>{
    const key=target.dataset.wfRename,name=target.textContent;let finished=false;
    const input=document.createElement('input');input.className='rt-inline-post-name';input.value=name;input.maxLength=40;input.setAttribute('aria-label','岗位名称');
    target.replaceWith(input);RT.editingPost=true;input.focus();input.select();
    const finish=async save=>{if(finished)return;finished=true;const value=input.value.trim();
      try{if(save&&value&&value!==name){await act('/api/rotation/act',{action:'rename_post',post_key:key,name:value},'岗位名称及相关任务名称已同步');}}
      finally{RT.editingPost=false;if(input.isConnected)input.replaceWith(target);scheduleLoad();}
    };
    input.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key==='Escape'){e.preventDefault();void finish(e.key==='Enter');}});
    input.addEventListener('blur',()=>void finish(true));
  };
  host.addEventListener('dblclick',event=>{const label=event.target.closest('[data-wf-rename]');if(label){event.preventDefault();edit(label);}});
  host.addEventListener('keydown',event=>{if(event.key==='Enter'&&event.target.matches('[data-wf-rename]')){event.preventDefault();edit(event.target);}});
});
