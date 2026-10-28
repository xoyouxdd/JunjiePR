'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process'),{chromium,webkit,firefox}=require('playwright');
const root=path.resolve(__dirname,'../..');let server,browser,log='';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{try{
  const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
  server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'rotation_formal_fixture.py'),String(port)],{cwd:path.join(root,'backend'),windowsHide:true});
  server.stderr.on('data',d=>log+=d);server.stdout.on('data',d=>log+=d);
  const base='http://127.0.0.1:'+port;let ready=false;
  for(let i=0;i<100;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await sleep(500);}assert(ready,log);
  for(const name of (process.env.ROTATION_FORMAL_ENGINES||'edge').split(',')){
    browser=await(name==='webkit'?webkit:name==='firefox'?firefox:chromium).launch(name==='edge'?{channel:'msedge',headless:true}:{headless:true});
    const contexts=[],pages=[],errors=[];
    for(const no of ['9710000','9710010']){
      const ctx=await browser.newContext({viewport:{width:390,height:844}});contexts.push(ctx);
      assert((await ctx.request.post(base+'/api/login',{data:{employee_no:no,password:'formal-fixture-2026'}})).ok());
      const h=await ctx.request.get(base+'/health').then(r=>r.json());await ctx.request.post(base+'/api/changelog/announcement/read',{data:{version:h.version}});
      const page=await ctx.newPage();page.on('pageerror',e=>errors.push(e.message));pages.push(page);
    }
    const [a,b]=pages;assert((await contexts[0].request.post(base+'/__fixture/cohort',{data:{mixed:true}})).ok());
    for(const page of pages){await page.goto(base+'/?view=actionCenter');await page.locator('[data-rotation-task=depart]').waitFor({timeout:15000});}
    const read=async i=>contexts[i].request.get(base+'/api/rotation-board/me').then(r=>r.json());
    const beforeA=(await read(0)).person,beforeB=(await read(1)).person;
    assert.equal(beforeA.task.batch,beforeB.task.batch);
    assert.notEqual(beforeA.task.id,beforeB.task.id);
    assert.equal(beforeA.assign.entryPost,'岗位1');assert.equal(beforeB.assign.entryPost,'岗位2');
    assert.equal(await a.locator('[data-entry-single],[data-entry-adjust],[data-group-depart]').count(),0);
    for(const page of pages)assert.equal(await page.locator('.rt-task-comparison .rt-task-line').count(),2);
    // A's response is lost after commit; retry uses the same receipt and cannot push twice.
    await a.route('**/api/rotation-board/me/act',async route=>{await route.fetch();await route.abort('failed');},{times:1});
    await a.locator('[data-rotation-task=depart]').click();await a.locator('[data-modal-confirm]').click();
    await a.waitForFunction(()=>document.querySelector('[data-formal-rotation-todo]')?.textContent.includes('在 A1 线'));
    const afterA=(await read(0)).person,stillB=(await read(1)).person;
    assert.equal(afterA.state,'onpost');assert.equal(stillB.state,'ready');
    assert.equal(stillB.confirmationTokens.depart,beforeB.confirmationTokens.depart);
    assert.equal((await contexts[0].request.post(base+'/api/rotation-board/me/act',{data:{action:'depart',confirmation_token:beforeA.confirmationTokens.depart}}).then(r=>r.json())).duplicate,true);
    await b.locator('[data-rotation-task=depart]').click();await b.locator('[data-modal-confirm]').click();
    await b.waitForFunction(()=>document.querySelector('[data-formal-rotation-todo]')?.textContent.includes('在 A1 线'));
    const afterB=(await read(1)).person;assert.equal(afterB.state,'onpost');assert.equal(afterB.post,'岗位2');
    for(const width of [320,390,430,768,1440]){for(const page of pages){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,name+' overflow '+width);}}
    assert.deepEqual(errors,[]);console.log(name+': independent mixed personal routes, lost-response recovery, unchanged peer token, duplicate protection and five widths passed');
    await browser.close();browser=null;
  }
}finally{if(browser)await browser.close();if(server){server.kill();await Promise.race([new Promise(r=>server.once('exit',r)),sleep(3000)]);}}})().catch(e=>{console.error(e);process.exitCode=1;});
