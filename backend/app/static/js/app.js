const app = document.getElementById('app');
const tabs = document.getElementById('tabs');
const toastEl = document.getElementById('toast');
let toastTimer = null;
const state = { me: null, options: null, tab: null, pendingMaterialFocusId: null, actionBadgeTotal: 0, homeMonth: null };
const circleTheme = name => ({'热力追踪':'heat','矮人迷宫':'dwarf','小熊罐子':'bear'})[String(name||'')] || 'all';
function applyCircleTheme(){document.body.dataset.circleTheme=circleTheme(state.me?.attraction_name);}
const statisticsDetailContext = () => new URLSearchParams(location.search);
const portalBasePath = location.pathname === '/recognition' || location.pathname.startsWith('/recognition/') ? '/recognition' : '';
function portalPath(path) {
  const value = String(path || '');
  if (!value || !value.startsWith('/') || /^\/\//.test(value) || /^[a-z][a-z0-9+.-]*:/i.test(value)) return value;
  if (!portalBasePath || value === portalBasePath || value.startsWith(`${portalBasePath}/`)) return value;
  return `${portalBasePath}${value}`;
}

const esc = value => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const pad2 = value => String(value).padStart(2,'0');
const today = () => {const value=new Date();return `${value.getFullYear()}-${pad2(value.getMonth()+1)}-${pad2(value.getDate())}`;};
const monthNow = () => today().slice(0, 7);
const monthStart = () => `${monthNow()}-01`;
const prefersReducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
function memberHomeMonths(){
  const now=new Date();
  return [0,1,2].map(offset=>{
    const value=new Date(now.getFullYear(),now.getMonth()-offset,1);
    return `${value.getFullYear()}-${pad2(value.getMonth()+1)}`;
  });
}
function memberHomeMonthLabel(month){
  const match=String(month||'').match(/^(\d{4})-(\d{2})$/);
  return match?`${match[1]}年${Number(match[2])}月`:String(month||'');
}
const fmt = n => {const value=Number(n||0);return (Math.abs(value)<0.005?0:value).toFixed(2);};
function recognitionScoreText(r){const original=fmt(r.fraction);if(r.status==='confirmed'){const credited=fmt(r.credited_fraction ?? r.fraction);if(credited!==original)return `计入${credited}分（原始${original}）`;return `${credited}分`;}return `${original}分`;}
function recognitionScoreNote(r){return r.monthly_cap_reason?`<small>${esc(r.monthly_cap_reason)}</small>`:'';}
const has = p => state.me.permissions.includes(p);
const newSubmissionKey = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
function normalizeSubmitDate(raw){const value=String(raw||'').trim();const match=value.match(/^(\d{4})\s*(?:-|\/|\.)\s*(\d{1,2})\s*(?:-|\/|\.)\s*(\d{1,2})$/)||value.match(/^(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?$/);if(!match)return '';const year=Number(match[1]),month=Number(match[2]),day=Number(match[3]),date=new Date(Date.UTC(year,month-1,day));return date.getUTCFullYear()===year&&date.getUTCMonth()===month-1&&date.getUTCDate()===day?`${year}-${pad2(month)}-${pad2(day)}`:'';}
function submittedDateValue(input){if(input?.dataset.submitDateCleared==='1')return '';return [input?.value,input?.dataset.submitDate,input?.defaultValue,input?.getAttribute('value')].map(normalizeSubmitDate).find(Boolean)||'';}
function bindDateSubmissionFallbacks(root=document){root.querySelectorAll('input[type="date"][name]').forEach(input=>{const remember=event=>{const value=normalizeSubmitDate(input.value);if(value){input.dataset.submitDate=value;delete input.dataset.submitDateCleared;}else if(event){delete input.dataset.submitDate;input.dataset.submitDateCleared='1';}};remember();input.addEventListener('input',remember);input.addEventListener('change',remember);});}
function requireSubmittedDate(data,input,label){const value=submittedDateValue(input);if(!value)throw new Error(`${label}未成功读取，请重新选择日期后提交。`);data.set(input.name,value);return value;}
function readableEvidenceFile(form){const input=[...form.querySelectorAll('[data-evidence-input]')].find(item=>{try{return Boolean(item.files?.length)}catch(_){return false;}});let file=null;try{file=input?.files?.[0]||null}catch(_){file=null;}if(!file||typeof file.name!=='string'||!file.name||!Number.isFinite(file.size)||file.size<=0)return null;return file;}
function evidenceSelectionAttempted(form){return [...form.querySelectorAll('[data-evidence-input]')].some(input=>{try{return Boolean(input.files?.length||input.value)}catch(_){return Boolean(input.value);}});}
function recognitionBrowserCompatibilityMessage(){return '当前浏览器不支持本次图片上传，请更换浏览器后重新选择图片；如需继续使用，请上报浏览器兼容问题。';}
function recognitionImageIssueMessage(form){if(!evidenceSelectionAttempted(form))return '请先选择认可图片后提交。';return '图片未成功读取，请重新选择图片后提交。';}
function recognitionImageFailureMessage(error){const fields=Array.isArray(error?.detail)?error.detail.map(item=>item?.loc?.[item.loc.length-1]):[];if(error?.detail?.code==='RECOGNITION_IMAGE_BROWSER_INCOMPATIBLE')return recognitionBrowserCompatibilityMessage();if(error?.detail?.code==='RECOGNITION_IMAGE_VALIDATION_ERROR'||(error?.status===422&&fields.includes('image')))return '图片未成功读取，请重新选择图片后提交。';return '';}
function submissionData(form){if(!form.dataset.submissionKey)form.dataset.submissionKey=newSubmissionKey();const data=new FormData(form);form.querySelectorAll('input[type="date"][name]').forEach(input=>{const value=submittedDateValue(input);if(value)data.set(input.name,value);});const evidence=readableEvidenceFile(form);if(evidence)data.set('image',evidence,evidence.name);else data.delete('image');data.delete('image_camera');data.delete('image_album');data.set('idempotency_key',form.dataset.submissionKey);return data;}
function recognitionSubmissionData(form,imageRequired=true){if(!form.dataset.submissionKey)form.dataset.submissionKey=newSubmissionKey();const file=readableEvidenceFile(form);if(imageRequired&&!file)throw new Error(recognitionImageIssueMessage(form));const data=new FormData();form.querySelectorAll('[name]').forEach(control=>{if(control.disabled||control.type==='file'||!control.name)return;if((control.type==='checkbox'||control.type==='radio')&&!control.checked)return;data.append(control.name,control.value);});form.querySelectorAll('input[type="date"][name]').forEach(input=>{const value=submittedDateValue(input);if(value)data.set(input.name,value);});if(file){data.append('image',file,file.name);data.set('image_client_ready','1');}data.set('idempotency_key',form.dataset.submissionKey);return data;}
function clearSubmissionKey(form){delete form.dataset.submissionKey;}
async function withSubmitLock(form, action){if(form.dataset.submitting==='true')return;form.dataset.submitting='true';const button=form.querySelector('button[type="submit"],button:not([type])');if(button)button.disabled=true;try{return await action();}finally{delete form.dataset.submitting;if(button)button.disabled=false;}}
function toast(message, bad=false, duration=2400, tone='') { clearTimeout(toastTimer); toastEl.setAttribute('role',bad?'alert':'status'); toastEl.setAttribute('aria-live',bad?'assertive':'polite'); toastEl.textContent=message; toastEl.classList.toggle('error',bad); toastEl.classList.toggle('encouragement',tone==='encouragement'); toastEl.classList.add('show'); if(bad){toastEl.onclick=()=>{toastEl.classList.remove('show');toastEl.onclick=null;};duration=Math.max(duration||0,8000);}else{toastEl.onclick=null;} toastTimer=setTimeout(()=>toastEl.classList.remove('show'),duration); }
function recognitionEncouragement(options){const rows=Array.isArray(options)?options.filter(row=>row&&row.template_id&&row.message):[];if(!rows.length)return '';const key=`recognition-v2:encouragement-seen:${state.me?.employee_no||'anonymous'}`;let seen=[];try{seen=JSON.parse(localStorage.getItem(key)||'[]')}catch(_){seen=[]}const selected=rows.find(row=>!seen.includes(row.template_id))||rows[0];try{localStorage.setItem(key,JSON.stringify([...seen.filter(id=>id!==selected.template_id),selected.template_id].slice(-24)))}catch(_){ }return selected.message;}
function showRecognitionEncouragement(options){const message=recognitionEncouragement(options);if(message)toast(`已提交，等待主管复核。\n${message}`,false,5600,'encouragement');else toast('加分已提交');}
function performanceFeedbackData(kind,record={}){
  const recognition=kind==='recognition',active=recognition?record.status==='confirmed':record.status==='active';
  const status=active?'已生效':record.status==='pending_upgrade'?'待升级审核':record.material_status==='missing'?'待补材料':record.material_status==='processing'?'材料处理中':'待审核';
  const note=active?(record.monthly_cap_reason||'是否计入月度汇总仍按现有LOA及计分规则执行。'):status==='待补材料'?'记录已保存，材料补齐并处理成功后才会生效。':status==='材料处理中'?'记录已保存，材料正在后台处理，暂未计分。':'记录已提交，审核通过后按现有规则计分。';
  const points=Number(recognition?(active?(record.credited_fraction??record.fraction):record.fraction):record.points);
  return {recognition,active,status,note,title:recognition?'登记成功，感谢你的认可！':'登记成功，感谢你的认真记录。',message:recognition?'每一次及时记录，\n都让伙伴的努力被看见。':'清晰、客观的记录，\n让团队管理更公平。',employee:record.employee_name||'',employeeNo:record.employee_no||'',date:record.recognition_date||record.occurred_on||'',score:kind==='upgrade'?'审核后确定':Number.isFinite(points)?`${recognition?'+':'−'}${Math.abs(points).toFixed(2)} 分`:'',scoreLabel:recognition&&active?'记录计入分值':'登记分值'};
}
function showPerformanceRegistrationFeedback(kind,result){
  if(!['TA_SUPERVISOR','SUPERVISOR','TA_GSM','GSM','AM','OM','HR_ADMIN','HR_CIRCLE','SYSTEM_ADMIN'].includes(state.me?.role_code))return false;
  if(result?.duplicate){toast('该登记已保存，无需重复提交');return true;}
  const record=result?.record||result?.request?.second_record;if(!record)return false;
  const data=performanceFeedbackData(kind,record),overlay=document.createElement('div'),titleId='performance-feedback-'+Date.now();overlay.className='modal-overlay';
  overlay.innerHTML=`<section class="modal-card performance-feedback" role="dialog" aria-modal="true" aria-labelledby="${titleId}"><button type="button" class="performance-feedback-close secondary" aria-label="关闭反馈">×</button><div class="performance-feedback-icon ${data.active?'is-active':'is-pending'}" aria-hidden="true">${data.active?'✓':'…'}</div><span class="badge ${data.active?'ok':'warn'}">${esc(data.status)}</span><h3 id="${titleId}">${esc(data.title)}</h3><p class="performance-feedback-message">${esc(data.message).replace(/\n/g,'<br>')}</p><div class="performance-feedback-summary"><div><strong>${esc(data.employee)}${data.employeeNo?` · ${esc(data.employeeNo)}`:''}</strong><span class="${data.recognition?'score-positive':'performance-feedback-value'}">${esc(data.score)}</span></div><small>${data.recognition?'认可日期':'事件日期'}：${esc(data.date)}${data.score?` · ${esc(data.scoreLabel)}`:''}</small></div><p class="field-hint">${esc(data.note)}</p><button type="button" class="primary performance-feedback-continue">继续登记</button></section>`;
  document.body.appendChild(overlay);const button=overlay.querySelector('.performance-feedback-continue'),layer=bindDialogLayer(overlay,{initialFocus:button});button.onclick=()=>layer.close();overlay.querySelector('.performance-feedback-close').onclick=()=>layer.close();return true;
}
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
async function showReleaseAnnouncement(){
  const data=await api('/api/changelog/announcement');
  const release=data.release;
  if(data.read||!release||!Array.isArray(release.items)||!release.items.length)return;
  const overlay=document.createElement('div');
  overlay.className='modal-overlay';
  overlay.innerHTML=`<section class="modal-card release-announcement" role="dialog" aria-modal="true" aria-labelledby="releaseAnnouncementTitle" tabindex="-1"><h3 id="releaseAnnouncementTitle">本次更新公告 · V${esc(release.version)}</h3><p class="field-hint">${release.content_version?`本次为修复补丁，沿用上次上线版本 V${esc(release.content_version)} 与你当前角色相关的公告内容。`:'以下是与你当前角色相关的更新内容，请阅读后关闭。'}</p><ul class="changelog-items">${release.items.map(item=>`<li><strong>${esc(item.summary)}</strong>${item.detail?`<p>${esc(item.detail)}</p>`:''}</li>`).join('')}</ul><div class="modal-actions"><button type="button" class="primary" data-announcement-close disabled>请阅读 5 秒</button></div></section>`;
  document.body.appendChild(overlay);
  const button=overlay.querySelector('[data-announcement-close]');
  let canClose=false,remaining=5,submitting=false;
  const timer=setInterval(()=>{remaining-=1;if(remaining>0){button.textContent=`请阅读 ${remaining} 秒`;}else{clearInterval(timer);button.disabled=false;button.textContent='我已阅读，关闭';}},1000);
  const layer=bindDialogLayer(overlay,{initialFocus:overlay.querySelector('.release-announcement'),allowClose:()=>canClose,onClose:()=>clearInterval(timer)});
  button.onclick=async()=>{if(remaining>0||submitting)return;submitting=true;button.disabled=true;try{await api('/api/changelog/announcement/read',json('POST',{version:release.version}));canClose=true;layer.close();}catch(error){toast(error.message||'阅读状态保存失败，请重试',true);button.disabled=false;submitting=false;}};
}
async function handlePendingMaterialConflict(error){const detail=error?.detail,record=detail?.code==='PENDING_MATERIAL_EXISTS'?detail.record:null;if(!record?.id)return false;const go=await confirmModal('已有待补材料记录',`<p>${esc(detail.message||'请先补充已有记录的材料。')}</p><p><strong>已有记录：</strong>${esc(record.employee_name)} · ${esc(record.occurred_on)} · ${esc(record.deduction_type)} · ${esc(record.deduction_level)}</p><p>补齐材料并处理完成后，才可继续登记下一条。</p>`,'去补充材料');if(go){state.pendingMaterialFocusId=Number(record.id);state.tab='entries';renderTabs();await render();}return true;}
function promptModal(title,message,placeholder='',confirmText='确认'){return new Promise(resolve=>{const overlay=document.createElement('div');overlay.className='modal-overlay';const titleId='dialog-title-'+Date.now();overlay.innerHTML=`<section class="modal-card" role="dialog" aria-modal="true" aria-labelledby="${titleId}"><h3 id="${titleId}">${esc(title)}</h3><div class="modal-message">${message}<input type="text" class="modal-input" data-modal-input placeholder="${esc(placeholder)}" maxlength="200" autocomplete="off"></div><div class="modal-actions"><button type="button" class="secondary" data-modal-cancel>取消</button><button type="button" class="primary" data-modal-confirm>${esc(confirmText)}</button></div></section>`;document.body.appendChild(overlay);const input=overlay.querySelector('[data-modal-input]'),cancel=overlay.querySelector('[data-modal-cancel]'),confirm=overlay.querySelector('[data-modal-confirm]');const layer=bindDialogLayer(overlay,{initialFocus:input,onClose:value=>resolve(typeof value==='string'?value:null)});const submit=()=>{const value=input.value.trim();if(value)layer.close(value)};input.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();submit();}});cancel.onclick=()=>layer.close(null);confirm.onclick=submit;});}
function selectModal(title,message,rows,confirmText='提交'){return new Promise(resolve=>{const overlay=document.createElement('div');overlay.className='modal-overlay';const titleId='dialog-title-'+Date.now();overlay.innerHTML=`<section class="modal-card" role="dialog" aria-modal="true" aria-labelledby="${titleId}"><h3 id="${titleId}">${esc(title)}</h3><div class="modal-message">${message}<select class="modal-input" data-modal-select><option value="">请选择</option>${rows.map(row=>`<option value="${esc(row.id)}">${esc(row.label||row.name)}</option>`).join('')}</select></div><div class="modal-actions"><button type="button" class="secondary" data-modal-cancel>返回</button><button type="button" class="primary" data-modal-confirm>${esc(confirmText)}</button></div></section>`;document.body.appendChild(overlay);const select=overlay.querySelector('[data-modal-select]'),cancel=overlay.querySelector('[data-modal-cancel]'),confirm=overlay.querySelector('[data-modal-confirm]');const layer=bindDialogLayer(overlay,{initialFocus:select,onClose:value=>resolve(typeof value==='string'&&value?value:null)});cancel.onclick=()=>layer.close(null);confirm.onclick=()=>{if(select.value)layer.close(select.value)};});}
function imagePreviewUrl(url){const separator=String(url).includes('?')?'&':'?';return portalPath(`${url}${separator}preview=1`);}
async function openImagePreview(url,title='签卡图片'){const overlay=document.createElement('div');overlay.className='modal-overlay image-preview-overlay';overlay.innerHTML=`<section class="image-preview-dialog" role="dialog" aria-modal="true" aria-label="${esc(title)}"><header><strong>${esc(title)}</strong><button type="button" class="image-preview-close" data-image-preview-close aria-label="关闭图片预览" title="关闭">×</button></header><div class="image-preview-stage"><span data-image-preview-status>图片加载中…</span><img data-image-preview-image alt="${esc(title)}" hidden></div></section>`;document.body.appendChild(overlay);const status=overlay.querySelector('[data-image-preview-status]'),image=overlay.querySelector('[data-image-preview-image]'),closeButton=overlay.querySelector('[data-image-preview-close]'),controller=new AbortController();let objectUrl='',closed=false;const layer=bindDialogLayer(overlay,{initialFocus:closeButton,onClose:()=>{closed=true;controller.abort();if(objectUrl){URL.revokeObjectURL(objectUrl);objectUrl='';}}});const close=()=>layer.close();closeButton.onclick=close;overlay.onclick=event=>{if(event.target===overlay)close();};image.onload=()=>image.classList.toggle('is-landscape',image.naturalWidth>image.naturalHeight*1.3);try{const response=await fetch(imagePreviewUrl(url),{credentials:'same-origin',signal:controller.signal});if(response.status===401){close();location.href=portalPath('/login');return;}if(!response.ok)throw new Error('图片加载失败');const blob=await response.blob();if(!blob.type.startsWith('image/'))throw new Error('该材料不是可预览图片');objectUrl=URL.createObjectURL(blob);image.src=objectUrl;image.hidden=false;status.hidden=true;}catch(error){if(!closed&&error.name!=='AbortError')status.textContent=error.message||'图片加载失败，请稍后重试';}}
function usesNativeMobilePdfViewer(){return window.matchMedia('(max-width: 760px), (pointer: coarse)').matches;}
async function openPdfPreview(url,title='PDF文件'){const previewUrl=imagePreviewUrl(url);if(usesNativeMobilePdfViewer()){window.location.assign(previewUrl);return;}const overlay=document.createElement('div');overlay.className='modal-overlay image-preview-overlay';overlay.innerHTML=`<section class="image-preview-dialog pdf-preview-dialog" role="dialog" aria-modal="true" aria-label="${esc(title)}"><header><strong>${esc(title)}</strong><button type="button" class="image-preview-close" data-file-preview-close aria-label="关闭PDF预览" title="关闭">×</button></header><div class="image-preview-stage pdf-preview-stage"><span data-file-preview-status>PDF加载中…</span><iframe data-pdf-preview title="${esc(title)}" hidden></iframe></div></section>`;document.body.appendChild(overlay);const status=overlay.querySelector('[data-file-preview-status]'),frame=overlay.querySelector('[data-pdf-preview]'),closeButton=overlay.querySelector('[data-file-preview-close]'),controller=new AbortController();let objectUrl='',closed=false;const layer=bindDialogLayer(overlay,{initialFocus:closeButton,onClose:()=>{closed=true;controller.abort();if(objectUrl){URL.revokeObjectURL(objectUrl);objectUrl='';}}});const close=()=>layer.close();closeButton.onclick=close;overlay.onclick=event=>{if(event.target===overlay)close();};try{const response=await fetch(previewUrl,{credentials:'same-origin',signal:controller.signal});if(response.status===401){close();location.href=portalPath('/login');return;}if(!response.ok)throw new Error('PDF加载失败');const blob=await response.blob();if(blob.type!=='application/pdf')throw new Error('该材料不是可预览PDF');objectUrl=URL.createObjectURL(blob);frame.src=objectUrl;frame.hidden=false;status.hidden=true;}catch(error){if(!closed&&error.name!=='AbortError')status.textContent=error.message||'PDF加载失败，请稍后重试';}}
function bindFilePreviews(root=document){root.querySelectorAll('[data-file-preview]').forEach(button=>button.onclick=()=>{const title=button.dataset.fileTitle||'查看材料';if(button.dataset.fileKind==='pdf')openPdfPreview(button.dataset.filePreview,title);else openImagePreview(button.dataset.filePreview,title);});}
function bindImagePreviews(root=document){root.querySelectorAll('[data-image-preview]').forEach(button=>button.onclick=()=>openImagePreview(button.dataset.imagePreview,button.dataset.imageTitle||'签卡图片'));bindFilePreviews(root);}
function imagePreviewButton(url,title='签卡图片'){return `<button type="button" class="evidence-link evidence-preview-link" data-image-preview="${esc(url)}" data-image-title="${esc(title)}">${esc(title)}</button>`;}
function attachmentControl(url,title,previewKind=''){if(!url)return '';if(previewKind==='image')return `<button type="button" class="evidence-link evidence-preview-link" data-file-preview="${esc(url)}" data-file-kind="image" data-file-title="${esc(title)}">${esc(title)}</button>`;if(previewKind==='pdf')return `<button type="button" class="evidence-link evidence-preview-link" data-file-preview="${esc(url)}" data-file-kind="pdf" data-file-title="${esc(title)}">${esc(title)}</button>`;return `<a class="evidence-link" href="${esc(portalPath(url))}" target="_blank" rel="noopener">${esc(title)}</a>`;}
function validationErrorMessage(detail){if(!detail||typeof detail!=='object')return '';if(detail.code==='SICK_LEAVE_VALIDATION_ERROR'&&Array.isArray(detail.fields)){return detail.fields.map(item=>item?.message).filter(Boolean).join(' ');}if(Array.isArray(detail)){const names={employee_id:'缺勤员工',leave_start_date:'开始日期',leave_end_date:'结束日期',leave_days:'缺勤天数',proof:'缺勤证明'};const fields=[...new Set(detail.map(item=>item?.loc?.[item.loc.length-1]).filter(Boolean))];return fields.length?`${fields.map(field=>`${names[field]||field}未成功提交`).join('、')}，请检查后重试。`:'';}return ''}
function apiFallbackMessage(status){const messages={400:'提交内容不符合要求，请检查页面提示后重试。',403:'当前账号没有执行此操作的权限，请确认操作范围或联系管理员。',404:'所需记录不存在，或已被其他人处理，请刷新页面后重试。',409:'数据刚刚被其他操作更新，请刷新页面确认最新状态后再试。',413:'上传文件过大，请选择不超过100MB的业务材料后重试。',422:'提交内容不完整，请确认已选择员工、日期和必填材料后重试。',429:'操作过于频繁，请稍候再试。',500:'系统暂时无法处理本次操作，请稍后重试；若持续发生请记录操作时间并联系管理员。',502:'服务正在恢复，请稍后重试。',503:'服务暂不可用，请稍后重试。'};return messages[status]||`请求失败（${status}），请稍后重试。`;}
async function readApiBody(res){
  let text='';
  try{ text=await res.text(); }
  catch(error){ if(error?.name==='AbortError')throw error; throw new Error('读取服务器响应失败，请稍后重试。'); }
  if(!text) return {};
  try{ return JSON.parse(text); }
  catch{ throw new Error('服务器返回了无法解析的数据，请稍后重试。'); }
}
async function api(url, options={}) {
  const requestOptions={...options};
  if(!requestOptions.signal&&(!requestOptions.method||requestOptions.method==='GET')&&renderAbortController)requestOptions.signal=renderAbortController.signal;
  let res;
  try { res = await fetch(portalPath(url), requestOptions); }
  catch(error) { if(error?.name==='AbortError')throw error; throw new Error('网络连接失败，请检查网络后重试；若持续发生请记录操作时间并联系管理员。'); }
  if (res.status === 401) { location.href=portalPath('/login'); throw new Error('登录已失效'); }
  const body = await readApiBody(res);
  if (!res.ok) { const detail=body.detail; const fallback=apiFallbackMessage(res.status); const error=new Error(typeof detail==='object'?(detail.message||validationErrorMessage(detail)||fallback):(detail||fallback)); error.detail=detail; error.status=res.status; throw error; }
  return body;
}
const json = (method, body) => ({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
function installScreenWatermark(){let layer=document.getElementById('screenWatermark');if(!layer){layer=document.createElement('div');layer.id='screenWatermark';layer.className='screen-watermark';layer.setAttribute('aria-hidden','true');document.body.appendChild(layer)}const label=`内部资料 · ${state.me.employee_no}`;const count=window.matchMedia('(max-width: 760px)').matches?8:12;layer.innerHTML=Array.from({length:count},()=>`<span>${esc(label)}</span>`).join('')}
function installPageBindHint(){let el=document.getElementById('pageBindHint');if(!el){const badge=document.getElementById('userBadge');const host=document.querySelector('.app-identity');if(!badge&&!host)return;el=document.createElement('span');el.id='pageBindHint';if(badge)badge.after(el);else host.appendChild(el)}const now=new Date(),p=n=>String(n).padStart(2,'0');el.textContent=`${state.me.name}(${state.me.employee_no}) · ${p(now.getMonth()+1)}-${p(now.getDate())} ${p(now.getHours())}:${p(now.getMinutes())}`;el.hidden=false}

let screenshotNoticePending=false;
async function recordScreenshotKey(){if(screenshotNoticePending)return;screenshotNoticePending=true;try{const result=await api('/api/security/screenshot-event',{method:'POST'});await noticeModal('截图安全提醒',`<p>${esc(result.message)}</p>`)}catch(error){await noticeModal('截图安全提醒','<p>检测到截图按键，请勿分享页面数据。记录提交失败，请联系管理员。</p>')}finally{setTimeout(()=>{screenshotNoticePending=false},1200)}}
const handleScreenshotKey=event=>{if(event.key==='PrintScreen')recordScreenshotKey()};
window.addEventListener('keydown',handleScreenshotKey);
window.addEventListener('keyup',handleScreenshotKey);
const opt = (rows, value='id', label='name') => rows.map(x=>`<option value="${esc(x[value])}">${esc(typeof label==='function'?label(x):x[label])}</option>`).join('');
const statusBadge = row => `<span class="badge status-chip ${row.status==='confirmed'||row.status==='issued'||(row.record_type==='sick_leave'&&row.status==='active')?'ok':row.status==='rejected'||row.status==='material_failed'||(row.record_type==='deduction'&&row.status==='active')?'danger':'warn'}">${esc(row.status_name || row.status)}</span>`;
function splitCoveredSickRecords(rows){
  const visible=[],covered=[];
  for(const row of rows)(row.record_type==='sick_leave'&&row.status==='covered'?covered:visible).push(row);
  return {visible,covered};
}
function coveredSickDisclosure(rows,content){
  return rows.length?`<details class="covered-sick-disclosure"><summary>已覆盖病假（${rows.length} 条）<span aria-hidden="true"></span></summary><div class="covered-sick-content">${content}</div></details>`:'';
}
const sameDayDuplicateBadge = row => row.same_day_duplicate?` <span class="badge warn same-day-duplicate" title="同一认可日期、员工、认可类型和认可人已有其他有效记录">${esc(row.same_day_duplicate_label||'今日已有同类登记')}</span>`:'';
function employeePicker(label,key,usage,searchHint='输入姓名或工号查找全部在职CM/TR',endpoint='',targetScope=''){const circles=state.options.employee_circles||state.options.attractions||[];const supervisorScope=targetScope==='supervisor';const global=supervisorScope||['loa','password_reset','absence_backup'].includes(usage)||(['SUPERVISOR','TA_GSM','GSM'].includes(state.me.role_code)&&['deduction','attendance'].includes(usage));const hint=supervisorScope?'可搜索范围：全部景点圈在职主管，不含代理TA GSM期间的主管（必须输入姓名或员工号）':['loa','absence_backup'].includes(usage)?'可搜索范围：全部景点圈在职员工（必须输入姓名或员工号）':usage==='password_reset'?'请输入姓名或工号搜索可重置密码的员工':global?'可搜索范围：全部景点圈在职CM/TR（必须输入姓名或员工号）':searchHint;return `<div class="employee-picker" data-employee-picker data-target-scope="${esc(targetScope)}" data-global-search="${global?'1':'0'}" data-employee-endpoint="${esc(endpoint)}" data-usage="${esc(usage)}" data-search-hint="${esc(hint)}" data-empty-label="${esc(`没有找到符合条件的${hint.replace('输入姓名或工号查找','')}`)}"><span class="employee-picker-label">${esc(label)}</span><div class="employee-picker-filters"><label>员工景点圈<select data-employee-attraction><option value="">全部景点圈</option>${opt(circles)}</select></label><label>搜索员工<input id="${key}Search" data-employee-search autocomplete="off" inputmode="search" placeholder="输入姓名或工号"></label></div><input type="hidden" name="employee_id" id="${key}Value"><div id="${key}Results" class="employee-search-results" hidden></div><div id="${key}Selected" class="employee-selected" hidden></div><span id="${key}Hint" class="field-hint">${esc(hint)}</span></div>`;}
function circleTransferEmployeePicker(){const scope=state.me.role_code==='HR_CIRCLE'?`可搜索范围：${state.me.attraction_name||'所属'}景点圈的在职CM/TR`:'可搜索范围：全部景点圈的在职CM/TR';return `<div class="employee-picker" data-employee-picker data-usage="circle_transfer" data-search-hint="输入姓名或工号查找可调动员工"><span class="employee-picker-label">员工</span><div class="employee-picker-filters"><label>搜索员工<input id="circleTransferEmployeeSearch" data-employee-search autocomplete="off" inputmode="search" placeholder="输入姓名或工号"><small class="field-hint employee-search-scope">${esc(scope)}</small></label></div><input type="hidden" name="employee_id" id="circleTransferEmployeeValue"><div id="circleTransferEmployeeResults" class="employee-search-results" hidden></div><div id="circleTransferEmployeeSelected" class="employee-selected" hidden></div><span id="circleTransferEmployeeHint" class="field-hint">输入姓名或工号查找可调动员工</span></div>`;}
function bindEmployeeSearches(root){root.querySelectorAll('[data-employee-picker]').forEach(picker=>{const input=picker.querySelector('[data-employee-search]'),attraction=picker.querySelector('[data-employee-attraction]'),target=picker.querySelector('[name=employee_id]'),results=picker.querySelector('.employee-search-results'),selected=picker.querySelector('.employee-selected'),hint=picker.querySelector(`#${input.id.replace('Search','Hint')}`)||picker.querySelector('.field-hint'),usage=picker.dataset.usage,endpoint=picker.dataset.employeeEndpoint,searchHint=picker.dataset.searchHint||'输入姓名或工号查找全部在职CM/TR',emptyLabel=picker.dataset.emptyLabel||'没有找到符合条件的在职CM/TR',globalSearch=picker.dataset.globalSearch==='1';let timer=null,controller=null,sequence=0;if(state.me.attraction_id&&attraction&&!globalSearch)attraction.value=String(state.me.attraction_id);const clearSelected=()=>{target.value='';delete target.dataset.attractionId;delete target.dataset.dutyRoleCode;delete target.dataset.loaActive;delete target.dataset.loaStartsOn;delete target.dataset.loaEndsOn;target.dispatchEvent(new Event('change'));selected.hidden=true;selected.innerHTML='';input.readOnly=false;};const closeResults=()=>{results.hidden=true;results.innerHTML='';};const choose=row=>{target.value=String(row.id);target.dataset.attractionId=String(row.attraction_id||'');target.dataset.dutyRoleCode=row.duty_role_code||'';target.dataset.loaActive=row.loa_active?'1':'';target.dataset.loaStartsOn=row.loa_starts_on||'';target.dataset.loaEndsOn=row.loa_ends_on||'';target.dispatchEvent(new Event('change'));input.value=`${row.name} · ${row.employee_no}`;input.readOnly=true;closeResults();const loaNote=row.loa_active?` · LOA：${esc(row.loa_starts_on)}${row.loa_ends_on?` 至 ${esc(row.loa_ends_on)}`:' 起'}`:'';selected.innerHTML=`<div><strong>${esc(row.name)}</strong><span class="badge">${esc(row.role_name)}</span><small>${esc(row.employee_no)} · ${esc(row.attraction_name||'未分配景点圈')}${row.group_name?` · ${esc(row.group_name)}`:''}${loaNote}</small></div><button type="button" class="secondary" data-employee-clear>重新选择</button>`;selected.hidden=false;selected.querySelector('[data-employee-clear]').onclick=()=>{clearSelected();input.value='';closeResults();hint.textContent=searchHint;input.focus();};hint.textContent=row.loa_active?(row.loa_ends_on?`该员工当前处于LOA，至 ${row.loa_ends_on}。`:`该员工已进入LOA：${row.loa_starts_on}，请填写结束日期。`):'已选择员工';};const renderRows=data=>{const rows=data.items||[];if(!rows.length){results.innerHTML=`<div class="employee-search-empty">${esc(emptyLabel)}</div>`;results.hidden=false;hint.textContent='没有匹配员工';return;}results.innerHTML=rows.map((row,index)=>`<button type="button" class="employee-search-result" data-result-index="${index}"><span><strong>${esc(row.name)}</strong><em>${esc(row.role_name)}${row.loa_active?' · LOA':''}</em></span><small>${esc(row.employee_no)} · ${esc(row.attraction_name||'未分配景点圈')}${row.group_name?` · ${esc(row.group_name)}`:''}${row.loa_active?` · LOA：${esc(row.loa_starts_on)}${row.loa_ends_on?` 至 ${esc(row.loa_ends_on)}`:' 起'}`:''}</small></button>`).join('');results.hidden=false;results.querySelectorAll('[data-result-index]').forEach(button=>button.onclick=()=>choose(rows[Number(button.dataset.resultIndex)]));hint.textContent=data.total>rows.length?`找到${data.total}名，显示前${rows.length}名，请继续输入缩小范围`:`找到${data.total}名员工`;};const search=async()=>{const requestId=++sequence,keyword=input.value.trim();if(!keyword){closeResults();hint.textContent=searchHint;return;}controller?.abort();controller=new AbortController();results.innerHTML='<div class="employee-search-empty">正在搜索…</div>';results.hidden=false;hint.textContent='正在搜索';const qs=new URLSearchParams({keyword,limit:'30'});if(!endpoint)qs.set('usage',usage);if(picker.dataset.targetScope)qs.set('scope',picker.dataset.targetScope);if(attraction?.value)qs.set('attraction_id',attraction.value);try{const data=await api((endpoint||'/api/employee-targets')+'?'+qs,{signal:controller.signal});if(requestId===sequence)renderRows(data);}catch(error){if(error.name!=='AbortError'&&requestId===sequence){closeResults();hint.textContent='搜索失败，请稍后重试';toast(error.message,true);}}};const schedule=()=>{clearSelected();clearTimeout(timer);timer=setTimeout(search,120);};input.addEventListener('input',schedule);attraction?.addEventListener('change',()=>{clearSelected();if(input.value.trim())schedule();else closeResults();});});}
function requireEmployeeSelection(form){const target=form.querySelector('[name=employee_id]');if(!target||target.value)return true;toast('请先搜索并选择员工',true);form.querySelector('[data-employee-search]')?.focus();return false;}

// 本职计分（CM/TR代理TA主管、主管含代理TA GSM）但菜单按管理角色显示时，补上本人成绩入口。
function isActingFrontline(){return ['CM','TR','SUPERVISOR'].includes(state.me?.base_role_code)&&!['CM','TR'].includes(state.me?.role_code);}
function menuItems() {
  const r=state.me.role_code, items=[];
  if (['CM','TR'].includes(r)) items.push(['home','首页'],['register','登记']);
  else if (['TA_SUPERVISOR','SUPERVISOR'].includes(r)) items.push(['review','复核'],['members','组员记录'],['register','绩效登记'],['entries','主管登记记录']);
  else if (['TA_GSM','GSM'].includes(r)) {items.push(['statistics',has('DATA_EXPORT')?'景点数据与导出':'景点数据查看'],['prRankings','PR排名数据'],['register','绩效登记'],['entries','我的登记记录']);}
  else if (['AM','OM'].includes(r)) {items.push(['statistics','景点数据与导出'],['prRankings','PR排名数据'],['register','POC特别贡献']);}
  else if (r==='SYSTEM_ADMIN') items.push(['hrEmployees','员工管理'],['monthClose','月结'],['circleHrAccounts','景点圈HR账号'],['hrGroups','小组管理'],['circleTransfers','跨圈调动'],['logs','审计日志'],['hrScores','分值设置']);
  else if (r==='HR_CIRCLE') {items.push(['hrEmployees','员工管理'],['monthClose','月结'],['hrGroups','小组管理'],['circleTransfers','跨圈调动'],['logs','审计日志'],['hrScores','分值设置']);}
  else if (r==='HR_ADMIN') {items.push(['hrEmployees','员工管理'],['hrGroups','小组管理'],['circleTransfers','跨圈调动'],['logs','审计日志'],['hrScores','分值设置']);}
  else if (has('DATA_VIEW')) items.push(['statistics',has('DATA_EXPORT')?'景点数据与导出':'景点数据查看']);
  if(has('DATA_VIEW')&&!items.some(([id])=>id==='statistics'))items.push(['statistics',has('DATA_EXPORT')?'景点数据与导出':'景点数据查看']);
  if(has('REVIEW_SUPERVISOR'))items.splice(Math.min(items.length,1),0,['supervisorReview','主管复核']);
  if(has('LOA_REGISTER'))items.push(['loa','LOA登记']);
  if(has('SICK_LEAVE_IMPORT'))items.push(['sickLeaveImport','缺勤登记']);
  if(has('DECLARATION_STATS_VIEW'))items.push(['declarationStatistics','声明登记统计']);
  if(has('HR_MONTHLY_REPORT')&&['GSM','AM','OM','SYSTEM_ADMIN'].includes(r))items.push(['hrMonthlyReport','HR月报制作']);
  if(r==='SYSTEM_ADMIN')items.splice(5,0,['operations','系统运营']);
  if(isActingFrontline())items.unshift(['home','我的成绩']);
  items.splice(['CM','TR'].includes(r)?1:0,0,['actionCenter','待办']);
  if(state.me.rotation_entry)items.push(['rotationTest','轮岗（测试）']);
  items.push(['changelog','更新记录']);
  items.push(['password',has('PASSWORD_RESET')?'密码管理':'修改密码']);
  return items;
}
// 手机底部栏按角色固定最常用的入口（不含「更多」），没有权限的项自动跳过，不足 4 个时按菜单顺序补齐。
const MOBILE_PRIMARY_TABS={
  CM:['actionCenter','home','register','password'],
  TR:['actionCenter','home','register','password'],
  TA_SUPERVISOR:['actionCenter','review','register','members'],
  ACTING_FRONTLINE:['actionCenter','review','register','home'],
  SUPERVISOR:['actionCenter','review','register','home'],
  TA_GSM:['actionCenter','statistics','register','entries'],
  GSM:['actionCenter','statistics','register','entries'],
  AM:['actionCenter','statistics','register','prRankings'],
  OM:['actionCenter','statistics','register','prRankings'],
  HR_ADMIN:['actionCenter','hrEmployees','logs','circleTransfers'],
  HR_CIRCLE:['actionCenter','hrEmployees','monthClose','logs'],
  SYSTEM_ADMIN:['actionCenter','hrEmployees','monthClose','logs'],
};
const MOBILE_PRIMARY_COUNT=4;
// 底部栏宽度有限，长名称用短名显示（无障碍标签和「更多」里仍用全名）。
const MOBILE_SHORT_LABELS={'景点数据与导出':'景点数据','景点数据查看':'景点数据','我的登记记录':'登记记录','主管登记记录':'登记记录','PR排名数据':'PR排名','POC特别贡献':'POC贡献'};
function mobilePrimaryIds(items){
  const available=new Set(items.map(([id])=>id));
  const ids=((['CM','TR'].includes(state.me.base_role_code)&&isActingFrontline()?MOBILE_PRIMARY_TABS.ACTING_FRONTLINE:MOBILE_PRIMARY_TABS[state.me.role_code])||['actionCenter']).filter(id=>available.has(id));
  for(const [id] of items){ if(ids.length>=MOBILE_PRIMARY_COUNT) break; if(!ids.includes(id)&&!NAV_FOOTER_IDS.includes(id)) ids.push(id); }
  return ids.slice(0,MOBILE_PRIMARY_COUNT);
}
function userIdentityParts(){
  const me=state.me;
  return {name:me.name,employeeNo:me.employee_no,role:me.role_label||me.role_name,circle:me.attraction_name||'',members:me.member_count?`${me.member_count}名组员`:''};
}
function renderUserBadge(){
  const badge=document.getElementById('userBadge');
  if(!badge) return;
  const p=userIdentityParts();
  // 手机上只显示「姓名 · 景点圈」（无景点圈时显示角色），完整身份在「更多」里查看。
  // 徽章是 flex 容器，分段首尾的普通空格会被吞掉，分隔符两侧用不换行空格。
  const sep=' · ';
  badge.innerHTML=`<span class="badge-part">${esc(p.name)}</span><span class="badge-part${p.circle?' badge-part-optional':''}">${sep}${esc(p.role)}</span>${p.circle?`<span class="badge-part">${sep}${esc(p.circle)}</span>`:''}${p.members?`<span class="badge-part badge-part-optional">${sep}${esc(p.members)}</span>`:''}`;
  badge.title=[p.name,p.role,p.circle,p.members].filter(Boolean).join(' · ');
}
function mobileIdentityCard(){
  const p=userIdentityParts();
  const rows=[['姓名',p.name],['工号',p.employeeNo],['角色',p.role],['景点圈',p.circle],['组员',p.members]].filter(([,v])=>v);
  return `<section class="mobile-identity-card" aria-label="我的身份"><dl>${rows.map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl></section>`;
}
const NAV_FOOTER_IDS=['changelog','password'];
const NAV_GROUP_DEFS=[
  {id:'work',label:'工作',ids:['home','review','supervisorReview','register','absence','members','entries']},
  {id:'data',label:'数据',ids:['statistics','prRankings','declarationStatistics','sickLeaveImport','hrMonthlyReport']},
  {id:'people',label:'人事',ids:['hrEmployees','circleHrAccounts','hrGroups','circleTransfers','loa']},
  {id:'close',label:'结算',ids:['monthClose','hrScores']},
  {id:'govern',label:'治理',ids:['logs']},
  {id:'system',label:'系统',ids:['operations']},
  {id:'rotation',label:'轮岗',ids:['rotationTest']},
];
function navIcon(id){
  const inner={
    home:'<path d="M4 11 12 4l8 7"/><path d="M6 10.5V20h4.5v-6h3V20H18v-9.5"/>',
    actionCenter:'<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 10h8M8 14h5"/>',
    register:'<circle cx="12" cy="12" r="8"/><path d="M12 8v8M8 12h8"/>',
    review:'<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 12.5 10.5 15l5.5-6"/>',
    supervisorReview:'<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 12.5 10.5 15l5.5-6"/>',
    members:'<circle cx="9" cy="8" r="3"/><circle cx="17" cy="9" r="2.4"/><path d="M3.6 19c.6-3.2 2.8-5 5.4-5s4.8 1.8 5.4 5M14.2 14.4c2 .3 3.7 1.8 4.2 4.6"/>',
    absence:'<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3v4M16 3v4M4 10h16M9.5 14.5l5 5M14.5 14.5l-5 5"/>',
    loa:'<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3v4M16 3v4M4 10h16M9 14h6M9 17h4"/>',
    entries:'<rect x="6" y="3" width="12" height="18" rx="2"/><path d="M9 8h6M9 12h6M9 16h4"/>',
    statistics:'<path d="M4 19h16M7 16V11M12 16V8M17 16v-5"/>',
    declarationStatistics:'<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 9h8M8 13h8M8 17h5"/>',
    prRankings:'<path d="M8 20h8M12 16v4M7 4h10v5a5 5 0 0 1-10 0V4zM7 6H4.8A3.2 3.2 0 0 0 8 10.2M17 6h2.2A3.2 3.2 0 0 1 16 10.2"/>',
    hrEmployees:'<circle cx="12" cy="8" r="3.4"/><path d="M5 20c1.1-3.6 3.6-5.5 7-5.5s5.9 1.9 7 5.5"/>',
    monthClose:'<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M8 3v4M16 3v4M4 10h16M8.5 15.5 11 18l4.5-5"/>',
    circleHrAccounts:'<rect x="4" y="10" width="16" height="10" rx="1.5"/><path d="M8 10V7.5a4 4 0 0 1 8 0V10"/>',
    hrGroups:'<circle cx="8" cy="8" r="2.4"/><circle cx="16" cy="8" r="2.4"/><circle cx="12" cy="16" r="2.4"/><path d="M10 9.6 11.2 13.8M14 9.6 12.8 13.8"/>',
    circleTransfers:'<path d="M8 7h11l-3.2-3.2M16 17H5l3.2 3.2"/>',
    rotationTest:'<path d="M19 12a7 7 0 0 1-12.2 4.7M5 12a7 7 0 0 1 12.2-4.7"/><path d="M17.5 3.5v4h-4M6.5 20.5v-4h4"/>',
    logs:'<path d="M6 4h9l3 3v13H6z"/><path d="M15 4v3h3M8 12h8M8 16h5.5"/>',
    hrScores:'<path d="M4 7h10M4 12h16M4 17h7"/><circle cx="16.5" cy="7" r="2"/><circle cx="12.5" cy="17" r="2"/>',
    operations:'<circle cx="12" cy="12" r="3"/><path d="M12 3.5V6M12 18v2.5M5 6.6 6.8 8M17.2 16l1.8 1.4M5 17.4 6.8 16M17.2 8l1.8-1.4"/>',
    changelog:'<circle cx="12" cy="12" r="8"/><path d="M12 8v4.2L15 14"/>',
    password:'<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    more:'<circle cx="6" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="18" cy="12" r="1.5"/>',
    circle:'<circle cx="12" cy="12" r="8"/>',
  };
  return `<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner[id]||inner.circle}</svg>`;
}
function groupedMenu(items){
  const byId=new Map(items.map(item=>[item[0],item]));
  const used=new Set();
  const pin=byId.get('actionCenter')||null;
  if(pin) used.add('actionCenter');
  const groups=[];
  for(const def of NAV_GROUP_DEFS){
    const groupItems=def.ids.map(id=>byId.get(id)).filter(Boolean);
    if(!groupItems.length) continue;
    groupItems.forEach(([id])=>used.add(id));
    groups.push({id:def.id,label:def.label,items:groupItems});
  }
  const footer=NAV_FOOTER_IDS.map(id=>byId.get(id)).filter(Boolean);
  footer.forEach(([id])=>used.add(id));
  const leftover=items.filter(([id])=>!used.has(id));
  if(leftover.length) groups.push({id:'other',label:'其他',items:leftover});
  return {pin,groups,footer};
}
function isTabActive(id){return id===state.tab||(id==='statistics'&&state.tab==='statisticsDetail');}
function currentPageMeta(items){
  if(state.tab==='statisticsDetail') return {group:'数据',name:'绩效明细'};
  const grouped=groupedMenu(items);
  if(grouped.pin&&grouped.pin[0]===state.tab) return {group:'',name:grouped.pin[1]};
  for(const group of grouped.groups){
    const item=group.items.find(([id])=>id===state.tab);
    if(item) return {group:group.label,name:item[1]};
  }
  const foot=grouped.footer.find(([id])=>id===state.tab);
  if(foot) return {group:'',name:foot[1]};
  const fallback=items.find(([id])=>id===state.tab);
  return {group:'',name:fallback?fallback[1]:''};
}
function renderPageHeading(items){
  const heading=document.getElementById('pageHeading');
  if(!heading) return;
  const meta=currentPageMeta(items);
  if(!meta.name){heading.hidden=true;heading.innerHTML='';return;}
  heading.hidden=false;
  heading.innerHTML=`${meta.group?`<span class="page-heading-group">${esc(meta.group)}</span>`:''}<h1>${esc(meta.name)}</h1>`;
}
function syncAppBadge(total){
  const count=Math.max(0,Number(total)||0);
  try{
    if(count&&typeof navigator.setAppBadge==='function')navigator.setAppBadge(Math.min(count,99));
    else if(!count&&typeof navigator.clearAppBadge==='function')navigator.clearAppBadge();
  }catch(_){/* Browser and installed-app badge support is optional. */}
}
function setActionBadge(total){
  state.actionBadgeTotal=Math.max(0,Number(total)||0);
  document.querySelectorAll('[data-action-center-badge]').forEach(badge=>{
    badge.textContent=state.actionBadgeTotal>99?'99+':String(state.actionBadgeTotal);
    badge.hidden=state.actionBadgeTotal===0;
  });
  syncAppBadge(state.actionBadgeTotal);
}
async function refreshActionBadge(){
  if(!state.me||!menuItems().some(([id])=>id==='actionCenter'))return;
  const isUpgradeReviewer=['TA_GSM','GSM'].includes(state.me.role_code);
  try{
    const [data,upgradeData]=await Promise.all([
      api('/api/action-center'),
      isUpgradeReviewer?api('/api/deduction-upgrades/pending'):Promise.resolve({items:[]}),
    ]);
    setActionBadge(Number(data.total||0)+(upgradeData.items||[]).length);
  }catch(_){/* A badge refresh must never interrupt the existing page workflow. */}
}
function renderTabs() {
  const items=menuItems();
  if (!state.tab || (!items.some(i=>i[0]===state.tab) && state.tab!=='statisticsDetail')) state.tab=items[0]?.[0];
  const tabButton=([id,name],extraClass='')=>`<button type="button" data-tab="${id}" class="${isTabActive(id)?'active':''} ${extraClass}" ${isTabActive(id)?'aria-current="page"':''} title="${esc(name)}" aria-label="${esc(name)}">${navIcon(id)}<span class="nav-label">${esc(name)}</span>${id==='actionCenter'?`<b class="nav-count-badge" data-action-center-badge ${state.actionBadgeTotal?'':'hidden'}>${state.actionBadgeTotal>99?'99+':state.actionBadgeTotal}</b>`:''}</button>`;
  const grouped=groupedMenu(items);
  const desktopPin=grouped.pin?`<div class="nav-pin">${tabButton(grouped.pin)}</div>`:'';
  const desktopGroups=grouped.groups.map(group=>{
    const active=group.items.some(([id])=>isTabActive(id));
    return `<section class="nav-group${active?' is-active':''}" aria-label="${esc(group.label)}"><p class="nav-group-label">${esc(group.label)}</p>${group.items.map(item=>tabButton(item)).join('')}</section>`;
  }).join('');
  const desktopFooter=grouped.footer.length?`<div class="nav-footer">${grouped.footer.map(item=>tabButton(item)).join('')}</div>`:'';
  const ids=mobilePrimaryIds(items);
  const primary=ids.map(id=>items.find(i=>i[0]===id));
  const overflow=items.filter(i=>!ids.includes(i[0]));
  const overflowActive=overflow.some(([id])=>id===state.tab);
  const mobileTab=([id,name])=>tabButton([id,name]).replace(`<span class="nav-label">${esc(name)}</span>`,`<span class="nav-label">${esc(MOBILE_SHORT_LABELS[name]||name)}</span>`);
  tabs.innerHTML=`<div class="tabs-desktop">${desktopPin}<div class="nav-scroll">${desktopGroups}</div>${desktopFooter}</div><div class="tabs-mobile has-overflow">${primary.map(mobileTab).join('')}<button type="button" data-open-more class="${overflowActive?'active':''}" aria-label="打开更多功能" title="更多">${navIcon('more')}<span class="nav-label">更多</span></button></div><div class="mobile-more-drawer" hidden><div class="mobile-more-drawer-panel" role="dialog" aria-modal="true" aria-label="更多功能"><header><strong>更多功能</strong><button type="button" class="secondary" data-close-more>关闭</button></header>${mobileIdentityCard()}${overflow.length?`<div class="mobile-more-menu" role="group" aria-label="更多功能">${overflow.map(item=>tabButton(item,'mobile-more-item')).join('')}</div>`:''}<button type="button" class="secondary mobile-logout" data-mobile-logout>退出登录</button></div></div>`;
  document.body.classList.add('has-nav');
  renderPageHeading(items);
  const drawer=tabs.querySelector('.mobile-more-drawer');
  let drawerLayer=null;
  const closeDrawer=()=>{drawerLayer?.close();};
  tabs.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{state.tab=b.dataset.tab;closeDrawer();renderTabs();render();});
  tabs.querySelector('[data-open-more]')?.addEventListener('click',()=>{if(!drawer||!drawer.hidden)return;drawer.hidden=false;drawerLayer=bindDialogLayer(drawer,{root:drawer.querySelector('.mobile-more-drawer-panel')||drawer,initialFocus:drawer.querySelector('[data-close-more]'),remove:false,inertRoots:[document.getElementById('app'),document.getElementById('pageHeading'),tabs.querySelector('.tabs-desktop'),tabs.querySelector('.tabs-mobile')].filter(Boolean),onClose:()=>{drawerLayer=null;leaveLayer(drawer,()=>{drawer.hidden=true;});}});});
  tabs.querySelector('[data-close-more]')?.addEventListener('click',closeDrawer);
  tabs.querySelector('[data-mobile-logout]')?.addEventListener('click',logout);
}
let renderGeneration=0;
let viewRequestId=0;
let renderAbortController=null;
function beginViewRequest(){
  const id=++viewRequestId;
  const generation=renderGeneration;
  return {
    isCurrent(){return id===viewRequestId&&generation===renderGeneration;},
    write(html){if(!this.isCurrent())return false;app.innerHTML=html;return true;}
  };
}
const activePageTimers=new Set();
const activePageCleanups=new Set();
function pageTimeout(callback,delay){const timer=setTimeout(()=>{activePageTimers.delete(timer);callback();},delay);activePageTimers.add(timer);return timer;}
function registerPageCleanup(callback){activePageCleanups.add(callback);return callback;}
function clearPageResources(){activePageTimers.forEach(clearTimeout);activePageTimers.clear();activePageCleanups.forEach(callback=>{try{callback();}catch(_){}});activePageCleanups.clear();}
function passwordRuleState(password){return {minimum:4,length:password.length>=4&&password.length<=64};}
function passwordRuleMarkup(){const rules=[['length','至少4位']];return `<div class="password-rule-box" data-password-rules aria-live="polite"><strong>密码规则</strong><ul>${rules.map(([key,label])=>`<li data-password-rule="${key}"><span aria-hidden="true">○</span>${label}</li>`).join('')}</ul></div>`;}
function passwordFieldsMarkup(){return `<label>当前密码<input name="current_password" type="password" autocomplete="current-password" required></label><label>新密码<input name="new_password" type="password" minlength="4" maxlength="64" autocomplete="new-password" placeholder="请输入符合下方规则的新密码" required></label>${passwordRuleMarkup()}<label>确认新密码<input name="confirm_password" type="password" minlength="4" maxlength="64" autocomplete="new-password" required></label>`;}
function bindPasswordForm(form,onSuccess){const current=form.querySelector('[name=current_password]'),next=form.querySelector('[name=new_password]'),confirmPassword=form.querySelector('[name=confirm_password]'),button=form.querySelector('button[type=submit]'),rules=form.querySelector('[data-password-rules]');const update=()=>{const result=passwordRuleState(next.value);const required=['length'];rules.querySelectorAll('[data-password-rule]').forEach(item=>{const ok=Boolean(result[item.dataset.passwordRule]);item.classList.toggle('is-met',ok);item.querySelector('span').textContent=ok?'✓':'○';});const valid=required.every(key=>result[key])&&current.value.length>0&&confirmPassword.value.length>0&&next.value===confirmPassword.value;button.disabled=!valid;confirmPassword.setCustomValidity(confirmPassword.value&&next.value!==confirmPassword.value?'两次输入的新密码不一致':'');};[current,next,confirmPassword].forEach(input=>input.addEventListener('input',update));update();form.onsubmit=async event=>{event.preventDefault();if(button.disabled)return;try{await api('/api/password',json('POST',Object.fromEntries(new FormData(form))));onSuccess(form);}catch(error){toast(error.message,true)}};}
function renderPasswordChangeRequired(){
  tabs.innerHTML='';
  document.body.classList.remove('has-nav');
  const heading=document.getElementById('pageHeading');
  if(heading){heading.hidden=true;heading.innerHTML='';}
  app.innerHTML=`<section class="panel"><h2>首次登录请修改密码</h2><p>为保护账号安全，请先完成密码修改后再进入系统。</p><form id="requiredPasswordForm" class="form-stack password-form">${passwordFieldsMarkup()}<button type="submit" class="primary">保存并进入系统</button></form></section>`;
  bindPasswordForm(document.getElementById('requiredPasswordForm'),()=>location.reload());
}

async function render(){
  const generation=++renderGeneration;
  clearPageResources();
  renderAbortController?.abort();
  const controller=new AbortController();
  renderAbortController=controller;
  app.innerHTML='<section class="empty skeleton" aria-busy="true">正在加载…</section>';
  try {
    const views={home:renderHome,actionCenter:renderActionCenter,operations:renderOperations,register:renderRegister,absence:renderAbsence,entries:renderEntries,review:()=>{reviewApiBase='/api/reviews';return renderReview();},supervisorReview:()=>{reviewApiBase='/api/supervisor-reviews';return renderReview();},upgradeReview:renderUpgradeReview,members:renderMembers,statistics:renderStatistics,statisticsDetail:renderStatisticsDetail,declarationStatistics:renderDeclarationStatistics,prRankings:renderPrRankings,password:renderPasswordPage,accountReset:renderAccountReset,circleHrAccounts:renderCircleHrAccounts,hrEmployees:renderHrEmployees,monthClose:renderMonthClose,circleTransfers:renderCircleTransfers,hrGroups:renderHrGroups,hrScores:renderHrScores,logs:renderLogs,changelog:renderChangelog,sickLeaveImport:renderSickLeaveImport,loa:renderLoa};
    views.hrMonthlyReport=renderHrMonthlyReport;
    views.rotationTest=renderRotationTest;
    await (views[state.tab] || renderHome)();
    if(generation!==renderGeneration) return;
  } catch(e) {
    if(generation!==renderGeneration||e?.name==='AbortError') return;
    app.innerHTML=`<section class="panel"><div class="error">${esc(e.message)}</div></section>`;
  }
}

function actionCenterWaiting(hours){return `${Number(hours||0).toFixed(1)}小时`;}
function actionCenterDetailHtml(detail){
  const rows=detail.items||[];
  if(detail.type==='backup_health'){
    const manual=detail.manual_backup||{},manualText=manual.status==='running'?'手动备份正在执行…':manual.status==='completed'?`最近手动备份成功：${esc(manual.file_name||'已完成')}`:manual.status==='failed'?'最近手动备份失败，请联系服务器管理员检查日志。':manual.status==='interrupted'?'上次手动备份可能已中断，请核对备份目录后重试。':'尚未执行手动备份';
    return `<section class="panel action-center-detail"><div class="statistics-detail-heading"><div><h3>${esc(detail.title)}</h3><span>最近检查：${esc(detail.checked_at_utc||'暂无')}</span></div><button type="button" class="secondary" data-close-action-detail>关闭</button></div><ul class="operations-issues">${rows.map(row=>`<li><strong>${esc(row.code)}</strong>${row.message?`：${esc(row.message)}`:''}</li>`).join('')||'<li>当前没有异常项</li>'}</ul><p class="field-hint" data-manual-backup-status>${manualText}</p><p class="field-hint">重新备份仅创建一次数据库快照，不会修复缺失的每日计划任务；清除待办不删除备份，若异常持续，24小时后会再次提醒。</p><div class="actions"><button type="button" class="secondary" data-backup-dismiss>删除此待办</button><button type="button" class="primary" data-backup-retry ${manual.status==='running'?'disabled':''}>重新备份</button></div></section>`;
  }
  if(detail.type==='ungrouped_employee')return `<section class="panel action-center-detail"><div class="statistics-detail-heading"><div><h3>${esc(detail.title)}</h3><span>请在员工管理中为以下员工分配主管。</span></div><button type="button" class="secondary" data-close-action-detail>关闭</button></div><div class="table-wrap"><table><thead><tr><th>员工</th><th>角色</th><th>景点圈</th><th>未分组起算日</th><th>待分组时长</th><th>计算依据</th></tr></thead><tbody>${rows.map(row=>`<tr><td><strong>${esc(row.employee_name)}</strong><br><small>${esc(row.employee_no)}</small></td><td>${esc(row.role_name)}</td><td>${esc(row.attraction_name)}</td><td>${esc(row.unassigned_since||'—')}</td><td>${row.waiting_days===null||row.waiting_days===undefined?'—':`${Number(row.waiting_days)}天`}</td><td>${esc(row.waiting_basis||'—')}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">当前没有待分组员工</td></tr>'}</tbody></table></div></section>`;
  const overdue=detail.type==='overdue_review';
  return `<section class="panel action-center-detail"><div class="statistics-detail-heading"><div><h3>${esc(detail.title)}</h3><span>${overdue?'已筛选等待超过48小时的签卡。':'按提交时间从早到晚展示。'}</span></div><button type="button" class="secondary" data-close-action-detail>关闭</button></div><div class="table-wrap"><table><thead><tr><th>员工</th><th>景点圈</th><th>签卡</th><th>员工提交时间</th><th>等待复核</th><th>应复核人</th></tr></thead><tbody>${rows.map(row=>`<tr><td><strong>${esc(row.employee_name)}</strong><br><small>${esc(row.employee_no)}</small></td><td>${esc(row.attraction_name)}${row.group_name?`<br><small>${esc(row.group_name)}</small>`:''}</td><td>${esc(row.recognition_date)} · ${esc(row.recognition_type)}</td><td>${esc(row.submitted_at)}</td><td>${actionCenterWaiting(row.waiting_hours)}</td><td><strong>${esc(row.reviewer_name)}</strong>${row.reviewer_no?`<br><small>${esc(row.reviewer_no)}${row.reviewer_role_name?` · ${esc(row.reviewer_role_name)}`:''}</small>`:''}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">当前没有待复核签卡</td></tr>'}</tbody></table></div></section>`;
}

async function renderActionCenter(){
  const request=beginViewRequest();
  const isUpgradeReviewer=['TA_GSM','GSM'].includes(state.me.role_code);
  const [data,upgradeData]=await Promise.all([api('/api/action-center'),isUpgradeReviewer?api('/api/deduction-upgrades/pending'):Promise.resolve({items:[]})]);
  if(!request.isCurrent())return;
  const items=data.items||[],upgradeItems=upgradeData.items||[],total=Number(data.total||0)+upgradeItems.length;
  setActionBadge(total);
  const todoCards=items.map(item=>`<div class="action-center-item"><article class="action-card severity-${esc(item.severity)}"><div class="action-card-main"><span class="action-severity">${item.severity==='critical'?'优先处理':item.severity==='warning'?'请跟进':'待核对'}</span><strong>${esc(item.title)}</strong><p>${esc(item.description)}</p></div><div class="action-card-side"><div class="action-count"><b>${Number(item.count)}</b><span>项</span></div><button type="button" class="secondary" data-action-tab="${esc(item.tab)}" data-action-type="${esc(item.type)}" aria-expanded="false">立即查看</button></div></article><div class="action-center-inline-detail" hidden></div></div>`).join('')||'<section class="empty action-center-empty"><strong>当前没有待处理事项</strong><span>新的待办出现后会在这里提醒你。</span></section>';
  const reviewTab=isUpgradeReviewer?`<button type="button" class="secondary" data-action-pane-tab="review">待审核 <span class="badge warn">${upgradeItems.length}</span></button>`:'';
  const reviewPane=isUpgradeReviewer?`<section class="action-center-pane" data-action-pane="review" hidden><p class="field-hint">声明升级工单仅显示分配给当前账户的记录。备忘录和一级警告无需上传文件，处理说明必填。</p><div class="governance-case-list">${upgradeReviewCards(upgradeItems)}</div></section>`:'';
  if(!request.write(`<div class="section-gap action-center-page"><section class="panel action-center-heading"><div><span class="eyebrow">行动中心</span><h2>待办中心</h2><p>${esc(data.month)} · 当前共 <strong>${total}</strong> 项待处理事项</p></div><button type="button" class="secondary" id="refreshActionCenter" title="刷新待办中心">刷新待办</button></section><section class="panel action-center-panel"><div class="action-center-switch"><button type="button" class="primary" data-action-pane-tab="todo">待办 <span class="badge">${Number(data.total||0)}</span></button>${reviewTab}</div><section class="action-center-pane" data-action-pane="todo"><section class="action-center-list">${todoCards}</section></section>${reviewPane}</section></div>`))return;
  const setPane=name=>{app.querySelectorAll('[data-action-pane]').forEach(pane=>{pane.hidden=pane.dataset.actionPane!==name;});app.querySelectorAll('[data-action-pane-tab]').forEach(button=>{const active=button.dataset.actionPaneTab===name;button.classList.toggle('primary',active);button.classList.toggle('secondary',!active);});};
  app.querySelectorAll('[data-action-pane-tab]').forEach(button=>button.onclick=()=>setPane(button.dataset.actionPaneTab));
  document.getElementById('refreshActionCenter').onclick=renderActionCenter;
  const closeDetail=button=>{const host=button.closest('.action-center-item').querySelector('.action-center-inline-detail');host.innerHTML='';host.hidden=true;button.textContent='立即查看';button.setAttribute('aria-expanded','false');};
  app.querySelectorAll('[data-action-tab]').forEach(button=>button.onclick=async()=>{const detailTypes=new Set(['backup_health','ungrouped_employee','recognition_review','overdue_review']);if(!detailTypes.has(button.dataset.actionType)){state.tab=button.dataset.actionTab;renderTabs();render();return;}if(button.getAttribute('aria-expanded')==='true'){closeDetail(button);return;}button.disabled=true;try{const detail=await api('/api/action-center/'+encodeURIComponent(button.dataset.actionType)+'/details');if(!request.isCurrent())return;app.querySelectorAll('[data-action-tab][aria-expanded="true"]').forEach(closeDetail);const host=button.closest('.action-center-item').querySelector('.action-center-inline-detail');host.innerHTML=actionCenterDetailHtml(detail);host.hidden=false;button.textContent='收起';button.setAttribute('aria-expanded','true');host.querySelector('[data-close-action-detail]')?.addEventListener('click',()=>closeDetail(button));if(detail.type==='backup_health'){
    host.querySelector('[data-backup-dismiss]').onclick=async()=>{const confirmed=await confirmModal('删除备份待办','<p>仅移除当前这条待办，不删除备份文件；新巡检异常或24小时后会重新提醒。</p>','确认删除');if(!confirmed)return;try{await api('/api/admin/backup-health/dismiss',json('POST',{}));toast('当前备份待办已移除');renderActionCenter();void refreshActionBadge();}catch(error){toast(error.message,true)}};
    host.querySelector('[data-backup-retry]').onclick=async()=>{const confirmed=await confirmModal('重新备份数据库','<p>将在服务器后台创建一次新的 SQLite 快照；这不会修复缺失的每日计划任务。</p>','确认备份');if(!confirmed)return;const retryButton=host.querySelector('[data-backup-retry]');retryButton.disabled=true;try{await api('/api/admin/backup-health/retry',json('POST',{}));toast('手动备份已启动');const statusNode=host.querySelector('[data-manual-backup-status]');statusNode.textContent='手动备份正在执行…';for(let i=0;i<120&&host.isConnected;i++){await new Promise(resolve=>setTimeout(resolve,3000));if(!host.isConnected)break;const status=await api('/api/admin/backup-health/manual-status');if(status.status!=='running'){statusNode.textContent=status.status==='completed'?`手动备份成功：${status.file_name||'已完成'}`:'手动备份失败，请联系服务器管理员检查日志。';toast(status.status==='completed'?'手动备份完成':'手动备份失败',status.status!=='completed');retryButton.disabled=false;break;}}}catch(error){retryButton.disabled=false;toast(error.message,true)}};
  }host.scrollIntoView({behavior:'smooth',block:'nearest'});}catch(error){toast(error.message,true)}finally{button.disabled=false;}});
  if(isUpgradeReviewer)bindUpgradeReviewActions(app,renderActionCenter);
}

function operationsBackupHtml(backup){
  const latest=backup.latest_backup||{},issues=backup.issues||[],alert=backup.alert||{},task=backup.task||{};
  return `<section class="panel operations-backup ${backup.ok?'is-ok':'is-alert'}"><div class="operations-heading"><div><h2>备份健康</h2><p>最近检查：${esc(backup.checked_at_utc||'暂无健康报告')}</p></div><span class="badge ${backup.ok?'ok':'danger'}">${esc(backup.status||'未知')}</span></div><div class="operations-metrics"><div><span>备份任务</span><strong>${task.exists&&task.enabled?esc(task.state||'已启用'):'未就绪'}</strong></div><div><span>最近备份</span><strong>${esc(latest.created_at_utc||'暂无')}</strong></div><div><span>备份时效</span><strong>${latest.age_hours===undefined||latest.age_hours===null?'暂无':`${esc(latest.age_hours)}小时`}</strong></div><div><span>完整性</span><strong>${latest.quick_check==='ok'&&latest.sha256_verified?'已核验':'待核验'}</strong></div></div><p class="field-hint">外部通知：${alert.configured?'已配置':'未配置'}${alert.attempted?`；最近投递${alert.delivered?'成功':'失败'}`:''}</p>${issues.length?`<ul class="operations-issues">${issues.map(issue=>`<li><strong>${esc(issue.code)}</strong>${issue.message?`：${esc(issue.message)}`:''}</li>`).join('')}</ul>`:''}</section>`;
}

async function renderOperations(){
  const request=beginViewRequest();
  const data=await api('/api/admin/operations-health'),closures=data.month_closures||[],governance=data.governance||{};
  if(!request.isCurrent())return;
  if(!request.write(`<div class="section-gap">${operationsBackupHtml(data.backup||{})}<section class="panel"><div class="operations-heading"><div><h2>运营概览</h2><p>${esc(data.month)} 当前状态</p></div></div><div class="operations-metrics"><div><span>未处理系统告警</span><strong>${Number(data.open_system_alerts||0)}</strong></div><div><span>待确认跨圈调动</span><strong>${Number(data.pending_circle_transfers||0)}</strong></div><div><span>已关闭月结</span><strong>${closures.filter(row=>row.is_closed).length} / ${closures.length}</strong></div></div><div class="operations-closures">${closures.map(row=>`<div><strong>${esc(row.attraction_name)}</strong><span class="badge ${row.is_closed?'ok':'warn'}">${row.is_closed?'已月结':'未月结'}</span><small>${row.is_closed?`关闭人：${esc(row.closed_by_name||'系统')}`:'等待核对'}</small></div>`).join('')||'<span class="field-hint">暂无景点圈月结信息</span>'}</div></section><section class="panel"><div class="operations-heading"><div><h2>治理与留存复核</h2><p>仅显示聚合数量，不展示人员或附件内容。</p></div></div><div class="operations-metrics"><div><span>超过48小时待复核</span><strong>${Number(governance.overdue_recognition_reviews||0)}</strong></div><div><span>附件留存待复核</span><strong>${Number(governance.retention_review_files||0)}</strong></div></div></section></div>`))return;
}

async function renderChangelog(){
  const request=beginViewRequest();
  const data=await api('/api/changelog');
  if(!request.isCurrent())return;
  const releases=(data.releases||[]).map(release=>{
    const badge=release.current?'<span class="badge ok">当前版本</span>':release.status==='production'?'<span class="badge">生产</span>':release.status==='candidate'?'<span class="badge warn">候选</span>':'';
    const items=(release.items||[]).map(item=>`<li><strong>${esc(item.summary)}</strong>${item.detail?`<p>${esc(item.detail)}</p>`:''}</li>`).join('')||'<li>本版本对你的功能没有单独说明。</li>';
    return `<article class="changelog-release"><div class="record-line"><h3>V${esc(release.version)}</h3>${badge}<small>${esc(release.date||'')}</small></div><ul class="changelog-items">${items}</ul></article>`;
  }).join('')||'<div class="empty">暂无更新记录</div>';
  request.write(`<section class="panel changelog-page"><h2>更新记录</h2><p>当前系统 V${esc(data.app_version||'')} · ${esc(data.role_name||'')}。下面只列出和你这个角色相关的变更。</p>${releases}</section>`);
}

// 首页"我的信息"：组员显示所在小组与主管/代理主管；主管显示所带小组。
function homeGroupInfo(){
  const me=state.me,led=me.led_groups||[];
  if(me.base_role_code==='SUPERVISOR')return `<div><span>带组</span><strong>${led.length?led.map(group=>esc(group.name)).join('、'):'暂未带组'}</strong></div>`;
  const acting=led.filter(group=>group.leader_type==='acting').map(group=>esc(group.name)).join('、');
  return `<div><span>小组</span><strong>${esc(me.group_name||'未分组')}</strong>${me.group_leader_label?`<small class="field-hint">${esc(me.group_leader_label)}</small>`:''}${acting?`<small class="field-hint">代理主管：${acting}</small>`:''}</div>`;
}

async function renderHome(){
  const request=beginViewRequest();
  const visibleMonths=memberHomeMonths();
  const selectedMonth=visibleMonths.includes(state.homeMonth)?state.homeMonth:visibleMonths[0];
  const isHistoricalMonth=selectedMonth!==visibleMonths[0];
  state.homeMonth=selectedMonth;
  const selectedMonthLabel=memberHomeMonthLabel(selectedMonth);
  const d=await api('/api/dashboard?month='+encodeURIComponent(selectedMonth));
  if(!request.isCurrent())return;
  const cats=['安全','礼仪','包容','效率','演出'];
  if(!request.write(`<div class="section-gap">
    <section class="panel"><div class="home-info-heading"><h2>我的信息</h2><label class="home-month-control">数据月份<select id="homeMonthSelect" aria-label="查看绩效月份">${visibleMonths.map((month,index)=>`<option value="${month}" ${month===selectedMonth?'selected':''}>${memberHomeMonthLabel(month)}${index===0?'（本月）':''}</option>`).join('')}</select></label>${isHistoricalMonth?'<span class="badge warn home-read-only">仅可查看</span>':''}</div><div class="member-info-grid">
      <div><span>姓名 / 角色</span><strong>${esc(state.me.name)} · ${esc(state.me.role_label||state.me.role_name)}</strong></div>
      <div><span>景点圈</span><strong>${esc(state.me.attraction_name||'未分配')}</strong></div>
      ${homeGroupInfo()}
      <div class="member-score-summary"><div class="member-score-items">
        ${cats.map(x=>`<div class="member-score-item"><span>${x}</span><strong>${fmt(d.category_scores?.[x])}</strong></div>`).join('')}
        <div class="member-score-item"><span>全勤分</span><strong>${fmt(d.attendance_score)}</strong></div>
        <div class="member-score-item"><span>扣分</span><strong>${fmtDeduction(d.deduction_score)}</strong></div>
        <div class="member-score-item member-score-total"><span>${isHistoricalMonth?'当月总分':'本月总分'}</span><strong>${fmt(d.total_score)}</strong></div>
      </div></div>
    </div><details class="score-explainer"><summary>${isHistoricalMonth?`${selectedMonthLabel}绩效分怎么算的？`:'本月总分怎么算的？'}</summary><ul><li>加分：认可人签卡确认后，按认可人当日角色分值计入对应类别（安全/礼仪/包容/效率/演出等）。</li><li>全勤分：基础分 10 分；当月无病假满勤再加 2 分；病假按天扣减（0.5 天扣 0.25 分），最低 0 分。</li><li>扣分：声明/备忘录/警告等扣分记录按等级计分，作废后不再计入。</li><li>本月总分 = 加分 + 全勤分 − 扣分（仅统计已确认的有效记录）。</li></ul></details></section>
    <section class="panel"><h2>我的签卡${isHistoricalMonth?` · ${selectedMonthLabel}`:''}</h2><div class="mobile-records">${(d.records||[]).length?d.records.map((r,i)=>recognitionCard(r,i,isHistoricalMonth)).join(''):`<div class="empty">${isHistoricalMonth?`${selectedMonthLabel}暂无签卡`:'本月暂无签卡'}</div>`}</div></section>
  </div>`))return;
  document.getElementById('homeMonthSelect').onchange=event=>{state.homeMonth=event.target.value;renderHome();};
  bindImagePreviews(app);
  bindWithdraw(app, ()=>renderHome());
}
function recognitionCard(r,i,readOnly=false){return `<article class="record-card tone-${i%2}"><div class="record-line"><strong>${esc(r.recognition_date.slice(2).replaceAll('-','/'))} · ${esc(r.recognition_type)}</strong><span>${recognitionScoreText(r)}</span></div><div class="record-line"><span>${esc(r.recognizer_name)}：${esc(r.content)} ${r.entry_label?`<em>${esc(r.entry_label)}</em>`:''} ${sameDayDuplicateBadge(r)} ${r.image_url?imagePreviewButton(r.image_url,'查看图片'):''}</span><span class="status-action">${statusBadge(r)}${readOnly?'':`<button class="withdraw-icon" data-withdraw="${r.id}" aria-label="撤回签卡" title="撤回签卡">撤回</button>`}</span></div>${recognitionScoreNote(r)}${r.review_note?`<small>复核说明：${esc(r.review_note)}</small>`:''}</article>`;}
function renderPasswordPage(){
  if(has('PASSWORD_RESET'))return renderAccountReset();
  app.innerHTML=`<section class="panel"><h2>修改密码</h2><p>修改后请使用新密码重新登录其他设备。</p><form id="passwordPageForm" class="form-stack password-form">${passwordFieldsMarkup()}<button type="submit" class="primary">保存新密码</button></form></section>`;
  bindPasswordForm(document.getElementById('passwordPageForm'),form=>{toast('密码已更新');form.reset();form.querySelector('[name=current_password]')?.focus();form.querySelector('[name=new_password]').dispatchEvent(new Event('input'));});
}
function bindWithdraw(root, done){root.querySelectorAll('[data-withdraw]').forEach(b=>b.onclick=async()=>{if(!await confirmModal('撤回签卡','<p>撤回后该条记录不再计分，操作记录仅供高级别导出复查，是否撤回？</p>','确认撤回'))return;try{await api('/api/recognitions/'+b.dataset.withdraw,{method:'DELETE'});toast('已撤回');done();}catch(e){toast(e.message,true)}})}

async function renderRegister(){
  // 正式GSM、TA GSM可为主管加分扣分；代理TA GSM期间的主管只能本人登记，由AM复核。
  const canScoreFrontline=has('EMPLOYEE_ADD');
  const canScoreSupervisors=has('SUPERVISOR_SCORE')&&['GSM','TA_GSM'].includes(state.me.role_code);
  const canProxy=canScoreFrontline||canScoreSupervisors;
  const showRecognition=has('SELF_RECOGNITION')||canProxy;
  // 代理职务人员和主管既能本人登记，也能为员工代录，用切换按钮区分两种登记。
  const dualRecognition=has('SELF_RECOGNITION')&&canProxy;
  const selfMode=dualRecognition?state.registerMode==='self':!canProxy;
  const targetScope=!canScoreSupervisors?'frontline':!canScoreFrontline?'supervisor':(state.registerTarget==='supervisor'?'supervisor':'frontline');
  const supervisorTarget=!selfMode&&targetScope==='supervisor';
  const canIssuePoc=['TA_GSM','GSM','AM','OM'].includes(state.me.role_code);
  const employeeField=!selfMode?employeePicker(supervisorTarget?'被加分主管（必选）':'被加分员工（必选）','addEmployee','recognition','输入姓名或工号查找全部在职CM/TR','',supervisorTarget?'supervisor':''):'';
  const registerModeSwitch=dualRecognition?`<div class="action-center-switch register-mode-switch"><button type="button" class="${selfMode?'secondary':'primary'}" data-register-mode="proxy">为员工登记</button><button type="button" class="${selfMode?'primary':'secondary'}" data-register-mode="self">本人登记</button></div>`:'';
  const registerTargetSwitch=canScoreFrontline&&canScoreSupervisors&&!selfMode?`<div class="action-center-switch register-target-switch"><button type="button" class="${supervisorTarget?'secondary':'primary'}" data-register-target="frontline">登记CM/TR</button><button type="button" class="${supervisorTarget?'primary':'secondary'}" data-register-target="supervisor">登记主管</button></div>`:'';
  const imageField=selfMode?`<fieldset class="evidence-picker"><legend>认可图片（必传1张）</legend><div class="evidence-actions native-file-actions"><label class="secondary native-file-trigger" for="recognitionCameraInput">拍照</label><label class="secondary native-file-trigger" for="recognitionAlbumInput">从相册选择</label></div><input id="recognitionCameraInput" class="native-file-input" name="image_camera" data-evidence-input data-evidence-camera-input type="file" accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp" capture="environment"><input id="recognitionAlbumInput" class="native-file-input" name="image_album" data-evidence-input data-evidence-album-input type="file" accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp"><span class="field-hint" data-evidence-name>请选择一张JPG、PNG或WebP图片，最大100MB。若入口未打开，请直接点击另一个入口重试。</span></fieldset>`:'';
  const selfReviewHint=state.me.base_role_code==='SUPERVISOR'?((state.me.duty_role_codes||[]).includes('TA_GSM')?'代理TA GSM期间：本人提交后由AM复核，认可人可选择GSM、AM或OM。':'本人提交后由正式GSM复核，认可人只能选择TA GSM及以上。'):'本人提交后由所在小组的负责人复核（有代理主管时由代理主管复核），认可人不能选择本人。';
  const recognitionPanel=showRecognition?`<section class="panel"><h2>${selfMode?(dualRecognition?'本人登记':'快速登记'):(supervisorTarget?'主管加分登记':'员工加分登记')}</h2>${registerModeSwitch}${registerTargetSwitch}<p>${selfMode?selfReviewHint:'代录后默认通过，签卡人默认为当前账户。'}</p>
    <form id="recognitionForm" class="form-stack" enctype="multipart/form-data">${employeeField}<div class="grid two"><label>认可日期<input name="recognition_date" id="recognitionDate" type="date" value="${today()}" required></label><label>认可类型<select name="recognition_type_id" id="recognitionTypeSelect" required>${opt((state.options.recognition_types||[]).filter(row=>!row.dedicated_entry))}</select></label></div>
    <label>认可内容<input name="content" maxlength="20" required placeholder="请输入20字以内的认可内容"></label>${imageField}<label>发生景点<select name="occurred_attraction_id" id="attractionSelect" required><option value="">请选择</option>${opt(state.options.recognition_venues||[])}</select></label>
    <label>认可人 / 签卡人<div class="recognizer-picker" data-recognizer-picker><select name="recognizer_employee_id" id="recognizerSelect" class="recognizer-native-select" aria-hidden="true" tabindex="-1"><option value="">请先选择员工</option></select><button type="button" class="recognizer-trigger" aria-haspopup="listbox" aria-expanded="false" aria-controls="recognizerMenu"><span data-recognizer-value>请选择</span><span aria-hidden="true" class="recognizer-trigger-icon">▾</span></button><div id="recognizerMenu" class="recognizer-menu" role="listbox" hidden></div></div><span id="scoreHint" class="field-hint">可选择三个景点圈的TALEAD/LEAD或TAGSM及以上；HR账号不显示</span></label><div class="form-sticky-actions"><button class="primary">提交加分</button></div></form></section>`:'';
  const recognitionSection=showRecognition&&!selfMode&&!dualRecognition&&['TA_SUPERVISOR','SUPERVISOR'].includes(state.me.role_code)?`<details class="secondary-feature"><summary>其他功能 · 员工加分登记</summary>${recognitionPanel}</details>`:recognitionPanel;
  const pocPanel=canIssuePoc?`<section class="panel" data-poc-panel><h2>POC特别贡献</h2><p>仅TA GSM、GSM、AM、OM可开具；仅CM/TR、TA主管、主管可获得。POC独立计分，不受五类认可单项月度上限影响。</p><form id="pocRecognitionForm" class="form-stack" data-poc-form>${employeePicker('被认可员工（必选）','pocEmployee','poc','输入姓名或工号查找全部在职CM/TR、TA主管、主管')}<div class="grid two"><label>认可日期<input name="recognition_date" type="date" value="${today()}" required></label><label>认可周期<select name="poc_period_type" required><option value="month">月度</option><option value="quarter">季度</option></select></label></div><label>POC分值<select name="points" required><option value="">请选择</option><option value="1">+1 分</option><option value="2">+2 分</option><option value="3">+3 分</option><option value="4">+4 分</option><option value="5">+5 分</option></select></label><label>特别贡献原因<textarea name="poc_reason" maxlength="100" required placeholder="请填写特别贡献原因（最多100字）"></textarea></label><p class="field-hint">签卡人和代录人固定为当前登录账号；提交后直接计入POC独立加分。</p><div class="form-sticky-actions"><button class="primary">提交POC特别贡献</button></div></form></section>`:'';
  app.innerHTML=`<div class="section-gap">${recognitionSection}${pocPanel}${!selfMode&&(supervisorTarget||(targetScope==='frontline'&&(has('DEDUCTION_DIRECT')||has('DEDUCTION_ALL'))))?deductionForm(targetScope):''}</div>`;
  bindDateSubmissionFallbacks(app);
  bindEmployeeSearches(app);
  app.querySelectorAll('[data-register-mode]').forEach(button=>button.onclick=()=>{state.registerMode=button.dataset.registerMode;renderRegister();});
  app.querySelectorAll('[data-register-target]').forEach(button=>button.onclick=()=>{state.registerTarget=button.dataset.registerTarget;renderRegister();});
  const recognitionForm=document.getElementById('recognitionForm');
  if(recognitionForm){
    const evidenceInputs=[...recognitionForm.querySelectorAll('[data-evidence-input]')],evidenceName=recognitionForm.querySelector('[data-evidence-name]');evidenceInputs.forEach(input=>input.onchange=()=>{if(input.files?.length){evidenceInputs.filter(other=>other!==input).forEach(other=>{other.value='';});evidenceName.textContent=`已选择：${input.files[0].name}`;}});
    const attraction=document.getElementById('attractionSelect'), recognizer=document.getElementById('recognizerSelect'),recognitionType=document.getElementById('recognitionTypeSelect'),recognitionDate=document.getElementById('recognitionDate'),target=recognitionForm.querySelector('[name=employee_id]');let recognizerRows=[];
    function recognizerGroups(rows){const groups=new Map([['circle:heat',{label:'热力追踪主管',order:0,rows:[]}],['circle:dwarf',{label:'矮人迷宫主管',order:1,rows:[]}],['circle:bear',{label:'小熊罐子主管',order:2,rows:[]}],['tagsm_plus',{label:'TAGSM及以上',order:3,rows:[]}]]);rows.forEach(row=>{let key=row.group_key||'other';if(key.startsWith('circle:'))key=[...groups].find(([,group])=>group.label===row.group_label||group.label===`${row.group_label}主管`)?.[0]||key;const entry=groups.get(key)||{label:row.group_label||'其他',order:Number(row.group_order||99),rows:[]};entry.rows.push(row);groups.set(key,entry)});return [...groups.values()].sort((a,b)=>a.order-b.order);}
    function recognizerRowLabel(row){return `${row.name} · ${row.role_name} · ${fmt(row.score)}分`;}
    function renderRecognizerOptions(){const selectedType=state.options.recognition_types.find(x=>String(x.id)===recognitionType.value),specialCode=selectedType?.fixed_score!=null?selectedType.code:null;const creditedId=selfMode?state.me.id:Number(target?.value||0);const creditedIsSupervisor=selfMode?state.me.base_role_code==='SUPERVISOR':supervisorTarget;const creditedDuty=selfMode?(state.me.duty_role_codes||[])[0]||'':(target?.dataset.dutyRoleCode||'');const allowedRecognizer=x=>!creditedIsSupervisor||(creditedDuty==='TA_GSM'?['GSM','AM','OM'].includes(x.role_code):['TA_GSM','GSM','AM','OM'].includes(x.role_code));const rows=specialCode?recognizerRows.filter(x=>String(x.id)===`special:${specialCode}`):recognizerRows.filter(x=>!x.special&&x.id!==creditedId&&allowedRecognizer(x));recognizer.innerHTML='<option value="">请选择</option>'+opt(rows,'id',recognizerRowLabel);if(specialCode&&rows.length){recognizer.value=String(rows[0].id)}else if(!selfMode){const own=rows.find(x=>x.id===state.me.id);if(own)recognizer.value=String(own.id)}const picker=recognizer.closest('[data-recognizer-picker]'),trigger=picker.querySelector('.recognizer-trigger'),valueLabel=picker.querySelector('[data-recognizer-value]'),menu=picker.querySelector('.recognizer-menu');const closeMenu=()=>{menu.hidden=true;trigger.setAttribute('aria-expanded','false');};if(picker._closeRecognizerMenu)document.removeEventListener('click',picker._closeRecognizerMenu);picker._closeRecognizerMenu=event=>{if(!picker.contains(event.target))closeMenu();};document.addEventListener('click',picker._closeRecognizerMenu);registerPageCleanup(()=>document.removeEventListener('click',picker._closeRecognizerMenu));const syncTrigger=()=>{const selected=rows.find(row=>String(row.id)===recognizer.value);valueLabel.textContent=selected?recognizerRowLabel(selected):'请选择';};const choose=id=>{recognizer.value=String(id);syncTrigger();closeMenu();recognizer.dispatchEvent(new Event('change',{bubbles:true}));};const personButtons=items=>items.length?items.map(row=>`<button type="button" class="recognizer-person" role="option" data-recognizer-option="${esc(row.id)}">${esc(recognizerRowLabel(row))}</button>`).join(''):'<div class="recognizer-empty">暂无可选人员</div>';if(specialCode){menu.innerHTML=personButtons(rows);}else{menu.innerHTML=recognizerGroups(rows).map(group=>`<section class="recognizer-group"><button type="button" class="recognizer-group-toggle" aria-expanded="false">${esc(group.label)}<span aria-hidden="true">▸</span></button><div class="recognizer-group-list" hidden>${personButtons(group.rows)}</div></section>`).join('');menu.querySelectorAll('.recognizer-group-toggle').forEach(toggle=>toggle.onclick=()=>{const list=toggle.nextElementSibling,isOpen=!list.hidden;menu.querySelectorAll('.recognizer-group-list').forEach(other=>{other.hidden=true;other.previousElementSibling.setAttribute('aria-expanded','false');other.previousElementSibling.lastElementChild.textContent='▸';});if(!isOpen){list.hidden=false;toggle.setAttribute('aria-expanded','true');toggle.lastElementChild.textContent='▾';}});}menu.querySelectorAll('[data-recognizer-option]').forEach(button=>{button.setAttribute('aria-selected',String(button.dataset.recognizerOption===recognizer.value));button.onclick=()=>choose(button.dataset.recognizerOption);});trigger.onclick=()=>{if(menu.hidden){menu.hidden=false;trigger.setAttribute('aria-expanded','true');}else closeMenu();};trigger.onkeydown=event=>{if(event.key==='Escape'){closeMenu();return;}if(event.key==='Enter'||event.key===' '){event.preventDefault();trigger.click();}};recognizer.onchange=()=>{const r=rows.find(x=>String(x.id)===recognizer.value);syncTrigger();document.getElementById('scoreHint').textContent=r?(specialCode?`${r.name}固定 ${fmt(r.score)} 分${selectedType.monthly_limit?`；每名员工每月限${selectedType.monthly_limit}次`:''}`:`${r.group_label} · 当前角色分值：${fmt(r.score)} 分`):'分值按认可人当日角色自动计算';};recognizer.onchange();}
    // 快速切换日期时只采用最后一次请求的结果，避免较早返回的旧名单覆盖新名单。
    let recognizerRequest=0;
    async function loadRecognizers(circleId,dateValue=recognitionDate?.value){const requestId=++recognizerRequest;recognizer.innerHTML='<option>正在加载…</option>';const params=new URLSearchParams({attraction_id:circleId});if(dateValue)params.set('recognition_date',dateValue);const rows=await api('/api/recognizers?'+params);if(requestId!==recognizerRequest)return;recognizerRows=rows;renderRecognizerOptions();}
    recognitionType.onchange=()=>{if(recognizerRows.length)renderRecognizerOptions();};
    target?.addEventListener('change',()=>{if(recognizerRows.length)renderRecognizerOptions();});
    target?.addEventListener('change',()=>{const circleId=target.dataset.attractionId;if(circleId)loadRecognizers(circleId);else{recognizerRows=[];recognizer.innerHTML='<option value="">请先选择员工</option>';const picker=recognizer.closest('[data-recognizer-picker]');picker.querySelector('[data-recognizer-value]').textContent='请先选择员工';picker.querySelector('.recognizer-menu').hidden=true;picker.querySelector('.recognizer-trigger').setAttribute('aria-expanded','false');}}); recognitionDate?.addEventListener('change',()=>{const circleId=target?.dataset.attractionId||state.me.attraction_id;if(circleId)loadRecognizers(circleId);}); if(!target&&state.me.attraction_id) await loadRecognizers(state.me.attraction_id);
    recognitionForm.onsubmit=async e=>{e.preventDefault();if(!requireEmployeeSelection(e.target))return;const picker=recognizer.closest('[data-recognizer-picker]');if(!recognizer.value){toast('请选择认可人',true);picker.querySelector('.recognizer-trigger').focus();return;}await withSubmitLock(e.target,async()=>{try{const submit=confirmed=>{const data=recognitionSubmissionData(e.target,selfMode);requireSubmittedDate(data,recognitionDate,'认可日期');if(confirmed)data.set('same_day_duplicate_confirmed','true');return api('/api/recognitions',{method:'POST',body:data});};let result;try{result=await submit(false);}catch(x){if(x?.detail?.code!=='SAME_DAY_RECOGNITION_DUPLICATE')throw x;const previous=(x.detail.previous_records||[]).map(row=>`<li>${esc(row.submitted_at)}：${esc(row.content)}</li>`).join('');const accepted=await confirmModal('今日已有同类登记',`<p>${esc(x.message)}</p><ul>${previous}</ul>`,'确认是独立表现，继续登记');if(!accepted)return;result=await submit(true);}clearSubmissionKey(e.target);if(!showPerformanceRegistrationFeedback('recognition',result))showRecognitionEncouragement(result?.encouragement_options);e.target.querySelector('[name=content]').value='';evidenceInputs.forEach(input=>{input.value='';});if(evidenceName)evidenceName.textContent='请选择一张JPG、PNG或WebP图片，最大100MB。';}catch(x){toast(recognitionImageFailureMessage(x)||x.message,true)}})};
  }
  const pocForm=document.getElementById('pocRecognitionForm');
  if(pocForm)pocForm.onsubmit=async event=>{event.preventDefault();if(!requireEmployeeSelection(pocForm))return;await withSubmitLock(pocForm,async()=>{try{const data=submissionData(pocForm);requireSubmittedDate(data,pocForm.querySelector('[name=recognition_date]'),'认可日期');const out=await api('/api/recognitions/poc',{method:'POST',body:data});clearSubmissionKey(pocForm);if(!showPerformanceRegistrationFeedback('recognition',out))toast('POC特别贡献已登记并计分');pocForm.reset();pocForm.querySelector('[name=employee_id]').value='';pocForm.querySelector('.employee-selected').hidden=true;pocForm.querySelector('[data-employee-search]').value='';}catch(error){toast(error.message,true)}})};
  if(document.getElementById('deductionForm')){bindDeductionUpgradeHint();bindDeductionV2244();}
}
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
function deductionUpgradeGroup(row){if(!row?.repeat_check)return null;if(['ATT_EARLY_CLOCK','ATT_LATE_CLOCK'].includes(row.code))return {category:'考勤异常',items:'早打卡、晚打卡'};if(['ATT_LATE_WITHIN_30','ATT_EARLY_LEAVE_WITHIN_30'].includes(row.code))return {category:'迟到早退',items:'迟到30分钟内、早退30分钟内'};return {category:'同类型',items:row.name};}
function deductionUpgradeOptionName(row){const group=deductionUpgradeGroup(row);return group?`${row.name}（${group.category}3个月内第2次触发升级）`:row.name;}
function deductionUpgradeOptionMarkup(row){const group=deductionUpgradeGroup(row);return `${esc(row.name)}${group?`<small class="deduction-upgrade-note">（${esc(group.category)}3个月内第2次触发升级）</small>`:''}`;}
function bindDeductionTypePicker(select){
  if(select.closest('[data-deduction-type-picker]'))return;
  const rows=state.options.deduction_types||[],picker=document.createElement('div');
  picker.className='recognizer-picker deduction-type-picker';picker.dataset.deductionTypePicker='';
  select.before(picker);picker.append(select);select.classList.add('recognizer-native-select');select.setAttribute('aria-hidden','true');select.tabIndex=-1;
  picker.insertAdjacentHTML('beforeend',`<button type="button" class="recognizer-trigger" aria-label="扣分类型" aria-describedby="deductionUpgradeHint" aria-haspopup="listbox" aria-expanded="false" aria-controls="deductionTypeMenu"><span data-deduction-type-value></span><span aria-hidden="true" class="recognizer-trigger-icon">▾</span></button><div id="deductionTypeMenu" class="recognizer-menu" role="listbox" aria-label="扣分类型" hidden>${rows.map(row=>`<button type="button" class="recognizer-person" role="option" tabindex="-1" data-deduction-type-option="${esc(row.id)}">${deductionUpgradeOptionMarkup(row)}</button>`).join('')}</div>`);
  const trigger=picker.querySelector('.recognizer-trigger'),menu=picker.querySelector('.recognizer-menu'),value=picker.querySelector('[data-deduction-type-value]'),buttons=[...menu.querySelectorAll('[data-deduction-type-option]')];
  const close=()=>{menu.hidden=true;trigger.setAttribute('aria-expanded','false');};
  const sync=()=>{const row=rows.find(item=>String(item.id)===select.value);value.innerHTML=row?deductionUpgradeOptionMarkup(row):'请选择';buttons.forEach(button=>button.setAttribute('aria-selected',String(button.dataset.deductionTypeOption===select.value)));};
  const focusOption=index=>{const button=buttons[Math.max(0,Math.min(index,buttons.length-1))];button?.focus();};
  const open=()=>{menu.hidden=false;trigger.setAttribute('aria-expanded','true');focusOption(buttons.findIndex(button=>button.dataset.deductionTypeOption===select.value));};
  trigger.onclick=event=>{event.preventDefault();if(menu.hidden)open();else close();};
  trigger.onkeydown=event=>{if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();open();}else if(event.key==='Escape'){event.preventDefault();close();}};
  buttons.forEach((button,index)=>{
    button.onclick=event=>{event.preventDefault();select.value=button.dataset.deductionTypeOption;select.dispatchEvent(new Event('change',{bubbles:true}));close();trigger.focus();};
    button.onkeydown=event=>{
      if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();focusOption(index+(event.key==='ArrowDown'?1:-1));}
      else if(event.key==='Home'||event.key==='End'){event.preventDefault();focusOption(event.key==='Home'?0:buttons.length-1);}
      else if(event.key==='Escape'){event.preventDefault();close();trigger.focus();}
      else if(event.key==='Tab'){close();}
    };
  });
  const outside=event=>{if(!picker.contains(event.target))close();};document.addEventListener('click',outside);registerPageCleanup(()=>document.removeEventListener('click',outside));
  picker.addEventListener('focusout',event=>{if(!picker.contains(event.relatedTarget))close();});
  select.addEventListener('change',sync);select.form.addEventListener('reset',()=>queueMicrotask(()=>{close();sync();}));sync();
}
function deductionForm(targetScope='frontline'){const supervisorScope=targetScope==='supervisor';const levels=has('DEDUCTION_ALL')||supervisorScope?state.options.deduction_levels:state.options.deduction_levels.filter(x=>x.code==='STATEMENT'),types=state.options.deduction_types||[];return `<section class="panel"><h2>${supervisorScope?'主管扣分登记':'扣分登记'}</h2><p>${supervisorScope?'按姓名或员工号搜索主管；可登记全部等级。':'按姓名或员工号搜索CM/TR'}；未上传材料时可先登记为待补充，材料就绪后才扣分。</p><form id="deductionForm" class="form-stack" enctype="multipart/form-data">${employeePicker('被扣分员工（必选）','deductionEmployee','deduction','输入姓名或工号查找全部在职CM/TR','',supervisorScope?'supervisor':'')}<div class="grid two"><label>扣分类型<select name="deduction_type_id" id="deductionTypeSelect" aria-describedby="deductionUpgradeHint">${opt(types,'id',deductionUpgradeOptionName)}</select><span id="deductionUpgradeHint" class="field-hint" aria-live="polite"></span></label><label>扣分等级<select name="deduction_level_id" id="deductionLevelSelect">${levels.map(x=>`<option value="${x.id}" data-code="${esc(x.code)}">${esc(`${x.name} · ${fmt(x.points)}分`)}</option>`).join('')}</select></label></div><label>事件日期<input name="occurred_on" id="deductionOccurredOn" type="date" value="${today()}" required></label><div id="deductionRepeatWarning" class="repeat-warning" hidden></div><details class="form-more"><summary>更多选项</summary><label>事件说明<textarea name="description" required></textarea></label>${deductionMaterialPickerMarkup()}</details><div id="deductionMaterialState" aria-live="polite"></div><div class="form-sticky-actions"><button id="deductionSubmit" class="danger">提交扣分登记</button></div></form></section>`;}
function bindDeductionUpgradeHint(){const form=document.getElementById('deductionForm'),select=document.getElementById('deductionTypeSelect'),hint=document.getElementById('deductionUpgradeHint');if(!form||!select||!hint)return;bindDeductionTypePicker(select);const sync=()=>{const row=(state.options.deduction_types||[]).find(item=>String(item.id)===select.value),group=deductionUpgradeGroup(row);hint.textContent=group?`对应升级项：${group.items}。仅提示归类；选定员工及事件日期后，按该事件日期前后各3个月内已登记的有效声明判断是否触发升级。`:'该类型不参与此处的声明升级判断。';};select.addEventListener('change',sync);form.addEventListener('reset',()=>queueMicrotask(sync));sync();}
function sickForm(){return `<section class="panel"><h2>缺勤登记</h2><p>登记CM/TR病假缺勤；支持0.5天，半天扣0.25分。同一员工已有日期交集的缺勤记录时，不能再次提交。</p><form id="sickForm" class="form-stack" enctype="multipart/form-data">${employeePicker('缺勤员工（必选）','sickEmployee','attendance')}<div class="grid two"><label>开始日期<input name="leave_start_date" type="date" value="${today()}" required></label><label>结束日期<input name="leave_end_date" type="date" value="${today()}" required></label></div><p id="sickDateError" class="field-error" aria-live="polite" hidden></p><label>缺勤天数<input name="leave_days" type="number" step="0.5" min="0.5" max="1" value="1" required><span class="field-hint">可以按0.5天调整，但不能超过所选日期范围。</span></label><label>缺勤证明（图片/PDF）<input name="proof" type="file" accept="image/*,.pdf" required><span class="field-hint">必传；支持 JPG/JPEG、PNG、HEIC、WebP、PDF，单个文件不超过100MB。</span></label><details class="form-more"><summary>更多选项</summary><label>备注<input name="note"></label></details><div class="form-sticky-actions"><button class="warn">提交缺勤登记</button></div></form></section>`;}
function bindDeduction(){const form=document.getElementById('deductionForm'),employee=form.querySelector('[name=employee_id]'),type=form.querySelector('[name=deduction_type_id]'),level=form.querySelector('[name=deduction_level_id]'),occurred=form.querySelector('[name=occurred_on]'),warning=document.getElementById('deductionRepeatWarning'),submit=document.getElementById('deductionSubmit');const ranks={STATEMENT:1,MEMO:2,WARNING_1:3,WARNING_2:4};let repeatInfo=null,checkSequence=0;const resetLevels=()=>[...level.options].forEach(option=>option.disabled=false);const checkRepeat=async()=>{const sequence=++checkSequence;repeatInfo=null;resetLevels();warning.hidden=true;warning.innerHTML='';submit.disabled=form.dataset.submitting==='true';const selectedType=state.options.deduction_types.find(row=>String(row.id)===type.value);if(!employee.value||!occurred.value||!selectedType?.repeat_check)return null;try{const params=new URLSearchParams({employee_id:employee.value,deduction_type_id:type.value,occurred_on:occurred.value});const info=await api('/api/deductions/attendance-repeat-check?'+params);if(sequence!==checkSequence)return null;repeatInfo=info;if(info.has_repeat){const history=info.previous_records.map(row=>`<li>${esc(row.occurred_on)}：${esc(row.deduction_type)}，${esc(row.deduction_level)}（${fmt(row.points)}分）</li>`).join('');warning.className=`repeat-warning ${info.blocked?'blocked':'notice'}`;warning.innerHTML=`<strong>3个月内同类型重复提醒</strong><p>${esc(info.message)}</p><ul>${history}</ul>`;warning.hidden=false;if(info.blocked){submit.disabled=true;}else{const minimumRank=ranks[info.minimum_level_code]||1;[...level.options].forEach(option=>option.disabled=(ranks[option.dataset.code]||0)<minimumRank);if((ranks[level.selectedOptions[0]?.dataset.code]||0)<minimumRank)level.value=String(info.minimum_level_id);}}return info;}catch(error){if(sequence!==checkSequence)return null;warning.className='repeat-warning blocked';warning.innerHTML='<strong>暂时无法检查3个月内同类型记录，请稍后重试。</strong>';warning.hidden=false;submit.disabled=true;throw error;}};employee.addEventListener('change',()=>checkRepeat().catch(()=>{}));type.addEventListener('change',()=>checkRepeat().catch(()=>{}));occurred.addEventListener('change',()=>checkRepeat().catch(()=>{}));form.onsubmit=async e=>{e.preventDefault();if(!requireEmployeeSelection(form)||form.dataset.submitting==='true')return;try{const info=await checkRepeat();if(info?.blocked){toast(info.message,true);return;}const data=submissionData(form);if(info?.has_repeat){const history=info.previous_records.map(row=>`<div>${esc(row.occurred_on)}：${esc(row.deduction_type)}，${esc(row.deduction_level)}（${fmt(row.points)}分）</div>`).join('');const accepted=await confirmModal('3个月内同类型重复提醒',`<p>${esc(info.message)}</p>${history}`,'我已知晓，继续登记');if(!accepted)return;data.set('repeat_confirmed','true');}else if(!(await confirmModal('确认提交扣分','<p>扣分提交后立即生效，是否继续？</p>','确认扣分')))return;form.dataset.submitting='true';submit.disabled=true;await api('/api/deductions',{method:'POST',body:data});clearSubmissionKey(form);toast('扣分已生效');form.querySelector('[name=description]').value='';form.querySelector('[name=document]').value='';}catch(x){if(!(await handlePendingMaterialConflict(x)))toast(x.message,true)}finally{delete form.dataset.submitting;await checkRepeat().catch(()=>{});}};}
function inclusiveDays(start,end){if(!start||!end)return 0;const a=Date.UTC(...start.split('-').map((x,i)=>Number(x)-(i===1?1:0)));const b=Date.UTC(...end.split('-').map((x,i)=>Number(x)-(i===1?1:0)));return b<a?0:Math.floor((b-a)/86400000)+1;}
/* Legacy absence binder retained temporarily for source traceability. */
/*
function bindSick(){const form=document.getElementById('sickForm'),employee=form.querySelector('[name=employee_id]'),start=form.querySelector('[name=leave_start_date]'),end=form.querySelector('[name=leave_end_date]'),days=form.querySelector('[name=leave_days]'),proof=form.querySelector('[name=proof]'),note=form.querySelector('[name=note]');const allowedExtensions=new Set(['jpg','jpeg','png','heic','webp','pdf']),maxProofBytes=100*1024*1024;const syncDays=()=>{const total=inclusiveDays(start.value,end.value);if(total<1){days.value='';days.max='0.5';return;}days.max=String(total);days.value=String(total);};const proofError=()=>{const file=proof.files?.[0];if(!file)return '请先选择缺勤证明（图片或PDF）。';const extension=(file.name.split('.').pop()||'').toLowerCase();if(!allowedExtensions.has(extension))return '缺勤证明仅支持 JPG/JPEG、PNG、HEIC、WebP 或 PDF。';if(file.size<=0)return '缺勤证明未成功读取，请重新选择文件后提交。';if(file.size>maxProofBytes)return `缺勤证明“${file.name}”超过100MB，请重新选择。`;return '';};const proofPayloadError=data=>{const attached=data.get('proof');if(!attached||typeof attached.name!=='string'||!attached.name||!Number.isFinite(attached.size)||attached.size<=0)return '缺勤证明未成功上传，请重新选择文件后提交。';return '';};start.onchange=syncDays;end.onchange=syncDays;proof.onchange=()=>{const message=proofError();if(message)toast(message,true);};syncDays();form.onsubmit=async e=>{e.preventDefault();if(!requireEmployeeSelection(form))return;const proofMessage=proofError();if(proofMessage){toast(proofMessage,true);proof.focus();return;}if(!start.value||!end.value){toast('请选择完整的开始日期和结束日期。',true);return;}if(inclusiveDays(start.value,end.value)<1){toast('结束日期不能早于开始日期。',true);end.focus();return;}const enteredDays=Number(days.value);if(!Number.isFinite(enteredDays)||enteredDays<=0||Math.round(enteredDays*2)!==enteredDays*2){toast('缺勤天数必须按0.5天递增。',true);days.focus();return;}if(enteredDays>inclusiveDays(start.value,end.value)){toast('缺勤天数不能超过所选日期范围。',true);days.focus();return;}if(end.value>start.value){const accepted=await confirmModal('病假日期确认','<p>请确认您所提交的病假日期中不含演职人员本休。</p>','已确认，继续提交');if(!accepted)return;}await withSubmitLock(e.target,async()=>{try{const data=submissionData(e.target),file=proof.files?.[0];data.set('employee_id',employee.value);data.set('leave_start_date',start.value);data.set('leave_end_date',end.value);data.set('leave_days',days.value);data.set('note',note.value);data.set('proof',file,file.name);const payloadMessage=proofPayloadError(data);if(payloadMessage){toast(payloadMessage,true);proof.focus();return;}if(end.value>start.value)data.set('rest_day_confirmed','true');const r=await api('/api/sick-leaves',{method:'POST',body:data});clearSubmissionKey(e.target);toast(`缺勤登记成功，当月全勤分 ${fmt(r.attendance_score)}`);e.target.querySelector('[name=proof]').value='';}catch(x){toast(x.message||'缺勤登记失败，请检查所填内容后重试。',true)}})}}
 */
function bindSick(){
  const form=document.getElementById('sickForm');
  if(!form)return;
  const employee=form.querySelector('[name=employee_id]'),start=form.querySelector('[name=leave_start_date]'),end=form.querySelector('[name=leave_end_date]'),days=form.querySelector('[name=leave_days]'),proof=form.querySelector('[name=proof]'),note=form.querySelector('[name=note]'),violation=form.querySelector('[name=is_violation]');
  const allowedExtensions=new Set(['jpg','jpeg','png','heic','webp','pdf']),maxProofBytes=100*1024*1024;
  const syncDays=()=>{const total=inclusiveDays(start.value,end.value);if(total<1){days.value='';days.max='0.5';return;}days.max=String(total);days.value=String(total);};
  const proofError=()=>{const file=proof.files?.[0],extension=(file?.name.split('.').pop()||'').toLowerCase();if(!file)return '请先选择缺勤证明（图片或PDF）。';if(!allowedExtensions.has(extension))return '缺勤证明仅支持 JPG/JPEG、PNG、HEIC、WebP 或 PDF。';if(file.size<=0)return '缺勤证明未成功读取，请重新选择文件后提交。';if(file.size>maxProofBytes)return `缺勤证明“${file.name}”超过100MB，请重新选择。`;return '';};
  const appendViolationMaterial=data=>{const picker=form._violationMaterial;if(!picker?.hasMaterial())return '';const scratch=new FormData(),mode=picker.appendTo(scratch);for(const [key,value] of scratch.entries())data.append(key==='document'?'violation_document':'violation_document_images',value,value.name);return mode;};
  start.addEventListener('change',syncDays);end.addEventListener('change',syncDays);proof.addEventListener('change',()=>{const message=proofError();if(message)toast(message,true);});syncDays();
  form.onsubmit=async event=>{event.preventDefault();if(!requireEmployeeSelection(form))return;const proofMessage=proofError();if(proofMessage){toast(proofMessage,true);proof.focus();return;}if(!start.value||!end.value){toast('请选择完整的开始日期和结束日期。',true);return;}if(inclusiveDays(start.value,end.value)<1){toast('结束日期不能早于开始日期。',true);end.focus();return;}const enteredDays=Number(days.value);if(!Number.isFinite(enteredDays)||enteredDays<=0||Math.round(enteredDays*2)!==enteredDays*2){toast('缺勤天数必须按0.5天递增。',true);days.focus();return;}if(enteredDays>inclusiveDays(start.value,end.value)){toast('缺勤天数不能超过所选日期范围。',true);days.focus();return;}if(end.value>start.value&&!await confirmModal('病假日期确认','<p>请确认您所提交的病假日期中不含演职人员本休。</p>','已确认，继续提交'))return;
    await withSubmitLock(form,async()=>{try{const data=submissionData(form),file=proof.files?.[0];data.set('employee_id',employee.value);data.set('leave_start_date',start.value);data.set('leave_end_date',end.value);data.set('leave_days',days.value);data.set('note',note.value);data.set('proof',file,file.name);if(end.value>start.value)data.set('rest_day_confirmed','true');let materialMode='';if(violation?.checked){data.set('is_violation','true');const reviewer=form.querySelector('[name=violation_reviewer_id]');if(reviewer&&!reviewer.closest('[hidden]')&&!reviewer.value){toast('请先选择GSM或TA GSM审核人。',true);reviewer.focus();return;}if(reviewer?.value)data.set('violation_reviewer_id',reviewer.value);materialMode=appendViolationMaterial(data);}const result=await api('/api/sick-leaves',{method:'POST',body:data});clearSubmissionKey(form);proof.value='';if(!violation?.checked){toast(`缺勤登记成功，当月全勤分 ${fmt(result.attendance_score)}`);return;}form._violationMaterial?.reset();violation.checked=false;form._refreshViolation?.();if(!materialMode){await confirmModal('缺勤与违规病假已登记','<p>缺勤已生效；违规病假声明暂未上传材料，已进入“待补充材料”。</p><p><strong>暂不扣分。</strong>请在待办或主管登记记录中补充材料。</p>','我知道了');}else if(result.violation?.material_status==='processing'){toast('缺勤已登记；违规病假声明材料正在后台生成PDF，完成后将自动进入扣分或升级流程。');}else if(result.violation_upgrade){toast('缺勤与违规病假声明已登记，已提交GSM/TA GSM审核。');}else{toast('缺勤与违规病假声明已登记，声明扣分已生效。');}}catch(error){toast(error.message||'缺勤登记失败，请检查所填内容后重试。',true);}});
  };
}

function bindSickLeaveOverlapGuard(form){
  const employee=form.querySelector('[name=employee_id]'),start=form.querySelector('[name=leave_start_date]'),end=form.querySelector('[name=leave_end_date]'),error=document.getElementById('sickDateError');
  let lastSignature='',lastResult=null,sequence=0;
  const reset=()=>{lastSignature='';lastResult=null;};
  const signature=()=>employee.value&&start.value&&end.value&&inclusiveDays(start.value,end.value)>0?`${employee.value}:${start.value}:${end.value}`:'';
  const check=async({showInline=false}={})=>{
    const value=signature();
    if(!value){reset();return null;}
    if(value===lastSignature&&lastResult)return lastResult;
    const requestId=++sequence;
    const result=await api('/api/sick-leaves/overlap-check?'+new URLSearchParams({employee_id:employee.value,leave_start_date:start.value,leave_end_date:end.value}));
    if(requestId!==sequence)return null;
    lastSignature=value;lastResult=result;
    if(showInline&&result.conflict){error.textContent=result.detail.message;error.hidden=false;start.setCustomValidity(result.detail.message);end.setCustomValidity(result.detail.message);}
    return result;
  };
  const clearInline=()=>{if(!error.textContent.includes('已有')&&!error.textContent.includes('日期有交集'))return;error.hidden=true;error.textContent='';start.setCustomValidity('');end.setCustomValidity('');};
  const schedule=()=>{reset();clearInline();check({showInline:true}).catch(()=>{});};
  employee.addEventListener('change',schedule);start.addEventListener('change',schedule);end.addEventListener('change',schedule);
  form.addEventListener('submit',async event=>{
    if(form.dataset.overlapGuardBypass==='1'){delete form.dataset.overlapGuardBypass;return;}
    event.preventDefault();event.stopImmediatePropagation();
    if(!employee.value||!start.value||!end.value||inclusiveDays(start.value,end.value)<1){form.onsubmit?.(event);return;}
    try{
      const result=await check({showInline:true});
      if(result?.conflict){const rows=(result.detail.records||[]).map(row=>`<li>${esc(row.leave_start_date)} 至 ${esc(row.leave_end_date)}（${Number(row.leave_days).toFixed(1)}天）</li>`).join('');await confirmModal('已有缺勤登记',`<p>${esc(result.detail.message)}</p><ul>${rows}</ul><p>请先作废或更正已有记录后再提交。</p>`,'返回修改');return;}
    }catch(requestError){toast(requestError.message||'无法检查已有缺勤登记，请稍后重试。',true);return;}
    form.dataset.overlapGuardBypass='1';form.onsubmit?.(event);
  },true);
}

function bindDeductionV2244(){
  const form=document.getElementById('deductionForm'),submit=document.getElementById('deductionSubmit'),material=bindDeductionMaterialPicker(form),stateBox=document.getElementById('deductionMaterialState');
  form.onsubmit=async event=>{
    event.preventDefault();
    if(!requireEmployeeSelection(form)||form.dataset.submitting==='true')return;
    try{
      const data=submissionData(form),occurredOn=form.querySelector('[name=occurred_on]'),direct=has('DEDUCTION_DIRECT')&&!has('DEDUCTION_ALL'),materialMode=material.appendTo(data);
      requireSubmittedDate(data,occurredOn,'事件日期');
      if(direct&&materialMode){
        const preview=await api('/api/deduction-upgrades/preview?'+new URLSearchParams({employee_id:data.get('employee_id'),deduction_type_id:data.get('deduction_type_id'),occurred_on:data.get('occurred_on')}));
        if(preview.eligible){
          const first=preview.first_record;
          const reviewerId=await selectModal('可升级声明工单',`<p>该员工三个月内已有同类声明，将进入升级审核。</p><p><strong>历史声明：</strong>${esc(first.occurred_on)} · ${esc(first.deduction_type)} · ${esc(first.description)}</p><p>提交后本次声明不可撤回、不可作废；本次不计分，等待审核结果。</p><label>审核 MOD（GSM / TA GSM）</label>`,(preview.reviewers||[]).map(x=>({id:x.id,label:`${x.name} · ${x.employee_no} · ${x.role_name}`})),'提交工单');
          if(!reviewerId)return;
          data.set('reviewer_id',reviewerId);
          form.dataset.submitting='true';submit.disabled=true;
          const out=await api('/api/deduction-upgrades',{method:'POST',body:data});
          clearSubmissionKey(form);form.reset();material.reset();
          if(out.processing)showDeductionMaterialState(stateBox,out.record);
          if(!showPerformanceRegistrationFeedback('upgrade',out))toast(out.processing?'扣分登记已提交，材料正在生成PDF。生成结果请到“我的登记记录”查看。':'声明升级工单已提交，等待审核');
          return;
        }
      }
      if(!confirm(materialMode==='photos'?'确认提交照片材料？PDF生成成功后才会正式扣分。':materialMode==='pdf'?'确认提交PDF材料？系统会后台优化后生效。':'未上传材料：将创建待补充记录，TA主管、主管、TA GSM和GSM均可补充；在材料就绪前不扣分。是否继续？'))return;
      form.dataset.submitting='true';submit.disabled=true;
      const out=await api('/api/deductions',{method:'POST',body:data});clearSubmissionKey(form);form.reset();material.reset();
      if(out.record?.material_status==='processing')showDeductionMaterialState(stateBox,out.record);
      if(!showPerformanceRegistrationFeedback('deduction',out)){if(out.record?.material_status==='processing')toast('扣分登记已提交，材料正在后台处理。');else if(out.record?.material_status==='missing'){await confirmModal('声明已登记', '<p>该声明尚未上传材料，已登记为“待补充材料”。</p><p><strong>暂不扣分。</strong>请在“待办”或“我的登记记录”中点击“补充材料”完成上传。</p>', '我知道了');}else toast('扣分已生效');}
    }catch(error){if(!(await handlePendingMaterialConflict(error)))toast(error.message,true)}finally{delete form.dataset.submitting;submit.disabled=false;}
  };
}
function showDeductionMaterialState(container,record,onFinished){if(!container||!record)return;const render=current=>{const status=current.material_status||'unknown',businessStatus=current.status||'',businessActive=businessStatus==='active';const failed=status==='failed',ready=status==='ready',missing=status==='missing',processing=status==='processing';const title=failed?'材料生成失败':ready?'材料已就绪':missing?'待补充材料':processing?'材料生成中':'材料状态未知';const body=failed?esc(current.material_error||'照片生成PDF失败，请重新提交材料。'):ready?(businessActive?'正式PDF已生成，扣分记录已生效。':`正式PDF已生成；当前业务状态为“${esc(current.status_name||businessStatus||'待处理')}”，是否计分以业务状态为准。`):missing?'该记录尚未上传材料，暂不计分。':processing?'照片已接收，系统正在后台合成正式PDF；完成后请以业务状态确认是否计分。':'请刷新后查看最新材料状态。';const action=failed||missing?`<button type="button" class="secondary" data-retry-material="${current.id}">${failed?'重新提交材料':'补充材料'}</button>`:ready?'<span class="field-hint">可在我的登记记录中查看材料与业务状态。</span>':'<span class="field-hint">此页面会自动刷新状态。</span>';container.innerHTML=`<div class="material-submission-state ${failed?'failed':ready?'ready':processing?'processing':'pending'}"><strong>${title}</strong><p>${body}</p>${action}</div>`;container.querySelector('[data-retry-material]')?.addEventListener('click',()=>openDeductionMaterialRetry(current.id,updated=>{render(updated);if(updated.material_status==='processing')watchDeductionMaterial(updated,container,onFinished);}));};render(record);if(record.material_status==='processing')watchDeductionMaterial(record,container,onFinished);}
function watchDeductionMaterial(record,container,onFinished){let tries=0;const poll=async()=>{if(!container?.isConnected)return;try{const current=(await api('/api/deductions/'+record.id+'/material-status')).record;if(current.material_status==='processing'&&tries++<120){pageTimeout(poll,1500);return;}if(current.material_status==='processing'){container.innerHTML=`<div class="material-submission-state processing"><strong>材料仍在生成</strong><p>处理时间较长，请稍后在我的登记记录查看，或手动刷新。</p><button type="button" class="secondary" data-refresh-material>刷新状态</button></div>`;container.querySelector('[data-refresh-material]')?.addEventListener('click',()=>watchDeductionMaterial(record,container,onFinished));return;}showDeductionMaterialState(container,current,onFinished);if(current.material_status==='ready'){toast(current.status==='active'?'照片材料已生成PDF，扣分记录已生效。':'照片材料已生成PDF，请继续查看业务处理状态。');onFinished?.(current);}}catch(error){if(error?.name==='AbortError'||!container?.isConnected)return;if(tries++<5)pageTimeout(poll,2500);else toast('材料状态暂时无法刷新，请稍后在我的登记记录查看。',true);}};pageTimeout(poll,1200);}
function openDeductionMaterialRetry(recordId,done){const overlay=document.createElement('div');overlay.className='modal-overlay';const titleId='deduction-material-title-'+Date.now();overlay.innerHTML=`<section class="modal-card deduction-material-retry" role="dialog" aria-modal="true" aria-labelledby="${titleId}"><h3 id="${titleId}">补充声明材料</h3><p>不会新建扣分记录。请直接上传 PDF，或选择照片由系统后台合成为 PDF；材料就绪后仍以业务状态判断是否计分。</p>${deductionMaterialPickerMarkup()}<div class="modal-actions"><button type="button" class="secondary" data-retry-cancel>取消</button><button type="button" class="primary" data-retry-submit>提交材料</button></div></section>`;document.body.appendChild(overlay);const material=bindDeductionMaterialPicker(overlay),cancel=overlay.querySelector('[data-retry-cancel]'),submit=overlay.querySelector('[data-retry-submit]');let submitting=false;const layer=bindDialogLayer(overlay,{initialFocus:cancel,allowClose:()=>!submitting,onClose:()=>material?.reset?.()});cancel.onclick=()=>layer.close();submit.onclick=async()=>{if(submitting)return;if(!material?.hasMaterial()){toast('请上传PDF或选择照片材料。',true);return;}const data=new FormData(),mode=material.appendTo(data);submitting=true;submit.disabled=true;try{const out=await api('/api/deductions/'+recordId+'/material',{method:'POST',body:data});submitting=false;layer.close();toast(mode==='photos'?'材料已提交，照片正在后台合成为PDF；完成后请查看业务状态。':'材料已提交，正在后台处理；完成后请查看业务状态。');done(out.record);}catch(error){submitting=false;submit.disabled=false;toast(error.message,true);}};}
async function renderAbsence(){
  if(!has('SICK_REGISTER')){app.innerHTML='<section class="panel"><div class="error">当前账号无缺勤登记权限</div></section>';return;}
  app.innerHTML=`<div class="section-gap">${sickForm()}</div>`;
  const sickFormNode=document.getElementById('sickForm');
  if(sickFormNode&&has('DEDUCTION_DIRECT')||sickFormNode&&has('DEDUCTION_ALL')){const toggle=document.createElement('label');toggle.className='checkbox-row';toggle.innerHTML='<input name="is_violation" type="checkbox" value="true"> 是否为违规病假 <span class="field-hint">在本页同步登记一条独立的违规病假声明；事件日期取开始日期，不按缺勤天数重复扣分。</span>';const detail=document.createElement('section');detail.className='inline-material-panel';detail.hidden=true;detail.innerHTML=`<p class="field-hint">缺勤证明与违规病假声明材料分别保存。声明材料可直接上传PDF，或拍照/从相册选择；未上传时可先登记，后续补齐材料才扣分。</p>${deductionMaterialPickerMarkup()}<div class="repeat-warning notice" data-violation-upgrade hidden></div>`;const sticky=sickFormNode.querySelector('.form-sticky-actions')||sickFormNode.querySelector('button.warn');sticky?.before(toggle);sticky?.before(detail);sickFormNode._violationMaterial=bindDeductionMaterialPicker(detail);const employee=sickFormNode.querySelector('[name=employee_id]'),start=sickFormNode.querySelector('[name=leave_start_date]'),warning=detail.querySelector('[data-violation-upgrade]');const refresh=async()=>{detail.hidden=!toggle.querySelector('input').checked;if(detail.hidden){warning.hidden=true;warning.innerHTML='';return;}if(!employee.value||!start.value){warning.hidden=true;return;}try{const out=await api('/api/sick-leaves/violation-upgrade-preview?'+new URLSearchParams({employee_id:employee.value,leave_start_date:start.value}));if(!out.eligible){warning.hidden=true;warning.innerHTML='';return;}warning.hidden=false;warning.innerHTML=`<strong>3个月内已有未升级的违规病假声明</strong><p>${esc(out.first_record.occurred_on)}：${esc(out.first_record.description)}。本次将进入升级工单，请选择审核人。</p><label>GSM / TA GSM 审核人<select name="violation_reviewer_id"><option value="">请选择审核人</option>${out.reviewers.map(item=>`<option value="${item.id}">${esc(item.name)} · ${esc(item.role_name)}</option>`).join('')}</select></label>`;}catch(error){warning.hidden=false;warning.className='repeat-warning blocked';warning.innerHTML='<strong>暂时无法检查违规病假升级条件，请稍后重试。</strong>';}};sickFormNode._refreshViolation=refresh;toggle.querySelector('input').addEventListener('change',refresh);employee.addEventListener('change',refresh);start.addEventListener('change',refresh);}
  bindEmployeeSearches(app);
  bindSick();
  const form=document.getElementById('sickForm'),start=form.querySelector('[name=leave_start_date]'),end=form.querySelector('[name=leave_end_date]'),error=document.getElementById('sickDateError');
  const validateDates=()=>{let message='';if(!start.value||!end.value)message='请选择完整的开始日期和结束日期。';else if(inclusiveDays(start.value,end.value)<1)message='结束日期不能早于开始日期。';error.hidden=!message;error.textContent=message;start.setCustomValidity(message);end.setCustomValidity(message);};
  [start,end].forEach(input=>['input','change','blur'].forEach(event=>input.addEventListener(event,validateDates)));
  validateDates();
  bindSickLeaveOverlapGuard(form);
}

function singleSickLeaveForm(){
  return `<form id="singleSickLeaveForm" class="form-stack">
    ${employeePicker('员工（必选）','singleSickEmployee','absence_backup','输入姓名或工号查找全部在职员工','/api/sick-leave-imports/employee-targets')}
    <div class="grid two"><label>病假类型<select name="leave_type" required><option value="法定病假">法定病假</option><option value="全薪病假">全薪病假</option><option value="无薪病假">无薪病假</option></select></label><label>所属月份<input id="singleSickMonth" readonly aria-readonly="true"><span class="field-hint">根据病假日期自动确定</span></label></div>
    <div class="grid two"><label>开始日期<input name="leave_start_date" type="date" value="${today()}" required></label><label>结束日期<input name="leave_end_date" type="date" value="${today()}" required></label></div>
    <div class="grid two"><label>实际病假天数<input name="leave_days" type="number" min="0.5" step="0.5" value="1" required><span class="field-hint">支持0.5天；不含本休，跨月请分开登记</span></label><label>备注（可选）<textarea name="note" rows="2" maxlength="300" placeholder="填写补登记说明"></textarea></label></div>
    <div class="notice">本条记录提交后生效；后续导入该月完整缺勤文件时，将以文件为准覆盖。</div>
    <div id="singleSickResult" aria-live="polite"></div><div class="actions form-sticky-actions"><button type="button" class="secondary" id="clearSingleSick">清空</button><button type="submit" class="primary">登记病假</button></div>
  </form>`;
}

function bindSingleSickLeave(form,onDone){
  const start=form.elements.leave_start_date,end=form.elements.leave_end_date,days=form.elements.leave_days,month=document.getElementById('singleSickMonth'),result=document.getElementById('singleSickResult');
  const syncDates=()=>{
    const valid=Boolean(start.value&&end.value&&end.value>=start.value&&start.value.slice(0,7)===end.value.slice(0,7));
    month.value=start.value?`${start.value.slice(0,4)}年${start.value.slice(5,7)}月`:'';
    end.setCustomValidity(valid?'':'病假日期无效，跨月请分开登记');
    days.max=String(Math.max(0.5,inclusiveDays(start.value,end.value)||0.5));
  };
  [start,end].forEach(input=>input.addEventListener('change',syncDates));
  const clearKey=()=>{if(form.dataset.submitting!=='true')clearSubmissionKey(form);};
  form.addEventListener('input',clearKey);form.addEventListener('change',clearKey);
  document.getElementById('clearSingleSick').onclick=()=>{
    if(form.dataset.submitting==='true')return;
    form.reset();form.querySelector('[data-employee-clear]')?.click();result.innerHTML='';clearSubmissionKey(form);syncDates();
  };
  form.onsubmit=async event=>{
    event.preventDefault();if(!requireEmployeeSelection(form))return;syncDates();if(!form.reportValidity())return;
    const enteredDays=Number(days.value);
    if(!Number.isFinite(enteredDays)||enteredDays<=0||enteredDays*2%1!==0||enteredDays>inclusiveDays(start.value,end.value)){toast('病假天数须按0.5天递增，且不能超过日期范围',true);return;}
    await withSubmitLock(form,async()=>{
      try{
        const employeeText=form.querySelector('[data-employee-search]').value;
        const accepted=await confirmModal('确认登记病假',`<p>${esc(employeeText)}</p><p>${esc(start.value)} 至 ${esc(end.value)} · ${esc(form.elements.leave_type.value)} · ${enteredDays}天</p>${end.value>start.value?'<p>请确认实际病假天数中不含演职人员本休。</p>':''}<p>后续导入该月完整缺勤文件时，将以文件为准覆盖。</p>`,'确认登记');
        if(!accepted)return;
        const data=submissionData(form);data.set('rest_day_confirmed','true');
        const out=await api('/api/sick-leave-imports/single',{method:'POST',body:data});clearSubmissionKey(form);
        result.innerHTML=`<div class="notice"><strong>${out.duplicate?'该条病假已登记，无需重复提交':'病假登记成功'}</strong><br>${esc(out.record.leave_start_date)} 至 ${esc(out.record.leave_end_date)} · ${esc(out.record.leave_type)} · ${Number(out.record.leave_days).toFixed(1)}天 · 单人备用登记${out.score_eligible?`<br>当月全勤分：${fmt(out.attendance_score)}`:''}</div>`;
        form.elements.note.value='';toast('病假已登记');await onDone(out.record.attendance_month);
      }catch(error){result.innerHTML=`<div class="error" role="alert">${esc(error.message||'病假登记失败，请稍后重试')}</div>`;toast(error.message||'病假登记失败',true);}
    });
  };
  syncDates();
}

function sickLeaveSourceLabel(row){return row.import_source==='single_backup'?'单人备用登记':row.import_source==='monthly_transaction_import'?'月度文件导入':'历史人工登记';}

async function renderSickLeaveImport(){
  if(!has('SICK_LEAVE_IMPORT')){app.innerHTML='<section class="panel"><div class="error">当前账号没有缺勤登记权限</div></section>';return;}
  app.innerHTML=`<div class="section-gap sick-import-page"><section class="panel"><h2>缺勤登记</h2><p>按月导入完整缺勤文件，或通过备用入口补登记单个员工的病假。</p><div class="actions" role="tablist" aria-label="缺勤登记方式"><button type="button" class="primary" data-sick-entry-mode="file" id="sickFileTab" role="tab" aria-selected="true" aria-controls="sickFilePanel">月度文件导入</button><button type="button" class="secondary" data-sick-entry-mode="single" id="sickSingleTab" role="tab" aria-selected="false" aria-controls="sickSinglePanel">单人病假登记（备用）</button></div><div id="sickFilePanel" role="tabpanel" aria-labelledby="sickFileTab"><p class="field-hint">选择月份后上传该月完整缺勤文件。未列出的本月员工视为无缺勤，旧缺勤标记“已覆盖”并退出计分。员工匹配失败须由登记人复核后跳过；月份不符或文件对账错误禁止覆盖。景点圈HR的文件导入仅更新本圈。</p><form id="sickLeaveImportForm" class="form-stack" enctype="multipart/form-data"><label>覆盖月份<input name="month" type="month" value="${monthNow()}" required></label><label>员工事务文件<input name="workbook" type="file" accept=".xls" required><span class="field-hint">仅支持不超过 10 MB 的 .xls；源文件 ID 去掉第一位后与系统 7 位员工号匹配。预检最多有效 20 分钟。</span></label><button type="submit" class="primary">预检文件</button></form><div id="sickLeaveImportResult"></div></div><div id="sickSinglePanel" role="tabpanel" aria-labelledby="sickSingleTab" hidden>${singleSickLeaveForm()}</div></section><section class="panel"><h2>月度缺勤登记记录</h2><p class="field-hint">展示月度文件及单人备用登记；已覆盖记录可展开查看，不再参与计天。历史人工登记仍在“我的登记记录”查看。</p><label>查看月份<input id="sickRecordsMonth" type="month" value="${monthNow()}"></label><div id="sickLeaveImportRecords" class="empty">正在加载…</div></section></div>`;
  const form=document.getElementById('sickLeaveImportForm'),result=document.getElementById('sickLeaveImportResult'),records=document.getElementById('sickLeaveImportRecords');
  const recordMonth=document.getElementById('sickRecordsMonth');let recordSequence=0;
  const importTable=rows=>`<div class="desktop-only table-wrap"><table><thead><tr><th>登记时间 / 来源</th><th>员工 / 景点圈</th><th>病假日期</th><th>类型 / 天数</th><th>登记人</th><th>状态</th></tr></thead><tbody>${rows.map(row=>`<tr class="${row.status==='covered'?'void-row':''}"><td>${esc(row.submitted_at)}<br><small>${esc(sickLeaveSourceLabel(row))}</small></td><td>${esc(row.employee_name)}<br><small>${esc(row.employee_no)} · ${esc(row.attraction_name||'未分配景点圈')}</small></td><td>${esc(row.leave_start_date)} 至 ${esc(row.leave_end_date)}</td><td>${esc(row.leave_type)} · ${Number(row.leave_days).toFixed(1)}天${row.note?`<br><small>${esc(row.note)}</small>`:''}</td><td>${esc(row.submitter_name)}</td><td>${statusBadge(row)}${row.status==='covered'?`<br><small>${esc(row.void_reason)}</small>`:''}</td></tr>`).join('')}</tbody></table></div><div class="mobile-only mobile-records">${rows.map(row=>`<article class="record-card"><div class="record-line"><strong>${esc(row.employee_name)} · ${esc(row.employee_no)}</strong>${statusBadge(row)}</div><p>${esc(row.attraction_name||'未分配景点圈')} · ${esc(row.leave_type)} · ${Number(row.leave_days).toFixed(1)}天</p><p>${esc(row.leave_start_date)} 至 ${esc(row.leave_end_date)}</p><small>${esc(sickLeaveSourceLabel(row))} · ${esc(row.submitter_name)} · ${esc(row.submitted_at)}</small>${row.note?`<p>${esc(row.note)}</p>`:''}${row.status==='covered'?`<p>${esc(row.void_reason)}</p>`:''}</article>`).join('')}</div>`;
  const loadRecords=async()=>{const sequence=++recordSequence;records.classList.remove('empty');const out=await api('/api/sick-leave-imports/records?'+new URLSearchParams({month:recordMonth.value}));if(sequence!==recordSequence)return;const rows=out.items||[],{visible,covered}=splitCoveredSickRecords(rows);records.innerHTML=rows.length?`${visible.length?importTable(visible):''}${coveredSickDisclosure(covered,importTable(covered))}`:'<div class="empty">所选月份暂无缺勤登记记录</div>';};
  recordMonth.addEventListener('change',()=>loadRecords().catch(error=>toast(error.message,true)));
  form.elements.month.addEventListener('change',()=>{recordMonth.value=form.elements.month.value;result.innerHTML='';loadRecords().catch(error=>toast(error.message,true));});
  app.querySelectorAll('[data-sick-entry-mode]').forEach(button=>button.onclick=()=>{app.querySelectorAll('[data-sick-entry-mode]').forEach(tab=>{const active=tab===button;tab.setAttribute('aria-selected',String(active));tab.className=active?'primary':'secondary';});document.getElementById('sickFilePanel').hidden=button.dataset.sickEntryMode!=='file';document.getElementById('sickSinglePanel').hidden=button.dataset.sickEntryMode!=='single';});
  bindEmployeeSearches(app);
  bindSingleSickLeave(document.getElementById('singleSickLeaveForm'),async month=>{recordMonth.value=month;await loadRecords();});
  form.onsubmit=async event=>{
    event.preventDefault();
    const month=form.elements.month.value,file=form.elements.workbook.files?.[0];
    if(!month||!file){toast('请选择月份和 .xls 文件',true);return;}
    const data=new FormData();data.set('month',month);data.set('workbook',file,file.name);
    try{
      const out=await api('/api/sick-leave-imports/preview',{method:'POST',body:data});
      const unmatched=out.unmatched||[],protectedRows=out.loa_protected||[],notImported=[...unmatched,...protectedRows],blocked=out.blocking_errors||[];
      const errors=blocked.length?`<div class="error"><strong>预检未通过</strong><ul>${blocked.map(item=>`<li>${esc(item)}</li>`).join('')}</ul></div>`:'';
      const rows=notImported.length?`<div class="table-wrap"><table><thead><tr><th>行号</th><th>姓名</th><th>源ID</th><th>系统员工号</th><th>日期</th><th>原因</th></tr></thead><tbody>${notImported.map(item=>`<tr><td>${item.row}</td><td>${esc(item.name)}</td><td>${esc(item.source_id)}</td><td>${esc(item.employee_no)}</td><td>${esc(item.date)}</td><td>${esc(item.reason)}</td></tr>`).join('')}</tbody></table></div>`:'<p class="field-hint">文件明细均已匹配。</p>';
      const protectedNote=protectedRows.length?`<div class="notice"><strong>LOA保护</strong><br>${protectedRows.length} 条文件明细落在LOA日期内，已跳过。</div>`:'';
      const unmatchedNote=unmatched.length?`<div class="notice"><strong>需登记人复核：${unmatched.length} 条员工未匹配</strong><br>以下行将跳过，不会写入缺勤；其原因已逐行列明。整月旧缺勤仍会按所选月份覆盖，请核对这些员工是否受影响。</div>`:'';
      const review=out.can_commit&&unmatched.length?`<label class="checkbox-row"><input id="reviewSickUnmatched" type="checkbox">我已逐行复核未匹配员工及原因，确认跳过这些行并继续整月覆盖</label>`:'';
      result.innerHTML=`${errors}<div class="notice"><strong>预检结果</strong><br>月份：${esc(out.month)}；文件匹配 ${out.matched_employee_count} 名员工、${out.matched_record_count} 条明细；当月旧记录 ${out.replaced_record_count} 条将被覆盖，其中 ${out.absent_employee_count} 名员工没有可匹配的文件缺勤，旧缺勤将退出计分。</div>${unmatchedNote}${protectedNote}${rows}${notImported.length?`<a class="secondary download-link" href="${portalPath('/api/sick-leave-imports/'+encodeURIComponent(out.token)+'/unmatched-file')}">下载未导入数据标红文件</a>`:''}${review}${out.can_commit?`<div class="actions"><button id="commitSickLeaveImport" class="warn" ${unmatched.length?'disabled':''}>确认覆盖所选月份全部缺勤</button></div>`:''}`;
      document.getElementById('reviewSickUnmatched')?.addEventListener('change',event=>{document.getElementById('commitSickLeaveImport').disabled=!event.target.checked;});
      document.getElementById('commitSickLeaveImport')?.addEventListener('click',async()=>{
        const confirmed=await confirmModal('确认覆盖整月缺勤',`<p>将以文件替换 ${esc(out.month)} 的全部旧缺勤，共 ${out.replaced_record_count} 条；其中 ${out.absent_employee_count} 名员工没有可匹配的文件缺勤，旧缺勤将退出计分。</p>${unmatched.length?`<p>已复核的 ${unmatched.length} 条未匹配员工行将跳过，不写入缺勤；旧记录仍按整月覆盖。</p>`:''}<p>旧记录保留“已覆盖”审计状态，不再参与计天；LOA不变。</p>`,'确认覆盖整月');
        if(!confirmed)return;
        try{const committed=await api('/api/sick-leave-imports/commit',json('POST',{token:out.token,month:out.month,reviewed_unmatched:unmatched.length>0}));result.innerHTML=`<div class="notice"><strong>覆盖完成</strong><br>${esc(committed.month)} 写入 ${committed.covered_record_count} 条文件明细；旧记录 ${committed.replaced_record_count} 条已覆盖，${committed.absent_employee_count} 名无可匹配文件缺勤的员工已退出本月缺勤计分；未匹配跳过 ${committed.unmatched_count} 条，LOA保护跳过 ${committed.loa_protected_count} 条。</div>`;recordMonth.value=committed.month;await loadRecords();toast('所选月份缺勤已覆盖并重算全勤分');}catch(error){toast(error.message,true);}
      });
    }catch(error){const message=error.message||'预检失败';result.innerHTML=`<div class="error" role="alert"><strong>无法完成预检</strong><p>${esc(message)}</p></div>`;toast(message,true);}
  };
  await loadRecords();
}

async function renderLoa(){
  if(!has('LOA_REGISTER')){app.innerHTML='<section class="panel"><div class="error">当前账号没有LOA登记权限</div></section>';return;}
  app.innerHTML=`<div class="section-gap loa-page"><section class="panel loa-workflow"><div class="loa-heading"><div><h2>LOA（长期病假）登记</h2><p>先搜索员工，再根据当前状态登记进入或结束。LOA 涉及的每个自然月均不参与任何计分。</p></div><span class="badge warn">两步登记</span></div><form id="loaForm" class="form-stack"><section class="loa-step"><strong>1. 搜索并选择员工</strong>${employeePicker('员工（必选）','loaEmployee','loa','输入姓名或工号查找全部在职员工')}</section><section id="loaState" class="loa-state notice"><strong>2. 确认 LOA 状态</strong><p>请先搜索并选择员工。</p></section><section id="loaDateStep" class="loa-step" hidden><strong id="loaDateTitle">3. 登记进入日期</strong><div class="grid two"><label id="loaStartField">LOA进入日期<input name="starts_on" type="date" value="${today()}" required></label><label id="loaEndField" hidden>LOA结束日期（必填）<input name="ends_on" type="date" value="${today()}"><span class="field-hint">默认当前日期，可手动选择其他日期；不可早于进入日期。</span></label></div><p id="loaMonthHint" class="field-hint" aria-live="polite"></p></section><label>备注（可选）<textarea name="note" maxlength="300" placeholder="例如：长期病假登记说明"></textarea></label><div class="form-sticky-actions"><button class="warn" id="loaSubmit" disabled>请先选择员工</button></div></form></section><section class="panel"><h2>近期 LOA 记录</h2><p class="field-hint">未填写结束日期的记录，可在下方直接补登记结束日期。</p><div id="loaRecords" class="empty">正在加载…</div></section></div>`;
  bindEmployeeSearches(app);
  const form=document.getElementById('loaForm'),start=form.elements.starts_on,end=form.elements.ends_on,hint=document.getElementById('loaMonthHint'),employee=form.querySelector('[name=employee_id]'),submit=document.getElementById('loaSubmit'),stateBox=document.getElementById('loaState'),dateStep=document.getElementById('loaDateStep'),dateTitle=document.getElementById('loaDateTitle'),startField=document.getElementById('loaStartField'),endField=document.getElementById('loaEndField');
  const monthsBetweenDates=()=>{if(!start.value||!end.value||end.value<start.value)return [];const months=[];let cursor=new Date(start.value+'T00:00:00'),last=new Date(end.value+'T00:00:00');cursor.setDate(1);last.setDate(1);while(cursor<=last){months.push(`${cursor.getFullYear()}-${String(cursor.getMonth()+1).padStart(2,'0')}`);cursor.setMonth(cursor.getMonth()+1);}return months;};
  const updateMode=()=>{if(!employee.value){dateStep.hidden=true;submit.disabled=true;submit.textContent='请先选择员工';stateBox.innerHTML='<strong>2. 确认 LOA 状态</strong><p>请先搜索并选择员工。</p>';return;}const active=employee.dataset.loaActive==='1',endsOn=employee.dataset.loaEndsOn||'';dateStep.hidden=false;if(active){start.value=employee.dataset.loaStartsOn;start.readOnly=true;startField.classList.add('loa-date-locked');endField.hidden=false;end.min=start.value;if(endsOn){start.disabled=true;end.disabled=true;end.required=false;end.value=endsOn;dateTitle.textContent='3. LOA期间（只读）';submit.disabled=true;submit.textContent='LOA进行中';stateBox.innerHTML=`<strong>2. 当前处于 LOA</strong><p>该员工的 LOA 时间为 <b>${esc(start.value)}</b> 至 <b>${esc(endsOn)}</b>。当前仍在 LOA 期间，不能重复登记。</p>`;hint.textContent='开始日期和结束日期均已登记，当前仅供查看。';}else{start.disabled=false;end.disabled=false;end.required=true;end.value=today();dateTitle.textContent='3. 登记 LOA 结束';submit.disabled=false;submit.textContent='登记LOA结束';stateBox.innerHTML=`<strong>2. 当前处于 LOA</strong><p>该员工于 <b>${esc(start.value)}</b> 进入 LOA。开始日期已锁定；请补登记结束日期。</p>`;updateHint();}}else{start.disabled=false;start.readOnly=false;startField.classList.remove('loa-date-locked');end.required=false;end.value='';end.disabled=true;end.min='';endField.hidden=true;dateTitle.textContent='3. 登记进入 LOA';submit.disabled=false;submit.textContent='登记进入LOA';stateBox.innerHTML='<strong>2. 当前未处于 LOA</strong><p>请选择实际进入日期后登记；结束日期以后再补登记。</p>';hint.textContent='';}};
  const updateHint=()=>{if(employee.dataset.loaActive!=='1'){hint.textContent='';return;}if(end.value&&end.value<start.value){hint.textContent='结束日期不能早于进入日期。';return;}const months=monthsBetweenDates();hint.textContent=months.length?`涉及月份：${months.join('、')}。这些月份均不参与计分。`:'';};
  const load=async()=>{
    const out=await api('/api/loa-periods'),rows=out.items||[],records=document.getElementById('loaRecords');
    records.classList.remove('empty');
    records.innerHTML=rows.length?`<div class="table-wrap"><table class="loa-record-table"><thead><tr><th>员工</th><th>进入日期</th><th>结束日期</th><th>状态</th><th>备注</th><th>登记人</th><th>操作</th></tr></thead><tbody>${rows.map(row=>{
      const ongoing=!row.ends_on||row.ends_on>=today(),endControl=row.ends_on?esc(row.ends_on):`<input type="date" value="${today()}" min="${esc(row.starts_on)}" aria-label="${esc(row.employee_name)}的LOA结束日期" data-loa-end-date="${row.id}">`,completeAction=row.ends_on?'':`<button type="button" class="secondary" data-loa-complete="${row.id}" data-employee-id="${row.employee_id}" data-starts-on="${esc(row.starts_on)}" data-employee-name="${esc(row.employee_name)}">登记结束</button>`;
      return `<tr><td>${esc(row.employee_name)}<br><small>${esc(row.employee_no)}</small></td><td>${esc(row.starts_on)}</td><td>${endControl}</td><td><span class="badge ${ongoing?'warn':'ok'}">${ongoing?'LOA进行中':'已结束'}</span></td><td>${esc(row.note||'—')}</td><td>${esc(row.created_by_name)}</td><td><div class="loa-record-actions">${completeAction}<button type="button" class="secondary" data-loa-cancel="${row.id}">撤销</button></div></td></tr>`;
    }).join('')}</tbody></table></div>`:'<div class="empty">暂无LOA记录</div>';
    records.querySelectorAll('[data-loa-complete]').forEach(button=>button.onclick=async()=>{
      const endDate=records.querySelector(`[data-loa-end-date="${button.dataset.loaComplete}"]`),startsOn=button.dataset.startsOn;
      if(!endDate?.value){toast('请选择LOA结束日期',true);endDate?.focus();return;}
      if(endDate.value<startsOn){toast('结束日期不能早于进入日期',true);endDate.focus();return;}
      const months=[];let cursor=new Date(startsOn+'T00:00:00'),last=new Date(endDate.value+'T00:00:00');cursor.setDate(1);last.setDate(1);while(cursor<=last){months.push(`${cursor.getFullYear()}-${String(cursor.getMonth()+1).padStart(2,'0')}`);cursor.setMonth(cursor.getMonth()+1);}
      if(!(await confirmModal('确认登记LOA结束',`<p>${esc(button.dataset.employeeName)} 的结束日期为 ${esc(endDate.value)}。</p><p>涉及月份：${esc(months.join('、'))}</p><p>这些月份只保留记录，不参与计分。</p>`,'确认登记')))return;
      try{const result=await api('/api/loa-periods',json('POST',{employee_id:Number(button.dataset.employeeId),starts_on:startsOn,ends_on:endDate.value}));toast(`LOA结束已登记，已排除 ${result.excluded_months.join('、')} 的计分`);await load();}catch(error){toast(error.message,true);}
    });
    records.querySelectorAll('[data-loa-cancel]').forEach(button=>button.onclick=async()=>{const reason=window.prompt('请填写撤销原因');if(!reason?.trim())return;try{await api('/api/loa-periods/'+button.dataset.loaCancel,{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({reason})});toast('LOA已撤销并重新计算涉及月份');await load();}catch(error){toast(error.message,true)}});
  };
  employee.addEventListener('change',updateMode);[start,end].forEach(input=>input.addEventListener('change',updateHint));updateMode();await load();
  form.onsubmit=async event=>{event.preventDefault();if(!requireEmployeeSelection(form))return;const ending=employee.dataset.loaActive==='1';if(ending&&!end.value){toast('请手动选择LOA结束日期',true);end.focus();return;}if(ending&&end.value<start.value){toast('结束日期不能早于进入日期',true);return;}const months=monthsBetweenDates();const content=ending?`<p>涉及月份：${esc(months.join('、'))}</p><p>这些月份的全勤、绩效加扣分和各类汇总均只保留记录，不参与计分。</p>`:'<p>将登记该员工进入 LOA。结束日期未登记前，记录状态为“LOA进行中”。</p>';if(!(await confirmModal(ending?'确认登记LOA结束':'确认登记进入LOA',content,'确认登记')))return;try{const out=await api('/api/loa-periods',json('POST',Object.fromEntries(new FormData(form))));toast(out.completed?`LOA结束已登记，已排除 ${out.excluded_months.join('、')} 的计分`:'已登记进入LOA，状态为进行中');form.reset();form.querySelector('[name=employee_id]').value='';form.querySelector('.employee-selected').hidden=true;form.querySelector('[data-employee-search]').value='';delete employee.dataset.loaActive;delete employee.dataset.loaStartsOn;delete employee.dataset.loaEndsOn;updateMode();await load();}catch(error){toast(error.message,true)}};
}

async function renderEntries(){
  const isSupervisor=['TA_SUPERVISOR','SUPERVISOR'].includes(state.me.role_code);
  const canCollaborate=['TA_SUPERVISOR','SUPERVISOR','TA_GSM','GSM'].includes(state.me.role_code);
  const title=isSupervisor?'主管登记记录':'我的登记记录';
  const description=isSupervisor?'默认展示本人登记的扣分和缺勤记录；可切换查看全部主管登记记录。其他主管的记录仅供查询，本人记录继续按原权限操作。':'展示本人登记的数据和重复处分跟进状态。';
  const scopeField=isSupervisor?'<label>查看范围<select name="scope"><option value="mine">我的登记记录</option><option value="supervisors">全部主管登记记录</option></select></label>':'';
  const recordTypes=isSupervisor?'<option value="all">全部</option><option value="deduction">扣分</option><option value="sick_leave">缺勤</option>':'<option value="all">全部</option><option value="recognition">加分</option><option value="deduction">扣分</option><option value="sick_leave">缺勤</option><option value="follow_up">处分跟进</option>';
  app.innerHTML=`<div class="section-gap"><section class="panel"><h2>${title}</h2><p>${description}</p><form id="entryFilter" class="entry-filter">${scopeField}<label>开始日期<input name="start_date" type="date" value="${monthStart()}"></label><label>结束日期<input name="end_date" type="date" value="${today()}"></label><label>记录类型<select name="record_type">${recordTypes}</select></label><label>状态<select name="status"><option value="">全部</option><option value="confirmed">已确认</option><option value="pending">待复核 / 待经理跟进</option><option value="issued">已开具</option><option value="rejected">不通过</option><option value="pending_material">待补充材料</option><option value="material_processing">材料生成中</option><option value="material_failed">材料生成失败</option><option value="active">已生效</option><option value="covered">已覆盖</option><option value="void">已作废</option></select></label><label>员工搜索<input name="keyword" placeholder="姓名/员工号"></label><button class="primary">查询</button></form><div class="actions"><button id="allHistoryBtn" class="secondary">查看全部历史</button></div></section><div id="entryResults"></div></div>`;
  const form=document.getElementById('entryFilter');let currentPage=1;
  const load=async()=>{
    const request=beginViewRequest();
    const qs=new URLSearchParams([...new FormData(form)].filter(([,value])=>value));qs.set('page',String(currentPage));qs.set('page_size','100');
    const materialScope=state.pendingMaterialScope||'mine';
    const [data,pendingMaterials]=await Promise.all([api('/api/my-entries?'+qs),canCollaborate?api('/api/deductions/pending-materials?'+new URLSearchParams({scope:materialScope})):Promise.resolve({items:[]})]);
    if(!request.isCurrent())return;
    const collaboration=(pendingMaterials.items||[]);
    const collaborationPanel=canCollaborate?`<section class="panel"><h3>待补充声明材料</h3><p class="field-hint">TA主管、主管、TA GSM和GSM均可协作补充；补齐并处理成功后才会正式扣分。本人提交且尚未计分的记录可作废。</p><label class="material-scope-filter">景点圈范围<select data-pending-material-scope><option value="mine" ${materialScope==='mine'?'selected':''}>我的景点圈</option><option value="all" ${materialScope==='all'?'selected':''}>全部景点圈</option></select></label>${collaboration.length?`<div class="mobile-only work-card-list">${collaboration.map(row=>{const own=Number(row.submitter_id)===Number(state.me.id);const origin=own?'<small class="material-inline-state">我提交</small>':'<small class="material-inline-state">协作补充</small>';const ownVoid=own?` <button data-entry-void="${row.id}" class="secondary">作废</button>`:'';return `<article class="work-card"><div class="work-card-head"><strong>${esc(row.employee_name)}</strong><small>${esc(row.employee_no)}</small>${statusBadge(row)}</div><p>${esc(row.occurred_on)} · ${esc(row.deduction_type)}</p><p>${origin}</p><div class="work-card-actions"><button data-entry-material-retry="${row.id}" class="secondary">补充材料</button>${ownVoid}</div></article>`;}).join('')}</div><div class="desktop-only table-wrap sticky-col"><table><thead><tr><th>登记时间</th><th>员工</th><th>事件日期</th><th>内容</th><th>状态</th><th>操作</th></tr></thead><tbody>${collaboration.map(row=>{const own=Number(row.submitter_id)===Number(state.me.id);const origin=own?'<br><small class="material-inline-state">我提交</small>':'<br><small class="material-inline-state">协作补充</small>';const ownVoid=own?` <button data-entry-void="${row.id}" class="secondary">作废</button>`:'';return `<tr><td>${esc(row.submitted_at)}</td><td>${esc(row.employee_name)}<br><small>${esc(row.employee_no)}</small></td><td>${esc(row.occurred_on)}</td><td>${esc(row.deduction_type)} · ${esc(row.deduction_level)}${origin}<br>${esc(row.description)}</td><td>${statusBadge(row)}</td><td><button data-entry-material-retry="${row.id}" class="secondary">补充材料</button>${ownVoid}</td></tr>`;}).join('')}</tbody></table></div>`:'<div class="empty">当前范围暂无待补充声明材料</div>'}</section>`:'';
    const {visible,covered}=splitCoveredSickRecords(data.items);
    const table=items=>`<div class="desktop-only table-wrap sticky-col"><table><thead><tr><th>登记时间</th><th>类别</th><th>员工</th><th>业务日期</th><th>内容</th><th>分值/天数</th><th>状态</th><th>操作</th></tr></thead><tbody>${items.map(entryRow).join('')}</tbody></table></div>`;
    const coveredContent=`<div class="mobile-only work-card-list">${covered.map(entryCard).join('')}</div>${table(covered)}`;
    document.getElementById('entryResults').innerHTML=`${collaborationPanel}<section class="panel"><div class="mobile-only work-card-list">${visible.map(entryCard).join('')||(!covered.length?'<div class="empty">无匹配记录</div>':'')}</div>${visible.length?table(visible):!covered.length?'<div class="desktop-only empty">无匹配记录</div>':''}${coveredSickDisclosure(covered,coveredContent)}<div class="pagination"><button id="entryPrev" class="secondary" ${data.page<=1?'disabled':''}>上一页</button><span>第${data.page}页，共${data.total}条</span><button id="entryNext" class="secondary" ${data.page*data.page_size>=data.total?'disabled':''}>下一页</button></div></section>`;
    bindFilePreviews(document.getElementById('entryResults'));
    bindEntryActions(load);
    document.querySelector('[data-pending-material-scope]')?.addEventListener('change',event=>{state.pendingMaterialScope=event.target.value;load();});
    const focusId=Number(state.pendingMaterialFocusId||0);
    if(focusId){state.pendingMaterialFocusId=null;const button=document.querySelector(`[data-entry-material-retry="${focusId}"]`);if(button)setTimeout(()=>button.click(),0);else toast('该待补材料记录已变化，请刷新后确认。',true);}
    document.getElementById('entryPrev').onclick=()=>{if(currentPage>1){currentPage--;load()}};
    document.getElementById('entryNext').onclick=()=>{if(currentPage*data.page_size<data.total){currentPage++;load()}};
  };
  form.onsubmit=e=>{e.preventDefault();currentPage=1;load()};
  document.getElementById('allHistoryBtn').onclick=()=>{form.querySelector('[name=start_date]').value='';form.querySelector('[name=end_date]').value='';currentPage=1;load()};
  await load();
}
function entryRow(row){
  if(row.record_type==='follow_up'){
    const issued=row.status==='issued'?`<br><small>${esc(row.issued_by_name)} · ${esc(row.issued_at)}</small>`:'';
    return `<tr><td>${esc(row.submitted_at)}</td><td>处分跟进</td><td>${esc(row.employee_name)}<br><small>${esc(row.employee_no)}</small></td><td>${esc(row.occurred_on)}</td><td>${esc(row.deduction_type)} · 三个月内第二次${issued}</td><td>—</td><td>${statusBadge(row)}</td><td></td></tr>`;
  }
  if(row.record_type==='recognition'){
    const evidence=attachmentControl(row.image_url,'图片',row.image_preview_kind);
    return `<tr><td>${esc(row.submitted_at)}</td><td>加分</td><td>${esc(row.employee_name)}<br><small>${esc(row.employee_no)}</small></td><td>${esc(row.recognition_date)}</td><td>${esc(row.recognition_type)} · ${esc(row.recognizer_name)}${sameDayDuplicateBadge(row)}<br>${esc(row.content)}${evidence}${row.monthly_cap_reason?`<br><small>${esc(row.monthly_cap_reason)}</small>`:''}</td><td class="score-positive">+${recognitionScoreText(row)}</td><td>${statusBadge(row)}</td><td>${row.available_actions.includes('withdraw')?`<button data-entry-withdraw="${row.id}" class="secondary">撤回</button>`:''}</td></tr>`;
  }
  if(row.record_type==='sick_leave'){
    const submitterNote=Number(row.submitter_id)===Number(state.me.id)?'':`<br><small>登记人：${esc(row.submitter_name)}</small>`;
    const sourceNote=row.import_source==='monthly_transaction_import'?'<br><small>来源：HR 月度病假事务导入</small>':'';
    return `<tr class="${row.status==='void'?'void-row':''}"><td>${esc(row.submitted_at)}</td><td>缺勤</td><td>${esc(row.employee_name)}<br><small>${esc(row.employee_no)}</small></td><td>${esc(row.leave_start_date)} 至 ${esc(row.leave_end_date)}</td><td>${esc(row.leave_type||'病假')} · ${esc(row.note||'无备注')} ${attachmentControl(row.proof_url,'证明',row.proof_preview_kind)}${sourceNote}${submitterNote}${row.void_reason?`<br><small>作废原因：${esc(row.void_reason)}</small>`:''}</td><td>${Number(row.leave_days).toFixed(1)}天<br><small>计费${row.charged_days}天</small></td><td>${statusBadge(row)}</td><td>${row.available_actions.includes('void')?`<button data-entry-sick-void="${row.id}" class="secondary">作废</button>`:''}</td></tr>`;
  }
  const materialNote=row.status==='pending_material'?'<br><small class="material-inline-state">待补充声明材料，暂不计分。</small>':row.material_status==='processing'?'<br><small class="material-inline-state">材料正在生成PDF，暂不计分。</small>':row.material_status==='failed'?`<br><small class="material-inline-state failed">${esc(row.material_error||'材料生成失败，请重新提交。')}</small>`:'';
  const supplement=row.available_actions.includes('supplement_material')?`<button data-entry-material-retry="${row.id}" class="secondary">补充材料</button>`:'';
  const actions=`${supplement}${row.available_actions.includes('void')?` <button data-entry-void="${row.id}" class="secondary">作废</button>`:''}`;
  const submitterNote=Number(row.submitter_id)===Number(state.me.id)?'':`<br><small>登记人：${esc(row.submitter_name)}</small>`;
  return `<tr class="${row.status==='void'?'void-row':''}"><td>${esc(row.submitted_at)}</td><td>扣分</td><td>${esc(row.employee_name)}<br><small>${esc(row.employee_no)}</small></td><td>${esc(row.occurred_on)}</td><td>${esc(row.deduction_type)} · ${esc(row.deduction_level)}<br>${esc(row.description)} ${attachmentControl(row.document_url,'声明PDF',row.document_preview_kind)}${submitterNote}${materialNote}</td><td class="score-negative">${row.material_status==='processing'||row.material_status==='failed'?'暂不计分':fmtDeduction(row.points)}</td><td>${statusBadge(row)}</td><td>${actions}</td></tr>`;
}
function entryCard(row){
  const person=`<strong>${esc(row.employee_name)}</strong><small>${esc(row.employee_no)}</small>`;
  const chip=statusBadge(row);
  if(row.record_type==='follow_up'){
    return `<article class="work-card"><div class="work-card-head">${person}${chip}</div><p>${esc(row.occurred_on)} · 处分跟进 · 三个月内第二次</p>${row.issued_by_name?`<p>${esc(row.issued_by_name)} · ${esc(row.issued_at||'')}</p>`:''}</article>`;
  }
  if(row.record_type==='recognition'){
    const actions=row.available_actions.includes('withdraw')?`<button data-entry-withdraw="${row.id}" class="secondary">撤回</button>`:'';
    const evidence=row.image_url?attachmentControl(row.image_url,'查看材料',row.image_preview_kind):'';
    return `<article class="work-card"><div class="work-card-head">${person}${chip}</div><p>${esc(row.recognition_date)} · 加分 · ${esc(row.recognition_type)} · ${recognitionScoreText(row)}</p><p>签卡人：${esc(row.recognizer_name)}</p><p>${esc(row.content)}${sameDayDuplicateBadge(row)}${evidence}</p>${recognitionScoreNote(row)}<div class="work-card-actions">${actions}</div></article>`;
  }
  if(row.record_type==='sick_leave'){
    const actions=row.available_actions.includes('void')?`<button data-entry-sick-void="${row.id}" class="secondary">作废</button>`:'';
    const proof=row.proof_url?attachmentControl(row.proof_url,'查看证明',row.proof_preview_kind):'';
    const sourceNote=row.import_source==='monthly_transaction_import'?' · HR月度导入':'';
    return `<article class="work-card ${row.status==='void'?'void-row':''}"><div class="work-card-head">${person}${chip}</div><p>${esc(row.leave_start_date)} 至 ${esc(row.leave_end_date)} · ${esc(row.leave_type||'病假')} · ${Number(row.leave_days).toFixed(1)}天${sourceNote}</p><p>${esc(row.note||'无备注')} ${proof}</p><div class="work-card-actions">${actions}</div></article>`;
  }
  const supplement=row.available_actions.includes('supplement_material')?`<button data-entry-material-retry="${row.id}" class="secondary">补充材料</button>`:'';
  const actions=`${supplement}${row.available_actions.includes('void')?` <button data-entry-void="${row.id}" class="secondary">作废</button>`:''}`;
  const material=row.document_url?attachmentControl(row.document_url,'查看材料',row.document_preview_kind):'';
  const points=row.material_status==='processing'||row.material_status==='failed'?'暂不计分':fmtDeduction(row.points);
  return `<article class="work-card ${row.status==='void'?'void-row':''}"><div class="work-card-head">${person}${chip}</div><p>${esc(row.occurred_on)} · 扣分 · ${esc(row.deduction_type)} · ${points}</p><p>${esc(row.description)} ${material}</p><div class="work-card-actions">${actions}</div></article>`;
}
function bindEntryActions(done){
  document.querySelectorAll('[data-entry-withdraw]').forEach(button=>button.onclick=async()=>{if(!await confirmModal('撤回签卡','<p>撤回后该条记录不再计分，操作记录仅供高级别导出复查，是否继续？</p>','确认撤回'))return;try{await api('/api/recognitions/'+button.dataset.entryWithdraw,{method:'DELETE'});toast('加分记录已撤回');done()}catch(error){toast(error.message,true)}});
  document.querySelectorAll('[data-entry-void]').forEach(button=>button.onclick=async()=>{const reason=await promptModal('作废扣分记录','请输入作废原因（必填）：','作废原因');if(!reason)return;try{await api('/api/deductions/'+button.dataset.entryVoid+'/void',json('POST',{reason}));toast('扣分记录已作废');done()}catch(error){toast(error.message,true)}});
  document.querySelectorAll('[data-entry-material-retry]').forEach(button=>button.onclick=async()=>{try{const current=(await api('/api/deductions/'+button.dataset.entryMaterialRetry+'/material-status')).record;openDeductionMaterialRetry(current.id,()=>done());}catch(error){toast(error.message,true)}});
  document.querySelectorAll('[data-entry-sick-void]').forEach(button=>button.onclick=async()=>{const reason=await promptModal('作废缺勤记录','请输入作废原因（必填）：','作废原因');if(!reason)return;try{await api('/api/sick-leaves/'+button.dataset.entrySickVoid+'/void',json('POST',{reason}));toast('缺勤记录已作废，全勤分已重新计算');done()}catch(error){toast(error.message,true)}});
}

function reviewCard(r){return `<article class="work-card" data-review-row="${r.id}"><div class="work-card-head"><strong>${esc(r.employee_name)}</strong><small>${esc(r.employee_no)}</small><span class="review-status">${statusBadge(r)}</span></div><p>${esc(r.submitted_at)} · ${esc(r.recognition_type)} · 认可人：${esc(r.recognizer_name)} · ${recognitionScoreText(r)}</p><p>${esc(r.content)}${sameDayDuplicateBadge(r)}${r.image_url?` ${attachmentControl(r.image_url,'查看认可图片',r.image_preview_kind)}`:''}</p>${recognitionScoreNote(r)}${r.review_note?`<small>复核说明：${esc(r.review_note)}</small>`:''}<div class="review-actions">${reviewActions(r)}</div></article>`;}
function reviewTableRow(r){return `<tr data-review-row="${r.id}"><td>${esc(r.submitted_at)}</td><td>${esc(r.employee_name)}<br><small>${esc(r.employee_no)}</small></td><td>${esc(r.recognition_type)} · ${esc(r.recognizer_name)}${sameDayDuplicateBadge(r)}<br>${esc(r.content)}${r.image_url?`<br>${attachmentControl(r.image_url,'查看认可图片',r.image_preview_kind)}`:''}${r.monthly_cap_reason?`<br><small>${esc(r.monthly_cap_reason)}</small>`:''}${r.review_note?`<br><small>复核说明：${esc(r.review_note)}</small>`:''}</td><td>${recognitionScoreText(r)}</td><td class="review-status">${statusBadge(r)}</td><td class="review-actions">${reviewActions(r)}</td></tr>`;}
function replaceReviewRows(record){app.querySelectorAll(`[data-review-row="${record.id}"]`).forEach(row=>{row.outerHTML=row.tagName==='TR'?reviewTableRow(record):reviewCard(record);});bindFilePreviews(app);}
// 组长复核直属组员；主管复核页（正式GSM/AM/OM）复用同一套界面，只换接口。
let reviewApiBase='/api/reviews';
async function renderReview(showHistory=false, offset=0){
  const request=beginViewRequest();
  const view=showHistory?'history':'queue';
  const supervisorQueue=reviewApiBase==='/api/supervisor-reviews';
  const data=await api(reviewApiBase+'?view='+view+'&limit=200&offset='+offset);
  if(!request.isCurrent())return;
  const rows=data.items||[];
  const total=Number(data.total||0);
  const limit=Number(data.limit||200);
  const pageOffset=Number(data.offset||offset||0);
  const hasMore=Boolean(data.has_more);
  const mobileQuery=window.matchMedia('(max-width: 760px)');
  const cards=rows.map(reviewCard).join('')||'<div class="empty">暂无记录</div>';
  const table=`<div class="table-wrap sticky-col"><table><thead><tr><th>提交时间</th><th>${supervisorQueue?'主管':'组员'}</th><th>认可信息</th><th>分值</th><th>状态</th><th>操作</th></tr></thead><tbody>${rows.map(reviewTableRow).join('')||'<tr><td colspan="6" class="empty">暂无记录</td></tr>'}</tbody></table></div>`;
  const hint=showHistory?`已处理记录（已确认和不通过，共${total}条）`:`待复核记录（共${total}条，最早提交的在前）`;
  const pager=(total>limit||pageOffset>0||hasMore)?`<div class="pagination"><button type="button" id="reviewPrev" class="secondary" ${pageOffset<=0?'disabled':''}>上一页</button><span>本页${rows.length}条 · 共${total}条</span><button type="button" id="reviewNext" class="secondary" ${hasMore?'':'disabled'}>下一页</button></div>`:'';
  if(!request.write(`<section class="panel"><div class="record-line"><div><h2>${supervisorQueue?'主管复核':'复核'}</h2><p>${hint}</p></div><button type="button" class="secondary" id="reviewHistoryToggle">${showHistory?'返回待处理':'查看已处理历史'}</button></div>${mobileQuery.matches?`<div class="work-card-list">${cards}</div>`:table}${pager}</section>`))return;
  const handleLayoutChange=()=>{clearPageResources();renderReview(showHistory,pageOffset);};
  mobileQuery.addEventListener?.('change',handleLayoutChange);
  registerPageCleanup(()=>mobileQuery.removeEventListener?.('change',handleLayoutChange));
  document.getElementById('reviewHistoryToggle').onclick=()=>{clearPageResources();renderReview(!showHistory,0);};
  document.getElementById('reviewPrev')?.addEventListener('click',()=>{if(pageOffset<=0)return;clearPageResources();renderReview(showHistory,Math.max(0,pageOffset-limit));});
  document.getElementById('reviewNext')?.addEventListener('click',()=>{if(!hasMore)return;clearPageResources();renderReview(showHistory,pageOffset+limit);});
  bindFilePreviews(app);
  bindReviewActions(showHistory,pageOffset);
}
function reviewActions(r){return r.status==='pending'?`<button data-review="${r.id}" data-action="confirm" class="primary">确认</button> <button data-review="${r.id}" data-action="reject" class="danger">不通过</button>`:`<button data-review="${r.id}" data-action="restore" class="secondary">还原</button>`}
function bindReviewActions(showHistory=false, offset=0){app.querySelectorAll('[data-review]').forEach(b=>b.onclick=async()=>{if(b.dataset.busy==='1')return;const note=b.dataset.action==='reject'?(await promptModal('请输入不通过原因','请填写不通过原因（必填）','不通过原因'))||'':'';if(b.dataset.action==='reject'&&!note)return;b.dataset.busy='1';try{const out=await api(reviewApiBase+'/'+b.dataset.review,json('POST',{action:b.dataset.action,note}));const record=out.record;if(!record){toast('操作成功');return;}if(!showHistory&&record.status!=='pending'){await renderReview(showHistory,offset);}else{replaceReviewRows(record);bindReviewActions(showHistory,offset);}refreshActionBadge();toast('操作成功');}catch(x){toast(x.message,true)}finally{delete b.dataset.busy;}})}
function upgradeReviewCards(items){
  return items.map(row=>`<article class="group-card" data-upgrade="${row.id}"><div class="record-line"><strong>${esc(row.employee_name)} · ${esc(row.employee_no)}</strong><span class="badge warn">待审核</span></div><p>${esc(row.deduction_type)} · 提交人：${esc(row.submitted_by)} · ${esc(row.created_at)}</p><p><strong>A1：</strong>${esc(row.first_record?.occurred_on||'')} · ${esc(row.first_record?.description||'')} ${attachmentControl(row.first_record?.document_url||'','查看A1声明',row.first_record?.document_preview_kind||'')}</p><p><strong>A2：</strong>${esc(row.second_record?.occurred_on||'')} · ${esc(row.second_record?.description||'')} ${attachmentControl(row.second_record?.document_url||'','查看A2声明',row.second_record?.document_preview_kind||'')}</p><div class="actions"><button type="button" class="secondary" data-upgrade-transfer="${row.id}">转交工单</button><button type="button" class="danger" data-upgrade-reject="${row.id}">不通过</button><button type="button" class="primary" data-upgrade-approve="${row.id}">同意升级</button></div></article>`).join('')||'<p class="empty">暂无声明升级待审核工单</p>';
}

function bindUpgradeReviewActions(root,refresh){
  const levels=(state.options.deduction_levels||[]).filter(x=>['MEMO','WARNING_1'].includes(x.code));
  bindFilePreviews(root);
  root.querySelectorAll('[data-upgrade-transfer]').forEach(button=>button.onclick=async()=>{const reviewers=await api('/api/deduction-upgrades/reviewers');const options=reviewers.items||[];const id=await selectModal('转交声明升级工单','请选择新的审核 MOD（GSM / TA GSM）。',options.map(x=>({id:x.id,label:`${x.name} · ${x.employee_no} · ${x.role_name}`})),'下一步');if(!id)return;const reason=await promptModal('转交说明','请输入转交原因（必填）','转交原因','确认转交');if(!reason)return;try{await api('/api/deduction-upgrades/'+button.dataset.upgradeTransfer+'/transfer',json('POST',{reviewer_id:id,reason}));toast('工单已转交');refresh()}catch(error){toast(error.message,true)}});
  root.querySelectorAll('[data-upgrade-reject]').forEach(button=>button.onclick=async()=>{const note=await promptModal('不通过声明升级','请填写处理说明（必填）','处理说明','确认不通过');if(!note)return;try{await api('/api/deduction-upgrades/'+button.dataset.upgradeReject+'/resolve',json('POST',{decision:'reject',handling_note:note}));toast('工单已处理，第二条声明已作废');refresh()}catch(error){toast(error.message,true)}});
  root.querySelectorAll('[data-upgrade-approve]').forEach(button=>button.onclick=async()=>{const levelId=await selectModal('选择升级结果','请选择真实开具的处分结果。',levels.map(x=>({id:x.id,label:`${x.name} · ${fmt(x.points)}分`})),'下一步');if(!levelId)return;const note=await promptModal('处理说明','请输入处理说明（必填）','处理说明','下一步');if(!note)return;const done=await confirmModal('确认真实处分已完成开具',`<p>请确认已完成真实备忘录或一级警告开具。</p><p>确认后系统将生效扣分，第二条声明不计分。</p>`,'是，已完成开具并继续');if(!done)return;try{await api('/api/deduction-upgrades/'+button.dataset.upgradeApprove+'/resolve',json('POST',{decision:'approve',result_level_id:levelId,handling_note:note,issued_confirmed:true}));toast('升级处分已生效');refresh()}catch(error){toast(error.message,true)}});
}

async function renderUpgradeReview(){
  const request=beginViewRequest();
  const data=await api('/api/deduction-upgrades/pending'),items=data.items||[];
  if(!request.isCurrent())return;
  if(!request.write(`<section class="panel"><h2>待审核 · 声明升级审核</h2><p>该页面已并入“待办中心”的待审核标签，此入口仅用于兼容已打开页面。</p><div class="governance-case-list">${upgradeReviewCards(items)}</div></section>`))return;
  bindUpgradeReviewActions(app,renderUpgradeReview);
}

function memberScoreRow(row){
  const button=(kind,label,value,scoreClass='')=>`<button type="button" class="member-score-link ${scoreClass}" data-member-detail="${kind}" data-employee-id="${row.employee_id}" aria-expanded="false" title="查看${label}明细">${value}</button>`;
  return `<tr class="member-score-row" data-member-row="${row.employee_id}"><th><strong>${esc(row.employee_name)}</strong><small>${esc(row.employee_no)} · ${esc(row.role_name)}</small></th><td>${button('recognition','加分','+'+fmt(row.recognition_score),'score-positive')}</td><td>${button('deduction','扣分',fmtDeduction(row.deduction_score),'score-negative')}</td><td>${button('attendance','全勤分',fmt(row.attendance_score))}</td><td>${button('all','综合分',fmt(row.total_score),'member-total-link')}</td></tr>`;
}

function fmtDeduction(value){return Number(value||0)===0?'0.00':`-${fmt(value)}`}

function memberAttachment(record){
  return attachmentControl(record.attachment_url,record.attachment_name||'查看材料',record.attachment_preview_kind);
}

function memberRecordCard(record){
  const scoreClass=record.record_type==='recognition'?'score-positive':record.record_type==='deduction'?'score-negative':'';
  const includedClass=record.included?'included':'excluded';
  const stateClass=record.status==='rejected'?'is-rejected':record.status==='void'?'is-void':record.status==='pending'?'is-pending':'';
  const score=record.record_type==='sick_leave'?'':`<strong class="member-record-score ${scoreClass} ${!record.included?'not-included':''}">${esc(record.score_text)}</strong>`;
  const reason=record.status==='rejected'&&record.reason?`<div class="member-reject-reason"><strong>不通过原因：</strong>${esc(record.reason)}</div>`:'';
  const employee=record.employee_name?`<span class="member-record-employee">${esc(record.employee_name)}${record.employee_no?` · ${esc(record.employee_no)}`:''}</span>`:'';
  return `<article class="member-detail-card ${stateClass} ${!record.included?'is-excluded':''}"><div class="member-detail-top"><div><span class="member-record-type type-${record.record_type}">${esc(record.record_type_name)}</span>${employee}<strong>${esc(record.business_date)} · ${esc(record.title)}</strong></div>${score}</div><p>${esc(record.content)}</p>${reason}<div class="member-detail-meta"><span class="member-record-status status-${esc(record.status)}">${esc(record.status_name)}</span><span>登记人：${esc(record.operator_name)}</span><span class="member-included ${includedClass}">${record.included?'已计入综合分':'未计入综合分'}</span>${memberAttachment(record)}</div></article>`;
}

function memberScoreDetailHtml(row,kind){
  const titles={recognition:'加分明细',deduction:'扣分明细',attendance:'全勤分明细',all:'全部记录'};
  const all=row.details.all_records||[];
  const records=kind==='all'?all:kind==='attendance'?all.filter(record=>['attendance','sick_leave'].includes(record.record_type)):all.filter(record=>record.record_type===kind);
  const {visible,covered}=splitCoveredSickRecords(records);
  const summary=`<div class="member-detail-summary"><span>加分 <strong class="score-positive">+${fmt(row.recognition_score)}</strong></span><span>扣分 <strong class="score-negative">${fmtDeduction(row.deduction_score)}</strong></span><span>全勤 <strong>${fmt(row.attendance_score)}</strong></span><span>综合 <strong>${fmt(row.total_score)}</strong></span></div>`;
  const attendance=row.details.attendance;
  const calculation=kind==='attendance'&&attendance?`<div class="statistics-attendance-summary"><span>基础分 <strong>${fmt(attendance.base_score)}</strong></span><span>全勤奖励 <strong>${fmt(attendance.perfect_bonus)}</strong></span><span>病假扣减 <strong>${fmtDeduction(attendance.sick_deduction)}</strong></span><span>实际 / 计费病假 <strong>${Number(attendance.actual_sick_days).toFixed(1)} / ${attendance.charged_sick_days}天</strong></span></div>`:'';
  return `<section class="panel member-detail-panel"><div class="statistics-detail-heading"><div><h2>${esc(row.employee_name)} · ${titles[kind]}</h2><span>${esc(row.employee_no)} · ${esc(row.role_name)}</span></div><button type="button" class="secondary" data-close-member-detail>关闭</button></div>${summary}${calculation}<div class="member-detail-list">${visible.map(memberRecordCard).join('')||(!covered.length?'<div class="empty">本月暂无相关记录</div>':'')}</div>${coveredSickDisclosure(covered,`<div class="member-detail-list">${covered.map(memberRecordCard).join('')}</div>`)}</section>`;
}

function bindMemberScoreDetails(data){
  const host=document.getElementById('memberDetail'),rowMap=new Map(data.rows.map(row=>[String(row.employee_id),row]));let activeButton=null;
  const close=()=>{if(activeButton){activeButton.setAttribute('aria-expanded','false');activeButton.closest('tr').classList.remove('is-selected')}activeButton=null;host.innerHTML='';};
  document.querySelectorAll('[data-member-detail]').forEach(button=>button.onclick=()=>{const wasActive=activeButton===button;close();if(wasActive)return;const row=rowMap.get(button.dataset.employeeId);if(!row)return;activeButton=button;button.setAttribute('aria-expanded','true');button.closest('tr').classList.add('is-selected');host.innerHTML=memberScoreDetailHtml(row,button.dataset.memberDetail);bindFilePreviews(host);host.querySelector('[data-close-member-detail]').onclick=close;host.scrollIntoView({behavior:prefersReducedMotion()?'auto':'smooth',block:'nearest'});});
}

async function renderMembers(){
  app.innerHTML=`<div class="section-gap"><section class="panel"><h2>组员记录</h2><p>每名直属CM/TR一行汇总，按综合分从高到低排列，同分按工号排序。点击加分、扣分、全勤分查看分类明细；点击综合分查看当月所有记录。</p><form id="memberFilter" class="entry-filter member-score-filter"><label>月份<input name="month" type="month" value="${monthNow()}" required></label><label>员工搜索<input name="keyword" placeholder="姓名/员工号"></label><button class="primary">查询</button></form></section><section class="panel"><div id="memberResults"></div></section><div id="memberDetail"></div></div>`;
  const form=document.getElementById('memberFilter');
  const load=async()=>{const request=beginViewRequest();const qs=new URLSearchParams([...new FormData(form)].filter(([,value])=>value));const data=await api('/api/member-score-summary?'+qs);if(!request.isCurrent())return;document.getElementById('memberResults').innerHTML=`<div class="table-wrap member-score-wrap"><table class="member-score-table"><thead><tr><th>员工</th><th>加分</th><th>扣分</th><th>全勤分</th><th>综合分</th></tr></thead><tbody>${data.rows.map(memberScoreRow).join('')||'<tr><td colspan="5" class="empty">无直属CM/TR或无匹配员工</td></tr>'}</tbody></table></div>`;document.getElementById('memberDetail').innerHTML='';bindMemberScoreDetails(data)};
  form.onsubmit=e=>{e.preventDefault();load()};
  await load();
}

function statisticsHierarchyRows(rows,emptyMessage='当前条件下暂无数据'){
  if(!rows.length)return `<tr><td colspan="6" class="empty">${esc(emptyMessage)}</td></tr>`;
  const scoreButton=(row,kind,value,scoreClass='')=>`<button type="button" class="statistics-score-link ${scoreClass}" data-stats-detail="${kind}" data-employee-ids="${esc((row.employee_ids||[row.employee_id]).join(','))}" data-employee-name="${esc(row.name||row.employee_name)}" data-score="${esc(value)}" aria-expanded="false">${esc(value)}</button>`;
  return rows.map(row=>{
    if(row.node_type!=='employee'){
      const meta=row.node_type==='attraction'?'景点圈':row.node_type==='gsm'?(row.role_name||'GSM'):row.node_type==='gsm_team'?`共同承接 · ${row.supervisor_count||0}个主管 · CM ${row.cm_count||0}人 · TR ${row.tr_count||0}人（共${row.member_count||0}人）`:`${row.role_name||'主管'} · ${row.member_count||0}人`;
      const scores=row.node_type==='supervisor'?`<td>${scoreButton(row,'recognitions','+'+fmt(row.recognition_score),'score-positive')}</td><td>${scoreButton(row,'deductions',fmtDeduction(row.deduction_score),'score-negative')}</td><td>${scoreButton(row,'attendance',fmt(row.attendance_score))}</td><td>${scoreButton(row,'all',fmt(row.total_score),'member-total-link')}</td><td><strong>${fmt(row.average_score)}</strong><small class="average-calculation">${fmt(row.total_score)} ÷ ${row.member_count||0}人</small></td>`:'<td></td><td></td><td></td><td></td><td></td>';
      const expanded=row.node_type!=='supervisor';
      return `<tr class="statistics-org-row ${row.node_type==='supervisor'?'statistics-score-row statistics-supervisor-row':''}" data-stats-node="${esc(row.node_id)}" data-stats-parent="${esc(row.parent_id)}" data-level="${row.level}"><th><button type="button" class="statistics-tree-toggle" data-stats-toggle="${esc(row.node_id)}" aria-expanded="${expanded}"><span class="statistics-tree-arrow">${expanded?'⌄':'›'}</span><strong>${esc(row.name)}</strong><small>${esc(meta)}</small></button></th>${scores}</tr>`;
    }
    return `<tr class="statistics-score-row statistics-employee-row" data-stats-node="${esc(row.node_id)}" data-stats-parent="${esc(row.parent_id)}" data-level="3"><th><strong>${esc(row.employee_name)}</strong><small>${esc(row.employee_no)} · ${esc(row.role_name||row.role_code)}</small>${actingNoteMarkup(row)}</th><td>${scoreButton(row,'recognitions','+'+fmt(row.recognition_score),'score-positive')}</td><td>${scoreButton(row,'deductions',fmtDeduction(row.deduction_score),'score-negative')}</td><td>${scoreButton(row,'attendance',fmt(row.attendance_score))}</td><td>${scoreButton(row,'all',fmt(row.total_score),'member-total-link')}</td><td></td></tr>`;
  }).join('');
}

function statisticsDetailHtml(data,button){
  const ids=(button.dataset.employeeIds||'').split(',').filter(Boolean),kind=button.dataset.statsDetail;
  const employeeRows=new Map(data.hierarchy.filter(row=>row.node_type==='employee').map(row=>[String(row.employee_id),row]));
  let records=[];
  ids.forEach(id=>{const detail=data.details[id]||{all_records:[]},employee=employeeRows.get(id)||{};records.push(...(detail.all_records||[]).map(record=>({...record,employee_name:employee.employee_name||'',employee_no:employee.employee_no||''}))) });
  if(kind==='recognitions')records=records.filter(record=>record.record_type==='recognition');
  else if(kind==='deductions')records=records.filter(record=>record.record_type==='deduction');
  else if(kind==='attendance')records=records.filter(record=>['attendance','sick_leave'].includes(record.record_type));
  records.sort((a,b)=>`${b.business_date}|${b.submitted_at}`.localeCompare(`${a.business_date}|${a.submitted_at}`));
  const titles={recognitions:'加分明细',deductions:'扣分明细',attendance:'全勤分明细',all:'全部记录'};
  const scopeName=ids.length>1?`${button.dataset.employeeName} · ${ids.length}名直属员工`:button.dataset.employeeName;
  return `<section class="panel statistics-detail-panel"><div class="statistics-detail-heading"><div><h2>${esc(scopeName)} · ${titles[kind]}</h2><span>合计 ${esc(button.dataset.score)}；未计入或不通过记录仍会显示</span></div><button type="button" class="secondary" id="closeStatsDetail">关闭</button></div><div class="member-detail-list">${records.map(memberRecordCard).join('')||'<div class="empty">本月暂无相关记录</div>'}</div></section>`;
}

function statisticsDetailKinds(kind){
  if(kind==='recognitions')return ['recognition'];
  if(kind==='deductions')return ['deduction'];
  if(kind==='attendance')return ['attendance','sick_leave'];
  return ['recognition','deduction','attendance','sick_leave'];
}

function statisticsDetailTitle(kind){
  return {recognitions:'加分有效明细',deductions:'扣分有效明细',attendance:'全勤分有效明细',all:'综合分有效明细'}[kind]||'员工有效绩效明细';
}

function statisticsDetailUrl({month,employeeIds,kind,returnFilters={}}){
  const params=new URLSearchParams({month:String(month),employee_ids:(employeeIds||[]).join(','),kind:String(kind||'all')});
  ['attraction_id','title','keyword'].forEach(key=>{const value=returnFilters[key];if(value)params.set(`return_${key}`,String(value));});
  return `${location.pathname}?${params.toString()}`;
}

function openStatisticsDetail(context){
  state.tab='statisticsDetail';
  history.pushState({statisticsDetail:true},'',statisticsDetailUrl(context));
  renderTabs();
  render();
}

async function renderStatisticsDetail(){
  const context=statisticsDetailContext(),month=context.get('month')||monthNow(),employeeIds=(context.get('employee_ids')||'').split(',').filter(value=>/^\d+$/.test(value)),kind=context.get('kind')||'all';
  if(!employeeIds.length){app.innerHTML='<section class="panel"><div class="error">缺少员工明细参数</div></section>';return;}
  const returnFilters={attraction_id:context.get('return_attraction_id')||'',title:context.get('return_title')||'',keyword:context.get('return_keyword')||''};
  app.innerHTML='<section class="panel"><div class="empty">正在加载有效绩效明细…</div></section>';
  try{
    const payload=await api(`/api/statistics/details?month=${encodeURIComponent(month)}&employee_ids=${encodeURIComponent(employeeIds.join(','))}&effective_only=true`),details=payload.details||{},rows=Object.values(details),allowedTypes=new Set(statisticsDetailKinds(kind));
    const records=rows.flatMap(row=>(row.all_records||[]).filter(record=>allowedTypes.has(record.record_type)).map(record=>({...record,employee_name:row.employee_name,employee_no:row.employee_no,role_name:row.role_name}))).sort((a,b)=>`${b.business_date}|${b.submitted_at}`.localeCompare(`${a.business_date}|${a.submitted_at}`));
    const employeeLabel=rows.length===1&&rows[0]?`${rows[0].employee_name} · ${rows[0].employee_no}`:`${rows.length}名员工`;
    app.innerHTML=`<div class="section-gap"><section class="panel statistics-detail-page"><div class="statistics-detail-heading"><div><h2>${esc(employeeLabel)} · ${statisticsDetailTitle(kind)}</h2><span>${esc(month)} · 仅展示已计入综合分的有效数据</span></div><button type="button" class="secondary" id="backToStatistics">返回组织树</button></div><div class="member-detail-list">${records.map(memberRecordCard).join('')||'<div class="empty">当前月份暂无有效记录</div>'}</div></section></div>`;
    bindFilePreviews(app);
    document.getElementById('backToStatistics').onclick=()=>{
      state.tab='statistics';history.pushState({statistics:true},'',location.pathname);
      try{localStorage.setItem(`recognition-v2:statistics-filters:${state.me.employee_no}`,JSON.stringify({month,attraction_id:returnFilters.attraction_id,title:returnFilters.title,keyword:returnFilters.keyword}));}catch(_error){}
      renderTabs();render();
    };
  }catch(error){app.innerHTML=`<section class="panel"><div class="error">${esc(error.message)}</div></section>`;}
}

function bindStatisticsHierarchy(data,{expandEmployees=false}={}){
  const rows=[...document.querySelectorAll('[data-stats-node]')],collapsed=new Set(expandEmployees?[]:rows.filter(row=>row.classList.contains('statistics-supervisor-row')).map(row=>row.dataset.statsNode));
  const rowMap=new Map(rows.map(row=>[row.dataset.statsNode,row]));
  const refresh=()=>rows.forEach(row=>{let parent=row.dataset.statsParent,hidden=false;while(parent){if(collapsed.has(parent)){hidden=true;break}parent=rowMap.get(parent)?.dataset.statsParent||''}row.hidden=hidden});
  document.querySelectorAll('[data-stats-toggle]').forEach(button=>button.onclick=()=>{const id=button.dataset.statsToggle,willCollapse=!collapsed.has(id);if(willCollapse)collapsed.add(id);else collapsed.delete(id);button.setAttribute('aria-expanded',String(!willCollapse));button.querySelector('.statistics-tree-arrow').textContent=willCollapse?'›':'⌄';refresh()});
  document.querySelectorAll('[data-stats-detail]').forEach(button=>button.onclick=()=>openStatisticsDetail({month:data.month,employeeIds:(button.dataset.employeeIds||'').split(',').filter(Boolean),kind:button.dataset.statsDetail,returnFilters:data.filters||{}}));
  refresh();
}

const TREND_SPANS=[3,6,12];
const trendLineColor=name=>({heat:'var(--circle-heat)',dwarf:'var(--circle-dwarf)',bear:'var(--circle-bear)'})[circleTheme(name)]||'var(--muted)';
function trendDelta(points,field){if(points.length<2)return null;const last=Number(points[points.length-1][field]||0),prev=Number(points[points.length-2][field]||0);return {value:last-prev,last,prev};}
function trendDeltaHtml(delta,invert=false){if(!delta||Math.abs(delta.value)<0.005)return '<span class="trend-delta flat">与上月持平</span>';const up=delta.value>0,good=invert?!up:up;return `<span class="trend-delta ${good?'good':'bad'}">${up?'↑':'↓'}${fmt(Math.abs(delta.value))} 较上月 · ${good?'改善':'变差'}</span>`;}
function trendChartSvg(months,lines,width=760){
  if(!months.length)return '<div class="empty">所选范围内没有数据</div>';
  // Drawn in real pixels for the space available, so axis text is not scaled down on phones.
  const w=Math.max(300,Math.min(1200,Math.round(width))),h=w<520?220:260,padL=w<520?42:52,padR=w<520?10:16,padT=18,padB=34,plotW=w-padL-padR,plotH=h-padT-padB;
  const values=lines.flatMap(line=>line.points);
  const rawMax=Math.max(0,...values),rawMin=Math.min(0,...values),span=(rawMax-rawMin)||1;
  const max=rawMax+span*0.08,min=rawMin-span*0.08,range=(max-min)||1;
  const px=i=>padL+(months.length<2?plotW/2:i*plotW/(months.length-1));
  const py=v=>padT+plotH*(1-(v-min)/range);
  const ticks=[0,0.25,0.5,0.75,1].map(t=>min+range*t);
  const grid=ticks.map(v=>`<line x1="${padL}" y1="${py(v).toFixed(1)}" x2="${w-padR}" y2="${py(v).toFixed(1)}" stroke="var(--line)" stroke-width="1"/><text x="${padL-8}" y="${(py(v)+4).toFixed(1)}" text-anchor="end" class="trend-axis">${fmt(v)}</text>`).join('');
  const labelStep=Math.max(1,Math.ceil(months.length/Math.max(1,Math.floor(plotW/40))));
  const labels=months.map((month,i)=>(months.length-1-i)%labelStep?'':(i===months.length-1&&i>0?`<text x="${w-2}" y="${h-10}" text-anchor="end" class="trend-axis">${esc(month.slice(2))}</text>`:`<text x="${px(i).toFixed(1)}" y="${h-10}" text-anchor="middle" class="trend-axis">${esc(month.slice(2))}</text>`)).join('');
  const paths=lines.map(line=>{
    const d=line.points.map((v,i)=>`${i?'L':'M'}${px(i).toFixed(1)} ${py(v).toFixed(1)}`).join(' ');
    const dots=line.points.map((v,i)=>`<circle cx="${px(i).toFixed(1)}" cy="${py(v).toFixed(1)}" r="3" fill="${line.color}"><title>${esc(line.name)} ${esc(months[i])}：${fmt(v)}分</title></circle>`).join('');
    return `<path d="${d}" fill="none" stroke="${line.color}" stroke-width="${line.emphasis?2.5:1.8}" stroke-linejoin="round" stroke-linecap="round"${line.emphasis?'':' stroke-dasharray="0"'}/>${dots}`;
  }).join('');
  return `<svg class="trend-chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="月度总分趋势"><line x1="${padL}" y1="${padT}" x2="${padL}" y2="${h-padB}" stroke="var(--line)" stroke-width="1"/>${grid}${labels}${paths}</svg>`;
}
function trendLegendHtml(lines){return `<div class="trend-legend">${lines.map(line=>`<span><i style="background:${line.color}"></i>${esc(line.name)}</span>`).join('')}</div>`;}
function trendTableHtml(data){
  const rows=data.overall.map((row,i)=>{
    const prev=i?data.overall[i-1]:null;
    const diff=prev?row.total_score-prev.total_score:null;
    return `<tr><td>${esc(row.month)}</td><td>${row.employee_count}</td><td>${fmt(row.recognition_score)}</td><td>${fmt(row.attendance_score)}</td><td>${fmt(row.deduction_score)}</td><td><strong>${fmt(row.total_score)}</strong></td><td>${diff===null?'—':`<span class="trend-delta ${Math.abs(diff)<0.005?'flat':diff>0?'good':'bad'}">${Math.abs(diff)<0.005?'持平':`${diff>0?'↑':'↓'}${fmt(Math.abs(diff))} ${diff>0?'改善':'变差'}`}</span>`}</td></tr>`;
  }).join('');
  return `<div class="table-wrap sticky-col"><table><thead><tr><th>月份</th><th>计分人数</th><th>认可</th><th>出勤</th><th>扣分</th><th>总分</th><th>较上月</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}
function trendResultsHtml(data){
  const latest=data.overall[data.overall.length-1];
  if(!latest)return '<section class="panel"><div class="empty">所选范围内没有数据</div></section>';
  const lines=[{name:'整体',color:'var(--primary)',emphasis:true,points:data.overall.map(row=>Number(row.total_score||0))},
    ...data.by_attraction.map(circle=>({name:circle.attraction_name,color:trendLineColor(circle.attraction_name),points:circle.series.map(point=>Number(point.total_score||0))}))];
  const cards=[['总分','total_score',false],['认可','recognition_score',false],['出勤','attendance_score',false],['扣分','deduction_score',true]]
    .map(([label,field,invert])=>`<div class="trend-card"><span class="trend-card-label">${label}</span><strong>${fmt(latest[field])}</strong>${trendDeltaHtml(trendDelta(data.overall,field),invert)}</div>`).join('');
  return `<section class="panel"><div class="statistics-page-heading"><h3>${esc(data.months[0])} 至 ${esc(data.end_month)} 趋势</h3><span class="field-hint">共 ${data.overall.length} 个月 · 不含未设置景点圈，计分规则不变</span></div><div class="trend-cards">${cards}</div>${trendChartSvg(data.months,lines,(app.clientWidth||760)-(window.matchMedia('(max-width: 760px)').matches?26:38))}${trendLegendHtml(lines)}${trendTableHtml(data)}</section>`;
}
async function renderStatistics(){
  const canExport=has('DATA_EXPORT');
  app.innerHTML=`<div class="section-gap"><section class="panel"><div class="statistics-page-heading"><h2>${canExport?'景点数据统计与月结导出':'景点数据统计'}</h2><span class="access-mode ${canExport?'can-export':'read-only'}">${canExport?'可导出':'只读'}</span></div><form id="statsFilter" class="statistics-filter"><label>月份<input name="month" type="month" value="${monthNow()}" required></label><label>景点圈<select name="attraction_id"><option value="">全部</option>${opt(state.options.employee_circles||state.options.attractions)}</select></label><label>Title<select name="title"><option value="">CM/TR全部</option><option value="CM">CM</option><option value="TR">TR</option><option value="SUPERVISOR">主管</option></select></label><label>员工搜索<input name="keyword" placeholder="姓名/员工号"></label><div class="statistics-actions"><button class="primary">查询统计</button>${canExport?' <button type="button" id="exportBtn" class="secondary">导出 Excel</button>':''}</div></form>${canExport?'<div id="recentExports" class="recent-exports"><span class="field-hint">正在加载最近导出记录…</span></div>':''}</section><section class="panel statistics-views"><div class="statistics-view-tabs" role="tablist"><button type="button" class="primary" data-stats-view="month" role="tab" aria-selected="true">当月统计</button><button type="button" class="secondary" data-stats-view="trend" role="tab" aria-selected="false">月度趋势</button></div><label class="trend-span" hidden>回溯月数<select id="trendSpan">${TREND_SPANS.map(n=>`<option value="${n}"${n===6?' selected':''}>${n} 个月</option>`).join('')}</select></label></section><div id="statsResults"></div></div>`;
  const form=document.getElementById('statsFilter');
  const preferenceKey=`recognition-v2:statistics-filters:${state.me.employee_no}`,queryButton=form.querySelector('button[type="submit"],button:not([type])'),resultsHost=document.getElementById('statsResults');
  try{const saved=JSON.parse(localStorage.getItem(preferenceKey)||'{}');['month','attraction_id','title','keyword'].forEach(name=>{if(!Object.prototype.hasOwnProperty.call(saved,name))return;const field=form.elements[name],value=String(saved[name]??'');if(name==='month'&&!value)return;if(field&&(!field.options||[...field.options].some(option=>option.value===value)))field.value=value})}catch(_error){}
  let lastStatistics=null,loadSequence=0;
  const saveFilters=()=>{try{localStorage.setItem(preferenceKey,JSON.stringify({month:form.elements.month.value,attraction_id:form.elements.attraction_id.value,title:form.elements.title.value,keyword:form.elements.keyword.value}))}catch(_error){}};
  const emptyMessage=formData=>{const values=Object.fromEntries(formData);if(String(values.keyword||'').trim())return '没有匹配当前姓名或员工号的参与计分人员';if(values.attraction_id)return '该景点圈本月没有参与计分人员';if(values.title)return `本月没有符合 ${values.title} 条件的参与计分人员`;return '本月尚无业务数据或参与计分人员'};
  const load=async()=>{const requestId=++loadSequence,formData=[...new FormData(form)],qs=new URLSearchParams(formData.filter(([,v])=>v)),expandEmployees=Boolean(String(formData.find(([key])=>key==='keyword')?.[1]||'').trim()),originalText=queryButton.textContent;saveFilters();queryButton.disabled=true;queryButton.textContent='查询中…';resultsHost.innerHTML='<section class="panel"><div class="empty">正在加载统计数据…</div></section>';try{const d=await api('/api/statistics?'+qs);if(requestId!==loadSequence)return;lastStatistics=d;const selectedTitle=d.title||'',averageLabel=selectedTitle?`${selectedTitle}小组平均分`:'CM/TR小组平均分',attractionSelect=form.elements.attraction_id,attractionLabel=attractionSelect.selectedOptions[0]?.textContent||'全部',updatedLabel=d.data_updated_at||'暂无业务更新',organizationBasis=d.organization_basis||'当前组织归属';resultsHost.innerHTML=`<section class="panel"><h2>员工综合分</h2><p class="statistics-scope">统计范围：${esc(d.month)} · ${esc(attractionLabel)} · ${esc(selectedTitle==='SUPERVISOR'?'主管':(selectedTitle||'CM/TR全部'))}；数据更新至 ${esc(updatedLabel)}；组织口径：${esc(organizationBasis)}；平均分按筛选后小组综合分 ÷ 筛选后人数计算。</p><div class="table-wrap"><table class="statistics-hierarchy-table"><thead><tr><th>组织 / 员工</th><th>加分</th><th>扣分</th><th>全勤分</th><th>小组综合分</th><th>${esc(averageLabel)}</th></tr></thead><tbody>${statisticsHierarchyRows(d.hierarchy,emptyMessage(formData))}</tbody></table></div></section><div id="statisticsDetail"></div>`;bindStatisticsHierarchy(d,{expandEmployees})}catch(error){if(requestId===loadSequence)resultsHost.innerHTML=`<section class="panel"><div class="error">${esc(error.message)}</div></section>`}finally{if(requestId===loadSequence){queryButton.disabled=false;queryButton.textContent=originalText}}};
  const loadRecentExports=async()=>{if(!canExport)return;const host=document.getElementById('recentExports');try{const data=await api('/api/statistics/my-exports');host.innerHTML=data.items.length?`<h3>本人最近景点数据导出</h3><div class="table-wrap"><table class="recent-export-table"><thead><tr><th>导出时间</th><th>月份</th><th>景点圈</th><th>Title</th><th>员工搜索</th><th>员工数</th></tr></thead><tbody>${data.items.map(row=>`<tr><td>${esc(row.exported_at)}</td><td>${esc(row.month)}</td><td>${esc(row.attraction_name)}</td><td>${esc(row.title)}</td><td>${esc(row.keyword||'无')}</td><td>${row.employee_count}</td></tr>`).join('')}</tbody></table></div>`:'<span class="field-hint">当前账号暂无景点数据导出记录</span>'}catch(error){host.innerHTML=`<span class="field-hint">最近导出记录暂时无法加载：${esc(error.message)}</span>`}};
  const spanLabel=app.querySelector('.trend-span'),spanSelect=document.getElementById('trendSpan'),viewButtons=[...app.querySelectorAll('[data-stats-view]')];let statsView='month';
  const loadTrend=async()=>{const requestId=++loadSequence,qs=new URLSearchParams();qs.set('month',form.elements.month.value||monthNow());if(form.elements.attraction_id.value)qs.set('attraction_id',form.elements.attraction_id.value);if(form.elements.title.value)qs.set('title',form.elements.title.value);qs.set('months',spanSelect.value);resultsHost.innerHTML='<section class="panel"><div class="empty">正在加载趋势数据…</div></section>';try{const d=await api('/api/statistics/trend?'+qs);if(requestId!==loadSequence)return;resultsHost.innerHTML=trendResultsHtml(d);}catch(error){if(requestId!==loadSequence)return;resultsHost.innerHTML=`<section class="panel"><div class="empty">${esc(error.message)}</div></section>`;toast(error.message,true);}};
  const loadCurrent=()=>statsView==='trend'?loadTrend():load();
  viewButtons.forEach(button=>button.onclick=()=>{if(statsView===button.dataset.statsView)return;statsView=button.dataset.statsView;viewButtons.forEach(other=>{const on=other.dataset.statsView===statsView;other.className=on?'primary':'secondary';other.setAttribute('aria-selected',on?'true':'false');});spanLabel.hidden=statsView!=='trend';const exportButton=document.getElementById('exportBtn');if(exportButton)exportButton.hidden=statsView==='trend';loadCurrent();});
  spanSelect.onchange=()=>{if(statsView==='trend')loadCurrent();};
  form.onsubmit=e=>{e.preventDefault();if(!queryButton.disabled)loadCurrent()};
  if(canExport){const exportButton=document.getElementById('exportBtn');exportButton.onclick=async()=>{if(exportButton.disabled)return;const formData=[...new FormData(form)],values=Object.fromEntries(formData),attractionLabel=form.elements.attraction_id.selectedOptions[0]?.textContent||'全部',titleLabel=values.title==='SUPERVISOR'?'主管':(values.title||'CM/TR全部'),keywordLabel=String(values.keyword||'').trim(),count=lastStatistics?.summary?.employee_count??0,loaCount=lastStatistics?.loa_rows?.length??0,confirmed=await confirmModal('确认导出景点数据',`<p><strong>${esc(values.month)}</strong> · ${esc(attractionLabel)} · ${esc(titleLabel)}</p>${keywordLabel?`<p>员工搜索：${esc(keywordLabel)}</p>`:''}<p>预计包含 ${count} 名参与计分人员${loaCount?`，另有 ${loaCount} 名整月LOA员工`:''}。</p>`,'导出 Excel');if(!confirmed)return;exportButton.disabled=true;exportButton.textContent='准备导出…';const qs=new URLSearchParams(formData.filter(([,v])=>v));location.href=portalPath('/api/statistics/export?'+qs);setTimeout(()=>{exportButton.disabled=false;exportButton.textContent='导出 Excel';loadRecentExports()},1400)}}
  await Promise.all([loadCurrent(),loadRecentExports()]);
}

const declarationLevelTones={STATEMENT:0,MEMO:1,WARNING_1:2,WARNING_2:3};
function declarationLevelTone(code){if(Object.prototype.hasOwnProperty.call(declarationLevelTones,code))return declarationLevelTones[code];return [...String(code||'')].reduce((n,ch)=>n+ch.charCodeAt(0),0)%6;}
function declarationStatusBadge(value){const tone=value==='已生效'?'ok':value==='已作废'?'danger':'warn';return `<span class="badge status-chip ${tone}">${esc(value)}</span>`;}
function declarationRecordDetails(rows){
  if(!rows.length)return '<div class="empty">当前范围没有符合条件的记录</div>';
  const material=row=>row.document_url?`<button type="button" class="secondary declaration-material" data-file-preview="${esc(row.document_url)}" data-file-kind="pdf" data-file-title="${esc(row.employee_name)}的处分材料">查看材料</button> <a class="secondary declaration-material" href="${esc(portalPath(row.document_url))}" download>下载材料</a>`:'<span class="field-hint">暂无可查看材料</span>';
  const level=row=>`<span class="badge declaration-level tone-${declarationLevelTone(row.level_code)}">${esc(row.level_name)}</span>`;
  return `<div class="table-wrap declaration-desktop"><table><thead><tr><th>员工 / 工号</th><th>原景点圈</th><th>事件日期</th><th>处分类型 / 等级</th><th>内容</th><th>登记人 / 时间</th><th>材料状态</th><th>审核或升级状态</th><th>业务状态</th><th>材料</th></tr></thead><tbody>${rows.map(row=>`<tr><td><strong>${esc(row.employee_name)}</strong><br><small>${esc(row.employee_no)}</small></td><td>${esc(row.attraction_name)}</td><td>${esc(row.occurred_on)}</td><td>${esc(row.type_name)}<br>${level(row)}</td><td>${esc(row.description)}</td><td>${esc(row.submitter_name)}<br><small>${esc(row.submitted_at)}</small></td><td>${esc(row.material_status)}</td><td>${esc(row.upgrade_status)}</td><td>${declarationStatusBadge(row.business_status)}</td><td>${material(row)}</td></tr>`).join('')}</tbody></table></div><div class="declaration-mobile">${rows.map(row=>`<article class="declaration-record"><div class="record-line"><strong>${esc(row.employee_name)} · ${esc(row.employee_no)}</strong>${declarationStatusBadge(row.business_status)}</div><p>${esc(row.attraction_name)} · ${esc(row.occurred_on)}</p><div class="record-line"><span>${esc(row.type_name)}</span>${level(row)}</div><p>${esc(row.description)}</p><small>登记：${esc(row.submitter_name)} · ${esc(row.submitted_at)}</small><small>材料：${esc(row.material_status)} · 审核/升级：${esc(row.upgrade_status)}</small>${material(row)}</article>`).join('')}</div>`;
}
async function renderDeclarationStatistics(){
  if(!has('DECLARATION_STATS_VIEW')){app.innerHTML='<section class="panel"><div class="error">当前账号没有声明登记统计查看权限</div></section>';return;}
  const request=beginViewRequest();
  const initial=await api('/api/declaration-statistics?month='+encodeURIComponent(monthNow()));
  if(!request.isCurrent())return;
  const levelOptions=initial.levels.map(row=>`<option value="${row.id}">${esc(row.name)}</option>`).join('');
  const circleOptions=initial.attractions.map(row=>`<option value="${row.id}">${esc(row.name)}</option>`).join('');
  const typeOptions=initial.types.map(row=>`<option value="${row.id}">${esc(row.name)}</option>`).join('');
  const canExport=has('DECLARATION_STATS_EXPORT');
  if(!request.write(`<div class="section-gap declaration-statistics-page"><section class="panel"><div class="statistics-page-heading"><h2>声明登记统计</h2><span class="access-mode ${canExport?'can-export':'read-only'}">${canExport?'可导出':'只读'}</span></div><p>汇总全部处分等级的登记事件；景点圈按登记时归属统计，升级关联记录不重复计数。</p><form id="declarationFilter" class="statistics-filter declaration-filter"><label>月份<input name="month" type="month" value="${monthNow()}" required></label><label>景点圈<select name="attraction_id"><option value="">全部景点圈</option>${circleOptions}</select></label><label>处分等级<select name="level_id"><option value="">全部等级</option>${levelOptions}</select></label><label>处分类型<select name="type_id"><option value="">全部类型</option>${typeOptions}</select></label><label>业务状态<select name="status"><option value="">全部状态</option>${['已生效','待补材料','审核中','已作废'].map(name=>`<option value="${name}">${name}</option>`).join('')}</select></label><label>员工搜索<input name="keyword" placeholder="姓名或工号"></label><div class="statistics-actions"><button type="submit" class="primary">查询</button>${canExport?'<button type="button" class="secondary" id="declarationExport">导出 Excel</button>':''}</div></form></section><div id="declarationResults"></div></div>`))return;
  const form=document.getElementById('declarationFilter'),host=document.getElementById('declarationResults');
  let sequence=0;
  const show=data=>{
    const cards=`<div class="declaration-kpis"><div class="trend-card"><span class="trend-card-label">总记录数</span><strong>${data.total_records}</strong></div><div class="trend-card"><span class="trend-card-label">涉及人数</span><strong>${data.employee_count}</strong></div>${data.levels.map(row=>`<div class="trend-card declaration-kpi tone-${declarationLevelTone(row.code)}"><span class="trend-card-label">${esc(row.name)}</span><strong>${row.count}</strong></div>`).join('')}</div>`;
    const statuses=`<div class="declaration-statuses">${Object.entries(data.statuses).map(([name,count])=>`<span class="declaration-status-count">${declarationStatusBadge(name)}<strong>${count} 条</strong></span>`).join('')}</div>`;
    const circles=data.circles.map(circle=>{
      const key=circle.attraction_id===null?'none':String(circle.attraction_id);
      const rows=data.records.filter(row=>(row.attraction_id===null?'none':String(row.attraction_id))===key);
      return `<section class="declaration-circle"><button type="button" class="declaration-circle-toggle" data-declaration-circle="${esc(key)}" aria-expanded="false"><strong>${esc(circle.attraction_name)}</strong><span>${circle.record_count} 条 · ${circle.employee_count} 人</span><span aria-hidden="true">⌄</span></button><div class="declaration-inline" data-declaration-circle-detail="${esc(key)}" hidden></div><div class="declaration-levels">${circle.levels.map(level=>`<div class="declaration-level-item"><button type="button" class="declaration-level-toggle tone-${declarationLevelTone(level.code)}" data-declaration-level="${esc(key)}:${level.id}" aria-expanded="false"><span>${esc(level.name)}</span><strong>${level.count} 条</strong><span aria-hidden="true">⌄</span></button><div class="declaration-inline" data-declaration-level-detail="${esc(key)}:${level.id}" hidden></div></div>`).join('')}</div></section>`;
    }).join('')||'<div class="empty">当前筛选范围暂无声明登记记录</div>';
    host.innerHTML=`<section class="panel"><h2>月度汇总</h2><p class="statistics-scope">${esc(data.month)} · ${data.total_records} 条记录；点击景点圈或处分等级可展开明细，再次点击收起。</p>${cards}${statuses}<div class="declaration-circle-list">${circles}</div></section>`;
    host.querySelectorAll('[data-declaration-circle]').forEach(button=>button.onclick=()=>{const key=button.dataset.declarationCircle,detail=host.querySelector(`[data-declaration-circle-detail="${key}"]`),open=detail.hidden;detail.hidden=!open;detail.innerHTML=open?declarationRecordDetails(data.records.filter(row=>(row.attraction_id===null?'none':String(row.attraction_id))===key)):'';button.setAttribute('aria-expanded',String(open));if(open)bindFilePreviews(detail);});
    host.querySelectorAll('[data-declaration-level]').forEach(button=>button.onclick=()=>{const key=button.dataset.declarationLevel,[circleId,levelId]=key.split(':'),detail=host.querySelector(`[data-declaration-level-detail="${key}"]`),open=detail.hidden;detail.hidden=!open;detail.innerHTML=open?declarationRecordDetails(data.records.filter(row=>(row.attraction_id===null?'none':String(row.attraction_id))===circleId&&String(row.level_id)===levelId)):'';button.setAttribute('aria-expanded',String(open));if(open)bindFilePreviews(detail);});
  };
  const params=()=>new URLSearchParams([...new FormData(form)].filter(([,value])=>value));
  const load=async()=>{const id=++sequence,button=form.querySelector('button[type="submit"]');button.disabled=true;host.innerHTML='<section class="panel"><div class="empty">正在加载统计数据…</div></section>';try{const data=await api('/api/declaration-statistics?'+params());if(id===sequence&&app.contains(form))show(data);}catch(error){if(id===sequence&&app.contains(form))host.innerHTML=`<section class="panel"><div class="error">${esc(error.message)}</div></section>`;}finally{if(id===sequence)button.disabled=false;}};
  form.onsubmit=event=>{event.preventDefault();void load();};
  if(canExport)document.getElementById('declarationExport').onclick=async()=>{const confirmed=await confirmModal('导出声明登记统计',`<p>将按当前筛选条件导出 ${esc(form.elements.month.value)} 的景点圈、等级汇总和逐条明细。</p>`,'导出 Excel');if(confirmed)location.href=portalPath('/api/declaration-statistics/export?'+params());};
  show(initial);
}

const prCategoryNames={overall:'综合分',recognition:'加分类型',deduction:'扣分类型',absence:'缺勤',leader:'主管发放排行',gsm_leader:'GSM/TA GSM认可次数'};
function prRankBadge(rank){return `<span class="pr-rank-badge ${rank<=3?`top-${rank}`:''}">${rank}</span>`;}
function prEmployeeCell(row,label='员工'){return `<strong>${esc(row.employee_name)}</strong><small>${esc(row.employee_no)} · ${esc(row.role_name||label)}</small>${actingNoteMarkup(row)}`;}
function actingNoteMarkup(row){return row.acting_note?`<small class="field-hint acting-note">${esc(row.acting_note)}</small>`:'';}
function prRecognitionCount(row,data){return `${esc(row.count)}${data.uncapped_ranking&&Number(row.score)>=5?` <small>（未封顶${fmt(row.uncapped_score)}分）</small>`:''}`;}
function prRankingCards(data){
  const field=(label,value,className='')=>`<div><dt>${label}</dt><dd class="${className}">${value}</dd></div>`;
  const cards=data.rows.map(row=>{
    let fields=[];
    if(data.category==='overall')fields=[field('角色',esc(row.role_name)),field('小组',esc(row.group_label||'未分组')),field('加分',`+${fmt(row.recognition_score)}`,'score-positive'),field('扣分',fmtDeduction(row.deduction_score),'score-negative'),field('全勤分',fmt(row.attendance_score)),field('综合分',fmt(row.total_score),'pr-total-score')];
    else if(data.category==='absence')fields=[field('角色',esc(row.role_name)),field('小组',esc(row.group_label||'未分组')),field('登记次数',esc(row.count)),field('缺勤天数',Number(row.leave_days||0).toFixed(1)),field('扣减全勤分',fmtDeduction(row.score),'score-negative'),field('最近缺勤',esc(row.recent_date||'—'))];
    else if(['leader','gsm_leader'].includes(data.category))fields=[field('角色',esc(row.role_name)),field('加分次数',esc(row.count)),field('累计加分',`+${fmt(row.score)}`,'score-positive'),field('最近一次加分',esc(row.recent_date||'—'))];
    else {const recognition=data.category==='recognition';fields=[field('角色',esc(row.role_name)),field('小组',esc(row.group_label||'未分组')),field('登记次数',prRecognitionCount(row,data)),field(recognition?'累计加分':'累计扣分',recognition?`+${fmt(row.score)}`:fmtDeduction(row.score),recognition?'score-positive':'score-negative'),field(recognition?'最近加分':'最近扣分',esc(row.recent_date||'—'))]}
    return `<article class="pr-ranking-card ${row.rank<=3?'pr-top-card':''}" role="listitem"><header><div>${prRankBadge(row.rank)}<span>第 ${row.rank} 名</span></div><div class="pr-card-employee">${prEmployeeCell(row,data.category==='leader'?'主管':'员工')}</div></header><dl>${fields.join('')}</dl></article>`;
  }).join('');
  return `<div class="pr-ranking-cards" role="list">${cards||'<div class="empty">当前条件下暂无排名数据</div>'}</div>`;
}
function prRankingTable(data){
  const commonHead='<th>排名</th><th>员工</th><th>角色</th>';
  let head='',rows='';
  if(data.category==='overall'){
    head=`${commonHead}<th>小组</th><th>加分</th><th>扣分</th><th>全勤分</th><th>综合分</th>`;
    rows=data.rows.map(row=>`<tr class="${row.rank<=3?'pr-top-row':''}"><td>${prRankBadge(row.rank)}</td><td>${prEmployeeCell(row)}</td><td>${esc(row.role_name)}</td><td>${esc(row.group_label||'未分组')}</td><td class="score-positive">+${fmt(row.recognition_score)}</td><td class="score-negative">${fmtDeduction(row.deduction_score)}</td><td>${fmt(row.attendance_score)}</td><td class="pr-total-score">${fmt(row.total_score)}</td></tr>`).join('');
  }else if(data.category==='absence'){
    head=`${commonHead}<th>小组</th><th>登记次数</th><th>缺勤天数</th><th>扣减全勤分</th><th>最近一次缺勤</th>`;
    rows=data.rows.map(row=>`<tr class="${row.rank<=3?'pr-top-row':''}"><td>${prRankBadge(row.rank)}</td><td>${prEmployeeCell(row)}</td><td>${esc(row.role_name)}</td><td>${esc(row.group_label||'未分组')}</td><td>${row.count}</td><td>${Number(row.leave_days||0).toFixed(1)}</td><td class="score-negative">${fmtDeduction(row.score)}</td><td>${esc(row.recent_date||'—')}</td></tr>`).join('');
  }else if(['leader','gsm_leader'].includes(data.category)){
    const personLabel=data.category==='gsm_leader'?'GSM / TA GSM':'主管';
    head=`<th>排名</th><th>${personLabel}</th><th>角色</th><th>加分次数</th><th>累计加分</th><th>最近一次加分</th>`;
    rows=data.rows.map(row=>`<tr class="${row.rank<=3?'pr-top-row':''}"><td>${prRankBadge(row.rank)}</td><td>${prEmployeeCell(row,personLabel)}</td><td>${esc(row.role_name)}</td><td>${row.count}</td><td class="score-positive">+${fmt(row.score)}</td><td>${esc(row.recent_date||'—')}</td></tr>`).join('');
  }else{
    const scoreLabel=data.category==='recognition'?'累计加分':'累计扣分',dateLabel=data.category==='recognition'?'最近一次加分':'最近一次扣分';
    head=`${commonHead}<th>小组</th><th>登记次数</th><th>${scoreLabel}</th><th>${dateLabel}</th>`;
    rows=data.rows.map(row=>`<tr class="${row.rank<=3?'pr-top-row':''}"><td>${prRankBadge(row.rank)}</td><td>${prEmployeeCell(row)}</td><td>${esc(row.role_name)}</td><td>${esc(row.group_label||'未分组')}</td><td>${prRecognitionCount(row,data)}</td><td class="${data.category==='recognition'?'score-positive':'score-negative'}">${data.category==='recognition'?'+':''}${data.category==='recognition'?fmt(row.score):fmtDeduction(row.score)}</td><td>${esc(row.recent_date||'—')}</td></tr>`).join('');
  }
  const pageButtons=[];for(let page=1;page<=data.pages;page++){if(page===1||page===data.pages||Math.abs(page-data.page)<=1)pageButtons.push(`<button type="button" class="${page===data.page?'active':''}" data-pr-page="${page}">${page}</button>`);else if(pageButtons[pageButtons.length-1]!=='<span>…</span>')pageButtons.push('<span>…</span>')}
  return `<div class="pr-result-meta"><span>共 <strong>${data.total}</strong> 人 · ${esc(data.attraction_name)} · ${esc(data.start_date)} 至 ${esc(data.end_date)}</span>${data.subtype_name?`<span>当前类型：<strong>${esc(data.subtype_name)}</strong></span>`:''}${data.uncapped_ranking?'<span>未封顶分数按已生效记录原始分值合计；累计分值排名使用此分数，不改变实际绩效计分。</span>':''}</div>${prRankingCards(data)}<div class="table-wrap pr-ranking-wrap"><table class="pr-ranking-table"><thead><tr>${head}</tr></thead><tbody>${rows||`<tr><td colspan="9" class="empty">当前条件下暂无排名数据</td></tr>`}</tbody></table></div><div class="pr-pagination"><button type="button" data-pr-page="${Math.max(1,data.page-1)}" ${data.page<=1?'disabled':''}>上一页</button>${pageButtons.join('')}<button type="button" data-pr-page="${Math.min(data.pages,data.page+1)}" ${data.page>=data.pages?'disabled':''}>下一页</button><span>${data.page_size}条/页</span></div>`;
}

async function renderPrRankings(){
  let category='overall',sortBy='score',page=1,requestSequence=0;
  const circles=state.options.employee_circles||state.options.attractions||[],selectedCircle=String(state.me.attraction_id||''),canExport=['GSM','AM','OM'].includes(state.me.role_code);
  const circleOptions=`<option value="">全部景点圈</option>${circles.map(row=>`<option value="${esc(row.id)}" ${String(row.id)===selectedCircle?'selected':''}>${esc(row.name)}</option>`).join('')}`;
  app.innerHTML=`<div class="section-gap pr-ranking-page"><section class="panel"><h2>PR排名数据</h2><p>TA GSM、GSM、AM和OM均可查询三个景点圈或全部景点圈；各榜单按需加载，不会一次读取全部排名。</p><form id="prFilter" class="pr-ranking-filter"><label>景点圈<select name="attraction_id">${circleOptions}</select></label><label>开始日期<input name="start_date" type="date" value="${monthStart()}" max="${today()}" required></label><label>结束日期<input name="end_date" type="date" value="${today()}" max="${today()}" required></label><label>排名人群<select name="population"><option value="frontline">CM/TR</option><option value="supervisor">主管（主管绩效排行）</option></select></label><label>员工搜索<input name="keyword" placeholder="搜索姓名或工号"></label><div class="pr-ranking-actions"><button class="primary">查询</button>${canExport?'<button type="button" id="prExport" class="secondary">导出</button>':'<span class="field-hint">TA GSM仅支持查询</span>'}</div></form></section><section class="panel pr-ranking-data"><div id="prCategoryTabs" class="pr-category-tabs">${Object.entries(prCategoryNames).map(([key,name])=>`<button type="button" data-pr-category="${key}" class="${key===category?'active':''}">${name}</button>`).join('')}</div><div id="prSubfilters" class="pr-subfilters"></div><div id="prResults"><div class="empty">正在加载排名…</div></div></section></div>`;
  const form=document.getElementById('prFilter'),subfilters=document.getElementById('prSubfilters'),results=document.getElementById('prResults');
  const recognitionTypes=state.options.recognition_types||[],deductionTypes=state.options.deduction_types||[];
  const renderSubfilters=()=>{
    let selector='';
    if(category==='recognition')selector=`<label>认可类型<select name="subtype_id"><option value="">全部加分类别</option>${opt(recognitionTypes)}</select></label>`;
    if(category==='deduction')selector=`<label>扣分类型<select name="subtype_id"><option value="">全部扣分类型</option>${opt(deductionTypes)}</select></label>`;
    if(category==='absence')selector='<label>缺勤类型<select name="subtype_id"><option value="">全部缺勤类型</option><option value="1">病假</option><option value="2">违规病假</option></select></label>';
    if(category==='leader'||category==='gsm_leader')selector=`<label>加分类别<select name="subtype_id"><option value="">全部加分类别</option>${opt(recognitionTypes)}</select></label>`;
    const sortControl=['recognition','deduction','absence'].includes(category)?`<div class="pr-sort-control"><span>排名依据</span><button type="button" data-pr-sort="score" class="${sortBy==='score'?'active':''}">累计分值</button><button type="button" data-pr-sort="count" class="${sortBy==='count'?'active':''}">登记次数</button></div>`:'';
    const hint=category==='leader'?'按已确认加分逐条统计：签卡人或代录人为主管即计入；同一条记录同一主管只计一次。':category==='gsm_leader'?'按认可日角色统计TA GSM/GSM参与的已确认加分；同一条记录同一人只计一次。':'切换类型后仅加载当前榜单';
    subfilters.innerHTML=`${selector}${sortControl}<span class="field-hint">${hint}</span>`;
    subfilters.querySelector('select')?.addEventListener('change',()=>{page=1;load()});
    subfilters.querySelectorAll('[data-pr-sort]').forEach(button=>button.onclick=()=>{sortBy=button.dataset.prSort;page=1;renderSubfilters();load()});
  };
  const params=()=>{const qs=new URLSearchParams([...new FormData(form)].filter(([,value])=>value));qs.set('category',category);qs.set('sort_by',sortBy);qs.set('page',String(page));qs.set('page_size','20');const subtype=subfilters.querySelector('[name=subtype_id]')?.value;if(subtype)qs.set('subtype_id',subtype);return qs};
  const updateExportState=()=>{const button=document.getElementById('prExport');if(!button)return;button.disabled=false;button.title='';};
  const load=async()=>{const requestId=++requestSequence;results.innerHTML='<div class="empty">正在加载排名…</div>';try{const data=await api('/api/pr-rankings?'+params());if(requestId!==requestSequence)return;results.innerHTML=prRankingTable(data);results.querySelectorAll('[data-pr-page]').forEach(button=>button.onclick=()=>{page=Number(button.dataset.prPage);load()})}catch(error){if(requestId===requestSequence)results.innerHTML=`<div class="error">${esc(error.message)}</div>`}};
  document.getElementById('prCategoryTabs').querySelectorAll('[data-pr-category]').forEach(button=>button.onclick=()=>{category=button.dataset.prCategory;sortBy=['leader','gsm_leader'].includes(category)?'count':'score';page=1;document.querySelectorAll('[data-pr-category]').forEach(item=>item.classList.toggle('active',item===button));renderSubfilters();updateExportState();load()});
  form.onsubmit=event=>{event.preventDefault();page=1;load()};
  form.elements.population.onchange=()=>{page=1;load()};
  if(canExport)document.getElementById('prExport').onclick=()=>{location.href=portalPath('/api/pr-rankings/export?'+params())};
  renderSubfilters();updateExportState();await load();
}

const HR_DUTY_ROLE_CODES=['TA_SUPERVISOR','TA_GSM'];
// 本职可叠加的代理职务：CM/TR → TA主管，主管 → TA GSM。
const HR_DUTY_FOR_BASE={CM:'TA_SUPERVISOR',TR:'TA_SUPERVISOR',SUPERVISOR:'TA_GSM'};
function hrTag(html,tone=''){return `<span class="org-tag ${tone}">${html}</span>`;}
// 组名在本景点圈内去掉圈名显示（热力追踪A组 → A组）；HR改过的名字原样显示。
function hrGroupShortName(group,circleName=''){return circleName&&group.name.startsWith(circleName)&&group.name.length>circleName.length?group.name.slice(circleName.length):group.name;}
// 负责人说明：两位都有就都写，只有一位写一位，都没有写“无负责人”。
function hrGroupLeaderText(group){return [group.formal_leader?`主管 ${group.formal_leader.name}`:'',group.acting_leader?`代理主管 ${group.acting_leader.name}`:''].filter(Boolean).join(' · ')||'无负责人';}
function hrLeaderTags(formal,acting){if(!formal&&!acting)return hrTag('无负责人 · 签卡暂无人复核','warn');return `${formal?hrTag(`<b>主管</b>${esc(formal.name)}`):hrTag('未设置主管','warn')}${acting?hrTag(`<b>代理主管</b>${esc(acting.name)}`,'accent'):''}`;}
function hrIdentityTags(employee){
  if(HR_DUTY_ROLE_CODES.includes(employee.base_role_code))return `${hrTag(esc(employee.base_role_name||employee.role_name))}${hrTag('旧记录：请确认本职','warn')}`;
  return `${hrTag(esc(employee.base_role_name||employee.role_name||'未配置'))}${employee.duty_role_code?hrTag(`代理${esc(employee.duty_role_name)}`,'accent'):''}`;
}
function hrStatusCell(employee){
  if(employee.employment_status==='loa')return hrTag('LOA','warn');
  if(employee.employment_status==='terminated')return hrTag('离职','muted');
  return '在职';
}
function hrEditable(employee){return state.options.roles.some(role=>role.code===employee.role_code);}
function hrPersonHead(groupLabel){return `<div class="hr-person-row hr-person-head" aria-hidden="true"><span>员工</span><span>身份</span><span>${groupLabel}</span><span>状态</span><span></span></div>`;}
function hrPersonRow(employee,groupCell){
  return `<div class="hr-person-row" data-hr-person="${employee.id}" data-status="${esc(employee.employment_status)}" data-search="${esc(`${employee.name} ${employee.employee_no}`.toLowerCase())}"><div class="hr-person-name"><strong>${esc(employee.name)}</strong><small>${esc(employee.employee_no)}</small></div><div class="hr-person-identity">${hrIdentityTags(employee)}</div><div class="hr-person-group">${groupCell}</div><div class="hr-person-status">${hrStatusCell(employee)}</div><div class="hr-person-actions">${hrEditable(employee)?`<button type="button" class="secondary" data-hr-edit="${employee.id}">编辑</button>`:'<span class="not-applicable" title="仅最高管理员可编辑">只读</span>'}</div></div>`;
}
// 组员行唯一可直接修改的项目：所属小组。
function hrMemberGroupCell(employee,circle){
  const current=circle.groups.find(group=>group.id===employee.group_id);
  if(!hrEditable(employee)||!employee.is_active)return esc(current?hrGroupShortName(current,circle.name):'未分组');
  return `<select class="inline-select" data-hr-group-select="${employee.id}" data-original="${employee.group_id||''}" aria-label="${esc(employee.name)}的所属小组"><option value="">未分组</option>${circle.groups.map(group=>`<option value="${group.id}" ${group.id===employee.group_id?'selected':''}>${esc(`${hrGroupShortName(group,circle.name)}（${hrGroupLeaderText(group)}）`)}</option>`).join('')}</select>`;
}
function hrBlock(title,tags,rows,{groupLabel='小组',empty='暂无人员',link=false,collapsed=false,key=''}={}){
  const head=`<span class="hr-block-title">${title}</span>${tags}${link?'<button type="button" class="link-button" data-goto-groups>去小组管理 →</button>':''}`;
  const body=`${hrPersonHead(groupLabel)}${rows||`<div class="empty">${empty}</div>`}`;
  return collapsed?`<details class="hr-block" data-hr-block data-hr-key="${esc(key)}"><summary class="hr-block-head">${head}</summary>${body}</details>`:`<div class="hr-block" data-hr-block><div class="hr-block-head">${head}</div>${body}</div>`;
}
function hrCircleSection(circle){
  const count=value=>hrTag(`${value}人`,'plain');
  // 主管列表和各小组默认折叠（标题上保留负责人与人数），未分组保持展开方便分配。
  const idleSupervisors=circle.supervisors.filter(employee=>!employee.led_group_id).length;
  const supervisors=circle.supervisors.map(employee=>hrPersonRow(employee,employee.led_group_name?esc(employee.led_group_name):hrTag('暂未带组','warn'))).join('');
  const groups=circle.groups.map(group=>hrBlock(`<span title="${esc(group.name)}">${esc(hrGroupShortName(group,circle.name))}</span>`,`${hrLeaderTags(group.formal_leader,group.acting_leader)}${count(group.member_count)}`,group.members.map(employee=>hrPersonRow(employee,hrMemberGroupCell(employee,circle))).join(''),{empty:'暂无组员',link:true,collapsed:true,key:`group-${group.id}`})).join('');
  const unassigned=circle.unassigned.length?hrBlock('未分组',hrTag(`${circle.unassigned.length}人`,'warn'),circle.unassigned.map(employee=>hrPersonRow(employee,hrMemberGroupCell(employee,circle))).join('')):'';
  const managers=circle.managers.length?hrBlock('GSM及以上 / HR',count(circle.managers.length),circle.managers.map(employee=>hrPersonRow(employee,'—')).join(''),{groupLabel:'',collapsed:true}):'';
  const inactive=circle.inactive.length?hrBlock('离职人员',count(circle.inactive.length),circle.inactive.map(employee=>hrPersonRow(employee,'—')).join(''),{groupLabel:'',collapsed:true}):'';
  const managerTags=`${circle.gsm_names.length?hrTag(`<b>景点GSM</b>${esc(circle.gsm_names.join('、'))}`):hrTag('未配置GSM','warn')}${circle.ta_gsm_names.length?hrTag(`<b>代理GSM</b>${esc(circle.ta_gsm_names.join('、'))}`,'accent'):''}`;
  return `<section class="hr-circle" data-hr-circle="${circle.id}"><header class="hr-circle-head"><h3>${esc(circle.name)}</h3>${managerTags}</header>${hrBlock('主管',`${count(circle.supervisors.length)}${idleSupervisors?hrTag(`暂未带组 ${idleSupervisors}人`,'warn'):''}`,supervisors,{groupLabel:'带组',empty:'暂无主管',collapsed:true,key:`supervisors-${circle.id}`})}${groups}${unassigned}${managers}${inactive}</section>`;
}
function hrOrganizationMarkup(organization){
  const circles=organization.circles||[],others=organization.others||[];
  const circleFilter=circles.length>1?`<label>景点圈<select id="hrCircleFilter"><option value="all">全部景点圈</option>${circles.map(circle=>`<option value="${circle.id}">${esc(circle.name)}</option>`).join('')}</select></label>`:'';
  const warnings=(organization.warnings||[]).map(row=>`<div class="notice warn hr-org-warning"><span>${esc(row.message)}。</span><button type="button" class="link-button" data-goto-groups>去小组管理安排 →</button></div>`).join('');
  const otherSection=others.length?`<section class="hr-circle" data-hr-circle="none"><header class="hr-circle-head"><h3>未归属景点圈</h3></header>${hrBlock('其他人员',hrTag(`${others.length}人`,'plain'),others.map(employee=>hrPersonRow(employee,'—')).join(''),{groupLabel:''})}</section>`:'';
  return `<div class="hr-filter hr-org-toolbar">${circleFilter}<label>搜索<input id="hrSearch" placeholder="搜索姓名或员工号"></label><label>人员状态<select id="hrStatusFilter"><option value="all">全部状态</option><option value="active">在职</option><option value="loa">LOA（长期病假）</option><option value="terminated">离职</option></select></label></div>${warnings}<div id="hrOrgTree">${circles.map(hrCircleSection).join('')}${otherSection}</div>`;
}
function hrOrganizationPeople(organization){
  const people=new Map();
  (organization.circles||[]).forEach(circle=>{[...circle.supervisors,...circle.unassigned,...circle.managers,...circle.inactive,...circle.groups.flatMap(group=>group.members)].forEach(employee=>people.set(String(employee.id),employee))});
  (organization.others||[]).forEach(employee=>people.set(String(employee.id),employee));
  return people;
}
function gotoTab(tab){state.tab=tab;renderTabs();render();}
// 小组可以没有负责人：保存成功后，把后端返回的提示弹出给 HR。
function showSaveWarnings(result){const warnings=result?.warnings||[];if(warnings.length)noticeModal('请注意',`<ul>${warnings.map(text=>`<li>${esc(text)}</li>`).join('')}</ul>`);}
// 通用表单弹窗：onSubmit 抛错时保留弹窗并提示，成功后关闭。
function formModal(title,subtitle,body,confirmText,{onReady,onSubmit}={}){return new Promise(resolve=>{const overlay=document.createElement('div');overlay.className='modal-overlay';const titleId='dialog-title-'+Date.now();overlay.innerHTML=`<section class="modal-card form-modal" role="dialog" aria-modal="true" aria-labelledby="${titleId}"><h3 id="${titleId}">${esc(title)}</h3>${subtitle?`<p class="field-hint form-modal-subtitle">${subtitle}</p>`:''}<form class="form-stack" data-modal-form novalidate>${body}<div class="modal-actions"><button type="button" class="secondary" data-modal-cancel>取消</button><button type="submit" class="primary">${esc(confirmText)}</button></div></form></section>`;document.body.appendChild(overlay);const form=overlay.querySelector('[data-modal-form]'),cancel=overlay.querySelector('[data-modal-cancel]'),submit=form.querySelector('[type=submit]');const layer=bindDialogLayer(overlay,{initialFocus:form.querySelector('select,input')||cancel,onClose:value=>resolve(value===true)});cancel.onclick=()=>layer.close(false);onReady?.(form,()=>layer.close(false));form.onsubmit=async event=>{event.preventDefault();submit.disabled=true;try{await onSubmit(form);layer.close(true)}catch(error){toast(error.message,true)}finally{submit.disabled=false}};});}
// 员工编辑弹窗：身份 / 归属 / 状态与账号。主管带组在小组管理设置。
function hrEditEmployee(employee,organization){
  const circles=organization.circles||[];
  const baseRoles=state.options.roles.filter(role=>!HR_DUTY_ROLE_CODES.includes(role.code));
  const legacy=HR_DUTY_ROLE_CODES.includes(employee.base_role_code);
  const originalBase=legacy?'':employee.base_role_code;
  const roleName=code=>(state.options.roles.find(role=>role.code===code)||{}).name||code;
  const account=employee.account_deleted_at?`<span class="badge danger" title="${esc(employee.account_deletion_reason||'员工与业务档案已保留')}">账号已删除·留档</span>`:`<select name="enabled"><option value="true" ${employee.account_enabled?'selected':''}>启用</option><option value="false" ${!employee.account_enabled?'selected':''}>停用</option></select>`;
  const archive=employee.account_deletion_eligible?`<button type="button" class="danger" data-delete-login-account title="删除登录凭据与会话，保留员工及所有历史档案">删除登录账号</button>`:'';
  const body=`<fieldset class="hr-edit-section"><legend>身份</legend><label>本职<select name="base_role">${legacy?'<option value="">请选择本职</option>':''}${baseRoles.map(role=>`<option value="${role.code}" ${role.code===originalBase?'selected':''}>${esc(role.name)}</option>`).join('')}</select></label>${legacy?`<p class="field-hint legacy-duty-hint">旧记录：请选择本职（${employee.base_role_code==='TA_GSM'?'主管':'CM/TR'}）后保存，代理职务保留</p>`:''}<label>代理职务<select name="duty"></select></label><label data-duty-end>代理至<input name="role_ends_on" type="date" value="${esc(employee.duty_ends_on||'')}"></label><p class="field-hint" data-duty-hint></p></fieldset><fieldset class="hr-edit-section"><legend>归属</legend><label>景点圈<select name="attraction"><option value="">无</option>${state.options.attractions.map(attraction=>`<option value="${attraction.id}" ${attraction.id===employee.attraction_id?'selected':''}>${esc(attraction.name)}</option>`).join('')}</select></label><label data-group-field>所属小组<select name="group"></select></label><p class="field-hint" data-group-hint></p></fieldset><fieldset class="hr-edit-section"><legend>状态与账号</legend><label>人员状态<select name="employment_status"></select></label><label data-loa-field>LOA开始日期<input name="loa_start_date" type="date" value="${esc(employee.loa_start_date||today())}"></label><label>登录账号${account}</label>${archive}</fieldset><label>修改原因<input name="reason" maxlength="200" placeholder="选填，会写入审计"></label>`;
  return formModal(`编辑员工 · ${employee.name}`,`${esc(employee.employee_no)} · ${esc(employee.attraction_name||'未分配景点圈')}`,body,'保存',{
    onReady:(form,close)=>{
      const base=form.elements.base_role,duty=form.elements.duty,attraction=form.elements.attraction,group=form.elements.group,status=form.elements.employment_status;
      const dutyEnd=form.querySelector('[data-duty-end]'),dutyHint=form.querySelector('[data-duty-hint]'),groupField=form.querySelector('[data-group-field]'),groupHint=form.querySelector('[data-group-hint]'),loaField=form.querySelector('[data-loa-field]');
      const originalDuty=legacy?employee.base_role_code:(employee.duty_role_code||'');
      let dutyChoice=originalDuty,groupChoice=String(employee.group_id||'');
      const sync=()=>{
        const baseCode=base.value,frontline=['CM','TR'].includes(baseCode)||(legacy&&employee.base_role_code==='TA_SUPERVISOR'&&!baseCode);
        const baseChanged=!legacy&&baseCode!==originalBase,available=HR_DUTY_FOR_BASE[baseCode];
        duty.innerHTML=legacy?`<option value="${employee.base_role_code}">代理${esc(roleName(employee.base_role_code))}（保留）</option>`:`<option value="">无</option>${available&&!baseChanged?`<option value="${available}">代理${esc(roleName(available))}</option>`:''}`;
        duty.value=[...duty.options].some(option=>option.value===dutyChoice)?dutyChoice:'';
        duty.disabled=legacy||baseChanged||!available;
        dutyEnd.hidden=!duty.value;
        dutyHint.textContent=baseChanged&&HR_DUTY_FOR_BASE[baseCode]?'更换本职后，请先保存，再设置代理职务。':duty.value?'不填结束日期表示长期代理。结束代理后，他代理的小组自动交回主管。':'';
        groupField.hidden=!frontline;
        const circle=circles.find(row=>String(row.id)===String(attraction.value));
        group.innerHTML=`<option value="">未分组</option>${(circle?circle.groups:[]).map(row=>`<option value="${row.id}">${esc(`${row.name}（${hrGroupLeaderText(row)}）`)}</option>`).join('')}`;
        group.value=[...group.options].some(option=>option.value===groupChoice)?groupChoice:'';
        groupHint.textContent=frontline?'只列本景点圈的小组。':'主管不选小组，带组在小组管理设置。';
        const loaAllowed=frontline,current=status.value||employee.employment_status;
        status.innerHTML=`<option value="active">在职</option>${loaAllowed?'<option value="loa">LOA（长期病假）</option>':''}<option value="terminated">离职</option>`;
        status.value=[...status.options].some(option=>option.value===current)?current:'active';
        loaField.hidden=status.value!=='loa';
      };
      base.onchange=()=>{dutyChoice=base.value===originalBase?originalDuty:'';sync()};
      duty.onchange=()=>{dutyChoice=duty.value;sync()};
      group.onchange=()=>{groupChoice=group.value};
      attraction.onchange=()=>{groupChoice=String(attraction.value)===String(employee.attraction_id)?String(employee.group_id||''):'';sync()};
      status.onchange=sync;sync();
      form.querySelector('[data-delete-login-account]')?.addEventListener('click',async()=>{if(!await confirmModal('删除登录账号',`<p>确认删除 <strong>${esc(employee.name)}（${esc(employee.employee_no)}）</strong> 的登录账号吗？</p><p>登录凭据和当前会话将被移除，不能再登录；员工、当月数据、历史记录、附件、调动和审计档案均会保留并标记“账号已删除·留档”。</p>`,'确认删除登录账号'))return;try{await api('/api/hr/employees/'+employee.id+'/account',json('DELETE',{reason:'离职/停用满7天后删除登录账号，保留员工与业务档案'}));toast('登录账号已删除，业务档案已保留');close();renderHrEmployees()}catch(error){toast(error.message,true)}});
    },
    onSubmit:async form=>{
      const base=form.elements.base_role.value,duty=form.elements.duty.value;
      if(!base)throw new Error('请选择本职');
      const roleCode=legacy?base:(duty||base);
      const frontline=!form.querySelector('[data-group-field]').hidden;
      const enabled=form.elements.enabled;
      const body={role_code:roleCode,attraction_id:form.elements.attraction.value||null,employment_status:form.elements.employment_status.value,loa_start_date:form.elements.loa_start_date.value||'',account_enabled:enabled?enabled.value==='true':false,group_id:frontline?(form.elements.group.value||null):null,reason:form.elements.reason.value.trim()||'HR员工管理编辑'};
      if(HR_DUTY_ROLE_CODES.includes(roleCode))body.role_ends_on=form.elements.role_ends_on.value||'';
      const result=await api('/api/hr/employees/'+employee.id,json('PUT',body));
      toast('已保存');renderHrEmployees();showSaveWarnings(result);
    },
  });
}
function bindHrOrganization(organization){
  const people=hrOrganizationPeople(organization),search=document.getElementById('hrSearch'),statusFilter=document.getElementById('hrStatusFilter'),circleFilter=document.getElementById('hrCircleFilter');
  const refresh=()=>{
    const query=search.value.trim().toLowerCase(),status=statusFilter.value,circle=circleFilter?circleFilter.value:'all';
    document.querySelectorAll('[data-hr-circle]').forEach(section=>{section.hidden=circle!=='all'&&section.dataset.hrCircle!==circle;});
    document.querySelectorAll('[data-hr-person]').forEach(row=>{row.hidden=(query&&!row.dataset.search.includes(query))||(status!=='all'&&row.dataset.status!==status);});
    const filtering=Boolean(query)||status!=='all';
    document.querySelectorAll('[data-hr-block]').forEach(block=>{const rows=[...block.querySelectorAll('[data-hr-person]')];block.hidden=filtering&&!rows.some(row=>!row.hidden);if(filtering&&block.tagName==='DETAILS'&&!block.hidden)block.open=true;});
  };
  // 保存后页面会重新渲染，记住已展开的主管栏和小组。
  const opened=state.hrOpenBlocks||(state.hrOpenBlocks=new Set());
  document.querySelectorAll('details[data-hr-key]').forEach(block=>{const key=block.dataset.hrKey;if(key&&opened.has(key))block.open=true;block.addEventListener('toggle',()=>{if(!key)return;if(block.open)opened.add(key);else opened.delete(key)})});
  search.oninput=refresh;statusFilter.onchange=refresh;if(circleFilter)circleFilter.onchange=refresh;
  document.querySelectorAll('[data-goto-groups]').forEach(button=>button.onclick=event=>{event.preventDefault();gotoTab('hrGroups')});
  document.querySelectorAll('[data-hr-edit]').forEach(button=>button.onclick=()=>{const employee=people.get(button.dataset.hrEdit);if(employee)hrEditEmployee(employee,organization)});
  document.querySelectorAll('[data-hr-group-select]').forEach(select=>select.onchange=async()=>{
    const employee=people.get(select.dataset.hrGroupSelect),target=select.selectedOptions[0]?.textContent||'未分组';
    if(!(await confirmModal('调整所属小组',`<p>确认将 ${esc(employee.name)} 调整到“${esc(target)}”？</p><p>待复核的签卡将转给新小组的负责人。</p>`,'确认调整'))){select.value=select.dataset.original;return}
    try{const result=await api('/api/hr/employees/'+employee.id,json('PUT',{group_id:select.value||null,reason:'HR调整小组'}));toast('已调整小组');renderHrEmployees();showSaveWarnings(result)}catch(error){select.value=select.dataset.original;toast(error.message,true)}
  });
  refresh();
}

function employeeNumberChangePanel(){
  if(!['HR_CIRCLE','SYSTEM_ADMIN'].includes(state.me.role_code))return '';
  const scope=state.me.role_code==='HR_CIRCLE'?'当前权限：仅可变更所属景点圈内 CM、TR、TA主管、主管的员工号。':'当前权限：可变更常规员工账号（CM/TR/TA主管/主管/TA GSM/GSM/AM/OM）的员工号。HR和最高管理员账号不在此入口处理。';
  return `<details class="panel account-status-panel hr-tool-panel"><summary><span><strong>员工号变更</strong><small>实习转正等场景更换员工号，保留同一员工档案</small></span><span class="badge">展开</span></summary><div class="account-status-content"><p>用于实习转正等员工号变更场景。系统保留同一员工档案、角色、景点圈、小组、加扣分、缺勤和审计历史，不会新建第二个账号。</p><div class="notice"><strong>可操作范围</strong><br>${esc(scope)}<br>变更后原账号立即失效，当前登录会话将退出；历史员工号可继续用于此处搜索和导出复查。</div><form id="employeeNumberChangeForm" class="form-stack"><label>搜索员工<input name="keyword" autocomplete="off" placeholder="输入当前/历史员工号或姓名"></label><div id="employeeNumberChangeResults" class="employee-search-results" hidden></div><input type="hidden" name="employee_id"><div id="employeeNumberChangeSelected" class="employee-selected" hidden></div><label>新员工号<input name="new_employee_no" inputmode="numeric" pattern="[0-9]{7}" minlength="7" maxlength="7" required disabled placeholder="请输入7位新员工号"><span class="field-hint">仅限7位数字；系统会校验员工与登录账号均未被占用。</span></label><label>变更原因<input name="reason" maxlength="300" required disabled placeholder="例如：实习转正，更换正式员工号"></label><label class="check-line"><input name="reset_password" type="checkbox" disabled> 同时重置密码为新员工号后四位（首次登录必须修改）</label><button type="submit" class="warn" disabled>确认变更员工号</button></form></div></details>`;
}

function bindEmployeeNumberChange(){
  const form=document.getElementById('employeeNumberChangeForm');if(!form)return;
  const keyword=form.elements.keyword,results=document.getElementById('employeeNumberChangeResults'),selected=document.getElementById('employeeNumberChangeSelected'),target=form.elements.employee_id,newNumber=form.elements.new_employee_no,reason=form.elements.reason,reset=form.elements.reset_password,submit=form.querySelector('button[type=submit]');let timer=null,controller=null,sequence=0;
  const clearSelected=()=>{target.value='';selected.hidden=true;selected.innerHTML='';newNumber.value='';newNumber.disabled=true;reason.value='';reason.disabled=true;reset.checked=false;reset.disabled=true;submit.disabled=true;};
  const choose=row=>{target.value=String(row.id);keyword.value=`${row.name} · ${row.employee_no}`;results.hidden=true;results.innerHTML='';newNumber.disabled=false;reason.disabled=false;reset.disabled=false;submit.disabled=false;const historic=row.matched_historical_no?`<small>命中历史员工号：${esc(row.matched_historical_no)}</small>`:'';selected.innerHTML=`<div><strong>${esc(row.name)}</strong><span class="badge">${esc(row.role_name)}</span><small>当前员工号：${esc(row.employee_no)} · ${esc(row.attraction_name)}</small>${historic}</div><button type="button" class="secondary">重新选择</button>`;selected.hidden=false;selected.querySelector('button').onclick=()=>{clearSelected();keyword.value='';keyword.focus();};newNumber.focus();};
  const search=async()=>{const value=keyword.value.trim(),requestId=++sequence;clearSelected();if(!value){results.hidden=true;results.innerHTML='';return;}controller?.abort();controller=new AbortController();results.hidden=false;results.innerHTML='<div class="employee-search-empty">正在搜索…</div>';try{const data=await api('/api/hr/employee-number-targets?'+new URLSearchParams({keyword:value,limit:'30'}),{signal:controller.signal});if(requestId!==sequence)return;const rows=data.items||[];if(!rows.length){results.innerHTML='<div class="employee-search-empty">没有找到可变更员工号的账号</div>';return;}results.innerHTML=rows.map((row,index)=>`<button type="button" class="employee-search-result" data-number-result="${index}"><span><strong>${esc(row.name)}</strong><em>${esc(row.role_name)}</em></span><small>当前：${esc(row.employee_no)} · ${esc(row.attraction_name)}${row.matched_historical_no?` · 历史：${esc(row.matched_historical_no)}`:''}</small></button>`).join('');results.querySelectorAll('[data-number-result]').forEach(button=>button.onclick=()=>choose(rows[Number(button.dataset.numberResult)]));}catch(error){if(error.name!=='AbortError'&&requestId===sequence){results.innerHTML='<div class="employee-search-empty">搜索失败，请稍后重试</div>';toast(error.message,true);}}};
  keyword.oninput=()=>{clearTimeout(timer);timer=setTimeout(search,120);};
  form.onsubmit=async event=>{event.preventDefault();if(!target.value)return;const body={employee_id:Number(target.value),new_employee_no:newNumber.value.trim(),reason:reason.value.trim(),reset_password:reset.checked};if(!/^\d{7}$/.test(body.new_employee_no)){toast('新员工号必须为7位纯数字',true);newNumber.focus();return;}if(!body.reason){toast('请填写员工号变更原因',true);reason.focus();return;}const confirmed=await confirmModal('确认变更员工号',`<p>确认将所选员工的登录员工号变更为 <strong>${esc(body.new_employee_no)}</strong> 吗？</p><p>原员工号将不能登录；当前会话会立即退出。员工档案、景点圈、小组和所有历史记录均保持同一人。</p>${body.reset_password?'<p>密码将同时重置为新员工号后四位，首次登录必须修改。</p>':'<p>密码保持不变。</p>'}`,'确认变更');if(!confirmed)return;try{const out=await api('/api/hr/employees/'+body.employee_id+'/employee-number',json('POST',body));toast(out.unchanged?'员工号未变化':'员工号已变更，原登录会话已失效');renderHrEmployees()}catch(error){toast(error.message,true)}};
}

function accountStatusBadge(label,code){
  const tone=['enabled','changed'].includes(code)?'ok':['locked','disabled','employee_inactive'].includes(code)?'danger':['pending_change','unknown','unprovisioned'].includes(code)?'warn':'';
  return `<span class="badge ${tone}">${esc(label)}</span>`;
}

function accountStatusRows(rows){
  if(!rows.length)return '<tr><td colspan="7" class="empty">没有符合筛选条件的账号</td></tr>';
  return rows.map(row=>`<tr><td>${esc(row.login_account||'未开通')}<br><small>${esc(row.employee_no)}</small></td><td><strong>${esc(row.name)}</strong></td><td>${esc(row.role_name)}</td><td>${esc(row.attraction_name)}</td><td>${accountStatusBadge(row.account_status,row.account_status_code)}</td><td>${accountStatusBadge(row.password_status,row.password_status_code)}${row.password_changed_at?`<br><small>修改于：${esc(row.password_changed_at)}</small>`:''}</td><td>${accountStatusBadge(row.login_status,row.last_login_at?'ok':'warn')}<br><small>${esc(row.last_login_at||'暂无登录记录')}</small></td></tr>`).join('');
}

function accountStatusPanel(){
  return `<details id="accountStatusPanel" class="panel account-status-panel"><summary><span><strong>账号状态与登录情况</strong><small>按需展开查看，不影响员工管理操作。</small></span><span class="badge">展开查看</span></summary><div id="accountStatusContent" class="account-status-content"><p class="field-hint">可按景点圈、账号状态或关键词筛选；仅展示状态和时间，不会显示真实密码。</p></div></details>`;
}

function accountStatusContent(data){
  const scope=data.scope_label||'权限范围内账号',rows=data.items||[];
  const attractions=[...new Set(rows.map(row=>row.attraction_name).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh-CN'));
  return `<div class="record-line"><div><h2>账号状态与登录情况</h2><p>当前可查看：${esc(scope)}。仅展示状态和时间，不会显示真实密码。</p></div><span class="badge">${rows.length} 个账号</span></div><div class="hr-filter account-status-filters"><label>景点圈<select id="accountStatusAttraction"><option value="all">全部景点圈</option>${attractions.map(name=>`<option value="${esc(name)}">${esc(name)}</option>`).join('')}</select></label><label>搜索账号<input id="accountStatusSearch" autocomplete="off" placeholder="姓名、员工号或账号"></label><label>状态筛选<select id="accountStatusFilter"><option value="all">全部状态</option><option value="never_logged_in">从未登录</option><option value="pending_change">待修改初始/重置密码</option><option value="changed">已修改密码</option><option value="unknown">历史密码状态未记录</option><option value="locked">临时锁定</option><option value="disabled">账号已停用</option></select></label></div><p id="accountStatusSummary" class="field-hint"></p><div class="table-wrap sticky-col"><table class="account-status-table"><thead><tr><th>登录账号 / 员工号</th><th>员工</th><th>角色</th><th>景点圈</th><th>账号状态</th><th>密码状态</th><th>最后登录</th></tr></thead><tbody id="accountStatusRows">${accountStatusRows(rows)}</tbody></table></div>`;
}

function bindAccountStatus(data){
  const input=document.getElementById('accountStatusSearch'),filter=document.getElementById('accountStatusFilter'),attraction=document.getElementById('accountStatusAttraction'),target=document.getElementById('accountStatusRows'),summary=document.getElementById('accountStatusSummary'),items=data.items||[];
  if(!input||!filter||!attraction||!target||!summary)return;
  const refresh=()=>{const keyword=input.value.trim().toLowerCase(),mode=filter.value,circle=attraction.value;const rows=items.filter(row=>{const text=[row.login_account,row.employee_no,row.name,row.role_name].join(' ').toLowerCase();if(circle!=='all'&&row.attraction_name!==circle)return false;if(keyword&&!text.includes(keyword))return false;if(mode==='never_logged_in')return !row.last_login_at;if(mode==='pending_change')return row.password_status_code==='pending_change';if(mode==='changed')return row.password_status_code==='changed';if(mode==='unknown')return row.password_status_code==='unknown';if(mode==='locked')return row.account_status_code==='locked';if(mode==='disabled')return row.account_status_code==='disabled';return true;});target.innerHTML=accountStatusRows(rows);summary.textContent=`当前显示 ${rows.length} / ${items.length} 个账号（${circle==='all'?'全部景点圈':circle}）。`};
  input.oninput=refresh;filter.onchange=refresh;attraction.onchange=refresh;refresh();
}

function bindAccountStatusPanel(){
  const panel=document.getElementById('accountStatusPanel'),content=document.getElementById('accountStatusContent');
  if(!panel||!content)return;
  let loaded=false,loading=false;
  const load=async()=>{if(loaded||loading)return;loading=true;content.innerHTML='<p class="field-hint">正在加载账号状态…</p>';try{const data=await api('/api/hr/account-status');content.innerHTML=accountStatusContent(data);loaded=true;bindAccountStatus(data);}catch(error){content.innerHTML='<p class="error-text">账号状态加载失败，请稍后重试。</p><button type="button" class="secondary" id="retryAccountStatus">重新加载</button>';document.getElementById('retryAccountStatus').onclick=()=>{loading=false;load();};}finally{loading=false;}};
  panel.addEventListener('toggle',()=>{if(panel.open)load();});
}

async function renderHrEmployees(){
  const organization=await api('/api/hr/organization');
  app.innerHTML=`<div class="section-gap"><details class="panel account-status-panel hr-tool-panel"><summary><span><strong>Excel 导入</strong><small>下载模板，填写后整表导入员工</small></span><span class="badge">展开</span></summary><div class="account-status-content"><p>请先下载 V2 模板，填写后整表导入。员工号必须为7位纯数字，员工仅可归属热力追踪、矮人迷宫或小熊罐子景点圈。初始密码留空时，系统使用员工号后四位并要求首次登录修改。</p><form id="importEmployees" enctype="multipart/form-data"><div class="actions native-file-actions"><label class="secondary native-file-trigger" for="importEmployeesWorkbook">选择文件</label><a class="secondary download-link" href="${portalPath('/api/hr/import-template')}">下载导入模板</a><button type="submit" class="primary">导入员工</button></div><input id="importEmployeesWorkbook" class="native-file-input" name="workbook" type="file" accept=".xlsx" required><span class="field-hint" data-import-filename>请选择 Excel 文件（.xlsx）</span></form></div></details><details class="panel account-status-panel hr-tool-panel hr-create-panel"><summary><span class="hr-create-heading"><strong>新建员工账号</strong><small>填写员工基础信息，创建后可在下方员工管理中分配小组</small></span><span class="badge">展开</span></summary><div class="account-status-content"><form id="newEmployee" class="hr-filter"><label class="hr-create-employee-no">员工号<input name="employee_no" inputmode="numeric" pattern="[0-9]{7}" minlength="7" maxlength="7" required placeholder="请输入7位员工号"><span class="field-hint">仅限7位数字，不能重复创建。</span></label><label class="hr-create-name">姓名<input name="name" required placeholder="请输入员工姓名"></label><label class="hr-create-role">角色<select name="role_code">${opt(state.options.roles,'code',x=>x.name)}</select></label><label class="hr-create-attraction">景点圈<select name="attraction_id"><option value="">无</option>${opt(state.options.employee_circles||state.options.attractions)}</select></label><label class="hr-create-password">初始密码<input name="password" placeholder="留空使用员工号后4位"><span class="field-hint">首次登录后必须立即修改密码。</span></label><div class="actions hr-create-actions"><button type="submit" class="primary">创建账号</button></div></form></div></details>${employeeNumberChangePanel()}<section class="panel"><h2>员工管理</h2><p>改人在这里：本职与代理职务、景点圈、所属小组、人员状态和账号。组员行可直接选择所属小组，其他修改请点“编辑”。小组的主管和代理主管请到<button type="button" class="link-button" data-goto-groups>小组管理</button>设置。</p>${hrOrganizationMarkup(organization)}</section>${accountStatusPanel()}</div>`;
  const importForm=document.getElementById('importEmployees'),workbookInput=importForm.querySelector('[name=workbook]'),fileHint=importForm.querySelector('[data-import-filename]');
  workbookInput.onchange=()=>{fileHint.textContent=workbookInput.files[0]?workbookInput.files[0].name:'请选择 Excel 文件（.xlsx）';};
  importForm.onsubmit=async event=>{event.preventDefault();try{const out=await api('/api/hr/import-employees',{method:'POST',body:new FormData(event.target)});toast(`成功导入 ${out.created} 名员工`);renderHrEmployees()}catch(error){toast(error.message,true)}};
  document.getElementById('newEmployee').onsubmit=async event=>{event.preventDefault();try{await api('/api/hr/employees',json('POST',Object.fromEntries(new FormData(event.target))));toast('员工已创建');renderHrEmployees()}catch(error){toast(error.message,true)}};
  bindHrOrganization(organization);
  bindAccountStatusPanel();
  bindEmployeeNumberChange();
}

function passwordResetScopeHint(){
  const role=state.me.role_code;
  if(role==='GSM')return '当前权限：可重置本人管理景点圈内的常规账号（CM、TR、TA主管、主管、TA GSM、GSM、AM、OM）；不能重置任何 HR 或系统管理员账号。';
  if(role==='HR_CIRCLE')return '当前权限：仅可重置所属景点圈内的 CM、TR、TA主管、主管账号；不能重置 GSM、TA GSM、AM、OM、HR 或系统管理员账号。';
  if(role==='SYSTEM_ADMIN')return '当前权限：可重置所有常规账号（CM、TR、TA主管、主管、TA GSM、GSM、AM、OM）；景点圈 HR 请在“景点圈HR账号”中单独重置；不能在此处重置系统管理员账号。';
  if(['AM','OM'].includes(role))return '当前权限：可重置所有常规账号（CM、TR、TA主管、主管、TA GSM、GSM、AM、OM）；不能重置任何 HR 或系统管理员账号。';
  return '当前权限：可重置权限范围内的常规员工账号；HR 和系统管理员账号不在此入口处理。';
}
async function renderAccountReset(){
  const canCorrectName=['HR_CIRCLE','SYSTEM_ADMIN'].includes(state.me.role_code);
  const nameScope=state.me.role_code==='HR_CIRCLE'?'当前权限：仅可修改所属景点圈内 CM、TR、TA主管、主管账号的中文姓名。':'当前权限：可修改普通账号的中文姓名；HR和系统管理员账号不在此入口处理。';
  app.innerHTML=`<div class="section-gap"><section class="panel"><h2>密码管理</h2><p>修改自己的密码，或按当前权限重置其他账号密码。</p><form id="passwordPageForm" class="form-stack password-form">${passwordFieldsMarkup()}<button type="submit" class="primary">保存我的新密码</button></form></section><section class="panel"><h2>密码重置</h2><p>按姓名或工号搜索并选择员工；确认后密码将重置为登录账号后四位，员工首次登录后必须修改。</p><div class="notice"><strong>可重置范围</strong><br>${esc(passwordResetScopeHint())}</div><div id="resetPasswordResult"></div><form id="resetEmployeePassword" class="form-stack">${employeePicker('重置对象（必选）','resetEmployee','password_reset','输入姓名或工号搜索可重置密码的员工','/api/accounts/reset-targets')}<div class="actions form-sticky-actions"><button type="submit" class="warn">重置密码</button></div></form></section>${canCorrectName?`<section class="panel"><h2>账号姓名修改</h2><p>用于更正账号名下的中文姓名。员工号、登录账号、角色、景点圈和历史记录均不会改变。</p><div class="notice"><strong>可修改范围</strong><br>${esc(nameScope)}</div><form id="accountNameForm" class="form-stack"><label>搜索账号<input name="keyword" autocomplete="off" placeholder="输入员工号或当前中文姓名"></label><div id="accountNameResults" class="employee-search-results" hidden></div><input type="hidden" name="employee_id"><div id="accountNameSelected" class="employee-selected" hidden></div><label>新中文姓名<input name="name" maxlength="100" required placeholder="请输入正确的中文姓名" disabled></label><button type="submit" class="primary" disabled>确认修改姓名</button></form></section>`:''}</div>`;
  bindPasswordForm(document.getElementById('passwordPageForm'),form=>{toast('密码已更新');form.reset();form.querySelector('[name=current_password]')?.focus();form.querySelector('[name=new_password]').dispatchEvent(new Event('input'));});
  const resetForm=document.getElementById('resetEmployeePassword');bindEmployeeSearches(resetForm);resetForm.onsubmit=async event=>{event.preventDefault();if(!requireEmployeeSelection(resetForm))return;const body=Object.fromEntries(new FormData(resetForm)),selected=resetForm.querySelector('[data-employee-search]').value;if(!(await confirmModal('确认重置密码',`<p>确认重置 ${esc(selected)} 的密码吗？原登录会话将立即失效，密码将重置为登录账号后四位。</p>`,'确认重置')))return;try{const out=await api('/api/accounts/reset-password',json('POST',body));document.getElementById('resetPasswordResult').innerHTML=`<div class="notice"><strong>${esc(out.employee_name)}</strong> 的重置密码：<code>${esc(out.temporary_password)}</code><br>密码为登录账号后四位；首次登录后必须修改。${out.account_enabled?'':' 该账号当前处于停用状态。'}</div>`;toast('密码已重置为登录账号后四位');resetForm.reset();resetForm.querySelector('.employee-selected').hidden=true;resetForm.querySelector('[data-employee-search]').value='';}catch(error){toast(error.message,true)}};
  if(!canCorrectName)return;
  const form=document.getElementById('accountNameForm'),keyword=form.elements.keyword,results=document.getElementById('accountNameResults'),selected=document.getElementById('accountNameSelected'),target=form.elements.employee_id,newName=form.elements.name,submit=form.querySelector('button[type=submit]');let timer=null,controller=null,sequence=0;
  const clearSelected=()=>{target.value='';selected.hidden=true;selected.innerHTML='';newName.value='';newName.disabled=true;submit.disabled=true;};
  const choose=row=>{target.value=String(row.id);keyword.value=`${row.name} · ${row.employee_no}`;results.hidden=true;results.innerHTML='';newName.disabled=false;newName.value=row.name;submit.disabled=false;selected.innerHTML=`<div><strong>${esc(row.name)}</strong><span class="badge">${esc(row.role_name)}</span><small>${esc(row.employee_no)} · ${esc(row.attraction_name)}</small></div><button type="button" class="secondary">重新选择</button>`;selected.hidden=false;selected.querySelector('button').onclick=()=>{clearSelected();keyword.value='';keyword.focus();};newName.focus();newName.select();};
  const search=async()=>{const value=keyword.value.trim(),requestId=++sequence;clearSelected();if(!value){results.hidden=true;results.innerHTML='';return;}controller?.abort();controller=new AbortController();results.hidden=false;results.innerHTML='<div class="employee-search-empty">正在搜索…</div>';try{const data=await api('/api/accounts/name-targets?'+new URLSearchParams({keyword:value,limit:'30'}),{signal:controller.signal});if(requestId!==sequence)return;const rows=data.items||[];if(!rows.length){results.innerHTML='<div class="employee-search-empty">没有找到可修改姓名的账号</div>';return;}results.innerHTML=rows.map((row,index)=>`<button type="button" class="employee-search-result" data-name-result="${index}"><span><strong>${esc(row.name)}</strong><em>${esc(row.role_name)}</em></span><small>${esc(row.employee_no)} · ${esc(row.attraction_name)}</small></button>`).join('');results.querySelectorAll('[data-name-result]').forEach(button=>button.onclick=()=>choose(rows[Number(button.dataset.nameResult)]));}catch(error){if(error.name!=='AbortError'&&requestId===sequence){results.innerHTML='<div class="employee-search-empty">搜索失败，请稍后重试</div>';toast(error.message,true);}}};
  keyword.oninput=()=>{clearTimeout(timer);timer=setTimeout(search,120);};
  form.onsubmit=async event=>{event.preventDefault();if(!target.value||newName.disabled)return;const name=newName.value.trim();if(!name){toast('请输入正确的中文姓名',true);newName.focus();return;}if(!await confirmModal('确认修改账号姓名',`<p>确认将所选账号的中文姓名修改为“${esc(name)}”吗？</p><p>员工号、登录账号和历史记录不会改变。</p>`,'确认修改'))return;try{const out=await api('/api/accounts/update-name',json('POST',{employee_id:Number(target.value),name}));toast(out.unchanged?'姓名未变化':'账号中文姓名已修改');keyword.value='';clearSelected();}catch(error){toast(error.message,true)}};
}

async function renderCircleHrAccounts(){
  const data=await api('/api/admin/circle-hr-accounts');
  app.innerHTML=`<div class="section-gap"><section class="panel"><h2>景点圈HR账号</h2><p>最高管理员可查看账号状态并重置密码。系统不会显示已设置的原密码；重置后密码为登录账号后四位，首次登录必须修改。</p><div id="circleHrResetResult"></div><div class="table-wrap sticky-col"><table><thead><tr><th>账号</th><th>景点圈</th><th>账号状态</th><th>密码状态</th><th>最近登录</th><th>操作</th></tr></thead><tbody>${data.items.map(row=>`<tr><td>${esc(row.login_account)}<small>${esc(row.name)}</small></td><td>${esc(row.attraction_name)}</td><td>${row.account_enabled?'启用':'停用'}</td><td>${esc(row.password_status)}</td><td>${esc(row.last_login_at||'暂无')}</td><td>${row.employee_id?`<button type="button" class="warn" data-circle-hr-reset="${row.employee_id}" data-circle-hr-name="${esc(row.name)}">重置密码</button>`:'账号未生成'}</td></tr>`).join('')}</tbody></table></div></section></div>`;
  app.querySelectorAll('[data-circle-hr-reset]').forEach(button=>button.onclick=async()=>{const confirmed=await confirmModal('重置景点圈HR密码',`<p>确认重置 ${esc(button.dataset.circleHrName)} 的密码吗？</p><p>原登录会话将立即失效，密码将重置为登录账号后四位。</p>`,'确认重置');if(!confirmed)return;try{const result=await api('/api/admin/circle-hr-accounts/'+button.dataset.circleHrReset+'/reset-password',{method:'POST'});await renderCircleHrAccounts();document.getElementById('circleHrResetResult').innerHTML=`<div class="notice"><strong>${esc(result.employee_name)}</strong> 重置密码：<code>${esc(result.temporary_password)}</code><br>${esc(result.message)}</div>`}catch(error){toast(error.message,true)}});
}

async function renderCircleTransfers(){
  const [data,groupOptions]=await Promise.all([api('/api/hr/circle-transfers'),api('/api/hr/group-options')]);
  const canActFor=(attractionId)=>state.me.role_code!=='HR_CIRCLE'||Number(state.me.attraction_id)===Number(attractionId);
  const transferRows=data.items.map(row=>{const incoming=row.status==='pending'&&canActFor(row.target_attraction_id),outgoing=row.status==='pending'&&canActFor(row.source_attraction_id),groups=groupOptions.filter(item=>Number(item.attraction_id)===Number(row.target_attraction_id));return `<article class="group-card"><div class="record-line"><strong>${esc(row.employee_name)} · ${esc(row.employee_no)}</strong><span class="badge ${row.status==='completed'?'ok':row.status==='pending'?'warn':'danger'}">${esc(row.status_name)}</span></div><p>${esc(row.source_attraction_name)} → ${esc(row.target_attraction_name)} · 原小组：${esc(row.source_group_name)}</p><p>原因：${esc(row.reason)} · 发起人：${esc(row.requested_by_name)} · ${esc(row.requested_at)}</p>${row.status==='completed'?`<p>已分配：${esc(row.target_group_name)}${row.target_leader_name?`（复核人 ${esc(row.target_leader_name)}）`:''}；迁移当月认可 ${row.migrated_record_counts.recognitions||0} 条、扣分 ${row.migrated_record_counts.deductions||0} 条、病假 ${row.migrated_record_counts.sick_leaves||0} 条。</p>`:''}${incoming?`<form data-circle-transfer-review="${row.id}" class="grid two"><label>目标小组<select name="target_group_id"><option value="">请选择</option>${groups.map(item=>`<option value="${item.id}">${esc(item.name)}（${esc(item.leader_label||'无负责人')}）</option>`).join('')}</select><span class="field-hint">没有合适的小组时，请先到小组管理新建。选择无负责人的小组时，该员工的签卡暂时无人复核。</span></label><label>审批说明<input name="review_note" placeholder="接受可选，拒绝必填"></label><div class="actions"><button type="button" class="secondary" data-transfer-reject>拒绝</button><button type="submit" class="primary">接受并立即生效</button></div></form>`:''}${outgoing?`<div class="actions"><button type="button" class="secondary" data-transfer-cancel="${row.id}">撤回申请</button></div>`:''}${row.review_note?`<p>审批说明：${esc(row.review_note)}</p>`:''}</article>`}).join('');
  app.innerHTML=`<div class="section-gap"><section class="panel"><h2>发起跨景点圈调动</h2><p>原景点圈HR发起，目标景点圈HR确认并选择目标小组；确认后员工、当月数据和待办事项立即同步迁移。</p><form id="circleTransferCreate" class="circle-transfer-form">${circleTransferEmployeePicker()}<div class="circle-transfer-details"><label>目标景点圈<select name="target_attraction_id" required><option value="">请选择</option>${opt(data.target_circles)}</select></label><label>调动原因<input name="reason" required maxlength="200"></label><div class="circle-transfer-submit"><button class="primary">提交目标HR确认</button></div></div></form></section><section class="panel"><h2>调动记录</h2>${transferRows||'<div class="empty">暂无跨景点圈调动记录</div>'}</section></div>`;
  bindEmployeeSearches(app);document.getElementById('circleTransferCreate').onsubmit=async event=>{event.preventDefault();if(!requireEmployeeSelection(event.target))return;try{await api('/api/hr/circle-transfers',json('POST',Object.fromEntries(new FormData(event.target))));toast('调动申请已提交目标HR');renderCircleTransfers()}catch(error){toast(error.message,true)}};
  app.querySelectorAll('[data-circle-transfer-review]').forEach(form=>{form.onsubmit=async event=>{event.preventDefault();const groupId=form.elements.target_group_id.value;if(!groupId){toast('请选择目标小组',true);return}const confirmed=await confirmModal('确认立即转圈',`<p>确认后员工归属、小组关系、当月数据和待办将立即迁移。</p>`,'接受并生效');if(!confirmed)return;try{const result=await api('/api/hr/circle-transfers/'+form.dataset.circleTransferReview+'/review',json('POST',{action:'accept',target_group_id:groupId,review_note:form.elements.review_note.value}));toast('跨圈调动已立即生效');renderCircleTransfers();showSaveWarnings(result)}catch(error){toast(error.message,true)}};form.querySelector('[data-transfer-reject]').onclick=async()=>{const note=form.elements.review_note.value.trim();if(!note){toast('拒绝原因必填',true);form.elements.review_note.focus();return}try{await api('/api/hr/circle-transfers/'+form.dataset.circleTransferReview+'/review',json('POST',{action:'reject',review_note:note}));toast('已拒绝调动申请');renderCircleTransfers()}catch(error){toast(error.message,true)}}});
  app.querySelectorAll('[data-transfer-cancel]').forEach(button=>button.onclick=async()=>{if(!await confirmModal('撤回调动申请','<p>确认撤回这条跨圈调动申请？</p>','确认撤回'))return;try{await api('/api/hr/circle-transfers/'+button.dataset.transferCancel+'/cancel',{method:'POST'});toast('申请已撤回');renderCircleTransfers()}catch(error){toast(error.message,true)}});
}

function previousScoreMonth(){const value=new Date();value.setDate(1);value.setMonth(value.getMonth()-1);return `${value.getFullYear()}-${String(value.getMonth()+1).padStart(2,'0')}`;}
async function renderMonthClose(){
  const circles=(state.options.employee_circles||state.options.attractions||[]).filter(row=>row.employee_circle!==false);
  const isCircleHr=state.me.role_code==='HR_CIRCLE';
  const ownId=Number(state.me.attraction_id||0);
  const available=isCircleHr?circles.filter(row=>Number(row.id)===ownId):circles;
  if(!available.length){app.innerHTML='<section class="panel"><h2>月结</h2><p class="error">当前账号没有可月结的景点圈。</p></section>';return;}
  app.innerHTML=`<div class="section-gap"><section class="panel"><h2>月结</h2><p>仅可关闭上月数据。关闭前须完成所选范围内所有待办与待跟进事项；最高管理员关闭全部景点圈时，待办检查覆盖所有圈。如确需更正，可填写原因后临时开放，所有操作均会写入审计。</p><form id="monthCloseForm" class="hr-filter"><label>结算月份<input name="month" type="month" value="${previousScoreMonth()}" required></label><label>景点圈<select name="attraction_id" ${isCircleHr?'disabled':''}>${state.me.role_code==='SYSTEM_ADMIN'?'<option value="">全部景点圈</option>':''}${opt(available)}</select></label><div class="actions form-sticky-actions"><button type="submit" class="primary">查询月结状态</button></div></form></section><section class="panel" id="monthCloseResult"><div class="empty">请选择月份并查询状态</div></section></div>`;
  const form=document.getElementById('monthCloseForm'),result=document.getElementById('monthCloseResult');
  const show=async()=>{const month=form.elements.month.value,rawAttraction=isCircleHr?String(ownId):form.elements.attraction_id.value,attractionId=rawAttraction===''||rawAttraction==null?null:Number(rawAttraction);if(!/^\d{4}-\d{2}$/.test(month)){toast('请选择结算月份',true);return;}const data=await api(attractionId==null?`/api/month-closes/${month}`:`/api/month-closes/${month}?attraction_id=${attractionId}`);const checklist=(data.checklist||[]);const blocking=checklist.filter(row=>Number(row.count)>0);const allowedTabs=new Set(menuItems().map(([id])=>id));const checklistRows=checklist.map(row=>{const blocked=Number(row.count)>0,targetAvailable=row.target&&allowedTabs.has(row.target);return `<li class="${blocked?'notice danger':'notice'}"><strong>${esc(row.name)}</strong><span>${Number(row.count)} 项</span>${blocked?(targetAvailable?` <button type="button" class="secondary" data-month-target="${esc(row.target)}">去处理</button>`:` <small>${esc(row.responsible||'请联系对应业务负责人处理')}</small>`):' 已完成'}</li>`;}).join('')||'<li>暂无待核对项目</li>';const status=data.is_closed?`<span class="badge danger">已月结</span> ${esc(data.closed_by_name||'')} · ${esc(data.closed_at||'')}`:'<span class="badge ok">可月结</span>';result.innerHTML=`<h2>${esc(data.attraction_name)} · ${esc(data.month)} 月结</h2><p>${status}</p>${data.is_closed?`<p>关闭原因：${esc(data.close_reason||'未填写')}</p>`:`<p>以下项目全部为 0 后才可关闭月结。</p>`}<h3>月结检查清单</h3><ul class="month-close-checklist">${checklistRows}</ul><div class="actions">${!data.is_closed&&data.can_close?'<button type="button" class="primary" data-month-close>确认关闭月结</button>':''}${data.is_closed&&data.can_reopen?'<button type="button" class="warn" data-month-reopen>临时开放月结</button>':''}</div>`;result.querySelectorAll('[data-month-target]').forEach(button=>button.onclick=()=>{state.tab=button.dataset.monthTarget;renderTabs();render();});const reasonPrompt=async(title,action)=>{const reason=await promptModal(`${title}原因`,'将写入审计。','请填写原因','确认');if(reason===null)return;if(!reason.trim()){toast('原因必填',true);return;}try{await api(`/api/month-closes/${month}/${action}`,json('POST',{attraction_id:attractionId,reason:reason.trim()}));toast(action==='close'?'月结已关闭':'月结已临时开放');show();}catch(error){const items=error?.detail?.items||[];toast(items.length?`${error.message}：${items.map(row=>`${row.name}${row.count}项`).join('；')}`:error.message,true);}};result.querySelector('[data-month-close]')?.addEventListener('click',()=>reasonPrompt('确认关闭月结','close'));result.querySelector('[data-month-reopen]')?.addEventListener('click',()=>reasonPrompt('临时开放月结','reopen'));};
  form.onsubmit=async event=>{event.preventDefault();try{await show();}catch(error){toast(error.message,true);}};
  await show();
}

// 小组管理：只管组本身和负责人（主管/代理主管）；组员在员工管理中调整。
function groupLeaderOptions(people,currentId,groupId,leaderType){
  const verb=leaderType==='formal'?'带':'代理';
  const label=person=>{
    if(person.group_id&&person.group_id!==groupId)return `${person.name}（已${verb} ${person.group_name}）`;
    if(groupId&&person.group_id===groupId)return `${person.name}（${person.role_label} · 当前）`;
    return `${person.name}（${person.role_label}${leaderType==='formal'?' · 暂未带组':''}）`;
  };
  return `<option value="">不设置</option>${people.map(person=>`<option value="${person.id}" ${person.id===currentId?'selected':''} ${person.group_id&&person.group_id!==groupId?'disabled':''}>${esc(label(person))}</option>`).join('')}`;
}
function groupLeaderFields(circle,group,reasonRequired){
  const groupId=group?group.id:null;
  return `<label>主管<select name="supervisor_id">${groupLeaderOptions(circle.supervisors,group?.formal_leader?.id||null,groupId,'formal')}</select></label><p class="field-hint">只列本景点圈、本职为主管的人；已带其他组的不可选。可留空。</p><label>代理主管<select name="acting_id">${groupLeaderOptions(circle.acting_candidates,group?.acting_leader?.id||null,groupId,'acting')}</select></label><p class="field-hint">只列本景点圈、有代理TA主管职务的人；已代理其他组的不可选。有代理主管时由代理主管复核组员，主管可查看。可留空。</p><label>原因<input name="reason" maxlength="200" ${reasonRequired?'required':''} placeholder="${reasonRequired?'例如：奚志成接手B组':'选填'}"></label>`;
}
async function renderHrGroups(){
  const request=beginViewRequest();
  const [data,alerts]=await Promise.all([api('/api/hr/groups'),api('/api/hr/alerts')]);
  if(!request.isCurrent())return;
  const circles=data.circles||[];
  if(!circles.length){request.write('<section class="panel"><h2>小组管理</h2><div class="empty">当前账号没有可管理的景点圈</div></section>');return;}
  const circle=circles.find(row=>String(row.id)===String(state.hrGroupsCircle))||circles[0];
  state.hrGroupsCircle=circle.id;
  const groups=circle.groups,missingSupervisor=groups.filter(group=>!group.formal_leader).length,idleSupervisors=circle.supervisors.filter(person=>!person.group_id).length;
  const metrics=[['小组',groups.length,''],['未设置主管',missingSupervisor,missingSupervisor?'warn':''],['暂未带组的主管',idleSupervisors,''],['未分组员工',circle.unassigned_count,circle.unassigned_count?'warn':'']];
  const circlePicker=circles.length>1?`<label class="group-circle-picker">景点圈<select id="groupCircle">${circles.map(row=>`<option value="${row.id}" ${row.id===circle.id?'selected':''}>${esc(row.name)}</option>`).join('')}</select></label>`:`<strong class="group-circle-name">${esc(circle.name)}</strong>`;
  const managerTags=`${circle.gsm_names.length?hrTag(`<b>景点GSM</b>${esc(circle.gsm_names.join('、'))}`):hrTag('未配置GSM','warn')}${circle.ta_gsm_names.length?hrTag(`<b>代理GSM</b>${esc(circle.ta_gsm_names.join('、'))}`,'accent'):''}`;
  const rows=groups.map(group=>{
    const formal=group.formal_leader,acting=group.acting_leader;
    const formalCell=formal?`${esc(formal.name)}${String(formal.role_label||'').includes('代理')?hrTag('代理GSM','accent'):''}`:hrTag('未设置主管','warn');
    return `<div class="group-row" data-group-row="${group.id}"><button type="button" class="group-name link-button" data-group-toggle="${group.id}" aria-expanded="false" title="${esc(group.name)}">${esc(hrGroupShortName(group,circle.name))}</button>${!formal&&!acting&&group.member_count?hrTag('无负责人','warn'):''}<div data-label="主管">${formalCell}</div><div data-label="代理主管">${acting?hrTag(esc(acting.name),'accent'):'<span class="muted-text">无</span>'}</div><div data-label="人数">${group.member_count}</div><div class="actions compact-actions"><button type="button" class="secondary" data-set-leaders="${group.id}">设置负责人</button><button type="button" class="secondary" data-rename-group="${group.id}">改名</button>${group.member_count===0?`<button type="button" class="danger" data-close-group="${group.id}">关闭</button>`:''}</div><div class="group-members" data-group-members="${group.id}" hidden>${group.members.map(member=>`<span>${esc(member.name)}<small>${esc(member.role_label)}</small></span>`).join('')||'<span class="muted-text">暂无组员</span>'}<p class="field-hint">组员只能查看；调整组员请到员工管理。</p></div></div>`;
  }).join('');
  if(!request.write(`<div class="section-gap"><section class="panel"><h2>小组管理</h2><p>改组在这里：新建小组、设置主管和代理主管、关闭空小组。新建小组按“景点圈 + 字母”命名，换负责人不改名，需要时可点“改名”；组员归属请在<button type="button" class="link-button" data-goto-employees>员工管理</button>中调整。</p><div class="group-toolbar">${circlePicker}${managerTags}<span class="toolbar-spacer"></span><button type="button" class="primary" id="createGroup">新建小组（${esc(circle.next_code)}组）</button></div><div class="group-metrics">${metrics.map(([label,value,tone])=>`<div class="group-metric ${tone}"><span>${label}</span><strong>${value}</strong></div>`).join('')}</div><div class="group-table"><div class="group-row group-head" aria-hidden="true"><span>小组</span><span>主管</span><span>代理主管</span><span>人数</span><span></span></div>${rows||'<div class="empty">该景点圈还没有小组</div>'}</div><p class="field-hint">点组名可展开查看组员。</p></section><section class="panel"><h2>组织提醒</h2>${alerts.filter(alert=>alert.status==='open').map(alert=>`<div class="notice">${esc(alert.message)} ${alert.due_date?`· ${alert.due_date}`:''}</div>`).join('')||'<div class="empty">无提醒</div>'}</section></div>`))return;
  document.getElementById('groupCircle')?.addEventListener('change',event=>{state.hrGroupsCircle=event.target.value;renderHrGroups()});
  app.querySelector('[data-goto-employees]').onclick=()=>gotoTab('hrEmployees');
  app.querySelectorAll('[data-group-toggle]').forEach(button=>button.onclick=()=>{const panel=app.querySelector(`[data-group-members="${button.dataset.groupToggle}"]`),open=panel.hidden;panel.hidden=!open;button.setAttribute('aria-expanded',String(open))});
  const leaderBody=form=>({supervisor_id:form.elements.supervisor_id.value||null,acting_id:form.elements.acting_id.value||null,reason:form.elements.reason.value.trim()});
  app.querySelectorAll('[data-set-leaders]').forEach(button=>button.onclick=()=>{
    const group=groups.find(row=>String(row.id)===button.dataset.setLeaders);
    formModal(`设置负责人 · ${group.name}`,`${group.member_count}名组员不受影响`,groupLeaderFields(circle,group,true),'保存',{onSubmit:async form=>{const body=leaderBody(form);if(!body.reason)throw new Error('请填写调整原因');const result=await api('/api/hr/groups/'+group.id+'/leaders',json('POST',{...body,revision:group.revision}));toast('负责人已更新');renderHrGroups();showSaveWarnings(result)}});
  });
  document.getElementById('createGroup').onclick=()=>formModal(`新建小组 · ${circle.name}${circle.next_code}组`,'组名按景点圈 + 下一个字母自动生成，之后可以改名。主管和代理主管可以稍后再设置，没有负责人时组员的签卡暂时无人复核。',groupLeaderFields(circle,null,false),'新建',{onSubmit:async form=>{const result=await api('/api/hr/groups',json('POST',{attraction_id:circle.id,...leaderBody(form)}));toast(`已新建${result.name}`);renderHrGroups()}});
  app.querySelectorAll('[data-rename-group]').forEach(button=>button.onclick=async()=>{
    const group=groups.find(row=>String(row.id)===button.dataset.renameGroup);
    const name=(await promptModal(`修改组名 · ${group.name}`,`<p>当前组名：<strong>${esc(group.name)}</strong></p><p>改名后，组员、负责人和历史记录都不变，排名、统计和导出改显示新组名。</p>`,'请输入新组名','下一步')||'').trim();
    if(!name||name===group.name)return;
    if(!await confirmModal('确认修改组名',`<p>确认将“${esc(group.name)}”改为“${esc(name)}”？</p><p>修改会写入审计日志。</p>`,'确认修改'))return;
    try{await api('/api/hr/groups/'+group.id+'/rename',json('POST',{name,revision:group.revision}));toast('组名已修改');renderHrGroups()}catch(error){toast(error.message,true)}
  });
  app.querySelectorAll('[data-close-group]').forEach(button=>button.onclick=async()=>{const group=groups.find(row=>String(row.id)===button.dataset.closeGroup);const reason=await promptModal(`关闭 ${group.name}`,'该组没有组员。关闭后不再出现在小组选项中，历史记录保留并标注“已关闭”；空出的字母会在下次新建小组时优先使用。请输入关闭原因（必填）：','关闭原因');if(!reason)return;try{await api('/api/hr/groups/'+group.id+'/close',json('POST',{reason,revision:group.revision}));toast('小组已关闭');renderHrGroups()}catch(error){toast(error.message,true)}});
}

async function renderHrScores(){const request=beginViewRequest();const rows=await api('/api/hr/score-rules');if(!request.isCurrent())return;const canEdit=state.me.role_code==='SYSTEM_ADMIN',editable=canEdit?rows:[],readOnly=canEdit?[]:rows,scopeHint=canEdit?'<div class="notice">这是全系统统一分值规则，修改后会影响所有景点圈后续新登记的认可；历史签卡不追溯改分。</div>':'<div class="notice">当前显示全系统统一分值规则，仅最高管理员可调整；本页面为只读。</div>';if(!request.write(`<section class="panel"><h2>认可人角色默认分值</h2><p>分值按角色和生效日期保留历史，已登记签卡不追溯改分。</p>${scopeHint}<div class="table-wrap sticky-col"><table><thead><tr><th>角色</th><th>当前分值</th><th>新分值</th><th>生效日期</th><th>操作</th></tr></thead><tbody>${editable.map(r=>`<tr><td>${esc(r.role_name)}</td><td>${fmt(r.score)}</td><td><input name="score" type="number" min="0" step="0.01" value="${r.score}"></td><td><input name="date" type="date" value="${today()}"></td><td><button class="primary" data-score-role="${r.role_code}">生效</button></td></tr>`).join('')}${readOnly.map(r=>`<tr><td>${esc(r.role_name)}</td><td>${fmt(r.score)}</td><td colspan="3"><span class="not-applicable">全局规则只读</span></td></tr>`).join('')}</tbody></table></div></section>`))return;app.querySelectorAll('[data-score-role]').forEach(b=>b.onclick=async()=>{const tr=b.closest('tr');try{await api('/api/hr/score-rules',json('POST',{role_code:b.dataset.scoreRole,score:tr.querySelector('[name=score]').value,effective_date:tr.querySelector('[name=date]').value}));toast('新分值规则已生效');render()}catch(x){toast(x.message,true)}})}
async function renderLogs(){const request=beginViewRequest();const rows=await api('/api/admin/logs');if(!request.isCurrent())return;if(!request.write(`<section class="panel"><h2>审计日志</h2><div class="table-wrap sticky-col"><table><thead><tr><th>时间</th><th>操作人</th><th>动作</th><th>对象</th><th>原因</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${r.time}</td><td>${esc(r.operator)}</td><td>${esc(r.action)}</td><td>${esc(r.entity)}</td><td>${esc(r.reason)}</td></tr>`).join('')}</tbody></table></div></section>`))return;}

async function renderHrMonthlyReport(){
  if(!has('HR_MONTHLY_REPORT')||!['GSM','AM','OM','SYSTEM_ADMIN'].includes(state.me.role_code)){app.innerHTML='<section class="panel"><div class="error">无HR月报制作权限</div></section>';return;}
  const request=beginViewRequest(),options=await api('/api/hr-monthly-reports/options');
  if(!request.isCurrent())return;
  request.write(`<div class="section-gap hr-report-page"><section class="panel"><h2>HR月报制作</h2><p>选择月份、景点圈和模板，预览后导出可编辑PPTX。未月结数据标为草稿，未提供素材的章节自动跳过。</p><form id="hrReportFilter" class="statistics-filter"><label>月份<input type="month" name="month" value="${monthNow()}" required></label><label>景点圈<select name="attraction_id"><option value="">全部景点圈</option>${opt(options.attractions)}</select></label><label>模板<select name="template">${options.templates.map(r=>`<option value="${esc(r.id)}">${esc(r.name)}</option>`).join('')}</select></label><div class="statistics-actions"><button type="submit" class="primary">预览报告</button><button type="button" id="hrReportExport" class="secondary" disabled>导出 PPTX</button></div></form></section><section class="panel"><h3>报告章节</h3><div class="hr-report-sections">${options.sections.map(r=>`<label><input type="checkbox" data-report-section="${esc(r.id)}" checked> ${esc(r.name)}</label>`).join('')}</div><p class="field-hint">封面固定保留；无数据章节隐藏。照片和祝福由用户提供，不自动生成真实员工事迹。</p><label>工作与规则说明（可选）<textarea id="hrReportNotes" maxlength="2000" rows="3"></textarea></label><label>生日祝福（可选）<textarea id="hrReportBirthday" maxlength="1000" rows="3"></textarea></label><label>活动照片（最多12张，每张5MB，总计30MB）<input id="hrReportPhotos" type="file" accept="image/png,image/jpeg,image/webp" multiple></label><label>活动说明<input id="hrReportCaption" maxlength="150" placeholder="活动名称、日期和说明"></label></section><div id="hrReportPreview"><section class="panel"><div class="empty">请预览所选月份的报告内容</div></section></div></div>`);
  const form=document.getElementById('hrReportFilter'),host=document.getElementById('hrReportPreview'),exportButton=document.getElementById('hrReportExport');let last=null,sequence=0;
  const table=(headers,rows)=>`<div class="table-wrap"><table><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(cell=>`<td>${esc(cell)}</td>`).join('')}</tr>`).join('')||`<tr><td colspan="${headers.length}" class="empty">暂无数据，导出时跳过</td></tr>`}</tbody></table></div>`;
  const show=data=>{const d=data.report,s=d.summary;host.innerHTML=`<section class="panel"><h3>${esc(d.month)} · ${esc(d.scope_name)} <span class="badge ${d.draft?'warn':'ok'}">${d.draft?'草稿':'已月结'}</span></h3><p>${esc(d.organization_basis)}</p><div class="hr-report-summary">${[['统计人数',s.employee_count],['确认认可次数',s.recognition_count],['有效处分次数',s.deduction_count],['认可率',s.recognition_rate==null?'不适用':s.recognition_rate+'%'],['实际加分',s.recognition_score],['实际扣分',s.deduction_score]].map(([name,value])=>`<div class="panel"><small>${esc(name)}</small><strong>${esc(value)}</strong></div>`).join('')}</div>${d.warnings.map(w=>`<p class="field-hint">${esc(w)}</p>`).join('')}${table(['景点圈','计分人数','认可次数','认可率','月结'],d.circles.map(r=>[r.name,r.employee_count,r.recognition_count,r.recognition_rate==null?'不适用':r.recognition_rate+'%',r.closed?'已月结':'草稿']))}</section><section class="panel"><h3>优秀员工候选（人工确认）</h3><p>按各圈各人员类别PR排名推荐前三名。勾选后纳入报告，不生成获奖事迹。</p><div class="hr-report-candidates">${d.candidates.map(r=>`<label><input type="checkbox" data-report-excellent="${r.employee_id}"> ${esc(r.employee_name)} · ${esc(r.employee_no)} · ${esc(r.attraction_name)} · ${esc(r.category)} · ${esc(r.total_score)}分</label>`).join('')||'<div class="empty">暂无候选，导出时跳过</div>'}</div></section>${[['减分分类',d.deductions],['加分分类',d.recognitions]].map(([title,rows])=>`<section class="panel"><details><summary>${title}（有效次数）</summary>${table(['景点圈','类别','类型','次数'],rows.map(r=>[r.attraction_name,r.category,r.type,r.count]))}</details></section>`).join('')}<section class="panel"><details><summary>认可发放排名</summary>${table(['签卡人','次数'],d.issuers.map(r=>[r.name,r.count]))}</details></section><section class="panel"><details><summary>小组分排名</summary>${table(['小组','景点圈','人数','均分'],d.groups.map(r=>[r.name,r.attraction_name,r.employee_count,r.average]))}</details></section>${d.rankings.filter(r=>r.rows.length).map(r=>`<section class="panel"><details><summary>${esc(r.attraction_name)} · ${esc(r.category)} PR排名（${r.rows.length}人）</summary>${table(['排名','员工','工号','加分','实际扣分','全勤','综合分'],r.rows.map(e=>[e.rank,e.employee_name,e.employee_no,e.recognition_score,e.deduction_score,e.attendance_score,e.total_score]))}</details></section>`).join('')}`;};
  const load=async()=>{const current=++sequence;last=null;exportButton.disabled=true;host.innerHTML='<section class="panel"><div class="empty">正在生成报告预览…</div></section>';try{const qs=new URLSearchParams([...new FormData(form)].filter(([k,v])=>v&&k!=='template'));const data=await api('/api/hr-monthly-reports/preview?'+qs);if(current!==sequence||!app.contains(form))return;last=data;show(data);exportButton.disabled=false;}catch(error){if(current===sequence&&app.contains(form))host.innerHTML=`<section class="panel"><div class="error">${esc(error.message)}</div></section>`;}};
  form.onsubmit=e=>{e.preventDefault();void load();};
  ['month','attraction_id'].forEach(key=>form.elements[key].onchange=()=>{++sequence;last=null;exportButton.disabled=true;host.innerHTML='<section class="panel"><div class="empty">筛选已变化，请重新预览</div></section>';});
  exportButton.onclick=async()=>{if(!last)return;const files=[...document.getElementById('hrReportPhotos').files];if(files.length>12||files.some(f=>f.size>5*1024*1024)||files.reduce((n,f)=>n+f.size,0)>30*1024*1024){toast('照片最多12张，每张5MB，总计30MB',true);return;}const selected=[...document.querySelectorAll('[data-report-section]:checked')].map(e=>e.dataset.reportSection);const config={month:last.report.month,attraction_id:last.report.attraction_id,template:form.elements.template.value,sections:selected,excellent_ids:[...new Set([...host.querySelectorAll('[data-report-excellent]:checked')].map(e=>Number(e.dataset.reportExcellent)))],notes:document.getElementById('hrReportNotes').value,birthday:document.getElementById('hrReportBirthday').value,photo_caption:document.getElementById('hrReportCaption').value,preview_digest:last.digest};const body=new FormData();body.append('config',JSON.stringify(config));if(selected.includes('photos'))files.forEach(f=>body.append('photos',f));exportButton.disabled=true;exportButton.textContent='正在制作…';try{const response=await fetch(portalPath('/api/hr-monthly-reports/export'),{method:'POST',body,credentials:'same-origin'});if(!response.ok){const error=await response.json();throw new Error(typeof error.detail==='string'?error.detail:'导出失败');}const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;const match=/filename\*=UTF-8''([^;]+)/i.exec(response.headers.get('Content-Disposition')||'');a.download=match?decodeURIComponent(match[1]):`HR月报_${config.month}.pptx`;a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);toast('月报已导出，可在PowerPoint中编辑');}catch(error){toast(error.message,true);}finally{exportButton.disabled=!last;exportButton.textContent='导出 PPTX';}};
  await load();
}

// 轮岗（测试）：输入模拟账号后进入独立的轮岗页面；数据全为模拟，不影响正式功能。
async function renderRotationTest(){
  const request=beginViewRequest();
  const quick=[['6666666','休息室大屏'],['7777777','轮岗主管'],['8888888','轮岗经理']];
  if(!request.write(`<section class="panel rotation-entry"><div class="rotation-entry-heading"><h2>轮岗</h2><span class="rotation-test-badge">测试</span></div><p>轮岗功能正在测试，所有数据均为模拟数据，不影响正式的签卡、待办和统计。</p><form id="rotationEnterForm" class="form-stack"><label>模拟账号<input name="account" inputmode="numeric" autocomplete="off" maxlength="50" placeholder="输入模拟账号，或名单中 CM/TR 的工号" required></label><button type="submit" class="primary">进入</button></form><div class="rotation-entry-quick">${quick.map(([no,label])=>`<button type="button" class="secondary" data-rotation-account="${no}"><span>${esc(label)}</span><small>${no}</small></button>`).join('')}</div><p class="field-hint">输入名单中某位 CM/TR 的工号，可查看他的个人轮岗和待办。</p></section>`))return;
  const enter=async account=>{try{const out=await api('/api/rotation/enter',json('POST',{account}));location.href=portalPath(out.redirect);}catch(error){toast(error.message,true);}};
  const form=document.getElementById('rotationEnterForm');
  form.onsubmit=event=>{event.preventDefault();const value=form.account.value.trim();if(value)void enter(value);};
  app.querySelectorAll('[data-rotation-account]').forEach(button=>button.onclick=()=>enter(button.dataset.rotationAccount));
}
async function logout(){await api('/api/logout',{method:'POST'});location.href=portalPath('/login')}
document.getElementById('logoutBtn').onclick=logout;
(async()=>{try{state.me=await api('/api/me');if(state.me.must_change_password){renderPasswordChangeRequired();return;}state.options=await api('/api/options');if(statisticsDetailContext().get('employee_ids'))state.tab='statisticsDetail';applyCircleTheme();installScreenWatermark();installPageBindHint();renderUserBadge();renderTabs();void refreshActionBadge();await render();void showReleaseAnnouncement().catch(error=>toast(error.message||'更新公告加载失败',true));}catch(e){if(!location.pathname.includes('/login'))location.href=portalPath('/login');}})();
