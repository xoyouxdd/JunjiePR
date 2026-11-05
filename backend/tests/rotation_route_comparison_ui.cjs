'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process');
const {chromium,webkit,firefox}=require('playwright');
const root=path.resolve(__dirname,'../..'),work=fs.mkdtempSync(path.join(os.tmpdir(),'rotation-route-comparison-'));
const report=path.join(root,'.handover_review/rotation-route-comparison');fs.mkdirSync(report,{recursive:true});
let server,browser,log='';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function ok(response){assert(response.ok(),await response.text());return response.json();}
async function comparison(scope){
  const card=scope.locator('.rt-task-comparison');await card.waitFor();
  assert.deepEqual(await card.locator('.rt-task-columns>div>p').allTextContents(),['执行前 · 当前站位','执行后 · 预计站位']);
  assert.equal(await card.locator('.rt-task-line').count(),2);
  const panes=await card.locator('.rt-task-line').evaluateAll(lines=>lines.map(line=>[...line.querySelectorAll('.rt-task-slot')].map(row=>({post:row.querySelector('span').textContent,name:row.querySelector('strong').textContent,changed:row.classList.contains('changed')}))));
  assert(panes[0].every(row=>!row.changed),'The before pane must keep its original neutral style');
  assert(panes[1].some(row=>row.changed),'The after pane must highlight changed positions');
  assert.equal(panes[0].length,panes[1].length,'Only actual post rows are shown');
  panes[1].forEach((row,i)=>assert.equal(row.changed,row.name!==panes[0][i].name,'Only changed occupants are highlighted'));
  assert.equal(await card.locator('.rt-task-extra').count(),0);
  const colors=await card.locator('.changed strong').evaluateAll(nodes=>nodes.map(n=>getComputedStyle(n).color.match(/\d+/g).slice(0,3).map(Number)));
  for(const [r,g,b] of colors)assert(b>r&&b>g,'Involved names must be blue');
  return panes;
}
async function redNotice(dialog,waiting){
  const notice=dialog.locator('.rt-route-change');await notice.waitFor();
  const text=await notice.innerText();assert.match(text,/原预计 .*现调整为/);
  if(waiting)assert(text.includes(waiting)&&text.includes('还未上岗，请确认先上。'),JSON.stringify({waiting,text}));
  else assert(!text.includes('还未上岗'));
  const [r,g,b]=await notice.evaluate(n=>getComputedStyle(n).color.match(/\d+/g).slice(0,3).map(Number));
  assert(r>g&&r>b,'Route-change notice must be red');
}
async function main(){try{
  const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
  server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(root,'local_tools/rotation_clock/run.py'),'--demo','--data-dir',path.join(work,'data'),'--port',String(port)],{cwd:root,windowsHide:true,env:{...process.env,PYTHONUTF8:'1'}});
  server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);
  const base='http://127.0.0.1:'+port;
  let ready=false;for(let i=0;i<120;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await sleep(250);}assert(ready,log);
  for(const engine of (process.env.ROTATION_COMPARISON_ENGINES||'edge,webkit,firefox').split(',')){
    browser=await(engine==='webkit'?webkit:engine==='firefox'?firefox:chromium).launch(engine==='edge'?{channel:'msedge',headless:true}:{headless:true});
    const context=await browser.newContext({viewport:{width:1280,height:900}}),request=context.request;
    await ok(await request.post(base+'/api/login',{data:{employee_no:'GSMTEST01',password:'rotation-local-2026'}}));
    const clock=async data=>ok(await request.post(base+'/__local/rotation-clock',{data}));
    const reset=await clock({action:'restart-preview'});await clock({action:'restart',confirm_date:reset.date,restart_token:reset.restart_token});
    await clock({action:'set',date:'2026-11-05',time:'07:30:00'});
    const post=async data=>ok(await request.post(base+'/api/rotation-board/act',{data}));
    const board=async()=>(await ok(await request.get(base+'/api/rotation-board/board'))).day;
    let day=await board();const ids=day.persons.filter(p=>p.state!=='onpost'&&p.start<=450).slice(0,2).map(p=>p.pid);
    assert.equal(ids.length,2,'Use spare crew so both actual lines remain fully occupied');
    for(const pid of ids){await post({action:'pool',pid,pool:'rest'});await post({action:'break_time',pid,remaining:0});}
    await post({action:'sort_time',line:'A',time:'07:15:00'});await post({action:'sort_time',line:'B',time:'07:16:00'});
    day=await board();const first=day.persons.find(p=>p.canDepart&&p.assign?.line==='A'),later=day.persons.find(p=>p.canDepart&&p.assign?.line==='B');
    assert(first&&later,'Two independent forecasts must exist');
    const before=day.lines.map(l=>l.posts.map(p=>p.occ));
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/rotation-board');await page.locator('[data-f-board="'+later.pid+'"]').click();
    await comparison(page.getByRole('dialog'));
    await page.getByRole('button',{name:'确认上岗',exact:true}).click();
    await page.getByRole('heading',{name:'线路已改变，请重新确认',exact:true}).waitFor();
    const dialog=page.getByRole('dialog');await redNotice(dialog,first.name);
    const desktop=await comparison(dialog);
    assert(!desktop[0].some(row=>row.name===later.name),'Before pane shows current occupants, without an incoming extra row');
    assert(desktop[1].some(row=>row.name===later.name&&row.changed));
    assert(!desktop[1].some(row=>row.post.includes('下岗')),'After pane keeps the original post-only layout');
    assert.deepEqual((await board()).lines.map(l=>l.posts.map(p=>p.occ)),before,'Reroute preview never executes');
    await page.screenshot({path:path.join(report,'desktop-'+engine+'.png'),animations:'disabled'});
    await page.getByRole('button',{name:'取消',exact:true}).click();
    const member=await browser.newContext({viewport:{width:390,height:844}});
    await ok(await member.request.post(base+'/api/login',{data:{employee_no:later.pid,password:'rotation-local-2026'}}));
    const health=await ok(await member.request.get(base+'/health'));
    await ok(await member.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}}));
    const mobile=await member.newPage();mobile.on('pageerror',e=>errors.push(e.message));await mobile.goto(base+'/?view=actionCenter');
    await comparison(mobile.locator('[data-formal-rotation-todo]'));
    await mobile.locator('[data-rotation-task=depart]').click();await comparison(mobile.getByRole('dialog'));
    await mobile.locator('[data-modal-confirm]').click();
    await mobile.getByRole('heading',{name:'线路已改变，请重新确认',exact:true}).waitFor();
    const reroute=mobile.getByRole('dialog',{name:'线路已改变，请重新确认',exact:true});
    await redNotice(reroute,first.name);
    assert.deepEqual(await comparison(reroute),desktop,'Mobile and board show identical actual routes');
    assert.equal(await mobile.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await mobile.screenshot({path:path.join(report,'mobile-'+engine+'.png'),animations:'disabled'});
    await mobile.setViewportSize({width:320,height:700});
    assert.equal(await mobile.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await reroute.locator('[data-modal-confirm]').click();
    await mobile.locator('[data-formal-rotation-todo]').filter({hasText:'已确认安排'}).waitFor();
    day=await board();assert.equal(day.lines.find(l=>l.id==='A').posts[0].occ,later.pid);assert.equal(day.persons.find(p=>p.pid===first.pid).state,'rest');
    // A header edit is a different cause and must never name a waiting person.
    await page.evaluate(()=>RT.load());await page.locator('[data-f-board="'+first.pid+'"]').click();
    const expected=(await board()).persons.find(p=>p.pid===first.pid).assign.line;
    const other=expected==='A'?'B':'A';await post({action:'sort_time',line:other,time:'07:00:00'});
    await page.getByRole('button',{name:'确认上岗',exact:true}).click();await page.getByRole('heading',{name:'线路已改变，请重新确认',exact:true}).waitFor();
    await redNotice(page.getByRole('dialog'),null);await comparison(page.getByRole('dialog'));
    await page.getByRole('button',{name:'取消',exact:true}).click();
    await member.close();
    // Complete a real opening from the board: its marker/task clears, its
    // actual and virtual ordering clocks stay unchanged until an ordinary push.
    await clock({action:'set',date:'2026-11-05',time:'08:53:00'});
    day=await board();const a=day.lines.find(l=>l.id==='A');
    await post({action:'post',line:'A',i:2,post_key:a.posts[2].key,open:false,expected_snapshot:day.forecastSnapshot});
    await post({action:'sort_time',line:'A',time:'08:38:00'});await post({action:'sort_time',line:'B',time:'08:45:00'});
    day=await board();const crew=day.persons.filter(p=>p.state!=='onpost'&&p.start<=533).slice(0,3);assert.equal(crew.length,3);
    for(const p of crew){await post({action:'pool',pid:p.pid,pool:'rest'});await post({action:'break_time',pid:p.pid,remaining:0});}
    await page.evaluate(()=>RT.load());await page.locator('[data-f-head=A]').click();await page.locator('[data-f-open]').click();
    await page.getByRole('button',{name:'确定',exact:true}).click();
    await page.waitForFunction(()=>!RT.modalOpen&&RT.data.day.lines.find(l=>l.id==='A').adding);
    day=await board();assert.equal(day.lines.find(l=>l.id==='A').sortAt,518);
    const opening=day.persons.find(p=>p.assign?.kind==='opening');assert(opening);
    assert(day.persons.some(p=>p.assign?.kind==='push'&&p.assign.line==='A'),'Virtual opening keeps the same oldest header');
    await page.locator('[data-f-board="'+opening.pid+'"]').click();await comparison(page.getByRole('dialog'));
    await page.getByRole('button',{name:'确认上岗',exact:true}).click();
    await page.waitForFunction(pid=>!RT.modalOpen&&RT.data.day.lines.find(l=>l.id==='A').posts[0].occ===pid,opening.pid);
    day=await board();const completed=day.persons.find(p=>p.pid===opening.pid),line=day.lines.find(l=>l.id==='A');
    assert.equal(line.sortAt,518);assert.equal(line.adding,false);assert.equal(line.posts[0].since,533);
    assert.equal(completed.assign,null);assert.equal(completed.task,null);
    assert(!day.persons.some(p=>p.assign?.line==='A'&&p.assign.kind==='opening'));
    await page.screenshot({path:path.join(report,'opening-'+engine+'.png'),animations:'disabled'});
    const ordinary=day.persons.find(p=>p.assign?.line==='A'&&p.assign.kind==='push');assert(ordinary);
    await page.locator('[data-f-board="'+ordinary.pid+'"]').click();await page.getByRole('button',{name:'确认上岗',exact:true}).click();
    await page.waitForFunction(()=>RT.data.day.lines.find(l=>l.id==='A').sortAt===533);
    assert.deepEqual(errors,[]);await context.close();await browser.close();browser=null;
    console.log(engine+': original before/after UI; neutral before pane, blue changed positions only after; identical board/mobile routes; red reroute; per-person confirmation; header edit; 320/390px; opening and ordinary header behavior PASS');
  }
}finally{if(browser)await browser.close();if(server&&server.exitCode===null){server.kill();await new Promise(r=>server.once('exit',r));}fs.writeFileSync(path.join(report,'server.log'),log);}}
main().catch(error=>{console.error(error);console.error(log.slice(-2500));process.exitCode=1;});
