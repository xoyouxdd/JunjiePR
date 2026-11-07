import { api } from './api.js';
import { esc } from './format.js';
import { beginViewRequest } from './context.js';
import { state } from './state.js';
import { mountLegacyPoints } from './legacy-points.js';

export async function renderActivityPoints(){
  const request=beginViewRequest();
  const legacyAllowed=state.me.base_role_code==='GSM'&&state.me.role_code==='GSM';
  if(!request.write('<div class="section-gap"><div id="personalActivityPoints"></div>'+(legacyAllowed?'<div id="legacyActivityPoints"></div>':'')+'</div>'))return;
  await mountActivityPoints(document.getElementById('personalActivityPoints'),request);
  if(legacyAllowed&&request.isCurrent())await mountLegacyPoints(document.getElementById('legacyActivityPoints'),request,{onApplied:()=>mountActivityPoints(document.getElementById('personalActivityPoints'),request)});
}

export async function mountActivityPoints(host,request){
  let sequence=0;
  async function load(page=1){
    const current=++sequence;
    host.innerHTML='<section class="panel activity-points-card" aria-busy="true">正在查询活动积点…</section>';
    try{
      const data=await api(`/api/activity-points/me?page=${page}`);
      if(!request.isCurrent()||current!==sequence||!host.isConnected)return;
      if(!data.eligible){host.replaceChildren();return;}
      const pages=Math.max(1,Math.ceil(data.total/data.page_size));
      host.innerHTML=`<section class="panel activity-points-card" aria-label="我的活动积分卡">
        <div class="activity-points-hero">
          <div class="points-pass">
            <div class="activity-points-heading"><div><span class="points-pass-brand">PR · RECOGNITION</span><h2>我的活动积分卡</h2></div><span class="points-lifetime">永久有效</span></div>
            <div class="points-pass-main"><div class="points-available"><span>可用积点</span><div class="points-amount"><strong>${esc(data.available)}</strong><span>点</span></div></div><span class="points-pass-mark" aria-hidden="true"><svg viewBox="0 0 64 64" fill="none"><path d="M32 5 56 19v26L32 59 8 45V19Z"/><path d="m32 16 15 9v16l-15 9-15-9V25Z"/><path d="m23 32 6 6 13-14"/></svg></span></div>
            <div class="activity-points-balances"><div><span>累计获得</span><strong>${esc(data.earned)}<small> 点</small></strong></div><div><span>已使用</span><strong>${esc(data.used)}<small> 点</small></strong></div></div>
          </div>
        </div>
        <div class="points-note"><span>PR封顶仍累计 · 积点不清零</span><small>可在活动中心使用 · 冻结 ${esc(data.frozen||0)} 点</small></div>
        <details class="points-detail"><summary><span>积点明细<span class="points-detail-count">${esc(data.total)} 笔</span></span><span class="points-detail-chevron" aria-hidden="true"></span></summary>
        <div class="activity-points-list">${data.items.length?data.items.map(item=>`<article><span class="points-entry-icon ${item.delta<0?'is-negative':''}" aria-hidden="true">${item.delta<0?'−':'+'}</span><div><strong>${esc(item.category)} · ${esc(item.signer_name)}</strong><small>${esc(item.business_date)} · ${item.origin==='legacy_import'?'历史补录'+(item.cutoff?' · 截至'+esc(item.cutoff):''):item.origin==='activity'?'活动消费或退款':item.origin==='historical_backfill'?'历史补计':'认可同步'}${item.delta<0&&item.origin!=='activity'?' · 撤销或调整':''}</small></div><b class="${item.delta<0?'points-negative':''}">${item.delta>0?'+':''}${esc(item.delta)} 点</b></article>`).join(''):'<p class="empty">还没有积点记录，收到有效的上级认可后会自动累计。</p>'}</div>
        ${pages>1?`<div class="activity-points-pages"><button type="button" class="secondary" data-points-prev ${page<=1?'disabled':''}>上一页</button><span>${page} / ${pages}</span><button type="button" class="secondary" data-points-next ${page>=pages?'disabled':''}>下一页</button></div>`:''}</details>
        </section>`;
      const details=host.querySelector('details');
      if(page>1)details.open=true;
      host.querySelector('[data-points-prev]')?.addEventListener('click',()=>load(page-1));
      host.querySelector('[data-points-next]')?.addEventListener('click',()=>load(page+1));
    }catch(error){
      if(!request.isCurrent()||current!==sequence||!host.isConnected)return;
      host.innerHTML=`<section class="panel activity-points-card"><h2>我的活动积分卡</h2><p class="error" role="alert">${esc(error.message)}</p><button type="button" class="secondary" data-points-retry>重新查询</button></section>`;
      host.querySelector('[data-points-retry]').onclick=()=>load(page);
    }
  }
  await load();
}
