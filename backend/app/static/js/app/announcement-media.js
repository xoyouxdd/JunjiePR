import { api, apiFallbackMessage, readApiBody } from './api.js';
import { registerPageCleanup } from './context.js';
import { esc, portalPath } from './format.js';
import { newSubmissionKey, withSubmitLock } from './submission.js';
import { toast } from './toast.js';

/* Embedded in announcement publication; no independent navigation or model calls. */
async function renderAnnouncementPosterEditor({container,isCurrent,seed,onUse,onClose}){
  const controller=new AbortController();
  let posterUrl=null,poster=null,posterKey=null,closed=false;
  const active=()=>!closed&&isCurrent()&&container.isConnected;
  const dispose=()=>{closed=true;controller.abort();if(posterUrl)URL.revokeObjectURL(posterUrl);};
  registerPageCleanup(dispose);
  container.innerHTML='<p class="field-hint">正在加载模板…</p>';
  try{
    const options=await api('/api/announcement-media/options');
    if(!active())return dispose;
    container.innerHTML=`<div class="announcement-media-page"><div class="actions"><button type="button" class="secondary" data-poster-back>返回编辑</button></div>
      <form id="announcementPosterForm" class="form-stack">
        <fieldset class="announcement-template-picker"><legend>选择配图模板</legend><div class="announcement-template-grid">
          <label class="announcement-template-card"><input type="radio" name="template_id" value="" checked><span class="announcement-template-basic">Aa<br><small>纯文字排版</small></span><strong>基础版式</strong></label>
          ${(options.templates||[]).map(x=>`<label class="announcement-template-card"><input type="radio" name="template_id" value="${esc(x.id)}"><img src="${esc(portalPath(x.preview_url))}" alt="${esc(x.name)}插画" loading="lazy"><strong>${esc(x.name)}</strong><small>${esc(x.category)}</small></label>`).join('')}</div></fieldset>
        <label>海报标题<input name="title" required maxlength="48" value="${esc(seed.title.slice(0,48))}"></label>
        <label>重点内容<textarea name="summary" required maxlength="180">${esc((seed.summary||'').slice(0,180))}</textarea></label>
        <div class="announcement-media-fields"><label>板块<select name="category">${options.categories.map(x=>`<option ${x===seed.category?'selected':''}>${esc(x)}</option>`).join('')}</select></label>
          <label>版式<select name="style"><option value="notice">清晰通知</option><option value="event">活动海报</option><option value="guide">简洁指引</option></select></label>
          <label>尺寸<select name="layout"><option value="portrait">竖版 · 1080 × 1440</option><option value="landscape">横版 · 1280 × 720</option></select></label>
          <label>适用范围<input name="scope" maxlength="60" value="${esc((seed.scope||'').slice(0,60))}"></label>
          <label>生效日期<input name="effective_date" type="date" value="${esc(seed.effective_on||'')}"></label></div>
        <button type="submit" class="primary">生成预览</button>
      </form><div data-poster-result hidden><img data-poster-preview class="announcement-media-preview" alt="公告配图预览">
        <div class="actions"><button type="button" class="primary" data-poster-use>加入公告</button><a class="announcement-tool-link" data-poster-download download="公告海报.png">下载图片</a></div></div>
      <p class="field-hint" role="status" data-poster-state></p></div>`;
    const form=container.querySelector('form'),use=container.querySelector('[data-poster-use]'),message=container.querySelector('[data-poster-state]');
    let generating=false,saving=false;
    const values=()=>{const result=Object.fromEntries(new FormData(form));result.style=form.elements.style.value;result.effective_date=result.effective_date||null;return result;};
    container.querySelector('[data-poster-back]').onclick=()=>{dispose();onClose();};
    const stale=()=>{if(poster){use.disabled=true;message.textContent='内容已修改，请重新生成预览。';}};
    form.addEventListener('input',stale);form.addEventListener('change',stale);
    form.querySelectorAll('[name=template_id]').forEach(radio=>radio.onchange=()=>{const template=options.templates.find(x=>x.id===radio.value);if(template&&template.category!=='通用')form.elements.category.value=template.category;form.elements.style.disabled=!!radio.value;});
    form.onsubmit=async event=>{event.preventDefault();if(generating||saving)return;generating=true;use.disabled=true;
      await withSubmitLock(form,async()=>{const outgoing=values();try{
        const response=await fetch(portalPath('/api/announcement-media/poster'),{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(outgoing),signal:controller.signal});
        if(!response.ok){const error=await readApiBody(response);throw new Error(typeof error.detail==='string'?error.detail:apiFallbackMessage(response.status));}
        const blob=await response.blob();if(!active())return;
        if(posterUrl)URL.revokeObjectURL(posterUrl);posterUrl=URL.createObjectURL(blob);poster={blob,title:outgoing.title,summary:outgoing.summary};posterKey=newSubmissionKey();
        container.querySelector('[data-poster-preview]').src=posterUrl;container.querySelector('[data-poster-download]').href=posterUrl;container.querySelector('[data-poster-result]').hidden=false;
        use.disabled=JSON.stringify(values())!==JSON.stringify(outgoing);message.textContent=use.disabled?'内容已修改，请重新生成预览。':'';
      }catch(error){if(active()&&error.name!=='AbortError')toast(error.message,true);}finally{generating=false;}});
    };
    use.onclick=async()=>{if(!poster||use.disabled||saving||generating)return;saving=true;use.disabled=true;
      const data=new FormData();data.set('title',poster.title);data.set('alt_text',poster.summary);data.set('source','template');data.set('request_key',posterKey);data.set('image',poster.blob,'announcement-poster.png');
      try{const result=await api('/api/announcement-media/images',{method:'POST',body:data});if(!active())return;await onUse(result.item);if(active()){dispose();onClose();}}
      catch(error){if(active()){use.disabled=false;toast(error.message,true);}}finally{saving=false;}
    };
  }catch(error){if(active()){container.innerHTML='<button type="button" class="secondary" data-poster-back>返回编辑</button>';container.querySelector('button').onclick=()=>{dispose();onClose();};toast(error.message,true);}}
  return dispose;
}

export { renderAnnouncementPosterEditor };
