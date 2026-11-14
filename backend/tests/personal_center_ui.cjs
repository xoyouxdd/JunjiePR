'use strict';
// Real local app; simulated personal data is intercepted only in this browser context.
const assert=require('node:assert/strict');
const path=require('node:path');
const fs=require('node:fs');
const net=require('node:net');
const {spawn}=require('node:child_process');
const {chromium}=require('./support/browser-runtime.cjs');
const backend=path.resolve(__dirname,'..');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));

async function main(){
  const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));
  const port=socket.address().port;await new Promise(r=>socket.close(r));
  const base='http://127.0.0.1:'+port;
  const server=spawn(path.join(backend,'.venv/Scripts/python.exe'),[path.join(__dirname,'recognition_photo_fixture.py'),String(port)],{cwd:backend,windowsHide:true});
  let log='',browser;
  server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);
  try{
    let health;
    for(let i=0;i<120;i++){try{const r=await fetch(base+'/health');if(r.ok){health=await r.json();break;}}catch{}await sleep(250);}
    assert(health,log);browser=await chromium.launch({channel:'msedge',headless:true});
    const output=path.resolve(backend,'../output/personal-center-ui');fs.mkdirSync(output,{recursive:true});
    for(const account of ['CMTEST01','SUPTEST01','TAGSMTEST01']){
      const context=await browser.newContext({viewport:{width:1280,height:2000}});
      assert((await context.request.post(base+'/api/login',{data:{employee_no:account,password:'1234'}})).ok());
      await context.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}});
      const me=await context.request.get(base+'/api/me').then(r=>r.json());
      const requestedMonths=[];let delayRank=false,releaseRank;
      await context.route('**/api/dashboard?*',async route=>{
        const response=await route.fetch();const data=await response.json();
        const month=new URL(route.request().url()).searchParams.get('month');requestedMonths.push(month);
        await route.fulfill({json:{...data,month,category_scores:{'安全':2,'礼仪':1.5,'包容':1,'效率':0.5,'演出':0.5},
          recognition_score:5.5,attendance_score:12,deduction_score:0,total_score:17.5,monthly_rank_available:true,
          records:Array.from({length:6},(_,i)=>({id:900+i,employee_id:me.id,recognition_date:month+'-'+String(i+1).padStart(2,'0'),
            recognition_type:['安全','礼仪','包容','效率','演出','安全'][i],recognizer_name:'示例GSM',
            content:['主动提醒安全事项，认真做好现场检查。','耐心解答游客疑问，主动提供帮助。','及时协助伙伴，顺利完成工作交接。'][i%3],
            status:'confirmed',fraction:i===1?0.5:1,credited_fraction:i===1?0.5:1,entry_label:'模拟示例'})),
          recognition_summary:{credited:6,pending:0,uncredited:0,rejected:0}}});
      });
      await context.route('**/api/activity-points/me?*',route=>route.fulfill({json:{eligible:true,earned:28,used:0,available:28,total:2,page:1,page_size:20,expires:false,redemption_enabled:false,items:[
        {id:1,delta:2,category:'安全',signer_name:'示例GSM',business_date:'2026-11-05',origin:'live'},
        {id:2,delta:1,category:'礼仪',signer_name:'示例主管',business_date:'2026-11-03',origin:'historical_backfill'}]}}));
      await context.route('**/api/self-month-rank?*',async route=>{
        if(delayRank)await new Promise(resolve=>{releaseRank=resolve;});
        const month=new URL(route.request().url()).searchParams.get('month');
        const historical=month!=='2026-11';
        await route.fulfill({json:{month,status:historical?'ranked':'pending',month_closed:historical,tier:30,label:historical?'荣耀黄金':'待月结',message:historical?'模拟月度勋章':''}});
      });
      await context.route('**/api/self-honors?*',route=>route.fulfill({json:{month:new URL(route.request().url()).searchParams.get('month'),skin:'sea',records:[],history:[],letter_state:'waiting'}}));
      const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
      await page.goto(base+'/');
      await page.locator('.tabs-desktop [data-tab="home"]').click();
      await page.locator('.personal-center .points-amount strong').filter({hasText:'28'}).waitFor();
      assert.equal(await page.locator('.personal-profile h2').textContent(),me.name);
      assert.equal(await page.locator('.personal-honor-tabs button').count(),2);
      assert.equal(await page.locator('.personal-profile-meta').getByText(/带组/).count(),(me.led_groups||[]).length?1:0);
      for(const width of [320,390,1280]){
        await page.setViewportSize({width,height:2000});
        assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),`${account} overflow ${width}`);
        assert(await page.locator('.personal-score-parts').evaluate(node=>[...node.querySelectorAll('strong')].every(item=>item.getBoundingClientRect().right<=item.parentElement.getBoundingClientRect().right+1)),`${account} score figures overlap at ${width}`);
        await page.locator('.personal-center').scrollIntoViewIfNeeded();await page.evaluate(()=>window.scrollBy(0,-130));
        await page.locator('.personal-center').screenshot({path:path.join(output,`${account}-${width}.png`)});
      }
      await page.locator('[data-inbox-entry]').click();
      await page.locator('[data-inbox-home]').waitFor();
      assert(await page.locator('#homeRecognitionContext').isHidden());
      await page.locator('[data-inbox-home]').click();
      await page.locator('[data-inbox-entry]').waitFor();
      assert(await page.locator('.personal-keepsakes').isVisible());
      await page.locator('.personal-profile-details>summary').click();
      assert(await page.locator('.personal-profile-meta').isVisible());
      await page.locator('.personal-profile-details>summary').click();
      await page.locator('#homeRankView').click();
      await page.locator('#homeRankContent .member-rank-awaiting-hero').waitFor();
      await page.locator('#homeMemoryView').click();
      await page.locator('#homeMemoryContent .monthly-memory-card').waitFor();
      const compactMemory=page.locator('#homeMemoryContent .monthly-memory-card');
      const previewHeight=(await compactMemory.boundingBox()).height;
      const originalFacts=await compactMemory.locator('.mm-facts').textContent();
      await page.locator('[data-memory-read]').click();
      assert((await compactMemory.boundingBox()).height>previewHeight,'Read full text must expand the preview');
      assert.equal(await compactMemory.locator('.mm-facts').textContent(),originalFacts);
      await page.locator('[data-memory-read]').click();
      assert((await compactMemory.boundingBox()).height<=previewHeight+1);
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'memory content overflow');
      assert(await page.locator('#homeRankContent').isHidden());
      assert.equal(await page.locator('#homeAttendanceView,#homeAttendanceContent').count(),0);
      // The selected month changes PR / honors / memories; lifetime points stay unchanged.
      const previous=await page.locator('#homeMonthSelect option').evaluateAll(options=>options.map(x=>x.value).find(x=>x!=='2026-11'));
      await page.locator('#homeMonthSelect').selectOption(previous);
      await page.waitForFunction(month=>document.querySelector('#homeMonthSelect')?.value===month&&!document.querySelector('.home-query-retry')&&document.querySelector('.personal-keepsakes'),previous);
      assert(requestedMonths.includes(previous));
      assert.equal(await page.locator('#homeAttendanceView,#homeAttendanceContent').count(),0);
      assert((await page.locator('.personal-section-heading').textContent()).includes(String(Number(previous.slice(5)))));
      assert.equal(await page.locator('.points-amount strong').textContent(),'28');
      await page.locator('#homeRankView').click();
      await page.locator('.member-rank-emblem').waitFor();
      assert(await page.locator('.personal-rank-content').evaluate(node=>node.scrollWidth<=node.clientWidth+1),'rank content overflow');
      assert.equal(await page.locator('.honor-hub-compact').count(),1);
      await page.locator('#homeRankContent [data-honor-tab="skins"]').click();
      assert.equal(await page.locator('[data-honor-skin-choice]').count(),8);
      await page.locator('#homeRankContent [data-honor-tab="collection"]:visible').first().click();
      await page.locator('.member-rank-emblem').waitFor();
      await page.locator('#homeMemoryView').click();await page.locator('.monthly-memory-card').waitFor();
      await page.locator('#homeMemoryView').click();
      // A delayed medal response cannot reopen the panel after selecting another tile.
      delayRank=true;await page.locator('#homeRankView').click();
      await page.waitForFunction(()=>document.querySelector('#homeRankView')?.disabled);
      while(!releaseRank)await sleep(20);
      await page.locator('#homeMemoryView').click();releaseRank();
      await page.waitForFunction(()=>!document.querySelector('#homeRankView')?.disabled);
      assert(await page.locator('#homeRankContent').isHidden());
      assert.equal(await page.locator('#homeMemoryView').getAttribute('aria-selected'),'true');
      assert.deepEqual(errors,[]);
      console.log('Verified personal center:',account);
      await context.close();
    }
    console.log('PASS: CM, LEAD and acting GSM; 320/390/1280px; month linkage, honor tabs, delayed-response guard. Simulated data only.');
  }finally{if(browser)await browser.close();server.kill();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
