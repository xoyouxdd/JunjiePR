const assert=require('node:assert/strict');
const fs=require('node:fs');
const http=require('node:http');
const path=require('node:path');
const {chromium,webkit}=require('./support/browser-runtime.cjs');

const root=path.resolve(__dirname,'../app/static');
const shell=`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="/static/css/style.css">
<header><div id="userBadge"></div></header><div id="tabs"></div><main id="app"></main>
<script type="module">
import {state} from '/static/js/app/state.js';
import {configureViews} from '/static/js/app/context.js';
import {renderMembers} from '/static/js/app/members.js';
import {renderTabs,renderUserBadge} from '/static/js/app/navigation.js';
import {renderFrontlineReview} from '/static/js/app/reviews.js';
import {renderActionCenter} from '/static/js/app/operations.js';
const params=new URLSearchParams(location.search);
const role=params.get('role');
state.me={id:1,name:'测试主管',employee_no:'1700010',role_code:role,role_name:role,
  base_role_code:role==='TA_SUPERVISOR'?'CM':role,permissions:[],member_count:2};
state.tab=params.has('actionCenter')?'actionCenter':'members';
configureViews({members:renderMembers,review:renderFrontlineReview,actionCenter:renderActionCenter});
renderUserBadge();
renderTabs();
if(state.tab==='actionCenter'&&params.has('actionCenter'))await renderActionCenter();
else if(role!=='TA_GSM')await renderMembers();
window.rosterReady=true;
</script>`;

const server=http.createServer((req,res)=>{
  const pathname=new URL(req.url,'http://localhost').pathname;
  if(pathname==='/'){
    res.setHeader('Content-Type','text/html; charset=utf-8');
    return res.end(shell);
  }
  const file=path.resolve(root,'.'+pathname.slice('/static'.length));
  if(pathname.startsWith('/static/')&&file.startsWith(root+path.sep)&&fs.existsSync(file)){
    res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'application/octet-stream');
    return res.end(fs.readFileSync(file));
  }
  res.writeHead(404).end();
});

const member=(employee_id,employee_name,employee_no)=>({
  employee_id,employee_name,employee_no,role_name:'CM',
  recognition_score:1,deduction_score:0,attendance_score:10,total_score:11
});
const archived=member(11,'待删除员工','1700011');
const retained=member(12,'保留员工','1700012');
const overseen=member(13,'代管组员工','1700013');
const previousMonth=(month)=>{
  const [year,number]=month.split('-').map(Number);
  return number===1?`${year-1}-12`:`${year}-${String(number-1).padStart(2,'0')}`;
};

async function checkRoster(browser,base,role){
  const page=await browser.newPage({viewport:{width:390,height:900}});
  const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  let deleted=false,memberCount=2,currentMonth,releaseQuery;
  let started;
  const queryStarted=new Promise(resolve=>{started=resolve;});
  const queryGate=new Promise(resolve=>{releaseQuery=resolve;});
  try{
    await page.route('**/api/member-score-summary?*',async route=>{
      const params=new URL(route.request().url()).searchParams;
      const month=params.get('month'),keyword=params.get('keyword')||'';
      currentMonth??=month;
      const rows=month!==currentMonth||!deleted?[archived,retained]:[retained];
      if(role==='SUPERVISOR')rows.push(overseen);
      const response={month,member_count:memberCount,rows:rows.filter(row=>
        row.employee_name.includes(keyword)||row.employee_no.includes(keyword))};
      if(month===currentMonth&&keyword==='0011'&&deleted){
        started();
        await queryGate;
      }
      await route.fulfill({json:response});
    });
    await page.goto(base+'/?role='+role);
    await page.waitForFunction(()=>window.rosterReady);
    const form=page.locator('#memberFilter');
    const search=form.locator('[name=keyword]');
    const query=form.getByRole('button',{name:'查询',exact:true});
    const drawer=page.locator('.mobile-more-drawer');
    const identity=drawer.locator('.mobile-identity-card');
    const badge=page.locator('#userBadge');
    assert.match(await badge.getAttribute('title'),/2名组员/);
    await page.locator('[data-member-row="11"]').waitFor();

    // Keep the original page and drawer while a filtered request comes back empty.
    deleted=true;
    memberCount=1;
    await search.fill('0011');
    await query.click();
    await queryStarted;
    await page.locator('[data-open-more]').click();
    await identity.getByText('2名组员',{exact:true}).waitFor();
    const originalDrawer=await drawer.elementHandle();
    releaseQuery();
    await identity.getByText('1名组员',{exact:true}).waitFor();
    assert.match(await badge.getAttribute('title'),/1名组员/);
    assert(await originalDrawer.evaluate(node=>node.isConnected&&!node.hidden));
    await drawer.locator('[data-close-more]').click();
    await drawer.waitFor({state:'hidden'});
    await page.getByText('无直属CM/TR或无匹配员工',{exact:true}).waitFor();

    // A historical match stays available and does not restore the previous headcount.
    await form.locator('[name=month]').fill(previousMonth(currentMonth));
    await query.click();
    await page.locator('[data-member-row="11"]').waitFor();
    assert.match(await badge.getAttribute('title'),/1名组员/);
    await form.locator('[name=month]').fill(currentMonth);
    await search.fill('');
    await query.click();
    await page.locator('[data-member-row="12"]').waitFor();
    assert.equal(await page.locator('[data-member-row="11"]').count(),0);
    assert.equal(await page.locator('[data-member-row="13"]').count(),Number(role==='SUPERVISOR'));
    assert.match(await badge.getAttribute('title'),/1名组员/);

    memberCount=0;
    await search.fill('没有匹配');
    await query.click();
    await page.waitForFunction(()=>!document.getElementById('userBadge').title.includes('名组员'));
    await page.locator('[data-open-more]').click();
    assert.equal(await identity.getByText('组员',{exact:true}).isVisible(),false);
    assert.deepEqual(errors,[]);
  }finally{
    releaseQuery();
    await page.close();
  }
}

async function checkTaGsmNavigation(browser,base){
  const page=await browser.newPage({viewport:{width:1280,height:900}});
  try{
    await page.route('**/api/reviews?*',route=>route.fulfill({json:{items:[],total:0}}));
    await page.route('**/api/member-score-summary?*',route=>route.fulfill({json:{rows:[retained],member_count:1}}));
    await page.goto(base+'/?role=TA_GSM');
    await page.waitForFunction(()=>window.rosterReady);
    const desktop=page.locator('.tabs-desktop');
    const configure=fixture=>page.evaluate(async fixture=>{
      const {state}=await import('/static/js/app/state.js');
      const {renderTabs}=await import('/static/js/app/navigation.js');
      Object.assign(state.me,fixture);
      renderTabs();
    },fixture);
    const formalGroup={id:1,name:'测试小组',leader_type:'formal'};
    const permissions=['REVIEW_DIRECT','MEMBER_RECORDS'];
    for(const fixture of [
      {base_role_code:'SUPERVISOR',led_groups:[],permissions},
      {base_role_code:'CM',led_groups:[formalGroup],permissions},
      {base_role_code:'SUPERVISOR',led_groups:[formalGroup],permissions:[]},
    ]){
      await configure(fixture);
      assert.equal(await desktop.locator('[data-tab=review],[data-tab=members]').count(),0);
    }
    await configure({base_role_code:'SUPERVISOR',led_groups:[formalGroup],permissions});
    await desktop.getByRole('button',{name:'复核',exact:true}).click();
    await page.locator('.review-page').waitFor();
    await desktop.getByRole('button',{name:'组员记录',exact:true}).click();
    await page.locator('[data-member-row="12"]').waitFor();
    await page.setViewportSize({width:390,height:900});
    await page.locator('[data-open-more]').click();
    const drawer=page.locator('.mobile-more-drawer');
    await drawer.getByRole('button',{name:'复核',exact:true}).click();
    await page.locator('.review-page').waitFor();
    await page.locator('[data-open-more]').click();
    await drawer.getByRole('button',{name:'组员记录',exact:true}).click();
    await page.locator('[data-member-row="12"]').waitFor();
  }finally{
    await page.close();
  }
}

async function checkReviewDetailsPagination(browser,base){
  const page=await browser.newPage({viewport:{width:390,height:900}});
  const rows=Array.from({length:501},(_,index)=>({
    id:index+1,employee_name:'签卡 '+(index+1),employee_no:'1700011',
    recognition_type:'安全',reviewer_name:'测试主管',waiting_hours:0,
  }));
  let pending=rows;
  try{
    await page.route('**/api/action-center',route=>route.fulfill({json:{
      total:rows.length,items:[{type:'recognition_review',title:'待复核签卡',tab:'review',count:rows.length}],
    }}));
    await page.route('**/api/deduction-upgrades/recovery',route=>route.fulfill({json:{items:[]}}));
    await page.route('**/api/action-center/recognition_review/details*',route=>{
      const params=new URL(route.request().url()).searchParams;
      const page_size=Number(params.get('page_size')||50);
      const anchor=Number(params.get('cursor')||0);
      const previous=params.get('direction')==='previous';
      const candidates=pending.filter(row=>previous?row.id<anchor:row.id>anchor);
      const items=previous?candidates.slice(-page_size):candidates.slice(0,page_size);
      const first=items[0]?.id,last=items.at(-1)?.id;
      return route.fulfill({json:{type:'recognition_review',title:'待复核签卡明细',
        total:pending.length,page_size,items,
        previous_cursor:pending.some(row=>row.id<first)?String(first):null,
        next_cursor:pending.some(row=>row.id>last)?String(last):null}});
    });
    await page.goto(base+'/?role=SUPERVISOR&actionCenter');
    await page.waitForFunction(()=>window.rosterReady);
    const opener=page.getByRole('button',{name:'查看明细',exact:true});
    await opener.click();
    const detail=page.locator('.action-center-detail');
    const next=detail.getByRole('button',{name:'下一页',exact:true});
    const previous=detail.getByRole('button',{name:'上一页',exact:true});
    const names=detail.locator('tbody td:first-child strong');
    const collected=[];
    await detail.getByText('本页 50 条 · 共 501 条',{exact:true}).waitFor();
    assert(await previous.isDisabled());
    collected.push(...await names.allTextContents());
    pending=rows.filter(row=>row.id!==1&&row.id!==50);
    for(let number=1;number<=10;number++){
      await next.click();
      await detail.getByText('签卡 '+(number*50+1),{exact:true}).waitFor();
      collected.push(...await names.allTextContents());
    }
    assert.deepEqual(collected,rows.map(row=>row.employee_name));
    assert(await next.isDisabled());
    assert.deepEqual(await names.allTextContents(),['签卡 501']);
    assert.equal(await page.locator('.action-count b').textContent(),'499');
    await previous.click();
    await detail.getByText('签卡 451',{exact:true}).waitFor();
    assert.deepEqual(await names.allTextContents(),rows.slice(450,500).map(row=>row.employee_name));
    pending=pending.filter(row=>row.id!==501);
    await next.click();
    await detail.getByText('此方向已无待复核签卡',{exact:true}).waitFor();
    await detail.getByRole('button',{name:'从头查看',exact:true}).click();
    await detail.getByText('签卡 2',{exact:true}).waitFor();
    await detail.getByRole('button',{name:'关闭',exact:true}).click();
    await opener.click();
    await detail.getByText('签卡 2',{exact:true}).waitFor();
  }finally{
    await page.close();
  }
}

async function main(){
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const base='http://127.0.0.1:'+server.address().port;
  try{
    for(const [name,engine,options] of [['Edge',chromium,{channel:'msedge'}],['WebKit',webkit,{}]]){
      const browser=await engine.launch({headless:true,...options});
      try{
        for(const role of ['SUPERVISOR','TA_SUPERVISOR'])await checkRoster(browser,base,role);
        await checkTaGsmNavigation(browser,base);
        await checkReviewDetailsPagination(browser,base);
        console.log(name+': both supervisors refreshed the header and open mobile drawer without reloading');
        console.log(name+': TA GSM group review and roster navigation passed on desktop and mobile');
        console.log(name+': review details reached item 501 without omissions or duplicates');
      }finally{await browser.close();}
    }
  }finally{
    server.closeAllConnections();
    await new Promise(resolve=>server.close(resolve));
  }
}

main().catch(error=>{console.error(error);process.exitCode=1;});
