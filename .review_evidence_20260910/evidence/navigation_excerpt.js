// Current source function bodies from app.js at 1c9da27a, see evidence manifest.
async function api(url, options={}) {
  const requestOptions={...options};
  if(!requestOptions.signal&&(!requestOptions.method||requestOptions.method==='GET')&&renderAbortController)requestOptions.signal=renderAbortController.signal;
  let res;
  try { res = await fetch(portalPath(url), requestOptions); }
  catch(error) { if(error?.name==='AbortError')throw error; throw new Error('网络连接失败，请检查网络后重试；若持续发生请记录操作时间并联系管理员。'); }
  if (res.status === 401) { location.href=portalPath('/login'); throw new Error('登录已失效'); }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) { const detail=body.detail; const fallback=apiFallbackMessage(res.status); const error=new Error(typeof detail==='object'?(detail.message||validationErrorMessage(detail)||fallback):(detail||fallback)); error.detail=detail; error.status=res.status; throw error; }
  return body;
}
async function renderChangelog(){
  const data=await api('/api/changelog');
  const releases=(data.releases||[]).map(release=>{
    const badge=release.current?'<span class="badge ok">当前版本</span>':release.status==='production'?'<span class="badge">生产</span>':release.status==='candidate'?'<span class="badge warn">候选</span>':'';
    const items=(release.items||[]).map(item=>`<li><strong>${esc(item.summary)}</strong>${item.detail?`<p>${esc(item.detail)}</p>`:''}</li>`).join('')||'<li>本版本对你的功能没有单独说明。</li>';
    return `<article class="changelog-release"><div class="record-line"><h3>V${esc(release.version)}</h3>${badge}<small>${esc(release.date||'')}</small></div><ul class="changelog-items">${items}</ul></article>`;
  }).join('')||'<div class="empty">暂无更新记录</div>';
  app.innerHTML=`<section class="panel changelog-page"><h2>更新记录</h2><p>当前系统 V${esc(data.app_version||'')} · ${esc(data.role_name||'')}。下面只列出和你这个角色相关的变更。</p>${releases}</section>`;
}
