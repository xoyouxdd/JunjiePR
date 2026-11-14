'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process');const {chromium,webkit,firefox}=require('./support/browser-runtime.cjs');
const root=path.resolve(__dirname,'../..'),work=fs.mkdtempSync(path.join(os.tmpdir(),'rotation-access-policy-'));
const report=path.join(root,'.handover_review/rotation-access-policy');fs.mkdirSync(report,{recursive:true});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));async function ok(r){assert(r.ok(),await r.text());return r.json();}
let server,browser,log='';
async function main(){try{
  const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
  server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'rotation_access_policy_fixture.py'),work,String(port)],{cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1'}});
  server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);const base='http://127.0.0.1:'+port;
  let ready=false;for(let i=0;i<150;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await sleep(250);}assert(ready,log);
  for(const [index,engine] of (process.env.ROTATION_POLICY_ENGINES||'edge,webkit,firefox').split(',').entries()){
    browser=await(engine==='webkit'?webkit:engine==='firefox'?firefox:chromium).launch(engine==='edge'?{channel:'msedge',headless:true}:{headless:true});
    const manager=await browser.newContext({viewport:{width:1280,height:900}}),display=await browser.newContext({viewport:{width:1280,height:900}}),tr=await browser.newContext({viewport:{width:390,height:844}}),errors=[];
    await ok(await manager.request.post(base+'/api/login',{data:{employee_no:'9738103',password:'rotation-local-2026'}}));
    await ok(await display.request.post(base+'/api/login',{data:{employee_no:'6666666',password:'1243'}}));
    await ok(await tr.request.post(base+'/api/login',{data:{employee_no:'9738110',password:'rotation-local-2026'}}));
    const day='2026-11-'+String(7+index).padStart(2,'0');
    const clock=async time=>ok(await manager.request.post(base+'/__local/rotation-clock',{data:{action:'set',date:day,time}}));
    await clock('02:59:59');
    const page=await manager.newPage(),screen=await display.newPage(),trainer=await tr.newPage();
    for(const p of [page,screen,trainer])p.on('pageerror',e=>errors.push(e.message));
    await Promise.all([page.goto(base+'/rotation-board'),screen.goto(base+'/rotation/screen'),trainer.goto(base+'/rotation-board')]);
    await page.waitForFunction(()=>RT.data&&RT.data.day===null);await screen.waitForFunction(()=>RT.data&&RT.data.day===null);
    await clock('03:00:00');
    for(const p of [page,screen,trainer])await p.waitForFunction(()=>RT.data?.day?.status==='live'&&RT.data.clock.minute===180);
    assert.match(await page.locator('#rtWho').innerText(),/热力追踪/);
    assert.match(await screen.locator('.rt-who-name').first().innerText(),/模拟伙伴/);
    const pre=(await ok(await manager.request.get(base+'/api/rotation-board/board'))).day;
    assert(pre.persons.every(p=>p.state==='notyet'&&p.continuousStart===null));assert(pre.lines.every(l=>l.sortAt===0));
    assert.equal(await screen.locator('#rtApp button').count(),0);
    assert(!(await screen.locator('#rtApp').innerText()).includes('双击修改'));
    assert((await screen.locator('.rt-mins').allTextContents()).every(t=>t!=='在岗'));
    assert.equal(await screen.locator('[draggable=true],[data-f-person],[data-f-post],[data-f-add],[data-f-close-all]').count(),0);
    assert.equal(await trainer.locator('[data-tab=settings]').count(),0);assert(await trainer.locator('[data-f-person]').count());
    await page.screenshot({path:path.join(report,'0300-board-'+engine+'.png'),animations:'disabled'});
    await screen.screenshot({path:path.join(report,'0300-screen-'+engine+'.png'),animations:'disabled'});
    await clock('07:15:00');
    for(const p of [page,screen])await p.waitForFunction(()=>RT.data.day.persons.filter(x=>x.state==='onpost').length===6);
    let current=(await ok(await manager.request.get(base+'/api/rotation-board/board'))).day;
    const pid=current.persons.find(p=>p.state==='onpost').pid;
    assert.equal((await display.request.post(base+'/api/rotation/screen/act',{data:{action:'pool',pid,pool:'pending'}})).status(),403);
    await ok(await tr.request.post(base+'/api/rotation-board/act',{data:{action:'pool',pid,pool:'pending'}}));
    await screen.locator('[data-f-arrive="'+pid+'"]').click();assert.equal(await screen.locator('[name=kind]').inputValue(),'rest');
    await screen.getByRole('button',{name:'确定',exact:true}).click();
    await page.waitForFunction(pid=>RT.data.day.persons.find(p=>p.pid===pid).state==='rest',pid);
    await clock('07:30:00');await screen.locator('[data-f-board="'+pid+'"]').waitFor();
    assert.equal(await screen.locator('[data-f-board="'+pid+'"]').innerText(),'确认去轮岗');
    await screen.locator('[data-f-board="'+pid+'"]').click();await screen.getByRole('button',{name:'确认上岗',exact:true}).click();
    const changed=screen.getByRole('heading',{name:'线路已改变，请重新确认',exact:true});
    await Promise.race([changed.waitFor(),screen.waitForFunction(pid=>RT.data.day.persons.find(p=>p.pid===pid).state==='onpost',pid)]);
    if(await changed.isVisible())await screen.getByRole('button',{name:'确认上岗',exact:true}).click();
    await page.waitForFunction(pid=>RT.data.day.persons.find(p=>p.pid===pid).state==='onpost',pid);
    current=(await ok(await display.request.get(base+'/api/rotation/screen/state'))).day;
    for(const action of ['place','pool','shift','rename_post','post_order','sort_time','all_close','entry_return','entry_adjust']){
      assert.equal((await display.request.post(base+'/api/rotation/screen/act',{data:{action,pid,line:'A',i:0,pool:'rest'}})).status(),403,action);
    }
    assert.equal(await screen.locator('[draggable=true],[data-f-person],[data-f-post],[data-f-add],[data-f-close-all]').count(),0);
    assert((await trainer.locator('[draggable=true]').count())>0);
    await screen.screenshot({path:path.join(report,'screen-confirm-only-'+engine+'.png'),animations:'disabled'});
    assert.deepEqual(errors,[]);await manager.close();await display.close();await tr.close();await browser.close();browser=null;
    console.log(engine+': 02:59:59 absent,03:00 shared preview without staffing; cross-circle TA_GSM targets Heat Pursuit; local TR operates; screen no management controls and API rejects all except rest/depart; both confirmations sync PASS');
  }
}finally{if(browser)await browser.close();if(server&&server.exitCode===null){server.kill();await new Promise(r=>server.once('exit',r));}fs.writeFileSync(path.join(report,'server.log'),log);}}
main().catch(e=>{console.error(e);console.error(log.slice(-2500));process.exitCode=1;});
