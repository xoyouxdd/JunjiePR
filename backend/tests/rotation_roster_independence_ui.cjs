'use strict';
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs'),net=require('node:net');
const {spawn}=require('node:child_process'),{chromium,webkit,firefox}=require('playwright');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));let server,browser;
(async()=>{try{
 const port=await new Promise(r=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>r(p));});});
 const base='http://127.0.0.1:'+port;let log='';
 server=spawn(path.resolve(__dirname,'../.venv/Scripts/python.exe'),[path.join(__dirname,'rotation_roster_independence_fixture.py'),String(port)],{cwd:path.resolve(__dirname,'..'),stdio:['ignore','pipe','pipe']});
 server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);
 let ready=false;for(let i=0;i<180;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await sleep(250);}assert(ready,log);
 let index=0;
 for(const [name,engine] of [['edge',chromium],['webkit',webkit],['firefox',firefox]]){
  browser=await engine.launch(name==='edge'?{channel:'msedge',headless:true}:{headless:true});
  const context=await browser.newContext(),page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.stack||e.message));
  const no=String(9740300+index),missing=String(9740310+index);
  assert((await context.request.post(base+'/api/login',{data:{employee_no:'9730020',password:'rotation-temp-fixture'}})).ok());
  await page.goto(base+'/rotation-board');await page.locator('[data-tab=roster]').click();
  const file=await context.request.get(base+'/__fixture/roster?index='+index);
  await page.locator('#rosterForm [type=file]').setInputFiles({name:'mixed-synthetic.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:await file.body()});
  await page.locator('#rosterForm [type=submit]').click();await page.locator('[data-include-exceptions]').waitFor();
  assert.match(await page.locator('#rtModal').innerText(),/账号停用/);assert.match(await page.locator('#rtModal').innerText(),/无登录账号/);
  await page.locator(`[data-exception-no="${missing}"]`).uncheck();
  for(const width of [320,390,430,768,1440]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,name+' exception modal overflow '+width);}
  const response=page.waitForResponse(async r=>r.url().endsWith('/api/rotation-board/roster/upload')&&!(await r.json()).preview);
  await page.locator('[data-include-exceptions]').click();const uploaded=await response;assert(uploaded.ok(),await uploaded.text());const result=await uploaded.json();assert.equal(result.entry_count,4);assert.deepEqual(result.skipped,[missing]);await page.locator('[data-include-exceptions]').waitFor({state:'detached'});
  const information=await context.request.get(base+'/__fixture/info?no='+no).then(r=>r.json());const heat=information.circles['热力追踪'],dwarf=information.circles['矮人迷宫'];assert.equal(information.account_enabled,false);
  assert((await context.request.post(base+`/__fixture/live?aid=${heat}&no=${no}`)).ok());
  await context.request.post(base+'/api/logout');await page.goto(base+'/login');await page.locator('[name=employee_no]').fill(no);await page.locator('[name=password]').fill('rotation-existing');await page.locator('[type=submit]').click();
  await page.getByRole('heading',{name:'轮岗待办',exact:true}).waitFor();await page.locator('[data-rotation-task=depart]').waitFor();assert.equal(await page.locator('#tabs').isVisible(),false);assert(!(await page.locator('#app').innerText()).includes('账号停用'));
  const submitted=page.waitForResponse(r=>r.url().endsWith('/api/rotation-board/me/act'));await page.locator('[data-rotation-task=depart]').click();assert.match(await page.getByRole('dialog').innerText(),/当前景点圈：热力追踪/);await page.locator('[data-modal-confirm]').click();assert((await submitted).ok());await page.getByText(/最近安排生效/).waitFor();
  assert.equal((await context.request.get(base+'/api/options')).status(),403);assert.equal((await context.request.get(base+'/__fixture/info?no='+no).then(r=>r.json())).account_enabled,false);
  assert((await context.request.post(base+'/__fixture/source')).ok());assert((await context.request.post(base+`/__fixture/live?aid=${heat}&no=9730001`)).ok());
  await context.request.post(base+'/api/logout');assert((await context.request.post(base+'/api/login',{data:{employee_no:'9730001',password:'rotation-temp-fixture'}})).ok());
  const release=await context.request.get(base+'/api/changelog/announcement').then(r=>r.json());
  await page.goto(base+'/?view=actionCenter');await page.locator('[data-personal-circle]').waitFor();
  if(release.release&&!release.read&&release.release.items?.length){await page.locator('[data-announcement-close]').click();await page.locator('[data-announcement-close]').waitFor({state:'detached'});}
  for(const aid of [dwarf,heat]){
   const refreshed=page.waitForResponse(r=>r.url().includes('/api/rotation-board/me?attraction_id='+aid));await page.locator('[data-personal-circle]').selectOption(String(aid));assert((await refreshed).ok());await page.waitForFunction(aid=>document.querySelector('[data-formal-rotation-todo]')?.dataset.rotationCircle===String(aid)&&!document.querySelector('[data-formal-rotation-todo]')?.dataset.scopeLoading,aid);
  }
  const refreshedAfterSubmit=new Promise(resolve=>{let reads=0;const listener=r=>{if(r.url().endsWith('/api/action-center')&&++reads===2){page.off('response',listener);resolve();}};page.on('response',listener);});
  const crossSubmit=page.waitForResponse(r=>r.url().endsWith('/api/rotation-board/me/act'),{timeout:12000}).then(response=>({response}),error=>({error}));
  await page.locator('[data-rotation-task=depart]').click();
  await page.locator('[data-modal-confirm]').click({timeout:8000});const outcome=await crossSubmit;
  if(outcome.error){console.error(errors,await page.locator('body').innerText());throw outcome.error;}
  const committed=outcome.response;assert(committed.ok(),await committed.text());assert.equal(committed.request().postDataJSON().attraction_id,heat);
  await Promise.race([refreshedAfterSubmit,sleep(12000).then(()=>{throw new Error('action-center did not refresh after confirmation');})]);
  await page.locator('[data-formal-rotation-todo] .rt-personal-task').getByText(/最近安排生效/).waitFor();
  for(const width of [320,375,390,430,768,1440]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,name+' multi circle overflow '+width);}
  const shots=process.env.ROTATION_INDEPENDENCE_SCREENSHOTS;if(shots){fs.mkdirSync(shots,{recursive:true});await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(shots,name+'-cross-circle-mobile.png')});}
  assert.deepEqual(errors,[]);console.log(name+': mixed roster exception selection, disabled rotation-only login, actual confirmation, cross-circle switch and scoped submission, 320-1440px passed');
  index++;await browser.close();browser=null;
 }
}finally{if(browser)await browser.close();if(server){server.kill();await Promise.race([new Promise(r=>server.once('exit',r)),sleep(2500)]);}}})().catch(e=>{console.error(e);process.exitCode=1;});
