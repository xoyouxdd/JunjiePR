'use strict';
const assert=require('node:assert/strict');
const net=require('node:net');
const path=require('node:path');
const fs=require('node:fs');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const backend=path.resolve(__dirname,'..');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));

async function run(){
  const socket=net.createServer();
  await new Promise(resolve=>socket.listen(0,'127.0.0.1',resolve));
  const port=socket.address().port;
  await new Promise(resolve=>socket.close(resolve));
  const base='http://127.0.0.1:'+port;
  const server=spawn(path.join(backend,'.venv/Scripts/python.exe'),[path.join(__dirname,'recognition_photo_fixture.py'),String(port)],{cwd:backend,windowsHide:true});
  let log='',browser;
  server.stdout.on('data',data=>log+=data);server.stderr.on('data',data=>log+=data);
  try{
    let ready=false,health;
    for(let i=0;i<120;i++){
      try{const response=await fetch(base+'/health');if(response.ok){health=await response.json();ready=true;break;}}catch{}
      await sleep(250);
    }
    assert(ready,log);
    browser=await chromium.launch({channel:'msedge',headless:true});
    const output=path.resolve(backend,'../output/activity-points-ui');fs.mkdirSync(output,{recursive:true});
    for(const width of [320,390,1280]){
      const context=await browser.newContext({viewport:{width,height:900}});
      const login=await context.request.post(base+'/api/login',{data:{employee_no:'CMTEST01',password:'1234'}});
      assert(login.ok(),await login.text());
      await context.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}});
      const page=await context.newPage();const errors=[];page.on('pageerror',error=>errors.push(error.message));
      await page.goto(base+'/');
      const card=page.locator('#activityPoints .activity-points-card');
      await card.getByRole('heading',{name:'我的活动积分卡'}).waitFor();
      assert((await card.textContent()).includes('永久有效'));
      await card.locator('summary').click();
      assert((await card.textContent()).includes('还没有积点记录'));
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),`overflow ${width}`);
      await card.screenshot({path:path.join(output,`card-${width}.png`)});
      if(width<768)await page.locator('[data-open-more]').click();
      await page.locator('[data-tab="activityPoints"]:visible').first().click();
      await page.locator('#personalActivityPoints h2').waitFor();
      await context.close();assert.deepEqual(errors,[]);
    }
    // Real API recognition must update the recipient's private card once.
    const context=await browser.newContext({viewport:{width:390,height:900}});
    await context.request.post(base+'/api/login',{data:{employee_no:'SUPTEST01',password:'1234'}});
    const options=await context.request.get(base+'/api/options').then(r=>r.json());
    const me=await context.request.get(base+'/api/me').then(r=>r.json());
    await context.request.post(base+'/api/logout');
    await context.request.post(base+'/api/login',{data:{employee_no:'CMTEST01',password:'1234'}});
    const recipient=await context.request.get(base+'/api/me').then(r=>r.json());
    await context.request.post(base+'/api/logout');
    await context.request.post(base+'/api/login',{data:{employee_no:'SUPTEST01',password:'1234'}});
    const photo=Buffer.from(await fetch(base+'/__fixture/photo/blue').then(r=>r.arrayBuffer()));
    const created=await context.request.post(base+'/api/recognitions',{multipart:{
      employee_id:String(recipient.id),recognition_date:new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai'}).format(new Date()),
      recognition_type_id:String(options.recognition_types.find(r=>r.code==='SAFETY').id),
      occurred_attraction_id:String(options.recognition_venues.find(r=>r.name==='热力追踪').id),
      recognizer_employee_id:String(me.id),content:'积点页面验证',active_recognition:'true',
      same_day_duplicate_confirmed:'true',idempotency_key:'points-ui',image:{name:'proof.png',mimeType:'image/png',buffer:photo}
    }});
    assert(created.ok(),await created.text());
    await context.request.post(base+'/api/logout');
    await context.request.post(base+'/api/login',{data:{employee_no:'CMTEST01',password:'1234'}});
    const page=await context.newPage();await page.goto(base+'/');
    const card=page.locator('#activityPoints .activity-points-card');
    await card.getByRole('heading',{name:'我的活动积分卡'}).waitFor();
    await card.locator('summary').click();
    assert((await card.textContent()).includes('+1 点'));
    for(const width of [320,390,1280]){
      await page.setViewportSize({width,height:1400});
      await card.scrollIntoViewIfNeeded();
      await page.evaluate(()=>window.scrollBy(0,-130));
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),`earned card overflow ${width}`);
      await card.screenshot({path:path.join(output,`card-earned-${width}.png`)});
    }
    const palettes=new Set();
    for(const skin of ['blue','green','ivory','medal','circle']){
      await page.evaluate(skin=>window.FZAppearance.set(skin),skin);
      palettes.add(await card.locator('.points-pass').evaluate(node=>getComputedStyle(node).backgroundImage));
      for(const width of [390,1280]){
        await page.setViewportSize({width,height:1400});
        await card.scrollIntoViewIfNeeded();await page.evaluate(()=>window.scrollBy(0,-130));
        assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),`${skin} overflow ${width}`);
        await card.screenshot({path:path.join(output,`card-earned-${skin}-${width}.png`)});
      }
    }
    assert.equal(palettes.size,5,'Each system skin should update the card without reloading');
    const circlePalettes=new Set();
    for(const circle of ['heat','dwarf','bear']){
      await page.evaluate(circle=>document.body.dataset.circleTheme=circle,circle);
      circlePalettes.add(await card.locator('.points-pass').evaluate(node=>getComputedStyle(node).backgroundImage));
    }
    assert.equal(circlePalettes.size,3,'Card follows circle-specific theme colors');
    await context.close();
    console.log('Activity points UI passed: 320/390/1280px, private card, menu and real recognition.');
  }finally{
    if(browser)await browser.close();server.kill();
  }
}
run().catch(error=>{console.error(error);process.exitCode=1;});
