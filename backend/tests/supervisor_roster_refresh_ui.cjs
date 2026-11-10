const assert=require('node:assert/strict');
const fs=require('node:fs');
const http=require('node:http');
const path=require('node:path');
const {chromium,webkit}=require('playwright');

const root=path.resolve(__dirname,'../app/static');
const shell=`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="/static/css/style.css">
<header><div id="userBadge"></div></header><div id="tabs"></div><main id="app"></main>
<script type="module">
import {state} from '/static/js/app/state.js';
import {renderMembers} from '/static/js/app/members.js';
import {renderTabs,renderUserBadge} from '/static/js/app/navigation.js';
const role=new URLSearchParams(location.search).get('role');
state.me={id:1,name:'测试主管',employee_no:'1700010',role_code:role,role_name:role,
  base_role_code:role==='TA_SUPERVISOR'?'CM':role,permissions:[],member_count:2};
state.tab='members';
renderUserBadge();
renderTabs();
await renderMembers();
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

async function main(){
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const base='http://127.0.0.1:'+server.address().port;
  try{
    for(const [name,engine,options] of [['Edge',chromium,{channel:'msedge'}],['WebKit',webkit,{}]]){
      const browser=await engine.launch({headless:true,...options});
      try{
        for(const role of ['SUPERVISOR','TA_SUPERVISOR'])await checkRoster(browser,base,role);
        console.log(name+': both supervisors refreshed the header and open mobile drawer without reloading');
      }finally{await browser.close();}
    }
  }finally{
    server.closeAllConnections();
    await new Promise(resolve=>server.close(resolve));
  }
}

main().catch(error=>{console.error(error);process.exitCode=1;});
