'use strict';
// Shared real-data presentation; screen devices never receive management controls.
function shortRotationShift(p){
  const part=value=>{const h=Math.floor(value/60)%24,m=Math.floor(value%60);return `${h%12||12}${m?pad2(m):''}`;};
  return `${part(p.start)}-${part(p.end)}`;
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
  const day=view.day||RT.data.day,posted=new Set(day.lines.flatMap(l=>l.posts.map(x=>x.occ).filter(Boolean))),ps=day.persons.filter(p=>!posted.has(p.pid)),seen=new Set();
  const section=(title,list,chip,cls='')=>{list.forEach(p=>seen.add(p.pid));return `<section class="rt-sect ${cls}${list.length?'':' is-empty'}"><h2>${title}<span class="rt-n">${list.length}</span></h2><div class="rt-chips${list.length?'':' empty'}">${list.length?list.map(chip).join(''):'暂无'}</div></section>`;};
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
  html+=section('待出发',ps.filter(p=>!p.entryHold&&poolState(p,n)==='ready'),p=>card(p,p.assign?'depart':null,`${p.assign?`<span class="rt-go">${goText(p)}</span>`:''}<span class="rt-task-summary">${workflowTaskSummary(p)}</span><span class="rt-sub">${p.task?`${hm(p.task.allowedAt)} 可出发 · ${hm(p.task.deadlineAt)} 前确认`:p.assign?hm(p.assign.departAt)+' 出发':'等待安排'}</span>${p.task?.transferredFromName?`<span class="rt-transfer">转接自 ${esc(p.task.transferredFromName)}</span>`:''}`,'wait'),'rt-ready');
  html+=section('待确定休息',ps.filter(p=>p.state==='walkback'||p.state==='pending'||opWaiting(p)),p=>card(p,'arrive',`<span class="rt-sub">${opWaiting(p)?'OP · 待确认休息':p.after==='done'?'轮岗结束，回休息室确认':'已下线，到休息室后确认'}</span>`),'rt-arrivals');
  html+=section('吃饭区',ps.filter(p=>p.state==='meal'),p=>card(p,null,`<span class="rt-sub">至 ${hm(p.readyAt)} · 剩 ${Math.max(0,Math.ceil(p.readyAt-n))} 分钟</span>${p.assign?`<span class="rt-go">${goText(p)}</span>`:''}`,'meal'),'rt-meals');
  html+=section('休息区',ps.filter(p=>poolState(p,n)==='rest'),p=>card(p,null,`<span class="rt-sub">${waitingInitialRest(p)?'预排休息 · '+hm(p.start)+' 开始计时':'至 '+hm(restUntil(p))+' · 剩 '+Math.max(0,Math.ceil(restUntil(p)-n))+' 分钟'}</span>${p.assign?`<span class="rt-go">${goText(p)}</span>`:''}`,'rest'),'rt-resting');
  html+=section('圈外人员',ps.filter(outsideKind),p=>card(p,null,`<span class="rt-sub">${esc(p.leaveReason||p.away?.reason||outsideKind(p))}</span>`,'dim'),'rt-outside');
  html+=section('任务超时 · 待本人确认',ps.filter(p=>p.entryHold),p=>card(p,'entry_return',`<span class="rt-sub">${esc(p.taskTimeout?.line||'')} · ${esc(p.taskTimeout?.entryPost||'原上岗')} 任务超时</span><span class="rt-task-summary">${p.taskTimeout?.transferredToName?'已转 '+esc(p.taskTimeout.transferredToName):'等待转接'}</span><span class="rt-sub">确认知晓后重新领取</span>`,'late'),'rt-timeout');
  html+=section('其他人员',ps.filter(p=>!seen.has(p.pid)),p=>card(p,null,`<span class="rt-sub">${esc(p.state==='notyet'?'班次未开始':STATE_NAME[p.state]||'')}</span>`,'dim'),'rt-others');
  return html;
}
function workflowBoardContent(day,n,ops,view={}){
  const context={...view,day};
  return `<div class="rt-board${view.daily?' rt-daily-board':''}"><div class="rt-lines">${day.lines.map(line=>lineCard(line,n,ops,context)).join('')}</div><div class="rt-pool">${workflowPools(n,ops,context)}</div></div>`;
}
function workflowBoardView(n,ops){
  const d=RT.data.day,pre=d.persons.length&&n<Math.min(...d.persons.map(p=>p.start));
  return `<section class="rt-workflow-board"><div class="rt-workflow-heading"><h1>${esc(d.date)} · ${pre?'预排看板':'轮岗看板'}</h1>${ops?`<div class="rt-workflow-tools"><button type="button" class="secondary ${d.hold101Active?'rt-101-on':''}" data-wf-101>${d.hold101Active?'取消 101 定岗':'101 定岗'}</button><button type="button" class="secondary" data-wf-undo>返回上一步</button></div>`:''}</div>${pre?'<p class="field-hint">只保存预排，不提前计时。岗位名双击修改，人员及岗位通过拖拽交换。</p>':''}${workflowBoardContent(d,n,ops)}</section>`;
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
  host.addEventListener('click',event=>{if(event.target.closest('[data-wf-rename],.rt-inline-post-name'))event.stopImmediatePropagation();},true);
  host.addEventListener('click',event=>{
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
