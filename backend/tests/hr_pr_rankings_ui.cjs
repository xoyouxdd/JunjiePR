// Real ranking renderer and menu function; isolated API responses, no live account access.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../app/static');
const ranking=fs.readFileSync(path.join(root,'js/app/rankings.js'),'utf8').replace(/^import .*\r?\n/gm,'').replace(/^export .*$/gm,'');
const navigation=fs.readFileSync(path.join(root,'js/app/navigation.js'),'utf8');
const start=navigation.indexOf('function menuItems() {'),menu=navigation.slice(start,navigation.indexOf('\n}',start)+2);
const css=['style.css','workspace-ui.css'].map(f=>fs.readFileSync(path.join(root,'css',f),'utf8')).join('\n');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
for(const width of [320,390,1280]){
 const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.setContent('<style>'+css+'</style><main id="app" data-workspace-ui></main>');
 await page.evaluate('(()=>{'+ranking+'\n'+menu+`\nwindow.requests=[];window.exports=[];
 const app=document.getElementById('app');
 const state={me:{role_code:'HR_CIRCLE',base_role_code:'HR_CIRCLE',attraction_id:1},options:{employee_circles:[{id:1,name:'热力追踪'},{id:2,name:'矮人迷宫'},{id:3,name:'小熊罐子'}],recognition_types:[{id:1,name:'安全'}],deduction_types:[{id:1,name:'考勤'}]}};
 const has=p=>['DATA_VIEW','DATA_EXPORT','HR_MANAGE'].includes(p);
 const beginViewRequest=()=>({isCurrent:()=>true});const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
 const fmt=v=>Number(v||0).toFixed(2),fmtDeduction=fmt,actingNoteMarkup=()=>'',monthStart=()=>'2026-11-01',today=()=>'2026-11-01',portalPath=s=>s;
 const opt=(rows)=>rows.map(r=>'<option value="'+r.id+'">'+esc(r.name)+'</option>').join('');
 const queueExport=async url=>exports.push(url),toast=()=>{};
 const api=async url=>{requests.push(url);const q=new URL(url,'http://fixture').searchParams;return {category:q.get('category'),population:q.get('population'),rows:[{employee_id:11,employee_no:'1234567',employee_name:'本圈员工',role_name:'CM',rank:1,group_label:'本圈A组',count:2,score:1,recognition_score:1,deduction_score:0,attendance_score:12,total_score:13,leave_days:0,charged_days:0,recent_date:'2026-11-01'}],total:21,pages:2,page:Number(q.get('page')||1),page_size:20,attraction_name:state.options.employee_circles.find(c=>String(c.id)===q.get('attraction_id'))?.name||'全部景点圈',start_date:q.get('start_date'),end_date:q.get('end_date')};};
 window.fixtureState=state;window.renderRanking=renderPrRankings;window.rankingMenu=menuItems;
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
 assert.equal(await page.locator('[data-pr-category]').count(),6);assert.equal(await page.locator('[name=population] option').count(),2);
 await page.evaluate(async()=>{fixtureState.me.role_code='TA_GSM';fixtureState.me.base_role_code='SUPERVISOR';await renderRanking()});
 assert.equal(await page.locator('#prExport').count(),0);
 await page.evaluate(()=>{fixtureState.me.role_code='HR_ADMIN';fixtureState.me.base_role_code='HR_ADMIN'});
 assert(!(await page.evaluate(()=>rankingMenu())).some(([id])=>id==='prRankings'));
 assert.deepEqual(errors,[]);await page.close();
}
console.log('PASS: three HR circles; menu, fixed scope, all categories, population/search/type/sort and export; GSM/TA GSM/HR admin unchanged; 320/390/1280px');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
