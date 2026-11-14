// Real production modules/CSP with synthetic APIs; never opens a business database.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {createHash}=require('node:crypto'),{chromium,webkit}=require('./support/browser-runtime.cjs');
const root=path.resolve(__dirname,'../app/static'),version='compact-ui-fixture';
const shell=fs.readFileSync(path.join(root,'index.html'),'utf8').replaceAll('__STATIC_CACHE_VERSION__',version).replace(/<script type="module"[^>]*><\/script>/,'');
const policySource=fs.readFileSync(path.join(root,'../security.py'),'utf8');
const basePolicy=[.../CONTENT_SECURITY_POLICY\s*=\s*"; "\.join\(\s*\(([\s\S]*?)\)\s*\)/.exec(policySource)[1].matchAll(/"([^"\r\n]+)"/g)].map(m=>m[1]).join('; ');
const map=/<script type="importmap">([\s\S]*?)<\/script>/.exec(shell)[1];
const policy=basePolicy.replace("script-src 'self'",`script-src 'self' 'sha256-${createHash('sha256').update(map.replace(/\r\n?/g,'\n')).digest('base64')}'`);
const server=http.createServer((req,res)=>{
 const route=new URL(req.url,'http://fixture').pathname;
 if(route==='/'){res.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Content-Security-Policy':policy});return res.end(shell);}
 if(route.startsWith('/static/')){
  const file=path.resolve(root,'.'+route.slice(7));
  if(!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.writeHead(404);return res.end();}
  res.setHeader('Content-Type',({'.js':'text/javascript','.css':'text/css','.png':'image/png','.json':'application/json'})[path.extname(file)]||'application/octet-stream');return res.end(fs.readFileSync(file));
 }
 res.writeHead(404);res.end();
});
async function main(){await new Promise(r=>server.listen(0,'127.0.0.1',r));try{for(const [engine,type,options] of [['edge',chromium,{channel:'msedge'}],['webkit',webkit,{}]]){const browser=await type.launch({...options,headless:true});try{const page=await browser.newPage({viewport:{width:1920,height:1080}});await page.goto(`http://127.0.0.1:${server.address().port}/`);await page.evaluate(()=>{document.body.classList.add('has-nav');document.documentElement.dataset.skin='circle';const app=document.getElementById('app');app.dataset.workspaceUi='true';app.innerHTML='<div class="section-gap"><section class="panel"><h2>组织提醒</h2><p>代理任期提醒</p></section><section class="panel"><h2>小组管理</h2><p>小组设置</p></section></div>';document.getElementById('pageHeading').hidden=false;document.getElementById('pageHeading').innerHTML='<span>人事 /</span><h1>小组管理</h1>';document.getElementById('themeStory').hidden=false;document.querySelector('.skin-story-wish-host').textContent='FZ · 今日小祝福';});
for(const width of [1920,2560,1440,1024,768,390,320]){
 await page.setViewportSize({width,height:1080});
 for(const hasNav of [true,false]){
 await page.evaluate(value=>document.body.classList.toggle('has-nav',value),hasNav);
 for(const skin of ['blue','green','ivory','medal','circle']){
  await page.evaluate(skin=>document.documentElement.dataset.skin=skin,skin);
  const pos=await page.evaluate(()=>['pageHeading','themeStory','app'].map(id=>{const el=document.getElementById(id),r=el.getBoundingClientRect();return {id,x:r.x,width:r.width,visible:getComputedStyle(el).display!=='none'};}));
  const shown=pos.filter(p=>p.visible);
  assert.ok(shown.every(p=>Math.abs(p.x-shown[0].x)<1&&Math.abs(p.width-shown[0].width)<1),JSON.stringify({width,skin,pos}));
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  if(width===1920&&skin==='circle'&&hasNav){await page.screenshot({path:path.resolve(__dirname,'../../output/release-ui/workspace-alignment-fixed.png'),fullPage:true});}
 }
}
}
console.log(engine+': five skins, 320/390/768/1024/1440/1920/2560 with/without sidebar alignment and page bounds passed');
await browser.close();}finally{if(browser.isConnected())await browser.close();}}}finally{server.closeAllConnections();await new Promise(r=>server.close(r));}}
main().catch(e=>{console.error(e);process.exitCode=1;});
