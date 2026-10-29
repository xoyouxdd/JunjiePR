'use strict';
// Real announcement modules; synthetic API fixtures only, never production.
const assert=require('node:assert/strict');
const {chromium,webkit,firefox}=require('playwright');
const fixture=require('./announcement_layout_ui.cjs');
let browser;
(async()=>{
 await new Promise(resolve=>fixture.server.listen(0,'127.0.0.1',resolve));
 try{
  for(const engine of (process.env.ANNOUNCEMENT_ALL_LEADS_ENGINES||'edge,webkit,firefox').split(',')){
   fixture.setRole('GSM');
   browser=await(engine==='webkit'?webkit:engine==='firefox'?firefox:chromium).launch(engine==='edge'?{channel:'msedge',headless:true}:{headless:true});
   const page=await browser.newPage({viewport:{width:390,height:900}}),errors=[];
   page.on('pageerror',e=>errors.push(e.message));
   async function open(){
    await page.goto(`http://127.0.0.1:${fixture.server.address().port}/`);await page.locator('#tabs').waitFor();
    await page.evaluate(async()=>{const url=new URL(document.querySelector('script[type=module][src]').src);const {state}=await import(new URL('./app/state.js',url));state.tab='announcements';const context=await import(new URL('./app/context.js',url));context.clearPageResources();const module=await import(new URL('./app/announcements.js',url));await module.renderAnnouncements();});
    await page.locator('[data-ann-new]').click();await page.locator('#announcementPublishForm').waitFor();
   }
   await open();
   const scope=page.locator('#announcementPublishForm [name=scope_kind]');
   assert.equal(await scope.locator('[value=all_leads]').count(),1);
   const initial=await page.locator('#announcementPublishForm [name=tiers]:checked').evaluateAll(xs=>xs.map(x=>x.value));
   await scope.selectOption('all_leads');
   assert(await page.locator('[data-ann-circle-scope]').isHidden());
   assert(await page.locator('[data-ann-receiving-tiers]').isHidden());
   assert(await page.locator('[data-ann-all-leads-hint]').isVisible());
   await scope.selectOption('circle');
   assert.deepEqual(await page.locator('#announcementPublishForm [name=tiers]:checked').evaluateAll(xs=>xs.map(x=>x.value)),initial);
   await scope.selectOption('all_leads');
   await page.locator('#announcementPublishForm [name=title]').fill('全部 Lead 岗位通知');
   await page.locator('#announcementPublishForm [name=body]').fill('仅供本地隔离验证，不会实际发布。');
   for(const width of [320,390,768,1440]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,engine+' overflow '+width);}
   await page.getByRole('button',{name:'预览',exact:true}).click();
   assert.match(await page.locator('#announcementPublishReview').innerText(),/所有 Lead（含 TA Lead）/);
   await page.locator('[data-ann-back]').click();
   if(await page.locator('[data-modal-confirm]').count())await page.locator('[data-modal-confirm]').click();
   await page.locator('[data-ann-resume]').click();await page.locator('#announcementPublishForm').waitFor();
   assert.equal(await scope.inputValue(),'all_leads');
   assert(await page.locator('[data-ann-receiving-tiers]').isHidden());
   let submitted=false;
   await page.route(/\/api\/announcements$/,async route=>{
    if(route.request().method()!=='POST')return route.continue();
    const body=route.request().postDataJSON();
    assert.equal(body.scope_kind,'all_leads');assert.deepEqual(body.tiers,['lead']);assert.deepEqual(body.attraction_ids,[]);assert.equal(body.project_id,null);
    submitted=true;await route.fulfill({json:{...fixture.announcement(),scope_kind:'all_leads',scope_label:'所有 Lead（含 TA Lead）',tiers:['lead'],attraction_ids:[]}});
   });
   await page.getByRole('button',{name:'预览',exact:true}).click();await page.locator('[data-ann-final-publish]').click();
   await page.locator('.announcement-record-toolbar').waitFor();assert(submitted);
   assert.deepEqual(errors,[]);
   await page.unroute(/\/api\/announcements$/);await page.setViewportSize({width:1440,height:900});await page.locator('[data-ann-side=manage]').click();await page.locator('.ann-manage-row .ann-management-progress').waitFor();assert.match(await page.locator('.ann-management-progress').first().innerText(),/18 \/ 24/);fixture.setRole('POC');await open();
   assert.equal(await scope.locator('[value=all_leads]').count(),0);
   await browser.close();browser=null;
   console.log(engine+': all-leads selection, four widths, scope switching, draft restore, preview and exact submit passed');
  }
 }finally{if(browser)await browser.close();fixture.server.closeAllConnections();await new Promise(resolve=>fixture.server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
