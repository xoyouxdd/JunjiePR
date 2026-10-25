'use strict';
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs'),net=require('node:net');
const {spawn}=require('node:child_process'),{chromium,webkit,firefox}=require('playwright');
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
let server,browser;
(async()=>{try{
  const port=await new Promise(r=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>r(p));});});
  const base='http://127.0.0.1:'+port;let output='';
  server=spawn(path.resolve(__dirname,'../.venv/Scripts/python.exe'),[path.join(__dirname,'rotation_temporary_fixture.py'),String(port)],{cwd:path.resolve(__dirname,'..'),stdio:['ignore','pipe','pipe']});
  server.stdout.on('data',d=>output+=d);server.stderr.on('data',d=>output+=d);
  let ready=false;for(let i=0;i<150;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await sleep(250);}assert(ready,output);
  let index=0;
  for(const [name,engine] of [['edge',chromium],['webkit',webkit],['firefox',firefox]]){
    browser=await engine.launch(name==='edge'?{channel:'msedge',headless:true}:{headless:true});
    const context=await browser.newContext(),page=await context.newPage();const errors=[];
    page.on('pageerror',e=>errors.push(e.stack||e.message));
    await page.goto(base+'/login');await page.locator('[name=employee_no]').fill('9730100');await page.locator('[name=password]').fill('0100');await page.locator('[type=submit]').click();
    await page.getByRole('heading',{name:'轮岗待办',exact:true}).waitFor();await page.locator('#rotationPersonalTasks .rt-personal-task').waitFor();
    assert.equal(await page.locator('#tabs').isVisible(),false);assert.equal(await page.getByRole('link',{name:'当日看板'}).count(),0);
    assert(!(await page.locator('#app').innerText()).includes('（临时）'));
    for(const width of [320,375,390,430,768,1440]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,name+' personal overflow '+width);}
    const shots=process.env.ROTATION_TEMP_SCREENSHOTS;if(shots){fs.mkdirSync(shots,{recursive:true});await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(shots,name+'-personal-mobile.png')});}
    assert.equal((await context.request.get(base+'/api/options')).status(),403);
    if(await page.locator('[data-rotation-task=depart]').count()){
      const submitted=page.waitForResponse(r=>r.url().endsWith('/api/rotation-board/me/act'));
      await page.locator('[data-rotation-task=depart]').click();await page.locator('[data-modal-confirm]').click();
      assert((await submitted).ok());await page.getByText(/最近安排生效/).waitFor();
    }
    await page.getByRole('button',{name:'退出',exact:true}).click();await page.waitForURL('**/login');
    await page.locator('[name=employee_no]').fill('9730020');await page.locator('[name=password]').fill('rotation-temp-fixture');await page.locator('[type=submit]').click();await page.waitForURL(base+'/');
    await page.goto(base+'/rotation-board');
    let releaseRoster;const gate=new Promise(r=>releaseRoster=r);
    await page.route('**/api/rotation-board/roster?**',async route=>{const response=await route.fetch();await gate;await route.fulfill({response});});
    const rosterRead=page.waitForResponse(r=>r.url().includes('/api/rotation-board/roster?'));
    await page.locator('[data-tab=roster]').click();
    const file=await context.request.get(base+'/__fixture/roster?no=973011'+index++);
    await page.locator('#rosterForm [type=file]').setInputFiles({name:'synthetic.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:await file.body()});
    releaseRoster();await rosterRead;
    await page.waitForFunction(()=>view.roster!==null);
    assert.equal(await page.locator('#rosterForm [type=file]').evaluate(el=>el.files.length),1,'delayed roster read lost selected file');
    const uploaded=page.waitForResponse(async r=>r.url().endsWith('/api/rotation-board/roster/upload')&&!(await r.json()).preview).catch(async e=>{console.error(await page.locator('body').innerText(),errors);throw e;});
    await page.locator('#rosterForm [type=submit]').click();await page.locator('#rtModal [data-ok]').click();const uploadedResponse=await uploaded;assert(uploadedResponse.ok(),await uploadedResponse.text());await page.locator('#rtModal [data-ok]').waitFor({state:'detached'});
    await page.locator('[data-tab=board]').click();await page.locator('.rt-board').waitFor();
    assert(!(await page.locator('#rtApp').innerText()).includes('（临时）'));
    for(const width of [320,390,430,768,1440]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,name+' board overflow '+width);}
    await page.locator('[data-tab=records]').click();await page.locator('#logLoad').click();await page.locator('#rtApp').getByText('合成临时成员（临时）',{exact:true}).first().waitFor();
    if(shots){await page.setViewportSize({width:1440,height:900});await page.screenshot({path:path.join(shots,name+'-manager-records.png')});}
    assert.deepEqual(errors,[]);console.log(name+': actual suffix login, personal-only page, logout, manager roster preview/upload, plain board names, responsive widths passed');
    await browser.close();browser=null;
  }
}finally{if(browser)await browser.close();if(server){server.kill();await Promise.race([new Promise(r=>server.once('exit',r)),sleep(2500)]);}}})().catch(e=>{console.error(e);process.exitCode=1;});
