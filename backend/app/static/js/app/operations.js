// Action-center, operations health and change history.
import { api, json } from './api.js';
import { announcementWorkspaces, setAnnouncementBadge } from './announcement-state.js';
import { beginViewRequest, render } from './context.js';
import { mountExportTasks } from './export-tasks.js';
import { confirmModal } from './dialogs.js';
import { esc } from './format.js';
import { menuItems, navIcon, refreshActionBadge, renderTabs, setActionBadge } from './navigation.js';
import { workItemDetailsHtml } from './records.js';
import { bindUpgradeReviewActions, upgradeReviewWorkspace, bindUpgradeRecoveryActions, upgradeRecoveryCards } from './reviews.js';
import { app, has, state } from './state.js';
import { toast } from './toast.js';
import { mountRotationTasks } from './rotation.js';
import { mountDailyWish } from './daily-wish.js';

function actionCenterWaiting(hours){return `${Number(hours||0).toFixed(1)}小时`;}

function reviewDetailTable(rows,pageable){
  if(!rows.length)return '';
  const groups=new Map();
  for(const row of rows){
    const key=row.unassigned?'unassigned':row.reviewer_group||row.reviewer_no||row.reviewer_name;
    if(!groups.has(key))groups.set(key,{name:row.unassigned?'无人复核':row.reviewer_name,role:row.reviewer_role_name,unassigned:row.unassigned,rows:[]});
    groups.get(key).rows.push(row);
  }
  const ordered=[...groups.values()].sort((a,b)=>Number(Boolean(a.unassigned))-Number(Boolean(b.unassigned)));
  const sections=ordered.map(group=>`<tbody data-review-group>
    <tr class="review-owner-heading"><th scope="rowgroup" colspan="5">
      <strong>${esc(group.name)}</strong>${group.role?` <span>${esc(group.role)}</span>`:''}
      <span class="review-owner-count">${pageable?'本页 ':''}${group.rows.length} 条</span>
    </th></tr>
    ${group.rows.map(row=>`<tr>
      <td data-label="员工"><div><strong>${esc(row.employee_name)}</strong><br><small>${esc(row.employee_no)}</small></div></td>
      <td data-label="景点圈"><div>${esc(row.attraction_name)}${row.group_name?`<br><small>${esc(row.group_name)}</small>`:''}</div></td>
      <td data-label="签卡"><span>${esc(row.recognition_date)} · ${esc(row.recognition_type)}</span></td>
      <td data-label="提交时间"><span>${esc(row.submitted_at)}</span></td>
      <td data-label="等待复核"><span>${actionCenterWaiting(row.waiting_hours)}</span></td>
    </tr>`).join('')}
  </tbody>`).join('');
  return `<div class="table-wrap"><table class="review-detail-table">
    <thead><tr><th>员工</th><th>景点圈</th><th>签卡</th><th>员工提交时间</th><th>等待复核</th></tr></thead>
    ${sections}
  </table></div>`;
}

function actionCenterDetailHtml(detail){
  const rows=detail.items||[];
  if(detail.type==='deduction_follow_up')return `<section class="panel action-center-detail"><h3>${esc(detail.title)}</h3>${workItemDetailsHtml(detail)}</section>`;
  if(detail.type==='backup_health'){
    const manual=detail.manual_backup||{},manualText=manual.status==='running'?'手动备份正在执行…':manual.status==='completed'?`最近手动备份成功：${esc(manual.file_name||'已完成')}`:manual.status==='failed'?'最近手动备份失败，请联系服务器管理员检查日志。':manual.status==='interrupted'?'上次手动备份可能已中断，请核对备份目录后重试。':'尚未执行手动备份';
    return `<section class="panel action-center-detail"><div class="statistics-detail-heading"><div><h3>${esc(detail.title)}</h3><span>最近检查：${esc(detail.checked_at_utc||'暂无')}</span></div><button type="button" class="secondary" data-close-action-detail>关闭</button></div><ul class="operations-issues">${rows.map(row=>`<li><strong>${esc(row.code)}</strong>${row.message?`：${esc(row.message)}`:''}</li>`).join('')||'<li>当前没有异常项</li>'}</ul><p class="field-hint" data-manual-backup-status>${manualText}</p><p class="field-hint">重新备份仅创建一次数据库快照，不会修复缺失的每日计划任务；清除待办不删除备份，若异常持续，24小时后会再次提醒。</p><div class="actions"><button type="button" class="secondary" data-backup-dismiss>删除此待办</button><button type="button" class="primary" data-backup-retry ${manual.status==='running'?'disabled':''}>重新备份</button></div></section>`;
  }
  if(detail.type==='ungrouped_employee')return `<section class="panel action-center-detail"><div class="statistics-detail-heading"><div><h3>${esc(detail.title)}</h3><span>请在员工管理中为以下员工分配主管。</span></div><button type="button" class="secondary" data-close-action-detail>关闭</button></div><div class="table-wrap"><table><thead><tr><th>员工</th><th>角色</th><th>景点圈</th><th>未分组起算日</th><th>待分组时长</th><th>计算依据</th></tr></thead><tbody>${rows.map(row=>`<tr><td><strong>${esc(row.employee_name)}</strong><br><small>${esc(row.employee_no)}</small></td><td>${esc(row.role_name)}</td><td>${esc(row.attraction_name)}</td><td>${esc(row.unassigned_since||'—')}</td><td>${row.waiting_days===null||row.waiting_days===undefined?'—':`${Number(row.waiting_days)}天`}</td><td>${esc(row.waiting_basis||'—')}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">当前没有待分组员工</td></tr>'}</tbody></table></div></section>`;
  const pageable=detail.type==='recognition_review'||detail.type==='supervisor_review';
  const restart=pageable&&!rows.length&&detail.total>0;
  const pager=pageable&&(detail.previous_cursor||detail.next_cursor||restart)
    ? `<div class="pagination" data-recognition-detail-pagination>
      <button type="button" class="secondary" data-detail-prev ${detail.previous_cursor?'':'disabled'}>上一页</button>
      <span>本页 ${rows.length} 条 · 共 ${detail.total} 条</span>
      <button type="button" class="secondary" data-detail-next ${detail.next_cursor?'':'disabled'}>下一页</button>
      ${restart?'<button type="button" class="secondary" data-detail-restart>从头查看</button>':''}
    </div>`
    : '';
  const hint=detail.escalated_only?'显示等待超过 7 天或无人复核的签卡。':'每组按提交时间从早到晚展示。';
  return `<section class="panel action-center-detail"><div class="statistics-detail-heading"><div><h3>${esc(detail.title)}</h3><span>${hint}</span></div><button type="button" class="secondary" data-close-action-detail>关闭</button></div>${reviewDetailTable(rows,pageable)||`<div class="empty">${restart?'此方向已无待复核签卡':'当前没有待复核签卡'}</div>`}${pager}</section>`;
}

function bindRecognitionDetailPagination(host,detail,request,closeDetail){
  const count=host.closest('.action-center-item')?.querySelector('.action-count b');
  if(count&&Number.isInteger(detail.total))count.textContent=detail.total;
  const pager=host.querySelector('[data-recognition-detail-pagination]');
  if(!pager)return;
  const controls=[...pager.querySelectorAll('button:not(:disabled)')];
  const isCurrent=()=>request.isCurrent()&&!host.hidden&&host.contains(pager);
  const loadPage=async (cursor,direction)=>{
    controls.forEach(button=>{button.disabled=true;});
    try{
      const params=new URLSearchParams({page_size:detail.page_size});
      if(cursor){
        params.set('cursor',cursor);
        params.set('direction',direction);
      }
      const data=await api('/api/action-center/'+detail.type+'/details?'+params);
      if(!isCurrent())return;
      host.innerHTML=actionCenterDetailHtml(data);
      host.querySelector('[data-close-action-detail]').onclick=closeDetail;
      bindRecognitionDetailPagination(host,data,request,closeDetail);
    }catch(error){
      if(isCurrent())toast(error.message,true);
    }finally{
      if(isCurrent())controls.forEach(button=>{button.disabled=false;});
    }
  };
  pager.querySelector('[data-detail-prev]').onclick=()=>loadPage(detail.previous_cursor,'previous');
  pager.querySelector('[data-detail-next]').onclick=()=>loadPage(detail.next_cursor,'next');
  const restart=pager.querySelector('[data-detail-restart]');
  if(restart)restart.onclick=()=>loadPage();
}

async function renderActionCenter(openPane){
  const request=beginViewRequest();
  const isUpgradeReviewer=state.me.role_code==='GSM'&&has('DEDUCTION_ALL');
  const [data,upgradeData,recoveryData]=await Promise.all([api('/api/action-center'),isUpgradeReviewer?api('/api/deduction-upgrades/pending'):Promise.resolve({items:[]}),api('/api/deduction-upgrades/recovery')]);
  if(!request.isCurrent())return;
  const items=data.items||[],upgradeItems=upgradeData.items||[],recoveryItems=recoveryData.items||[];
  const reviewCount=upgradeItems.filter(row=>row.reviewer_id===state.me.id).length+(upgradeData.notices||[]).length+recoveryItems.length;
  const total=Number(data.total||0)+reviewCount;
  const overdueCount=Number(data.overdue_review_reminder||0);
  const overdueReminder=overdueCount
    ? `<p class="review-overdue-reminder">你当前可复核的签卡中，有 <strong>${overdueCount} 条</strong> 已等超过 48 小时。</p>`
    : '';
  setActionBadge(total);
  if(data.announcement_counts)setAnnouncementBadge(data.announcement_counts);
  const severityOrder={critical:0,warning:1,info:2};
  const visibleItems=items.filter(item=>item.type!=='rotation_step').sort((a,b)=>Number(!!b.strong_reminder)-Number(!!a.strong_reminder)||(severityOrder[a.severity]??3)-(severityOrder[b.severity]??3));
  const todoCards=visibleItems.map((item,index)=>`<div class="action-center-item"><article class="action-card severity-${esc(item.severity)} ${index===0?'is-priority':''}"><div class="action-card-main"><span class="action-severity">${item.severity==='critical'?'优先处理':item.severity==='warning'?'请跟进':'待核对'}</span><strong>${esc(item.title)}</strong><p>${esc(item.description)}</p></div><div class="action-card-side"><div class="action-count"><b>${Number(item.count)}</b><span>项</span></div><button type="button" class="${index===0?'primary':'secondary'}" data-action-tab="${esc(item.tab)}" data-action-type="${esc(item.type)}" aria-expanded="false">${item.type==='recognition_review'?'开始复核':'立即查看'}</button>${['recognition_review','supervisor_review'].includes(item.type)?`<button type="button" class="secondary" data-action-tab="${esc(item.tab)}" data-action-type="${esc(item.type)}" data-action-details="true" aria-expanded="false">查看明细</button>`:''}</div></article><div class="action-center-inline-detail" hidden></div></div>`).join('')||(items.some(item=>item.type==='rotation_step')?'':'<section class="empty action-center-empty"><span class="workbench-empty-mark" aria-hidden="true">✓</span><strong>这里暂时没有待办</strong><span>当前没有待处理事项。新的待办会在这里提醒你。</span></section>');
  const showUpgradePane=isUpgradeReviewer||recoveryItems.length>0;
  const reviewTab=showUpgradePane?`<button type="button" class="secondary" data-action-pane-tab="review">${isUpgradeReviewer?'待审核':'重新指派'} <span class="badge warn">${reviewCount}</span></button>`:'';
  const reviewPane=showUpgradePane?`<section class="action-center-pane" data-action-pane="review" hidden>${isUpgradeReviewer?'<p class="field-hint">备忘录和一级警告无需上传文件，处理说明必填。</p>':''}<div class="governance-case-list">${upgradeRecoveryCards(recoveryItems)}${isUpgradeReviewer?upgradeReviewWorkspace(upgradeData):''}</div></section>`:'';
  const shortcuts=menuItems().filter(([id])=>!['actionCenter','password','logs','hrScores','circleHrAccounts'].includes(id)).slice(0,6);
  const side=`<aside class="panel workbench-shortcuts"><h3>常用工作</h3><div>${shortcuts.map(([id,label])=>`<button type="button" class="workbench-shortcut" data-workbench-tab="${esc(id)}">${navIcon(id)}<span>${esc(label)}</span><span aria-hidden="true">›</span></button>`).join('')}</div><div data-workbench-exports></div></aside>`;
  if(!request.write(`<div class="section-gap action-center-page"><section class="workbench-heading"><div><span class="eyebrow">${esc(state.me.role_label||state.me.role_name||'FZ工作台')}</span><h2>今日工作</h2><p>${esc(data.month)} · <strong data-action-center-total-count>${total}</strong> 项待处理<span class="workbench-updated"> · 本次已更新</span></p></div><button type="button" class="secondary" id="refreshActionCenter" title="刷新待办中心">刷新</button></section><div class="workbench-columns"><section class="panel action-center-panel"><div class="action-center-switch"><button type="button" class="primary" data-action-pane-tab="todo">待办 <span class="badge" data-action-center-todo-count>${Number(data.total||0)}</span></button>${reviewTab}</div><section class="action-center-pane" data-action-pane="todo"><div class="action-center-list" data-warehouse-priority></div><div data-formal-rotation-todo></div>${overdueReminder}<section class="action-center-list">${todoCards}</section></section>${reviewPane}</section>${side}</div></div>`))return;
  const wishHost=document.getElementById('themeStory');if(wishHost)mountDailyWish(wishHost,state.me.id,{integrated:true});
  app.querySelectorAll('[data-workbench-tab]').forEach(button=>button.onclick=()=>{state.tab=button.dataset.workbenchTab;renderTabs();render();});
  mountExportTasks(app.querySelector('[data-workbench-exports]'));
  mountRotationTasks(app.querySelector('[data-formal-rotation-todo]'),request,()=>void render(),app.querySelector('.workbench-columns'));
  const setPane=name=>{app.querySelectorAll('[data-action-pane]').forEach(pane=>{pane.hidden=pane.dataset.actionPane!==name;});app.querySelectorAll('[data-action-pane-tab]').forEach(button=>{const active=button.dataset.actionPaneTab===name;button.classList.toggle('primary',active);button.classList.toggle('secondary',!active);});};
  app.querySelectorAll('[data-action-pane-tab]').forEach(button=>button.onclick=()=>setPane(button.dataset.actionPaneTab));
  if(showUpgradePane&&(openPane==='review'||(!Number(data.total||0)&&(reviewCount||upgradeItems.length))))setPane('review');
  const warehouseTask=app.querySelector('[data-action-type="warehouse_followup"]')?.closest('.action-center-item');
  if(warehouseTask)app.querySelector('[data-warehouse-priority]').append(warehouseTask);
  document.getElementById('refreshActionCenter').onclick=()=>render();
  const closeDetail=button=>{const host=button.closest('.action-center-item').querySelector('.action-center-inline-detail');host.innerHTML='';host.hidden=true;button.textContent=button.dataset.actionDetails?'查看明细':'立即查看';button.setAttribute('aria-expanded','false');};
  app.querySelectorAll('[data-action-tab]').forEach(button=>button.onclick=async()=>{if(button.dataset.actionType==='recognition_review'&&!button.dataset.actionDetails){state.reviewFocus=true;state.tab=button.dataset.actionTab;renderTabs();render();return;}const detailTypes=new Set(['backup_health','ungrouped_employee','recognition_review','supervisor_review','overdue_review','deduction_follow_up']);if(!detailTypes.has(button.dataset.actionType)||(button.dataset.actionType==='supervisor_review'&&!button.dataset.actionDetails)){if(button.dataset.actionType==='deduction_material')state.pendingMaterialScope='all';if(button.dataset.actionType==='learning_access')state.learningApprovals=true;if(button.dataset.actionType==='warehouse_followup')state.warehouseOpenTasks=true;if(button.dataset.actionTab==='announcements'){let workspace=announcementWorkspaces.get(state.me.id);if(!workspace){workspace={view:'pending',category:'',q:'',page:1,draft:null};announcementWorkspaces.set(state.me.id,workspace);}workspace.view=button.dataset.actionType==='announcement_receipt'?'pending':'messages';workspace.page=1;workspace.q='';workspace.category='';}state.tab=button.dataset.actionTab;renderTabs();render();return;}if(button.getAttribute('aria-expanded')==='true'){closeDetail(button);return;}button.disabled=true;try{const params=button.dataset.actionType==='overdue_review'?'?escalated_only=true':'';const detail=await api('/api/action-center/'+encodeURIComponent(button.dataset.actionType)+'/details'+params);if(!request.isCurrent())return;app.querySelectorAll('[data-action-tab][aria-expanded="true"]').forEach(closeDetail);const host=button.closest('.action-center-item').querySelector('.action-center-inline-detail');host.innerHTML=actionCenterDetailHtml(detail);host.hidden=false;
if(detail.type==='recognition_review'||detail.type==='supervisor_review'){
  bindRecognitionDetailPagination(host,detail,request,()=>closeDetail(button));
}
if(detail.type==='deduction_follow_up'){
  let detailPage=1,detailSequence=0;
  const showWork=async(pageNumber,initial)=>{const serial=++detailSequence;try{const data=initial||await api('/api/action-center/deduction_follow_up/details?page='+pageNumber);if(!request.isCurrent()||!host.isConnected||host.hidden||serial!==detailSequence)return;detailPage=data.page||pageNumber;host.innerHTML=actionCenterDetailHtml(data);host.querySelector('[data-work-prev]').onclick=()=>showWork(detailPage-1);host.querySelector('[data-work-next]').onclick=()=>showWork(detailPage+1);host.querySelectorAll('[data-follow-up-register]').forEach(link=>link.onclick=()=>{const row=data.items.find(item=>item.id===Number(link.dataset.followUpRegister));state.followUpEntry=row;state.registerMode='proxy';state.tab='register';renderTabs();render();});}catch(error){toast(error.message,true)}};
  await showWork(1,detail);
}
button.textContent='收起';button.setAttribute('aria-expanded','true');host.querySelector('[data-close-action-detail]')?.addEventListener('click',()=>closeDetail(button));if(detail.type==='backup_health'){
    host.querySelector('[data-backup-dismiss]').onclick=async()=>{const confirmed=await confirmModal('删除备份待办','<p>仅移除当前这条待办，不删除备份文件；新巡检异常或24小时后会重新提醒。</p>','确认删除');if(!confirmed)return;try{await api('/api/admin/backup-health/dismiss',json('POST',{}));toast('当前备份待办已移除');request.refresh(renderActionCenter);void refreshActionBadge();}catch(error){toast(error.message,true)}};
    host.querySelector('[data-backup-retry]').onclick=async()=>{const confirmed=await confirmModal('重新备份数据库','<p>将在服务器后台创建一次新的 SQLite 快照；这不会修复缺失的每日计划任务。</p>','确认备份');if(!confirmed)return;const retryButton=host.querySelector('[data-backup-retry]');retryButton.disabled=true;try{await api('/api/admin/backup-health/retry',json('POST',{}));toast('手动备份已启动');const statusNode=host.querySelector('[data-manual-backup-status]');statusNode.textContent='手动备份正在执行…';for(let i=0;i<120&&host.isConnected;i++){await new Promise(resolve=>setTimeout(resolve,3000));if(!host.isConnected)break;const status=await api('/api/admin/backup-health/manual-status');if(status.status!=='running'){statusNode.textContent=status.status==='completed'?`手动备份成功：${status.file_name||'已完成'}`:'手动备份失败，请联系服务器管理员检查日志。';toast(status.status==='completed'?'手动备份完成':'手动备份失败',status.status!=='completed');retryButton.disabled=false;break;}}}catch(error){retryButton.disabled=false;toast(error.message,true)}};
  }host.scrollIntoView({behavior:'smooth',block:'nearest'});}catch(error){toast(error.message,true)}finally{button.disabled=false;}});
  if(isUpgradeReviewer)bindUpgradeReviewActions(app,()=>renderActionCenter('review'));
  bindUpgradeRecoveryActions(app,recoveryData,()=>renderActionCenter('review'));
}

function operationsBackupHtml(backup){
  const latest=backup.latest_backup||{},issues=backup.issues||[],alert=backup.alert||{},task=backup.task||{};
  return `<section class="panel operations-backup ${backup.ok?'is-ok':'is-alert'}"><div class="operations-heading"><div><h2>备份健康</h2><p>最近检查：${esc(backup.checked_at_utc||'暂无健康报告')}</p></div><span class="badge ${backup.ok?'ok':'danger'}">${esc(backup.status||'未知')}</span></div><div class="operations-metrics"><div><span>备份任务</span><strong>${task.exists&&task.enabled?esc(task.state||'已启用'):'未就绪'}</strong></div><div><span>最近备份</span><strong>${esc(latest.created_at_utc||'暂无')}</strong></div><div><span>备份时效</span><strong>${latest.age_hours===undefined||latest.age_hours===null?'暂无':`${esc(latest.age_hours)}小时`}</strong></div><div><span>完整性</span><strong>${latest.quick_check==='ok'&&latest.sha256_verified?'已核验':'待核验'}</strong></div></div><p class="field-hint">外部通知：${alert.configured?'已配置':'未配置'}${alert.attempted?`；最近投递${alert.delivered?'成功':'失败'}`:''}</p>${issues.length?`<ul class="operations-issues">${issues.map(issue=>`<li><strong>${esc(issue.code)}</strong>${issue.message?`：${esc(issue.message)}`:''}</li>`).join('')}</ul>`:''}</section>`;
}

async function renderOperations(){
  const request=beginViewRequest();
  const data=await api('/api/admin/operations-health'),closures=data.month_closures||[],governance=data.governance||{};
  if(!request.isCurrent())return;
  if(!request.write(`<div class="section-gap">${operationsBackupHtml(data.backup||{})}<section class="panel"><div class="operations-heading"><div><h2>运营概览</h2><p>${esc(data.month)} 当前状态</p></div></div><div class="operations-metrics"><div><span>未处理系统告警</span><strong>${Number(data.open_system_alerts||0)}</strong></div><div><span>待确认跨圈调动</span><strong>${Number(data.pending_circle_transfers||0)}</strong></div><div><span>已关闭月结</span><strong>${closures.filter(row=>row.is_closed).length} / ${closures.length}</strong></div></div><div class="operations-closures">${closures.map(row=>`<div><strong>${esc(row.attraction_name)}</strong><span class="badge ${row.is_closed?'ok':'warn'}">${row.is_closed?'已月结':'未月结'}</span><small>${row.is_closed?`关闭人：${esc(row.closed_by_name||'系统')}`:'等待核对'}</small></div>`).join('')||'<span class="field-hint">暂无景点圈月结信息</span>'}</div></section><section class="panel"><div class="operations-heading"><div><h2>治理与留存复核</h2><p>查看签卡复核及附件留存情况。</p></div></div><div class="operations-metrics"><div><span>超过48小时待复核</span><strong>${Number(governance.overdue_recognition_reviews||0)}</strong><button type="button" class="secondary" data-operations-review aria-expanded="false">查看签卡明细</button></div><div><span>附件留存待复核</span><strong>${Number(governance.retention_review_files||0)}</strong></div></div><div data-operations-review-detail hidden></div></section></div>`))return;
  const opener=app.querySelector('[data-operations-review]');
  const host=app.querySelector('[data-operations-review-detail]');
  const close=()=>{host.hidden=true;host.replaceChildren();opener.setAttribute('aria-expanded','false');};
  opener.onclick=async()=>{
    if(!host.hidden){close();return;}
    opener.disabled=true;
    try{
      const detail=await api('/api/action-center/overdue_review/details');
      if(!request.isCurrent())return;
      host.innerHTML=actionCenterDetailHtml(detail);
      host.hidden=false;
      opener.setAttribute('aria-expanded','true');
      host.querySelector('[data-close-action-detail]').onclick=()=>{close();opener.focus();};
    }catch(error){if(request.isCurrent())toast(error.message,true);}
    finally{opener.disabled=false;}
  };
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
