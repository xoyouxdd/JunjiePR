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
function workflowComparison(p){
  if(!p.task)return '';
  const t=p.task;
  const table=(posts,after)=>`<div class="rt-task-line"><header>${esc(t.line)}</header>${posts.map((x,i)=>{
    const changed=after&&x.occ!==t.beforePosts[i]?.occ;
    const entryTime=Number.isFinite(t.beforePosts[i]?.entrySortAt)||Number.isFinite(t.afterPosts[i]?.entrySortAt);
    return `<div class="rt-task-slot${changed?' changed':''}${entryTime?' entry-time-row':''}"><span>${esc(x.name)}</span><div class="rt-task-person"><span class="rt-task-name"><strong>${esc(x.occName||'空岗')}</strong>${rotationPostEntryTime(x)}</span></div></div>`;
  }).join('')}</div>`;
  return `<section class="rt-task-comparison"><h4>执行本任务前后</h4><div class="rt-task-columns"><div><p>执行前 · 当前站位</p>${table(t.beforePosts,false)}</div><div><p>执行后 · 预计站位</p>${table(t.afterPosts,true)}</div></div><small>蓝色仅标出本任务变化，不代表其他任务已经执行。</small></section>`;
}
function workflowPools(n,view){
  if(!view.daily&&!view.readOnly)return forecastPools(n);
  const {day}=view,posted=new Set(day.lines.flatMap(l=>l.posts.map(x=>x.occ).filter(Boolean))),ps=day.persons.filter(p=>!posted.has(p.pid)),seen=new Set();
  const section=(title,list,chip,cls='')=>{list.forEach(p=>seen.add(p.pid));const contents=cls==='rt-others'&&list.length?workflowShiftGroups(list,chip,day,view):`<div class="rt-chips${list.length?'':' empty'}">${list.length?list.map(chip).join(''):'暂无'}</div>`;return `<section class="rt-sect ${cls}${list.length?'':' is-empty'}"><h2>${title}<span class="rt-n">${list.length}</span></h2>${contents}</section>`;};
  if(view.readOnly)return section('未占岗人员',[...ps,...rosterSickPeople(day)],p=>`<div class="rt-chip"><span class="rt-nm">${esc(p.name)}</span><span class="rt-sub">${esc(outsideKind(p)||STATE_NAME[p.state]||'')}</span></div>`);
  const card=(p,body,cls='')=>{
    if(p.rosterSick)return `<div class="rt-chip dim"><span class="rt-nm">${esc(p.name)}</span>${body}</div>`;
    const drag=view.editable&&p.role==='rotation'?` draggable="true" data-daily-person="person:${esc(p.pid)}"`:'';
    const livePerson=forecastActive()&&RT.data.day.status==='live'&&day.date===RT.data.day.date;
    const tag=view.editable||livePerson?'button':'div';
    const action=view.editable?` type="button" aria-haspopup="dialog" data-daily-pid="${esc(p.pid)}"${drag}`:livePerson?` type="button" data-f-person="${esc(p.pid)}"`:'';
    return `<${tag}${action} class="rt-chip ${cls}">${rotationName(p)}${rotationSevenBadge(p,p.start===420&&!p.sevenReplaced)}${body}</${tag}>`;
  };
  const isOutside=p=>['出圈','病假'].includes(outsideKind(p));
  const inside=ps.filter(p=>!isOutside(p));
  const initial=p=>p.role==='rotation'&&p.state==='notyet'&&[420,435].includes(p.start);
  const pending=p=>!plannedReturnRestLabel(p)&&(['walkback','pending','op'].includes(p.state)||p.role==='op'&&p.state==='notyet');
  const chip=p=>card(p,`<span class="rt-sub">${p.role==='op'?'OP':p.role==='excluded'?'不轮岗':p.state==='notyet'?(view.frozen?'开班预排':'班次未开始'):esc(STATE_NAME[p.state]||'待安排')}</span>`);
  let html=section('待出发',inside.filter(p=>p.state==='ready'),chip,'rt-ready');
  html+=section('待确定休息',inside.filter(pending),chip,'rt-arrivals');
  html+=section('吃饭区',inside.filter(p=>p.state==='meal'),chip,'rt-meals');
  html+=section('休息区',inside.filter(p=>p.state==='rest'||initial(p)||plannedReturnRestLabel(p)),p=>{
    const label=plannedReturnRestLabel(p);
    return label?card(p,`<span class="rt-sub">${esc(label)}</span>`):chip(p);
  },'rt-resting');
  html+=section('圈外人员',[...ps.filter(isOutside),...rosterSickPeople(day)],p=>card(p,`<span class="rt-sub">${esc(outsideKind(p)==='病假'?sickLeaveLabel(p):p.away?.reason||'出圈')}</span>`,'dim'),'rt-outside');
  html+=section('其他人员',inside.filter(p=>!seen.has(p.pid)),chip,'rt-others');
  return html;
}
function workflowBoardContent(day,n,ops,view={}){
  const context={...view,day};
  return `<div class="rt-board${view.daily?' rt-daily-board':''}"><div class="rt-lines">${day.lines.map(line=>lineCard(line,n,ops,context)).join('')}</div><div class="rt-pool">${workflowPools(n,context)}</div></div>`;
}
function workflowBoardView(n,ops){
  const d=RT.data.day,pre=d.persons.length&&n<Math.min(...d.persons.map(p=>p.start));
  return `<section class="rt-workflow-board"><div class="rt-workflow-heading"><h1>${esc(d.date)} · ${pre?'预排看板':'轮岗看板'}</h1>${ops?`<div class="rt-workflow-tools"><button type="button" class="secondary ${d.hold101Active?'rt-101-on':''}" data-wf-101>${d.hold101Active?'取消 101 定岗':'101 定岗'}</button><button type="button" class="secondary" data-wf-undo>返回上一步</button></div>`:''}</div>${pre?`<p class="field-hint">首班到班前仅展示预排，尚未实际占岗或计时。${ops?'岗位名双击修改，人员及岗位通过拖拽交换。':''}</p>`:''}${rotationEndLabel(d)}${workflowBoardContent(d,n,ops)}</section>`;
}
function workflowUndo(){
  const lines=RT.data.day.lines;
  formBox('选择返回上一步的线路',`<label>线路<select name="line">${lines.map(l=>`<option value="${esc(l.id)}">${esc(l.id)}</option>`).join('')}</select></label>`,async values=>{
    try{const p=await rtPost('/api/rotation-board/undo/preview',{line:values.line});
      const snapshots=(lines)=>lines.filter(l=>p.related_lines.includes(l.id)).map(l=>`<div class="rt-task-line"><header>${esc(l.id)}</header>${l.posts.map(x=>`<div class="rt-task-slot"><span>${esc(x.name)}</span><strong>${esc(x.occName||'空岗')}</strong></div>`).join('')}</div>`).join('');
      const names={depart:'确认去上岗',move:'人员拖拽',post_order:'岗位拖拽',post:'加岗／撤岗',hold101:'101定岗',resume101:'取消101定岗',rename_post:'岗位改名',line:'线路启停',away:'暂离',leave:'离岗'};
      setTimeout(()=>confirmBox('恢复预览',`<p>${hm(p.minute)} · ${esc(names[p.action]||p.action)}</p><ul>${p.changes.map(c=>`<li>${esc(c)}</li>`).join('')}</ul><div class="rt-task-columns"><div><p>当前安排</p>${snapshots(p.before_lines)}</div><div><p>恢复后</p>${snapshots(p.after_lines)}</div></div><p>涉及线路：${p.related_lines.map(esc).join('、')}</p><p>${esc(p.notice)}</p><label class="checkbox-row"><input type="checkbox" data-wf-notified>已核实或可立即通知相关人员</label>`,'确认恢复',()=>{
        if(!document.querySelector('[data-wf-notified]')?.checked){toast('请先核实现场并确认可通知人员',true);return false;}
        return act('/api/rotation-board/act',{action:'restore_line',line:values.line,checkpoint:p.checkpoint,expected_snapshot:p.expected_snapshot,notified:true},'恢复已同步');
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
  host.addEventListener('click',event=>{
    const hold=event.target.closest('[data-wf-101]');
    if(hold){void act('/api/rotation-board/act',{action:'hold101',on:!RT.data.day.hold101Active},'101状态已同步').then(done=>{
      if(!done)return;
      const recent=RT.data.day.hold101Recent||[];
      if(recent.length)confirmBox('近3分钟已执行相关任务',`<ul>${recent.map(r=>`<li>${hm(r.minute)} · ${esc(personOf(r.pid)?.name||r.pid)} · ${esc(r.line||'')} · ${esc(r.post||'')}</li>`).join('')}</ul><p>仅提示，不撤销、不重放已执行任务。</p>`,'知道了',()=>true);
    });return;}
    if(event.target.closest('[data-wf-undo]')){workflowUndo();return;}
  });
});
