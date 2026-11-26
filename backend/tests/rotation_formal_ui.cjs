'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http'),net=require('node:net');
const {spawn}=require('node:child_process'),{chromium,webkit,firefox}=require('./support/browser-runtime.cjs');
const root=path.resolve(__dirname,'../..');let server,browser,log='';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{try{
  const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
  server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'rotation_formal_fixture.py'),String(port)],{cwd:path.join(root,'backend'),windowsHide:true});server.stderr.on('data',d=>log+=d);server.stdout.on('data',d=>log+=d);
  const base='http://127.0.0.1:'+port;let ready=false;
  for(let i=0;i<100;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await sleep(500);}assert(ready,log);
  for(const engine of (process.env.ROTATION_FORMAL_ENGINES||'edge').split(',')){
    browser=await(engine==='webkit'?webkit:engine==='firefox'?firefox:chromium).launch(engine==='edge'?{channel:'msedge',headless:true}:{headless:true});
    const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
    assert((await context.request.post(base+'/api/login',{data:{employee_no:'9710000',password:'formal-fixture-2026'}})).ok());
    const health=await context.request.get(base+'/health').then(r=>r.json());await context.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}});
    await page.goto(base+'/rotation-board');await page.locator('.rt-line').first().waitFor();
    assert.equal(await page.locator('[data-tab=settings]').count(),0);assert.equal(await page.locator('#rtClockBar').isVisible(),false);assert.equal(await page.locator('#rtTabs').isVisible(),false);
    assert.equal(await page.locator('#rtApp button[data-f-person]').count(),0);assert.equal(await page.locator('[data-mode101-toggle],[data-f-post]').count(),0);
    // The normal workspace inbox exposes only this employee's token-confirmed task.
    await page.goto(base+'/?view=actionCenter');await page.locator('[data-formal-rotation-todo] .rt-task-comparison').waitFor({timeout:12000}).catch(async e=>{console.error(errors,await page.locator('body').innerText());throw e;});
    if(engine==='edge'){
      const cookie=(await context.cookies()).map(c=>c.name+'='+c.value).join('; ');
      const streams=await Promise.all(Array.from({length:15},()=>new Promise((resolve,reject)=>{const req=http.get(base+'/api/rotation-board/stream',{headers:{Cookie:cookie}},res=>{if(res.statusCode!==200){reject(new Error('Stream auth '+res.statusCode));return;}res.once('data',()=>resolve({req,res}));res.on('error',()=>{});});req.on('error',reject);req.setTimeout(12000,()=>{req.destroy();reject(new Error('Stream timeout'));});})));
      try{const pool=await context.request.get(base+'/__fixture/pool').then(r=>r.json());assert(pool.checked_out<8,'SSE holds database connections: '+JSON.stringify(pool));assert((await context.request.get(base+'/api/rotation-board/board')).ok());}finally{streams.forEach(s=>{s.req.destroy();s.res.destroy();});}
      const watcher=await browser.newContext({viewport:{width:1280,height:900}});
      await watcher.request.post(base+'/api/login',{data:{employee_no:'9710010',password:'formal-fixture-2026'}});
      const board=await watcher.newPage();await board.goto(base+'/rotation-board');await board.locator('.rt-line').first().waitFor();
      assert.equal(await board.evaluate(()=>RT.data.day.persons.find(p=>p.pid==='9710000').state),'ready');
      // The server commits, then the browser loses the response: recover by the same step token.
      await page.route('**/api/rotation-board/me/act',async route=>{await route.fetch();await route.abort('failed');},{times:1});
      await page.locator('[data-rotation-task=depart]').click();
      await page.waitForFunction(()=>document.querySelector('[data-formal-rotation-todo]')?.textContent.includes('最近安排生效'));
      assert.match(await page.locator('[data-formal-rotation-todo]').innerText(),/已确认上岗安排.*前往/s);
      assert.equal(await page.locator('[data-formal-rotation-todo] .rt-task-comparison').count(),0,'Confirmed routes stay collapsed');
      const {person}=await context.request.get(base+'/api/rotation-board/me').then(r=>r.json());
      let reviewWrites=0;
      const countWrite=request=>{if(new URL(request.url()).pathname==='/api/rotation-board/me/act'&&request.method()==='POST')reviewWrites++;};
      page.on('request',countWrite);
      await page.getByRole('button',{name:'查看推岗路线',exact:true}).click();
      await page.getByRole('heading',{name:'本次已确认推岗路线',exact:true}).waitFor();
      const finalPosts=page.getByRole('dialog').locator('.rt-movement-row');
      assert.equal(await finalPosts.count(),3);
      assert.equal(await finalPosts.getByText(person.name,{exact:true}).count(),1);
      await page.locator('[data-modal-confirm]').click();
      assert.equal(reviewWrites,0,'viewing the confirmed route must not submit again');
      page.off('request',countWrite);
      await page.reload();
      await page.getByRole('button',{name:'查看推岗路线',exact:true}).waitFor();
      assert.equal((await context.request.get(base+'/api/rotation-board/me').then(r=>r.json())).person.state,'onpost');

      await page.waitForFunction(()=>document.querySelector('.workbench-heading strong')?.textContent==='0');
      await board.waitForFunction(()=>['heading','onpost'].includes(RT.data.day.persons.find(p=>p.pid==='9710000').state),{timeout:15000});
      await watcher.close();
    }
    for(const width of [320,390,430,768,1440]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,engine+' inbox overflow '+width);}
    const shots=process.env.ROTATION_FORMAL_SCREENSHOTS;
    if(shots){await page.locator('#toast.show').waitFor({state:'hidden'});fs.mkdirSync(shots,{recursive:true});await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(shots,engine+'-formal-todo.png'),fullPage:true});}
    await context.request.post(base+'/api/logout');assert((await context.request.post(base+'/api/login',{data:{employee_no:'9710010',password:'formal-fixture-2026'}})).ok());
    await page.goto(base+'/rotation-board');await page.locator('.rt-line').first().waitFor();
    assert.equal(await page.locator('[data-tab=records]').count(),0);assert.equal(await page.locator('[data-tab=settings]').count(),0);
    await page.locator('[data-mode101-toggle]').waitFor();
    assert.equal(await page.locator('[data-wf-undo]').count(),0);
    assert((await page.locator('#rtApp button[data-f-person]').count())>0);
    await page.locator('#rtApp button[data-f-person]').first().click();await page.locator('[role=dialog]').waitFor();assert.equal(await page.locator('[data-a=record]').count(),0);await page.locator('[data-close]').click();
    const state=await context.request.get(base+'/api/rotation-board/board').then(r=>r.json());
    const tower=state.day.lines[0].posts[0], nextPost=state.day.lines[0].posts[1];
    assert((await context.request.post(base+'/api/rotation-board/act',{data:{action:'rename_post',post_key:tower.key,expected_name:tower.name,name:'塔台'}})).ok());
    await page.evaluate(()=>RT.load());
    await page.getByRole('button',{name:'101 模式',exact:true}).click();
    await page.getByRole('button',{name:'确认开启',exact:true}).click();
    const fixed=page.locator('.rt-mode101-fixed [data-mode101-post]');
    await fixed.waitFor();
    assert.match(await fixed.innerText(),/（塔台）/);
    const towerRow=page.locator('[data-f-slot="A1#0"]'),normalRow=page.locator('[data-f-slot="A1#1"]');
    await normalRow.locator('[data-f-drag]').dragTo(towerRow);
    await page.getByRole('heading',{name:'确认交换人员',exact:true}).waitFor();
    await page.getByRole('button',{name:'确认',exact:true}).click();
    await towerRow.locator(`[data-f-drag="${nextPost.occ}"]`).waitFor();
    assert.match(await towerRow.getAttribute('class'),/rt-mode101-fixed/);
    await normalRow.locator(`[data-f-drag="${tower.occ}"]`).waitFor();
    assert.doesNotMatch(await normalRow.getAttribute('class'),/rt-mode101-fixed/);
    await fixed.dragTo(normalRow);
    await page.getByRole('heading',{name:'确认交换人员',exact:true}).waitFor();
    await page.getByRole('button',{name:'确认',exact:true}).click();
    await towerRow.locator(`[data-f-drag="${tower.occ}"]`).waitFor();
    await normalRow.locator(`[data-f-drag="${nextPost.occ}"]`).waitFor();
    await fixed.click();
    await page.getByRole('button',{name:'调整应到岗位',exact:true}).click();
    await page.getByLabel('应到岗位').selectOption(nextPost.key);
    await page.getByRole('button',{name:'确定',exact:true}).click();
    await page.waitForFunction(key=>RT.data.day.lines[0].posts[0].mode101.target===key,nextPost.key);
    await page.getByRole('button',{name:'结束 101 模式',exact:true}).click();
    await page.getByRole('button',{name:'确认结束',exact:true}).click();
    await page.waitForFunction(()=>RT.data.day.lines[0].posts[0].mode101.phase==='return');
    await page.locator('.rt-mode101-fixed').waitFor({state:'detached'});
    await context.request.post(base+'/api/logout');assert((await context.request.post(base+'/api/login',{data:{employee_no:'9710020',password:'formal-fixture-2026'}})).ok());
    await page.goto(base+'/rotation-board');await page.locator('[data-tab=settings]').click();await page.locator('#settingsForm').waitFor();
    assert.equal(await page.locator('[data-reset]:visible').count(),0);assert.equal(await page.locator('#rtClockBar').isVisible(),false);
    await page.locator('[data-tab=draft]').click();await page.locator('.rt-daily-calendar').waitFor();
    assert.deepEqual(errors,[]);console.log(engine+': formal CM read-only, personal inbox/line, TR operations and restricted tabs, manager settings/daily, five widths passed');
    await browser.close();browser=null;
  }
}finally{if(browser)await browser.close();if(server){server.kill();await Promise.race([new Promise(r=>server.once('exit',r)),sleep(3000)]);}}})().catch(e=>{console.error(e);process.exitCode=1;});
