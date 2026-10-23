'use strict';
// Real isolated API + browser: no production data, service or credentials.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),net=require('node:net');
const {spawn}=require('node:child_process');
const {chromium,webkit,firefox}=require('playwright');
const root=path.resolve(__dirname,'../..'),review=path.join(root,'.handover_review');fs.mkdirSync(review,{recursive:true});const work=fs.mkdtempSync(path.join(review,'daily-ui-'));
const shots=process.env.ROTATION_DAILY_SCREENSHOTS;
let server,browser,log='';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function main(){try{
  const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
  server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'rotation_daily_fixture.py'),work,String(port)],{cwd:path.join(root,'backend'),windowsHide:true});
  server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);
  const base='http://127.0.0.1:'+port;
  let ready=false;for(let i=0;i<90;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await sleep(500);}assert(ready,log);
  for(const engine of (process.env.ROTATION_DAILY_ENGINES||'edge').split(',')){
    browser=await(engine==='webkit'?webkit:engine==='firefox'?firefox:chromium).launch(engine==='edge'?{channel:'msedge',headless:true}:{headless:true});
    const context=await browser.newContext({viewport:{width:390,height:844},hasTouch:true});
    assert((await context.request.post(base+'/api/login',{data:{employee_no:'GSMTEST01',password:'daily-fixture-2026'}})).ok());
    assert((await context.request.post(base+'/api/rotation/enter',{data:{account:'7777777'}})).ok());
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/rotation');await page.locator('[data-tab=draft]').click();
    await page.locator('[data-daily-date-button="2026-10-06"]').click();
    await page.locator('[data-daily-person="A#0"]').waitFor({timeout:10000}).catch(async e=>{console.error(await page.locator('body').innerText(),errors);throw e;});
    async function data(){const r=await context.request.get(base+'/api/rotation/daily/2026-10-06');assert(r.ok());return r.json();}
    async function choose(a,b){await page.locator(`[data-daily-person="${a}"]`).click();await page.locator(`[data-daily-person="${b}"]`).click();await page.getByRole('button',{name:'确认交换',exact:true}).click();await page.waitForFunction(()=>!daily.busy&&!daily.loading&&!RT.loading);}
    let before=await data();await choose('A#0','B#0');
    await page.waitForFunction(pid=>document.querySelector('[data-draft][data-key="A#0"]')?.value===pid,before.draft.plan.crew['B#0']);
    let after=await data();assert.equal(after.draft.plan.crew['A#0'],before.draft.plan.crew['B#0']);
    assert.equal(after.draft.plan.crew['B#0'],before.draft.plan.crew['A#0']);
    if(engine==='edge'){
      await page.locator('[data-daily-post="A#0"]').dragTo(page.locator('[data-daily-post="A#1"]'));
      await page.getByRole('button',{name:'确认交换',exact:true}).click();
      await page.waitForFunction(()=>document.querySelector('[data-daily-post="A#0"]')?.textContent==='安全观察');
      await page.waitForFunction(()=>!daily.busy&&!daily.loading&&!RT.loading);
      // Real touch events with pointer capture and drop hit testing.
      await page.setViewportSize({width:390,height:1200});
      const source=page.locator('[data-daily-person="A#0"]'),target=page.locator('[data-daily-person="A#1"]');
      await source.scrollIntoViewIfNeeded();const start=await source.boundingBox(),point=await target.boundingBox();
      const cdp=await context.newCDPSession(page);
      await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:start.x+20,y:start.y+20}]});
      await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:point.x+20,y:point.y+20}]});
      await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
      await page.getByRole('button',{name:'确认交换',exact:true}).click();await page.waitForFunction(()=>!daily.busy&&!daily.loading&&!RT.loading);
    }
    for(const width of [320,360,390,430,768,1440]){
      await page.setViewportSize({width,height:900});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,engine+' overflow '+width);
      assert(await page.locator('.rt-daily-slot button,.rt-daily-calendar button').evaluateAll(nodes=>nodes.every(n=>n.getBoundingClientRect().height>=43)),engine+' touch target '+width);
    }
    if(shots){fs.mkdirSync(shots,{recursive:true});await page.setViewportSize({width:390,height:844});await page.locator('#toast').evaluate(el=>el.classList.remove('show'));await page.screenshot({path:path.join(shots,engine+'-daily-mobile.png'),fullPage:true});await page.locator('.rt-daily').screenshot({path:path.join(shots,engine+'-daily-calendar.png')});await page.setViewportSize({width:1440,height:1000});await page.screenshot({path:path.join(shots,engine+'-daily-desktop.png'),fullPage:true});}
    // A stale editor must not overwrite another operator's saved plan.
    before=await data();assert((await context.request.post(base+'/api/rotation/daily/2026-10-06',{data:{action:'draft_close',revision:before.revision,close:'20:00'}})).ok());
    await choose('A#0','B#0');await page.waitForFunction(()=>document.querySelector('#draftClose')?.value==='20:00');
    after=await data();assert.deepEqual(after.draft.plan,before.draft.plan,'stale write changed assignments');
    const historicalBefore=await context.request.get(base+'/api/rotation/daily/2026-10-04').then(r=>r.json());
    await page.locator('[data-daily-date]').fill('2026-10-04');await page.locator('[data-daily-date]').dispatchEvent('change');await page.locator('[data-daily-time]').waitFor();
    await page.locator('[data-daily-time]').fill('07:00');await page.locator('[data-daily-time]').dispatchEvent('change');
    await page.getByRole('heading',{name:'2026-10-04 · 轮岗回看'}).waitFor();assert.equal(await page.locator('[data-daily-person]').count(),0);
    const historicalAfter=await context.request.get(base+'/api/rotation/daily/2026-10-04').then(r=>r.json());
    assert.equal(historicalBefore.revision,historicalAfter.revision,'history modified live state');
    // Member and screen identities retain the original initialization restriction.
    await context.request.post(base+'/api/rotation/enter',{data:{account:'9100000'}});
    assert.equal((await context.request.get(base+'/api/rotation/daily?month=2026-10')).status(),403);
    assert.equal((await context.request.post(base+'/api/rotation/daily/2026-10-06',{data:{action:'draft_generate',revision:after.revision}})).status(),403);
    assert.deepEqual(errors,[]);console.log(engine+': daily calendar, future plan, swaps, stale editor, history, permission and six widths passed');
    await browser.close();browser=null;
  }
}finally{if(browser)await browser.close();if(server){server.kill();await Promise.race([new Promise(r=>server.once('exit',r)),sleep(5000)]);}/* Keep the isolated DB as diagnostic evidence; never delete across shells. */}}
main().catch(e=>{console.error(e);process.exitCode=1;});
