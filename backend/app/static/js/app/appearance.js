// Browser-only appearance preferences; business state and identity colors are independent.
import { bindDialogLayer } from './dialogs.js';
import { registerPageCleanup, unregisterPageCleanup } from './context.js';
import { esc, portalPath } from './format.js';
import { state } from './state.js';
import { logout } from './auth.js';

let artworkCatalog;
function loadArtworkCatalog(){
  if(!artworkCatalog)artworkCatalog=fetch(portalPath('/static/images/avatars/catalog.json')).then(response=>{if(!response.ok)throw new Error('插画目录暂时不可用');return response.json();}).then(data=>data.avatars.filter(item=>/^[a-z0-9][a-z0-9-]{0,79}$/.test(item.key))).catch(error=>{artworkCatalog=null;throw error;});
  return artworkCatalog;
}

function mountArtwork(host){
  let disposed=false,items=null,group='',page=0;
  host.innerHTML='<details class="appearance-basics appearance-artwork"><summary>页眉插画 <span>像换头像一样，选择喜欢的伙伴</span></summary><p class="field-hint">独立于皮肤和个人头像，即选即用。</p><div class="appearance-artwork-modes"><button type="button" class="secondary" data-artwork-choice="theme" aria-pressed="false">跟随主题</button><button type="button" class="secondary" data-artwork-choice="medal" aria-pressed="false">星光勋章</button></div><div data-artwork-catalog></div><p data-artwork-status role="status" aria-live="polite"></p></details>';
  const details=host.querySelector('details'),catalog=host.querySelector('[data-artwork-catalog]'),status=host.querySelector('[data-artwork-status]');
  const sync=()=>host.querySelectorAll('[data-artwork-choice]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.artworkChoice===window.FZAppearance.getArtwork())));
  const select=(value,name)=>{const saved=window.FZAppearance.setArtwork(value);status.textContent=`已使用${name}。${saved?'当前浏览器会记住你的选择。':'浏览器未允许保存，下次打开需重新选择。'}`;};
  host.addEventListener('click',event=>{const button=event.target.closest('[data-artwork-choice]');if(button&&host.contains(button))select(button.dataset.artworkChoice,button.dataset.artworkName||button.textContent.trim());});
  function paint(){
    const rows=group?items.filter(item=>item.group===group):items,pages=Math.max(1,Math.ceil(rows.length/8));page=Math.min(page,pages-1);
    catalog.innerHTML=`<label class="appearance-artwork-group">插画分类<select data-artwork-group><option value="">全部插画</option>${[...new Set(items.map(item=>item.group))].map(name=>`<option value="${esc(name)}" ${name===group?'selected':''}>${esc(name)}</option>`).join('')}</select></label><div class="appearance-artwork-grid">${rows.slice(page*8,page*8+8).map(item=>`<button type="button" class="appearance-artwork-item" data-artwork-choice="avatar:${esc(item.key)}" data-artwork-name="${esc(item.name)}" aria-pressed="false"><img src="${esc(portalPath('/static/images/avatars/'+item.key+'-160.webp'))}" width="160" height="160" alt="" loading="lazy" decoding="async"><span>${esc(item.name)}</span></button>`).join('')}</div><div class="appearance-artwork-pages"><button type="button" class="secondary" data-artwork-prev ${page===0?'disabled':''}>上一页</button><span>${page+1} / ${pages}</span><button type="button" class="secondary" data-artwork-next ${page===pages-1?'disabled':''}>下一页</button></div>`;
    catalog.querySelector('[data-artwork-group]').onchange=event=>{group=event.target.value;page=0;paint();};
    catalog.querySelector('[data-artwork-prev]').onclick=()=>{page--;paint();};catalog.querySelector('[data-artwork-next]').onclick=()=>{page++;paint();};sync();
  }
  async function load(){
    if(!details.open||items)return;
    catalog.innerHTML='<p role="status">正在读取插画…</p>';
    try{const rows=await loadArtworkCatalog();if(disposed)return;items=rows;paint();}
    catch(error){if(!disposed){catalog.innerHTML='<button type="button" class="secondary" data-artwork-retry>重新读取插画</button>';status.textContent=error.message;catalog.querySelector('[data-artwork-retry]').onclick=load;}}
  }
  details.addEventListener('toggle',load);window.addEventListener('app:artwork-changed',sync);sync();
  return ()=>{disposed=true;details.removeEventListener('toggle',load);window.removeEventListener('app:artwork-changed',sync);};
}

const SKINS = [
  {id:'medal',name:'月度勋章 · 星光礼赞',caption:'午夜蓝与香槟金，珍藏每一份用心。',collection:'HONORS COLLECTION'},
  {id:'circle',name:'景点圈 · 手绘漫游',caption:'跟随所属景点圈，走进伙伴的故事。',collection:'STORYBOOK COLLECTION'},
  {id:'blue',name:'经典蓝',caption:'清晰有序 · 熟悉的工作台'},
  {id:'green',name:'清新青绿',caption:'柔和清爽 · 从容处理日常'},
  {id:'ivory',name:'暖米白',caption:'温暖简洁 · 专注阅读与工作'}
];

function mountAppearance(host) {
  const card=s=>`<button type="button" class="appearance-option ${s.collection?'appearance-featured':''}" data-skin-choice="${s.id}" aria-pressed="false"><span class="appearance-preview appearance-preview-${s.id}" aria-hidden="true">${s.collection?`<span class="appearance-collection">${s.collection}</span><span class="appearance-art"></span><span class="appearance-preview-word">${s.id==='medal'?'每一份用心<br>都值得闪耀':'让日常<br>多一点奇想'}</span>`:'<span class="appearance-preview-top"></span><span class="appearance-preview-side"></span><span class="appearance-preview-content"><i></i><i></i><i></i></span>'}</span><span class="appearance-option-title">${s.name}<span class="appearance-check" aria-hidden="true">✓</span></span><span class="appearance-caption">${s.caption}</span></button>`;
  host.innerHTML=`<section class="appearance-section" aria-label="界面皮肤"><div class="appearance-heading"><div><h3>把喜欢的故事，带进工作台</h3><p>两款主题系列，开启不一样的日常。</p></div><span class="appearance-label">主题精选</span></div><div class="appearance-options appearance-feature-options" role="group" aria-label="主题系列">${SKINS.filter(s=>s.collection).map(card).join('')}</div><p class="appearance-theme-note">勋章主题为外观设计，实际月度勋章仍在个人页面查看。景点圈主题跟随你的所属景点圈。</p><details class="appearance-basics"><summary>基础浅色外观 <span>经典蓝 · 清新青绿 · 暖米白</span></summary><div class="appearance-options" role="group" aria-label="基础浅色外观">${SKINS.filter(s=>!s.collection).map(card).join('')}</div></details><div class="appearance-footer"><p data-skin-status role="status" aria-live="polite">仅保存在当前浏览器，不同设备可分别选择。</p><button type="button" class="appearance-reset" data-skin-reset>恢复经典蓝</button></div></section>`;
  const me=state.me||{},badge=document.getElementById('userBadge');
  const identity={name:me.name||badge?.dataset.employeeName||'',employeeNo:me.employee_no||badge?.dataset.employeeNo||'',role:me.role_label||me.role_name||badge?.dataset.roleLabel||'',circle:me.attraction_name||badge?.dataset.circleLabel||''};
  const summary=document.createElement('section');summary.className='appearance-identity';summary.setAttribute('aria-label','我的身份');
  summary.innerHTML=`<div><strong>${esc(identity.name)}</strong><p>${[['工号',identity.employeeNo],['身份',identity.role],['景点圈',identity.circle]].filter(([,value])=>value).map(([label,value])=>`<span>${label} ${esc(value)}</span>`).join('')}</p></div><button type="button" class="secondary" data-appearance-logout>退出登录</button><span class="appearance-logout-status" role="status" aria-live="polite"></span>`;
  host.prepend(summary);
  summary.querySelector('[data-appearance-logout]').onclick=async event=>{const button=event.currentTarget;button.disabled=true;try{await logout();}catch(error){summary.querySelector('[role=status]').textContent=error.message||'退出失败，请重试';button.disabled=false;}};
  const artworkHost=document.createElement('div');artworkHost.className='appearance-artwork-host';host.querySelector('.appearance-footer').before(artworkHost);
  const disposeArtwork=mountArtwork(artworkHost);
  host.querySelector('.appearance-basics').open=['blue','green','ivory'].includes(window.FZAppearance.get());
  const sync=()=>host.querySelectorAll('[data-skin-choice]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.skinChoice===window.FZAppearance.get())));
  const select=value=>{
    const persisted=window.FZAppearance.set(value);
    const name=SKINS.find(s=>s.id===window.FZAppearance.get()).name;
    host.querySelector('[data-skin-status]').textContent=persisted?`已使用${name}，当前浏览器会记住你的选择。`:`已使用${name}；浏览器未允许保存，下次打开需重新选择。`;
  };
  host.querySelectorAll('[data-skin-choice]').forEach(button=>button.onclick=()=>select(button.dataset.skinChoice));
  host.querySelector('[data-skin-reset]').onclick=()=>select('blue');
  window.addEventListener('app:skin-changed',sync);sync();
  return ()=>{disposeArtwork();window.removeEventListener('app:skin-changed',sync);};
}

function showAppearanceSettings() {
  if(document.querySelector('[data-appearance-dialog]'))return;
  document.getElementById('appearanceBtn')?.focus({preventScroll:true});
  const overlay=document.createElement('div');overlay.className='modal-overlay';
  overlay.innerHTML='<section class="modal-card appearance-dialog" data-appearance-dialog role="dialog" aria-modal="true" aria-labelledby="appearanceTitle"><div class="appearance-dialog-heading"><h2 id="appearanceTitle">个人外观</h2><button type="button" class="secondary" data-appearance-close>关闭</button></div><div data-appearance-host></div></section>';
  document.body.append(overlay);
  const dispose=mountAppearance(overlay.querySelector('[data-appearance-host]'));
  let pageCleanup;
  const layer=bindDialogLayer(overlay,{initialFocus:overlay.querySelector('[data-appearance-close]'),onClose:()=>{dispose();unregisterPageCleanup(pageCleanup);}});
  overlay.querySelector('[data-appearance-close]').onclick=()=>layer.close();
  overlay.onclick=event=>{if(event.target===overlay)layer.close();};
  pageCleanup=registerPageCleanup(()=>layer.close());
}

export { mountAppearance, showAppearanceSettings };
