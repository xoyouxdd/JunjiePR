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
function forecastPath(){return forecastScreen()?'/api/rotation/screen/act':'/api/rotation/act';}
async function forecastSend(body){
  const result=await rtPost(forecastPath(),body);
  await RT.load();
  return result;
}
function forecastRoutes(task){return `<ul>${(task?.routes||[]).map(r=>`<li>${esc(r.name)}：${esc(r.from)} → ${esc(r.to)}</li>`).join('')}</ul>`;}
function forecastOverdue(p,n){
  if(!p.canDepart&&p.state!=='exception')return '';
  // The existing three-minute deadline includes closed-line pauses and delayed
  // openings. Derive elapsed eligible time from it, not the unadjusted break end.
  const deadline=p.exception?.deadlineAt??p.task?.deadlineAt;
  const start=Number.isFinite(deadline)?deadline-3:p.readyAt;
  if(!Number.isFinite(start)||!Number.isFinite(n)||n<start)return '';
  const seconds=Math.floor(Math.max(0,n-start)*60+1e-6);
  return `<span class="rt-forecast-overdue" title="从可上岗起累计，满3分钟进入异常池">超时${Math.floor(seconds/60)}分${pad2(seconds%60)}秒</span>`;
}
function forecastLineCard(L,n,ops){
  const manage=forecastOperator(),editPosts=forecastPostOperator(),personTag=manage?'button':'span';
  return `<div class="rt-line${L.active?'':' off'}"><div class="rt-line-head g-${esc(L.group||'')}" data-f-head="${esc(L.id)}"${manage?' tabindex="0" role="button"':''}><span class="rt-lid">${esc(L.id)}</span><span class="rt-sort-time" title="线路排序时间">${forecastClock(L.sortAt||0)}</span>${L.adding?'<span class="rt-badge">加岗</span>':''}<span>${L.posts.filter(x=>x.open&&!x.hold).length}/${L.posts.length}岗</span></div>${L.posts.map((post,i)=>{
    const p=personOf(post.occ),drag=manage&&p?' draggable="true" data-f-drag="'+esc(p.pid)+'"':'';
    const status=post.openExpired?'加岗已失效':post.prearranged||p?.state==='notyet'?'预排':post.open?(p?'在岗':'空岗'):'未开岗';
    const label=editPosts?`<button type="button" class="rt-pn" draggable="true" data-f-post="${esc(L.id)}#${i}" data-f-post-key="${esc(post.key)}" aria-label="${esc(post.name)}，双击改名，拖拽交换岗位" title="双击改名，拖拽交换岗位">${esc(post.name)}</button>`:`<span class="rt-pn">${esc(post.name)}</span>`;
    return `<div class="rt-post${post.open?'':' closed'}${p?.preparing?' prep':''}${p?.state==='onpost'&&p.batchOnPost!=null?' rt-batch-on-post':''}" data-f-slot="${esc(L.id)}#${i}" data-f-slot-key="${esc(post.key)}">${label}<${personTag}${manage?' type="button"':''} class="rt-who-name"${manage&&p?' data-f-person="'+esc(p.pid)+'"':''}${drag}>${esc(p?.name||'空岗')}${rotationPostShiftEnd(p)}${rotationSevenBadge(p)}${rotationMealBadge(p)}${p?.preparing?'<span class="rt-badge prep">'+preparingText(p.preparing)+'</span>':''}${post.hold?'<span class="rt-badge post-hold">定岗</span>':''}</${personTag}><span class="rt-mins">${status}</span></div>`;
  }).join('')}</div>`;
}
function forecastPools(n){
  const day=RT.data.day,manage=forecastOperator(),confirm=manage||forecastScreen(),personTag=manage?'button':'span',posted=new Set(day.lines.flatMap(l=>l.posts.map(x=>x.occ).filter(Boolean)));
  const people=day.persons.filter(p=>p.upcomingShift||!posted.has(p.pid));
  const definitions=[['pending','待确定休息',p=>['pending','walkback','op'].includes(p.state)],
    ['meal','吃饭区',p=>p.state==='meal'||p.state==='ready'&&p.breakKind==='meal'],
    ['rest','休息区',p=>p.upcomingShift||p.state==='rest'||p.state==='ready'&&p.breakKind!=='meal'],
    ['away','圈外人员',p=>p.state==='away'],['other','其他人员',p=>!p.upcomingShift&&['notyet','other','done','sick','excluded'].includes(p.state)],
    ['exception','异常池',p=>p.state==='exception']];
  return definitions.map(([key,title,filter])=>{
    const list=people.filter(filter);
    return `<section class="rt-sect${list.length?'':' is-empty'}" data-f-pool="${key}"><h2>${title}<span class="rt-n">${list.length}</span>${manage&&!forecastScreen()&&key!=='exception'?'<button type="button" class="secondary rt-pool-add" data-f-add>新增人员</button>':''}</h2><div class="rt-chips${list.length?'':' empty'}">${list.map(p=>{
      const upcoming=p.upcomingShift,ready=!!p.canDepart,rest=['rest','meal','ready'].includes(p.state);
      const a=upcoming?p.previewAssign:p.assign,route=upcoming?a:p.task;
      const mealTail=p.breakKind==='meal_rest'?'饭后休息 · ':'';
      const detail=upcoming?`准备上班 · 距到班 ${Math.max(0,Math.ceil(p.start-n))} 分钟`:p.awaitingShiftEntry?(ready?'准备上班 · 已到班':'准备上班 · 已到班，等待安排'):p.exception?`原预计 ${esc(p.exception.assignment?.line||'')} · ${forecastClock(p.exception.allowedAt)}可上岗 · ${forecastClock(p.exception.at)}超时`:rest?`${mealTail}${Math.max(0,Math.ceil((p.readyAt??n)-n))}分钟 · ${forecastClock(p.readyAt)}可上岗`:p.state==='notyet'?`${forecastClock(p.start)}到班`:p.state==='sick'?'病假':p.state==='done'?'已下班':'';
      return `<div class="rt-chip${ready?' rt-can-board':''}"${manage?' draggable="true" data-f-drag="'+esc(p.pid)+'"':''}><div class="rt-forecast-person-head"><${personTag}${manage?' type="button"':''} class="rt-forecast-name"${manage?' data-f-person="'+esc(p.pid)+'"':''}>${rotationName(p)}</${personTag}>${rotationMealBadge(p)}${forecastOverdue(p,n)}</div><span class="rt-sub">${detail}</span>${confirm&&p.canBatchCheckIn?`<button type="button" class="primary" data-f-check-in="${esc(p.pid)}">已到</button>`:p.batchCheckedIn?'<span class="rt-sub">已签到</span>':''}${a?`<span class="rt-go">预计 ${esc(a.line)} · ${esc(a.why)}${a.batchSize>1?' · 同分钟预计'+a.batchSize+'人':''}</span>`:''}${a&&(!upcoming||route?.routes?.length)?`<details class="rt-forecast-detail"><summary>预计路线</summary>${forecastRoutes(route)}</details>`:''}${confirm&&ready&&p.task?.kind==='push7'?'<span class="rt-sub">请本人在待办确认推7点</span>':confirm&&ready?`<button type="button" class="primary" data-f-board="${esc(p.pid)}">${forecastScreen()?'确认去轮岗':'可上岗'}</button>`:''}${confirm&&['pending','walkback','op'].includes(p.state)?`<button type="button" class="primary" data-f-arrive="${esc(p.pid)}">${forecastScreen()?'去休息':'确认休息／吃饭'}</button>`:''}</div>`;
    }).join('')||'暂无'}</div></section>`;
  }).join('')+(manage&&!forecastScreen()?'<button type="button" class="danger" data-f-close-all>全线关闭</button>':'');
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
        const result=await forecastSend({...body,expected_line:task.line,expected_push7:task.push7Route??null,expected_kind:task.kind});
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
  formBox(`${p.name} · 确认餐休`,'<label>本次安排<select name="kind">'+choices+'</select></label>'+reserve,async values=>{
    const repeat=values.kind==='meal'&&p.ate;
    if(repeat&&!window.confirm('此人已进过吃饭区，是否继续？'))return false;
    try{await forecastSend({action:'arrive',pid:p.pid,confirmation_token:p.confirmationTokens.arrive,meal_choice:values.kind,repeat_meal:repeat});return true;}
    catch(error){toast(error.message,true);return false;}
  });
  activeRotationConfirmation={pid:p.pid,date,token:p.confirmationTokens.arrive,tokenAction:'arrive',refresh:current=>{
    if(JSON.stringify(current.mealOptions)!==mealSignature){closeModal();toast('餐休条件已更新，请重新确认');}
  }};
}
function forecastPerson(p){
  if(!p||!forecastOperator())return;
  const screen=forecastScreen();
  const actions=[['pool','移动到人员池'],...(!screen?[['shift','修改当天班次'],['sick','病假（在岗等待接替）'],['away','安排出圈'],...(p.state==='away'?[['back','回圈，休息15分钟']]:[]),...(['rest','meal','ready'].includes(p.state)?[['break_time','修改餐休剩余时间']]:[]),...(p.state==='onpost'?[['post_hold',p.postHold?'取消持续定岗':'设置持续定岗']]:[])]:[])];
  openModal(`<h3>${esc(p.name)}</h3><p>${forecastClock(p.start)}—${forecastClock(p.end)}</p><div class="rt-actions">${actions.map(([key,label])=>`<button type="button" class="secondary" data-f-operation="${key}">${label}</button>`).join('')}<button type="button" class="ghost" data-close>关闭</button></div>`,host=>{
    host.querySelectorAll('[data-f-operation]').forEach(button=>button.onclick=()=>{
      const action=button.dataset.fOperation,base={action,pid:p.pid};
      if(action==='shift')formBox('修改当天班次',forecastShiftFields(p.start,p.end),v=>forecastRun({...base,...v,start_day_offset:Number(v.start_day_offset)}));
      else if(action==='break_time')formBox('修改剩余时间',`<label>剩余分钟<input type="number" name="remaining" min="0" max="1440" step="0.1" value="${Math.max(0,Math.ceil(p.readyAt-nowMin()))}" required></label>`,v=>forecastRun({...base,remaining:Number(v.remaining)}));
      else if(action==='post_hold')confirmBox(button.textContent,`<p>${esc(p.name)} · ${esc(p.post||'')}</p>`,'确认',()=>forecastRun({...base,line:p.line,i:p.postIndex,on:!p.postHold,reason:'现场持续定岗'}));
      else if(action==='pool')formBox('移动人员池','<label>目标池<select name="pool"><option value="pending">待确定休息</option><option value="rest">休息区</option><option value="meal">吃饭区</option><option value="away">圈外人员</option><option value="other">其他人员</option></select></label>',v=>forecastPoolMove(p,v.pool));
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
function forecastHeadIndex(line){return line?.active?line.posts.findIndex(p=>p.open&&!p.hold&&!p.hold101):-1;}
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
async function forecastPoolMove(p,pool){
  const repeat=pool==='meal'&&p.ate&&!['rest','ready'].includes(p.state);
  if(repeat&&!window.confirm('此人已进过吃饭区，是否继续？'))return false;
  return forecastRun({action:'pool',pid:p.pid,pool,repeat_meal:repeat,person_context:{state:p.state,readyAt:p.readyAt??null,sequence:p.confirmationSequence??0,line:p.state==='onpost'?p.line:null,postIndex:p.postIndex??null}});
}
function forecastPlace(pid,line,i){
  const p=personOf(pid),L=RT.data.day.lines.find(x=>x.id===line),post=L?.posts[i];if(!p||!post)return;
  const sourceLine=RT.data.day.lines.find(x=>x.posts.some(item=>item.occ===pid));
  const sourcePost=sourceLine?.posts.find(item=>item.occ===pid),other=personOf(post.occ);
  const destination=`${esc(line)} · ${esc(post.name)}`;
  const source=sourcePost?`${esc(sourceLine.id)} · ${esc(sourcePost.name)}`:'';
  const swapLabel=other?(sourcePost?'直接交换，两人互换岗位':'直接交换，本人上岗、原岗人员回待确定休息'):'直接放入空岗';
  const swapDetail=`${esc(p.name)}：${source?source+' → ':''}${destination}${other?`<br>${esc(other.name)}：${destination} → ${source||'待确定休息'}`:source?'；原岗位空出。':'；不替下其他人员。'}`;
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
    const lines=[];
    if(i===forecastHeadIndex(L)&&post.occ!==pid&&(mode!=='push'||!recover||
      !L.posts.slice(earlyIndex+1).some(item=>item.open&&!item.hold)))lines.push(L.id);
    if(mode==='swap'&&other&&sourcePost&&sourceLine.posts[forecastHeadIndex(sourceLine)]===sourcePost&&other.pid!==pid)lines.push(sourceLine.id);
    return lines;
  });
}
function forecastHead(line){
  if(!forecastOperator()||forecastScreen())return;
  const L=RT.data.day.lines.find(x=>x.id===line);if(!L)return;
  const lineContext={date:RT.data.day.date,active:L.active,planRevision:RT.data.day.prestartPlanRevision??null};
  openModal(`<h3>${esc(line)} · 线路操作</h3><div class="rt-actions"><button type="button" class="secondary" data-f-sort>修改排序时间</button><button type="button" class="secondary" data-f-open>顺推加岗</button><button type="button" class="secondary" data-f-close>撤岗</button><button type="button" class="secondary" data-f-line>${L.active?'停用':'启用'}线路</button><button type="button" class="ghost" data-close>关闭</button></div>`,host=>{
    host.querySelector('[data-f-sort]').onclick=()=>formBox('修改排序时间',`<label>日期<select name="day_offset">${[0,1,2].map(day=>`<option value="${day}" ${Math.floor((L.sortAt||0)/1440)===day?'selected':''}>${['本工作日','次日','后日'][day]}</option>`).join('')}</select></label><label>排序时间<input type="time" name="time" step="1" value="${forecastClock(L.sortAt||0).split(' ').at(-1)}" required></label>`,v=>forecastRun({action:'sort_time',line,...v,day_offset:Number(v.day_offset)}));
    for(const open of [true,false])host.querySelector(open?'[data-f-open]':'[data-f-close]').onclick=()=>{
      const options=L.posts.map((p,i)=>({p,i})).filter(({p})=>open?!p.open:p.open);
      if(!options.length){toast(open?'没有可加的岗位':'没有开放岗位',true);return;}
      formBox(open?'顺推加岗':'撤岗',`<label>岗位<select name="i">${options.map(({p,i})=>`<option value="${i}">${esc(p.name)}</option>`).join('')}</select></label><label>计划日期<select name="day_offset">${[0,1,2].map(day=>`<option value="${day}" ${Math.floor(nowMin()/1440)===day?'selected':''}>${['本工作日','次日','后日'][day]}</option>`).join('')}</select></label><label>计划时间（留空立即生效）<input type="time" name="time" step="1"></label>${open?'<p class="field-hint">定时加岗到点时线路仍停用，这次计划失效。</p>':''}`,v=>forecastRun({action:'post',line,i:Number(v.i),day_offset:Number(v.day_offset),post_key:L.posts[Number(v.i)].key,open,...(v.time?{[open?'openAt':'closeAt']:v.time}:{}),expected_snapshot:RT.data.day.forecastSnapshot}));
    };
    host.querySelector('[data-f-line]').onclick=()=>confirmBox(L.active?'停用线路':'启用线路',`<p>${lineContext.planRevision?'将调整当天的预排人员；首班开始后须重新核对。':'按当前现场站位调整本线路。'}</p>`,L.active?'停用':'启用',()=>forecastRun({action:'line',line,active:!L.active,line_context:lineContext}));
  });
}
function forecastAdd(){
  let request=0,timer,selected=null;
  openModal('<h3>新增人员</h3><label>姓名或工号<input name="query" autocomplete="off" maxlength="100"></label><div data-f-results aria-live="polite"></div><label><input type="checkbox" name="temporary">新增临时人员（只作轮岗记录）</label>'+forecastShiftFields(null,null,nowMin()>=1440?1:0)+'<div class="rt-actions"><button type="button" class="primary" data-f-save>新增</button><button type="button" class="ghost" data-close>关闭</button></div>',host=>{
    const input=host.querySelector('[name=query]'),results=host.querySelector('[data-f-results]');
    input.oninput=()=>{selected=null;clearTimeout(timer);const generation=++request;
      timer=setTimeout(async()=>{try{const data=await rtApi('/api/rotation/people-search?q='+encodeURIComponent(input.value));if(!host.isConnected||generation!==request)return;
        results.innerHTML=data.people.map((p,i)=>`<button type="button" class="secondary" data-f-result="${i}">${esc(p.name)} · ${esc(p.pid)} · ${esc(p.attraction||'')}</button>`).join('');
        results.querySelectorAll('[data-f-result]').forEach(b=>b.onclick=()=>{selected=data.people[Number(b.dataset.fResult)];results.textContent=selected.name+' · '+selected.pid;});
      }catch(error){if(host.isConnected&&generation===request)results.textContent=error.message;}},200);
    };
    const save=host.querySelector('[data-f-save]');save.onclick=async()=>{const temporary=host.querySelector('[name=temporary]').checked;
      if(!temporary&&!selected){toast('请先选择员工，或勾选临时人员',true);return;}
      save.disabled=true;try{const ok=await forecastRun({action:'add_person',temporary,pid:selected?.pid,name:input.value,start_day_offset:Number(host.querySelector('[name=start_day_offset]').value),start:host.querySelector('[name=start]').value,end:host.querySelector('[name=end]').value});if(ok){++request;clearTimeout(timer);closeModal();}}finally{save.disabled=false;}
    };
  });
}
document.addEventListener('click',event=>{
  if(!forecastActive())return;
  const target=event.target.closest('[data-f-board],[data-f-check-in],[data-f-arrive],[data-f-person],[data-f-head],[data-f-add],[data-f-close-all]');
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
  else if(target.hasAttribute('data-f-arrive'))forecastArrive(personOf(target.dataset.fArrive));
  else if(target.hasAttribute('data-f-person'))forecastPerson(personOf(target.dataset.fPerson));
  else if(target.hasAttribute('data-f-head'))forecastHead(target.dataset.fHead);
  else if(target.hasAttribute('data-f-add'))forecastAdd();
  else confirmBox('全线关闭','所有在岗人员移入待确定休息，包含解除定岗；立即刷新预排。','确认全线关闭',()=>forecastRun({action:'all_close'}));
},true);
document.addEventListener('contextmenu',event=>{const head=event.target.closest('[data-f-head]');if(forecastActive()&&forecastOperator()&&head){event.preventDefault();forecastHead(head.dataset.fHead);}},true);
document.addEventListener('dblclick',event=>{const post=event.target.closest('[data-f-post]');if(!forecastActive()||!post||!forecastPostOperator())return;event.preventDefault();event.stopImmediatePropagation();forecastRenamePost(post);},true);
document.addEventListener('keydown',event=>{if(event.key==='Enter'&&forecastActive()&&forecastPostOperator()&&event.target.matches('[data-f-post]')){event.preventDefault();event.stopImmediatePropagation();forecastRenamePost(event.target);}},true);
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
  const p=event.target.closest('[data-f-drag]');if(!p)return;event.stopImmediatePropagation();RT.dragging=true;forecastDrag={kind:'person',pid:p.dataset.fDrag,date:RT.data.day.date};event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('application/x-rotation-person',p.dataset.fDrag);event.dataTransfer.setData('text/plain','rotation-person:'+p.dataset.fDrag);
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
  event.preventDefault();event.stopImmediatePropagation();forecastDrag=null;RT.dragging=false;RT.pressing=false;
  if(slot){const [line,i]=slot.dataset.fSlot.split('#');forecastPlace(pid,line,Number(i));}
  else if(pool.dataset.fPool!=='exception')void forecastPoolMove(personOf(pid),pool.dataset.fPool);
},true);
