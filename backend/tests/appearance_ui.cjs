// Real isolated FastAPI fixture: skin switching must preserve business DOM and make no API calls.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),net=require('node:net'),crypto=require('node:crypto'),zlib=require('node:zlib');
const assert=require('node:assert/strict'),{spawn}=require('node:child_process');
const {chromium,webkit,firefox}=require('./support/browser-runtime.cjs');
const backend=path.resolve(__dirname,'..'),output=path.resolve(backend,'../output/appearance-preview');
fs.mkdirSync(output,{recursive:true});
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function freePort(){const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));return port;}
const engines=process.env.APPEARANCE_ENGINES?.split(',')||['chromium','webkit','firefox'];
(async()=>{
 const port=await freePort(),base='http://127.0.0.1:'+port,password='Fixture-'+crypto.randomBytes(16).toString('hex');
 const data=fs.mkdtempSync(path.join(os.tmpdir(),'fz-appearance-'));
 const service=spawn(path.join(backend,'.venv/Scripts/python.exe'),['-m','uvicorn','app.main:app','--host','127.0.0.1','--port',String(port)],{cwd:backend,windowsHide:true,env:{...process.env,RECOGNITION_ENVIRONMENT:'test',RECOGNITION_V2_DATA_DIR:data,RECOGNITION_BACKUP_HEALTH_STATUS_FILE:path.join(data,'backups/backup-health-status.json'),RECOGNITION_ENABLE_TEST_ACCOUNTS:'1',RECOGNITION_TEST_DEFAULT_PASSWORD:password,RECOGNITION_TEST_ADMIN_PASSWORD:password}});
 let log='',browser;service.stderr.on('data',c=>log=(log+c).slice(-8000));service.stdout.on('data',()=>{});
 const checks=[],errors=[];
 try{
  let ready=false;for(let i=0;i<125;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(service.exitCode!==null)throw Error(log);await sleep(200);}assert(ready,'Fixture startup failed: '+log);
  for(const engine of engines){
   console.log('Appearance checks: '+engine);
   browser=await({chromium,webkit,firefox}[engine]).launch(engine==='chromium'?{channel:'msedge',headless:true}:{headless:true});
   async function login(account){
    const context=await browser.newContext({viewport:{width:1440,height:1000}});
    // Isolate appearance's zero-request contract from the independent minute
    // badge ticker. All API requests from the tested controls are still counted.
    await context.addInitScript(()=>{
      const schedule=window.setInterval.bind(window);
      window.setInterval=(callback,delay,...args)=>schedule(delay===60000?()=>{}:callback,delay,...args);
    });
    const page=await context.newPage();
    page.on('pageerror',e=>errors.push(engine+': '+e.message));
    page.on('response',r=>{if(r.url().includes('/api/')&&r.status()>=500)errors.push(engine+': API '+r.status()+' '+r.url());});
    await page.goto(base+'/login');await page.locator('[name=employee_no]').fill(account);await page.locator('[name=password]').fill(password);await page.locator('#loginForm button[type=submit]').click();await page.waitForURL(base+'/');
    await page.locator('#appearanceBtn').waitFor();
    const notice=page.locator('[data-announcement-close]');
    try{await notice.waitFor({timeout:2500});await page.waitForFunction(()=>!document.querySelector('[data-announcement-close]').disabled);await notice.click();}catch(error){if(error.name!=='TimeoutError')throw error;}
    return {context,page};
   }
   const {context,page}=await login('CMTEST02');
   await page.locator('#tabs [data-tab=register]:visible').click();await page.locator('#app form').first().waitFor();
   const draft=page.locator('#app input[name=content]').first();await draft.waitFor();await draft.fill('皮肤切换保留草稿');
   // Freeze the baseline only after the independent recognizer request settles.
   // Otherwise a successful late response looks like a skin-induced DOM change.
   await page.waitForFunction(()=>document.querySelector('[data-batch-recognizer]')?.getAttribute('aria-busy')!=='true'&&[...document.querySelectorAll('[name=recognizer_employee_id] option')].some(option=>option.value));
   await page.evaluate(()=>{window.skinOriginalForm=document.querySelector('#app form');});
   await page.locator('#appearanceBtn[data-avatar-settings]').click();await page.locator('[data-skin-choice=blue]').waitFor();
   let requests=[];page.on('request',r=>{if(r.url().includes('/api/'))requests.push(r.url());});
   for(const skin of ['blue','green','ivory','medal','circle']){
    const before=await page.evaluate(()=>({circle:document.body.dataset.circleTheme,status:getComputedStyle(document.body).getPropertyValue('--danger'),html:document.querySelector('#app').innerHTML}));
    const count=requests.length;
    await page.locator(`[data-skin-choice=${skin}]`).click();
    assert.equal(await page.getAttribute('html','data-skin'),skin);
    assert.equal(await page.locator(`[data-skin-choice=${skin}]`).getAttribute('aria-pressed'),'true');
    await sleep(100);assert.equal(requests.length,count,'Switching skin must make zero API requests');
    const after=await page.evaluate(()=>({circle:document.body.dataset.circleTheme,status:getComputedStyle(document.body).getPropertyValue('--danger'),html:document.querySelector('#app').innerHTML}));
    assert.deepEqual(after,before,'Skin must preserve business DOM, circle identity and danger colors');
    assert(await page.evaluate(()=>window.skinOriginalForm===document.querySelector('#app form')),'Original form DOM must survive');
    assert.equal(await draft.inputValue(),'皮肤切换保留草稿');
    if(process.env.FZ_AXE_PATH){
     if(!await page.evaluate(()=>!!window.axe)){await page.route('**/static/js/axe-audit.js',route=>route.fulfill({contentType:'text/javascript',body:fs.readFileSync(process.env.FZ_AXE_PATH,'utf8')}));await page.addScriptTag({url:base+'/static/js/axe-audit.js'});}
     const audit=await page.evaluate(()=>window.axe.run(document.querySelector('.appearance-section'),{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}));
     assert.deepEqual(audit.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)})),[],'New appearance chooser accessibility');
    }
    for(const width of [320,390,768,1440]){
     await page.setViewportSize({width,height:1000});
     assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),'No page overflow at '+width);
     const header=await page.locator('.app-topbar').boundingBox();
     if(width<=760){assert(header.height<=65,'Phone header remains a single 64px row');assert(!await page.locator('.identity-meta').isVisible());assert(!await page.locator('.identity-employee-no').isVisible());}
     else{const identity=await page.locator('.app-identity').boundingBox();assert(Math.abs(identity.x+identity.width/2-header.x-header.width/2)<2,'Desktop identity is centered');}
     const bounds=await page.locator('[data-avatar-dialog]').boundingBox();assert(bounds.x>=0&&bounds.x+bounds.width<=width+1);
     if(engine==='chromium'&&[390,1440].includes(width))await page.screenshot({path:path.join(output,`${skin}-picker-${width}.png`),fullPage:true});
    }
   }
   const badgeBefore=await page.locator('#userBadge').innerHTML();
   await page.locator('.appearance-artwork > summary').click();await page.locator('[data-artwork-group]').waitFor();
   const expectedKeys=JSON.parse(fs.readFileSync(path.join(backend,'app/static/images/avatars/catalog.json'),'utf8')).avatars.map(item=>'avatar:'+item.key).sort(),seenKeys=new Set();
   await page.locator('[data-artwork-group]').selectOption('');
   for(let current=0;current<Math.ceil(expectedKeys.length/8);current++){for(const key of await page.locator('.appearance-artwork-grid [data-artwork-choice]').evaluateAll(nodes=>nodes.map(node=>node.dataset.artworkChoice)))seenKeys.add(key);if(current<Math.ceil(expectedKeys.length/8)-1)await page.locator('[data-artwork-next]').click();}
   assert.deepEqual([...seenKeys].sort(),expectedKeys,'All catalog artwork remains selectable');
   await page.locator('[data-artwork-group]').selectOption({label:'疯狂动物城 · 手绘新造型'});
   const beforeArtworkRequests=requests.length;
   await page.locator('[data-artwork-choice="avatar:new-zoo-clawhauser"]').click();
   assert.equal(await page.evaluate(()=>window.FZAppearance.getArtwork()),'avatar:new-zoo-clawhauser');
   assert.equal(await page.getAttribute('html','data-header-artwork'),'portrait');
   const chosenSkin=await page.getAttribute('html','data-skin');
   assert.equal(chosenSkin,'circle');assert.equal(await page.locator('#userBadge').innerHTML(),badgeBefore);
   await page.locator('[data-skin-choice=medal]').click();assert.equal(await page.evaluate(()=>window.FZAppearance.getArtwork()),'avatar:new-zoo-clawhauser');
   assert(await page.locator('.skin-portrait-front').evaluate(e=>getComputedStyle(e).backgroundImage.includes('new-zoo-clawhauser')));
   assert.equal(requests.length,beforeArtworkRequests,'Art and skin selection must make zero business API calls: '+requests.slice(beforeArtworkRequests).join(', '));
   if(engine==='chromium')await page.screenshot({path:path.join(output,'artwork-selector.png'),fullPage:true});
   await page.keyboard.press('Escape');await page.locator('[data-avatar-dialog]').waitFor({state:'detached'});await page.reload();await page.locator('#appearanceBtn').waitFor();
   assert.equal(await page.evaluate(()=>window.FZAppearance.getArtwork()),'avatar:new-zoo-clawhauser');
   await page.locator('#appearanceBtn[data-avatar-settings]').click();await page.locator('.appearance-artwork > summary').click();
   await page.locator('[data-artwork-choice=medal]').click();assert.equal(await page.getAttribute('html','data-header-artwork'),'medal');
   await page.locator('[data-skin-choice=circle]').click();assert(await page.locator('.skin-medal-art').isVisible());
   await page.locator('[data-artwork-choice=theme]').click();assert.equal(await page.getAttribute('html','data-header-artwork'),'theme');
   for(const width of [320,390,768,1440]){await page.setViewportSize({width,height:1000});assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth));}
   await page.locator('[data-skin-reset]').click();assert.equal(await page.getAttribute('html','data-skin'),'blue');
   await page.keyboard.press('Escape');await page.locator('[data-avatar-dialog]').waitFor({state:'detached'});
   assert(await page.locator('#appearanceBtn[data-avatar-settings]').evaluate(e=>e===document.activeElement));
   assert.equal(await page.locator('[data-daily-wish]').count(),1);
   const scratch=page.locator('#themeStory [data-wish-scratch]');await scratch.scrollIntoViewIfNeeded();const scratchBounds=await scratch.boundingBox(),wishRequests=requests.length;
   for(let y=12;y<scratchBounds.height;y+=18){await page.mouse.move(scratchBounds.x+8,scratchBounds.y+y);await page.mouse.down();await page.mouse.move(scratchBounds.x+scratchBounds.width-8,scratchBounds.y+y,{steps:8});await page.mouse.up();if(await scratch.isHidden())break;}
   if(!await scratch.isHidden()){await page.screenshot({path:path.join(output,'scratch-failure.png')});console.log(await scratch.evaluate(canvas=>{const r=canvas.getBoundingClientRect(),pixels=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;let blank=0,count=0;for(let i=3;i<pixels.length;i+=64){count++;if(pixels[i]<64)blank++;}return {rect:{x:r.x,y:r.y,w:r.width,h:r.height},alphaBlank:blank/count,hit:document.elementFromPoint(r.x+r.width/2,r.y+12)?.outerHTML.slice(0,160),inert:!!canvas.closest('[inert]')};}));}
   assert(await scratch.isHidden(),'Dragging really reveals the merged scratch card');
   const firstWish=await page.locator('#themeStory [data-wish-text]').innerText();await page.locator('#themeStory [data-wish-next]').click();assert.notEqual(await page.locator('#themeStory [data-wish-text]').innerText(),firstWish);assert(await scratch.isVisible());await page.locator('#themeStory [data-wish-reveal]').click();assert(await scratch.isHidden());assert.equal(requests.length,wishRequests,'Merged scratch card adds no API calls');
   await page.evaluate(()=>{document.querySelector('.app-shell').scrollTop=80;window.FZAppearance.set('green');});
   const scroll=await page.locator('.app-shell').evaluate(e=>e.scrollTop);await page.evaluate(()=>window.FZAppearance.set('ivory'));assert.equal(await page.locator('.app-shell').evaluate(e=>e.scrollTop),scroll);
   await page.reload();await page.locator('#appearanceBtn').waitFor();assert.equal(await page.getAttribute('html','data-skin'),'ivory');
   const second=await context.newPage();await second.goto(base+'/');await second.locator('#appearanceBtn').waitFor();
   await page.evaluate(()=>{window.FZAppearance.set('green');window.FZAppearance.setArtwork('avatar:character-white-rabbit');});await second.waitForFunction(()=>document.documentElement.dataset.skin==='green'&&window.FZAppearance.getArtwork()==='avatar:character-white-rabbit');await second.close();await page.evaluate(()=>window.FZAppearance.setArtwork('theme'));
   await page.setViewportSize({width:390,height:1000});await page.locator('[data-open-more]').click();await page.locator('.mobile-identity-card [data-avatar-settings]').click();await page.locator('[data-skin-choice=ivory]').click();await page.keyboard.press('Escape');await page.locator('[data-avatar-dialog]').waitFor({state:'detached'});
   await page.setViewportSize({width:1440,height:1000});await page.locator('#tabs [data-tab=actionCenter]:visible').click();await page.locator('.action-center-page').waitFor();
   if(engine==='chromium'){
    for(const skin of ['blue','green','ivory','medal','circle']){await page.evaluate(s=>window.FZAppearance.set(s),skin);await page.screenshot({path:path.join(output,skin+'-desktop.png'),fullPage:true});}
    for(const circle of ['heat','dwarf','bear','all']){
     await page.evaluate(c=>{window.FZAppearance.set('circle');document.body.dataset.circleTheme=c;},circle);
     const expectedArt={heat:'new-zoo-clawhauser',dwarf:'character-white-rabbit',bear:'base-04-01',all:'new-zoo-clawhauser'};
     assert(await page.locator('.skin-portrait-front').evaluate((node,key)=>getComputedStyle(node).backgroundImage.includes(key),expectedArt[circle]),'Matching circle artwork');
     assert.equal(await page.locator('[data-daily-wish]').count(),1,'A single merged wish card');
     for(const width of [320,390,768,1440]){await page.setViewportSize({width,height:1000});assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth));if([390,1440].includes(width))await page.screenshot({path:path.join(output,`circle-${circle}-${width}.png`),fullPage:true});}
    }
    await page.evaluate(()=>document.body.dataset.circleTheme='heat');await page.setViewportSize({width:1440,height:1000});
    await exportPreview(page,'index.html');
    await page.locator('#tabs [data-tab=register]:visible').click();await page.locator('#app form').first().waitFor();await exportPreview(page,'register.html');
   }
   // Skin preferences remain usable even where browser storage is disabled.
   const blocked=await browser.newContext();await blocked.addInitScript(()=>{Storage.prototype.getItem=()=>{throw Error('blocked');};Storage.prototype.setItem=()=>{throw Error('blocked');};});
   const blockedPage=await blocked.newPage();await blockedPage.goto(base+'/login');
   await blockedPage.addScriptTag({url:base+'/static/js/appearance-boot.js'});assert.equal(await blockedPage.evaluate(()=>window.FZAppearance.set('green')),false);assert.equal(await blockedPage.evaluate(()=>window.FZAppearance.setArtwork('avatar:new-zoo-clawhauser')),false);assert.equal(await blockedPage.getAttribute('html','data-skin'),'green');await blocked.close();
   const admin=await login('HR01');await admin.page.locator('#tabs [data-tab=hrEmployees]:visible').click();await admin.page.locator('[data-hr-person]').first().waitFor({state:'attached'});
   await admin.page.locator('#appearanceBtn[data-avatar-settings]').click();await admin.page.locator('[data-skin-choice=green]').click();await admin.page.keyboard.press('Escape');await admin.page.locator('[data-avatar-dialog]').waitFor({state:'detached'});
   if(engine==='chromium'){await admin.page.screenshot({path:path.join(output,'green-hr-desktop.png'),fullPage:true});await exportPreview(admin.page,'employees.html');}
   await admin.page.locator('#appearanceBtn').click();await admin.page.locator('[data-appearance-logout]').click();await admin.page.waitForURL(base+'/login');assert.equal((await admin.context.request.get(base+'/api/me')).status(),401,'Logout remains available in personal appearance');
   checks.push({engine,result:'PASS',checks:['5 skins including medal/circle collections','64px single-row phone header','centered desktop identity','complete artwork catalog','real merged scratch drag/reveal/reset','zero switch and scratch API requests','draft/DOM/scroll preserved','identity/severity unchanged','320/390/768/1440px','reload and cross-tab persistence','mobile identity entry','Escape and focus return','storage blocked fallback','HR employee page','logout via personal appearance']});
   await browser.close();browser=null;
  }
  assert.deepEqual(errors,[]);
  const files=['css/appearance.css','js/appearance-boot.js','js/app/appearance.js','images/appearance-honors.svg'];const bytes=files.reduce((n,f)=>n+zlib.gzipSync(fs.readFileSync(path.join(backend,'app/static',f))).length,0);assert(bytes<20*1024);
  const report={result:'PASS',fixture:'new isolated SQLite; no production or local user data',gzipBytes:bytes,checks};fs.writeFileSync(path.join(output,'result.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 }finally{if(browser)await browser.close();service.kill();await new Promise(r=>{if(service.exitCode!==null)r();else{service.once('exit',r);setTimeout(r,5000);}});}
})().catch(e=>{console.error(e);process.exitCode=1;});

async function exportPreview(page,name){
 const html=await page.evaluate(()=>{const clone=document.documentElement.cloneNode(true);clone.querySelectorAll('script,.screen-watermark,.environment-banner,[data-runtime-environment]').forEach(el=>{if(el.tagName==='BODY')el.removeAttribute('data-runtime-environment');else el.remove();});clone.querySelectorAll('[src],[href]').forEach(el=>{for(const attr of ['src','href']){const value=el.getAttribute(attr);if(value?.startsWith('static/')||value?.startsWith('/static/'))el.setAttribute(attr,'/backend/app/'+value.replace(/^\//,''));}});return '<!doctype html>'+clone.outerHTML;});
 const head='<script src="/backend/app/static/js/appearance-boot.js"></script><script type="module" src="preview.js"></script><link rel="stylesheet" href="preview.css">';
 const bar='<aside class="preview-bar"><strong>FZ · 本地 UI 预览</strong><span>演示数据 · 仅外观预览</span><nav><a href="index.html">待办</a><a href="register.html">登记</a><a href="employees.html">员工管理</a><label class="preview-circle-label">景点圈演示 <select data-preview-circle aria-label="景点圈演示"><option value="heat">热力追踪</option><option value="dwarf">矮人迷宫</option><option value="bear">小熊罐子</option><option value="all">伙伴集结</option></select></label></nav></aside>';
 fs.writeFileSync(path.join(output,name),html.replace('</head>',head+'</head>').replace(/<body([^>]*)>/,'<body$1>'+bar));
 fs.writeFileSync(path.join(output,'preview.js'),fs.readFileSync(path.join(backend,'../scripts/appearance-preview.js'),'utf8'));
 fs.writeFileSync(path.join(output,'preview.css'),'.preview-bar{position:fixed;inset:0 0 auto;z-index:2000;height:58px;display:flex;align-items:center;gap:16px;padding:0 20px;background:#fffefa;border-bottom:1px solid #dce5f0;color:#183451;font:14px system-ui}.preview-bar span{font-size:12px;color:#526274}.preview-circle-label{display:flex;align-items:center;gap:5px;font-size:11px;white-space:nowrap}.preview-bar select{width:auto;max-width:120px;min-height:32px;padding:4px;border:1px solid #dce5f0;border-radius:6px;font-size:12px}.preview-bar nav{display:flex;align-items:center;gap:14px;margin-left:auto}.preview-bar a{color:#245a9b;text-decoration:none}.preview-bar button{width:auto;min-height:34px;padding:6px 12px;background:#183451;color:white;border:0;border-radius:7px}html body{padding-top:58px;box-sizing:border-box}body .app-frame{height:calc(100dvh - 58px);min-height:0}@media(max-width:760px){.preview-bar{height:86px;flex-wrap:wrap;gap:5px 12px;padding:8px 12px}.preview-bar nav{width:100%;margin:0;font-size:12px;gap:8px;justify-content:space-between}.preview-circle-label{font-size:0}.preview-bar select{max-width:90px;font-size:11px}.preview-bar button{padding:6px 8px;font-size:11px}.preview-bar span{font-size:10px}html body{padding-top:86px}body .app-frame{height:auto}}');
}
