// Function bodies retrieved at 1c9da27ac3844c34f7f4aa2980ce2291690d9dea.
// backend/app/static/js/app.js, helpers + renderReview/reviewActions/bindReviewActions.
// Dependencies and API responses are supplied by a local, explicitly synthetic harness.
function recognitionScoreText(r){const original=fmt(r.fraction);if(r.status==='confirmed'){const credited=fmt(r.credited_fraction ?? r.fraction);if(credited!==original)return `计入${credited}分（原始${original}）`;return `${credited}分`;}return `${original}分`;}
function recognitionScoreNote(r){return r.monthly_cap_reason?`<small>${esc(r.monthly_cap_reason)}</small>`:'';}
async function renderReview(showHistory=false){
  const rows=await api('/api/reviews?view='+(showHistory?'history':'queue'));
  const mobileQuery=window.matchMedia('(max-width: 760px)');
  const cards=rows.map(r=>`<article class="work-card" data-review-row="${r.id}"><div class="work-card-head"><strong>${esc(r.employee_name)}</strong><small>${esc(r.employee_no)}</small><span class="review-status">${statusBadge(r)}</span></div><p>${esc(r.submitted_at)} · ${esc(r.recognition_type)} · 认可人：${esc(r.recognizer_name)} · ${recognitionScoreText(r)}</p><p>${esc(r.content)}${sameDayDuplicateBadge(r)}${r.image_url?` ${attachmentControl(r.image_url,'查看认可图片',r.image_preview_kind)}`:''}</p>${recognitionScoreNote(r)}<div class="review-actions">${reviewActions(r)}</div></article>`).join('')||'<div class="empty">暂无记录</div>';
  const table=`<div class="table-wrap sticky-col"><table><thead><tr><th>提交时间</th><th>组员</th><th>认可信息</th><th>分值</th><th>状态</th><th>操作</th></tr></thead><tbody>${rows.map(r=>`<tr data-review-row="${r.id}"><td>${esc(r.submitted_at)}</td><td>${esc(r.employee_name)}<br><small>${esc(r.employee_no)}</small></td><td>${esc(r.recognition_type)} · ${esc(r.recognizer_name)}${sameDayDuplicateBadge(r)}<br>${esc(r.content)}${r.image_url?`<br>${attachmentControl(r.image_url,'查看认可图片',r.image_preview_kind)}`:''}${r.monthly_cap_reason?`<br><small>${esc(r.monthly_cap_reason)}</small>`:''}</td><td>${recognitionScoreText(r)}</td><td class="review-status">${statusBadge(r)}</td><td class="review-actions">${reviewActions(r)}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">暂无记录</td></tr>'}</tbody></table></div>`;
  app.innerHTML=`<section class="panel"><div class="record-line"><div><h2>复核</h2><p>${showHistory?'已处理记录（已确认和不通过，最多200条）':'待复核记录'}</p></div><button type="button" class="secondary" id="reviewHistoryToggle">${showHistory?'返回待处理':'查看已处理历史'}</button></div>${mobileQuery.matches?`<div class="work-card-list">${cards}</div>`:table}</section>`;
  const handleLayoutChange=()=>{clearPageResources();renderReview(showHistory);};
  mobileQuery.addEventListener?.('change',handleLayoutChange);
  registerPageCleanup(()=>mobileQuery.removeEventListener?.('change',handleLayoutChange));
  document.getElementById('reviewHistoryToggle').onclick=()=>{clearPageResources();renderReview(!showHistory);};
  bindFilePreviews(app);
  bindReviewActions();
}
function reviewActions(r){return r.status==='pending'?`<button data-review="${r.id}" data-action="confirm" class="primary">确认</button> <button data-review="${r.id}" data-action="reject" class="danger">不通过</button>`:`<button data-review="${r.id}" data-action="restore" class="secondary">还原</button>`}
function bindReviewActions(){app.querySelectorAll('[data-review]').forEach(b=>b.onclick=async()=>{const note=b.dataset.action==='reject'?(await promptModal('请输入不通过原因','请填写不通过原因（必填）','不通过原因'))||'':'';if(b.dataset.action==='reject'&&!note)return;try{const out=await api('/api/reviews/'+b.dataset.review,json('POST',{action:b.dataset.action,note}));app.querySelectorAll(`[data-review-row="${b.dataset.review}"]`).forEach(row=>{const status=row.querySelector('.review-status'),actions=row.querySelector('.review-actions');if(status)status.innerHTML=statusBadge(out.record);if(actions)actions.innerHTML=reviewActions(out.record);});bindReviewActions();toast('操作成功');}catch(x){toast(x.message,true)}})}
