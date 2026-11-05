// Real API/browser workflow verification against isolated data only.
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process'),{chromium,webkit,firefox}=require('playwright');
const root=path.resolve(__dirname,'../..'),sleep=ms=>new Promise(r=>setTimeout(r,ms));let server,browser,log='';
const shots=process.env.ROTATION_WORKFLOW_SCREENSHOTS;
(async()=>{try{
 const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
 server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'rotation_formal_fixture.py'),String(port)],{cwd:path.join(root,'backend'),windowsHide:true});server.stderr.on('data',d=>log+=d);server.stdout.on('data',d=>log+=d);
 const base='http://127.0.0.1:'+port;let ready=false;for(let i=0;i<100;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await sleep(500);}assert(ready,log);
 for(const engine of (process.env.ROTATION_WORKFLOW_ENGINES||'edge,webkit,firefox').split(',')){
  browser=await(engine==='webkit'?webkit:engine==='firefox'?firefox:chromium).launch(engine==='edge'?{channel:'msedge',headless:true}:{headless:true});
  const screen=await browser.newContext({viewport:{width:1440,height:1000}}),manager=await browser.newContext({viewport:{width:1440,height:1000}}),mobile=await browser.newContext({viewport:{width:390,height:844}});
  const errors=[];const screenPage=await screen.newPage(),board=await manager.newPage(),phone=await mobile.newPage();for(const [name,p] of [['screen',screenPage],['board',board],['phone',phone]])p.on('pageerror',e=>errors.push(name+': '+e.message));
  const phoneReads=new Set();
  phone.on('request',request=>{if(new URL(request.url()).pathname==='/api/rotation-board/me')phoneReads.add(request);});
  phone.on('requestfinished',request=>phoneReads.delete(request));
  phone.on('requestfailed',request=>phoneReads.delete(request));
  async function reloadPhone(){
   // Pause the old document's polling and let its current read finish before navigation.
   await phone.evaluate(()=>Object.defineProperty(document,'hidden',{configurable:true,get:()=>true}));
   const deadline=Date.now()+15000;
   while(phoneReads.size&&Date.now()<deadline)await sleep(20);
   assert.equal(phoneReads.size,0,'phone polling must drain before reloading');
   await phone.reload();
  }
  for(const [context,no,pw] of [[screen,'6666666','1243'],[manager,'9710020','formal-fixture-2026'],[mobile,'9710000','formal-fixture-2026']])assert((await context.request.post(base+'/api/login',{data:{employee_no:no,password:pw}})).ok());
  if(engine==='edge'){
   const cookie=(await screen.cookies()).map(c=>c.name+'='+c.value).join('; ');
   const streams=[];
   try{
    for(let i=0;i<3;i++){
     const controller=new AbortController();streams.push(controller);
     const response=await fetch(base+'/api/rotation/stream',{headers:{cookie},signal:controller.signal});
     assert(response.ok);
     assert.equal((await response.body.getReader().read()).done,false);
    }
    assert.equal((await manager.request.get(base+'/__fixture/pool').then(r=>r.json())).checked_out,0);
   }finally{for(const controller of streams)controller.abort();}
  }
  await manager.request.post(base+'/__fixture/cohort',{data:{three:true}});
  await screenPage.goto(base+'/rotation/screen');await screenPage.locator('.rt-workflow-board').waitFor();
  assert.equal(await screenPage.locator('[data-wf-101],[data-wf-undo],[draggable=true],[data-wf-rename],[data-adjust-login]').count(),0);
  assert(await screenPage.locator('[data-wf-action=depart]').count()>=3);
  await screenPage.locator('[data-wf-action=depart][data-wf-pid="9710000"]').click();
  await screenPage.locator('.rt-task-comparison').waitFor();
  const screenDialog=screenPage.getByRole('dialog');
  assert.match(await screenDialog.innerText(),/→ 下班/);
  assert.doesNotMatch(await screenDialog.innerText(),/下线人员回休息室后确认休息/);
  await screenPage.locator('[data-close]').click();
  await board.goto(base+'/rotation-board');await board.locator('[data-wf-101]').waitFor();
  const poolMenu=board.locator('[data-pool-person][data-pid="9710000"]');
  await poolMenu.click();
  const poolDialog=board.getByRole('dialog');
  for(const name of ['安排出圈（暂离）','安排到指定岗位','标记病假 / 早退']){
   await poolDialog.getByRole('button',{name,exact:true}).waitFor();
  }
  await poolDialog.getByRole('button',{name:'安排到指定岗位',exact:true}).click();
  await poolDialog.locator('[name=post]').selectOption('A1#1');
  await poolDialog.getByRole('button',{name:'下一步',exact:true}).click();
  await poolDialog.getByRole('heading',{name:'确认强制安排上岗',exact:true}).waitFor();
  await poolDialog.getByRole('button',{name:'取消',exact:true}).click();
  assert.equal(await screenPage.locator('[data-pool-person]').count(),0);
  const label=board.locator('[data-wf-rename]').first(),key=await label.getAttribute('data-wf-rename');await label.dblclick();await board.locator('.rt-inline-post-name').fill('塔台测试改名');await board.locator('.rt-inline-post-name').press('Enter');
  await board.waitForFunction(k=>document.querySelector('[data-wf-rename="'+k+'"]')?.textContent==='塔台测试改名',key);
  await screenPage.waitForFunction(()=>document.querySelector('.rt-lines')?.textContent.includes('塔台测试改名'));
  // Shared task details and before/after are rendered in the real mobile inbox.
  const health=await mobile.request.get(base+'/health').then(r=>r.json());await mobile.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}});
  await phone.goto(base+'/?view=actionCenter');await phone.locator('[data-formal-rotation-todo] .rt-task-comparison').waitFor({timeout:15000});
  const legacy=await mobile.newPage();legacy.on('pageerror',e=>errors.push('legacy: '+e.message));
  await legacy.goto(base+'/rotation-board?personal=1');await legacy.locator('[data-member-act=depart]').waitFor();
  assert(await legacy.locator('[data-member-act=depart]').isEnabled());
  assert.equal(await legacy.locator('.rotation-test-badge').count(),0);
  assert.equal(await phone.locator('.rt-task-columns .rt-task-line').count(),2);
  assert.equal(await phone.locator('[data-confirm-page=depart] .rt-confirm-times strong').count(),2);
  assert.equal(await phone.locator('.rt-task-columns>div').first().locator('.changed').count(),0);
  assert((await phone.locator('.rt-task-columns>div').last().locator('.changed').count())>0);
  for(const width of [320,390,800,1024,1440]){
   await screenPage.setViewportSize({width,height:1000});await board.setViewportSize({width,height:1000});await phone.setViewportSize({width,height:900});
   for(const p of [screenPage,board,phone])assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,engine+' overflow '+width);
  }
  if(engine==='edge'){
   await screenPage.setViewportSize({width:1440,height:1000});
   await phone.setViewportSize({width:390,height:844});
   if(shots){
    fs.mkdirSync(shots,{recursive:true});
    await screenPage.screenshot({path:path.join(shots,'screen.png'),fullPage:true});
    await phone.screenshot({path:path.join(shots,'phone.png'),fullPage:true});
    await phone.locator('[data-confirm-page=depart]').screenshot({path:path.join(shots,'phone-depart.png'),style:'.tabs-mobile{visibility:hidden!important}'});
   }
   // Confirm only this person's task; the other two remain independent.
   await phone.locator('[data-rotation-task=depart]').click();await phone.locator('[data-modal-confirm]').click();
   await screenPage.waitForFunction(()=>RT.data.day.persons.find(p=>p.pid==='9710000').state==='onpost');
   await legacy.waitForFunction(()=>RT.data.person?.state==='onpost');
   assert.equal(await legacy.locator('[data-member-act=depart]').count(),0);
   assert((await legacy.locator('.rt-status').innerText()).includes('连续在岗'));
   assert.equal(await screenPage.evaluate(()=>RT.data.day.persons.find(p=>p.pid==='9710010').state),'ready');
   await board.locator('[data-wf-undo]').click();await board.locator('#rtModal select[name=line]').waitFor();await board.locator('#rtModal button[type=submit]').click();await board.locator('[data-wf-notified]').waitFor();
   assert(await board.locator('#rtModal .rt-task-line').count()>=2);await board.locator('[data-wf-notified]').check();await board.locator('[data-ok]').click();
   await screenPage.waitForFunction(()=>RT.data.day.persons.find(p=>p.pid==='9710000').state==='ready');
  }
  if(engine==='edge'){
   await manager.request.post(base+'/__fixture/cohort',{data:{cross:true}});await board.reload();await board.locator('[data-drag-pid="9710020"]').waitFor();
   await board.locator('[data-drag-pid="9710020"]').dragTo(board.locator('[data-post="B1#0"]'));
   await board.locator('.rt-edit-preview').waitFor();await board.locator('[data-ok]').click();
   await screenPage.waitForFunction(()=>RT.data.day.lines.find(l=>l.id==='B1')?.posts[0].occ==='9710020');
   // The screen can receive the mutation before this page finishes RT.load()
   // and closes its confirmation overlay. Start the next drag only afterward.
   await board.waitForFunction(()=>!RT.modalOpen&&RT.data.day.lines.find(l=>l.id==='B1')?.posts[0].occ==='9710020');
   const key=await board.locator('[data-post="B1#0"] [data-drag-post]').getAttribute('data-wf-rename');
   await board.locator('[data-post="B1#0"] [data-drag-post]').dragTo(board.locator('[data-post="A1#1"]'));
   await board.locator('.rt-edit-preview').waitFor();await board.locator('[data-ok]').click();
   await screenPage.waitForFunction(k=>RT.data.day.lines.find(l=>l.id==='A1')?.posts[1].key===k,key);
  }
  await manager.request.post(base+'/__fixture/cohort',{data:{three:true,hold101:true}});
  await legacy.waitForFunction(()=>!RT.loading);
  await legacy.goto('about:blank');
  await legacy.close();
  await board.reload();await board.locator('[data-wf-101]').waitFor();await board.locator('[data-wf-101]').click();
  await screenPage.waitForFunction(()=>document.querySelectorAll('.holding101').length===2);
  assert.equal(await screenPage.locator('[data-wf-101]').count(),0);
  await board.locator('[data-wf-101]').click();await screenPage.waitForFunction(()=>document.querySelectorAll('.holding101').length===0);
  if(engine==='edge'){
   await manager.request.post(base+'/__fixture/display');await screenPage.reload();await screenPage.locator('.rt-workflow-board .rt-line').nth(8).waitFor();await screenPage.setViewportSize({width:1440,height:1000});
   if(shots)await screenPage.screenshot({path:path.join(shots,'screen.png'),fullPage:true});
  }
  // Real arrival UI, choice eligibility, retained selection, forced meal and idempotent receipt.
  for(const forced of [false,true]){
   const setup=await manager.request.post(base+'/__fixture/room',{data:{forced}});assert(setup.ok(),await setup.text()+'\n'+log);
   await reloadPhone();await phone.locator('[data-confirm-page=room]').waitFor();
   assert.match(await phone.locator('.rt-confirm-origin').innerText(),/A1.*上客1/s);
   assert(await phone.locator('.rt-confirm-page h3').evaluate(el=>parseFloat(getComputedStyle(el).fontSize)>=22));
   const own=await mobile.request.get(base+'/api/rotation-board/me').then(r=>r.json());
   assert.equal(own.person.mealOptions.forced,forced);
   if(!forced){
    assert(own.person.mealOptions.canChoose);
    await phone.locator('[name=formalRotationMeal][value=meal]').check();
    await phone.locator('.rt-confirm-page h3').click();
    await phone.waitForTimeout(5500);
    assert(await phone.locator('[name=formalRotationMeal][value=meal]').isChecked());
   }else{
    assert.equal(await phone.locator('[name=formalRotationMeal][value=rest]').count(),0);
    assert(await phone.locator('[name=formalRotationMeal][value=meal]').isDisabled());
   }
   for(const width of [320,390,736]){
    await phone.setViewportSize({width,height:900});
    assert.equal(await phone.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,engine+' room overflow '+width);
   }
   if(engine==='edge'&&!forced){await phone.setViewportSize({width:390,height:844});if(shots)await phone.locator('[data-confirm-page=room]').screenshot({path:path.join(shots,'phone-room.png'),style:'.tabs-mobile{visibility:hidden!important}'});}
   await phone.locator('[data-rotation-task=arrive]').click();await phone.locator('[data-modal-confirm]').click();
   await phone.getByText('吃饭已开始',{exact:true}).waitFor();
   assert.equal((await mobile.request.get(base+'/api/rotation-board/me').then(r=>r.json())).person.state,'meal');
   assert.equal(await phone.locator('[data-rotation-task=arrive]').count(),0);
  }
  assert((await manager.request.post(base+'/__fixture/room',{data:{forced:false}})).ok());
  await reloadPhone();await phone.locator('[data-confirm-page=room]').waitFor();
  assert(await phone.locator('[name=formalRotationMeal][value=rest]').isChecked());
  await phone.locator('[data-rotation-task=arrive]').click();await phone.locator('[data-modal-confirm]').click();
  await phone.getByText('休息已开始',{exact:true}).waitFor();
  assert.equal((await mobile.request.get(base+'/api/rotation-board/me').then(r=>r.json())).person.state,'rest');
  for(const modalOpen of [false,true]){
   assert((await manager.request.post(base+'/__fixture/room',{data:{forced:false}})).ok());
   await reloadPhone();
   const mealOption=phone.locator('[name=formalRotationMeal][value=meal]');
   await mealOption.check();await mealOption.focus();
   if(modalOpen){
    await phone.locator('[data-rotation-task=arrive]').click();
    await phone.getByRole('dialog').getByText(/本次选择吃饭/).waitFor();
   }else{
    await phone.waitForTimeout(5500);
    assert(await mealOption.evaluate(el=>document.activeElement===el&&el.checked));
   }
   const own=(await mobile.request.get(base+'/api/rotation-board/me').then(r=>r.json())).person;
   const result=await screen.request.post(base+'/api/rotation/screen/act',{data:{action:'arrive',pid:own.pid,confirmation_token:own.confirmationTokens.arrive,meal_choice:'rest',expected_arrival_outcome:null}});
   assert(result.ok(),await result.text());
   await phone.getByText('休息已开始',{exact:true}).waitFor();
   assert.equal(await phone.locator('[data-rotation-task=arrive]').count(),0);
   if(modalOpen)await phone.getByRole('dialog').waitFor({state:'hidden'});
  }
  for(const crossesDuringSubmit of [false,true]){
   const nearEnd=await manager.request.post(base+'/__fixture/room',{data:{near_shift_end:true}});
   assert(nearEnd.ok(),await nearEnd.text());
   const {attraction_id,minute}=await nearEnd.json();
   async function crossBoundary(){
    const clock=await manager.request.post(base+'/__fixture/waiting-dispatch',{data:{attraction_id,minute:minute+1}});
    assert(clock.ok(),await clock.text());
   }
   try{
    await reloadPhone();await phone.locator('[data-rotation-task=arrive]').click();
    const dialog=phone.getByRole('dialog');
    await dialog.getByText(/本次选择休息/).waitFor();
    if(crossesDuringSubmit){
     await phone.route('**/api/rotation-board/me/act',async route=>{await crossBoundary();await route.continue();},{times:1});
     const conflict=phone.waitForResponse(r=>new URL(r.url()).pathname==='/api/rotation-board/me/act');
     await dialog.locator('[data-modal-confirm]').click();
     assert.equal((await conflict).status(),409);
    }else{
     await crossBoundary();
    }
    const updatedDialog=dialog.filter({hasText:/安排已更新.*结束当日轮岗/});
    await updatedDialog.waitFor();
    assert.equal((await mobile.request.get(base+'/api/rotation-board/me').then(r=>r.json())).person.state,'walkback');
    await updatedDialog.locator('[data-modal-confirm]').click();
    await phone.getByText('当日轮岗已结束',{exact:true}).waitFor();
    assert.equal((await mobile.request.get(base+'/api/rotation-board/me').then(r=>r.json())).person.state,'done');
   }finally{
    assert((await manager.request.post(base+'/__fixture/waiting-dispatch',{data:{attraction_id,restore:true}})).ok());
   }
  }
  for(const retry of [false,true]){
   const screenRoom=await manager.request.post(base+'/__fixture/room',{data:{near_shift_end:true}});
   assert(screenRoom.ok(),await screenRoom.text());
   const screenClock=await screenRoom.json();
   try{
    await screenPage.reload();
    await screenPage.locator('[data-wf-pid="9710000"][data-wf-action=arrive]').click();
    const dialog=screenPage.getByRole('dialog');
    await dialog.getByText(/按餐休安排开始计时/).waitFor();
    await screenPage.route('**/api/rotation/screen/act',async route=>{
     const shifted=await manager.request.post(base+'/__fixture/waiting-dispatch',{data:{attraction_id:screenClock.attraction_id,minute:screenClock.minute+1}});
     assert(shifted.ok(),await shifted.text());
     await route.continue();
    },{times:1});
    const response=screenPage.waitForResponse(r=>new URL(r.url()).pathname==='/api/rotation/screen/act');
    await dialog.locator('[data-ok]').click();
    assert.equal((await response).status(),409);
    await dialog.getByText(/确认到达后结束当日轮岗/).waitFor();
    assert.equal((await mobile.request.get(base+'/api/rotation-board/me').then(r=>r.json())).person.state,'walkback');
    const confirm=dialog.locator('[data-ok]:enabled');
    await confirm.waitFor();
    if(retry)await confirm.click();
    await dialog.waitFor({state:'hidden',timeout:12000});
    assert.equal((await mobile.request.get(base+'/api/rotation-board/me').then(r=>r.json())).person.state,retry?'done':'walkback');
   }finally{
    assert((await manager.request.post(base+'/__fixture/waiting-dispatch',{data:{attraction_id:screenClock.attraction_id,restore:true}})).ok());
   }
  }
  const departureSetup=await manager.request.post(base+'/__fixture/departure-hint');
  assert(departureSetup.ok(),await departureSetup.text());
  const departureClock=await departureSetup.json();
  const departurePhone=await mobile.newPage();
  departurePhone.on('pageerror',e=>errors.push('departure phone: '+e.message));
  try{
   await departurePhone.goto(base+'/?view=actionCenter');await screenPage.reload();
   const mobileButton=departurePhone.locator('[data-rotation-task=depart]');
   await mobileButton.click();
   const mobileRoutes=departurePhone.getByRole('dialog').locator('[data-departure-comparison]');
   await mobileRoutes.waitFor();assert.equal(await mobileRoutes.locator('.rt-task-line').count(),2);
   const beforeRoute=await mobile.request.get(base+'/api/rotation-board/me').then(r=>r.json());
   assert(beforeRoute.person.departureRoutes.some(r=>r.to.includes('回休息室')));
   await screenPage.locator('[data-wf-action=depart][data-wf-pid="9710000"]').click();
   const screenRoutes=screenPage.getByRole('dialog').locator('[data-departure-person]');
   assert.match(await screenRoutes.innerText(),/回休息室/);
   const shifted=await manager.request.post(base+'/__fixture/waiting-dispatch',{data:{...departureClock,minute:departureClock.minute+2}});
   assert(shifted.ok(),await shifted.text());
   await screenRoutes.getByText(/→ 下班/).waitFor();
   await screenPage.locator('[data-close]').click();
   const updatedRoute=await mobile.request.get(base+'/api/rotation-board/me').then(r=>r.json());
   assert(updatedRoute.person.departureRoutes.some(r=>r.to==='下班'));
   assert.equal(await mobileRoutes.locator('.rt-task-line').count(),2);
   await departurePhone.getByRole('dialog').locator('[data-modal-confirm]').click();
   await departurePhone.getByText(/最近安排生效.*已确认上岗安排/).waitFor();
  }finally{
   await departurePhone.close();
   assert((await manager.request.post(base+'/__fixture/waiting-dispatch',{data:{attraction_id:departureClock.attraction_id,restore:true}})).ok());
  }
  // A manager handles this persisted legacy timeout; the screen only displays it.
  // The receiver waits in the
  // normal ready area while their real break and fresh departure deadline remain intact.
  const timeoutSetup=await manager.request.post(base+'/__fixture/timeout');assert(timeoutSetup.ok(),await timeoutSetup.text());
  await screenPage.reload();
  await screenPage.locator('.rt-timeout').waitFor();
  assert.equal(await screenPage.locator('.rt-timeout [data-wf-action]').count(),0);
  const received=screenPage.locator('.rt-ready [data-wf-pid="9710010"]');await received.waitFor();
  assert.match(await received.innerText(),/任务转接自/);
  assert.equal(await received.getAttribute('data-wf-action'),'depart');
  assert.equal(await received.getAttribute('aria-disabled'),'true');
  assert.equal(await screenPage.locator('.rt-resting [data-wf-pid="9710010"],.rt-meals [data-wf-pid="9710010"]').count(),0);
  const receiverContext=await browser.newContext({viewport:{width:390,height:844}});
  assert((await receiverContext.request.post(base+'/api/login',{data:{employee_no:'9710010',password:'formal-fixture-2026'}})).ok());
  const release=await receiverContext.request.get(base+'/health').then(r=>r.json());await receiverContext.request.post(base+'/api/changelog/announcement/read',{data:{version:release.version}});
  const receiverPhone=await receiverContext.newPage();receiverPhone.on('pageerror',e=>errors.push('receiver: '+e.message));
  await receiverPhone.goto(base+'/?view=actionCenter');await receiverPhone.locator('[data-confirm-page=depart]').waitFor();
  assert.match(await receiverPhone.locator('.rt-transfer').innerText(),/任务转接自/);
  assert(await receiverPhone.locator('[data-rotation-task=depart]').isDisabled());
  const beforeReceive=await receiverContext.request.get(base+'/api/rotation-board/me').then(r=>r.json());
  assert.equal(beforeReceive.person.state,'rest');
  assert.equal(beforeReceive.person.task.deadlineAt,beforeReceive.person.task.allowedAt+3);
  assert.equal((await receiverContext.request.post(base+'/api/rotation-board/me/act',{data:{action:'depart',confirmation_token:beforeReceive.person.confirmationTokens.depart}})).status(),400);
  await board.reload();await board.locator('.rt-timeout [data-wf-pid="9710000"]').click();await board.locator('[data-ok]').click();
  await screenPage.waitForFunction(()=>!RT.data.day.persons.find(p=>p.pid==='9710000').entryHold);
  assert(await screenPage.evaluate(()=>{const p=RT.data.day.persons.find(p=>p.pid==='9710000');return p.state==='ready'&&p.canDepart&&p.readyAt===RT.data.clock.minute;}));
  const afterReceive=await receiverContext.request.get(base+'/api/rotation-board/me').then(r=>r.json());
  assert.equal(afterReceive.person.task.id,beforeReceive.person.task.id);
  assert.equal(afterReceive.person.readyAt,beforeReceive.person.readyAt);
  assert.equal(afterReceive.person.task.deadlineAt,beforeReceive.person.task.deadlineAt);
  // Stop the polling document before disposing its context. WebKit can report
  // an access-control pageerror when context teardown aborts an in-flight fetch.
  await receiverPhone.goto('about:blank');
  await receiverPhone.waitForLoadState('networkidle');
  await receiverContext.close();
  const waitingSetup=await manager.request.post(base+'/__fixture/timeout',{data:{waiting:true,materialize_before_timeout:true}});
  assert(waitingSetup.ok(),await waitingSetup.text());
  assert.equal((await waitingSetup.json()).materialized_before_reset,true);
  await screenPage.reload();await screenPage.locator('.rt-timeout').waitFor();
  assert.equal(await screenPage.locator('.rt-timeout [data-wf-action]').count(),0);
  const waitingOwner=await mobile.request.get(base+'/api/rotation-board/me').then(r=>r.json());
  await board.reload();await board.locator('.rt-timeout [data-wf-pid="9710000"]').click();await board.locator('[data-ok]').click();
  const reclaimed=screenPage.locator('.rt-ready [data-wf-pid="9710000"]');await reclaimed.waitFor();
  assert.equal(await reclaimed.getAttribute('aria-disabled'),null);
  assert.equal(await reclaimed.locator('.rt-transfer').count(),0);
  const restoredOwner=await mobile.request.get(base+'/api/rotation-board/me').then(r=>r.json());
  assert.equal(restoredOwner.person.task.id,waitingOwner.person.taskTimeout.task);
  assert(restoredOwner.person.canDepart);
  assert.equal(restoredOwner.person.task.allowedAt,restoredOwner.clock.minute);
  assert.equal(restoredOwner.person.task.deadlineAt,restoredOwner.clock.minute+3);
  // Management identities use the same visible card on the shared board.
  // This used to fail with the obsolete "本人确认已到场" restriction.
  assert((await manager.request.post(base+'/__fixture/timeout',{data:{waiting:true,materialize_before_timeout:true}})).ok());
  await board.reload();
  const boardTimeout=board.locator('.rt-timeout [data-wf-pid="9710000"]');await boardTimeout.waitFor();
  await boardTimeout.click();await board.locator('[data-ok]').click();
  await board.waitForFunction(()=>!RT.data.day.persons.find(p=>p.pid==='9710000').entryHold);
  assert(await board.evaluate(()=>{const p=RT.data.day.persons.find(p=>p.pid==='9710000');return p.canDepart&&p.readyAt===RT.data.clock.minute&&p.task.deadlineAt===RT.data.clock.minute+3;}));
  assert.deepEqual(errors,[]);console.log(engine+': confirmation-only screen, isolated permissions, inline rename, personal routes, undo, room/rest/meal confirmations, manager handles legacy timeout, receiver placement and 5 widths passed');
  await browser.close();browser=null;
 }
}finally{if(browser)await browser.close();if(server){server.kill();await Promise.race([new Promise(r=>server.once('exit',r)),sleep(3000)]);}}})().catch(e=>{console.error(e);process.exitCode=1;});
