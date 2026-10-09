// Watermark, page identity binding and screenshot-key notices.
import { api } from './api.js';
import { noticeModal } from './dialogs.js';
import { circleTheme, esc } from './format.js';
import { state } from './state.js';

function applyCircleTheme(){document.body.dataset.circleTheme=circleTheme(state.me?.attraction_name);}

function installScreenWatermark(){let layer=document.getElementById('screenWatermark');if(!layer){layer=document.createElement('div');layer.id='screenWatermark';layer.className='screen-watermark';layer.setAttribute('aria-hidden','true');document.body.appendChild(layer)}const label=`内部资料 · ${state.me.employee_no}`;const count=window.matchMedia('(max-width: 760px)').matches?8:12;layer.innerHTML=Array.from({length:count},()=>`<span>${esc(label)}</span>`).join('')}

function installPageBindHint(){let el=document.getElementById('pageBindHint');if(!el){const badge=document.getElementById('userBadge');const host=document.querySelector('.app-identity');if(!badge&&!host)return;el=document.createElement('span');el.id='pageBindHint';if(badge)badge.after(el);else host.appendChild(el)}const now=new Date(),p=n=>String(n).padStart(2,'0');el.textContent=`${state.me.name}(${state.me.employee_no}) · ${p(now.getMonth()+1)}-${p(now.getDate())} ${p(now.getHours())}:${p(now.getMinutes())}`;el.hidden=false}

let screenshotNoticePending=false;

async function recordScreenshotKey(){if(screenshotNoticePending)return;screenshotNoticePending=true;try{const result=await api('/api/security/screenshot-event',{method:'POST'});await noticeModal('截图安全提醒',`<p>${esc(result.message)}</p>`)}catch(error){await noticeModal('截图安全提醒','<p>检测到截图按键，请勿分享页面数据。记录提交失败，请联系管理员。</p>')}finally{setTimeout(()=>{screenshotNoticePending=false},1200)}}

const handleScreenshotKey=event=>{if(event.key==='PrintScreen')recordScreenshotKey()};

function installSecurityEvents(){window.addEventListener('keydown',handleScreenshotKey);window.addEventListener('keyup',handleScreenshotKey);}

export { applyCircleTheme, installPageBindHint, installScreenWatermark, installSecurityEvents };
