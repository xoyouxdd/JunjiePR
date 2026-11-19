// Reproduce tall workflow content through the real screen startup and renderer.
const assert=require('node:assert/strict');
const {chromium,webkit}=require('./support/browser-runtime.cjs');
const {createScreenFixture}=require('./support/rotation-screen-fixture.cjs');
const date='2026-10-11';
const persons=[];
const lines=Array.from({length:12},(_,i)=>({id:`A${i+1}`,group:'A',active:true,posts:Array.from({length:8},(_,j)=>{
 const pid=`post-${i}-${j}`;
 persons.push({pid,name:'测试员工',state:'onpost',start:420,end:1080,lineStart:470,flags:[],tags:[]});
 return {name:`岗位 ${j+1}`,occ:pid,open:true};
})}));
for(const state of ['ready','pending','rest','meal','away'])for(let i=0;i<20;i++){
 persons.push({pid:`${state}-${i}`,name:`测试人员 ${i+1}`,state,start:420,end:1080,readyAt:state==='ready'?480:510,flags:[],tags:[]});
}
const day={date,status:'live',taskWorkflow:true,lines,persons,timeoutCases:lines.slice(0,4).map(line=>({id:line.id,line:line.id,people:[]}))};
(async()=>{for(const engine of (process.env.ROTATION_SCROLL_ENGINES||'edge,webkit').split(',')){
 const [type,options]={edge:[chromium,{channel:'msedge'}],webkit:[webkit,{}]}[engine];
 const browser=await type.launch({...options,headless:true});try{
 for(const [width,height] of [[390,844],[1024,768],[1180,620],[1920,1080]]){
  const screen=createScreenFixture({version:1,clock:{date,minute:480,paused:true,mode:'sim'},settings:{},day});
  const origin=await screen.start();
  const page=await browser.newPage({viewport:width===1180?{width:620,height:1180}:{width,height},hasTouch:true});
  try{
  await page.goto(`${origin}/rotation/screen`);
  await page.locator('.rt-timeout-banner').first().waitFor();
  if(width===1180)await page.setViewportSize({width,height});
  assert(await page.locator('body').evaluate(el=>el.classList.contains('rt-screen-flow')));
  const before=await page.evaluate(()=>scrollY);
  await page.mouse.move(width/2,Math.min(300,height/2));await page.mouse.wheel(0,650);
  await page.waitForFunction(previous=>scrollY>previous+100,before,{timeout:3000});
  // A real touch pan on the tablet-sized viewport must scroll too.
  if(engine==='edge'&&width===1180){
   await page.evaluate(()=>scrollTo(0,0));
   const cdp=await page.context().newCDPSession(page);
   await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:500,y:410}]});
   for(const y of [360,310,260,210,160]){
    await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:500,y}]});
    await page.evaluate(()=>RT.render());
   }
   await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
   await page.waitForFunction(()=>scrollY>100,{timeout:3000});
   await cdp.detach();
   await page.waitForFunction(()=>{const y=scrollY,t=performance.now();if(!window.scrollRest||window.scrollRest.y!==y){window.scrollRest={y,t};return false;}return t-window.scrollRest.t>150;},null,{timeout:5000});
  }
  await page.evaluate(()=>scrollTo(0,600));
  const offset=await page.evaluate(()=>scrollY);
  assert(await page.evaluate(()=>{
   const before=document.querySelector('.rt-timeout-banner');
   RT.render();
   return document.querySelector('.rt-timeout-banner')===before;
  }),'clock refresh must retain the current touch target');
  screen.updateDay({...day,timeoutCases:day.timeoutCases.map((entry,i)=>i?entry:{...entry,handler:'正在核实'})});
  await page.evaluate(()=>RT.load());
  assert.match(await page.locator('.rt-timeout-banner').first().innerText(),/正在核实/);
  assert(Math.abs(await page.evaluate(()=>scrollY)-offset)<=2,'refresh must retain page position');
  const end=page.locator('.rt-pool .rt-sect h2').last();
  await end.scrollIntoViewIfNeeded();
  const geometry=await end.boundingBox();assert(geometry.y+geometry.height<=height+1,'bottom must be reachable');
  assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1));
  if(width>=960){
   screen.updateDay({...day,taskWorkflow:false});
   await page.evaluate(()=>RT.load());
   assert.equal(await page.locator('body').evaluate(el=>getComputedStyle(el).overflowY),'hidden','legacy screen layout must remain fixed');
  }
  await page.locator('body').evaluate(el=>el.classList.remove('rotation-screen'));
  screen.updateDay(day);
  await page.evaluate(()=>RT.load());
  await page.evaluate(()=>scrollTo(0,0));await page.mouse.wheel(0,650);
  await page.waitForFunction(()=>scrollY>100,{timeout:3000});
  console.log('PASS',engine,width+'x'+height,'workflow screen scroll, refresh, bottom, legacy and ordinary board');
  }finally{try{await page.close();}finally{await screen.close();}}
 }
}finally{await browser.close();}}})().catch(e=>{console.error(e);process.exitCode=1;});
