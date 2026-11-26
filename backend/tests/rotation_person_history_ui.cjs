'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process'),{chromium,webkit}=require('./support/browser-runtime.cjs');
const root=path.resolve(__dirname,'../..'),out=path.join(root,'.handover_review/rotation-person-history');fs.mkdirSync(out,{recursive:true});
let server,browser,log='';
async function ok(r){assert(r.ok(),await r.text());return r.json();}
(async()=>{try{
  const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
  server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'rotation_person_history_fixture.py'),String(port)],{cwd:path.join(root,'backend'),windowsHide:true});
  server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);
  const base=`http://127.0.0.1:${port}`;let ready=false;
  for(let i=0;i<100;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await new Promise(r=>setTimeout(r,250));}assert(ready,log);
  for(const engine of (process.env.ROTATION_HISTORY_ENGINES||'edge,webkit').split(',')){
    browser=await(engine==='webkit'?webkit:chromium).launch(engine==='edge'?{channel:'msedge',headless:true}:{headless:true});
    const context=await browser.newContext({viewport:{width:1440,height:1100}}),request=context.request;
    await ok(await request.post(base+'/api/login',{data:{employee_no:'9710040',password:'formal-fixture-2026'}}));
    const board=async()=>(await ok(await request.get(base+'/api/rotation-board/board'))).day;
    const before=await board(),date=before.date;
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/rotation-board');await page.locator('[data-tab=records]').click();
    await page.locator('#personHistoryQuery').fill('接班测试员工');
    await page.locator('[data-history-person="9710000"]').click();
    await page.locator('.rt-history-identity').waitFor();
    assert.match(await page.locator('.rt-history-metrics').innerText(),/55分/);
    assert.match(await page.locator('.rt-history-metrics').innerText(),/20分/);
    assert.match(await page.locator('.rt-history-day').first().innerText(),/休息前 1时/);
    assert.match(await page.locator('.rt-history-day').first().innerText(),/实休 20分/);
    assert.match(await page.locator('.rt-history-day').first().innerText(),/含异常等岗/);
    assert.match(await page.locator('.rt-history-day').first().innerText(),/吃饭/);
    const bars=await page.locator('.rt-history-bar i').evaluateAll(nodes=>nodes.map(n=>({missing:n.classList.contains('is-missing'),h:n.getBoundingClientRect().height,w:n.getBoundingClientRect().width})));assert(bars.filter(b=>b.missing).every(b=>b.h<=3));assert(bars.some(b=>!b.missing&&b.h>5&&b.w>5),JSON.stringify(bars));
    await page.screenshot({path:path.join(out,engine+'-desktop.png'),fullPage:true,animations:'disabled'});
    await page.locator('[data-history-span]').selectOption('30');
    await page.waitForFunction(()=>personnelHistory.record?.start===historyRange().start&&!personnelHistory.loading);
    assert.equal(await page.locator('.rt-history-day').count(),2);
    await page.locator('.rt-history-day').first().locator('summary').click();
    await page.waitForFunction(()=>!document.querySelector('.rt-history-day').open);
    await page.evaluate(()=>{document.activeElement.blur();RT.render();});
    assert.equal(await page.locator('.rt-history-day').first().evaluate(el=>el.open),false,'Live sync preserves collapsed details');
    for(const width of [320,390,768,1440]){
      await page.setViewportSize({width,height:1000});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'overflow '+width);
    }
    await page.setViewportSize({width:390,height:1000});assert((await page.locator('#personHistoryQuery').boundingBox()).width>120);assert((await page.locator('#recordForm [type=submit]').boundingBox()).width<110);await page.locator('.rt-history-day').first().locator('summary').click();await page.evaluate(()=>{for(let el=document.querySelector('.rt-person-history');el;el=el.parentElement)el.scrollTop=0;window.scrollTo(0,0);const chart=document.querySelector('.rt-history-trend');chart.scrollLeft=chart.scrollWidth;});await page.waitForTimeout(200);await page.screenshot({path:path.join(out,engine+'-mobile.png'),fullPage:true,animations:'disabled'});
    // Failed analytics read stays in its own panel, without breaking the board.
    await page.route('**/api/rotation-board/person-insights?**',r=>r.fulfill({status:500,contentType:'application/json',body:JSON.stringify({detail:'记录暂不可用'})}),{times:1});
    await page.locator('#recordForm [type=submit]').click();
    await page.locator('[data-history-result] [role=alert]').waitFor();
    assert.match(await page.locator('[data-history-result]').innerText(),/记录暂不可用/);
    assert.equal(await page.locator('[data-tab=board]').count(),1);
    await page.locator('#recordForm [type=submit]').click();await page.locator('.rt-history-identity').waitFor();
    assert.deepEqual((await board()).lines,before.lines,'Analytics reads never change assignments');
    assert.equal((await board()).forecastSnapshot,before.forecastSnapshot);
    for(const employee_no of ['9710020','9710010','9710000']){
      const role=await browser.newContext();await ok(await role.request.post(base+'/api/login',{data:{employee_no,password:'formal-fixture-2026'}}));
      const response=await role.request.get(base+`/api/rotation-board/person-insights?employee_no=9710000&start=${date}&end=${date}`);
      assert.equal(response.status(),employee_no==='9710020'?200:403);
      const suggestions=await role.request.get(base+`/api/rotation-board/record-people?q=接班&start=${date}&end=${date}`);
      assert.equal(suggestions.status(),employee_no==='9710020'?200:403);await role.close();
    }
    assert.deepEqual(errors,[]);await context.close();await browser.close();browser=null;
    console.log(engine+': name lookup, work/rest/line durations, trends, 30 days, narrow widths, preserved expansion, failure recovery, manager permissions, read-only PASS');
  }
}finally{await browser?.close();if(server&&server.exitCode===null){server.kill();await new Promise(r=>server.once('exit',r));}fs.writeFileSync(path.join(out,'server.log'),log);}})().catch(e=>{console.error(e);console.error(log.slice(-2500));process.exitCode=1;});
