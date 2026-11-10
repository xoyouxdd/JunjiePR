// Real ranking renderer and menu function; isolated API responses, no live account access.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../app/static');
assert.match(fs.readFileSync(path.join(root,'js/app/rankings.js'),'utf8'),/import \{ toast \} from '\.\/toast\.js'/);
const ranking=fs.readFileSync(path.join(root,'js/app/rankings.js'),'utf8').replace(/^import .*\r?\n/gm,'').replace(/^export .*$/gm,'');
const statistics=fs.readFileSync(path.join(root,'js/app/statistics.js'),'utf8');
const statisticsCode=statistics.replace(/^import .*\r?\n/gm,'').replace(/^export .*$/gm,'');
const navigation=fs.readFileSync(path.join(root,'js/app/navigation.js'),'utf8');
const start=navigation.indexOf('function menuItems() {'),menu=navigation.slice(start,navigation.indexOf('\n}',start)+2);
const css=['style.css','workspace-ui.css'].map(f=>fs.readFileSync(path.join(root,'css',f),'utf8')).join('\n');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
for(const width of [320,390,1280]){
 const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.setContent('<style>'+css+'</style><main id="app" data-workspace-ui></main>');
 await page.evaluate('(()=>{'+ranking+'\n'+statisticsCode+'\n'+menu+`\nwindow.requests=[];window.exports=[];window.toasts=[];
 const app=document.getElementById('app');
 const state={me:{role_code:'HR_CIRCLE',base_role_code:'HR_CIRCLE',attraction_id:1},options:{employee_circles:[{id:1,name:'热力追踪'},{id:2,name:'矮人迷宫'},{id:3,name:'小熊罐子'}],recognition_types:[{id:1,name:'安全'}],deduction_types:[{id:1,name:'考勤'}]}};
 const has=p=>['DATA_VIEW','DATA_EXPORT','HR_MANAGE'].includes(p);
 let viewGeneration=0;const beginViewRequest=()=>{const generation=++viewGeneration;return {isCurrent:()=>generation===viewGeneration}};const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
 const fmt=v=>Number(v||0).toFixed(2),fmtDeduction=fmt,actingNoteMarkup=()=>'',monthStart=()=>'2026-11-01',today=()=>'2026-11-01',portalPath=s=>s;
 const monthNow=()=>'2026-11',registerPageCleanup=()=>{},TREND_SPANS=[3,6,12],confirmModal=async()=>true;
 const opt=(rows)=>rows.map(r=>'<option value="'+r.id+'">'+esc(r.name)+'</option>').join('');
 const queueExport=async url=>{exports.push(url);if(window.failExport)throw Error('导出失败测试');if(window.deferExport)await new Promise(resolve=>window.releaseExport=resolve)},toast=(...args)=>window.toasts.push(args);
 const api=async url=>{requests.push(url);const q=new URL(url,'http://fixture').searchParams;if(url.startsWith('/api/statistics/my-exports'))return {items:[]};if(url.startsWith('/api/statistics?'))return {month:q.get('month'),title:q.get('title')||'',summary:{employee_count:0},hierarchy:[],filters:{},loa_rows:[],issuer_keyword:q.get('issuer_keyword')||'',issuer_counts:{population_name:q.get('issuer_population'),attraction_name:'热力追踪',venue_columns:[],rows:[{employee_no:'1234567',employee_name:q.get('issuer_keyword')||'签卡AM',role_name:'AM',count:2,score:1,venue_counts:{}}]}};const payload={category:q.get('category'),population:q.get('population'),venue_columns:['leader','gsm_leader'].includes(q.get('category'))?[{key:'1',name:'热力追踪'},{key:'2',name:'矮人迷宫'},{key:'3',name:'小熊罐子'}]:[],circle_scope:state.me.role_code==='HR_CIRCLE'?'recipient':'issuer',population_name:q.get('population')==='all'?'全部人群':q.get('population')==='supervisor'?'LEAD':'CM/TR',rows:[{employee_id:11,employee_no:'1234567',employee_name:'本圈员工',role_name:'CM',rank:1,venue_counts:{'1':1,'2':0,'3':1},group_label:'本圈A组',count:2,score:1,recognition_score:1,deduction_score:0,attendance_score:12,total_score:13,leave_days:0,charged_days:0,recent_date:'2026-11-01'}],total:21,pages:2,page:Number(q.get('page')||1),page_size:20,attraction_name:state.options.employee_circles.find(c=>String(c.id)===q.get('attraction_id'))?.name||'全部景点圈',start_date:q.get('start_date'),end_date:q.get('end_date')};if(window.deferCategory===q.get('category'))await new Promise(resolve=>window.releaseRanking=resolve);if(window.emptyRanking){payload.rows=[];payload.total=0;payload.pages=1;}return payload;};
 window.fixtureState=state;window.renderRanking=renderPrRankings;window.rankingMenu=menuItems;window.managementCountsHtml=managementRecognitionCounts;window.renderStats=renderStatistics;
 })()`);
 for(const circleId of [1,2,3]){
  const offset=await page.evaluate(async id=>{const offset=requests.length;fixtureState.me.attraction_id=id;await renderRanking();return offset},circleId);
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
  await page.locator('#prExport').click();
  const url=await page.evaluate(()=>exports.at(-1)),qs=new URL(url,'http://fixture').searchParams;
  assert.equal(qs.get('attraction_id'),String(circleId));assert.equal(qs.get('population'),'frontline');assert.equal(qs.get('subtype_id'),'1');assert.equal(qs.get('keyword'),'本圈');assert.equal(qs.get('sort_by'),'count');
  const ownRequests=await page.evaluate(offset=>requests.slice(offset),offset);assert(ownRequests.length>0&&ownRequests.every(url=>new URL(url,'http://fixture').searchParams.get('attraction_id')===String(circleId)));
  for(const category of ['leader','gsm_leader']){
    await page.locator('[data-pr-category='+category+']').click();
    assert.match(await page.locator('#prResults').textContent(),/累计/);
    assert.match(await page.locator('#prResults').textContent(),/次数/);
    await page.locator('#prExport').click();
    const issuedUrl=await page.evaluate(()=>exports.at(-1));
    assert.equal(new URL(issuedUrl,'http://fixture').searchParams.get('category'),category);
  }
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'overflow '+width);
 }
 await page.evaluate(async()=>{fixtureState.me.role_code='GSM';fixtureState.me.base_role_code='GSM';fixtureState.me.attraction_id=1;await renderRanking()});
 assert.equal(await page.locator('#prFilter [name=attraction_id] option').count(),4);assert(await page.locator('#prExport').isVisible());
 assert.equal(await page.locator('[data-pr-category]').count(),7);assert.equal(await page.locator('[name=population] option').count(),2);
 // The manager's issuer boards reuse the population control and show occurrence
 // counts in desktop tables and mobile cards.  The other four boards stay split.
 await page.locator('[data-pr-category=gsm_leader]').click();
 assert.equal(await page.locator('[data-pr-category=gsm_leader]').textContent(),'管理层认可次数');
 assert.match(await page.locator('.pr-ranking-table th').nth(1).textContent(),/AM \/ OM/);
 assert.equal(await page.locator('[name=population] option').count(),3);
 assert.equal(await page.locator('[data-pr-circle-label]').textContent(),'签卡人所属圈');
 assert.equal(await page.locator('[data-pr-search-label]').textContent(),'签卡人搜索');
 await page.locator('[name=population]').selectOption('all');
 await page.locator('#prExport').click();
 assert.equal(new URL(await page.evaluate(()=>exports.at(-1)),'http://fixture').searchParams.get('population'),'all');
 for(const category of ['overall','recognition','deduction','absence']){
  await page.locator('[data-pr-category='+category+']').click();
  assert.equal(await page.locator('[name=population] option').count(),2);
  assert.equal(await page.locator('[data-pr-circle-label]').textContent(),'景点圈');
  await page.locator('[name=population]').selectOption('supervisor');
  assert.equal(new URL(await page.evaluate(()=>requests.at(-1)),'http://fixture').searchParams.get('population'),'supervisor');
 }
 await page.locator('[data-pr-category=leader]').click();
 assert(await page.locator('[name=population]').isDisabled());
 assert.equal(await page.locator('[name=population]').inputValue(),'frontline');
 await page.locator('#prExport').click();
 assert.equal(new URL(await page.evaluate(()=>exports.at(-1)),'http://fixture').searchParams.get('population'),'frontline');
 await page.locator('[data-pr-category=gsm_leader]').click();
 assert.equal(await page.locator('[name=population]').inputValue(),'all');
 assert.match(await page.locator('#prResults').textContent(),/认可次数（总数）/);
 assert.deepEqual((await page.locator('.pr-ranking-table th').allTextContents()).slice(3,7),['认可次数（总数）','热力追踪','矮人迷宫','小熊罐子']);
 const counts=(await page.locator('.pr-ranking-table tbody tr').first().locator('td').allTextContents()).slice(3,7).map(Number);
 assert.equal(counts[0],counts.slice(1).reduce((a,b)=>a+b,0));
 assert.deepEqual((await page.locator('.pr-ranking-card dt').allTextContents()).slice(1,5),['认可次数（总数）','热力追踪','矮人迷宫','小熊罐子']);
 await page.evaluate(()=>window.emptyRanking=true);
 await page.locator('#prFilter button.primary').click();
 assert.equal(await page.locator('.pr-ranking-table tbody td.empty').getAttribute('colspan'),'9');
 await page.evaluate(()=>{window.emptyRanking=false;window.deferCategory='leader'});
 await page.locator('[data-pr-category=leader]').click();
 await page.locator('[data-pr-category=recognition]').click();
 await page.evaluate(()=>{window.deferCategory=null;window.releaseRanking()});
 assert.equal(await page.locator('.pr-ranking-table th').nth(1).textContent(),'员工');
 await page.locator('[data-pr-category=gsm_leader]').click();
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'manager overflow '+width);
 const beforeExports=await page.evaluate(()=>exports.length);
 await page.evaluate(()=>window.deferExport=true);
 await page.locator('#prExport').click();
 assert(await page.locator('#prExport').isDisabled());
 await page.locator('[data-pr-category=recognition]').click();
 assert(await page.locator('#prExport').isDisabled(),'switching tabs must not unlock a pending export');
 await page.evaluate(()=>document.getElementById('prExport').click());
 assert.equal(await page.evaluate(()=>exports.length),beforeExports+1);
 await page.evaluate(()=>{window.deferExport=false;window.releaseExport()});
 assert(await page.locator('#prExport').isEnabled());
 await page.evaluate(()=>window.deferExport=true);
 await page.locator('#prExport').click();
 await page.evaluate(async()=>{window.releaseOldExport=window.releaseExport;await renderRanking()});
 assert(await page.locator('#prExport').isEnabled());
 await page.locator('#prExport').click();
 const toastCount=await page.evaluate(()=>toasts.length);
 await page.evaluate(()=>window.releaseOldExport());
 assert(await page.locator('#prExport').isDisabled(),'an old view must not unlock the new view export');
 assert.equal(await page.evaluate(()=>toasts.length),toastCount);
 await page.evaluate(()=>{window.deferExport=false;window.releaseExport()});
 assert(await page.locator('#prExport').isEnabled());
 await page.evaluate(()=>window.failExport=true);
 await page.locator('#prExport').click();
 assert(await page.locator('#prExport').isEnabled(),'failed export must allow retry');
 assert.deepEqual(await page.evaluate(()=>toasts.at(-1)),['导出失败测试',true]);
 await page.evaluate(()=>window.failExport=false);
 await page.evaluate(async()=>{fixtureState.me.role_code='TA_GSM';fixtureState.me.base_role_code='SUPERVISOR';await renderRanking()});
 assert.equal(await page.locator('#prExport').count(),0);
 await page.evaluate(()=>{fixtureState.me.role_code='HR_ADMIN';fixtureState.me.base_role_code='HR_ADMIN'});
 assert(!(await page.evaluate(()=>rankingMenu())).some(([id])=>id==='prRankings'));
 await page.evaluate(()=>{
   document.getElementById('app').innerHTML=managementCountsHtml({issuer_counts:{
     venue_columns:[{key:'1',name:'热力追踪'},{key:'2',name:'矮人迷宫'},{key:'3',name:'小熊罐子'}],
     rows:[{employee_name:'签卡AM',employee_no:'1234567',role_name:'AM',count:3,venue_counts:{'1':1,'2':2},score:2},
           {employee_name:'签卡OM',employee_no:'1234568',role_name:'OM',count:1,venue_counts:{'3':1},score:0}],
   }});
 });
 assert.equal(await page.locator('[data-management-recognitions] tbody tr').count(),2);
 assert.match(await page.locator('[data-management-recognitions]').textContent(),/签卡AM.*AM.*3/);
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'statistics issuer overflow '+width);
 await page.evaluate(async()=>{fixtureState.me.role_code='GSM';fixtureState.me.employee_no='GSMTEST';await renderStats()});
 await page.locator('#statsFilter [name=keyword]').fill('员工筛选');
 await page.locator('#statsFilter [name=issuer_keyword]').fill('AMTEST01');
 await page.locator('#statsFilter [name=issuer_population]').selectOption('supervisor');
 await page.locator('#statsFilter button.primary').click();
 await page.locator('[data-management-recognitions] tbody tr').filter({hasText:'AMTEST01'}).waitFor();
 const statsQuery=new URL(await page.evaluate(()=>requests.filter(url=>url.startsWith('/api/statistics?')).at(-1)),'http://fixture').searchParams;
 assert.equal(statsQuery.get('keyword'),'员工筛选');
 assert.equal(statsQuery.get('issuer_keyword'),'AMTEST01');
 assert.equal(statsQuery.get('issuer_population'),'supervisor');
 await page.locator('#exportBtn').click();
 const exportQuery=new URL(await page.evaluate(()=>exports.at(-1)),'http://fixture').searchParams;
 assert.equal(exportQuery.get('keyword'),'员工筛选');
 assert.equal(exportQuery.get('issuer_keyword'),'AMTEST01');
 assert.equal(exportQuery.get('issuer_population'),'supervisor');
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'statistics filter overflow '+width);
 assert.deepEqual(errors,[]);await page.close();
}
console.log('PASS: HR and manager scope; six categories; population/search/type/sort/export; venue tables and mobile cards; empty results; stale response race; TA GSM export restriction; 320/390/1280px');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
