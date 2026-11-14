const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium,webkit}=require('./support/browser-runtime.cjs');
const root=path.resolve(__dirname,'../app/static');
const matched=[{file_employee_no:'01836527',source_name:'Jason',system_employee_no:'1836527',system_name:'张森 Jason <b>测试</b>',match_method:'工号匹配、姓名包含匹配'},
 {file_employee_no:'1836527',source_name:'张森',system_employee_no:'1836527',system_name:'张森 Jason',match_method:'工号匹配、姓名包含匹配'}];
const shell=`<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/static/css/style.css"><div id="tabs"></div><main id="app"></main><div id="toast"></div><script type="module">
import {state} from '/static/js/app/state.js';import {renderSickLeaveImport} from '/static/js/app/absence.js';
state.me={permissions:['SICK_LEAVE_IMPORT'],role_code:'GSM'};state.options={employee_circles:[]};state.tab='sickLeaveImport';window.openImport=renderSickLeaveImport;window.testState=state;await renderSickLeaveImport();window.ready=true;</script>`;
let previews=0;
const server=http.createServer((req,res)=>{
 const url=new URL(req.url,'http://localhost'),route=url.pathname;
 if(route==='/'){res.setHeader('Content-Type','text/html; charset=utf-8');return res.end(shell);}
 if(route.startsWith('/static/')){const file=path.resolve(root,'.'+route.slice(7));if(!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.statusCode=404;return res.end();}res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':'text/css');return res.end(fs.readFileSync(file));}
 res.setHeader('Content-Type','application/json');
 if(route==='/api/sick-leave-imports/records')return res.end(JSON.stringify({items:[]}));
 if(route==='/api/sick-leave-imports/preview'){previews++;return res.end(JSON.stringify({token:'demo',month:'2098-06',matched,matched_employee_count:1,matched_record_count:2,replaced_record_count:0,absent_employee_count:0,can_commit:true}));}
 res.statusCode=404;res.end('{}');
});
(async()=>{let browser;try{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 for(const [name,type,launch] of [['edge',chromium,{channel:'msedge'}],['webkit',webkit,{}]]){
  browser=await type.launch({...launch,headless:true});
  for(const width of [390,1280]){
   const page=await browser.newPage({viewport:{width,height:900}}),errors=[];page.on('pageerror',e=>{errors.push(e.message);console.error('browser error:',e.message);});
   await page.goto('http://127.0.0.1:'+server.address().port);await page.waitForFunction(()=>window.ready);
   assert.match(await page.locator('#sickLeaveImportForm').textContent(),/前导0/);
   await page.locator('[name=month]').fill('2098-06');
   await page.locator('[name=workbook]').setInputFiles({name:'names.xls',mimeType:'application/vnd.ms-excel',buffer:Buffer.from('mock workbook')});
   await page.locator('#sickLeaveImportForm [type=submit]').click();
   await page.getByText('查看已匹配明细（2条）').click();
   const table=page.locator('#sickLeaveImportResult details table');assert.equal(await table.locator('tbody tr').count(),2);
   assert.match(await table.textContent(),/01836527/);assert.match(await table.textContent(),/1836527/);
   assert.match(await table.textContent(),/张森 Jason/);assert.match(await table.textContent(),/姓名包含匹配/);
   assert.equal(await table.locator('b').count(),0);assert.match(await table.textContent(),/<b>测试<\/b>/);
   assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1));
   const before=previews;await page.evaluate(async()=>{testState.me.permissions=[];await openImport();});
   assert.match(await page.locator('#app').textContent(),/没有缺勤登记权限/);assert.equal(previews,before);
   assert.deepEqual(errors,[]);await page.close();
  }
  console.log(name+': import number/name comparison, escaped text, mobile/desktop and permission UI passed');await browser.close();browser=null;
 }
}finally{if(browser)await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));}})().catch(e=>{console.error(e);process.exitCode=1;});
