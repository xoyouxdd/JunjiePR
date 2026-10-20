// Render the real filter templates and CSS; use synthetic options/history only.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),http=require('node:http');
const {chromium,webkit,firefox}=require('playwright');
const root=path.resolve(__dirname,'../app/static'),history=Array.from({length:12},(_,i)=>({exported_at:'2026-10-19 20:08',month:'2026-10',attraction_name:'热力追踪',title:'CM/TR全部',keyword:i?'':'合成长姓名和员工号',employee_count:120+i}));
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const options='<option value="1">热力追踪</option><option value="2">矮人迷宫</option>';
const vars={monthNow:()=> '2026-10',monthStart:()=> '2026-10-01',today:()=> '2026-10-20',opt:()=>options,state:{options:{}},circleOptions:options,canExport:true,scopeField:'',recordTypes:'<option value="all">全部</option>',levelOptions:'<option value="1">声明</option>',typeOptions:'<option value="1">合成类型</option>',esc};
function form(file,id,extra={}){
  const source=fs.readFileSync(path.join(root,'js/app',file),'utf8'),start=source.indexOf(`<form id="${id}"`),end=source.indexOf('</form>',start);
  assert(start>=0&&end>start);return vm.runInNewContext('`'+source.slice(start,end+7)+'`',{...vars,...extra});
}
const statsSource=fs.readFileSync(path.join(root,'js/app/statistics.js'),'utf8'),historyStart=statsSource.indexOf('host.innerHTML=data.items.length?`')+'host.innerHTML=data.items.length?`'.length,historyEnd=statsSource.indexOf("`:'<span class=\"field-hint\">当前账号暂无",historyStart);
assert(historyEnd>historyStart);
const historyHtml=vm.runInNewContext('`'+statsSource.slice(historyStart,historyEnd)+'`',{data:{items:history},esc});
const fixtures=[form('statistics.js','statsFilter'),form('rankings.js','prFilter'),form('entries.js','entryFilter'),form('declarations.js','declarationFilter'),form('entries.js','entryFilter',{scopeField:'<label>查看范围<select name="scope"><option value="mine">我的登记记录</option></select></label>'})];
const server=http.createServer((req,res)=>{
  const url=new URL(req.url,'http://fixture');
  if(url.pathname==='/'){
    const fixture=fixtures[Number(url.searchParams.get('case')||0)];
    res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});res.end(`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/style.css"><link rel="stylesheet" href="/workspace-ui.css"><body><div class="app-frame"><main class="app-shell"><div id="app" data-workspace-ui><section class="panel"><h2>合成筛选布局</h2>${fixture}<div class="recent-exports">${historyHtml}</div></section></div></main></div></body></html>`);return;
  }
  const file={'/style.css':'style.css','/workspace-ui.css':'workspace-ui.css'}[url.pathname];
  if(!file){res.writeHead(404);res.end();return;}res.writeHead(200,{'Content-Type':'text/css'});res.end(fs.readFileSync(path.join(root,'css',file)));
});
async function main(){
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const checks=[];
  try{for(const engine of (process.env.LAYOUT_TEST_ENGINES||'edge').split(',')){
    const browserType={edge:chromium,webkit,firefox}[engine];assert(browserType);
    const browser=await browserType.launch({headless:true,...(engine==='edge'?{channel:'msedge'}:{})});
    try{const page=await browser.newPage({viewport:{width:390,height:844},locale:'zh-CN'});
      for(let index=0;index<fixtures.length;index++){
        await page.goto(`http://127.0.0.1:${server.address().port}/?case=${index}`);
        const original=await page.locator('form').evaluate(form=>[...new FormData(form)]);
        for(const width of [320,360,375,390,414,430,768,1440]){
          await page.setViewportSize({width,height:900});
          const result=await page.locator('form').evaluate(form=>{
            const controls=[...form.querySelectorAll('label>input,label>select')].map(input=>{const r=input.getBoundingClientRect(),label=input.closest('label').getBoundingClientRect();return {name:input.name,y:r.y,h:r.height,labelY:label.y,out:r.x<label.x-1||r.right>label.right+1,appearance:getComputedStyle(input).appearance};});
            const rows=[];for(const c of controls){let row=rows.find(row=>Math.abs(row[0].labelY-c.labelY)<1);if(!row)rows.push(row=[]);row.push(c);}
            return {controls,rows,overflow:document.documentElement.scrollWidth>innerWidth+1,data:[...new FormData(form)]};
          });
          assert(!result.overflow);assert(result.controls.every(c=>!c.out));assert.deepEqual(result.data,original);
          if(width<=760){assert(result.controls.every(c=>Math.abs(c.h-46)<1));assert(result.controls.every(c=>c.appearance==='none'||!['month','start_date','end_date','attraction_id'].includes(c.name)));for(const row of result.rows)assert(row.every(c=>Math.abs(c.y-row[0].y)<1));}
          const detail=page.locator('.recent-export-history');assert.equal(await detail.getAttribute('open'),null);
          await detail.locator('summary').click();assert.equal(await detail.locator('tbody tr').count(),history.length);
          const cells=await detail.locator('tbody tr').first().locator('td').allTextContents();
          assert.deepEqual(cells,[history[0].exported_at,history[0].month,history[0].attraction_name,history[0].title,history[0].keyword,String(history[0].employee_count)]);
          if(width<=760)assert(await detail.evaluate(d=>d.querySelector('table').scrollWidth<=d.clientWidth+1));
          await detail.locator('summary').click();checks.push({engine,fixture:index,width});
        }
      }
      await page.close();
    }finally{await browser.close();}
  }console.log(JSON.stringify({status:'passed',states:checks.length,checks},null,2));
  }finally{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
