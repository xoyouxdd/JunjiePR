/* Only authenticated video ranges are cached. Never an offline application shell. */
const LIMIT=300*1024*1024, BLOCK=1024*1024, AGE=7*86400000, DB='fz-video-blocks-v1';
let epoch=0, serial=Promise.resolve();
const offlineClients=new Set();
const locked=fn=>{const p=serial.then(fn,fn);serial=p.catch(()=>{});return p;};
function database(){return new Promise((resolve,reject)=>{
  let settled=false;const r=indexedDB.open(DB,1);
  const fail=error=>{if(settled)return;settled=true;clearTimeout(timer);reject(error);};
  const timer=setTimeout(()=>fail(new Error('cache open timeout')),2000);
  r.onupgradeneeded=()=>{const db=r.result;db.createObjectStore('blocks',{keyPath:'key'}).createIndex('used','used');db.createObjectStore('meta');};
  r.onsuccess=()=>{if(settled){r.result.close();return;}settled=true;clearTimeout(timer);resolve(r.result);};
  r.onerror=()=>fail(r.error);r.onblocked=()=>fail(new Error('cache unavailable'));
});}
function request(r){return new Promise((resolve,reject)=>{r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});}
async function transact(stores,action){const db=await database();try{return await new Promise((resolve,reject)=>{const tx=db.transaction(stores,'readwrite');let result;tx.oncomplete=()=>resolve(result);tx.onabort=tx.onerror=()=>reject(tx.error||new Error('cache failed'));try{action(tx,v=>result=v);}catch(e){tx.abort();reject(e);}});}finally{db.close();}}
async function clear(){epoch++;return locked(()=>transact(['blocks','meta'],tx=>{tx.objectStore('blocks').clear();tx.objectStore('meta').clear();}));}
async function maintain(identity){return locked(()=>transact(['blocks','meta'],tx=>{
  const blocks=tx.objectStore('blocks'),meta=tx.objectStore('meta');
  const r=meta.get('identity');r.onsuccess=()=>{
    if(identity&&r.result!==identity){epoch++;blocks.clear();meta.put(0,'bytes');meta.put(identity,'identity');return;}
    const b=meta.get('bytes');b.onsuccess=()=>{let bytes=b.result||0;const c=blocks.index('used').openCursor(IDBKeyRange.upperBound(Date.now()-AGE));c.onsuccess=()=>{const cur=c.result;if(!cur){meta.put(Math.max(0,bytes),'bytes');return;}bytes-=cur.value.bytes;cur.delete();cur.continue();};};
  };
}));}
async function read(key){return locked(()=>transact(['blocks'],(tx,done)=>{const store=tx.objectStore('blocks'),r=store.get(key);r.onsuccess=()=>{const v=r.result;if(v){v.used=Date.now();store.put(v);done(v.data);}else done(null);};}));}
async function write(key,data,seen){if(seen!==epoch)return;return locked(()=>transact(['blocks','meta'],tx=>{
  if(seen!==epoch)return;
  const blocks=tx.objectStore('blocks'),meta=tx.objectStore('meta');
  const r=meta.get('bytes');r.onsuccess=()=>{let bytes=r.result||0;const old=blocks.get(key);old.onsuccess=()=>{
    bytes-=old.result?.bytes||0;blocks.put({key,data,bytes:data.byteLength,used:Date.now()});bytes+=data.byteLength;
    const cursor=blocks.index('used').openCursor();cursor.onsuccess=()=>{const cur=cursor.result;if(bytes<=LIMIT||!cur){meta.put(Math.max(0,bytes),'bytes');return;}bytes-=cur.value.bytes;cur.delete();cur.continue();};
  };};
}));}
async function auth(url){if(self.navigator.onLine===false)throw Object.assign(new Error('offline'),{status:503});const u=new URL(url);u.pathname=u.pathname.replace(/\/stream$/,'/authorize');const r=await fetch(u,{cache:'no-store',credentials:'same-origin'});if(!r.ok)throw Object.assign(new Error('authorization failed'),{status:r.status});return r.json();}
function keyFor(a,start){return [a.identity,a.video_id,a.version,a.quality,a.generation,start].join(':');}
async function media(requested){
  let a;try{a=await auth(requested.url);}catch(e){await clear().catch(()=>{});return new Response('需要联网重新确认观看权限',{status:e.status||503,headers:{'Cache-Control':'no-store'}});}
  try{await maintain(a.identity);}catch(_){return fetch(requested);}
  const seen=epoch,header=requested.headers.get('Range');
  const m=header?.match(/^bytes=(\d*)-(\d*)$/);
  if(header&&!m)return fetch(requested);
  let start=0,end=a.bytes-1;
  if(m){if(m[1]){start=Number(m[1]);if(m[2])end=Math.min(end,Number(m[2]));}else{if(!Number(m[2]))return fetch(requested);start=Math.max(0,a.bytes-Number(m[2]));}}
  if(!Number.isSafeInteger(start)||start>end)return new Response(null,{status:416,headers:{'Content-Range':`bytes */${a.bytes}`}});
  let offset=start,stopped=false;const controller=new AbortController();
  const body=new ReadableStream({async pull(stream){try{
    if(stopped||seen!==epoch)throw new Error('session changed');
    if(offset>end){stream.close();return;}
    // Cache hits also need live authorization. Do not trust an earlier response.
    const fresh=await auth(requested.url);
    if(fresh.identity!==a.identity||fresh.version!==a.version||fresh.generation!==a.generation)throw new Error('video changed');
    const blockStart=Math.floor(offset/BLOCK)*BLOCK,blockEnd=Math.min(a.bytes-1,blockStart+BLOCK-1),key=keyFor(a,blockStart);
    let data;try{data=await read(key);}catch(_){}
    if(!data){const r=await fetch(requested.url,{headers:{Range:`bytes=${blockStart}-${blockEnd}`},cache:'no-store',credentials:'same-origin',signal:controller.signal});
      if(r.status!==206||r.headers.get('Content-Range')!==`bytes ${blockStart}-${blockEnd}/${a.bytes}`)throw new Error('invalid video range');
      data=await r.arrayBuffer();if(data.byteLength!==blockEnd-blockStart+1)throw new Error('incomplete video range');
      await write(key,data,seen).catch(()=>{});
    }
    if(stopped||seen!==epoch)throw new Error('playback cancelled');
    const count=Math.min(end-offset+1,blockEnd-offset+1);stream.enqueue(new Uint8Array(data,offset-blockStart,count));offset+=count;
  }catch(e){stopped=true;controller.abort();await clear().catch(()=>{});stream.error(e);}},cancel(){stopped=true;controller.abort();}}, {highWaterMark:0});
  const headers={'Content-Type':'video/mp4','Accept-Ranges':'bytes','Content-Length':String(end-start+1),'Cache-Control':'private, no-store'};
  if(m)headers['Content-Range']=`bytes ${start}-${end}/${a.bytes}`;
  return new Response(body,{status:m?206:200,headers});
}
self.addEventListener('install',e=>e.waitUntil(self.skipWaiting()));
self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));
self.addEventListener('message',event=>{const type=event.data?.type;if(type==='fz-video-offline'||type==='fz-video-online'){if(event.source?.id){if(type==='fz-video-offline')offlineClients.add(event.source.id);else offlineClients.delete(event.source.id);}event.ports[0]?.postMessage({ok:true});return;}if(type==='fz-video-claim'){event.waitUntil(self.clients.claim());return;}if(!['fz-video-clear','fz-video-stats'].includes(type))return;
  event.waitUntil((async()=>{try{if(type==='fz-video-clear')await clear();else await maintain();const db=await database();try{const bytes=await request(db.transaction('meta').objectStore('meta').get('bytes'));event.ports[0]?.postMessage({bytes:bytes||0,limit:LIMIT});}finally{db.close();}}catch(_){event.ports[0]?.postMessage({unsupported:true});}})());
});
self.addEventListener('fetch',event=>{const u=new URL(event.request.url);if(u.origin!==self.location.origin)return;
  if(/\/api\/(?:login|logout)$/.test(u.pathname)&&event.request.method==='POST'){
    event.respondWith((async()=>{const r=await fetch(event.request);if(r.ok)await clear().catch(()=>{});return r;})());return;
  }
  if(event.request.method==='GET'&&/\/api\/video-learning\/videos\/\d+\/stream$/.test(u.pathname))event.respondWith(offlineClients.has(event.clientId)?Promise.resolve(new Response('需要联网观看',{status:503,headers:{'Cache-Control':'no-store'}})):media(event.request));
});
