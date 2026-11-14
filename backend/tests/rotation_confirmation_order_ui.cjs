'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process');
const {chromium,webkit,firefox}=require('./support/browser-runtime.cjs');
const root=path.resolve(__dirname,'../..'),work=fs.mkdtempSync(path.join(os.tmpdir(),'rotation-confirm-order-'));
const report=path.join(root,'.handover_review/rotation-confirm-order');fs.mkdirSync(report,{recursive:true});
let server,browser,log='';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function ok(response){assert(response.ok(),await response.text());return response.json();}
async function main(){try{
  const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
  server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'rotation_daily_fixture.py'),work,String(port)],{cwd:path.join(root,'backend'),windowsHide:true,env:{...process.env,ROTATION_FORECAST_FIXTURE:'1',PYTHONUTF8:'1'}});
  server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);
  const base='http://127.0.0.1:'+port;
  let ready=false;for(let i=0;i<120;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await sleep(250);}assert(ready,log);
  for(const engine of ['edge','webkit','firefox']){
    browser=await(engine==='webkit'?webkit:engine==='firefox'?firefox:chromium).launch(engine==='edge'?{channel:'msedge',headless:true}:{headless:true});
    const context=await browser.newContext({viewport:{width:1280,height:900}}),request=context.request;
    await ok(await request.post(base+'/api/login',{data:{employee_no:'GSMTEST01',password:'daily-fixture-2026'}}));
    await ok(await request.post(base+'/api/rotation/enter',{data:{account:'7777777'}}));
    await ok(await request.post(base+'/api/rotation/test-clock',{data:{action:'set',date:'2026-10-04',time:'07:15'}}));
    await ok(await request.post(base+'/api/rotation/draft',{data:{action:'draft_generate',date:'2026-10-04',force:true}}));
    await ok(await request.post(base+'/api/rotation/draft',{data:{action:'publish',date:'2026-10-04'}}));
    await ok(await request.post(base+'/api/rotation/test-clock',{data:{action:'set',date:'2026-10-04',time:'07:30'}}));
    const post=async data=>ok(await request.post(base+'/api/rotation/act',{data}));
    const board=async()=>(await ok(await request.get(base+'/api/rotation/board'))).day;
    for(let i=0;i<2;i++){
      const name='顺序验证'+i;
      await post({action:'add_person',temporary:true,name,start:'07:15',end:'20:00'});
      const pid=(await board()).persons.find(p=>p.name===name).pid;
      await post({action:'pool',pid,pool:'rest'});await post({action:'break_time',pid,remaining:0});
    }
    await post({action:'sort_time',line:'A',time:'07:15:00'});await post({action:'sort_time',line:'B',time:'07:16:00'});
    let day=await board();
    const ordered=day.persons.filter(p=>p.canDepart&&p.assign?.kind==='push').sort((a,b)=>a.readyAt-b.readyAt||(a.forecastOrder<b.forecastOrder?-1:a.forecastOrder>b.forecastOrder?1:0));
    const first=ordered[0];
    const later=day.persons.find(p=>p.canDepart&&p.assign?.line==='B');
    assert(first&&later,'Both independent forecast lines must exist');
    const before=day.lines.map(l=>l.posts.map(p=>p.occ));
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/rotation');await page.locator('[data-f-board="'+later.pid+'"]').click();
    assert.match(await page.getByRole('dialog').innerText(),/预计 B线.*表头 07:16:00/);
    await page.getByRole('button',{name:'确认上岗',exact:true}).click();
    await page.getByRole('heading',{name:'线路已改变，请重新确认',exact:true}).waitFor();
    assert.match(await page.getByRole('dialog').innerText(),/预计 A线.*表头 07:15:00.*3个参与岗位/);
    day=await board();assert.deepEqual(day.lines.map(l=>l.posts.map(p=>p.occ)),before);
    assert.equal(day.persons.find(p=>p.pid===later.pid).state,'rest');
    await page.screenshot({path:path.join(report,'confirm-'+engine+'.png')});
    await page.getByRole('button',{name:'确认上岗',exact:true}).click();
    await page.waitForFunction(pid=>RT.data.day.lines.find(l=>l.id==='A').posts[0].occ===pid,later.pid);
    day=await board();assert.equal(day.lines.find(l=>l.id==='A').sortAt,450);
    assert.equal(day.persons.find(p=>p.pid===first.pid).state,'rest');
    assert.equal(day.persons.find(p=>p.pid===first.pid).assign.line,'B');
    const retry=await post({action:'depart',pid:later.pid,confirmation_token:later.confirmationTokens.depart,expected_line:'A'});
    assert(retry.duplicate);
    assert.deepEqual(errors,[]);await context.close();await browser.close();browser=null;
    console.log(engine+': later person confirms first -> actual oldest header, reroute without moving, second confirmation, refresh and duplicate protection PASS');
  }
}finally{if(browser)await browser.close();if(server&&server.exitCode===null){server.kill();await new Promise(r=>server.once('exit',r));}fs.writeFileSync(path.join(report,'confirmation-ui-server.log'),log);}}
main().catch(error=>{console.error(error);console.error(log.slice(-3000));process.exitCode=1;});
