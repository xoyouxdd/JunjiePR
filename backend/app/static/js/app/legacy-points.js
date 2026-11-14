import {api,json} from './api.js';
import {esc,portalPath} from './format.js';
import {getViewSignal} from './context.js';
import {confirmModal} from './dialogs.js';

export async function mountLegacyPoints(host,request,{onApplied=()=>{}}={}){
  let rows=[],page=1,sequence=0,busy=false,preview=null;
  const valid=()=>host.isConnected&&request.isCurrent();
  host.innerHTML='<section class="panel legacy-points-panel" aria-busy="true">正在读取历史补录…</section>';
  try{
    const options=await api('/api/activity-points/legacy/options');
    if(!valid())return;
    host.innerHTML=`<section class="panel legacy-points-panel" aria-label="历史积点补录"><header class="legacy-heading"><div><span class="legacy-eyebrow">历史积点</span><h2>补录与核对</h2></div><span class="legacy-access">正式GSM</span></header>
      <div class="legacy-toolbar"><label>景点圈<select data-legacy-circle aria-label="选择景点圈">${options.circles.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join('')}</select></label><div class="legacy-actions"><button type="button" class="secondary" data-legacy-export>导出名单</button><button type="button" class="primary" data-legacy-upload>导入预览</button><input type="file" accept=".xlsx" data-legacy-file hidden></div></div>
      <div class="legacy-roster-heading"><span data-legacy-count></span><label><input type="checkbox" data-legacy-archived>含已归档</label></div><div data-legacy-roster></div>
      <p class="legacy-help">按圈导出和导入。只填黄色 H 列（核定总数）和 I 列（备注），修正须备注。</p><p class="legacy-status" data-legacy-status role="status" aria-live="polite"></p><div data-legacy-preview></div></section>`;
    const circle=host.querySelector('[data-legacy-circle]'),archive=host.querySelector('[data-legacy-archived]');
    const status=text=>{if(valid())host.querySelector('[data-legacy-status]').textContent=text;};
    const lock=locked=>{busy=locked;host.querySelectorAll('.legacy-toolbar button,.legacy-toolbar select,[data-legacy-archived],[data-legacy-apply],[data-legacy-view]').forEach(el=>el.disabled=locked);};
    const resetPreview=()=>{preview=null;host.querySelector('[data-legacy-preview]').replaceChildren();};
    function renderRoster(){
      const visible=rows.filter(row=>archive.checked||row.status==='在职'),pages=Math.max(1,Math.ceil(visible.length/10));
      page=Math.min(page,pages);
      host.querySelector('[data-legacy-count]').textContent=`名单 ${visible.length} 人`;
      host.querySelector('[data-legacy-roster]').innerHTML=visible.length?`<div class="legacy-table-wrap"><table class="legacy-table"><thead><tr><th>员工</th><th>截止月份</th><th>已存积点</th><th>状态</th></tr></thead><tbody>${visible.slice((page-1)*10,page*10).map(row=>`<tr><td><strong>${esc(row.name)}</strong><small>${esc(row.employee_no)} · ${esc(row.role)}</small>${row.needs_review?'<span class="legacy-review">历史变动需核对</span>':''}</td><td><span>${esc(row.cutoff||'待核对')}</span><details><summary>范围</summary><p>${esc(row.coverage)}</p></details></td><td class="legacy-number">${esc(row.total)}</td><td><span class="legacy-row-state">${esc(row.blocked?'待核对':row.status)}</span></td></tr>`).join('')}</tbody></table></div>${pages>1?`<div class="legacy-pagination"><button type="button" class="secondary" data-legacy-prev ${page<=1?'disabled':''}>上一页</button><span>${page} / ${pages}</span><button type="button" class="secondary" data-legacy-next ${page>=pages?'disabled':''}>下一页</button></div>`:''}`:'<p class="empty">本圈暂无可补录名单。</p>';
      host.querySelector('[data-legacy-prev]')?.addEventListener('click',()=>{page--;renderRoster();});
      host.querySelector('[data-legacy-next]')?.addEventListener('click',()=>{page++;renderRoster();});
    }
    async function loadRoster(){
      const token=++sequence;resetPreview();page=1;
      rows=[];host.querySelector('[data-legacy-roster]').replaceChildren();host.querySelector('[data-legacy-count]').textContent='';
      if(!circle.value){status('暂无开放历史积点补录的景点圈。');lock(true);return;}
      lock(true);status('正在读取名单…');
      try{const result=await api('/api/activity-points/legacy/roster?circle_id='+encodeURIComponent(circle.value));if(!valid()||token!==sequence)return;rows=result.items;renderRoster();status('');}
      catch(error){if(valid()&&token===sequence){status(error.message);host.querySelector('[data-legacy-roster]').innerHTML='<button type="button" class="secondary" data-legacy-roster-retry>重新读取名单</button>';host.querySelector('[data-legacy-roster-retry]').onclick=loadRoster;}}
      finally{if(valid()&&token===sequence)lock(false);}
    }
    function renderPreview(data,mode='attention'){
      const s=data.summary;
      const changed=data.changes.filter(row=>row.delta!==0),shown=mode==='errors'?[]:mode==='all'?data.changes:changed;
      const errors=mode==='changes'?[]:data.errors;
      host.querySelector('[data-legacy-preview]').innerHTML=`<section class="legacy-preview"><header><h3>导入预览</h3><span class="legacy-preview-tag ${data.errors.length?'has-errors':''}">${data.errors.length?`${data.errors.length} 行待修正`:'核对后入账'}</span></header><div class="legacy-preview-counts"><div><span>新增</span><strong>${s.new}<small> 人</small></strong></div><div><span>调整</span><strong>${s.adjusted}<small> 人</small></strong></div><div><span>不变 / 留空</span><strong>${s.unchanged+s.blank}<small> 人</small></strong></div></div><div class="legacy-deltas"><span>补入 <b>+${s.increase}</b> 点</span><span>冲回 <b>−${s.decrease}</b> 点</span></div>
        <div class="legacy-preview-filters" aria-label="预览内容">${[['attention','差额和错误',changed.length+data.errors.length],['changes','仅差额',changed.length],['errors','仅错误',data.errors.length],['all','全部已填',data.changes.length]].map(([id,label,count])=>`<button type="button" class="${mode===id?'primary':'secondary'}" data-legacy-view="${id}" aria-pressed="${mode===id}">${label} <span>${count}</span></button>`).join('')}</div>
        ${errors.length?`<div class="legacy-errors" role="alert">${errors.map(e=>`<p>第${esc(e.row)}行 · ${esc(e.employee_no)}<br><span>${esc(e.message)}</span></p>`).join('')}</div>`:''}${data.errors.length?'<p class="legacy-help legacy-error-notice">有错误行，整批不会入账。请修正后重新导入。</p>':''}
        ${shown.length?`<div class="legacy-table-wrap legacy-preview-table"><table class="legacy-table"><thead><tr><th>员工</th><th>原有</th><th>核定</th><th>差额</th></tr></thead><tbody>${shown.map(row=>`<tr><td><strong>${esc(row.name)}</strong><small>${esc(row.employee_no)}</small></td><td>${row.previous}</td><td>${row.total}</td><td class="${row.delta<0?'legacy-negative':row.delta>0?'legacy-positive':''}">${row.delta>0?'+':''}${row.delta}</td></tr>`).join('')}</tbody></table></div>`:!errors.length?`<p class="empty">${mode==='errors'?'没有错误行':mode==='all'?'没有已填记录':'没有需要调整的积点'}</p>`:''}
        ${data.can_apply?'<button type="button" class="primary legacy-apply" data-legacy-apply>确认入账</button>':!data.errors.length?'<p class="legacy-help">本次无差额，无需入账。</p>':''}</section>`;
      host.querySelectorAll('[data-legacy-view]').forEach(button=>button.onclick=()=>{if(!busy)renderPreview(data,button.dataset.legacyView);});
      host.querySelector('[data-legacy-apply]')?.addEventListener('click',async()=>{
        if(busy||!preview)return;
        const selected=preview;
        if(!await confirmModal('确认历史积点入账',`<p>${esc(selected.circle_name)}：补入 ${selected.summary.increase} 点，冲回 ${selected.summary.decrease} 点。</p><p>仅按差额入账，PR分值不变。</p>`,'确认入账'))return;
        if(!valid()||preview!==selected)return;
        lock(true);status('正在入账…');
        try{const result=await api('/api/activity-points/legacy/apply',json('POST',{preview_id:selected.preview_id}));if(!valid())return;await loadRoster();if(!valid())return;status(`已完成：${result.changed} 人，补入 ${result.increase} 点，冲回 ${result.decrease} 点。`);await onApplied();}
        catch(error){if(valid())status(error.message);}
        finally{if(valid())lock(false);}
      });
    }
    circle.onchange=loadRoster;
    archive.onchange=()=>{page=1;resetPreview();renderRoster();};
    host.querySelector('[data-legacy-export]').onclick=async()=>{
      if(busy)return;lock(true);status('正在导出…');
      try{
        const url=portalPath('/api/activity-points/legacy/export?'+new URLSearchParams({circle_id:circle.value,include_archived:String(archive.checked)}));
        const response=await fetch(url,{method:'POST',signal:getViewSignal()});
        if(!response.ok){let detail;try{detail=(await response.json()).detail;}catch{}throw new Error(typeof detail==='string'?detail:'导出失败，请重试');}
        const blob=await response.blob();if(!valid())return;
        const href=URL.createObjectURL(blob),link=document.createElement('a');link.href=href;link.download=`历史积点补录_${circle.selectedOptions[0].textContent}.xlsx`;link.click();setTimeout(()=>URL.revokeObjectURL(href),1000);
        status('名单已导出，填写黄色列后导入预览。');
      }catch(error){if(valid()&&error.name!=='AbortError')status(error.message);}
      finally{if(valid())lock(false);}
    };
    const file=host.querySelector('[data-legacy-file]');
    host.querySelector('[data-legacy-upload]').onclick=()=>{if(!busy){file.value='';file.click();}};
    file.onchange=async()=>{
      if(busy||!file.files[0])return;resetPreview();lock(true);status('正在核对Excel…');
      try{const form=new FormData();form.append('file',file.files[0]);const result=await api('/api/activity-points/legacy/preview?circle_id='+encodeURIComponent(circle.value),{method:'POST',body:form});if(!valid())return;preview=result;renderPreview(result);status(`${result.circle_name} · ${file.files[0].name}`);}
      catch(error){if(valid())status(error.message);}
      finally{if(valid())lock(false);}
    };
    await loadRoster();
  }catch(error){if(valid())host.innerHTML=`<section class="panel legacy-points-panel"><h2>历史积点补录</h2><p class="error" role="alert">${esc(error.message)}</p><button type="button" class="secondary" data-legacy-retry>重试</button></section>`;host.querySelector('[data-legacy-retry]')?.addEventListener('click',()=>mountLegacyPoints(host,request,{onApplied}));}
}
