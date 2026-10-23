'use strict';
// Actual screen templates, renderer and CSS; synthetic data, no business writes.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../app/static'),out=path.resolve(__dirname,'../../.handover_review');
function fixture(){
  const persons=[],names=['林晓晨','陈雨欣','李佳宁','王思远','周乐怡','张明轩','黄子涵','许嘉宁','赵雨桐','杨文昊'];let seq=0;
  const person=(state,extra={})=>{const p={pid:String(++seq),name:names[(seq-1)%names.length][0]+names[Math.floor((seq-1)/names.length)%names.length].slice(1),state,flags:[],tags:[],start:420,end:1080,lineStart:480-(seq%9)*3,...extra};persons.push(p);return p;};
  const definitions=[['A1','A',['塔台','上客1','上协1','下协','大出口商店']],['A2','A',['饮水童车','小C','核销','身高墙','分组1']],['B1','B',['塔台协助','上客2','上协2','分组2','快迎2']],['B2','B',['迎宾童车','迎宾','豹协','汇合','快速汇合']],['C1','C',['上客3','上协3','预演','预演协助','单人队末']],['C2','C',['快迎1','单人迎宾','分组3','上客4','上协4']],['D1','D',['河马池','铁出口','迎宾协助','下客1','下客2']],['D2','D',['铁A','铁B','铁C']],['E','E',['城内巡视','队末']]];
  const lines=definitions.map(([id,group,posts],j)=>({id,group,active:j<7,posts:posts.map((name,i)=>{const scheduled=j===0&&i===4,standby=j>=7,p=scheduled?null:person(standby?'notyet':'onpost',{line:id,post:name});return {name,open:!scheduled&&!standby,opened:!scheduled,openAt:scheduled?'09:00':null,closeAt:j===1&&i===3?'08:45':null,occ:p?.pid||null};})}));
  for(let i=0;i<3;i++)person('ready',{readyAt:480,assign:{line:['A1','C2','D1'][i],departAt:480,mode:'push'}});
  for(let i=0;i<14;i++)person('walkback',{walkbackSince:475-i%5,after:'rest'});
  for(let i=0;i<16;i++)person('rest',{readyAt:484+i,assign:i%3===0?{line:'B1',mode:'push',departAt:484+i}:null});
  for(let i=0;i<6;i++)person('meal',{readyAt:506+i*3});
  return {version:1,test_mode:true,clock:{date:'2026-10-11',minute:480,paused:true,mode:'sim'},settings:{departEarly:0},day:{status:'running',lines,persons}};
}
function html(){let page=fs.readFileSync(path.join(root,'rotation-screen.html'),'utf8').replace(/<link[^>]+>/g,'').replace(/<script[\s\S]*?<\/script>/g,'').replace('测试环境 · 模拟数据','布局演示 · 模拟数据');
  page=page.replace('</head>',`<style>${fs.readFileSync(path.join(root,'css/style.css'),'utf8')}\n${fs.readFileSync(path.join(root,'css/rotation.css'),'utf8')}</style></head>`);
  let screen=fs.readFileSync(path.join(root,'js/rotation-screen.js'),'utf8');screen=screen.slice(0,screen.lastIndexOf('(async () => {'));
  return page.replace('</body>',`<script>${fs.readFileSync(path.join(root,'js/rotation-common.js'),'utf8')}</script><script>${screen}</script><script>RT.data=${JSON.stringify(fixture())};RT.fetchedAt=Date.now();RT.render();document.getElementById('rtSync').textContent='布局演示 · 不操作业务数据';confirmRotation=()=>toast('布局演示，仅展示排版；实际操作请进入轮岗测试。');</script></body>`);
}
async function main(){const server=http.createServer((req,res)=>{res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});res.end(html());});
  if(process.argv.includes('--serve')){server.listen(18112,'127.0.0.1',()=>console.log('Tablet layout preview: http://127.0.0.1:18112/'));return;}
  await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await chromium.launch({channel:'msedge',headless:true});
  try{fs.mkdirSync(out,{recursive:true});for(const [width,height] of [[1280,800],[1366,1024],[1180,820],[1024,768],[800,1280],[390,844]]){
    const page=await browser.newPage({viewport:{width,height}}),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.locator('.rt-line').first().waitFor();assert.equal(await page.locator('.rt-line').count(),9);assert.equal(await page.locator('.rt-sect').count(),4);
    const size=await page.evaluate(()=>({width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight,frames:[...document.querySelectorAll('.rt-sect')].map(e=>({height:e.clientHeight,top:e.getBoundingClientRect().top,bottom:e.getBoundingClientRect().bottom,chipHeight:e.querySelector('.rt-chips').clientHeight})),lines:document.querySelector('.rt-lines').getBoundingClientRect().width}));
    assert(size.width<=width,`${width}: horizontal overflow`);if(width>=960){assert(size.height<=height,`${width}: page exceeds screen`);assert(size.frames.every(f=>f.chipHeight>=65&&f.bottom<=height),`${width}: clipped pool`);}
    assert.equal(await page.locator('.pending-add').count(),1);assert.equal(await page.locator('.pending-remove').count(),1);assert.deepEqual(errors,[]);
    await page.screenshot({path:path.join(out,`rotation-tablet-${width}x${height}.png`),fullPage:true});
    await page.evaluate(()=>{RT.data.clock.minute=434;RT.data.day.persons.push({pid:'waiting715',name:'待开始休息测试',state:'notyet',scheduledRestStart:435,start:435,end:975,flags:[],tags:[]});RT.render();});
    assert.match(await page.locator('.rt-resting').innerText(),/待开始 1 人/);assert.match(await page.locator('.rt-resting').innerText(),/班次未开始 · 07:15开始休息/);assert.equal(await page.locator('[data-pid=waiting715][data-act]').count(),0);assert.equal(await page.locator('.rt-ready').getByText('待开始休息测试').count(),0);
    await page.evaluate(()=>{RT.data.day.persons=RT.data.day.persons.filter(p=>p.state==='onpost');RT.render();});assert.equal(await page.locator('.rt-sect.is-empty').count(),4);
    await page.evaluate(()=>{RT.data.day=null;RT.render();});assert.match(await page.locator('#rtApp').innerText(),/没有开始/);
    await page.close();console.log(`PASS ${width}x${height}: populated, empty and unstarted display`);
  }}finally{await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
