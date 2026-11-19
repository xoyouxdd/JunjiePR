'use strict';
// Real renderers, synthetic day snapshots, no production requests.
const assert=require('node:assert/strict');
const {chromium,webkit,firefox}=require('./support/browser-runtime.cjs');
const {openRotationPage,origin,serveRotationFixture}=require('./support/rotation-browser.cjs');
const basePerson={pid:'1',name:'轮岗测试人员',role:'rotation',state:'rest',start:420,end:1080,
 readyAt:585,breakKind:'rest',flags:[],tags:[],visited:[],absences:[],mealOptions:{canChoose:false},
 assign:{line:'D1',entryPost:'河马池',mode:'push',departAt:620},task:null};
const issued=(p,state='assigned')=>({...p,assign:{...p.assign,departAt:585.75},task:{id:'new-task',line:'D1',status:state,
 allowedAt:585.75,deadlineAt:588.75,routes:[{pid:'1',name:p.name,from:'休息区',to:'D1 · 河马池'}],
 beforePosts:[],afterPosts:[]},departureRoutes:[{name:p.name,from:'休息区',to:'D1 · 河马池'}],canDepart:false});
async function boardViews(page,p,minute,extra={}){
 return page.evaluate(({p,minute,extra})=>{
  RT.data={taskWorkflow:true,clock:{date:'2026-11-03',minute,paused:true},settings:{departEarly:1},
   notices:[],confirmations:[],person:p,...extra,day:{date:'2026-11-03',taskWorkflow:1,lines:[],persons:[p]}};
  RT.sync='live';RT.lastSyncedAt=Date.now();
  document.body.classList.remove('rotation-screen');const readonly=workflowPools(minute,false);
  document.body.classList.add('rotation-screen');const screen=workflowPools(minute,false);
  document.body.classList.remove('rotation-screen');
  const board=workflowPools(minute,true),legacy=memberView();
  document.body.classList.add('rotation-screen');
  document.getElementById('rtApp').innerHTML=screen;
  return {board,screen,readonly,legacy,can:canDepartNow(p,minute),time:hm(585.75),precise:taskTime(585.75)};
 },{p,minute,extra});
}
async function openPhone(page){
  const feed={};
  await serveRotationFixture(page,{'/phone':'<!doctype html><meta name="viewport" content="width=device-width"><div id="app"><div id="host"></div></div><nav id="tabs"></nav><div id="toast"></div>'},pathname=>{
   if(pathname==='/api/rotation-board/confirmation')return feed.offline?{status:503,json:{detail:'offline'}}:{json:feed.confirmation};
   if(pathname==='/api/rotation-board/me')return {json:feed.data};
   throw new Error(`Unexpected personal request: ${pathname}`);
  });
  await page.goto(origin+'/phone');
  await page.evaluate(async()=>{
   const {state}=await import('/static/js/app/state.js');
   const {clearPageResources}=await import('/static/js/app/context.js');
   const {mountRotationTasks}=await import('/static/js/app/rotation.js');
   state.me={rotation_entry:true,id:1};
   window.mountFixtureTasks=pendingRecord=>{
    clearPageResources();
    const host=document.getElementById('host');host.innerHTML='';
    sessionStorage.removeItem('formalRotation.todo.1');
    if(pendingRecord)sessionStorage.setItem('formalRotation.todo.1',JSON.stringify(pendingRecord));
    mountRotationTasks(host,{isCurrent:()=>true});
   };
  });
  return feed;
}
async function mountPhone(page,feed,p,minute,extra={}){
  const {confirmationReply,confirmationOffline,pendingRecord,...snapshot}=extra;
  feed.data={taskWorkflow:true,clock:{date:'2026-11-03',minute},person:p,
   confirmations:[{action:'arrive',status:'executed',minute:540,source:'screen'}],rotation_circles:[],...snapshot};
  feed.offline=!!confirmationOffline;
  feed.confirmation=confirmationReply??{confirmed:false};
  await page.evaluate(pending=>window.mountFixtureTasks(pending),pendingRecord);
}
async function readPhone(page){
 await page.locator('#host .rt-personal-task').waitFor();
 return {html:await page.locator('#host').innerHTML(),text:await page.locator('#host').innerText()};
}

async function checkBreakBoundary(board) {
 for(const width of [1920,390]){
  await board.setViewportSize({width,height:1000});
  for(const kind of ['rest','meal','meal_rest'])for(const ops of [true,false]){
   const state=kind==='meal'?'meal':'rest';
   await board.evaluate(({state,kind,ops})=>{
    document.body.classList.add('rotation-screen');
    const person=window.breakFixturePerson={pid:'break-test',name:'餐休测试',role:'rotation',state,
     start:420,end:1080,readyAt:585,breakStart:540,breakKind:kind,flags:[],tags:[],
     assign:{line:'A1',entryPost:'塔台',mode:'push',departAt:620},task:null};
    RT.data={day:{date:'2026-11-03',taskWorkflow:true,lines:[],persons:[person]},settings:{departEarly:1}};
    RT.lastSyncedAt=Date.now();RT.sync='live';
    window.drawBreakFixture=minute=>{document.getElementById('rtApp').innerHTML=workflowPools(minute,ops);};
    window.drawBreakFixture(540);
   },{state,kind,ops});
   const original=board.locator(`${state==='meal'?'.rt-meals':'.rt-resting'} .rt-chip`);
   const waiting=board.locator('.rt-ready .rt-chip');
   const depart=board.locator('.rt-ready [data-wf-action=depart]');
   assert.equal(await original.count(),1);
   assert.equal(await original.locator('.rt-go,.rt-task-summary').count(),0);
   assert.match(await original.innerText(),/剩 45 分钟/);
   assert.equal(await waiting.count(),0);
   // The clock reaches the break end both before and after the server sets ready.
   for(const ready of [false,true]){
    await board.evaluate(ready=>{if(ready)window.breakFixturePerson.state='ready';window.drawBreakFixture(585);},ready);
    assert.equal(await original.count(),0);
    assert.equal(await waiting.count(),1);
    assert.match(await waiting.innerText(),/等待派发/);
    assert.equal(await waiting.locator('.rt-go').count(),0);
    assert.equal(await board.locator('.rt-ready [data-wf-action]').count(),0);
    assert.doesNotMatch(await waiting.innerText(),/塔台|可出发/);
   }
   await board.evaluate(()=>{
    Object.assign(window.breakFixturePerson,{task:{status:'assigned',allowedAt:620,deadlineAt:623,routes:[]},canDepart:false});
    window.drawBreakFixture(617);
   });
   assert.match(await waiting.innerText(),/塔台.*10:20/s);
   assert.doesNotMatch(await waiting.innerText(),/餐休剩 0 分钟/);
   assert.equal(await depart.getAttribute('aria-disabled'),'true');
   await board.evaluate(()=>{window.breakFixturePerson.canDepart=true;window.drawBreakFixture(620);});
   assert.equal(await depart.getAttribute('aria-disabled'),null);
   await board.evaluate(state=>{
    const p=window.breakFixturePerson;
    p.assign.departAt=585;
    Object.assign(p,{state,task:{status:'assigned',allowedAt:585,deadlineAt:588,routes:[]},canDepart:false});
    window.drawBreakFixture(582);
   },state);
   assert.equal(await original.count(),0);
   assert.equal(await waiting.count(),1);
   for(const [now,remaining] of [[582,3],[583,2],[584,1]]){
    await board.evaluate(minute=>window.drawBreakFixture(minute),now);
    assert.ok((await waiting.innerText()).includes('餐休剩 '+remaining+' 分钟'));
    assert.equal(await depart.getAttribute('aria-disabled'),'true');
   }
   await board.evaluate(()=>{
    Object.assign(window.breakFixturePerson,{state:'ready',canDepart:true});
    window.drawBreakFixture(585);
   });
   assert.equal(await depart.getAttribute('aria-disabled'),null);
   assert.equal(await waiting.locator('.rt-go').count(),1);
  }
 }
}

async function checkWaitingNotices(board) {
 await board.setViewportSize({width:1920,height:1080});
 const result=await board.evaluate(()=>{
  const host=document.getElementById('rtApp');
  host.innerHTML='<div id="fixtureAlerts"></div><section id="fixtureBoard">轮岗岗位与人员</section>';
  const alerts=document.getElementById('fixtureAlerts'),lineBoard=document.getElementById('fixtureBoard');
  const waiting=Array.from({length:500},(_,i)=>({line:['A1','A2','B1','B2','C2'][i%5],id:'wait-'+i}));
  const day={timeoutCases:[],timeoutWaitingMatch:waiting};
  const before=JSON.stringify(day),tops=[];
  for(const ops of [true,false])for(const timeoutWaitingMatch of [[],waiting]){
   alerts.innerHTML=workflowTimeoutBanner({...day,timeoutWaitingMatch},ops);
   if(alerts.innerHTML)throw new Error('Waiting bookkeeping was rendered');
   tops.push(lineBoard.getBoundingClientRect().top);
  }
  const realCase={id:'real-case',line:'A1',people:[{pid:'1'}],handler:null};
  RT.data.day={timeoutCases:[realCase],timeoutWaitingMatch:[{line:'A2'}]};
  alerts.innerHTML=workflowTimeoutBanner(RT.data.day,false);
  return {tops,unchanged:JSON.stringify(day)===before};
 });
 assert.ok(result.tops.every(y=>y===result.tops[0]));
 assert.equal(result.unchanged,true);
 assert.equal(await board.locator('.rt-timeout-waiting').count(),0);
 assert.equal(await board.locator('.rt-timeout-banner').count(),1);
 assert.equal(await board.locator('[data-wf-timeout]').count(),0);
 await board.evaluate(()=>{
  RT.modalOpen=true;
  document.getElementById('fixtureAlerts').innerHTML=workflowTimeoutBanner(RT.data.day,true);
 });
 assert.equal(await board.locator('[data-wf-timeout]').count(),1);
 assert.equal(await board.locator('[data-wf-timeout]').getAttribute('data-wf-timeout'),'real-case');
 assert.equal(await board.locator('#rtModal [role=dialog]').count(),0);
}
(async()=>{for(const engine of (process.env.ROTATION_DISPLAY_ENGINES||'edge,webkit,firefox').split(',')){
 const type={edge:chromium,webkit,firefox}[engine];
 const browser=await type.launch({headless:true,...(engine==='edge'?{channel:'msedge'}:{})});
 try{
  const board=await browser.newPage({viewport:{width:390,height:844}}),phone=await browser.newPage({viewport:{width:390,height:844}}),errors=[];
  for(const page of [board,phone])page.on('pageerror',e=>errors.push(e.message));
  await openRotationPage(board,'/rotation-board');
  await board.evaluate(()=>{RT.load=async()=>{};RT.render=()=>{};});
  const feed=await openPhone(phone);
  const phoneView=async(page,p,minute,extra)=>{await mountPhone(page,feed,p,minute,extra);return readPhone(page);};
  for(const state of ['rest','meal']){
   const p={...basePerson,state};const v=await boardViews(board,p,540),f=await phoneView(phone,p,540);
   for(const html of [v.board,v.screen,v.legacy,f.html]){
    assert(!html.includes('河马池'));assert(!html.includes('10:20'));assert.match(html,/45 (分钟|分)/);
   }
   assert(!v.readonly.includes('data-wf-action'));
   const t=issued(p);
   for(const [now,remaining]of [[582.75,3],[583.75,2],[584.75,1]]){
    const v=await boardViews(board,t,now),f=await phoneView(phone,t,now);
    for(const html of [v.board,v.screen,v.legacy,f.html]){
     assert(html.includes('河马池'));assert(html.includes('09:45:45'));assert(html.includes('09:48:45'));
     assert.match(html,new RegExp('餐休剩 '+remaining+' 分钟'));
    }
    assert(!v.can);assert(v.screen.includes('aria-disabled="true"'));
    assert(await phone.locator('[data-rotation-task=depart]').isDisabled());
   }
   const ready={...t,state:'ready',canDepart:true};
   assert((await boardViews(board,ready,585.75)).can);
   await phoneView(phone,ready,585.75);assert(await phone.locator('[data-rotation-task=depart]').isEnabled());
  }
  for(const flag of ['timeoutCase','timeoutPending','heldTaskOnly']){
   const p=issued(basePerson,'exception_hold');if(flag!=='heldTaskOnly')p[flag]={line:'D1',id:'case'};
   const v=await boardViews(board,p,583),f=await phoneView(phone,p,583);
   for(const html of [v.screen,v.board,v.legacy,f.html]){
    assert(html.includes('关联任务待处理'));assert(html.includes('暂不计确认超时'));
    assert(!html.includes('河马池'));assert(!html.includes('到现场后，按这条路线接替'));
    assert(!/data-(wf-action|member-act|rotation-task)=/.test(html));
   }
   // A previous departure receipt must not turn a paused on-post person into an instruction card.
   await phoneView(phone,{...p,state:'onpost',line:'D1',post:'河马池'},583,
    {confirmations:[{action:'depart',status:'executed',minute:580,line:'D1',post:'河马池'}]});
   assert((await phone.locator('#host').innerText()).includes('旧详情暂停执行'));
  }
  // New task after recovery replaces the paused view, without requiring another phone acknowledgement.
  const recovered=issued({...basePerson,state:'ready'});recovered.canDepart=true;
  await phoneView(phone,recovered,585.75);assert(await phone.locator('[data-rotation-task=depart]').isEnabled());
  const pendingRecord={date:'2026-11-03',body:{action:'depart',pid:'1',confirmation_token:'old-token'}};
  for(const reply of [{confirmed:false},{confirmed:false,result:{status:'reversed'}},{confirmed:false,result:{status:'superseded'}},{confirmed:true,result:{action:'entry_return',status:'executed',source:'screen'}}]){
   await phoneView(phone,recovered,585.75,{pendingRecord,confirmationReply:reply});
   assert.equal(await phone.evaluate(()=>sessionStorage.getItem('formalRotation.todo.1')),null);
   assert.equal(await phone.locator('[data-rotation-retry]').count(),0);
   assert(await phone.locator('[data-rotation-task=depart]').isEnabled());
  }
  // Unknown transport result remains pending; an authoritative negative result clears it on reconnect.
  await mountPhone(phone,feed,recovered,585.75,{confirmations:[],pendingRecord,confirmationOffline:true});
  await phone.locator('[data-rotation-refresh]').waitFor();
  assert(await phone.evaluate(()=>!!sessionStorage.getItem('formalRotation.todo.1')));
  feed.offline=false;
  await phone.locator('[data-rotation-refresh]').click();
  await phone.locator('[data-rotation-task=depart]').waitFor();
  assert.equal(await phone.locator('[data-rotation-retry]').count(),0);
  const hold={...basePerson,state:'ready',assign:null,entryHold:{reason:'任务超时'},taskTimeout:{task:'t'},confirmationTokens:{entry_return:'token'}};
  const v=await boardViews(board,hold,600),f=await phoneView(phone,hold,600);
  assert(v.screen.includes('任务超时'));assert(!v.screen.includes('data-wf-action="entry_return"'));
  assert(!v.readonly.includes('data-wf-action="entry_return"'));
  assert(v.board.includes('data-wf-action="entry_return"'));assert(v.legacy.includes('data-member-act="entry_return"'));
  assert(f.html.includes('data-rotation-task="entry_return"'));
  for(const html of [v.screen,v.legacy,f.html])assert(html.includes('不新增休息'));
  const acknowledgement={action:'entry_return',status:'executed',minute:585.75,state:'ready',readyAt:585.75,source:'supervisor'};
  const acknowledgedLegacy=await boardViews(board,recovered,585.75,{confirmations:[acknowledgement]});
  const acknowledgedPhone=await phoneView(phone,recovered,585.75,{confirmations:[acknowledgement]});
  for(const html of [acknowledgedLegacy.legacy,acknowledgedPhone.html]){
   assert(html.includes('已恢复可分配状态'));assert(!html.includes('已到休息室'));assert(!html.includes('已确认到达休息室'));
  }
  for(const unknown of [false,true]){
   const p={...basePerson,state:'onpost',line:'D1',post:'河马池',assign:null,continuousStart:500,lineStart:590,timingUnverified:unknown};
   const v=await boardViews(board,p,600),f=await phoneView(phone,p,600,{confirmations:[{action:'depart',status:'executed',source:'supervisor',minute:500,line:'D1',post:'河马池'}]});
   const posted=await board.evaluate(p=>{RT.data.day.persons=[p];return lineCard({id:'D1',active:true,posts:[{name:p.post,key:'k',open:true,occ:p.pid}]},600,false);},p);
   assert(v.legacy.includes('100 分钟'));assert(f.text.includes('100 分钟'));assert(posted.includes('100 分'));
   for(const html of [posted,v.legacy,f.html])assert.equal(html.includes('历史时间待核实'),unknown);
   assert(!f.html.includes('data-rotation-task="depart"')); // manager/screen confirmation is already effective
  }
  for(const value of [null,0,585.75,1439.999,1440,-.25]){
   const a=await board.evaluate(x=>({minute:hm(x),precise:taskTime(x)}),value);
   const p=issued({...recovered,end:value},'assigned');
   p.task.allowedAt=value;
   await phoneView(phone,p,585.75);
   assert.equal(await phone.locator('.rt-personal-identity > span').innerText(),a.minute);
   assert.equal(await phone.locator('.rt-confirm-times strong').first().innerText(),a.precise);
  }
  const reliefFields=await board.evaluate(()=>{
   const persons={};for(const pid of ['1','2','3','4','5','6'])persons[pid]={pid,name:'人员'+pid,role:'rotation',start:Number(pid)<=3?420:435,end:1080};
   const draft={date:'2026-11-03',draftAlerts:[],persons,lines:[{id:'A',posts:[{name:'迎宾',seven:1},{name:'快迎',seven:2},{name:'小C',seven:3}]}],
    plan:{seven:{'A#0':'1','A#1':'2','A#2':'3'},crew:{},push7:['5','6'],push7Targets:{'5':'2','6':'3'},lost:[]}};
   const host=document.createElement('div');host.innerHTML=initialDraftView({date:draft.date,draft,can_edit:true});
   return [...host.querySelectorAll('[data-list=push7]')].map(x=>[x.dataset.target,x.value,x.parentElement.textContent]);
  });
  assert.deepEqual(reliefFields.map(x=>x.slice(0,2)),[['1',''],['2','5'],['3','6']]);
  assert(reliefFields.every(x=>x[2].includes('接替 人员'+x[0])));
  // Neither an estimate nor an old reversed receipt may be offered as an active task.
  const empty={...basePerson,state:'ready'};
  const noTask=await boardViews(board,empty,625);assert(!noTask.can);assert(!noTask.legacy.includes('data-member-act="depart"'));
  const noReceipt=await phoneView(phone,empty,625,{confirmations:[{action:'depart',status:'reversed',line:'OLD',post:'旧岗位'}]});
  assert(!noReceipt.text.includes('旧岗位'));assert(!noReceipt.html.includes('data-rotation-task="depart"'));
  for(const width of [320,390,800,1920]){
   for(const page of [board,phone])await page.setViewportSize({width,height:900});
   const p={...basePerson,state:'onpost',assign:null,line:'D1',post:'河马池',continuousStart:500,lineStart:590,timingUnverified:true};
   await board.evaluate(p=>{RT.data.day={date:'2026-11-03',taskWorkflow:1,persons:[p],lines:[{id:'D1',active:true,posts:[{name:p.post,key:'k',open:true,occ:p.pid}]}]};document.getElementById('rtApp').innerHTML=workflowBoardContent(RT.data.day,600,false);},p);
   await phoneView(phone,p,600);
   for(const page of [board,phone])assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,engine+' overflow '+width);
  }
  if(engine!=='firefox'){
   await checkBreakBoundary(board);
   await checkWaitingNotices(board);
  }
  assert.deepEqual(errors,[]);
  console.log(engine+': pause/recovery, rest/meal 3/2/1 window, permissions, personal timeout, continuous timing, unverified history, clock seconds and widths 320-1920 passed');
 }finally{await browser.close();}
}})().catch(e=>{console.error(e);process.exitCode=1;});
