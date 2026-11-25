'use strict';
const {resetAndPublish}=require('./support/rotation-daily-fixture.cjs');
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
  server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'rotation_daily_fixture.py'),work,String(port)],{cwd:path.join(root,'backend'),windowsHide:true,env:{...process.env,ROTATION_FORECAST_FIXTURE:'1',ROTATION_SICK_FIXTURE:'1',PYTHONUTF8:'1'}});
  server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);
  const base='http://127.0.0.1:'+port;
  let ready=false;for(let i=0;i<100;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await sleep(300);}assert(ready,log);
  for(const engine of (process.env.ROTATION_FORECAST_ENGINES||'edge,webkit,firefox').split(',')){
    browser=await(engine==='webkit'?webkit:engine==='firefox'?firefox:chromium).launch(engine==='edge'?{channel:'msedge',headless:true}:{headless:true});
    const context=await browser.newContext({viewport:{width:1280,height:900}}),request=context.request;
    await ok(await request.post(base+'/api/login',{data:{employee_no:'GSMTEST01',password:'daily-fixture-2026'}}));
    const post=async data=>ok(await request.post(base+'/api/rotation-board/act',{data}));
    await ok(await request.post(base+'/__fixture/rotation-clock',{data:{action:'set',date:'2026-10-04',time:'07:15'}}));
    await resetAndPublish(request,base,'2026-10-04',ok);
    const board=async()=>(await ok(await request.get(base+'/api/rotation-board/board'))).day;
    let day=await board();assert.equal(day.ruleVersion,2);
    const seated=day.lines[0].posts.find(p=>p.occ).occ;
    await post({action:'pool',pid:seated,pool:'rest'});
    await post({action:'break_time',pid:seated,remaining:0});
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/rotation-board');
    await page.locator('[data-f-board="'+seated+'"]').waitFor({timeout:15000});
    assert.equal(await page.locator('h2').filter({hasText:'待出发'}).count(),0);
    assert.equal(await page.getByRole('button',{name:'全线关闭',exact:true}).count(),0);
    const boardTotals=page.locator('#rtBoardTotals');
    await boardTotals.waitFor();
    const display=await browser.newContext();
    try{
      await ok(await display.request.post(base+'/api/login',{data:{employee_no:'6666666',password:'1243'}}));
      const screen=await display.newPage();
      await screen.goto(base+'/rotation/screen');
      const screenTotals=screen.locator('#rtScreenTotals');
      await screenTotals.waitFor();
      const numbers=await boardTotals.locator('[data-rotation-total]').allTextContents();
      assert.equal(numbers.length,4);
      assert.deepEqual(numbers,await screenTotals.locator('[data-rotation-total]').allTextContents());
    }finally{await display.close();}
    assert(await page.locator('[data-f-board="'+seated+'"]').locator('..').evaluate(el=>el.classList.contains('rt-can-board')));
    await page.locator('[data-f-board="'+seated+'"]').click();
    await page.getByRole('button',{name:'确认上岗',exact:true}).click();
    await page.waitForFunction(pid=>RT.data.day.persons.find(p=>p.pid===pid).state==='onpost',seated);
    assert.equal((await board()).persons.find(p=>p.pid===seated).state,'onpost');
    const head=page.locator('[data-f-head=A]');
    const previousTime=await head.locator('.rt-sort-time').innerText();
    await head.click({button:'right'});
    await page.getByRole('button',{name:'修改排序时间',exact:true}).click();
    await page.locator('[name=time]').fill('00:00:01');
    await page.getByRole('button',{name:'确定',exact:true}).click();
    await head.getByText('00:00:01',{exact:true}).waitFor();
    assert.equal(await head.locator('.rt-previous-sort-time').innerText(),'上次 '+previousTime);
    // Removing someone immediately refreshes all remaining forecasts.
    day=await board();const person=day.persons.find(p=>p.state==='rest'||p.state==='pending');
    await post({action:'pool',pid:person.pid,pool:'rest'});
    await post({action:'break_time',pid:person.pid,remaining:0});
    await page.evaluate(()=>RT.load());
    await page.locator('[data-f-person="'+person.pid+'"]').click();
    await page.getByRole('button',{name:'修改餐休剩余时间',exact:true}).click();
    await page.locator('[name=remaining]').fill('20');await page.getByRole('button',{name:'确定',exact:true}).click();
    await page.waitForFunction(pid=>RT.data.day.persons.find(p=>p.pid===pid).readyAt===455,person.pid);
    await page.locator('[data-f-board="'+person.pid+'"]').waitFor({state:'hidden'});
    // New personnel dialog searches on demand; temporary identities remain local records.
    await page.locator('[data-f-add]').first().click();
    await page.locator('[name=query]').fill('临时伙伴');await page.locator('[name=temporary]').check();
    await page.locator('[name=start]').fill('08:00');await page.locator('[name=end]').fill('00:30');
    await page.getByRole('button',{name:'新增',exact:true}).click();
    await page.waitForFunction(()=>RT.data.day.persons.some(p=>p.name==='临时伙伴'));
    await page.waitForFunction(()=>!RT.submitting&&!RT.modalOpen);
    const dragPid=(await board()).lines[0].posts[0].occ;
    // Keep drag events on one board render, including when polling refreshes the live view.
    const dropPerson=(source,targetSelector)=>page.evaluate(({source,selector})=>{
      const element=document.querySelector(source),target=document.querySelector(selector);
      if(!element||!target)throw new Error('Drag endpoints are missing from the rendered board');
      const dataTransfer=new DataTransfer();
      element.dispatchEvent(new DragEvent('dragstart',{bubbles:true,cancelable:true,dataTransfer}));
      if(!dataTransfer.getData('application/x-rotation-person'))throw new Error('Person drag did not start');
      target.dispatchEvent(new DragEvent('dragover',{bubbles:true,cancelable:true,dataTransfer}));
      target.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer}));
      element.dispatchEvent(new DragEvent('dragend',{bubbles:true,dataTransfer}));
      return {modal:document.querySelector('#rtModal h3')?.textContent,toast:document.getElementById('toast')?.textContent};
    },{source,selector:targetSelector});
    const [rested]=await Promise.all([
      page.waitForResponse(response=>response.request().method()==='POST'&&response.url().endsWith('/api/rotation-board/act')&&response.request().postDataJSON()?.action==='pool'),
      dropPerson('[data-f-slot="A#0"] [data-f-drag="'+dragPid+'"]','[data-f-pool=rest]'),
    ]);
    await ok(rested);
    await page.waitForFunction(pid=>RT.data.day.persons.find(p=>p.pid===pid).state==='rest',dragPid);
    await page.waitForFunction(()=>!RT.submitting);
    const placement=await dropPerson('[data-f-pool=rest] [data-f-drag="'+dragPid+'"]','[data-f-slot="A#0"]');
    assert.equal(placement.modal,'现场安排',JSON.stringify(placement));
    await page.locator('[name=place_mode]').selectOption('swap');
    const [placed]=await Promise.all([
      page.waitForResponse(response=>response.request().method()==='POST'&&response.url().endsWith('/api/rotation-board/act')&&response.request().postDataJSON()?.action==='place'),
      page.getByRole('button',{name:'确定',exact:true}).click(),
    ]);
    await ok(placed);
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
    await ok(await request.post(base+'/__fixture/rotation-clock',{data:{action:'jump',minutes:1}}));await page.evaluate(()=>RT.load());
    assert.equal(await page.getByRole('dialog').count(),0);
    assert.equal((await board()).persons.find(p=>p.pid===reviewPid).state,'exception');
    assert.equal(await page.locator('[data-f-board="'+reviewPid+'"]').count(),0);
    // The working day and live clock continue after midnight; readiness and
    // the blue action use the same time base instead of clamping at 24:00.
    await post({action:'shift',pid:reviewPid,start:'07:15',end:'01:30'});
    await post({action:'pool',pid:reviewPid,pool:'rest'});
    const clock=await ok(await request.get(base+'/api/rotation-board/board'));
    await post({action:'break_time',pid:reviewPid,remaining:1445-clock.clock.minute});
    await ok(await request.post(base+'/__fixture/rotation-clock',{data:{action:'set',date:'2026-10-05',time:'00:05'}}));await page.evaluate(()=>RT.load());
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
    const withdrawal=page.getByLabel('撤岗方式');
    assert.equal(await withdrawal.inputValue(),'push');
    await withdrawal.selectOption('rest');
    await page.getByText('直接送休息区，表头轮岗时间保持原值。',{exact:true}).waitFor();
    await withdrawal.selectOption('push');
    await page.locator('[name=time]').fill('00:15');
    const destination=page.getByLabel('顺推安排');
    assert.equal(await destination.inputValue(),'push_off');
    await destination.selectOption({label:'到点时顺推接替病假人员'});
    assert.equal(await destination.inputValue(),'push_sick');
    await destination.selectOption({label:'到点时顺推加岗'});
    assert.equal(await destination.inputValue(),'push_open');
    await page.getByRole('button',{name:'确定',exact:true}).click();
    await page.waitForFunction(()=>{
      const post=RT.data.day.lines[0].posts[1];
      return post.closeAt==='24:15:00'&&post.closeMode==='push_open';
    });
    await ok(await request.post(base+'/__fixture/rotation-clock',{data:{action:'set',date:'2026-10-05',time:'00:20'}}));
    await page.evaluate(()=>RT.load());
    await upcoming.locator('[data-f-board]').waitFor();
    const support=(await board()).persons.find(p=>p.name==='午夜支援');
    assert.equal(support.state,'ready');
    assert.equal(support.directShiftEntry,1460);
    assert.equal((await board()).lines[0].posts[1].open,false);
    assert.equal(await upcoming.locator('[data-f-arrive]').count(),0);
    for(const width of [320,390,768,1280]){
      await page.setViewportSize({width,height:900});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'overflow '+width);
      const totalsBox=await boardTotals.boundingBox();
      assert(totalsBox&&totalsBox.x+totalsBox.width<=width,'totals overflow '+width);
    }
    const shots=path.join(root,'.handover_review','rotation-forecast-review');fs.mkdirSync(shots,{recursive:true});
    await page.screenshot({path:path.join(shots,'forecast-'+engine+'.png'),fullPage:true});
    await ok(await request.post(base+'/__fixture/rotation-clock',{data:{action:'set',date:'2026-10-06',time:'10:30'}}));
    await resetAndPublish(request,base,'2026-10-06',ok);
    await page.evaluate(()=>RT.load());
    const sickTotal=boardTotals.locator('[data-rotation-total=sick]');
    assert.equal(await sickTotal.innerText(),'2');
    await dropPerson('[data-f-pool=away] [data-f-drag="9100008"]','[data-f-pool=rest]');
    await page.locator('[data-f-pool=rest] [data-f-person="9100008"]').waitFor();
    assert.equal(await sickTotal.innerText(),'1');
    await page.locator('[data-f-pool=away] [data-f-person="9100009"]').click();
    await page.getByLabel('目标池').selectOption('meal');
    await page.locator('[name=start]').fill('07:30');
    await page.locator('[name=end]').fill('16:30');
    await page.getByRole('button',{name:'确定',exact:true}).click();
    await page.locator('[data-f-pool=meal] [data-f-person="9100009"]').waitFor();
    assert.equal(await sickTotal.innerText(),'0');
    await post({action:'add_person',temporary:true,name:'批次时间验证',start:'13:30',end:'22:00'});
    const batchWorker=(await board()).persons.find(p=>p.name==='批次时间验证');
    const batchClock=async data=>ok(await request.post(base+'/__fixture/rotation-clock',{data}));
    await batchClock({action:'set',date:'2026-10-06',time:'13:26'});
    const checked=(await board()).persons.find(p=>p.pid===batchWorker.pid);
    await post({action:'batch_check_in',pid:checked.pid,confirmation_token:checked.confirmationTokens.batch_check_in});
    await batchClock({action:'jump',minutes:1+5/60});
    const assigned=(await board()).persons.find(p=>p.pid===batchWorker.pid);
    assert.equal(assigned.state,'onpost');
    assert.equal(assigned.entrySortAt,807+5/60);
    await page.evaluate(()=>RT.load());
    const entryStamp=page.locator('[data-f-person="'+assigned.pid+'"] .rt-post-entry-time');
    await entryStamp.filter({hasText:'轮岗 13:27:05'}).waitFor();
    await page.locator('[data-f-head="'+assigned.line+'"] .rt-sort-time').filter({hasText:'13:27:05'}).waitFor();
    assert.deepEqual(errors,[]);
    console.log(engine+': forecast, individual execution, head edit, responsive widths, sick leave corrections, batch entry clock PASS');
    await context.close();await browser.close();browser=null;
  }
}finally{if(browser)await browser.close();if(server){server.kill();await new Promise(r=>server.once('exit',r));}}}
main().catch(error=>{console.error(error,log.slice(-7000));process.exitCode=1;});
