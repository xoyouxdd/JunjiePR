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
    await page.getByRole('dialog',{name:'确认导入',exact:true}).waitFor();await page.getByRole('button',{name:'确认导入',exact:true}).click();await page.getByRole('dialog',{name:'导入已完成'}).waitFor();await page.getByRole('button',{name:'知道了',exact:true}).click();
    await page.locator('#newEmployee').waitFor({state:'attached'});people=await ctx.request.get(base+'/api/hr/employees').then(r=>r.json());assert(people.some(p=>p.employee_no===next&&p.employee_type==='Intern'));assert.equal(people.find(p=>p.employee_no===no).name,'界面合成实习生');
    for(const width of [320,390,430,768,1024,1440]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,engine+' directory overflow '+width);}
    const shots=process.env.EMPLOYEE_TYPE_SCREENSHOTS;if(shots){fs.mkdirSync(shots,{recursive:true});await page.setViewportSize({width:390,height:844});await row.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(shots,engine+'-employee-mobile.png')});await page.setViewportSize({width:1440,height:1000});await row.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(shots,engine+'-employee-desktop.png')});}
    const dwarf=await browser.newContext({viewport:{width:1440,height:900}}),dwarfPage=await dwarf.newPage();dwarfPage.on('pageerror',e=>errors.push(e.message));
    assert((await dwarf.request.post(base+'/api/login',{data:{employee_no:'HR-DWARF',password:'type-fixture-only'}})).ok());
    await dwarf.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}});
    await dwarfPage.goto(base+'/');await dwarfPage.locator('.tabs-desktop [data-tab=hrEmployees]').click();await dwarfPage.locator('#importEmployees').waitFor({state:'attached'});
    await dwarfPage.locator('details').filter({has:dwarfPage.locator('#importEmployees')}).locator('summary').click();
    const workbook=await ctx.request.get(base+'/__fixture/simple-workbook?index='+(index-1));assert(workbook.ok());
    await dwarfPage.locator('#importEmployeesWorkbook').setInputFiles({name:'员工导入.xlsx',mimeType:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',buffer:await workbook.body()});
    await dwarfPage.locator('#importEmployees [type=submit]').click();
    const previewDialog=dwarfPage.getByRole('dialog',{name:'确认导入',exact:true});await previewDialog.waitFor();
    assert.equal(await previewDialog.locator('[data-import-row]').count(),6);
    assert.equal(await previewDialog.locator('[data-import-row="5"]').count(),0,'identical row should not prompt');
    assert.equal(await previewDialog.locator('[data-import-row="4"] [data-import-original]').count(),0,'both original values empty should be omitted');
    assert.match(await previewDialog.locator('[data-import-row="3"] [data-import-original]').innerText(),/原Type：Intern.*原实习结束：2099-11-30/);
    assert.equal(await previewDialog.locator('[data-import-row="2"] [data-import-original]').innerText(),'原Type：Comp28');
    assert(await previewDialog.locator('[data-import-row="6"] [name=action]').isDisabled());
    assert.match(await previewDialog.locator('[data-import-row="6"]').innerText(),/陈雨.*陈宇/s);assert.match(await previewDialog.locator('[data-import-row="6"]').innerText(),/系统员工管理/);
    assert(await previewDialog.locator('[data-import-row="7"] [name=action]').isDisabled());
    await previewDialog.locator('[data-import-all]').click();assert.equal(await previewDialog.locator('[name=action]:checked').count(),4);
    await previewDialog.locator('[data-import-none]').click();assert.equal(await previewDialog.locator('[name=action]:checked').count(),0);
    await previewDialog.locator('[data-import-all]').click();
    for(const width of [320,390,600,768,1440]){
      await dwarfPage.setViewportSize({width,height:844});assert.equal(await dwarfPage.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,engine+' simple import overflow '+width);
      const bounds=await previewDialog.evaluate(el=>{const b=el.querySelector('[type=submit]').getBoundingClientRect();return {top:b.top,bottom:b.bottom,height:innerHeight,scroll:el.scrollHeight-el.clientHeight};});assert(bounds.top>=0&&bounds.bottom<=bounds.height&&bounds.scroll<=1,engine+' fixed confirm '+width);
    }
    if(shots){for(const width of [1440,390]){await dwarfPage.setViewportSize({width,height:844});await previewDialog.evaluate(el=>Promise.all(el.getAnimations({subtree:true}).map(a=>a.finished.catch(()=>{}))));await dwarfPage.screenshot({path:path.join(shots,engine+'-import-simple-'+width+'.png')});}}
    await dwarfPage.getByRole('button',{name:'确认导入',exact:true}).click();await dwarfPage.getByRole('dialog',{name:'导入已完成'}).waitFor();await dwarfPage.getByRole('button',{name:'知道了',exact:true}).click();
    const peopleAfter=await dwarf.request.get(base+'/api/hr/employees').then(r=>r.json()),firstNo=9860000+(index-1)*100;
    const get=n=>peopleAfter.find(person=>person.employee_no===String(firstNo+n));
    assert.equal(get(0).employee_type,'Comp40');assert.equal(get(1).internship_ends_on,'2099-12-31');assert.equal(get(2).employee_type,'Intern');assert.equal(get(3).employee_type,'Comp40');assert.equal(get(4).name,'陈雨');assert.equal(get(4).employee_type,'Comp28');assert.equal(get(6).employee_type,'');
    const allPeople=await ctx.request.get(base+'/api/hr/employees').then(r=>r.json());assert.equal(allPeople.find(person=>person.employee_no===String(firstNo+5)).employee_type,'Comp28');
    assert.equal(get(0).account_enabled,false);
    await dwarf.close();assert.deepEqual(errors,[]);console.log(engine+': employee create/edit, two-choice file overwrite, unchanged omission, nonempty originals, mismatch/cross-circle skip and actual import passed');await browser.close();browser=null;

  }
}finally{if(browser)await browser.close();if(server){server.kill();await Promise.race([new Promise(r=>server.once('exit',r)),sleep(3000)]);}}})().catch(e=>{console.error(e);process.exitCode=1;});
