'use strict';
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs'),net=require('node:net');
const {spawn}=require('node:child_process'),{chromium,webkit,firefox}=require('playwright');
const root=path.resolve(__dirname,'../..'),sleep=ms=>new Promise(r=>setTimeout(r,ms));let server,browser,log='';
(async()=>{try{
 const sock=net.createServer();await new Promise(r=>sock.listen(0,'127.0.0.1',r));const port=sock.address().port;await new Promise(r=>sock.close(r));
 server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'month_close_scope_fixture.py'),String(port)],{cwd:path.join(root,'backend'),windowsHide:true});server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);
 const base='http://127.0.0.1:'+port;let ready=false;for(let i=0;i<100;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await sleep(300);}assert(ready,log);
 let index=0;
 for(const engine of (process.env.MONTH_SCOPE_ENGINES||'edge').split(',')){
  browser=await(engine==='webkit'?webkit:engine==='firefox'?firefox:chromium).launch(engine==='edge'?{channel:'msedge',headless:true}:{headless:true});
  const admin=await browser.newContext({viewport:{width:1440,height:1000}}),hr=await browser.newContext({viewport:{width:1440,height:1000}}),errors=[];
  assert((await admin.request.post(base+'/api/login',{data:{employee_no:'HR01',password:'type-admin-only'}})).ok());
  assert((await hr.request.post(base+'/api/login',{data:{employee_no:'HR-BEAR',password:'type-fixture-only'}})).ok());
  const h=await admin.request.get(base+'/health').then(r=>r.json());for(const c of [admin,hr])await c.request.post(base+'/api/changelog/announcement/read',{data:{version:h.version}});
  const fixture=await admin.request.post(base+'/__fixture/month-close-scope',{data:{index:index++,local:false}}).then(r=>r.json());
  const page=await admin.newPage(),hrPage=await hr.newPage();for(const p of [page,hrPage])p.on('pageerror',e=>errors.push(e.message));
  const writes=[];page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/reopen'))writes.push(r.postDataJSON());});
  async function query(p,select){await p.goto(base+'/');await p.locator('.tabs-desktop [data-tab=monthClose]').click();await p.locator('#monthCloseForm [name=month]').fill(fixture.month);if(select)await p.locator('#monthCloseForm [name=attraction_id]').selectOption(String(fixture.attraction_id));await p.locator('#monthCloseForm [type=submit]').click();await p.locator('#monthCloseResult').getByText('实际生效范围：').waitFor();}
  await query(hrPage,false);assert.equal(await hrPage.locator('[data-month-reopen]').count(),0);assert.match(await hrPage.locator('#monthCloseResult').innerText(),/联系最高管理员/);
  const loa={employee_id:fixture.employee_id,starts_on:'2026-09-03',ends_on:'2026-09-05'};
  const blocked=await hr.request.post(base+'/api/loa-periods',{data:loa});assert.equal(blocked.status(),423);assert.match((await blocked.json()).detail.message,/最高管理员.*全部景点圈/);
  await query(page,true);assert.match(await page.locator('#monthCloseResult').innerText(),/实际生效范围：全部景点圈/);
  await page.locator('[data-month-reopen]').click();await page.getByRole('dialog').getByText(/这会解除全部景点圈/).waitFor();await page.locator('[data-modal-cancel]').click();await page.locator('.modal-overlay').waitFor({state:'detached'});assert.equal(writes.length,0);
  await page.locator('[data-month-reopen]').click();await page.locator('[data-modal-input]').fill('更正合成LOA');
  for(const width of [320,390,768,1440]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,engine+' overflow '+width);}
  const shots=process.env.MONTH_SCOPE_SCREENSHOTS;if(shots){fs.mkdirSync(shots,{recursive:true});await page.setViewportSize({width:390,height:844});await page.getByRole('dialog').evaluate(el=>Promise.all(el.getAnimations({subtree:true}).map(a=>a.finished.catch(()=>{}))));await page.screenshot({path:path.join(shots,engine+'-global-reopen-mobile.png')});}
  await page.locator('[data-modal-confirm]').click();await page.locator('.modal-overlay').waitFor({state:'detached'});await page.locator('#monthCloseResult .badge.ok').waitFor();assert.equal(writes.length,1);assert.equal(writes[0].attraction_id,null);
  const saved=await hr.request.post(base+'/api/loa-periods',{data:loa});assert(saved.ok(),await saved.text());
  // A global release cannot silently release an independent circle closure.
  await admin.request.post(base+'/__fixture/month-close-scope',{data:{index:index-1,local:true}});
  await page.setViewportSize({width:1440,height:1000});await query(page,true);await page.locator('[data-month-reopen]').click();await page.locator('[data-modal-input]').fill('仅开放全局层');await page.locator('[data-modal-confirm]').click();
  await page.getByRole('button',{name:'临时开放月结',exact:true}).waitFor();assert.match(await page.locator('#monthCloseResult').innerText(),/实际生效范围：小熊罐子/);
  const local=await admin.request.get(base+'/api/month-closes/'+fixture.month+'?attraction_id='+fixture.attraction_id).then(r=>r.json());assert(local.is_closed&&local.reopen_target_attraction_id===fixture.attraction_id);
  assert.deepEqual(errors,[]);console.log(engine+': actual global target null, explicit scope, cancel/no write, HR restriction, LOA blocked then saved, residual local closure and widths passed');
  await browser.close();browser=null;
 }
}finally{if(browser)await browser.close();if(server){server.kill();await Promise.race([new Promise(r=>server.once('exit',r)),sleep(3000)]);}}})().catch(e=>{console.error(e);process.exitCode=1;});
