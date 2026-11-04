'use strict';
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs'),net=require('node:net');
const {spawn}=require('node:child_process'),{chromium,webkit,firefox}=require('playwright');
const root=path.resolve(__dirname,'../..'),sleep=ms=>new Promise(r=>setTimeout(r,ms));let server,browser,log='';

async function checkWaitingDispatch(base,browser,cm,op,personal,board,errors){
 const pid='9710000';
 const fixture=async data=>{
  const response=await op.request.post(base+'/__fixture/waiting-dispatch',{data});
  assert(response.ok(),await response.text());
  return response.json();
 };
 const {attraction_id}=await fixture({start:true});
 const screen=await browser.newContext({viewport:{width:1440,height:1000}});
 try{
  assert((await screen.request.post(base+'/api/login',{data:{employee_no:'6666666',password:'1243'}})).ok());
  const screenPage=await screen.newPage();screenPage.on('pageerror',e=>errors.push(e.message));
  const displays=[board,screenPage];
  const mine=async()=>{
   const response=await cm.request.get(base+'/api/rotation-board/me');
   assert(response.ok(),await response.text());
   return (await response.json()).person;
  };
  const {name,readyAt}=await mine();
  const allowedAt=readyAt+6;
  const card=page=>page.locator('.rt-pool .rt-chip').filter({has:page.getByText(name,{exact:true})});
  const phoneTask=personal.locator('[data-formal-rotation-todo]');
  const phoneDepart=phoneTask.locator('[data-rotation-task=depart]');
  const loadDisplays=()=>Promise.all(displays.map(page=>page.evaluate(()=>RT.load())));
  await Promise.all([board.goto(base+'/rotation-board'),screenPage.goto(base+'/rotation/screen'),personal.reload()]);
  await phoneTask.getByText(/休息至/).waitFor();
  for(const page of displays){
   await card(page).waitFor();
   assert.equal(await page.locator('.rt-resting').getByText(name,{exact:true}).count(),1);
   assert.match(await card(page).innerText(),/剩 2 分钟/);
   assert.equal(await card(page).locator('.rt-go,[data-wf-action]').count(),0);
  }
  assert.equal(await phoneDepart.count(),0);
  assert.equal((await mine()).task,null);

  // First let only the display clocks cross the boundary, keeping the last server snapshot.
  for(const page of displays){
   await page.evaluate(({pid,readyAt})=>{
    if(RT.data.day.persons.find(p=>p.pid===pid)?.state!=='rest')throw Error('expected cached rest state');
    RT.data.clock.minute=readyAt;
    RT.render();
   },{pid,readyAt});
   assert.equal(await card(page).count(),1);
   assert.equal(await page.locator('.rt-ready').getByText(name,{exact:true}).count(),1);
   assert.match(await card(page).innerText(),/等待派发/);
  }

  await fixture({attraction_id,minute:readyAt});
  const waiting=await mine();
  assert.equal(waiting.state,'ready');assert.equal(waiting.readyAt,readyAt);
  assert.equal(waiting.task,null);assert.equal(waiting.canDepart,false);
  await Promise.all([loadDisplays(),personal.reload()]);
  await phoneTask.waitFor();
  for(const page of displays){
   assert.equal(await card(page).count(),1);
   assert.equal(await page.locator('.rt-ready').getByText(name,{exact:true}).count(),1);
   assert.match(await card(page).innerText(),/等待派发/);
   assert.doesNotMatch(await card(page).innerText(),/剩 0 分钟|A1|可出发/);
   assert.equal(await card(page).locator('.rt-go').count(),0);
   assert.equal(await card(page).getAttribute('data-wf-action'),null);
  }
  assert.equal(await phoneDepart.count(),0);
  assert.equal((await mine()).task,null);

  await fixture({attraction_id,minute:allowedAt-3});
  const issued=await mine();
  assert.equal(issued.task.status,'assigned');assert.equal(issued.task.allowedAt,allowedAt);
  assert.equal(issued.readyAt,readyAt);assert.equal(issued.canDepart,false);
  const clockText=`${String(Math.floor(allowedAt/60)).padStart(2,'0')}:${String(allowedAt%60).padStart(2,'0')}`;
  await Promise.all([loadDisplays(),personal.reload()]);
  await phoneTask.locator('.rt-confirm-destination').filter({hasText:issued.task.line}).waitFor();
  assert(await phoneDepart.isDisabled());
  for(const page of displays){
   assert.equal(await card(page).count(),1);
   assert.match(await card(page).locator('.rt-go').innerText(),new RegExp(issued.task.line));
   assert((await card(page).innerText()).includes(clockText+' 可出发'));
   assert.equal(await card(page).getAttribute('aria-disabled'),'true');
  }
  const early=await cm.request.post(base+'/api/rotation-board/me/act',{data:{action:'depart',confirmation_token:issued.confirmationTokens.depart}});
  assert.equal(early.status(),400);

  const {day}=await op.request.get(base+'/api/rotation-board/board').then(r=>r.json());
  const close=await op.request.post(base+'/api/rotation-board/act',{data:{action:'line',line:'A1',active:false,
   line_context:{date:day.date,active:true,planRevision:null}}});
  assert(close.ok(),await close.text());
  const rerouted=await mine();
  assert.equal(rerouted.task.line,'B1');
  assert.equal(rerouted.task.allowedAt,allowedAt);assert.equal(rerouted.readyAt,readyAt);
  await Promise.all([loadDisplays(),personal.reload()]);
  await phoneTask.locator('.rt-confirm-destination').filter({hasText:'B1'}).waitFor();
  assert(await phoneDepart.isDisabled());
  for(const page of displays){
   assert.match(await card(page).locator('.rt-go').innerText(),/B1.*B线岗位/);
   assert((await card(page).innerText()).includes(clockText+' 可出发'));
   assert.equal(await card(page).getAttribute('aria-disabled'),'true');
  }

  await fixture({attraction_id,minute:allowedAt});
  assert.equal((await mine()).canDepart,true);
  await Promise.all([loadDisplays(),personal.reload()]);
  await phoneDepart.waitFor();assert(await phoneDepart.isEnabled());
  for(const page of displays)assert.equal(await card(page).getAttribute('aria-disabled'),null);
  await phoneDepart.click();await personal.locator('[data-modal-confirm]').click();
  await phoneTask.locator('.rt-confirm-receipt').getByText(/已确认上岗安排，请前往 B1线/).waitFor();
  for(const page of displays){
   const occupant = page.locator('.rt-lines .rt-who-name').filter({hasText:name});
   await occupant.waitFor();
   assert.equal(await occupant.evaluate(el=>el.firstChild.textContent.trim()),name);
   assert.equal(await card(page).count(),0);
  }
  assert.equal((await mine()).state,'onpost');
 }finally{
  await screen.close();
  await fixture({attraction_id,restore:true});
 }
}

(async()=>{try{
 const sock=net.createServer();await new Promise(r=>sock.listen(0,'127.0.0.1',r));const port=sock.address().port;await new Promise(r=>sock.close(r));
 server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'rotation_formal_fixture.py'),String(port)],{cwd:path.join(root,'backend'),windowsHide:true});server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);
 const base='http://127.0.0.1:'+port;let ready=false;for(let i=0;i<100;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await sleep(300);}assert(ready,log);
 for(const engine of (process.env.ROTATION_WINDOW_ENGINES||'edge').split(',')){
  browser=await(engine==='webkit'?webkit:engine==='firefox'?firefox:chromium).launch(engine==='edge'?{channel:'msedge',headless:true}:{headless:true});
  const cm=await browser.newContext({viewport:{width:390,height:844}}),op=await browser.newContext({viewport:{width:1440,height:1000}}),errors=[];
  for(const [ctx,no] of [[cm,'9710000'],[op,'9710020']]){assert((await ctx.request.post(base+'/api/login',{data:{employee_no:no,password:'formal-fixture-2026'}})).ok());const h=await ctx.request.get(base+'/health').then(r=>r.json());await ctx.request.post(base+'/api/changelog/announcement/read',{data:{version:h.version}});}
  const personal=await cm.newPage(),board=await op.newPage();for(const p of [personal,board])p.on('pageerror',e=>errors.push(e.message));
  const reset=async remaining=>assert((await op.request.post(base+'/__fixture/assignment-window',{data:{remaining}})).ok());
  await reset(4);await personal.goto(base+'/?view=actionCenter');await personal.locator('[data-formal-rotation-todo]').getByText(/休息至/).waitFor();
  assert.equal(await personal.locator('.rt-confirm-destination,.rt-task-comparison,.rt-confirm-routes').count(),0);
  assert(!/A1|B1/.test(await personal.locator('[data-formal-rotation-todo]').innerText()));
  assert.equal(await personal.locator('[data-rotation-task=depart]').count(),0);
  let data=await cm.request.get(base+'/api/rotation-board/me').then(r=>r.json());assert(!data.person.assignmentLocked&&!data.person.canDepart);
  await reset(3);await personal.reload();await personal.locator('[data-formal-rotation-todo]').getByText(/本轮任务已派发/).waitFor();
  data=await cm.request.get(base+'/api/rotation-board/me').then(r=>r.json());assert(data.person.assignmentLocked&&!data.person.canDepart);
  assert(await personal.locator('[data-rotation-task=depart]').isDisabled());
  assert.equal(data.person.state,'rest');
  assert.equal((await op.request.post(base+'/api/rotation-board/act',{data:{action:'reassign',pid:'9710000',line:'B1'}})).status(),400);
  assert((await op.request.post(base+'/api/rotation-board/act',{data:{action:'reassign',pid:'9710010',line:'A1'}})).ok());
  await board.goto(base+'/rotation-board');await board.locator('[data-drag-pid="9710000"]').waitFor();
  assert.equal(await board.locator('.rt-ready [data-drag-pid="9710000"]').count(),1);
  assert.equal(await board.locator('.rt-resting [data-drag-pid="9710000"],.rt-meals [data-drag-pid="9710000"]').count(),0);
  assert.match(await board.locator('[data-drag-pid="9710000"]').innerText(),/剩 3 分钟/);assert.match(await board.locator('[data-drag-pid="9710000"]').innerText(),/A1/);
  for(const width of [320,390,430,768,1440]){await board.setViewportSize({width,height:900});assert.equal(await board.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,engine+' preview overflow '+width);}
  const shots=process.env.ROTATION_WINDOW_SCREENSHOTS;if(shots){fs.mkdirSync(shots,{recursive:true});await board.setViewportSize({width:390,height:844});await board.evaluate(()=>{RT.dragging=true;document.querySelector('.rt-ready .rt-chip').scrollIntoView({block:'center'});});try{await board.screenshot({path:path.join(shots,engine+'-route-preview.png')});}finally{await board.evaluate(()=>{RT.dragging=false;});}await personal.screenshot({path:path.join(shots,engine+'-fixed-personal.png'),fullPage:true});}
  await board.locator('[data-post="A1#0"]').click();
  await board.locator('[data-edit-line]').click();
  const closed=board.waitForResponse(r=>new URL(r.url()).pathname==='/api/rotation-board/act'&&r.request().method()==='POST');
  await board.getByRole('button',{name:'停用',exact:true}).click();
  assert((await closed).ok());
  await board.waitForFunction(()=>!RT.modalOpen&&RT.data.day.lines.find(line=>line.id==='A1')?.active===false);
  await personal.reload();await personal.locator('[data-formal-rotation-todo]').locator('.rt-confirm-destination').filter({hasText:'B1'}).waitFor();
  assert(await personal.locator('[data-rotation-task=depart]').isDisabled());
  data=await cm.request.get(base+'/api/rotation-board/me').then(r=>r.json());assert.equal(data.person.assign.line,'B1');assert(!data.person.assign.blocked);assert(!data.person.canDepart);
  await reset(0);data=await cm.request.get(base+'/api/rotation-board/me').then(r=>r.json());assert(data.person.canDepart,JSON.stringify(data.person));await personal.reload();await personal.locator('[data-rotation-task=depart]').waitFor();
  await personal.locator('[data-rotation-task=depart]').click();await personal.locator('[data-modal-confirm]').click();
  await personal.locator('[data-formal-rotation-todo]').getByText(/已确认上岗安排，请前往 A1线/).waitFor();
  assert.equal((await cm.request.get(base+'/api/rotation-board/me').then(r=>r.json())).person.state,'onpost');
  await checkWaitingDispatch(base,browser,cm,op,personal,board,errors);
  assert.deepEqual(errors,[]);console.log(engine+': exact 3-minute window, closed-line route refresh, widths and continuous dispatch lifecycle across formal board, lounge screen and mobile passed');
  await browser.close();browser=null;
 }
}finally{if(browser)await browser.close();if(server){server.kill();await Promise.race([new Promise(r=>server.once('exit',r)),sleep(3000)]);}}})().catch(e=>{console.error(e);process.exitCode=1;});
