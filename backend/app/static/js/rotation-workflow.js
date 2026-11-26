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
function workflowComparison({task},batch=false){
  const group=RT.data?.day?.lines.find(line=>line.id===task?.line)?.group||'';
  return globalThis.RotationTaskPresentation.render(task,{batch,group,entryTime:rotationPostEntryTime});
}

const batchNoticeMinutes=5;
function rotationContinuedBatchPost(day,line,post){
  return day.persons.some(person=>{
    const result=person.batchResult;
    return result?.line===line.id&&result.beforePosts?.some(p=>p.occ===post.occ)&&
      result.afterPosts?.some(p=>p.key===post.key&&p.occ===post.occ)&&
      result.afterPosts.every(p=>line.posts.some(current=>current.key===p.key&&current.occ===p.occ));
  });
}
function recentRotationBatch(day,n){
  const dispatches=day?.completedShiftBatches;
  if(!dispatches||day.readOnly||day.status!=='live')return null;
  const results=day.persons.filter(p=>p.batchResult);
  const dispatchAt=start=>dispatches[Math.round(start*60)];
  const starts=[...new Set(results.map(p=>p.batchResult.start))].filter(start=>{
    const at=dispatchAt(start);
    return Number.isFinite(at)&&at<=n&&n<at+batchNoticeMinutes;
  });
  if(!starts.length)return null;
  const start=starts.sort((a,b)=>dispatchAt(b)-dispatchAt(a)||b-a)[0];
  return {start,at:dispatchAt(start),people:results.filter(p=>p.batchResult.start===start)};
}

function rotationBatchTitle(batch){
  const scheduledShift=batch.people.every(p=>p.batchResult.reportAt==null);
  return `${hm(batch.start)} ${scheduledShift?'班批量排岗':'批量排岗'}`;
}

function rotationBatchNotice(day,n){
  const batch=recentRotationBatch(day,n);
  if(!batch)return '';
  const boarded=batch.people.filter(p=>p.batchResult.line).length;
  const resting=batch.people.length-boarded;
  return `<section class="rt-batch-notice" aria-label="批量上岗通知"><span role="status"><strong>${rotationBatchTitle(batch)}</strong>
    <span> · ${forecastClock(batch.at)} · ${boarded} 人上岗${resting?` · ${resting} 人休息`:''}</span></span><button type="button" class="secondary" data-rotation-batch>查看安排</button></section>`;
}

document.addEventListener('click',event=>{
  if(!event.target.closest('[data-rotation-batch]'))return;
  const day=RT.data?.day,batch=recentRotationBatch(day,nowMin());
  if(!batch)return;
  const lines=new Map(batch.people.filter(p=>p.batchResult.line).map(p=>[p.batchResult.line,p.batchResult]));
  const comparisons=day.lines.map(line=>workflowComparison({task:lines.get(line.id)},true)).join('');
  const resting=batch.people.filter(p=>!p.batchResult.line);
  openModal(`<h3>${rotationBatchTitle(batch)}</h3>${comparisons?`${comparisons}`:''}
    ${resting.length?`<p class="rt-task-rest-note rt-tone-rest">休息安排 · ${resting.map(p=>`${esc(p.name)}（${hm(p.batchResult.restUntil)} 可上岗）`).join('、')}</p>`:''}
    <div class="modal-actions"><button type="button" class="secondary" data-close>关闭</button></div>`);
});
function workflowPools(n,view){
  if(!view.daily&&!view.readOnly)return forecastPools(n,view);
  const {day}=view,posted=new Set(day.lines.flatMap(l=>l.posts.map(x=>x.occ).filter(Boolean))),ps=day.persons.filter(p=>!posted.has(p.pid)),seen=new Set();
  const section=(title,list,chip,cls='')=>{list.forEach(p=>seen.add(p.pid));const contents=cls==='rt-others'&&list.length?workflowShiftGroups(list,chip,day,view):`<div class="rt-chips${list.length?'':' empty'}">${list.length?list.map(chip).join(''):'暂无'}</div>`;return `<section class="rt-sect ${cls}${list.length?'':' is-empty'}"><h2>${title}<span class="rt-n">${list.length}</span></h2>${contents}</section>`;};
  if(view.readOnly)return section('未占岗人员',[...ps,...rosterSickPeople(day)],p=>`<div class="rt-chip"><span class="rt-nm">${esc(p.name)}</span><span class="rt-sub">${esc(outsideKind(p)||STATE_NAME[p.state]||'')}</span></div>`);
  const card=(p,body,cls='')=>{
    if(p.rosterSick)return `<div class="rt-chip dim"><span class="rt-nm">${esc(p.name)}</span>${body}</div>`;
    const drag=view.editable&&p.role==='rotation'?` draggable="true" data-daily-person="person:${esc(p.pid)}"`:'';
    const tag=view.editable?'button':'div';
    const action=view.editable?` type="button" aria-haspopup="dialog" data-daily-pid="${esc(p.pid)}"${drag}`:'';
    return `<${tag}${action} class="rt-chip ${cls}">${rotationName(p)}${rotationSevenBadge(p,p.start===420&&!p.sevenReplaced)}${body}</${tag}>`;
  };
  const inside=ps.filter(p=>!outsideKind(p));
  const pending=p=>!plannedRestLabel(p)&&(['walkback','pending','op'].includes(p.state)||opWaiting(p));
  const chip=p=>card(p,`<span class="rt-sub">${p.role==='op'?'OP':p.role==='excluded'?'不轮岗':p.state==='notyet'?(view.frozen?'开班预排':'班次未开始'):esc(STATE_NAME[p.state]||'待安排')}</span>`);
  let html=section('待出发',inside.filter(p=>p.state==='ready'),chip,'rt-ready');
  html+=section('待确定休息',inside.filter(pending),chip,'rt-arrivals');
  html+=section('吃饭区',inside.filter(p=>p.state==='meal'),chip,'rt-meals');
  html+=section('休息区',inside.filter(p=>p.state==='rest'||plannedRestLabel(p)),p=>{
    const label=plannedRestLabel(p);
    return label?card(p,`<span class="rt-sub">${esc(label)}</span>`):chip(p);
  },'rt-resting');
  html+=section('圈外人员',[...ps.filter(outsideKind),...rosterSickPeople(day)],p=>card(p,`<span class="rt-sub">${esc(outsideRosterDetail(p))}</span>`,'dim'),'rt-outside');
  html+=section('其他人员',inside.filter(p=>!seen.has(p.pid)),chip,'rt-others');
  return html;
}
function workflowBoardContent(day,n,ops,view={}){
  const context={...view,day};
  return `<div class="rt-board${view.daily?' rt-daily-board':''}"><div class="rt-lines">${day.lines.map(line=>lineCard(line,n,ops,context)).join('')}</div><div class="rt-pool">${workflowPools(n,context)}</div></div>`;
}
function workflowBoardView(n,ops,canManage=false){
  const d=RT.data.day,pre=d.persons.length&&n<Math.min(...d.persons.map(p=>p.start));
  const settings=ops&&d.prestartPlanRevision&&canManage
    ?'<button type="button" class="secondary" data-opening-settings>预排设置</button>':'';
  return `<section class="rt-workflow-board"><div class="rt-workflow-heading"><h1>${esc(d.date)} · ${pre?'预排看板':'轮岗看板'}</h1>${ops?`<div class="rt-workflow-tools">${settings}<button type="button" class="secondary" data-mode101-toggle aria-pressed="${!!d.mode101Active}" ${RT.mode101SwitchPending||d.status!=='live'||d.readOnly?'disabled':''}>${RT.mode101SwitchPending?'正在切换…':RT.mode101SwitchNeedsSync?'同步101状态':d.mode101Active?'结束 101 模式':'101 模式'}</button></div>`:''}</div>${pre?`<p class="field-hint">首班到班前仅展示预排，尚未实际占岗或计时。${ops?'岗位名双击修改，人员及岗位通过拖拽交换。':''}</p>`:''}${rotationEndLabel(d)}${ops?staffingWarningView(d):''}${rotationBatchNotice(d,n)}${workflowBoardContent(d,n,ops)}</section>`;
}

async function workflowToggle101(){
  if(RT.mode101SwitchPending||!forecastOperator())return;
  const day=RT.data?.day;
  if(!day||day.status!=='live'||day.readOnly)return;
  if(RT.mode101SwitchNeedsSync){
    RT.mode101SwitchPending=true;RT.render();
    try{await RT.load();RT.mode101SwitchNeedsSync=false;toast('101 状态已同步，请按当前状态操作');}
    catch(error){toast('暂未同步成功，请稍后再试；不会重复提交切换',true);}
    finally{RT.mode101SwitchPending=false;RT.render();}
    return;
  }
  const on=!day.mode101Active,date=day.date,snapshot=day.forecastSnapshot;
  const names=day.lines.flatMap(line=>line.posts.filter(post=>post.mode101||['塔台','塔协','塔台协助'].includes(post.name.replace(/\s/g,''))).map(post=>`${line.id} · ${post.name}`));
  confirmBox(on?'开启 101 模式？':'结束 101 模式？',`<p>${on?'塔台、塔协将暂停参与顺推，岗位人员置灰。':'置灰人员进入待恢复状态，等待后续推岗恢复。'}</p><p>${names.map(esc).join('、')}</p>`,on?'确认开启':'确认结束',async()=>{
    if(RT.mode101SwitchPending)return false;
    if(RT.data?.day?.date!==date||RT.data.day.forecastSnapshot!==snapshot){toast('站位已变化，请重新核对 101 模式',true);return true;}
    RT.mode101SwitchPending=true;RT.render();
    let committed=false;
    try{
      await rtPost(forecastPath(),{action:'mode101',on,expected_snapshot:snapshot});
      committed=true;
      await RT.load();
      RT.mode101SwitchNeedsSync=false;
      toast(on?'101 模式已开启':'101 模式已结束，人员按后续推岗恢复');
    }catch(error){
      RT.mode101SwitchNeedsSync=committed||!!error.uncertain;
      if(committed)toast(`101 ${on?'开启':'结束'}已生效，画面尚未同步。请点“同步101状态”核对`,true);
      else if(error.uncertain)toast('101 切换结果待核对，请点“同步101状态”；不要重复切换',true);
      else{toast(error.message,true);try{await RT.load();}catch{RT.mode101SwitchNeedsSync=true;}}
    }finally{RT.mode101SwitchPending=false;RT.render();}
    // A completed or uncertain write is never retried by the confirmation dialog.
    return true;
  });
}

function workflow101Menu(key) {
  const day=RT.data.day;
  const line=day.lines.find(item=>item.posts.some(post=>post.key===key));
  if(!line)return;
  const index=line.posts.findIndex(post=>post.key===key), post=line.posts[index];
  if(!post.mode101)return;
  const snapshot=day.forecastSnapshot;
  const options=line.posts.filter((candidate,i)=>i===index||i>index&&candidate.open&&!candidate.hold&&!candidate.mode101);
  const targetOptions=options.map(candidate=>`<option value="${esc(candidate.key)}" ${post.mode101.target===candidate.key?'selected':''}>${esc(candidate.name)}</option>`).join('');
  const fixedPeople=new Set(day.lines.flatMap(item=>item.posts.filter(slot=>slot.mode101).map(slot=>slot.occ)));
  const people=day.persons.filter(person=>!fixedPeople.has(person.pid)&&!person.postHold&&['onpost','rest','ready','pending'].includes(person.state));
  const title=post.mode101.phase==='fixed'?'101 岗位':'101 岗位待恢复';
  openModal(`<h3>${title}</h3><p>${esc(line.id)} · ${esc(post.name)}</p><div class="rt-actions">
    ${post.occ?'<button type="button" class="secondary" data-mode101-goal>调整应到岗位</button>':''}
    ${post.mode101.phase==='fixed'&&line.active&&post.open&&people.length&&!post.hold?'<button type="button" class="secondary" data-mode101-swap>更换人员</button>':''}
    ${post.manualHold&&post.occ?'<button type="button" class="secondary" data-mode101-release-hold>取消持续定岗</button>':''}
    <button type="button" class="ghost" data-close>关闭</button></div>`,host=>{
    const target=host.querySelector('[data-mode101-goal]');
    if(target)target.onclick=()=>formBox('调整应到岗位',
      `<label>应到岗位<select name="target">${targetOptions}<option value="" ${post.mode101.target===null?'selected':''}>待下线</option></select></label>
      <p>结束 101 后，恢复顺推时从这个岗位的下一岗继续。</p>`,
      values=>forecastRun({action:'mode101_target',post_key:key,target_post_key:values.target||null,expected_snapshot:snapshot}));
    const swap=host.querySelector('[data-mode101-swap]');
    if(swap)swap.onclick=()=>formBox('更换 101 岗位人员',
      `<label>接替人员<select name="pid" required><option value="">请选择</option>${people.map(person=>`<option value="${esc(person.pid)}">${esc(person.name)} · ${esc(person.line||STATE_NAME[person.state])}</option>`).join('')}</select></label>`,
      values=>forecastRun({action:'mode101_swap',post_key:key,pid:values.pid,expected_snapshot:snapshot}));
    const hold=host.querySelector('[data-mode101-release-hold]');
    if(hold)hold.onclick=()=>confirmBox('取消持续定岗',`<p>${esc(post.name)}</p>`,'确认',
      ()=>forecastRun({action:'post_hold',line:line.id,i:index,pid:post.occ,on:false,expected_snapshot:snapshot}));
  });
}
document.addEventListener('DOMContentLoaded',()=>{
  const host=document.getElementById('rtApp');if(!host)return;
  host.addEventListener('toggle',event=>{
    const group=event.target;if(!group.matches?.('[data-workflow-shift]')||!group.isConnected)return;
    if(group.open)workflowExpandedShifts.add(group.dataset.workflowShift);else workflowExpandedShifts.delete(group.dataset.workflowShift);
  },true);
  host.addEventListener('click',event=>{
    if(!forecastOperator()||!RT.data?.day)return;
    const toggle=event.target.closest('[data-mode101-toggle]');
    if(toggle){
      void workflowToggle101();
      return;
    }
    const post=event.target.closest('[data-mode101-post]');
    if(post)workflow101Menu(post.dataset.mode101Post);
  });
});
