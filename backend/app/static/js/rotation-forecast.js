'use strict';
let forecastDrag=null;
// Version 2 UI. No new timer, animation loop or whole-page polling is introduced.
function forecastActive(){return RT.data?.day?.ruleVersion===2;}
function forecastOperator(){return !forecastScreen()&&typeof isManager==='function'&&isManager();}
function forecastScreen(){return document.body.classList.contains('rotation-screen');}
function forecastPostOperator(){return forecastOperator()&&!forecastScreen();}
function forecastClock(value){
  if(!Number.isFinite(value))return '—';
  const seconds=Math.round(value*60),day=Math.floor(seconds/86400);
  return `${day===1?'次日 ':day>1?'+'+day+'日 ':''}${pad2(Math.floor(seconds/3600)%24)}:${pad2(Math.floor(seconds/60)%60)}:${pad2(seconds%60)}`;
}
function forecastShiftFields(start,end,offset=0){
  const day=Number.isFinite(start)?Math.floor(start/1440):offset;
  return `<label>开始日期<select name="start_day_offset"><option value="0">本工作日</option><option value="1" ${day===1?'selected':''}>次日</option></select></label><label>开始<input type="time" name="start" value="${Number.isFinite(start)?hm(start):''}" required></label><label>结束<input type="time" name="end" value="${Number.isFinite(end)?hm(end):''}" required></label><p class="field-hint">结束时间不晚于开始时间时，按跨零点班次处理。</p>`;
}
function forecastPath(){return forecastScreen()?`${rotationScreenApi}/act`:'/api/rotation-board/act';}
async function forecastSend(body){
  const result=await rtPost(forecastPath(),body);
  await RT.load();
  return result;
}
function forecastRoutes(task){return `<ul>${(task?.routes||[]).map(r=>`<li>${esc(r.name)}：${esc(r.from)} → ${esc(r.to)}</li>`).join('')}</ul>`;}
function forecastOverdue(p,n,screen=false){
  if(p.state==='exception')return '<span class="rt-forecast-overdue">已超时</span>';
  if(!p.canDepart)return '';
  // The deadline includes closed-line pauses and delayed openings.
  const deadline=p.task?.deadlineAt,windowMinutes=p.task?.timeoutWindowMinutes??3;
  if(screen){
    return Number.isFinite(deadline)?`<span class="rt-screen-countdown" data-screen-deadline="${deadline}"></span>`:'';
  }
  const start=Number.isFinite(deadline)?deadline-windowMinutes:p.readyAt;
  if(!Number.isFinite(start)||!Number.isFinite(n)||n<start)return '';
  const seconds=Math.floor(Math.max(0,n-start)*60+1e-6);
  return `<span class="rt-forecast-overdue" title="从可上岗起累计，满${windowMinutes}分钟进入异常池">超时${Math.floor(seconds/60)}分${pad2(seconds%60)}秒</span>`;
}
function forecastLineCard(L,n,ops,{day=RT.data.day,viewOnly=false}={}){
  const manage=!viewOnly&&forecastOperator(),editPosts=manage&&forecastPostOperator(),personTag=manage?'button':'span',screen=forecastScreen();
  const times=`<span class="rt-sort-times"><span class="rt-sort-time" title="当前线路排序时间">${forecastClock(L.sortAt||0)}</span><span class="rt-previous-sort-time">上次 ${forecastClock(L.previousSortAt)}</span></span>`;
  const collapsed=typeof rotationLineCollapsed==='function'&&rotationLineCollapsed(L);
  const status=collapsed?'未开线':`${L.posts.filter(x=>x.open&&!x.hold&&x.mode101?.phase!=='fixed').length}/${L.posts.length}岗`;
  const head=`<div class="rt-line-head g-${esc(L.group||'')}"${viewOnly?'':` data-f-head="${esc(L.id)}"`}${manage?' tabindex="0" role="button"':''}><span class="rt-lid">${esc(L.id)}</span>${collapsed?'':times}${L.adding?'<span class="rt-badge">加岗</span>':''}<span>${status}</span></div>`;
  const rows=collapsed?'':L.posts.map((post,i)=>{
    const p=day.persons.find(p=>p.pid===post.occ),drag=manage&&p?' draggable="true" data-f-drag="'+esc(p.pid)+'"':'';
    const addTime=pendingAddTime(post)||(post.openingStatus?post.openAt:null);
    const adding=addTime||post.openingStatus;
    const marker=post.mode101,modeControl=manage&&marker;
    const goal=marker?.target===null?'待下线':L.posts.find(slot=>slot.key===marker?.target)?.name||post.name;
    const displayName=p&&marker?`${p.name}（${goal}）`:p?.name;
    const who=displayName||(addTime?`${addTime} 加岗`:post.openingStatus?'等待接替':'空岗');
    const status=post.openExpired?'加岗已失效':adding?(post.openingStatus?'准备加岗':'待加'):post.prearranged||p?.state==='notyet'?'预排':post.open?(p?'在岗':'空岗'):'未开岗';
    const label=editPosts?`<button type="button" class="rt-pn" draggable="true" data-f-post="${esc(L.id)}#${i}" data-f-post-key="${esc(post.key)}" aria-label="${esc(post.name)}，双击改名，拖拽交换岗位" title="双击改名，拖拽交换岗位">${esc(post.name)}</button>`:`<span class="rt-pn">${esc(post.name)}</span>`;
    const canCorrect=(manage||screen)&&!day.readOnly&&post.missedCheckIn&&p?.canCorrectCheckIn;
    const attendanceBadge=(post.missedCheckIn||p?.missedCheckIn)&&!canCorrect
      ?'<small class="rt-badge rt-missed-attendance">未答到</small>'
      :post.pendingCheckIn?'<small class="rt-badge prep">未签到</small>':'';
    const checkIn=canCorrect
      ?`<button type="button" class="rt-badge rt-missed-attendance rt-post-check-in" data-f-correct-check-in="${esc(p.pid)}" title="补记已到">未答到</button>`
      :manage&&p?.canBatchCheckIn&&(p.state==='onpost'||post.pendingCheckIn)?`<button type="button" class="secondary rt-post-check-in" data-f-check-in="${esc(p.pid)}">已到</button>`:'';
    const continued=p?.state==='onpost'&&marker?.phase!=='fixed'&&rotationContinuedBatchPost(day,L,post);
    const batch=!continued&&p?.state==='onpost'&&p.batchOnPost!=null&&marker?.phase!=='fixed';
    const entry=p?.state==='onpost'?rotationPostEntryTime(p):'';
    const end=rotationPostShiftEnd(p);
    const notices=rotationSevenBadge(p,p?.state==='notyet'&&p.start===420)+attendanceBadge+rotationMealBadge(p)
      +(p?.preparing?'<span class="rt-badge prep">'+preparingText(p.preparing)+'</span>':'')
      +(post.hold||post.plannedHold?`<span class="rt-badge post-hold">${post.hold?'定岗':'预予定岗'}</span>`:'');
    const addNotice=p&&addTime?`<span class="rt-badge prep">待加 ${esc(addTime)}</span>`:'';
    const rowClass=`rt-post${post.open?'':' closed'}${adding?' pending-add':''}${p?.preparing?' prep':''}${batch?' rt-batch-on-post':''}${continued?' rt-batch-continued':''}${marker?.phase==='fixed'?' rt-mode101-fixed':''}${screen?'':' rt-post-compact'}`;
    const rowAttrs=viewOnly?'':`data-f-slot="${esc(L.id)}#${i}" data-f-slot-key="${esc(post.key)}"`;
    const modeDrag=marker?.phase==='fixed'&&!post.hold&&p?.state==='onpost'?drag:'';
    const workerAttrs=modeControl?` type="button" data-mode101-post="${esc(post.key)}"${modeDrag}`:manage&&p?` type="button" data-f-person="${esc(p.pid)}"${drag}`:'';
    if(!screen){
      const summary=p?`<span class="rt-post-name" title="${esc(displayName)}">${esc(displayName)}</span>${end?`<span class="rt-post-end-label">下班 ${end}</span>`:''}`
        :`<span>${esc(post.openExpired?status:adding?(post.openingStatus?'等待接替':'待加岗'):'空岗')}</span>${addTime?`<time>${esc(addTime)}</time>`:''}`;
      const detail=p?entry+notices+addNotice+(status==='预排'?'<span class="rt-post-slot-status">预排</span>':'')
        :status==='未开岗'?'<span class="rt-post-slot-status">未开岗</span>':'';
      const tag=p||modeControl?personTag:'span';
      const batchLabel=batch?' title="系统批量上岗"':'';
      return `<div class="${rowClass}" ${rowAttrs}${batchLabel}>${label}<${tag} class="rt-who-name"${workerAttrs}>
        <span class="rt-post-person-main">${summary}</span>${detail?`<span class="rt-post-person-detail">${detail}</span>`:''}</${tag}>${checkIn}</div>`;
    }
    const statusTag=screen&&status===who&&!checkIn?'':`<span class="rt-mins${status==='在岗'&&!checkIn?' rt-post-staffed':''}">${status}${checkIn}</span>`;
    const screenName=p?`<span class="rt-post-name">${esc(who)}</span>`:esc(who);
    return `<div class="${rowClass}" ${rowAttrs}${batch?' title="系统批量上岗"':''}>${label}<span class="rt-who-name">${screenName}${addNotice}${entry}${end}${notices}</span>${statusTag}</div>`;
  }).join('');
  return `<div class="rt-line${L.active?'':' off'}${collapsed?' is-collapsed':''}${screen?'':' rt-compact-line'}">${head}${rows}</div>`;
}
function forecastPreparing(p){return p.upcomingShift||p.state==='notyet'&&(p.canBatchCheckIn||p.batchCheckedIn);}
function forecastPoolData(day){
  const posted=new Set(day.lines.flatMap(l=>l.posts.map(x=>x.occ).filter(Boolean)));
  const people=[...day.persons.filter(p=>p.state!=='onpost'&&(!posted.has(p.pid)||p.upcomingShift&&!openingShift(p))),...rosterSickPeople(day)];
  const outside=people.filter(outsideKind),inside=people.filter(p=>!outsideKind(p));
  const pending=p=>opWaiting(p)||['pending','walkback','op'].includes(p.state);
  const rest=p=>!pending(p)&&(forecastPreparing(p)||plannedRestLabel(p)||p.state==='rest'||p.state==='ready'&&p.breakKind!=='meal');
  const definitions=[['pending','待确定休息',pending],
    ['meal','吃饭区',p=>p.state==='meal'||p.state==='ready'&&p.breakKind==='meal'],
    ['rest','休息区',rest],
    ['away','圈外人员',outsideKind],['other','其他人员',p=>!pending(p)&&!rest(p)&&['notyet','other','done','excluded'].includes(p.state)],
    ['exception','异常池',p=>p.state==='exception']];
  return definitions.map(([key,title,filter])=>[key,title,key==='away'?outside:inside.filter(filter)]);
}

function renderRotationTotals(container, day, now, pools) {
  const live = day?.ruleVersion === 2 && !day.readOnly && day.status !== 'ended';
  container.hidden = !live;
  if (!live) return;
  if (!container.firstElementChild) {
    container.innerHTML = `
      <span title="启用线路中当前开放的岗位数">岗位数 <strong data-rotation-total="posts"></strong></span>
      <span title="休息区与待确认休息人数">休息人数 <strong data-rotation-total="rest"></strong></span>
      <span title="当前时刻处于病假时段的人数">病假人数 <strong data-rotation-total="sick"></strong></span>
      <span title="当前已出圈的人数">出圈人数 <strong data-rotation-total="away"></strong></span>`;
  }
  const { rest, pending, away } = Object.fromEntries(
    (pools ?? forecastPoolData(day)).map(([key, , people]) => [key, people]));
  const resting = rest.filter(p => !p.canDepart && !p.canBatchCheckIn && !p.awaitingShiftEntry &&
    (p.state === 'rest' || p.state === 'ready'));
  const totals = {
    posts: day.lines.reduce((count, line) => count + (line.active ? line.posts.filter(post => post.open).length : 0), 0),
    rest: resting.length + pending.filter(p=>p.state!=='notyet').length,
    sick: away.filter(p => outsideKind(p) === '病假' &&
      (!Number.isFinite(p.start) || now >= p.start) && (!Number.isFinite(p.end) || now < p.end)).length,
    away: away.filter(p => p.state === 'away' && outsideKind(p) === '出圈').length,
  };
  for (const count of container.querySelectorAll('[data-rotation-total]')) {
    const total = String(totals[count.dataset.rotationTotal]);
    if (count.textContent !== total) count.textContent = total;
  }
}

function forecastPoolCard(p,key,n,{manage=false,screen=false}={}){
  if(p.rosterSick){
    const canMove=manage&&RT.data?.day?.rosterSickPoolMoves;
    const name=canMove?`<button type="button" class="rt-forecast-name" data-f-person="${esc(p.pid)}">${esc(p.name)}</button>`:`<span class="rt-nm">${esc(p.name)}</span>`;
    return `<div class="rt-chip dim"${canMove?` draggable="true" data-f-drag="${esc(p.pid)}"`:''}>${name}<span class="rt-sub">${esc(sickLeaveLabel(p))}</span></div>`;
  }
  const confirm=manage||screen,personTag=manage?'button':'span';
  const upcoming=forecastPreparing(p),ready=!!p.canDepart,rest=['rest','meal','ready'].includes(p.state);
  const a=upcoming?p.previewAssign:p.assign,route=upcoming?a:p.task;
  const mealTail=p.breakKind==='meal_rest'?'饭后休息 · ':'';
  const plannedRest=plannedRestLabel(p);
  const canCheckIn=confirm&&p.canBatchCheckIn&&!(screen&&key==='rest');
  const detail=screen&&ready?'':key==='away'?esc(outsideRosterDetail(p)):opWaiting(p)?(p.state==='notyet'?`OP · ${hm(p.start)}到班`:'OP'):plannedRest?esc(plannedRest):upcoming?`准备上班 · 距到班 ${Math.max(0,Math.ceil(p.start-n))} 分钟`:p.awaitingShiftEntry?(ready?'准备上班 · 已到班':'准备上班 · 已到班，等待安排'):p.exception?`原预计 ${esc(p.exception.assignment?.line||'')} · ${forecastClock(p.exception.allowedAt)}可上岗 · ${forecastClock(p.exception.at)}进异常池`:rest?`${mealTail}${Math.max(0,Math.ceil((p.readyAt??n)-n))}分钟 · ${forecastClock(p.readyAt)}可上岗`:p.state==='notyet'?`${forecastClock(p.start)}到班`:p.state==='done'?'已下班':'';
  return `<div class="rt-chip${ready?' rt-can-board':''}"${manage?' draggable="true" data-f-drag="'+esc(p.pid)+'"':''}><div class="rt-forecast-person-head"><${personTag}${manage?' type="button"':''} class="rt-forecast-name"${manage?' data-f-person="'+esc(p.pid)+'"':''}>${rotationName(p)}</${personTag}>${rotationSevenBadge(p,p.state==='notyet'&&p.start===420)}${p.missedCheckIn?'<small class="rt-badge rt-missed-attendance">未答到</small>':''}${rotationMealBadge(p)}${forecastOverdue(p,n,screen)}</div>${detail?`<span class="rt-sub">${detail}</span>`:''}${canCheckIn?`<button type="button" class="primary" data-f-check-in="${esc(p.pid)}">已到</button>`:upcoming&&p.batchCheckedIn?'<span class="rt-sub">已签到</span>':''}${a?`<span class="rt-go">预计 ${esc(a.line)}${a.why?' · '+esc(a.why):''}${a.batchSize>1?' · 同分钟预计'+a.batchSize+'人':''}</span>`:''}${route?.routes?.length?`<details class="rt-forecast-detail"${screen?` data-screen-detail="route:${esc(p.pid)}"`:''}><summary>预计路线</summary>${forecastRoutes(route)}</details>`:''}${confirm&&ready?`<button type="button" class="primary" data-f-board="${esc(p.pid)}">${screen?'确认去轮岗':'可上岗'}</button>`:''}${confirm&&['pending','walkback','op'].includes(p.state)?`<button type="button" class="primary" data-f-arrive="${esc(p.pid)}">${screen?'去休息':'确认休息／吃饭'}</button>`:''}</div>`;
}

function forecastPools(n,{day=RT.data.day,viewOnly=false}={}){
  const manage=!viewOnly&&forecastOperator(),screen=forecastScreen();
  return forecastPoolData(day).map(([key,title,list])=>
    `<section class="rt-sect${list.length?'':' is-empty'}"${viewOnly?'':` data-f-pool="${key}"`}><h2>${title}<span class="rt-n">${list.length}</span>${manage&&key!=='exception'?'<button type="button" class="secondary rt-pool-add" data-f-add>新增人员</button>':''}</h2><div class="rt-chips${list.length?'':' empty'}">${list.map(p=>forecastPoolCard(p,key,n,{manage,screen})).join('')||'暂无'}</div></section>`
  ).join('');
}
function forecastConfirm(p){
  if(!p.canDepart){toast('餐休尚未结束或此人已退出预排',true);return;}
  const body={action:'depart',pid:p.pid,confirmation_token:p.confirmationTokens.depart,expected_line:p.assign?.line};
  const date=RT.data.day.date;
  const show=(task,changed=false,previous=body.expected_line,waiting=[])=>{
    const current=personOf(p.pid);
    if(!task||RT.data?.day?.date!==date||!current?.canDepart||current.confirmationTokens?.depart!==body.confirmation_token){
      toast('人员状态已变化，请查看最新安排',true);return;
    }
    const basis=`<p>预计 ${esc(task.line)}线 · ${esc(task.reason||'')}${Number.isFinite(task.sortAt)?` · 表头 ${esc(forecastClock(task.sortAt))} · ${task.participatingPosts}个参与岗位`:''}</p>`;
    const names=waiting.map(person=>esc(person.name)).join('、');
    const sameLine=previous===task.line;
    const notice=changed?`<p class="rt-route-change" role="alert">${sameLine?'本线接替路线已更新。':`原预计 ${esc(previous)}，现调整为 ${esc(task.line)}。`}${names?`<br>${names}还未上岗，请确认先上。`:''}</p>`:'';
    confirmBox(changed?(sameLine?'接替路线已改变，请重新确认':'线路已改变，请重新确认'):`${p.name} · 确认上岗`,`${notice}${basis}${workflowComparison({task})}`,'确认上岗',async()=>{
      const origin=document.querySelector('#rtModal .rt-card');
      RT.submitting=true;
      try{
        const result=await forecastSend({...body,expected_line:task.line,expected_push7:task.push7Route??null,expected_opening:task.openingRoute??null,expected_kind:task.kind,expected_route:task.routeSignature});
        if(!origin?.isConnected)return true;
        if(result?.line_changed){setTimeout(()=>show(result.task,true,result.previous_line,result.waiting_predecessors||[]),0);return true;}
        toast('已上岗');return true;
      }catch(error){toast(error.message,true);return true;}
      finally{RT.submitting=false;}
    });
    activeRotationConfirmation={pid:p.pid,date,token:body.confirmation_token,tokenAction:'depart'};
  };
  show(p.task);
}
function forecastArrive(p){
  const date=RT.data.day.date,mealSignature=JSON.stringify(p.mealOptions);
  const choices=p.mealOptions?.forced?'<option value="meal">吃饭（已到强制饭点）</option>':'<option value="rest">休息</option>'+(p.mealOptions?.canChoose?'<option value="meal">吃饭</option>':'');
  const reserve=p.mealOptions?.reserveText?`<p class="field-hint">${esc(p.mealOptions.reserveText)}。基础用餐条件及强制饭点沿用现有规则。</p>`:'';
  formBox(`${p.name} · 确认餐休`,'<label>本次安排<select name="kind">'+choices+'</select></label>'+reserve,(values, form)=>{
    const repeat=values.kind==='meal'&&p.ate;
    const send=()=>forecastRun({action:'arrive',pid:p.pid,confirmation_token:p.confirmationTokens.arrive,meal_choice:values.kind,repeat_meal:repeat});
    return repeat?confirmRepeatMeal(send,form):send();
  });
  activeRotationConfirmation={pid:p.pid,date,token:p.confirmationTokens.arrive,tokenAction:'arrive',refresh:current=>{
    if(JSON.stringify(current.mealOptions)!==mealSignature){closeModal();toast('餐休条件已更新，请重新确认');}
  }};
}
function forecastPoolPerson(pid){
  const day=RT.data.day;
  const person=personOf(pid);
  if(person||!day.rosterSickPoolMoves)return person;
  const sick=rosterSickPeople(day).find(p=>p.pid===pid);
  return sick?{...sick,rosterDate:day.date}:null;
}
function forecastNeedsShift(p){return p.rosterSick&&!(Number.isFinite(p.start)&&Number.isFinite(p.end));}
function forecastChoosePool(p, selected='pending'){
  const pools=[['pending','待确定休息'],['rest','休息区'],['meal','吃饭区'],['away','圈外人员'],['other','其他人员']];
  const options=pools.map(([key,label])=>`<option value="${key}"${key===selected?' selected':''}>${label}</option>`).join('');
  const shift=forecastNeedsShift(p)?`<p class="field-hint">班表只标了病假。请补填当天班次，供餐休和上岗安排使用。</p>${forecastShiftFields()}`:'';
  const name=p.rosterSick?`<p>${esc(p.name)} · ${esc(sickLeaveLabel(p))}</p>`:'';
  formBox('移动人员池',`${name}<label>目标池<select name="pool">${options}</select></label>${shift}`,(v,form)=>forecastPoolMove(p,v.pool,form,v));
}
function forecastPerson(p){
  if(!p||!forecastOperator())return;
  if(p.rosterSick){forecastChoosePool(p,'rest');return;}
  const screen=forecastScreen();
  const actions=[['pool','移动到人员池'],...(!screen?[['shift','修改当天班次'],['sick',p.state==='onpost'?'病假（等待接替）':'病假'],['away','安排出圈'],...(p.state==='away'?[['back','回圈，休息15分钟']]:[]),...(['rest','meal','ready'].includes(p.state)?[['break_time','修改餐休剩余时间']]:[]),...(p.state==='onpost'?[['post_hold',p.postHold?'取消持续定岗':'设置持续定岗']]:[])]:[])];
  openModal(`<h3>${esc(p.name)}</h3><p>${forecastClock(p.start)}—${forecastClock(p.end)}</p><div class="rt-actions">${actions.map(([key,label])=>`<button type="button" class="secondary" data-f-operation="${key}">${label}</button>`).join('')}<button type="button" class="ghost" data-close>关闭</button></div>`,host=>{
    host.querySelectorAll('[data-f-operation]').forEach(button=>button.onclick=()=>{
      const action=button.dataset.fOperation,base={action,pid:p.pid};
      if(action==='shift')formBox('修改当天班次',forecastShiftFields(p.start,p.end),v=>forecastRun({...base,...v,start_day_offset:Number(v.start_day_offset)}));
      else if(action==='break_time')formBox('修改剩余时间',`<label>剩余分钟<input type="number" name="remaining" min="0" max="1440" step="0.1" value="${Math.max(0,Math.ceil(p.readyAt-nowMin()))}" required></label>`,v=>forecastRun({...base,remaining:Number(v.remaining)}));
      else if(action==='post_hold')confirmBox(button.textContent,`<p>${esc(p.name)} · ${esc(p.post||'')}</p>`,'确认',()=>forecastRun({...base,line:p.line,i:p.postIndex,on:!p.postHold,reason:'现场持续定岗'}));
      else if(action==='pool')forecastChoosePool(p,outsideKind(p)==='病假'?'rest':'pending');
      else confirmBox(button.textContent,`<p>${esc(p.name)}</p>`,'确认',()=>forecastRun(base));
    });
  });
}
async function forecastRun(body){try{await forecastSend(body);return true;}catch(error){toast(error.message,true);return false;}}
function forecastRenamePost(target){
  if(!forecastPostOperator()||RT.editingPost)return;
  const key=target.dataset.fPostKey,post=RT.data.day.lines.flatMap(L=>L.posts).find(p=>p.key===key);
  if(!post)return;
  const original=post.name,input=document.createElement('input');let finished=false;
  input.className='rt-inline-post-name';input.value=original;input.maxLength=40;input.setAttribute('aria-label','岗位名称');
  RT.editingPost=true;target.replaceWith(input);input.focus();input.select();
  const finish=async save=>{
    if(finished)return;finished=true;const value=input.value.trim();
    try{
      if(save&&value&&value!==original){await forecastSend({action:'rename_post',post_key:key,expected_name:original,name:value});toast('岗位名称已更新');}
    }catch(error){toast(error.message,true);}
    finally{RT.editingPost=false;if(input.isConnected)input.replaceWith(target);RT.render();}
  };
  input.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();void finish(event.key==='Enter');}});
  input.addEventListener('blur',()=>void finish(true));
}
function forecastPostSwap(source,line,i,targetKey){
  if(!forecastPostOperator())return;
  const sourceLine=RT.data.day.lines.find(L=>L.id===source.line),targetLine=RT.data.day.lines.find(L=>L.id===line);
  const left=sourceLine?.posts[source.i],right=targetLine?.posts[i];
  if(source.date!==RT.data.day.date||!left||!right||left.key!==source.key||right.key!==targetKey){toast('岗位已变化，请刷新后重新拖拽',true);return;}
  if(source.line===line&&source.i===i)return;
  formBox('选择岗位交换方式',`<p>${esc(source.line)} · ${esc(left.name)} ↔ ${esc(line)} · ${esc(right.name)}</p><label>交换方式<select name="swapMode"><option value="posts">只交换岗位，人员留在原位置</option><option value="with_people">岗位和人员一起交换</option></select></label><p>只交换岗位：人员保持原位置；连人带岗：岗位和人员一起移动。定岗须先解除。</p>${forecastHeaderOption()}`,values=>forecastRun({action:'post_order',line:source.line,i:source.i,target_line:line,target_i:i,swapMode:values.swapMode,source_post_key:source.key,target_post_key:targetKey,expected_snapshot:source.snapshot,update_sort_time:values.update_sort_time==='on'}));
  forecastBindHeaderOption(document.querySelector('#rtModal [name=swapMode]'),mode=>{
    if(mode!=='with_people')return [];
    const copies=RT.data.day.lines.map(L=>({...L,posts:L.posts.slice()}));
    const from=copies.find(L=>L.id===source.line),to=copies.find(L=>L.id===line);
    [from.posts[source.i],to.posts[i]]=[to.posts[i],from.posts[source.i]];
    return copies.filter(L=>{
      const previous=RT.data.day.lines.find(x=>x.id===L.id),oldIndex=forecastHeadIndex(previous),newIndex=forecastHeadIndex(L);
      const before=previous.posts[oldIndex]?.occ,after=L.posts[newIndex]?.occ;
      return after&&after!==before&&personOf(after)?.state==='onpost';
    }).map(L=>L.id);
  });
}
function forecastHeadIndex(line){return line?.active?line.posts.findIndex(p=>p.open&&!p.hold&&p.mode101?.phase!=='fixed'):-1;}
function forecastHeaderOption(){return '<div data-f-header-option hidden><label class="checkbox-row"><input type="checkbox" name="update_sort_time">更新表头轮岗时间（默认不更新）</label><p class="field-hint" data-f-header-lines></p></div>';}
function forecastBindHeaderOption(select,affected){
  const host=document.querySelector('#rtModal [data-f-header-option]'),checkbox=host.querySelector('[name=update_sort_time]');
  const update=()=>{
    const lines=[...new Set(affected(select.value))];
    host.hidden=!lines.length;checkbox.disabled=!lines.length;checkbox.checked=false;
    host.querySelector('[data-f-header-lines]').textContent=`勾选后将 ${lines.join('、')} 线表头更新为当前时间；不勾选保留原表头。实际站岗计时按本次操作记录。`;
  };
  select.addEventListener('change',update);update();
}
function confirmRepeatMeal(send,returnToForm){confirmBox('再次进入吃饭区','此人已进过吃饭区，是否继续？','继续',send,{warn:true,returnToForm});return false;}
async function forecastPoolMove(p,pool,form,values){
  if(!p)return;
  if(p.rosterSick){
    if(forecastNeedsShift(p)&&!values){forecastChoosePool(p,pool);return false;}
    const correction={date:p.rosterDate};
    if(forecastNeedsShift(p))Object.assign(correction,{start:values.start,end:values.end,start_day_offset:Number(values.start_day_offset)});
    return forecastRun({action:'pool',pid:p.pid,pool,roster_sick:correction});
  }
  const repeat=pool==='meal'&&p.ate&&!['rest','ready'].includes(p.state);
  const send=()=>forecastRun({action:'pool',pid:p.pid,pool,repeat_meal:repeat,person_context:{state:p.state,readyAt:p.readyAt??null,sequence:p.confirmationSequence??0,line:p.state==='onpost'?p.line:null,postIndex:p.postIndex??null}});
  return repeat?confirmRepeatMeal(send,form):send();
}
function forecastPlace(pid,line,i){
  const p=personOf(pid),L=RT.data.day.lines.find(x=>x.id===line),post=L?.posts[i];if(!p||!post)return;
  const sourceLine=RT.data.day.lines.find(x=>x.posts.some(item=>item.occ===pid));
  const sourcePost=sourceLine?.posts.find(item=>item.occ===pid),other=personOf(post.occ);
  const destination=`${esc(line)} · ${esc(post.name)}`;
  const source=sourcePost?`${esc(sourceLine.id)} · ${esc(sourcePost.name)}`:'';
  if(RT.data.day.prestartPlanRevision&&p.state==='notyet'){
    if(post.occ===pid)return;
    const expected_snapshot=RT.data.day.forecastSnapshot;
    confirmBox('调整预排人员',`${esc(p.name)} → ${destination}${other?`<br>${esc(other.name)} → ${source||'待安排区'}`:''}`,'确认调整',
      ()=>forecastRun({action:'move',pid,line,i,expected_snapshot}));
    return;
  }
  const swapLabel=other?(sourcePost?'直接交换，两人互换岗位':'直接交换，本人上岗、原岗人员回待确定休息'):'直接放入空岗';
  const exchange=!!(sourcePost&&other);
  const swapDetail=`${esc(p.name)}：${source?source+' → ':''}${destination}${other?`<br>${esc(other.name)}：${destination} → ${source||'待确定休息'}`:source?'；原岗位空出。':'；不替下其他人员。'}${exchange?'<br>保留各岗位的轮岗时间和线路表头时间。':''}`;
  const original=p.exception?.assignment;
  const early=original?.kind==='push7'&&original.line===line&&i===forecastHeadIndex(L)&&personOf(original.target);
  const earlyIndex=early&&L.posts.findIndex(item=>item.occ===early.pid&&item.open&&!item.hold);
  const recover=early&&earlyIndex>=0&&early.start===420&&!early.sevenReplaced&&early.requestedExit!=='sick';
  const pushLabel=recover?`顺推至 ${esc(early.name)} 所在岗，送待确定休息`:'顺推，遇空岗填补，否则末岗下线';
  const snapshot=RT.data.day.forecastSnapshot;
  formBox('现场安排',`<p>${esc(p.name)} → ${destination}${other?'（'+esc(other.name)+'）':''}</p><label>方式<select name="place_mode"><option value="push">${pushLabel}</option><option value="swap">${swapLabel}</option></select></label><p data-f-place-detail hidden>${swapDetail}</p>${forecastHeaderOption()}`,v=>forecastRun({action:'place',pid,line,i,place_mode:v.place_mode,expected_snapshot:snapshot,update_sort_time:v.update_sort_time==='on'}));
  const select=document.querySelector('#rtModal [name=place_mode]'),detail=document.querySelector('#rtModal [data-f-place-detail]');
  select.onchange=()=>{detail.hidden=select.value!=='swap';};
  forecastBindHeaderOption(select,mode=>{
    if(mode==='swap'&&exchange)return [];
    return i===forecastHeadIndex(L)&&post.occ!==pid&&(mode!=='push'||!recover||
      !L.posts.slice(earlyIndex+1).some(item=>item.open&&!item.hold))?[L.id]:[];
  });
}

function forecastMode101Drop(person,lineId,index,snapshot){
  const day=RT.data.day;
  const sourceLine=day.lines.find(line=>line.posts.some(post=>post.occ===person.pid));
  const source=sourceLine?.posts.find(post=>post.occ===person.pid);
  const line=day.lines.find(item=>item.id===lineId);
  const destination=line?.posts[index];
  const fromFixed=source?.mode101?.phase==='fixed',toFixed=destination?.mode101?.phase==='fixed';
  if(!fromFixed&&!toFixed)return false;
  if(source===destination)return true;
  if(snapshot&&snapshot!==day.forecastSnapshot){toast('岗位安排已变化，请重新拖拽',true);return true;}
  if(!line?.active||!destination?.open||destination.hold||source?.hold||
      destination.mode101?.phase==='return'||source?.mode101?.phase==='return'){
    toast('请选择已开放且可交换的岗位',true);
    return true;
  }
  if(!['onpost','ready','rest','pending'].includes(person.state)){
    toast('此人当前不能接替 101 岗位',true);
    return true;
  }
  const other=personOf(destination.occ);
  const from=source?`${esc(sourceLine.id)} · ${esc(source.name)}`:'';
  const to=`${esc(line.id)} · ${esc(destination.name)}`;
  const detail=`<p>${esc(person.name)} → ${to}</p>${other?`<p>${esc(other.name)} → ${from||'待确定休息'}</p>`:''}`;
  const target=fromFixed?{post_key:source.key,destination_post_key:destination.key}:{post_key:destination.key};
  confirmBox(other?'确认交换人员':'确认移动人员',detail,'确认',
    ()=>forecastRun({action:'mode101_swap',pid:person.pid,...target,expected_snapshot:day.forecastSnapshot}));
  return true;
}

function newPostFields(line){
  const positions=line.posts.map((post,i)=>`<option value="${i}">第 ${i+1} 岗 · 在${esc(post.name)}前</option>`).join('');
  return `<label>新岗位名称<input name="name" maxlength="40"></label><label>插入位置<select name="position">${positions}<option value="${line.posts.length}" selected>第 ${line.posts.length+1} 岗 · 末尾</option></select></label>`;
}

function forecastPostSchedule(line,open){
  const day=RT.data.day;
  const L=day?.lines.find(item=>item.id===line);
  if(!L)return;
  const withWithdrawal=!open&&Boolean(day.closurePreviews);
  const options=L.posts.map((p,i)=>({p,i})).filter(({p})=>!p.mode101&&(open?!p.open:p.open&&!p.hold));
  const canCreate=open&&RT.data.structuralPosts;
  if(!options.length&&!canCreate){toast(open?'没有可加的岗位':'没有可撤的开放岗位',true);return;}
  const dates=['本工作日','次日','后日'];
  const offset=Math.floor(nowMin()/1440);
  const scheduleFields=`<label>岗位<select name="i">${options.map(({p,i})=>`<option value="${i}">${esc(p.name)}</option>`).join('')}${canCreate?'<option value="new">新建岗位</option>':''}</select></label>
    ${canCreate?`<div class="form-stack" data-f-new-post hidden>${newPostFields(L)}</div>`:''}
    <label>计划日期<select name="day_offset">${dates.map((label,i)=>`<option value="${i}" ${i===offset?'selected':''}>${label}</option>`).join('')}</select></label>
    <label>计划时间（留空立即生效）<input type="time" name="time" step="1"></label>`;
  const modeFields=withWithdrawal?`
    <label>撤岗方式<select name="withdrawal"><option value="push">顺推</option><option value="rest">原岗人员直接去休息区</option></select></label>
    <label data-f-close-destination>顺推安排<select name="push_destination"></select></label>
    <p class="field-hint" data-f-close-summary aria-live="polite"></p>`:
    open?'<p class="field-hint">定时加岗到点时线路仍停用，这次计划失效。</p>':'';
  formBox(open?'顺推加岗':'撤岗',scheduleFields+modeFields,v=>{
    if(v.i==='new'){
      const body={action:'post_create',line:L.id,i:Number(v.position),name:v.name,expected_snapshot:day.forecastSnapshot};
      if(v.time)Object.assign(body,{openAt:v.time,day_offset:Number(v.day_offset)});
      return forecastRun(body);
    }
    const i=Number(v.i),post=L.posts[i];
    const body={action:'post',line:L.id,i,day_offset:Number(v.day_offset),post_key:post.key,open,expected_snapshot:day.forecastSnapshot};
    if(v.time)body[open?'openAt':'closeAt']=v.time;
    if(withWithdrawal){
      body.close_mode=v.withdrawal==='rest'?'rest':v.push_destination;
      if(!v.time&&body.close_mode!=='rest')body.expected_closure=day.closurePreviews?.[post.key]?.[body.close_mode]?.signature;
    }
    return forecastRun(body);
  });
  if(canCreate){
    const form=document.querySelector('#rtModal [data-form]');
    const fields=form.querySelector('[data-f-new-post]');
    const showNew=()=>{
      const selected=form.elements.i.value==='new';
      fields.hidden=!selected;
      form.elements.name.required=selected;
    };
    form.elements.i.addEventListener('change',showNew);
    showNew();
  }
  if(!withWithdrawal)return;
  const form=document.querySelector('#rtModal [data-form]');
  const destination=form.elements.push_destination,summary=form.querySelector('[data-f-close-summary]');
  const outcomes={done:'下班',sick:'进入病假区'};
  let preferred='push_off';
  function showClosure(event){
    if(event?.target===destination)preferred=destination.value;
    const i=Number(form.elements.i.value),post=L.posts[i];
    const timed=Boolean(form.elements.time.value);
    const push=form.elements.withdrawal.value==='push';
    const variants=day.closurePreviews?.[post.key],off=variants?.push_off;
    const opening=variants?.push_open,sick=variants?.push_sick;
    const later=timed&&L.posts.length>i+1;
    const choices=[['push','普通顺推']];
    if(later||off?.target)choices.push(['push_off',timed?'到点时优先推出连续在岗最久的待下班人员':
      `优先推出待下班人员 ${off.exitName}（${off.exitPost}）`]);
    if(opening||(later&&'push_open' in (variants||{})))choices.push(['push_open',timed?'到点时顺推加岗':
      `顺推加岗 · ${opening.openingPost}`]);
    if(sick||(later&&'push_sick' in (variants||{})))choices.push(['push_sick',timed?'到点时顺推接替病假人员':
      `顺推接替病假人员 ${sick.exitName}（${sick.exitPost}）`]);
    destination.replaceChildren(...choices.map(([value,label])=>new Option(label,value)));
    destination.value=choices.some(([value])=>value===preferred)?preferred:'push';
    form.querySelector('[data-f-close-destination]').hidden=!push||choices.length===1;
    form.querySelector('[type=submit]').disabled=!variants;
    if(!variants){summary.textContent='请刷新看板后重新选择岗位。';return;}
    if(!push){summary.textContent='直接送休息区，表头轮岗时间保持原值。';return;}
    if(timed){
      const fallback=destination.value==='push'?'':'若目标已处理或到点时不可用，则按普通顺推。';
      summary.textContent='到点按实际站位执行。同一线路同一时间开撤岗时保留表头轮岗时间。'+fallback;
      return;
    }
    const result=variants[destination.value];
    const exit=result.openingPost?`顺推填入 ${result.openingPost}。`:
      result.exitName?`${result.exitPost}的 ${result.exitName} ${outcomes[result.outcome]||'去待确定休息'}。`:'本次无人被推出。';
    summary.textContent=exit+(result.updateSortTime?'将更新表头轮岗时间。':'表头轮岗时间保持原值。');
  }
  form.addEventListener('input',showClosure);
  form.addEventListener('change',showClosure);
  showClosure();
}
function forecastHead(line){
  if(!forecastOperator()||forecastScreen())return;
  const L=RT.data.day.lines.find(x=>x.id===line);if(!L)return;
  const lineContext={date:RT.data.day.date,active:L.active,planRevision:RT.data.day.prestartPlanRevision??null};
  const emptyPosts=L.posts.map((p,i)=>({p,i})).filter(({p})=>p.canDeletePost);
  const snapshot=RT.data.day.forecastSnapshot;
  openModal(`<h3>${esc(line)} · 线路操作</h3><div class="rt-actions"><button type="button" class="secondary" data-f-sort>修改排序时间</button><button type="button" class="secondary" data-f-open>顺推加岗</button><button type="button" class="secondary" data-f-close>撤岗</button>${RT.data.day.postTransferFairnessAvailable?'<button type="button" class="secondary" data-f-transfer>跨线移岗</button>':''}${emptyPosts.length?'<button type="button" class="danger" data-f-delete-post>删除空岗</button>':''}<button type="button" class="secondary" data-f-line>${L.active?'停用':'启用'}线路</button><button type="button" class="ghost" data-close>关闭</button></div>`,host=>{
    host.querySelector('[data-f-sort]').onclick=()=>formBox('修改排序时间',`<label>日期<select name="day_offset">${[0,1,2].map(day=>`<option value="${day}" ${Math.floor((L.sortAt||0)/1440)===day?'selected':''}>${['本工作日','次日','后日'][day]}</option>`).join('')}</select></label><label>排序时间<input type="time" name="time" step="1" value="${forecastClock(L.sortAt||0).split(' ').at(-1)}" required></label>`,v=>forecastRun({action:'sort_time',line,...v,day_offset:Number(v.day_offset)}));
    host.querySelector('[data-f-open]').onclick=()=>forecastPostSchedule(line,true);
    host.querySelector('[data-f-close]').onclick=()=>forecastPostSchedule(line,false);
    host.querySelector('[data-f-transfer]')?.addEventListener('click',()=>forecastTransferForm(line));
    host.querySelector('[data-f-delete-post]')?.addEventListener('click',()=>formBox('删除空岗',
      `<label>岗位<select name="i">${emptyPosts.map(({p,i})=>`<option value="${i}">第 ${i+1} 岗 · ${esc(p.name)}</option>`).join('')}</select></label><p class="field-hint">删除当前工作日的岗位，有定时加撤岗安排的会一并取消。</p>`,
      v=>forecastRun({action:'post_delete',line,i:Number(v.i),post_key:L.posts[Number(v.i)].key,expected_snapshot:snapshot}),
      {warn:true,okText:'确认删除'}));
    host.querySelector('[data-f-line]').onclick=()=>confirmBox(L.active?'停用线路':'启用线路',`<p>${lineContext.planRevision?'将调整当天的预排人员；首班开始后须重新核对。':'按当前现场站位调整本线路。'}</p>`,L.active?'停用':'启用',()=>forecastRun({action:'line',line,active:!L.active,line_context:lineContext}));
  });
}
function forecastAdd(){
  let request=0,revision=0,timer,selected=null,preview=null;
  const numbered=formalRotation?'<div data-f-temp-fields hidden><label>工号（选填）<input name="temporary_no" inputmode="numeric" maxlength="8" autocomplete="off"></label><p class="field-hint">填写工号后，本人可登录查看轮岗待办。</p><div data-f-identity aria-live="polite"></div></div>':'';
  openModal('<h3>新增人员</h3><label><span data-f-name-label>姓名或工号</span><input name="query" autocomplete="off" maxlength="100"></label><div data-f-results aria-live="polite"></div><label><input type="checkbox" name="temporary">新增临时人员</label>'+numbered+forecastShiftFields(null,null,nowMin()>=1440?1:0)+'<div class="rt-actions"><button type="button" class="primary" data-f-save>新增</button><button type="button" class="ghost" data-close>关闭</button></div>',host=>{
    const input=host.querySelector('[name=query]'),results=host.querySelector('[data-f-results]');
    const toggle=host.querySelector('[name=temporary]'),fields=host.querySelector('[data-f-temp-fields]');
    const number=host.querySelector('[name=temporary_no]'),identity=host.querySelector('[data-f-identity]');
    const save=host.querySelector('[data-f-save]');
    const clearPreview=()=>{++revision;preview=null;if(identity)identity.textContent='';save.textContent='新增';};
    host.querySelectorAll('input, select').forEach(field=>field.addEventListener('input',clearPreview));
    toggle.onchange=()=>{
      ++request;clearTimeout(timer);results.textContent='';selected=null;
      if(fields)fields.hidden=!toggle.checked;
      host.querySelector('[data-f-name-label]').textContent=toggle.checked?'姓名':'姓名或工号';
      if(!toggle.checked&&input.value)input.oninput();
    };
    input.oninput=()=>{selected=null;clearTimeout(timer);const generation=++request;
      results.textContent='';if(toggle.checked||!input.value.trim())return;
      timer=setTimeout(async()=>{try{const data=await rtApi('/api/rotation-board/people-search?q='+encodeURIComponent(input.value));if(!input.isConnected||generation!==request)return;
        results.innerHTML=data.people.map((p,i)=>`<button type="button" class="secondary" data-f-result="${i}">${esc(p.name)} · ${esc(p.pid)} · ${esc(p.attraction||'')}</button>`).join('');
        results.querySelectorAll('[data-f-result]').forEach(b=>b.onclick=()=>{selected=data.people[Number(b.dataset.fResult)];results.textContent=selected.name+' · '+selected.pid;});
      }catch(error){if(input.isConnected&&generation===request)results.textContent=error.message;}},200);
    };
    save.onclick=async()=>{const temporary=toggle.checked,pid=temporary?number?.value.trim():selected?.pid;
      if(!temporary&&!selected){toast('请先选择员工，或勾选临时人员',true);return;}
      const body={action:'add_person',temporary,pid,name:input.value.trim(),start_day_offset:Number(host.querySelector('[name=start_day_offset]').value),start:host.querySelector('[name=start]').value,end:host.querySelector('[name=end]').value};
      save.disabled=true;
      try{
        if(temporary&&pid&&!preview){
          const generation=revision;
          const result=await rtPost('/api/rotation-board/people-preview',body);
          if(!input.isConnected||generation!==revision)return;
          const facts=result.identity;
          const account=result.initial_pin?'新临时账号用完整工号登录，初始密码为工号后四位。'
            :result.reuse_account?'沿用已有轮岗登录方式。':facts.issues.length?'用原账号密码登录，仅可查看本人轮岗待办。':'用原员工账号登录。';
          identity.textContent=[facts.name,facts.origin_name,...facts.issues,account].filter(Boolean).join(' · ');
          preview=result;
          save.textContent='确认新增';
          return;
        }
        await forecastSend(preview?{...body,expected_identity:preview.digest,confirm_exceptions:true}:body);
        if(input.isConnected){++request;clearTimeout(timer);closeModal();}
      }catch(error){clearPreview();toast(error.message,true);}finally{save.disabled=false;}
    };
  });
}
document.addEventListener('click',event=>{
  if(!forecastActive())return;
  const target=event.target.closest('[data-f-board],[data-f-check-in],[data-f-correct-check-in],[data-f-arrive],[data-f-person],[data-f-head],[data-f-add]');
  if(!target)return;event.preventDefault();event.stopImmediatePropagation();
  if(target.hasAttribute('data-f-board'))forecastConfirm(personOf(target.dataset.fBoard));
  else if(target.hasAttribute('data-f-check-in')){
    const p=personOf(target.dataset.fCheckIn);
    if(p?.canBatchCheckIn){
      target.disabled=true;
      void forecastRun({action:'batch_check_in',pid:p.pid,confirmation_token:p.confirmationTokens.batch_check_in})
        .finally(()=>{target.disabled=false;});
    }
  }
  else if(target.hasAttribute('data-f-correct-check-in')){
    const p=personOf(target.dataset.fCorrectCheckIn);
    if(p?.canCorrectCheckIn){
      const body={action:'correct_check_in',pid:p.pid,confirmation_token:p.confirmationTokens.correct_check_in};
      confirmBox('补记已到',`<p>确认 ${esc(p.name)} 已到？</p>`,'确认已到',()=>forecastRun(body));
    }
  }
  else if(target.hasAttribute('data-f-arrive'))forecastArrive(personOf(target.dataset.fArrive));
  else if(target.hasAttribute('data-f-person'))forecastPerson(forecastPoolPerson(target.dataset.fPerson));
  else if(target.hasAttribute('data-f-head'))forecastHead(target.dataset.fHead);
  else if(target.hasAttribute('data-f-add'))forecastAdd();
},true);
document.addEventListener('contextmenu',event=>{const head=event.target.closest('[data-f-head]');if(forecastActive()&&forecastOperator()&&head){event.preventDefault();forecastHead(head.dataset.fHead);}},true);
document.addEventListener('dblclick',event=>{const post=event.target.closest('[data-f-post]');if(!forecastActive()||!post||!forecastPostOperator())return;event.preventDefault();event.stopImmediatePropagation();forecastRenamePost(post);},true);
document.addEventListener('keydown',event=>{
  if(!forecastActive()||!forecastOperator())return;
  if((event.key==='Enter'||event.key===' ')&&event.target.matches('[data-f-head][role="button"]')){
    event.preventDefault();forecastHead(event.target.dataset.fHead);
  }else if(event.key==='Enter'&&event.target.matches('[data-f-post]')){
    event.preventDefault();event.stopImmediatePropagation();forecastRenamePost(event.target);
  }
},true);
document.addEventListener('dragstart',event=>{
  if(!forecastActive()||!forecastOperator())return;
  const post=event.target.closest('[data-f-post]');
  if(post){
    if(!forecastPostOperator()||RT.editingPost){event.preventDefault();return;}
    const [line,index]=post.dataset.fPost.split('#');
    forecastDrag={kind:'post',line,i:Number(index),key:post.dataset.fPostKey,date:RT.data.day.date,snapshot:RT.data.day.forecastSnapshot};
    event.stopImmediatePropagation();RT.dragging=true;event.dataTransfer.effectAllowed='move';
    event.dataTransfer.setData('application/x-rotation-post',JSON.stringify(forecastDrag));event.dataTransfer.setData('text/plain','rotation-post:'+JSON.stringify(forecastDrag));return;
  }
  const p=event.target.closest('[data-f-drag]');if(!p)return;event.stopImmediatePropagation();RT.dragging=true;forecastDrag={kind:'person',pid:p.dataset.fDrag,date:RT.data.day.date,snapshot:RT.data.day.forecastSnapshot};event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('application/x-rotation-person',p.dataset.fDrag);event.dataTransfer.setData('text/plain','rotation-person:'+p.dataset.fDrag);
},true);
document.addEventListener('dragover',event=>{if(forecastActive()&&forecastOperator()&&event.target.closest('[data-f-slot],[data-f-pool]:not([data-f-pool=exception])'))event.preventDefault();},true);
document.addEventListener('dragend',()=>{forecastDrag=null;if(forecastActive()){RT.dragging=false;RT.pressing=false;}},true);
document.addEventListener('drop',event=>{
  if(!forecastActive()||!forecastOperator())return;
  if(forecastDrag&&forecastDrag.date!==RT.data.day.date){forecastDrag=null;toast('轮岗日期已变化，请重新拖拽',true);return;}
  const raw=event.dataTransfer.getData('text/plain');
  const postRaw=event.dataTransfer.getData('application/x-rotation-post')||(raw.startsWith('rotation-post:')?raw.slice(14):'');
  if(postRaw||forecastDrag?.kind==='post'){
    const slot=event.target.closest('[data-f-slot]');let source=forecastDrag;
    event.preventDefault();event.stopImmediatePropagation();forecastDrag=null;RT.dragging=false;RT.pressing=false;
    if(postRaw){try{source=JSON.parse(postRaw);}catch{toast('岗位拖拽信息无效，请重新拖拽',true);return;}}
    if(!slot||!forecastPostOperator()||source?.kind!=='post'){toast('请将岗位拖到另一个岗位',true);return;}
    const [line,index]=slot.dataset.fSlot.split('#');forecastPostSwap(source,line,Number(index),slot.dataset.fSlotKey);return;
  }
  const pid=event.dataTransfer.getData('application/x-rotation-person')||(raw.startsWith('rotation-person:')?raw.slice(16):'')||forecastDrag?.pid;
  if(!pid)return;
  const slot=event.target.closest('[data-f-slot]'),pool=event.target.closest('[data-f-pool]');if(!slot&&!pool)return;
  const dragSnapshot=forecastDrag?.snapshot;
  event.preventDefault();event.stopImmediatePropagation();forecastDrag=null;RT.dragging=false;RT.pressing=false;
  const person=forecastPoolPerson(pid);
  if(!person)return;
  if(slot){
    if(person.rosterSick){toast('请先将班表病假人员移入人员池，再安排上岗',true);return;}
    const [line,i]=slot.dataset.fSlot.split('#');
    if(!forecastMode101Drop(person,line,Number(i),dragSnapshot))forecastPlace(pid,line,Number(i));
  }
  else if(pool.dataset.fPool!=='exception')void forecastPoolMove(person,pool.dataset.fPool);
},true);
