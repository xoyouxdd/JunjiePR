'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process'),{chromium,webkit,firefox}=require('./support/browser-runtime.cjs');
const root=path.resolve(__dirname,'../..'),out=path.join(root,'.handover_review/rotation-staffing-transfer');fs.mkdirSync(out,{recursive:true});
let server,browser,log='';
async function ok(r){assert(r.ok(),await r.text());return r.json();}
(async()=>{try{
  const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
  server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'rotation_staffing_transfer_fixture.py'),String(port)],{cwd:path.join(root,'backend'),windowsHide:true});
  server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);
  const base=`http://127.0.0.1:${port}`;let ready=false;
  for(let i=0;i<100;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await new Promise(r=>setTimeout(r,250));}assert(ready,log);
  for(const engine of (process.env.ROTATION_STAFFING_ENGINES||'edge,webkit,firefox').split(',')){
    browser=await(engine==='webkit'?webkit:engine==='firefox'?firefox:chromium).launch(engine==='edge'?{channel:'msedge',headless:true}:{headless:true});
    const context=await browser.newContext({viewport:{width:1440,height:1000}}),request=context.request;
    await ok(await request.post(base+'/__fixture/reset'));
    await ok(await request.post(base+'/api/login',{data:{employee_no:'9710040',password:'formal-fixture-2026'}}));
    const board=async()=>(await ok(await request.get(base+'/api/rotation-board/board'))).day;
    const before=await board(),source=before.lines.find(l=>l.id==='A'),post=source.posts[2],person=before.persons.find(p=>p.pid===post.occ);
    assert.deepEqual(before.lines.map(l=>l.posts.length),[5,3]);
    assert(before.staffingWarnings.some(p=>p.at===1200&&p.gap===1));
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(base+'/rotation-board');await page.locator('.rt-staffing-warning').waitFor();
    assert((await page.locator('.rt-staffing-warning').innerText()).length<80,'Default warning is one compact summary');
    assert.equal(await page.locator('.rt-staffing-warning').getAttribute('open'),null);
    await page.locator('.rt-staffing-warning>summary').click();assert.match(await page.locator('.rt-staffing-warning').innerText(),/20:00.*保留 8 岗.*缺 1 人/s);
    await page.waitForFunction(()=>staffingExpanded.has('staffing:'+RT.data.day.date));
    await page.evaluate(()=>RT.render());
    assert.equal(await page.locator('.rt-staffing-warning').evaluate(el=>el.open),true,'Refresh preserves requested expansion');
    assert.equal(await page.locator('.rt-staffing-people').first().isVisible(),false,'Names remain collapsed inside details');
    await page.locator('.rt-staffing-person-details>summary').first().click();
    await page.locator('.rt-staffing-people').first().waitFor({state:'visible'});
    await page.screenshot({path:path.join(out,engine+'-warning.png'),animations:'disabled'});
    const open=async()=>{await page.locator('[data-f-head=A]').click();await page.locator('[data-f-transfer]').click();await page.locator('[name=post_key]').selectOption(post.key);await page.locator('[name=target_line]').selectOption('B');await page.waitForFunction(()=>!document.querySelector('[data-form] [type=submit]').disabled);assert.match(await page.locator('[data-transfer-advice]').innerText(),/建议第/);assert.equal(await page.locator('.rt-transfer-details').evaluate(el=>el.open),false);await page.locator('[name=target_i]').selectOption('1');await page.waitForFunction(()=>!document.querySelector('[data-form] [type=submit]').disabled);};
    await open();const modal=page.getByRole('dialog');assert.deepEqual((await board()).lines,before.lines,'Preview is read-only');assert.match(await modal.innerText(),/5 → 4 岗/);assert.match(await modal.innerText(),/3 → 4 岗/);
    // Out-of-order read-only replies must never replace the current choice.
    let delayedReply;
    const delayedReady=new Promise(resolve=>delayedReply=resolve);
    await page.route('**/api/rotation-board/post-transfer-preview',async route=>{
      const response=await route.fetch();
      if(route.request().postDataJSON().target_i===0){delayedReply();await new Promise(r=>setTimeout(r,600));}
      await route.fulfill({response});
    });
    await page.locator('[name=target_i]').selectOption('0');await delayedReady;
    await page.locator('[name=target_i]').selectOption('1');
    await page.waitForFunction(()=>!document.querySelector('[data-form] [type=submit]').disabled);
    await page.waitForTimeout(700);assert.equal(await page.locator('[name=target_i]').inputValue(),'1');
    await page.unroute('**/api/rotation-board/post-transfer-preview');
    await page.route('**/api/rotation-board/post-transfer-preview',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'核对暂不可用'})}),{times:1});
    await page.locator('[name=target_i]').selectOption('2');
    await page.locator('[data-transfer-retry]').waitFor();assert.equal(await modal.locator('[type=submit]').isDisabled(),true);
    await page.locator('[data-transfer-retry]').click();await page.waitForFunction(()=>!document.querySelector('[data-form] [type=submit]').disabled);
    await page.locator('[name=target_i]').selectOption('1');await page.waitForFunction(()=>!document.querySelector('[data-form] [type=submit]').disabled);
    await page.screenshot({path:path.join(out,engine+'-preview.png'),animations:'disabled'});
    for(const width of [320,390,768,1440]){await page.setViewportSize({width,height:1000});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,engine+' overflow '+width);}
    await modal.getByRole('button',{name:'取消',exact:true}).click();assert.deepEqual((await board()).lines,before.lines,'Cancel does not change positions');
    await page.setViewportSize({width:1440,height:1000});await open();
    await modal.getByRole('button',{name:'确认移岗',exact:true}).click();
    await page.waitForFunction(()=>RT.data.day.lines.every(l=>l.posts.length===4));
    const after=await board(),moved=after.persons.find(p=>p.pid===post.occ);
    assert.equal(after.lines.find(l=>l.id==='B').posts[1].key,post.key);assert.equal(moved.line,'B');
    for(const key of ['lineStart','continuousStart','entrySortAt'])assert.equal(moved[key],person[key],key+' preserved');
    assert.deepEqual(after.lines.map(l=>[l.sortAt,l.previousSortAt]),before.lines.map(l=>[l.sortAt,l.previousSortAt]));
    assert.equal(after.persons.length,before.persons.length);
    assert(after.staffingWarnings.some(p=>p.at===1200&&p.gap===1),'Moving a post does not invent extra headcount');
    await page.screenshot({path:path.join(out,engine+'-after.png'),animations:'disabled'});
    const duplicate=await request.post(base+'/api/rotation-board/act',{data:{action:'post_transfer',line:'A',post_key:post.key,target_line:'B',target_i:1,expected_snapshot:before.forecastSnapshot}});
    assert.equal(duplicate.status(),409);
    const member=await browser.newContext();await ok(await member.request.post(base+'/api/login',{data:{employee_no:'9710000',password:'formal-fixture-2026'}}));
    const previewDenied=await member.request.post(base+'/api/rotation-board/post-transfer-preview',{data:{line:'B',post_key:post.key,target_line:'A',expected_snapshot:after.forecastSnapshot}});assert.equal(previewDenied.status(),403);
    const denied=await member.request.post(base+'/api/rotation-board/act',{data:{action:'post_transfer',line:'B',post_key:post.key,target_line:'A',target_i:0,expected_snapshot:after.forecastSnapshot}});assert.equal(denied.status(),403);await member.close();
    assert.deepEqual(errors,[]);await context.close();await browser.close();browser=null;
    console.log(engine+': warning, preview, cancel, five/three to four/four, timing, duplicate protection, CM denial and four widths PASS');
  }
}finally{await browser?.close();if(server&&server.exitCode===null){server.kill();await new Promise(r=>server.once('exit',r));}fs.writeFileSync(path.join(out,'server.log'),log);}})().catch(e=>{console.error(e);console.error(log.slice(-2000));process.exitCode=1;});
