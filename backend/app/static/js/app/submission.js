// Date fallbacks, readable image payloads, idempotency keys and per-form submit locks.
import { pad2 } from './format.js';
import { confirmModal } from './dialogs.js';

// Keep drafts in the current page only; never persist business text or files.
const editedForms=new Set();
function clearFormEdits(form){editedForms.delete(form);}
function pendingForms(){return [...editedForms].filter(form=>{if(!form.isConnected){editedForms.delete(form);return false;}return true;});}
function installFormProtection(){
  let prompting=false,approved=null;
  const remember=event=>{const form=event.target.closest?.('form.form-stack');if(form)editedForms.add(form);};
  document.addEventListener('input',remember,true);
  document.addEventListener('change',remember,true);
  document.addEventListener('reset',event=>queueMicrotask(()=>clearFormEdits(event.target)),true);
  window.addEventListener('beforeunload',event=>{if(pendingForms().length){event.preventDefault();event.returnValue='';}});
  document.addEventListener('click',async event=>{
    const target=event.target.closest?.('[data-tab],[data-register-mode],[data-register-target],[data-mobile-logout],#logoutBtn,[data-ann-back],[data-ann-view],[data-ann-compose],[data-ann-new],[data-ann-resume]');
    if(!target)return;
    if(approved===target){approved=null;return;}
    const forms=pendingForms();if(!forms.length)return;
    event.preventDefault();event.stopImmediatePropagation();
    if(prompting)return;
    prompting=true;
    const busy=forms.some(form=>form.dataset.submitting==='true');
    try{
      if(await confirmModal(busy?'操作仍在提交':'内容尚未提交',busy?'<p>提交仍在进行，离开后请先核对记录，避免重复操作。确定离开吗？</p>':'<p>离开后未提交的内容可能丢失，确定离开吗？</p>','确认离开')){
        if(!target.isConnected)return;
        forms.forEach(clearFormEdits);approved=target;target.click();approved=null;
      }
    }finally{prompting=false;}
  },true);
}

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

function clearSubmissionKey(form,keepEdits=false){delete form.dataset.submissionKey;if(!keepEdits)clearFormEdits(form);}

async function withSubmitLock(form, action){
  if(form.dataset.submitting==='true')return;
  form.dataset.submitting='true';
  const button=form.querySelector('button[type="submit"],button:not([type])'),label=button?.textContent,disabled=button?.disabled;
  if(button){button.disabled=true;button.textContent='正在提交…';button.setAttribute('aria-busy','true');}
  const status=document.createElement('p');status.className='field-hint';status.setAttribute('role','status');status.textContent='正在提交，请稍候。';form.appendChild(status);
  const timer=setTimeout(()=>{if(status.isConnected)status.textContent='仍在等待服务器确认，请勿重复提交；若离开，请先在记录中核对结果。';},15000);
  try{return await action();}finally{clearTimeout(timer);status.remove();delete form.dataset.submitting;if(button){button.disabled=!!disabled;button.textContent=label;button.removeAttribute('aria-busy');}}
}

export { bindDateSubmissionFallbacks, clearFormEdits, clearSubmissionKey, installFormProtection, newSubmissionKey, normalizeSubmitDate, readableEvidenceFile, recognitionImageFailureMessage, recognitionImageIssueMessage, recognitionSubmissionData, requireSubmittedDate, submissionData, submittedDateValue, withSubmitLock };
