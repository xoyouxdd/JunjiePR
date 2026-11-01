// Local, read-only UI preview. Exposes only generated previews and public static assets.
const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..'),preview=path.join(root,'output/appearance-preview'),assets=path.join(root,'backend/app/static');
const types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.json':'application/json; charset=utf-8','.png':'image/png','.webp':'image/webp','.jpg':'image/jpeg','.svg':'image/svg+xml','.ico':'image/x-icon','.woff2':'font/woff2'};
const server=http.createServer((req,res)=>{
 if(!['GET','HEAD'].includes(req.method)){res.writeHead(405);res.end();return;}
 let pathname;try{pathname=decodeURIComponent(new URL(req.url,'http://localhost').pathname);}catch{res.writeHead(400);res.end();return;}
 if(pathname==='/'){res.writeHead(302,{Location:'/output/appearance-preview/'});res.end();return;}
 let base,relative;
 if(pathname.startsWith('/output/appearance-preview/')){base=preview;relative=pathname.slice('/output/appearance-preview/'.length)||'index.html';}
 else if(pathname.startsWith('/backend/app/static/')){base=assets;relative=pathname.slice('/backend/app/static/'.length);}
 else if(pathname.startsWith('/static/')){base=assets;relative=pathname.slice('/static/'.length);}
 else{res.writeHead(404);res.end();return;}
 const file=path.resolve(base,relative),type=types[path.extname(file).toLowerCase()];
 if(!file.startsWith(base+path.sep)||!type){res.writeHead(404);res.end();return;}
 fs.readFile(file,(error,body)=>{if(error){res.writeHead(404);res.end();return;}res.writeHead(200,{'Content-Type':type,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(req.method==='HEAD'?undefined:body);});
});
server.listen(8766,'127.0.0.1',()=>console.log('UI preview: http://127.0.0.1:8766/output/appearance-preview/'));
