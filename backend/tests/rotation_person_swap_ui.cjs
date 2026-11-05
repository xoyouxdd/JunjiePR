'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process');const {chromium,webkit,firefox}=require('playwright');
const root=path.resolve(__dirname,'../..'),work=fs.mkdtempSync(path.join(os.tmpdir(),'rotation-person-swap-'));
const report=path.join(root,'.handover_review/rotation-person-swap');fs.mkdirSync(report,{recursive:true});
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
    await ok(await request.post(base+'/api/rotation/test-clock',{data:{action:'set',date:'2026-10-04',time:'07:15'}}));
    await ok(await request.post(base+'/api/rotation/draft',{data:{action:'draft_generate',date:'2026-10-04',force:true}}));
    await ok(await request.post(base+'/api/rotation/draft',{data:{action:'publish',date:'2026-10-04'}}));
    const board=async()=>(await ok(await request.get(base+'/api/rotation/board'))).day;
    const post=async data=>ok(await request.post(base+'/api/rotation/act',{data}));
    const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));await page.goto(base+'/rotation');
    await page.waitForFunction(()=>RT.data?.day?.ruleVersion===2);
    const open=async(pid,line,i)=>{await page.evaluate(()=>RT.load());await page.evaluate(({pid,line,i})=>forecastPlace(pid,line,i),{pid,line,i});};
    const choose=async()=>{await page.locator('[name=place_mode]').selectOption('swap');assert(await page.locator('[data-f-place-detail]').isVisible());};
    const save=async()=>{await page.getByRole('button',{name:'确定',exact:true}).click();await page.waitForFunction(()=>!RT.modalOpen);};
    let day=await board();const first=day.lines[0].posts[0].occ,second=day.lines[0].posts[1].occ;
    const occupiedBefore=day.persons.filter(p=>p.state==='onpost').length;
    await open(first,'A',1);
    assert.equal(await page.locator('[name=place_mode] option[value=swap]').textContent(),'直接交换，两人互换岗位');
    await choose();
    const details=await page.locator('[data-f-place-detail]').innerText();
    assert(details.includes(day.persons.find(p=>p.pid===first).name));assert(details.includes(day.persons.find(p=>p.pid===second).name));assert(!details.includes('待确定休息'));
    for(const width of [390,1280]){await page.setViewportSize({width,height:900});assert(await page.getByRole('dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1));await page.screenshot({path:path.join(report,engine+'-'+width+'.png'),animations:'disabled'});}
    await save();day=await board();assert.equal(day.lines[0].posts[0].occ,second);assert.equal(day.lines[0].posts[1].occ,first);
    assert.equal(day.persons.filter(p=>p.state==='onpost').length,occupiedBefore);
    // Cross-line exchange also keeps both people on posts.
    const across=day.lines[1].posts[1].occ;await open(second,'B',1);await choose();await save();day=await board();
    assert.equal(day.lines[0].posts[0].occ,across);assert.equal(day.lines[1].posts[1].occ,second);
    assert.equal(day.persons.filter(p=>p.state==='onpost').length,occupiedBefore);
    // Pool replacement keeps its distinct meaning: the displaced person rests.
    const pool=day.persons.find(p=>['rest','pending'].includes(p.state));assert(pool);
    const displaced=day.lines[0].posts[1].occ;await open(pool.pid,'A',1);
    assert.match(await page.locator('[name=place_mode] option[value=swap]').textContent(),/原岗人员回待确定休息/);
    await choose();assert.match(await page.locator('[data-f-place-detail]').innerText(),/待确定休息/);await save();day=await board();
    assert.equal(day.lines[0].posts[1].occ,pool.pid);assert.equal(day.persons.find(p=>p.pid===displaced).state,'pending');
    // An open empty target is a direct move, and the original post becomes empty.
    const cleared=day.lines[1].posts[2].occ;await post({action:'pool',pid:cleared,pool:'rest'});
    await open(across,'B',2);assert.equal(await page.locator('[name=place_mode] option[value=swap]').textContent(),'直接放入空岗');
    await choose();assert.match(await page.locator('[data-f-place-detail]').innerText(),/原岗位空出/);await save();day=await board();
    assert.equal(day.lines[0].posts[0].occ,null);assert.equal(day.lines[1].posts[2].occ,across);
    const ids=day.lines.flatMap(l=>l.posts.map(p=>p.occ)).filter(Boolean);assert.equal(new Set(ids).size,ids.length);
    assert.deepEqual(errors,[]);await context.close();await browser.close();browser=null;
    console.log(engine+': on-post same-line/cross-line exchanges, pool replacement, empty-post move and 390/1280px dialogs PASS');
  }
}finally{if(browser)await browser.close();if(server&&server.exitCode===null){server.kill();await new Promise(r=>server.once('exit',r));}fs.writeFileSync(path.join(report,'server.log'),log);}}
main().catch(error=>{console.error(error);console.error(log.slice(-3000));process.exitCode=1;});
