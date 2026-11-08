// Material picker, conversion polling and retry dialog.
import { api, holdUnauthorizedRedirect } from './api.js';
import { captureViewContext, pageTimeout, registerPageCleanup, unregisterPageCleanup } from './context.js';
import { bindDialogLayer } from './dialogs.js';
import { esc, portalPath } from './format.js';
import { state } from './state.js';
import { toast } from './toast.js';

let deductionMaterialPickerSequence=0;

function deductionMaterialPickerMarkup(){const id=`deductionMaterial${++deductionMaterialPickerSequence}`;return `<fieldset class="deduction-material-picker" data-deduction-material><legend>声明材料（可后补）</legend><p class="field-hint">可直接上传 PDF（最多100MB、6页），或拍照/从相册选择照片（最多6张、单张25MB、合计100MB）。未上传时可先提交，主管可后续补充；材料成功后才扣分。</p><div class="evidence-actions native-file-actions"><label class="secondary native-file-trigger" for="${id}Pdf">上传PDF</label><label class="secondary native-file-trigger" for="${id}Camera">拍照</label><label class="secondary native-file-trigger" for="${id}Album">从相册选择</label></div><input id="${id}Pdf" class="native-file-input" data-material-pdf-input type="file" accept="application/pdf,.pdf"><input id="${id}Camera" class="native-file-input" data-material-camera-input type="file" accept="image/jpeg,image/png,image/webp,image/heic,image/heif,.jpg,.jpeg,.png,.webp,.heic,.heif" capture="environment"><input id="${id}Album" class="native-file-input" data-material-album-input type="file" accept="image/jpeg,image/png,image/webp,image/heic,image-heif,.jpg,.jpeg,.png,.webp,.heic,.heif" multiple><div class="deduction-material-summary" data-material-summary aria-live="polite">可先不上传，后续由主管补充材料。</div></fieldset>`;}

function bindDeductionMaterialPicker(root){
  const picker=root.querySelector('[data-deduction-material]');if(!picker)return null;
  const pdfInput=picker.querySelector('[data-material-pdf-input]'),cameraInput=picker.querySelector('[data-material-camera-input]'),albumInput=picker.querySelector('[data-material-album-input]'),summary=picker.querySelector('[data-material-summary]');
  let pdfFile=null,photos=[];const previews=new Map(),maxPdfBytes=100*1024*1024,maxPhotoBytes=25*1024*1024,maxTotalBytes=100*1024*1024,maxPhotos=6,allowed=new Set(['jpg','jpeg','png','webp','heic','heif']);
  const fail=message=>{toast(message,true);return false;};
  const releasePreview=file=>{const url=previews.get(file);if(url){URL.revokeObjectURL(url);previews.delete(file);}};
  const clearPhotos=()=>{photos.forEach(releasePreview);photos=[];cameraInput.value='';albumInput.value='';};
  const previewFor=file=>{if(!previews.has(file))previews.set(file,URL.createObjectURL(file));return previews.get(file);};
  const photoOK=file=>{const ext=(file.name.split('.').pop()||'').toLowerCase();if(!allowed.has(ext))return fail('照片仅支持 JPG/JPEG、PNG、HEIC、WebP。');if(!file.size)return fail('照片未成功读取，请重新选择。');if(file.size>maxPhotoBytes)return fail(`照片“${file.name}”超过25MB，请重新选择。`);return true;};
  const render=()=>{
    picker.classList.toggle('is-selected',!!pdfFile||photos.length>0);
    if(pdfFile){
      summary.innerHTML=`<div class="material-selected-file"><strong>已选择PDF：</strong><span>${esc(pdfFile.name)} · ${(pdfFile.size/1024/1024).toFixed(1)}MB</span><button type="button" class="secondary" data-material-remove-pdf>删除</button><label class="secondary native-file-trigger" for="${pdfInput.id}" data-material-replace-pdf>更换PDF</label></div>`;
      summary.querySelector('[data-material-remove-pdf]').onclick=()=>{pdfFile=null;pdfInput.value='';render();};return;
    }
    if(photos.length){
      summary.innerHTML=`<div class="material-photo-grid">${photos.map((file,index)=>`<figure class="material-photo-card"><img src="${previewFor(file)}" alt="已选照片 ${index+1}" data-material-preview><figcaption title="${esc(file.name)}">${esc(file.name)}</figcaption><button type="button" class="material-photo-remove" data-material-remove-photo="${index}" aria-label="删除第${index+1}张照片">×</button></figure>`).join('')}</div><div class="material-selection-actions"><strong>已选择${photos.length}张照片</strong><label class="secondary native-file-trigger" for="${albumInput.id}" data-material-replace-photos>重新选择照片</label></div><span>提交后将显示“材料生成中”，PDF生成成功后自动生效。</span>`;
      summary.querySelectorAll('[data-material-remove-photo]').forEach(button=>button.onclick=()=>{const index=Number(button.dataset.materialRemovePhoto);const [removed]=photos.splice(index,1);if(removed)releasePreview(removed);render();});
      summary.querySelector('[data-material-replace-photos]').onclick=()=>{clearPhotos();render();};
      summary.querySelectorAll('[data-material-preview]').forEach(image=>image.onerror=()=>{image.hidden=true;});return;
    }
    summary.textContent='请选择一种材料方式。';
  };
  const addPhotos=files=>{const incoming=[...files];if(!incoming.length)return;for(const file of incoming)if(!photoOK(file))return;const next=[...photos,...incoming];if(next.length>maxPhotos)return fail(`一次最多选择${maxPhotos}张照片。`);if(next.reduce((sum,file)=>sum+file.size,0)>maxTotalBytes)return fail('全部照片合计不能超过100MB。');pdfFile=null;pdfInput.value='';photos=next;render();};
  pdfInput.onchange=()=>{const file=pdfInput.files?.[0];if(!file)return;const ext=(file.name.split('.').pop()||'').toLowerCase();if(ext!=='pdf'||!file.size)return fail('请选择可读取的PDF文件。');if(file.size>maxPdfBytes)return fail('PDF不能超过100MB。');pdfFile=file;clearPhotos();render();};
  cameraInput.onchange=()=>{addPhotos(cameraInput.files||[]);cameraInput.value='';};albumInput.onchange=()=>{addPhotos(albumInput.files||[]);albumInput.value='';};render();
  const controller={appendTo(data){if(pdfFile){data.set('document',pdfFile,pdfFile.name);return 'pdf';}if(photos.length){photos.forEach(file=>data.append('document_images',file,file.name));return 'photos';}return '';},reset(){pdfFile=null;clearPhotos();pdfInput.value='';render();},hasMaterial(){return !!pdfFile||photos.length>0;}};
  registerPageCleanup(()=>controller.reset());
  return controller;
}

function showDeductionMaterialState(container,record,onFinished){if(!container||!record)return;const render=current=>{const status=current.material_status||'unknown',businessStatus=current.status||'',businessActive=businessStatus==='active';const failed=status==='failed',ready=status==='ready',missing=status==='missing',processing=status==='processing';const title=failed?'材料生成失败':ready?'材料已就绪':missing?'待补充材料':processing?'材料生成中':'材料状态未知';const body=failed?esc(current.material_error||'照片生成PDF失败，请重新提交材料。'):ready?(businessActive?'正式PDF已生成，扣分记录已生效。':`正式PDF已生成；当前业务状态为“${esc(current.status_name||businessStatus||'待处理')}”，是否计分以业务状态为准。`):missing?'该记录尚未上传材料，暂不计分。':processing?'照片已接收，系统正在后台合成正式PDF；完成后请以业务状态确认是否计分。':'请刷新后查看最新材料状态。';const action=failed||missing?`<button type="button" class="secondary" data-retry-material="${current.id}">${failed?'重新提交材料':'补充材料'}</button>`:ready?'<span class="field-hint">可在我的登记记录中查看材料与业务状态。</span>':'<span class="field-hint">此页面会自动刷新状态。</span>';container.innerHTML=`<div class="material-submission-state ${failed?'failed':ready?'ready':processing?'processing':'pending'}"><strong>${title}</strong><p>${body}</p>${action}</div>`;container.querySelector('[data-retry-material]')?.addEventListener('click',()=>openDeductionMaterialRetry(current.id,updated=>{render(updated);if(updated.material_status==='processing')watchDeductionMaterial(updated,container,onFinished);}));};render(record);if(record.material_status==='processing')watchDeductionMaterial(record,container,onFinished);}

function watchDeductionMaterial(record,container,onFinished){const page=captureViewContext(container);let tries=0;const poll=async()=>{if(!page.isCurrent()||!container?.isConnected)return;try{const current=(await api('/api/deductions/'+record.id+'/material-status')).record;if(!page.isCurrent())return;if(current.material_status==='processing'&&tries++<120){pageTimeout(poll,1500);return;}if(current.material_status==='processing'){container.innerHTML=`<div class="material-submission-state processing"><strong>材料仍在生成</strong><p>处理时间较长，请稍后在我的登记记录查看，或手动刷新。</p><button type="button" class="secondary" data-refresh-material>刷新状态</button></div>`;container.querySelector('[data-refresh-material]')?.addEventListener('click',()=>watchDeductionMaterial(record,container,onFinished));return;}showDeductionMaterialState(container,current,onFinished);if(current.material_status==='ready'){toast(current.status==='active'?'照片材料已生成PDF，扣分记录已生效。':'照片材料已生成PDF，请继续查看业务处理状态。');onFinished?.(current);}}catch(error){if(error?.name==='AbortError'||!page.isCurrent()||!container?.isConnected)return;if(tries++<5)pageTimeout(poll,2500);else toast('材料状态暂时无法刷新，请稍后在我的登记记录查看。',true);}};pageTimeout(poll,1200);}

function openDeductionMaterialRetry(recordId,done){
 const page=captureViewContext(),overlay=document.createElement('div');overlay.className='modal-overlay';
 const titleId='deduction-material-title-'+Date.now();
 overlay.innerHTML=`<section class="modal-card deduction-material-retry" role="dialog" aria-modal="true" aria-labelledby="${titleId}"><h3 id="${titleId}">补充声明材料</h3><p>沿用原声明。请上传 PDF，或拍照／从相册选择材料；是否计分以业务状态为准。</p><div data-material-upgrade aria-live="polite">正在检查升级条件…</div>${deductionMaterialPickerMarkup()}<div data-material-error class="material-submission-state failed" role="alert" tabindex="-1" hidden></div><a data-material-login class="secondary" href="${esc(portalPath('/login'))}" target="_blank" rel="noopener" hidden>在新标签页重新登录原账号</a><div class="modal-actions"><button type="button" class="secondary" data-retry-cancel>取消</button><button type="button" class="primary" data-retry-submit>提交材料</button></div></section>`;
 document.body.appendChild(overlay);
 const material=bindDeductionMaterialPicker(overlay),cancel=overlay.querySelector('[data-retry-cancel]'),submit=overlay.querySelector('[data-retry-submit]'),upgrade=overlay.querySelector('[data-material-upgrade]'),errorBox=overlay.querySelector('[data-material-error]'),login=overlay.querySelector('[data-material-login]'),originalEmployeeId=state.me?.id;
 let submitting=false,closed=false,preview=null,needsReauth=false,exiting=false,writeStarted=false;
 const requestOptions={redirectOnUnauthorized:false};
 const showError=error=>{if(closed)return;if(error.status===401||error.detail?.code==='MATERIAL_OPERATOR_CHANGED')needsReauth=true;errorBox.textContent=error.status===401?'登录已失效。所选材料已保留，请在新标签页重新登录原账号，再返回此弹窗重试。':((error.message||'提交未完成，请稍后重试。')+(error.cause?.message?' 原因：'+error.cause.message:''));errorBox.hidden=false;login.hidden=!needsReauth;errorBox.focus();toast(errorBox.textContent,true);};
 const releaseRedirect=holdUnauthorizedRedirect();
 const releaseDraft=()=>{releaseRedirect();unregisterPageCleanup(exitDraft);};
 const exitDraft=()=>{exiting=true;layer.close();overlay.remove();if(writeStarted)toast('材料正在提交，结果暂未确认。请稍后查看记录，不要重复提交。');};
 const layer=bindDialogLayer(overlay,{initialFocus:cancel,allowClose:()=>exiting||!submitting,onClose:()=>{closed=true;releaseDraft();material?.reset?.();}});
 registerPageCleanup(exitDraft);
 cancel.onclick=()=>layer.close();
 const loadUpgrade=async()=>{
  if(closed||!page.isCurrent())throw new Error('页面或账号已变化，请重新打开补材料入口');
  const latest=await api('/api/deductions/'+recordId+'/material-upgrade-preview',requestOptions);
  if(typeof latest?.eligible!=='boolean'||(latest.eligible&&(!latest.first_record?.id||!Array.isArray(latest.reviewers))))throw new Error('升级条件响应异常，未提交材料，请稍后重试。');
  if(closed||!page.isCurrent())throw new Error('页面或账号已变化，请重新打开补材料入口');
  const old=preview,selected=upgrade.querySelector('select')?.value,selectedReason=upgrade.querySelector('[data-recovery-reason]')?.value||'';
  preview=latest;
  if(latest.eligible){
   upgrade.innerHTML=`<div class="material-submission-state pending"><strong>此声明需要升级</strong><p>关联历史声明：${esc(latest.first_record.occurred_on)} · ${esc(latest.first_record.description)}</p><p>仅两张声明参与升级，经理确认实际开具的备忘录或一级警告。</p><p>沿用本条声明作为第二张来源，暂不扣分。材料就绪后进入所选经理待办，再录入备忘录或一级警告。</p><label>审核经理<select data-material-upgrade-reviewer><option value="">请选择</option>${(latest.reviewers||[]).map(row=>`<option value="${row.id}">${esc(row.name)} · ${esc(row.employee_no)} · ${esc(row.role_name)}</option>`).join('')}</select></label>${latest.recover_legacy?'<label>重新指派原因<textarea data-recovery-reason maxlength="500" required></textarea></label>':''}</div>`;
   const select=upgrade.querySelector('select'),choice=old?.eligible&&old.first_record.id===latest.first_record.id?selected:String(latest.default_reviewer_id||'');
   if([...select.options].some(option=>option.value===choice))select.value=choice;if(latest.recover_legacy)upgrade.querySelector('[data-recovery-reason]').value=selectedReason;
   submit.textContent='提交材料并转升级';
  }else{upgrade.textContent=latest.needs_recovery?'原审核经理已失效，请在待办的升级待办中重新指派；原声明和材料保留。':latest.existing_upgrade?'已有升级工单，补交后继续原审核经理的待办。':'';submit.textContent='提交材料';}
  return old&&((!!old.eligible!==!!latest.eligible)||(old.first_record?.id!==latest.first_record?.id));
 };
 submit.disabled=true;
 void loadUpgrade().catch(error=>{if(!closed){upgrade.textContent='升级条件检查未完成';showError(error);}}).finally(()=>{if(!closed)submit.disabled=false;});
 submit.onclick=async()=>{
  if(submitting)return;
  if(!material?.hasMaterial()){showError(new Error('请上传PDF或选择照片材料。'));return;}
  errorBox.hidden=true;submitting=true;submit.disabled=true;cancel.disabled=true;
  try{
   const me=await api('/api/me',requestOptions);
   if(closed||!page.isCurrent())return;
   if(!originalEmployeeId||Number(me.id)!==Number(originalEmployeeId)){needsReauth=true;throw new Error('当前登录账号已变化，材料仍保留；请重新登录原账号后再提交。');}
   needsReauth=false;login.hidden=true;
   if(await loadUpgrade())throw new Error('升级条件已变化，请核对历史声明和审核经理后再次提交');
   const data=new FormData(),mode=material.appendTo(data);
   data.set('expected_operator_id',originalEmployeeId);
   if(preview.eligible){const reviewer=upgrade.querySelector('select').value;if(!reviewer)throw new Error('请选择审核经理');data.set('reviewer_id',reviewer);data.set('expected_first_id',preview.first_record.id);if(preview.recover_legacy){const reason=upgrade.querySelector('[data-recovery-reason]').value.trim();if(!reason)throw new Error('请填写重新指派原因');data.set('recovery_reason',reason);}}
   writeStarted=true;
   const out=await api('/api/deductions/'+recordId+'/material',{...requestOptions,method:'POST',body:data});
   if(out?.ok!==true||Number(out.record?.id)!==Number(recordId))throw new Error('提交结果暂未确认：服务器返回的材料记录不完整或不匹配。请先查看相关记录，确认未成功后再重试。');
   const alreadyClosed=closed;
   submitting=false;layer.close();toast(alreadyClosed?`声明 #${recordId} 的材料已提交，请在相关记录中查看处理状态。`:preview.eligible?'原声明已转入升级流程，材料就绪后请到所选经理待办处理。':mode==='photos'?'照片材料已提交，正在后台合成为PDF。':'材料已提交，正在后台处理。');if(!alreadyClosed)try{await page.refresh(done,out.record);}catch(error){toast('材料已提交，但列表刷新失败。请刷新页面查看结果，不要重复提交。',true);}
  }catch(error){if(closed){if(writeStarted)toast(error.status&&error.status<500?'材料提交被拒绝：'+error.message:'提交结果暂未确认，请先查看相关记录，不要重复提交。'+(error.message?' '+error.message:''),true);}else showError(error);}
  finally{submitting=false;writeStarted=false;if(!closed){submit.disabled=false;cancel.disabled=false;}}
 };
}

export { bindDeductionMaterialPicker, deductionMaterialPickerMarkup, openDeductionMaterialRetry, showDeductionMaterialState };
