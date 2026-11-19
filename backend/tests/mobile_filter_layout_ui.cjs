// Check the real report, ranking, entry and declaration forms at mobile/tablet widths.
const assert=require('node:assert/strict');
const {chromium,webkit,firefox}=require('./support/browser-runtime.cjs');
const {routeFrontend}=require('./support/frontend-page.cjs');
const html=`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/static/css/style.css"><link rel="stylesheet" href="/static/css/workspace-ui.css">
<div class="app-frame"><main class="app-shell"><div id="app" data-workspace-ui></div></main></div><div id="tabs"></div><div id="toast"></div>`;
const history=Array.from({length:12},(_,i)=>({exported_at:'2026-10-19 20:08',month:'2026-10',
  attraction_name:'热力追踪',title:'CM/TR全部',keyword:i?'':'合成长姓名和员工号',employee_count:120+i}));
const circles=[{id:1,name:'热力追踪'},{id:2,name:'矮人迷宫'}];
const cases=[
  {role:'GSM',module:'statistics',render:'renderStatistics',form:'#statsFilter'},
  {role:'GSM',module:'rankings',render:'renderPrRankings',form:'#prFilter'},
  {role:'GSM',module:'entries',render:'renderEntries',form:'#entryFilter'},
  {role:'GSM',module:'declarations',render:'renderDeclarationStatistics',form:'#declarationFilter'},
  {role:'SUPERVISOR',module:'entries',render:'renderEntries',form:'#entryFilter'},
  {role:'HR_CIRCLE',module:'rankings',render:'renderPrRankings',form:'#prFilter'}
];
async function main(){
  const checks=[];
  for(const engine of (process.env.LAYOUT_TEST_ENGINES||'edge').split(',')){
    const browserType={edge:chromium,webkit,firefox}[engine];assert(browserType);
    const browser=await browserType.launch({headless:true,...(engine==='edge'?{channel:'msedge'}:{})});
    try{
      const page=await browser.newPage({viewport:{width:390,height:844},locale:'zh-CN'});
      const errors=[];
      page.on('pageerror',error=>errors.push(error.message));
      await page.route('**/api/**',route=>{
        const url=new URL(route.request().url()),q=url.searchParams;
        if(url.pathname==='/api/statistics/my-exports')return route.fulfill({json:{items:history}});
        if(url.pathname==='/api/statistics')return route.fulfill({json:{month:q.get('month'),hierarchy:[]}});
        if(url.pathname==='/api/pr-rankings')return route.fulfill({json:{category:q.get('category'),total:0,pages:1,page:1,
          page_size:20,rows:[],start_date:q.get('start_date'),end_date:q.get('end_date'),attraction_name:'全部景点圈'}});
        if(url.pathname==='/api/my-entries')return route.fulfill({json:{items:[],total:0,page:1,page_size:100}});
        if(url.pathname==='/api/deductions/pending-materials')return route.fulfill({json:{items:[],total:0}});
        assert.equal(url.pathname,'/api/declaration-statistics');
        return route.fulfill({json:{month:q.get('month'),attractions:circles,levels:[{id:1,name:'声明',count:0}],
          types:[{id:1,name:'合成类型'}],total_records:0,employee_count:0,statuses:{},circles:[],records:[]}});
      });
      await page.goto(await routeFrontend(page,{html}));
      for(const spec of cases){
        await page.evaluate(async ({spec,circles})=>{
          const {state}=await import('/static/js/app/state.js');
          const {clearPageResources}=await import('/static/js/app/context.js');
          clearPageResources();
          state.me={role_code:spec.role,base_role_code:spec.role,attraction_id:1,employee_no:spec.role,
            permissions:['DATA_VIEW','DATA_EXPORT','DECLARATION_STATS_VIEW','DECLARATION_STATS_EXPORT']};
          state.options={employee_circles:circles,recognition_types:[],deduction_types:[]};
          state.tab=spec.module;
          const module=await import('/static/js/app/'+spec.module+'.js');
          await module[spec.render]();
        },{spec,circles});
        const form=page.locator(spec.form);
        if(spec.role==='HR_CIRCLE'){
          assert.equal(await form.locator('[name=attraction_id] option').count(),1);
          assert.deepEqual(await form.locator('[name=population] option').evaluateAll(xs=>xs.map(x=>x.value)),['frontline']);
        }
        const original=await form.evaluate(form=>[...new FormData(form)]);
        for(const width of [320,360,375,390,414,430,768,1440]){
          await page.setViewportSize({width,height:900});
          const result=await form.evaluate(form=>{
            const controls=[...form.querySelectorAll('label>input,label>select')].map(input=>{
              const r=input.getBoundingClientRect(),label=input.closest('label').getBoundingClientRect();
              return {name:input.name,y:r.y,h:r.height,labelY:label.y,out:r.x<label.x-1||r.right>label.right+1,appearance:getComputedStyle(input).appearance};
            });
            const rows=[];
            for(const control of controls){
              let row=rows.find(row=>Math.abs(row[0].labelY-control.labelY)<1);
              if(!row)rows.push(row=[]);
              row.push(control);
            }
            return {controls,rows,overflow:document.documentElement.scrollWidth>innerWidth+1,data:[...new FormData(form)]};
          });
          assert(!result.overflow,spec.render+' overflows at '+width);
          assert(result.controls.every(c=>!c.out));
          assert.deepEqual(result.data,original);
          if(width<=760){
            assert(result.controls.every(c=>Math.abs(c.h-46)<1));
            assert(result.controls.every(c=>c.appearance==='none'||!['month','start_date','end_date','attraction_id'].includes(c.name)));
            for(const row of result.rows)assert(row.every(c=>Math.abs(c.y-row[0].y)<1));
          }
          if(spec.module==='statistics'){
            const detail=page.locator('.recent-export-history');
            assert.equal(await detail.getAttribute('open'),null);
            await detail.locator('summary').click();
            assert.equal(await detail.locator('tbody tr').count(),history.length);
            const cells=await detail.locator('tbody tr').first().locator('td').allTextContents();
            assert.deepEqual(cells,[history[0].exported_at,history[0].month,history[0].attraction_name,history[0].title,history[0].keyword,String(history[0].employee_count)]);
            if(width<=760)assert(await detail.evaluate(d=>d.querySelector('table').scrollWidth<=d.clientWidth+1));
            await detail.locator('summary').click();
          }
          checks.push({engine,case:spec.role+'/'+spec.module,width});
        }
      }
      assert.deepEqual(errors,[]);
      await page.close();
    }finally{await browser.close();}
  }
  console.log(JSON.stringify({status:'passed',states:checks.length,checks},null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
