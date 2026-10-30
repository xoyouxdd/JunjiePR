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
  return 'avatar-frame-'+(['autumn','ocean','forest','winter','thumb','sun','spark','neon','cosmic'].includes(frame)?frame:'plain');
}
function profilePortrait(me,key=me.avatar_key,frame=me.avatar_frame){
  return `<span class="employee-avatar-portrait ${frameClass(frame)}">${validKey(key)?`<img src="${esc(thumb(key))}" alt="${esc(me.name)}的头像" width="64" height="64">`:`<span aria-hidden="true">${esc((me.name||'我').slice(0,1))}</span>`}</span>`;
}
function mountAvatarWardrobe(host,{isCurrent=()=>true}={}){
  let disposed=false,catalog=null,preferences=null,draft='',draftFrame='plain',group='',page=0,busy=false,token=0;
  let wardrobe={favorites:[],recent:[],presets:[null,null,null]},filter='all',activePreset=0,frameOpen=false,presetOpen=false,themeOpen=false,wardrobeQueue=Promise.resolve();
  let activePane='avatars';
  const owner=state.me.id,valid=()=>!disposed&&host.isConnected&&isCurrent()&&state.me.id===owner;
  host.innerHTML=`<div class="employee-avatar-profile"><div data-avatar-profile>${profilePortrait(state.me)}</div><div><strong>${esc(state.me.name)}</strong><p class="field-hint">个人形象 · 由你选择</p></div><button type="button" class="secondary" data-avatar-open aria-expanded="false">装扮头像</button></div><section class="avatar-wardrobe" aria-label="头像与边框" hidden></section>`;
  const editor=host.querySelector('.avatar-wardrobe'),open=host.querySelector('[data-avatar-open]');
  const changed=event=>{if(valid()&&event.detail?.owner===owner)host.querySelector('[data-avatar-profile]').innerHTML=profilePortrait(state.me);};
  window.addEventListener('app:avatar-changed',changed);
  const close=()=>{++token;editor.hidden=true;editor.innerHTML='';open.setAttribute('aria-expanded','false');open.focus({preventScroll:true});};
  function paint(){
    if(!valid())return;
    const groups=[...new Set(catalog.avatars.map(a=>a.group))];
    const items=filter==='favorites'?catalog.avatars.filter(a=>wardrobe.favorites.includes(a.key)):filter==='recent'?wardrobe.recent.map(k=>catalog.avatars.find(a=>a.key===k)).filter(Boolean):group==='全部头像'?catalog.avatars:catalog.avatars.filter(a=>a.group===group),pages=Math.max(1,Math.ceil(items.length/8));page=Math.min(page,pages-1);
    const chosen=catalog.avatars.find(a=>a.key===draft);
    const frameLabel=preferences.frames.find(f=>f.key===draftFrame)?.label||'简洁';
    const presetName=i=>wardrobe.presets[i]?.name||['日常','森林','冬日'][i];
    const calendar=Object.fromEntries(new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Shanghai',year:'numeric',month:'numeric',day:'numeric'}).formatToParts(new Date()).map(p=>[p.type,p.value]));
    const week=Math.floor((Date.UTC(Number(calendar.year),Number(calendar.month)-1,Number(calendar.day))-Date.UTC(1970,0,5))/604800000);
    const inspirations=[{key:'base-01-02',frame:'autumn',name:'暖暖营业中'},{key:'character-white-rabbit',frame:'winter',name:'森林里的清晨'},{key:'base-01-01',frame:'thumb',name:'给自己点个赞'},{key:'character-owl',frame:'cosmic',name:'星光小伙伴'}];
    const weekly=inspirations[(week%inspirations.length+inspirations.length)%inspirations.length];
    editor.innerHTML=`<div class="avatar-wardrobe-heading"><h3>选择你的形象</h3><button type="button" class="secondary" data-avatar-cancel>取消</button></div>
      <details class="avatar-compact" data-presets ${presetOpen?'open':''}><summary>我的三套搭配 <span>按需展开</span></summary><div class="avatar-preset-list">${wardrobe.presets.map((p,i)=>`<button type="button" class="secondary" data-preset="${i}" aria-pressed="${i===activePreset}">${['日常','森林','冬日'][i]} · ${esc(catalog.avatars.find(a=>a.key===p?.avatar_key)?.name||'空搭配')}</button>`).join('')}</div><button type="button" class="secondary" data-preset-store>将当前预览存入选中搭配</button></details>
      <details class="avatar-compact" data-themes ${themeOpen?'open':''}><summary>主题套装 <span>展开选择</span></summary><div class="avatar-preset-list"><button type="button" class="secondary" data-theme="daily">动物城日常</button><button type="button" class="secondary" data-theme="winter">冬日森林</button></div></details>
      <div class="avatar-filters"><button type="button" class="secondary" data-filter="all" aria-pressed="${filter==='all'}">全部</button><button type="button" class="secondary" data-filter="favorites" aria-pressed="${filter==='favorites'}">收藏 ${wardrobe.favorites.length}</button><button type="button" class="secondary" data-filter="recent" aria-pressed="${filter==='recent'}">最近</button><button type="button" class="secondary" data-avatar-random ${items.length<2?'disabled':''}>帮我选一个</button></div>
      <label class="avatar-theme-label">头像主题<select data-avatar-group-select ${filter!=='all'?'disabled':''}>${['全部头像',...groups].map(name=>`<option value="${esc(name)}" ${name===group?'selected':''}>${esc(name)}</option>`).join('')}</select></label>
      <p class="field-hint">${esc(filter==='favorites'?'我的收藏':filter==='recent'?'最近保存':group)} · ${items.length}款</p><div class="avatar-picker-grid">${items.slice(page*8,page*8+8).map(a=>`<div class="avatar-tile"><button type="button" class="avatar-picker-item" data-avatar-choice="${esc(a.key)}" aria-pressed="${draft===a.key}"><img src="${esc(thumb(a.key,160))}" width="160" height="160" alt="" loading="lazy" decoding="async"><span>${esc(a.name)}</span></button><button type="button" class="avatar-favorite" data-favorite="${esc(a.key)}" aria-label="${wardrobe.favorites.includes(a.key)?'取消收藏':'收藏'}${esc(a.name)}" aria-pressed="${wardrobe.favorites.includes(a.key)}">${wardrobe.favorites.includes(a.key)?'★':'☆'}</button></div>`).join('')}</div>${items.length?'':'<p class="field-hint">暂无头像，选择其他分类试试。</p>'}<div class="avatar-picker-pages"><button type="button" class="secondary" data-avatar-prev ${page===0?'disabled':''}>上一页</button><span>${page+1} / ${pages}</span><button type="button" class="secondary" data-avatar-next ${page===pages-1?'disabled':''}>下一页</button></div><div class="avatar-draft">${profilePortrait(state.me,draft,draftFrame)}<span>${esc(chosen?.name||'姓名首字')} · ${esc(frameLabel)} · 预览</span></div>
      <details class="avatar-compact" data-frames ${frameOpen?'open':''}><summary>选择边框 <span>${esc(frameLabel)} · 展开更换</span></summary><div class="avatar-frame-options" aria-label="选择边框">${preferences.frames.map(f=>`<button type="button" class="secondary avatar-frame-option" data-avatar-frame="${esc(f.key)}" aria-pressed="${draftFrame===f.key}">${profilePortrait(state.me,draft,f.key)}<span>${esc(f.label)}</span></button>`).join('')}</div></details>
      <p class="field-hint">历史月度边框仅在个人页面装扮中展示，不代表当前段位或绩效。组员列表和组织树只显示头像。</p><div class="actions"><button type="button" class="secondary" data-avatar-clear>使用姓名首字</button><button type="button" class="primary" data-avatar-save>保存装扮</button></div><p data-avatar-feedback role="status" aria-live="polite"></p>`;
    const panes=document.createElement('div');panes.className='avatar-pane-stack';
    const tabBar=document.createElement('div');tabBar.className='avatar-editor-tabs';tabBar.setAttribute('role','tablist');tabBar.setAttribute('aria-label','装扮分类');
    const labels={avatars:'头像',frames:'边框',outfits:'搭配'};
    for(const [key,label] of Object.entries(labels)){
      const button=document.createElement('button');button.type='button';button.className='secondary';button.dataset.avatarTab=key;button.id=`avatar-tab-${owner}-${key}`;button.setAttribute('role','tab');button.setAttribute('aria-selected',String(activePane===key));button.setAttribute('aria-controls',`avatar-pane-${owner}-${key}`);button.tabIndex=activePane===key?0:-1;button.textContent=label;tabBar.append(button);
      const pane=document.createElement('div');pane.id=`avatar-pane-${owner}-${key}`;pane.dataset.avatarPane=key;pane.setAttribute('role','tabpanel');pane.setAttribute('aria-labelledby',button.id);pane.hidden=activePane!==key;panes.append(pane);
    }
    const avatarPane=panes.querySelector('[data-avatar-pane=avatars]');let node=editor.querySelector('.avatar-filters');while(node&&!(node.nodeType===1&&node.classList.contains('avatar-draft'))){const next=node.nextSibling;avatarPane.append(node);node=next;}
    for(const selector of ['[data-presets]','[data-themes]']){const detail=editor.querySelector(selector);detail.open=true;panes.querySelector('[data-avatar-pane=outfits]').append(detail);}
    const frames=editor.querySelector('[data-frames]');frames.open=true;panes.querySelector('[data-avatar-pane=frames]').append(frames);
    editor.querySelector('.avatar-wardrobe-heading').after(tabBar,panes);
    const outfits=panes.querySelector('[data-avatar-pane=outfits]');
    const inspiration=document.createElement('section');inspiration.className='avatar-compact avatar-weekly';inspiration.innerHTML=`<h4>本周穿搭灵感</h4><div class="avatar-weekly-preview">${profilePortrait(state.me,weekly.key,weekly.frame)}<div><strong>${esc(weekly.name)}</strong><p class="field-hint">${esc(catalog.avatars.find(a=>a.key===weekly.key)?.name||'')} ＋ ${esc(preferences.frames.find(f=>f.key===weekly.frame)?.label||'')}</p></div></div><button type="button" class="secondary" data-weekly-try>试穿这套</button><p class="field-hint">每周更新推荐，自由选择，保存后生效。</p>`;outfits.prepend(inspiration);
    editor.querySelectorAll('[data-preset]').forEach(b=>{const i=Number(b.dataset.preset);b.textContent=presetName(i)+' · '+(catalog.avatars.find(a=>a.key===wardrobe.presets[i]?.avatar_key)?.name||'空搭配');});
    const rename=document.createElement('div');rename.className='avatar-preset-rename';rename.innerHTML=`<label>搭配名称<input data-preset-name maxlength="12" value="${esc(presetName(activePreset))}" aria-label="搭配名称"></label><button type="button" class="secondary" data-preset-rename>保存名称</button><p class="field-hint">最多12个字，仅自己可见。</p>`;editor.querySelector('[data-preset-store]').after(rename);
    const switchPane=key=>{activePane=key;tabBar.querySelectorAll('button').forEach(b=>{const selected=b.dataset.avatarTab===key;b.setAttribute('aria-selected',String(selected));b.tabIndex=selected?0:-1;});panes.querySelectorAll('[data-avatar-pane]').forEach(p=>p.hidden=p.dataset.avatarPane!==key);};
    tabBar.querySelectorAll('button').forEach(b=>{b.onclick=()=>switchPane(b.dataset.avatarTab);b.onkeydown=e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const keys=Object.keys(labels),index=keys.indexOf(activePane),key=e.key==='Home'?keys[0]:e.key==='End'?keys[2]:keys[(index+(e.key==='ArrowRight'?1:2))%3];switchPane(key);tabBar.querySelector(`[data-avatar-tab=${key}]`).focus();};});
    editor.querySelector('[data-avatar-group-select]').onchange=event=>{group=event.target.value;page=0;paint();editor.querySelector('[data-avatar-group-select]').focus({preventScroll:true});};
    editor.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{filter=b.dataset.filter;page=0;paint();});
    editor.querySelectorAll('[data-favorite]').forEach(b=>b.onclick=()=>{const key=b.dataset.favorite;wardrobe.favorites=wardrobe.favorites.includes(key)?wardrobe.favorites.filter(k=>k!==key):[...wardrobe.favorites,key];paint();persistWardrobe();});
    editor.querySelectorAll('[data-preset]').forEach(b=>b.onclick=()=>{activePreset=Number(b.dataset.preset);const p=wardrobe.presets[activePreset];if(p&&preferences.frames.some(f=>f.key===p.frame)){draft=p.avatar_key;draftFrame=p.frame;}paint();});
    editor.querySelector('[data-preset-store]').onclick=()=>{if(!draft){editor.querySelector('[data-avatar-feedback]').textContent='请先选择头像';return;}wardrobe.presets[activePreset]={avatar_key:draft,frame:draftFrame,name:presetName(activePreset)};paint();persistWardrobe('搭配已保存');};
    editor.querySelector('[data-weekly-try]').onclick=()=>{draft=weekly.key;draftFrame=weekly.frame;paint();editor.querySelector('[data-avatar-feedback]').textContent='已试穿本周搭配，保存装扮后生效。';};
    editor.querySelector('[data-preset-rename]').onclick=()=>{const name=editor.querySelector('[data-preset-name]').value.trim();if(!name||Array.from(name).length>12){editor.querySelector('[data-avatar-feedback]').textContent='请填写1至12个字的搭配名称';return;}const pair=wardrobe.presets[activePreset]||(draft?{avatar_key:draft,frame:draftFrame}:null);if(!pair){editor.querySelector('[data-avatar-feedback]').textContent='请先选择头像并保存搭配';return;}wardrobe.presets[activePreset]={...pair,name};paint();persistWardrobe('搭配名称已保存');};
    editor.querySelectorAll('[data-theme]').forEach(b=>b.onclick=()=>{const winter=b.dataset.theme==='winter';draft=winter?'character-white-rabbit':'base-01-02';draftFrame=winter?'winter':'autumn';group=catalog.avatars.find(a=>a.key===draft)?.group||'全部头像';filter='all';page=0;paint();});
    editor.querySelector('[data-avatar-random]').onclick=()=>{const pool=items.filter(a=>a.key!==draft);if(pool.length){draft=pool[Math.floor(Math.random()*pool.length)].key;paint();}};
    editor.querySelectorAll('[data-avatar-choice]').forEach(b=>b.onclick=()=>{draft=b.dataset.avatarChoice;paint();});
    editor.querySelectorAll('[data-avatar-frame]').forEach(b=>b.onclick=()=>{draftFrame=b.dataset.avatarFrame;paint();});
    editor.querySelector('[data-avatar-cancel]').onclick=close;
    editor.querySelector('[data-avatar-clear]').onclick=()=>{draft='';paint();};
    editor.querySelector('[data-avatar-prev]').onclick=()=>{page--;paint();};
    editor.querySelector('[data-avatar-next]').onclick=()=>{page++;paint();};
    editor.querySelector('[data-avatar-save]').onclick=save;
  }
  function persistWardrobe(message=''){
    const snapshot=JSON.parse(JSON.stringify(wardrobe));
    wardrobeQueue=wardrobeQueue.catch(()=>{}).then(()=>{if(valid())return api('/api/self-avatar-wardrobe',json('PUT',snapshot));}).then(()=>{const feedback=editor.querySelector('[data-avatar-feedback]');if(message&&valid()&&feedback)feedback.textContent=message;}).catch(()=>{const feedback=editor.querySelector('[data-avatar-feedback]');if(valid()&&feedback)feedback.textContent='收藏或搭配保存失败，请重试。';throw new Error('wardrobe save failed');});
    wardrobeQueue.catch(()=>{});
  }
  async function save(){
    if(busy||!valid())return;busy=true;editor.querySelectorAll('button').forEach(b=>b.disabled=true);
    try{
      const result=await api('/api/self-avatar',json('PUT',{avatar_key:draft,frame:draftFrame}));
      if(state.me.id===owner){Object.assign(state.me,{avatar_key:result.avatar_key,avatar_frame:result.avatar_frame});window.dispatchEvent(new CustomEvent('app:avatar-changed',{detail:{owner}}));}
      if(!valid())return;
      host.querySelector('[data-avatar-profile]').innerHTML=profilePortrait(state.me);if(draft){wardrobe.recent=[draft,...wardrobe.recent.filter(k=>k!==draft)].slice(0,6);persistWardrobe();}close();
    }catch(error){if(valid()){editor.querySelector('[data-avatar-feedback]').textContent=error.message;editor.querySelectorAll('button').forEach(b=>b.disabled=false);}}
    finally{busy=false;}
  }
  async function load(){
    if(busy)return;
    if(!editor.hidden){close();return;}
    const current=++token;editor.hidden=false;open.setAttribute('aria-expanded','true');open.disabled=true;
    editor.innerHTML='<p role="status">正在读取头像装扮…</p>';
    try{
      await wardrobeQueue.catch(()=>{});
      const results=await Promise.all([catalog?Promise.resolve(catalog):api(assetRoot+'catalog.json'),api('/api/self-avatar'),api('/api/self-avatar-wardrobe')]);
      if(!valid()||current!==token)return;
      [catalog,preferences,wardrobe]=results;Object.assign(state.me,{avatar_key:preferences.avatar_key,avatar_frame:preferences.avatar_frame});host.querySelector('[data-avatar-profile]').innerHTML=profilePortrait(state.me);draft=preferences.avatar_key||'';draftFrame=preferences.frames.some(f=>f.key===preferences.avatar_frame)?preferences.avatar_frame:'plain';
      group=catalog.avatars.find(a=>a.key===draft)?.group||catalog.avatars[0].group;page=0;activePane='avatars';paint();
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
