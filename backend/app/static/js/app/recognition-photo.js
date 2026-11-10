// Local previews never replace the native file used by the upload pipeline.
import { registerPageCleanup } from './context.js';
import { bindDialogLayer } from './dialogs.js';
import { esc } from './format.js';
import { readableEvidenceFile, rememberEvidenceFile, clearEvidenceFile, evidenceSelectionAttempted, recognitionImageIssueMessage } from './submission.js';

function bindRecognitionPhoto(form,{batch=false}={}){
  const inputs=[...form.querySelectorAll('[data-evidence-input]')],name=form.querySelector('[data-evidence-name]'),preview=form.querySelector('[data-evidence-preview]');
  if(!inputs.length||!preview)return {reset(){}};
  const image=preview.querySelector('img'),status=preview.querySelector('[data-evidence-preview-status]');
  const thumbnail=preview.querySelector('[data-evidence-thumbnail]'),view=preview.querySelector('[data-evidence-view]');
  const initialHint=name.textContent;
  let thumbnailUrl='',closePreview=null;
  const busy=()=>form.dataset.submitting==='true'||form.dataset.batchSubmitting==='true';
  const release=()=>{closePreview?.();closePreview=null;image.removeAttribute('src');if(thumbnailUrl)URL.revokeObjectURL(thumbnailUrl);thumbnailUrl='';};
  const reset=()=>{release();inputs.forEach(clearEvidenceFile);preview.hidden=true;name.textContent=initialHint;};
  const refresh=()=>{
    release();
    const file=readableEvidenceFile(form);
    const attempted=Boolean(file)||evidenceSelectionAttempted(form);
    preview.hidden=!attempted;
    thumbnail.hidden=!file;
    view.hidden=!file;
    status.hidden=true;
    if(!file){name.textContent=attempted?recognitionImageIssueMessage(form):initialHint;return;}
    name.textContent=`${batch?'本批全部记录共用':'已选择'}：${file.name}`;
    image.hidden=false;
    image.onerror=()=>{image.hidden=true;status.textContent='当前浏览器无法预览此图片，已保留原文件，可删除后重新选择。';status.hidden=false;};
    try{thumbnailUrl=URL.createObjectURL(file.slice(0,file.size,file.type));image.src=thumbnailUrl;}catch(_){image.onerror();}
  };
  inputs.forEach(input=>input.onchange=()=>{const file=rememberEvidenceFile(input);if(file||input.value)inputs.filter(other=>other!==input).forEach(clearEvidenceFile);refresh();});
  preview.querySelector('[data-evidence-remove]').onclick=()=>{if(busy())return;reset();form.dispatchEvent(new Event('change',{bubbles:true}));};
  const open=()=>{
    if(busy())return;const file=readableEvidenceFile(form);if(!file)return;
    closePreview?.();
    const overlay=document.createElement('div');overlay.className='modal-overlay image-preview-overlay';
    overlay.innerHTML=`<section class="image-preview-dialog" role="dialog" aria-modal="true" aria-label="已选照片预览"><header><strong>${esc(file.name)}</strong><button type="button" class="image-preview-close" aria-label="关闭图片预览">×</button></header><div class="image-preview-stage"><img alt="已选认可照片"><p hidden>当前浏览器无法预览此格式，原文件仍保留在登记表单中。</p></div></section>`;
    document.body.appendChild(overlay);let url='';const full=overlay.querySelector('img');
    const layer=bindDialogLayer(overlay,{initialFocus:overlay.querySelector('button'),onClose:()=>{full.removeAttribute('src');if(url)URL.revokeObjectURL(url);closePreview=null;}});closePreview=()=>layer.close();
    overlay.querySelector('button').onclick=()=>layer.close();overlay.onclick=event=>{if(event.target===overlay)layer.close();};
    full.onerror=()=>{full.hidden=true;overlay.querySelector('p').hidden=false;};
    try{url=URL.createObjectURL(file.slice(0,file.size,file.type));full.src=url;}catch(_){full.onerror();}
  };
  view.onclick=open;
  thumbnail.onclick=open;
  // Keep native inputs enabled: some picker bridges need FormData(form) fallback.
  const guard=event=>{if(busy()&&event.target.closest('.evidence-picker')){event.preventDefault();event.stopImmediatePropagation();}};
  form.addEventListener('click',guard,true);
  let disposed=false;
  const dispose=()=>{if(disposed)return;disposed=true;release();inputs.forEach(clearEvidenceFile);inputs.forEach(input=>input.onchange=null);form.removeEventListener('click',guard,true);form.removeEventListener('reset',reset);};
  form.addEventListener('reset',reset);form._disposeRecognitionPhoto=dispose;registerPageCleanup(dispose);
  return {reset};
}

export { bindRecognitionPhoto };
