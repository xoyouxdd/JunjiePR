// Synthetic API data only; exercises the actual embedded page and honor module.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium,webkit}=require('playwright');
const root=path.resolve(__dirname,'../app/static');
const out=path.resolve(__dirname,'../../output/attendance-medals-local');fs.mkdirSync(out,{recursive:true});
let unknownCode=false,classified=false,classifications=0;
let published=0,confirmed=0,imported=0,role='HR_CIRCLE',status='待确认发放',ready=true;
const rows=[{employee_id:10,employee_no:'1700010',name:'张三 <b>员工</b>',status:'拟发放',reason:'符合资格',first_month:'2026-11',leaves:[]},
 {employee_id:11,employee_no:'1700011',name:'李四',status:'未获得',reason:'当月存在有效请假记录',first_month:'2026-11',leaves:[{date:'2027-01-02',leave_type:'事假',hours:'1'}]}];
const shell=`<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/static/css/style.css"><link rel="stylesheet" href="/static/css/attendance-medals.css"><div id="tabs"></div><main id="app"></main><div id="toast"></div><script type="module">
import {state} from '/static/js/app/state.js';import {renderSickLeaveImport} from '/static/js/app/absence.js';import {mountMedalCollection} from '/static/js/app/attendance-medals.js';
state.me={id:10,permissions:['SICK_LEAVE_IMPORT'],role_code:'HR_CIRCLE'};state.options={employee_circles:[]};state.tab='sickLeaveImport';
window.setRole=async r=>{state.me.role_code=r;state.me.permissions=r==='SYSTEM_ADMIN'?['SYSTEM_ADMIN','SICK_LEAVE_IMPORT']:['SICK_LEAVE_IMPORT'];await renderSickLeaveImport();};
window.refreshCollection=()=>mountMedalCollection(document.getElementById('collection'),10,{lookup:true});
window.collection=async()=>{document.getElementById('app').innerHTML='<div id="collection"></div>';await mountMedalCollection(document.getElementById('collection'),10,{lookup:true});};
await renderSickLeaveImport();window.ready=true;</script>`;
const server=http.createServer(async(req,res)=>{
 const url=new URL(req.url,'http://localhost'),route=url.pathname;
 if(route==='/'){res.setHeader('Content-Type','text/html; charset=utf-8');return res.end(shell);}
 if(route.startsWith('/static/')){const file=path.resolve(root,'.'+route.slice(7));if(!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.statusCode=404;return res.end();}res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.webp')?'image/webp':'image/svg+xml');return res.end(fs.readFileSync(file));}
 res.setHeader('Content-Type','application/json');
 const month=url.searchParams.get('month')||'2027-01';
 if(route==='/api/sick-leave-imports/records')return res.end('{"items":[]}');
 if(route==='/api/attendance-medals/manage')return res.end(JSON.stringify({month,can_import:role==='SYSTEM_ADMIN',highest:role==='SYSTEM_ADMIN',revision:1,data_ready:ready,unassigned_count:0,
  batch:role==='SYSTEM_ADMIN'?{filename:'全员.xls',imported_by:'统一导入员',imported_at:'2027-02-01',leaves:1,issues:unknownCode?[{code_type:classified?'classification_changed':'unclassified',leave_type:'SL',reason:'请核实代码后重新替换导入'}]:[],ignored_codes:['正常工作','调休']}:null,
  wage_codes:classified?[{code:'SL',kind:'leave',revision:1}]:[],circles:[{id:1,name:'热力追踪'}],items:[{month,attraction_id:1,trial:month<'2027-01',revision:1,fingerprint:'v1',status:ready?status:'待数据就绪',can_publish:ready&&status!=='已发放',month_ended:true,rows,issues:unknownCode?[role==='SYSTEM_ADMIN'?{row:8,source_name:'敏感姓名',source_no:'1999999',reason:'需核实来源'}:{redacted:true,reason:'存在归属未明确的数据，请联系统一导入管理员处理'}]:[],changes:[{name:'张三 <b>员工</b>',action:'补发',reason:'无请假'}],rule:'依据请假数据，不绑定加分'}]}));
 if(route==='/api/attendance-medals/classify-code'){let body='';for await(const chunk of req)body+=chunk;const input=JSON.parse(body);assert.equal(input.code,'SL');assert.equal(input.kind,'leave');assert(input.reason);classifications++;classified=true;ready=false;return res.end('{"ok":true,"affected_months":["2027-01"]}');}
 if(route==='/api/attendance-medals/publish'){published++;status='已发放';return res.end('{"awarded":1,"revoked":0}');}
 if(route==='/api/attendance-medals/confirm-data'){confirmed++;ready=true;return res.end('{"ok":true}');}
 if(route==='/api/attendance-medals/import-preview')return res.end(JSON.stringify({revision:1,file_digest:'hash',leaves:2,added:1,removed:0,issues:[]}));
 if(route==='/api/attendance-medals/import'){imported++;ready=false;return res.end('{"leaves":2,"issues":[]}');}
 if(route==='/api/attendance-medals/history')return res.end('{"items":[{"time":"2027-02-02","operator":"圈管理员","action":"确认全勤勋章发放","reason":""}]}');
 if(route==='/api/attendance-medals/employees/10')return res.end(JSON.stringify({visible:true,count:1,items:[{month:'2027-01',issued_at:'2027-02-01'}],rule:'只按请假评定',notices:[{id:1,message:'你获得了2027-01月度全勤勋章',reason:'',read:false}],assessments:[]}));
 if(route==='/api/attendance-medals/employees/11')return res.end(JSON.stringify({visible:true,count:1,items:[{month:'2027-01',issued_at:'2027-02-01'}],rule:'只按请假评定'}));
 if(route==='/api/attendance-medals/people')return res.end(JSON.stringify({items:[{id:11,name:'李四',employee_no:'1700011'}]}));
 if(route==='/api/attendance-medals/notices/1/read')return res.end('{"ok":true}');
 res.statusCode=404;res.end('{}');
});
(async()=>{let browser;try{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 for(const [name,type,launch] of [['edge',chromium,{channel:'msedge'}],['webkit',webkit,{}]]){
  browser=await type.launch({...launch,headless:true});
  for(const width of [390,1280]){
   published=confirmed=imported=classifications=0;unknownCode=classified=false;role='HR_CIRCLE';status='待确认发放';ready=true;
   const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.addInitScript(()=>{const NativeDate=Date;window.NativeDate=NativeDate;window.Date=class extends NativeDate{constructor(...args){super(...(args.length?args:['2027-02-02T12:00:00+08:00']));}static now(){return new NativeDate('2027-02-02T12:00:00+08:00').valueOf();}};});
   await page.goto('http://127.0.0.1:'+server.address().port);await page.waitForFunction(()=>window.ready);
   await page.locator('[data-medal-publish]').waitFor();
   assert.equal(await page.locator('[data-medal-import]').count(),0,'circle HR cannot import all-employee data');
   assert.equal(await page.locator('.medal-person b').count(),0,'employee text is escaped');
   await page.locator('[data-medal-search]').fill('李四');assert.equal(await page.locator('.medal-person:visible').count(),1);
   await page.locator('[data-medal-search]').fill('');
   await page.locator('[data-medal-publish]').click();await page.locator('.modal-overlay:not(.is-leaving) [data-modal-confirm]').click();await page.waitForFunction(()=>document.querySelector('[data-medal-publish]')?.disabled);
   assert.equal(published,1);await page.locator('[data-medal-history]').click();await page.getByText('2027-02-02 · 圈管理员').waitFor();
   await page.screenshot({path:path.join(out,`manager-${name}-${width}.png`),fullPage:true});
   role='SYSTEM_ADMIN';await page.evaluate(()=>setRole('SYSTEM_ADMIN'));await page.locator('[data-medal-import]').waitFor({state:'attached'});
   await page.getByText('全员请假数据导入与确认',{exact:true}).click();
   await page.locator('[data-medal-import] [name=workbook]').setInputFiles({name:'全员.xls',mimeType:'application/vnd.ms-excel',buffer:Buffer.from('mock')});
   await page.locator('[data-medal-import] [type=submit]').click();await page.getByText('文件请假 2条',{exact:false}).waitFor();await page.locator('.modal-overlay:not(.is-leaving) [data-modal-confirm]').click();
   await page.waitForFunction(()=>!document.querySelector('[data-medal-month]').disabled);assert.equal(imported,1);
   await page.getByText('全员请假数据导入与确认',{exact:true}).click();await page.locator('[data-medal-confirm]').click();await page.locator('.modal-overlay:not(.is-leaving) [data-modal-confirm]').click();await page.waitForFunction(()=>!document.querySelector('[data-medal-month]').disabled);assert.equal(confirmed,1);
   unknownCode=true;ready=false;await page.evaluate(()=>setRole('SYSTEM_ADMIN'));await page.locator('[data-medal-code]').waitFor({state:'attached'});
   await page.getByText('全员请假数据导入与确认',{exact:true}).click();await page.getByText('核实工资代码分类',{exact:true}).click();
   await page.locator('[data-medal-code] [name=kind]').selectOption('leave');await page.locator('[data-medal-code] [name=reason]').fill('HR文件SL表示请假');
   await page.locator('[data-medal-code] [type=submit]').click();await page.locator('.modal-overlay:not(.is-leaving) [data-modal-confirm]').click();
   await page.waitForFunction(()=>!document.querySelector('[data-medal-month]').disabled);assert.equal(classifications,1);
   role='HR_CIRCLE';await page.evaluate(()=>setRole('HR_CIRCLE'));await page.getByText('存在归属未明确的数据',{exact:false}).waitFor();
   assert(!((await page.locator('#app').textContent()).includes('敏感姓名')));assert(!((await page.locator('#app').textContent()).includes('undefined')));
   await page.locator('[data-medal-month]').fill('2026-11');await page.locator('[data-medal-month]').dispatchEvent('change');await page.getByText('管理端试用月份：',{exact:false}).waitFor();
   assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1));
   await page.evaluate(()=>collection());await page.getByText('我的全勤勋章',{exact:false}).waitFor();
   assert.equal(await page.locator('.medal-item').count(),1);await page.locator('[data-medal-notice]').click();await page.waitForFunction(()=>!document.querySelector('[data-medal-notice]'));
   await page.getByText('查看同事已获勋章',{exact:true}).click();await page.locator('[data-medal-lookup] input').fill('李四');await page.locator('[data-medal-lookup] button').click();await page.locator('[data-medal-peer-id]').click();await page.locator('[data-medal-peer] .medal-item').waitFor();
   assert.equal(await page.getByText('请假详情',{exact:true}).count(),0);
   await page.screenshot({path:path.join(out,`employee-${name}-${width}.png`),fullPage:true});
   await page.evaluate(async()=>{const NativeDate=window.NativeDate;window.Date=class extends NativeDate{constructor(...args){super(...(args.length?args:['2026-11-07T12:00:00+08:00']));}};await refreshCollection();});
   assert.equal(await page.locator('#collection').textContent(),'','employee content is hidden before launch');
   assert.deepEqual(errors,[]);await page.close();
  }
  console.log(name+': embedded management, confirmation, trial isolation, collection privacy and 390/1280 layouts passed');await browser.close();browser=null;
 }
}finally{if(browser)await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));}})().catch(e=>{console.error(e);process.exitCode=1;});
