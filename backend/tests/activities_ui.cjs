'use strict';
const assert=require('node:assert/strict');
const net=require('node:net');
const path=require('node:path');
const fs=require('node:fs');
const {spawn}=require('node:child_process');
const {chromium}=require('playwright');
const backend=path.resolve(__dirname,'..');
const pause=ms=>new Promise(r=>setTimeout(r,ms));

async function run(){
  const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));
  const port=socket.address().port;await new Promise(r=>socket.close(r));
  const base='http://127.0.0.1:'+port;
  const server=spawn(path.join(backend,'.venv/Scripts/python.exe'),[path.join(__dirname,'activities_fixture.py'),String(port)],{cwd:backend,windowsHide:true});
  let log='',browser;server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);
  const output=path.resolve(backend,'../output/activities-ui');fs.mkdirSync(output,{recursive:true});
  try{
    let health;for(let i=0;i<120;i++){try{const r=await fetch(base+'/health');if(r.ok){health=await r.json();break;}}catch{}await pause(250);}assert(health,log);
    browser=await chromium.launch({channel:'msedge',headless:true});
    const errors=[];
    async function session(no){
      const context=await browser.newContext({viewport:{width:1280,height:1000},hasTouch:no==='CMTEST02'});
      // LAN HTTP browsers may expose crypto without secure-context randomUUID.
      await context.addInitScript(()=>Object.defineProperty(Crypto.prototype,'randomUUID',{value:undefined,configurable:true}));
      const r=await context.request.post(base+'/api/login',{data:{employee_no:no,password:'1234'}});assert(r.ok(),await r.text());
      await context.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}});
      const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto(base+'/');
      await page.locator('[data-tab="activities"]:visible').first().click();
      await page.locator('#activitiesPage h2').waitFor();return {page,context};
    }
    const gsm=await session('GSMTEST01');
    async function create(kind,title){
      await gsm.page.setViewportSize({width:390,height:950});
      await gsm.page.getByRole('button',{name:'创建活动',exact:true}).click();
      const modal=gsm.page.getByRole('dialog');await modal.locator('[name=title]').fill(title);
      await modal.locator('[name=kind]').selectOption(kind);
      await modal.locator('[name=description]').fill('参与必得，缺货可退回对应积点。');
      await modal.locator('[name=location]').fill('测试领取处，联系活动发起人');
      const lot=modal.locator('.activity-lot-editor').first();
      await lot.locator('[name=lot_name]').fill(kind==='draw'?'欢乐20点奖池':'精品礼盒竞拍');
      await lot.locator('[name=prize_name]').fill('纪念礼盒');
      const photoEditor=lot.locator('[data-prize-photo-editor]');
      assert.equal(await photoEditor.locator('[data-photo-file="camera"]').getAttribute('capture'),'environment');
      const photoBytes=Buffer.from(await gsm.page.evaluate(()=>{
        const canvas=document.createElement('canvas');canvas.width=2400;canvas.height=1800;const ctx=canvas.getContext('2d');
        for(const [index,color] of ['rgb(200,30,30)','rgb(30,180,70)','rgb(30,80,200)'].entries()){ctx.fillStyle=color;ctx.fillRect(index*800,0,800,1800);}
        return canvas.toDataURL('image/png').split(',')[1];
      }),'base64');
      // Capture and album use separate native pickers but the same editor pipeline.
      await photoEditor.locator(`[data-photo-file="${kind==='draw'?'camera':'album'}"]`).setInputFiles({name:'camera-original.png',mimeType:'image/png',buffer:photoBytes});
      await photoEditor.locator('[data-photo-edit-panel]').waitFor({state:'visible'});
      assert.equal(await photoEditor.locator('[data-photo-name]').textContent(),'纪念礼盒');
      const setRange=async(name,value)=>photoEditor.locator(`[data-photo-control="${name}"]`).evaluate((node,value)=>{node.value=String(value);node.dispatchEvent(new Event('input',{bubbles:true}));},value);
      const centerPixel=()=>photoEditor.locator('canvas').evaluate(canvas=>Array.from(canvas.getContext('2d').getImageData(Math.floor(canvas.width/2),Math.floor(canvas.height/2),1,1).data));
      if(kind==='draw'){
        await setRange('zoom',2);await setRange('x',0);let pixel=await centerPixel();assert(pixel[0]>pixel[2]+100,'left crop must show red area');
        await setRange('x',100);pixel=await centerPixel();assert(pixel[2]>pixel[0]+100,'right crop must show blue area');
        await photoEditor.locator('.prize-photo-adjust summary').click();await setRange('brightness',110);await setRange('saturation',80);
        assert((await centerPixel())[0]>pixel[0],'optional color/brightness adjustment changes pixels');
      }else{await photoEditor.locator('[data-photo-control="ratio"]').selectOption('1.3333333333');}
      assert(await gsm.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'photo editor must fit phone');
      await photoEditor.locator('canvas').scrollIntoViewIfNeeded();
      await gsm.page.screenshot({path:path.join(output,`photo-editor-${kind}-390.png`)});
      await photoEditor.getByRole('button',{name:'确认照片并压缩'}).click();
      await photoEditor.locator('[data-photo-edit-panel]').waitFor({state:'hidden'});
      assert((await photoEditor.locator('[data-photo-size]').textContent()).includes('KB'));
      const confirmedUrl=await photoEditor.locator('.prize-photo-result img').getAttribute('src');
      await photoEditor.getByRole('button',{name:'重新裁剪'}).click();await setRange('x',0);
      await photoEditor.getByRole('button',{name:'取消裁剪'}).click();
      assert.equal(await photoEditor.locator('.prize-photo-result img').getAttribute('src'),confirmedUrl,'cancel preserves the confirmed photo');
      if(kind==='draw'){
        await lot.locator('[name=prize_quantity]').fill('2');await lot.locator('[name=per_person]').fill('2');
        await modal.getByRole('button',{name:'增加奖池 / 拍品'}).click();
        const second=modal.locator('.activity-lot-editor').nth(1);
        await second.locator('[name=lot_name]').fill('精品50点奖池');await second.locator('[name=price]').fill('50');
        await second.locator('[name=prize_name]').fill('高级礼品');
      }else{await lot.locator('[name=step]').fill('5');}
      await modal.getByRole('button',{name:'保存草稿',exact:true}).click();await modal.waitFor({state:'hidden'});
      const data=await gsm.context.request.get(base+'/api/activities').then(r=>r.json());const summary=data.items.find(e=>e.title===title);assert(summary);
      const event=await gsm.context.request.get(base+`/api/activities/${summary.id}`).then(r=>r.json());
      assert(event.lots[0].prizes[0].photo_url,'photo must bind to the saved prize');
      const savedPhoto=await gsm.context.request.get(base+event.lots[0].prizes[0].photo_url);
      assert(savedPhoto.ok());assert.equal(savedPhoto.headers()['content-type'],'image/jpeg');assert((await savedPhoto.body()).length<=500*1024);
      const card=gsm.page.locator(`details[data-event="${event.id}"]`);await card.locator(':scope > summary').click();
      await card.getByRole('button',{name:'核对并发布'}).click();
      await gsm.page.getByRole('dialog').getByRole('button',{name:'确认',exact:true}).click();
      await gsm.page.getByRole('dialog').waitFor({state:'hidden'});
      return event;
    }
    const draw=await create('draw','多奖池必得抽奖界面验证');
    const cm=await session('CMTEST01');
    let card=cm.page.locator(`details[data-event="${draw.id}"]`);await card.locator(':scope > summary').click();
    const pools=card.locator('.activity-lot');
    await pools.first().locator('.activity-prize-photo img').evaluate(image=>image.decode());
    assert.equal(await pools.first().locator('.activity-prize-photo img').getAttribute('alt'),'纪念礼盒');
    await pools.first().getByRole('button',{name:'查看纪念礼盒照片'}).click();
    await cm.page.getByRole('dialog').locator('.activity-photo-full').evaluate(image=>image.decode());
    await cm.page.getByRole('dialog').getByRole('button',{name:'关闭预览',exact:true}).click();
    assert.equal(await pools.count(),2);
    assert(await pools.nth(1).getByRole('button',{name:'可用积点不足，还差5点'}).isDisabled());
    assert((await pools.nth(1).textContent()).includes('高级礼品'));
    // Simulate a successful charge whose response is lost, then retry the same form.
    let lostResponse=false;
    await cm.page.route('**/api/activities/lots/*/join',async route=>{
      if(lostResponse){await route.continue();return;}
      lostResponse=true;
      const committed=await route.fetch();assert(committed.ok(),await committed.text());
      await route.abort('failed');
    });
    await pools.first().getByRole('button',{name:'消耗积点参与'}).click();
    await cm.page.getByRole('dialog').getByRole('button',{name:'确认消耗积点'}).click();
    await cm.page.getByText(/提交结果暂未确认/).last().waitFor();
    await cm.page.getByRole('dialog').getByRole('button',{name:'确认消耗积点'}).click();
    await cm.page.getByRole('dialog').waitFor({state:'hidden'});
    await cm.page.locator('.activity-wallet').getByText('25',{exact:false}).waitFor();
    await gsm.context.request.post(base+`/__fixture/end/${draw.lots[0].id}`);
    const pointCardPage=await cm.context.newPage();
    await pointCardPage.goto(base+'/');
    await pointCardPage.locator('.tabs-desktop [data-tab="activityPoints"]').click();
    const pointCard=pointCardPage.locator('#personalActivityPoints .activity-points-card');
    await pointCard.getByText('已支出 20 点 · 奖品待领取',{exact:true}).waitFor();
    assert.equal(await pointCard.locator('.points-amount strong').textContent(),'45');
    for(const width of [320,390,1280]){
      await pointCardPage.setViewportSize({width,height:1000});
      assert(await pointCardPage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`point card overflow ${width}`);
      await pointCard.screenshot({path:path.join(output,`points-pending-${width}.png`)});
    }
    await pointCardPage.close();
    await gsm.page.getByRole('button',{name:'刷新',exact:true}).click();
    const gmCard=gsm.page.locator(`details[data-event="${draw.id}"]`);
    if(!await gmCard.evaluate(n=>n.open))await gmCard.locator(':scope > summary').click();
    await gmCard.getByRole('button',{name:'缺货退款'}).click();
    const refund=gsm.page.getByRole('dialog');await refund.locator('[name=reason]').fill('现场清点少一份奖品');
    await refund.getByRole('button',{name:'确认',exact:true}).click();await refund.waitFor({state:'hidden'});
    await gmCard.locator('[data-record-state="refunded"]').click();
    await gmCard.locator('.activity-record-list').getByText('已退款',{exact:false}).waitFor();
    await cm.page.getByRole('button',{name:'刷新',exact:true}).click();
    await cm.page.locator('.activity-wallet').getByText('45',{exact:false}).waitFor();
    const auction=await create('auction','积点拍卖界面验证');
    await cm.page.getByRole('button',{name:'刷新',exact:true}).click();
    const ac=cm.page.locator(`details[data-event="${auction.id}"]`);await ac.locator(':scope > summary').click();
    await ac.getByRole('button',{name:'我要出价'}).click();
    await cm.page.getByRole('dialog').getByRole('button',{name:'确认出价并冻结'}).click();await cm.page.getByRole('dialog').waitFor({state:'hidden'});
    assert((await cm.page.locator('.activity-wallet').textContent()).includes('冻结 20'));
    const cm2=await session('CMTEST02');const ac2=cm2.page.locator(`details[data-event="${auction.id}"]`);await ac2.locator(':scope > summary').click();
    await ac2.getByRole('button',{name:'我要出价'}).click();await cm2.page.getByRole('dialog').locator('[name=amount]').fill('30');
    await cm2.page.getByRole('dialog').getByRole('button',{name:'确认出价并冻结'}).click();await cm2.page.getByRole('dialog').waitFor({state:'hidden'});
    await cm.page.getByRole('button',{name:'刷新',exact:true}).click();
    await cm.page.locator('.activity-wallet').getByText('冻结 0 点',{exact:true}).waitFor();
    await gsm.context.request.post(base+`/__fixture/end/${auction.lots[0].id}`);
    await gsm.page.getByRole('button',{name:'刷新',exact:true}).click();
    const ga=gsm.page.locator(`details[data-event="${auction.id}"]`);
    if(!await ga.evaluate(n=>n.open))await ga.locator(':scope > summary').click();
    await ga.getByRole('button',{name:'已发放，等待本人签收'}).click();
    await gsm.page.getByRole('dialog').getByRole('button',{name:'确认',exact:true}).click();await gsm.page.getByRole('dialog').waitFor({state:'hidden'});
    await cm2.page.setViewportSize({width:390,height:1000});
    await cm2.page.getByRole('button',{name:'刷新',exact:true}).click();
    await ac2.getByRole('button',{name:'本人签字确认领取'}).click();
    const signing=cm2.page.getByRole('dialog');
    await signing.locator('[name=accepted]').check();
    await signing.getByRole('button',{name:'提交签收并归档'}).click();
    await cm2.page.getByText('请手写签名后提交',{exact:true}).waitFor();
    const pad=signing.locator('canvas');await pad.scrollIntoViewIfNeeded();
    let box=await pad.boundingBox();
    const touch=await cm2.context.newCDPSession(cm2.page);
    await touch.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:box.x+35,y:box.y+box.height*.6}]});
    for(let i=0;i<20;i++)await touch.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:box.x+35+i*(box.width-70)/20,y:box.y+box.height*(.5+.25*Math.sin(i*.8))}]});
    await touch.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await touch.detach();
    assert(await cm2.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'signature modal overflow');
    await cm2.page.screenshot({path:path.join(output,'signature-390.png'),animations:'disabled'});
    await signing.getByRole('button',{name:'提交签收并归档'}).click();await signing.waitFor({state:'hidden'});
    await ac2.getByRole('button',{name:'查看本人签收档案'}).waitFor();
    await ac2.getByRole('button',{name:'查看本人签收档案'}).click();
    await cm2.page.getByRole('dialog').locator('.receipt-signature-image').evaluate(image=>image.decode());
    await cm2.page.screenshot({path:path.join(output,'signature-archive-390.png'),animations:'disabled'});
    await cm2.page.getByRole('dialog').getByRole('button',{name:'关闭档案'}).click();
    await gsm.page.getByRole('button',{name:'刷新',exact:true}).click();
    await ga.locator('[data-record-state="signed"]').click();
    await ga.locator('.activity-record-list').getByText('已签收',{exact:false}).waitFor();
    // Actual downloads, including on the narrow GSM layout, must produce XLSX files.
    await gsm.page.setViewportSize({width:390,height:950});
    for(const [name,eventCard] of [['auction',ga],['draw',gmCard]]){
      const downloading=gsm.page.waitForEvent('download');
      await eventCard.getByRole('link',{name:'导出核销Excel',exact:true}).click();
      const download=await downloading;
      assert(download.suggestedFilename().endsWith('.xlsx'));
      const target=path.join(output,`receipt-${name}.xlsx`);
      await download.saveAs(target);assert.equal(await download.failure(),null);
      assert.equal(fs.readFileSync(target).subarray(0,2).toString(),'PK');
    }
    assert.equal(await cm.page.locator('[data-activity-export]').count(),0,'participants cannot export the full recipient list');
    await cm.page.getByRole('button',{name:'刷新',exact:true}).click();
    await cm.page.locator(`details[data-event="${auction.id}"]`).getByRole('button',{name:'已开奖 / 成交',exact:true}).waitFor();
    for(const width of [320,390,1280]){
      for(const [name,session] of [['employee',cm],['manager',gsm]]){
        await session.page.setViewportSize({width,height:1100});
        assert(await session.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${name} overflow ${width}`);
        await session.page.locator('#activitiesPage').screenshot({path:path.join(output,`${name}-${width}.png`)});
      }
    }
    // The creation dialog must also fit a narrow phone.
    await gsm.page.setViewportSize({width:320,height:900});await gsm.page.getByRole('button',{name:'创建活动',exact:true}).click();
    await gsm.page.getByRole('dialog').locator('[name=kind]').selectOption('auction');
    assert(await gsm.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'creation modal overflow');
    await gsm.page.getByRole('dialog').screenshot({path:path.join(output,'create-320.png')});
    await gsm.page.getByRole('dialog').getByRole('button',{name:'取消',exact:true}).click();
    assert.deepEqual(errors,[]);
    console.log('Activities UI passed: camera/album pickers, mobile crop/filter/preview/compression and private photo display; creation/publication, guaranteed draw/refund, bid/outbid/settlement, staff issue and employee signature/archive, 320/390/1280px.');
  }finally{if(browser)await browser.close();server.kill();}
}
run().catch(e=>{console.error(e);process.exitCode=1;});
