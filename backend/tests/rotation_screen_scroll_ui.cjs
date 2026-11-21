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
lines[0].sortAt=1440+1/60;
lines[0].previousSortAt=1439+58/60;
for(const state of ['ready','pending','rest','meal','away'])for(let i=0;i<20;i++){
 persons.push({pid:`${state}-${i}`,name:`测试人员 ${i+1}`,state,start:420,end:1080,readyAt:state==='ready'?480:510,canDepart:state==='ready',flags:[],tags:[],...(state==='ready'?{task:{deadlineAt:i===0?480.5:483}}:{})});
}
persons.push({pid:'done',name:'已下班测试',state:'done',start:360,end:480,flags:[],tags:[]});
persons.push({pid:'oncoming',name:'待到班测试',state:'notyet',start:960,end:1350,flags:[],tags:[]});
const day={date,status:'live',ruleVersion:2,taskWorkflow:2,rotationEnd:1080,lines,persons};
(async()=>{for(const engine of (process.env.ROTATION_SCROLL_ENGINES||'edge,webkit').split(',')){
 const [type,options]={edge:[chromium,{channel:'msedge'}],webkit:[webkit,{}]}[engine];
 const browser=await type.launch({...options,headless:true});try{
 for(const [width,height] of [[390,844],[1024,768],[1180,620],[1920,1080]]){
  const screen=createScreenFixture({version:1,clock:{date,minute:480,paused:true,mode:'sim'},settings:{},day});
  const origin=await screen.start();
  const page=await browser.newPage({viewport:width===1180?{width:620,height:1180}:{width,height},hasTouch:true});
  try{
  await page.goto(`${origin}/rotation/screen`);
  await page.locator('.rt-lines .rt-line').first().waitFor();
  if(width===1180)await page.setViewportSize({width,height});
  const head=page.locator('[data-f-head=A1]');
  const currentTime=head.locator('.rt-sort-time'),previousTime=head.locator('.rt-previous-sort-time');
  assert.equal(await currentTime.innerText(),'次日 00:00:01');
  assert.equal(await previousTime.innerText(),'上次 23:59:58');
  assert((await currentTime.boundingBox()).y<(await previousTime.boundingBox()).y);
  assert.equal(await page.locator('[data-f-head=A2] .rt-previous-sort-time').innerText(),'上次 —');
  assert(await page.locator('body').evaluate(el=>el.classList.contains('rt-screen-flow')));
  const before=await page.evaluate(()=>scrollY);
  const summary=await page.locator('#rtScreenSummary').boundingBox();
  await page.mouse.move(summary.x+summary.width/2,summary.y+summary.height/2);
  await page.mouse.wheel(0,650);
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
   const before=document.querySelector('.rt-lines .rt-line');
   RT.render();
   return document.querySelector('.rt-lines .rt-line')===before;
  }),'clock refresh must retain the current touch target');
  const refreshed=lines.map((line,i)=>i?line:{...line,posts:line.posts.map((post,j)=>j?post:{...post,name:'已更新岗位'})});
  screen.updateDay({...day,lines:refreshed});
  await page.evaluate(()=>RT.load());
  assert.match(await page.locator('.rt-lines .rt-line').first().innerText(),/已更新岗位/);
  assert(Math.abs(await page.evaluate(()=>scrollY)-offset)<=2,'refresh must retain page position');
  const pendingMore=page.locator('details[data-screen-detail="more:pending"]');
  await pendingMore.locator('summary').click();
  const other=page.locator('details[data-screen-detail="other"]');
  const end=other.locator('summary');
  await end.scrollIntoViewIfNeeded();
  const geometry=await end.boundingBox();assert(geometry.y+geometry.height<=height+1,'bottom must be reachable');
  assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1));
  assert.equal(await other.evaluate(el=>el.open),false);
  await end.click();
  assert(await other.getByText('已下班测试',{exact:true}).isVisible());
  await end.focus();
  screen.updateDay({...day,lines:refreshed,persons:[...persons,{pid:'new-timeout',name:'上岗超时测试',state:'exception',start:420,end:1080,flags:[],tags:[],exception:{assignment:{line:'A1'},allowedAt:473,at:476,deadlineAt:476}}]});
  await page.evaluate(()=>RT.load());
  await page.locator('[data-f-pool="exception"]').getByText('上岗超时测试',{exact:true}).waitFor();
  assert.equal(await page.locator('[data-screen-count="exception"]').innerText(),'1');
  assert.equal(await pendingMore.evaluate(el=>el.open),true,'server refresh must keep the pending list expanded');
  assert.equal(await other.evaluate(el=>el.open),true,'server refresh must keep other people expanded');
  assert(await end.evaluate(el=>el===document.activeElement),'server refresh must preserve disclosure focus');
  assert(await other.getByText('待到班测试',{exact:true}).isVisible());
  if(width===1920){
   const jump=page.locator('[data-screen-metric="ready"]');
   const heading=page.locator('[data-screen-heading="ready"]');
   await jump.click();
   assert(await heading.evaluate(el=>el===document.activeElement),'summary shortcut should focus its section');
   const button=page.locator('[data-f-board="ready-0"]');
   const card=page.locator('.rt-chip').filter({has:button});
   await card.getByText(/即将超时 · 剩 30秒/).waitFor();
   await button.focus();
   await page.evaluate(()=>{RT.data.clock.minute=480.5;RT.render();});
   await card.getByText('确认时间已到，正在同步',{exact:true}).waitFor();
   assert.equal(await card.getByText(/即将超时/).count(),0);
   assert.equal(await page.locator('[data-screen-count="exception"]').innerText(),'1','the browser clock cannot create a server exception');
   assert(await button.evaluate(el=>el===document.activeElement),'countdown must preserve the focused action');
   const changed=persons.map(p=>p.pid==='ready-0'?{...p,name:'更新后的待上岗人员'}:p);
   screen.updateDay({...day,rotationEnd:1090,lines:refreshed,persons:changed});
   await page.evaluate(()=>RT.load());
   await card.getByText('更新后的待上岗人员',{exact:true}).waitFor();
   assert(await button.evaluate(el=>el===document.activeElement),'server refresh must preserve focus on a remaining action');
   const expired=changed.map(p=>p.pid==='ready-0'?{...p,state:'exception',canDepart:false,task:null,exception:{at:480.5}}:p);
   screen.updateDay({...day,lines:refreshed,persons:expired});
   await page.evaluate(()=>RT.load());
   await page.locator('[data-f-pool="exception"]').getByText('更新后的待上岗人员',{exact:true}).waitFor();
   assert.equal(await page.locator('[data-screen-count="exception"]').innerText(),'1');
   assert.equal(await page.locator('[data-screen-count="ready"]').innerText(),'19');
  }
  if(width===390){
   const operationRequests=[];
   page.on('request',request=>{
    const endpoint=new URL(request.url()).pathname;
    if(['/api/rotation-screen/act','/api/rotation-screen/confirmation'].includes(endpoint))operationRequests.push(endpoint);
   });
   await page.evaluate(date=>sessionStorage.setItem('rotation.pendingConfirmation',JSON.stringify({date,pid:'pending-0',token:'a'.repeat(64)})),date);
   screen.updateDay({...day,ruleVersion:1,readOnly:true,taskWorkflow:false});
   await page.reload();
   await page.getByRole('heading',{name:/轮岗存档/}).waitFor();
   await page.getByText('旧版记录，仅供查看。',{exact:true}).waitFor();
   await page.waitForFunction(()=>!sessionStorage.getItem('rotation.pendingConfirmation'));
   assert.equal(await page.locator('#rtApp button,[draggable=true]').count(),0);
   assert.match(await page.locator('.rt-lines').innerText(),/测试员工/);
   assert.match(await page.locator('.rt-pool').innerText(),/测试人员/);
   assert.deepEqual(operationRequests,[]);
  }
  console.log('PASS',engine,width+'x'+height,'formal screen scroll, refresh and archive');
  }finally{try{await page.close();}finally{await screen.close();}}
 }
}finally{await browser.close();}}})().catch(e=>{console.error(e);process.exitCode=1;});
