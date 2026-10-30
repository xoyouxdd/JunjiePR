// Real shared renderer/CSS, isolated people; no backend or production connection.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
 const page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.setContent('<body class="rotation-page"><main id="rtApp" class="rt-workflow-board"></main></body>');
 await page.addStyleTag({content:fs.readFileSync(path.resolve(__dirname,'../app/static/css/rotation.css'),'utf8')});
 await page.addScriptTag({content:`window.RT={data:{attraction_name:'示例圈',day:{date:'2026-10-30',lines:[],persons:Array.from({length:22},(_,i)=>({pid:String(i),name:'示例人员'+i,role:'rotation',state:'notyet',start:i<20?555:i===20?540:600,end:i<21?1335:1080}))}}};
 window.pad2=n=>String(n).padStart(2,'0');window.esc=s=>String(s??'').replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;');window.hm=n=>pad2(Math.floor(n/60))+':'+pad2(n%60);window.STATE_NAME={notyet:'班次未开始'};window.poolState=p=>p.state;window.outsideKind=()=>false;window.opWaiting=()=>false;window.badges=()=>'';`});
 await page.addScriptTag({content:fs.readFileSync(path.resolve(__dirname,'../app/static/js/rotation-workflow.js'),'utf8')});
 await page.evaluate(()=>document.dispatchEvent(new Event('DOMContentLoaded')));
 for(const daily of [false,true]){
  await page.evaluate(daily=>{window.drawShiftTest=()=>document.getElementById('rtApp').innerHTML=workflowPools(540,true,{daily,editable:true});drawShiftTest();},daily);
  const groups=page.locator('.rt-others .rt-shift-group');assert.equal(await groups.count(),3);
  const large=groups.filter({has:page.getByText('09:15–22:15',{exact:true})});
  assert.match(await large.locator('summary').innerText(),/20 人/);
  assert.equal(await large.evaluate(el=>el.open),false);
  await large.locator('summary').click();await page.waitForFunction(()=>document.querySelectorAll('.rt-shift-group[open]').length===1);
  assert.equal(await large.locator('.rt-chip:visible').count(),20);
  assert.equal(await large.locator('.rt-chip[draggable=true]').count(),20);
  assert((await large.locator('.rt-name-row small').allTextContents()).every(t=>t==='22:15'));
  await page.evaluate(()=>drawShiftTest());assert.equal(await large.evaluate(el=>el.open),true,'expanded state lost on refresh');
  assert.equal(await large.locator('.rt-chips').evaluate(el=>getComputedStyle(el).maxHeight),'none');
  for(const width of [320,1440]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);const rects=await large.locator('.rt-chip').evaluateAll(xs=>xs.map(x=>{const r=x.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,height:r.height}}));assert(rects.every(r=>r.height>=30));assert(!rects.some((a,i)=>rects.slice(i+1).some(b=>Math.min(a.right,b.right)>Math.max(a.left,b.left)+1&&Math.min(a.bottom,b.bottom)>Math.max(a.top,b.top)+1)),'cards overlap');}
  await large.locator('summary').click();await page.waitForFunction(()=>document.querySelectorAll('.rt-shift-group[open]').length===0);await page.evaluate(()=>drawShiftTest());assert.equal(await large.evaluate(el=>el.open),false,'collapsed state lost on refresh');
 }
 assert.deepEqual(errors,[]);console.log('PASS: full-shift grouping, counts, collapsed default, refresh state, end-time labels, drag handles and non-overlapping cards at 320/1440px');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
