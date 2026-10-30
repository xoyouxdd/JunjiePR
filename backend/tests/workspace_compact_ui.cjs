// Real production modules/CSP with synthetic APIs; never opens a business database.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {createHash}=require('node:crypto'),{chromium,webkit,firefox}=require('playwright');
const root=path.resolve(__dirname,'../app/static'),version='compact-ui-fixture';
const shell=fs.readFileSync(path.join(root,'index.html'),'utf8').replaceAll('__STATIC_CACHE_VERSION__',version).replace(/<script type="module"[^>]*><\/script>/,'');
const policySource=fs.readFileSync(path.join(root,'../security.py'),'utf8');
const basePolicy=[.../CONTENT_SECURITY_POLICY\s*=\s*"; "\.join\(\s*\(([\s\S]*?)\)\s*\)/.exec(policySource)[1].matchAll(/"([^"\r\n]+)"/g)].map(m=>m[1]).join('; ');
const map=/<script type="importmap">([\s\S]*?)<\/script>/.exec(shell)[1];
const policy=basePolicy.replace("script-src 'self'",`script-src 'self' 'sha256-${createHash('sha256').update(map.replace(/\r\n?/g,'\n')).digest('base64')}'`);
const sourceRows=Array.from({length:6},(_,i)=>({id:i+1,record_type:'recognition',employee_name:`合成员工${i+1}`,employee_no:`DEMO${i+1}`,recognition_date:'2026-10-25',submitted_at:'2026-10-26 12:00:00',recognition_type:i%2?'演出':'安全',recognizer_name:'合成主管',fraction:1,credited_fraction:i===1?0:1,monthly_cap_reason:i===1?'本月已达认可上限':'',content:i===0?'合成内容 <img src=x onerror=alert(1)>':'完整认可内容，仅用于隔离测试',status:i%2?'confirmed':'pending',status_name:i%2?'已确认':'待复核',available_actions:i===0?['withdraw']:[],review_note:i===1?'合成复核说明':''}));
let entries=sourceRows.map(r=>({...r})),writes=[],calls=[],reviewCount=0;
const months=['2026-08','2026-09','2026-10'];
const trend={months,end_month:months[2],overall:months.map((month,i)=>({month,employee_count:[0,3,6][i],recognition_score:[0,6,12][i],attendance_score:[0,12,12][i],deduction_score:[0,3,6][i],total_score:[0,15,18][i]})),by_attraction:[
 {attraction_id:1,attraction_name:'热力追踪',series:months.map((month,i)=>({month,employee_count:[0,1,2][i],total_score:[0,12,12][i]}))},
 {attraction_id:2,attraction_name:'矮人迷宫',series:months.map((month,i)=>({month,employee_count:[0,2,4][i],total_score:[0,3,6][i]}))}
]};
const json=(res,data)=>{res.writeHead(200,{'Content-Type':'application/json; charset=utf-8'});res.end(JSON.stringify(data));};
const server=http.createServer((req,res)=>{
 const url=new URL(req.url,'http://fixture'),route=url.pathname.replace(/^\/recognition(?=\/|$)/,'')||'/';calls.push({url:req.url,method:req.method});
 if(route==='/'){res.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Content-Security-Policy':policy});return res.end(shell);}
 if(route.startsWith('/static/')){const file=path.resolve(root,'.'+route.slice(7));if(!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.writeHead(404);return res.end();}res.writeHead(200,{'Content-Type':({'.js':'text/javascript','.css':'text/css','.png':'image/png','.json':'application/json'})[path.extname(file)]||'application/octet-stream'});return res.end(fs.readFileSync(file));}
 if(route==='/favicon.ico'){res.writeHead(204);return res.end();}
 if(route==='/api/my-entries'){const page=Number(url.searchParams.get('page')||1);return json(res,{items:page===1?entries:[],total:106,page,page_size:100});}
 if(route==='/api/deductions/pending-materials')return json(res,{items:[],total:0,page:1,page_size:50});
 if(route==='/api/statistics/trend')return json(res,trend);
 if(route==='/api/statistics')return json(res,{month:url.searchParams.get('month'),hierarchy:[],summary:{employee_count:0}});
 if(route==='/api/recognitions/1'&&req.method==='DELETE'){writes.push(route);entries=entries.filter(r=>r.id!==1);return json(res,{ok:true});}
 if(route==='/api/action-center')return json(res,{month:'2026-10',total:7,items:[{type:'recognition_review',title:'待复核签卡',description:'沿用原直属复核队列',tab:'review',severity:'warning',count:3},{type:'announcement_receipt',title:'待签收公告',description:'沿用公告签收入口',tab:'announcements',severity:'info',count:2},{type:'deduction_material',title:'待补材料',description:'材料协作',tab:'entries',severity:'info',count:2}]});
 if(route==='/api/deduction-upgrades/pending')return json(res,{items:[]});
 if(route==='/api/rotation/adjust/mine'||route==='/api/export-tasks')return json(res,{items:[]});
 if(route==='/api/reviews')return json(res,{items:reviewCount?[]:[{...sourceRows[0],employee_name:'最早待复核员工'}],total:reviewCount?0:1,limit:Number(url.searchParams.get('limit')),offset:0,has_more:false});
 if(route==='/api/reviews/1'&&req.method==='POST'){reviewCount++;return json(res,{record:{...sourceRows[0],status:'confirmed'}});}
 res.writeHead(404);res.end('Unmocked fixture request: '+route);
});
const out=path.resolve(__dirname,'../../output/workspace-ui-20261027');fs.mkdirSync(out,{recursive:true});
async function main(){await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser;try{
 const engine=process.env.FRONTEND_TEST_ENGINE||'edge';browser=await ({edge:chromium,webkit,firefox}[engine]).launch({headless:true,...(engine==='edge'?{channel:'msedge'}:{})});
 const origin=`http://127.0.0.1:${server.address().port}`,page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{window.cspViolations=[];document.addEventListener('securitypolicyviolation',e=>window.cspViolations.push(e.effectiveDirective));});
 const open=async(p=page,prefix='/recognition/')=>{await p.goto(origin+prefix);await p.evaluate(async()=>{
  const [{state,app},{configureViews},{renderEntries},{renderStatistics},{renderActionCenter},{renderFrontlineReview}]=await Promise.all([import('./static/js/app/state.js'),import('./static/js/app/context.js'),import('./static/js/app/entries.js'),import('./static/js/app/statistics.js'),import('./static/js/app/operations.js'),import('./static/js/app/reviews.js')]);
  state.me={id:1,employee_no:'DEMO',name:'合成测试员',role_code:'GSM',base_role_code:'GSM',role_name:'GSM',permissions:['DATA_VIEW'],attraction_id:1};state.options={attractions:[{id:1,name:'热力追踪'}],employee_circles:[{id:1,name:'热力追踪'}]};app.dataset.workspaceUi='true';
  configureViews({entries:renderEntries,statistics:renderStatistics,actionCenter:renderActionCenter,review:renderFrontlineReview});
 });};
 const navigate=async(name,p=page)=>p.evaluate(async name=>{const {state}=await import('./static/js/app/state.js');state.tab=name;await (await import('./static/js/app/context.js')).render();},name);
 await open();assert.equal(calls.some(c=>c.url.includes('/vendor/')),false,'No eager vendor downloads');
 await navigate('entries');await page.locator('.has-recognition-grid').waitFor();assert.equal(await page.locator('.tabulator-row').count(),6);assert.equal(await page.locator('#entryResults .mobile-only.work-card-list').last().isVisible(),false,'Desktop must not duplicate the mobile cards');
 assert.equal(calls.filter(c=>c.url.includes('/api/my-entries')).length,1,'One page query, no full-history fetch');
 const reads=calls.filter(c=>c.url.includes('/api/')).length;
 await page.locator('[data-grid-pending]').click();assert.equal(await page.locator('.tabulator-row').count(),3);
 await page.locator('[data-grid-search]').fill('DEMO3');assert.equal(await page.locator('.tabulator-row').count(),1);
 await page.locator('[data-grid-reset]').click();assert.equal(await page.locator('.tabulator-row').count(),6);
 await page.locator('.tabulator-col[tabulator-field="employee_name"]').click();await page.locator('.tabulator-col[tabulator-field="employee_name"]').click();
 assert.equal(await page.evaluate(()=>window.Tabulator.findTable(document.querySelector('[data-recognition-grid]'))[0].getData('active')[0].id),6);
 await page.locator('[data-grid-reset]').click();assert.deepEqual(await page.evaluate(()=>window.Tabulator.findTable(document.querySelector('[data-recognition-grid]'))[0].getData('active').map(r=>r.id)),[1,2,3,4,5,6],'Reset restores the original server order');
 assert.equal(calls.filter(c=>c.url.includes('/api/')).length,reads,'Local search/sort does not add API calls');
 await page.locator('[data-grid-detail="1"]').click();await page.locator('.recognition-inline-detail').waitFor();
 assert.match(await page.locator('.recognition-inline-detail').innerText(),/<img src=x/);assert.equal(await page.locator('.recognition-inline-detail img').count(),0,'Untrusted text is escaped');
 assert.equal(await page.locator('.tabulator-row').count(),6,'Expanding a detail retains all rows');
 if(engine==='edge')await page.evaluate(()=>{document.querySelectorAll('*').forEach(el=>{if(el.scrollTop)el.scrollTop=0;});window.scrollTo(0,0);});if(engine==='edge')await page.screenshot({path:path.join(out,'recognition-desktop.png'),fullPage:true});
 await page.locator('.recognition-grid [data-entry-withdraw="1"]').click();await page.locator('[data-modal-cancel]').click();await page.locator('[data-modal-confirm]').waitFor({state:'detached'});assert.equal(writes.length,0);
 await page.locator('.recognition-grid [data-entry-withdraw="1"]').click();await page.locator('[data-modal-confirm]').click();await page.waitForFunction(()=>document.querySelectorAll('.tabulator-row').length===5);assert.equal(writes.length,1,'Original withdrawal confirmation and DELETE retained');
 const afterWriteReads=calls.filter(c=>c.url.includes('/api/my-entries')).length;
 await page.setViewportSize({width:390,height:844});await page.locator('.recognition-grid').waitFor({state:'detached'});assert.equal(calls.filter(c=>c.url.includes('/api/my-entries')).length,afterWriteReads,'Viewport changes do not requery');
 await page.locator('.recognition-card-detail summary').first().click();assert.match(await page.locator('.recognition-card-detail[open]').innerText(),/合成主管/);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 if(engine==='edge')await page.evaluate(()=>{document.querySelectorAll('*').forEach(el=>{if(el.scrollTop)el.scrollTop=0;});window.scrollTo(0,0);});if(engine==='edge')await page.screenshot({path:path.join(out,'recognition-mobile.png'),fullPage:true});
 await page.setViewportSize({width:1280,height:900});await page.locator('.has-recognition-grid').waitFor();
 await page.setViewportSize({width:800,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Tablet table overflow stays inside the grid');await page.setViewportSize({width:1280,height:900});
 await page.locator('#entryNext').click();await page.waitForFunction(()=>document.querySelector('#entryPrev')?.parentElement.textContent.includes('第2页'));assert(calls.some(c=>/my-entries.*page=2/.test(c.url)));
 await navigate('statistics');assert.equal(calls.some(c=>c.url.includes('echarts')),false,'Default monthly statistics does not load charts');
 await page.locator('[data-stats-view="trend"]').click();await page.locator('.trend-interactive-chart canvas').waitFor();assert.equal(await page.locator('.trend-chart-fallback').isVisible(),false);
 const chartState=await page.evaluate(()=>{const c=window.echarts.getInstanceByDom(document.querySelector('.trend-interactive-chart'));c.dispatchAction({type:'showTip',seriesIndex:0,dataIndex:1});c.dispatchAction({type:'legendToggleSelect',name:'热力追踪'});return {tooltip:c.getModel().getComponent('tooltip')!==undefined,legend:c.getModel().getComponent('legend')!==undefined,hidden:c.getOption().legend[0].selected['热力追踪']===false,series:c.getOption().series.map(s=>s.data)};});assert(chartState.tooltip);assert(chartState.legend);assert(chartState.hidden,'Legend controls the actual circle series');assert.deepEqual(chartState.series,[[0,15,18],[0,12,12],[0,3,6]]);
 const beforeSwitchCalls=calls.filter(c=>c.url.includes('/api/statistics/trend')).length;
 await page.evaluate(()=>document.getElementById('toast')?.classList.remove('show'));
 await page.locator('[data-trend-mode="average"]').click();await page.locator('.trend-interactive-chart canvas').waitFor();
 const averageChart=await page.evaluate(()=>window.echarts.getInstanceByDom(document.querySelector('.trend-interactive-chart')).getOption());
 assert.deepEqual(averageChart.series.map(s=>s.data),[[null,5,3],[null,12,6],[null,1.5,1.5]],'Overall mean uses total / people, not an unweighted mean of circles');
 assert.equal(averageChart.yAxis[0].name,'分/人');
 assert.deepEqual(await page.locator('.trend-card strong').allTextContents(),['3.00','2.00','2.00','1.00']);
 assert.match(await page.locator('.trend-card').first().innerText(),/↓2.00.*变差/);
 assert.deepEqual((await page.locator('.compact-trend-panel tbody tr').first().locator('td').allTextContents()).slice(2,7),['—','—','—','—','—']);
 assert.match(await page.locator('.compact-trend-panel thead').innerText(),/人均总分/);
 assert.equal(await page.locator('[data-trend-mode="average"]').getAttribute('aria-pressed'),'true');
 for(const width of [320,390,800,1280]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`Average statistics fits ${width}px`);}
 if(engine==='edge'){await page.locator('.compact-trend-panel').screenshot({path:path.join(out,'statistics-average-desktop.png')});await page.setViewportSize({width:390,height:844});await page.locator('.compact-trend-panel').screenshot({path:path.join(out,'statistics-average-mobile.png')});await page.setViewportSize({width:1280,height:900});}
 await page.locator('[data-trend-mode="total"]').click();await page.locator('.trend-interactive-chart canvas').waitFor();
 assert.equal(await page.locator('.trend-card strong').first().innerText(),'18.00');
 assert.equal(calls.filter(c=>c.url.includes('/api/statistics/trend')).length,beforeSwitchCalls,'Switching measures adds no API request');
 assert.equal(await page.locator('[data-trend-month]').count(),3);if(engine==='edge')await page.evaluate(()=>{document.querySelectorAll('*').forEach(el=>{if(el.scrollTop)el.scrollTop=0;});window.scrollTo(0,0);});if(engine==='edge')await page.screenshot({path:path.join(out,'statistics-desktop.png'),fullPage:true});
 const trendCalls=calls.filter(c=>c.url.includes('/api/statistics/trend')).length;await page.locator('[data-trend-month="2026-08"]').click();await page.locator('.statistics-hierarchy-table').waitFor();assert.equal(await page.locator('#statsFilter [name="month"]').inputValue(),'2026-08');assert.equal(calls.filter(c=>c.url.includes('/api/statistics/trend')).length,trendCalls);
 await page.locator('[data-stats-view="trend"]').click();await page.locator('.trend-interactive-chart canvas').waitFor();
 for(const width of [320,390,800,1280]){await page.setViewportSize({width,height:900});await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`Statistics fits ${width}px`);}
 await page.evaluate(async()=>{const {state}=await import('./static/js/app/state.js');state.me.role_code='SUPERVISOR';state.me.base_role_code='SUPERVISOR';});
 await navigate('actionCenter');assert.equal(await page.locator('[data-action-tab]').count(),4,'Keep every original category and both review actions');
 const wish=page.locator('[data-daily-wish]');assert.equal(await wish.locator('details').getAttribute('open'),null);const beforeWishCalls=calls.filter(c=>c.url.includes('/api/')).length;
 await wish.locator('summary').click();await wish.locator('canvas').waitFor();const scratchBox=await wish.locator('canvas').boundingBox();const alphaBefore=await wish.locator('canvas').evaluate(c=>c.getContext('2d').getImageData(10,10,1,1).data[3]);await page.mouse.move(scratchBox.x+10,scratchBox.y+10);await page.mouse.down();await page.mouse.move(scratchBox.x+50,scratchBox.y+10);await page.mouse.up();const alphaAfter=await wish.locator('canvas').evaluate(c=>c.getContext('2d').getImageData(10,10,1,1).data[3]);assert(alphaAfter<alphaBefore,'Dragging removes the scratch cover');await wish.locator('[data-wish-reveal]').click();const firstWish=await wish.locator('[data-wish-text]').innerText();assert(firstWish.length>10);await wish.locator('[data-wish-next]').click();assert.notEqual(await wish.locator('[data-wish-text]').innerText(),firstWish);const nextWish=await wish.locator('[data-wish-text]').innerText();assert.equal(calls.filter(c=>c.url.includes('/api/')).length,beforeWishCalls,'Wish interaction adds no API calls');
 await navigate('actionCenter');await wish.locator('summary').click();await page.waitForFunction(()=>Boolean(document.querySelector('[data-wish-text]')?.textContent));assert.equal(await wish.locator('[data-wish-text]').innerText(),nextWish,'Same day persists on this device');
 await page.evaluate(()=>{const d=new Date();localStorage.setItem('fz-daily-wish-v1-1',JSON.stringify({day:`${d.getFullYear()}-${d.getMonth()+1}-${d.getDate()}`,id:2000,history:[2000]}));});await navigate('actionCenter');await wish.locator('summary').click();await page.waitForFunction(()=>Boolean(document.querySelector('[data-wish-text]')?.textContent));assert.equal(await wish.locator('[data-wish-text]').innerText(),'愿你今天遇到的人，都能给彼此多一点耐心。','Independently written wishes render and persist');
 for(const width of [320,390,800,1280]){await page.setViewportSize({width,height:900});await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`Workbench fits ${width}px`);}
 if(engine==='edge')await page.evaluate(()=>{document.querySelectorAll('*').forEach(el=>{if(el.scrollTop)el.scrollTop=0;});window.scrollTo(0,0);});if(engine==='edge')await page.screenshot({path:path.join(out,'today-desktop.png'),fullPage:true});
 await page.locator('[data-action-type="recognition_review"]:not([data-action-details])').click();await page.locator('.review-focused').waitFor();assert.match(await page.locator('.review-focused').innerText(),/最早待复核员工/);assert(calls.some(c=>/reviews\?.*limit=1/.test(c.url)));
 await page.locator('[data-action="reject"]').click();await page.locator('[data-modal-confirm]').click();assert.equal(reviewCount,0,'Reject still requires a reason');await page.locator('[data-modal-cancel]').click();
 await page.locator('[data-action="confirm"]').click();await page.waitForFunction(()=>document.querySelector('.review-page')?.textContent.includes('暂无记录'));assert.equal(reviewCount,1);
 assert.deepEqual(errors,[]);assert.deepEqual(await page.evaluate(()=>window.cspViolations),[],'Do not weaken production CSP');
 const blocked=await browser.newPage({viewport:{width:1280,height:900}});await blocked.route('**/static/vendor/**',r=>r.abort());await open(blocked);await navigate('entries',blocked);await blocked.locator('[data-entry-native]').first().waitFor({state:'visible'});await navigate('statistics',blocked);await blocked.locator('[data-stats-view="trend"]').click();await blocked.locator('.trend-chart-fallback').waitFor({state:'visible'});assert.equal(await blocked.locator('[data-trend-month]').count(),3,'Numeric table survives missing optional assets');
 await blocked.locator('[data-trend-mode="average"]').click();
 assert.equal(await blocked.locator('.trend-card strong').first().innerText(),'3.00');
 assert.equal(await blocked.locator('.trend-chart circle').count(),6,'SVG omits no-person points');
 assert.equal(await blocked.locator('.trend-chart path').first().getAttribute('d').then(d=>d.trim().startsWith('M')),true,'SVG starts after missing data');
 await blocked.close();
 assert(calls.filter(c=>c.url.includes('/vendor/')).every(c=>c.url.startsWith('/recognition/static/')&&c.url.includes('?v='+version)),'Local versioned assets preserve deployment prefix');
 console.log(JSON.stringify({status:'passed',engine,checks:['lazy local assets and prefix','current-page filtering/sorting without API calls','escaped details and all rows retained','original withdrawal and pagination','mobile resize without requery','exact chart series and tooltip','weighted person averages and zero-person gaps','measure switch without requests','monthly drill-through','320/390/800/1280 layouts','original review order and reject/confirm','strict CSP','missing-library fallback']},null,2));
}finally{await browser?.close();server.closeAllConnections();await new Promise(r=>server.close(r));}}
main().catch(e=>{console.error(e);process.exitCode=1;});


