// Role-specific release-announcement read flow.
import { api, json } from './api.js';
import { bindDialogLayer } from './dialogs.js';
import { esc } from './format.js';
import { toast } from './toast.js';

async function showReleaseAnnouncement(){
  const data=await api('/api/changelog/announcement');
  const release=data.release;
  if(data.read||!release||!Array.isArray(release.items)||!release.items.length)return;
  const overlay=document.createElement('div');
  overlay.className='modal-overlay';
  overlay.innerHTML=`<section class="modal-card release-announcement" role="dialog" aria-modal="true" aria-labelledby="releaseAnnouncementTitle" tabindex="-1"><h3 id="releaseAnnouncementTitle">本次更新公告 · V${esc(release.version)}</h3><p class="field-hint">${release.content_version?`本次为修复补丁，沿用上次上线版本 V${esc(release.content_version)} 与你当前角色相关的公告内容。`:'以下是与你当前角色相关的更新内容，请阅读后关闭。'}</p><ul class="changelog-items">${release.items.map(item=>`<li><strong>${esc(item.summary)}</strong>${item.detail?`<p>${esc(item.detail)}</p>`:''}</li>`).join('')}</ul><div class="modal-actions"><button type="button" class="primary" data-announcement-close disabled>请阅读 5 秒</button></div></section>`;
  document.body.appendChild(overlay);
  const button=overlay.querySelector('[data-announcement-close]');
  let canClose=false,remaining=5,submitting=false;
  const timer=setInterval(()=>{remaining-=1;if(remaining>0){button.textContent=`请阅读 ${remaining} 秒`;}else{clearInterval(timer);button.disabled=false;button.textContent='我已阅读，关闭';}},1000);
  const layer=bindDialogLayer(overlay,{initialFocus:overlay.querySelector('.release-announcement'),allowClose:()=>canClose,onClose:()=>clearInterval(timer)});
  button.onclick=async()=>{if(remaining>0||submitting)return;submitting=true;button.disabled=true;try{await api('/api/changelog/announcement/read',json('POST',{version:release.version}));canClose=true;layer.close();}catch(error){toast(error.message||'阅读状态保存失败，请重试',true);button.disabled=false;submitting=false;}};
}

export { showReleaseAnnouncement };
