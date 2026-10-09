// Accessible modal lifecycle and form dialogs.
import { esc, prefersReducedMotion } from './format.js';
import { toast } from './toast.js';

function isNativePickerControl(node){const type=String(node?.type||'').toLowerCase();return type==='file'||type==='date'||type==='month'||type==='datetime-local'||type==='time';}

function dialogFocusables(root){return [...root.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]):not([type="hidden"]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')].filter(node=>!node.hidden&&!node.closest('[hidden]')&&node.getAttribute('aria-hidden')!=='true');}

// Lets a layer play its .is-leaving animation before it disappears; the timeout matches the CSS exit duration.
function leaveLayer(node,done){if(prefersReducedMotion()||!node.isConnected){done();return;}node.classList.add('is-leaving');setTimeout(()=>{node.classList.remove('is-leaving');done();},160);}

function bindDialogLayer(overlay,{root,initialFocus,onClose,allowClose,remove=true,inertRoots}={}){
  const dialog=root||overlay.querySelector('[role="dialog"]')||overlay;
  const previousFocus=document.activeElement;
  const previousOverflow=document.body.style.overflow;
  document.body.style.overflow='hidden';
  const inertNodes=[];
  (inertRoots||[...document.body.children].filter(node=>node!==overlay&&node.id!=='toast')).forEach(node=>{if(node.inert)return;node.inert=true;inertNodes.push(node);});
  let closed=false;
  const close=value=>{
    if(closed)return value;
    if(allowClose&&allowClose()===false)return value;
    closed=true;
    document.body.style.overflow=previousOverflow;
    document.removeEventListener('keydown',onKey,true);
    inertNodes.forEach(node=>{node.inert=false;});
    if(remove&&overlay.isConnected)leaveLayer(overlay,()=>overlay.remove());
    previousFocus?.focus?.();
    onClose?.(value);
    return value;
  };
  const onKey=event=>{
    if(event.key==='Escape'){if(isNativePickerControl(document.activeElement))return;event.preventDefault();close(null);return;}
    if(event.key!=='Tab')return;
    const nodes=dialogFocusables(dialog);
    if(!nodes.length)return;
    const first=nodes[0],last=nodes[nodes.length-1];
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  };
  document.addEventListener('keydown',onKey,true);
  (initialFocus||dialogFocusables(dialog)[0])?.focus?.();
  return {close,dialog};
}

function confirmModal(title,message,confirmText='确认'){return new Promise(resolve=>{const overlay=document.createElement('div');overlay.className='modal-overlay';const titleId='dialog-title-'+Date.now();overlay.innerHTML=`<section class="modal-card" role="dialog" aria-modal="true" aria-labelledby="${titleId}"><h3 id="${titleId}">${esc(title)}</h3><div class="modal-message">${message}</div><div class="modal-actions"><button type="button" class="secondary" data-modal-cancel>取消</button><button type="button" class="primary" data-modal-confirm>${esc(confirmText)}</button></div></section>`;document.body.appendChild(overlay);const cancel=overlay.querySelector('[data-modal-cancel]'),confirm=overlay.querySelector('[data-modal-confirm]');const layer=bindDialogLayer(overlay,{initialFocus:cancel,onClose:value=>resolve(value===true)});cancel.onclick=()=>layer.close(false);confirm.onclick=()=>layer.close(true);});}

function noticeModal(title,message,confirmText='知道了'){return new Promise(resolve=>{const overlay=document.createElement('div');overlay.className='modal-overlay';const titleId='dialog-title-'+Date.now();overlay.innerHTML=`<section class="modal-card" role="dialog" aria-modal="true" aria-labelledby="${titleId}"><h3 id="${titleId}">${esc(title)}</h3><div class="modal-message">${message}</div><div class="modal-actions"><button type="button" class="primary" data-modal-confirm>${esc(confirmText)}</button></div></section>`;document.body.appendChild(overlay);const confirm=overlay.querySelector('[data-modal-confirm]');const layer=bindDialogLayer(overlay,{initialFocus:confirm,onClose:()=>resolve()});confirm.onclick=()=>layer.close(true);});}

function promptModal(title,message,placeholder='',confirmText='确认'){return new Promise(resolve=>{const overlay=document.createElement('div');overlay.className='modal-overlay';const titleId='dialog-title-'+Date.now();overlay.innerHTML=`<section class="modal-card" role="dialog" aria-modal="true" aria-labelledby="${titleId}"><h3 id="${titleId}">${esc(title)}</h3><div class="modal-message">${message}<input type="text" class="modal-input" data-modal-input placeholder="${esc(placeholder)}" maxlength="200" autocomplete="off"></div><div class="modal-actions"><button type="button" class="secondary" data-modal-cancel>取消</button><button type="button" class="primary" data-modal-confirm>${esc(confirmText)}</button></div></section>`;document.body.appendChild(overlay);const input=overlay.querySelector('[data-modal-input]'),cancel=overlay.querySelector('[data-modal-cancel]'),confirm=overlay.querySelector('[data-modal-confirm]');const layer=bindDialogLayer(overlay,{initialFocus:input,onClose:value=>resolve(typeof value==='string'?value:null)});const submit=()=>{const value=input.value.trim();if(value)layer.close(value)};input.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();submit();}});cancel.onclick=()=>layer.close(null);confirm.onclick=submit;});}

function selectModal(title,message,rows,confirmText='提交'){return new Promise(resolve=>{const overlay=document.createElement('div');overlay.className='modal-overlay';const titleId='dialog-title-'+Date.now();overlay.innerHTML=`<section class="modal-card" role="dialog" aria-modal="true" aria-labelledby="${titleId}"><h3 id="${titleId}">${esc(title)}</h3><div class="modal-message">${message}<select class="modal-input" data-modal-select><option value="">请选择</option>${rows.map(row=>`<option value="${esc(row.id)}">${esc(row.label||row.name)}</option>`).join('')}</select></div><div class="modal-actions"><button type="button" class="secondary" data-modal-cancel>返回</button><button type="button" class="primary" data-modal-confirm>${esc(confirmText)}</button></div></section>`;document.body.appendChild(overlay);const select=overlay.querySelector('[data-modal-select]'),cancel=overlay.querySelector('[data-modal-cancel]'),confirm=overlay.querySelector('[data-modal-confirm]');const layer=bindDialogLayer(overlay,{initialFocus:select,onClose:value=>resolve(typeof value==='string'&&value?value:null)});cancel.onclick=()=>layer.close(null);confirm.onclick=()=>{if(select.value)layer.close(select.value)};});}

// 小组可以没有负责人：保存成功后，把后端返回的提示弹出给 HR。
function showSaveWarnings(result){const warnings=result?.warnings||[];if(warnings.length)noticeModal('请注意',`<ul>${warnings.map(text=>`<li>${esc(text)}</li>`).join('')}</ul>`);}

// 通用表单弹窗：onSubmit 抛错时保留弹窗并提示，成功后关闭。
function formModal(title,subtitle,body,confirmText,{onReady,onSubmit}={}){return new Promise(resolve=>{const overlay=document.createElement('div');overlay.className='modal-overlay';const titleId='dialog-title-'+Date.now();overlay.innerHTML=`<section class="modal-card form-modal" role="dialog" aria-modal="true" aria-labelledby="${titleId}"><h3 id="${titleId}">${esc(title)}</h3>${subtitle?`<p class="field-hint form-modal-subtitle">${subtitle}</p>`:''}<form class="form-stack" data-modal-form novalidate>${body}<div class="modal-actions"><button type="button" class="secondary" data-modal-cancel>取消</button><button type="submit" class="primary">${esc(confirmText)}</button></div></form></section>`;document.body.appendChild(overlay);const form=overlay.querySelector('[data-modal-form]'),cancel=overlay.querySelector('[data-modal-cancel]'),submit=form.querySelector('[type=submit]');const layer=bindDialogLayer(overlay,{initialFocus:form.querySelector('select,input')||cancel,onClose:value=>resolve(value===true)});cancel.onclick=()=>layer.close(false);onReady?.(form,()=>layer.close(false));form.onsubmit=async event=>{event.preventDefault();submit.disabled=true;try{await onSubmit(form);layer.close(true)}catch(error){toast(error.message,true)}finally{submit.disabled=false}};});}

export { bindDialogLayer, confirmModal, formModal, leaveLayer, noticeModal, promptModal, selectModal, showSaveWarnings };
