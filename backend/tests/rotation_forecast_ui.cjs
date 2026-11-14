'use strict';
// Real isolated API + browsers, random local port; never touches the running service.
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process');
const {chromium,webkit,firefox}=require('./support/browser-runtime.cjs');
const root=path.resolve(__dirname,'../..'),work=fs.mkdtempSync(path.join(os.tmpdir(),'rotation-forecast-'));
let server,browser,log='';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function ok(response){assert(response.ok(),await response.text());return response.json();}
async function main(){try{
  const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
  server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'rotation_daily_fixture.py'),work,String(port)],{cwd:path.join(root,'backend'),windowsHide:true,env:{...process.env,ROTATION_FORECAST_FIXTURE:'1',PYTHONUTF8:'1'}});
  server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);
  const base='http://127.0.0.1:'+port;
  let ready=false;for(let i=0;i<100;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await sleep(300);}assert(ready,log);
  for(const engine of (process.env.ROTATION_FORECAST_ENGINES||'edge,webkit,firefox').split(',')){
    browser=await(engine==='webkit'?webkit:engine==='firefox'?firefox:chromium).launch(engine==='edge'?{channel:'msedge',headless:true}:{headless:true});
    const context=await browser.newContext({viewport:{width:1280,height:900}}),request=context.request;
    await ok(await request.post(base+'/api/login',{data:{employee_no:'GSMTEST01',password:'daily-fixture-2026'}}));
    await ok(await request.post(base+'/api/rotation/enter',{data:{account:'7777777'}}));
    const post=async data=>ok(await request.post(base+'/api/rotation/act',{data}));
    await ok(await request.post(base+'/api/rotation/test-clock',{data:{action:'set',date:'2026-10-04',time:'07:15'}}));
    await ok(await request.post(base+'/api/rotation/draft',{data:{action:'draft_generate',date:'2026-10-04',force:true}}));
    await ok(await request.post(base+'/api/rotation/draft',{data:{action:'publish',date:'2026-10-04'}}));
    const board=async()=>(await ok(await request.get(base+'/api/rotation/board'))).day;
    let day=await board();assert.equal(day.ruleVersion,2);
    const seated=day.lines[0].posts.find(p=>p.occ).occ;
    await post({action:'pool',pid:seated,pool:'rest'});
    await post({action:'break_time',pid:seated,remaining:0});
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/rotation');
    await page.locator('[data-f-board="'+seated+'"]').waitFor({timeout:15000});
    assert.equal(await page.locator('h2').filter({hasText:'待出发'}).count(),0);
    assert(await page.locator('[data-f-board="'+seated+'"]').locator('..').evaluate(el=>el.classList.contains('rt-can-board')));
    await page.locator('[data-f-board="'+seated+'"]').click();
    await page.getByRole('button',{name:'确认上岗',exact:true}).click();
    await page.waitForFunction(pid=>RT.data.day.persons.find(p=>p.pid===pid).state==='onpost',seated);
    assert.equal((await board()).persons.find(p=>p.pid===seated).state,'onpost');
    await page.locator('[data-f-head=A]').click({button:'right'});
    await page.getByRole('button',{name:'修改排序时间',exact:true}).click();
    await page.locator('[name=time]').fill('00:00:01');
    await page.getByRole('button',{name:'确定',exact:true}).click();
    await page.waitForFunction(()=>RT.data.day.lines.find(l=>l.id==='A').sortAt===1/60);
    // Removing someone immediately refreshes all remaining forecasts.
    day=await board();const person=day.persons.find(p=>p.state==='rest'||p.state==='pending');
    await post({action:'pool',pid:person.pid,pool:'rest'});
    await post({action:'break_time',pid:person.pid,remaining:0});
    await page.evaluate(()=>RT.load());
    await page.locator('[data-f-person="'+person.pid+'"]').click();
    await page.getByRole('button',{name:'修改餐休剩余时间',exact:true}).click();
    await page.locator('[name=remaining]').fill('20');await page.getByRole('button',{name:'确定',exact:true}).click();
    await page.waitForFunction(pid=>RT.data.day.persons.find(p=>p.pid===pid).readyAt===455,person.pid);
    assert.equal(await page.locator('[data-f-board="'+person.pid+'"]').count(),0);
    // New personnel dialog searches on demand; temporary identities remain local records.
    await page.locator('[data-f-add]').first().click();
    await page.locator('[name=query]').fill('临时伙伴');await page.locator('[name=temporary]').check();
    await page.locator('[name=start]').fill('08:00');await page.locator('[name=end]').fill('00:30');
    await page.getByRole('button',{name:'新增',exact:true}).click();
    await page.waitForFunction(()=>RT.data.day.persons.some(p=>p.name==='临时伙伴'));
    const dragPid=(await board()).lines[0].posts[0].occ;
    await page.evaluate(()=>{window.forecastDragEvents=[];for(const type of ['dragstart','dragover','drop','dragend'])document.addEventListener(type,e=>{if(type!=='dragover'||!window.forecastDragEvents.some(x=>x.type==='dragover'))window.forecastDragEvents.push({type,types:[...(e.dataTransfer?.types||[])],target:e.target.closest('[data-f-drag],[data-f-pool],[data-f-slot]')?.outerHTML.slice(0,160)});},true);});
    await page.locator('[data-f-drag="'+dragPid+'"]').first().dragTo(page.locator('[data-f-pool=rest]'));
    await page.waitForFunction(pid=>RT.data.day.persons.find(p=>p.pid===pid).state==='rest',dragPid).catch(async error=>{console.error(engine,await page.evaluate(pid=>({events:window.forecastDragEvents,state:RT.data.day.persons.find(p=>p.pid===pid),toast:document.getElementById('toast').textContent}),dragPid));throw error;});
    await page.locator('[data-f-drag="'+dragPid+'"]').first().dragTo(page.locator('[data-f-slot="A#0"]'));
    await page.locator('[name=place_mode]').selectOption('swap');
    await page.getByRole('button',{name:'确定',exact:true}).click();
    await page.waitForFunction(pid=>RT.data.day.lines[0].posts[0].occ===pid,dragPid);
    // An open confirmation may be rerouted repeatedly, never executed on the
    // old line, and must name the immediately preceding route each time.
    const reviewPid=person.pid;
    await post({action:'break_time',pid:reviewPid,remaining:0});await page.evaluate(()=>RT.load());
    const before=(await board()).persons.find(p=>p.pid===reviewPid),original=before.assign.line,other=original==='A'?'B':'A';
    await page.locator('[data-f-board="'+reviewPid+'"]').click();
    await post({action:'sort_time',line:original,time:'23:59:00'});await post({action:'sort_time',line:other,time:'00:00:00'});
    await page.getByRole('button',{name:'确认上岗',exact:true}).click();
    await page.getByRole('heading',{name:'线路已改变，请重新确认',exact:true}).waitFor();
    assert.equal((await board()).persons.find(p=>p.pid===reviewPid).state,'rest');
    await post({action:'sort_time',line:other,time:'23:59:00'});await post({action:'sort_time',line:original,time:'00:00:00'});
    await page.getByRole('button',{name:'确认上岗',exact:true}).click();
    await page.waitForFunction(previous=>document.getElementById('rtModal').textContent.includes('原预计 '+previous),other);
    await page.getByRole('button',{name:'确认上岗',exact:true}).click();
    await page.waitForFunction(pid=>RT.data.day.persons.find(p=>p.pid===pid).state==='onpost',reviewPid);
    // Expiration while the modal is open closes the stale confirmation rather
    // than leaving an executable-looking dialog above the exception pool.
    await post({action:'pool',pid:reviewPid,pool:'rest'});await post({action:'break_time',pid:reviewPid,remaining:0});await page.evaluate(()=>RT.load());
    await page.locator('[data-f-board="'+reviewPid+'"]').click();
    await ok(await request.post(base+'/api/rotation/test-clock',{data:{action:'jump',minutes:3}}));await page.evaluate(()=>RT.load());
    assert.equal(await page.getByRole('dialog').count(),0);
    assert.equal((await board()).persons.find(p=>p.pid===reviewPid).state,'exception');
    assert.equal(await page.locator('[data-f-board="'+reviewPid+'"]').count(),0);
    // The working day and live clock continue after midnight; readiness and
    // the blue action use the same time base instead of clamping at 24:00.
    await post({action:'shift',pid:reviewPid,start:'07:15',end:'01:30'});
    await post({action:'pool',pid:reviewPid,pool:'rest'});
    const clock=await ok(await request.get(base+'/api/rotation/board'));
    await post({action:'break_time',pid:reviewPid,remaining:1445-clock.clock.minute});
    await ok(await request.post(base+'/api/rotation/test-clock',{data:{action:'set',date:'2026-10-05',time:'00:05'}}));await page.evaluate(()=>RT.load());
    assert.equal(await page.evaluate(()=>RT.data.clock.date),'2026-10-04');
    const liveMinute=await page.evaluate(()=>{const paused=RT.data.clock.paused,fetched=RT.fetchedAt;RT.data.clock.paused=false;RT.fetchedAt=Date.now()-1000;const value=nowMin();RT.data.clock.paused=paused;RT.fetchedAt=fetched;return value;});
    assert(liveMinute>=1445&&liveMinute<1446,'overnight live clock '+liveMinute);
    await page.locator('[data-f-board="'+reviewPid+'"]').click();await page.getByRole('button',{name:'确认上岗',exact:true}).click();
    await page.waitForFunction(pid=>RT.data.day.persons.find(p=>p.pid===pid).state==='onpost',reviewPid);
    await page.locator('[data-f-add]').first().click();
    assert.equal(await page.locator('[name=start_day_offset]').inputValue(),'1');
    await page.locator('[name=query]').fill('午夜支援');await page.locator('[name=temporary]').check();
    await page.locator('[name=start]').fill('00:20');await page.locator('[name=end]').fill('04:00');
    await page.getByRole('button',{name:'新增',exact:true}).click();
    await page.waitForFunction(()=>RT.data.day.persons.some(p=>p.name==='午夜支援'&&p.start===1460&&p.end===1680&&p.state==='notyet'));
    const upcoming=page.locator('[data-f-pool=rest] .rt-chip').filter({hasText:'午夜支援'});
    await upcoming.getByText('准备上班 · 距到班 15 分钟',{exact:true}).waitFor();
    assert.equal(await page.locator('[data-f-pool=other]').getByText('午夜支援').count(),0);
    assert.equal(await upcoming.locator('[data-f-board],[data-f-arrive],.rt-forecast-overdue').count(),0);
    assert.match(await upcoming.locator('.rt-go').innerText(),/^预计 /);
    await page.locator('[data-f-head=A]').click();await page.getByRole('button',{name:'撤岗',exact:true}).click();
    await page.locator('[name=i]').selectOption('1');
    assert.equal(await page.locator('[name=day_offset]').inputValue(),'1');
    await page.locator('[name=time]').fill('00:15');await page.getByRole('button',{name:'确定',exact:true}).click();
    await page.waitForFunction(()=>RT.data.day.lines[0].posts[1].closeAt==='24:15:00');
    await ok(await request.post(base+'/api/rotation/test-clock',{data:{action:'set',date:'2026-10-05',time:'00:20'}}));
    await page.evaluate(()=>RT.load());
    await upcoming.locator('[data-f-board]').waitFor();
    const support=(await board()).persons.find(p=>p.name==='午夜支援');
    assert.equal(support.state,'ready');
    assert.equal(support.directShiftEntry,1460);
    assert.equal(await upcoming.locator('[data-f-arrive]').count(),0);
    for(const width of [320,390,768,1280]){
      await page.setViewportSize({width,height:900});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'overflow '+width);
    }
    assert.deepEqual(errors,[]);
    const shots=path.join(root,'.handover_review','rotation-forecast-review');fs.mkdirSync(shots,{recursive:true});
    await page.screenshot({path:path.join(shots,'forecast-'+engine+'.png'),fullPage:true});
    console.log(engine+': forecast, individual execution, head edit, rest edit, temporary person, responsive widths PASS');
    await context.close();await browser.close();browser=null;
  }
}finally{if(browser)await browser.close();if(server){server.kill();await new Promise(r=>server.once('exit',r));}}}
main().catch(error=>{console.error(error,log.slice(-7000));process.exitCode=1;});
