// Real ranking, navigation and statistics modules with isolated HTTP responses.
const assert=require('node:assert/strict');
const {chromium}=require('./support/browser-runtime.cjs');
const {routeFrontend}=require('./support/frontend-page.cjs');
const html=`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/static/css/style.css"><link rel="stylesheet" href="/static/css/workspace-ui.css">
<main id="app" data-workspace-ui></main><nav id="tabs"></nav><div id="toast"></div>`;
const circles=[{id:1,name:'热力追踪'},{id:2,name:'矮人迷宫'},{id:3,name:'小熊罐子'}];
const venues=circles.map(circle=>({key:String(circle.id),name:circle.name}));
const row={employee_id:11,employee_no:'1234567',employee_name:'本圈员工',role_name:'CM',rank:1,
 venue_counts:{'1':1,'2':0,'3':1},group_label:'本圈A组',count:2,score:1,recognition_score:1,
 deduction_score:0,attendance_score:12,total_score:13,leave_days:0,charged_days:0,recent_date:'2026-11-01'};

(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
for(const width of [320,390,1280]){
 const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const requests=[],exports=[],pendingExports=[],pendingRankings=[];
 let role='HR_CIRCLE',deferCategory=null,emptyRanking=false,deferExport=false,failExport=false,uncappedMode=null;
 await page.route('**/api/**',async route=>{
   const url=new URL(route.request().url()),q=url.searchParams,category=q.get('category');
   if(url.pathname==='/api/export-tasks'){
     assert.equal(route.request().method(),'POST');
     exports.push(q.get('target'));
     if(deferExport)await new Promise(resolve=>pendingExports.push(resolve));
     if(failExport)return route.fulfill({status:422,json:{detail:'导出失败测试'}});
     return route.fulfill({json:{id:exports.length,label:'测试导出',status:'queued'}});
   }
   if(url.pathname==='/api/statistics/my-exports')return route.fulfill({json:{items:[]}});
   if(url.pathname==='/api/statistics/export-preview')return route.fulfill({json:{employee_count:0,loa_count:0}});
   requests.push(url);
   if(url.pathname==='/api/statistics'){
     const issuer=q.get('issuer_keyword');
     return route.fulfill({json:{month:q.get('month'),title:q.get('title')||'',summary:{employee_count:0},hierarchy:[],filters:{},
       loa_rows:[],issuer_keyword:issuer||'',issuer_counts:{population_name:q.get('issuer_population'),attraction_name:'热力追踪',
         venue_columns:venues,rows:[
           {employee_no:'1234567',employee_name:issuer||'签卡AM',role_name:'AM',count:3,score:2,venue_counts:{'1':1,'2':2}},
           {employee_no:'1234568',employee_name:'签卡OM',role_name:'OM',count:1,score:0,venue_counts:{'3':1}}
         ]}}});
   }
   assert.equal(url.pathname,'/api/pr-rankings');
   const payload={category,population:q.get('population'),venue_columns:['leader','gsm_leader'].includes(category)?venues:[],
     circle_scope:role==='HR_CIRCLE'?'recipient':'issuer',
     population_name:q.get('population')==='all'?'全部人群':q.get('population')==='supervisor'?'LEAD':'CM/TR',
     rows:[row],total:21,pages:2,page:Number(q.get('page')||1),page_size:20,
     attraction_name:circles.find(c=>String(c.id)===q.get('attraction_id'))?.name||'全部景点圈',
     start_date:q.get('start_date'),end_date:q.get('end_date')};
   if(deferCategory===category)await new Promise(resolve=>pendingRankings.push(resolve));
   if(emptyRanking){payload.rows=[];payload.total=0;payload.pages=1;}
   if(category==='recognition'&&uncappedMode!==null){
     payload.uncapped_ranking=uncappedMode;
     payload.rows=[0,4,4.99,undefined,5,'5.00',6].map((score,index)=>({...row,
       rank:index+1,count:17,score,uncapped_score:9,
       employee_no:String(1000000+index),employee_name:'阈值验证 '+index}));
   }
   return route.fulfill({json:payload});
 });
 await page.goto(await routeFrontend(page,{html}));
 await page.evaluate(async circles=>{
   const {state}=await import('/static/js/app/state.js');
   const {renderPrRankings}=await import('/static/js/app/rankings.js');
   const {renderStatistics}=await import('/static/js/app/statistics.js');
   const {menuItems}=await import('/static/js/app/navigation.js');
   state.me={role_code:'HR_CIRCLE',base_role_code:'HR_CIRCLE',attraction_id:1,employee_no:'HRTEST',permissions:['DATA_VIEW','DATA_EXPORT','HR_MANAGE']};
   state.options={employee_circles:circles,recognition_types:[{id:1,name:'安全'}],deduction_types:[{id:1,name:'考勤'}]};
   Object.assign(window,{fixtureState:state,renderRanking:renderPrRankings,rankingMenu:menuItems,renderStats:renderStatistics});
 },circles);
 const selectCategory=async category=>{
   const response=page.waitForResponse(response=>new URL(response.url()).searchParams.get('category')===category);
   await page.locator('[data-pr-category='+category+']').click();
   await response;
 };
 const submitExport=async(button='#prExport',{confirm=false,pending=false}={})=>{
   const started=page.waitForRequest(request=>new URL(request.url()).pathname==='/api/export-tasks'&&request.method()==='POST');
   await page.locator(button).click();
   if(confirm)await page.locator('[data-modal-confirm]').click();
   const request=await started;
   if(!pending) {
     await request.response();
     await page.waitForFunction(sel=>!document.querySelector(sel)?.disabled,button);
   }
   return new URL(new URL(request.url()).searchParams.get('target'),'http://fixture').searchParams;
 };
 const waitForToast=(message)=>page.locator('#toast').getByText(message,{exact:true}).waitFor();
 for(const circleId of [1,2,3]){
  const offset=requests.length;
  await page.evaluate(async id=>{fixtureState.me.attraction_id=id;await renderRanking();},circleId);
  const menuItems=await page.evaluate(()=>rankingMenu());assert(menuItems.some(([id])=>id==='prRankings'));
  assert.equal(await page.locator('#prFilter [name=attraction_id] option').count(),1);
  assert.equal(await page.locator('#prFilter [name=attraction_id]').inputValue(),String(circleId));
  assert(await page.locator('#prExport').isVisible());
  assert.equal(await page.locator('[data-pr-category]').count(),6);
  assert.equal(await page.locator('[name=population] option').count(),1);
  assert.equal(await page.locator('[data-pr-category=leader],[data-pr-category=gsm_leader]').count(),2);
  await page.locator('[data-pr-category=recognition]').click();
  await page.locator('[data-pr-sort=count]').click();
  await page.locator('#prSubfilters [name=subtype_id]').selectOption('1');
  await page.locator('[data-pr-sort=score]').click();
  assert.equal(await page.locator('#prSubfilters [name=subtype_id]').inputValue(),'1');
  await page.locator('[data-pr-sort=count]').click();
  assert.equal(await page.locator('#prSubfilters [name=subtype_id]').inputValue(),'1');
  await page.locator('#prFilter [name=keyword]').fill('本圈');await page.locator('#prFilter button.primary').click();
  const qs=await submitExport();
  assert.equal(qs.get('attraction_id'),String(circleId));assert.equal(qs.get('population'),'frontline');assert.equal(qs.get('subtype_id'),'1');assert.equal(qs.get('keyword'),'本圈');assert.equal(qs.get('sort_by'),'count');
  const ownRequests=requests.slice(offset);assert(ownRequests.length>0&&ownRequests.every(url=>url.searchParams.get('attraction_id')===String(circleId)));
  for(const category of ['leader','gsm_leader']){
    await selectCategory(category);
    assert.match(await page.locator('#prResults').textContent(),/累计/);
    assert.match(await page.locator('#prResults').textContent(),/次数/);
    assert.equal((await submitExport()).get('category'),category);
  }
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'overflow '+width);
 }
 role='GSM';
 await page.evaluate(async()=>{fixtureState.me.role_code='GSM';fixtureState.me.base_role_code='GSM';fixtureState.me.attraction_id=1;await renderRanking()});
 assert.equal(await page.locator('#prFilter [name=attraction_id] option').count(),4);assert(await page.locator('#prExport').isVisible());
 assert.equal(await page.locator('[data-pr-category]').count(),7);assert.equal(await page.locator('[name=population] option').count(),2);
 // The manager's issuer boards reuse the population control and show occurrence
 // counts in desktop tables and mobile cards.  The other four boards stay split.
 await selectCategory('gsm_leader');
 assert.equal(await page.locator('[data-pr-category=gsm_leader]').textContent(),'管理层认可次数');
 assert.match(await page.locator('.pr-ranking-table th').nth(1).textContent(),/AM \/ OM/);
 assert.equal(await page.locator('[name=population] option').count(),3);
 assert.equal(await page.locator('[data-pr-circle-label]').textContent(),'签卡人所属圈');
 assert.equal(await page.locator('[data-pr-search-label]').textContent(),'签卡人搜索');
 await page.locator('[name=population]').selectOption('all');
 assert.equal((await submitExport()).get('population'),'all');
 for(const category of ['overall','recognition','deduction','absence']){
  await selectCategory(category);
  assert.equal(await page.locator('[name=population] option').count(),2);
  assert.equal(await page.locator('[data-pr-circle-label]').textContent(),'景点圈');
  const scopedRequest=page.waitForRequest(request=>new URL(request.url()).searchParams.get('population')==='supervisor');
  await page.locator('[name=population]').selectOption('supervisor');
  assert.equal(new URL((await scopedRequest).url()).searchParams.get('population'),'supervisor');
 }
 await selectCategory('leader');
 assert(await page.locator('[name=population]').isDisabled());
 assert.equal(await page.locator('[name=population]').inputValue(),'frontline');
 assert.equal((await submitExport()).get('population'),'frontline');
 await selectCategory('gsm_leader');
 assert.equal(await page.locator('[name=population]').inputValue(),'all');
 assert.match(await page.locator('#prResults').textContent(),/认可次数（总数）/);
 assert.deepEqual((await page.locator('.pr-ranking-table th').allTextContents()).slice(3,7),['认可次数（总数）','热力追踪','矮人迷宫','小熊罐子']);
 const counts=(await page.locator('.pr-ranking-table tbody tr').first().locator('td').allTextContents()).slice(3,7).map(Number);
 assert.equal(counts[0],counts.slice(1).reduce((a,b)=>a+b,0));
 assert.deepEqual((await page.locator('.pr-ranking-card dt').allTextContents()).slice(1,5),['认可次数（总数）','热力追踪','矮人迷宫','小熊罐子']);
 const queryRanking=async()=>{
   const response=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/pr-rankings');
   await page.locator('#prFilter button.primary').click();
   await response;
 };
 emptyRanking=true;
 await queryRanking();
 const empty=page.locator('.pr-ranking-table tbody td.empty');
 await empty.waitFor({state:'attached'});
 assert.equal(await empty.getAttribute('colspan'),'9');
 emptyRanking=false;deferCategory='leader';
 const leaderRequest=page.waitForRequest(request=>new URL(request.url()).searchParams.get('category')==='leader');
 await page.locator('[data-pr-category=leader]').click();
 const stale=await leaderRequest;
 await selectCategory('recognition');
 deferCategory=null;assert.equal(pendingRankings.length,1);pendingRankings.shift()();
 await (await stale.response()).finished();
 assert.equal(await page.locator('.pr-ranking-table th').nth(1).textContent(),'员工');
 for(const enabled of [true,false]){
   uncappedMode=enabled;
   await queryRanking();
   await page.locator('.pr-ranking-table tbody tr').filter({hasText:'阈值验证 6'}).waitFor({state:'attached'});
   const tableCounts=await page.locator('.pr-ranking-table tbody tr td:nth-child(5)').allTextContents();
   const cardCounts=await page.locator('.pr-ranking-card dl > div').filter({has:page.getByText('登记次数',{exact:true})}).locator('dd').allTextContents();
   const expected=Array.from({length:7},(_,i)=>enabled&&i>=4?'17 （未封顶9.00分）':'17');
   assert.deepEqual(tableCounts,expected);
   assert.deepEqual(cardCounts,expected);
 }
 uncappedMode=null;
 await selectCategory('gsm_leader');
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'manager overflow '+width);
 const beforeExports=exports.length;
 deferExport=true;
 await submitExport('#prExport',{pending:true});
 assert(await page.locator('#prExport').isDisabled());
 await selectCategory('recognition');
 assert(await page.locator('#prExport').isDisabled(),'switching tabs must not unlock a pending export');
 await page.evaluate(()=>document.getElementById('prExport').click());
 assert.equal(exports.length,beforeExports+1);
 deferExport=false;assert.equal(pendingExports.length,1);pendingExports.shift()();
 await page.waitForFunction(()=>!document.getElementById('prExport').disabled);
 deferExport=true;
 await submitExport('#prExport',{pending:true});
 await page.evaluate(()=>renderRanking());
 assert(await page.locator('#prExport').isEnabled());
 await submitExport('#prExport',{pending:true});
 const priorToast=await page.locator('#toast').textContent();
 const previousResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/export-tasks');
 assert.equal(pendingExports.length,2);pendingExports.shift()();
 await (await previousResponse).finished();
 assert(await page.locator('#prExport').isDisabled(),'an old view must not unlock the new view export');
 assert.equal(await page.locator('#toast').textContent(),priorToast);
 deferExport=false;pendingExports.shift()();
 await page.waitForFunction(()=>!document.getElementById('prExport').disabled);
 failExport=true;
 await submitExport();
 assert(await page.locator('#prExport').isEnabled(),'failed export must allow retry');
 await waitForToast('导出失败测试');
 assert.equal(await page.locator('#toast').getAttribute('role'),'alert');
 failExport=false;
 await page.evaluate(async()=>{fixtureState.me.role_code='TA_GSM';fixtureState.me.base_role_code='SUPERVISOR';await renderRanking()});
 assert.equal(await page.locator('#prExport').count(),0);
 await page.evaluate(()=>{fixtureState.me.role_code='HR_ADMIN';fixtureState.me.base_role_code='HR_ADMIN'});
 assert(!(await page.evaluate(()=>rankingMenu())).some(([id])=>id==='prRankings'));
 await page.evaluate(async()=>{fixtureState.me.role_code='GSM';fixtureState.me.base_role_code='GSM';fixtureState.me.employee_no='GSMTEST';await renderStats()});
 assert.equal(await page.locator('[data-management-recognitions] tbody tr').count(),2);
 assert.match(await page.locator('[data-management-recognitions]').textContent(),/签卡AM.*AM.*3/);
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'statistics issuer overflow '+width);
 await page.locator('#statsFilter [name=keyword]').fill('员工筛选');
 await page.locator('#statsFilter [name=issuer_keyword]').fill('AMTEST01');
 await page.locator('#statsFilter [name=issuer_population]').selectOption('supervisor');
 await page.locator('#statsFilter button.primary').click();
 await page.locator('[data-management-recognitions] tbody tr').filter({hasText:'AMTEST01'}).waitFor();
 const statsQuery=requests.filter(url=>url.pathname==='/api/statistics').at(-1).searchParams;
 assert.equal(statsQuery.get('keyword'),'员工筛选');
 assert.equal(statsQuery.get('issuer_keyword'),'AMTEST01');
 assert.equal(statsQuery.get('issuer_population'),'supervisor');
 const exportQuery=await submitExport('#exportBtn',{confirm:true});
 assert.equal(exportQuery.get('keyword'),'员工筛选');
 assert.equal(exportQuery.get('issuer_keyword'),'AMTEST01');
 assert.equal(exportQuery.get('issuer_population'),'supervisor');
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'statistics filter overflow '+width);
 assert.deepEqual(errors,[]);await page.close();
}
console.log('PASS: HR and manager scope; ranking/issuer filters and export; uncapped score boundaries in tables and cards; empty results; stale response race; TA GSM export restriction; 320/390/1280px');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
