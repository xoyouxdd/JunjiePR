'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),net=require('node:net'),crypto=require('node:crypto');
const {spawn}=require('node:child_process'),{chromium,webkit,firefox}=require('playwright');
const root=path.resolve(__dirname,'../..'),sleep=ms=>new Promise(r=>setTimeout(r,ms));let server,browser,log='';
(async()=>{try{
  const sock=net.createServer();await new Promise(r=>sock.listen(0,'127.0.0.1',r));const port=sock.address().port;await new Promise(r=>sock.close(r));
  server=spawn(path.join(root,'backend/.venv/Scripts/python.exe'),[path.join(__dirname,'recognition_photo_fixture.py'),String(port)],{cwd:path.join(root,'backend'),windowsHide:true});server.stdout.on('data',d=>log+=d);server.stderr.on('data',d=>log+=d);
  const base='http://127.0.0.1:'+port;let ready=false;for(let n=0;n<100;n++){try{if((await fetch(base+'/health')).ok){ready=true;break;}}catch{}if(server.exitCode!==null)break;await sleep(400);}assert(ready,log);
  const red=Buffer.from(await fetch(base+'/__fixture/photo/red').then(r=>r.arrayBuffer())),blue=Buffer.from(await fetch(base+'/__fixture/photo/blue').then(r=>r.arrayBuffer())),hash=data=>crypto.createHash('sha256').update(data).digest('hex');
  for(const engine of (process.env.PHOTO_ENGINES||'edge,webkit,firefox').split(',')){
    browser=await(engine==='webkit'?webkit:engine==='firefox'?firefox:chromium).launch({headless:true,...(engine==='edge'?{channel:'msedge'}:{})});
    for(const account of ['SUPTEST01','CMTEST01','TRTEST01','TATEST01']){
      const ctx=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'}),page=await ctx.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
      assert((await ctx.request.post(base+'/api/login',{data:{employee_no:account,password:'1234'}})).ok());
      const health=await ctx.request.get(base+'/health').then(r=>r.json());await ctx.request.post(base+'/api/changelog/announcement/read',{data:{version:health.version}});
      await page.goto(base+'/');await page.locator('.tabs-desktop [data-tab=register]').click();
      if(await page.locator('[data-register-mode=self]').count())await page.locator('[data-register-mode=self]').click();
      const form=page.locator('#recognitionForm'),album=form.locator('[data-evidence-album-input]'),camera=form.locator('[data-evidence-camera-input]'),preview=form.locator('[data-evidence-preview]');await album.waitFor({state:'attached'});
      const options=await ctx.request.get(base+'/api/options').then(r=>r.json()),me=await ctx.request.get(base+'/api/me').then(r=>r.json());
      const venue=options.recognition_venues.find(row=>row.name==='热力追踪'),kind=options.recognition_types.find(row=>row.code==='SAFETY');
      const signers=await ctx.request.get(base+'/api/recognizers?attraction_id='+me.attraction_id).then(r=>r.json()),gsm=signers.find(row=>row.employee_no==='GSMTEST01');
      const batch=account!=='SUPTEST01';
      async function fillEntries(){
        const cards=batch?await form.locator('[data-batch-entry]').all():[form];
        for(let i=0;i<cards.length;i++){
          const card=cards[i];await card.locator('[name=content]').fill('照片验证'+engine+i);
          await card.locator('[name=recognition_type_id]').selectOption(String(kind.id));await card.locator('[name=occurred_attraction_id]').selectOption(String(venue.id));
          await card.locator(`[name=recognizer_employee_id] option[value="${gsm.id}"]`).waitFor({state:'attached'});
          await card.locator('[name=recognizer_employee_id]').selectOption(String(gsm.id),{force:true});
        }
      }
      await fillEntries();if(account==='CMTEST01'){await form.locator('[data-batch-add]').click();await fillEntries();}
      // Local previews use separate Blob URLs; the native selected image remains unchanged.
      await album.setInputFiles({name:'原照片.png',mimeType:'image/png',buffer:red});await preview.locator('img').evaluate(img=>img.decode());
      await preview.locator('[data-evidence-view]').click();await page.getByRole('dialog',{name:'已选照片预览'}).locator('img').evaluate(img=>img.decode());await page.getByRole('button',{name:'关闭图片预览'}).click();
      assert.equal(await album.evaluate(input=>input.files[0].name),'原照片.png');
      await preview.locator('[data-evidence-remove]').click();assert.equal(await preview.isVisible(),false);assert.equal(await album.evaluate(input=>input.files.length),0);assert.equal(await camera.evaluate(input=>input.files.length),0);
      // No stale photo can be submitted after deletion.
      let posts=0;const count=request=>{if(request.method()==='POST'&&/\/api\/recognitions(?:\/batch)?$/.test(request.url()))posts++;};page.on('request',count);
      await form.locator('button[type=submit],.form-sticky-actions>button').last().click();await page.locator('#toast').filter({hasText:'请先选择认可图片'}).waitFor();assert.equal(posts,0);
      await album.setInputFiles({name:'原照片.png',mimeType:'image/png',buffer:red});await preview.locator('[data-evidence-remove]').click();
      await album.setInputFiles({name:'原照片.png',mimeType:'image/png',buffer:red});
      await camera.setInputFiles({name:'重新拍照.png',mimeType:'image/png',buffer:blue});assert.equal(await album.evaluate(input=>input.files.length),0);
      await preview.locator('[data-evidence-thumbnail]').click();await page.getByRole('dialog',{name:'已选照片预览'}).waitFor();await page.getByRole('button',{name:'关闭图片预览'}).click();
      // Return to album with a Chinese filename; camera must no longer supply bytes.
      await album.setInputFiles({name:'重新选择（最终）.png',mimeType:'image/png',buffer:blue});assert.equal(await camera.evaluate(input=>input.files.length),0);
      const payloadHash=await page.evaluate(async()=>{const m=await import('/static/js/app/submission.js'),form=document.getElementById('recognitionForm'),file=m.readableEvidenceFile(form);const digest=await crypto.subtle.digest('SHA-256',await file.arrayBuffer());return [...new Uint8Array(digest)].map(n=>n.toString(16).padStart(2,'0')).join('');});assert.equal(payloadHash,hash(blue));
      // A retained native selection still uploads if an embedded picker hides its FileList.
      if(account==='TATEST01')await album.evaluate(input=>Object.defineProperty(input,'files',{get(){throw new Error('synthetic picker bridge');}}));
      for(const width of [320,390,768,1440]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,engine+' '+account+' overflow '+width);}
      const shots=process.env.PHOTO_SCREENSHOTS;if(shots&&['SUPTEST01','CMTEST01'].includes(account)){fs.mkdirSync(shots,{recursive:true});await page.setViewportSize({width:390,height:844});await page.locator('#toast').evaluate(el=>el.classList.remove('show'));await preview.evaluate(el=>el.scrollIntoView({block:'center'}));await page.screenshot({path:path.join(shots,engine+'-'+account+'-photo.png')});}
      // A failed request keeps the selected file and form content for a manual retry.
      await page.evaluate(()=>{const native=window.fetch.bind(window);window._photoNativeFetch=native;window.fetch=(url,options)=>/\/api\/recognitions(?:\/batch)?$/.test(String(url))?Promise.reject(new Error('synthetic offline')):native(url,options);});
      await form.locator('button[type=submit],.form-sticky-actions>button').last().click();await page.locator('#toast').filter({hasText:'提交结果暂未确认'}).waitFor();
      assert(await preview.isVisible());assert.match(await form.locator('[name=content]').first().inputValue(),/照片验证/);
      await page.evaluate(()=>{window.fetch=window._photoNativeFetch;delete window._photoNativeFetch;});
      // Model a confirmed pre-write missing-file rejection: retry must preserve the final bytes.
      await page.evaluate(()=>{const native=window.fetch.bind(window);let first=true;window.fetch=(url,options)=>{if(first&&/\/api\/recognitions(?:\/batch)?$/.test(String(url))){first=false;const data=new FormData();for(const [k,v] of options.body)if(k!=='image')data.append(k,v);return native(url,{...options,body:data});}return native(url,options);};});
      const response=page.waitForResponse(r=>/\/api\/recognitions(?:\/batch)?$/.test(r.url())&&r.request().method()==='POST'&&r.status()===200);
      await form.locator('button[type=submit],.form-sticky-actions>button').last().click();
      // Later engines meet same-day duplicates: confirm the existing business dialog.
      const confirmed=async()=>{for(let n=0;n<50;n++){const button=page.getByRole('button',{name:'确认是独立表现，继续登记'});if(await button.isVisible())await button.click();if(await preview.isHidden())return;await sleep(100);}};
      await confirmed();const out=await (await response).json(),records=out.records||[out.record];assert.equal(records.length,account==='CMTEST01'?2:1);
      for(const row of records){assert(row.has_image);const receipts=await ctx.request.get(base+'/__fixture/receipt/'+row.id).then(r=>r.json());assert.equal(receipts.length,1);assert.equal(receipts[0].sha256,hash(blue));assert.notEqual(receipts[0].sha256,hash(red));assert.equal(receipts[0].name,'重新选择（最终）.png');}
      assert(await preview.isHidden());assert.deepEqual(errors,[]);console.log(engine+' '+account+': preview/delete/reselect, missing-file retry, exact backend bytes passed');await ctx.close();
    }
    await browser.close();browser=null;
  }
}finally{if(browser)await browser.close();if(server){server.kill();await Promise.race([new Promise(r=>server.once('exit',r)),sleep(3000)]);}}})().catch(error=>{console.error(error);process.exitCode=1;});
