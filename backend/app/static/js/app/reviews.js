// Frontline/supervisor recognition review and statement-upgrade review.
import { api, json } from './api.js';
import { beginViewRequest, captureViewContext, clearPageResources, registerPageCleanup } from './context.js';
import { confirmModal, promptModal, selectModal } from './dialogs.js';
import { attachmentControl, bindFilePreviews } from './files.js';
import { esc, fmt, recognitionScoreNote, recognitionScoreText } from './format.js';
import { refreshActionBadge } from './navigation.js';
import { sameDayDuplicateBadge, statusBadge } from './records.js';
import { app, state } from './state.js';
import { toast } from './toast.js';

function reviewCard(r){return `<article class="work-card" data-review-row="${r.id}"><div class="work-card-head"><strong>${esc(r.employee_name)}</strong><small>${esc(r.employee_no)}</small><span class="review-status">${statusBadge(r)}</span></div><p><strong>认可日期：${esc(r.recognition_date||'未记录')}</strong><br><small>提交时间：${esc(r.submitted_at)}</small></p><p>${esc(r.recognition_type)} · 认可人：${esc(r.recognizer_name)} · ${recognitionScoreText(r)}</p><p>${esc(r.content)}${sameDayDuplicateBadge(r)}${r.image_url?` ${attachmentControl(r.image_url,'查看认可图片',r.image_preview_kind)}`:''}</p>${recognitionScoreNote(r)}${r.review_note?`<small>复核说明：${esc(r.review_note)}</small>`:''}<div class="review-actions">${reviewActions(r)}</div></article>`;}

function reviewTableRow(r){return `<tr data-review-row="${r.id}"><td><strong>认可日期：${esc(r.recognition_date||'未记录')}</strong><br><small>提交时间：${esc(r.submitted_at)}</small></td><td>${esc(r.employee_name)}<br><small>${esc(r.employee_no)}</small></td><td>${esc(r.recognition_type)} · ${esc(r.recognizer_name)}${sameDayDuplicateBadge(r)}<br>${esc(r.content)}${r.image_url?`<br>${attachmentControl(r.image_url,'查看认可图片',r.image_preview_kind)}`:''}${r.monthly_cap_reason?`<br><small>${esc(r.monthly_cap_reason)}</small>`:''}${r.review_note?`<br><small>复核说明：${esc(r.review_note)}</small>`:''}</td><td>${recognitionScoreText(r)}</td><td class="review-status">${statusBadge(r)}</td><td class="review-actions">${reviewActions(r)}</td></tr>`;}

function replaceReviewRows(record){app.querySelectorAll(`[data-review-row="${record.id}"]`).forEach(row=>{row.outerHTML=row.tagName==='TR'?reviewTableRow(record):reviewCard(record);});bindFilePreviews(app);}

// 组长复核直属组员；主管复核页（正式GSM/AM/OM）复用同一套界面，只换接口。
let reviewApiBase='/api/reviews';

async function renderReview(showHistory=false, offset=0){
  const request=beginViewRequest();
  const view=showHistory?'history':'queue';
  const supervisorQueue=reviewApiBase==='/api/supervisor-reviews';
  const requestedLimit=!showHistory&&state.reviewFocus?1:200;
  const data=await api(reviewApiBase+'?view='+view+'&limit='+requestedLimit+'&offset='+offset);
  if(!request.isCurrent())return;
  const rows=data.items||[];
  if(!showHistory&&!rows.length&&offset>0)return renderReview(false,Math.max(0,offset-Number(data.limit||200)));
  const focused=!showHistory&&Boolean(state.reviewFocus);
  const displayRows=focused?rows.slice(0,1):rows;
  const total=Number(data.total||0);
  const limit=Number(data.limit||200);
  const pageOffset=Number(data.offset||offset||0);
  const hasMore=Boolean(data.has_more);
  const mobileQuery=window.matchMedia('(max-width: 760px)');
  const cards=displayRows.map(reviewCard).join('')||'<div class="empty">暂无记录</div>';
  const table=`<div class="table-wrap sticky-col"><table><thead><tr><th>认可日期 / 提交时间</th><th>${supervisorQueue?'主管':'组员'}</th><th>认可信息</th><th>分值</th><th>状态</th><th>操作</th></tr></thead><tbody>${rows.map(reviewTableRow).join('')||'<tr><td colspan="6" class="empty">暂无记录</td></tr>'}</tbody></table></div>`;
  const hint=showHistory?`已处理记录（已确认和不通过，共${total}条）`:`待复核记录（共${total}条，最早提交的在前）`;
  const pager=(total>limit||pageOffset>0||hasMore)?`<div class="pagination"><button type="button" id="reviewPrev" class="secondary" ${pageOffset<=0?'disabled':''}>上一页</button><span>本页${rows.length}条 · 共${total}条</span><button type="button" id="reviewNext" class="secondary" ${hasMore?'':'disabled'}>下一页</button></div>`:'';
  if(!request.write(`<section class="panel review-page"><div class="record-line"><h2>${supervisorQueue?'主管复核':'复核'}</h2><button type="button" class="secondary" id="reviewHistoryToggle">${showHistory?'返回待处理':'查看已处理历史'}</button></div><p>${hint}</p>${!showHistory?`<div class="review-focus-toolbar"><span>${focused?'逐条核对图片与内容；处理成功后载入最早待复核的一条。':'可切换为逐条处理，每次专注一条完整记录。'}</span><button type="button" class="secondary" id="reviewFocusToggle">${focused?'返回完整列表':'逐条处理'}</button></div>`:''}<div class="${focused?'review-focused':''}">${focused||mobileQuery.matches?`<div class="work-card-list">${cards}</div>`:table}</div>${focused?'':pager}</section>`))return;
  const reload=(history,offset)=>{const owner=captureViewContext(app,{latest:false});clearPageResources();void renderReview(history,offset).catch(error=>{if(error?.name!=='AbortError'&&owner.isCurrent())toast(error.message||'加载复核记录失败，请重试',true);});};
  const handleLayoutChange=()=>reload(showHistory,pageOffset);
  mobileQuery.addEventListener?.('change',handleLayoutChange);
  registerPageCleanup(()=>mobileQuery.removeEventListener?.('change',handleLayoutChange));
  document.getElementById('reviewFocusToggle')?.addEventListener('click',()=>{state.reviewFocus=!focused;reload(false,0);});
  if(focused)app.querySelectorAll('[data-action=confirm]').forEach(button=>button.textContent='确认，继续下一条');
  document.getElementById('reviewHistoryToggle').onclick=()=>reload(!showHistory,0);
  document.getElementById('reviewPrev')?.addEventListener('click',()=>{if(pageOffset<=0)return;reload(showHistory,Math.max(0,pageOffset-limit));});
  document.getElementById('reviewNext')?.addEventListener('click',()=>{if(!hasMore)return;reload(showHistory,pageOffset+limit);});
  bindFilePreviews(app);
  bindReviewActions(showHistory,pageOffset);
}

function reviewActions(r){return r.status==='pending'?`<button data-review="${r.id}" data-action="confirm" class="primary">确认</button> <button data-review="${r.id}" data-action="reject" class="danger">不通过</button>`:`<button data-review="${r.id}" data-action="restore" class="secondary">还原</button>`}

const reviewWrites=new Set();
function bindReviewActions(showHistory=false,offset=0){
  const page=captureViewContext(),apiBase=reviewApiBase;
  app.querySelectorAll('[data-review]').forEach(button=>button.onclick=async()=>{
    const key=apiBase+'/'+button.dataset.review;
    if(reviewWrites.has(key))return;
    reviewWrites.add(key);
    const peers=[...app.querySelectorAll(`[data-review="${button.dataset.review}"]`)];
    peers.forEach(node=>node.disabled=true);
    try{
      const note=button.dataset.action==='reject'?(await promptModal('请输入不通过原因','请填写不通过原因（必填）','不通过原因'))||'':'';
      // The accepted action keeps its captured queue across navigation;
      // only repaint/refresh belongs to the original page context.
      if(button.dataset.action==='reject'&&!note)return;
      const out=await api(key,json('POST',{action:button.dataset.action,note}));
      void refreshActionBadge();toast('操作成功');
      if(!page.isCurrent()||!out.record)return;
      if(!showHistory&&out.record.status!=='pending'){
        clearPageResources();await renderReview(showHistory,offset);
      }else{replaceReviewRows(out.record);bindReviewActions(showHistory,offset);}
    }catch(error){toast(error.message,true);}
    finally{reviewWrites.delete(key);peers.forEach(node=>node.disabled=false);}
  });
}

function upgradeReviewCards(items){
  return items.map(row=>`<article class="group-card" data-upgrade="${row.id}"><div class="record-line"><strong>${esc(row.employee_name)} · ${esc(row.employee_no)}</strong><span class="badge warn">待审核</span></div><p>${esc(row.deduction_type)} · 提交人：${esc(row.submitted_by)} · ${esc(row.created_at)}</p><p><strong>A1：</strong>${esc(row.first_record?.occurred_on||'')} · ${esc(row.first_record?.description||'')} ${attachmentControl(row.first_record?.document_url||'','查看A1声明',row.first_record?.document_preview_kind||'')}</p><p><strong>A2：</strong>${esc(row.second_record?.occurred_on||'')} · ${esc(row.second_record?.description||'')} ${attachmentControl(row.second_record?.document_url||'','查看A2声明',row.second_record?.document_preview_kind||'')}</p><div class="actions"><button type="button" class="secondary" data-upgrade-transfer="${row.id}">转交工单</button><button type="button" class="danger" data-upgrade-reject="${row.id}">不通过</button><button type="button" class="primary" data-upgrade-approve="${row.id}">同意升级</button></div></article>`).join('')||'<p class="empty">暂无声明升级待审核工单</p>';
}

function bindUpgradeReviewActions(root,refresh){const page=captureViewContext();
  const levels=(state.options.deduction_levels||[]).filter(x=>['MEMO','WARNING_1'].includes(x.code));
  bindFilePreviews(root);
  root.querySelectorAll('[data-upgrade-transfer]').forEach(button=>button.onclick=async()=>{const reviewers=await api('/api/deduction-upgrades/reviewers');if(!page.isCurrent())return;const options=reviewers.items||[];const id=await selectModal('转交声明升级工单','请选择新的审核 MOD（GSM / TA GSM）。',options.map(x=>({id:x.id,label:`${x.name} · ${x.employee_no} · ${x.role_name}`})),'下一步');if(!id)return;const reason=await promptModal('转交说明','请输入转交原因（必填）','转交原因','确认转交');if(!reason)return;try{await api('/api/deduction-upgrades/'+button.dataset.upgradeTransfer+'/transfer',json('POST',{reviewer_id:id,reason}));toast('工单已转交');page.refresh(refresh)}catch(error){toast(error.message,true)}});
  root.querySelectorAll('[data-upgrade-reject]').forEach(button=>button.onclick=async()=>{const note=await promptModal('不通过声明升级','请填写处理说明（必填）','处理说明','确认不通过');if(!note)return;try{await api('/api/deduction-upgrades/'+button.dataset.upgradeReject+'/resolve',json('POST',{decision:'reject',handling_note:note}));toast('工单已处理，第二条声明已作废');page.refresh(refresh)}catch(error){toast(error.message,true)}});
  root.querySelectorAll('[data-upgrade-approve]').forEach(button=>button.onclick=async()=>{const levelId=await selectModal('选择升级结果','请选择真实开具的处分结果。',levels.map(x=>({id:x.id,label:`${x.name} · ${fmt(x.points)}分`})),'下一步');if(!levelId)return;const note=await promptModal('处理说明','请输入处理说明（必填）','处理说明','下一步');if(!note)return;const done=await confirmModal('确认真实处分已完成开具',`<p>请确认已完成真实备忘录或一级警告开具。</p><p>确认后系统将生效扣分，第二条声明不计分。</p>`,'是，已完成开具并继续');if(!done)return;try{await api('/api/deduction-upgrades/'+button.dataset.upgradeApprove+'/resolve',json('POST',{decision:'approve',result_level_id:levelId,handling_note:note,issued_confirmed:true}));toast('升级处分已生效');page.refresh(refresh)}catch(error){toast(error.message,true)}});
}

async function renderUpgradeReview(){
  const request=beginViewRequest();
  const data=await api('/api/deduction-upgrades/pending'),items=data.items||[];
  if(!request.isCurrent())return;
  if(!request.write(`<section class="panel"><h2>待审核 · 声明升级审核</h2><p>该页面已并入“待办中心”的待审核标签，此入口仅用于兼容已打开页面。</p><div class="governance-case-list">${upgradeReviewCards(items)}</div></section>`))return;
  bindUpgradeReviewActions(app,renderUpgradeReview);
}

function renderSupervisorReview(){reviewApiBase='/api/supervisor-reviews';return renderReview();}
function renderFrontlineReview(){reviewApiBase='/api/reviews';return renderReview();}

export { bindUpgradeReviewActions, renderFrontlineReview, renderSupervisorReview, renderUpgradeReview, upgradeReviewCards };
