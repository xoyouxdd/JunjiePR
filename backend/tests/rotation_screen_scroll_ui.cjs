// Reproduce tall workflow content using the real screen shell and styles.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../app/static');
const shell=fs.readFileSync(path.join(root,'rotation-screen.html'),'utf8').replace(/<link[^>]*>/g,'').replace(/<script[\s\S]*?<\/script>/g,'');
const css=['style.css','rotation.css'].map(file=>fs.readFileSync(path.join(root,'css',file),'utf8')).join('\n');
const lines=Array.from({length:12},(_,i)=>`<section class="rt-line"><header class="rt-line-head">线路 ${i+1}</header>${Array.from({length:8},(_,j)=>`<div class="rt-post"><span>岗位 ${j+1}</span><span class="rt-who-name">测试员工</span><span class="rt-mins">10分</span></div>`).join('')}</section>`).join('');
const pools=Array.from({length:5},(_,i)=>`<section class="rt-sect"><h2>人员区 ${i+1}</h2><div class="rt-chips">${Array.from({length:20},(_,j)=>`<div class="rt-chip"><span class="rt-nm">测试人员 ${j+1}</span><span class="rt-go">等待下一轮安排</span></div>`).join('')}</div></section>`).join('');
const workflow=`<section class="rt-workflow-board"><div class="rt-workflow-heading"><h1>轮岗看板</h1></div><div class="rt-board"><div class="rt-lines">${lines}</div><div class="rt-pool">${pools}</div></div><p data-scroll-end>底部内容</p></section>`;
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
 for(const [width,height] of [[390,844],[1024,768],[1440,900],[1920,1080]]){
  const page=await browser.newPage({viewport:{width,height},hasTouch:true});
  await page.setContent(shell.replace('</head>','<style>'+css+'</style></head>'));
  await page.locator('#rtApp').evaluate((el,html)=>el.innerHTML=html,workflow);
  const before=await page.evaluate(()=>scrollY);
  await page.mouse.move(width/2,Math.min(300,height/2));await page.mouse.wheel(0,650);
  await page.waitForFunction(previous=>scrollY>previous+100,before,{timeout:3000});
  // A real touch pan on the tablet-sized viewport must scroll too.
  if(width===1024){
   await page.evaluate(()=>scrollTo(0,0));
   const cdp=await page.context().newCDPSession(page);
   await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:500,y:650}]});
   for(const y of [600,500,400,300,200])await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:500,y}]});
   await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
   await page.waitForFunction(()=>scrollY>100,{timeout:3000});
   await cdp.detach();
   await page.waitForFunction(()=>{const y=scrollY,t=performance.now();if(!window.scrollRest||window.scrollRest.y!==y){window.scrollRest={y,t};return false;}return t-window.scrollRest.t>150;},null,{timeout:5000});
  }
  await page.evaluate(()=>scrollTo(0,600));
  const offset=await page.evaluate(()=>scrollY);
  await page.locator('#rtApp').evaluate((el,html)=>el.innerHTML=html,workflow);
  assert(Math.abs(await page.evaluate(()=>scrollY)-offset)<=2,'refresh must retain page position');
  await page.locator('[data-scroll-end]').scrollIntoViewIfNeeded();
  const geometry=await page.locator('[data-scroll-end]').boundingBox();assert(geometry.y+geometry.height<=height+1,'bottom must be reachable');
  assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1));
  if(width>=960){
   await page.locator('#rtApp').evaluate(el=>el.innerHTML='<div class="rt-board"><section class="rt-line-area"><div class="rt-lines">旧版固定布局</div></section><div class="rt-pool"></div></div>');
   assert.equal(await page.locator('body').evaluate(el=>getComputedStyle(el).overflowY),'hidden','legacy screen layout must remain fixed');
  }
  await page.locator('body').evaluate(el=>el.classList.remove('rotation-screen'));
  await page.locator('#rtApp').evaluate((el,html)=>el.innerHTML=html,workflow);
  await page.evaluate(()=>scrollTo(0,0));await page.mouse.wheel(0,650);
  await page.waitForFunction(()=>scrollY>100,{timeout:3000});
  console.log('PASS',width+'x'+height,'workflow screen scroll, refresh, bottom, legacy and ordinary board');await page.close();
 }
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
