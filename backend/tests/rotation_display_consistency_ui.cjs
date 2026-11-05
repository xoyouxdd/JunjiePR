'use strict';
// Real renderers, synthetic day snapshots, no production requests.
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const {chromium,webkit,firefox}=require('playwright');
const root=path.resolve(__dirname,'../app/static');
const common=fs.readFileSync(path.join(root,'js/rotation-common.js'),'utf8');
const workflow=fs.readFileSync(path.join(root,'js/rotation-workflow.js'),'utf8').split('const workflowTimeoutSeen=')[0];
const source=fs.readFileSync(path.join(root,'js/rotation.js'),'utf8');
const manager=source.slice(0,source.lastIndexOf('(async () => {'));
const personal=fs.readFileSync(path.join(root,'js/app/rotation.js'),'utf8')
 .replace(/^import .*;\r?\n/gm,'').replace(/^export .*;\r?\n?/gm,'')
 .replaceAll('import.meta.url',"'https://example.invalid/rotation.js'");
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
async function phoneView(page,p,minute,extra={}){
 await page.evaluate(({p,minute,extra})=>{
  window.cleanups.splice(0).forEach(fn=>fn());
  window.fixture={taskWorkflow:true,clock:{date:'2026-11-03',minute},person:p,
   confirmations:[{action:'arrive',status:'executed',minute:540,source:'screen'}],rotation_circles:[],...extra};
  const host=document.getElementById('host');host.innerHTML='';
  window.confirmationReply=extra.confirmationReply??{confirmed:false};
  window.confirmationOffline=!!extra.confirmationOffline;
  sessionStorage.removeItem('formalRotation.todo.1');
  if(extra.pendingRecord)sessionStorage.setItem('formalRotation.todo.1',JSON.stringify(extra.pendingRecord));
  mountRotationTasks(host,{isCurrent:()=>true});
 },{p,minute,extra});
 await page.locator('#host .rt-personal-task').waitFor();
 return {html:await page.locator('#host').innerHTML(),text:await page.locator('#host').innerText()};
}
(async()=>{for(const [engine,type,opts]of [['edge',chromium,{channel:'msedge'}],['webkit',webkit,{}],['firefox',firefox,{}]]){
 const browser=await type.launch({...opts,headless:true});
 try{
  const board=await browser.newPage({viewport:{width:390,height:844}}),phone=await browser.newPage({viewport:{width:390,height:844}}),errors=[];
  for(const page of [board,phone])page.on('pageerror',e=>errors.push(e.message));
  await board.setContent('<body data-formal-rotation><main id="rtApp"></main><div id="rtTabs"></div><button id="rtSwitch"></button></body>');
  await board.addStyleTag({content:fs.readFileSync(path.join(root,'css/rotation.css'),'utf8')});
  for(const content of [common,workflow,manager])await board.addScriptTag({content});
  await phone.setContent('<body><link id="formalRotationStyles"><div id="host"></div></body>');
  await phone.addStyleTag({content:fs.readFileSync(path.join(root,'css/rotation.css'),'utf8')});
  await phone.addScriptTag({content:`window.fixture={};window.cleanups=[];window.state={me:{rotation_entry:true,id:1}};
   const storage=new Map();Object.defineProperty(window,'sessionStorage',{value:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)}});
   window.api=async url=>{if(url.includes('/confirmation?')){if(window.confirmationOffline)throw new Error('offline');return structuredClone(window.confirmationReply);}return structuredClone(window.fixture);};window.json=()=>{};window.beginViewRequest=()=>{};
   window.registerPageCleanup=fn=>window.cleanups.push(fn);window.confirmModal=()=>{};window.formModal=()=>{};
   window.refreshActionBadge=()=>{};window.toast=()=>{};window.esc=x=>String(x??'').replaceAll('<','&lt;');window.portalPath=x=>x;`});
  await phone.addScriptTag({content:personal});
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
  await phone.evaluate(({p,pendingRecord})=>{
   cleanups.splice(0).forEach(fn=>fn());fixture={taskWorkflow:true,clock:{date:'2026-11-03',minute:585.75},person:p,confirmations:[],rotation_circles:[]};
   confirmationOffline=true;sessionStorage.setItem('formalRotation.todo.1',JSON.stringify(pendingRecord));document.getElementById('host').innerHTML='';mountRotationTasks(document.getElementById('host'),{isCurrent:()=>true});
  },{p:recovered,pendingRecord});
  await phone.locator('[data-rotation-refresh]').waitFor();
  assert(await phone.evaluate(()=>!!sessionStorage.getItem('formalRotation.todo.1')));
  await phone.evaluate(()=>{confirmationOffline=false;confirmationReply={confirmed:false};document.querySelector('[data-rotation-refresh]').click();});
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
   const b=await phone.evaluate(x=>({minute:hm(x),precise:taskTime(x)}),value);assert.deepEqual(a,b);
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
  assert.deepEqual(errors,[]);
  console.log(engine+': pause/recovery, rest/meal 3/2/1 window, permissions, personal timeout, continuous timing, unverified history, clock seconds and widths 320-1920 passed');
 }finally{await browser.close();}
}})().catch(e=>{console.error(e);process.exitCode=1;});
