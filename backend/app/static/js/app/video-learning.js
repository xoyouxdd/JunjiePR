// Video learning uses the shared shell, dialogs, API and page lifecycle.
import { api, json } from './api.js';
import { beginViewRequest, captureViewContext, clearPageResources, pageTimeout, registerPageCleanup } from './context.js';
import { bindDialogLayer, confirmModal, formModal } from './dialogs.js';
import { esc, portalPath } from './format.js';
import { navIcon } from './navigation.js';
import { app } from './state.js';
import { clearFormEdits, withSubmitLock } from './submission.js';
import { toast } from './toast.js';

const base='/api/video-learning';
const filters={keyword:'',circle:'',tab:'all',offset:0};
const labels={processing:'处理中',failed:'处理失败',ready:'待发布',published:'已发布',withdrawn:'已撤下'};
const size=n=>n>=1024**3?`${(n/1024**3).toFixed(1)} GB`:`${(n/1024**2).toFixed(1)} MB`;
const time=n=>`${String(Math.floor(n/60)).padStart(2,'0')}:${String(Math.floor(n%60)).padStart(2,'0')}`;
const get=id=>app.querySelector(id);
const notify=error=>{if(error?.name!=='AbortError')toast(error.message,true);};
let cacheRegistration=null;
async function enableVideoCache(){
  if(!window.isSecureContext||!('serviceWorker' in navigator)||!('indexedDB' in window))return false;
  try{
    if(!cacheRegistration)cacheRegistration=navigator.serviceWorker.register(portalPath('/api/video-learning/cache-worker.js'),{scope:portalPath('/')});
    const registration=await Promise.race([cacheRegistration,new Promise((_,reject)=>setTimeout(()=>reject(new Error('cache registration timeout')),2500))]);
    registration.active?.postMessage({type:'fz-video-claim'});
    await Promise.race([navigator.serviceWorker.ready,new Promise(resolve=>setTimeout(resolve,2000))]);
    if(!navigator.serviceWorker.controller)await new Promise(resolve=>{const changed=()=>{clearTimeout(timer);navigator.serviceWorker.removeEventListener('controllerchange',changed);resolve();};const timer=setTimeout(changed,1500);navigator.serviceWorker.addEventListener('controllerchange',changed);registration.active?.postMessage({type:'fz-video-claim'});});
    return Boolean(navigator.serviceWorker.controller);
  }catch(_){cacheRegistration=null;return false;}
}
async function cacheMessage(type){
  const worker=navigator.serviceWorker?.controller;if(!worker)return {unsupported:true};
  return new Promise(resolve=>{const c=new MessageChannel(),timer=setTimeout(()=>{c.port1.close();resolve({unsupported:true});},2500);c.port1.onmessage=e=>{clearTimeout(timer);c.port1.close();resolve(e.data);};worker.postMessage({type},[c.port2]);});
}

function failureView(ctx,error,retry){
  if(error?.name==='AbortError'||!ctx.write(`<section class="panel video-learning"><h2>视频学习</h2><p class="error" role="alert">${esc(error.message||'读取失败，请重试。')}</p><button type="button" class="primary" data-video-reload>重新加载</button></section>`))return;
  get('[data-video-reload]').onclick=()=>void retry().catch(notify);
}

async function renderVideoLearning(){
  const ctx=beginViewRequest();
  let options;
  try{options=await api(base+'/options');}catch(error){failureView(ctx,error,renderVideoLearning);return;}
  if(!ctx.isCurrent())return;
  await enableVideoCache();
  await showLibrary(options);
}

async function showLibrary(options){
  clearPageResources();
  const ctx=beginViewRequest();
  if(!options.can_upload&&!options.can_manage_history&&filters.tab==='mine')filters.tab='all';
  if(filters.circle&&!options.circles.some(c=>String(c.id)===filters.circle))filters.circle='';
  const query=new URLSearchParams({keyword:filters.keyword,offset:filters.offset,mine:filters.tab==='mine',continued:filters.tab==='continued'});
  if(filters.circle)query.set('attraction_id',filters.circle);
  let data;
  try{data=await api(base+'/videos?'+query);}catch(error){failureView(ctx,error,()=>showLibrary(options));return;}
  if(!ctx.write(`<section class="panel video-learning"><header class="video-heading"><div><h2>视频学习</h2><p class="field-hint">内部学习资料 · 按授权景点查看</p></div>${options.can_upload?'<button type="button" class="primary" data-video-upload>＋ 上传视频</button>':''}</header><div class="video-cache-tools"><span class="field-hint" data-video-cache-usage>缓存加速按需保存，观看始终需要联网</span><button type="button" class="secondary" data-video-cache-clear>清理设备缓存</button></div><nav class="video-tabs" aria-label="视频栏目">${[['all','全部视频'],['continued','继续观看'],...((options.can_upload||options.can_manage_history)?[['mine','我发布的']]:[])].map(([id,name])=>`<button type="button" class="${filters.tab===id?'video-tab-active':''}" data-video-tab="${id}" aria-pressed="${filters.tab===id}">${name}</button>`).join('')}</nav><form class="video-search" id="videoSearch"><label><span class="sr-only">搜索视频</span><input type="search" name="keyword" maxlength="120" value="${esc(filters.keyword)}" placeholder="搜索视频标题"></label><label><span class="sr-only">所属景点</span><select name="circle"><option value="">授权景点全部</option>${options.circles.map(c=>`<option value="${c.id}" ${String(c.id)===filters.circle?'selected':''}>${esc(c.name)}</option>`).join('')}</select></label><button class="secondary" type="submit">搜索</button></form><div class="video-section-heading"><h3>${filters.tab==='mine'?'我发布的视频':filters.tab==='continued'?'继续学习':'学习视频'}</h3><span class="field-hint">${data.total} 个视频</span></div><div class="video-grid">${data.items.map(row=>`<article class="video-card"><button type="button" class="video-card-open" data-video-id="${row.id}"><div class="video-thumb">${row.cover_url?`<img src="${esc(portalPath(row.cover_url))}" alt="" loading="lazy">`:`<div class="video-cover-placeholder">${navIcon('videoLearning')}<span>${esc(labels[row.status])}</span></div>`}<span class="video-duration">${row.duration?time(row.duration):labels[row.status]}</span></div><div class="video-card-copy"><h3>${esc(row.title)}</h3><p>${esc(row.audience_label||row.attraction_name)} · ${esc(row.published_at?.slice(0,10)||row.created_at.slice(0,10))}</p>${filters.tab==='mine'?`<p>${esc(labels[row.status])} · 版本${row.version}</p>`:row.position?`<p class="video-progress-label">已看至 ${time(row.position)}</p>`:''}</div></button></article>`).join('')}</div>${!data.items.length?`<div class="video-empty">${navIcon('videoLearning')}<h3>${filters.keyword?'没有找到匹配的视频':filters.tab==='continued'?'还没有观看记录':filters.tab==='mine'?'还没有上传视频':'当前暂无学习视频'}</h3><p class="field-hint">${filters.keyword?'换一个关键词或景点试试。':filters.tab==='continued'?'从全部视频开始，之后可在这里接着观看。':'视频发布后会显示在这里。'}</p></div>`:''}<div class="video-pagination">${filters.offset?'<button type="button" class="secondary" data-video-prev>上一页</button>':''}${data.next_offset!==null?'<button type="button" class="secondary" data-video-next>下一页</button>':''}</div></section>`))return;
  const cacheUsage=get('[data-video-cache-usage]');
  void cacheMessage('fz-video-stats').then(info=>{if(ctx.isCurrent())cacheUsage.textContent=info.unsupported?'当前使用正常在线播放，设备缓存不可用':`设备缓存 ${size(info.bytes)} / 300 MB · 7天未用清理 · 观看需联网`;});
  get('[data-video-cache-clear]').onclick=async()=>{const info=await cacheMessage('fz-video-clear');if(ctx.isCurrent()){cacheUsage.textContent=info.unsupported?'当前设备缓存不可用':'设备缓存已清理 · 观看需联网';}};
  get('#videoSearch').onsubmit=e=>{e.preventDefault();const f=new FormData(e.target);Object.assign(filters,{keyword:f.get('keyword'),circle:f.get('circle'),offset:0});void showLibrary(options).catch(notify);};
  app.querySelectorAll('[data-video-tab]').forEach(b=>b.onclick=()=>{filters.tab=b.dataset.videoTab;filters.offset=0;void showLibrary(options).catch(notify);});
  app.querySelectorAll('[data-video-id]').forEach(b=>b.onclick=()=>void showDetail(Number(b.dataset.videoId),options).catch(notify));
  get('[data-video-upload]')?.addEventListener('click',()=>showUpload(options));
  get('[data-video-prev]')?.addEventListener('click',()=>{filters.offset=Math.max(0,filters.offset-24);void showLibrary(options).catch(notify);});
  get('[data-video-next]')?.addEventListener('click',()=>{filters.offset=data.next_offset;void showLibrary(options).catch(notify);});
  if(data.items.some(r=>r.status==='processing')&&filters.tab==='mine')pageTimeout(()=>{if(ctx.isCurrent())void showLibrary(options).catch(notify);},5000);
}

function confidentialNotice(options){
  return new Promise(resolve=>{
    const overlay=document.createElement('div');overlay.className='modal-overlay';
    overlay.innerHTML=`<section class="modal-card video-notice" role="dialog" aria-modal="true" aria-labelledby="videoNoticeTitle"><div class="video-notice-icon">${navIcon('about')}</div><h3 id="videoNoticeTitle">内部学习，请妥善保管</h3><p>${esc(options.notice)}</p><p class="field-hint">水印用于保护与追溯，无法绝对阻止外部拍摄或后期处理。</p><label class="video-consent-label"><input type="checkbox" data-video-consent>我已阅读并知悉以上要求</label><div class="modal-actions"><button type="button" class="secondary" data-cancel>暂不观看</button><button type="button" class="primary" data-accept disabled>确认并观看</button></div></section>`;
    document.body.appendChild(overlay);
    const layer=bindDialogLayer(overlay,{onClose:v=>resolve(v===true),initialFocus:overlay.querySelector('[data-video-consent]')});
    overlay.querySelector('[data-video-consent]').onchange=e=>overlay.querySelector('[data-accept]').disabled=!e.target.checked;
    overlay.querySelector('[data-cancel]').onclick=()=>layer.close(false);
    overlay.querySelector('[data-accept]').onclick=()=>layer.close(true);
  });
}

async function showDetail(id,options){
  clearPageResources();
  const ctx=beginViewRequest();let row;
  try{row=await api(`${base}/videos/${id}`);}catch(error){failureView(ctx,error,()=>showDetail(id,options));return;}
  if(!ctx.write(`<section class="panel video-learning"><button type="button" class="secondary" data-video-back>‹ 视频学习</button><header class="video-detail-heading"><h2>${esc(row.title)}</h2><p class="field-hint">${esc(row.audience_label||row.attraction_name)} · ${esc(row.owner_name)} · ${labels[row.status]} · 版本${row.version}</p></header><div class="video-player-wrap"><video playsinline controls controlslist="nodownload nofullscreen noremoteplayback" disablepictureinpicture disableremoteplayback x-webkit-airplay="deny" preload="metadata" ${row.cover_url?`poster="${esc(portalPath(row.cover_url))}"`:''} aria-label="${esc(row.title)}"></video><div class="video-employee-watermark" aria-hidden="true" hidden><span></span><span></span></div><div class="video-player-gate"><p data-video-status role="status">${row.status==='published'||row.status==='ready'?'确认保密提示后观看内部学习视频':'视频尚未准备完成'}</p>${row.status==='published'||row.status==='ready'?'<button type="button" class="primary" data-video-start>开始观看</button>':''}</div></div><details class="video-play-settings"><summary>播放设置</summary><div class="video-settings-grid"><label>播放速度<select data-video-speed>${['0.5','0.75','1','1.25','1.5','2'].map(n=>`<option ${n==='1'?'selected':''} value="${n}">${n}×</option>`).join('')}</select></label><label>清晰度<select data-video-quality><option value="auto">自动</option><option value="720">720P上限</option><option value="1080">1080P上限</option></select></label><label>画面亮度 <output data-brightness-output>100%</output><input type="range" min="60" max="140" value="100" data-video-brightness></label><label>音量 <output data-volume-output>70%</output><input type="range" min="0" max="100" value="70" data-video-volume></label></div><p class="field-hint">自动模式按设备选择清晰度，不超过原片；iPhone音量可能需使用机身按键。亮度仅影响视频画面，系统全屏可能不支持。员工号显示于页面播放层，原生全屏、画中画和投屏暂不开放。</p></details><div class="video-description"><h3>本节内容</h3><p>${esc(row.description||'暂无补充说明。')}</p><p class="field-hint">观看进度仅用于继续学习，不作为完成培训或绩效评价依据。</p></div>${row.can_manage||row.can_delete?`<section class="video-management"><h3>视频管理</h3><p class="field-hint">原文件 ${size(row.original_bytes)}${Object.entries(row.profiles).map(([q,p])=>` · ${q}P上限 ${size(p.bytes)}（${p.width}×${p.height}）`).join('')}</p>${row.error?`<p role="alert" class="error">${esc(row.error)}</p>`:''}${['queued','processing'].includes(row.cover_status)?'<p class="field-hint" role="status">封面正在生成，完成后可发布。</p>':''}${row.status==='ready'&&row.publish_block_reason?`<p class="field-hint" role="status">${esc(row.publish_block_reason)}</p>`:''}${row.cover_error?`<p class="error" role="alert">${esc(row.cover_error)}，请重新调整封面。</p>`:''}<div class="video-manage-actions">${row.can_edit_audience&&row.status!=='withdrawn'?'<button class="secondary" data-video-audience>调整观看范围</button>':''}${row.status==='ready'?'<button class="secondary" data-video-edit>编辑信息</button><button class="secondary" data-video-cover>调整封面</button><button class="primary" data-video-publish>确认发布</button>':''}${row.status==='failed'?'<button class="primary" data-video-retry>重新处理</button>':''}${['ready','published'].includes(row.status)?'<button class="secondary" data-video-withdraw>撤下视频</button>':''}${row.can_delete?'<button class="secondary" data-video-delete>删除此版本</button>':''}${row.can_replace?'<button class="secondary" data-video-replace>上传新版本</button>':''}</div></section>`:''}</section>`))return;
  if(get('[data-video-publish]'))get('[data-video-publish]').disabled=!row.can_publish||['queued','processing','failed'].includes(row.cover_status);
  const video=get('video'),gate=get('.video-player-gate'),status=get('[data-video-status]');
  const owner=captureViewContext();let token=null,quality='auto',pollGeneration=0,lastSaved=-1,dead=false,started=false,startBusy=false,actionBusy=false,resumePosition=row.position||0,resumePlaying=false;
  const alive=()=>!dead&&owner.isCurrent()&&video.isConnected;
  registerPageCleanup(()=>{dead=true;++pollGeneration;video.pause();video.removeAttribute('src');video.load();});
  const setStatus=text=>{if(alive()){gate.hidden=false;status.textContent=text;}};
  const save=()=>{if(!alive()||!token||!started)return;const position=Math.floor(video.currentTime);if(position===lastSaved)return;lastSaved=position;void api(`${base}/videos/${id}/progress?token=${encodeURIComponent(token)}`,json('PUT',{position})).catch(error=>{if(alive())toast(error.message,true);});};
  const chooseQuality=()=>quality==='auto'?(window.innerWidth>=900?'1080':'720'):quality;
  async function preparePlayback(){
    const generation=++pollGeneration,q=chooseQuality();
    if(started){resumePosition=video.currentTime;resumePlaying=!video.paused;}
    const resume=resumePosition,wasPlaying=resumePlaying;
    started=false;video.pause();video.onloadedmetadata=null;video.removeAttribute('src');video.load();get('[data-video-start]').hidden=true;setStatus('正在准备共享视频；旧版本首次升级可能需要等待…');
    try{
      await api(`${base}/videos/${id}/playback?token=${encodeURIComponent(token)}&quality=${q}`,{method:'POST'});
      if(!alive()||generation!==pollGeneration)return;
      async function poll(){
        try{
          const result=await api(`${base}/videos/${id}/playback?token=${encodeURIComponent(token)}&quality=${q}`);
          if(!alive()||generation!==pollGeneration)return;
          if(result.status==='failed')throw new Error(result.error||'共享视频准备失败，请重试');
          if(result.status==='missing')throw new Error('播放准备任务已过期，请重新开始');
          if(!result.url){pageTimeout(poll,2000);return;}
          get('.video-employee-watermark').querySelectorAll('span').forEach(n=>n.textContent=result.employee_no);get('.video-employee-watermark').hidden=false;
          video.onloadedmetadata=()=>{if(!alive()||generation!==pollGeneration)return;video.currentTime=Math.min(resume||0,Math.max(0,video.duration-1));video.playbackRate=Number(get('[data-video-speed]').value);started=true;gate.hidden=true;if(wasPlaying)void video.play().catch(()=>{});};
          video.src=portalPath(result.url);video.load();
        }catch(error){if(generation===pollGeneration)failure(error);}
      }
      await poll();
    }catch(error){if(generation===pollGeneration)failure(error);}
  }
  function failure(error){if(!alive())return;if(started)resumePosition=video.currentTime;started=false;++pollGeneration;token=null;get('.video-employee-watermark').hidden=true;video.pause();video.onloadedmetadata=null;video.removeAttribute('src');video.load();setStatus(error.message||'播放中断，请重新确认后重试');get('[data-video-start]').hidden=false;}
  get('[data-video-start]')?.addEventListener('click',async()=>{
    if(startBusy||!alive())return;startBusy=true;const button=get('[data-video-start]');button.disabled=true;
    try{if(!await confidentialNotice(options)||!alive())return;const grant=await api(`${base}/videos/${id}/consent`,json('POST',{accepted:true,notice_version:options.notice_version}));if(!alive())return;token=grant.token;await preparePlayback();}catch(error){failure(error);}finally{startBusy=false;if(button.isConnected)button.disabled=false;}
  });
  video.onerror=()=>{if(alive()&&video.getAttribute('src'))failure(new Error('视频播放中断，请重新确认保密提示后重试。'));};
  video.ontimeupdate=()=>{if(Math.floor(video.currentTime)%10===0)save();};video.onpause=save;video.onended=save;
  let playAuthorized=false,verifying=false;
  async function verify(){
    if(!alive()||!token)throw new Error('请重新确认保密提示');
    const grant=await api(`${base}/videos/${id}/authorize?token=${encodeURIComponent(token)}&quality=${chooseQuality()}`);
    if(!alive())throw new Error('播放页面已关闭');
    if(grant.version!==row.version)throw new Error('视频版本已变化，请重新进入');
    return grant;
  }
  video.onplay=()=>{if(playAuthorized){playAuthorized=false;return;}video.pause();if(verifying)return;verifying=true;void verify().then(()=>{if(alive()&&started){playAuthorized=true;return video.play().catch(()=>{playAuthorized=false;});}}).catch(failure).finally(()=>verifying=false);};
  const visibility=()=>{if(document.hidden){resumePlaying=!video.paused;video.pause();}else if(started){void verify().then(()=>{if(alive()&&resumePlaying){playAuthorized=true;return video.play().catch(()=>{playAuthorized=false;});}}).catch(failure);}};
  const offline=()=>{void cacheMessage('fz-video-offline');failure(new Error('网络已断开，已暂停播放；恢复联网后请重新确认。'));};
  const online=()=>{void cacheMessage('fz-video-online');};
  document.addEventListener('visibilitychange',visibility);window.addEventListener('offline',offline);window.addEventListener('online',online);
  registerPageCleanup(()=>{document.removeEventListener('visibilitychange',visibility);window.removeEventListener('offline',offline);window.removeEventListener('online',online);});
  const heartbeat=()=>{if(!alive())return;if(started&&token)void verify().catch(failure);pageTimeout(heartbeat,60000);};pageTimeout(heartbeat,60000);
  const watermark=get('.video-employee-watermark');let watermarkStep=0;
  const moveMark=()=>{if(!alive())return;watermark.dataset.position=String(++watermarkStep%3);pageTimeout(moveMark,9000);};pageTimeout(moveMark,9000);
  const blockedMode=()=>{failure(new Error('此播放模式无法保留员工号水印，请回到页面内重新观看。'));if(document.pictureInPictureElement)void document.exitPictureInPicture?.().catch(()=>{});try{video.webkitExitFullscreen?.();}catch(_){}if(alive())toast('请在页面内观看，以保留员工号水印。',true);};
  video.addEventListener('webkitbeginfullscreen',blockedMode);video.addEventListener('enterpictureinpicture',blockedMode);
  if(video.remote)video.remote.onconnecting=blockedMode;
  const full=document.createElement('button');full.type='button';full.className='secondary';full.textContent='页面全屏';full.dataset.videoFullscreen='';
  if(document.fullscreenEnabled){get('.video-play-settings').append(full);full.onclick=()=>void get('.video-player-wrap').requestFullscreen().catch(notify);}
  video.preservesPitch=true;
  get('[data-video-speed]').onchange=e=>video.playbackRate=Number(e.target.value);
  get('[data-video-quality]').onchange=e=>{quality=e.target.value;if(token)void preparePlayback();};
  get('[data-video-brightness]').oninput=e=>{video.style.filter=`brightness(${e.target.value}%)`;get('[data-brightness-output]').textContent=e.target.value+'%';};
  const volumeControl=get('[data-video-volume]'),volumeOutput=get('[data-volume-output]');
  try{video.volume=.7;if(Math.abs(video.volume-.7)>.05)throw new Error('system volume');}
  catch(_){volumeControl.disabled=true;volumeOutput.textContent='使用机身按键';}
  volumeControl.oninput=e=>{try{video.volume=Number(e.target.value)/100;video.muted=video.volume===0;volumeOutput.textContent=Math.round(video.volume*100)+'%';}catch(_){volumeControl.disabled=true;volumeOutput.textContent='使用机身按键';}};
  get('[data-video-back]').onclick=()=>{save();dead=true;video.pause();video.removeAttribute('src');video.load();void showLibrary(options).catch(notify);};
  async function action(suffix,message){if(actionBusy||!alive())return;actionBusy=true;try{if(message&&!await confirmModal('确认操作',`<p>${esc(message)}</p>`))return;if(!alive())return;await api(`${base}/videos/${id}/${suffix}`,{method:'POST'});if(alive()){dead=true;video.pause();void showDetail(id,options).catch(notify);}}catch(error){if(alive())toast(error.message,true);}finally{actionBusy=false;}}
  get('[data-video-publish]')?.addEventListener('click',()=>void action('publish',`观看人员：${row.audience_label}。TR不包含CM，请核对人员范围，并预览画面、封面和声音后发布。`));
  get('[data-video-withdraw]')?.addEventListener('click',()=>void action('withdraw','撤下后其他人员不能再开始或继续请求该视频。'));
  get('[data-video-delete]')?.addEventListener('click',async()=>{
    if(actionBusy||!alive())return;actionBusy=true;
    try{
      if(!await confirmModal('删除此视频版本','<p>仅删除当前已撤下版本，不删除其他历史或替代版本。原片、播放文件、封面将永久清理，无法恢复；审计和访问记录保留。</p>','继续删除'))return;
      if(!await confirmModal('最后确认','<p>确认永久删除此版本？删除后无法恢复。</p>','永久删除'))return;
      if(!alive())return;await api(`${base}/videos/${id}`,{method:'DELETE'});
      await cacheMessage('fz-video-clear');if(alive()){dead=true;video.pause();toast('视频已删除，文件清理已排队');void showLibrary(options).catch(notify);}
    }catch(error){notify(error);}finally{actionBusy=false;}
  });
  get('[data-video-retry]')?.addEventListener('click',()=>void action('retry'));
  get('[data-video-replace]')?.addEventListener('click',()=>{dead=true;video.pause();video.removeAttribute('src');video.load();showUpload(options,row);});
  get('[data-video-cover]')?.addEventListener('click',()=>void formModal('调整封面','从视频中选择时间点，自动叠加标题。',`<label>封面画面（秒）<input type="number" name="second" min="0" max="${row.duration-1}" value="${row.cover_second}" required></label><label>封面标题<input name="title" maxlength="40" value="${esc(row.cover_title)}" required></label>`,'生成封面',{onSubmit:async form=>{const f=new FormData(form);await api(`${base}/videos/${id}/cover`,json('POST',{second:Number(f.get('second')),title:f.get('title')}));toast('封面已进入处理队列');if(alive()){dead=true;void showDetail(id,options).catch(notify);}}}));
  get('[data-video-audience]')?.addEventListener('click',()=>void formModal('调整观看范围','保存后按新范围校验访问；已缓冲画面无法追回。',audiencePicker({...options,...row.audience_editor},row),'保存观看范围',{onReady:form=>bindAudience(form),onSubmit:async form=>{const audience_codes=[...form.querySelectorAll('[name=audience]:checked')].map(n=>n.value);await api(`${base}/videos/${id}/audience`,json('PATCH',{audience_codes,revision:row.audience.revision}));toast('观看范围已更新');if(alive()){dead=true;void showDetail(id,options).catch(notify);}}}));
  get('[data-video-edit]')?.addEventListener('click',()=>void formModal('编辑视频信息','发布前可调整标题与说明。',`<label>完整标题<input name="title" maxlength="120" value="${esc(row.title)}" required></label><label>封面短标题<input name="cover_title" maxlength="40" value="${esc(row.cover_title)}"></label><label>内容说明<textarea name="description" maxlength="2000">${esc(row.description)}</textarea></label>`,'保存并生成封面',{onSubmit:async form=>{const f=new FormData(form);await api(`${base}/videos/${id}`,json('PATCH',{title:f.get('title'),cover_title:f.get('cover_title'),description:f.get('description'),attraction_ids:row.attraction_ids,replaces_id:row.replaces_id}));toast('已保存，封面正在更新');if(alive()){dead=true;void showDetail(id,options).catch(notify);}}}));
  if(row.status==='processing'||['queued','processing'].includes(row.cover_status)){const refresh=()=>{if(!alive())return;if(document.querySelector('.modal-overlay')||(started&&!video.paused)){pageTimeout(refresh,1000);return;}dead=true;void showDetail(id,options).catch(notify);};pageTimeout(refresh,5000);}
}

async function fingerprint(file,chunkSize,update){
  const hex=buffer=>[...new Uint8Array(buffer)].map(b=>b.toString(16).padStart(2,'0')).join('');
  if(!globalThis.crypto?.subtle)throw new Error('当前浏览器不支持安全视频上传，请使用HTTPS或本机localhost访问。');
  const hashes=[];
  for(let offset=0;offset<file.size;offset+=chunkSize){const blob=file.slice(offset,offset+chunkSize);const part=typeof blob.arrayBuffer==='function'?await blob.arrayBuffer():await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('视频读取失败，请重新选择本机视频'));reader.readAsArrayBuffer(blob);});hashes.push(hex(await crypto.subtle.digest('SHA-256',part)));if(update(Math.min(100,Math.round((offset+part.byteLength)/file.size*100)))===false)throw new Error('视频读取已停止');}
  return hex(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(hashes.join(''))));
}

function audiencePicker(options,replaces){
  const selected=replaces?.audience?.codes||(!options.can_select_multiple?['local_tr']:[]);
  return `<fieldset class="video-audience"><legend>观看人员</legend><p class="field-hint">${options.can_select_multiple?'可单选或多选，多个范围合并生效；TR不包含CM，“所有人”包含CM及各层级账号。':'仅本圈 TR 账号可看，CM不纳入；发布者可预览。'}${options.local_circle_name?' 本圈：'+esc(options.local_circle_name)+'。':''}</p>${options.can_select_multiple?'<button type="button" class="secondary" data-video-select-all aria-pressed="false">全选观看范围</button>':''}<div class="video-audience-options">${options.audience_options.map(item=>`<label><input type="checkbox" name="audience" value="${esc(item.code)}" ${selected.includes(item.code)?'checked':''}><span>${esc(item.label)}</span></label>`).join('')}</div><p class="field-hint" data-audience-summary aria-live="polite"></p></fieldset>`;
}

function bindAudience(form){
  const update=()=>{const boxes=[...form.querySelectorAll('[name=audience]')],count=boxes.filter(n=>n.checked).length,summary=form.querySelector('[data-audience-summary]'),all=form.querySelector('[data-video-select-all]');if(summary)summary.textContent=`已选 ${count} / ${boxes.length} 类观看人员`;if(all){all.setAttribute('aria-pressed',String(count===boxes.length&&count>0));all.textContent=count===boxes.length&&count>0?'取消全选':'全选观看范围';}};
  form.querySelectorAll('[name=audience]').forEach(n=>n.addEventListener('change',update));
  form.querySelector('[data-video-select-all]')?.addEventListener('click',()=>{if(form.dataset.submitting==='true')return;const boxes=[...form.querySelectorAll('[name=audience]')],checked=boxes.some(n=>!n.checked);boxes.forEach(n=>n.checked=checked);form.dispatchEvent(new Event('change',{bubbles:true}));update();});
  update();
}

function showUpload(options,replaces=null){
  clearPageResources();
  beginViewRequest();
  app.innerHTML=`<section class="panel video-learning"><button type="button" class="secondary" data-upload-back>‹ 视频学习</button><header class="video-detail-heading"><h2>${replaces?'上传新版本':'上传学习视频'}</h2><p class="field-hint">上传后由服务器自动压缩、均衡响度、生成封面；预览后再发布。</p></header>${!options.tools_ready?'<p class="error" role="alert">视频处理工具未配置，暂不能上传。</p>':''}<form id="videoUploadForm" class="form-stack"><label>视频标题<input name="title" maxlength="120" required value="${esc(replaces?.title||'')}" placeholder="例如：开场前的设备与环境检查"></label>${audiencePicker(options,replaces)}<label>封面短标题（选填）<input name="cover_title" maxlength="40" placeholder="默认使用视频标题前40字" value="${esc(replaces?.cover_title||'')}"></label><label>内容说明（选填）<textarea name="description" maxlength="2000">${esc(replaces?.description||'')}</textarea></label><label class="video-file-field">选择视频<input type="file" name="video" accept="video/*,.mp4,.mov,.m4v" required><span class="field-hint">MP4 / MOV / M4V · 最多500MB、20分钟；上传后自动压缩。苹果相册视频无法点选时，可先在相册下载原片，或保存到“文件”后使用下方入口。</span></label><button type="button" class="secondary" data-video-file-fallback>从文件选取（视频无法点选时）</button><div class="video-upload-status" role="status" aria-live="polite">上传后后台处理，不会直接向观看者开放原片。</div><progress max="100" value="0" class="video-upload-progress" aria-label="视频上传进度"></progress><div class="video-upload-actions"><button type="submit" class="primary" ${!options.tools_ready?'disabled':''}>开始上传</button><button type="button" class="secondary" data-upload-cancel hidden>取消本次上传</button></div></form></section>`;
  const form=get('#videoUploadForm'),ctx=captureViewContext(),status=get('.video-upload-status'),progress=get('progress');let uploadId=null,cancelled=false,changed=false,leaving=false,cancelling=false;
  const current=()=>ctx.isCurrent()&&form.isConnected;
  bindAudience(form);form.addEventListener('input',()=>changed=true);form.addEventListener('change',()=>changed=true);
  const note=t=>{if(current())status.textContent=t;};
  const fileInput=form.elements.video;
  get('[data-video-file-fallback]').onclick=()=>{if(form.dataset.submitting==='true')return;fileInput.removeAttribute('accept');fileInput.click();};
  fileInput.addEventListener('change',()=>{
    const selected=fileInput.files?.[0]||fileInput.files?.item?.(0);
    if(!selected)return;
    if(!selected.size||selected.size>options.max_bytes){fileInput.setCustomValidity('请选择非空且不超过500MB的视频');note('所选文件不符合大小要求，请重新选择。');return;}
    fileInput.setCustomValidity('');
    note(`已选择 ${selected.name} · ${size(selected.size)}。上传后自动生成适合播放的清晰度，不会在手机上整段转码；请保持页面前台。`);
  });
  get('[data-upload-back]').onclick=async()=>{if(leaving||!current())return;leaving=true;try{const busy=form.dataset.submitting==='true';if((changed||busy)&&!await confirmModal(busy?'正在上传':'内容尚未提交',busy?'<p>离开将停止发送后续分片，已上传部分会保留24小时；重新选择同一视频可继续。</p>':'<p>离开后未提交的标题、观看范围和所选视频会丢失，确定离开吗？</p>','确认离开'))return;if(!current())return;cancelled=true;clearFormEdits(form);void showLibrary(options).catch(notify);}finally{leaving=false;}};
  get('[data-upload-cancel]').onclick=async()=>{if(!uploadId||cancelling)return;cancelling=true;const button=get('[data-upload-cancel]');button.disabled=true;cancelled=true;try{await api(`${base}/uploads/${uploadId}`,{method:'DELETE'});uploadId=null;if(current()){button.hidden=true;progress.value=0;note('上传已取消，可以重新选择视频。');}}catch(error){if(current())toast(error.message,true);}finally{cancelling=false;if(button.isConnected)button.disabled=false;}};
  form.onsubmit=e=>{e.preventDefault();void withSubmitLock(form,async()=>{
    try{
      cancelled=false;const file=form.elements.video.files?.[0]||form.elements.video.files?.item?.(0);
      if(!form.reportValidity())return;
      if(!file?.size||file.size>options.max_bytes)throw new Error('请选择不超过500MB的视频文件');
      const title=form.elements.title.value.trim();if(!title)throw new Error('请填写视频标题');
      const audience_codes=[...form.querySelectorAll('[name=audience]:checked')].map(n=>n.value);
      if(!audience_codes.length)throw new Error('请至少选择一类观看人员');
      const uploadConfig={title,cover_title:form.elements.cover_title.value.trim(),description:form.elements.description.value.trim(),audience_codes,replaces_id:replaces?.id||null};
      form.querySelectorAll('input,select,textarea').forEach(n=>n.disabled=true);
      const hash=await fingerprint(file,options.chunk_bytes,n=>{if(!current()||cancelled)return false;note(`正在核对视频文件… ${n}%`);});if(!current()||cancelled)return;
      const data=await api(base+'/uploads',json('POST',{...uploadConfig,size:file.size,fingerprint:hash}));
      if(!current()||cancelled)return;uploadId=data.upload_id;get('[data-upload-cancel]').hidden=false;
      if(data.video_id){clearFormEdits(form);await showDetail(data.video_id,options);return;}
      let offset=data.offset;
      while(offset<file.size){
        if(!current()||cancelled)return;
        note(`正在上传 ${Math.round(offset/file.size*100)}% · ${size(offset)} / ${size(file.size)}`);progress.value=offset/file.size*100;
        const result=await api(`${base}/uploads/${uploadId}?offset=${offset}`,{method:'PUT',headers:{'Content-Type':'application/octet-stream'},body:file.slice(offset,offset+options.chunk_bytes)});
        offset=result.offset;
      }
      if(!current()||cancelled)return;progress.value=100;note('上传完成，正在提交处理任务…');
      const result=await api(`${base}/uploads/${uploadId}/finish`,{method:'POST'});
      if(!current())return;clearFormEdits(form);toast('已上传，视频正在后台处理');await showDetail(result.video_id,options);
    }catch(error){if(current()){note(error.message+' 已接收的分片会保留24小时；点击开始上传可核对进度后继续。');toast(error.message,true);}}finally{form.querySelectorAll('input,select,textarea').forEach(n=>n.disabled=false);}
  });};
}

export { renderVideoLearning, fingerprint };
