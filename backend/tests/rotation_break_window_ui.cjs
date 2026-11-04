// Real shared clock/pool predicates and renderer; no production data.
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const {chromium,webkit}=require('playwright');const root=path.resolve(__dirname,'../app/static');
const common=fs.readFileSync(path.join(root,'js/rotation-common.js'),'utf8'),source=fs.readFileSync(path.join(root,'js/rotation-workflow.js'),'utf8');const workflow=source.slice(0,source.indexOf('const workflowTimeoutSeen='));
(async()=>{for(const [engine,type,opts] of [['edge',chromium,{channel:'msedge'}],['webkit',webkit,{}]]){const browser=await type.launch({...opts,headless:true});try{for(const width of [1920,390]){const page=await browser.newPage({viewport:{width,height:1000}});await page.setContent('<body class="rotation-screen"><main id="rtApp" class="rt-pool"></main></body>');await page.addStyleTag({content:fs.readFileSync(path.join(root,'css/rotation.css'),'utf8')});await page.addScriptTag({content:common});await page.addScriptTag({content:workflow});
for(const kind of ['rest','meal','meal_rest'])for(const ops of [true,false]){
const state=kind==='meal'?'meal':'rest';
await page.evaluate(({state,kind,ops})=>{window.person={pid:'break-test',name:'餐休测试',role:'rotation',state,start:420,end:1080,readyAt:585,breakStart:540,breakKind:kind,flags:[],tags:[],assign:{line:'A1',entryPost:'塔台',mode:'push',departAt:620},task:null};RT.data={day:{date:'2026-11-03',taskWorkflow:true,lines:[],persons:[person]},settings:{departEarly:1}};RT.lastSyncedAt=Date.now();RT.sync='live';window.draw=n=>document.getElementById('rtApp').innerHTML=workflowPools(n,ops);draw(540);},{state,kind,ops});
const original=state==='meal'?'.rt-meals':'.rt-resting';assert.equal(await page.locator(original+' .rt-chip').count(),1);assert.equal(await page.locator(original+' .rt-go').count(),0);assert.equal(await page.locator(original+' .rt-task-summary').count(),0);assert.match(await page.locator(original+' .rt-chip').innerText(),/剩 45 分钟/);assert.equal(await page.locator('.rt-ready .rt-chip').count(),0);
// The clock may reach the break end before the server updates the person's state.
for(const ready of [false,true]){
 await page.evaluate(ready=>{if(ready)person.state='ready';draw(585);},ready);
 assert.equal(await page.locator(original+' .rt-chip').count(),0);
 const waiting=page.locator('.rt-ready .rt-chip');
 assert.equal(await waiting.count(),1);
 assert.match(await waiting.innerText(),/等待派发/);
 assert.equal(await waiting.locator('.rt-go,[data-wf-action]').count(),0);
 assert.doesNotMatch(await waiting.innerText(),/塔台|可出发/);
}
await page.evaluate(()=>{person.task={status:'assigned',allowedAt:620,deadlineAt:623,routes:[]};person.canDepart=false;draw(617);});
assert.match(await page.locator('.rt-ready .rt-chip').innerText(),/塔台.*10:20/s);
assert.doesNotMatch(await page.locator('.rt-ready .rt-chip').innerText(),/餐休剩 0 分钟/);
assert.equal(await page.locator('.rt-ready [data-wf-action=depart]').getAttribute('aria-disabled'),'true');
await page.evaluate(()=>{person.canDepart=true;draw(620);});
assert.equal(await page.locator('.rt-ready [data-wf-action=depart]').getAttribute('aria-disabled'),null);
await page.evaluate(state=>{person.state=state;},state);
await page.evaluate(()=>{person.assign.departAt=585;person.task={status:'assigned',allowedAt:585,deadlineAt:588,routes:[]};person.canDepart=false;draw(582);});assert.equal(await page.locator(original+' .rt-chip').count(),0);assert.equal(await page.locator('.rt-ready .rt-chip').count(),1);assert.match(await page.locator('.rt-ready .rt-chip').innerText(),/餐休剩 3 分钟/);assert.equal(await page.locator('.rt-ready [data-wf-action=depart]').getAttribute('aria-disabled'),'true');
for(const [now,remaining] of [[583,2],[584,1]]){await page.evaluate(n=>draw(n),now);assert.ok((await page.locator('.rt-ready .rt-chip').innerText()).includes('餐休剩 '+remaining+' 分钟'));assert.equal(await page.locator('.rt-ready [data-wf-action=depart]').getAttribute('aria-disabled'),'true');}
await page.evaluate(()=>{person.state='ready';person.canDepart=true;draw(585);});assert.equal(await page.locator('.rt-ready [data-wf-action=depart]').getAttribute('aria-disabled'),null);assert.equal(await page.locator('.rt-ready .rt-go').count(),1);
}
await page.close();}console.log(engine+': meal/rest forecasts hidden; physical break countdown; published task moves to ready at 3/2/1 minutes; departure blocked until end; manager/screen and desktop/mobile passed');}finally{await browser.close();}}})().catch(e=>{console.error(e);process.exitCode=1;});
