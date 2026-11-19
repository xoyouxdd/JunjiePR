'use strict';
const assert=require('node:assert/strict');
const {chromium}=require('./support/browser-runtime.cjs');
const {createScreenFixture}=require('./support/rotation-screen-fixture.cjs');
async function main(){
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{for(const width of [1280,390]){
  const page=await browser.newPage({viewport:{width,height:width===390?844:800}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const employee={pid:'1',name:'测试甲',state:'onpost',flags:[],tags:[],start:420,end:1200,line:'A',post:'塔台',lineStart:580};
  const day={status:'running',lines:[{id:'A',group:'A',active:true,posts:[{name:'塔台',open:true,occ:'1'},{name:'上客',open:true,occ:null}]}],persons:[employee]};
  let auth;
  // Exercise the retained screen-adjustment integration in an isolated fixture.
  const screen=createScreenFixture({version:1,clock:{date:'2026-10-11',minute:600,mode:'sim',paused:true},settings:{departEarly:0},day}, {
   adjust(url){
    if(url.endsWith('/login'))auth={active:true,name:'测试TR · 临时授权',temporary:true,can_authorize:false,expires_at:new Date(Date.now()+600000).toISOString()};
    if(url.endsWith('/logout'))auth=null;
    if(url.endsWith('/status'))return auth||{active:false};
    if(url.endsWith('/preview'))return {expected_snapshot:'b'.repeat(64),summary:'现场调整',changes:['测试甲：A · 塔台 → 下线']};
    return {ok:true};
   }
  });
  const origin=await screen.start();
  try{
  await page.goto(`${origin}/rotation/screen`);
  await page.locator('.rt-line').first().waitFor();
  await page.getByRole('button',{name:'登录调整',exact:true}).click();
  await page.locator('[name=mode]').selectOption('tr');await page.locator('[name=account]').fill('TRTEST');await page.locator('[name=credential]').fill('123456789012');
  await page.getByRole('dialog').getByRole('button',{name:'登录调整',exact:true}).click();
  await page.getByRole('button',{name:'退出调整',exact:true}).waitFor();
  assert.match(await page.locator('#rtAdjustment').innerText(),/TR授权剩余/);
  assert.equal(await page.getByRole('button',{name:'TR临时授权',exact:true}).count(),0);
  await page.locator('[data-post="A#0"]').click();
  await page.getByRole('button',{name:'撤岗 / 修改撤岗时间',exact:true}).click();
  assert.equal(await page.locator('[name=closeMode]').inputValue(),'push');
  await page.getByRole('button',{name:'预览影响',exact:true}).click();
  await page.getByRole('heading',{name:'确认调整',exact:true}).waitFor();
  assert.match(await page.getByRole('dialog').innerText(),/测试甲.*塔台.*下线/);
  await page.getByRole('button',{name:'执行调整',exact:true}).click();
  await page.getByRole('heading',{name:'调整已完成',exact:true}).waitFor();
  const writes=()=>screen.writes.filter(x=>x.url.endsWith('/act'));
  assert.equal(writes().length,1);
  await page.getByRole('button',{name:'继续调整',exact:true}).click();
  const resting={...employee,pid:'meal',name:'餐休人员',state:'meal',line:null,lineStart:null,breakKind:'meal',readyAt:630,assign:null};
  screen.updateDay({...day,persons:[employee,resting]});
  await page.evaluate(()=>RT.load());
  const meal=page.locator('[data-drag-pid="meal"]'),target=page.locator('[data-post="A#0"]');
  assert.equal(await meal.getAttribute('draggable'),'true');
  const transfer=await page.evaluateHandle(()=>new DataTransfer());
  await meal.dispatchEvent('dragstart',{dataTransfer:transfer});await target.dispatchEvent('drop',{dataTransfer:transfer});
  assert.match(await page.getByRole('dialog').innerText(),/剩余餐休 30 分钟.*测试甲将下线/);
  await page.locator('[name=reason]').fill('现场应急支援');await page.getByRole('button',{name:'预览影响',exact:true}).click();
  await page.getByRole('button',{name:'执行调整',exact:true}).click();
  await page.getByRole('heading',{name:'调整已完成',exact:true}).waitFor();
  const forced=writes().at(-1).body;
  assert.equal(forced.action,'move');assert.equal(forced.force,true);assert.equal(forced.reason,'现场应急支援');assert.equal(forced.pid,'meal');
  await page.getByRole('button',{name:'继续调整',exact:true}).click();
  const postTransfer=await page.evaluateHandle(()=>new DataTransfer());
  await page.locator('[data-drag-post="A#0"]').dispatchEvent('dragstart',{dataTransfer:postTransfer});
  await page.locator('[data-post="A#1"]').dispatchEvent('drop',{dataTransfer:postTransfer});
  await page.locator('[name=swapMode]').selectOption('with_people');
  await page.getByRole('button',{name:'预览影响',exact:true}).click();await page.getByRole('button',{name:'执行调整',exact:true}).click();
  await page.getByRole('heading',{name:'调整已完成',exact:true}).waitFor();
  assert.equal(writes().at(-1).body.swapMode,'with_people');
  await page.getByRole('button',{name:'继续调整',exact:true}).click();
  await page.evaluate(()=>{adjustmentState.expires_at=new Date(Date.now()-1).toISOString();adjustmentTick();});
  await page.getByRole('button',{name:'登录调整',exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(errors,[]);
  console.log(`PASS ${width}px: TR login, countdown, no delegation, close preview, one submit, completion exit and expiry`);
  }finally{try{await page.close();}finally{await screen.close();}}
 }}finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
