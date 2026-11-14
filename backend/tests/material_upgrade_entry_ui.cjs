const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium,webkit}=require('./support/browser-runtime.cjs');
const root=path.resolve(__dirname,'../app/static');
const startup=`import {state,app} from '/static/js/app/state.js';
import {openDeductionMaterialRetry,deductionMaterialPickerMarkup} from '/static/js/app/deduction-materials.js';
import {bindDeductionV2244,deductionForm} from '/static/js/app/deductions.js';
import {upgradeRecoveryCards,bindUpgradeRecoveryActions,upgradeReviewCards,bindUpgradeReviewActions} from '/static/js/app/reviews.js';
import {renderActionCenter} from '/static/js/app/operations.js';
state.me={id:1,permissions:['DEDUCTION_ALL']};state.options={deduction_levels:[{id:1,code:'STATEMENT',name:'声明'},{id:2,code:'MEMO',name:'备忘录',points:3},{id:3,code:'WARNING_1',name:'一级警告',points:5}],deduction_types:[{id:1,code:'SICK_LEAVE_VIOLATION',name:'违规病假'}]};
window.recovery=()=>{const data={items:[{id:77,employee_name:'原员工',employee_no:'CM1',reviewer_id:9,reviewer_name:'停用经理',status:'awaiting_material'}],reviewers:[{id:2,name:'接手经理',employee_no:'GSM2',role_name:'GSM'}]};app.innerHTML=upgradeRecoveryCards(data.items);bindUpgradeRecoveryActions(app,data,()=>window.recovered=true);};
window.minimum=()=>{app.innerHTML=upgradeReviewCards([{id:78,employee_name:'原员工',employee_no:'CM1',minimum_level_id:3,minimum_level_name:'一级警告'}]);bindUpgradeReviewActions(app,()=>{});};
window.actionCenter=async(role='SUPERVISOR')=>{state.me.id=1;state.me.role_code=role;state.me.permissions=role==='SUPERVISOR'?['DEDUCTION_DIRECT']:['DEDUCTION_ALL'];await renderActionCenter();};
window.openRetry=()=>openDeductionMaterialRetry(33,()=>window.finished=true);
window.newSupervisorEntry=()=>{state.me.role_code='GSM';state.me.permissions=['DEDUCTION_ALL','SUPERVISOR_SCORE'];app.innerHTML=deductionForm('supervisor');app.querySelector('[name=employee_id]').value='6';bindDeductionV2244();};
window.newEntry=()=>{app.innerHTML='<form id="deductionForm"><input name="employee_id" value="5"><input name="deduction_type_id" value="1"><input name="deduction_level_id" value="1"><input name="occurred_on" value="2026-11-01"><textarea name="description">原登记内容</textarea>'+deductionMaterialPickerMarkup()+'<button type="submit" id="deductionSubmit">提交</button></form><div id="deductionMaterialState"></div>';bindDeductionV2244();};window.ready=true;`;
const server=http.createServer((req,res)=>{const url=new URL(req.url,'http://x');if(url.pathname==='/fixture.js'){res.setHeader('Content-Type','text/javascript');return res.end(startup)}if(url.pathname==='/'){res.setHeader('Content-Type','text/html;charset=utf-8');return res.end('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/style.css"><main id="app"></main><div id="tabs"></div><div id="toast"></div><script type="module" src="/fixture.js"></script>')}const file=path.resolve(root,'.'+url.pathname.replace('/static',''));if(!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.statusCode=404;return res.end()}res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'application/octet-stream');res.end(fs.readFileSync(file))});
(async()=>{await new Promise(r=>server.listen(0,'127.0.0.1',r));try{for(const [name,type,opts] of [['edge',chromium,{channel:'msedge'}],['webkit',webkit,{}]]){const browser=await type.launch({...opts,headless:true});try{for(const width of [1280,390,320]){const page=await browser.newPage({viewport:{width,height:900}}),errors=[],posts=[];let first=70,workspaceRole='SUPERVISOR',noticeDismissed=false,assistingDone=false,pendingReads=0,taRecoveryAvailable=false,supervisorPreviews=0;page.on('pageerror',e=>errors.push(e.message));
await page.route('**/api/**',route=>{const req=route.request(),url=new URL(req.url());
if(url.pathname==='/api/deduction-upgrades/preview'&&url.searchParams.get('employee_id')==='6'){
  supervisorPreviews++;
  return route.fulfill({status:400,json:{detail:'只能为在职、账号启用的CM/TR登记扣分'}});
}
if(url.pathname==='/api/deduction-upgrades/pending'){
  pendingReads++;
  const mine={id:81,employee_name:'原员工',employee_no:'CM1',reviewer_id:1,reviewer_name:'本人经理'};
  const assist={id:82,employee_name:'其他员工',employee_no:'CM2',reviewer_id:2,reviewer_name:'其他经理'};
  const notice={id:80,employee_name:'协助对象',status:'approved',result_level:'备忘录',resolved_by_name:'协助经理',resolved_at:'2026-11-09 18:00:00'};
  return route.fulfill({json:{items:assistingDone?[mine]:[mine,assist],notices:noticeDismissed?[]:[notice]}});
}
if(url.pathname==='/api/deduction-upgrades/80/dismiss-notice'){noticeDismissed=true;return route.fulfill({json:{ok:true}});}
if(url.pathname==='/api/deduction-upgrades/82/resolve'){
  assistingDone=true;
  return route.fulfill({status:409,json:{detail:{code:'UPGRADE_ALREADY_RESOLVED',message:'已由其他经理处理：审核不通过'}}});
}
if(url.pathname==='/api/deduction-upgrades/recovery'&&workspaceRole!=='SUPERVISOR'&&!taRecoveryAvailable)return route.fulfill({json:{items:[]}});
if(url.pathname==='/api/deduction-upgrades/77/recover'&&workspaceRole==='TA_GSM'){
  taRecoveryAvailable=false;
  posts.push({url:req.url(),body:req.postData()});
  return route.fulfill({json:{ok:true}});
}
if(url.pathname==='/api/action-center')return route.fulfill({json:{month:'2026-11',total:0,items:[]}});if(url.pathname==='/api/deduction-upgrades/recovery')return route.fulfill({json:{items:[{id:77,employee_name:'原员工',employee_no:'CM1',reviewer_id:9,reviewer_name:'停用经理',status:'awaiting_material',awaiting_reviewer:workspaceRole==='TA_GSM',status_name:workspaceRole==='TA_GSM'?'材料已就绪 · 待重新指派':'待材料就绪'}],reviewers:[{id:2,name:'接手经理',employee_no:'GSM2',role_name:'GSM'}]}});if(url.pathname==='/api/export-tasks')return route.fulfill({json:{items:[]}});if(url.pathname==='/api/me')return route.fulfill({json:{id:1}});if(req.method()==='GET')return route.fulfill({json:{eligible:true,first_record:{id:first,occurred_on:'2026-10-01',deduction_type:'违规病假',description:'历史声明'},reviewers:[{id:1,name:'本人经理',employee_no:'GSM1',role_name:'GSM'},{id:2,name:'其他经理',employee_no:'GSM2',role_name:'GSM'}],default_reviewer_id:1}});posts.push({url:req.url(),body:req.postData()});return route.fulfill({json:{ok:true,record:{id:33,status:'pending_upgrade',material_status:'ready'}}})});
await page.goto('http://127.0.0.1:'+server.address().port);await page.waitForFunction(()=>window.ready);await page.evaluate(()=>openRetry());
const reviewer=page.locator('[data-material-upgrade-reviewer]');await reviewer.waitFor();assert.equal(await reviewer.inputValue(),'1');await reviewer.selectOption('2');
assert.equal(await page.locator('[data-material-camera-input]').getAttribute('capture'),'environment');
await page.locator('[data-material-pdf-input]').setInputFiles({name:'proof.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.4 test')});
assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
// A changed source must be acknowledged, while retaining selected material.
first=71;await page.locator('[data-retry-submit]').click();await page.waitForFunction(()=>document.getElementById('toast').textContent.includes('升级条件已变化'));assert.equal(posts.length,0);assert.equal(await page.locator('[data-material-pdf-input]').evaluate(el=>el.files.length),1);
await reviewer.selectOption('2');await page.locator('[data-retry-submit]').click();await page.waitForFunction(()=>window.finished);assert.equal(posts.length,1);assert.match(posts[0].url,/deductions\/33\/material$/);assert.match(posts[0].body,/name="reviewer_id"\r\n\r\n2/);assert.match(posts[0].body,/name="expected_first_id"\r\n\r\n71/);
await page.waitForSelector('.deduction-material-retry',{state:'detached'});
// Manager-only DEDUCTION_ALL goes through the same new-entry upgrade selector.
await page.evaluate(()=>newEntry());await page.locator('[data-material-pdf-input]').setInputFiles({name:'new.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.4 test')});await page.locator('#deductionSubmit').click();const select=page.locator('[data-modal-select]');await select.waitFor();assert.equal(await select.inputValue(),'1');await select.selectOption('2');await page.locator('[data-modal-confirm]').last().click();await page.waitForFunction(()=>document.getElementById('deductionSubmit').disabled===false);assert.equal(posts.length,2);assert.match(posts[1].url,/deduction-upgrades$/);assert.match(posts[1].body,/name="reviewer_id"\r\n\r\n2/);
await page.waitForSelector('[data-modal-select]',{state:'detached'});await page.evaluate(()=>recovery());await page.locator('[data-recover-upgrade]').click();await page.locator('[data-modal-select]').selectOption('2');await page.locator('[data-modal-confirm]').last().click();await page.locator('[data-modal-input]').fill('原经理停用，重新指派');await page.locator('[data-modal-confirm]').last().click();await page.waitForFunction(()=>window.recovered);assert.equal(posts.length,3);assert.match(posts[2].url,/deduction-upgrades\/77\/recover$/);assert.deepEqual(JSON.parse(posts[2].body),{reviewer_id:2,expected_reviewer_id:9,expected_status:'awaiting_material',reason:'原经理停用，重新指派'});
await page.waitForSelector('[data-modal-input]',{state:'detached'});await page.evaluate(()=>minimum());await page.locator('[data-upgrade-approve]').click();await page.locator('[data-modal-select]').waitFor();assert.equal(await page.locator('[data-modal-select] option[value="2"]').count(),1);assert.equal(await page.locator('[data-modal-select] option[value="3"]').count(),1);await page.locator('[data-modal-cancel]').click();await page.waitForSelector('[data-modal-select]',{state:'detached'});await page.evaluate(()=>actionCenter());await page.locator('[data-recover-upgrade="77"]').waitFor({state:'visible'});assert.equal(await page.locator('[data-action-pane="review"]').isVisible(),true);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.deepEqual(errors,[]);
workspaceRole='GSM';await page.evaluate(()=>actionCenter('GSM'));
await page.getByRole('heading',{name:'我的待审核'}).waitFor();
await page.getByRole('heading',{name:'可协助'}).waitFor();
assert.equal(await page.getByRole('button',{name:'转交工单'}).count(),0);
await page.locator('[data-upgrade="82"]').getByText('指定审核人：其他经理',{exact:false}).waitFor();
const notice=page.locator('[data-upgrade-notice="80"]');
await notice.getByText('协助经理 已协助处理 · 审核通过 · 已开具备忘录',{exact:true}).waitFor();
await notice.getByRole('button',{name:'关闭提醒'}).click();
await notice.waitFor({state:'detached'});
assert(noticeDismissed);
assert.equal(await page.locator('[data-action-pane-tab="review"] .badge').textContent(),'1');
await page.getByRole('button',{name:'协助判定不通过'}).click();
await page.locator('[data-modal-input]').fill('核对声明材料');
await page.getByRole('button',{name:'确认不通过'}).click();
await page.locator('#toast').getByText('已由其他经理处理：审核不通过',{exact:true}).waitFor();
await page.locator('[data-upgrade="82"]').waitFor({state:'detached'});
assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
workspaceRole='TA_GSM';const reads=pendingReads;
taRecoveryAvailable=true;
await page.evaluate(()=>actionCenter('TA_GSM'));
await page.getByRole('button',{name:/^重新指派/}).waitFor();
await page.getByText('PDF 已生成，重新指派后可直接进入审核。',{exact:true}).waitFor();
assert.equal(await page.getByRole('heading',{name:'我的待审核'}).count(),0);
assert.equal(await page.locator('[data-upgrade-approve],[data-upgrade-reject]').count(),0);
await page.locator('[data-recover-upgrade="77"]').click();
await page.locator('[data-modal-select]').selectOption('2');
await page.locator('[data-modal-confirm]').last().click();
await page.locator('[data-modal-input]').fill('待材料工单重新指派');
await page.locator('[data-modal-confirm]').last().click();
await page.locator('[data-recover-upgrade="77"]').waitFor({state:'detached'});
assert.deepEqual(JSON.parse(posts.at(-1).body),{reviewer_id:2,expected_reviewer_id:9,expected_status:'awaiting_material',reason:'待材料工单重新指派'});
assert.equal(await page.locator('[data-action-pane-tab="review"]').count(),0);
assert.equal(pendingReads,reads);
// The supervisor form submits the selected PDF directly to ordinary deduction entry.
await page.evaluate(()=>newSupervisorEntry());
await page.getByText('更多选项',{exact:true}).click();
await page.locator('#deductionForm textarea').fill('主管普通声明');
await page.locator('[data-material-pdf-input]').setInputFiles({name:'supervisor.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.4 test')});
page.once('dialog',dialog=>dialog.accept());
const ordinaryPost=page.waitForRequest(req=>req.method()==='POST'&&new URL(req.url()).pathname==='/api/deductions');
await page.locator('#deductionSubmit').click();
const ordinary=await ordinaryPost;
assert.equal(supervisorPreviews,0);
assert.match(ordinary.postData(),/name="employee_id"\r\n\r\n6/);
assert.match(ordinary.postData(),/filename="supervisor\.pdf"/);
assert.deepEqual(errors,[]);await page.close();
}console.log(name+': supplement reuses ID, source-change confirmation, manager upgrade, supervisor PDF entry and 1280/390/320 passed');}finally{await browser.close()}}}finally{server.closeAllConnections();await new Promise(r=>server.close(r))}})().catch(e=>{console.error(e);process.exitCode=1});
