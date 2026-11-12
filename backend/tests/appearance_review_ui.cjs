// Targeted lifecycle review. Real modules + strict CSP; no backend or business data.
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict'),{createHash}=require('node:crypto');
const {chromium,webkit,firefox}=require('playwright');
const root=path.resolve(__dirname,'../app/static');
const html=fs.readFileSync(path.join(root,'index.html'),'utf8').replaceAll('__STATIC_CACHE_VERSION__','appearance-review').replace(/<script type="module"[^>]*><\/script>/,'<script type="module" src="review-harness.js"></script>');
const map=/<script type="importmap">([\s\S]*?)<\/script>/.exec(html)[1];
const csp="default-src 'self'; script-src 'self' 'sha256-"+createHash('sha256').update(map.replace(/\r\n?/g,'\n')).digest('base64')+"'; style-src 'self'; img-src 'self' data:; object-src 'none'";
const harness=`import {state} from './static/js/app/state.js';import {configureViews,render,reviewCleanupCount} from './static/js/app/context.js';import {mountDailyWish} from './static/js/app/daily-wish.js';import {renderUserBadge} from './static/js/app/navigation.js';import {applyCircleTheme} from './static/js/app/security.js';state.me={id:1,name:'状态审查员工',employee_no:'1234567',role_code:'CM',base_role_code:'CM',role_name:'组员',attraction_name:'热力追踪',permissions:[]};const story=document.getElementById('themeStory');configureViews({actionCenter:()=>{mountDailyWish(story,state.me.id,{integrated:true});document.getElementById('app').innerHTML='<h2>今日工作</h2>';},home:()=>{document.getElementById('app').innerHTML='<h2>首页</h2>';}});window.review={async navigate(tab){state.tab=tab;await render();},mount:()=>mountDailyWish(story,state.me.id,{integrated:true}),cleanupCount:reviewCleanupCount,user(id){state.me.id=id;mountDailyWish(story,id,{integrated:true});}};applyCircleTheme();renderUserBadge();await window.review.navigate('actionCenter');window.reviewReady=true;`;
const server=http.createServer((req,res)=>{
 const url=new URL(req.url,'http://fixture');let body,type='text/javascript';
 if(url.pathname==='/'){body=html;type='text/html; charset=utf-8';res.setHeader('Content-Security-Policy',csp);}
 else if(url.pathname==='/review-harness.js')body=harness;
 else if(url.pathname.startsWith('/static/')){const file=path.resolve(root,'.'+decodeURIComponent(url.pathname.slice(7)));if(!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.writeHead(404);res.end();return;}body=fs.readFileSync(file);type=({'.js':'text/javascript','.css':'text/css','.png':'image/png','.webp':'image/webp','.svg':'image/svg+xml','.json':'application/json'})[path.extname(file)]||'application/octet-stream';
  // Read-only introspection of the private cleanup set; registration logic is untouched.
  if(file===path.join(root,'js/app/context.js'))body=body.toString()+"\nexport const reviewCleanupCount=()=>activePageCleanups.size;\n";
 }else{res.writeHead(404);res.end();return;}
 res.writeHead(200,{'Content-Type':type,'Cache-Control':'no-store'});res.end(body);
});
(async()=>{await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser;const failures=[],results=[];try{
 for(const engine of (process.env.APPEARANCE_ENGINES?.split(',')||['chromium','webkit','firefox'])){
  browser=await({chromium,webkit,firefox}[engine]).launch(engine==='chromium'?{channel:'msedge',headless:true}:{headless:true});
  const page=await browser.newPage({viewport:{width:1280,height:1000},hasTouch:process.env.APPEARANCE_REVIEW_TOUCH==='1'}),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto('http://127.0.0.1:'+server.address().port+'/');await page.waitForFunction(()=>window.reviewReady);
  async function check(name,fn){if(process.env.APPEARANCE_REVIEW_FILTER&&!name.includes(process.env.APPEARANCE_REVIEW_FILTER))return;try{await fn();results.push(engine+': '+name);}catch(error){failures.push(engine+': '+name+' — '+error.message);}}
  await check('input event matrix updates exactly once',async()=>{
   const button=page.locator('[data-wish-next]');await button.click();
   for(const rollover of [false,true])for(const mode of ['mouse','touch','Enter','Space','programmatic']){
    if(mode==='touch'&&process.env.APPEARANCE_REVIEW_TOUCH!=='1')continue;
    await button.focus();
    const before=await page.evaluate(rollover=>{const NativeDate=Date;window.reviewInputDate=NativeDate;const now=new NativeDate();if(rollover)now.setDate(now.getDate()+1);const time=now.getTime();window.Date=class extends NativeDate{constructor(...args){super(...(args.length?args:[time]));}static now(){return time;}};window.reviewInputSetter=Storage.prototype.setItem;window.reviewInputWrites=0;Storage.prototype.setItem=function(key,value){if(key==='fz-daily-wish-v1-1')window.reviewInputWrites++;return window.reviewInputSetter.call(this,key,value);};return {id:JSON.parse(localStorage.getItem('fz-daily-wish-v1-1')).id,day:now.getFullYear()+'-'+(now.getMonth()+1)+'-'+now.getDate()};},rollover);
    try{
     if(mode==='mouse')await button.click();
     else if(mode==='touch'){const box=await button.boundingBox();await page.touchscreen.tap(box.x+box.width/2,box.y+box.height/2);}
     else if(mode==='programmatic')await button.evaluate(node=>node.click());
     else await button.press(mode);
     const result=await page.evaluate(()=>({writes:window.reviewInputWrites,value:JSON.parse(localStorage.getItem('fz-daily-wish-v1-1'))}));
     assert.equal(result.writes,1,mode+' '+(rollover?'next day':'same day')+' must update once');assert.equal(result.value.day,before.day);assert.notEqual(result.value.id,before.id);
    }finally{await page.evaluate(()=>{window.Date=window.reviewInputDate;Storage.prototype.setItem=window.reviewInputSetter;review.mount();});}
   }
  });
  await check('midnight next button generates exactly one wish',async()=>{
   await page.locator('[data-wish-next]').click();
   await page.evaluate(()=>{window.reviewNativeDate=Date;const tomorrow=new Date();tomorrow.setDate(tomorrow.getDate()+1);const time=tomorrow.getTime(),NativeDate=Date;window.Date=class extends NativeDate{constructor(...args){super(...(args.length?args:[time]));}static now(){return time;}};window.reviewNativeSetItem=Storage.prototype.setItem;window.reviewDayWrites=0;Storage.prototype.setItem=function(key,value){if(key==='fz-daily-wish-v1-1')window.reviewDayWrites++;return window.reviewNativeSetItem.call(this,key,value);};});
   try{await page.locator('[data-wish-next]').click();assert.equal(await page.evaluate(()=>window.reviewDayWrites),1,'one user action must not silently generate and remember two wishes');}
   finally{await page.evaluate(()=>{window.Date=window.reviewNativeDate;Storage.prototype.setItem=window.reviewNativeSetItem;review.mount();});}
  });
  await check('foreground return resets yesterday revealed card',async()=>{
   await page.locator('[data-wish-reveal]').click();const previous=await page.evaluate(()=>JSON.parse(localStorage.getItem('fz-daily-wish-v1-1')).id);
   await page.evaluate(()=>{window.reviewNativeDate=Date;const tomorrow=new Date();tomorrow.setDate(tomorrow.getDate()+1);const time=tomorrow.getTime(),NativeDate=Date;window.Date=class extends NativeDate{constructor(...args){super(...(args.length?args:[time]));}static now(){return time;}};document.dispatchEvent(new Event('visibilitychange'));});
   try{assert(await page.locator('[data-wish-scratch]').isVisible());assert(await page.locator('[data-wish-reveal]').isEnabled());assert.notEqual(await page.evaluate(()=>JSON.parse(localStorage.getItem('fz-daily-wish-v1-1')).id),previous);}
   finally{await page.evaluate(()=>{window.Date=window.reviewNativeDate;review.mount();});}
  });
  await check('stale disposal cannot clear replacement account card',async()=>{
   const valid=await page.evaluate(()=>{const disposeOld=review.mount();review.user(2);const current=document.querySelector('[data-wish-scratch]');disposeOld();review.mount();return current===document.querySelector('[data-wish-scratch]')&&document.getElementById('themeStory')._dailyWishContext.userId==='2';});assert(valid);await page.evaluate(()=>review.user(1));
  });
  await check('reveal survives navigation and repeated mount',async()=>{await page.locator('[data-wish-reveal]').click();const same=await page.evaluate(async()=>{const before=document.querySelector('[data-wish-scratch]');await review.navigate('home');await review.navigate('actionCenter');review.mount();return before===document.querySelector('[data-wish-scratch]')&&before.hidden;});assert(same);});
  await check('partial scratch survives resize',async()=>{await page.locator('[data-wish-next]').click();const canvas=page.locator('[data-wish-scratch]');await canvas.scrollIntoViewIfNeeded();const b=await canvas.boundingBox();await page.mouse.move(b.x+30,b.y+25);await page.mouse.down();await page.mouse.move(b.x+90,b.y+25,{steps:6});await page.mouse.up();const fraction=()=>canvas.evaluate(c=>{const p=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let clear=0,n=0;for(let i=3;i<p.length;i+=16){n++;if(p[i]<64)clear++;}return clear/n;});const before=await fraction();assert(before>0&&before<.32);await page.setViewportSize({width:390,height:1000});await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));const after=await fraction();assert(after>before*.5,'resize erased all scratch progress');await page.setViewportSize({width:1280,height:1000});});
  await check('midnight first interaction refreshes day',async()=>{const dates=await page.evaluate(()=>{const NativeDate=Date;window.reviewNativeDate=NativeDate;const tomorrow=new NativeDate();tomorrow.setDate(tomorrow.getDate()+1);const time=tomorrow.getTime();window.Date=class extends NativeDate{constructor(...args){super(...(args.length?args:[time]));}static now(){return time;}};return {expected:tomorrow.getFullYear()+'-'+(tomorrow.getMonth()+1)+'-'+tomorrow.getDate(),oldId:JSON.parse(localStorage.getItem('fz-daily-wish-v1-1')).id};});await page.locator('[data-wish-reveal]').click();const stored=await page.evaluate(()=>JSON.parse(localStorage.getItem('fz-daily-wish-v1-1')));assert.equal(stored.day,dates.expected);assert.notEqual(stored.id,dates.oldId);await page.evaluate(()=>{window.Date=window.reviewNativeDate;});});
  await page.evaluate(()=>{if(window.reviewNativeDate)window.Date=window.reviewNativeDate;review.mount();});
  await check('closed employee and HR dialogs release cleanup',async()=>{const base=await page.evaluate(()=>review.cleanupCount());for(const role of ['CM','HR_CIRCLE']){await page.evaluate(async role=>{const {state}=await import('./static/js/app/state.js');state.me.role_code=role;state.me.base_role_code=role;(await import('./static/js/app/navigation.js')).renderUserBadge();},role);for(let i=0;i<4;i++){await page.locator('#appearanceBtn').click();await page.locator('[data-avatar-dialog-close],[data-appearance-close]').click();await page.locator('[data-avatar-dialog],[data-appearance-dialog]').waitFor({state:'detached'});}assert.equal(await page.evaluate(()=>review.cleanupCount()),base);}});
  await check('navigation closes dialog without skipping other cleanup',async()=>{await page.locator('#appearanceBtn').click();await page.evaluate(async()=>{const {registerPageCleanup}=await import('./static/js/app/context.js');window.reviewCleanupProbe=0;registerPageCleanup(()=>window.reviewCleanupProbe++);await review.navigate('home');});await page.locator('[data-avatar-dialog],[data-appearance-dialog]').waitFor({state:'detached'});assert.equal(await page.evaluate(()=>window.reviewCleanupProbe),1);assert.equal(await page.locator('.app-frame[inert]').count(),0);assert.equal(await page.evaluate(()=>document.body.style.overflow),'');});
  await check('user-scoped storage stays separate',async()=>{const original=await page.evaluate(()=>localStorage.getItem('fz-daily-wish-v1-1'));await page.evaluate(()=>review.user(2));await page.locator('[data-wish-next]').click();assert.equal(await page.evaluate(()=>localStorage.getItem('fz-daily-wish-v1-1')),original);assert(await page.evaluate(()=>!!localStorage.getItem('fz-daily-wish-v1-2')));});
  await check('new wish and favorites survive reload and stay account scoped',async()=>{
   await page.setViewportSize({width:320,height:850});
   await page.evaluate(()=>{
    const now=new Date(),day=now.getFullYear()+'-'+(now.getMonth()+1)+'-'+now.getDate();
    localStorage.setItem('fz-daily-wish-v1-1',JSON.stringify({day,id:2199,history:[2000,2199]}));
   });
   await page.reload();await page.waitForFunction(()=>window.reviewReady);
   const favorite=page.locator('[data-wish-favorite]'),toggle=page.locator('[data-wish-favorites-toggle]');
   const panel=page.locator('[data-wish-favorites]'),list=panel.locator('li');
   const expected='希望回头想起今天，你还能记得一件跟工作无关、光是想起来就有点高兴的小事。';
   assert(await favorite.isHidden());
   await toggle.click();assert(await panel.locator('[data-wish-favorites-empty]').isVisible());
   await page.locator('[data-wish-reveal]').click();
   assert.equal(await page.locator('[data-wish-text]').innerText(),expected);
   await favorite.click();assert.equal(await favorite.getAttribute('aria-pressed'),'true');
   assert.equal(await list.locator('p').innerText(),expected);
   assert(await panel.evaluate(node=>node.scrollWidth<=node.clientWidth));
   await page.locator('[data-wish-next]').click();assert(await favorite.isHidden());
   assert.equal(await list.locator('p').innerText(),expected);
   await page.reload();await page.waitForFunction(()=>window.reviewReady);
   await toggle.click();assert.equal(await list.locator('p').innerText(),expected);
   await page.evaluate(()=>review.user(2));await toggle.click();assert.equal(await list.count(),0);
   await page.evaluate(()=>review.user(1));await toggle.click();assert.equal(await list.count(),1);
   await list.getByRole('button').click();assert.equal(await list.count(),0);
   assert.equal(await toggle.evaluate(node=>node===document.activeElement),true);
   assert.deepEqual(await page.evaluate(()=>JSON.parse(localStorage.getItem('fz-daily-wish-favorites-v1-1'))),[]);
  });
  await check('flip a favorite scratch card without changing the daily wish',async()=>{
   await page.setViewportSize({width:320,height:850});
   await page.reload();await page.waitForFunction(()=>window.reviewReady);
   const toggle=page.locator('[data-wish-favorites-toggle]'),flip=page.getByRole('button',{name:'翻一张'});
   const list=page.locator('[data-wish-favorites-list]'),card=page.locator('[data-wish-favorites-card]');
   const mask=page.locator('[data-wish-replay-scratch]'),quote=page.locator('[data-wish-replay-text]');
   const dailyKey='fz-daily-wish-v1-1',favoriteKey='fz-daily-wish-favorites-v1-1';
   const stored=key=>page.evaluate(name=>localStorage.getItem(name),key);
   await toggle.click();assert(await flip.isHidden());
   await page.locator('[data-wish-reveal]').click();
   const daily=await stored(dailyKey),dailyText=await page.locator('[data-wish-text]').innerText();
   await page.evaluate(key=>{
    localStorage.setItem(key,JSON.stringify([2140,2000]));
    window.dispatchEvent(new StorageEvent('storage',{key}));
   },favoriteKey);
   assert(await flip.isVisible());
   const pick=number=>flip.evaluate((button,result)=>{
    const random=Math.random;
    try{Math.random=()=>result;button.click();}finally{Math.random=random;}
   },number);
   await pick(0);
   assert(await list.isHidden());assert(await mask.isVisible());
   assert.equal(await quote.getAttribute('aria-hidden'),'true');
   assert.equal(await quote.innerText(),'希望你今天吃饭的时候，饭还是热的，也有时间慢慢吃。');
   assert(await card.evaluate(node=>node.scrollWidth<=node.clientWidth));
   await mask.scrollIntoViewIfNeeded();
   const box=await mask.boundingBox();
   await page.mouse.move(box.x+12,box.y+12);await page.mouse.down();
   for(let y=12;y<box.height;y+=20){
    await page.mouse.move(box.x+12,box.y+y);
    await page.mouse.move(box.x+box.width-12,box.y+y,{steps:4});
   }
   await page.mouse.up();await mask.waitFor({state:'hidden'});
   assert.equal(await quote.getAttribute('aria-hidden'),null);
   await pick(0.99);
   assert(await mask.isVisible());
   assert.equal(await quote.innerText(),'愿你今天遇到的人，都能给彼此多一点耐心。');
   await page.getByRole('button',{name:'直接查看收藏'}).click();assert(await mask.isHidden());
   await page.getByRole('button',{name:'返回收藏'}).click();
   assert(await card.isHidden());assert(await list.isVisible());
   assert.equal(await stored(dailyKey),daily);
   assert.equal(await stored(favoriteKey),'[2140,2000]');
   assert.equal(await page.locator('[data-wish-text]').innerText(),dailyText);
   assert(await page.locator('[data-wish-scratch]').isHidden());
   await list.getByRole('button').first().click();assert(await flip.isVisible());
   await list.getByRole('button').first().click();assert(await flip.isHidden());
  });
  await check('blocked storage preserves current in-memory card',async()=>{await page.reload();await page.waitForFunction(()=>window.reviewReady);await page.evaluate(()=>{Storage.prototype.getItem=()=>{throw Error('blocked');};Storage.prototype.setItem=()=>{throw Error('blocked');};});await page.locator('[data-wish-reveal]').click();assert(await page.evaluate(()=>{const c=document.querySelector('[data-wish-scratch]');review.mount();return c===document.querySelector('[data-wish-scratch]')&&c.hidden;}));});
  assert.deepEqual(errors,[]);await browser.close();browser=null;
 }
 console.log(JSON.stringify({result:failures.length?'FAIL':'PASS',checks:results,failures},null,2));assert.deepEqual(failures,[]);
}finally{if(browser)await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));}})().catch(error=>{console.error(error.message);process.exitCode=1;});
