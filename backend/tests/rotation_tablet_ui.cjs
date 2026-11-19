'use strict';
// Actual screen templates, renderer and CSS; synthetic data, no business writes.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('./support/browser-runtime.cjs');
const {createScreenFixture}=require('./support/rotation-screen-fixture.cjs');
const out=path.resolve(__dirname,'../../.handover_review');
function fixture(){
  const persons=[],names=['林晓晨','陈雨欣','李佳宁','王思远','周乐怡','张明轩','黄子涵','许嘉宁','赵雨桐','杨文昊'];let seq=0;
  const person=(state,extra={})=>{const p={pid:String(++seq),name:names[(seq-1)%names.length][0]+names[Math.floor((seq-1)/names.length)%names.length].slice(1),state,flags:[],tags:[],start:420,end:1080,lineStart:480-(seq%9)*3,...extra};persons.push(p);return p;};
  const definitions=[['A1','A',['塔台','上客1','上协1','下协','大出口商店']],['A2','A',['饮水童车','小C','核销','身高墙','分组1']],['B1','B',['塔台协助','上客2','上协2','分组2','快迎2']],['B2','B',['迎宾童车','迎宾','豹协','汇合','快速汇合']],['C1','C',['上客3','上协3','预演','预演协助','单人队末']],['C2','C',['快迎1','单人迎宾','分组3','上客4','上协4']],['D1','D',['河马池','铁出口','迎宾协助','下客1','下客2']],['D2','D',['铁A','铁B','铁C']],['E','E',['城内巡视','队末']]];
  const lines=definitions.map(([id,group,posts],j)=>({id,group,active:j<7,posts:posts.map((name,i)=>{const scheduled=j===0&&i===4,standby=j>=7,p=scheduled?null:person(standby?'notyet':'onpost',{line:id,post:name});return {name,open:!scheduled&&!standby,opened:!scheduled,openAt:scheduled?'09:00':null,closeAt:j===1&&i===3?'08:45':null,occ:p?.pid||null};})}));
  for(let i=0;i<3;i++)person('ready',{readyAt:480,assign:{line:['A1','C2','D1'][i],departAt:480,mode:'push'}});
  for(let i=0;i<14;i++)person('walkback',{walkbackSince:475-i%5,after:'rest'});
  for(let i=0;i<16;i++)person('rest',{readyAt:484+i,assign:i%3===0?{line:'B1',mode:'push',departAt:484+i}:null});
  for(let i=0;i<6;i++)person('meal',{readyAt:506+i*3});
  person('done',{pid:'sick',name:'病假测试',leaveReason:'病假'});
  person('away',{pid:'outside',name:'出圈测试',away:{reason:'专项任务'}});
  person('excluded',{pid:'ojt',name:'OJT测试',role:'excluded',label:'OJT'});
  return {version:1,test_mode:true,clock:{date:'2026-10-11',minute:480,paused:true,mode:'sim'},settings:{departEarly:0},day:{status:'running',lines,persons}};
}
async function main(){
  const screen=createScreenFixture(fixture(), {demo:true});
  const preview=process.argv.includes('--serve');
  const origin=await screen.start(preview?18112:0);
  if(preview){console.log(`Tablet layout preview: ${origin}/`);return;}
  let browser;
  try{browser=await chromium.launch({channel:'msedge',headless:true});fs.mkdirSync(out,{recursive:true});for(const [width,height] of [[1280,800],[1366,1024],[1180,820],[1024,768],[800,1280],[390,844]]){
    const page=await browser.newPage({viewport:{width,height}}),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(`${origin}/`);
    await page.locator('.rt-line').first().waitFor();assert.equal(await page.locator('.rt-line').count(),9);assert.equal(await page.locator('.rt-sect').count(),5);
    assert.deepEqual(await page.locator('.rt-outside [data-outside-kind]').evaluateAll(nodes=>nodes.map(n=>n.dataset.outsideKind)),['病假','出圈','OJT']);
    for(const [kind,name] of [['病假','病假测试'],['出圈','出圈测试'],['OJT','OJT测试']]){
      assert.equal(await page.locator(`[data-outside-kind="${kind}"] .rt-nm`).getByText(name,{exact:true}).count(),1);
      assert.equal(await page.locator('.rt-nm').getByText(name,{exact:true}).count(),1);
    }
    const size=await page.evaluate(()=>({width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight}));
    assert(size.width<=width,`${width}: horizontal overflow`);
    if(width>=960){
      assert(size.height<=height,`${width}: page exceeds screen`);
      const unreachable=await page.locator('.rt-sect').evaluateAll(sections=>sections.flatMap(section=>{
        const list=section.querySelector('.rt-chips');
        const names=list.querySelectorAll('.rt-nm');
        if(section.getBoundingClientRect().bottom>innerHeight+1||names.length===0)return [section.innerText];
        const visible=[names[0],names[names.length-1]].every(name=>{
          name.scrollIntoView({block:'nearest'});
          const frame=list.getBoundingClientRect(),row=name.getBoundingClientRect();
          return row.top>=frame.top-1&&row.bottom<=frame.bottom+1;
        });
        list.scrollTop=0;
        return visible?[]:[section.querySelector('h2').textContent];
      }));
      assert.deepEqual(unreachable,[],`${width}: employees must be reachable inside each pool`);
    }
    assert.equal(await page.locator('.pending-add').count(),1);assert.equal(await page.locator('.pending-remove').count(),1);assert.deepEqual(errors,[]);
    if(width===390){
      await page.locator('.rt-arrivals [data-act=arrive]').first().click();
      assert.match(await page.locator('#toast').innerText(),/布局演示，仅展示排版/);
      assert.equal(await page.getByRole('dialog').count(),0);
    }
    await page.screenshot({path:path.join(out,`rotation-tablet-${width}x${height}.png`),fullPage:true});
    await page.evaluate(()=>{RT.data.clock.minute=434;RT.data.day.persons.push({pid:'waiting715',name:'待开始休息测试',state:'notyet',scheduledRestStart:435,start:435,end:975,flags:[],tags:[]});RT.render();});
    assert.match(await page.locator('.rt-resting').innerText(),/待开始 1 人/);assert.match(await page.locator('.rt-resting').innerText(),/班次未开始 · 07:15开始休息/);assert.equal(await page.locator('[data-pid=waiting715][data-act]').count(),0);assert.equal(await page.locator('.rt-ready').getByText('待开始休息测试').count(),0);
    await page.evaluate(()=>{RT.data.day.persons=RT.data.day.persons.filter(p=>p.state==='onpost');RT.render();});assert.equal(await page.locator('.rt-sect.is-empty').count(),5);
    assert.equal(await page.locator('.rt-outside [data-outside-kind]:visible').count(),0);
    assert.equal(await page.locator('.rt-outside .rt-chips').innerText(),'暂无');
    await page.evaluate(()=>{RT.data.day=null;RT.render();});assert.match(await page.locator('#rtApp').innerText(),/没有开始/);
    await page.close();console.log(`PASS ${width}x${height}: populated, empty and unstarted display`);
  }}finally{try{await browser?.close();}finally{await screen.close();}}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
