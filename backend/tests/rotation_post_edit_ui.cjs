'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process');const {chromium,webkit,firefox}=require('playwright');
const root=path.resolve(__dirname,'../..'),work=fs.mkdtempSync(path.join(os.tmpdir(),'rotation-post-edit-'));
const report=path.join(root,'.handover_review/rotation-post-edit');fs.mkdirSync(report,{recursive:true});
let server,browser,log='';const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function ok(response){assert(response.ok(),await response.text());return response.json();}
async function main(){try{
  const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
  server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'rotation_daily_fixture.py'),work,String(port)],{cwd:path.join(root,'backend'),windowsHide:true,env:{...process.env,ROTATION_FORECAST_FIXTURE:'1',PYTHONUTF8:'1'}});
  server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);const base='http://127.0.0.1:'+port;
  let ready=false;for(let i=0;i<120;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await sleep(250);}assert(ready,log);
  for(const engine of (process.env.ROTATION_POST_EDIT_ENGINES||'edge,webkit,firefox').split(',')){
    browser=await(engine==='webkit'?webkit:engine==='firefox'?firefox:chromium).launch(engine==='edge'?{channel:'msedge',headless:true}:{headless:true});
    const context=await browser.newContext({viewport:{width:1280,height:900}}),request=context.request;
    await ok(await request.post(base+'/api/login',{data:{employee_no:'GSMTEST01',password:'daily-fixture-2026'}}));
    await ok(await request.post(base+'/api/rotation/enter',{data:{account:'7777777'}}));
    await ok(await request.post(base+'/api/rotation/test-clock',{data:{action:'set',date:'2026-10-04',time:'07:15'}}));
    await ok(await request.post(base+'/api/rotation/draft',{data:{action:'draft_generate',date:'2026-10-04',force:true}}));
    await ok(await request.post(base+'/api/rotation/draft',{data:{action:'publish',date:'2026-10-04'}}));
    const board=async()=>(await ok(await request.get(base+'/api/rotation/board'))).day;
    const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));let renameWrites=0;
    page.on('request',r=>{if(r.method()==='POST'&&r.postDataJSON()?.action==='rename_post')renameWrites++;});
    await page.goto(base+'/rotation');await page.locator('[data-f-post="A#0"]').waitFor();
    const original=(await board()).lines[0].posts[0],name='新入口<&>';
    await page.locator('[data-f-post="A#0"]').dblclick();await page.getByRole('textbox',{name:'岗位名称',exact:true}).fill(name);
    await page.getByRole('textbox',{name:'岗位名称',exact:true}).press('Enter');
    await page.waitForFunction(name=>RT.data.day.lines[0].posts[0].name===name&&!RT.editingPost,name);
    assert.equal(renameWrites,1);assert.equal(await page.locator('[data-f-post="A#0"]').innerText(),name);
    assert.equal((await board()).lines[0].posts[0].key,original.key);assert.equal((await board()).lines[0].posts[0].occ,original.occ);
    await page.locator('[data-f-post="A#0"]').dblclick();await page.getByRole('textbox',{name:'岗位名称',exact:true}).fill('取消的名字');await page.getByRole('textbox',{name:'岗位名称',exact:true}).press('Escape');
    assert.equal(renameWrites,1);assert.equal((await board()).lines[0].posts[0].name,name);
    await page.locator('[data-f-post="A#0"]').dblclick();await page.getByRole('textbox',{name:'岗位名称',exact:true}).press('Enter');assert.equal(renameWrites,1);
    // True native post-label drag must open the post dialog, not the person dialog.
    const drag=async(source,target)=>{
      await page.waitForFunction(()=>!RT.loading&&!RT.modalOpen&&!RT.editingPost&&!RT.dragging&&!RT.submitting);
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      await page.locator('[data-f-post="'+source+'"]').dragTo(page.locator('[data-f-slot="'+target+'"]'));
      await page.getByRole('heading',{name:'选择岗位交换方式',exact:true}).waitFor();
      assert.equal(await page.locator('[name=place_mode]').count(),0);
      assert.equal(await page.locator('[name=swapMode] option').count(),2);
    };
    const save=async mode=>{await page.locator('[name=swapMode]').selectOption(mode);await page.getByRole('button',{name:'确定',exact:true}).click();await page.waitForFunction(()=>!RT.modalOpen);};
    let before=await board();await drag('A#0','A#1');
    for(const width of [390,1280]){await page.setViewportSize({width,height:900});assert(await page.getByRole('dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1));await page.screenshot({path:path.join(report,engine+'-'+width+'.png'),animations:'disabled'});}
    await save('posts');let after=await board();assert.equal(after.lines[0].posts[1].name,name);assert.equal(after.lines[0].posts[0].occ,before.lines[0].posts[0].occ);assert.equal(after.lines[0].posts[1].occ,before.lines[0].posts[1].occ);
    before=after;await drag('A#0','A#1');await save('with_people');after=await board();
    assert.equal(after.lines[0].posts[0].key,before.lines[0].posts[1].key);assert.equal(after.lines[0].posts[0].occ,before.lines[0].posts[1].occ);
    before=after;await drag('A#0','B#1');await save('with_people');after=await board();
    assert.equal(after.lines[0].posts[0].key,before.lines[1].posts[1].key);assert.equal(after.lines[0].posts[0].occ,before.lines[1].posts[1].occ);assert.equal(after.lines[1].posts[1].occ,before.lines[0].posts[0].occ);
    before=after;await drag('A#1','B#0');await save('posts');after=await board();
    assert.equal(after.lines[0].posts[1].key,before.lines[1].posts[0].key);assert.equal(after.lines[0].posts[1].occ,before.lines[0].posts[1].occ);assert.equal(after.lines[1].posts[0].occ,before.lines[1].posts[0].occ);
    // Dragging a person remains the separate push/person-swap operation.
    const pid=after.lines[0].posts[0].occ;
    await page.locator('[data-f-drag="'+pid+'"]').first().dragTo(page.locator('[data-f-slot="B#0"]'));
    await page.getByRole('heading',{name:'现场安排',exact:true}).waitFor();assert.equal(await page.locator('[name=place_mode]').count(),1);assert.equal(await page.locator('[name=swapMode]').count(),0);
    await page.getByRole('button',{name:'取消',exact:true}).click();
    assert.deepEqual(errors,[]);await context.close();await browser.close();browser=null;
    console.log(engine+': double-click rename, Enter/Escape/no-change, same/cross-line post swap modes, distinct person drag and 390/1280px PASS');
  }
}finally{if(browser)await browser.close();if(server&&server.exitCode===null){server.kill();await new Promise(r=>server.once('exit',r));}fs.writeFileSync(path.join(report,'server.log'),log);}}
main().catch(error=>{console.error(error);console.error(log.slice(-4000));process.exitCode=1;});
