'use strict';
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs'),os=require('node:os'),net=require('node:net');
const {spawn}=require('node:child_process'),{chromium,webkit}=require('playwright');
const root=path.resolve(__dirname,'../..'),sleep=ms=>new Promise(r=>setTimeout(r,ms));let server,browser,log='';
(async()=>{try{
 const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
 server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'rotation_daily_fixture.py'),fs.mkdtempSync(path.join(os.tmpdir(),'rotation-early-')),String(port)],{cwd:path.join(root,'backend'),windowsHide:true});
 server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);const base='http://127.0.0.1:'+port;
 for(let i=0;;i++){try{if((await fetch(base+'/health')).ok)break;}catch{}assert(i<100&&server.exitCode===null,log);await sleep(300);}
 for(const [name,type,options]of [['edge',chromium,{channel:'msedge'}],['webkit',webkit,{}]]){
  browser=await type.launch({...options,headless:true});const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const ok=async response=>{assert(response.ok(),await response.text());return response.json();};
  await ok(await context.request.post(base+'/api/login',{data:{employee_no:'GSMTEST01',password:'daily-fixture-2026'}}));
  await ok(await context.request.post(base+'/api/rotation/enter',{data:{account:'7777777'}}));
  await ok(await context.request.put(base+'/api/rotation/config',{data:{lines:[{id:'A',posts:[{name:'小C',seven:1},{name:'普通岗1'},{name:'普通岗2'}]},{id:'B',posts:[{name:'迎宾'},{name:'快迎'},{name:'普通岗3'}]},...['D2','E'].map(id=>({id,standby:true,posts:Array.from({length:5},(_,i)=>({name:'备用'+i}))}))]}}));
  const date='2026-10-06',url=base+'/api/rotation/daily/'+date;
  const before=await ok(await context.request.get(url));await ok(await context.request.post(url,{data:{action:'draft_generate',revision:before.revision}}));
  await page.goto(base+'/rotation');await page.locator('[data-tab=draft]').click();
  await page.locator(`[data-daily-date-button="${date}"]`).click();await page.waitForFunction(date=>daily.data?.date===date&&!daily.loading,date);
  const initial=await ok(await context.request.get(url)),pid=initial.draft.plan.crew['B#0'];assert(pid);assert.equal(initial.draft.persons[pid].start,435);
  await page.locator('[data-daily-person="B#0"]').dragTo(page.locator('[data-daily-person="A#0"]'));
  await page.getByRole('button',{name:'确认交换',exact:true}).waitFor();assert((await page.locator('#rtModal').innerText()).includes('为7点岗位'));
  await page.getByRole('button',{name:'确认交换',exact:true}).click();
  await page.waitForFunction(pid=>!daily.busy&&daily.data?.draft?.plan.crew['A#0']===pid,pid);
  const after=await ok(await context.request.get(url));assert.equal(after.draft.plan.crew['A#0'],pid);assert(!after.draft.plan.seven['A#0']);assert.equal(after.draft.persons[pid].start,435);
  assert.equal(await page.locator('[data-draft=seven][data-key="A#0"]').inputValue(),pid);
  assert(await page.locator('[data-draft=seven][data-key="A#0"] option').count()>2);
  assert.equal(await page.locator('[data-daily-rename="A#0"]').innerText(),'小C');
  assert.equal(await page.locator('.rt-daily-post-label .rt-badge').first().innerText(),'7点岗位');
  await page.locator('[data-daily-line="D2"]').click({button:'right'});
  await page.getByRole('button',{name:'启用线路',exact:true}).waitFor();
  assert((await page.locator('#rtModal').innerText()).includes('不提前开始计时'));
  await page.getByRole('button',{name:'启用线路',exact:true}).click();
  await page.waitForFunction(()=>!daily.busy&&daily.data?.draft?.lines.find(l=>l.id==='D2')?.standby===false);
  assert.equal(await page.locator('[data-daily-person="D2#0"]').getAttribute('draggable'),'true');
  const assignedBefore=await ok(await context.request.get(url)),moved=assignedBefore.draft.plan.crew['B#1'];assert(moved);
  await page.locator('[data-daily-person="B#1"]').dragTo(page.locator('[data-daily-person="D2#0"]'));
  await page.getByRole('button',{name:'确认交换',exact:true}).click();
  await page.waitForFunction(pid=>!daily.busy&&daily.data?.draft?.plan.crew['D2#0']===pid,moved);
  await page.locator('[data-daily-line="D2"]').click({button:'right'});
  await page.getByRole('button',{name:'停用线路',exact:true}).click();
  await page.waitForFunction(()=>!daily.busy&&daily.data?.draft?.lines.find(l=>l.id==='D2')?.standby===true);
  const disabled=await ok(await context.request.get(url));assert(!Object.values(disabled.draft.plan.crew).includes(moved));
  await page.locator('[data-daily-line="E"]').click();
  await page.getByRole('button',{name:'启用线路',exact:true}).click();
  await page.waitForFunction(()=>!daily.busy&&daily.data?.draft?.lines.find(l=>l.id==='E')?.standby===false);
  await page.locator('[data-daily-line="E"]').press('Enter');
  await page.getByRole('button',{name:'停用线路',exact:true}).waitFor();await page.locator('#rtModal [data-close]').click();
  await page.locator('[data-daily-rename="A#0"]').dblclick();await page.locator('.rt-inline-post-name').fill('小C改名');await page.locator('.rt-inline-post-name').press('Enter');
  await page.waitForFunction(()=>daily.data?.draft?.lines[0].posts[0].name==='小C改名'&&!RT.editingPost);
  assert.equal(await page.locator('.rt-daily-post-label .rt-badge').first().innerText(),'7点岗位');
  for(const width of [320,390,1024]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);}
  assert.deepEqual(errors,[]);console.log(name+': cross-shift early-post swap, reminder, dropdown, rename, right-click/click/keyboard line enable and disable, and narrow layout passed');await browser.close();browser=null;
 }
}finally{if(browser)await browser.close();if(server&&server.exitCode===null){server.kill();await new Promise(r=>{server.once('exit',r);setTimeout(r,4000);});}}
})().catch(e=>{console.error(e,log.slice(-2500));process.exitCode=1;});
