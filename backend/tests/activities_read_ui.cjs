'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process'),{chromium}=require('./support/browser-runtime.cjs');
const backend=path.resolve(__dirname,'..'),out=path.resolve(backend,'../output/activity-read-review');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
 const base='http://127.0.0.1:'+port,server=spawn(path.join(backend,'.venv/Scripts/python.exe'),[path.join(__dirname,'activity_read_fixture.py'),String(port)],{cwd:backend,windowsHide:true});
 let log='',browser;server.stderr.on('data',d=>log+=d);server.stdout.on('data',d=>log+=d);
 try{
  let health;for(let i=0;i<120;i++){try{const r=await fetch(base+'/health');if(r.ok){health=await r.json();break;}}catch{}await pause(250);}assert(health,log);
  browser=await chromium.launch({channel:'msedge',headless:true});fs.mkdirSync(out,{recursive:true});
  const c=await browser.newContext({viewport:{width:1280,height:1000}});
  assert((await c.request.post(base+'/api/login',{data:{employee_no:'GSMTEST01',password:'1234'}})).ok());
  await c.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}});
  const p=await c.newPage(),errors=[],details=[];p.on('pageerror',e=>errors.push(e.message));
  p.on('request',r=>{if(/\/api\/activities\/\d+(\?|$)/.test(r.url()))details.push(r.url());});
  await p.goto(base+'/');await p.locator('[data-tab="activities"]:visible').click();
  await p.locator('.activity-event').first().waitFor();assert.equal(await p.locator('.activity-event').count(),10);assert.equal(details.length,0,'collapsed cards must not request detail');
  for(const width of [390,1280]){
   await p.setViewportSize({width,height:1000});assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await p.locator('[data-page-kind="list"][data-page="2"]').scrollIntoViewIfNeeded();await p.screenshot({path:path.join(out,`activities-pagination-${width}.png`)});
  }
  await p.locator('[data-page-kind="list"][data-page="2"]').click();await p.getByText('伙伴活动 04',{exact:true}).waitFor();
  assert.equal(await p.locator('.activity-event').count(),4);assert.equal(details.length,0);
  await p.locator('[data-page-kind="list"][data-page="1"]').click();await p.getByText('秋日伙伴礼遇',{exact:true}).click();
  await p.locator('.activity-record-list .activity-award').first().waitFor();assert.equal(details.length,1);
  assert.equal(await p.locator('.activity-record-list .activity-award').count(),20);
  await p.locator('[data-page-kind="records"][data-page="2"]').click();await p.locator('.activity-record-list .activity-award').filter({hasText:'凭证 #21'}).waitFor();
  assert.equal(await p.locator('.activity-record-list .activity-award').count(),3);
  await p.locator('[data-record-state="refunded"]').click();await p.locator('.activity-record-list').getByText('已退款',{exact:false}).waitFor();
  assert.equal(await p.locator('.activity-record-list .activity-award').count(),1);assert.equal(await p.locator('.activity-record-list [data-action="refund"]').count(),0);
  await p.locator('[data-record-search] [name=keyword]').fill('不存在的员工');await p.getByRole('button',{name:'搜索记录',exact:true}).click();await p.getByText('当前条件下暂无记录',{exact:true}).waitFor();
  await p.locator('[data-record-state="all"]').click();await p.locator('[data-record-search] [name=keyword]').fill('#1');await p.getByRole('button',{name:'搜索记录',exact:true}).click();
  await p.locator('.activity-record-list .activity-award').filter({hasText:'凭证 #1 ·'}).waitFor();assert.equal(await p.locator('.activity-record-list .activity-award').count(),1);
  assert.equal(await p.locator('.activity-record-list [data-action="issue"]').count(),1);
  for(const width of [320,390,1280]){
   await p.setViewportSize({width,height:1050});assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'records overflow '+width);
   await p.locator('.activity-records').screenshot({path:path.join(out,`receipts-search-${width}.png`)});
  }
  // A delayed search must not overwrite a later state choice.
  let release,started;const began=new Promise(r=>started=r);
  await p.route('**/api/activities/*/records?*',async route=>{
   const u=new URL(route.request().url());if(u.searchParams.get('keyword')==='CMTEST01'&&u.searchParams.get('status')==='all'){
    const response=await route.fetch();started();await new Promise(r=>release=r);await route.fulfill({response});
   }else await route.continue();
  });
  await p.locator('[data-record-search] [name=keyword]').fill('CMTEST01');await p.getByRole('button',{name:'搜索记录',exact:true}).click();await began;
  await p.locator('[data-record-state="refunded"]').click();await p.locator('.activity-record-list').getByText('已退款',{exact:false}).waitFor();
  const delayed=p.waitForResponse(r=>r.url().includes('keyword=CMTEST01')&&r.url().includes('status=all'));release();await delayed;
  assert.equal(await p.locator('[data-record-state="refunded"]').getAttribute('aria-pressed'),'true');
  assert.equal(await p.locator('.activity-record-list .activity-award').count(),1);
  await p.unroute('**/api/activities/*/records?*');
  // Opening another event must not replace an existing search input or its focus.
  let releaseDetail,detailStarted;const detailBegan=new Promise(r=>detailStarted=r);
  await p.route(base+'/api/activities/13',async route=>{const response=await route.fetch();detailStarted();await new Promise(r=>releaseDetail=r);await route.fulfill({response});});
  await p.getByText('伙伴活动 13',{exact:true}).click();await detailBegan;
  const search=p.locator('[data-record-search="14"] [name=keyword]');
  await search.fill('尚未提交的核对文字');const original=await search.elementHandle();
  releaseDetail();await p.locator('[data-records-event="13"]').waitFor();
  assert(await original.evaluate(el=>el.isConnected),'another detail response must not replace this input');
  assert.equal(await search.inputValue(),'尚未提交的核对文字');assert(await search.evaluate(el=>el===document.activeElement));
  await p.unroute(base+'/api/activities/13');
  // Refresh retains the live controls, submitted empty search, and input draft.
  const stateButton=await p.locator('[data-records-event="14"] [data-record-state="all"]').elementHandle();
  const pageButton=await p.locator('[data-records-event="14"] [data-page-kind="records"]').first().elementHandle();
  await search.fill('');await p.locator('[data-record-search="14"] button').click();
  await p.getByRole('button',{name:'刷新',exact:true}).click();
  await p.locator('[data-records-event="14"] [data-record-state="all"]').click();
  await p.locator('[data-records-event="14"] .activity-award').filter({hasText:'凭证 #1 ·'}).waitFor();
  assert(await stateButton.evaluate(el=>el.isConnected),'state controls survive refresh');
  assert(await pageButton.evaluate(el=>el.isConnected),'pagination controls survive refresh');
  assert.equal(await search.inputValue(),'');
  assert((await p.locator('[data-records-event="14"] .activity-pagination').textContent()).includes('27条'));
  // Same-page refresh must update pending signatures without replacing controls.
  const signFilter=p.locator('[data-filter="sign"]');
  const originalSignFilter=await signFilter.elementHandle();
  await p.route(base+'/api/activities?*',async route=>{
   const response=await route.fetch();const body=await response.json();
   await route.fulfill({response,json:{...body,pending_signatures:3}});
  });
  await p.getByRole('button',{name:'刷新',exact:true}).click();
  await signFilter.getByText('待本人签收 3',{exact:true}).waitFor({timeout:3000});
  assert(await originalSignFilter.evaluate(el=>el.isConnected),'badge update keeps the filter control');
  await p.unroute(base+'/api/activities?*');
  await p.getByRole('button',{name:'刷新',exact:true}).click();
  await signFilter.getByText('待本人签收 0',{exact:true}).waitFor();
  // Out-of-order list filter responses must not restore the old activity list.
  let releaseList,listStarted;const listBegan=new Promise(r=>listStarted=r);
  await p.route('**/api/activities?*',async route=>{if(new URL(route.request().url()).searchParams.get('view')==='manage'){const response=await route.fetch();listStarted();await new Promise(r=>releaseList=r);await route.fulfill({response});}else await route.continue();});
  await p.locator('[data-filter="manage"]').click();await listBegan;
  await p.locator('[data-filter="mine"]').click();await p.getByText('暂无符合条件的活动',{exact:true}).waitFor();
  const oldList=p.waitForResponse(r=>r.url().includes('view=manage'));releaseList();await oldList;
  assert.equal(await p.locator('[data-filter="mine"]').getAttribute('aria-pressed'),'true');assert.equal(await p.locator('.activity-event').count(),0);
  // Navigating away while opening an archive cancels its read without an error toast.
  await p.route('**/api/activities/14/records?*',async route=>{
   const response=await route.fetch(),body=await response.json();
   if(body.items.length)body.items[0].receipt_id='ui-abort-check';
   await route.fulfill({response,json:body});
  });
  let archiveStarted,releaseArchive;
  const archiveBegan=new Promise(r=>archiveStarted=r),archiveRelease=new Promise(r=>releaseArchive=r);
  await p.route('**/api/activities/receipts/ui-abort-check',async route=>{archiveStarted();await archiveRelease;await route.abort().catch(()=>{});});
  await p.locator('[data-filter="all"]').click();
  await p.getByText('秋日伙伴礼遇',{exact:true}).click();
  await p.locator('[data-receipt="ui-abort-check"]').click();await archiveBegan;
  const cancelled=p.waitForEvent('requestfailed',{predicate:r=>r.url().endsWith('/receipts/ui-abort-check')});
  await p.getByRole('button',{name:'我的积点卡',exact:true}).click();await cancelled;releaseArchive();
  await p.getByRole('heading',{name:'我的积点卡',exact:true}).waitFor();
  assert(!/abort|cancel/i.test(await p.locator('#toast').innerText()),'cancelled archive reads must not show an error toast');
  assert.deepEqual(errors,[]);console.log('PASS: pagination, lazy detail, search/status, pending count, cancelled archive reads; 320/390/1280px.');
 }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
