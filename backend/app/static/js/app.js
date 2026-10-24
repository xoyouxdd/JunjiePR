// Native ES module composition. Business pages share explicit modules and never depend on this entry.
import { renderAbsence, renderLoa, renderSickLeaveImport } from './app/absence.js';
import { renderAccountReset, renderCircleHrAccounts, renderPasswordChangeRequired, renderPasswordPage } from './app/accounts.js';
import { showReleaseAnnouncement } from './app/announcement.js';
import { renderAnnouncements } from './app/announcements.js';
import { api } from './app/api.js';
import { logout } from './app/auth.js';
import { configureViews, render, beginViewRequest } from './app/context.js';
import { renderDeclarationStatistics } from './app/declarations.js';
import { renderEntries } from './app/entries.js';
import { startExportTasks } from './app/export-tasks.js';
import { esc } from './app/format.js';
import { renderHome } from './app/home.js';
import { renderCircleTransfers, renderHrGroups, renderHrScores, renderLogs, renderMonthClose } from './app/hr.js';
import { renderHrEmployees } from './app/hr-employees.js';
import { renderMembers } from './app/members.js';
import { renderHrMonthlyReport } from './app/monthly-report.js';
import { refreshActionBadge, renderTabs, renderUserBadge } from './app/navigation.js';
import { renderActionCenter, renderChangelog, renderOperations } from './app/operations.js';
import { renderPrRankings } from './app/rankings.js';
import { renderRegister } from './app/registration.js';
import { renderFrontlineReview, renderSupervisorReview, renderUpgradeReview } from './app/reviews.js';
import { renderRotationTest, mountRotationTasks } from './app/rotation.js';
import { applyCircleTheme, installPageBindHint, installScreenWatermark, installSecurityEvents } from './app/security.js';
import { app, state } from './app/state.js';
import { installFormProtection } from './app/submission.js';
import { renderStatistics, renderStatisticsDetail, statisticsDetailContext } from './app/statistics.js';
import { toast } from './app/toast.js';
import { renderVideoLearning } from './app/video-learning.js';

configureViews({
  home: renderHome,
  actionCenter: renderActionCenter,
  operations: renderOperations,
  register: renderRegister,
  absence: renderAbsence,
  entries: renderEntries,
  review: renderFrontlineReview,
  supervisorReview: renderSupervisorReview,
  upgradeReview: renderUpgradeReview,
  members: renderMembers,
  statistics: renderStatistics,
  statisticsDetail: renderStatisticsDetail,
  declarationStatistics: renderDeclarationStatistics,
  prRankings: renderPrRankings,
  password: renderPasswordPage,
  accountReset: renderAccountReset,
  circleHrAccounts: renderCircleHrAccounts,
  hrEmployees: renderHrEmployees,
  monthClose: renderMonthClose,
  circleTransfers: renderCircleTransfers,
  hrGroups: renderHrGroups,
  hrScores: renderHrScores,
  logs: renderLogs,
  changelog: renderChangelog,
  sickLeaveImport: renderSickLeaveImport,
  loa: renderLoa,
  hrMonthlyReport: renderHrMonthlyReport,
  rotationTest: renderRotationTest,
  announcements: renderAnnouncements,
  videoLearning: renderVideoLearning,
});

installSecurityEvents();
installFormProtection();
document.getElementById('logoutBtn').onclick=logout;

async function initializeApp(){
  try{
    state.me=await api('/api/me');
    if(state.me.rotation_temporary){
      document.getElementById('tabs').hidden=true;
      document.body.classList.add('rotation-personal-only');
      document.getElementById('userBadge').textContent=`${state.me.name} · ${state.me.attraction_name}`;
      applyCircleTheme();installPageBindHint();installScreenWatermark();
      app.innerHTML='<section class="panel"><h2>轮岗待办</h2><p class="field-hint">仅显示本人当日安排，请按实际到位情况确认。</p></section><div id="rotationPersonalTasks"></div><section class="empty" id="rotationPersonalEmpty" role="status">正在同步当日轮岗安排…</section>';
      mountRotationTasks(document.getElementById('rotationPersonalTasks'),beginViewRequest());
      return;
    }
    if(state.me.must_change_password){renderPasswordChangeRequired();return;}
    state.options=await api('/api/options');
    startExportTasks(state.me);
    if(new URLSearchParams(location.search).get('view')==='actionCenter')state.tab='actionCenter';
    if(statisticsDetailContext().get('employee_ids'))state.tab='statisticsDetail';
    applyCircleTheme();
    installScreenWatermark();
    installPageBindHint();
    renderUserBadge();
    renderTabs();
    void refreshActionBadge();
    await render();
    void showReleaseAnnouncement().catch(error=>toast(error.message||'更新公告加载失败',true));
  }catch(error){
    if(error.status===401)return;
    app.innerHTML=`<section class="panel"><h2>暂时无法连接系统</h2><p role="alert">${esc(error.message||'请检查网络后重试。')}</p><button type="button" class="primary" id="retryInitialize">重新连接</button></section>`;
    document.getElementById('retryInitialize').onclick=()=>{app.innerHTML='<section class="empty" role="status">正在重新连接…</section>';void initializeApp();};
  }
}

void initializeApp();
