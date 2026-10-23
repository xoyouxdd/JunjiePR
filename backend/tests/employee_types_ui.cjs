'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),net=require('node:net');
const {spawn}=require('node:child_process'),{chromium,webkit,firefox}=require('playwright');
const root=path.resolve(__dirname,'../..');let server,browser,log='';const sleep=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{try{
  const sock=net.createServer();await new Promise(r=>sock.listen(0,'127.0.0.1',r));const port=sock.address().port;await new Promise(r=>sock.close(r));
  server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'employee_types_fixture.py'),String(port)],{cwd:path.join(root,'backend'),windowsHide:true});server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);
  const base='http://127.0.0.1:'+port;let ready=false;for(let i=0;i<100;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await sleep(500);}assert(ready,log);
  let index=0;
  for(const engine of (process.env.EMPLOYEE_TYPE_ENGINES||'edge').split(',')){
    const no='9847'+String(index++).padStart(3,'0'),next=String(Number(no)+100);
    browser=await(engine==='webkit'?webkit:engine==='firefox'?firefox:chromium).launch(engine==='edge'?{channel:'msedge',headless:true}:{headless:true});
    const ctx=await browser.newContext({viewport:{width:1440,height:1000}}),page=await ctx.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
    assert((await ctx.request.post(base+'/api/login',{data:{employee_no:'HR01',password:'type-admin-only'}})).ok());
    const health=await ctx.request.get(base+'/health').then(r=>r.json());await ctx.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}});
    await page.goto(base+'/');await page.locator('.tabs-desktop [data-tab=hrEmployees]').click();await page.locator('#newEmployee').waitFor({state:'attached'});await page.locator('.hr-create-panel>summary').click();
    const form=page.locator('#newEmployee');await form.locator('[name=employee_no]').fill(no);await form.locator('[name=name]').fill('界面合成实习生');await form.locator('[name=role_code]').selectOption('CM');
    const options=await ctx.request.get(base+'/api/options').then(r=>r.json()),circle=options.employee_circles.find(c=>c.name==='热力追踪');await form.locator('[name=attraction_id]').selectOption(String(circle.id));
    assert.equal(await form.locator('[data-intern-date]').isVisible(),false);await form.locator('[name=employee_type]').fill('Intern');assert(await form.locator('[data-intern-date]').isVisible());
    await form.locator('[name=internship_ends_on]').fill('2099-12-18');
    for(const width of [320,375,390,430,768,1024,1440]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,engine+' create overflow '+width);const boxes=await form.locator('[name=internship_ends_on]').evaluate(el=>({left:el.getBoundingClientRect().left,right:el.getBoundingClientRect().right,parent:el.parentElement.getBoundingClientRect().right}));assert(boxes.right<=boxes.parent+1,engine+' date overflow '+width);}
    const created=page.waitForResponse(r=>r.url().endsWith('/api/hr/employees')&&r.request().method()==='POST');await form.locator('[type=submit]').click();const response=await created;assert(response.ok(),await response.text());let people=await ctx.request.get(base+'/api/hr/employees').then(r=>r.json());const person=people.find(p=>p.employee_no===no);assert(person&&person.employee_type==='Intern'&&person.base_role_code==='CM');
    const row=page.locator(`[data-hr-person="${person.id}"]`);await row.locator('.hr-person-type').filter({hasText:'2099-12-18'}).waitFor();
    await row.locator('[data-hr-edit]').click();const edit=page.locator('[data-modal-form]');await edit.locator('[name=employee_type]').fill('Comp40');assert.equal(await edit.locator('[data-intern-date]').isVisible(),false);await edit.locator('[type=submit]').click();await edit.waitFor({state:'detached'});await row.locator('.hr-person-type').filter({hasText:'Comp40'}).waitFor();
    people=await ctx.request.get(base+'/api/hr/employees').then(r=>r.json());assert.equal(people.find(p=>p.id===person.id).internship_ends_on,'');
    await row.locator('[data-hr-edit]').click();await edit.locator('[name=employee_type]').fill('Intern');await edit.locator('[name=internship_ends_on]').fill('2099-12-18');
    for(const width of [320,390,430,768,1440]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,engine+' edit overflow '+width);}
    await edit.locator('[type=submit]').click();await edit.waitFor({state:'detached'});await row.locator('.hr-person-type').filter({hasText:'2099-12-18'}).waitFor();
    const excel=await ctx.request.get(base+`/__fixture/workbook?existing=${no}&new=${next}`);await page.locator('details').filter({has:page.locator('#importEmployees')}).locator('summary').click();
    await page.locator('#importEmployeesWorkbook').setInputFiles({name:'synthetic.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:await excel.body()});await page.locator('#importEmployees [type=submit]').click();
    await page.getByRole('dialog',{name:'导入预检'}).waitFor();assert.match(await page.getByRole('dialog',{name:'导入预检'}).innerText(),/已有员工/);await page.locator('[data-modal-confirm]').click();await page.getByRole('dialog',{name:'导入已完成'}).waitFor();await page.locator('[data-modal-confirm]').click();
    await page.locator('#newEmployee').waitFor({state:'attached'});people=await ctx.request.get(base+'/api/hr/employees').then(r=>r.json());assert(people.some(p=>p.employee_no===next&&p.employee_type==='Intern'));assert.equal(people.find(p=>p.employee_no===no).name,'界面合成实习生');
    for(const width of [320,390,430,768,1024,1440]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,engine+' directory overflow '+width);}
    const shots=process.env.EMPLOYEE_TYPE_SCREENSHOTS;if(shots){fs.mkdirSync(shots,{recursive:true});await page.setViewportSize({width:390,height:844});await row.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(shots,engine+'-employee-mobile.png')});await page.setViewportSize({width:1440,height:1000});await row.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(shots,engine+'-employee-desktop.png')});}
    assert.deepEqual(errors,[]);console.log(engine+': actual create/edit, Intern/date visibility, V3 preview/skip, preserved old data, mobile/desktop layout passed');await browser.close();browser=null;
  }
}finally{if(browser)await browser.close();if(server){server.kill();await Promise.race([new Promise(r=>server.once('exit',r)),sleep(3000)]);}}})().catch(e=>{console.error(e);process.exitCode=1;});
