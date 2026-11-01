// Exercise the real panel functions with isolated HR API data; no live accounts or database.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const staticRoot=path.resolve(__dirname,'../app/static');
const source=fs.readFileSync(path.join(staticRoot,'js/app/hr-employees.js'),'utf8');
const functions=source.slice(source.indexOf('function temporaryRecognizerPanel(){'),source.indexOf('async function renderHrEmployees(){'));
const css=['style.css','workspace-ui.css'].map(f=>fs.readFileSync(path.join(staticRoot,'css',f),'utf8')).join('\n');

(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    for(const width of [320,390,1280]){
      const page=await browser.newPage({viewport:{width,height:1000}});
      const errors=[];page.on('pageerror',e=>errors.push(e.message));
      await page.setContent('<style>'+css+'</style><main id="app" data-workspace-ui></main>');
      await page.evaluate('(()=>{'+functions+`;window.messages=[];window.calls=[];window.confirmations=[];
        window.esc=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
        window.captureViewContext=()=>({isCurrent:()=>true});window.json=(method,body)=>({method,body});
        window.toast=(...args)=>messages.push(args);window.confirmModal=async(title,html)=>{confirmations.push({title,html});return true;};
        window.fixture={month:'2026-11',employees:[
          {id:1,name:'测试甲',employee_no:'1234567',attraction_id:1,attraction_name:'热力追踪',eligible:true,revision:'a',dates:[],records:[]},
          {id:2,name:'测试乙',employee_no:'7654321',attraction_id:2,attraction_name:'矮人迷宫',eligible:true,revision:'b',dates:['2026-11-03'],records:[{date:'2026-11-03',status:'active',reason:'已有资格'}]}
        ]};
        window.api=async(url,options)=>{calls.push({url,options});if(options){const employee=fixture.employees.find(e=>url.endsWith('/'+e.id));employee.dates=options.body.dates;employee.records=employee.dates.map(date=>({date,status:'active',reason:options.body.reason}));employee.revision+='x';return {ok:true};}return fixture;};
        document.getElementById('app').innerHTML=temporaryRecognizerPanel();bindTemporaryRecognizers();})()`);
      assert.equal((await page.evaluate(()=>calls)).length,0,'collapsed panel loads lazily');
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
      await page.waitForFunction(()=>messages.some(m=>m[0]==='临时主管认可资格已保存'));
      const saved=await page.evaluate(()=>calls.find(c=>c.options));
      assert.equal(saved.url,'/api/hr/temporary-recognizers/1');
      assert.deepEqual(saved.options.body,{dates:['2026-11-03','2026-11-08'],reason:'临时主管认可安排',expected_revision:'a'});
      assert(!('role_code' in saved.options.body));
      await page.locator('[data-adjust-temporary="1"]').click();
      assert.equal(await page.locator('[data-temporary-days] input:checked').count(),2);
      await page.locator('[name=keyword]').fill('7654321');
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
