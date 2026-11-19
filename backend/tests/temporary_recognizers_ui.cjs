// Exercise the real panel functions with isolated HR API data; no live accounts or database.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('./support/browser-runtime.cjs');
const {routeFrontend}=require('./support/frontend-page.cjs');
const html=`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/static/css/style.css"><link rel="stylesheet" href="/static/css/workspace-ui.css">
<main id="app" data-workspace-ui></main><div id="tabs"></div><div id="toast"></div>`;

(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    for(const width of [320,390,1280]){
      const page=await browser.newPage({viewport:{width,height:1000}});
      const errors=[];page.on('pageerror',e=>errors.push(e.message));
      const calls=[];
      const fixture={month:'2026-11',employees:[
        {id:1,name:'测试甲',employee_no:'1234567',attraction_id:1,attraction_name:'热力追踪',eligible:true,revision:'a',dates:[],records:[]},
        {id:2,name:'测试乙',employee_no:'7654321',attraction_id:2,attraction_name:'矮人迷宫',eligible:true,revision:'b',dates:['2026-11-03'],records:[{date:'2026-11-03',status:'active',reason:'已有资格'}]}
      ]};
      await page.route('**/api/hr/organization',route=>route.fulfill({json:{circles:[],others:[]}}));
      await page.route('**/api/hr/temporary-recognizers**',route=>{
        const request=route.request(),url=new URL(request.url()).pathname;
        const body=request.method()==='PUT'?request.postDataJSON():null;
        calls.push({url,body});
        if(body){
          const employee=fixture.employees.find(e=>url.endsWith('/'+e.id));
          employee.dates=body.dates;
          employee.records=employee.dates.map(date=>({date,status:'active',reason:body.reason}));
          employee.revision+='x';
          return route.fulfill({json:{ok:true}});
        }
        return route.fulfill({json:fixture});
      });
      await page.goto(await routeFrontend(page,{html}));
      await page.evaluate(async()=>{
        const {state}=await import('/static/js/app/state.js');
        const {renderHrEmployees}=await import('/static/js/app/hr-employees.js');
        state.me={role_code:'HR_CIRCLE',permissions:['HR_MANAGE']};
        state.options={roles:[],employee_circles:[]};
        state.tab='hrEmployees';
        await renderHrEmployees();
      });
      assert.equal(calls.length,0,'collapsed panel loads lazily');
      await page.locator('#temporaryRecognizerPanel>summary').click();
      const form=page.locator('[data-temporary-form]');await form.waitFor();
      assert.equal(await form.locator('[data-temporary-days] input').count(),30);
      await form.locator('[name=circle]').selectOption('1');
      assert.equal(await form.locator('[name=employee] option').count(),2);
      await form.locator('[name=employee]').selectOption('1');
      await form.locator('input[aria-label="2026-11-03"]').check();
      await form.locator('input[aria-label="2026-11-08"]').check();
      assert.match(await form.locator('[data-temporary-summary]').textContent(),/已选2天/);
      await form.locator('[name=reason]').fill('临时主管认可安排');
      await form.locator('[type=submit]').click();
      const confirm=page.getByRole('dialog',{name:'确认临时主管认可资格'});
      await confirm.getByRole('button',{name:'确认保存',exact:true}).click();
      await page.locator('#toast').getByText('临时主管认可资格已保存').waitFor();
      const saved=calls.find(c=>c.body);
      assert.equal(saved.url,'/api/hr/temporary-recognizers/1');
      assert.deepEqual(saved.body,{dates:['2026-11-03','2026-11-08'],reason:'临时主管认可安排',expected_revision:'a'});
      assert(!('role_code' in saved.body));
      await page.locator('[data-adjust-temporary="1"]').click();
      assert.equal(await page.locator('[data-temporary-days] input:checked').count(),2);
      await form.locator('[name=keyword]').fill('7654321');
      assert.equal(await page.locator('[name=employee] option').count(),2);
      await page.locator('[name=employee]').selectOption('2');
      assert.equal(await page.locator('[data-temporary-days] input:checked').count(),1);
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no horizontal overflow');
      assert.deepEqual(errors,[]);
      if(process.env.TEMPORARY_UI_SCREENSHOT_DIR){
        fs.mkdirSync(process.env.TEMPORARY_UI_SCREENSHOT_DIR,{recursive:true});
        await page.screenshot({path:path.join(process.env.TEMPORARY_UI_SCREENSHOT_DIR,`temporary-recognizers-${width}.png`),fullPage:true});
      }
      await page.close();
    }
    console.log('PASS: 320/390/1280px; lazy load, circle/search, discrete dates, confirmation payload, saved records and no overflow');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
