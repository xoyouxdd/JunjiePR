const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium,webkit}=require('./support/browser-runtime.cjs');
const root=path.resolve(__dirname,'../app/static');
const startup="import {state,app} from '/static/js/app/state.js';import {beginViewRequest,configureViews,render} from '/static/js/app/context.js';import {renderDeclarationStatistics} from '/static/js/app/declarations.js';const pointerListeners=new Set(),add=document.addEventListener.bind(document),remove=document.removeEventListener.bind(document);document.addEventListener=(type,listener,options)=>{if(type==='pointerdown')pointerListeners.add(listener);return add(type,listener,options)};document.removeEventListener=(type,listener,options)=>{if(type==='pointerdown')pointerListeners.delete(listener);return remove(type,listener,options)};window.pointerListenerCount=()=>pointerListeners.size;configureViews({declaration:renderDeclarationStatistics,other:()=>{app.innerHTML='<h2 id=other>其他页面</h2>'}});window.show=async admin=>{state.me={permissions:['DECLARATION_STATS_VIEW',...(admin?['SYSTEM_ADMIN','DECLARATION_STATS_EXPORT']:[])]};state.tab='declaration';await render();};window.invalidate=()=>beginViewRequest();window.leave=()=>{state.tab='other';return render();};await show(true);window.ready=true;";
const server=http.createServer((req,res)=>{res.setHeader("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'");if(req.url==='/fixture.js'){res.setHeader('Content-Type','text/javascript');return res.end(startup);}if(req.url==='/'){res.setHeader('Content-Type','text/html;charset=utf-8');return res.end(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/style.css"><header id="globalHeader">全局页头</header><main id="app"></main><div id="tabs"></div><div id="toast"></div><script type="module" src="/fixture.js"></script>`);}const file=path.resolve(root,'.'+new URL(req.url,'http://x').pathname.replace('/static',''));if(!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.statusCode=404;return res.end();}res.setHeader('Content-Type',path.extname(file)==='.js'?'text/javascript':path.extname(file)==='.css'?'text/css':'application/octet-stream');res.end(fs.readFileSync(file));});
(async()=>{await new Promise(r=>server.listen(0,'127.0.0.1',r));try{for(const [engine,type,options] of [['edge',chromium,{channel:'msedge'}],['webkit',webkit,{}]]){const browser=await type.launch({...options,headless:true});try{for(const width of [1280,390]){const page=await browser.newPage({viewport:{width,height:900},hasTouch:width===390,isMobile:width===390}),errors=[],writes=[],reads=[];page.on('pageerror',e=>errors.push(e.message));let multiple=false,failWrite=false,failRead=false,empty=false,holdRead=false,releaseRead;let started;const readStarted=new Promise(resolve=>started=resolve);
const row={id:1,employee_id:1,employee_name:'员工LongName'.repeat(8),employee_no:'0183652',attraction_id:1,attraction_name:'热力追踪',occurred_on:'2026-11-01',type_id:1,type_name:'安全',level_id:1,level_code:'STATEMENT',level_name:'声明',description:'<img src=x onerror="window.injected=true">\n'+('很长的登记内容 https://example.test/'+('a'.repeat(80))+'\n').repeat(3),submitter_name:'登记人EnglishName'.repeat(8),submitted_at:'2026-11-01 12:00:00',material_status:'材料已就绪',upgrade_status:'无',business_status:'已生效',raw_status:'active',upgrade_request_id:0,upgrade_role:null,document_url:'/api/files/42'};
const data=()=>({month:'2026-11',total_records:1,employee_count:1,levels:[{id:1,code:'STATEMENT',name:'声明',count:1}],attractions:[{id:1,name:'热力追踪'}],types:[{id:1,name:'安全',active:true},{id:2,name:'礼仪',active:true}],statuses:{已生效:1},circles:[{attraction_id:1,attraction_name:'热力追踪',record_count:1,employee_count:1,levels:[{id:1,code:'STATEMENT',name:'声明',count:1}]}],records:multiple?[row,{...row,id:2,employee_id:2,employee_no:'0283652',employee_name:'员工二'}]:[row]});
await page.route('**/api/**',async route=>{const req=route.request();if(req.method()!=='GET'){writes.push({url:req.url(),body:req.postDataJSON()});return route.fulfill({status:failWrite?409:200,json:failWrite?{detail:'记录已变化，请刷新'}:{ok:true}});}reads.push(req.url());if(holdRead){started();await new Promise(resolve=>releaseRead=resolve);}await route.fulfill({status:failRead?503:200,json:failRead?{detail:'服务暂不可用'}:empty?{...data(),records:[],circles:[],total_records:0,employee_count:0}:data()});});
await page.goto('http://127.0.0.1:'+server.address().port);await page.waitForFunction(()=>window.ready);
const visible=()=>page.locator('.declaration-list-content');
const open=()=>page.locator('[data-declaration-circle]').click();
assert.equal(new URL(reads[0]).searchParams.get('status'),'current');
assert.equal(await page.locator('[name=status]').inputValue(),'current');
// Same name/number suggestion interaction as registration, with optional free-text query.
await page.locator('[name=keyword]').fill('员工');
await page.locator('[data-search-result]').waitFor();
assert.equal(new URL(reads.at(-1)).searchParams.get('keyword'),'员工');
assert.equal(new URL(reads.at(-1)).searchParams.get('status'),'current');
assert.equal(await page.locator('[data-search-result]').count(),1);
await page.locator('[name=keyword]').press('ArrowDown');
await page.locator('[data-search-result]').press('Enter');
assert.equal(await page.locator('[name=keyword]').inputValue(),'0183652');
assert.ok((await page.locator('#declarationEmployeeHint').innerText()).includes('已选择'));
// Multiple candidates: Up enters at the last row; both directions wrap.
multiple=true;
await page.locator('[name=keyword]').fill('员工');
await page.locator('[data-search-result="1"]').waitFor();
await page.locator('[name=keyword]').press('ArrowUp');
assert.equal(await page.evaluate(()=>document.activeElement.dataset.searchResult),'1');
await page.keyboard.press('ArrowDown');
assert.equal(await page.evaluate(()=>document.activeElement.dataset.searchResult),'0');
await page.keyboard.press('ArrowUp');
assert.equal(await page.evaluate(()=>document.activeElement.dataset.searchResult),'1');
await page.keyboard.press('Enter');
assert.equal(await page.locator('[name=keyword]').inputValue(),'0283652');
multiple=false;
assert.equal(await page.evaluate(()=>pointerListenerCount()),1);
// WebKit touch regression: blur must not remove a candidate before click/tap selects it.
for(const action of width===390?['click','tap']:['click']){
 await page.locator('[name=keyword]').fill('员工');
 await page.locator('[data-search-result]').waitFor();
 await page.locator('[data-search-result]')[action]();
 assert.equal(await page.locator('[name=keyword]').inputValue(),'0183652',engine+' '+action+' must select the candidate');
 assert.equal(await page.locator('#declarationEmployeeResults').isHidden(),true);
 assert.ok((await page.locator('#declarationEmployeeHint').innerText()).includes('已选择'));
}
await page.locator('#declarationFilter button[type=submit]').click();
await page.waitForSelector('[data-declaration-circle]');
const defaultQuery=new URL(reads.at(-1));
assert.equal(defaultQuery.searchParams.get('keyword'),'0183652');
assert.equal(defaultQuery.searchParams.get('status'),'current');
await page.locator('#declarationExport').click();
await page.locator('[data-modal-confirm]').click();
await page.waitForSelector('[role=dialog]',{state:'detached'});
const defaultExport=writes.filter(w=>w.url.includes('/export-tasks?')).at(-1);
assert.equal(new URL(new URL(defaultExport.url).searchParams.get('target'),'http://fixture').search,defaultQuery.search);
// Tapping outside still dismisses suggestions and preserves the free-text keyword.
await page.locator('[name=keyword]').fill('员工');
await page.locator('[data-search-result]').waitFor();
await page.locator('.statistics-page-heading h2')[width===390?'tap':'click']();
assert.equal(await page.locator('#declarationEmployeeResults').isHidden(),true);
assert.equal(await page.locator('[name=keyword]').inputValue(),'员工');
// Global header is outside the statistics subtree, including on touch WebKit.
await page.locator('[name=keyword]').fill('员工');
await page.locator('[data-search-result]').waitFor();
await page.locator('#globalHeader')[width===390?'tap':'click']();
assert.equal(await page.locator('#declarationEmployeeResults').isHidden(),true);
assert.equal(await page.locator('[name=keyword]').inputValue(),'员工');
// Leaving removes the document listener and pending timer; returning adds only one.
await page.locator('[name=keyword]').fill('员工');
const readsBeforeLeave=reads.length;
await page.evaluate(()=>leave());
assert.equal(await page.evaluate(()=>pointerListenerCount()),0);
await page.waitForTimeout(180);
assert.equal(reads.length,readsBeforeLeave);
for(let repeat=0;repeat<2;repeat++){
 await page.evaluate(()=>show(true));
 assert.equal(await page.evaluate(()=>pointerListenerCount()),1);
 await page.evaluate(()=>leave());
 assert.equal(await page.evaluate(()=>pointerListenerCount()),0);
}
await page.evaluate(()=>show(true));
assert.equal(await page.evaluate(()=>pointerListenerCount()),1);
await page.locator('[name=keyword]').fill('1836');
await page.locator('[data-search-result]').waitFor();
assert.equal(new URL(reads.at(-1)).searchParams.get('keyword'),'1836');
await page.locator('[name=keyword]').press('Escape');
assert.equal(await page.locator('#declarationEmployeeResults').isHidden(),true);
await page.locator('#declarationFilter button[type=submit]').click();
await page.waitForSelector('[data-declaration-circle]');
assert.equal(new URL(reads.at(-1)).searchParams.get('keyword'),'1836');
assert.equal(new URL(reads.at(-1)).searchParams.get('status'),'current');
assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
// Search and export retain every filter, including a seven-digit employee number with its leading zero.
await page.locator('[name=month]').fill('2026-10');await page.locator('[name=attraction_id]').selectOption('1');await page.locator('[name=type_id]').selectOption('1');await page.locator('[name=level_id]').selectOption('1');await page.locator('[name=status]').selectOption('已生效');await page.locator('[name=keyword]').fill('0183652');await page.locator('[name=keyword]').press('Escape');await page.locator('#declarationFilter button[type=submit]').click();await page.waitForSelector('[data-declaration-circle]');const query=new URL(reads.at(-1));assert.equal(query.searchParams.get('keyword'),'0183652');assert.equal(query.searchParams.get('month'),'2026-10');assert.equal(query.searchParams.get('attraction_id'),'1');assert.equal(query.searchParams.get('type_id'),'1');assert.equal(query.searchParams.get('level_id'),'1');assert.equal(query.searchParams.get('status'),'已生效');
// Successful exports snapshot the same filters displayed at confirmation time.
await page.locator('#declarationExport').click();await page.evaluate(()=>document.querySelector('[name=keyword]').value='changed while confirming');await page.locator('[data-modal-confirm]').click();await page.waitForSelector('[role=dialog]',{state:'detached'});const exportRequest=writes.findLast(w=>w.url.includes('/export-tasks?'));assert.ok(exportRequest);const target=new URL(new URL(exportRequest.url).searchParams.get('target'),'http://fixture');assert.equal(target.pathname,'/api/declaration-statistics/export');assert.equal(target.search,query.search);await page.locator('[name=keyword]').fill('0183652');
// Repeated open/close must not accumulate actions or interpret literal user content as HTML.
for(let i=0;i<3;i++){await open();await open();}await open();await page.locator('[data-declaration-list-row] > summary').click();await visible().locator('.declaration-record').waitFor();assert.equal(await visible().locator('[data-correct-type]').count(),1);assert.equal(await visible().locator('.declaration-description').innerText(),row.description);assert.equal(await visible().locator('.declaration-description img').count(),0);
if(width===1280){assert.ok(await visible().locator('.declaration-card-identity').evaluate(p=>p.querySelector('strong').getBoundingClientRect().right<=p.getBoundingClientRect().right+1),'long employee name overflows its card');}
assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
// A rejected edit keeps its modal and original selected value, and does not refresh or alter another row.
failWrite=true;const readCount=reads.length;await visible().locator('[data-correct-type]').selectOption('2');await page.locator('[role=dialog] textarea').fill('复核');await page.locator('[role=dialog] button[type=submit]').click();await page.waitForFunction(()=>document.getElementById('toast').textContent.includes('记录已变化'));assert.equal(await page.locator('[role=dialog]').count(),1);assert.equal(await visible().locator('[data-correct-type]').inputValue(),'1');assert.equal(reads.length,readCount);await page.locator('[data-modal-cancel]').click();await page.waitForSelector('[role=dialog]',{state:'detached'});failWrite=false;
// A stale edit dialog must not submit from a view that has already been replaced.
await visible().locator('[data-correct-type]').selectOption('2');await page.evaluate(()=>leave());const beforeWrites=writes.length;await page.locator('[role=dialog] textarea').fill('旧页面提交');await page.locator('[role=dialog] button[type=submit]').click();await page.waitForTimeout(100);assert.equal(writes.length,beforeWrites,'old dialog submitted after navigation');assert.equal(await page.locator('#other').count(),1);await page.locator('[data-modal-cancel]').click();await page.waitForSelector('[role=dialog]',{state:'detached'});
// Export confirmation must not use the replacement page's button or queue work after navigation.
await page.evaluate(()=>show(true));await page.locator('#declarationExport').click();await page.evaluate(()=>leave());await page.locator('[data-modal-confirm]').click();await page.waitForSelector('[role=dialog]',{state:'detached'});assert.equal(writes.length,beforeWrites);assert.deepEqual(errors,[]);
// Query failures remain recoverable, and empty results retain usable filters.
await page.evaluate(()=>show(true));failRead=true;await page.locator('#declarationFilter button[type=submit]').click();await page.waitForSelector('#declarationResults .error');assert.equal(await page.locator('#declarationFilter button[type=submit]').isEnabled(),true);failRead=false;await page.locator('#declarationFilter button[type=submit]').click();await page.waitForSelector('[data-declaration-circle]');empty=true;await page.locator('#declarationFilter button[type=submit]').click();await page.waitForSelector('#declarationResults .empty');assert.ok((await page.locator('#declarationResults .empty').innerText()).includes('暂无声明登记记录'));assert.equal(await page.locator('#declarationFilter button[type=submit]').isEnabled(),true);assert.deepEqual(errors,[]);
// A response arriving after a newer view starts must not repaint the old form, even before its DOM is removed.
empty=false;await page.evaluate(()=>show(true));holdRead=true;await page.locator('#declarationFilter button[type=submit]').click();await readStarted;await page.evaluate(()=>invalidate());const lateResponse=page.waitForResponse(r=>r.url().includes('/api/declaration-statistics'));releaseRead();await lateResponse;await page.waitForFunction(()=>!document.querySelector('#declarationFilter button[type=submit]').disabled);assert.ok((await page.locator('#declarationResults').innerText()).includes('正在加载统计数据'));assert.equal(await page.locator('[data-declaration-circle]').count(),0);assert.deepEqual(errors,[]);
await page.close();}console.log(engine+': filters, long content, repeated expansion, failed edits and navigation guards passed');}finally{await browser.close();}}}finally{server.closeAllConnections();await new Promise(r=>server.close(r));}})().catch(e=>{console.error(e);process.exitCode=1;});
