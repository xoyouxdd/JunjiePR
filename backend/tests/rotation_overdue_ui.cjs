'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process');const {chromium,webkit,firefox}=require('playwright');
const root=path.resolve(__dirname,'../..'),work=fs.mkdtempSync(path.join(os.tmpdir(),'rotation-overdue-'));
const report=path.join(root,'.handover_review/rotation-overdue');fs.mkdirSync(report,{recursive:true});
let server,browser,log='';const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function ok(response){assert(response.ok(),await response.text());return response.json();}
async function main(){try{
  const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
  server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'rotation_daily_fixture.py'),work,String(port)],{cwd:path.join(root,'backend'),windowsHide:true,env:{...process.env,ROTATION_FORECAST_FIXTURE:'1',PYTHONUTF8:'1'}});
  server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);
  const base='http://127.0.0.1:'+port;let ready=false;
  for(let i=0;i<120;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await sleep(250);}assert(ready,log);
  for(const engine of ['edge','webkit','firefox']){
    browser=await(engine==='webkit'?webkit:engine==='firefox'?firefox:chromium).launch(engine==='edge'?{channel:'msedge',headless:true}:{headless:true});
    const context=await browser.newContext({viewport:{width:1280,height:900}}),request=context.request;
    await ok(await request.post(base+'/api/login',{data:{employee_no:'GSMTEST01',password:'daily-fixture-2026'}}));
    await ok(await request.post(base+'/api/rotation/enter',{data:{account:'7777777'}}));
    const clock=async data=>ok(await request.post(base+'/api/rotation/test-clock',{data}));
    await clock({action:'set',date:'2026-10-04',time:'07:15'});
    await ok(await request.post(base+'/api/rotation/draft',{data:{action:'draft_generate',date:'2026-10-04',force:true}}));
    await ok(await request.post(base+'/api/rotation/draft',{data:{action:'publish',date:'2026-10-04'}}));
    const board=async()=>(await ok(await request.get(base+'/api/rotation/board'))).day;
    const post=async data=>ok(await request.post(base+'/api/rotation/act',{data}));
    let day=await board();const ids=day.persons.filter(p=>p.state==='onpost').slice(0,3).map(p=>p.pid);assert.equal(ids.length,3);
    const rest=async(pid,remaining=0)=>{await post({action:'pool',pid,pool:'rest'});await post({action:'break_time',pid,remaining});};
    await rest(ids[0]);await clock({action:'jump',minutes:1+10/60});await rest(ids[1]);await clock({action:'jump',minutes:1+34/60});await rest(ids[2],11);
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(base+'/rotation');
    const elapsed=pid=>page.locator('[data-f-person="'+pid+'"]').locator('..').locator('.rt-forecast-overdue');
    await elapsed(ids[0]).waitFor();assert.equal(await elapsed(ids[0]).innerText(),'超时2分44秒');assert.equal(await elapsed(ids[1]).innerText(),'超时1分34秒');
    assert.equal(await elapsed(ids[2]).count(),0);
    await page.waitForFunction(pid=>{const el=document.querySelector('[data-f-person="'+pid+'"]')?.parentElement.querySelector('.rt-forecast-overdue');return el?.isConnected&&getComputedStyle(el).color==='rgb(180, 35, 24)';},ids[0]);
    await sleep(1100);assert.equal(await elapsed(ids[0]).innerText(),'超时2分44秒','paused clock must freeze the display');
    for(const width of [320,390,1280]){
      await page.setViewportSize({width,height:900});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      assert(await elapsed(ids[0]).evaluate(el=>el.scrollWidth<=el.clientWidth+1));
      await page.locator('[data-f-pool=rest]').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(report,engine+'-'+width+'.png'),animations:'disabled'});
    }
    // A paused-closure allowance must come from the adjusted deadline rather
    // than counting all wall time since the original break ended.
    assert.match(await page.evaluate(()=>forecastOverdue({canDepart:true,state:'rest',readyAt:600,task:{deadlineAt:633}},631)),/超时1分00秒/);
    await clock({action:'jump',minutes:16/60});await page.evaluate(()=>RT.load());
    assert.equal((await board()).persons.find(p=>p.pid===ids[0]).state,'exception');assert.equal(await elapsed(ids[0]).innerText(),'超时3分00秒');
    assert.equal(await page.locator('[data-f-board="'+ids[0]+'"]').count(),0);
    await post({action:'break_time',pid:ids[1],remaining:5});await page.evaluate(()=>RT.load());assert.equal(await elapsed(ids[1]).count(),0);
    // Midnight keeps elapsed minutes, not a negative clock subtraction.
    await post({action:'shift',pid:ids[2],start:'07:15',end:'01:30'});await clock({action:'set',date:'2026-10-04',time:'23:59'});
    await rest(ids[2]);await clock({action:'set',date:'2026-10-05',time:'00:01'});await page.evaluate(()=>RT.load());
    assert.equal(await elapsed(ids[2]).innerText(),'超时2分00秒');assert.equal(await page.evaluate(()=>RT.data.clock.date),'2026-10-04');
    assert.deepEqual(errors,[]);await context.close();await browser.close();browser=null;
    console.log(engine+': red elapsed minutes/seconds, paused display, three-minute exception, rest edit, adjusted deadline, midnight and 320/390/1280px PASS');
  }
}finally{if(browser)await browser.close();if(server&&server.exitCode===null){server.kill();await new Promise(r=>server.once('exit',r));}fs.writeFileSync(path.join(report,'server.log'),log);}}
main().catch(error=>{console.error(error);console.error(log.slice(-3000));process.exitCode=1;});
