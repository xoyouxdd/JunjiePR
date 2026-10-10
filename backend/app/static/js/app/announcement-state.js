import { state } from './state.js';

const announcementWorkspaces=new Map();
let announcementBadgeTimer=null;

function startAnnouncementNotifications(refreshBadge){
  if(announcementBadgeTimer)return;
  const refresh=()=>{if(document.visibilityState==='visible'&&state.me&&!state.me.must_change_password)void refreshBadge();};
  announcementBadgeTimer=setInterval(refresh,60000);
  document.addEventListener('visibilitychange',refresh);
}

function setAnnouncementBadge(counts){
  if(!state.me)return;
  state.me.announcement_counts=counts;
  document.querySelectorAll('[data-announcement-badge]').forEach(el=>{el.textContent=counts.pending>99?'99+':String(counts.pending);el.hidden=!counts.pending;});
  document.querySelectorAll('[data-ann-pending]').forEach(el=>el.textContent=counts.pending);
  document.querySelectorAll('[data-ann-messages]').forEach(el=>el.textContent=counts.messages);
}


export { announcementWorkspaces, setAnnouncementBadge, startAnnouncementNotifications };
