const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../app/static'),output=path.resolve(__dirname,'../../output/avatar-generation-20261028');
fs.mkdirSync(output,{recursive:true});
const catalog=JSON.parse(fs.readFileSync(path.join(root,'images/avatars/catalog.json'),'utf8'));
let preference={avatar_key:'',avatar_frame:'plain',frames:[{key:'plain',label:'简洁'},{key:'ocean',label:'晴空蓝'},{key:'medal:2026-09:50',label:'2026-09 · 永恒钻石'}]},fail=false,writes=0;
let wardrobe={favorites:[],recent:[],presets:[null,null,null]};
const harness=`import {state} from './static/js/app/state.js';import {mountAvatarWardrobe,avatarMarkup,bindDirectoryAvatars} from './static/js/app/avatars.js';import {bindCelebration} from './static/js/app/celebration.js';state.me={id:1,name:'测试员工',avatar_key:'',avatar_frame:'plain'};window.disposeWardrobe=mountAvatarWardrobe(document.getElementById('wardrobe'));document.getElementById('directory').innerHTML='<details><summary>展开组员</summary>'+avatarMarkup('zoo-gary','测试员工',{deferred:true})+'</details>';window.disposeDirectory=bindDirectoryAvatars(document.getElementById('directory'));window.disposeCelebration=bindCelebration(document.getElementById('effects'));window.fixtureReady=true;`;
const server=http.createServer((req,res)=>{
  const route=new URL(req.url,'http://fixture').pathname.replace(/^\/recognition(?=\/|$)/,'')||'/';
  if(route==='/api/self-avatar-wardrobe'){
    if(req.method==='PUT'){let data='';req.on('data',c=>data+=c);req.on('end',()=>{wardrobe=JSON.parse(data);res.setHeader('Content-Type','application/json');res.end(JSON.stringify(wardrobe));});return;}
    res.setHeader('Content-Type','application/json');res.end(JSON.stringify(wardrobe));return;
  }
  if(route==='/api/self-avatar'){
    if(req.method==='PUT'){let data='';req.on('data',c=>data+=c);req.on('end',()=>{writes++;if(fail){res.writeHead(503,{'Content-Type':'application/json'});res.end(JSON.stringify({detail:'模拟保存失败'}));return;}const change=JSON.parse(data);preference={...preference,avatar_key:change.avatar_key,avatar_frame:change.frame};res.setHeader('Content-Type','application/json');res.end(JSON.stringify(preference));});return;}
    res.setHeader('Content-Type','application/json');res.end(JSON.stringify(preference));return;
  }
  if(route==='/harness.js'){res.setHeader('Content-Type','text/javascript');res.end(harness);return;}
  if(route==='/'){
    res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'");
    res.setHeader('Content-Type','text/html;charset=utf-8');res.end('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="static/css/style.css"><link rel="stylesheet" href="static/css/avatars.css"><div id="app"><section class="panel"><h2>我的信息</h2><div id="wardrobe"></div></section><section id="directory" class="panel"></section><section id="effects" class="panel honor-content"><button type="button" data-celebrate>庆祝一下</button></section></div><div id="tabs"></div><script type="module" src="harness.js"></script>');return;
  }
  if(route.startsWith('/static/')){const file=path.resolve(root,route.slice(8));if(!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.writeHead(404);res.end();return;}res.setHeader('Content-Type',({'.js':'text/javascript','.css':'text/css','.json':'application/json','.webp':'image/webp'})[path.extname(file)]||'application/octet-stream');res.end(fs.readFileSync(file));return;}
  res.writeHead(404);res.end();
});
(async()=>{await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const browser=await chromium.launch({channel:'msedge',headless:true});try{
 const page=await browser.newPage();const errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(r.url()));
 for(const prefix of ['/','/recognition/']){
  preference.avatar_key='';preference.avatar_frame='plain';wardrobe={favorites:[],recent:[],presets:[null,null,null]};await page.goto('http://127.0.0.1:'+server.address().port+prefix);await page.waitForFunction(()=>window.fixtureReady);
  assert(!requests.some(u=>u.endsWith('/catalog.json')),'Catalog must wait for explicit opening');assert(!await page.locator('#directory img').getAttribute('src'),'Closed branches must not load avatars');
  await page.locator('#directory summary').click();await page.waitForFunction(()=>document.querySelector('#directory img').naturalWidth===64);
  await page.locator('[data-avatar-open]').click();await page.locator('[data-avatar-group-select]').selectOption({label:'疯狂动物城 · 新伙伴与装扮'});assert.equal(await page.locator('[data-avatar-choice]').count(),8);await page.locator('[data-avatar-next]').click();assert.equal(await page.locator('[data-avatar-choice]').count(),2);
  await page.locator('[data-avatar-choice="zoo-flash-driving"]').click();await page.locator('[data-frames] summary').click();await page.locator('[data-avatar-frame="medal:2026-09:50"]').click();
  fail=true;await page.locator('[data-avatar-save]').click();await page.locator('[data-avatar-feedback]').filter({hasText:'模拟保存失败'}).waitFor();assert(await page.locator('.avatar-wardrobe').isVisible());fail=false;
  await page.locator('[data-avatar-save]').click();await page.waitForFunction(()=>document.querySelector('.avatar-wardrobe').hidden);assert.equal(preference.avatar_key,'zoo-flash-driving');assert.equal(preference.avatar_frame,'medal:2026-09:50');
  await page.locator('[data-avatar-open]').click();assert.equal(await page.locator('[data-avatar-frame="medal:2026-09:50"]').getAttribute('aria-pressed'),'true');await page.locator('[data-avatar-cancel]').click();
  const effects=[];for(let i=0;i<4;i++){await page.locator('[data-celebrate]').click();effects.push(await page.locator('.honor-celebration').getAttribute('data-effect'));}assert.equal(new Set(effects).size,4);
  await page.evaluate(()=>window.disposeCelebration());assert.equal(await page.locator('.honor-celebration').count(),0);
  await page.locator('[data-avatar-open]').click();await page.locator('[data-avatar-group-select]').selectOption({label:'疯狂动物城 · 手绘新造型'});await page.locator('[data-favorite="new-zoo-flash"]').click();await page.locator('[data-filter="favorites"]').click();assert.equal(await page.locator('[data-avatar-choice]').count(),1);await page.locator('[data-presets] summary').click();await page.locator('[data-preset-store]').click();await page.locator('[data-filter="all"]').click();
  for(const width of [1280,736,390,320]){await page.setViewportSize({width,height:1100});assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),'Overflow at '+width);}
  await page.setViewportSize({width:1024,height:1100});await page.screenshot({path:path.join(output,prefix==='/'?'local-avatar-ui.png':'local-avatar-ui-portal.png'),fullPage:true});
  await page.emulateMedia({reducedMotion:'reduce'});await page.reload();await page.waitForFunction(()=>window.fixtureReady);await page.locator('[data-celebrate]').click();assert.equal(await page.locator('.honor-celebration').count(),0);await page.emulateMedia({reducedMotion:'no-preference'});requests.length=0;
 }
 assert.deepEqual(errors,[]);console.log(JSON.stringify({result:'PASS',avatars:catalog.avatars.length,prefixes:['/','/recognition/'],writes,checks:['deferred catalog','deferred directory images','pagination','save failure retains selection','account preference restore','four effects','cleanup','reduced motion','320–1280 layout','strict CSP']}));
}finally{await browser.close();server.close();}})().catch(e=>{console.error(e);server.close();process.exitCode=1;});
