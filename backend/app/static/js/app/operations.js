// Action-center, operations health and change history.
import { api, json } from './api.js';
import { announcementWorkspaces, setAnnouncementBadge } from './announcement-state.js';
import { beginViewRequest, render } from './context.js';
import { confirmModal } from './dialogs.js';
import { esc } from './format.js';
import { refreshActionBadge, renderTabs, setActionBadge } from './navigation.js';
import { bindUpgradeReviewActions, upgradeReviewCards } from './reviews.js';
import { app, state } from './state.js';
import { toast } from './toast.js';

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
  if(data.announcement_counts)setAnnouncementBadge(data.announcement_counts);
  const todoCards=items.map(item=>`<div class="action-center-item"><article class="action-card severity-${esc(item.severity)}"><div class="action-card-main"><span class="action-severity">${item.severity==='critical'?'优先处理':item.severity==='warning'?'请跟进':'待核对'}</span><strong>${esc(item.title)}</strong><p>${esc(item.description)}</p></div><div class="action-card-side"><div class="action-count"><b>${Number(item.count)}</b><span>项</span></div><button type="button" class="secondary" data-action-tab="${esc(item.tab)}" data-action-type="${esc(item.type)}" aria-expanded="false">立即查看</button></div></article><div class="action-center-inline-detail" hidden></div></div>`).join('')||'<section class="empty action-center-empty"><strong>当前没有待处理事项</strong><span>新的待办出现后会在这里提醒你。</span></section>';
  const reviewTab=isUpgradeReviewer?`<button type="button" class="secondary" data-action-pane-tab="review">待审核 <span class="badge warn">${upgradeItems.length}</span></button>`:'';
  const reviewPane=isUpgradeReviewer?`<section class="action-center-pane" data-action-pane="review" hidden><p class="field-hint">声明升级工单仅显示分配给当前账户的记录。备忘录和一级警告无需上传文件，处理说明必填。</p><div class="governance-case-list">${upgradeReviewCards(upgradeItems)}</div></section>`:'';
  if(!request.write(`<div class="section-gap action-center-page"><section class="panel action-center-heading"><div><span class="eyebrow">行动中心</span><h2>待办中心</h2><p>${esc(data.month)} · 当前共 <strong>${total}</strong> 项待处理事项</p></div><button type="button" class="secondary" id="refreshActionCenter" title="刷新待办中心">刷新待办</button></section><section class="panel action-center-panel"><div class="action-center-switch"><button type="button" class="primary" data-action-pane-tab="todo">待办 <span class="badge">${Number(data.total||0)}</span></button>${reviewTab}</div><section class="action-center-pane" data-action-pane="todo"><section class="action-center-list">${todoCards}</section></section>${reviewPane}</section></div>`))return;
  const setPane=name=>{app.querySelectorAll('[data-action-pane]').forEach(pane=>{pane.hidden=pane.dataset.actionPane!==name;});app.querySelectorAll('[data-action-pane-tab]').forEach(button=>{const active=button.dataset.actionPaneTab===name;button.classList.toggle('primary',active);button.classList.toggle('secondary',!active);});};
  app.querySelectorAll('[data-action-pane-tab]').forEach(button=>button.onclick=()=>setPane(button.dataset.actionPaneTab));
  document.getElementById('refreshActionCenter').onclick=renderActionCenter;
  const closeDetail=button=>{const host=button.closest('.action-center-item').querySelector('.action-center-inline-detail');host.innerHTML='';host.hidden=true;button.textContent='立即查看';button.setAttribute('aria-expanded','false');};
  app.querySelectorAll('[data-action-tab]').forEach(button=>button.onclick=async()=>{const detailTypes=new Set(['backup_health','ungrouped_employee','recognition_review','overdue_review']);if(!detailTypes.has(button.dataset.actionType)){if(button.dataset.actionTab==='announcements'){let workspace=announcementWorkspaces.get(state.me.id);if(!workspace){workspace={view:'pending',category:'',q:'',page:1,draft:null};announcementWorkspaces.set(state.me.id,workspace);}workspace.view=button.dataset.actionType==='announcement_receipt'?'pending':'messages';workspace.page=1;workspace.q='';workspace.category='';}state.tab=button.dataset.actionTab;renderTabs();render();return;}if(button.getAttribute('aria-expanded')==='true'){closeDetail(button);return;}button.disabled=true;try{const detail=await api('/api/action-center/'+encodeURIComponent(button.dataset.actionType)+'/details');if(!request.isCurrent())return;app.querySelectorAll('[data-action-tab][aria-expanded="true"]').forEach(closeDetail);const host=button.closest('.action-center-item').querySelector('.action-center-inline-detail');host.innerHTML=actionCenterDetailHtml(detail);host.hidden=false;button.textContent='收起';button.setAttribute('aria-expanded','true');host.querySelector('[data-close-action-detail]')?.addEventListener('click',()=>closeDetail(button));if(detail.type==='backup_health'){
    host.querySelector('[data-backup-dismiss]').onclick=async()=>{const confirmed=await confirmModal('删除备份待办','<p>仅移除当前这条待办，不删除备份文件；新巡检异常或24小时后会重新提醒。</p>','确认删除');if(!confirmed)return;try{await api('/api/admin/backup-health/dismiss',json('POST',{}));toast('当前备份待办已移除');request.refresh(renderActionCenter);void refreshActionBadge();}catch(error){toast(error.message,true)}};
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

export { renderActionCenter, renderChangelog, renderOperations };
