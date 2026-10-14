const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const {createHash}=require('node:crypto');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../app/static');
const out=path.resolve(__dirname,'../../.handover_review/monthly-rank-ui');
fs.mkdirSync(out,{recursive:true});
const html=fs.readFileSync(path.join(root,'index.html'),'utf8').replaceAll('__STATIC_CACHE_VERSION__','monthly-rank-ui');
const security=fs.readFileSync(path.join(root,'../security.py'),'utf8');
const tuple=/CONTENT_SECURITY_POLICY\s*=\s*"; "\.join\(\s*\(([\s\S]*?)\)\s*\)/.exec(security);
const policy=[...tuple[1].matchAll(/"([^"\r\n]+)"/g)].map(m=>m[1]).join('; ');
const importMap=/<script type="importmap">([\s\S]*?)<\/script>/.exec(html)[1];
const hash=createHash('sha256').update(importMap.replace(/\r\n?/g,'\n')).digest('base64');
const csp=policy.replace("script-src 'self'",`script-src 'self' 'sha256-${hash}'`);
let tier=90,identity='CM',status='ranked',previous=false,hostile=false,expectedQueryFailure=false,enabled=true;
const rankRequests=[];
const month=()=>{const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;};
const previousMonth=()=>{const d=new Date();d.setMonth(d.getMonth()-1,1);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;};
const names={90:'荣耀王者',80:'傲世宗师',70:'超凡大师',60:'至尊星耀',50:'永恒钻石',40:'尊贵铂金',30:'荣耀黄金',20:'探索白银',10:'成长青铜',0:'启航新星'};
const messages={pending:'所属景点圈完成该月月结后，才能查看段位与评语。',reopened:'该月份已重开，段位重新核算中。',excluded:'该月LOA，不参与段位比较。',insufficient:'本月比较人数不足10人，暂不生成游戏段位。',same_scores:'本月同类人员积分相同，不生成游戏段位。',unavailable:'该月暂无可用的月结段位数据。',waiting_global:'待参与主管计分的各景点圈全部月结后，统一展示主管与TA GSM段位。'};
function dashboardData(selected){
  const closed=status!=='pending'&&status!=='reopened';
  return {month:selected,total_score:24.5,attendance_score:12,deduction_score:1,monthly_rank_available:enabled,month_closed:closed&&!previous,
    category_scores:{安全:3,礼仪:3,包容:2.5,效率:3,演出:2},records:[],
    recognition_summary:{credited:selected===month()?5:1,pending:selected===month()?2:0,uncredited:0,rejected:0},
    query_months:[month(),previousMonth(),'2025-12'].map(value=>({month:value,
      status:closed?'closed':status,status_label:closed?'已月结':status==='reopened'?'重新核算中':'待月结'})),
    month_organization:selected==='2025-12'?{attraction_name:'历史景点圈',base_role_code:'TR',role_label:'TR',group_name:'历史小组',leader_name:'历史主管'}:null,
    month_rank:{status,tier,label:hostile?'<img src=x onerror=alert(1)>':names[tier],message:messages[status]||'秋意渐浓，比上月增加3分。继续保持自己的节奏。',
      month:previous?previousMonth():selected,month_closed:!previous&&closed,
      attraction_name:selected==='2025-12'?'历史景点圈':'热力追踪',population_label:['SUPERVISOR','TA_GSM'].includes(identity)?'主管':'CM/TR',
      comparison_scope:['SUPERVISOR','TA_GSM'].includes(identity)?'all_circles':'circle',
      comparison_label:['SUPERVISOR','TA_GSM'].includes(identity)?'全部景点圈':selected==='2025-12'?'历史景点圈':'热力追踪'}};
}
function me(){return {id:1,name:'手机布局长姓名示例',employee_no:'UI001',role_code:identity,role_name:identity,
  role_label:identity==='TA_SUPERVISOR'?'CM · 代理TA主管':identity==='TA_GSM'?'主管 · 代理TA GSM':identity,base_role_code:identity==='TA_SUPERVISOR'?'CM':identity==='TA_GSM'?'SUPERVISOR':identity,
  attraction_id:1,attraction_name:'热力追踪',group_name:'热力追踪A组',group_leader_label:'主管 长姓名主管示例 · 代理主管 长姓名代理示例',
  led_groups:identity==='SUPERVISOR'?[{name:'热力追踪A组',leader_type:'formal'}]:identity==='TA_SUPERVISOR'?[{name:'热力追踪B组',leader_type:'acting'}]:[],
  permissions:[],must_change_password:false};}
function json(res,data){res.writeHead(200,{'Content-Type':'application/json; charset=utf-8'});res.end(JSON.stringify(data));}
const missing=[],errors=[];
const server=http.createServer((req,res)=>{
  const url=new URL(req.url,'http://fixture'),route=url.pathname;
  if(route==='/'){res.writeHead(200,{'Content-Type':'text/html','Content-Security-Policy':csp});res.end(html);}
  else if(route.startsWith('/static/')){
    const file=path.resolve(root,'.'+route.slice(7));
    if(!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.writeHead(404);res.end();return;}
    res.writeHead(200,{'Content-Type':{'.js':'text/javascript','.css':'text/css','.png':'image/png'}[path.extname(file)]||'application/octet-stream'});
    res.end(fs.readFileSync(file));
  }else if(route==='/api/me')json(res,me());
  else if(route==='/api/options')json(res,{roles:[],attractions:[],employee_circles:[],recognition_types:[],deduction_types:[]});
  else if(route==='/api/dashboard')json(res,dashboardData(url.searchParams.get('month')));
  else if(route==='/api/self-month-rank'){rankRequests.push(url.search);json(res,dashboardData(url.searchParams.get('month')).month_rank);}
  else if(route==='/api/member-score-summary')json(res,{month:url.searchParams.get('month'),rows:[{
    employee_id:2,employee_name:'测试组员',employee_no:'CM002',role_name:'CM',
    recognition_score:3,deduction_score:0,attendance_score:12,total_score:15,
    month_rank:url.searchParams.get('month')===previousMonth()?{month:previousMonth(),tier:90,label:'最强王者',comparison_label:'热力追踪'}:null,
  }]});
  else if(route==='/api/changelog/announcement')json(res,{read:true});
  else if(route==='/api/announcements/summary')json(res,{pending:0,messages:0,handovers:0});
  else if(route==='/api/rotation/adjust/mine')json(res,{items:[]});
  else if(route==='/api/action-center')json(res,{items:[],total:0});
  else if(route==='/favicon.ico'){res.writeHead(204);res.end();}
  else{missing.push(route);res.writeHead(404);res.end();}
});
async function render(page){
  await page.evaluate(async data=>{
    const entry=new URL(document.querySelector('script[type=module][src]').src);
    const {state}=await import(new URL('./app/state.js',entry));
    state.me=data.me;state.tab='home';state.homeMonth=data.month;
    const {renderHome}=await import(new URL('./app/home.js',entry));
    await renderHome();
  },{me:me(),month:month()});
  if(enabled)await showRank(page);
}
async function showRank(page){
  await page.locator('#homeRankView').click();
  if(previous)await page.locator('#homeRankContent [role=alert]').waitFor();
  else await page.locator('#homeRankView[aria-expanded=true]').waitFor();
}
async function main(){
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({channel:'msedge',headless:true});
  let checked=0;
  try{
    const page=await browser.newPage();
    page.on('pageerror',e=>errors.push(e.message));
    page.on('console',m=>{if(m.type()==='error'&&!(expectedQueryFailure&&m.text().includes('status of 503')))errors.push(m.text());});
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.locator('#homeMonthSelect').waitFor();
    assert.equal(rankRequests.length,0,'Rank results are never fetched automatically');
    assert.equal(await page.locator('.member-rank-emblem').count(),0);
    for(identity of ['CM','TR','TA_SUPERVISOR','SUPERVISOR','TA_GSM']){
      for(const width of [320,390,768,1280]){
        await page.setViewportSize({width,height:1000});
        const backgrounds=new Set(),emblemPositions=new Set();
        for(tier of [90,80,70,60,50,40,30,20,10,0]){
          await render(page);
          assert.equal(await page.locator('.member-rank-copy h3').textContent(),names[tier]);
          assert.equal(await page.locator('[data-rank-tier]').getAttribute('data-rank-tier'),String(tier));
          assert.equal(await page.locator('.score-explainer').count(),['SUPERVISOR','TA_GSM'].includes(identity)?1:0);
          assert((await page.locator('.member-rank-band').textContent()).includes('月度PR积分'));
          assert(!/[％%]|超过|名次|排名/.test(await page.locator('.member-rank-hero').innerText()));
          assert((await page.locator('.home-recognition-summary').textContent()).includes('待复核2张'));
          const measurement=await page.evaluate(()=>{
            const panel=document.querySelector('.member-home-panel');
            const bounds=panel.getBoundingClientRect();
            return {overflow:document.documentElement.scrollWidth>innerWidth+1,
              clipped:[...panel.querySelectorAll('h2,h3,h4,p,strong,label,select,summary')].filter(el=>{
                const b=el.getBoundingClientRect();return b.width>0&&(b.left<bounds.left-1||b.right>bounds.right+1);
              }).map(el=>el.className),
              columns:getComputedStyle(panel.querySelector('.member-score-items')).gridTemplateColumns.split(' ').length,
              background:getComputedStyle(panel).backgroundImage,
              emblemImage:getComputedStyle(panel.querySelector('.member-rank-emblem')).backgroundImage,
              emblemPosition:getComputedStyle(panel.querySelector('.member-rank-emblem')).backgroundPosition,
              monthHeight:panel.querySelector('select').getBoundingClientRect().height,
              font:parseFloat(getComputedStyle(panel.querySelector('select')).fontSize)};
          });
          assert.equal(measurement.overflow,false,`${identity} ${width} ${tier} overflow`);
          assert.deepEqual(measurement.clipped,[],`${identity} ${width} ${tier} clipped`);
          assert.equal(measurement.columns,width<=760?4:8);
          if(width<=760){assert(measurement.monthHeight>=44);assert(measurement.font>=16);}
          backgrounds.add(measurement.background);
          assert(measurement.emblemImage.includes('rank-emblems-v2.png'));
          emblemPositions.add(measurement.emblemPosition);
          if(tier===90&&['CM','TA_SUPERVISOR','SUPERVISOR'].includes(identity)&&[390,1280].includes(width)){
            await page.screenshot({path:path.join(out,`${identity}-${width}.png`),fullPage:true});
          }
          checked++;
        }
        assert.equal(backgrounds.size,10);
        assert.equal(emblemPositions.size,10);
      }
    }
    identity='CM';
    for(status of Object.keys(messages)){
      await render(page);
      assert.equal(await page.locator('.member-rank-panel').count(),0);
      assert.equal(await page.locator('.member-rank-emblem').count(),0);
      assert.equal(await page.locator('.member-rank-empty').textContent(),messages[status]);
    }
    status='ranked';tier=90;previous=true;await render(page);
    assert.equal(await page.locator('.member-rank-panel').count(),0);
    assert((await page.locator('#homeRankContent [role=alert]').textContent()).includes('返回的数据月份不一致'));
    assert.equal(await page.locator('.member-month-closed').count(),0);
    previous=false;hostile=true;await render(page);
    assert.equal(await page.locator('.member-rank-copy img').count(),0);
    assert((await page.locator('.member-rank-copy h3').textContent()).startsWith('<img'));
    hostile=false;identity='SUPERVISOR';await render(page);
    await page.locator('.score-explainer summary').click();
    assert.equal(await page.locator('.score-explainer[open]').count(),1);
    identity='CM';await render(page);
    const chosen=await page.locator('#homeMonthSelect option').nth(1).getAttribute('value');
    await page.locator('#homeMonthSelect').selectOption(chosen);
    await showRank(page);
    await page.waitForFunction(month=>document.querySelector('.member-rank-month')?.textContent.includes(`${Number(month.slice(5))}月`),chosen);
    assert.equal(await page.locator('.home-read-only').count(),1);
    assert.equal(await page.locator('.home-recognition-pending').count(),0);
    assert.equal(await page.locator('#homeMonthSelect').count(),1);
    assert.equal(await page.locator('#homeQueryRetry').count(),0);
    assert.equal(await page.locator('.member-home-panel button').count(),1);
    assert.equal(await page.locator('#homeMonthSelect optgroup[label="2025年"]').count(),1);
    await page.locator('#homeMonthSelect').selectOption('2025-12');
    await showRank(page);
    await page.waitForFunction(()=>document.querySelector('.member-info-grid')?.textContent.includes('历史小组'));
    assert((await page.locator('.member-info-grid').textContent()).includes('历史景点圈'));
    assert((await page.locator('.member-info-grid').textContent()).includes('月结角色'));
    assert((await page.locator('.member-rank-month').textContent()).includes('2025年12月'));
    assert.equal(await page.locator('.score-explainer').count(),0);
    assert.equal(await page.locator('.member-home-panel').getAttribute('data-season'),'winter');

    // Slow requests clear old results immediately and keep the same selector usable.
    const dashboardRoute=/\/api\/dashboard\?/;
    let releaseSlow;
    const slowGate=new Promise(resolve=>{releaseSlow=resolve;});
    const slowHandler=async route=>{
      const selected=new URL(route.request().url()).searchParams.get('month');
      if(selected!==chosen){await route.continue();return;}
      await slowGate;
      await route.fulfill({json:dashboardData(selected)});
    };
    await page.route(dashboardRoute,slowHandler);
    await page.locator('#homeMonthSelect').selectOption(chosen);
    await page.locator('[role="status"][aria-busy="true"]').waitFor();
    assert.equal(await page.locator('.member-score-items').count(),0);
    assert.equal(await page.locator('.member-rank-hero').count(),0);
    releaseSlow();
    await page.locator('#homeRankView').waitFor();await showRank(page);
    await page.locator('.member-rank-hero').waitFor();
    await page.unroute(dashboardRoute,slowHandler);

    // A failed request shows an explicit error and a retry for the same month.
    let failOnce=true;
    const failureHandler=async route=>{
      if(failOnce){failOnce=false;await route.fulfill({status:503,json:{detail:'测试服务暂不可用'}});}
      else await route.fulfill({json:dashboardData(new URL(route.request().url()).searchParams.get('month'))});
    };
    expectedQueryFailure=true;
    await page.route(dashboardRoute,failureHandler);
    await page.locator('#homeMonthSelect').selectOption(month());
    await page.locator('[role="alert"]').waitFor();
    assert((await page.locator('[role="alert"]').textContent()).includes('测试服务暂不可用'));
    assert.equal(await page.locator('.member-score-items').count(),0);
    await page.locator('#homeQueryRetry').click();
    await page.locator('#homeRankView').waitFor();await showRank(page);
    await page.locator('.member-rank-hero').waitFor();
    assert.equal(await page.locator('#homeMonthSelect').inputValue(),month());
    assert.equal(await page.locator('#homeQueryRetry').count(),0);
    await page.unroute(dashboardRoute,failureHandler);
    expectedQueryFailure=false;

    // Finishing an older request cannot overwrite the newest selected month.
    let releaseStale,markReceived;
    const staleGate=new Promise(resolve=>{releaseStale=resolve;});
    const received=new Promise(resolve=>{markReceived=resolve;});
    const staleHandler=async route=>{
      const selected=new URL(route.request().url()).searchParams.get('month');
      if(selected===chosen){markReceived();await staleGate;}
      await route.fulfill({json:dashboardData(selected)});
    };
    await page.route(dashboardRoute,staleHandler);
    await page.locator('#homeMonthSelect').selectOption(chosen);
    await received;
    await page.locator('#homeMonthSelect').selectOption('2025-12');
    await page.waitForFunction(()=>document.querySelector('.member-info-grid')?.textContent.includes('历史景点圈'));
    await showRank(page);
    const staleResponse=page.waitForResponse(response=>new URL(response.url()).searchParams.get('month')===chosen);
    releaseStale();
    await (await staleResponse).finished();
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    assert.equal(await page.locator('#homeMonthSelect').inputValue(),'2025-12');
    assert((await page.locator('.member-rank-month').textContent()).includes('2025年12月'));
    await page.unroute(dashboardRoute,staleHandler);
    // An older personal-result request cannot reopen or repaint another month.
    await page.locator('#homeRankView').click();
    let releaseRank,rankStarted;
    const rankGate=new Promise(resolve=>{releaseRank=resolve;});
    const rankReceived=new Promise(resolve=>{rankStarted=resolve;});
    const personalRoute=/\/api\/self-month-rank\?/;
    const olderRank=async route=>{
      const selected=new URL(route.request().url()).searchParams.get('month');
      if(selected==='2025-12'){rankStarted();await rankGate;}
      await route.fulfill({json:dashboardData(selected).month_rank});
    };
    await page.route(personalRoute,olderRank);
    await page.locator('#homeRankView').click();await rankReceived;
    await page.locator('#homeMonthSelect').selectOption(month());
    await page.locator('#homeRankView').waitFor();await showRank(page);
    const oldRankResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/self-month-rank'&&new URL(response.url()).searchParams.get('month')==='2025-12');
    releaseRank();await (await oldRankResponse).finished();
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    assert.equal(await page.locator('#homeMonthSelect').inputValue(),month());
    assert((await page.locator('.member-rank-month').textContent()).includes(`${Number(month().slice(5))}月`));
    await page.unroute(personalRoute,olderRank);
    await page.locator('#homeRankView').click();
    assert.equal(await page.locator('#homeRankContent').isVisible(),false);
    assert.equal(await page.locator('.member-rank-panel').count(),0);
    enabled=false;await render(page);
    assert.equal(await page.locator('#homeRankView').count(),0);
    assert.equal(await page.locator('.member-rank-emblem').count(),0);
    enabled=true;
    await page.evaluate(async()=>{
      const entry=new URL(document.querySelector('script[type=module][src]').src);
      const {state}=await import(new URL('./app/state.js',entry));state.tab='members';
      const {renderMembers}=await import(new URL('./app/members.js',entry));await renderMembers();
    });
    assert.equal(await page.locator('.member-history-rank').count(),0);
    await page.locator('#memberFilter [name=month]').fill(previousMonth());
    await page.locator('#memberFilter').getByRole('button',{name:'查询',exact:true}).click();
    await page.locator('.member-score-row').waitFor();
    assert.equal(await page.locator('.member-history-rank').count(),0);
    assert(!/[％%]|段位/.test(await page.locator('#memberResults').innerText()));
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
    assert.deepEqual(missing,[]);assert.deepEqual(errors,[]);
    console.log(JSON.stringify({checked,states:Object.keys(messages).length,errors,output:out}));
    await page.close();
  }finally{await browser.close();server.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;server.close();});
