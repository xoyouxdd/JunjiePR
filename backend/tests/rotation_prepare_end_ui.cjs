'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process');
const {chromium,webkit,firefox}=require('./support/browser-runtime.cjs');
const root=path.resolve(__dirname,'../..'),work=fs.mkdtempSync(path.join(os.tmpdir(),'rotation-prepare-end-'));
const report=path.join(root,'.handover_review/rotation-prepare-end');fs.mkdirSync(report,{recursive:true});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function ok(response){assert(response.ok(),await response.text());return response.json();}
let server,browser,log='';
async function main(){try{
  const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
  server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(root,'local_tools/rotation_clock/run.py'),'--demo','--data-dir',path.join(work,'data'),'--port',String(port)],{cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1'}});
  server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);
  const base='http://127.0.0.1:'+port;
  let ready=false;for(let i=0;i<120;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await sleep(250);}assert(ready,log);
  for(const engine of (process.env.ROTATION_PREPARE_ENGINES||'edge,webkit,firefox').split(',')){
    browser=await(engine==='webkit'?webkit:engine==='firefox'?firefox:chromium).launch(engine==='edge'?{channel:'msedge',headless:true}:{headless:true});
    const context=await browser.newContext({viewport:{width:1280,height:900}}),request=context.request,errors=[];
    await ok(await request.post(base+'/api/login',{data:{employee_no:'GSMTEST01',password:'rotation-local-2026'}}));
    const clock=async data=>ok(await request.post(base+'/__local/rotation-clock',{data}));
    const reset=await clock({action:'restart-preview'});await clock({action:'restart',confirm_date:reset.date,restart_token:reset.restart_token});
    await clock({action:'set',date:'2026-11-05',time:'15:49:59'});
    const board=async()=>(await ok(await request.get(base+'/api/rotation-board/board'))).day;
    const post=async data=>ok(await request.post(base+'/api/rotation-board/act',{data}));
    const row=(page,pid)=>page.locator('[data-f-slot]').filter({has:page.locator('.rt-who-name').filter({hasText:'模拟伙伴'+String(Number(pid)-9720000).padStart(2,'0')})});
    const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto(base+'/rotation-board');
    let day=await board();const pid=day.persons.find(p=>p.state==='onpost'&&p.end===975).pid;
    await row(page,pid).waitFor();assert.equal(await row(page,pid).getByText('准备下班',{exact:true}).count(),0);
    const mobile=await browser.newContext({viewport:{width:390,height:844}});
    await ok(await mobile.request.post(base+'/api/login',{data:{employee_no:pid,password:'rotation-local-2026'}}));
    const health=await ok(await mobile.request.get(base+'/health'));await ok(await mobile.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}}));
    const phone=await mobile.newPage();phone.on('pageerror',e=>errors.push(e.message));await phone.goto(base+'/?view=actionCenter');
    await phone.locator('[data-formal-rotation-todo] .rt-personal-task').waitFor();assert.equal(await phone.getByText('准备下班',{exact:true}).count(),0);
    const display=await browser.newContext();await ok(await display.request.post(base+'/api/login',{data:{employee_no:'6666666',password:'1243'}}));
    const screen=await display.newPage();screen.on('pageerror',e=>errors.push(e.message));await screen.goto(base+'/rotation/screen');await row(screen,pid).waitFor();
    await clock({action:'set',date:'2026-11-05',time:'15:50:00'});
    for(const view of [page,screen])await row(view,pid).getByText('准备下班',{exact:true}).waitFor();
    await phone.locator('[data-formal-rotation-todo]').getByText('准备下班',{exact:true}).waitFor();
    await phone.locator('.rt-personal-identity .rt-badge.prep').waitFor();
    assert.equal((await board()).persons.find(p=>p.pid===pid).state,'onpost','Reminder does not automatically take the employee off post');
    await page.screenshot({path:path.join(report,'board-'+engine+'.png'),animations:'disabled'});
    await phone.screenshot({path:path.join(report,'phone-'+engine+'.png'),animations:'disabled'});
    for(const width of [390,320]){await page.setViewportSize({width,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);}
    await post({action:'shift',pid,start:'07:15',end:'17:15'});
    await page.waitForFunction(pid=>RT.data.day.persons.find(p=>p.pid===pid).preparing===null,pid);
    await screen.waitForFunction(pid=>RT.data.day.persons.find(p=>p.pid===pid).preparing===null,pid);
    await phone.locator('[data-formal-rotation-todo]').getByText('准备下班',{exact:true}).waitFor({state:'detached'});
    await mobile.close();
    // The next day's 01:35 is still this workday, 25 minutes before 02:00.
    await clock({action:'set',date:'2026-11-06',time:'01:34:59'});
    day=await board();const late=day.persons.find(p=>p.end===1560),line=day.lines.find(l=>l.id==='A');
    await post({action:'place',pid:late.pid,line:'A',i:0,post_key:line.posts[0].key,place_mode:'swap',update_sort_time:false,expected_snapshot:day.forecastSnapshot});
    await page.evaluate(()=>RT.load());await row(page,late.pid).waitFor();assert.equal(await row(page,late.pid).getByText('准备下班',{exact:true}).count(),0);
    await clock({action:'set',date:'2026-11-06',time:'01:35:00'});
    await row(page,late.pid).getByText('准备下班',{exact:true}).waitFor();await row(screen,late.pid).getByText('准备下班',{exact:true}).waitFor();
    day=await board();assert.equal(day.date,'2026-11-05');assert.equal(day.status,'live');assert.equal(day.persons.find(p=>p.pid===late.pid).preparing,'推下班');
    await post({action:'pool',pid:late.pid,pool:'pending'});await page.waitForFunction(pid=>RT.data.day.persons.find(p=>p.pid===pid).preparing===null,late.pid);
    assert.equal((await board()).persons.find(p=>p.pid===late.pid).preparing,null);
    assert.deepEqual(errors,[]);await display.close();await context.close();await browser.close();browser=null;
    console.log(engine+': 25min+1sec absent, exactly25min shown; automatic board/screen/phone sync; shift edit and removal clear; overnight 01:35->02:00; 320/390px PASS');
  }
}finally{if(browser)await browser.close();if(server&&server.exitCode===null){server.kill();await new Promise(r=>server.once('exit',r));}fs.writeFileSync(path.join(report,'server.log'),log);}}
main().catch(error=>{console.error(error);console.error(log.slice(-2000));process.exitCode=1;});
