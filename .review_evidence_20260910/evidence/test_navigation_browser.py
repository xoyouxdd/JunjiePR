"""Current api/changelog functions, native browser Response with a controlled stream.
This does NOT make HTTP requests: local HTTP is blocked by browser policy.
We inject the specified AbortError during JSON consumption to test the catch path.
"""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parent
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=browser.new_page();page.set_default_timeout(5000)
    page.set_content('<html><main id="app">initial</main></html>')
    page.add_script_tag(content='''
        const app=document.getElementById('app');const esc=v=>String(v??'');const portalPath=v=>v;
        const apiFallbackMessage=()=>'';const validationErrorMessage=()=>'';
        let renderAbortController=new AbortController();
        window.fetch=async(url,options)=>{
          const body=new ReadableStream({start(controller){
             options.signal.addEventListener('abort',()=>controller.error(new DOMException('Controlled stream aborted','AbortError')),{once:true});
          }});
          window.headersReceived=true;
          return new Response(body,{status:200,headers:{'Content-Type':'application/json'}});
        };
    '''+(ROOT/'navigation_excerpt.js').read_text())
    page.evaluate("() => {window.finished=false;window.oldPage=renderChangelog().then(()=>{window.finished=true;window.result='resolved';},e=>{window.finished=true;window.result=e.name;});}")
    page.wait_for_function('window.headersReceived===true')
    page.evaluate("renderAbortController.abort();renderAbortController=new AbortController();app.innerHTML='<h2>当前新页面B</h2>';window.beforeOldCompletion=app.textContent;")
    page.wait_for_function('window.finished===true')
    result=page.evaluate("({before:window.beforeOldCompletion,after:app.textContent,oldPromiseResult:window.result})")
    result.update(overwritten_by_cancelled_old_view=('更新记录' in result['after'] and '新页面B' not in result['after']),mode='original api/renderChangelog; native Response over controlled ReadableStream that errors on abort; synthetic navigation marker; NOT actual network/navigation integration',chromium_version=browser.version,unavailable='Attempted local HTTP navigation returned ERR_BLOCKED_BY_ADMINISTRATOR; no browser policy was changed')
    (ROOT/'navigation_results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    browser.close()
