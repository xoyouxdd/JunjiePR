// Actual component + shipped artwork, synthetic employee data, no backend server.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium,webkit}=require('playwright');
const root=path.resolve(__dirname,'../app/static'),output=path.resolve(__dirname,'../../output/attendance-medals-integration');
fs.mkdirSync(output,{recursive:true});
let requests=[],broken=false;
const items=Array.from({length:12},(_,i)=>({month:`2027-${String(i+1).padStart(2,'0')}`,
 issued_at:`${i===11?'2028-01':`2027-${String(i+2).padStart(2,'0')}`}-09 08:00:00`}));
const shell=`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/style.css"><link rel="stylesheet" href="/static/css/attendance-medals.css"><div id="tabs"></div><main id="app"><section class="panel"><div id="medals"></div></section></main><div id="toast"></div><script type="module">
import {state} from '/static/js/app/state.js';import {mountMedalCollection} from '/static/js/app/attendance-medals.js';
state.me={id:10,name:'合成员工',role_code:'CM',permissions:[]};state.tab='home';
window.showMedals=month=>mountMedalCollection(document.getElementById('medals'),10,{month});
await showMedals(new URLSearchParams(location.search).get('month'));window.ready=true;</script>`;
const server=http.createServer((req,res)=>{
 const url=new URL(req.url,'http://localhost');let route=url.pathname.replace(/^\/recognition(?=\/|$)/,'')||'/';
 requests.push(route);
 if(route==='/'){res.setHeader('Content-Type','text/html; charset=utf-8');return res.end(shell);}
 if(route.startsWith('/static/')){
  if(broken&&route.endsWith('-thumb.webp')){res.statusCode=404;return res.end();}
  const file=path.resolve(root,'.'+route.slice(7));if(!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.statusCode=404;return res.end();}
  res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.webp')?'image/webp':'image/svg+xml');
  if(file.endsWith('.webp'))res.setHeader('Cache-Control','public, max-age=31536000, immutable');
  return res.end(fs.readFileSync(file));
 }
 res.setHeader('Content-Type','application/json');
 if(route==='/api/attendance-medals/employees/10')return res.end(JSON.stringify({visible:true,count:12,items,
  rule:'依据当月有效请假数据评定；正常休息、调休不影响，不绑定加分。',notices:[],assessments:[]}));
 res.statusCode=404;res.end('{}');
});
const imageRequests=()=>requests.filter(r=>r.includes('/images/attendance-medals/'));
async function clock(page,instant){
 await page.addInitScript(instant=>{const NativeDate=Date;window.Date=class extends NativeDate{constructor(...args){super(...(args.length?args:[instant]));}static now(){return new NativeDate(instant).valueOf();}};},instant);
}
(async()=>{let browser;try{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin='http://127.0.0.1:'+server.address().port;
 for(const [engine,type,launch] of [['edge',chromium,{channel:'msedge'}],['webkit',webkit,{}]]){
  browser=await type.launch({...launch,headless:true});
  for(const width of [390,1280]){
   requests=[];broken=false;
   const page=await browser.newPage({viewport:{width,height:950}}),errors=[],external=[];
   page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(!r.url().startsWith(origin))external.push(r.url());});
   await clock(page,'2028-02-03T12:00:00+08:00');await page.goto(origin);await page.waitForFunction(()=>window.ready);
   assert.equal(await page.locator('.medal-item').count(),12);
   assert.equal(imageRequests().filter(r=>r.endsWith('-detail.webp')).length,0,'no full image before expansion');
   for(const m of ['01','02','03','04','05','06','07','08','09','10','11','12']){
    const card=page.locator(`.medal-item[data-medal-art-month="${m}"]`);
    await card.scrollIntoViewIfNeeded();
    await page.waitForFunction(m=>{const img=document.querySelector(`.medal-item[data-medal-art-month="${m}"]>summary img`);return img?.complete&&img.naturalWidth===240;},m);
    assert((await card.locator('summary img').getAttribute('src')).endsWith(`/${m}-thumb.webp`));
   }
   assert(imageRequests().length<=12,'at most twelve shared thumbnails for the entire collection');
   const apiBefore=requests.filter(r=>r.startsWith('/api/')).length;
   const january=page.locator('.medal-item[data-medal-art-month="01"]');await january.locator('>summary').click();
   await page.waitForFunction(()=>document.querySelector('.medal-item[data-medal-art-month="01"]>summary img')?.naturalWidth===640);
   assert.equal(imageRequests().filter(r=>r.endsWith('-detail.webp')).length,1);
   assert.match(await january.locator('.medal-detail-card').textContent(),/累计 12 次/);
   assert.match(await january.locator('.medal-detail-card').textContent(),/2027-02-09/);
   assert.match(await january.textContent(),/每一份坚持，都值得被看见/);
   assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1));
   const afterFirstOpen=imageRequests().length;
   await january.locator('>summary').click();await january.waitFor({state:'visible'});
   await page.waitForFunction(()=>!document.querySelector('.medal-item[data-medal-art-month="01"]').open);
   await january.locator('>summary').click();
   await page.waitForFunction(()=>document.querySelector('.medal-item[data-medal-art-month="01"]>summary img')?.naturalWidth===640);
   assert.equal(imageRequests().length,afterFirstOpen,'repeat expansion uses the browser cache');
   const december=page.locator('.medal-item[data-medal-art-month="12"]');await december.locator('>summary').click();
   await page.waitForFunction(()=>document.querySelector('.medal-item[data-medal-art-month="12"]>summary img')?.naturalWidth===640);
   assert.equal(await page.locator('.medal-item[open]').count(),1);
   assert.match(await december.locator('.medal-detail-card').textContent(),/2028-01-09/);
   assert.equal(requests.filter(r=>r.startsWith('/api/')).length,apiBefore,'opening artwork never adds an API call');
   await december.locator('>summary').click();
   await page.screenshot({path:path.join(output,`collection-${engine}-${width}.png`),fullPage:true});
   await page.evaluate(()=>showMedals('2027-01'));
   await page.locator('.medal-item[data-medal-art-month="01"]>summary').click();
   await page.waitForFunction(()=>document.querySelector('.medal-item>summary img')?.naturalWidth===640);
   assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1));
   await page.screenshot({path:path.join(output,`expanded-${engine}-${width}.png`),fullPage:true});
   assert.deepEqual(external,[]);assert.deepEqual(errors,[]);await page.close();
  }
  // Existing portal prefix works for both the existing API and all artwork URLs.
  requests=[];const portal=await browser.newPage({viewport:{width:390,height:900}});
  await clock(portal,'2028-02-03T12:00:00+08:00');await portal.goto(origin+'/recognition/?month=2027-01');await portal.waitForFunction(()=>window.ready);
  const src=await portal.locator('.medal-item>summary img').getAttribute('src');assert(src.startsWith('/recognition/static/'));
  await pageImageReady(portal);await portal.close();
  // Artwork failure keeps the honor visible and does not start retries/full-image work.
  requests=[];broken=true;const failed=await browser.newPage({viewport:{width:390,height:900}});
  await clock(failed,'2028-02-03T12:00:00+08:00');await failed.goto(origin+'/?month=2027-01');await failed.waitForFunction(()=>window.ready);
  await failed.waitForFunction(()=>document.querySelector('.medal-item>summary img')?.dataset.failed==='true');
  assert(await failed.locator('.medal-item>summary .medal-art-fallback').isVisible());
  await failed.locator('.medal-item>summary').click();assert.match(await failed.locator('.medal-detail-card').textContent(),/2027-02-09/);
  assert.equal(imageRequests().filter(r=>r.endsWith('-detail.webp')).length,0);await failed.close();broken=false;
  // Frontline view before launch performs neither a medal API call nor artwork requests.
  requests=[];const hidden=await browser.newPage();await clock(hidden,'2026-11-07T12:00:00+08:00');await hidden.goto(origin);await hidden.waitForFunction(()=>window.ready);
  assert.equal(await hidden.locator('.medal-item').count(),0);assert.equal(imageRequests().length,0);assert.equal(requests.filter(r=>r.startsWith('/api/')).length,0);await hidden.close();
  console.log(engine+': twelve colors, live text, lazy detail, cache reuse, no added API calls, portal prefix, fallback and prelaunch isolation passed');
  await browser.close();browser=null;
 }
}finally{if(browser)await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));}})().catch(e=>{console.error(e);process.exitCode=1;});
async function pageImageReady(page){await page.waitForFunction(()=>document.querySelector('.medal-item>summary img')?.naturalWidth===240);}
