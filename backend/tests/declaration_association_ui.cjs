// Real modules, read-only fixtures: ordinary records and both upgrade outcomes.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium,webkit}=require('playwright');
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
 {...base,id:20,type_name:'考勤-迟到30分钟内',actual_points:1.5,score_status:'已扣分'},
 {...base,id:57,type_name:'考勤-迟到30分钟以上',level_id:3,level_code:'WARNING_1',level_name:'一级警告',actual_points:5},
 {...base,id:166,type_name:'考勤-早退30分钟内',raw_status:'pending_material',business_status:'待补材料',actual_points:0,score_status:'待补材料 · 未扣分',material_status:'材料缺失',document_url:''},
 {...base,id:64,raw_status:'void',business_status:'已作废',actual_points:0,score_status:'已作废 · 不计分',void_reason:'原因填写错误'},
 {...base,id:65,raw_status:'pending_upgrade',business_status:'审核中',upgrade_role:'source_second',actual_points:0,score_status:'审核中 · 未扣分',document_url:''},
];
rows.forEach(row=>{row.registered_points=row.upgrade_role==='source_second'?7:row.actual_points;row.deduction_month=[70,90].includes(row.id)?'2026-08':'2026-09';row.loa_excluded=[80,90].includes(row.id);if(row.loa_excluded){row.actual_points=0;row.score_status=(row.upgrade_role==='source_first'?'首次声明':'升级结果')+' · 未扣分 · LOA当月不计分'}});
for(const ids of [[70,71,80],[90,91,92]])for(const id of ids){const row=rows.find(r=>r.id===id);row.upgrade_links=[{request_id:ids[0],status:'approved',role:row.upgrade_role,records:ids.map(rid=>{const r=rows.find(x=>x.id===rid);return {id:rid,role:r.upgrade_role,level_name:r.level_name}})}]}
const rejectedMembers=[{id:70,role:'source_first',level_name:'声明'},{id:64,role:'source_second',level_name:'声明'}];
rows.find(r=>r.id===70).upgrade_links.unshift({request_id:69,status:'rejected',role:'source_first',records:rejectedMembers});
Object.assign(rows.find(r=>r.id===64),{registered_points:1,upgrade_role:'source_second',upgrade_links:[{request_id:69,status:'rejected',role:'source_second',records:rejectedMembers}]});
function dataFor(onlySecond=false){const details=onlySecond?rows.filter(r=>r.id===71):rows,records=details.filter(r=>r.counted_in_statistics);return {month:'2026-09',total_records:records.length,employee_count:records.length?1:0,levels:levels.map(l=>({...l,count:records.filter(r=>r.level_id===l.id).length})),types:[{id:1,name:'违规病假',active:true}],attractions:[{id:1,name:'热力追踪'}],statuses:Object.fromEntries(['已生效','待补材料','审核中','已作废'].map(s=>[s,records.filter(r=>r.business_status===s).length])),records,detail_records:details,related_records:rows.filter(r=>r.upgrade_links.length),circles:records.length?[{attraction_id:1,attraction_name:'热力追踪',record_count:records.length,employee_count:1,levels:levels.filter(l=>records.some(r=>r.level_id===l.id)).map(l=>({...l,count:records.filter(r=>r.level_id===l.id).length}))}]:[]}}
const startup="import {state} from '/static/js/app/state.js';import {renderDeclarationStatistics} from '/static/js/app/declarations.js';window.show=async admin=>{state.me={permissions:['DECLARATION_STATS_VIEW',...(admin?['SYSTEM_ADMIN']:[])]};await renderDeclarationStatistics();};await show(true);window.ready=true;";
const server=http.createServer((req,res)=>{const url=new URL(req.url,'http://x');if(url.pathname==='/fixture.js'){res.setHeader('Content-Type','text/javascript');return res.end(startup)}if(url.pathname==='/'){res.setHeader('Content-Type','text/html;charset=utf-8');return res.end('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/style.css"><main id="app"></main><div id="tabs"></div><div id="toast"></div><script type="module" src="/fixture.js"></script>')}const file=path.resolve(root,'.'+url.pathname.replace('/static',''));if(!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.statusCode=404;return res.end()}res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'application/octet-stream');res.end(fs.readFileSync(file))});
(async()=>{await new Promise(r=>server.listen(0,'127.0.0.1',r));try{for(const [name,engine,options] of [['edge',chromium,{channel:'msedge'}],['webkit',webkit,{}]]){const browser=await engine.launch({...options,headless:true});try{for(const width of [1440,390,320]){const page=await browser.newPage({viewport:{width,height:1000}}),errors=[],writes=[];page.on('pageerror',e=>errors.push(e.message));let onlySecond=false;await page.route('**/api/**',route=>{if(route.request().method()!=='GET')writes.push(route.request().url());return route.fulfill({json:dataFor(onlySecond)})});await page.goto('http://127.0.0.1:'+server.address().port);await page.waitForFunction(()=>window.ready);await page.locator('[data-declaration-circle]').click();
 const detail=page.locator('[data-declaration-circle-detail]'),visible=detail.locator(width<=760?':scope > .declaration-mobile':':scope > .declaration-desktop');
 assert.equal(await visible.locator('[data-declaration-record]').count(),11);
 const card=id=>visible.locator(`[data-declaration-record="${id}"]`);
 for(const [id,text] of [[70,'首次声明 · 已扣分'],[71,'第二次声明 · 未扣分'],[166,'待补材料 · 未扣分'],[64,'已作废 · 不计分'],[65,'审核中 · 未扣分']])assert.ok((await card(id).innerText()).includes(text));
 assert.ok((await card(20).locator('.declaration-score').innerText()).includes('−1.5'));
 assert.ok((await card(70).locator('.declaration-score-reason').innerText()).includes('首次声明的扣分保留'));
 assert.ok((await card(71).locator('.declaration-score-reason').innerText()).includes('不单独扣分；请查看对应备忘录或警告'));
 assert.ok((await card(80).locator('.declaration-score-reason').innerText()).includes('本条所属月份为 LOA'));
 assert.ok((await card(64).locator('.declaration-score-reason').innerText()).includes('已作废，不计入绩效扣分'));
 assert.ok((await card(65).locator('.declaration-score-reason').innerText()).includes('尚未生效，当前不扣分'));
 assert.equal(await card(71).locator('header .declaration-score').count(),0);
 assert.equal(await card(80).locator('.declaration-registered-score').innerText(),'原登记分数 3 分');
 assert.ok((await card(80).locator('.declaration-performance-score').innerText()).includes('0'));
 assert.ok((await card(80).innerText()).includes('LOA当月不计分'));
 assert.ok((await card(64).innerText()).includes('原因填写错误'));
 assert.equal(await card(57).locator('[data-declaration-related]').count(),0);
 assert.ok((await card(57).innerText()).includes('无升级关联'));
 assert.equal(await card(80).locator('[data-correct-level],[data-correct-type],[data-declaration-action]').count(),0);
 assert.equal(await card(71).locator('[data-file-preview]').getAttribute('data-file-preview'),'/api/files/42');
 assert.equal(await visible.locator('script').count(),0);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 const area=detail.locator('[data-declaration-relation-view]');
 assert.ok((await card(70).innerText()).includes('工单 #69 · 审核不通过'));
 assert.ok((await card(70).innerText()).includes('工单 #70 · 已升级'));
 await card(70).locator('[data-declaration-related="64"]').click();
 assert.ok((await area.innerText()).includes('已作废 · 不计分'));
 await area.locator(width<=760?'.declaration-mobile [data-declaration-related="70"]':'.declaration-desktop [data-declaration-related="70"]').click();
 assert.ok((await area.innerText()).includes('工单 #69 · 审核不通过'));
 assert.ok((await area.innerText()).includes('工单 #70 · 已升级'));
 await area.locator('[data-declaration-relation-close]').click();
 for(const [source,result,other,label] of [[70,80,71,'备忘录'],[90,92,91,'一级警告']]){
  await card(source).locator(`[data-declaration-related="${result}"]`).click();
  assert.ok((await area.locator('h3').innerText()).includes(label+' #'+result));
  const relation=area.locator(width<=760?'.declaration-mobile':'.declaration-desktop');
  assert.ok((await relation.innerText()).includes('绩效月份 2026-09'));
  assert.equal((await relation.innerText()).includes('LOA当月不计分'),result===80);
  assert.equal(await relation.locator('.declaration-registered-score').innerText(),`原登记分数 ${result===80?3:5} 分`);
  await relation.locator(`[data-declaration-related="${other}"]`).click();
  assert.ok((await area.locator('h3').innerText()).includes('声明 #'+other));
  assert.equal(await area.locator(width<=760?'.declaration-mobile .declaration-registered-score':'.declaration-desktop .declaration-registered-score').innerText(),'原登记分数 7 分');
  assert.equal(await area.locator('[data-correct-type],[data-correct-level],[data-declaration-action]').count(),0);
  await area.locator('[data-declaration-relation-close]').click();await area.waitFor({state:'hidden'});
 }
 // Re-expansion must not multiply delegated handlers or leave stale relation views.
 await page.locator('[data-declaration-circle]').click();await page.locator('[data-declaration-circle]').click();
 await card(70).locator('[data-declaration-related="80"]').click();assert.equal(await area.locator('h3').count(),1);
 await area.locator('[data-declaration-relation-close]').click();
 if(name==='edge'){const out=path.resolve(__dirname,'../../output/release-ui');fs.mkdirSync(out,{recursive:true});if(width===1440)await card(71).locator('.declaration-score').screenshot({path:path.join(out,'declaration-second-score.png')});await page.screenshot({path:path.join(out,`declaration-association-${width}.png`)});}
 onlySecond=true;await page.locator('#declarationFilter button[type=submit]').click();await page.locator('[data-declaration-circle]').waitFor();await page.locator('[data-declaration-level]').click();
 const filtered=page.locator('[data-declaration-level-detail]');assert.equal(await filtered.locator(width<=760?'.declaration-mobile > article':'.declaration-desktop > article').count(),1);
 await filtered.locator(width<=760?'.declaration-mobile [data-declaration-related="80"]':'.declaration-desktop [data-declaration-related="80"]').click();assert.ok((await filtered.locator('[data-declaration-relation-view] h3').innerText()).includes('备忘录 #80'));
 await page.evaluate(()=>show(false));await page.locator('[data-declaration-circle]').click();assert.equal(await page.locator('[data-correct-type],[data-correct-level],[data-declaration-action]').count(),0);
 assert.deepEqual(writes,[]);assert.deepEqual(errors,[]);await page.close();
 }console.log(name+': cards, both upgrade outcomes, cross-filter reciprocal links, score states, readonly and 1440/390/320 bounds passed');}finally{await browser.close()}}}finally{server.closeAllConnections();await new Promise(r=>server.close(r))}})().catch(e=>{console.error(e);process.exitCode=1});
