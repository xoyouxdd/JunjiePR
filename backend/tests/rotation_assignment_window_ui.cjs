'use strict';
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs'),net=require('node:net');
const {spawn}=require('node:child_process'),{chromium,webkit,firefox}=require('playwright');
const root=path.resolve(__dirname,'../..'),sleep=ms=>new Promise(r=>setTimeout(r,ms));let server,browser,log='';
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
  await reset(4);await personal.goto(base+'/?view=actionCenter');await personal.locator('[data-formal-rotation-todo]').getByText(/预计安排/).waitFor();
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
  assert((await op.request.post(base+'/api/rotation-board/act',{data:{action:'line',line:'A1',active:false}})).ok());
  await personal.reload();await personal.locator('[data-formal-rotation-todo]').locator('.rt-confirm-destination').filter({hasText:'B1'}).waitFor();
  assert(await personal.locator('[data-rotation-task=depart]').isDisabled());
  data=await cm.request.get(base+'/api/rotation-board/me').then(r=>r.json());assert.equal(data.person.assign.line,'B1');assert(!data.person.assign.blocked);assert(!data.person.canDepart);
  await reset(0);data=await cm.request.get(base+'/api/rotation-board/me').then(r=>r.json());assert(data.person.canDepart,JSON.stringify(data.person));await personal.reload();await personal.locator('[data-rotation-task=depart]').waitFor();
  await personal.locator('[data-rotation-task=depart]').click();await personal.locator('[data-modal-confirm]').click();
  await personal.locator('[data-formal-rotation-todo]').getByText(/已确认上岗安排，请前往 A1线/).waitFor();
  assert.deepEqual(errors,[]);console.log(engine+': early preview, exact 3-minute lock, no early departure, mutable teammate, closed-line route refresh, final confirmation and widths passed');
  await browser.close();browser=null;
 }
}finally{if(browser)await browser.close();if(server){server.kill();await Promise.race([new Promise(r=>server.once('exit',r)),sleep(3000)]);}}})().catch(e=>{console.error(e);process.exitCode=1;});
