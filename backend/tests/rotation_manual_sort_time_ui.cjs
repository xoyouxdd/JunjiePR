'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process');const {chromium,webkit,firefox}=require('./support/browser-runtime.cjs');
const root=path.resolve(__dirname,'../..'),work=fs.mkdtempSync(path.join(os.tmpdir(),'rotation-manual-sort-'));
const report=path.join(root,'.handover_review/rotation-manual-sort-time');fs.mkdirSync(report,{recursive:true});
let server,browser,log='';const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function ok(response){assert(response.ok(),await response.text());return response.json();}
async function main(){try{
  const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
  server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'rotation_daily_fixture.py'),work,String(port)],{cwd:path.join(root,'backend'),windowsHide:true,env:{...process.env,ROTATION_FORECAST_FIXTURE:'1',PYTHONUTF8:'1'}});
  server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);const base='http://127.0.0.1:'+port;
  let ready=false;for(let i=0;i<120;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await sleep(250);}assert(ready,log);
  for(const engine of (process.env.ROTATION_MANUAL_SORT_ENGINES||'edge,webkit,firefox').split(',')){
    browser=await(engine==='webkit'?webkit:engine==='firefox'?firefox:chromium).launch(engine==='edge'?{channel:'msedge',headless:true}:{headless:true});
    const context=await browser.newContext({viewport:{width:1280,height:900}}),request=context.request;
    await ok(await request.post(base+'/api/login',{data:{employee_no:'GSMTEST01',password:'daily-fixture-2026'}}));
    await ok(await request.post(base+'/api/rotation/enter',{data:{account:'7777777'}}));
    const clock=async data=>ok(await request.post(base+'/api/rotation/test-clock',{data}));
    await clock({action:'set',date:'2026-10-04',time:'07:15'});
    await ok(await request.post(base+'/api/rotation/draft',{data:{action:'draft_generate',date:'2026-10-04',force:true}}));
    await ok(await request.post(base+'/api/rotation/draft',{data:{action:'publish',date:'2026-10-04'}}));
    await clock({action:'set',date:'2026-10-04',time:'07:30'});
    const board=async()=>(await ok(await request.get(base+'/api/rotation/board'))).day;
    const post=async data=>ok(await request.post(base+'/api/rotation/act',{data}));
    const page=await context.newPage(),errors=[],writes=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST')writes.push(r.postDataJSON());});
    await page.goto(base+'/rotation');await page.waitForFunction(()=>RT.data?.day?.ruleVersion===2);
    const checkbox=()=>page.locator('[name=update_sort_time]');
    const open=async(pid,line,i)=>{await page.evaluate(()=>RT.load());await page.evaluate(({pid,line,i})=>forecastPlace(pid,line,i),{pid,line,i});};
    const save=async()=>{await page.getByRole('button',{name:'确定',exact:true}).click();await page.waitForFunction(()=>!RT.modalOpen);};
    let day=await board();const a=day.lines[0].posts[0].occ;
    await post({action:'pool',pid:a,pool:'rest'});await open(a,'A',0);
    assert(await checkbox().isVisible());assert.equal(await checkbox().isChecked(),false);
    await save();day=await board();assert.equal(day.lines[0].sortAt,435);assert.equal(day.lines[0].posts[0].since,450);
    assert.equal(writes.at(-1).update_sort_time,false);
    // Normal confirmation remains automatic and has no manual override checkbox.
    await post({action:'add_person',temporary:true,name:'确认上岗验证',start:'07:00',end:'20:00'});
    const extra=(await board()).persons.find(p=>p.name==='确认上岗验证').pid;
    await post({action:'pool',pid:extra,pool:'rest'});await post({action:'break_time',pid:extra,remaining:0});
    day=await board();const normal=day.persons.find(p=>p.canDepart&&p.assign?.line==='A');assert(normal);
    await page.evaluate(()=>RT.load());await page.locator('[data-f-board="'+normal.pid+'"]').click();
    assert.equal(await checkbox().count(),0);await page.getByRole('button',{name:'确认上岗',exact:true}).click();
    await page.waitForFunction(()=>RT.data.day.lines[0].sortAt===450);assert.equal((await board()).lines[0].posts[0].occ,normal.pid);
    // Explicit manual opt-in updates the destination head.
    const b=(await board()).lines[1].posts[0].occ;await post({action:'pool',pid:b,pool:'rest'});await open(b,'B',0);
    assert.equal(await checkbox().isChecked(),false);await checkbox().check();await save();assert.equal((await board()).lines[1].sortAt,450);
    await clock({action:'jump',minutes:1});day=await board();await open(day.lines[0].posts[0].occ,'B',1);
    assert.equal(await checkbox().isVisible(),false);await page.locator('[name=place_mode]').selectOption('swap');
    assert(await checkbox().isVisible());assert.equal(await checkbox().isChecked(),false);await save();
    assert.deepEqual((await board()).lines.map(L=>L.sortAt),[450,450]);
    day=await board();await open(day.lines[0].posts[0].occ,'B',1);await page.locator('[name=place_mode]').selectOption('swap');
    await checkbox().check();await save();assert.deepEqual((await board()).lines.map(L=>L.sortAt),[451,450]);
    // Changing operation mode resets the opt-in; a two-head exchange names both lines.
    await clock({action:'jump',minutes:1});day=await board();await open(day.lines[0].posts[0].occ,'B',0);
    await checkbox().check();await page.locator('[name=place_mode]').selectOption('swap');assert.equal(await checkbox().isChecked(),false);
    assert.match(await page.locator('[data-f-header-lines]').innerText(),/A/);assert.match(await page.locator('[data-f-header-lines]').innerText(),/B/);
    await checkbox().check();
    for(const width of [390,1280]){await page.setViewportSize({width,height:900});assert(await page.getByRole('dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1));await page.screenshot({path:path.join(report,engine+'-'+width+'.png'),animations:'disabled'});}
    await save();assert.deepEqual((await board()).lines.map(L=>L.sortAt),[452,452]);
    // Whole-post/person movement uses the same option; configuration-only swap does not.
    await clock({action:'jump',minutes:1});
    const openPosts=async()=>{await page.evaluate(()=>RT.load());await page.evaluate(()=>{const L=RT.data.day.lines[0];forecastPostSwap({line:L.id,i:0,key:L.posts[0].key,date:RT.data.day.date,snapshot:RT.data.day.forecastSnapshot},L.id,1,L.posts[1].key);});};
    await openPosts();assert.equal(await checkbox().isVisible(),false);await page.locator('[name=swapMode]').selectOption('with_people');assert(await checkbox().isVisible());assert.equal(await checkbox().isChecked(),false);
    await save();assert.deepEqual((await board()).lines.map(L=>L.sortAt),[452,452]);
    await openPosts();await page.locator('[name=swapMode]').selectOption('with_people');await checkbox().check();await save();assert.deepEqual((await board()).lines.map(L=>L.sortAt),[453,452]);
    for(const body of writes.filter(b=>['place','post_order'].includes(b?.action)))assert.equal(typeof body.update_sort_time,'boolean');
    assert.deepEqual(errors,[]);await context.close();await browser.close();browser=null;
    console.log(engine+': manual default unchanged, explicit head update, normal confirm, affected source/two-line heads, mode reset and post/person options PASS');
  }
}finally{if(browser)await browser.close();if(server&&server.exitCode===null){server.kill();await new Promise(r=>server.once('exit',r));}fs.writeFileSync(path.join(report,'server.log'),log);}}
main().catch(error=>{console.error(error);console.error(log.slice(-4000));process.exitCode=1;});
