// Real API/browser workflow verification against isolated data only.
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process'),{chromium,webkit,firefox}=require('playwright');
const root=path.resolve(__dirname,'../..'),sleep=ms=>new Promise(r=>setTimeout(r,ms));let server,browser,log='';
(async()=>{try{
 const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
 server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'rotation_formal_fixture.py'),String(port)],{cwd:path.join(root,'backend'),windowsHide:true});server.stderr.on('data',d=>log+=d);server.stdout.on('data',d=>log+=d);
 const base='http://127.0.0.1:'+port;let ready=false;for(let i=0;i<100;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await sleep(500);}assert(ready,log);
 for(const engine of (process.env.ROTATION_WORKFLOW_ENGINES||'edge,webkit,firefox').split(',')){
  browser=await(engine==='webkit'?webkit:engine==='firefox'?firefox:chromium).launch(engine==='edge'?{channel:'msedge',headless:true}:{headless:true});
  const screen=await browser.newContext({viewport:{width:1440,height:1000}}),manager=await browser.newContext({viewport:{width:1440,height:1000}}),mobile=await browser.newContext({viewport:{width:390,height:844}});
  const errors=[];const screenPage=await screen.newPage(),board=await manager.newPage(),phone=await mobile.newPage();for(const p of [screenPage,board,phone])p.on('pageerror',e=>errors.push(e.message));
  for(const [context,no,pw] of [[screen,'6666666','1243'],[manager,'9710020','formal-fixture-2026'],[mobile,'9710000','formal-fixture-2026']])assert((await context.request.post(base+'/api/login',{data:{employee_no:no,password:pw}})).ok());
  await manager.request.post(base+'/__fixture/cohort',{data:{three:true}});
  await screenPage.goto(base+'/rotation/screen');await screenPage.locator('.rt-workflow-board').waitFor();
  assert.equal(await screenPage.locator('[data-wf-101],[data-wf-undo],[draggable=true],[data-wf-rename],[data-adjust-login]').count(),0);
  assert(await screenPage.locator('[data-wf-action=depart]').count()>=3);
  await screenPage.locator('[data-wf-action=depart]').first().click();await screenPage.locator('.rt-task-comparison').waitFor();await screenPage.locator('[data-close]').click();
  await board.goto(base+'/rotation-board');await board.locator('[data-wf-101]').waitFor();
  const label=board.locator('[data-wf-rename]').first(),key=await label.getAttribute('data-wf-rename');await label.dblclick();await board.locator('.rt-inline-post-name').fill('塔台测试改名');await board.locator('.rt-inline-post-name').press('Enter');
  await board.waitForFunction(k=>document.querySelector('[data-wf-rename="'+k+'"]')?.textContent==='塔台测试改名',key);
  await screenPage.waitForFunction(()=>document.querySelector('.rt-lines')?.textContent.includes('塔台测试改名'));
  // Shared task details and before/after are rendered in the real mobile inbox.
  const health=await mobile.request.get(base+'/health').then(r=>r.json());await mobile.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}});
  await phone.goto(base+'/?view=actionCenter');await phone.locator('[data-formal-rotation-todo] .rt-task-comparison').waitFor({timeout:15000});
  assert.equal(await phone.locator('.rt-task-columns .rt-task-line').count(),2);
  assert.equal(await phone.locator('[data-confirm-page=depart] .rt-confirm-times strong').count(),2);
  assert.equal(await phone.locator('.rt-confirm-routes>div').count()>0,true);
  for(const width of [320,390,800,1024,1440]){
   await screenPage.setViewportSize({width,height:1000});await board.setViewportSize({width,height:1000});await phone.setViewportSize({width,height:900});
   for(const p of [screenPage,board,phone])assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,engine+' overflow '+width);
  }
  if(engine==='edge'){
   const shots=path.join(root,'.handover_review/rotation-workflow-20261028');await screenPage.setViewportSize({width:1440,height:1000});await screenPage.screenshot({path:path.join(shots,'screen.png'),fullPage:true});await phone.setViewportSize({width:390,height:844});await phone.screenshot({path:path.join(shots,'phone.png'),fullPage:true});await phone.locator('[data-confirm-page=depart]').screenshot({path:path.join(shots,'phone-depart.png'),style:'.tabs-mobile{visibility:hidden!important}'});
   // Confirm only this person's task; the other two remain independent.
   await phone.locator('[data-rotation-task=depart]').click();await phone.locator('[data-modal-confirm]').click();
   await screenPage.waitForFunction(()=>RT.data.day.persons.find(p=>p.pid==='9710000').state==='onpost');
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
   const key=await board.locator('[data-post="B1#0"] [data-drag-post]').getAttribute('data-wf-rename');
   await board.locator('[data-post="B1#0"] [data-drag-post]').dragTo(board.locator('[data-post="A1#1"]'));
   await board.locator('.rt-edit-preview').waitFor();await board.locator('[data-ok]').click();
   await screenPage.waitForFunction(k=>RT.data.day.lines.find(l=>l.id==='A1')?.posts[1].key===k,key);
  }
  await manager.request.post(base+'/__fixture/cohort',{data:{three:true,hold101:true}});
  await board.reload();await board.locator('[data-wf-101]').waitFor();await board.locator('[data-wf-101]').click();
  await screenPage.waitForFunction(()=>document.querySelectorAll('.holding101').length===2);
  assert.equal(await screenPage.locator('[data-wf-101]').count(),0);
  await board.locator('[data-wf-101]').click();await screenPage.waitForFunction(()=>document.querySelectorAll('.holding101').length===0);
  if(engine==='edge'){
   await manager.request.post(base+'/__fixture/display');await screenPage.reload();await screenPage.locator('.rt-workflow-board .rt-line').nth(8).waitFor();await screenPage.setViewportSize({width:1440,height:1000});
   await screenPage.screenshot({path:path.join(root,'.handover_review/rotation-workflow-20261028/screen.png'),fullPage:true});
  }
  // Real arrival UI, choice eligibility, retained selection, forced meal and idempotent receipt.
  for(const forced of [false,true]){
   const setup=await manager.request.post(base+'/__fixture/room',{data:{forced}});assert(setup.ok(),await setup.text()+'\n'+log);
   await phone.reload();await phone.locator('[data-confirm-page=room]').waitFor();
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
   if(engine==='edge'&&!forced){await phone.setViewportSize({width:390,height:844});await phone.locator('[data-confirm-page=room]').screenshot({path:path.join(root,'.handover_review/rotation-workflow-20261028/phone-room.png'),style:'.tabs-mobile{visibility:hidden!important}'});}
   await phone.locator('[data-rotation-task=arrive]').click();await phone.locator('[data-modal-confirm]').click();
   await phone.getByText('吃饭已开始',{exact:true}).waitFor();
   assert.equal((await mobile.request.get(base+'/api/rotation-board/me').then(r=>r.json())).person.state,'meal');
   assert.equal(await phone.locator('[data-rotation-task=arrive]').count(),0);
  }
  assert((await manager.request.post(base+'/__fixture/room',{data:{forced:false}})).ok());
  await phone.reload();await phone.locator('[data-confirm-page=room]').waitFor();
  assert(await phone.locator('[name=formalRotationMeal][value=rest]').isChecked());
  await phone.locator('[data-rotation-task=arrive]').click();await phone.locator('[data-modal-confirm]').click();
  await phone.getByText('休息已开始',{exact:true}).waitFor();
  assert.equal((await mobile.request.get(base+'/api/rotation-board/me').then(r=>r.json())).person.state,'rest');
  // A timed-out owner acknowledges on the screen; the receiver waits in the
  // normal ready area while their real break and fresh departure deadline remain intact.
  const timeoutSetup=await manager.request.post(base+'/__fixture/timeout');assert(timeoutSetup.ok(),await timeoutSetup.text());
  await screenPage.reload();
  const timedOut=screenPage.locator('.rt-timeout [data-wf-pid="9710000"]');await timedOut.waitFor();
  const received=screenPage.locator('.rt-ready [data-wf-pid="9710010"]');await received.waitFor();
  assert.match(await received.innerText(),/任务转接自/);
  assert.equal(await received.getAttribute('data-wf-action'),'depart');
  assert.equal(await received.getAttribute('aria-disabled'),'true');
  assert.equal(await screenPage.locator('.rt-resting [data-wf-pid="9710010"],.rt-meals [data-wf-pid="9710010"]').count(),0);
  const receiverContext=await browser.newContext({viewport:{width:390,height:844}});
  assert((await receiverContext.request.post(base+'/api/login',{data:{employee_no:'9710010',password:'formal-fixture-2026'}})).ok());
  const release=await receiverContext.request.get(base+'/health').then(r=>r.json());await receiverContext.request.post(base+'/api/changelog/announcement/read',{data:{version:release.version}});
  const receiverPhone=await receiverContext.newPage();receiverPhone.on('pageerror',e=>errors.push(e.message));
  await receiverPhone.goto(base+'/?view=actionCenter');await receiverPhone.locator('[data-confirm-page=depart]').waitFor();
  assert.match(await receiverPhone.locator('.rt-transfer').innerText(),/任务转接自/);
  assert(await receiverPhone.locator('[data-rotation-task=depart]').isDisabled());
  const beforeReceive=await receiverContext.request.get(base+'/api/rotation-board/me').then(r=>r.json());
  assert.equal(beforeReceive.person.state,'rest');
  assert.equal(beforeReceive.person.task.deadlineAt,beforeReceive.person.task.allowedAt+3);
  assert.equal((await receiverContext.request.post(base+'/api/rotation-board/me/act',{data:{action:'depart',confirmation_token:beforeReceive.person.confirmationTokens.depart}})).status(),400);
  await timedOut.click();await screenPage.locator('[data-ok]').click();
  await screenPage.waitForFunction(()=>!RT.data.day.persons.find(p=>p.pid==='9710000').entryHold);
  assert(await screenPage.evaluate(()=>{const p=RT.data.day.persons.find(p=>p.pid==='9710000');return p.state==='ready'&&p.canDepart&&p.readyAt===RT.data.clock.minute;}));
  const afterReceive=await receiverContext.request.get(base+'/api/rotation-board/me').then(r=>r.json());
  assert.equal(afterReceive.person.task.id,beforeReceive.person.task.id);
  assert.equal(afterReceive.person.readyAt,beforeReceive.person.readyAt);
  assert.equal(afterReceive.person.task.deadlineAt,beforeReceive.person.task.deadlineAt);
  await receiverContext.close();
  const waitingSetup=await manager.request.post(base+'/__fixture/timeout',{data:{waiting:true,materialize_before_timeout:true}});
  assert(waitingSetup.ok(),await waitingSetup.text());
  assert.equal((await waitingSetup.json()).materialized_before_reset,true);
  await screenPage.reload();await timedOut.waitFor();
  const waitingOwner=await mobile.request.get(base+'/api/rotation-board/me').then(r=>r.json());
  await timedOut.click();await screenPage.locator('[data-ok]').click();
  const reclaimed=screenPage.locator('.rt-ready [data-wf-pid="9710000"]');await reclaimed.waitFor();
  assert.equal(await reclaimed.getAttribute('aria-disabled'),null);
  assert.equal(await reclaimed.locator('.rt-transfer').count(),0);
  const restoredOwner=await mobile.request.get(base+'/api/rotation-board/me').then(r=>r.json());
  assert.equal(restoredOwner.person.task.id,waitingOwner.person.taskTimeout.task);
  assert(restoredOwner.person.canDepart);
  assert.equal(restoredOwner.person.task.allowedAt,restoredOwner.clock.minute);
  assert.equal(restoredOwner.person.task.deadlineAt,restoredOwner.clock.minute+3);
  assert.deepEqual(errors,[]);console.log(engine+': shared screen, isolated permissions, inline rename, personal routes, undo, room/rest/meal confirmations, timed-out screen acknowledgement, receiver placement and 5 widths passed');
  await browser.close();browser=null;
 }
}finally{if(browser)await browser.close();if(server){server.kill();await Promise.race([new Promise(r=>server.once('exit',r)),sleep(3000)]);}}})().catch(e=>{console.error(e);process.exitCode=1;});
