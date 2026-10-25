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
    const [a,b]=pages;assert((await contexts[0].request.post(base+'/__fixture/cohort')).ok());
    for(const page of pages){await page.goto(base+'/?view=actionCenter');await page.locator('[data-rotation-task=depart]').waitFor({timeout:15000});}
    await a.locator('[data-rotation-task=depart]').click();await a.locator('[data-modal-confirm]').click();
    await a.waitForFunction(()=>document.querySelector('[data-formal-rotation-todo] .rt-entry-group')?.textContent.includes('1 / 2'));
    assert.equal(await a.locator('[data-rotation-task=depart]').count(),0);
    const waiting=await contexts[0].request.get(base+'/api/rotation-board/me').then(r=>r.json());assert.equal(waiting.person.state,'ready');
    await b.locator('[data-rotation-task=depart]').click();await b.locator('[data-modal-confirm]').click();
    for(const page of pages)await page.waitForFunction(()=>document.querySelector('[data-formal-rotation-todo]')?.textContent.includes('在 A1 线'));
    // Reset only this isolated fixture. B substitutes for A's relief duty.
    assert((await contexts[0].request.post(base+'/__fixture/cohort')).ok());
    await b.reload();await b.locator('[data-entry-single]').waitFor({timeout:15000});await b.locator('[data-entry-single]').click();
    assert.match(await b.locator('.modal-card[role=dialog]').innerText(),/下班/);await b.locator('[data-modal-confirm]').click();
    await b.waitForFunction(()=>document.querySelector('[data-formal-rotation-todo]')?.textContent.includes('在 A1 线'));
    await a.reload();await a.locator('[data-rotation-task=entry_return]').waitFor({timeout:15000});
    for(const width of [320,390,430,768,1440]){await a.setViewportSize({width,height:900});assert.equal(await a.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,name+' overflow '+width);}
    await a.locator('[data-rotation-task=entry_return]').click();await a.locator('[data-modal-confirm]').click();await a.locator('[data-rotation-task=depart]').waitFor();
    assert.equal((await contexts[0].request.get(base+'/api/rotation-board/me').then(r=>r.json())).person.entryHold,null);
    // Three scheduled entrants, one missing: preview a new pair, then reconfirm.
    assert((await contexts[0].request.post(base+'/__fixture/cohort',{data:{three:true}})).ok());
    await b.reload();await b.locator('[data-entry-adjust]').waitFor();await b.locator('[data-entry-adjust]').click();
    await b.locator('[name=entryMissing][value="9710000"]').check();await b.locator('.form-modal [type=submit]').click();
    await b.locator('[data-modal-confirm]').waitFor();assert.match(await b.getByRole('dialog',{name:'确认新的同行组合'}).innerText(),/剩余人员需要重新各自确认/);
    await b.locator('[data-modal-confirm]').click();
    await b.waitForFunction(()=>document.querySelector('.rt-entry-group')?.textContent.includes('2人协同上岗'));
    const revised=await contexts[1].request.get(base+'/api/rotation-board/me').then(r=>r.json());assert.equal(revised.person.state,'ready');assert.equal(revised.person.entryGroup.confirmed,0);assert.equal(revised.person.assign.mode,'chain');
    await b.goto(base+'/rotation-board');await b.locator('.rt-line').first().waitFor();
    for(const width of [320,390,430,768,1440]){await b.setViewportSize({width,height:900});assert.equal(await b.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,name+' board overflow '+width);}
    const shots=process.env.ROTATION_FORMAL_SCREENSHOTS;
    if(shots){await contexts[0].request.post(base+'/__fixture/cohort');await a.reload();await a.locator('.rt-entry-group').waitFor();await a.setViewportSize({width:390,height:844});fs.mkdirSync(shots,{recursive:true});await a.screenshot({path:path.join(shots,name+'-cohort-mobile.png'),fullPage:true});await b.reload();await b.locator('.rt-entry-group').first().waitFor();await b.setViewportSize({width:1440,height:1000});await b.screenshot({path:path.join(shots,name+'-cohort-board.png'),fullPage:true});}
    // Operator completes a group after one self-vote, without replaying that vote token.
    const operator=await browser.newContext({viewport:{width:1440,height:1000}});
    assert((await operator.request.post(base+'/api/login',{data:{employee_no:'9710020',password:'formal-fixture-2026'}})).ok());
    assert((await contexts[0].request.post(base+'/__fixture/cohort',{data:{three:true}})).ok());
    const self=await contexts[0].request.get(base+'/api/rotation-board/me').then(r=>r.json());
    assert.equal(self.person.assign.entryPost,'岗位3');
    assert((await contexts[0].request.post(base+'/api/rotation-board/me/act',{data:{action:'depart',confirmation_token:self.person.confirmationTokens.depart}})).ok());
    const board=await operator.newPage();board.on('pageerror',e=>errors.push(e.message));
    await board.goto(base+'/rotation-board');await board.locator('[data-group-depart]').waitFor();
    assert.equal(await board.locator('[data-entry-group]').count(),1);
    assert.equal(await board.locator('.rt-entry-group ul').count(),0);
    assert.match(await board.locator('.rt-entry-group').innerText(),/岗位3/);
    await board.locator('[data-group-depart]').click();
    assert.match(await board.getByRole('dialog').innerText(),/确认以下人员整组上岗/);
    await board.getByRole('button',{name:'确认去轮岗',exact:true}).click();
    await board.waitForFunction(()=>!document.querySelector('[data-group-depart]'));
    const result=await contexts[0].request.get(base+'/api/rotation-board/me').then(r=>r.json());
    assert.equal(result.person.state,'onpost');assert.equal(result.person.post,'岗位3');
    await operator.close();
    assert.deepEqual(errors,[]);console.log(name+': personal N/N, operator whole-group after prior vote, final posts, no route details, single relief, self return and five-width layout passed');
    await browser.close();browser=null;
  }
}finally{if(browser)await browser.close();if(server){server.kill();await Promise.race([new Promise(r=>server.once('exit',r)),sleep(3000)]);}}})().catch(e=>{console.error(e);process.exitCode=1;});
