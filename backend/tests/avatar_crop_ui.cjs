const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium,webkit}=require('./support/browser-runtime.cjs');
const root=path.resolve(__dirname,'../app/static'),catalog=JSON.parse(fs.readFileSync(path.join(root,'images/avatars/catalog.json'),'utf8'));
const cropped=catalog.avatars.filter(a=>a.display_images),shots=path.resolve(__dirname,'../../output/release-ui');
const shell=`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/style.css"><script src="/static/js/appearance-boot.js"></script><main id="app"></main><div id="tabs"></div><script type="module">
import {avatarAssetFile} from '/static/js/app/format.js';import {avatarMarkup} from '/static/js/app/avatars.js';window.asset=avatarAssetFile;window.markup=avatarMarkup;window.ready=true;</script>`;
const server=http.createServer((req,res)=>{
 const url=new URL(req.url,'http://x'),route=url.pathname.replace(/^\/recognition(?=\/|$)/,'')||'/';
 if(route==='/'){res.setHeader('Content-Type','text/html; charset=utf-8');return res.end(shell.replaceAll('"/static/','"'+(url.pathname.startsWith('/recognition')?'/recognition':'')+'/static/').replaceAll("from '/static/","from '"+(url.pathname.startsWith('/recognition')?'/recognition':'')+'/static/'));}
 if(route.startsWith('/static/')){const file=path.resolve(root,'.'+route.slice(7));if(!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.statusCode=404;return res.end();}res.setHeader('Content-Type',({'.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.webp':'image/webp'})[path.extname(file)]||'application/octet-stream');return res.end(fs.readFileSync(file));}
 res.statusCode=404;res.end();
});
(async()=>{let browser;try{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const base='http://127.0.0.1:'+server.address().port;
 for(const [engine,type,options] of [['edge',chromium,{channel:'msedge'}],['webkit',webkit,{}]]){
  browser=await type.launch({...options,headless:true});
  for(const prefix of ['/','/recognition/']){
   const page=await browser.newPage({viewport:{width:390,height:844},deviceScaleFactor:3}),errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.goto(base+prefix);await page.waitForFunction(()=>window.ready);
   const results=await page.evaluate(async entries=>{
    const root=document.getElementById('app');root.style.cssText='display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding:12px;';
    const load=src=>new Promise((resolve,reject)=>{const img=new Image();img.onload=()=>resolve(img);img.onerror=()=>reject(new Error('image failed '+src));img.src=src;});
    const result=[];
    for(const entry of entries){
     for(const size of [64,160]){
      const url=new URL((location.pathname.startsWith('/recognition')?'/recognition':'')+'/static/images/avatars/'+asset(entry.key,size),location.origin).href;
      const expectedFile=(entry.display_images||entry.images)[String(size)].file;
      if(asset(entry.key,size)!==expectedFile)throw new Error('catalog path mismatch '+entry.key);
      const img=await load(url),original=await load(url.replace('-crop.svg','.webp'));
      if(img.naturalWidth!==size||img.naturalHeight!==size)throw new Error('dimensions '+entry.key);
      const a=document.createElement('canvas'),b=document.createElement('canvas');a.width=a.height=b.width=b.height=size;
      a.getContext('2d').drawImage(img,0,0,size,size);
      const side=size*(160-(entry.display_crop?.bottom||0))/160,left=(size-side)/2;
      b.getContext('2d').drawImage(original,left,0,side,side,0,0,size,size);
      const actual=a.getContext('2d').getImageData(0,0,size,size).data,expected=b.getContext('2d').getImageData(0,0,size,size).data;
      let delta=0;for(let i=0;i<actual.length;i++)delta+=Math.abs(actual[i]-expected[i]);
      result.push({key:entry.key,size,delta:delta/actual.length});
      if(size===160&&entries.indexOf(entry)>=8){const card=document.createElement('div');card.style.cssText='min-width:0;border:1px solid #ddd;border-radius:12px;overflow:hidden;background:white';img.style.cssText='display:block;width:100%;height:auto';card.append(img);const caption=document.createElement('p');caption.textContent=entry.name;card.append(caption);root.append(card);}
     }
    }
    return result;
   },catalog.avatars);
   for(const r of results)assert(r.delta<5,engine+' rendered crop differs '+JSON.stringify(r));
   if(prefix==='/')await page.screenshot({path:path.join(shots,'avatar-crop-fixed-'+engine+'.png'),fullPage:true});
   for(const entry of cropped){
    await page.evaluate(key=>{FZAppearance.setArtwork('avatar:'+key);},entry.key);
    assert.ok((await page.evaluate(()=>document.documentElement.style.getPropertyValue('--header-portrait'))).includes(entry.key+'-160-crop.svg'));
    await page.reload();await page.waitForFunction(()=>window.ready);
    assert.ok((await page.evaluate(()=>document.documentElement.style.getPropertyValue('--header-portrait'))).includes(entry.key+'-160-crop.svg'));
   }
   assert.equal(await page.evaluate(()=>asset('new-zoo-flash',64)),'new-zoo-flash-64.webp');
   assert.deepEqual(errors,[]);await page.close();
  }
  console.log(engine+': all '+catalog.avatars.length+' avatars x 2 sizes, '+cropped.length+' crops, pixel comparison, root/portal, high-DPI and persisted header artwork passed');
  await browser.close();browser=null;
 }
}finally{if(browser)await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));}})().catch(e=>{console.error(e);process.exitCode=1;});
