// Real supervisor board and formal API, using the isolated daytime fixture.
const assert=require('node:assert/strict'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process'),{chromium,webkit,firefox}=require('playwright');
const root=path.resolve(__dirname,'../..'),sleep=ms=>new Promise(r=>setTimeout(r,ms));let server,browser,log='';
(async()=>{try{
 const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));const base='http://127.0.0.1:'+port;
 server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'rotation_formal_fixture.py'),String(port)],{cwd:path.join(root,'backend'),windowsHide:true});server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);
 let ready=false;for(let i=0;i<100;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await sleep(300);}assert(ready,log);
 for(const engine of(process.env.ROTATION_TIMEOUT_ENGINES||'edge,webkit,firefox').split(',')){
  browser=await(engine==='webkit'?webkit:engine==='firefox'?firefox:chromium).launch({headless:true,...(engine==='edge'?{channel:'msedge'}:{})});
  const context=await browser.newContext({viewport:{width:390,height:900}}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  assert((await context.request.post(base+'/api/login',{data:{employee_no:'9710020',password:'formal-fixture-2026'}})).ok());
  for(const executed of[true,false]){
   const setup=await context.request.post(base+'/__fixture/related-timeout');assert(setup.ok(),await setup.text());
   await page.goto(base+'/rotation-board');await page.locator('[data-ok]').waitFor();assert.match(await page.locator('#rtModal').innerText(),/强提醒/);
   if(!executed){await page.locator('[data-close]').click();await page.locator('[data-wf-timeout]').click();}
   else await page.locator('[data-ok]').click();
   await page.locator('[data-timeout-form]').waitFor();assert.match(await page.locator('.rt-related-paused').innerText(),/旧详情暂停/);
   assert.equal(await page.locator('.rt-related-paused [data-wf-action]').count(),0);
   if(executed)await page.locator('[data-timeout-root] [name=result]').selectOption('executed');
   for(const width of[320,390,1440]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'overflow '+width);}
   await page.locator('[data-timeout-form] [type=submit]').click();await page.getByRole('heading',{name:'核对纠正与任务顺接',exact:true}).waitFor();
   await page.locator('[data-ok]').click();assert(await page.locator('[data-timeout-notified]').isVisible());
   await page.locator('[data-timeout-notified]').check();await page.locator('[data-ok]').click();
   await page.waitForFunction(()=>RT.data.day.timeoutCases.length===0);
   await page.locator('.rt-timeout-banner').waitFor({state:'detached'});
   assert.equal(await page.locator('.rt-timeout-banner').count(),0);
   const state=await page.evaluate(()=>{const p=RT.data.day.persons.find(p=>p.pid==='9710000'),next=RT.data.day.persons.find(p=>p.pid==='9710010');return {onpost:p.state==='onpost',next:next.task,hold:p.entryHold};});
   assert.equal(state.onpost,executed);assert(state.next&&state.next.deadlineAt===state.next.allowedAt+3);if(!executed)assert(state.hold);
  }
  assert.deepEqual(errors,[]);await browser.close();browser=null;console.log(engine+': strong alert, retained banner, claim, both correction branches, paused cards, preview, notification gate and recovery passed');
 }
}finally{if(browser)await browser.close();if(server){server.kill();await Promise.race([new Promise(r=>server.once('exit',r)),sleep(3000)]);}}})().catch(e=>{console.error(e,log.slice(-2000));process.exitCode=1;});
