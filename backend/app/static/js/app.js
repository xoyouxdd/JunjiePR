// Native ES module composition. Business pages share explicit modules and never depend on this entry.
import { renderAbsence, renderLoa, renderSickLeaveImport } from './app/absence.js';
import { renderAccountReset, renderCircleHrAccounts, renderPasswordChangeRequired, renderPasswordPage } from './app/accounts.js';
import { showReleaseAnnouncement } from './app/announcement.js';
import { api } from './app/api.js';
import { logout } from './app/auth.js';
import { configureViews, render } from './app/context.js';
import { renderDeclarationStatistics } from './app/declarations.js';
import { renderEntries } from './app/entries.js';
import { portalPath } from './app/format.js';
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
import { renderRotationTest } from './app/rotation.js';
import { applyCircleTheme, installPageBindHint, installScreenWatermark, installSecurityEvents } from './app/security.js';
import { state } from './app/state.js';
import { renderStatistics, renderStatisticsDetail, statisticsDetailContext } from './app/statistics.js';
import { toast } from './app/toast.js';

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
});

installSecurityEvents();
document.getElementById('logoutBtn').onclick=logout;

async function initializeApp(){
  try{
    state.me=await api('/api/me');
    if(state.me.must_change_password){renderPasswordChangeRequired();return;}
    state.options=await api('/api/options');
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
    if(!location.pathname.includes('/login'))location.href=portalPath('/login');
  }
}

void initializeApp();
