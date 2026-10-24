// Persistent across page navigation; the server owns generation and authorization.
import { readApiBody, apiFallbackMessage } from './api.js';
import { esc, portalPath } from './format.js';

let owner=null,timer=null,busy=false,opened=false,tray=null,items=[],opener=null;
const labels={receiving:'正在接收素材',queued:'排队中',running:'正在制作',completed:'可下载',failed:'未完成'};
const resume=()=>{if(!document.hidden)void refresh();};

function paint(){
  if(!tray)return;
  const focused=tray.contains(document.activeElement)?document.activeElement:null;
  const focusKey=focused?.dataset.exportDownload;
  const focusClose=focused?.hasAttribute('data-export-close');
  const active=items.filter(row=>['receiving','queued','running'].includes(row.status)).length;
  tray.hidden=!items.length&&!opened;
  tray.innerHTML=`<button type="button" class="export-task-toggle secondary" aria-expanded="${opened}" aria-controls="exportTaskList">导出任务${active?` · ${active} 项制作中`:''}</button><section id="exportTaskList" class="export-task-list panel" ${opened?'':'hidden'} aria-label="本人导出任务"><header><strong>导出任务</strong><button type="button" class="secondary" data-export-close aria-label="收起导出任务">收起</button></header><p class="field-hint">可以切换页面，制作完成后在这里下载。文件最多保留30分钟，请及时保存；失效后可回原页面重新导出。</p>${items.map(row=>`<article><div><strong>${esc(row.label)}</strong><span role="status">${labels[row.status]||'状态待确认'}${row.bytes?` · ${(row.bytes/1048576).toFixed(2)} MB`:''}</span>${row.error?`<p class="error">${esc(row.error)}</p>`:''}</div>${row.download_url?`<a class="secondary download-link" data-export-download="${esc(row.id)}" href="${esc(portalPath(row.download_url))}" download>下载文件</a>`:''}</article>`).join('')}</section>`;
  tray.querySelector('.export-task-toggle').onclick=()=>{opened=!opened;paint();};
  if(!items.length){const empty=document.createElement('p');empty.className='field-hint';empty.textContent='暂无导出任务。生成报告后，可在这里查看进度和下载文件。';tray.querySelector('.export-task-list').append(empty);}
  tray.querySelector('[data-export-close]').onclick=()=>{opened=false;paint();if(!items.length&&opener?.isConnected)opener.focus();};
  if(focused){const replacement=focusKey?[...tray.querySelectorAll('[data-export-download]')].find(node=>node.dataset.exportDownload===focusKey):focusClose&&opened?tray.querySelector('[data-export-close]'):tray.querySelector('.export-task-toggle');replacement?.focus({preventScroll:true});}
  tray.onkeydown=event=>{if(event.key==='Escape'&&opened){opened=false;paint();if(items.length)tray.querySelector('.export-task-toggle').focus();else if(opener?.isConnected)opener.focus();}};
}

async function refresh(){
  if(busy||!owner||document.hidden)return;
  const current=owner;busy=true;
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
  try{
    const response=await fetch(portalPath('/api/export-tasks'),{credentials:'same-origin',cache:'no-store',signal:controller.signal});
    if(current!==owner)return;
    if(response.status===401){stopExportTasks();return;}
    if(!response.ok)throw new Error('暂时无法更新任务状态');
    items=(await response.json()).items||[];paint();
  }catch(_){if(tray&&items.length){const node=tray.querySelector('.field-hint');if(node)node.textContent='任务状态暂未刷新，稍后自动重试。请勿重复导出。';}}
  finally{clearTimeout(timeout);busy=false;clearTimeout(timer);if(owner)timer=setTimeout(refresh,items.some(r=>['receiving','queued','running'].includes(r.status))?3000:20000);}
}

function stopExportTasks(){owner=null;clearTimeout(timer);document.removeEventListener('visibilitychange',resume);tray?.remove();tray=null;items=[];opened=false;opener=null;}
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
  if(!items.some(row=>row.id===data.id))items.unshift(data);
  opened=true;paint();void refresh();return data;
}

function mountExportTasks(host){
  if(!host)return;
  host.innerHTML='<button type="button" class="secondary">查看导出任务</button>';
  host.querySelector('button').onclick=event=>{opener=event.currentTarget;opened=true;paint();void refresh();tray?.querySelector('[data-export-close]')?.focus();};
}

export { queueExport, startExportTasks, stopExportTasks, mountExportTasks };
