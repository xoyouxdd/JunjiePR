'use strict';
// Real page/API with delayed responses: preserve the board, scroll, rules and stale-write guards.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),net=require('node:net');
const {spawn}=require('node:child_process'),{chromium,webkit}=require('playwright');
const root=path.resolve(__dirname,'../..'),work=fs.mkdtempSync(path.join(os.tmpdir(),'rotation-refresh-'));
const pause=ms=>new Promise(r=>setTimeout(r,ms));
let server,browser,log='';
(async()=>{try{
 const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
 server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'rotation_daily_fixture.py'),work,String(port)],{cwd:path.join(root,'backend'),windowsHide:true});
 server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);
 const base='http://127.0.0.1:'+port;
 for(let i=0;;i++){try{if((await fetch(base+'/health')).ok)break;}catch{}assert(i<90&&server.exitCode===null,log);await pause(300);}
 for(const engine of (process.env.ROTATION_DAILY_ENGINES||'edge').split(',')){
  browser=await(engine==='webkit'?webkit:chromium).launch(engine==='edge'?{channel:'msedge',headless:true}:{headless:true});
  const context=await browser.newContext({viewport:{width:390,height:844},hasTouch:true});
  assert((await context.request.post(base+'/api/login',{data:{employee_no:'GSMTEST01',password:'daily-fixture-2026'}})).ok());
  assert((await context.request.post(base+'/api/rotation/enter',{data:{account:'7777777'}})).ok());
  const page=await context.newPage(),errors=[],requests=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{const p=new URL(r.url()).pathname;if(p.startsWith('/api/rotation/'))requests.push([r.method(),p]);});
  await page.goto(base+'/rotation');await page.locator('[data-tab=draft]').click();
  await page.locator('[data-daily-date-button="2026-10-06"]').click();
  await page.waitForFunction(()=>daily.data?.date==='2026-10-06'&&!daily.loading);
  await page.evaluate(()=>{const load=RT.load;window.swapBoardLoads=0;RT.load=function(...args){if(new Error().stack.includes('dailySend'))window.swapBoardLoads++;return load(...args);};});
  const data=()=>context.request.get(base+'/api/rotation/daily/2026-10-06').then(r=>r.json());
  let postGate,getGate,faultPost=false,faultRead=false,releasePost,releaseGet;
  await page.route('**/api/rotation/daily/2026-10-06',async route=>{
   if(route.request().method()==='POST'){
    if(postGate)await postGate;
    if(faultPost)return route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({detail:'测试保存失败'})});
   }else{
    if(getGate)await getGate;
    if(faultRead)return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'测试读取失败'})});
   }
   await route.continue();
  });
  async function swap(mode,{postFailure=false,readFailure=false}={}){
   faultPost=postFailure;faultRead=readFailure;
   postGate=new Promise(r=>releasePost=r);getGate=new Promise(r=>releaseGet=r);
   await page.locator(`[data-daily-${mode}="A#0"]`).dragTo(page.locator(`[data-daily-${mode}="B#0"]`));
   await page.getByRole('button',{name:'确认交换',exact:true}).waitFor();
   await page.evaluate(()=>{
    scrollTo({top:700,behavior:'instant'});
    window.swapProbe={node:document.querySelector('[data-daily-person="A#0"]'),y:scrollY,bad:[],active:true};
    function sample(){const p=window.swapProbe;if(!p.active)return;
     if(!document.querySelector('[data-daily-person="A#0"]'))p.bad.push('board missing');
     if(Math.abs(scrollY-p.y)>2)p.bad.push('scroll moved '+scrollY);
     requestAnimationFrame(sample);
    }requestAnimationFrame(sample);
   });
   assert(await page.evaluate(()=>swapProbe.y>100),'test must start below the calendar');
   const checkpoint=requests.length;
   await page.getByRole('button',{name:'确认交换',exact:true}).click();
   await page.waitForFunction(()=>daily.busy);
   await page.evaluate(()=>dailySend({action:'draft_swap_people',source:'A#0',target:'B#0'}));
   await page.waitForTimeout(120);
   assert(await page.evaluate(()=>swapProbe.node.isConnected),'pending save removed the original board');
   assert(await page.locator('[data-daily-person="A#0"]').isDisabled());
   assert.match(await page.locator('[data-daily-selection]').textContent(),/正在保存/);
   releasePost();
   await page.waitForFunction(()=>daily.loading);
   await page.waitForTimeout(120);
   assert(await page.evaluate(()=>swapProbe.node.isConnected),'pending read removed the original board');
   releaseGet();
   await page.waitForFunction(()=>!daily.busy&&!daily.loading&&!RT.modalOpen);
   await page.waitForTimeout(60);
   const probe=await page.evaluate(()=>{swapProbe.active=false;return {bad:swapProbe.bad,y:scrollY,expected:swapProbe.y};});
   if(probe.bad.length)console.error('scroll failure',probe,await page.evaluate(()=>({error:daily.error,loading:daily.loading,busy:daily.busy,status:document.querySelector('[data-daily-status]')?.outerHTML,errorNode:document.querySelector('[data-daily-error]')?.outerHTML})));
   assert.deepEqual(probe.bad,[]);assert.equal(probe.y,probe.expected);
   const writes=requests.slice(checkpoint).filter(([m,p])=>m==='POST'&&p==='/api/rotation/daily/2026-10-06');
   const reads=requests.slice(checkpoint).filter(([m,p])=>m==='GET'&&p==='/api/rotation/daily/2026-10-06');
   assert.equal(writes.length,1);assert.equal(reads.length,1);
   assert.equal(requests.slice(checkpoint).filter(([m,p])=>m==='GET'&&p==='/api/rotation/daily').length,0,'swap must not fetch the month calendar');
   assert.equal(await page.evaluate(()=>swapBoardLoads),0,'swap must not wait for another board load; SSE/polling remains independent');
   postGate=getGate=null;faultPost=faultRead=false;
  }
  for(const width of [390,1440]){
   await page.setViewportSize({width,height:844});
   let before=await data();await swap('person');let after=await data();
   assert.equal(after.draft.plan.crew['A#0'],before.draft.plan.crew['B#0']);
   assert.equal(after.draft.plan.crew['B#0'],before.draft.plan.crew['A#0']);
   before=after;await swap('post');after=await data();
   assert.equal(after.draft.lines[0].posts[0].name,before.draft.lines[1].posts[0].name);
   assert.equal(after.draft.plan.crew['A#0'],before.draft.plan.crew['B#0']);
   assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1));
  }
  let before=await data();await swap('person',{postFailure:true});assert.deepEqual((await data()).draft.plan,before.draft.plan);
  // A competing operator updates the revision; recovery fetches only this day.
  assert((await context.request.post(base+'/api/rotation/daily/2026-10-06',{data:{action:'draft_close',revision:before.revision,close:'20:00'}})).ok());
  await swap('person');assert.deepEqual((await data()).draft.plan,before.draft.plan);
  assert.equal(await page.locator('#draftClose').inputValue(),'20:00');
  // Save succeeds but reconciliation fails: preserve the old board and block edits.
  await swap('person',{readFailure:true});
  assert(await page.locator('[data-daily-person="A#0"]').isDisabled());
  assert.match(await page.locator('[data-daily-error]').textContent(),/刷新班表/);
  await page.locator('[data-daily-reload]').click();await page.waitForFunction(()=>!daily.loading&&!daily.error);
  assert(!await page.locator('[data-daily-person="A#0"]').isDisabled());
  // Switching dates during a delayed save must not paint the saved date back.
  postGate=new Promise(r=>releasePost=r);
  await page.locator('[data-daily-person="A#0"]').dragTo(page.locator('[data-daily-person="B#0"]'));
  await page.getByRole('button',{name:'确认交换',exact:true}).click();await page.waitForFunction(()=>daily.busy);
  await page.evaluate(()=>void loadDaily('2026-10-04','',false));
  await page.waitForFunction(()=>daily.data?.date==='2026-10-04'&&!daily.loading);
  releasePost();await page.waitForFunction(()=>!daily.busy&&!RT.modalOpen);
  assert.equal(await page.evaluate(()=>daily.date),'2026-10-04');
  assert(await page.getByRole('heading',{name:'2026-10-04 · 开班安排回顾'}).isVisible());
  postGate=null;
  assert.deepEqual(errors,[]);
  console.log(engine+': people/posts, mobile/desktop frame-by-frame scroll, retained DOM, one POST + one day GET, single-flight, failure, conflict, read-retry and date-switch race passed');
  await browser.close();browser=null;
 }
}finally{if(browser)await browser.close();if(server){server.kill();await Promise.race([new Promise(r=>server.once('exit',r)),pause(5000)]);}}
})().catch(e=>{console.error(e);process.exitCode=1;});
