'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process');
const {chromium,webkit,firefox}=require('playwright');
const root=path.resolve(__dirname,'../..'),work=fs.mkdtempSync(path.join(os.tmpdir(),'rotation-local-clock-ui-'));
const report=path.join(root,'.handover_review/rotation-local-clock');fs.mkdirSync(report,{recursive:true});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function ok(response){assert(response.ok(),await response.text());return response.json();}
let server,browser,log='';
async function main(){try{
  const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
  server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(root,'local_tools/rotation_clock/run.py'),'--demo','--data-dir',path.join(work,'data'),'--port',String(port)],{cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1'}});
  server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);
  const base='http://127.0.0.1:'+port;
  let ready=false;for(let i=0;i<120;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await sleep(300);}assert(ready,log);
  for(const engine of (process.env.ROTATION_CLOCK_ENGINES||'edge,webkit,firefox').split(',')){
    browser=await(engine==='webkit'?webkit:engine==='firefox'?firefox:chromium).launch(engine==='edge'?{channel:'msedge',headless:true}:{headless:true});
    const context=await browser.newContext({viewport:{width:1680,height:1000}}),request=context.request;
    await ok(await request.post(base+'/api/login',{data:{employee_no:'GSMTEST01',password:'rotation-local-2026'}}));
    const clock=async data=>ok(await request.post(base+'/__local/rotation-clock',{data}));
    const reset=await clock({action:'restart-preview'});await clock({action:'restart',confirm_date:reset.date,restart_token:reset.restart_token});
    const post=async data=>ok(await request.post(base+'/api/rotation-board/act',{data}));
    const state=async()=>ok(await request.get(base+'/api/rotation-board/board'));
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/rotation-board');await page.locator('#lcDate').waitFor();
    await page.locator('#lcDate').fill('2026-11-05');await page.locator('#lcTime').fill('07:30:12');
    await page.getByRole('button',{name:'应用时间',exact:true}).click();
    await page.waitForFunction(()=>RT.data?.clock.minute===450.2&&RT.data.clock.paused);
    assert.match(await page.locator('#localSimulationBadge').innerText(),/本地模拟时间.*已暂停.*×1/);
    // Updating the board must not replace a partially edited clock input.
    await page.locator('#lcTime').fill('07:40:23');await page.evaluate(()=>RT.load());
    assert.equal(await page.locator('#lcTime').inputValue(),'07:40:23');
    await page.locator('#lcTime').fill('07:30:12');
    await page.locator('#lcSpeed').selectOption('60');
    await page.waitForFunction(()=>RT.data.clock.speed===60);
    await page.getByRole('button',{name:'继续',exact:true}).click();
    await page.waitForFunction(()=>!RT.data.clock.paused);
    await page.getByRole('button',{name:'暂停',exact:true}).click();
    await page.waitForFunction(()=>RT.data.clock.paused);
    const frozen=(await state()).clock.minute;await sleep(150);
    assert.equal((await state()).clock.minute,frozen);
    await page.locator('#lcTime').fill('07:30:12');await page.getByRole('button',{name:'应用时间',exact:true}).click();
    await page.waitForFunction(()=>RT.data.clock.minute===450.2&&RT.data.clock.paused);
    let data=await state();const ids=data.day.persons.filter(p=>p.state==='onpost').slice(0,2).map(p=>p.pid);
    for(const pid of ids){await post({action:'pool',pid,pool:'rest'});await post({action:'break_time',pid,remaining:1});}
    await page.evaluate(()=>RT.load());await page.getByRole('button',{name:'快进1分钟',exact:true}).click();
    await page.waitForFunction(()=>RT.data.clock.minute===451.2);
    for(const pid of ids)assert.equal((await state()).day.persons.find(p=>p.pid===pid).state,'rest');
    // A real personal page confirms only itself; the other person remains unexecuted.
    const member=await browser.newContext({viewport:{width:390,height:844}});
    await ok(await member.request.post(base+'/api/login',{data:{employee_no:ids[0],password:'rotation-local-2026'}}));
    const health=await ok(await member.request.get(base+'/health'));
    await ok(await member.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}}));
    const personal=await member.newPage();personal.on('pageerror',e=>errors.push(e.message));
    await personal.goto(base+'/?view=actionCenter');
    await personal.locator('[data-rotation-task=depart]').click();
    await personal.locator('[data-modal-confirm]').click();
    // A later forecast may now select a different actual line while earlier
    // people remain unconfirmed. Preserve the required second confirmation.
    const changed=personal.getByRole('dialog',{name:'线路已改变，请重新确认',exact:true});
    await Promise.race([changed.waitFor(),personal.locator('[data-formal-rotation-todo]').filter({hasText:'已确认安排'}).waitFor()]);
    if(await changed.isVisible())await changed.locator('[data-modal-confirm]').click();
    await personal.locator('[data-formal-rotation-todo]').filter({hasText:'已确认安排'}).waitFor();
    assert.equal((await ok(await member.request.get(base+'/api/rotation-board/me'))).person.state,'onpost');
    assert.notEqual((await state()).day.persons.find(p=>p.pid===ids[1]).state,'onpost');
    // Screen reads the same paused backend clock, using its normal login.
    const screen=await browser.newContext();await ok(await screen.request.post(base+'/api/login',{data:{employee_no:'6666666',password:'1243'}}));
    const display=await screen.newPage();display.on('pageerror',e=>errors.push(e.message));await display.goto(base+'/rotation/screen');
    await display.waitForFunction(()=>RT.data?.clock.minute===451.2&&RT.data.clock.paused);
    await display.waitForFunction(()=>document.querySelector('#rtClock b')?.textContent==='07:31:12');
    assert.match(await display.locator('#localSimulationBadge').innerText(),/本地模拟时间/);
    await page.getByRole('button',{name:'快进10分钟',exact:true}).click();
    await page.waitForFunction(()=>RT.data.clock.minute===461.2);
    assert.equal((await state()).day.persons.find(p=>p.pid===ids[1]).state,'exception');
    await display.waitForFunction(pid=>RT.data.day.persons.find(p=>p.pid===pid).state==='exception',ids[1]);
    // Two separate confirmations; cancellation and a wrong date do not clear results.
    await page.getByRole('button',{name:'从当天初始班表重新开始',exact:true}).click();
    await page.getByRole('button',{name:'取消',exact:true}).click();
    assert.equal((await state()).clock.minute,461.2);
    await page.getByRole('button',{name:'从当天初始班表重新开始',exact:true}).click();
    await page.getByRole('button',{name:'继续核对重测日期',exact:true}).click();
    await page.locator('[name=confirm_date]').fill('2026-11-04');
    await page.getByRole('button',{name:'确认清理并从初始班表开始',exact:true}).click();
    assert.equal((await state()).clock.minute,461.2);
    await page.locator('[name=confirm_date]').fill('2026-11-05');
    await page.getByRole('button',{name:'确认清理并从初始班表开始',exact:true}).click();
    await page.waitForFunction(()=>RT.data?.clock.minute===240&&RT.data.clock.paused);
    // Calendar and working date remain distinct over midnight until the actual final shift.
    await page.locator('#lcDate').fill('2026-11-05');await page.locator('#lcTime').fill('23:59:12');
    await page.getByRole('button',{name:'应用时间',exact:true}).click();await page.waitForFunction(()=>RT.data.clock.minute===1439.2);
    await page.getByRole('button',{name:'快进1分钟',exact:true}).click();await page.waitForFunction(()=>RT.data.clock.minute===1440.2);
    await page.waitForFunction(()=>document.querySelector('#rtClock b')?.textContent==='00:00:12');
    assert.equal((await state()).clock.calendarDate,'2026-11-06');assert.equal((await state()).day.status,'live');
    for(const width of [1680,1280,768,390,320]){
      await page.setViewportSize({width,height:1000});await sleep(70);
      assert(await page.locator('#rtClockBar').isVisible());
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'overflow at '+width);
    }
    await page.setViewportSize({width:1680,height:1000});await page.screenshot({path:path.join(report,engine+'.png')});
    assert.deepEqual(errors,[]);await member.close();await screen.close();await context.close();await browser.close();browser=null;
    console.log(engine+': local controls, seconds, pause/speed/jump, personal/screen synchronization, independent confirmation, timeout, double reset, midnight and responsive widths PASS');
  }
}finally{if(browser)await browser.close();if(server&&server.exitCode===null){server.kill();await new Promise(r=>server.once('exit',r));}fs.writeFileSync(path.join(report,'server.log'),log);}}
main().catch(e=>{console.error(e);console.error(log);process.exitCode=1;});
