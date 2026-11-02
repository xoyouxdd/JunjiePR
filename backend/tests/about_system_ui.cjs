// Real about dialog, modal lifecycle and product styles; no live accounts or writes.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../app/static');
const navigation=fs.readFileSync(path.join(root,'js/app/navigation.js'),'utf8');
const about=navigation.slice(navigation.indexOf('function showAboutSystem('),navigation.indexOf('\nfunction renderTabs()'));
const dialogSource=fs.readFileSync(path.join(root,'js/app/dialogs.js'),'utf8');
const dialogs=dialogSource.slice(dialogSource.indexOf('function isNativePickerControl('),dialogSource.indexOf('\nfunction confirmModal('));
const css=['style.css','workspace-ui.css','appearance.css'].map(f=>fs.readFileSync(path.join(root,'css',f),'utf8')).join('\n');
const logo='data:image/png;base64,'+fs.readFileSync(path.join(root,'images/fzpr-icon.png')).toString('base64');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
for(const width of [320,390,1280]){
 const page=await browser.newPage({viewport:{width,height:850},reducedMotion:'reduce'}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.setContent('<style>'+css+'</style><div class="app-frame"><img class="app-brand-logo" src="'+logo+'" width="30" height="30"><div style="position:relative;z-index:2">今日小祝福</div><nav id="tabs"></nav><button id="trigger">关于系统</button></div>');
 await page.evaluate('(()=>{\n'+dialogs+'\n'+about+`\n
  window.credits={maintainers:'赵君杰、张森',feedback:'',revision:0,can_edit:false};window.failSave=false;window.readFail=false;window.deferSave=false;window.saveCount=0;window.destination='';
  window.state={me:{role_code:'SYSTEM_ADMIN',employee_no:'HR01'}};
  window.tabs=document.getElementById('tabs');
  window.esc=v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
  window.prefersReducedMotion=()=>true;
  window.json=(method,body)=>({method,body:JSON.stringify(body)});
  window.gotoTab=tab=>window.destination=tab;
  window.api=async(url,options={})=>{
   if(url==='/api/changelog')return {app_version:'2026.11.01.1'};
   if(window.readFail&&!options.method)throw new Error('读取失败');
   if(options.method==='PUT'){
    window.saveCount++;if(window.deferSave)await new Promise(resolve=>window.finishSave=resolve);
    if(window.failSave)throw new Error('仅ADMIN账号可修改关于系统名单');
    const body=JSON.parse(options.body);window.credits={...window.credits,maintainers:body.maintainers,feedback:body.feedback,revision:body.revision+1};
   }
   return {...window.credits};
  };
  document.getElementById('trigger').onclick=()=>showAboutSystem(document.getElementById('trigger'));
 })();`);
 const open=async()=>{await page.locator('#trigger').click();await page.locator('[data-about-maintainers]').filter({hasText:'张森'}).waitFor();await page.locator('[data-about-dialog]').evaluate(async el=>{await Promise.all(el.getAnimations({subtree:true}).map(animation=>animation.finished));});};
 await open();
 assert.equal(await page.locator('.about-system-credits dt').allTextContents().then(x=>x.join('|')),'建设与维护|建议与反馈');
 assert.equal(await page.locator('[data-about-feedback]').textContent(),'—');
 assert(await page.locator('[data-about-maintainers]').evaluate(el=>Number(getComputedStyle(el).fontWeight)>=600));
 assert(!(await page.locator('[data-about-edit]').isVisible()),'HR01 must have no editor');
 const geometry=await page.evaluate(()=>{const header=document.querySelector('.about-system-heading'),title=header.querySelector('h3').getBoundingClientRect(),close=header.querySelector('button').getBoundingClientRect(),modal=document.querySelector('[data-about-dialog]').getBoundingClientRect();return {titleHeight:title.height,closeWidth:close.width,overlap:title.right>close.left,overflow:document.documentElement.scrollWidth>innerWidth,top:document.elementFromPoint(modal.left+10,modal.top+10).closest('[data-about-dialog]')!==null};});
 assert(geometry.titleHeight<32&&!geometry.overlap&&!geometry.overflow&&geometry.top);
 assert.equal(geometry.closeWidth,44);
 assert(await page.locator('.about-system-brand img').evaluate(el=>el.src===document.querySelector('.app-brand-logo').src));
 await page.keyboard.press('Escape');assert.equal(await page.locator('[data-about-dialog]').count(),0);
 assert(await page.locator('#trigger').evaluate(el=>el===document.activeElement));
 await page.evaluate(()=>credits.can_edit=true);await open();
 await page.locator('[data-about-edit]').click();
 await page.locator('#aboutMaintainers').fill('未保存');await page.locator('#aboutFeedback').fill('未保存建议人');await page.locator('[data-about-cancel]').click();
 assert.equal(await page.evaluate(()=>saveCount),0);
 await page.locator('[data-about-edit]').click();assert.equal(await page.locator('#aboutMaintainers').inputValue(),'赵君杰、张森');
 assert.equal(await page.locator('#aboutFeedback').inputValue(),'');
 await page.locator('#aboutMaintainers').fill('<img src=x onerror=alert(1)>\n李晓');
 await page.locator('#aboutFeedback').fill('<b>建议人甲</b>\n建议人乙');
 await page.locator('[data-about-form] [type=submit]').click();await page.locator('[data-about-status]').filter({hasText:'已保存'}).waitFor();
 assert.equal(await page.locator('[data-about-maintainers] img').count(),0,'names must be plain text');
 assert.equal(await page.locator('[data-about-maintainers]').textContent(),'<img src=x onerror=alert(1)>\n李晓');
 assert.equal(await page.locator('[data-about-feedback]').textContent(),'<b>建议人甲</b>\n建议人乙');
 assert.equal(await page.locator('[data-about-feedback] b').count(),0);
 await page.locator('[data-about-edit]').click();await page.locator('#aboutMaintainers').fill('保留输入');
 await page.evaluate(()=>failSave=true);await page.locator('[data-about-form] [type=submit]').click();
 await page.locator('[data-about-status]').filter({hasText:'仅ADMIN'}).waitFor();
 assert.equal(await page.locator('#aboutMaintainers').inputValue(),'保留输入');assert(await page.locator('[data-about-form]').isVisible());
 await page.evaluate(()=>{failSave=false;deferSave=true;});await page.locator('[data-about-form] [type=submit]').click();
 assert(await page.locator('[data-about-close]').isDisabled());await page.keyboard.press('Escape');assert.equal(await page.locator('[data-about-dialog]').count(),1);
 await page.evaluate(()=>finishSave());await page.locator('[data-about-status]').filter({hasText:'已保存'}).waitFor();
 await page.locator('[data-about-updates]').click();assert.equal(await page.evaluate(()=>destination),'changelog');
 assert.equal(await page.locator('[data-about-dialog]').count(),0);assert.equal(await page.evaluate(()=>document.body.style.overflow),'');
 await page.evaluate(()=>{credits.maintainers='赵君杰、张森';readFail=true;});await page.locator('#trigger').click();
 await page.locator('[data-about-maintainers]').filter({hasText:'名单读取失败'}).waitFor();assert(!(await page.locator('[data-about-edit]').isVisible()));
 assert.equal(errors.length,0,errors.join('\n'));
 console.log('PASS about dialog',width,'layout, original logo, HR01 read-only, ADMIN edit/cancel/save/error, XSS, focus and updates');
 await page.close();
}
}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
