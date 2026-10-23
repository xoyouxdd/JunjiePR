// Real isolated API + real FFmpeg media; no production data or service.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),net=require('node:net');
const {spawn}=require('node:child_process');
const {chromium,webkit,firefox}=require('playwright');
const root=path.resolve(__dirname,'../..'),work=fs.mkdtempSync(path.join(os.tmpdir(),'fz-video-ui-'));
const engines=(process.env.VIDEO_TEST_ENGINES||'edge').split(',');
const layoutOnly=process.env.VIDEO_TEST_LAYOUT_ONLY==='1';
const screenshots=process.env.VIDEO_TEST_SCREENSHOTS;
if(screenshots)fs.mkdirSync(screenshots,{recursive:true});
let server,base=process.env.VIDEO_TEST_BASE,serverLog='',browser;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function ready(){for(let i=0;i<120;i++){try{if((await fetch(base+'/health')).ok)return;}catch{}await sleep(500);}throw new Error('Isolated video API failed to start: '+serverLog);}
async function openLibrary(page){
  await page.evaluate(async()=>{const m=JSON.parse(document.querySelector('[type=importmap]').textContent).imports;const s=await import(m['./static/js/app/state.js']);s.state.tab='videoLearning';(await import(m['./static/js/app/navigation.js'])).renderTabs();await(await import(m['./static/js/app/context.js'])).render();});
  await page.locator('.video-card').first().waitFor();
}
async function checkWidth(page,width){
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'page overflow '+width);
  const controls=await page.locator('.video-learning :is(input,select,textarea,button),.modal-card :is(input,select,textarea,button)').evaluateAll(nodes=>nodes.filter(n=>n.getClientRects().length).map(n=>{const r=n.getBoundingClientRect();return {w:r.width,h:r.height,right:r.right,left:r.left};}));
  assert(controls.every(r=>r.left>=-1&&r.right<=width+1),'control overflow '+width);
  const buttons=await page.locator('.video-learning button,.modal-card button').evaluateAll(nodes=>nodes.filter(n=>n.getClientRects().length).map(n=>n.getBoundingClientRect().height));
  assert(buttons.every(h=>h>=43),'undersized button '+width);
  const overlap=await page.locator('.video-settings-grid,.video-search,.video-audience-options').evaluateAll(groups=>groups.some(group=>{const rects=[...group.children].map(n=>n.getBoundingClientRect());return rects.some((a,i)=>rects.slice(i+1).some(b=>Math.min(a.right,b.right)-Math.max(a.left,b.left)>1&&Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>1));}));
  assert(!overlap,'overlapping fields '+width);
}
(async()=>{try{
  if(!base){const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));base='http://127.0.0.1:'+port;
    const python=path.join(root,'backend/.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
    server=spawn(python,[path.join(__dirname,'video_learning_fixture.py'),work,String(port)],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});server.stdout.on('data',b=>serverLog+=b);server.stderr.on('data',b=>serverLog+=b);
  }
  await ready();let states=0;
  for(const engine of engines){
    browser=await ({edge:chromium,webkit,firefox}[engine]).launch({headless:true,...(engine==='edge'?{channel:'msedge'}:{})});
    const context=await browser.newContext({viewport:{width:390,height:844},locale:'zh-CN',reducedMotion:'reduce'});
    const login=await context.request.post(base+'/api/login',{data:{employee_no:'SUPTEST01',password:'fixture2026'}});assert(login.ok());
    const health=await(await context.request.get(base+'/health')).json();await context.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}});
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(base);await page.waitForLoadState('networkidle');await openLibrary(page);
    for(const width of [320,360,390,430,768,1440]){
      await page.setViewportSize({width,height:900});await openLibrary(page);await checkWidth(page,width);states++;
      if(screenshots&&[390,1440].includes(width))await page.screenshot({path:path.join(screenshots,`${engine}-${width}-library.png`),fullPage:true});
      // Tabs stay on one row rather than inheriting global full-width buttons.
      if(width>=360){const tops=await page.locator('.video-tabs button').evaluateAll(a=>a.map(n=>n.getBoundingClientRect().top));assert(Math.max(...tops)-Math.min(...tops)<2);}
    }
    if(layoutOnly){
      await page.setViewportSize({width:390,height:844});await openLibrary(page);
      await page.locator('[data-video-id]').first().click();await page.locator('[data-video-audience]').click();
      for(const width of [320,360,390,430,768,1440]){await page.setViewportSize({width,height:900});await checkWidth(page,width);states++;}
      await page.locator('[data-modal-cancel]').click();await page.locator('.modal-overlay').waitFor({state:'detached'});
      await page.locator('[data-video-back]').click();await page.locator('[data-video-upload]').click();
      for(const width of [320,360,390,430,768,1440]){await page.setViewportSize({width,height:900});await checkWidth(page,width);states++;}
      await context.request.post(base+'/api/logout');assert((await context.request.post(base+'/api/login',{data:{employee_no:'GSMTEST01',password:'fixture2026'}})).ok());
      await context.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}});await page.reload();await page.waitForLoadState('networkidle');await openLibrary(page);
      await page.locator('[data-video-upload]').click();assert.equal(await page.locator('[name=audience]').count(),9);
      for(const width of [320,360,390,430,768,1440]){await page.setViewportSize({width,height:900});await checkWidth(page,width);states++;}
      console.log(engine+': library, audience editor, Lead/senior upload form layout passed; video decoding/upload/playback NOT tested in this mode');await browser.close();browser=null;continue;
    }
    await page.setViewportSize({width:390,height:844});await openLibrary(page);
    await page.locator('[data-video-upload]').click();await page.locator('[name=title]').fill('浏览器实收与封面测试 '+engine);
    assert.equal(await page.locator('[data-video-select-all]').count(),0);
    assert.equal(await page.locator('[name=audience]').count(),1,'Lead only has local TR');assert.equal(await page.locator('[name=audience]').inputValue(),'local_tr');
    await page.locator('[data-open-more]').click();await page.locator('.mobile-more-drawer [data-open-about]').click();
    await page.locator('[data-about-updates]').click();await page.locator('[data-modal-cancel]').click();
    assert.equal(await page.locator('[name=title]').inputValue(),'浏览器实收与封面测试 '+engine);
    await page.locator('[data-about-close]').click();await page.locator('[data-about-dialog]').waitFor({state:'detached'});
    assert((await page.locator('[name=video]').getAttribute('accept')).includes('video/*'));
    const chooserPromise=page.waitForEvent('filechooser');
    await page.locator('[data-video-file-fallback]').click();
    const chooser=await chooserPromise;
    assert.equal(await page.locator('[name=video]').getAttribute('accept'),null);
    await chooser.setFiles(path.join(process.env.VIDEO_TEST_BASE?path.join(root,'output/video-learning-20261021'):work,'sample.mp4'));
    assert((await page.locator('.video-upload-status').innerText()).includes('已选择 sample.mp4'));
    await page.locator('[data-upload-back]').click();await page.locator('[data-modal-cancel]').click();await page.locator('.modal-overlay').waitFor({state:'detached'});
    assert.equal(await page.locator('[name=title]').inputValue(),'浏览器实收与封面测试 '+engine);
    assert.equal(await page.locator('[name=video]').evaluate(n=>n.files.length),1,'Internal back cancel retains the selected native video');
    await page.locator('#videoUploadForm [type=submit]').click();await page.locator('[data-video-start]').waitFor({timeout:90000});
    assert(await page.locator('[data-video-publish]').isVisible());
    await page.locator('[data-video-start]').evaluate(button=>{button.click();button.click();});assert.equal(await page.locator('.video-notice').count(),1);assert(await page.locator('[data-accept]').isDisabled());await page.locator('[data-video-consent]').check();await page.locator('[data-accept]').click();
    await page.waitForFunction(()=>document.querySelector('video')?.readyState>=1,{},{timeout:90000});
    console.log(engine+': waiting for actual playback');
    await page.locator('video').evaluate(v=>{void v.play().catch(()=>{});});
    await page.waitForFunction(()=>{const v=document.querySelector('video');return v&&!v.paused&&v.currentTime>0.1;},{},{timeout:15000});
    assert((await page.locator('.video-employee-watermark').innerText()).includes('SUPTEST01'));
    await page.waitForFunction(()=>Boolean(navigator.serviceWorker.controller));
    const cacheStats=()=>page.evaluate(()=>new Promise(resolve=>{const c=new MessageChannel();c.port1.onmessage=e=>{c.port1.close();resolve(e.data);};navigator.serviceWorker.controller.postMessage({type:'fz-video-stats'},[c.port2]);}));
    const streamURL=await page.locator('video').getAttribute('src');
    assert.equal(await page.evaluate(async url=>(await fetch(url,{headers:{Range:'bytes=0-99'}})).status,streamURL),206);
    const firstStats=await cacheStats();assert(firstStats.bytes>0&&firstStats.bytes<=300*1024*1024);
    assert.equal(await page.evaluate(async url=>(await fetch(url,{headers:{Range:'bytes=0-99'}})).status,streamURL),206);
    assert.equal((await cacheStats()).bytes,firstStats.bytes,'A repeated range reuses the same cached block');
await page.locator('.video-play-settings summary').click();
    if(await page.locator('[data-video-fullscreen]').count()){
      await page.locator('[data-video-fullscreen]').click();
      await page.waitForFunction(()=>document.fullscreenElement?.classList.contains('video-player-wrap'));
      assert(await page.locator('.video-employee-watermark').evaluate(n=>document.fullscreenElement.contains(n)&&!n.hidden));
      await page.evaluate(()=>document.exitFullscreen());await page.waitForFunction(()=>!document.fullscreenElement);
    }

    await page.locator('[data-video-speed]').selectOption('1.5');assert.equal(await page.locator('video').evaluate(v=>v.playbackRate),1.5);
    await page.locator('[data-video-brightness]').fill('120');assert.equal(await page.locator('video').evaluate(v=>v.style.filter),'brightness(120%)');
    if(screenshots)await page.screenshot({path:path.join(screenshots,`${engine}-390-player.png`),fullPage:true});
    await page.locator('video').evaluate(v=>{v.pause();v.currentTime=2;});
    let releaseOldQuality,markOldQuality;
    const oldQualityGate=new Promise(resolve=>releaseOldQuality=resolve),oldQualityStarted=new Promise(resolve=>markOldQuality=resolve);
    const playbackRoute='**/api/video-learning/videos/*/playback?*';
    await page.route(playbackRoute,async route=>{if(route.request().method()==='POST'&&new URL(route.request().url()).searchParams.get('quality')==='1080'){markOldQuality();await oldQualityGate;await route.fulfill({status:503,json:{detail:'隔离旧清晰度请求失败'}});}else await route.continue();});
    const oldQualityResponse=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().includes('/playback?')&&r.status()===503);
    await page.locator('[data-video-quality]').evaluate(select=>{select.value='1080';select.dispatchEvent(new Event('change'));select.value='720';select.dispatchEvent(new Event('change'));});
    await oldQualityStarted;
    await page.waitForFunction(()=>{const v=document.querySelector('video');return v?.readyState>=1&&v.src.includes('quality=720')&&v.currentTime>=1.8;},{},{timeout:90000});
    releaseOldQuality();await oldQualityResponse;await page.waitForTimeout(150);
    assert(await page.locator('.video-player-gate').isHidden(),'An obsolete failed request must not stop current playback');
    assert((await page.locator('video').getAttribute('src')).includes('quality=720'));
    await page.unroute(playbackRoute);
    await page.locator('[data-video-quality]').selectOption('1080');await page.waitForFunction(()=>document.querySelector('video')?.readyState>=1&&document.querySelector('video').src.includes('quality=1080'),{},{timeout:90000});
    for(const width of [320,390,430,768,1440]){await page.setViewportSize({width,height:900});await checkWidth(page,width);states++;}
    await page.locator('[data-video-publish]').evaluate(button=>{button.click();button.click();});assert.equal(await page.locator('[data-modal-confirm]').count(),1);assert((await page.locator('.modal-message').innerText()).includes('本圈 TR'));await page.locator('[data-modal-confirm]').click();await page.locator('[data-video-replace]').waitFor();
    await page.locator('[data-video-back]').click();await page.locator('#videoSearch [name=keyword]').fill('不存在的合成标题');await page.locator('#videoSearch [type=submit]').click();await page.locator('.video-empty').waitFor();
    assert.equal(await page.locator('.video-card').count(),0);
    const listRoute='**/api/video-learning/videos?*';
    await page.route(listRoute,route=>route.fulfill({status:503,json:{detail:'隔离网络故障'}}));
    await page.locator('#videoSearch [type=submit]').click();await page.locator('[data-video-reload]').waitFor();
    await page.unroute(listRoute);await page.locator('[data-video-reload]').click();await page.locator('.video-empty').waitFor();
    // Senior multi-circle selection uses the real mobile form and one upload.
    await context.request.post(base+'/api/logout');assert((await context.request.post(base+'/api/login',{data:{employee_no:'GSMTEST01',password:'fixture2026'}})).ok());
    await context.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}});await page.reload();await page.waitForLoadState('networkidle');await openLibrary(page);
    await page.setViewportSize({width:390,height:844});await page.locator('[data-video-upload]').click();
    const audiences=page.locator('[name=audience]');assert.equal(await audiences.count(),9);
    assert.equal(await page.locator('[name=audience]:checked').count(),0,'Do not preselect all audiences');
    await page.locator('[data-video-select-all]').click();assert.equal(await page.locator('[name=audience]:checked').count(),9);
    await audiences.first().uncheck();assert.equal(await page.locator('[name=audience]:checked').count(),8);
    await page.locator('[data-video-select-all]').click();assert.equal(await page.locator('[name=audience]:checked').count(),9);
    await page.locator('[data-video-select-all]').click();assert.equal(await page.locator('[name=audience]:checked').count(),0);
    await page.locator('[data-video-select-all]').click();
    for(const width of [320,360,390,430,768,1440]){await page.setViewportSize({width,height:900});await checkWidth(page,width);states++;}
    if(screenshots){await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(screenshots,`${engine}-390-multi-upload.png`),fullPage:true});}
    const sharedTitle='多圈单份上传 '+engine;await page.locator('[name=title]').fill(sharedTitle);
    await page.locator('[name=video]').setInputFiles(path.join(process.env.VIDEO_TEST_BASE?path.join(root,'output/video-learning-20261021'):work,'sample.mp4'));
    await page.locator('#videoUploadForm [type=submit]').click();await page.locator('[data-video-publish]:not([disabled])').waitFor({timeout:90000});
    await page.locator('[data-video-publish]').click();assert((await page.locator('.modal-message').innerText()).includes('所有 GSM'));await page.locator('[data-modal-confirm]').click();await page.locator('[data-video-replace]').waitFor();
    const shared=await(await context.request.get(base+'/api/video-learning/videos?keyword='+encodeURIComponent(sharedTitle))).json();
    assert.equal(shared.total,1);assert.equal(shared.items[0].audience.codes.length,9);
    const detailRoute=base+'/api/video-learning/videos/'+shared.items[0].id;
    let detailReads=0;
    await page.route(detailRoute,async route=>{detailReads++;const response=await route.fetch();const data=await response.json();await route.fulfill({json:{...data,cover_status:'queued'}});});
    await page.locator('[data-video-back]').click();await page.locator('[data-video-id="'+shared.items[0].id+'"]').click();
    await page.locator('[data-video-audience]').click();
    await page.locator('.form-modal [name=audience][value=all_tr]').uncheck();
    await page.locator('.form-modal [name=audience][value=local_tr]').uncheck();
    const readsBefore=detailReads;
    await page.waitForTimeout(5500);
    assert.equal(detailReads,readsBefore,'Background refresh must not replace a view under an open editor');
    assert.equal(await page.locator('.form-modal [name=audience]:checked').count(),7);
    for(const width of [320,360,390,430,768,1440]){await page.setViewportSize({width,height:900});await checkWidth(page,width);states++;}
    if(screenshots){await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(screenshots,`${engine}-390-audience-editor.png`),fullPage:true});}
    await page.unroute(detailRoute);
    await page.locator('.form-modal [type=submit]').click();await page.locator('.modal-overlay').waitFor({state:'detached'});
    await page.locator('[data-video-audience]').waitFor();
    const edited=await(await context.request.get(base+'/api/video-learning/videos/'+shared.items[0].id)).json();
    assert.equal(edited.audience.codes.length,7);assert.equal(edited.audience.revision,2);
    await page.locator('[data-video-replace]').click();assert.equal(await page.locator('[name=audience]:checked').count(),7);
    assert.equal(await page.locator('[name=audience]:disabled').count(),0,'Publisher may revise replacement audience');
    assert.equal(await page.locator('[data-video-select-all]').count(),1);
    // Same real navigation and module under an ordinary account: no upload UI.
    await context.request.post(base+'/api/logout');assert((await context.request.post(base+'/api/login',{data:{employee_no:'CMTEST01',password:'fixture2026'}})).ok());
    await context.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}});await page.reload();await page.waitForLoadState('networkidle');
    await page.evaluate(async()=>{const m=JSON.parse(document.querySelector('[type=importmap]').textContent).imports;const s=await import(m['./static/js/app/state.js']);s.state.tab='videoLearning';(await import(m['./static/js/app/navigation.js'])).renderTabs();await(await import(m['./static/js/app/context.js'])).render();});
    await page.locator('#videoSearch').waitFor();assert.equal(await page.locator('[data-video-upload]').count(),0);assert.equal(errors.length,0,errors.join('\n'));
    // CM is allowed by everyone, never granted upload. Cache cannot bypass live policy.
    await page.locator('#videoSearch [name=keyword]').fill(sharedTitle);await page.locator('#videoSearch [type=submit]').click();
    await page.locator('[data-video-id="'+shared.items[0].id+'"]').click();
    await page.locator('[data-video-start]').waitFor({timeout:8000}).catch(async e=>{console.log('CM detail diagnostic:',await page.locator('#app').innerText());throw e;});
    await page.locator('[data-video-start]').click();await page.locator('[data-video-consent]').check();await page.locator('[data-accept]').click();
    await page.waitForFunction(()=>document.querySelector('video')?.readyState>=1,{},{timeout:15000});
    const cmURL=await page.locator('video').getAttribute('src');
    assert.equal(await page.evaluate(async url=>(await fetch(url,{headers:{Range:'bytes=0-99'}})).status,cmURL),206);
    assert((await cacheStats()).bytes>0);
    await context.setOffline(true);await page.evaluate(()=>window.dispatchEvent(new Event('offline')));
    await cacheStats(); // Acknowledge earlier messages before the subsequent range request.
    assert.equal(await page.evaluate(async url=>{try{return (await fetch(url,{headers:{Range:'bytes=0-99'}})).status;}catch(_){return 503;}},cmURL),503);
    assert(await page.locator('video').evaluate(v=>v.paused&&!v.getAttribute('src')));
    await context.setOffline(false);await page.evaluate(()=>window.dispatchEvent(new Event('online')));await cacheStats();
    const ownerContext=await browser.newContext();
    try{
      assert((await ownerContext.request.post(base+'/api/login',{data:{employee_no:'GSMTEST01',password:'fixture2026'}})).ok());
      assert((await ownerContext.request.post(base+'/api/video-learning/videos/'+shared.items[0].id+'/withdraw')).ok());
    }finally{await ownerContext.close();}
    assert.equal(await page.evaluate(async url=>(await fetch(url,{headers:{Range:'bytes=0-99'}})).status,cmURL),404);
    assert.equal((await cacheStats()).bytes,0,'A denied request purges cached bytes');
    await context.request.post(base+'/api/logout');await context.request.post(base+'/api/login',{data:{employee_no:'GSMTEST01',password:'fixture2026'}});
    await page.reload();await page.waitForLoadState('networkidle');await openLibrary(page);await page.locator('[data-video-tab=mine]').click();
    await page.locator('[data-video-id="'+shared.items[0].id+'"]').click();await page.locator('[data-video-delete]').click();
    await page.locator('[data-modal-confirm]').click();await page.locator('.modal-message').filter({hasText:'确认永久删除此版本'}).waitFor();
    await page.locator('[data-modal-confirm]').click();await page.locator('#videoSearch').waitFor();
    assert.equal((await context.request.get(base+'/api/video-learning/videos/'+shared.items[0].id)).status(),404);
    assert.equal(errors.length,0,errors.join('\n'));


    if(process.env.VIDEO_TEST_CACHE_POLICY==='1'&&engine==='edge'){
      const worker=context.serviceWorkers()[0];assert(worker,'Cache worker is active');
      const policy=await worker.evaluate(async()=>{
        await clear();await maintain('isolated-policy-test');const seen=epoch;
        const data=new Uint8Array(1024*1024).buffer;
        for(let n=0;n<301;n++)await write('policy:'+n,data,seen);
        const oldest=await read('policy:0'),newest=await read('policy:300');
        const db=await database();const usage=await request(db.transaction('meta').objectStore('meta').get('bytes'));db.close();
        await transact(['blocks'],tx=>{const store=tx.objectStore('blocks'),r=store.get('policy:300');r.onsuccess=()=>{const row=r.result;if(row){row.used=Date.now()-8*86400000;store.put(row);}};});
        await maintain('isolated-policy-test');const expired=await read('policy:300');await clear();
        return {oldest:oldest===null,newest:Boolean(newest),bytes:usage,expired:expired===null};
      });
      assert.deepEqual(policy,{oldest:true,newest:true,bytes:300*1024*1024,expired:true});
      console.log(engine+': actual 301MiB IndexedDB writes, 300MiB LRU and 7-day expiry passed');
      const available=(await(await context.request.get(base+'/api/video-learning/videos')).json()).items[0];assert(available);
      const opts=await(await context.request.get(base+'/api/video-learning/options')).json();
      const grant=await(await context.request.post(base+'/api/video-learning/videos/'+available.id+'/consent',{data:{accepted:true,notice_version:opts.notice_version}})).json();
      const fallbackURL=base+'/api/video-learning/videos/'+available.id+'/stream?token='+encodeURIComponent(grant.token)+'&quality=720';
      await worker.evaluate(()=>{self.fzSavedOpen=indexedDB.open;indexedDB.open=()=>{throw new DOMException('cache disabled','QuotaExceededError');};});
      try{assert.equal(await page.evaluate(async url=>(await fetch(url,{headers:{Range:'bytes=0-99'}})).status,fallbackURL),206);}
      finally{await worker.evaluate(()=>{indexedDB.open=self.fzSavedOpen;delete self.fzSavedOpen;});}
      await worker.evaluate(async()=>{await maintain('isolated-switch-test');await write('switch',new Uint8Array(100).buffer,epoch);});
      assert((await cacheStats()).bytes>0);
      assert.equal(await page.evaluate(async base=>(await fetch(base+'/api/logout',{method:'POST'})).status,base),200);
      assert.equal((await cacheStats()).bytes,0,'Logout clears cached blocks');
      console.log(engine+': quota failure streams normally; real logout clears device cache');

    }
    console.log(engine+': real upload, processing, consent, private preview, settings, quality change, publish, search, role UI passed');
    await browser.close();browser=null;
  }
  console.log(JSON.stringify({status:'passed',engines,layoutOnly,layoutStates:states}));
}finally{if(browser)await browser.close();server?.kill();}})().catch(e=>{console.error(e);process.exitCode=1});
