// Real upload helpers + browser multipart encoder + an HTTP receiving endpoint.
// Fault injection models API failures; it does not impersonate a physical phone.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium,webkit,firefox}=require('playwright');
const root=path.resolve(__dirname,'../app/static');
const image=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jQ8kAAAAASUVORK5CYII=','base64');
const received=[],checks=[],missing=new Map();let missingResponses=0;
const server=http.createServer(async(req,res)=>{
  try{
    const url=new URL(req.url,'http://fixture');
    if(url.pathname==='/'){
      res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});
      res.end('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body><div id="app"><form id="upload"><input name="content" value="合成认可"><input type="date" name="recognition_date" value="2026-10-12"><input name="image_album" type="file" data-evidence-input></form></div><nav id="tabs"></nav><div id="toast"></div></body></html>');return;
    }
    if(url.pathname.startsWith('/static/')){
      const file=path.resolve(root,'.'+url.pathname.slice('/static'.length));
      if(!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.writeHead(404);res.end();return;}
      res.writeHead(200,{'Content-Type':'text/javascript'});res.end(fs.readFileSync(file));return;
    }
    if(req.method==='POST'&&['/api/recognitions','/api/recognitions/batch'].includes(url.pathname)){
      assert.match(req.headers['content-type'],/^multipart\/form-data;\s*boundary=/);
      const chunks=[];for await(const chunk of req)chunks.push(chunk);
      const form=await new Request('http://fixture/upload',{method:'POST',headers:req.headers,body:Buffer.concat(chunks)}).formData();
      const file=form.get('image'),key=form.get('idempotency_key');
      const snapshot=Object.fromEntries([...form].filter(([k])=>k!=='image'&&k!=='image_client_ready'));
      if(!file){
        const previous=missing.get(key);if(previous)assert.deepEqual(snapshot,previous.snapshot);
        missing.set(key,{snapshot,attempts:(previous?.attempts||0)+1});missingResponses++;
        const batch=url.pathname.endsWith('/batch');
        res.writeHead(batch?422:400,{'Content-Type':'application/json'});res.end(JSON.stringify({detail:batch?{code:'RECOGNITION_IMAGE_BROWSER_INCOMPATIBLE',fields:[{field:'image',message:'synthetic missing image'}]}:'CM/TR本人登记必须上传1张认可图片'}));return;
      }
      assert(typeof file!=='string');if(missing.has(key))assert.deepEqual(snapshot,missing.get(key).snapshot,'Retry must preserve all original fields and the idempotency key');
      assert.equal(form.getAll('image').length,1);assert.equal(form.get('image_client_ready'),'1');
      assert.deepEqual(Buffer.from(await file.arrayBuffer()),image);
      if(url.pathname.endsWith('/batch'))assert.equal(JSON.parse(form.get('entries')).length,2);
      else assert.equal(form.get('content'),'合成认可');
      const receipt={path:url.pathname,name:file.name,size:file.size,attempts:(missing.get(key)?.attempts||0)+1};received.push(receipt);
      res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(receipt));return;
    }
    res.writeHead(404);res.end();
  }catch(error){res.writeHead(500,{'Content-Type':'application/json'});res.end(JSON.stringify({error:error.stack}));}
});
async function main(){
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const engines=(process.env.UPLOAD_TEST_ENGINES||'edge').split(',');
  try{
    for(const name of engines){
      const type={edge:chromium,webkit,firefox}[name];assert(type,`Unknown engine ${name}`);
      const browser=await type.launch({headless:true,...(name==='edge'?{channel:'msedge'}:{})});
      try{
        const page=await browser.newPage({viewport:{width:390,height:844}}),errors=[];
        page.on('pageerror',error=>errors.push(error.message));
        const scenarios=['normal','item_only','native_form','retained_selection','file_stringified','file_append_throws','file_set_drops','chinese_filename','server_missing_image'];
        for(const scenario of scenarios){
          for(const target of ['/api/recognitions','/api/recognitions/batch']){
            await page.goto(`http://127.0.0.1:${server.address().port}/`);
            const filename=scenario==='chinese_filename'?'活动认可（合成）.png':'fixture.png';
            await page.locator('[type=file]').setInputFiles({name:filename,mimeType:'image/png',buffer:image});
            const result=await page.evaluate(async({scenario,target,key})=>{
              const m=await import('/static/js/app/submission.js');
              const input=document.querySelector('[type=file]'),original=input.files[0],form=input.form;
              m.rememberEvidenceFile(input);
              if(scenario==='item_only')Object.defineProperty(input,'files',{get:()=>({length:1,item:()=>original})});
              if(['native_form','retained_selection'].includes(scenario))Object.defineProperty(input,'files',{get:()=>{throw new Error('synthetic picker bridge');}});
              if(scenario==='native_form')m.rememberEvidenceFile(input); // Clear saved file: require native form fallback.
              if(scenario==='retained_selection'){
                const Native=FormData;
                window.FormData=class extends Native{constructor(owner){super();}}; // Picker and native form both unavailable.
              }
              const file=m.readableEvidenceFile(form);if(!file)throw new Error('Selected image was lost');
              const data=new FormData(),nativeAppend=data.append.bind(data),nativeSet=data.set.bind(data);
              if(scenario==='file_stringified')data.append=(key,value,filename)=>{
                if(key==='image'&&Object.prototype.toString.call(value)==='[object File]')nativeAppend(key,'[object File]');
                else if(filename!==undefined)nativeAppend(key,value,filename);else nativeAppend(key,value);
              };
              if(scenario==='file_append_throws')data.append=(key,value,filename)=>{
                if(key==='image'&&Object.prototype.toString.call(value)==='[object File]')throw new TypeError('synthetic File wrapper');
                if(filename!==undefined)nativeAppend(key,value,filename);else nativeAppend(key,value);
              };
              if(scenario==='file_set_drops')data.set=(key,value,...rest)=>{if(key!=='image')nativeSet(key,value,...rest);};
              if(target.endsWith('/batch'))data.set('entries',JSON.stringify([{content:'合成认可1'},{content:'合成认可2'}]));
              else data.set('content','合成认可');
              data.set('idempotency_key',key);
              m.appendRecognitionImage(data,file);
              if(scenario==='server_missing_image'){
                const nativeFetch=window.fetch.bind(window);let first=true;
                window.fetch=(url,options)=>{
                  if(first){first=false;const dropped=new FormData();for(const [k,v] of options.body)if(k!=='image')dropped.append(k,v);form.elements.content.value='修改不影响已提交快照';return nativeFetch(url,{...options,body:dropped});}
                  return nativeFetch(url,options);
                };
              }
              return await m.sendRecognitionImage(target,data);
            },{scenario,target,key:`${name}-${scenario}-${target}`});
            assert.equal(result.name,filename);assert.equal(result.size,image.length);
            assert.equal(result.attempts,scenario==='server_missing_image'?2:1);
            checks.push({engine:name,scenario,target});
          }
        }
        await page.goto(`http://127.0.0.1:${server.address().port}/`);
        const rejected=await page.evaluate(async()=>{
          const {appendRecognitionImage}=await import('/static/js/app/submission.js');
          const file=new File([new Uint8Array([1,2,3])],'fixture.png',{type:'image/png'}),data=new FormData();
          const append=data.append.bind(data);data.append=(key,value)=>append(key,key==='image'?'unreadable':value);
          data.set('image_client_ready','1');let failure='';try{appendRecognitionImage(data,file);}catch(error){failure=error.message;}
          return {failure,image:data.get('image'),ready:data.get('image_client_ready')};
        });
        assert.match(rejected.failure,/图片未成功加入提交/);assert.equal(rejected.image,null);assert.equal(rejected.ready,null);
        checks.push({engine:name,scenario:'unrecoverable image rejected before sending'});
        await page.goto(`http://127.0.0.1:${server.address().port}/`);
        const retryLimits=await page.evaluate(async()=>{
          const m=await import('/static/js/app/submission.js'),nativeFetch=window.fetch;
          const cases=[
            {name:'network',error:true},
            {name:'permission',status:403,detail:'no permission'},
            {name:'format',status:400,detail:'bad image format'},
            {name:'business conflict',status:409,detail:{code:'SAME_DAY_RECOGNITION_DUPLICATE'}},
            {name:'other validation',status:422,detail:[{loc:['body','content'],type:'missing'}]},
            {name:'missing twice',status:422,retry:true,detail:{code:'RECOGNITION_IMAGE_BROWSER_INCOMPATIBLE'}},
            {name:'legacy missing single',status:400,retry:true,detail:'CM/TR本人登记必须上传1张认可图片'},
            {name:'legacy text on other route',target:'/api/recognitions/batch',status:400,detail:'CM/TR本人登记必须上传1张认可图片'},
          ],results=[];
          for(const test of cases){let calls=0;window.fetch=async()=>{calls++;if(test.error)throw new Error('synthetic offline');return new Response(JSON.stringify({detail:test.detail}),{status:test.status,headers:{'Content-Type':'application/json'}});};
            const data=new FormData();data.set('idempotency_key','unchanged');m.appendRecognitionImage(data,new File([new Uint8Array([1,2,3])],'fixture.png'));
            let failed=false;try{await m.sendRecognitionImage(test.target||'/api/recognitions',data);}catch(_){failed=true;}results.push({name:test.name,calls,failed,retry:!!test.retry});
          }
          window.fetch=nativeFetch;return results;
        });
        for(const test of retryLimits){assert(test.failed);assert.equal(test.calls,test.retry?2:1);checks.push({engine:name,scenario:test.name});}
        assert.deepEqual(errors,[]);
        await page.close();
      }finally{await browser.close();}
    }
    console.log(JSON.stringify({status:'passed',engines,checks,received:received.length,missingResponses},null,2));
  }finally{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
