"""Executes latest review function bodies, not the whole original app.
API responses, CSS and dependency helpers are controlled test fixtures.
Screenshots intentionally carry a test-harness label: not production UI.
"""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parent
helpers=r'''
const app=document.getElementById('app');
const fmt=n=>Number(n||0).toFixed(2);
const esc=v=>String(v??'').replace(/[&<>\'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const statusBadge=r=>`<span>${r.status_name}</span>`;
const sameDayDuplicateBadge=()=>'';
const attachmentControl=()=>'';
const bindFilePreviews=()=>{};
const cleanups=[];
const registerPageCleanup=f=>cleanups.push(f);
const clearPageResources=()=>{cleanups.splice(0).forEach(f=>f())};
const json=(method,payload)=>({method,payload});
const toast=message=>{document.getElementById('toast').textContent=message};
const promptModal=async()=>null;
window.fixture={id:77,employee_name:'合成测试员工',employee_no:'0000077',submitted_at:'2026-09-10 09:00',recognition_type:'安全',recognizer_name:'测试主管',content:'月度上限场景',fraction:0.5,credited_fraction:0,status:'pending',status_name:'待复核',image_url:'',monthly_cap_reason:''};
window.apiCalls=[];
const api=async(url,opts={})=>{
  window.apiCalls.push({url,method:opts.method||'GET'});
  if(opts.method==='POST'){
    window.fixture={...window.fixture,status:'confirmed',status_name:'已确认',credited_fraction:0,monthly_cap_reason:'本月安全已达5分上限，本条保留认可记录但不再计分'};
    return {ok:true,record:{...window.fixture}};
  }
  return [{...window.fixture}];
};
'''
css='''body{font-family:Arial,"Noto Sans CJK SC",sans-serif;margin:18px;font-size:15px;} .test-label{padding:12px;border:2px dashed;margin-bottom:16px} .panel{border:1px solid;padding:16px} .record-line{display:flex;justify-content:space-between;gap:10px} .table-wrap{overflow:auto} table{border-collapse:collapse;width:100%} th,td{padding:10px;border:1px solid} button{padding:10px;margin:4px} .work-card{border:1px solid;padding:12px} .work-card-head{display:flex;flex-wrap:wrap;gap:10px} #expected{margin-top:16px;border:1px dashed;padding:10px}'''
viewports=[(1920,1080),(1440,900),(1366,768),(390,844),(375,667),(360,800)]
results=[]
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    version=browser.version
    for width,height in viewports:
        page=browser.new_page(viewport={'width':width,'height':height},locale='zh-CN')
        page.set_content(f'<html lang="zh"><meta charset="utf-8"><style>{css}</style><div class="test-label">局部函数复现实验 · 非完整应用截图<br>JunjiePR .8 · API和样式为测试夹具</div><main id="app"></main><p id="toast"></p><p id="expected">模拟服务端确认结果：原始 0.50 分，实际计入 0.00 分。</p></html>')
        page.add_script_tag(content=helpers+'\n'+(ROOT/'review_excerpt.js').read_text())
        page.evaluate('renderReview()')
        before=page.locator('[data-review-row="77"]').inner_text()
        # Read current DOM before selecting the unique confirm control.
        controls=page.locator('[data-action="confirm"]').count()
        assert controls==1
        page.locator('[data-action="confirm"]').click()
        page.wait_for_function("document.getElementById('toast').textContent==='操作成功'")
        after=page.locator('[data-review-row="77"]').inner_text()
        screenshot=f'review_after_{width}x{height}.png'
        if width in (1366,390):page.screenshot(path=str(ROOT/screenshot),full_page=True)
        results.append({'viewport':f'{width}x{height}','before_text':before,'after_text':after,'response':page.evaluate('window.fixture'),'calls':page.evaluate('window.apiCalls'),'stale_score_visible':'0.50分' in after and '计入0.00分' not in after,'cap_reason_missing':'已达5分上限' not in after,'processed_row_still_in_queue':page.locator('[data-review-row="77"]').count()==1,'screenshot':screenshot if width in (1366,390) else None})
        page.close()
    browser.close()
output={'mode':'real Chromium, latest extracted renderReview + bindReviewActions; synthetic API/helper/CSS fixture; NOT full app or real devices','chromium_version':version,'results':results}
(ROOT/'review_browser_results.json').write_text(json.dumps(output,ensure_ascii=False,indent=2))
print(json.dumps(output,ensure_ascii=False,indent=2))
