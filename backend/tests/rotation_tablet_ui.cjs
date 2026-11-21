'use strict';
// Actual screen templates, renderer and CSS; synthetic data, no business writes.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('./support/browser-runtime.cjs');
const {createScreenFixture}=require('./support/rotation-screen-fixture.cjs');
const out=path.resolve(__dirname,'../../.handover_review');
function fixture(){
  const persons=[],names=['林晓晨','陈雨欣','李佳宁','王思远','周乐怡','张明轩','黄子涵','许嘉宁','赵雨桐','杨文昊'];let seq=0;
  const person=(state,extra={})=>{const p={pid:String(++seq),name:names[(seq-1)%names.length][0]+names[Math.floor((seq-1)/names.length)%names.length].slice(1),state,role:'rotation',flags:[],tags:[],start:420,end:1080,lineStart:480-(seq%9)*3,...extra};persons.push(p);return p;};
  const definitions=[['A1','A',['塔台','上客1','上协1','下协','大出口商店']],['A2','A',['饮水童车','小C','核销','身高墙','分组1']],['B1','B',['塔台协助','上客2','上协2','分组2','快迎2']],['B2','B',['迎宾童车','迎宾','豹协','汇合','快速汇合']],['C1','C',['上客3','上协3','预演','预演协助','单人队末']],['C2','C',['快迎1','单人迎宾','分组3','上客4','上协4']],['D1','D',['河马池','铁出口','迎宾协助','下客1','下客2']],['D2','D',['铁A','铁B','铁C']],['E','E',['城内巡视','队末']]];
  const lines=definitions.map(([id,group,posts],j)=>({id,group,active:j<7,posts:Array.from({length:6},(_,i)=>{
    const name=posts[i]??(i===5?'末端备用岗':'巡游交接岗位');
    const scheduled=j===0&&i===4,standby=j>=7,vacant=j===1&&i===5;
    const p=scheduled||vacant?null:person(standby?'notyet':'onpost',{
      line:id,post:name,...(i===2?{name:'欧阳沐辰·现场支援',mealEligible:true,preparing:standby?null:'推下班'}:{}),
      ...(i===3?{name:'司马晓筠·轮岗伙伴',flags:['7点岗'],mealEligible:true,preparing:standby?null:'推病假'}:{}),
      ...(j===0&&i===0?{entrySortAt:450+5/60}:{}),
    });
    return {name,key:`${id}#${i}`,open:!scheduled&&!standby,opened:!scheduled,openAt:scheduled?'09:00':null,
      closeAt:j===1&&i===3?'08:45':null,occ:p?.pid||null};
  })}));
  for(const [i,deadlineAt] of [483,482,481.2,481.1,480.5].entries())person('ready',{
    pid:`priority-${i}`,name:`待上岗测试 ${i+1}`,readyAt:480,canDepart:true,breakKind:i===2?'meal':'rest',
    task:{deadlineAt,routes:i===4?[{name:'测试伙伴',from:'休息室',to:'上客1'}]:[]},
    assign:{line:['A1','C2','D1'][i%3],departAt:480,why:'正常轮岗'},
  });
  for(let i=0;i<14;i++)person('walkback',{pid:`waiting-${i}`,walkbackSince:475-i,after:'rest'});
  for(let i=15;i>=0;i--)person('rest',{pid:`rest-${i}`,name:`休息伙伴 ${i+1}`,readyAt:484+i,assign:i%3===0?{line:'B1',mode:'push',departAt:484+i}:null});
  for(let i=5;i>=0;i--)person('meal',{pid:`meal-${i}`,name:`餐休伙伴 ${i+1}`,readyAt:506+i*3});
  person('done',{pid:'sick',name:'病假测试',leaveReason:'病假'});
  person('away',{pid:'outside',name:'出圈测试',away:{reason:'专项任务'}});
  person('notyet',{pid:'future-outside',name:'待出圈测试',initialPool:'away',start:900});
  person('excluded',{pid:'ojt',name:'OJT测试',role:'excluded',label:'OJT'});
  person('done',{pid:'ended',name:'已下班测试'});
  person('notyet',{pid:'later',name:'待到班测试',start:900});
  for(let i=0;i<46;i++)person('done',{name:`下班人员 ${i+1}`});
  person('notyet',{pid:'check-in',name:'可签到测试',upcomingShift:true,start:485,canBatchCheckIn:true});
  person('exception',{pid:'timeout',name:'上岗超时测试',exception:{assignment:{line:'A1'},allowedAt:470,at:473,deadlineAt:473}});
  for(let i=1;i<=3;i++)person('exception',{name:`超时伙伴 ${i}`,exception:{assignment:{line:'A1'},allowedAt:470-i,at:473-i,deadlineAt:473-i}});
  const off=[
    {pid:'sick-shift',name:'当前班次病假',label:'病假',start:470,end:500},
    {pid:'sick-later',name:'后续班次病假',label:'病假',start:510,end:600},
    {pid:'sick-ended',name:'已结束班次病假',label:'病假',start:450,end:480},
    {pid:'sick-all-day',name:'未标时间的病假',label:'病假'},
  ];
  const date='2026-10-11';
  return {version:1,clock:{date,minute:480,paused:true,mode:'sim'},settings:{departEarly:0},day:{date,status:'live',ruleVersion:2,taskWorkflow:2,lines,persons,off}};
}
async function main(){
  const screen=createScreenFixture(fixture(), {demo:true});
  const preview=process.argv.includes('--serve');
  const origin=await screen.start(preview?18112:0);
  if(preview){console.log(`Tablet layout preview: ${origin}/`);return;}
  let browser;
  try{browser=await chromium.launch({channel:'msedge',headless:true});fs.mkdirSync(out,{recursive:true});for(const [width,height] of [[1920,1080],[1280,800],[1366,1024],[1180,820],[1024,768],[800,1280],[390,844]]){
    const page=await browser.newPage({viewport:{width,height}}),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(`${origin}/`);
    await page.locator('.rt-screen-live .rt-line').first().waitFor();
    assert.equal(await page.locator('.rt-line').count(),9);
    const entry=page.locator('[data-f-slot="A1#0"] .rt-post-entry-time');
    assert.equal(await entry.innerText(),'入线 07:30:05');
    assert(await entry.evaluate(el=>el.getBoundingClientRect().right<=el.closest('.rt-post').getBoundingClientRect().right+1));
    assert.equal(await page.locator('.rt-screen-live [data-f-pool]').count(),8);
    const pool=key=>page.locator(`[data-f-pool="${key}"]`);
    const count=key=>page.locator(`[data-screen-count="${key}"]`).innerText();
    const total=key=>page.locator(`[data-rotation-total="${key}"]`).innerText();
    assert(await page.locator('#rtScreenSummary').isVisible());
    assert.deepEqual(await Promise.all(['pending','ready','exception','checkin'].map(count)),['14','5','4','1']);
    const totals=()=>Promise.all(['posts','rest','sick','away'].map(total));
    assert.deepEqual(await totals(),['41','30','3','1']);
    const totalsBox=await page.locator('#rtScreenTotals').boundingBox();
    assert(totalsBox.x+totalsBox.width<=width);
    if(width>1040){
      const summaryBox=await page.locator('#rtScreenSummary').boundingBox();
      assert(totalsBox.x>=summaryBox.x+summaryBox.width);
    }
    assert.equal(await pool('ready').locator('[data-f-board]').count(),5);
    assert.equal(await page.locator('[data-f-board]').count(),5);
    assert.equal(await pool('ready').locator('[data-f-board]').first().getAttribute('data-f-board'),'priority-4');
    assert.equal(await pool('pending').locator('[data-f-arrive]').count(),14);
    assert.equal(await pool('pending').locator('[data-f-arrive]').first().getAttribute('data-f-arrive'),'waiting-13');
    assert.equal(await pool('checkin').locator('[data-f-check-in="check-in"]').count(),1);
    assert.equal(await pool('rest').locator('[data-f-check-in]').count(),0);
    assert.match(await pool('rest').locator('.rt-chip').first().innerText(),/休息伙伴 1\b/);
    assert.match(await pool('meal').locator('.rt-chip').first().innerText(),/餐休伙伴 1\b/);
    assert(await pool('exception').getByText('上岗超时测试',{exact:true}).isVisible());
    assert.equal(await pool('exception').locator('.rt-chip:visible').count(),4);
    assert.match(await pool('exception').locator('.rt-chip').first().innerText(),/超时伙伴 3/);
    const checkinBox=await pool('checkin').boundingBox();
    assert(checkinBox.y<=(await pool('ready').boundingBox()).y);
    assert(checkinBox.y<=(await pool('pending').boundingBox()).y);
    const exceptionBox=await pool('exception').boundingBox();
    assert(exceptionBox.y<=(await pool('rest').boundingBox()).y);
    assert(exceptionBox.y<=(await pool('meal').boundingBox()).y);
    for(const [region,name] of [['away','病假测试'],['away','出圈测试'],['other','OJT测试']]){
      assert.equal(await pool(region).getByText(name,{exact:true}).count(),1);
      assert.equal(await page.locator('.rt-pool').getByText(name,{exact:true}).count(),1);
    }
    const other=page.locator('details[data-screen-detail="other"]');
    assert.equal(await other.evaluate(el=>el.open),false);
    assert.equal(await other.getByText('已下班测试',{exact:true}).isVisible(),false);
    await other.locator('summary').click();
    assert(await other.getByText('已下班测试',{exact:true}).isVisible());
    assert(await other.getByText('待到班测试',{exact:true}).isVisible());
    assert(await other.getByText('下班人员 46',{exact:true}).isVisible());
    await other.locator('summary').click();
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${width}: horizontal overflow`);
    const pending=pool('pending'),more=pending.locator('details[data-screen-detail="more:pending"]');
    const arrivals=pending.locator('[data-f-arrive]');
    const rowsFit=list=>list.evaluate(el=>{
      const box=el.getBoundingClientRect(), cards=[...el.querySelectorAll('.rt-chip')];
      return el.scrollHeight<=el.clientHeight+1&&cards.every(card=>{
        const bounds=card.getBoundingClientRect(), button=card.querySelector('button').getBoundingClientRect();
        return bounds.top>=box.top-1&&bounds.bottom<=box.bottom+1&&button.bottom<=bounds.bottom+1;
      });
    });
    const previewLimit=width>=1200?3:4;
    assert.equal(await pending.locator('[data-f-arrive]:visible').count(),previewLimit);
    assert.equal(await pool('ready').locator('[data-f-board]:visible').count(),previewLimit);
    const columns=list=>list.evaluate(el=>getComputedStyle(el).gridTemplateColumns.trim().split(/\s+/).length);
    assert.equal(await columns(pending.locator(':scope > .rt-chips')),width>=1200?3:2);
    if(width>600&&width<=1040){
      const rest=pool('rest');
      assert((await rest.boundingBox()).width>=width*.85,`${width}: rest area should extend across the secondary stack`);
      assert((await columns(rest.locator(':scope > .rt-chips')))>=2,`${width}: rest cards should not form a narrow single column`);
    }
    assert.equal(await more.evaluate(el=>el.open),false);
    assert(await rowsFit(pending.locator(':scope > .rt-chips')),`${width}: collapsed priority list must show complete cards and buttons`);
    await more.getByText(`展开其余 ${14-previewLimit} 人`,{exact:true}).click();
    assert.equal(await pending.locator('[data-f-arrive]:visible').count(),14);
    assert(await rowsFit(more.locator(':scope > .rt-chips')),`${width}: expanded priority list must show complete cards and buttons`);
    const lastArrival=arrivals.last();
    await lastArrival.scrollIntoViewIfNeeded();
    assert(await lastArrival.evaluate(el=>{
      const {top,bottom}=el.getBoundingClientRect();
      const list=el.closest('.rt-chips').getBoundingClientRect();
      return top>=Math.max(0,list.top)-1&&bottom<=Math.min(innerHeight,list.bottom)+1;
    }));
    assert.equal(await page.locator('.pending-add').count(),1);assert.deepEqual(errors,[]);
    if(width===390){
      await lastArrival.click();
      assert.match(await page.locator('#toast').innerText(),/布局演示，仅展示排版/);
      assert.equal(await page.getByRole('dialog').count(),0);
    }
    await more.getByText('收起其余人员',{exact:true}).click();
    await page.evaluate(()=>scrollTo(0,0));
    await page.screenshot({path:path.join(out,`rotation-tablet-${width}x${height}.png`),fullPage:width!==1920});
    if(width===1920){
      const slots=page.locator('.rt-lines [data-f-slot]');
      assert.equal(await slots.count(),54);
      const bottom=await slots.evaluateAll(items=>Math.max(...items.map(node=>node.getBoundingClientRect().bottom)));
      if(bottom>height+1){
        const lines=await page.locator('.rt-lines .rt-line').evaluateAll(items=>items.map(node=>({top:Math.round(node.getBoundingClientRect().top),postHeights:[...node.querySelectorAll('[data-f-slot]')].map(post=>Math.round(post.getBoundingClientRect().height))})));
        assert.fail(`9 lines × 6 posts must fit the 1080p opening viewport, bottom ${bottom}. ${JSON.stringify(lines)}`);
      }
    }
    await page.evaluate(()=>{RT.data.clock.minute=434;RT.data.day.persons.push({pid:'waiting715',name:'待开始休息测试',state:'notyet',upcomingShift:true,start:435,end:975,flags:[],tags:[]});RT.render();});
    const unopened=page.locator('[data-f-pool="rest"] .rt-chip').filter({hasText:'待开始休息测试'});
    assert.match(await unopened.innerText(),/准备上班 · 距到班 1 分钟/);
    assert.equal(await unopened.locator('button').count(),0);
    assert.deepEqual(await totals(),['41','30','2','1']);
    await page.evaluate(()=>{
      RT.data.clock.minute=500;
      Object.assign(RT.data.day.persons.find(p=>p.pid==='outside'),{state:'rest',away:null,readyAt:525});
      RT.render();
    });
    assert.deepEqual(await totals(),['41','31','2','0']);
    await page.evaluate(()=>{RT.data.clock.minute=510;RT.render();});
    assert.equal(await total('sick'),'3');
    await page.evaluate(()=>{
      RT.data.day.persons=RT.data.day.persons.filter(p=>p.state==='onpost');
      RT.data.day.off=[];
      RT.data.day.lines[0].posts[0].open=false;
      RT.data.day.lines[1].active=false;
      RT.render();
    });
    assert.equal(await page.locator('[data-f-pool].is-empty').count(),7);
    assert.deepEqual(await Promise.all(['pending','ready','exception'].map(count)),['0','0','0']);
    assert.deepEqual(await totals(),['34','0','0','0']);
    assert.equal(await page.locator('[data-screen-count="checkin"]').isVisible(),false);
    assert.equal(await pool('checkin').count(),0);
    const emptyHeight=(await pool('pending').boundingBox()).height;
    assert(emptyHeight<=48,`${width}: empty region must stay within one compact row`);
    await page.evaluate(()=>{RT.data.day=null;RT.render();});
    assert.match(await page.locator('#rtApp').innerText(),/没有开始/);
    assert.equal(await page.locator('#rtScreenSummary').isVisible(),false);
    assert.equal(await page.locator('#rtScreenTotals').isVisible(),false);
    assert(!await page.locator('.rt-top').evaluate(el=>getComputedStyle(el).gridTemplateAreas.includes('metrics')));
    await page.close();console.log(`PASS ${width}x${height}: populated, empty and unstarted display`);
  }}finally{try{await browser?.close();}finally{await screen.close();}}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
