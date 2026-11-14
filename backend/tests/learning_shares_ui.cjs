// Actual API + native modules + synthetic isolated identities and material only.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os'),net=require('node:net');
const {spawn}=require('node:child_process');const {chromium,webkit,firefox}=require('./support/browser-runtime.cjs');
const root=path.resolve(__dirname,'..'),work=fs.mkdtempSync(path.join(os.tmpdir(),'fz-learning-ui-'));
let server,browser,logs='';const sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));const base='http://127.0.0.1:'+port;
try{
 server=spawn(path.join(root,'.venv/Scripts/python.exe'),[path.join(__dirname,'learning_shares_fixture.py'),work,String(port)],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});server.stdout.on('data',d=>logs+=d);server.stderr.on('data',d=>logs+=d);
 let ready=false;for(let i=0;i<100;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}await sleep(300);}assert(ready,logs);
 for(const engine of (process.env.LEARNING_UI_ENGINES||'edge,webkit,firefox').split(',')){
  browser=await({edge:chromium,webkit,firefox}[engine]).launch({headless:true,...(engine==='edge'?{channel:'msedge'}:{})});
  const context=await browser.newContext({viewport:{width:390,height:900},locale:'zh-CN'});const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  async function login(no){await context.request.post(base+'/api/logout');const res=await context.request.post(base+'/api/login',{data:{employee_no:no,password:'fixture2026'}});assert(res.ok());const health=await(await context.request.get(base+'/health')).json();await context.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}});await page.goto(base);await page.waitForLoadState('networkidle');await page.locator('#tabs').waitFor();await page.evaluate(async()=>{const m=JSON.parse(document.querySelector('[type=importmap]').textContent).imports;const {state}=await import(m['./static/js/app/state.js']);state.tab='learningShares';await(await import(m['./static/js/app/context.js'])).render();});await page.locator('.learning-circles').waitFor();}
  await login('TRTEST01');await page.locator('[data-learning-new]').click();await page.locator('[name=title]').fill('浏览器攻略 '+engine);await page.locator('[name=body]').fill('岗位学习内容与交接信息。');await page.locator('[name=category]').selectOption('开场准备');await page.locator('[data-learning-upload]').setInputFiles(path.join(work,'sample.png'));await page.locator('[data-learning-assets]').getByText('sample.png',{exact:false}).waitFor();await page.locator('[data-learning-save-draft]').click();await page.getByText('草稿已保存',{exact:false}).waitFor();await page.locator('[data-learning-back]').click();await page.locator('.learning-personal > summary').click();await page.locator('[data-view=mine]').click();await page.locator('[data-draft-open]').first().click();assert.equal(await page.locator('[name=title]').inputValue(),'浏览器攻略 '+engine);await page.locator('.learning-form [type=submit]').click();await page.locator('[data-preview-confirm]').click();await page.locator('.learning-circles').waitFor();assert.equal(await page.locator('#tabs [data-tab=learningShares]').first().innerText(),'岗位知识库');await page.locator('[data-category=异常处理]').click();await page.locator('[data-category=异常处理][aria-pressed=true]').waitFor();assert.equal(await page.locator('[data-learning-open]').count(),0);await page.locator('[data-category=开场准备]').click();await page.locator('[data-category=开场准备][aria-pressed=true]').waitFor();await page.locator('[data-learning-search] [name=keyword]').fill('浏览器攻略');const searchForm=await page.locator('[data-learning-search]').elementHandle();await page.locator('[data-learning-search] [type=submit]').click();await searchForm.waitForElementState('hidden');await page.getByRole('button',{name:'浏览器攻略 '+engine,exact:true}).click();try{await page.locator('[data-learning-asset]').waitFor();}catch(e){const dir=path.resolve(root,'../output/knowledge-library-review');fs.mkdirSync(dir,{recursive:true});await page.screenshot({path:path.join(dir,engine+'-asset-failure.png'),fullPage:true});console.error(await page.locator('#app').innerText());throw e;}await page.locator('[data-learning-asset]').click();await page.locator('.learning-image').waitFor();assert(await page.locator('.learning-image').evaluate(i=>i.complete&&i.naturalWidth===160));await page.locator('[data-like]').click();await page.getByRole('button',{name:'已点赞 1',exact:true}).waitFor();await page.locator('[data-star]').click();await page.getByRole('button',{name:'★ 已星标',exact:true}).waitFor();assert((await page.locator('.learning-watermarks').innerText()).includes('TRTEST01'));
  await page.locator('[data-learning-edit]').click();await page.locator('[name=body]').fill('原发布TR更新的内容');await page.locator('[name=summary]').fill('补充');await page.locator('.learning-form [type=submit]').click();await page.locator('[data-preview-confirm]').click();await page.locator('.learning-circles').waitFor();
  const personal=page.locator('.learning-personal');
  assert.equal(await personal.getAttribute('open'),null);
  assert(await personal.locator('[data-view=star]').isHidden());
  await personal.locator('summary').click();
  await personal.getByRole('button',{name:'我的星标',exact:true}).click();
  await page.locator('.learning-view-heading').getByRole('heading',{name:'我的星标',exact:true}).waitFor();
  await page.getByRole('button',{name:'浏览器攻略 '+engine,exact:true}).waitFor();
  await page.getByRole('button',{name:'返回知识库',exact:true}).click();
  await page.locator('.learning-view-heading').waitFor({state:'detached'});
  for(let index=0;index<9;index++){
   const seeded=await context.request.post(base+'/api/learning',{data:{title:'查找演示 '+engine+' '+index,body:'本地测试内容',category:'岗位操作'}});
   assert(seeded.ok(),await seeded.text());
  }
  const libraryOrder=await page.locator('.learning-library').evaluate(node=>
   ['.learning-circles','.learning-search','.learning-categories','.learning-list'].map(selector=>node.querySelector(selector).getBoundingClientRect().top));
  assert.deepEqual(libraryOrder,[...libraryOrder].sort((a,b)=>a-b));
  await page.locator('.learning-search [name=keyword]').fill('查找演示 '+engine);
  await page.locator('[data-category=异常处理]').click();
  await page.getByRole('button',{name:'查看全部分类',exact:true}).click();
  await page.getByRole('button',{name:'查找演示 '+engine+' 1',exact:true}).waitFor();
  assert.equal(await page.locator('.learning-search [name=keyword]').inputValue(),'查找演示 '+engine);
  assert.equal(await page.locator('.learning-categories [data-category=""]').getAttribute('aria-pressed'),'true');
  await page.locator('[data-category=岗位操作]').click();
  await page.locator('[data-category=岗位操作][aria-pressed=true]').waitFor();
  const selectedCircle=await page.locator('[data-circle][aria-pressed=true]').getAttribute('data-circle');
  const result=page.getByRole('button',{name:'查找演示 '+engine+' 1',exact:true});
  await result.evaluate(button=>button.scrollIntoView({block:'center'}));
  const beforeScroll=await page.evaluate(()=>window.scrollY);
  const beforeOffset=await result.evaluate(button=>button.getBoundingClientRect().top);
  assert(beforeScroll>0);
  await result.click();
  await page.locator('.learning-detail-heading h2').waitFor();
  await page.locator('[data-learning-back]').click();
  await result.waitFor();
  assert.equal(await page.locator('[data-circle][aria-pressed=true]').getAttribute('data-circle'),selectedCircle);
  assert.equal(await page.locator('[data-category=岗位操作]').getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('.learning-search [name=keyword]').inputValue(),'查找演示 '+engine);
  assert(Math.abs(await result.evaluate(button=>button.getBoundingClientRect().top)-beforeOffset)<2);
  assert(Math.abs(await page.evaluate(()=>window.scrollY)-beforeScroll)<2);
  await page.locator('[data-learning-clear-search]').click();
  await page.locator('[data-learning-clear-search]').waitFor({state:'detached'});
  assert.equal(await page.locator('.learning-search [name=keyword]').inputValue(),'');
  for(const width of [320,390,768,1440]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'overflow '+width);}
  // Formal managers can read all circles directly, without gaining cross-circle edit controls.
  for(const account of ['SUPTEST01','GSMTEST01']){
    await login(account);
    const circles=(await(await context.request.get(base+'/api/learning/options')).json()).circles;
    assert(circles.length===3&&circles.every(c=>c.readable&&!c.expires_at));
    const cross=circles.find(c=>!c.own);
    await page.locator('[data-circle="'+cross.id+'"]').click();
    await page.getByRole('button',{name:'跨圈学习材料',exact:true}).waitFor();
    assert.equal(await page.locator('[data-learning-apply]').count(),0);
    await page.getByRole('button',{name:'跨圈学习材料',exact:true}).click();
    assert.equal(await page.locator('[data-learning-edit]').count(),0);
    assert.equal(await page.locator('[data-learning-withdraw]').count(),0);
    const attachment=page.locator('[data-learning-asset]');
    await attachment.evaluate(button=>button.scrollIntoView({block:'center'}));
    const previousOffset=await attachment.evaluate(button=>button.getBoundingClientRect().top);
    assert.equal(await attachment.getAttribute('aria-expanded'),'false');
    assert(await page.getByRole('button',{name:'‹ 返回列表',exact:true}).evaluate(button=>button.offsetWidth<button.parentElement.clientWidth));
    assert(await page.locator('.learning-detail-body .learning-reading-content').evaluate(body=>body.offsetHeight<180));
    await attachment.click();
    await page.locator('[data-pdf-rendered=true]').waitFor();
    const reader=attachment.locator('..').locator('.learning-inline-reader');
    assert.equal(await attachment.getAttribute('aria-expanded'),'true');
    assert(await reader.locator('[data-pdf-page]').isVisible());
    assert.match(await reader.locator('.learning-watermarks').innerText(),new RegExp(account));
    await reader.getByRole('button',{name:'关闭',exact:true}).click();
    assert(await reader.isHidden());
    assert.equal(await attachment.getAttribute('aria-expanded'),'false');
    assert.equal(await reader.locator('canvas').count(),0);
    assert(await attachment.evaluate(button=>button===document.activeElement));
    assert(Math.abs(await attachment.evaluate(button=>button.getBoundingClientRect().top)-previousOffset)<2);
    await attachment.click();
    await reader.locator('[data-pdf-rendered=true]').waitFor();
  }
  // Another authorized manager submits a revision; only owner publishes.
  const own=(await(await context.request.get(base+'/api/learning/options')).json()).circles.find(c=>c.own).id;
  const shares=await(await context.request.get(base+'/api/learning?circle_id='+own)).json();const share=shares.items.find(a=>a.title==='浏览器攻略 '+engine);
  await login('GSMTEST01');await page.getByRole('button',{name:share.title,exact:true}).click();await page.locator('[data-learning-edit]').click();await page.locator('[name=body]').fill('主管提交的修改稿');await page.locator('[name=summary]').fill('请确认');await page.locator('.learning-form [type=submit]').click();await page.locator('[data-preview-confirm]').click();await page.locator('.learning-circles').waitFor();
  await login('TRTEST01');await page.locator('[data-learning-revisions="'+share.id+'"]').click();await page.locator('[data-review-approve]').click();await page.locator('[data-modal-confirm]').click();await page.getByText('当前 V3',{exact:false}).waitFor();
  await login('CMTEST02');await page.getByRole('button',{name:share.title,exact:true}).click();await page.locator('.learning-feedback summary').click();await page.locator('[data-feedback-form] [name=content]').fill('请补充第二步的核对方法');await page.locator('[data-feedback-form] [type=submit]').click();await page.getByText('反馈已提交，等待负责人员处理',{exact:true}).waitFor();await login('TRTEST01');await page.getByRole('button',{name:share.title,exact:true}).click();await page.locator('.learning-feedback summary').click();await page.locator('[data-feedback-response]').first().fill('已核对，后续修改时补充');await page.locator('[data-feedback-resolve]').first().click();await page.getByText('处理说明：已核对，后续修改时补充',{exact:false}).waitFor();await login('CMTEST02');const opts=await(await context.request.get(base+'/api/learning/options')).json(),target=opts.circles.find(c=>!c.own);await page.locator('[data-circle="'+target.id+'"]').click();await page.locator('.learning-access').waitFor();
  if(!target.readable){assert.equal(await page.locator('[data-learning-open]').count(),0);await page.locator('[data-learning-apply]').click();await page.locator('[name=reason]').fill('多资质岗位复习');await page.locator('form [type=submit]').click();await page.getByRole('button',{name:'待确认',exact:true}).waitFor();await login('SUPTEST01');await page.locator('.learning-personal > summary').click();await page.locator('[data-learning-requests]').click();await page.locator('[data-approve]').click();await page.locator('[data-modal-confirm]').click();await page.getByText('暂无待审批申请').waitFor();await login('CMTEST02');await page.locator('[data-circle="'+target.id+'"]').click();}
  await page.setViewportSize({width:390,height:900});await page.getByRole('button',{name:'跨圈学习材料',exact:true}).click();await page.locator('[data-learning-asset]').click();await page.locator('[data-pdf-rendered=true]').waitFor();assert((await page.locator('.learning-watermarks').first().innerText()).includes('CMTEST02'));assert.equal(await page.locator('[data-learning-edit]').count(),0);
  if(process.env.LEARNING_UI_SCREENSHOTS){fs.mkdirSync(process.env.LEARNING_UI_SCREENSHOTS,{recursive:true});await page.screenshot({path:path.join(process.env.LEARNING_UI_SCREENSHOTS,engine+'-reading.png'),fullPage:true});}
  await context.request.post(base+'/__learning_test/expire');await page.locator('[data-learning-back]').click();await page.locator('[data-learning-open]').first().click();await page.getByRole('heading',{name:'查看权限已到期',exact:true}).waitFor();assert.equal(await page.locator('.learning-pdf-canvas').count(),0);
  await login('SUPTEST01');
  await page.locator('[data-circle="'+target.id+'"]').click();
  await page.locator('[data-circle="'+target.id+'"][aria-pressed=true]').waitFor();
  assert.equal(await page.locator('[data-learning-new]').count(),0);
  await page.locator('[data-circle="'+own+'"]').click();
  await page.locator('[data-learning-new]').click();
  const title='主管发布攻略 '+engine;
  await page.locator('[name=title]').fill(title);
  await page.locator('[name=body]').fill('原公开版本。');
  await page.locator('[data-learning-upload]').setInputFiles(path.join(work,'sample.png'));
  await page.locator('[data-learning-assets]').getByText('sample.png',{exact:false}).waitFor();
  await page.locator('.learning-form [type=submit]').click();
  await page.locator('[data-preview-confirm]').click();
  await page.getByRole('button',{name:title,exact:true}).waitFor();
  const supervisorShare=(await(await context.request.get(base+'/api/learning?circle_id='+own)).json()).items.find(a=>a.title===title);
  await login('TATEST01');assert.equal(await page.locator('[data-learning-new]').count(),1);
  await page.locator('[data-learning-open="'+supervisorShare.id+'"]').click();
  await page.locator('[data-learning-edit]').click();
  await page.locator('[name=body]').fill('由接任负责人确认的版本。');
  await page.locator('[name=summary]').fill('补充岗位核对项');
  await page.locator('.learning-form [type=submit]').click();
  await page.getByRole('button',{name:'确认提交修改稿',exact:true}).click();
  await page.locator('.learning-circles').waitFor();
  await login('SUPTEST01');
  await page.locator('[data-learning-open="'+supervisorShare.id+'"]').click();
  await page.locator('[data-learning-transfer]').click();
  const nextOwner=page.locator('[name=employee_id] option').filter({hasText:/ · GSMTEST01$/});
  await page.locator('[name=employee_id]').selectOption(await nextOwner.getAttribute('value'));
  await page.locator('[name=reason]').fill('后续由本圈GSM维护');
  await page.getByRole('button',{name:'确认交接',exact:true}).click();
  await page.locator('.learning-detail-heading').getByText('测试GSM',{exact:false}).waitFor();
  await login('GSMTEST01');
  await page.locator('[data-learning-revisions="'+supervisorShare.id+'"]').click();
  const comparison=page.locator('.learning-review-compare');
  const history=page.locator('.learning-review-history');
  await comparison.waitFor();
  assert.equal(await history.getAttribute('open'),null);
  const columns=comparison.locator('.learning-review-column');
  assert.match(await columns.first().innerText(),/当前版\s*V1[\s\S]*原公开版本/);
  assert.match(await columns.last().innerText(),/修改稿\s*基于 V1[\s\S]*由接任负责人确认的版本/);
  const positions=()=>columns.evaluateAll(nodes=>nodes.map(node=>{const {x,y,bottom}=node.getBoundingClientRect();return {x,y,bottom};}));
  for(const width of [320,390,1280]){
   await page.setViewportSize({width,height:900});
   const [left,right]=await positions();
   assert(width===1280?Math.abs(left.y-right.y)<1&&right.x>left.x:Math.abs(left.x-right.x)<1&&right.y>=left.bottom);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  }
  await columns.first().getByRole('button',{name:'sample.png · 阅读'}).click();
  await comparison.locator('..').locator('.learning-review-reader .learning-image').waitFor();
  await columns.last().getByRole('button',{name:'sample.png · 阅读'}).click();
  await comparison.locator('..').locator('.learning-review-reader .learning-image').waitFor();
  await page.locator('[data-review-approve]').click();
  await page.locator('[data-modal-confirm]').click();
  await page.getByText('当前 V2',{exact:false}).waitFor();
  await page.getByText('暂无待确认稿',{exact:true}).waitFor();
  assert.equal(await history.getAttribute('open'),null);
  await history.locator('summary').click();
  await history.getByText('由接任负责人确认的版本。',{exact:true}).waitFor();
  assert.deepEqual(errors,[]);await browser.close();browser=null;console.log(engine+': publishing, owner approval, supervisor-to-GSM handover, cross-circle access and PDF reading passed');
 }
}finally{if(browser)await browser.close();if(server){server.kill();await new Promise(r=>server.once('exit',r));}}
})().catch(e=>{console.error(e,logs.slice(-1500));process.exitCode=1;});
