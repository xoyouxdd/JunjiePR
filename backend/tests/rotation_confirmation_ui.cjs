'use strict';
// Real mobile DOM fixture. No production server, accounts or data are used.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('./support/browser-runtime.cjs');
const {openRotationPage}=require('./support/rotation-browser.cjs');

async function checkRepeatMeal(page){
  await page.evaluate(()=>{
    me={kind:'supervisor',name:'测试主管',capabilities:{can_operate:true,can_manage:true}};
    RT.data.day={date:'2026-10-11'};
    RT.render=()=>{};
    RT.load=async()=>{};
    window.mealPerson={pid:'1',name:'测试员工',ate:true,state:'pending',start:420,end:1000,
      mealOptions:{canChoose:true},confirmationTokens:{arrive:'a'.repeat(64)}};
    window.mealPosts=[];
    window.fetch=async (path,options)=>{
      if(path!=='/api/rotation-board/act'||options?.method!=='POST')throw new Error('Unexpected rotation request');
      const body=JSON.parse(options.body);
      window.mealPosts.push(body);
      if(body.action==='pool') await new Promise(resolve=>{window.finishMealPost=resolve;});
      return Response.json({ok:true});
    };
    forecastArrive(window.mealPerson);
    window.mealForm=document.querySelector('[data-form]');
  });

  const dialog=page.getByRole('dialog');
  const confirmation=dialog.getByRole('heading',{name:'再次进入吃饭区'});
  const choice=dialog.getByLabel('本次安排');
  const submit=dialog.getByRole('button',{name:'确定',exact:true});
  await choice.selectOption('meal');
  await submit.click();
  await confirmation.waitFor();
  assert.equal(await page.evaluate(()=>activeRotationConfirmation?.tokenAction),'arrive');
  await dialog.getByRole('button',{name:'取消',exact:true}).click();
  await dialog.getByRole('heading',{name:'测试员工 · 确认餐休'}).waitFor();
  assert.equal(await page.evaluate(()=>document.querySelector('[data-form]')===window.mealForm),true);
  assert.equal(await choice.inputValue(),'meal');
  assert.deepEqual(await page.evaluate(()=>window.mealPosts),[]);
  await choice.selectOption('rest');
  await submit.click();
  await dialog.waitFor({state:'hidden'});
  assert.deepEqual(await page.evaluate(()=>window.mealPosts.map(body=>[body.action,body.meal_choice,body.repeat_meal])),[['arrive','rest',false]]);

  await page.evaluate(()=>forecastPerson(window.mealPerson));
  await dialog.getByRole('button',{name:'移动到人员池'}).click();
  const pool=dialog.getByLabel('目标池');
  await pool.selectOption('meal');
  await submit.click();
  await confirmation.waitFor();
  await page.keyboard.press('Escape');
  assert.equal(await pool.inputValue(),'meal');
  assert.equal(await page.evaluate(()=>window.mealPosts.length),1);
  await submit.click();
  await confirmation.waitFor();
  await dialog.getByRole('button',{name:'继续',exact:true}).click();
  await page.keyboard.press('Escape');
  await page.evaluate(()=>{
    document.querySelector('[data-ok]').click();
    window.finishMealPost();
  });
  await dialog.waitFor({state:'hidden'});
  assert.deepEqual(await page.evaluate(()=>window.mealPosts.slice(1).map(body=>[body.action,body.pool,body.repeat_meal])),[['pool','meal',true]]);
  console.log('Repeat meal: cancel restores the form and selection, continue sends one request');
}

async function main(){
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    const context=await browser.newContext({viewport:{width:390,height:844},deviceScaleFactor:1});
    const page=await context.newPage();
    await openRotationPage(page,'/rotation-board?personal=1');
    await page.evaluate(()=>{
      RT.sync='live';RT.lastSyncedAt=Date.now();RT.fetchedAt=Date.now();
      RT.data={version:1,clock:{date:'2026-10-11',minute:495,paused:true,mode:'sim'},settings:{departEarly:0},
        person:{pid:'1',name:'测试员工',state:'ready',readyAt:480,start:435,end:1000,flags:[],tags:[],canDepart:true,
          task:{status:'assigned',line:'B1',allowedAt:495,deadlineAt:498,beforePosts:[],afterPosts:[],routes:[]},
          confirmationTokens:{depart:'a'.repeat(64)},assign:{line:'B1',entryPost:'塔台协助',mode:'chain',why:'推下班',targetName:'测试目标',targetLine:'B1',targetPost:'分组2',departAt:495}},
        notices:[{kind:'depart',title:'现在出发去 B1 线',body:'在个人待办或大屏确认去轮岗',status:'open'}],
        confirmations:[],today:[],week_minutes:{B1:30},line:{id:'B1',posts:[{name:'塔台协助',open:true,occ:'2',occName:'测试同事'},{name:'分组2',open:true,occ:'3',occName:'测试目标'}]}};
      window.writes=0;
      window.fetch=async(path,options={})=>{
        if(options.method==='POST'){
          if(path!=='/api/rotation-board/me/act')throw new Error('Unexpected rotation request');
          window.writes++;
          const body=JSON.parse(options.body);
          if(body.pid!=='1'||body.confirmation_token!=='a'.repeat(64))throw new Error('wrong step');
          const result={pid:'1',action:'depart',minute:495,line:'B1',post:'塔台协助',state:'onpost',source:'member',assignment:{...RT.data.person.assign}};
          RT.data={...RT.data,version:2,person:{...RT.data.person,state:'onpost',line:'B1',post:'塔台协助',lineStart:495,assign:null,task:null,canDepart:false,confirmationTokens:{}},
            notices:[],confirmations:[result],line:{id:'B1',posts:[{name:'塔台协助',open:true,occ:'1',occName:'测试员工'},{name:'分组2',open:true,occ:'2',occName:'测试同事'}]}};
          return Response.json({ok:true,duplicate:false,result});
        }
        return Response.json(RT.data);
      };
      renderWho();RT.render();renderSyncStatus();
    });
    await page.getByRole('button',{name:'去轮岗',exact:true}).click();
    const modal=page.getByRole('dialog');
    await modal.waitFor();
    assert.match(await modal.innerText(),/进入 B1 线.*塔台协助/s);
    assert.match(await modal.innerText(),/测试目标.*原岗位 B1 · 分组2/s);
    await modal.getByRole('button',{name:'确认去轮岗',exact:true}).click();
    await page.getByRole('heading',{name:'本次确认',exact:true}).waitFor();
    assert.match(await page.locator('.rt-confirmation').first().innerText(),/原岗位：B1 · 分组2/);
    assert.equal(await page.evaluate(()=>window.writes),1);
    assert.equal(await page.locator('[data-member-act]').count(),0);
    const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);
    assert.equal(overflow,false,'Mobile view must not overflow horizontally');
    const out=path.join(__dirname,'../../.handover_review/rotation-mobile-20261011.png');
    fs.mkdirSync(path.dirname(out),{recursive:true});
    await page.locator('#toast.show').waitFor({state:'hidden'});
    await page.screenshot({path:out,fullPage:true});
    await page.evaluate(()=>{RT.sync='offline';renderSyncStatus();});
    assert.match(await page.locator('#rtSync').innerText(),/连接中断/);
    console.log('Rotation mobile DOM: confirm, original target post, one write, result card, offline badge and 390px layout passed');
    const board=await context.newPage();
    await openRotationPage(board,'/rotation-board');
    await checkRepeatMeal(board);
  }finally{await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
