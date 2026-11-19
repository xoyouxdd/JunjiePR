// Persistent across page navigation; the server owns generation and authorization.
import { readApiBody, apiFallbackMessage } from './api.js';
import { esc, portalPath } from './format.js';

let owner=null,timer=null,busy=false,opened=false,tray=null,items=[],opener=null,taskRevision=0;
const retrying=new Set(),retryErrors=new Map();
const labels={receiving:'正在接收素材',queued:'排队中',running:'正在制作',completed:'可下载',failed:'未完成'};
const resume=()=>{if(!document.hidden)void refresh();};

function taskRow(row){
  const filters=row.retry_filters;
  const sending=retrying.has(row.id);
  const error=retryErrors.get(row.id);
  const scope=filters?`<dl class="export-retry-scope" aria-label="原导出条件">
    <div><dt>月份</dt><dd>${esc(filters.month)}</dd></div>
    <div><dt>景点圈</dt><dd>${esc(filters.attraction_name)}</dd></div>
    <div><dt>员工筛选</dt><dd>${esc(filters.keyword||'不限')}</dd></div>
  </dl>`:'';
  const retry=filters?`<div class="export-retry-actions">
    ${row.retry_task_id?'<span role="status">已提交重试</span>':`<button type="button" class="primary" data-export-retry="${esc(row.id)}" aria-disabled="${sending}">${sending?'正在提交…':'按原条件重试'}</button>`}
  </div>${error&&!row.retry_task_id?`<p class="error" role="alert">${esc(error)}</p>`:''}`:'';
  const status=filters?'制作失败':(labels[row.status]||'状态待确认');
  return `<article><div><strong>${esc(row.label)}</strong><span role="status">${status}${row.bytes?` · ${(row.bytes/1048576).toFixed(2)} MB`:''}</span>${row.error?`<p class="error">${esc(row.error)}</p>`:''}${scope}${retry}</div>${row.download_url?`<a class="secondary download-link" data-export-download="${esc(row.id)}" href="${esc(portalPath(row.download_url))}" download>下载文件</a>`:''}</article>`;
}

function paint(){
  if(!tray)return;
  const focused=tray.contains(document.activeElement)?document.activeElement:null;
  const focusKey=focused?.dataset.exportDownload;
  const focusRetry=focused?.dataset.exportRetry;
  const focusClose=focused?.hasAttribute('data-export-close');
  const active=items.filter(row=>['receiving','queued','running'].includes(row.status)).length;
  tray.hidden=!items.length&&!opened;
  tray.innerHTML=`<button type="button" class="export-task-toggle secondary" aria-expanded="${opened}" aria-controls="exportTaskList">导出任务${active?` · ${active} 项制作中`:''}</button><section id="exportTaskList" class="export-task-list panel" ${opened?'':'hidden'} aria-label="本人导出任务"><header><strong>导出任务</strong><button type="button" class="secondary" data-export-close aria-label="收起导出任务">收起</button></header><p class="field-hint">可以切换页面，制作完成后在这里下载。文件最多保留30分钟，请及时保存；失效后可回原页面重新导出。</p>${items.map(taskRow).join('')}</section>`;
  tray.querySelector('.export-task-toggle').onclick=()=>{opened=!opened;paint();};
  tray.querySelectorAll('[data-export-retry]').forEach(button=>{button.onclick=()=>retryTask(button.dataset.exportRetry);});
  if(!items.length){const empty=document.createElement('p');empty.className='field-hint';empty.textContent='暂无导出任务。生成报告后，可在这里查看进度和下载文件。';tray.querySelector('.export-task-list').append(empty);}
  tray.querySelector('[data-export-close]').onclick=()=>{opened=false;paint();if(!items.length&&opener?.isConnected)opener.focus();};
  if(focused){
    const controls=[...tray.querySelectorAll('[data-export-download],[data-export-retry]')];
    const replacement=controls.find(node=>focusKey?node.dataset.exportDownload===focusKey:focusRetry&&node.dataset.exportRetry===focusRetry)
      ||((focusClose||focusRetry)&&opened?tray.querySelector('[data-export-close]'):tray.querySelector('.export-task-toggle'));
    replacement?.focus({preventScroll:true});
  }
  tray.onkeydown=event=>{if(event.key==='Escape'&&opened){opened=false;paint();if(items.length)tray.querySelector('.export-task-toggle').focus();else if(opener?.isConnected)opener.focus();}};
}

async function refresh(){
  if(busy||!owner||document.hidden)return;
  const current=owner,revision=taskRevision;busy=true;
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
  try{
    const response=await fetch(portalPath('/api/export-tasks'),{credentials:'same-origin',cache:'no-store',signal:controller.signal});
    if(current!==owner)return;
    if(response.status===401){stopExportTasks();return;}
    if(!response.ok)throw new Error('暂时无法更新任务状态');
    const data=await response.json();
    if(current===owner&&revision===taskRevision){items=data.items||[];paint();}
  }catch(_){if(tray&&items.length){const node=tray.querySelector('.field-hint');if(node)node.textContent='任务状态暂未刷新，稍后自动重试。请勿重复导出。';}}
  finally{clearTimeout(timeout);busy=false;clearTimeout(timer);if(owner)timer=setTimeout(refresh,items.some(r=>['receiving','queued','running'].includes(r.status))?3000:20000);}
}

function stopExportTasks(){owner=null;clearTimeout(timer);document.removeEventListener('visibilitychange',resume);tray?.remove();tray=null;items=[];opened=false;opener=null;retrying.clear();retryErrors.clear();}
function startExportTasks(user){
  stopExportTasks();if(user.rotation_temporary)return;
  owner=user.id;tray=document.createElement('aside');tray.className='export-task-tray';tray.hidden=true;document.body.append(tray);document.addEventListener('visibilitychange',resume);void refresh();
}

async function queueExport(target,body){
  // Never inherit the current page's AbortSignal: changing views must not
  // cancel a received job. A failed response is uncertain and is not retried.
  let response;
  try{response=await fetch(portalPath('/api/export-tasks?'+new URLSearchParams({target})),{method:'POST',body,credentials:'same-origin'});}
  catch(_){opened=true;void refresh();paint();throw new Error('连接中断，任务是否收到尚未确认。请先查看导出任务，再决定是否重试。');}
  const data=await readApiBody(response);
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:apiFallbackMessage(response.status));
  if(!items.some(row=>row.id===data.id)){items.unshift(data);taskRevision++;}
  opened=true;paint();void refresh();return data;
}

async function retryTask(key){
  const row=items.find(item=>item.id===key);
  if(!row?.retry_filters||row.retry_task_id||retrying.has(key))return;
  const taskOwner=owner;
  retrying.add(key);
  retryErrors.delete(key);
  paint();
  try{
    const response=await fetch(portalPath(`/api/export-tasks/${encodeURIComponent(key)}/retry`),{method:'POST',credentials:'same-origin'});
    const data=await readApiBody(response);
    if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:apiFallbackMessage(response.status));
    if(taskOwner!==owner)return;
    items=items.map(item=>item.id===key?{...item,retry_task_id:data.id}:item);
    if(!items.some(item=>item.id===data.id))items.unshift(data);
    taskRevision++;
  }catch(error){
    if(taskOwner===owner)retryErrors.set(key,error instanceof TypeError?'连接中断，可再次点击此任务重试。':error.message);
  }finally{
    if(taskOwner===owner){retrying.delete(key);paint();void refresh();}
  }
}

function mountExportTasks(host){
  if(!host)return;
  host.innerHTML='<button type="button" class="secondary">查看导出任务</button>';
  host.querySelector('button').onclick=event=>{opener=event.currentTarget;opened=true;paint();void refresh();tray?.querySelector('[data-export-close]')?.focus();};
}

export { queueExport, startExportTasks, stopExportTasks, mountExportTasks };
