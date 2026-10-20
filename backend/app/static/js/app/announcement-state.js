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
  for(const [selector,value] of [['[data-ann-pending]',counts.pending],['[data-ann-messages]',counts.messages]])document.querySelectorAll(selector).forEach(el=>{el.textContent=el.hasAttribute('data-ann-compact')&&value>99?'99+':value;el.title=String(value);});
  const status=document.querySelector('[data-ann-center-status]');
  if(status){if(counts.pending>0){const number=document.createElement('strong');number.dataset.annPending='';number.textContent=String(counts.pending);status.replaceChildren(number,' 条公告待查收');}else status.textContent='当前无待查收';}
}


export { announcementWorkspaces, setAnnouncementBadge, startAnnouncementNotifications };
