'use strict';
// Real mobile DOM fixture. No production server, accounts or data are used.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
const root=path.join(__dirname,'../app/static');
async function main(){
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:390,height:844},deviceScaleFactor:1});
    let html=fs.readFileSync(path.join(root,'rotation.html'),'utf8');
    html=html.replace(/<link[^>]+>/g,'').replace(/<script[\s\S]*?<\/script>/g,'');
    html=html.replace('</head>',`<style>${fs.readFileSync(path.join(root,'css/style.css'),'utf8')}\n${fs.readFileSync(path.join(root,'css/rotation.css'),'utf8')}</style></head>`);
    await page.setContent(html);
    await page.addScriptTag({content:fs.readFileSync(path.join(root,'js/rotation-common.js'),'utf8')});
    const source=fs.readFileSync(path.join(root,'js/rotation.js'),'utf8');
    await page.addScriptTag({content:source.slice(0,source.lastIndexOf('(async () => {'))});
    await page.evaluate(()=>{
      me={kind:'member',name:'测试员工',employee_no:'1'};
      RT.sync='live';RT.lastSyncedAt=Date.now();RT.fetchedAt=Date.now();
      RT.data={version:1,test_mode:true,clock:{date:'2026-10-11',minute:495,paused:true,mode:'sim'},settings:{departEarly:0},
        person:{pid:'1',name:'测试员工',state:'ready',readyAt:480,start:435,end:1000,flags:[],tags:[],
          confirmationTokens:{depart:'a'.repeat(64)},assign:{line:'B1',entryPost:'塔台协助',mode:'chain',why:'推下班',targetName:'测试目标',targetLine:'B1',targetPost:'分组2',departAt:495}},
        notices:[{kind:'depart',title:'现在出发去 B1 线',body:'在个人待办或大屏确认去轮岗',status:'open'}],
        confirmations:[],today:[],week_minutes:{B1:30},line:{id:'B1',posts:[{name:'塔台协助',open:true,occ:'2',occName:'测试同事'},{name:'分组2',open:true,occ:'3',occName:'测试目标'}]}};
      window.writes=0;
      rtApi=async(path,options={})=>{
        if(options.method==='POST'){
          window.writes++;
          const body=JSON.parse(options.body);
          if(body.pid!=='1'||body.confirmation_token!=='a'.repeat(64))throw new Error('wrong step');
          const result={pid:'1',action:'depart',minute:495,line:'B1',post:'塔台协助',state:'onpost',source:'member',assignment:{...RT.data.person.assign}};
          RT.data={...RT.data,version:2,person:{...RT.data.person,state:'onpost',line:'B1',post:'塔台协助',lineStart:495,assign:null,confirmationTokens:{}},
            notices:[],confirmations:[result],line:{id:'B1',posts:[{name:'塔台协助',open:true,occ:'1',occName:'测试员工'},{name:'分组2',open:true,occ:'2',occName:'测试同事'}]}};
          return {ok:true,duplicate:false,result};
        }
        return RT.data;
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
  }finally{await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
