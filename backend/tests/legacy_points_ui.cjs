'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process');const {chromium}=require('./support/browser-runtime.cjs');
const backend=path.resolve(__dirname,'..'),output=path.resolve(backend,'../output/legacy-points');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
 const base='http://127.0.0.1:'+port,server=spawn(path.join(backend,'.venv/Scripts/python.exe'),[path.join(__dirname,'legacy_points_fixture.py'),String(port)],{cwd:backend,windowsHide:true});
 let log='',browser;server.stderr.on('data',d=>log+=d);server.stdout.on('data',d=>log+=d);
 try{
  let health;for(let i=0;i<120;i++){try{const r=await fetch(base+'/health');if(r.ok){health=await r.json();break;}}catch{}await sleep(250);}assert(health,log);
  browser=await chromium.launch({channel:'msedge',headless:true});fs.mkdirSync(output,{recursive:true});
  const context=await browser.newContext({viewport:{width:1280,height:2000},acceptDownloads:true});
  assert((await context.request.post(base+'/api/login',{data:{employee_no:'GSMTEST01',password:'1234'}})).ok());
  await context.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/');await page.locator('.tabs-desktop [data-tab="activityPoints"]').click();
  await page.locator('[data-legacy-roster]').getByText('林晓',{exact:true}).waitFor();
  const [download]=await Promise.all([page.waitForEvent('download'),page.locator('[data-legacy-export]').click()]);
  const template=path.join(output,'历史积点补录-模拟名单.xlsx');await download.saveAs(template);
  const filled=await context.request.post(base+'/__fixture/legacy-filled',{multipart:{file:{name:'名单.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:fs.readFileSync(template)}}});
  assert(filled.ok(),await filled.text());const content=await filled.body();
  fs.writeFileSync(path.join(output,'历史积点补录-模拟填写.xlsx'),content);
  const review=await context.request.post(base+'/__fixture/legacy-filled?review=true',{multipart:{file:{name:'名单.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:fs.readFileSync(template)}}});
  assert(review.ok(),await review.text());
  await page.locator('[data-legacy-file]').setInputFiles({name:'核对示例.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:await review.body()});
  await page.locator('.legacy-errors').waitFor();assert.equal(await page.locator('[data-legacy-apply]').count(),0,'an error must block the entire batch in every view');
  assert.equal(await page.locator('.legacy-preview-table tbody tr').count(),1,'default view excludes unchanged people');
  assert.equal(await page.locator('.legacy-preview-table').getByText('陈晨',{exact:true}).count(),0);
  await page.locator('[data-legacy-view="all"]').click();assert.equal(await page.locator('.legacy-preview-table tbody tr').count(),2);
  await page.locator('[data-legacy-view="errors"]').click();assert.equal(await page.locator('.legacy-preview-table').count(),0);assert(await page.locator('.legacy-errors').isVisible());
  await page.locator('[data-legacy-view="changes"]').click();assert.equal(await page.locator('.legacy-errors').count(),0);assert(await page.locator('.legacy-error-notice').isVisible());assert.equal(await page.locator('[data-legacy-apply]').count(),0);
  await page.locator('[data-legacy-view="attention"]').click();
  for(const width of [320,390,1280]){await page.setViewportSize({width,height:1100});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.locator('.legacy-preview').screenshot({path:path.join(output,`legacy-attention-${width}.png`)});}
  await page.locator('[data-legacy-file]').setInputFiles({name:'历史积点核定.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:content});
  await page.locator('[data-legacy-apply]').waitFor();
  assert((await page.locator('.legacy-deltas').textContent()).includes('+21'));
  for(const width of [320,390,1280]){
   await page.setViewportSize({width,height:2200});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'overflow '+width);
   assert(await page.locator('.legacy-table-wrap').evaluateAll(nodes=>nodes.every(node=>node.scrollWidth<=node.clientWidth+1)),'Table columns must fit '+width);
   const panel=page.locator('.legacy-points-panel');await panel.scrollIntoViewIfNeeded();await page.evaluate(()=>scrollBy(0,-140));
   await panel.screenshot({path:path.join(output,`legacy-preview-${width}.png`)});
  }
  await page.setViewportSize({width:390,height:900});
  await page.locator('[data-legacy-apply]').click();await page.getByRole('dialog').waitFor();
  await page.getByRole('dialog').getByRole('button',{name:'确认入账',exact:true}).click();
  await page.locator('[data-legacy-status]').filter({hasText:'已完成：2 人'}).waitFor();
  for(const width of [390,1280]){
   await page.setViewportSize({width,height:2200});
   const panel=page.locator('.legacy-points-panel');await panel.scrollIntoViewIfNeeded();await page.evaluate(()=>scrollBy(0,-140));
   await panel.screenshot({path:path.join(output,`legacy-confirmed-${width}.png`)});
  }
  for(const [account,total,name] of [['7300001',18,'linxiao'],['7300002',15,'chenchen']]){
   const c=await browser.newContext({viewport:{width:1280,height:1800}});
   assert((await c.request.post(base+'/api/login',{data:{employee_no:account,password:'1234'}})).ok());
   await c.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}});
   const balance=await (await c.request.get(base+'/api/activity-points/me')).json();
   assert.equal(balance.available,total);assert.equal(balance.earned,total);assert.equal(balance.used,0);
   const p=await c.newPage();await p.goto(base+'/');await p.locator('.tabs-desktop [data-tab="activityPoints"]').click();
   await p.locator('#personalActivityPoints h2').waitFor();
   await p.locator('.points-detail').evaluate(el=>el.open=true);
   for(const width of [390,1280]){
    await p.setViewportSize({width,height:1800});
    assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Card overflow '+width);
    const card=p.locator('#personalActivityPoints');await card.scrollIntoViewIfNeeded();await p.evaluate(()=>scrollBy(0,-140));
    await card.screenshot({path:path.join(output,`legacy-card-${name}-${width}.png`)});
   }
   fs.writeFileSync(path.join(output,`simulated-balance-${name}.json`),JSON.stringify(balance,null,2));
   await c.close();
  }
  await page.locator('[data-legacy-file]').setInputFiles({name:'历史积点核定.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:content});
  await page.getByText('本次无差额，无需入账。',{exact:true}).waitFor();
  assert.equal(await page.locator('[data-legacy-apply]').count(),0);
  assert.equal(await page.locator('.legacy-preview-table').count(),0,'repeated file has no rows requiring attention');
  await page.locator('[data-legacy-view="all"]').click();assert.equal(await page.locator('.legacy-preview-table tbody tr').count(),2);
  await page.locator('[data-legacy-archived]').check();await page.locator('[data-legacy-roster]').getByText('周宁',{exact:true}).waitFor();
  await page.locator('[data-legacy-archived]').uncheck();assert.equal(await page.locator('[data-legacy-roster]').getByText('周宁',{exact:true}).count(),0);
  assert.deepEqual(errors,[]);await context.close();
  for(const account of ['CMTEST01','TAGSMTEST01']){
   const c=await browser.newContext();await c.request.post(base+'/api/login',{data:{employee_no:account,password:'1234'}});
   await c.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}});
   const p=await c.newPage();await p.goto(base+'/');await p.locator('.tabs-desktop [data-tab="activityPoints"]').click();
   await p.locator('#personalActivityPoints h2').waitFor();assert.equal(await p.locator('#legacyActivityPoints').count(),0);
   assert.equal((await c.request.get(base+'/api/activity-points/legacy/options')).status(),403);await c.close();
  }
  console.log('PASS: real export, filled Excel preview, confirmed delta import, repeated-file idempotency, archive toggle, GSM-only entry; 320/390/1280px.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
