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
function toast(message, bad=false, duration=2400, tone='') { clearTimeout(toastTimer); toastEl.textContent=message; toastEl.classList.toggle('error',bad); toastEl.classList.toggle('encouragement',tone==='encouragement'); toastEl.classList.add('show'); if(bad){toastEl.onclick=()=>{toastEl.classList.remove('show');toastEl.onclick=null;};duration=Math.max(duration||0,8000);}else{toastEl.onclick=null;} toastTimer=setTimeout(()=>toastEl.classList.remove('show'),duration); }
function recognitionEncouragement(options){const rows=Array.isArray(options)?options.filter(row=>row&&row.template_id&&row.message):[];if(!rows.length)return '';const key=`recognition-v2:encouragement-seen:${state.me?.employee_no||'anonymous'}`;let seen=[];try{seen=JSON.parse(localStorage.getItem(key)||'[]')}catch(_){seen=[]}const selected=rows.find(row=>!seen.includes(row.template_id))||rows[0];try{localStorage.setItem(key,JSON.stringify([...seen.filter(id=>id!==selected.template_id),selected.template_id].slice(-24)))}catch(_){ }return selected.message;}
function showRecognitionEncouragement(options){const message=recognitionEncouragement(options);if(message)toast(`已提交，等待主管复核。\n${message}`,false,5600,'encouragement');else toast('加分已提交');}
function isNativePickerControl(node){const type=String(node?.type||'').toLowerCase();return type==='file'||type==='date'||type==='month'||type==='datetime-local'||type==='time';}
function dialogFocusables(root){return [...root.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]):not([type="hidden"]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')].filter(node=>!node.hidden&&!node.closest('[hidden]')&&node.getAttribute('aria-hidden')!=='true');}
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
    if(remove&&overlay.isConnected)overlay.remove();
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
async function handlePendingMaterialConflict(error){const detail=error?.detail,record=detail?.code==='PENDING_MATERIAL_EXISTS'?detail.record:null;if(!record?.id)return false;const go=await confirmModal('已有待补材料记录',`<p>${esc(detail.message||'请先补充已有记录的材料。')}</p><p><strong>已有记录：</strong>${esc(record.employee_name)} · ${esc(record.occurred_on)} · ${esc(record.deduction_type)} · ${esc(record.deduction_level)}</p><p>补齐材料并处理完成后，才可继续登记下一条。</p>`,'去补充材料');if(go){state.pendingMaterialFocusId=Number(record.id);state.tab='entries';renderTabs();await render();}return true;}
function promptModal(title,message,placeholder='',confirmText='确认'){return new Promise(resolve=>{const overlay=document.createElement('div');overlay.className='modal-overlay';const titleId='dialog-title-'+Date.now();overlay.innerHTML=`<section class="modal-card" role="dialog" aria-modal="true" aria-labelledby="${titleId}"><h3 id="${titleId}">${esc(title)}</h3><div class="modal-message">${message}<input type="text" class="modal-input" data-modal-input placeholder="${esc(placeholder)}" maxlength="200" autocomplete="off"></div><div class="modal-actions"><button type="button" class="secondary" data-modal-cancel>取消</button><button type="button" class="primary" data-modal-confirm>${esc(confirmText)}</button></div></section>`;document.body.appendChild(overlay);const input=overlay.querySelector('[data-modal-input]'),cancel=overlay.querySelector('[data-modal-cancel]'),confirm=overlay.querySelector('[data-modal-confirm]');const layer=bindDialogLayer(overlay,{initialFocus:input,onClose:value=>resolve(typeof value==='string'?value:null)});const submit=()=>{const value=input.value.trim();if(value)layer.close(value)};input.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();submit();}});cancel.onclick=()=>layer.close(null);confirm.onclick=submit;});}
function selectModal(title,message,rows,confirmText='提交'){return new Promise(resolve=>{const overlay=document.createElement('div');overlay.className='modal-overlay';const titleId='dialog-title-'+Date.now();overlay.innerHTML=`<section class="modal-card" role="dialog" aria-modal="true" aria-labelledby="${titleId}"><h3 id="${titleId}">${esc(title)}</h3><div class="modal-message">${message}<select class="modal-input" data-modal-select><option value="">请选择</option>${rows.map(row=>`<option value="${esc(row.id)}">${esc(row.label||row.name)}</option>`).join('')}</select></div><div class="modal-actions"><button type="button" class="secondary" data-modal-cancel>返回</button><button type="button" class="primary" data-modal-confirm>${esc(confirmText)}</button></div></section>`;document.body.appendChild(overlay);const select=overlay.querySelector('[data-modal-select]'),cancel=overlay.querySelector('[data-modal-cancel]'),confirm=overlay.querySelector('[data-modal-confirm]');const layer=bindDialogLayer(overlay,{initialFocus:select,onClose:value=>resolve(typeof value==='string'&&value?value:null)});cancel.onclick=()=>layer.close(null);confirm.onclick=()=>{if(select.value)layer.close(select.value)};});}
function imagePreviewUrl(url){const separator=String(url).includes('?')?'&':'?';return portalPath(`${url}${separator}preview=1`);}
async function openImagePreview(url,title='签卡图片'){const overlay=document.createElement('div');overlay.className='modal-overlay image-preview-overlay';overlay.innerHTML=`<section class="image-preview-dialog" role="dialog" aria-modal="true" aria-label="${esc(title)}"><header><strong>${esc(title)}</strong><button type="button" class="image-preview-close" data-image-preview-close aria-label="关闭图片预览" title="关闭">×</button></header><div class="image-preview-stage"><span data-image-preview-status>图片加载中…</span><img data-image-preview-image alt="${esc(title)}" hidden></div></section>`;document.body.appendChild(overlay);const status=overlay.querySelector('[data-image-preview-status]'),image=overlay.querySelector('[data-image-preview-image]'),closeButton=overlay.querySelector('[data-image-preview-close]'),controller=new AbortController();let objectUrl='',closed=false;const layer=bindDialogLayer(overlay,{initialFocus:closeButton,onClose:()=>{closed=true;controller.abort();if(objectUrl){URL.revokeObjectURL(objectUrl);objectUrl='';}}});const close=()=>layer.close();closeButton.onclick=close;overlay.onclick=event=>{if(event.target===overlay)close();};image.onload=()=>image.classList.toggle('is-landscape',image.naturalWidth>image.naturalHeight*1.3);try{const response=await fetch(imagePreviewUrl(url),{credentials:'same-origin',signal:controller.signal});if(response.status===401){close();location.href=portalPath('/login');return;}if(!response.ok)throw new Error('图片加载失败');const blob=await response.blob();if(!blob.type.startsWith('image/'))throw new Error('该材料不是可预览图片');objectUrl=URL.createObjectURL(blob);image.src=objectUrl;image.hidden=false;status.hidden=true;}catch(error){if(!closed&&error.name!=='AbortError')status.textContent=error.message||'图片加载失败，请稍后重试';}}
function usesNativeMobilePdfViewer(){return window.matchMedia('(max-width: 700px), (pointer: coarse)').matches;}
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
function installPageBindHint(){let el=document.getElementById('pageBindHint');if(!el){const badge=document.getElementById('userBadge');const host=document.querySelector('.app-identity');if(!badge&&!host)return;el=document.createElement('span');el.id='pageBindHint';if(badge)badge.after(el);else host.appendChild(el)}const now=new Date(),p=n=>String(n).padStart(2,'0');el.textContent=`本页内容绑定 ${state.me.name}(${state.me.employee_no}) · ${p(now.getMonth()+1)}-${p(now.getDate())} ${p(now.getHours())}:${p(now.getMinutes())}`;el.hidden=false}

let screenshotNoticePending=false;
async function recordScreenshotKey(){if(screenshotNoticePending)return;screenshotNoticePending=true;try{const result=await api('/api/security/screenshot-event',{method:'POST'});alert(result.message)}catch(error){alert('检测到截图按键，请勿分享页面数据。记录提交失败，请联系管理员。')}finally{setTimeout(()=>{screenshotNoticePending=false},1200)}}
const handleScreenshotKey=event=>{if(event.key==='PrintScreen')recordScreenshotKey()};
window.addEventListener('keydown',handleScreenshotKey);
window.addEventListener('keyup',handleScreenshotKey);
const opt = (rows, value='id', label='name') => rows.map(x=>`<option value="${esc(x[value])}">${esc(typeof label==='function'?label(x):x[label])}</option>`).join('');
const statusBadge = row => `<span class="badge status-chip ${row.status==='confirmed'||row.status==='issued'||(row.record_type==='sick_leave'&&row.status==='active')?'ok':row.status==='rejected'||row.status==='material_failed'||(row.record_type==='deduction'&&row.status==='active')?'danger':'warn'}">${esc(row.status_name || row.status)}</span>`;
const sameDayDuplicateBadge = row => row.same_day_duplicate?` <span class="badge warn same-day-duplicate" title="同一认可日期、员工、认可类型和认可人已有其他有效记录">${esc(row.same_day_duplicate_label||'今日已有同类登记')}</span>`:'';
function employeePicker(label,key,usage,searchHint='输入姓名或工号查找全部在职CM/TR'){const circles=state.options.employee_circles||state.options.attractions||[];const global=['SUPERVISOR','TA_GSM','GSM'].includes(state.me.role_code)&&['deduction','attendance'].includes(usage);const hint=global?'可搜索范围：全部景点圈在职CM/TR（必须输入姓名或员工号）':searchHint;return `<div class="employee-picker" data-employee-picker data-global-search="${global?'1':'0'}" data-usage="${esc(usage)}" data-search-hint="${esc(hint)}" data-empty-label="${esc(`没有找到符合条件的${hint.replace('输入姓名或工号查找','')}`)}"><span class="employee-picker-label">${esc(label)}</span><div class="employee-picker-filters"><label>员工景点圈<select data-employee-attraction><option value="">全部景点圈</option>${opt(circles)}</select></label><label>搜索员工<input id="${key}Search" data-employee-search autocomplete="off" inputmode="search" placeholder="输入姓名或工号"></label></div><input type="hidden" name="employee_id" id="${key}Value"><div id="${key}Results" class="employee-search-results" hidden></div><div id="${key}Selected" class="employee-selected" hidden></div><span id="${key}Hint" class="field-hint">${esc(hint)}</span></div>`;}
function circleTransferEmployeePicker(){const scope=state.me.role_code==='HR_CIRCLE'?`可搜索范围：${state.me.attraction_name||'所属'}景点圈的在职CM/TR`:'可搜索范围：全部景点圈的在职CM/TR';return `<div class="employee-picker" data-employee-picker data-usage="circle_transfer" data-search-hint="输入姓名或工号查找可调动员工"><span class="employee-picker-label">员工</span><div class="employee-picker-filters"><label>搜索员工<input id="circleTransferEmployeeSearch" data-employee-search autocomplete="off" inputmode="search" placeholder="输入姓名或工号"><small class="field-hint employee-search-scope">${esc(scope)}</small></label></div><input type="hidden" name="employee_id" id="circleTransferEmployeeValue"><div id="circleTransferEmployeeResults" class="employee-search-results" hidden></div><div id="circleTransferEmployeeSelected" class="employee-selected" hidden></div><span id="circleTransferEmployeeHint" class="field-hint">输入姓名或工号查找可调动员工</span></div>`;}
function bindEmployeeSearches(root){root.querySelectorAll('[data-employee-picker]').forEach(picker=>{const input=picker.querySelector('[data-employee-search]'),attraction=picker.querySelector('[data-employee-attraction]'),target=picker.querySelector('[name=employee_id]'),results=picker.querySelector('.employee-search-results'),selected=picker.querySelector('.employee-selected'),hint=picker.querySelector(`#${input.id.replace('Search','Hint')}`)||picker.querySelector('.field-hint'),usage=picker.dataset.usage,searchHint=picker.dataset.searchHint||'输入姓名或工号查找全部在职CM/TR',emptyLabel=picker.dataset.emptyLabel||'没有找到符合条件的在职CM/TR',globalSearch=picker.dataset.globalSearch==='1';let timer=null,controller=null,sequence=0;if(state.me.attraction_id&&attraction&&!globalSearch)attraction.value=String(state.me.attraction_id);/* remaining binding logic is unchanged */const clearSelected=()=>{if(!target.value)return;target.value='';delete target.dataset.attractionId;target.dispatchEvent(new Event('change'));selected.hidden=true;selected.innerHTML='';input.readOnly=false;};const closeResults=()=>{results.hidden=true;results.innerHTML='';};const choose=row=>{target.value=String(row.id);target.dataset.attractionId=String(row.attraction_id||'');target.dispatchEvent(new Event('change'));input.value=`${row.name} · ${row.employee_no}`;input.readOnly=true;closeResults();selected.innerHTML=`<div><strong>${esc(row.name)}</strong><span class="badge">${esc(row.role_name)}</span><small>${esc(row.employee_no)} · ${esc(row.attraction_name||'未分配景点圈')}${row.group_name?` · ${esc(row.group_name)}`:''}</small></div><button type="button" class="secondary" data-employee-clear>重新选择</button>`;selected.hidden=false;selected.querySelector('[data-employee-clear]').onclick=()=>{clearSelected();input.value='';hint.textContent=searchHint;input.focus();};hint.textContent='已选择员工';};const renderRows=data=>{const rows=data.items||[];if(!rows.length){results.innerHTML=`<div class="employee-search-empty">${esc(emptyLabel)}</div>`;results.hidden=false;hint.textContent='没有匹配员工';return;}results.innerHTML=rows.map((row,index)=>`<button type="button" class="employee-search-result" data-result-index="${index}"><span><strong>${esc(row.name)}</strong><em>${esc(row.role_name)}</em></span><small>${esc(row.employee_no)} · ${esc(row.attraction_name||'未分配景点圈')}${row.group_name?` · ${esc(row.group_name)}`:''}</small></button>`).join('');results.hidden=false;results.querySelectorAll('[data-result-index]').forEach(button=>button.onclick=()=>choose(rows[Number(button.dataset.resultIndex)]));hint.textContent=data.total>rows.length?`找到${data.total}名，显示前${rows.length}名，请继续输入缩小范围`:`找到${data.total}名员工`;};const search=async()=>{const requestId=++sequence,keyword=input.value.trim();if(!keyword){closeResults();hint.textContent=searchHint;return;}controller?.abort();controller=new AbortController();results.innerHTML='<div class="employee-search-empty">正在搜索…</div>';results.hidden=false;hint.textContent='正在搜索';const qs=new URLSearchParams({usage,keyword,limit:'30'});if(attraction?.value)qs.set('attraction_id',attraction.value);try{const data=await api('/api/employee-targets?'+qs,{signal:controller.signal});if(requestId===sequence)renderRows(data);}catch(error){if(error.name!=='AbortError'&&requestId===sequence){closeResults();hint.textContent='搜索失败，请稍后重试';toast(error.message,true);}}};const schedule=()=>{clearSelected();clearTimeout(timer);timer=setTimeout(search,120);};input.addEventListener('input',schedule);attraction?.addEventListener('change',()=>{clearSelected();if(input.value.trim())schedule();else closeResults();});});}
function requireEmployeeSelection(form){const target=form.querySelector('[name=employee_id]');if(!target||target.value)return true;toast('请先搜索并选择员工',true);form.querySelector('[data-employee-search]')?.focus();return false;}

function menuItems() {
  const r=state.me.role_code, items=[];
  if (['CM','TR'].includes(r)) items.push(['home','首页'],['register','登记'],['governance','申诉']);
  else if (['TA_SUPERVISOR','SUPERVISOR'].includes(r)) items.push(['review','复核'],['members','组员记录'],['register','绩效登记'],['absence','缺勤登记'],['entries','主管登记记录']);
  else if (['TA_GSM','GSM'].includes(r)) {items.push(['statistics',has('DATA_EXPORT')?'景点数据与导出':'景点数据查看'],['prRankings','PR排名数据'],['register','绩效登记'],['entries','我的登记记录']);}
  else if (['AM','OM'].includes(r)) {items.push(['statistics','景点数据与导出']);if(r==='AM')items.push(['register','POC特别贡献']);}
  else if (r==='SYSTEM_ADMIN') items.push(['hrEmployees','员工管理'],['monthClose','月结'],['circleHrAccounts','景点圈HR账号'],['hrGroups','整组移交'],['circleTransfers','跨圈调动'],['governance','治理复核'],['logs','审计日志'],['hrScores','分值设置']);
  else if (r==='HR_CIRCLE') {items.push(['hrEmployees','员工管理'],['monthClose','月结'],['hrGroups','整组移交'],['circleTransfers','跨圈调动'],['governance','治理复核'],['hrScores','分值设置']);}
  else if (r==='HR_ADMIN') {items.push(['hrEmployees','员工管理'],['hrGroups','整组移交'],['circleTransfers','跨圈调动'],['governance','治理复核'],['hrScores','分值设置']);}
  else if (has('DATA_VIEW')) items.push(['statistics',has('DATA_EXPORT')?'景点数据与导出':'景点数据查看']);
  if(has('DATA_VIEW')&&!items.some(([id])=>id==='statistics'))items.push(['statistics',has('DATA_EXPORT')?'景点数据与导出':'景点数据查看']);
  if(r==='SYSTEM_ADMIN')items.splice(5,0,['operations','系统运营']);
  items.splice(['CM','TR'].includes(r)?1:0,0,['actionCenter','待办']);
  items.push(['changelog','更新记录']);
  items.push(['password',has('PASSWORD_RESET')?'密码管理':'修改密码']);
  return items;
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
  const tabButton=([id,name],extraClass='')=>`<button type="button" data-tab="${id}" class="${id===state.tab?'active':''} ${extraClass}" ${id===state.tab?'aria-current="page"':''}><span>${name}</span>${id==='actionCenter'?`<b class="nav-count-badge" data-action-center-badge ${state.actionBadgeTotal?'':'hidden'}>${state.actionBadgeTotal>99?'99+':state.actionBadgeTotal}</b>`:''}</button>`;
  const ids=[];
  if(items.some(i=>i[0]==='actionCenter')) ids.push('actionCenter');
  for(const item of items){ if(ids.length>=3) break; if(!ids.includes(item[0])) ids.push(item[0]); }
  const primary=ids.map(id=>items.find(i=>i[0]===id));
  const overflow=items.filter(i=>!ids.includes(i[0]));
  const overflowActive=overflow.some(([id])=>id===state.tab);
  tabs.innerHTML=`<div class="tabs-desktop">${items.map(item=>tabButton(item)).join('')}</div><div class="tabs-mobile ${overflow.length?'has-overflow':'no-overflow'}">${primary.map(item=>tabButton(item)).join('')}${overflow.length?`<button type="button" data-open-more class="${overflowActive?'active':''}" aria-label="打开更多功能"><span>更多</span></button>`:''}</div>${overflow.length?`<div class="mobile-more-drawer" hidden><div class="mobile-more-drawer-panel" role="dialog" aria-modal="true" aria-label="更多功能"><header><strong>更多功能</strong><button type="button" class="secondary" data-close-more>关闭</button></header><div class="mobile-more-menu" role="group" aria-label="更多功能">${overflow.map(item=>tabButton(item,'mobile-more-item')).join('')}</div></div></div>`:''}`;
  const drawer=tabs.querySelector('.mobile-more-drawer');
  let drawerLayer=null;
  const closeDrawer=()=>{drawerLayer?.close();};
  tabs.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{state.tab=b.dataset.tab;closeDrawer();renderTabs();render();});
  tabs.querySelector('[data-open-more]')?.addEventListener('click',()=>{if(!drawer||!drawer.hidden)return;drawer.hidden=false;drawerLayer=bindDialogLayer(drawer,{root:drawer.querySelector('.mobile-more-drawer-panel')||drawer,initialFocus:drawer.querySelector('[data-close-more]'),remove:false,inertRoots:[document.getElementById('app'),tabs.querySelector('.tabs-desktop'),tabs.querySelector('.tabs-mobile')].filter(Boolean),onClose:()=>{drawer.hidden=true;drawerLayer=null;}});});
  tabs.querySelector('[data-close-more]')?.addEventListener('click',closeDrawer);
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
    const views={home:renderHome,actionCenter:renderActionCenter,operations:renderOperations,governance:renderGovernance,register:renderRegister,absence:renderAbsence,entries:renderEntries,review:renderReview,upgradeReview:renderUpgradeReview,members:renderMembers,statistics:renderStatistics,statisticsDetail:renderStatisticsDetail,prRankings:renderPrRankings,password:renderPasswordPage,accountReset:renderAccountReset,circleHrAccounts:renderCircleHrAccounts,hrEmployees:renderHrEmployees,monthClose:renderMonthClose,circleTransfers:renderCircleTransfers,hrGroups:renderHrGroups,hrScores:renderHrScores,logs:renderLogs,changelog:renderChangelog};
    await (views[state.tab] || renderHome)();
    if(generation!==renderGeneration) return;
  } catch(e) {
    if(generation!==renderGeneration||e?.name==='AbortError') return;
    app.innerHTML=`<section class="panel"><div class="error">${esc(e.message)}</div></section>`;
  }
}

async function renderActionCenter(){
  const request=beginViewRequest();
  const isUpgradeReviewer=['TA_GSM','GSM'].includes(state.me.role_code);
  const [data,upgradeData]=await Promise.all([api('/api/action-center'),isUpgradeReviewer?api('/api/deduction-upgrades/pending'):Promise.resolve({items:[]})]);
  if(!request.isCurrent())return;
  const items=data.items||[],upgradeItems=upgradeData.items||[],total=Number(data.total||0)+upgradeItems.length;
  setActionBadge(total);
  const todoCards=items.map(item=>`<article class="action-card severity-${esc(item.severity)}"><div class="action-card-main"><span class="action-severity">${item.severity==='critical'?'优先处理':item.severity==='warning'?'请跟进':'待核对'}</span><strong>${esc(item.title)}</strong><p>${esc(item.description)}</p></div><div class="action-card-side"><div class="action-count"><b>${Number(item.count)}</b><span>项</span></div><button type="button" class="secondary" data-action-tab="${esc(item.tab)}">立即查看</button></div></article>`).join('')||'<section class="empty action-center-empty"><strong>当前没有待处理事项</strong><span>新的待办出现后会在这里提醒你。</span></section>';
  const reviewTab=isUpgradeReviewer?`<button type="button" class="secondary" data-action-pane-tab="review">待审核 <span class="badge warn">${upgradeItems.length}</span></button>`:'';
  const reviewPane=isUpgradeReviewer?`<section class="action-center-pane" data-action-pane="review" hidden><p class="field-hint">声明升级工单仅显示分配给当前账户的记录。备忘录和一级警告无需上传文件，处理说明必填。</p><div class="governance-case-list">${upgradeReviewCards(upgradeItems)}</div></section>`:'';
  if(!request.write(`<div class="section-gap action-center-page"><section class="panel action-center-heading"><div><span class="eyebrow">行动中心</span><h2>待办中心</h2><p>${esc(data.month)} · 当前共 <strong>${total}</strong> 项待处理事项</p></div><button type="button" class="secondary" id="refreshActionCenter" title="刷新待办中心">刷新待办</button></section><section class="panel action-center-panel"><div class="action-center-switch"><button type="button" class="primary" data-action-pane-tab="todo">待办 <span class="badge">${Number(data.total||0)}</span></button>${reviewTab}</div><section class="action-center-pane" data-action-pane="todo"><section class="action-center-list">${todoCards}</section></section>${reviewPane}</section></div>`))return;
  const setPane=name=>{app.querySelectorAll('[data-action-pane]').forEach(pane=>{pane.hidden=pane.dataset.actionPane!==name;});app.querySelectorAll('[data-action-pane-tab]').forEach(button=>{const active=button.dataset.actionPaneTab===name;button.classList.toggle('primary',active);button.classList.toggle('secondary',!active);});};
  app.querySelectorAll('[data-action-pane-tab]').forEach(button=>button.onclick=()=>setPane(button.dataset.actionPaneTab));
  document.getElementById('refreshActionCenter').onclick=renderActionCenter;
  app.querySelectorAll('[data-action-tab]').forEach(button=>button.onclick=()=>{state.tab=button.dataset.actionTab;renderTabs();render();});
  if(isUpgradeReviewer)bindUpgradeReviewActions(app,renderActionCenter);
}

function governanceCaseMarkup(row, canReview){
  const isOpen=row.status==='open';
  const typeName=row.case_type==='month_correction'?'月结更正账本':'记录申诉';
  const stateMarkup=`<span class="badge ${isOpen?'warn':'ok'}">${esc(row.status_name)}</span>`;
  const details=`<div class="governance-case-meta"><span>${esc(typeName)}</span><span>提交人：${esc(row.submitted_by_name)}</span><span>提交时间：${esc(row.submitted_at)}</span>${isOpen?`<span>处理时限：${esc(row.due_at)}</span>`:`<span>处理人：${esc(row.resolved_by_name||'系统')}</span>`}</div>`;
  const resolution=row.decision?`<div class="governance-result"><strong>${esc(row.decision_name)}</strong><p>${esc(row.resolution||'')}</p></div>`:'';
  const controls=canReview&&row.can_resolve?`<form class="governance-resolution" data-governance-case="${Number(row.id)}"><label>处理结论<select name="decision" required><option value="uphold">维持原记录</option><option value="correction_required">需要按受控流程更正</option></select></label><label>处理说明<textarea name="resolution" minlength="5" maxlength="500" required placeholder="5至500字，说明复核依据"></textarea></label><button class="primary" type="submit">提交复核</button></form>`:'';
  return `<article class="governance-case ${isOpen?'is-open':''}"><div class="governance-case-heading"><strong>${esc(row.record_type||'治理事项')} #${Number(row.record_id||row.id)}</strong>${stateMarkup}</div>${details}<p class="governance-reason">${esc(row.reason)}</p>${resolution}${controls}</article>`;
}

async function renderGovernance(){
  const request=beginViewRequest();
  const frontline=['CM','TR'].includes(state.me.role_code);
  if(frontline){
    const [caseData,appealable]=await Promise.all([api('/api/governance/cases'),api('/api/governance/appealable-records')]);
    if(!request.isCurrent())return;
    const items=appealable.items||[],cases=caseData.items||[];
    if(!request.write(`<div class="section-gap"><section class="panel"><h2>记录申诉</h2><p class="field-hint">仅可申诉本人被拒绝、撤回的认可记录或有效扣分记录。处理结论不会直接修改原始记录，实质更正仍须走既有受控流程。</p><form id="appealForm" class="form-stack"><label>选择记录<select name="record_key" required><option value="">请选择</option>${items.map(row=>`<option value="${esc(row.record_type)}:${Number(row.record_id)}">${esc(row.label)}</option>`).join('')}</select></label><label>申诉说明<textarea name="reason" minlength="5" maxlength="500" required placeholder="请说明需要复核的事实与依据"></textarea></label><button class="primary" type="submit" ${items.length?'':'disabled'}>提交申诉</button></form>${items.length?'':'<p class="field-hint">当前没有可申诉记录</p>'}</section><section class="panel"><h2>我的申诉</h2><div class="governance-case-list">${cases.map(row=>governanceCaseMarkup(row,false)).join('')||'<p class="empty">暂无申诉记录</p>'}</div></section></div>`))return;
    const form=document.getElementById('appealForm');
    form.onsubmit=async event=>{event.preventDefault();const [record_type,record_id]=String(form.elements.record_key.value||'').split(':');if(!record_type||!record_id)return;const button=form.querySelector('button[type=submit]');button.disabled=true;try{await api('/api/governance/appeals',json('POST',{record_type,record_id:Number(record_id),reason:form.elements.reason.value.trim()}));toast('申诉已提交，请在本页查看处理结果');renderGovernance()}catch(error){toast(error.message,true);button.disabled=false;}};
    return;
  }
  const caseData=await api('/api/governance/cases'),cases=caseData.items||[];
  if(!request.isCurrent())return;
  if(!request.write(`<div class="section-gap"><section class="panel"><h2>治理复核</h2><p class="field-hint">复核人不得是原登记、复核、作废操作人或申诉提交人。结论不直接变更原始记录；需要更正时，仍通过月结、作废等既有受控流程和审计完成。</p><div class="governance-case-list">${cases.map(row=>governanceCaseMarkup(row,Boolean(caseData.can_review))).join('')||'<p class="empty">当前范围没有治理事项</p>'}</div></section></div>`))return;
  app.querySelectorAll('[data-governance-case]').forEach(form=>form.onsubmit=async event=>{event.preventDefault();const button=form.querySelector('button[type=submit]');button.disabled=true;try{await api(`/api/governance/cases/${Number(form.dataset.governanceCase)}/resolve`,json('POST',{decision:form.elements.decision.value,resolution:form.elements.resolution.value.trim()}));toast('复核结论已保存，申请人可在申诉页查看');renderGovernance()}catch(error){toast(error.message,true);button.disabled=false;}});
}

function operationsBackupHtml(backup){
  const latest=backup.latest_backup||{},issues=backup.issues||[],alert=backup.alert||{},task=backup.task||{};
  return `<section class="panel operations-backup ${backup.ok?'is-ok':'is-alert'}"><div class="operations-heading"><div><h2>备份健康</h2><p>最近检查：${esc(backup.checked_at_utc||'暂无健康报告')}</p></div><span class="badge ${backup.ok?'ok':'danger'}">${esc(backup.status||'未知')}</span></div><div class="operations-metrics"><div><span>备份任务</span><strong>${task.exists&&task.enabled?esc(task.state||'已启用'):'未就绪'}</strong></div><div><span>最近备份</span><strong>${esc(latest.created_at_utc||'暂无')}</strong></div><div><span>备份时效</span><strong>${latest.age_hours===undefined||latest.age_hours===null?'暂无':`${esc(latest.age_hours)}小时`}</strong></div><div><span>完整性</span><strong>${latest.quick_check==='ok'&&latest.sha256_verified?'已核验':'待核验'}</strong></div></div><p class="field-hint">外部通知：${alert.configured?'已配置':'未配置'}${alert.attempted?`；最近投递${alert.delivered?'成功':'失败'}`:''}</p>${issues.length?`<ul class="operations-issues">${issues.map(issue=>`<li><strong>${esc(issue.code)}</strong>${issue.message?`：${esc(issue.message)}`:''}</li>`).join('')}</ul>`:''}</section>`;
}

async function renderOperations(){
  const request=beginViewRequest();
  const data=await api('/api/admin/operations-health'),closures=data.month_closures||[],governance=data.governance||{};
  if(!request.isCurrent())return;
  if(!request.write(`<div class="section-gap">${operationsBackupHtml(data.backup||{})}<section class="panel"><div class="operations-heading"><div><h2>运营概览</h2><p>${esc(data.month)} 当前状态</p></div></div><div class="operations-metrics"><div><span>未处理系统告警</span><strong>${Number(data.open_system_alerts||0)}</strong></div><div><span>待确认跨圈调动</span><strong>${Number(data.pending_circle_transfers||0)}</strong></div><div><span>已关闭月结</span><strong>${closures.filter(row=>row.is_closed).length} / ${closures.length}</strong></div></div><div class="operations-closures">${closures.map(row=>`<div><strong>${esc(row.attraction_name)}</strong><span class="badge ${row.is_closed?'ok':'warn'}">${row.is_closed?'已月结':'未月结'}</span><small>${row.is_closed?`关闭人：${esc(row.closed_by_name||'系统')}`:'等待核对'}</small></div>`).join('')||'<span class="field-hint">暂无景点圈月结信息</span>'}</div></section><section class="panel"><div class="operations-heading"><div><h2>治理与留存复核</h2><p>仅显示聚合数量，不展示人员或附件内容。</p></div></div><div class="operations-metrics"><div><span>待处理治理事项</span><strong>${Number(governance.open_cases||0)}</strong></div><div><span>超时申诉</span><strong>${Number(governance.overdue_cases||0)}</strong></div><div><span>超过48小时待复核</span><strong>${Number(governance.overdue_recognition_reviews||0)}</strong></div><div><span>附件留存待复核</span><strong>${Number(governance.retention_review_files||0)}</strong></div></div></section></div>`))return;
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
      <div><span>姓名 / 角色</span><strong>${esc(state.me.name)} · ${esc(state.me.role_name)}</strong></div>
      <div><span>景点圈</span><strong>${esc(state.me.attraction_name||'未分配')}</strong></div>
      <div><span>组长</span><strong>${esc(state.me.leader_name)}</strong></div>
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
  const showRecognition=has('SELF_RECOGNITION')||has('EMPLOYEE_ADD');
  const canIssuePoc=['TA_GSM','GSM','AM'].includes(state.me.role_code);
  const employeeField=has('EMPLOYEE_ADD')?employeePicker('被加分员工（必选）','addEmployee','recognition'):'';
  const imageField=has('SELF_RECOGNITION')?`<fieldset class="evidence-picker"><legend>认可图片（必传1张）</legend><div class="evidence-actions native-file-actions"><label class="secondary native-file-trigger" for="recognitionCameraInput">拍照</label><label class="secondary native-file-trigger" for="recognitionAlbumInput">从相册选择</label></div><input id="recognitionCameraInput" class="native-file-input" name="image_camera" data-evidence-input data-evidence-camera-input type="file" accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp" capture="environment"><input id="recognitionAlbumInput" class="native-file-input" name="image_album" data-evidence-input data-evidence-album-input type="file" accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp"><span class="field-hint" data-evidence-name>请选择一张JPG、PNG或WebP图片，最大100MB。若入口未打开，请直接点击另一个入口重试。</span></fieldset>`:'';
  const recognitionPanel=showRecognition?`<section class="panel"><h2>${has('EMPLOYEE_ADD')?'员工加分登记':'快速登记'}</h2><p>${has('EMPLOYEE_ADD')?'代录后默认通过，签卡人默认为当前账户。':'本人提交后由当前组长复核。'}</p>
    <form id="recognitionForm" class="form-stack" enctype="multipart/form-data">${employeeField}<div class="grid two"><label>认可日期<input name="recognition_date" id="recognitionDate" type="date" value="${today()}" required></label><label>认可类型<select name="recognition_type_id" id="recognitionTypeSelect" required>${opt((state.options.recognition_types||[]).filter(row=>!row.dedicated_entry))}</select></label></div>
    <label>认可内容<input name="content" maxlength="20" required placeholder="请输入20字以内的认可内容"></label>${imageField}<label>发生景点<select name="occurred_attraction_id" id="attractionSelect" required><option value="">请选择</option>${opt(state.options.recognition_venues||[])}</select></label>
    <label>认可人 / 签卡人<div class="recognizer-picker" data-recognizer-picker><select name="recognizer_employee_id" id="recognizerSelect" class="recognizer-native-select" aria-hidden="true" tabindex="-1"><option value="">请先选择员工</option></select><button type="button" class="recognizer-trigger" aria-haspopup="listbox" aria-expanded="false" aria-controls="recognizerMenu"><span data-recognizer-value>请选择</span><span aria-hidden="true" class="recognizer-trigger-icon">▾</span></button><div id="recognizerMenu" class="recognizer-menu" role="listbox" hidden></div></div><span id="scoreHint" class="field-hint">可选择三个景点圈的TALEAD/LEAD或TAGSM及以上；HR账号不显示</span></label><div class="form-sticky-actions"><button class="primary">提交加分</button></div></form></section>`:'';
  const recognitionSection=showRecognition&&['TA_SUPERVISOR','SUPERVISOR'].includes(state.me.role_code)?`<details class="secondary-feature"><summary>其他功能 · 员工加分登记</summary>${recognitionPanel}</details>`:recognitionPanel;
  const pocPanel=canIssuePoc?`<section class="panel" data-poc-panel><h2>POC特别贡献</h2><p>仅TA GSM、GSM、AM可开具；仅CM/TR、TA主管、主管可获得。POC独立计分，不受五类认可单项月度上限影响。</p><form id="pocRecognitionForm" class="form-stack" data-poc-form>${employeePicker('被认可员工（必选）','pocEmployee','poc','输入姓名或工号查找全部在职CM/TR、TA主管、主管')}<div class="grid two"><label>认可日期<input name="recognition_date" type="date" value="${today()}" required></label><label>认可周期<select name="poc_period_type" required><option value="month">月度</option><option value="quarter">季度</option></select></label></div><label>POC分值<select name="points" required><option value="">请选择</option><option value="1">+1 分</option><option value="2">+2 分</option><option value="3">+3 分</option><option value="4">+4 分</option><option value="5">+5 分</option></select></label><label>特别贡献原因<textarea name="poc_reason" maxlength="100" required placeholder="请填写特别贡献原因（最多100字）"></textarea></label><p class="field-hint">签卡人和代录人固定为当前登录账号；提交后直接计入POC独立加分。</p><div class="form-sticky-actions"><button class="primary">提交POC特别贡献</button></div></form></section>`:'';
  app.innerHTML=`<div class="section-gap">${recognitionSection}${pocPanel}${has('DEDUCTION_DIRECT')||has('DEDUCTION_ALL')?deductionForm():''}</div>`;
  bindDateSubmissionFallbacks(app);
  bindEmployeeSearches(app);
  const recognitionForm=document.getElementById('recognitionForm');
  if(recognitionForm){
    const evidenceInputs=[...recognitionForm.querySelectorAll('[data-evidence-input]')],evidenceName=recognitionForm.querySelector('[data-evidence-name]');evidenceInputs.forEach(input=>input.onchange=()=>{if(input.files?.length){evidenceInputs.filter(other=>other!==input).forEach(other=>{other.value='';});evidenceName.textContent=`已选择：${input.files[0].name}`;}});
    const attraction=document.getElementById('attractionSelect'), recognizer=document.getElementById('recognizerSelect'),recognitionType=document.getElementById('recognitionTypeSelect'),recognitionDate=document.getElementById('recognitionDate'),target=recognitionForm.querySelector('[name=employee_id]');let recognizerRows=[];
    function recognizerGroups(rows){const groups=new Map([['circle:heat',{label:'热力追踪主管',order:0,rows:[]}],['circle:dwarf',{label:'矮人迷宫主管',order:1,rows:[]}],['circle:bear',{label:'小熊罐子主管',order:2,rows:[]}],['tagsm_plus',{label:'TAGSM及以上',order:3,rows:[]}]]);rows.forEach(row=>{let key=row.group_key||'other';if(key.startsWith('circle:'))key=[...groups].find(([,group])=>group.label===row.group_label||group.label===`${row.group_label}主管`)?.[0]||key;const entry=groups.get(key)||{label:row.group_label||'其他',order:Number(row.group_order||99),rows:[]};entry.rows.push(row);groups.set(key,entry)});return [...groups.values()].sort((a,b)=>a.order-b.order);}
    function recognizerRowLabel(row){return `${row.name} · ${row.role_name} · ${fmt(row.score)}分`;}
    function renderRecognizerOptions(){const selectedType=state.options.recognition_types.find(x=>String(x.id)===recognitionType.value),specialCode=selectedType?.fixed_score!=null?selectedType.code:null;const rows=specialCode?recognizerRows.filter(x=>String(x.id)===`special:${specialCode}`):recognizerRows.filter(x=>!x.special);recognizer.innerHTML='<option value="">请选择</option>'+opt(rows,'id',recognizerRowLabel);if(specialCode&&rows.length){recognizer.value=String(rows[0].id)}else{const own=rows.find(x=>x.id===state.me.id);if(own)recognizer.value=String(own.id)}const picker=recognizer.closest('[data-recognizer-picker]'),trigger=picker.querySelector('.recognizer-trigger'),valueLabel=picker.querySelector('[data-recognizer-value]'),menu=picker.querySelector('.recognizer-menu');const closeMenu=()=>{menu.hidden=true;trigger.setAttribute('aria-expanded','false');};if(picker._closeRecognizerMenu)document.removeEventListener('click',picker._closeRecognizerMenu);picker._closeRecognizerMenu=event=>{if(!picker.contains(event.target))closeMenu();};document.addEventListener('click',picker._closeRecognizerMenu);registerPageCleanup(()=>document.removeEventListener('click',picker._closeRecognizerMenu));const syncTrigger=()=>{const selected=rows.find(row=>String(row.id)===recognizer.value);valueLabel.textContent=selected?recognizerRowLabel(selected):'请选择';};const choose=id=>{recognizer.value=String(id);syncTrigger();closeMenu();recognizer.dispatchEvent(new Event('change',{bubbles:true}));};const personButtons=items=>items.length?items.map(row=>`<button type="button" class="recognizer-person" role="option" data-recognizer-option="${esc(row.id)}">${esc(recognizerRowLabel(row))}</button>`).join(''):'<div class="recognizer-empty">暂无可选人员</div>';if(specialCode){menu.innerHTML=personButtons(rows);}else{menu.innerHTML=recognizerGroups(rows).map(group=>`<section class="recognizer-group"><button type="button" class="recognizer-group-toggle" aria-expanded="false">${esc(group.label)}<span aria-hidden="true">▸</span></button><div class="recognizer-group-list" hidden>${personButtons(group.rows)}</div></section>`).join('');menu.querySelectorAll('.recognizer-group-toggle').forEach(toggle=>toggle.onclick=()=>{const list=toggle.nextElementSibling,isOpen=!list.hidden;menu.querySelectorAll('.recognizer-group-list').forEach(other=>{other.hidden=true;other.previousElementSibling.setAttribute('aria-expanded','false');other.previousElementSibling.lastElementChild.textContent='▸';});if(!isOpen){list.hidden=false;toggle.setAttribute('aria-expanded','true');toggle.lastElementChild.textContent='▾';}});}menu.querySelectorAll('[data-recognizer-option]').forEach(button=>{button.setAttribute('aria-selected',String(button.dataset.recognizerOption===recognizer.value));button.onclick=()=>choose(button.dataset.recognizerOption);});trigger.onclick=()=>{if(menu.hidden){menu.hidden=false;trigger.setAttribute('aria-expanded','true');}else closeMenu();};trigger.onkeydown=event=>{if(event.key==='Escape'){closeMenu();return;}if(event.key==='Enter'||event.key===' '){event.preventDefault();trigger.click();}};recognizer.onchange=()=>{const r=rows.find(x=>String(x.id)===recognizer.value);syncTrigger();document.getElementById('scoreHint').textContent=r?(specialCode?`${r.name}固定 ${fmt(r.score)} 分${selectedType.monthly_limit?`；每名员工每月限${selectedType.monthly_limit}次`:''}`:`${r.group_label} · 当前角色分值：${fmt(r.score)} 分`):'分值按认可人当日角色自动计算';};recognizer.onchange();}
    async function loadRecognizers(circleId,dateValue=recognitionDate?.value){recognizer.innerHTML='<option>正在加载…</option>';const params=new URLSearchParams({attraction_id:circleId});if(dateValue)params.set('recognition_date',dateValue);recognizerRows=await api('/api/recognizers?'+params);renderRecognizerOptions();}
    recognitionType.onchange=()=>{if(recognizerRows.length)renderRecognizerOptions();};
    target?.addEventListener('change',()=>{const circleId=target.dataset.attractionId;if(circleId)loadRecognizers(circleId);else{recognizerRows=[];recognizer.innerHTML='<option value="">请先选择员工</option>';const picker=recognizer.closest('[data-recognizer-picker]');picker.querySelector('[data-recognizer-value]').textContent='请先选择员工';picker.querySelector('.recognizer-menu').hidden=true;picker.querySelector('.recognizer-trigger').setAttribute('aria-expanded','false');}}); recognitionDate?.addEventListener('change',()=>{const circleId=target?.dataset.attractionId||state.me.attraction_id;if(circleId)loadRecognizers(circleId);}); if(!target&&state.me.attraction_id) await loadRecognizers(state.me.attraction_id);
    recognitionForm.onsubmit=async e=>{e.preventDefault();if(!requireEmployeeSelection(e.target))return;const picker=recognizer.closest('[data-recognizer-picker]');if(!recognizer.value){toast('请选择认可人',true);picker.querySelector('.recognizer-trigger').focus();return;}await withSubmitLock(e.target,async()=>{try{const submit=confirmed=>{const data=recognitionSubmissionData(e.target,has('SELF_RECOGNITION')&&!has('EMPLOYEE_ADD'));requireSubmittedDate(data,recognitionDate,'认可日期');if(confirmed)data.set('same_day_duplicate_confirmed','true');return api('/api/recognitions',{method:'POST',body:data});};let result;try{result=await submit(false);}catch(x){if(x?.detail?.code!=='SAME_DAY_RECOGNITION_DUPLICATE')throw x;const previous=(x.detail.previous_records||[]).map(row=>`<li>${esc(row.submitted_at)}：${esc(row.content)}</li>`).join('');const accepted=await confirmModal('今日已有同类登记',`<p>${esc(x.message)}</p><ul>${previous}</ul>`,'确认是独立表现，继续登记');if(!accepted)return;result=await submit(true);}clearSubmissionKey(e.target);showRecognitionEncouragement(result?.encouragement_options);e.target.querySelector('[name=content]').value='';evidenceInputs.forEach(input=>{input.value='';});if(evidenceName)evidenceName.textContent='请选择一张JPG、PNG或WebP图片，最大100MB。';}catch(x){toast(recognitionImageFailureMessage(x)||x.message,true)}})};
  }
  const pocForm=document.getElementById('pocRecognitionForm');
  if(pocForm)pocForm.onsubmit=async event=>{event.preventDefault();if(!requireEmployeeSelection(pocForm))return;await withSubmitLock(pocForm,async()=>{try{const data=submissionData(pocForm);requireSubmittedDate(data,pocForm.querySelector('[name=recognition_date]'),'认可日期');await api('/api/recognitions/poc',{method:'POST',body:data});clearSubmissionKey(pocForm);toast('POC特别贡献已登记并计分');pocForm.reset();pocForm.querySelector('[name=employee_id]').value='';pocForm.querySelector('.employee-selected').hidden=true;pocForm.querySelector('[data-employee-search]').value='';}catch(error){toast(error.message,true)}})};
  if(document.getElementById('deductionForm')) bindDeductionV2244();
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
function deductionForm(){const levels=has('DEDUCTION_ALL')?state.options.deduction_levels:state.options.deduction_levels.filter(x=>x.code==='STATEMENT'),types=(state.options.deduction_types||[]).filter(x=>x.code!=='SICK_LEAVE_VIOLATION');return `<section class="panel"><h2>扣分登记</h2><p>按姓名或员工号搜索CM/TR；未上传材料时可先登记为待补充，材料就绪后才扣分。</p><form id="deductionForm" class="form-stack" enctype="multipart/form-data">${employeePicker('被扣分员工（必选）','deductionEmployee','deduction')}<div class="grid two"><label>扣分类型<select name="deduction_type_id" id="deductionTypeSelect">${opt(types)}</select></label><label>扣分等级<select name="deduction_level_id" id="deductionLevelSelect">${levels.map(x=>`<option value="${x.id}" data-code="${esc(x.code)}">${esc(`${x.name} · ${fmt(x.points)}分`)}</option>`).join('')}</select></label></div><label>事件日期<input name="occurred_on" id="deductionOccurredOn" type="date" value="${today()}" required></label><div id="deductionRepeatWarning" class="repeat-warning" hidden></div><details class="form-more"><summary>更多选项</summary><label>事件说明<textarea name="description" required></textarea></label>${deductionMaterialPickerMarkup()}</details><div id="deductionMaterialState" aria-live="polite"></div><div class="form-sticky-actions"><button id="deductionSubmit" class="danger">提交扣分登记</button></div></form></section>`;}
function sickForm(){return `<section class="panel"><h2>缺勤登记</h2><p>登记CM/TR病假缺勤；支持0.5天，半天扣0.25分。同一员工已有日期交集的缺勤记录时，不能再次提交。</p><form id="sickForm" class="form-stack" enctype="multipart/form-data">${employeePicker('缺勤员工（必选）','sickEmployee','attendance')}<div class="grid two"><label>开始日期<input name="leave_start_date" type="date" value="${today()}" required></label><label>结束日期<input name="leave_end_date" type="date" value="${today()}" required></label></div><p id="sickDateError" class="field-error" aria-live="polite" hidden></p><label>缺勤天数<input name="leave_days" type="number" step="0.5" min="0.5" max="1" value="1" required><span class="field-hint">可以按0.5天调整，但不能超过所选日期范围。</span></label><label>缺勤证明（图片/PDF）<input name="proof" type="file" accept="image/*,.pdf" required><span class="field-hint">必传；支持 JPG/JPEG、PNG、HEIC、WebP、PDF，单个文件不超过100MB。</span></label><details class="form-more"><summary>更多选项</summary><label>备注<input name="note"></label></details><div class="form-sticky-actions"><button class="warn">提交缺勤登记</button></div></form></section>`;}
function bindDeduction(){const form=document.getElementById('deductionForm'),employee=form.querySelector('[name=employee_id]'),type=form.querySelector('[name=deduction_type_id]'),level=form.querySelector('[name=deduction_level_id]'),occurred=form.querySelector('[name=occurred_on]'),warning=document.getElementById('deductionRepeatWarning'),submit=document.getElementById('deductionSubmit');const ranks={STATEMENT:1,MEMO:2,WARNING_1:3,WARNING_2:4};let repeatInfo=null,checkSequence=0;const resetLevels=()=>[...level.options].forEach(option=>option.disabled=false);const checkRepeat=async()=>{const sequence=++checkSequence;repeatInfo=null;resetLevels();warning.hidden=true;warning.innerHTML='';submit.disabled=form.dataset.submitting==='true';const selectedType=state.options.deduction_types.find(row=>String(row.id)===type.value);if(!employee.value||!occurred.value||!selectedType?.repeat_check)return null;try{const params=new URLSearchParams({employee_id:employee.value,deduction_type_id:type.value,occurred_on:occurred.value});const info=await api('/api/deductions/attendance-repeat-check?'+params);if(sequence!==checkSequence)return null;repeatInfo=info;if(info.has_repeat){const history=info.previous_records.map(row=>`<li>${esc(row.occurred_on)}：${esc(row.deduction_type)}，${esc(row.deduction_level)}（${fmt(row.points)}分）</li>`).join('');warning.className=`repeat-warning ${info.blocked?'blocked':'notice'}`;warning.innerHTML=`<strong>3个月内同类型重复提醒</strong><p>${esc(info.message)}</p><ul>${history}</ul>`;warning.hidden=false;if(info.blocked){submit.disabled=true;}else{const minimumRank=ranks[info.minimum_level_code]||1;[...level.options].forEach(option=>option.disabled=(ranks[option.dataset.code]||0)<minimumRank);if((ranks[level.selectedOptions[0]?.dataset.code]||0)<minimumRank)level.value=String(info.minimum_level_id);}}return info;}catch(error){if(sequence!==checkSequence)return null;warning.className='repeat-warning blocked';warning.innerHTML='<strong>暂时无法检查3个月内同类型记录，请稍后重试。</strong>';warning.hidden=false;submit.disabled=true;throw error;}};employee.addEventListener('change',()=>checkRepeat().catch(()=>{}));type.addEventListener('change',()=>checkRepeat().catch(()=>{}));occurred.addEventListener('change',()=>checkRepeat().catch(()=>{}));form.onsubmit=async e=>{e.preventDefault();if(!requireEmployeeSelection(form)||form.dataset.submitting==='true')return;try{const info=await checkRepeat();if(info?.blocked){toast(info.message,true);return;}const data=submissionData(form);if(info?.has_repeat){const history=info.previous_records.map(row=>`<div>${esc(row.occurred_on)}：${esc(row.deduction_type)}，${esc(row.deduction_level)}（${fmt(row.points)}分）</div>`).join('');const accepted=await confirmModal('3个月内同类型重复提醒',`<p>${esc(info.message)}</p>${history}`,'我已知晓，继续登记');if(!accepted)return;data.set('repeat_confirmed','true');}else if(!confirm('扣分提交后立即生效，是否继续？'))return;form.dataset.submitting='true';submit.disabled=true;await api('/api/deductions',{method:'POST',body:data});clearSubmissionKey(form);toast('扣分已生效');form.querySelector('[name=description]').value='';form.querySelector('[name=document]').value='';}catch(x){if(!(await handlePendingMaterialConflict(x)))toast(x.message,true)}finally{delete form.dataset.submitting;await checkRepeat().catch(()=>{});}};}
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
          if(out.processing){showDeductionMaterialState(stateBox,out.record);toast('扣分登记已提交，材料正在生成PDF。生成结果请到“我的登记记录”查看。');}else toast('声明升级工单已提交，等待审核');
          return;
        }
      }
      if(!confirm(materialMode==='photos'?'确认提交照片材料？PDF生成成功后才会正式扣分。':materialMode==='pdf'?'确认提交PDF材料？系统会后台优化后生效。':'未上传材料：将创建待补充记录，TA主管、主管、TA GSM和GSM均可补充；在材料就绪前不扣分。是否继续？'))return;
      form.dataset.submitting='true';submit.disabled=true;
      const out=await api('/api/deductions',{method:'POST',body:data});clearSubmissionKey(form);form.reset();material.reset();
      if(out.record?.material_status==='processing'){showDeductionMaterialState(stateBox,out.record);toast('扣分登记已提交，材料正在后台处理。');}else if(out.record?.material_status==='missing'){await confirmModal('声明已登记', '<p>该声明尚未上传材料，已登记为“待补充材料”。</p><p><strong>暂不扣分。</strong>请在“待办”或“我的登记记录”中点击“补充材料”完成上传。</p>', '我知道了');}else toast('扣分已生效');
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

async function renderEntries(){
  const isSupervisor=['TA_SUPERVISOR','SUPERVISOR'].includes(state.me.role_code);
  const canCollaborate=['TA_SUPERVISOR','SUPERVISOR','TA_GSM','GSM'].includes(state.me.role_code);
  const title=isSupervisor?'主管登记记录':'我的登记记录';
  const description=isSupervisor?'默认展示本人登记的扣分和缺勤记录；可切换查看全部主管登记记录。其他主管的记录仅供查询，本人记录继续按原权限操作。':'展示本人登记的数据和重复处分跟进状态。';
  const scopeField=isSupervisor?'<label>查看范围<select name="scope"><option value="mine">我的登记记录</option><option value="supervisors">全部主管登记记录</option></select></label>':'';
  const recordTypes=isSupervisor?'<option value="all">全部</option><option value="deduction">扣分</option><option value="sick_leave">缺勤</option>':'<option value="all">全部</option><option value="recognition">加分</option><option value="deduction">扣分</option><option value="sick_leave">缺勤</option><option value="follow_up">处分跟进</option>';
  app.innerHTML=`<div class="section-gap"><section class="panel"><h2>${title}</h2><p>${description}</p><form id="entryFilter" class="entry-filter">${scopeField}<label>开始日期<input name="start_date" type="date" value="${monthStart()}"></label><label>结束日期<input name="end_date" type="date" value="${today()}"></label><label>记录类型<select name="record_type">${recordTypes}</select></label><label>状态<select name="status"><option value="">全部</option><option value="confirmed">已确认</option><option value="pending">待复核 / 待经理跟进</option><option value="issued">已开具</option><option value="rejected">不通过</option><option value="pending_material">待补充材料</option><option value="material_processing">材料生成中</option><option value="material_failed">材料生成失败</option><option value="active">已生效</option><option value="void">已作废</option></select></label><label>员工搜索<input name="keyword" placeholder="姓名/员工号"></label><button class="primary">查询</button></form><div class="actions"><button id="allHistoryBtn" class="secondary">查看全部历史</button></div></section><div id="entryResults"></div></div>`;
  const form=document.getElementById('entryFilter');let currentPage=1;
  const load=async()=>{
    const request=beginViewRequest();
    const qs=new URLSearchParams([...new FormData(form)].filter(([,value])=>value));qs.set('page',String(currentPage));qs.set('page_size','100');
    const materialScope=state.pendingMaterialScope||'mine';
    const [data,pendingMaterials]=await Promise.all([api('/api/my-entries?'+qs),canCollaborate?api('/api/deductions/pending-materials?'+new URLSearchParams({scope:materialScope})):Promise.resolve({items:[]})]);
    if(!request.isCurrent())return;
    const collaboration=(pendingMaterials.items||[]);
    const collaborationPanel=canCollaborate?`<section class="panel"><h3>待补充声明材料</h3><p class="field-hint">TA主管、主管、TA GSM和GSM均可协作补充；补齐并处理成功后才会正式扣分。本人提交且尚未计分的记录可作废。</p><label class="material-scope-filter">景点圈范围<select data-pending-material-scope><option value="mine" ${materialScope==='mine'?'selected':''}>我的景点圈</option><option value="all" ${materialScope==='all'?'selected':''}>全部景点圈</option></select></label>${collaboration.length?`<div class="mobile-only work-card-list">${collaboration.map(row=>{const own=Number(row.submitter_id)===Number(state.me.id);const origin=own?'<small class="material-inline-state">我提交</small>':'<small class="material-inline-state">协作补充</small>';const ownVoid=own?` <button data-entry-void="${row.id}" class="secondary">作废</button>`:'';return `<article class="work-card"><div class="work-card-head"><strong>${esc(row.employee_name)}</strong><small>${esc(row.employee_no)}</small>${statusBadge(row)}</div><p>${esc(row.occurred_on)} · ${esc(row.deduction_type)}</p><p>${origin}</p><div class="work-card-actions"><button data-entry-material-retry="${row.id}" class="secondary">补充材料</button>${ownVoid}</div></article>`;}).join('')}</div><div class="desktop-only table-wrap sticky-col"><table><thead><tr><th>登记时间</th><th>员工</th><th>事件日期</th><th>内容</th><th>状态</th><th>操作</th></tr></thead><tbody>${collaboration.map(row=>{const own=Number(row.submitter_id)===Number(state.me.id);const origin=own?'<br><small class="material-inline-state">我提交</small>':'<br><small class="material-inline-state">协作补充</small>';const ownVoid=own?` <button data-entry-void="${row.id}" class="secondary">作废</button>`:'';return `<tr><td>${esc(row.submitted_at)}</td><td>${esc(row.employee_name)}<br><small>${esc(row.employee_no)}</small></td><td>${esc(row.occurred_on)}</td><td>${esc(row.deduction_type)} · ${esc(row.deduction_level)}${origin}<br>${esc(row.description)}</td><td>${statusBadge(row)}</td><td><button data-entry-material-retry="${row.id}" class="secondary">补充材料</button>${ownVoid}</td></tr>`;}).join('')}</tbody></table></div>`:'<div class="empty">当前范围暂无待补充声明材料</div>'}</section>`:'';
    document.getElementById('entryResults').innerHTML=`${collaborationPanel}<section class="panel"><div class="mobile-only work-card-list">${data.items.map(entryCard).join('')||'<div class="empty">无匹配记录</div>'}</div><div class="desktop-only table-wrap sticky-col"><table><thead><tr><th>登记时间</th><th>类别</th><th>员工</th><th>业务日期</th><th>内容</th><th>分值/天数</th><th>状态</th><th>操作</th></tr></thead><tbody>${data.items.map(entryRow).join('')||'<tr><td colspan="8" class="empty">无匹配记录</td></tr>'}</tbody></table></div><div class="pagination"><button id="entryPrev" class="secondary" ${data.page<=1?'disabled':''}>上一页</button><span>第${data.page}页，共${data.total}条</span><button id="entryNext" class="secondary" ${data.page*data.page_size>=data.total?'disabled':''}>下一页</button></div></section>`;
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
    return `<tr class="${row.status==='void'?'void-row':''}"><td>${esc(row.submitted_at)}</td><td>缺勤</td><td>${esc(row.employee_name)}<br><small>${esc(row.employee_no)}</small></td><td>${esc(row.leave_start_date)} 至 ${esc(row.leave_end_date)}</td><td>${esc(row.note||'无备注')} ${attachmentControl(row.proof_url,'证明',row.proof_preview_kind)}${submitterNote}${row.void_reason?`<br><small>作废原因：${esc(row.void_reason)}</small>`:''}</td><td>${Number(row.leave_days).toFixed(1)}天<br><small>计费${row.charged_days}天</small></td><td>${statusBadge(row)}</td><td>${row.available_actions.includes('void')?`<button data-entry-sick-void="${row.id}" class="secondary">作废</button>`:''}</td></tr>`;
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
    return `<article class="work-card ${row.status==='void'?'void-row':''}"><div class="work-card-head">${person}${chip}</div><p>${esc(row.leave_start_date)} 至 ${esc(row.leave_end_date)} · 缺勤 · ${Number(row.leave_days).toFixed(1)}天</p><p>${esc(row.note||'无备注')} ${proof}</p><div class="work-card-actions">${actions}</div></article>`;
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
async function renderReview(showHistory=false, offset=0){
  const request=beginViewRequest();
  const view=showHistory?'history':'queue';
  const data=await api('/api/reviews?view='+view+'&limit=200&offset='+offset);
  if(!request.isCurrent())return;
  const rows=data.items||[];
  const total=Number(data.total||0);
  const limit=Number(data.limit||200);
  const pageOffset=Number(data.offset||offset||0);
  const hasMore=Boolean(data.has_more);
  const mobileQuery=window.matchMedia('(max-width: 760px)');
  const cards=rows.map(reviewCard).join('')||'<div class="empty">暂无记录</div>';
  const table=`<div class="table-wrap sticky-col"><table><thead><tr><th>提交时间</th><th>组员</th><th>认可信息</th><th>分值</th><th>状态</th><th>操作</th></tr></thead><tbody>${rows.map(reviewTableRow).join('')||'<tr><td colspan="6" class="empty">暂无记录</td></tr>'}</tbody></table></div>`;
  const hint=showHistory?`已处理记录（已确认和不通过，共${total}条）`:`待复核记录（共${total}条，最早提交的在前）`;
  const pager=(total>limit||pageOffset>0||hasMore)?`<div class="pagination"><button type="button" id="reviewPrev" class="secondary" ${pageOffset<=0?'disabled':''}>上一页</button><span>本页${rows.length}条 · 共${total}条</span><button type="button" id="reviewNext" class="secondary" ${hasMore?'':'disabled'}>下一页</button></div>`:'';
  if(!request.write(`<section class="panel"><div class="record-line"><div><h2>复核</h2><p>${hint}</p></div><button type="button" class="secondary" id="reviewHistoryToggle">${showHistory?'返回待处理':'查看已处理历史'}</button></div>${mobileQuery.matches?`<div class="work-card-list">${cards}</div>`:table}${pager}</section>`))return;
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
function bindReviewActions(showHistory=false, offset=0){app.querySelectorAll('[data-review]').forEach(b=>b.onclick=async()=>{if(b.dataset.busy==='1')return;const note=b.dataset.action==='reject'?(await promptModal('请输入不通过原因','请填写不通过原因（必填）','不通过原因'))||'':'';if(b.dataset.action==='reject'&&!note)return;b.dataset.busy='1';try{const out=await api('/api/reviews/'+b.dataset.review,json('POST',{action:b.dataset.action,note}));const record=out.record;if(!record){toast('操作成功');return;}if(!showHistory&&record.status!=='pending'){await renderReview(showHistory,offset);}else{replaceReviewRows(record);bindReviewActions(showHistory,offset);}refreshActionBadge();toast('操作成功');}catch(x){toast(x.message,true)}finally{delete b.dataset.busy;}})}
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
  const score=`<strong class="member-record-score ${scoreClass} ${!record.included?'not-included':''}">${esc(record.score_text)}</strong>`;
  const reason=record.status==='rejected'&&record.reason?`<div class="member-reject-reason"><strong>不通过原因：</strong>${esc(record.reason)}</div>`:'';
  const employee=record.employee_name?`<span class="member-record-employee">${esc(record.employee_name)}${record.employee_no?` · ${esc(record.employee_no)}`:''}</span>`:'';
  return `<article class="member-detail-card ${stateClass} ${!record.included?'is-excluded':''}"><div class="member-detail-top"><div><span class="member-record-type type-${record.record_type}">${esc(record.record_type_name)}</span>${employee}<strong>${esc(record.business_date)} · ${esc(record.title)}</strong></div>${score}</div><p>${esc(record.content)}</p>${reason}<div class="member-detail-meta"><span class="member-record-status status-${esc(record.status)}">${esc(record.status_name)}</span><span>登记人：${esc(record.operator_name)}</span><span class="member-included ${includedClass}">${record.included?'已计入综合分':'未计入综合分'}</span>${memberAttachment(record)}</div></article>`;
}

function memberScoreDetailHtml(row,kind){
  const titles={recognition:'加分明细',deduction:'扣分明细',attendance:'全勤分明细',all:'全部记录'};
  const all=row.details.all_records||[];
  const records=kind==='all'?all:kind==='attendance'?all.filter(record=>['attendance','sick_leave'].includes(record.record_type)):all.filter(record=>record.record_type===kind);
  const summary=`<div class="member-detail-summary"><span>加分 <strong class="score-positive">+${fmt(row.recognition_score)}</strong></span><span>扣分 <strong class="score-negative">${fmtDeduction(row.deduction_score)}</strong></span><span>全勤 <strong>${fmt(row.attendance_score)}</strong></span><span>综合 <strong>${fmt(row.total_score)}</strong></span></div>`;
  const attendance=row.details.attendance;
  const calculation=kind==='attendance'&&attendance?`<div class="statistics-attendance-summary"><span>基础分 <strong>${fmt(attendance.base_score)}</strong></span><span>全勤奖励 <strong>${fmt(attendance.perfect_bonus)}</strong></span><span>病假扣减 <strong>${fmtDeduction(attendance.sick_deduction)}</strong></span><span>实际 / 计费病假 <strong>${Number(attendance.actual_sick_days).toFixed(1)} / ${attendance.charged_sick_days}天</strong></span></div>`:'';
  return `<section class="panel member-detail-panel"><div class="statistics-detail-heading"><div><h2>${esc(row.employee_name)} · ${titles[kind]}</h2><span>${esc(row.employee_no)} · ${esc(row.role_name)}</span></div><button type="button" class="secondary" data-close-member-detail>关闭</button></div>${summary}${calculation}<div class="member-detail-list">${records.map(memberRecordCard).join('')||'<div class="empty">本月暂无相关记录</div>'}</div></section>`;
}

function bindMemberScoreDetails(data){
  const host=document.getElementById('memberDetail'),rowMap=new Map(data.rows.map(row=>[String(row.employee_id),row]));let activeButton=null;
  const close=()=>{if(activeButton){activeButton.setAttribute('aria-expanded','false');activeButton.closest('tr').classList.remove('is-selected')}activeButton=null;host.innerHTML='';};
  document.querySelectorAll('[data-member-detail]').forEach(button=>button.onclick=()=>{const wasActive=activeButton===button;close();if(wasActive)return;const row=rowMap.get(button.dataset.employeeId);if(!row)return;activeButton=button;button.setAttribute('aria-expanded','true');button.closest('tr').classList.add('is-selected');host.innerHTML=memberScoreDetailHtml(row,button.dataset.memberDetail);bindFilePreviews(host);host.querySelector('[data-close-member-detail]').onclick=close;host.scrollIntoView({behavior:prefersReducedMotion()?'auto':'smooth',block:'nearest'});});
}

async function renderMembers(){
  app.innerHTML=`<div class="section-gap"><section class="panel"><h2>组员记录</h2><p>每名直属CM/TR一行汇总。点击加分、扣分、全勤分查看分类明细；点击综合分查看当月所有记录。</p><form id="memberFilter" class="entry-filter member-score-filter"><label>月份<input name="month" type="month" value="${monthNow()}" required></label><label>员工搜索<input name="keyword" placeholder="姓名/员工号"></label><button class="primary">查询</button></form></section><section class="panel"><div id="memberResults"></div></section><div id="memberDetail"></div></div>`;
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
    return `<tr class="statistics-score-row statistics-employee-row" data-stats-node="${esc(row.node_id)}" data-stats-parent="${esc(row.parent_id)}" data-level="3"><th><strong>${esc(row.employee_name)}</strong><small>${esc(row.employee_no)} · ${esc(row.role_name||row.role_code)}</small></th><td>${scoreButton(row,'recognitions','+'+fmt(row.recognition_score),'score-positive')}</td><td>${scoreButton(row,'deductions',fmtDeduction(row.deduction_score),'score-negative')}</td><td>${scoreButton(row,'attendance',fmt(row.attendance_score))}</td><td>${scoreButton(row,'all',fmt(row.total_score),'member-total-link')}</td><td></td></tr>`;
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

async function renderStatistics(){
  const canExport=has('DATA_EXPORT');
  app.innerHTML=`<div class="section-gap"><section class="panel"><div class="statistics-page-heading"><h2>${canExport?'景点数据统计与月结导出':'景点数据统计'}</h2><span class="access-mode ${canExport?'can-export':'read-only'}">${canExport?'可导出':'只读'}</span></div><form id="statsFilter" class="statistics-filter"><label>月份<input name="month" type="month" value="${monthNow()}" required></label><label>景点圈<select name="attraction_id"><option value="">全部</option>${opt(state.options.employee_circles||state.options.attractions)}</select></label><label>Title<select name="title"><option value="">全部</option><option value="CM">CM</option><option value="TR">TR</option></select></label><label>员工搜索<input name="keyword" placeholder="姓名/员工号"></label><div class="statistics-actions"><button class="primary">查询统计</button>${canExport?' <button type="button" id="exportBtn" class="secondary">导出 Excel</button>':''}</div></form>${canExport?'<div id="recentExports" class="recent-exports"><span class="field-hint">正在加载最近导出记录…</span></div>':''}</section><div id="statsResults"></div></div>`;
  const form=document.getElementById('statsFilter');
  const preferenceKey=`recognition-v2:statistics-filters:${state.me.employee_no}`,queryButton=form.querySelector('button[type="submit"],button:not([type])'),resultsHost=document.getElementById('statsResults');
  try{const saved=JSON.parse(localStorage.getItem(preferenceKey)||'{}');['month','attraction_id','title','keyword'].forEach(name=>{if(!Object.prototype.hasOwnProperty.call(saved,name))return;const field=form.elements[name],value=String(saved[name]??'');if(name==='month'&&!value)return;if(field&&(!field.options||[...field.options].some(option=>option.value===value)))field.value=value})}catch(_error){}
  let lastStatistics=null,loadSequence=0;
  const saveFilters=()=>{try{localStorage.setItem(preferenceKey,JSON.stringify({month:form.elements.month.value,attraction_id:form.elements.attraction_id.value,title:form.elements.title.value,keyword:form.elements.keyword.value}))}catch(_error){}};
  const emptyMessage=formData=>{const values=Object.fromEntries(formData);if(String(values.keyword||'').trim())return '没有匹配当前姓名或员工号的参与计分人员';if(values.attraction_id)return '该景点圈本月没有参与计分人员';if(values.title)return `本月没有符合 ${values.title} 条件的参与计分人员`;return '本月尚无业务数据或参与计分人员'};
  const load=async()=>{const requestId=++loadSequence,formData=[...new FormData(form)],qs=new URLSearchParams(formData.filter(([,v])=>v)),expandEmployees=Boolean(String(formData.find(([key])=>key==='keyword')?.[1]||'').trim()),originalText=queryButton.textContent;saveFilters();queryButton.disabled=true;queryButton.textContent='查询中…';resultsHost.innerHTML='<section class="panel"><div class="empty">正在加载统计数据…</div></section>';try{const d=await api('/api/statistics?'+qs);if(requestId!==loadSequence)return;lastStatistics=d;const selectedTitle=d.title||'',averageLabel=selectedTitle?`${selectedTitle}小组平均分`:'CM/TR小组平均分',attractionSelect=form.elements.attraction_id,attractionLabel=attractionSelect.selectedOptions[0]?.textContent||'全部',updatedLabel=d.data_updated_at||'暂无业务更新',organizationBasis=d.organization_basis||'当前组织归属';resultsHost.innerHTML=`<section class="panel"><h2>员工综合分</h2><p class="statistics-scope">统计范围：${esc(d.month)} · ${esc(attractionLabel)} · ${esc(selectedTitle||'CM/TR全部')}；数据更新至 ${esc(updatedLabel)}；组织口径：${esc(organizationBasis)}；平均分按筛选后小组综合分 ÷ 筛选后人数计算。</p><div class="table-wrap"><table class="statistics-hierarchy-table"><thead><tr><th>组织 / 员工</th><th>加分</th><th>扣分</th><th>全勤分</th><th>小组综合分</th><th>${esc(averageLabel)}</th></tr></thead><tbody>${statisticsHierarchyRows(d.hierarchy,emptyMessage(formData))}</tbody></table></div></section><div id="statisticsDetail"></div>`;bindStatisticsHierarchy(d,{expandEmployees})}catch(error){if(requestId===loadSequence)resultsHost.innerHTML=`<section class="panel"><div class="error">${esc(error.message)}</div></section>`}finally{if(requestId===loadSequence){queryButton.disabled=false;queryButton.textContent=originalText}}};
  const loadRecentExports=async()=>{if(!canExport)return;const host=document.getElementById('recentExports');try{const data=await api('/api/statistics/my-exports');host.innerHTML=data.items.length?`<h3>本人最近景点数据导出</h3><div class="table-wrap"><table class="recent-export-table"><thead><tr><th>导出时间</th><th>月份</th><th>景点圈</th><th>Title</th><th>员工搜索</th><th>员工数</th></tr></thead><tbody>${data.items.map(row=>`<tr><td>${esc(row.exported_at)}</td><td>${esc(row.month)}</td><td>${esc(row.attraction_name)}</td><td>${esc(row.title)}</td><td>${esc(row.keyword||'无')}</td><td>${row.employee_count}</td></tr>`).join('')}</tbody></table></div>`:'<span class="field-hint">当前账号暂无景点数据导出记录</span>'}catch(error){host.innerHTML=`<span class="field-hint">最近导出记录暂时无法加载：${esc(error.message)}</span>`}};
  form.onsubmit=e=>{e.preventDefault();if(!queryButton.disabled)load()};
  if(canExport){const exportButton=document.getElementById('exportBtn');exportButton.onclick=async()=>{if(exportButton.disabled)return;const formData=[...new FormData(form)],values=Object.fromEntries(formData),attractionLabel=form.elements.attraction_id.selectedOptions[0]?.textContent||'全部',titleLabel=values.title||'CM/TR全部',keywordLabel=String(values.keyword||'').trim(),count=lastStatistics?.summary?.employee_count??0,loaCount=lastStatistics?.loa_rows?.length??0,confirmed=await confirmModal('确认导出景点数据',`<p><strong>${esc(values.month)}</strong> · ${esc(attractionLabel)} · ${esc(titleLabel)}</p>${keywordLabel?`<p>员工搜索：${esc(keywordLabel)}</p>`:''}<p>预计包含 ${count} 名参与计分人员${loaCount?`，另有 ${loaCount} 名整月LOA员工`:''}。</p>`,'导出 Excel');if(!confirmed)return;exportButton.disabled=true;exportButton.textContent='准备导出…';const qs=new URLSearchParams(formData.filter(([,v])=>v));location.href=portalPath('/api/statistics/export?'+qs);setTimeout(()=>{exportButton.disabled=false;exportButton.textContent='导出 Excel';loadRecentExports()},1400)}}
  await Promise.all([load(),loadRecentExports()]);
}

const prCategoryNames={overall:'综合分',recognition:'加分类型',deduction:'扣分类型',absence:'缺勤',leader:'主管加分次数',gsm_leader:'GSM/TA GSM认可次数'};
function prRankBadge(rank){return `<span class="pr-rank-badge ${rank<=3?`top-${rank}`:''}">${rank}</span>`;}
function prEmployeeCell(row,label='员工'){return `<strong>${esc(row.employee_name)}</strong><small>${esc(row.employee_no)} · ${esc(row.role_name||label)}</small>`;}
function prRankingCards(data){
  const field=(label,value,className='')=>`<div><dt>${label}</dt><dd class="${className}">${value}</dd></div>`;
  const cards=data.rows.map(row=>{
    let fields=[];
    if(data.category==='overall')fields=[field('角色',esc(row.role_name)),field('主管',esc(row.leader_name||'未分配')),field('加分',`+${fmt(row.recognition_score)}`,'score-positive'),field('扣分',fmtDeduction(row.deduction_score),'score-negative'),field('全勤分',fmt(row.attendance_score)),field('综合分',fmt(row.total_score),'pr-total-score')];
    else if(data.category==='absence')fields=[field('角色',esc(row.role_name)),field('主管',esc(row.leader_name||'未分配')),field('登记次数',esc(row.count)),field('缺勤天数',Number(row.leave_days||0).toFixed(1)),field('扣减全勤分',fmtDeduction(row.score),'score-negative'),field('最近缺勤',esc(row.recent_date||'—'))];
    else if(['leader','gsm_leader'].includes(data.category))fields=[field('角色',esc(row.role_name)),field('加分次数',esc(row.count)),field('累计加分',`+${fmt(row.score)}`,'score-positive'),field('最近一次加分',esc(row.recent_date||'—'))];
    else {const recognition=data.category==='recognition';fields=[field('角色',esc(row.role_name)),field('主管',esc(row.leader_name||'未分配')),field('登记次数',esc(row.count)),field(recognition?'累计加分':'累计扣分',recognition?`+${fmt(row.score)}`:fmtDeduction(row.score),recognition?'score-positive':'score-negative'),field(recognition?'最近加分':'最近扣分',esc(row.recent_date||'—'))]}
    return `<article class="pr-ranking-card ${row.rank<=3?'pr-top-card':''}" role="listitem"><header><div>${prRankBadge(row.rank)}<span>第 ${row.rank} 名</span></div><div class="pr-card-employee">${prEmployeeCell(row,data.category==='leader'?'主管':'员工')}</div></header><dl>${fields.join('')}</dl></article>`;
  }).join('');
  return `<div class="pr-ranking-cards" role="list">${cards||'<div class="empty">当前条件下暂无排名数据</div>'}</div>`;
}
function prRankingTable(data){
  const commonHead='<th>排名</th><th>员工</th><th>角色</th>';
  let head='',rows='';
  if(data.category==='overall'){
    head=`${commonHead}<th>主管</th><th>加分</th><th>扣分</th><th>全勤分</th><th>综合分</th>`;
    rows=data.rows.map(row=>`<tr class="${row.rank<=3?'pr-top-row':''}"><td>${prRankBadge(row.rank)}</td><td>${prEmployeeCell(row)}</td><td>${esc(row.role_name)}</td><td>${esc(row.leader_name||'未分配')}</td><td class="score-positive">+${fmt(row.recognition_score)}</td><td class="score-negative">${fmtDeduction(row.deduction_score)}</td><td>${fmt(row.attendance_score)}</td><td class="pr-total-score">${fmt(row.total_score)}</td></tr>`).join('');
  }else if(data.category==='absence'){
    head=`${commonHead}<th>主管</th><th>登记次数</th><th>缺勤天数</th><th>扣减全勤分</th><th>最近一次缺勤</th>`;
    rows=data.rows.map(row=>`<tr class="${row.rank<=3?'pr-top-row':''}"><td>${prRankBadge(row.rank)}</td><td>${prEmployeeCell(row)}</td><td>${esc(row.role_name)}</td><td>${esc(row.leader_name||'未分配')}</td><td>${row.count}</td><td>${Number(row.leave_days||0).toFixed(1)}</td><td class="score-negative">${fmtDeduction(row.score)}</td><td>${esc(row.recent_date||'—')}</td></tr>`).join('');
  }else if(['leader','gsm_leader'].includes(data.category)){
    const personLabel=data.category==='gsm_leader'?'GSM / TA GSM':'主管';
    head=`<th>排名</th><th>${personLabel}</th><th>角色</th><th>加分次数</th><th>累计加分</th><th>最近一次加分</th>`;
    rows=data.rows.map(row=>`<tr class="${row.rank<=3?'pr-top-row':''}"><td>${prRankBadge(row.rank)}</td><td>${prEmployeeCell(row,personLabel)}</td><td>${esc(row.role_name)}</td><td>${row.count}</td><td class="score-positive">+${fmt(row.score)}</td><td>${esc(row.recent_date||'—')}</td></tr>`).join('');
  }else{
    const scoreLabel=data.category==='recognition'?'累计加分':'累计扣分',dateLabel=data.category==='recognition'?'最近一次加分':'最近一次扣分';
    head=`${commonHead}<th>主管</th><th>登记次数</th><th>${scoreLabel}</th><th>${dateLabel}</th>`;
    rows=data.rows.map(row=>`<tr class="${row.rank<=3?'pr-top-row':''}"><td>${prRankBadge(row.rank)}</td><td>${prEmployeeCell(row)}</td><td>${esc(row.role_name)}</td><td>${esc(row.leader_name||'未分配')}</td><td>${row.count}</td><td class="${data.category==='recognition'?'score-positive':'score-negative'}">${data.category==='recognition'?'+':''}${data.category==='recognition'?fmt(row.score):fmtDeduction(row.score)}</td><td>${esc(row.recent_date||'—')}</td></tr>`).join('');
  }
  const pageButtons=[];for(let page=1;page<=data.pages;page++){if(page===1||page===data.pages||Math.abs(page-data.page)<=1)pageButtons.push(`<button type="button" class="${page===data.page?'active':''}" data-pr-page="${page}">${page}</button>`);else if(pageButtons[pageButtons.length-1]!=='<span>…</span>')pageButtons.push('<span>…</span>')}
  return `<div class="pr-result-meta"><span>共 <strong>${data.total}</strong> 人 · ${esc(data.attraction_name)} · ${esc(data.start_date)} 至 ${esc(data.end_date)}</span>${data.subtype_name?`<span>当前类型：<strong>${esc(data.subtype_name)}</strong></span>`:''}</div>${prRankingCards(data)}<div class="table-wrap pr-ranking-wrap"><table class="pr-ranking-table"><thead><tr>${head}</tr></thead><tbody>${rows||`<tr><td colspan="9" class="empty">当前条件下暂无排名数据</td></tr>`}</tbody></table></div><div class="pr-pagination"><button type="button" data-pr-page="${Math.max(1,data.page-1)}" ${data.page<=1?'disabled':''}>上一页</button>${pageButtons.join('')}<button type="button" data-pr-page="${Math.min(data.pages,data.page+1)}" ${data.page>=data.pages?'disabled':''}>下一页</button><span>${data.page_size}条/页</span></div>`;
}

async function renderPrRankings(){
  let category='overall',sortBy='score',page=1,requestSequence=0;
  const circles=state.options.employee_circles||state.options.attractions||[],selectedCircle=String(state.me.attraction_id||''),canExport=state.me.role_code==='GSM';
  const circleOptions=`<option value="">全部景点圈</option>${circles.map(row=>`<option value="${esc(row.id)}" ${String(row.id)===selectedCircle?'selected':''}>${esc(row.name)}</option>`).join('')}`;
  app.innerHTML=`<div class="section-gap pr-ranking-page"><section class="panel"><h2>PR排名数据</h2><p>TAGSM与GSM均可查询三个景点圈或全部景点圈；各榜单按需加载，不会一次读取全部排名。</p><form id="prFilter" class="pr-ranking-filter"><label>景点圈<select name="attraction_id">${circleOptions}</select></label><label>开始日期<input name="start_date" type="date" value="${monthStart()}" max="${today()}" required></label><label>结束日期<input name="end_date" type="date" value="${today()}" max="${today()}" required></label><label>员工搜索<input name="keyword" placeholder="搜索姓名或工号"></label><div class="pr-ranking-actions"><button class="primary">查询</button>${canExport?'<button type="button" id="prExport" class="secondary">导出</button>':'<span class="field-hint">TAGSM仅支持查询</span>'}</div></form></section><section class="panel pr-ranking-data"><div id="prCategoryTabs" class="pr-category-tabs">${Object.entries(prCategoryNames).map(([key,name])=>`<button type="button" data-pr-category="${key}" class="${key===category?'active':''}">${name}</button>`).join('')}</div><div id="prSubfilters" class="pr-subfilters"></div><div id="prResults"><div class="empty">正在加载排名…</div></div></section></div>`;
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
  if(canExport)document.getElementById('prExport').onclick=()=>{location.href=portalPath('/api/pr-rankings/export?'+params())};
  renderSubfilters();updateExportState();await load();
}

function hrLeaderSelect(employee,leaders,attractionId=employee.attraction_id){
  const rows=leaders.filter(row=>String(row.attraction_id||'')===String(attractionId||''));
  const groups=new Map();
  rows.forEach(row=>{const key=`${row.attraction_name} → ${row.gsm_name}${row.gsm_role_name?` · ${row.gsm_role_name}`:''}`;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row)});
  const options=[`<option value="">未分配</option>`];
  groups.forEach((items,label)=>options.push(`<optgroup label="${esc(label)}">${items.map(row=>{const value=`${row.group_id||''}|${row.id}`,selected=employee.group_id===row.group_id&&employee.leader_id===row.id;return `<option value="${value}" ${selected?'selected':''}>${esc(row.name)} · ${esc(row.role_name)} · ${esc(row.group_name)}</option>`}).join('')}</optgroup>`));
  return `<select name="leader" class="inline-select hr-leader-select" data-original-group="${employee.group_id||''}" data-original-leader="${employee.leader_id||''}">${options.join('')}</select>`;
}

function hrOrganizationRows(rows,leaders){
  const childParents=new Set(rows.map(row=>row.parent_id).filter(Boolean));
  return rows.map(row=>{
    if(row.node_type!=='employee'){
      const meta=row.node_type==='attraction'?'景点圈':row.node_type==='placeholder'?`${row.member_count||0}人`:'';
      const expanded=row.level<2;
      return `<tr class="hr-org-heading" data-hr-node="${esc(row.node_id)}" data-hr-parent="${esc(row.parent_id)}" data-level="${row.level}"><th colspan="7"><button type="button" class="hr-tree-toggle" data-hr-toggle="${esc(row.node_id)}" aria-expanded="${expanded}"><span class="hr-tree-arrow">${expanded?'⌄':'›'}</span><strong>${esc(row.name)}</strong>${meta?`<small>${esc(meta)}</small>`:''}</button></th></tr>`;
    }
    const employee=row.employee,hasChildren=childParents.has(row.node_id),frontline=['CM','TR'].includes(employee.role_code),editable=state.options.roles.some(role=>role.code===employee.role_code),statusName=({active:'在职',loa:'LOA（长期病假）',terminated:'离职'})[employee.employment_status]||employee.employment_status;
    const expanded=row.hierarchy_role!=='supervisor';
    const treeControl=hasChildren?`<button type="button" class="hr-tree-toggle hr-employee-toggle" data-hr-toggle="${esc(row.node_id)}" aria-expanded="${expanded}"><span class="hr-tree-arrow">${expanded?'⌄':'›'}</span></button>`:'<span class="hr-tree-spacer" aria-hidden="true"></span>';
    const accountCell=employee.account_deleted_at?`<span class="badge danger" title="${esc(employee.account_deletion_reason||'员工与业务档案已保留')}">账号已删除·留档</span>`:`<select name="enabled"><option value="true" ${employee.account_enabled?'selected':''}>启用</option><option value="false" ${!employee.account_enabled?'selected':''}>停用</option></select>`;
    const archiveAction=employee.account_deletion_eligible?`<button type="button" data-delete-login-account="${employee.id}" class="danger" title="删除登录凭据与会话，保留员工及所有历史档案">删除登录账号</button>`:employee.account_deleted_at?`<small class="field-hint">档案保留</small>`:'';
    const editableCells=`<td><select name="role" class="inline-select">${state.options.roles.map(role=>`<option value="${role.code}" ${role.code===employee.role_code?'selected':''}>${esc(role.name)}</option>`).join('')}</select></td><td><select name="attraction" class="inline-select"><option value="">无</option>${state.options.attractions.map(attraction=>`<option value="${attraction.id}" ${attraction.id===employee.attraction_id?'selected':''}>${esc(attraction.name)}</option>`).join('')}</select></td><td class="hr-leader-cell">${frontline?hrLeaderSelect(employee,leaders):'<span class="not-applicable">不适用</span>'}</td><td><select name="employment_status"><option value="active" ${employee.employment_status==='active'?'selected':''}>在职</option>${frontline?`<option value="loa" ${employee.employment_status==='loa'?'selected':''}>LOA（长期病假）</option>`:''}<option value="terminated" ${employee.employment_status==='terminated'?'selected':''}>离职</option></select>${frontline?`<input name="loa_start_date" type="date" value="${esc(employee.loa_start_date||today())}" title="LOA开始日期">`:''}</td><td>${accountCell}</td><td><div class="actions compact-actions"><button data-save-employee="${employee.id}" class="secondary">保存</button>${archiveAction}</div></td>`;
    const readOnlyCells=`<td><span class="badge">${esc(employee.role_name||employee.role_code)}</span></td><td>${esc(employee.attraction_name||'未分配')}</td><td><span class="not-applicable">不适用</span></td><td>${esc(statusName)}</td><td>${employee.account_deleted_at?'<span class="badge danger">账号已删除·留档</span>':employee.account_enabled?'启用':'停用'}</td><td><span class="not-applicable">仅最高管理员可编辑</span></td>`;
    const batchUnassigned=editable&&frontline&&String(row.parent_id||'').startsWith('hr-unassigned-');
    return `<tr class="hr-org-employee" data-hr-node="${esc(row.node_id)}" data-hr-parent="${esc(row.parent_id)}" data-level="${row.level}" data-hierarchy-role="${esc(row.hierarchy_role||'other')}" data-employee-editable="${editable}" data-batch-unassigned="${batchUnassigned}" data-employee-name="${esc(employee.name)}" data-employee-no="${esc(employee.employee_no)}"><th>${treeControl}<span class="hr-employee-identity"><strong>${esc(employee.name)}</strong><small>${esc(employee.employee_no)}</small></span></th>${editable?editableCells:readOnlyCells}</tr>`;
  }).join('');
}

function bindHrOrganization(organization,leaders){
  const rows=[...document.querySelectorAll('[data-hr-node]')],rowMap=new Map(rows.map(row=>[row.dataset.hrNode,row])),collapsed=new Set(rows.filter(row=>row.dataset.level==='2'&&row.querySelector('[data-hr-toggle]')).map(row=>row.dataset.hrNode)),search=document.getElementById('hrSearch');
  const refresh=()=>{const query=search.value.trim().toLowerCase();if(query){const visible=new Set();rows.filter(row=>row.classList.contains('hr-org-employee')&&row.textContent.toLowerCase().includes(query)).forEach(row=>{visible.add(row.dataset.hrNode);let parent=row.dataset.hrParent;while(parent){visible.add(parent);parent=rowMap.get(parent)?.dataset.hrParent||''}});rows.forEach(row=>row.hidden=!visible.has(row.dataset.hrNode));return}rows.forEach(row=>{let parent=row.dataset.hrParent,hidden=false;while(parent){if(collapsed.has(parent)){hidden=true;break}parent=rowMap.get(parent)?.dataset.hrParent||''}row.hidden=hidden})};
  document.querySelectorAll('[data-hr-toggle]').forEach(button=>button.onclick=()=>{const id=button.dataset.hrToggle,closing=!collapsed.has(id);if(closing)collapsed.add(id);else collapsed.delete(id);button.setAttribute('aria-expanded',String(!closing));button.querySelector('.hr-tree-arrow').textContent=closing?'›':'⌄';refresh()});
  search.oninput=refresh;
  document.querySelectorAll('.hr-org-employee[data-employee-editable="true"]').forEach(tr=>{const role=tr.querySelector('[name=role]'),attraction=tr.querySelector('[name=attraction]'),leaderCell=tr.querySelector('.hr-leader-cell');const employeeRow=organization.rows.find(row=>row.node_type==='employee'&&String(row.employee.id)===tr.querySelector('[data-save-employee]').dataset.saveEmployee),employee=employeeRow.employee;const syncLeader=()=>{if(['CM','TR'].includes(role.value)){leaderCell.innerHTML=hrLeaderSelect(employee,leaders,attraction.value)}else leaderCell.innerHTML='<span class="not-applicable">不适用</span>'};role.onchange=syncLeader;attraction.onchange=syncLeader});
  const batchBar=document.getElementById('hrBatchLeaderBar'),batchCount=document.getElementById('hrBatchLeaderCount'),batchRows=[...document.querySelectorAll('.hr-org-employee[data-batch-unassigned="true"]')];
  const batchChanges=()=>batchRows.map(tr=>{const select=tr.querySelector('[name=leader]');if(!select||!select.value||select.value===`${select.dataset.originalGroup||''}|${select.dataset.originalLeader||''}`)return null;const [groupId,leaderId]=select.value.split('|');return {tr,select,employee_id:Number(tr.querySelector('[data-save-employee]').dataset.saveEmployee),employee_name:tr.dataset.employeeName,group_id:groupId?Number(groupId):null,leader_id:Number(leaderId),target:select.selectedOptions[0]?.textContent||''}}).filter(Boolean);
  const refreshBatch=()=>{const changes=batchChanges();batchBar.hidden=!changes.length;batchCount.textContent=`已选择 ${changes.length} 名未分类组员`;batchRows.forEach(tr=>tr.classList.toggle('hr-batch-dirty',changes.some(change=>change.tr===tr)));};
  batchRows.forEach(tr=>tr.querySelector('[name=leader]')?.addEventListener('change',refreshBatch));
  document.getElementById('hrBatchLeaderCancel').onclick=()=>{batchRows.forEach(tr=>{const select=tr.querySelector('[name=leader]');if(select)select.value=`${select.dataset.originalGroup||''}|${select.dataset.originalLeader||''}`});refreshBatch();};
  document.getElementById('hrBatchLeaderSave').onclick=async()=>{const changes=batchChanges();if(!changes.length)return;const list=changes.map(change=>`<li>${esc(change.employee_name)} → ${esc(change.target)}</li>`).join('');if(!await confirmModal('保存全部组长调整',`<p>将一次保存以下 ${changes.length} 名未分类组员：</p><ul>${list}</ul><p>任意一人校验失败时，本批次不会保存任何修改。</p>`,'确认批量保存'))return;try{const result=await api('/api/hr/employees/batch-leaders',json('POST',{items:changes.map(({employee_id,group_id,leader_id})=>({employee_id,group_id,leader_id})),reason:'HR未分类组员批量分组'}));toast(`已批量保存 ${result.updated} 名组员`);renderHrEmployees()}catch(error){toast(error.message,true)}};
  document.querySelectorAll('[data-save-employee]').forEach(button=>button.onclick=async()=>{const tr=button.closest('tr'),roleCode=tr.querySelector('[name=role]').value,leaderSelect=tr.querySelector('[name=leader]'),enabledControl=tr.querySelector('[name=enabled]');const body={role_code:roleCode,attraction_id:tr.querySelector('[name=attraction]').value||null,employment_status:tr.querySelector('[name=employment_status]').value,loa_start_date:tr.querySelector('[name=loa_start_date]')?.value||'',account_enabled:enabledControl?enabledControl.value==='true':false,reason:'HR页面更新'};if(['CM','TR'].includes(roleCode)){const [groupId,leaderId]=(leaderSelect?.value||'|').split('|');body.group_id=groupId||null;body.leader_id=leaderId||null;if(leaderSelect&&(String(leaderSelect.dataset.originalGroup||'')!==String(groupId||'')||String(leaderSelect.dataset.originalLeader||'')!==String(leaderId||''))){const target=leaderSelect.selectedOptions[0]?.textContent||'未分配';if(!confirm(`确认将 ${tr.dataset.employeeName} 的组长调整为“${target}”？`))return}}else{body.group_id=null;body.leader_id=null}try{await api('/api/hr/employees/'+button.dataset.saveEmployee,json('PUT',body));toast('已保存');renderHrEmployees()}catch(error){toast(error.message,true)}});
  document.querySelectorAll('[data-delete-login-account]').forEach(button=>button.onclick=async()=>{const tr=button.closest('tr'),name=tr.dataset.employeeName,employeeNo=tr.dataset.employeeNo;if(!await confirmModal('删除登录账号',`<p>确认删除 <strong>${esc(name)}（${esc(employeeNo)}）</strong> 的登录账号吗？</p><p>登录凭据和当前会话将被移除，不能再登录；员工、当月数据、历史记录、附件、调动和审计档案均会保留并标记“账号已删除·留档”。</p>`,'确认删除登录账号'))return;try{await api('/api/hr/employees/'+button.dataset.deleteLoginAccount+'/account',json('DELETE',{reason:'离职/停用满7天后删除登录账号，保留员工与业务档案'}));toast('登录账号已删除，业务档案已保留');renderHrEmployees()}catch(error){toast(error.message,true)}});
  refresh();
}

function employeeNumberChangePanel(){
  if(!['HR_CIRCLE','SYSTEM_ADMIN'].includes(state.me.role_code))return '';
  const scope=state.me.role_code==='HR_CIRCLE'?'当前权限：仅可变更所属景点圈内 CM、TR、TA主管、主管的员工号。':'当前权限：可变更常规员工账号（CM/TR/TA主管/主管/TA GSM/GSM/AM/OM）的员工号。HR和最高管理员账号不在此入口处理。';
  return `<section class="panel"><h2>员工号变更</h2><p>用于实习转正等员工号变更场景。系统保留同一员工档案、角色、景点圈、小组、加扣分、缺勤和审计历史，不会新建第二个账号。</p><div class="notice"><strong>可操作范围</strong><br>${esc(scope)}<br>变更后原账号立即失效，当前登录会话将退出；历史员工号可继续用于此处搜索和导出复查。</div><form id="employeeNumberChangeForm" class="form-stack"><label>搜索员工<input name="keyword" autocomplete="off" placeholder="输入当前/历史员工号或姓名"></label><div id="employeeNumberChangeResults" class="employee-search-results" hidden></div><input type="hidden" name="employee_id"><div id="employeeNumberChangeSelected" class="employee-selected" hidden></div><label>新员工号<input name="new_employee_no" inputmode="numeric" pattern="[0-9]{7}" minlength="7" maxlength="7" required disabled placeholder="请输入7位新员工号"><span class="field-hint">仅限7位数字；系统会校验员工与登录账号均未被占用。</span></label><label>变更原因<input name="reason" maxlength="300" required disabled placeholder="例如：实习转正，更换正式员工号"></label><label class="check-line"><input name="reset_password" type="checkbox" disabled> 同时重置密码为新员工号后四位（首次登录必须修改）</label><button type="submit" class="warn" disabled>确认变更员工号</button></form></section>`;
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
  const [organization,leaders]=await Promise.all([api('/api/hr/organization'),api('/api/hr/leader-options')]);
  app.innerHTML=`<div class="section-gap"><section class="panel"><h2>Excel 导入</h2><p>请先下载 V2 模板，填写后整表导入。员工号必须为7位纯数字，员工仅可归属热力追踪、矮人迷宫或小熊罐子景点圈。初始密码留空时，系统使用员工号后四位并要求首次登录修改。</p><form id="importEmployees" enctype="multipart/form-data"><div class="actions native-file-actions"><label class="secondary native-file-trigger" for="importEmployeesWorkbook">选择文件</label><a class="secondary download-link" href="${portalPath('/api/hr/import-template')}">下载导入模板</a><button type="submit" class="primary">导入员工</button></div><input id="importEmployeesWorkbook" class="native-file-input" name="workbook" type="file" accept=".xlsx" required><span class="field-hint" data-import-filename>请选择 Excel 文件（.xlsx）</span></form></section><section class="panel hr-create-panel"><div class="hr-create-heading"><h2>新建员工账号</h2><p>填写员工基础信息，创建后可继续在下方员工管理中调整组织归属。</p></div><form id="newEmployee" class="hr-filter"><label class="hr-create-employee-no">员工号<input name="employee_no" inputmode="numeric" pattern="[0-9]{7}" minlength="7" maxlength="7" required placeholder="请输入7位员工号"><span class="field-hint">仅限7位数字，不能重复创建。</span></label><label class="hr-create-name">姓名<input name="name" required placeholder="请输入员工姓名"></label><label class="hr-create-role">角色<select name="role_code">${opt(state.options.roles,'code',x=>x.name)}</select></label><label class="hr-create-attraction">景点圈<select name="attraction_id"><option value="">无</option>${opt(state.options.employee_circles||state.options.attractions)}</select></label><label class="hr-create-password">初始密码<input name="password" placeholder="留空使用员工号后4位"><span class="field-hint">首次登录后必须立即修改密码。</span></label><div class="actions hr-create-actions"><button type="submit" class="primary">创建账号</button></div></form></section>${employeeNumberChangePanel()}<section class="panel"><h2>员工管理</h2><p>人员状态支持在职、LOA（长期病假）和离职；LOA开始日期用于当月病假计费。未分配主管的CM/TR可以分别选择组长后统一批量保存。</p><input id="hrSearch" placeholder="搜索姓名或员工号"><div class="table-wrap sticky-col"><table class="hr-organization-table"><thead><tr><th>员工</th><th>角色</th><th>景点圈</th><th>组长</th><th>人员状态</th><th>账号启用</th><th>保存</th></tr></thead><tbody id="hrOrganizationRows">${hrOrganizationRows(organization.rows,leaders)}</tbody></table></div><div id="hrBatchLeaderBar" class="hr-batch-leader-bar" hidden><strong id="hrBatchLeaderCount">已选择 0 名未分类组员</strong><span>仅批量保存组长归属</span><div class="actions"><button type="button" id="hrBatchLeaderCancel" class="secondary">取消修改</button><button type="button" id="hrBatchLeaderSave" class="primary">保存全部组长调整</button></div></div></section>${accountStatusPanel()}</div>`;
  const importForm=document.getElementById('importEmployees'),workbookInput=importForm.querySelector('[name=workbook]'),fileHint=importForm.querySelector('[data-import-filename]');
  workbookInput.onchange=()=>{fileHint.textContent=workbookInput.files[0]?workbookInput.files[0].name:'请选择 Excel 文件（.xlsx）';};
  importForm.onsubmit=async event=>{event.preventDefault();try{const out=await api('/api/hr/import-employees',{method:'POST',body:new FormData(event.target)});toast(`成功导入 ${out.created} 名员工`);renderHrEmployees()}catch(error){toast(error.message,true)}};
  document.getElementById('newEmployee').onsubmit=async event=>{event.preventDefault();try{await api('/api/hr/employees',json('POST',Object.fromEntries(new FormData(event.target))));toast('员工已创建');renderHrEmployees()}catch(error){toast(error.message,true)}};
  bindHrOrganization(organization,leaders);
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
  app.innerHTML=`<div class="section-gap"><section class="panel"><h2>密码管理</h2><p>修改自己的密码，或按当前权限重置其他账号密码。</p><form id="passwordPageForm" class="form-stack password-form">${passwordFieldsMarkup()}<button type="submit" class="primary">保存我的新密码</button></form></section><section class="panel"><h2>密码重置</h2><p>输入员工号和姓名，确认后密码将重置为一次性临时密码，员工首次登录后必须修改。</p><div class="notice"><strong>可重置范围</strong><br>${esc(passwordResetScopeHint())}</div><div id="resetPasswordResult"></div><form id="resetEmployeePassword" class="hr-filter"><label>员工号<input name="employee_no" required placeholder="请输入员工号"></label><label>姓名<input name="name" required placeholder="请输入员工姓名"></label><div class="actions form-sticky-actions"><button type="submit" class="warn">重置密码</button></div></form></section>${canCorrectName?`<section class="panel"><h2>账号姓名修改</h2><p>用于更正账号名下的中文姓名。员工号、登录账号、角色、景点圈和历史记录均不会改变。</p><div class="notice"><strong>可修改范围</strong><br>${esc(nameScope)}</div><form id="accountNameForm" class="form-stack"><label>搜索账号<input name="keyword" autocomplete="off" placeholder="输入员工号或当前中文姓名"></label><div id="accountNameResults" class="employee-search-results" hidden></div><input type="hidden" name="employee_id"><div id="accountNameSelected" class="employee-selected" hidden></div><label>新中文姓名<input name="name" maxlength="100" required placeholder="请输入正确的中文姓名" disabled></label><button type="submit" class="primary" disabled>确认修改姓名</button></form></section>`:''}</div>`;
  bindPasswordForm(document.getElementById('passwordPageForm'),form=>{toast('密码已更新');form.reset();form.querySelector('[name=current_password]')?.focus();form.querySelector('[name=new_password]').dispatchEvent(new Event('input'));});
  document.getElementById('resetEmployeePassword').onsubmit=async event=>{event.preventDefault();const body=Object.fromEntries(new FormData(event.target));if(!confirm(`确认重置 ${body.name}（${body.employee_no}）的密码吗？原登录会话将立即失效，并生成一次性临时密码。`))return;try{const out=await api('/api/accounts/reset-password',json('POST',body));document.getElementById('resetPasswordResult').innerHTML=`<div class="notice"><strong>${esc(out.employee_name)}</strong> 的临时密码：<code>${esc(out.temporary_password)}</code><br>临时密码仅显示本次，请立即转交账号本人；首次登录后必须修改。${out.account_enabled?'':' 该账号当前处于停用状态。'}</div>`;toast('密码已重置，请妥善转交一次性临时密码');event.target.reset()}catch(error){toast(error.message,true)}};
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
  app.innerHTML=`<div class="section-gap"><section class="panel"><h2>景点圈HR账号</h2><p>最高管理员可查看账号状态并重置临时密码。系统不会显示已设置的原密码，重置后临时密码仅显示一次。</p><div id="circleHrResetResult"></div><div class="table-wrap sticky-col"><table><thead><tr><th>账号</th><th>景点圈</th><th>账号状态</th><th>密码状态</th><th>最近登录</th><th>操作</th></tr></thead><tbody>${data.items.map(row=>`<tr><td>${esc(row.login_account)}<small>${esc(row.name)}</small></td><td>${esc(row.attraction_name)}</td><td>${row.account_enabled?'启用':'停用'}</td><td>${esc(row.password_status)}</td><td>${esc(row.last_login_at||'暂无')}</td><td>${row.employee_id?`<button type="button" class="warn" data-circle-hr-reset="${row.employee_id}" data-circle-hr-name="${esc(row.name)}">重置密码</button>`:'账号未生成'}</td></tr>`).join('')}</tbody></table></div></section></div>`;
  app.querySelectorAll('[data-circle-hr-reset]').forEach(button=>button.onclick=async()=>{const confirmed=await confirmModal('重置景点圈HR密码',`<p>确认重置 ${esc(button.dataset.circleHrName)} 的密码吗？</p><p>原登录会话将立即失效，并生成一次性临时密码。</p>`,'确认重置');if(!confirmed)return;try{const result=await api('/api/admin/circle-hr-accounts/'+button.dataset.circleHrReset+'/reset-password',{method:'POST'});await renderCircleHrAccounts();document.getElementById('circleHrResetResult').innerHTML=`<div class="notice"><strong>${esc(result.employee_name)}</strong> 临时密码：<code>${esc(result.temporary_password)}</code><br>${esc(result.message)}</div>`}catch(error){toast(error.message,true)}});
}

async function renderCircleTransfers(){
  const [data,leaders]=await Promise.all([api('/api/hr/circle-transfers'),api('/api/hr/leader-options')]);
  const canActFor=(attractionId)=>state.me.role_code!=='HR_CIRCLE'||Number(state.me.attraction_id)===Number(attractionId);
  const transferRows=data.items.map(row=>{const incoming=row.status==='pending'&&canActFor(row.target_attraction_id),outgoing=row.status==='pending'&&canActFor(row.source_attraction_id),leaderRows=leaders.filter(item=>Number(item.attraction_id)===Number(row.target_attraction_id));return `<article class="group-card"><div class="record-line"><strong>${esc(row.employee_name)} · ${esc(row.employee_no)}</strong><span class="badge ${row.status==='completed'?'ok':row.status==='pending'?'warn':'danger'}">${esc(row.status_name)}</span></div><p>${esc(row.source_attraction_name)} → ${esc(row.target_attraction_name)} · 原LEAD：${esc(row.source_leader_name)}</p><p>原因：${esc(row.reason)} · 发起人：${esc(row.requested_by_name)} · ${esc(row.requested_at)}</p>${row.status==='completed'?`<p>已分配：${esc(row.target_leader_name)} · ${esc(row.target_group_name)}；迁移当月认可 ${row.migrated_record_counts.recognitions||0} 条、扣分 ${row.migrated_record_counts.deductions||0} 条、病假 ${row.migrated_record_counts.sick_leaves||0} 条。</p>`:''}${incoming?`<form data-circle-transfer-review="${row.id}" class="grid two"><label>接收LEAD / 小组<select name="target"><option value="">请选择</option>${leaderRows.map(item=>`<option value="${item.group_id||''}|${item.id}">${esc(item.name)} · ${esc(item.group_name)}</option>`).join('')}</select></label><label>审批说明<input name="review_note" placeholder="接受可选，拒绝必填"></label><div class="actions"><button type="button" class="secondary" data-transfer-reject>拒绝</button><button type="submit" class="primary">接受并立即生效</button></div></form>`:''}${outgoing?`<div class="actions"><button type="button" class="secondary" data-transfer-cancel="${row.id}">撤回申请</button></div>`:''}${row.review_note?`<p>审批说明：${esc(row.review_note)}</p>`:''}</article>`}).join('');
  app.innerHTML=`<div class="section-gap"><section class="panel"><h2>发起跨景点圈调动</h2><p>原景点圈HR发起，目标景点圈HR确认并选择新LEAD；确认后员工、当月数据和待办事项立即同步迁移。</p><form id="circleTransferCreate" class="circle-transfer-form">${circleTransferEmployeePicker()}<div class="circle-transfer-details"><label>目标景点圈<select name="target_attraction_id" required><option value="">请选择</option>${opt(data.target_circles)}</select></label><label>调动原因<input name="reason" required maxlength="200"></label><div class="circle-transfer-submit"><button class="primary">提交目标HR确认</button></div></div></form></section><section class="panel"><h2>调动记录</h2>${transferRows||'<div class="empty">暂无跨景点圈调动记录</div>'}</section></div>`;
  bindEmployeeSearches(app);document.getElementById('circleTransferCreate').onsubmit=async event=>{event.preventDefault();if(!requireEmployeeSelection(event.target))return;try{await api('/api/hr/circle-transfers',json('POST',Object.fromEntries(new FormData(event.target))));toast('调动申请已提交目标HR');renderCircleTransfers()}catch(error){toast(error.message,true)}};
  app.querySelectorAll('[data-circle-transfer-review]').forEach(form=>{form.onsubmit=async event=>{event.preventDefault();const [groupId,leaderId]=(form.elements.target.value||'|').split('|');if(!leaderId){toast('请选择接收LEAD和小组',true);return}const confirmed=await confirmModal('确认立即转圈',`<p>确认后员工归属、组关系、当月数据和待办将立即迁移。</p>`,'接受并生效');if(!confirmed)return;try{await api('/api/hr/circle-transfers/'+form.dataset.circleTransferReview+'/review',json('POST',{action:'accept',target_group_id:groupId||null,target_leader_id:leaderId,review_note:form.elements.review_note.value}));toast('跨圈调动已立即生效');renderCircleTransfers()}catch(error){toast(error.message,true)}};form.querySelector('[data-transfer-reject]').onclick=async()=>{const note=form.elements.review_note.value.trim();if(!note){toast('拒绝原因必填',true);form.elements.review_note.focus();return}try{await api('/api/hr/circle-transfers/'+form.dataset.circleTransferReview+'/review',json('POST',{action:'reject',review_note:note}));toast('已拒绝调动申请');renderCircleTransfers()}catch(error){toast(error.message,true)}}});
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

async function renderHrGroups(){const request=beginViewRequest();const [groups,leaders,alerts]=await Promise.all([api('/api/hr/groups'),api('/api/hr/leader-options'),api('/api/hr/alerts')]);if(!request.isCurrent())return;if(!request.write(`<div class="section-gap"><section class="panel"><h2>整组移交</h2><p>组和成员不变，待复核数据一并转给新组长。新组长必须为同景点圈在职 TA 主管/主管。</p>${groups.map(g=>`<article class="group-card"><h3>${esc(g.name)}</h3><p>${esc(g.attraction_name)} · 当前组长：${esc(g.leader_name)} · ${g.member_count}人</p>${g.previous_leader_name?`<p class="field-hint">原组长：${esc(g.previous_leader_name)}（展示至 ${esc(g.previous_leader_until)}）</p>`:''}<p>成员：${g.members.map(m=>esc(m.name)).join('、')||'无'}</p><form data-transfer="${g.id}" data-revision="${g.revision}" class="grid two"><label>新组长<select name="new_leader_id">${opt(leaders.filter(x=>x.attraction_id===g.attraction_id),'id',x=>`${x.name} · ${x.role_name}`)}</select></label><label>移交原因<input name="reason" required></label><div class="actions form-sticky-actions"><button type="submit" class="primary">确认整组移交</button></div></form></article>`).join('')}</section><section class="panel"><h2>组织提醒</h2>${alerts.map(a=>`<div class="notice">${esc(a.message)} ${a.due_date?`· ${a.due_date}`:''}</div>`).join('')||'<div class="empty">无提醒</div>'}</section></div>`))return;app.querySelectorAll('[data-transfer]').forEach(f=>f.onsubmit=async e=>{e.preventDefault();if(!await confirmModal('确认整组移交','<p>组和成员不变，待复核数据一并转给新组长。</p>','确认移交'))return;const body=Object.fromEntries(new FormData(f));body.revision=Number(f.dataset.revision);body.effective_date=today();try{await api('/api/hr/groups/'+f.dataset.transfer+'/transfer',json('POST',body));toast('整组移交已生效');renderHrGroups()}catch(x){toast(x.message,true)}})}

async function renderHrScores(){const request=beginViewRequest();const rows=await api('/api/hr/score-rules');if(!request.isCurrent())return;const canEdit=state.me.role_code==='SYSTEM_ADMIN',editable=canEdit?rows:[],readOnly=canEdit?[]:rows,scopeHint=canEdit?'<div class="notice">这是全系统统一分值规则，修改后会影响所有景点圈后续新登记的认可；历史签卡不追溯改分。</div>':'<div class="notice">当前显示全系统统一分值规则，仅最高管理员可调整；本页面为只读。</div>';if(!request.write(`<section class="panel"><h2>认可人角色默认分值</h2><p>分值按角色和生效日期保留历史，已登记签卡不追溯改分。</p>${scopeHint}<div class="table-wrap sticky-col"><table><thead><tr><th>角色</th><th>当前分值</th><th>新分值</th><th>生效日期</th><th>操作</th></tr></thead><tbody>${editable.map(r=>`<tr><td>${esc(r.role_name)}</td><td>${fmt(r.score)}</td><td><input name="score" type="number" min="0" step="0.01" value="${r.score}"></td><td><input name="date" type="date" value="${today()}"></td><td><button class="primary" data-score-role="${r.role_code}">生效</button></td></tr>`).join('')}${readOnly.map(r=>`<tr><td>${esc(r.role_name)}</td><td>${fmt(r.score)}</td><td colspan="3"><span class="not-applicable">全局规则只读</span></td></tr>`).join('')}</tbody></table></div></section>`))return;app.querySelectorAll('[data-score-role]').forEach(b=>b.onclick=async()=>{const tr=b.closest('tr');try{await api('/api/hr/score-rules',json('POST',{role_code:b.dataset.scoreRole,score:tr.querySelector('[name=score]').value,effective_date:tr.querySelector('[name=date]').value}));toast('新分值规则已生效');render()}catch(x){toast(x.message,true)}})}
async function renderLogs(){const request=beginViewRequest();const rows=await api('/api/admin/logs');if(!request.isCurrent())return;if(!request.write(`<section class="panel"><h2>审计日志</h2><div class="table-wrap sticky-col"><table><thead><tr><th>时间</th><th>操作人</th><th>动作</th><th>对象</th><th>原因</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${r.time}</td><td>${esc(r.operator)}</td><td>${esc(r.action)}</td><td>${esc(r.entity)}</td><td>${esc(r.reason)}</td></tr>`).join('')}</tbody></table></div></section>`))return;}

document.getElementById('logoutBtn').onclick=async()=>{await api('/api/logout',{method:'POST'});location.href=portalPath('/login')};
(async()=>{try{state.me=await api('/api/me');if(state.me.must_change_password){renderPasswordChangeRequired();return;}state.options=await api('/api/options');if(statisticsDetailContext().get('employee_ids'))state.tab='statisticsDetail';applyCircleTheme();installScreenWatermark();installPageBindHint();document.getElementById('userBadge').textContent=`${state.me.name} · ${state.me.role_name}${state.me.attraction_name?` · ${state.me.attraction_name}`:''}${state.me.member_count?` · ${state.me.member_count}名组员`:''}`;renderTabs();void refreshActionBadge();await render();}catch(e){if(!location.pathname.includes('/login'))location.href=portalPath('/login');}})();
