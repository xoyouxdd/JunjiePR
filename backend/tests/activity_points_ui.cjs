'use strict';
const assert=require('node:assert/strict');
const net=require('node:net');
const path=require('node:path');
const fs=require('node:fs');
const {spawn}=require('node:child_process');
const {chromium}=require('./support/browser-runtime.cjs');
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
      await card.getByRole('heading',{name:'我的积点卡'}).waitFor();
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
    await card.getByRole('heading',{name:'我的积点卡'}).waitFor();
    assert.equal(await card.locator('.points-amount strong').textContent(),'1');
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

    const manager=await browser.newContext({viewport:{width:1280,height:1450}});
    assert((await manager.request.post(base+'/api/login',{data:{employee_no:'GSMTEST01',password:'1234'}})).ok());
    await manager.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}});
    const ranking=await manager.newPage();
    await ranking.goto(base+'/');
    await ranking.locator('.tabs-desktop [data-tab="prRankings"]').click();
    await ranking.locator('[data-pr-category="points"]').click();
    await ranking.locator('#prResults th').getByText('区间累计积点',{exact:true}).waitFor({state:'attached'});
    assert.equal(await ranking.locator('#prResults tbody tr').first().locator('.pr-points-value').textContent(),'1 点');
    assert(await ranking.locator('#prExport').isVisible());
    await ranking.locator('#prFilter [name=attraction_id]').selectOption('');
    await ranking.locator('.pr-result-meta').getByText(/全部景点圈/).waitFor();
    for(const width of [320,390,1280]){
      await ranking.setViewportSize({width,height:1450});
      assert(await ranking.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`ranking overflow ${width}`);
      assert(await ranking.locator(width<768?'.pr-points-row':'.pr-ranking-table').first().isVisible());
      await ranking.locator('.pr-ranking-page').screenshot({path:path.join(output,`points-ranking-${width}.png`)});
    }
    for(const width of [390,1280]){
      await ranking.setViewportSize({width,height:1450});
      const queued=ranking.waitForResponse(res=>res.request().method()==='POST'&&new URL(res.url()).pathname==='/api/export-tasks');
      await ranking.locator('#prExport').click();
      const exportResponse=await queued;
      assert.equal(exportResponse.status(),202);
      const target=new URL(exportResponse.url()).searchParams.get('target');
      const exportParams=new URL(target,base).searchParams;
      assert.equal(exportParams.get('category'),'points');
      assert.equal(exportParams.has('attraction_id'),false);
      const {id}=await exportResponse.json();
      let job;
      for(let i=0;i<80;i++){
        const jobs=await manager.request.get(base+'/api/export-tasks').then(res=>res.json());
        job=jobs.items.find(item=>item.id===id);
        if(job?.status==='completed'||job?.status==='failed')break;
        await sleep(100);
      }
      assert.equal(job?.status,'completed',JSON.stringify(job));
      const file=await manager.request.get(base+job.download_url);
      assert(file.ok(),await file.text());
      assert.match(decodeURIComponent(file.headers()['content-disposition']),/积点榜_全部景点圈/);
      const link=ranking.locator(`[data-export-download="${id}"]`);
      const [download]=await Promise.all([ranking.waitForEvent('download'),link.click()]);
      assert.match(download.suggestedFilename(),/积点榜_全部景点圈/);
      await download.saveAs(path.join(output,`points-ranking-all-circles-${width}.xlsx`));
      await ranking.locator('[data-export-close]').click();
    }
    await ranking.locator('#prFilter [name=keyword]').fill('CMTEST01');
    await ranking.locator('#prFilter .primary').click();
    await ranking.locator('#prResults tbody tr').filter({hasText:'CMTEST01'}).waitFor();
    assert.equal(await ranking.locator('#prResults tbody tr').count(),1);
    await ranking.locator('[data-pr-category="overall"]').click();
    assert(await ranking.locator('#prExport').isVisible());
    await manager.close();
    console.log('Activity points UI passed: 320/390/1280px, private card, real recognition, points ranking and PR filters.');
  }finally{
    if(browser)await browser.close();server.kill();
  }
}
run().catch(error=>{console.error(error);process.exitCode=1;});
