// Real about dialog, modal lifecycle and product styles; no live accounts or writes.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {chromium}=require('./support/browser-runtime.cjs');
const {routeFrontend}=require('./support/frontend-page.cjs');
const releases=require('../app/changelog.json');
const versionCount=new Set(releases.map(release=>release.version)).size;

const html=`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/static/css/style.css"><link rel="stylesheet" href="/static/css/workspace-ui.css">
<link rel="stylesheet" href="/static/css/appearance.css">
<div class="app-frame"><header class="app-topbar"><img class="app-brand-logo" src="/static/images/fzpr-icon.png" width="30" height="30"></header>
<nav id="tabs"></nav><div style="position:relative;z-index:2">今日小祝福</div><main class="app-shell"><div id="app"></div></main></div><div id="toast"></div>`;
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true}),captures={};try{
for(const width of [320,390,1280]){
 const page=await browser.newPage({viewport:{width,height:850},reducedMotion:'reduce'}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 let credits={maintainers:'赵君杰、张森',feedback:'',revision:0,can_edit:false};
 let failSave=false,readFail=false,deferSave=false,saveCount=0,finishSave;
 await page.route('**/api/changelog',route=>route.fulfill(readFail
  ?{status:500,json:{detail:'读取失败'}}
  :{json:{app_version:releases[0].version,version_count:versionCount,releases:releases.slice(0,1)}}));
 await page.route('**/api/about-system',async route=>{
  if(route.request().method()==='GET'&&readFail)return route.fulfill({status:500,json:{detail:'读取失败'}});
  if(route.request().method()==='PUT'){
   saveCount++;
   if(deferSave)await new Promise(resolve=>{finishSave=resolve;});
   if(failSave)return route.fulfill({status:403,json:{detail:'仅ADMIN账号可修改关于系统名单'}});
   const {maintainers,feedback,revision}=route.request().postDataJSON();
   credits={...credits,maintainers,feedback,revision:revision+1};
  }
  await route.fulfill({json:credits});
 });
 await page.goto(await routeFrontend(page,{html}));
 await page.evaluate(async()=>{
  const {state}=await import('/static/js/app/state.js');
  const {renderTabs}=await import('/static/js/app/navigation.js');
  const {configureViews}=await import('/static/js/app/context.js');
  document.documentElement.dataset.skin='medal';
  state.me={role_code:'SYSTEM_ADMIN',base_role_code:'SYSTEM_ADMIN',employee_no:'HR01',permissions:[]};
  state.tab='home';
  configureViews({home:()=>{},changelog:()=>{document.getElementById('app').textContent='测试更新记录';}});
  renderTabs();
 });
 const trigger=page.locator(width<=760?'.mobile-more-drawer [data-open-about]':'.tabs-desktop [data-open-about]').last();
 const more=page.locator('[data-open-more]');
 const openDialog=async()=>{if(width<=760)await more.click();await trigger.click();};
 const open=async()=>{await openDialog();await page.locator('[data-about-maintainers]').filter({hasText:'张森'}).waitFor();await page.locator('[data-about-dialog]').evaluate(async el=>{await Promise.all(el.getAnimations({subtree:true}).map(animation=>animation.finished));});};
 await open();
 const dialog=page.locator('[data-about-dialog]');
 const history=dialog.locator('.about-system-history');
 await dialog.locator('[data-about-edition-count]').filter({hasText:String(versionCount)}).waitFor();
 assert.equal(await dialog.locator('[data-about-edition-count]').textContent(),String(versionCount));
 assert.equal(await dialog.locator('[data-about-version]').textContent(),'版本 '+releases[0].version);
 assert.equal(await history.evaluate(el=>el.open),false);
 assert.equal(await history.locator('li').first().isVisible(),false);
 await history.locator('summary').press('Enter');
 assert.equal(await history.locator('li:visible').count(),4);
 assert(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth));
 await history.locator('summary').click();
 assert.equal(await history.evaluate(el=>el.open),false);
 assert.equal(await page.locator('.about-system-credits dt').allTextContents().then(x=>x.join('|')),'建设与维护|建议与反馈');
 assert.equal(await page.locator('[data-about-feedback]').textContent(),'—');
 assert(await page.locator('[data-about-maintainers]').evaluate(el=>Number(getComputedStyle(el).fontWeight)>=600));
 assert(!(await page.locator('[data-about-edit]').isVisible()),'HR01 must have no editor');
 const geometry=await page.evaluate(()=>{const header=document.querySelector('.about-system-heading'),title=header.querySelector('h3').getBoundingClientRect(),close=header.querySelector('button').getBoundingClientRect(),modal=document.querySelector('[data-about-dialog]').getBoundingClientRect();return {titleHeight:title.height,closeWidth:close.width,overlap:title.right>close.left,overflow:document.documentElement.scrollWidth>innerWidth,top:document.elementFromPoint(modal.left+10,modal.top+10).closest('[data-about-dialog]')!==null};});
 assert(geometry.titleHeight<32&&!geometry.overlap&&!geometry.overflow&&geometry.top);
 assert.equal(geometry.closeWidth,44);
 assert(await page.locator('.about-system-brand img').evaluate(el=>el.src===document.querySelector('.app-brand-logo').src));
 await page.keyboard.press('Escape');assert.equal(await page.locator('[data-about-dialog]').count(),0);
 assert(await (width<=760?more:trigger).evaluate(el=>el===document.activeElement));
 credits.can_edit=true;
 if(width===390&&process.argv[2]){
  credits.feedback='姚思佳';await open();
  await dialog.locator('[data-about-edition-count]').filter({hasText:String(versionCount)}).waitFor();
  captures.COLLAPSED=(await dialog.screenshot({animations:'disabled'})).toString('base64');
  await history.locator('summary').click();
  captures.EXPANDED=(await dialog.screenshot({animations:'disabled'})).toString('base64');
  await page.keyboard.press('Escape');await dialog.waitFor({state:'detached'});credits.feedback='';
 }
 await open();
 assert.equal(await history.evaluate(el=>el.open),false);
 await page.locator('[data-about-edit]').click();
 await page.locator('#aboutMaintainers').fill('未保存');await page.locator('#aboutFeedback').fill('未保存建议人');await page.locator('[data-about-cancel]').click();
 assert.equal(saveCount,0);
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
 failSave=true;await page.locator('[data-about-form] [type=submit]').click();
 await page.locator('[data-about-status]').filter({hasText:'仅ADMIN'}).waitFor();
 assert.equal(await page.locator('#aboutMaintainers').inputValue(),'保留输入');assert(await page.locator('[data-about-form]').isVisible());
 failSave=false;deferSave=true;await page.locator('[data-about-form] [type=submit]').click();
 assert(await page.locator('[data-about-close]').isDisabled());await page.keyboard.press('Escape');assert.equal(await page.locator('[data-about-dialog]').count(),1);
 assert.equal(typeof finishSave,'function');finishSave();await page.locator('[data-about-status]').filter({hasText:'已保存'}).waitFor();
 await page.locator('[data-about-updates]').click();await page.getByText('测试更新记录',{exact:true}).waitFor();
 assert.equal(await page.locator('[data-about-dialog]').count(),0);assert.equal(await page.evaluate(()=>document.body.style.overflow),'');
 credits.maintainers='赵君杰、张森';readFail=true;await openDialog();
 await page.locator('[data-about-maintainers]').filter({hasText:'名单读取失败'}).waitFor();assert(!(await page.locator('[data-about-edit]').isVisible()));
 await dialog.getByText('暂时无法读取版本数',{exact:true}).waitFor();
 assert.equal(await dialog.locator('[data-about-edition-count]').textContent(),'—');
 assert.equal(errors.length,0,errors.join('\n'));
 console.log('PASS about dialog',width,'shared version count, collapsed history, layout, credits permissions, focus and updates');
 await page.close();
}
}finally{await browser.close();}
if(process.argv[2]){
 let preview=fs.readFileSync(process.argv[2],'utf8');
 for(const [key,data] of Object.entries(captures))preview=preview.replace(`PREVIEW_${key}`,'data:image/png;base64,'+data);
 fs.writeFileSync(process.argv[2],preview);
}
})().catch(error=>{console.error(error);process.exitCode=1;});
