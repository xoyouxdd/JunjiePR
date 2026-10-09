// Recognition encouragement and performance-registration feedback.
import { bindDialogLayer } from './dialogs.js';
import { esc } from './format.js';
import { state } from './state.js';
import { toast } from './toast.js';

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

export { performanceFeedbackData, recognitionEncouragement, showPerformanceRegistrationFeedback, showRecognitionEncouragement };
