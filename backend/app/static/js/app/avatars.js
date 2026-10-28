// Static thumbnails and account-backed cosmetics. No timers or per-person API reads.
import { api, json } from './api.js';
import { esc, portalPath } from './format.js';
import { state } from './state.js';
import { registerPageCleanup } from './context.js';
import { bindDialogLayer } from './dialogs.js';

const assetRoot='/static/images/avatars/';
const validKey=key=>/^[a-z0-9][a-z0-9-]{0,79}$/.test(key||'');
const thumb=(key,size=64)=>portalPath(assetRoot+key+'-'+size+'.webp');
function avatarMarkup(key,name,{deferred=false}={}){
  if(!validKey(key))return '';
  return `<img class="employee-avatar-small" ${deferred?'data-avatar-src':'src'}="${esc(thumb(key))}" width="32" height="32" alt="" loading="lazy" decoding="async">`;
}
function bindDirectoryAvatars(root){
  const load=()=>root.querySelectorAll('[data-avatar-src]').forEach(img=>{
    let node=img;
    while(node&&node!==root){if(node.hidden||(node.tagName==='DETAILS'&&!node.open))return;node=node.parentElement;}
    img.src=img.dataset.avatarSrc;delete img.dataset.avatarSrc;
  });
  root.addEventListener('toggle',load,true);load();
  return ()=>root.removeEventListener('toggle',load,true);
}
function frameClass(frame){
  if(/^medal:\d{4}-\d{2}:(0|10|20|30|40|50|60|70|80|90)$/.test(frame||''))return 'avatar-frame-medal avatar-tier-'+frame.split(':')[2];
  return 'avatar-frame-'+(['autumn','ocean','forest'].includes(frame)?frame:'plain');
}
function profilePortrait(me,key=me.avatar_key,frame=me.avatar_frame){
  return `<span class="employee-avatar-portrait ${frameClass(frame)}">${validKey(key)?`<img src="${esc(thumb(key))}" alt="${esc(me.name)}的头像" width="64" height="64">`:`<span aria-hidden="true">${esc((me.name||'我').slice(0,1))}</span>`}</span>`;
}
function mountAvatarWardrobe(host,{isCurrent=()=>true}={}){
  let disposed=false,catalog=null,preferences=null,draft='',draftFrame='plain',group='',page=0,busy=false,token=0;
  const owner=state.me.id,valid=()=>!disposed&&host.isConnected&&isCurrent()&&state.me.id===owner;
  host.innerHTML=`<div class="employee-avatar-profile"><div data-avatar-profile>${profilePortrait(state.me)}</div><div><strong>${esc(state.me.name)}</strong><p class="field-hint">个人形象 · 由你选择</p></div><button type="button" class="secondary" data-avatar-open aria-expanded="false">装扮头像</button></div><section class="avatar-wardrobe" aria-label="头像与边框" hidden></section>`;
  const editor=host.querySelector('.avatar-wardrobe'),open=host.querySelector('[data-avatar-open]');
  const changed=event=>{if(valid()&&event.detail?.owner===owner)host.querySelector('[data-avatar-profile]').innerHTML=profilePortrait(state.me);};
  window.addEventListener('app:avatar-changed',changed);
  const close=()=>{++token;editor.hidden=true;editor.innerHTML='';open.setAttribute('aria-expanded','false');open.focus({preventScroll:true});};
  function paint(){
    if(!valid())return;
    const groups=[...new Set(catalog.avatars.map(a=>a.group))];
    const items=catalog.avatars.filter(a=>a.group===group),pages=Math.max(1,Math.ceil(items.length/8));page=Math.min(page,pages-1);
    const chosen=catalog.avatars.find(a=>a.key===draft);
    editor.innerHTML=`<div class="avatar-wardrobe-heading"><h3>选择你的形象</h3><button type="button" class="secondary" data-avatar-cancel>取消</button></div><div class="avatar-group-options" aria-label="头像主题">${groups.map((name,i)=>`<button type="button" class="secondary" data-avatar-group="${i}" aria-pressed="${name===group}">${esc(name)}</button>`).join('')}</div><p class="field-hint">${esc(group)} · ${items.length}款</p><div class="avatar-picker-grid">${items.slice(page*8,page*8+8).map(a=>`<button type="button" class="avatar-picker-item" data-avatar-choice="${esc(a.key)}" aria-pressed="${draft===a.key}"><img src="${esc(thumb(a.key,160))}" width="160" height="160" alt="" loading="lazy" decoding="async"><span>${esc(a.name)}</span></button>`).join('')}</div><div class="avatar-picker-pages"><button type="button" class="secondary" data-avatar-prev ${page===0?'disabled':''}>上一页</button><span>${page+1} / ${pages}</span><button type="button" class="secondary" data-avatar-next ${page===pages-1?'disabled':''}>下一页</button></div><div class="avatar-draft">${profilePortrait(state.me,draft,draftFrame)}<span>${esc(chosen?.name||'姓名首字')} · 预览</span></div><div class="avatar-frame-options" aria-label="选择边框">${preferences.frames.map(f=>`<button type="button" class="secondary" data-avatar-frame="${esc(f.key)}" aria-pressed="${draftFrame===f.key}">${esc(f.label)}</button>`).join('')}</div><p class="field-hint">历史月度边框仅在个人页面装扮中展示，不代表当前段位或绩效。组员列表和组织树只显示头像。</p><div class="actions"><button type="button" class="secondary" data-avatar-clear>使用姓名首字</button><button type="button" class="primary" data-avatar-save>保存装扮</button></div><p data-avatar-feedback role="status" aria-live="polite"></p>`;
    const mobileGroup=document.createElement('label');mobileGroup.className='avatar-mobile-group';mobileGroup.innerHTML=`头像主题<select data-avatar-group-select>${groups.map((name,i)=>`<option value="${i}"${name===group?' selected':''}>${esc(name)}</option>`).join('')}</select>`;
    editor.querySelector('.avatar-group-options').before(mobileGroup);
    mobileGroup.querySelector('select').onchange=event=>{group=groups[Number(event.target.value)];page=0;paint();editor.querySelector('[data-avatar-group-select]').focus({preventScroll:true});};
    editor.querySelectorAll('[data-avatar-group]').forEach(b=>b.onclick=()=>{group=groups[Number(b.dataset.avatarGroup)];page=0;paint();});
    editor.querySelectorAll('[data-avatar-choice]').forEach(b=>b.onclick=()=>{draft=b.dataset.avatarChoice;paint();});
    editor.querySelectorAll('[data-avatar-frame]').forEach(b=>b.onclick=()=>{draftFrame=b.dataset.avatarFrame;paint();});
    editor.querySelector('[data-avatar-cancel]').onclick=close;
    editor.querySelector('[data-avatar-clear]').onclick=()=>{draft='';paint();};
    editor.querySelector('[data-avatar-prev]').onclick=()=>{page--;paint();};
    editor.querySelector('[data-avatar-next]').onclick=()=>{page++;paint();};
    editor.querySelector('[data-avatar-save]').onclick=save;
  }
  async function save(){
    if(busy||!valid())return;busy=true;editor.querySelectorAll('button').forEach(b=>b.disabled=true);
    try{
      const result=await api('/api/self-avatar',json('PUT',{avatar_key:draft,frame:draftFrame}));
      if(state.me.id===owner){Object.assign(state.me,{avatar_key:result.avatar_key,avatar_frame:result.avatar_frame});window.dispatchEvent(new CustomEvent('app:avatar-changed',{detail:{owner}}));}
      if(!valid())return;
      host.querySelector('[data-avatar-profile]').innerHTML=profilePortrait(state.me);preferences=result;close();
    }catch(error){if(valid()){editor.querySelector('[data-avatar-feedback]').textContent=error.message;editor.querySelectorAll('button').forEach(b=>b.disabled=false);}}
    finally{busy=false;}
  }
  async function load(){
    if(busy)return;
    if(!editor.hidden){close();return;}
    const current=++token;editor.hidden=false;open.setAttribute('aria-expanded','true');open.disabled=true;
    editor.innerHTML='<p role="status">正在读取头像装扮…</p>';
    try{
      const results=await Promise.all([catalog?Promise.resolve(catalog):api(assetRoot+'catalog.json'),api('/api/self-avatar')]);
      if(!valid()||current!==token)return;
      [catalog,preferences]=results;Object.assign(state.me,{avatar_key:preferences.avatar_key,avatar_frame:preferences.avatar_frame});host.querySelector('[data-avatar-profile]').innerHTML=profilePortrait(state.me);draft=preferences.avatar_key||'';draftFrame=preferences.frames.some(f=>f.key===preferences.avatar_frame)?preferences.avatar_frame:'plain';
      group=catalog.avatars.find(a=>a.key===draft)?.group||catalog.avatars[0].group;page=0;paint();
    }catch(error){if(valid()&&current===token){editor.innerHTML=`<p class="error" role="alert">${esc(error.message)}</p><button type="button" class="secondary" data-avatar-retry>重试</button><button type="button" class="secondary" data-avatar-cancel>取消</button>`;editor.querySelector('[data-avatar-retry]').onclick=()=>{editor.hidden=true;load();};editor.querySelector('[data-avatar-cancel]').onclick=close;}}
    finally{if(valid())open.disabled=false;}
  }
  open.onclick=load;
  return ()=>{disposed=true;++token;window.removeEventListener('app:avatar-changed',changed);open.onclick=null;editor.innerHTML='';};
}
function showAvatarSettings({onSaved}={}){
  if(document.querySelector('[data-avatar-dialog]'))return;
  const originalFocus=document.activeElement;
  const overlay=document.createElement('div');overlay.className='modal-overlay';
  overlay.innerHTML='<section class="modal-card avatar-settings-dialog member-home-panel" data-avatar-dialog role="dialog" aria-modal="true" aria-labelledby="avatarSettingsTitle"><div class="avatar-wardrobe-heading"><h2 id="avatarSettingsTitle">头像装扮</h2><button type="button" class="secondary" data-avatar-dialog-close>关闭</button></div><div data-avatar-settings-host></div></section>';
  document.body.append(overlay);
  const owner=state.me.id,host=overlay.querySelector('[data-avatar-settings-host]');
  const dispose=mountAvatarWardrobe(host,{isCurrent:()=>overlay.isConnected&&state.me.id===owner});
  const saved=event=>{if(event.detail?.owner===owner)onSaved?.();};window.addEventListener('app:avatar-changed',saved);
  const layer=bindDialogLayer(overlay,{initialFocus:overlay.querySelector('[data-avatar-dialog-close]'),onClose:()=>{dispose();window.removeEventListener('app:avatar-changed',saved);if(!originalFocus?.isConnected)document.querySelector('#userBadge [data-avatar-settings]')?.focus({preventScroll:true});}});
  overlay.querySelector('[data-avatar-dialog-close]').onclick=()=>layer.close();
  overlay.onclick=event=>{if(event.target===overlay)layer.close();};registerPageCleanup(()=>layer.close());
  host.querySelector('[data-avatar-open]').click();
}
export { avatarMarkup, bindDirectoryAvatars, mountAvatarWardrobe, showAvatarSettings };
