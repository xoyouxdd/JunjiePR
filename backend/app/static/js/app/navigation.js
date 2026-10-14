// Role-aware navigation, identity display and action badges. Pages are composed through context.render().
import { api } from './api.js';
import { setAnnouncementBadge, startAnnouncementNotifications } from './announcement-state.js';
import { logout } from './auth.js';
import { render } from './context.js';
import { bindDialogLayer, leaveLayer } from './dialogs.js';
import { esc } from './format.js';
import { has, state, tabs } from './state.js';

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
  items.splice(Math.min(items.length,1),0,['announcements','公告中心']);
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
  if(available.has('announcements')&&!ids.includes('announcements'))ids.splice(1,0,'announcements');
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
  {id:'work',label:'工作',ids:['announcements','home','review','supervisorReview','register','absence','members','entries']},
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
    announcements:'<path d="M4 10v5h4l9 4V6L8 10H4zM8 15l2 5M20 9v7"/>',
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
  const employeeId=state.me.id;
  const isUpgradeReviewer=['TA_GSM','GSM'].includes(state.me.role_code);
  try{
    const [data,upgradeData]=await Promise.all([
      api('/api/action-center'),
      isUpgradeReviewer?api('/api/deduction-upgrades/pending'):Promise.resolve({items:[]}),
    ]);
    if(state.me?.id!==employeeId)return;
    setActionBadge(Number(data.total||0)+(upgradeData.items||[]).length);
    if(data.announcement_counts)setAnnouncementBadge(data.announcement_counts);
  }catch(_){/* A badge refresh must never interrupt the existing page workflow. */}
}

function renderTabs() {
  startAnnouncementNotifications(refreshActionBadge);
  const items=menuItems();
  if (!state.tab || (!items.some(i=>i[0]===state.tab) && state.tab!=='statisticsDetail')) state.tab=items[0]?.[0];
  const tabButton=([id,name],extraClass='')=>`<button type="button" data-tab="${id}" class="${isTabActive(id)?'active':''} ${extraClass}" ${isTabActive(id)?'aria-current="page"':''} title="${esc(name)}" aria-label="${esc(name)}">${navIcon(id)}<span class="nav-label">${esc(name)}</span>${id==='announcements'?`<b class="nav-count-badge" data-announcement-badge ${(state.me.announcement_counts?.pending||0)?'':'hidden'}>${(state.me.announcement_counts?.pending||0)>99?'99+':(state.me.announcement_counts?.pending||0)}</b>`:id==='actionCenter'?`<b class="nav-count-badge" data-action-center-badge ${state.actionBadgeTotal?'':'hidden'}>${state.actionBadgeTotal>99?'99+':state.actionBadgeTotal}</b>`:''}</button>`;
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

function gotoTab(tab){state.tab=tab;renderTabs();render();}

export { gotoTab, menuItems, mobilePrimaryIds, refreshActionBadge, renderTabs, renderUserBadge, setActionBadge };
