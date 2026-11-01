// Read-only preview uses the product's existing personal entry points.
import { showAppearanceSettings } from '/backend/app/static/js/app/appearance.js';
import { bindDialogLayer } from '/backend/app/static/js/app/dialogs.js';
import { mountDailyWish } from '/backend/app/static/js/app/daily-wish.js';

const query=new URLSearchParams(location.search);
if(query.has('skin'))window.FZAppearance.set(query.get('skin'));
const circles=['heat','dwarf','bear','all'],circleSelect=document.querySelector('[data-preview-circle]');
if(circles.includes(query.get('circle')))document.body.dataset.circleTheme=query.get('circle');
if(circleSelect){
  circleSelect.value=document.body.dataset.circleTheme||'all';
  circleSelect.onchange=()=>{document.body.dataset.circleTheme=circleSelect.value;window.FZAppearance.set('circle');};
}
let drawerLayer;
const frame=document.querySelector('.app-frame');
mountDailyWish(document.getElementById('themeStory'),'preview-'+(document.getElementById('userBadge')?.dataset.employeeNo||'demo'),{integrated:true});
frame.addEventListener('click',event=>{
  event.preventDefault();event.stopImmediatePropagation();
  const personal=event.target.closest('[data-avatar-settings],[data-appearance-settings]');
  if(personal){drawerLayer?.close();document.getElementById('appearanceBtn')?.focus({preventScroll:true});showAppearanceSettings();const logout=document.querySelector('[data-appearance-logout]');if(logout)logout.disabled=true;return;}
  if(event.target.closest('[data-open-more]')){
    const drawer=frame.querySelector('.mobile-more-drawer');if(!drawer||!drawer.hidden)return;drawer.hidden=false;
    drawerLayer=bindDialogLayer(drawer,{remove:false,root:drawer.querySelector('.mobile-more-drawer-panel'),initialFocus:drawer.querySelector('[data-close-more]'),inertRoots:[document.querySelector('#app'),frame.querySelector('.tabs-mobile'),frame.querySelector('.tabs-desktop')].filter(Boolean),onClose:()=>{drawer.hidden=true;drawerLayer=null;}});
  }
  if(event.target.closest('[data-close-more]'))drawerLayer?.close();
},true);
frame.addEventListener('submit',event=>event.preventDefault(),true);
