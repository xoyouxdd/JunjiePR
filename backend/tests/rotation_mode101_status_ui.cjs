'use strict';
// The real board and screen templates with isolated, synthetic status transitions.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('./support/browser-runtime.cjs');
const {createScreenFixture}=require('./support/rotation-screen-fixture.cjs');
const {openRotationPage}=require('./support/rotation-browser.cjs');
const out=path.resolve(__dirname,'../../.handover_review/rotation-mode101-status');fs.mkdirSync(out,{recursive:true});
const names=['张松','张瑞','朱一晶','杜红','王国庆','何智轩','李欣','郭皓','叶青','李志豪'];
const definitions=[['A1','塔台','上客1','上协1','下协','大出口商店'],['A2','饮水童车','小C','核销','身高墙','分组1'],['B1','塔台协助','上客2','上协2','分组2','快迎2'],['B2','迎宾童车','迎宾','豹协','汇合','快速汇合'],['C1','上客3','上协3','预演','预演协助','单人队末'],['C2','快迎1','单人迎宾','分组3','上客4','上协4'],['D1','铁马C','铁马B','铁马A','铁马出口','迎宾协助'],['D2','河马池','单人藏流','下客1','下客2']];
function snapshot(){
  const persons=[];
  const lines=definitions.map(([id,...posts],j)=>({id,group:id[0],active:true,sortAt:807,previousSortAt:780,posts:posts.map((name,i)=>{
    const pid=`${id}-${i}`;persons.push({pid,name:names[(j*5+i)%names.length],role:'rotation',state:'onpost',start:435,end:1335,line:id,lineStart:807,entrySortAt:807,flags:[],tags:[]});
    return {key:pid,name,occ:pid,open:true,opened:true};
  })}));
  for(let i=0;i<20;i++)persons.push({pid:`rest-${i}`,name:`休息伙伴${i+1}`,role:'rotation',state:'rest',start:435,end:1335,readyAt:820+i,flags:[],tags:[]});
  return {version:1,attraction_name:'热力追踪',clock:{date:'2026-11-26',minute:808,mode:'sim',paused:true,speed:1},settings:{departEarly:0},day:{date:'2026-11-26',status:'live',ruleVersion:2,taskWorkflow:2,rotationEnd:1365,persons,lines,off:[]}};
}
async function mode(page,phase){await page.evaluate(phase=>{
  const day=RT.data.day;day.mode101Active=phase==='fixed';
  for(const line of day.lines)for(const post of line.posts){
    if(!['塔台','塔台协助'].includes(post.name))continue;
    if(phase)post.mode101={phase,target:line.posts[1].key};else delete post.mode101;
  }
  RT.render();
},phase);}
async function check(page,label){
  const banner=page.locator('#rtMode101Status');
  assert.equal(await banner.isVisible(),false);
  await mode(page,'fixed');await banner.waitFor({state:'visible'});
  assert.match(await banner.innerText(),/101 模式进行中/);
  assert.equal(await banner.locator('strong').evaluate(el=>getComputedStyle(el).animationName),'rt-mode101-title-pulse');
  await page.evaluate(()=>{window.savedModeTitle=document.querySelector('#rtMode101Status strong');renderClock();renderClock();});
  assert(await page.evaluate(()=>savedModeTitle===document.querySelector('#rtMode101Status strong')),'Clock updates must not recreate or reannounce the banner');
  await page.setViewportSize({width:1920,height:1080});
  await page.screenshot({path:path.join(out,`${label}-active.png`),animations:'disabled'});
  await page.locator('.rt-top').screenshot({path:path.join(out,`${label}-active-header.png`),animations:'disabled'});
  for(const width of [320,390,768,1920]){
    await page.setViewportSize({width,height:850});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,`${label} overflow ${width}`);
    await page.evaluate(()=>window.scrollTo(0,400));
    const box=await banner.boundingBox();assert(box.y>=0&&box.y+box.height<850,`${label} status must remain visible while scrolling`);
    await page.evaluate(()=>window.scrollTo(0,0));
  }
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:path.join(out,`${label}-phone.png`),animations:'disabled'});
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await banner.locator('strong').evaluate(el=>getComputedStyle(el).animationName),'none');
  await page.emulateMedia({reducedMotion:'no-preference'});
  await mode(page,'return');assert.match(await banner.innerText(),/待恢复 2 人/);
  assert.equal(await banner.locator('strong').evaluate(el=>getComputedStyle(el).animationName),'none');
  await page.setViewportSize({width:1920,height:1080});await page.screenshot({path:path.join(out,`${label}-returning.png`),animations:'disabled'});
  await page.locator('.rt-top').screenshot({path:path.join(out,`${label}-returning-header.png`),animations:'disabled'});
  await page.evaluate(()=>{delete RT.data.day.lines[0].posts[0].mode101;RT.modalOpen=true;renderClock();});
  assert.match(await banner.innerText(),/待恢复 1 人/,'Status also updates while a task dialog is open');
  await page.evaluate(()=>{RT.modalOpen=false;});
  await mode(page,null);assert.equal(await banner.isVisible(),false);
  await mode(page,'fixed');
  await page.evaluate(()=>{RT.data.day.status='ended';RT.render();});assert.equal(await banner.isVisible(),false);
  await page.evaluate(()=>{RT.data.day.status='live';RT.data.day.readOnly=true;RT.render();});assert.equal(await banner.isVisible(),false);
  await page.evaluate(()=>{RT.data.day.readOnly=false;RT.render();});assert.equal(await banner.isVisible(),true);
  console.log(`${label}: active/returning/recovered, sticky at four widths, reduced motion, modal updates and ended/archive visibility PASS`);
}
(async()=>{let browser;const fixture=createScreenFixture(snapshot(),{demo:true}),origin=await fixture.start();try{
  browser=await chromium.launch({channel:'msedge',headless:true});const errors=[];
  const screen=await browser.newPage();screen.on('pageerror',e=>errors.push(e.message));await screen.goto(origin+'/rotation/screen');await screen.locator('.rt-line').first().waitFor();await check(screen,'screen');
  const board=await browser.newPage();board.on('pageerror',e=>errors.push(e.message));await openRotationPage(board,'/rotation-board');
  await board.evaluate(data=>{me={...me,name:'隔离测试主管',kind_label:'主管',capabilities:{can_operate:true,can_manage:true}};renderWho();RT.data=data;tab='board';RT.render();},snapshot());
  await check(board,'board');
  for(const tabName of ['roster','records','settings']){
    await board.evaluate(name=>{tab=name;RT.render();},tabName);
    assert.match(await board.locator('#rtMode101Status').innerText(),/101 模式进行中/);
  }
  await board.evaluate(()=>{me.capabilities.can_operate=false;RT.render();});assert.equal(await board.locator('#rtMode101Status').isVisible(),false,'CM has no management status banner');
  await board.evaluate(()=>{me.capabilities={can_operate:true,can_manage:false};RT.render();});assert.equal(await board.locator('#rtMode101Status').isVisible(),true,'TR operation view retains the same reminder');
  assert.deepEqual(errors,[]);
}finally{await browser?.close();await fixture.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
