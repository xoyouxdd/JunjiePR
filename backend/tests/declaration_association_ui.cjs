// Real modules, read-only fixtures: ordinary records and both upgrade outcomes.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium,webkit}=require('./support/browser-runtime.cjs');
const root=path.resolve(__dirname,'../app/static');
const levels=[{id:1,code:'STATEMENT',name:'声明'},{id:2,code:'MEMO',name:'备忘录'},{id:3,code:'WARNING_1',name:'一级警告'},{id:4,code:'WARNING_2',name:'二级警告'}];
const base={employee_id:1,employee_name:'朱靖宇 <测试>',employee_no:'2057762',attraction_id:1,attraction_name:'热力追踪',occurred_on:'2026-09-01',type_id:1,type_name:'违规病假',level_id:1,level_code:'STATEMENT',level_name:'声明',description:'原文 <script>不应执行</script>',submitter_name:'登记人',submitted_at:'2026-09-30 14:20',material_status:'材料已就绪',upgrade_status:'无',raw_status:'active',business_status:'已生效',upgrade_request_id:0,upgrade_role:null,actual_points:1,score_status:'已扣分',counted_in_statistics:true,document_url:'/api/files/42',upgrade_links:[]};
const rows=[
 {...base,id:70,upgrade_role:'source_first',score_status:'首次声明 · 已扣分'},
 {...base,id:71,occurred_on:'2026-09-30',upgrade_role:'source_second',actual_points:0,score_status:'第二次声明 · 未扣分',counted_in_statistics:false},
 {...base,id:80,level_id:2,level_code:'MEMO',level_name:'备忘录',upgrade_role:'result',actual_points:3,score_status:'升级结果 · 已扣分',document_url:''},
 {...base,id:90,upgrade_role:'source_first',score_status:'首次声明 · 已扣分'},
 {...base,id:91,upgrade_role:'source_second',actual_points:0,score_status:'第二次声明 · 未扣分',counted_in_statistics:false},
 {...base,id:92,level_id:3,level_code:'WARNING_1',level_name:'一级警告',upgrade_role:'result',actual_points:5,score_status:'升级结果 · 已扣分',document_url:''},
 {...base,id:20,type_name:'考勤-迟到30分钟内',actual_points:1.5,score_status:'已扣分',upgrade_slot_open:true},
 {...base,id:57,type_name:'考勤-迟到30分钟以上',level_id:3,level_code:'WARNING_1',level_name:'一级警告',actual_points:5},
 {...base,id:166,type_name:'考勤-早退30分钟内',raw_status:'pending_material',business_status:'待补材料',actual_points:0,score_status:'待补材料 · 未扣分',material_status:'材料缺失',document_url:''},
 {...base,id:64,raw_status:'void',business_status:'已作废',actual_points:0,score_status:'已作废 · 不计分',void_reason:'原因填写错误'},
 {...base,id:65,raw_status:'pending_upgrade',business_status:'审核中',upgrade_role:'source_second',actual_points:0,score_status:'审核中 · 未扣分'},
 {...base,id:66,upgrade_role:'source_first',occurred_on:'2026-08-30'},
 {...base,id:67,upgrade_role:'source_first',occurred_on:'2026-08-31'},
 {...base,id:68,upgrade_role:'source_first',occurred_on:'2026-08-31'},
 {...base,id:168,raw_status:'pending_upgrade',business_status:'审核中',actual_points:0,score_status:'审核中 · 未扣分'},
];
rows.forEach(row=>{row.registered_points=row.upgrade_role==='source_second'?7:row.actual_points;row.deduction_month=[66,67,68,70,90].includes(row.id)?'2026-08':'2026-09';row.loa_excluded=[80,90].includes(row.id);if(row.loa_excluded){row.actual_points=0;row.score_status=(row.upgrade_role==='source_first'?'首次声明':'升级结果')+' · 未扣分 · LOA当月不计分'}});
for(const [first,second,work,statusName,status] of [[66,65,66,'材料已就绪 · 待重新指派','awaiting_material'],[67,166,67,'待材料就绪','awaiting_material'],[68,168,68,'待审核','pending']]){
 const records=[{id:first,role:'source_first',level_name:'声明'},{id:second,role:'source_second',level_name:'声明'}];
 for(const record of records)Object.assign(rows.find(row=>row.id===record.id),{
  upgrade_role:record.role,upgrade_links:[{request_id:work,status,status_name:statusName,role:record.role,records}],
 });
}
for(const ids of [[70,71,80],[90,91,92]])for(const id of ids){const row=rows.find(r=>r.id===id);row.upgrade_links=[{request_id:ids[0],status:'approved',role:row.upgrade_role,records:ids.map(rid=>{const r=rows.find(x=>x.id===rid);return {id:rid,role:r.upgrade_role,level_name:r.level_name}})}]}
const rejectedMembers=[{id:70,role:'source_first',level_name:'声明'},{id:64,role:'source_second',level_name:'声明'}];
rows.find(r=>r.id===70).upgrade_links.unshift({request_id:69,status:'rejected',role:'source_first',records:rejectedMembers});
Object.assign(rows.find(r=>r.id===64),{registered_points:1,upgrade_role:'source_second',upgrade_links:[{request_id:69,status:'rejected',role:'source_second',records:rejectedMembers}]});
function dataFor(onlySecond=false,slotOpen=true){const details=onlySecond?rows.filter(r=>r.id===71):rows.map(row=>row.id===20?{...row,upgrade_slot_open:slotOpen}:row),records=details.filter(r=>r.counted_in_statistics);return {month:'2026-09',total_records:records.length,employee_count:records.length?1:0,levels:levels.map(l=>({...l,count:records.filter(r=>r.level_id===l.id).length})),types:[{id:1,name:'违规病假',active:true}],attractions:[{id:1,name:'热力追踪'}],statuses:Object.fromEntries(['已生效','待补材料','审核中','已作废'].map(s=>[s,records.filter(r=>r.business_status===s).length])),records,detail_records:details,related_records:rows.filter(r=>r.upgrade_links.length),circles:records.length?[{attraction_id:1,attraction_name:'热力追踪',record_count:records.length,employee_count:1,levels:levels.filter(l=>records.some(r=>r.level_id===l.id)).map(l=>({...l,count:records.filter(r=>r.level_id===l.id).length}))}]:[]}}
const startup="import {state} from '/static/js/app/state.js';import {renderDeclarationStatistics} from '/static/js/app/declarations.js';window.show=async admin=>{state.me={permissions:['DECLARATION_STATS_VIEW',...(admin?['SYSTEM_ADMIN']:[])]};await renderDeclarationStatistics();};await show(true);window.ready=true;";
const server=http.createServer((req,res)=>{const url=new URL(req.url,'http://x');if(url.pathname==='/fixture.js'){res.setHeader('Content-Type','text/javascript');return res.end(startup)}if(url.pathname==='/'){res.setHeader('Content-Type','text/html;charset=utf-8');return res.end('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/style.css"><header class="topbar app-topbar"><strong>FZ工作台</strong></header><nav id="tabs" class="tabs"><div class="tabs-mobile"><button type="button">更多</button></div></nav><main class="app-shell"><div id="app"></div></main><div id="toast"></div><script type="module" src="/fixture.js"></script>')}const file=path.resolve(root,'.'+url.pathname.replace('/static',''));if(!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.statusCode=404;return res.end()}res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'application/octet-stream');res.end(fs.readFileSync(file))});
(async()=>{await new Promise(r=>server.listen(0,'127.0.0.1',r));try{for(const [name,engine,options] of [['edge',chromium,{channel:'msedge'}],['webkit',webkit,{}]]){const browser=await engine.launch({...options,headless:true});try{for(const width of [1440,390,320]){const page=await browser.newPage({viewport:{width,height:width===320?640:1000},hasTouch:width<=760}),errors=[],writes=[];page.on('pageerror',e=>errors.push(e.message));let onlySecond=false,slotOpen=true;await page.route('**/api/**',route=>{if(route.request().method()!=='GET')writes.push(route.request().url());return route.fulfill({json:dataFor(onlySecond,slotOpen)})});await page.goto('http://127.0.0.1:'+server.address().port);await page.waitForFunction(()=>window.ready);
 const press=async locator=>{
  if(width>760)return locator.click();
  await locator.evaluate(element=>{
   const rect=element.getBoundingClientRect();
   const top=document.querySelector('.app-topbar').getBoundingClientRect().bottom;
   const bottom=document.getElementById('tabs').getBoundingClientRect().top;
   if(rect.top<top||rect.bottom>bottom)element.scrollIntoView({block:'center',behavior:'instant'});
  });
  return locator.tap();
 };
 const detail=page.locator('[data-declaration-circle-detail]');
 const circle=page.locator('[data-declaration-circle]');
 const row=(id,group=detail)=>group.locator('[data-declaration-list-row="'+id+'"]');
 const summary=(id,group=detail)=>row(id,group).locator(':scope > summary');
 const open=async(id,group=detail)=>{
  if(await row(id,group).getAttribute('open')===null)await press(summary(id,group));
  await row(id,group).locator('[data-declaration-pair]').waitFor();
 };
 const card=(id,container)=>container.locator('[data-declaration-record="'+id+'"]');
 const member=(id,container)=>container.locator('[data-declaration-member="'+id+'"]');
 const toggleLevel=id=>page.locator('[data-declaration-level="1:'+id+'"]');
 const levelGroup=id=>page.locator('[data-declaration-level-detail="1:'+id+'"]');
 for(const [level,id] of [[1,71],[2,80],[3,92]]){
  const group=levelGroup(level);
  await press(toggleLevel(level));
  assert.equal(await group.locator('[data-declaration-record],[data-declaration-pair],[data-file-preview]').count(),0);
  assert.match(await summary(id,group).innerText(),/2057762.*2026-09/s);
  assert.doesNotMatch(await summary(id,group).innerText(),/原文|实际扣分|升级结果/);
  await open(id,group);
  const selected=member(id,row(id,group));
  assert.notEqual(await selected.getAttribute('open'),null);
  assert.ok(await selected.locator('.declaration-description').isVisible());
  await press(toggleLevel(level));
 }
 await press(circle);
 assert.equal(await detail.locator('[data-declaration-list-row]').count(),15);
 assert.equal(await detail.locator('[data-declaration-record]').count(),0);
 await open(20);
 assert.ok((await card(20,row(20)).locator('.declaration-score').innerText()).includes('−1.5'));
 assert.ok((await row(20).locator('[data-declaration-side]').innerText()).includes('等待第二张声明'));
 for(const [id,score] of [[57,'已扣分'],[64,'已作废 · 不计分']]){
  await open(id);
  assert.equal(await row(id).locator('[data-declaration-side]').isVisible(),false);
  assert.match(await card(id,row(id)).locator('.declaration-score').innerText(),new RegExp(score));
 }
 assert.ok((await card(64,row(64)).textContent()).includes('原因填写错误'));
 for(const [first,second,label,score] of [[66,65,'材料已就绪 · 待重新指派','审核中 · 未扣分'],[67,166,'待材料就绪','待补材料 · 未扣分'],[68,168,'待审核','审核中 · 未扣分']]){
  await open(first);
  const parent=row(first),leftCard=card(first,parent),side=parent.locator('[data-declaration-side]');
  assert.equal(await side.locator('.declaration-upgrade-heading strong').textContent(),'工单 #'+first);
  assert.equal(await side.locator('.declaration-upgrade-heading .badge').textContent(),label);
  assert.equal(await card(second,side).isVisible(),false);
  assert.ok((await leftCard.innerText()).includes('绩效月份 2026-08'));
  const left=await leftCard.boundingBox(),right=await side.boundingBox();
  assert.ok(width<=760?right.y>=left.y+left.height:right.x>=left.x+left.width);
  await press(member(second,parent).locator('summary').first());
  const secondCard=card(second,side);
  assert.ok((await secondCard.innerText()).includes('绩效月份 2026-09'));
  assert.ok((await secondCard.innerText()).includes(score));
  assert.equal(await side.locator('.declaration-upgrade-heading .badge').textContent(),label);
 }
 await open(70);
 const firstCard=card(70,row(70));
 await press(firstCard.locator('summary'));
 assert.ok((await firstCard.innerText()).includes('工单 #69 · 审核不通过'));
 const area=row(70).locator('[data-declaration-relation-view]');
 await press(firstCard.locator('[data-declaration-related="64"]'));
 assert.ok((await area.innerText()).includes('已作废 · 不计分'));
 await press(area.locator('summary'));
 await press(area.locator('[data-declaration-related="70"]'));
 assert.ok((await area.textContent()).includes('工单 #69 · 审核不通过'));
 await press(area.locator('[data-declaration-relation-close]'));
 await area.waitFor({state:'hidden'});
 assert.ok(await row(70).locator('[data-declaration-upgrade-records]').isVisible());
 assert.ok((await firstCard.locator('.declaration-score-reason').textContent()).includes('首次声明的扣分保留'));
 for(const [source,result,second,label] of [[70,80,71,'备忘录'],[90,92,91,'一级警告']]){
  await open(second);
  const parent=row(second),original=parent.locator('.declaration-original'),right=parent.locator('[data-declaration-side]');
  const left=await original.elementHandle(),leftText=await original.innerText();
  assert.ok(leftText.includes('绩效月份 2026-08'));
  const secondCard=card(second,right);
  assert.ok((await secondCard.innerText()).includes('第二次声明 · 未扣分'));
  assert.equal(await secondCard.locator('[data-file-preview]').getAttribute('data-file-preview'),'/api/files/42');
  assert.ok((await secondCard.locator('.declaration-score-reason').textContent()).includes('不单独扣分'));
  const resultCard=card(result,right);
  assert.equal(await resultCard.isVisible(),false);
  const resultSummary=member(result,parent).locator(':scope > summary');
  if(width===320)await resultSummary.evaluate(element=>{
   const top=document.querySelector('.app-topbar').getBoundingClientRect().bottom;
   scrollBy({top:element.getBoundingClientRect().top-top-40,behavior:'instant'});
  });
  await press(resultSummary);
  if(width===320){
   await page.waitForFunction(element=>{
    const summary=element.querySelector('summary').getBoundingClientRect();
    const heading=element.querySelector('.declaration-record-heading').getBoundingClientRect();
    const top=document.querySelector('.app-topbar').getBoundingClientRect().bottom;
    const bottom=document.getElementById('tabs').getBoundingClientRect().top;
    return element.open&&summary.top>=top&&heading.bottom<=bottom;
   },await member(result,parent).elementHandle(),{timeout:2000});
  }
  assert.ok((await resultCard.innerText()).includes(label+' #'+result));
  assert.ok((await resultCard.innerText()).includes('绩效月份 2026-09'));
  assert.equal((await resultCard.innerText()).includes('LOA当月不计分'),result===80);
  assert.equal(await resultCard.locator('.declaration-registered-score').textContent(),'原登记分数 '+(result===80?3:5)+' 分');
  if(result===80)assert.ok((await resultCard.locator('.declaration-score-reason').textContent()).includes('本条所属月份为 LOA'));
  assert.equal(await resultCard.locator('[data-correct-type],[data-correct-level],[data-declaration-action]').count(),0);
  assert.equal(await secondCard.isVisible(),false);
  await press(member(second,parent).locator(':scope > summary'));
  assert.equal(await secondCard.locator('.declaration-registered-score').textContent(),'原登记分数 7 分');
  assert.equal(await left.evaluate(node=>node.isConnected),true);
  assert.equal(await original.innerText(),leftText);
  assert.equal(await parent.locator('[data-declaration-pair]').getAttribute('data-declaration-pair'),String(source));
 }
 assert.equal(await detail.locator('script').count(),0);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 // Closing and reopening the group starts at the compact list again.
 await press(circle);await press(circle);
 assert.equal(await detail.locator('[data-declaration-pair]').count(),0);
 await open(71);
 if(name==='edge'){const out=path.resolve(__dirname,'../../output/release-ui');fs.mkdirSync(out,{recursive:true});if(width===1440)await card(71,row(71)).locator('.declaration-score').screenshot({path:path.join(out,'declaration-second-score.png')});await page.screenshot({path:path.join(out,'declaration-association-'+width+'.png')});}
 // A fresh query reflects lost eligibility, restoration, and expiry.
 for(const eligible of [false,true,false]){
  slotOpen=eligible;
  await press(page.locator('#declarationFilter button[type=submit]'));
  await press(page.locator('[data-declaration-circle][aria-expanded="false"]'));
  await open(20);
  assert.equal(await row(20).locator('[data-declaration-side]').isVisible(),eligible);
  assert.ok((await card(20,row(20)).locator('.declaration-score').innerText()).includes('−1.5'));
 }
 onlySecond=true;
 await press(page.locator('#declarationFilter button[type=submit]'));
 await page.locator('[data-declaration-circle][aria-expanded="false"]').waitFor();
 await press(toggleLevel(1));
 const filtered=levelGroup(1);
 assert.equal(await filtered.locator('[data-declaration-list-row]').count(),1);
 assert.equal(await filtered.locator('[data-declaration-pair]').count(),0);
 await open(71,filtered);
 const filteredPair=row(71,filtered);
 assert.ok((await filteredPair.locator('.declaration-original').innerText()).includes('绩效月份 2026-08'));
 assert.ok((await card(71,filteredPair).innerText()).includes('绩效月份 2026-09'));
 await press(member(80,filteredPair).locator(':scope > summary'));
 assert.ok((await card(80,filteredPair).innerText()).includes('备忘录 #80'));
 await page.evaluate(()=>show(false));await press(circle);await open(71);
 assert.equal(await page.locator('[data-correct-type],[data-correct-level],[data-declaration-action]').count(),0);
 assert.deepEqual(writes,[]);assert.deepEqual(errors,[]);await page.close();
 }console.log(name+': cards, both upgrade outcomes, cross-filter reciprocal links, score states, readonly and 1440/390/320 bounds passed');}finally{await browser.close()}}}finally{server.closeAllConnections();await new Promise(r=>server.close(r))}})().catch(e=>{console.error(e);process.exitCode=1});
