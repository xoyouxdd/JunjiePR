// Reproduce tall workflow content using the real screen shell and styles.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium,webkit}=require('./support/browser-runtime.cjs');
const root=path.resolve(__dirname,'../app/static');
const shell=fs.readFileSync(path.join(root,'rotation-screen.html'),'utf8').replace(/<link[^>]*>/g,'').replace(/<script[\s\S]*?<\/script>/g,'');
const css=['style.css','rotation.css'].map(file=>fs.readFileSync(path.join(root,'css',file),'utf8')).join('\n');
const lines=Array.from({length:12},(_,i)=>`<section class="rt-line"><header class="rt-line-head">线路 ${i+1}</header>${Array.from({length:8},(_,j)=>`<div class="rt-post"><span>岗位 ${j+1}</span><span class="rt-who-name">测试员工</span><span class="rt-mins">10分</span></div>`).join('')}</section>`).join('');
const pools=Array.from({length:5},(_,i)=>`<section class="rt-sect"><h2>人员区 ${i+1}</h2><div class="rt-chips">${Array.from({length:20},(_,j)=>`<div class="rt-chip"><span class="rt-nm">测试人员 ${j+1}</span><span class="rt-go">等待下一轮安排</span></div>`).join('')}</div></section>`).join('');
const banners=Array.from({length:4},(_,i)=>`<section class="rt-timeout-banner"><div><strong>A${i+1} · 关联任务待核实</strong><p>等待负责人接手</p></div></section>`).join('');
const workflow=`<section class="rt-workflow-board"><div class="rt-workflow-heading"><h1>轮岗看板</h1></div>${banners}<div class="rt-board"><div class="rt-lines">${lines}</div><div class="rt-pool">${pools}</div></div><p data-scroll-end>底部内容</p></section>`;
const screenSource=fs.readFileSync(path.join(root,'js/rotation-screen.js'),'utf8');
(async()=>{for(const [engine,type,options] of [['Edge',chromium,{channel:'msedge'}],['WebKit',webkit,{}]]){
 const browser=await type.launch({...options,headless:true});try{
 for(const [width,height] of [[390,844],[1024,768],[1180,620],[1920,1080]]){
  const page=await browser.newPage({viewport:{width,height},hasTouch:true});
  await page.setContent(shell.replace('</head>','<style>'+css+'</style></head>'));
  await page.addScriptTag({path:path.join(root,'js/rotation-common.js')});
  await page.addScriptTag({content:screenSource.slice(0,screenSource.lastIndexOf('(async () => {'))});
  if(width===1180){
   await page.setViewportSize({width:620,height:1180});
  }
  await page.evaluate(html=>{
   RT.data={day:{taskWorkflow:true,status:'live'}};
   window.screenMarkup=html;
   screenView=()=>window.screenMarkup;
   RT.render();
  },workflow);
  if(width===1180)await page.setViewportSize({width,height});
  assert(await page.locator('body').evaluate(el=>el.classList.contains('rt-screen-flow')));
  const before=await page.evaluate(()=>scrollY);
  await page.mouse.move(width/2,Math.min(300,height/2));await page.mouse.wheel(0,650);
  await page.waitForFunction(previous=>scrollY>previous+100,before,{timeout:3000});
  // A real touch pan on the tablet-sized viewport must scroll too.
  if(engine==='Edge'&&width===1180){
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
  await page.evaluate(()=>{window.screenMarkup=window.screenMarkup.replace('等待负责人接手','正在核实');RT.render();});
  assert(Math.abs(await page.evaluate(()=>scrollY)-offset)<=2,'refresh must retain page position');
  await page.locator('[data-scroll-end]').scrollIntoViewIfNeeded();
  const geometry=await page.locator('[data-scroll-end]').boundingBox();assert(geometry.y+geometry.height<=height+1,'bottom must be reachable');
  assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1));
  if(width>=960){
   await page.evaluate(()=>{
    RT.data.day.taskWorkflow=false;
    window.screenMarkup='<div class="rt-board"><section class="rt-line-area"><div class="rt-lines">旧版固定布局</div></section><div class="rt-pool"></div></div>';
    RT.render();
   });
   assert.equal(await page.locator('body').evaluate(el=>getComputedStyle(el).overflowY),'hidden','legacy screen layout must remain fixed');
  }
  await page.locator('body').evaluate(el=>el.classList.remove('rotation-screen'));
  await page.locator('#rtApp').evaluate((el,html)=>el.innerHTML=html,workflow);
  await page.evaluate(()=>scrollTo(0,0));await page.mouse.wheel(0,650);
  await page.waitForFunction(()=>scrollY>100,{timeout:3000});
  console.log('PASS',engine,width+'x'+height,'workflow screen scroll, refresh, bottom, legacy and ordinary board');await page.close();
 }
}finally{await browser.close();}}})().catch(e=>{console.error(e);process.exitCode=1;});
