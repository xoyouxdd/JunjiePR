// Real local API and board scripts, synthetic accounts and roster only.
const assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs'),net=require('node:net');
const {spawn}=require('node:child_process'),{chromium,webkit,firefox}=require('playwright');
const root=path.resolve(__dirname,'../..'),out=path.join(root,'.handover_review/rotation-post-holds-20261024');
fs.mkdirSync(out,{recursive:true});let server,browser,log='';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{try{
  const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
  server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'rotation_formal_fixture.py'),String(port)],{cwd:path.join(root,'backend'),windowsHide:true});
  server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);const base='http://127.0.0.1:'+port;
  for(let i=0;i<100;i++){try{if((await fetch(base+'/health')).ok)break;}catch{}if(server.exitCode!==null)throw new Error(log);await sleep(300);}
  for(const name of (process.env.ROTATION_HOLD_ENGINES||'edge,webkit,firefox').split(',')){
    browser=await({edge:chromium,webkit,firefox}[name]).launch({headless:true,...(name==='edge'?{channel:'msedge'}:{})});
    const context=await browser.newContext({viewport:{width:390,height:844}}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
    assert((await context.request.post(base+'/api/login',{data:{employee_no:'9710010',password:'formal-fixture-2026'}})).ok());
    await page.goto(base+'/rotation-board');await page.locator('[data-post="A1#0"]').waitFor();
    await page.locator('[data-post="A1#0"] .rt-pn').click();await page.locator('[data-edit-hold]').click();
    await page.locator('#rtModal [name=reason]').fill('特殊接待 · 合成演示');await page.locator('#rtModal button[type=submit]').click();
    await page.locator('[data-post="A1#0"] .post-hold').waitFor();
    const watcher=await browser.newContext({viewport:{width:390,height:844}}),cm=await watcher.newPage();
    assert((await watcher.request.post(base+'/api/login',{data:{employee_no:'9710000',password:'formal-fixture-2026'}})).ok());
    await cm.goto(base+'/rotation-board');await cm.locator('.rt-post.holding .post-hold').waitFor();
    assert.equal(await cm.locator('[data-edit-hold]').count(),0);
    for(const width of [320,390,430,768,1440]){
      await page.setViewportSize({width,height:900});assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1));
      if(width===390||width===1440)await page.screenshot({path:path.join(out,name+'-'+width+'.png'),fullPage:true});
    }
    await page.locator('[data-post="A1#0"] .rt-pn').click();assert.match(await page.locator('#rtModal').innerText(),/特殊接待.*手动取消/s);
    await page.locator('[data-edit-hold]').click();await page.locator('#rtModal [data-ok]').click();
    await page.locator('[data-post="A1#0"] .post-hold').waitFor({state:'detached'});
    await cm.locator('.rt-post.holding').waitFor({state:'detached',timeout:15000});
    assert.deepEqual(errors,[]);await watcher.close();await context.close();await browser.close();browser=null;
    console.log(name+': TR set/cancel, CM read-only shared state, five widths, actual API passed');
  }
}finally{
  if(browser)await browser.close();
  if(server&&server.exitCode===null){await new Promise(resolve=>{const kill=spawn('taskkill',['/PID',String(server.pid),'/T','/F'],{windowsHide:true});kill.on('exit',resolve);});}
}})().catch(error=>{console.error(error);process.exitCode=1;});
