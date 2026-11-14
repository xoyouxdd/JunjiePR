// Real native module renderer and API, confined to a fresh temporary database.
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),net=require('node:net'),assert=require('node:assert/strict');
const {spawn}=require('node:child_process'),{chromium}=require('./support/browser-runtime.cjs');
const root=path.resolve(__dirname,'../..'),wait=ms=>new Promise(r=>setTimeout(r,ms));
let server,browser,log='';
(async()=>{try{
 const listener=net.createServer();await new Promise(r=>listener.listen(0,'127.0.0.1',r));const port=listener.address().port;await new Promise(r=>listener.close(r));
 const data=fs.mkdtempSync(path.join(os.tmpdir(),'scoring-ui-'));
 server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'scoring_config_fixture.py'),String(port)],{cwd:path.join(root,'backend'),windowsHide:true,env:{...process.env,RECOGNITION_V2_DATA_DIR:data,RECOGNITION_ENVIRONMENT:'test',RECOGNITION_ENABLE_TEST_ACCOUNTS:'1',RECOGNITION_TEST_DEFAULT_PASSWORD:'1234',RECOGNITION_TEST_ADMIN_PASSWORD:'HR123'}});
 server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);
 const base='http://127.0.0.1:'+port;let ready=false;for(let i=0;i<100;i++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await wait(250);}assert(ready,log);
 browser=await chromium.launch({channel:'msedge',headless:true});
 const harness='<link rel="stylesheet" href="/static/css/style.css"><link rel="stylesheet" href="/static/css/score-settings.css"><main id="app"></main><nav id="tabs"></nav><div id="toast"></div><script type="module">import {state} from "/static/js/app/state.js";import {api} from "/static/js/app/api.js";import {renderScoreSettings} from "/static/js/app/score-settings.js";state.me=await api("/api/me");state.options=await api("/api/options");state.tab="hrScores";await renderScoreSettings();window.settingsReady=true;</script>';
 const errors=[];
 async function open(account){const context=await browser.newContext({viewport:{width:1024,height:1000}});const login=await context.request.post(base+'/api/login',{data:{employee_no:account,password:account==='HR01'?'HR123':'1234'}});assert.equal(login.status(),200,await login.text());const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.route(base+'/__scoring-ui',route=>route.fulfill({contentType:'text/html',body:harness}));await page.goto(base+'/__scoring-ui');await page.waitForFunction(()=>window.settingsReady);return page;}
 const admin=await open('HR01');
 const confirm=async(page,action)=>{await action();await page.locator('[data-rule-form] [type=submit]').click();await page.locator('[data-commit]').click();await page.locator('.ss-message').filter({hasText:'规则已保存'}).waitFor();};
 await admin.locator('[data-edit=MSP]').click();
 await confirm(admin,async()=>{await admin.locator('[name=score]').fill('2');await admin.locator('[name=countEnabled]').check();await admin.locator('[name=overflow]').selectOption('zero');});
 let cfg=await admin.request.get(base+'/api/hr/scoring-config').then(r=>r.json());let msp=cfg.recognition.frontline.find(r=>r.code==='MSP');assert.equal(msp.score,2);assert.equal(msp.count,1);assert.equal(msp.overflow,'zero');assert.equal(cfg.recognition.supervisor.find(r=>r.code==='MSP').score,3);
 await admin.locator('[data-tab=deductions]').click();await admin.locator('[data-operator]').selectOption('direct');assert.equal(await admin.locator('.sr-rulerow').count(),16);await admin.locator('[data-operator]').selectOption('manager');assert.equal(await admin.locator('.sr-rulerow').count(),18);
 await admin.locator('[data-edit=ATT_LATE_OVER_30]').click();assert(await admin.locator('[name=direct]').isDisabled());await admin.locator('[data-cancel-editor]').click();
 await admin.locator('[data-edit=SAFETY]').click();await confirm(admin,async()=>{await admin.locator('[name=mode]').selectOption('fixed');assert(await admin.locator('[name=direct]').isDisabled());await admin.locator('[name=score]').fill('2.5');});
 await admin.locator('[data-operator]').selectOption('direct');assert.equal(await admin.locator('.sr-rulerow').count(),15);
 for(const width of [320,390,1280]){await admin.setViewportSize({width,height:1000});for(const tab of ['frontline','supervisor','deductions']){await admin.locator('[data-tab='+tab+']').click();assert(!(await admin.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1)),tab+' overflow '+width);}console.log('PASS real scoring UI layout',width);}
 const hr=await open('HR-HEAT');assert(!(await hr.locator('[data-add]').isVisible()));await hr.locator('[data-edit=MSP]').click();assert(await hr.locator('[name=score]').isDisabled());assert(!(await hr.locator('[data-rule-form] [type=submit]').isVisible()));
 assert.equal((await hr.request.post(base+'/api/hr/scoring-config',{data:{revision:cfg.revision,kind:'role',scope:'frontline',code:'GSM',effective_date:cfg.date,policy:{score:9}}})).status(),403);
 assert.deepEqual(errors,[]);console.log('PASS actual API: fixed score + monthly quota, separate recipients, deduction visibility, immutable supervisor boundary, HR read-only');
}finally{if(browser)await browser.close();if(server)server.kill();}})().catch(e=>{console.error(e);console.error(log.slice(-2500));process.exitCode=1;});
