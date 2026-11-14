import { api, json } from './api.js';
import { esc, portalPath } from './format.js';
import { beginViewRequest, pageTimeout } from './context.js';
import { formModal, noticeModal } from './dialogs.js';
import { state } from './state.js';
import { toast } from './toast.js';
import { newSubmissionKey as key } from './submission.js';
import { prizePhotoFields, bindPrizePhotos, uploadPrizePhotos } from './activity-photo.js';
import { signActivityAward, viewActivityReceipt } from './activity-signature.js';
import { gotoTab } from './navigation.js';

const labels={draft:'草稿',published:'已发布',completed:'已结案',cancelled:'已取消',open:'开放',settled:'已开奖 / 成交',unsold:'流拍',pending:'待开奖',ready:'待领取',claimed:'旧核销（无签名）',issued:'已发放，待签收',disputed:'签收异常',signed:'已签收',refunded:'已退款',expired:'逾期关闭'};
const stamp=value=>String(value||'').replace('T',' ').slice(0,16);
const integer=(value,min=1)=>{const number=Number(value);if(!Number.isSafeInteger(number)||number<min)throw new Error('请填写有效的整数');return number;};
const required=value=>{if(!String(value||'').trim())throw new Error('请填写完整的必填内容');return String(value).trim();};
const localInput=offset=>new Date(Date.now()+offset+8*3600000).toISOString().slice(0,16);
const input=(name,label,type='text',value='',extra='')=>`<label>${label}<input name="${name}" type="${type}" value="${esc(value)}" ${extra} required></label>`;

function prizeFields(kind='draw'){return `<div class="activity-prize-editor">${input('prize_name','奖品名称')}${prizePhotoFields()}${input('prize_quantity',kind==='auction'?'份数（每个竞拍单元一件）':'份数','number',1,kind==='auction'?'readonly':'min="1" max="1000"')}<label>奖品说明<input name="prize_description" maxlength="2000"></label><button type="button" class="secondary" data-remove-prize>移除此奖品</button></div>`;}
function lotFields(kind){return `<fieldset class="activity-lot-editor"><legend>${kind==='draw'?'奖池':'拍品'}</legend>
  <div class="activity-form-grid">${input('lot_name',kind==='draw'?'奖池名称':'拍品名称')}${input('price',kind==='draw'?'每次消耗积点':'起拍积点','number',20,'min="1" max="1000000"')}
  ${kind==='draw'?input('per_person','每人最多参与次数','number',1,'min="1" max="100"'):input('step','最低加价积点','number',1,'min="1"')}
  ${kind==='draw'?'':'<label>结束前自动延时<select name="extend_seconds"><option value="60">最后1分钟出价，顺延1分钟</option><option value="0">不延时</option></select></label>'}</div>
  <div data-prizes>${prizeFields(kind)}</div>${kind==='draw'?'<button type="button" class="secondary" data-add-prize>增加奖品</button>':''}
  <button type="button" class="secondary" data-remove-lot>移除此${kind==='draw'?'奖池':'拍品'}</button></fieldset>`;}

async function createDialog(){
  const requestKey=key();
  let disposePhotos;
  try{return await formModal('创建积点活动','先保存草稿并核对规则，再发布。时间均为北京时间。',`
    ${input('title','活动名称','text','','maxlength="120"')}
    <label>玩法<select name="kind"><option value="draw">多奖池必得抽奖</option><option value="auction">积点拍卖</option></select></label>
    <label>活动介绍<textarea name="description" maxlength="5000"></textarea></label>
    <label>参与范围<select name="scope"><option value="all">全员</option><option value="own">本圈</option><option value="selected">指定多个圈</option></select></label>
    <div data-circle-choices hidden>${(state.options.employee_circles||[]).map(c=>`<label class="activity-check"><input type="checkbox" name="circle_ids" value="${c.id}">${esc(c.name)}</label>`).join('')}</div>
    <div class="activity-form-grid">${input('starts_at','开放时间','datetime-local',localInput(0))}${input('ends_at','截止及开奖时间','datetime-local',localInput(86400000))}${input('claim_until','领取截止（含宽限期）','datetime-local',localInput(8*86400000))}</div>
    ${input('location','领取地点及联系方式','text','','maxlength="300"')}
    <label>协助人员工号（选填，多个用逗号分隔）<input name="helpers"></label>
    <div data-lots>${lotFields('draw')}</div><button type="button" class="secondary" data-add-lot>增加奖池 / 拍品</button>
    <p class="field-hint">抽奖每次必得一份奖品，按份数限售。工作人员发放后，必须由员工本人手机签字确认；退款由发起人办理。超过领取宽限期后可关闭资格，默认不退点；主办方缺货须退款。拍卖出价成功后不可自行撤价。</p>`, '保存草稿', {
    lockWhileSubmitting:true,
    onReady(form){
      disposePhotos=bindPrizePhotos(form);
      const kind=form.elements.kind;
      kind.onchange=()=>{form.querySelector('[data-lots]').innerHTML=lotFields(kind.value);};
      form.elements.scope.onchange=()=>{form.querySelector('[data-circle-choices]').hidden=form.elements.scope.value!=='selected';};
      form.onclick=event=>{
        const button=event.target.closest('button');if(!button)return;
        if(button.hasAttribute('data-add-lot'))form.querySelector('[data-lots]').insertAdjacentHTML('beforeend',lotFields(kind.value));
        if(button.hasAttribute('data-add-prize'))button.closest('fieldset').querySelector('[data-prizes]').insertAdjacentHTML('beforeend',prizeFields());
        if(button.hasAttribute('data-remove-prize')){const parent=button.closest('[data-prizes]');if(parent.children.length>1)button.closest('.activity-prize-editor').remove();else toast('至少保留一种奖品',true);}
        if(button.hasAttribute('data-remove-lot')){const parent=form.querySelector('[data-lots]');if(parent.children.length>1)button.closest('fieldset').remove();else toast('至少保留一个奖池或拍品',true);}
      };
    },
    async onSubmit(form){
      const v=name=>form.elements[name].value;
      let circles=[];
      if(v('scope')==='own'){if(!state.me.attraction_id)throw new Error('当前没有所属圈，请选择其他范围');circles=[state.me.attraction_id];}
      if(v('scope')==='selected'){circles=[...form.querySelectorAll('[name=circle_ids]:checked')].map(i=>Number(i.value));if(!circles.length)throw new Error('请选择至少一个圈');}
      const lots=[...form.querySelectorAll('.activity-lot-editor')].map(lot=>{
        const lv=name=>lot.querySelector(`[name=${name}]`)?.value;
        return {name:required(lv('lot_name')),price:integer(lv('price')),per_person:v('kind')==='draw'?integer(lv('per_person')):1,
          step:v('kind')==='auction'?integer(lv('step')):1,extend_seconds:v('kind')==='auction'?Number(lv('extend_seconds')):0,
          prizes:[...lot.querySelectorAll('.activity-prize-editor')].map(p=>({name:required(p.querySelector('[name=prize_name]').value),
            quantity:v('kind')==='auction'?1:integer(p.querySelector('[name=prize_quantity]').value),description:p.querySelector('[name=prize_description]').value}))};
      });
      const payload={request_key:requestKey,title:required(v('title')),kind:v('kind'),description:v('description'),
        starts_at:required(v('starts_at')),ends_at:required(v('ends_at')),claim_until:required(v('claim_until')),location:required(v('location')),
        circle_ids:circles,helper_employee_nos:v('helpers').split(/[,，\s]+/).filter(Boolean),lots};
      const locked=[...form.querySelectorAll('input,select,textarea,button')].map(node=>[node,node.disabled]);locked.forEach(([node])=>{node.disabled=true;});
      try{
        const photos=await uploadPrizePhotos(form);
        [...form.querySelectorAll('.activity-lot-editor')].forEach((lot,i)=>[...lot.querySelectorAll('.activity-prize-editor')].forEach((prize,j)=>{if(photos.has(prize))lots[i].prizes[j].photo_id=photos.get(prize);}));
        await api('/api/activities',json('POST',payload));
      }finally{locked.forEach(([node,disabled])=>{node.disabled=disabled;});}
      toast('草稿已保存，请核对后发布');
    }
  });}finally{disposePhotos?.();}
}

export async function renderActivities(){
  const request=beginViewRequest();
  const destination=state.activityDestination;
  state.activityDestination=null;
  if(!request.write('<section id="activitiesPage" class="activities-page"><p class="empty">正在查询活动…</p></section>'))return;
  const host=document.getElementById('activitiesPage');
  let data,filter=destination?.filter||'all',sequence=0,page=1,displayedFilter='all';
  let focusedEvent=destination?.eventId||null,displayedEvent=null,returnToPending=false;
  let revealDestination=Boolean(destination);
  const details=new Map(),recordViews=new Map(),opened=new Set(),detailLoads=new Map();
  if(focusedEvent)opened.add(focusedEvent);
  const pager=(current,total,size,kind,id='')=>{const pages=Math.max(1,Math.ceil(total/size));return `<nav class="activity-pagination" aria-label="${kind==='list'?'活动':'核销记录'}分页"><button type="button" class="secondary" data-page-kind="${kind}" data-page-event="${id}" data-page="${current-1}" ${current<=1?'disabled':''}>上一页</button><span>${current} / ${pages} · ${total}${kind==='list'?'场':'条'}</span><button type="button" class="secondary" data-page-kind="${kind}" data-page-event="${id}" data-page="${current+1}" ${current>=pages?'disabled':''}>下一页</button></nav>`;};
  const active=()=>request.isCurrent()&&host.isConnected;
  const backLink=()=>returnToPending
    ? '<button type="button" class="secondary" data-activity-back>‹ 返回待处理活动</button>'
    : destination?.from?'<button type="button" class="secondary" data-activity-back>‹ 返回积点卡</button>':'';
  function bindBack(){
    host.querySelector('[data-activity-back]')?.addEventListener('click',()=>{
      if(returnToPending){
        focusedEvent=null;returnToPending=false;filter='pending';opened.clear();revealDestination=true;load();
      }else gotoTab(destination.from);
    });
  }
  function paintPending(result){
    const rows=result.items.map(item=>`<li class="activity-pending-row">
      <div><strong>${esc(item.title)}</strong><small>${esc({issued:'待签收'}[item.state]||labels[item.state])} · 已支出 ${esc(item.cost)} 点</small></div>
      <button type="button" class="secondary" data-pending-event="${esc(item.event_id)}">${{ready:'查看领取',issued:'查看签收'}[item.state]||'查看活动'} ›</button>
    </li>`).join('');
    host.innerHTML=`<section class="panel activity-pending">
      <header>${backLink()}<h2>我的待处理活动</h2></header>
      ${rows?`<ul class="activity-pending-list">${rows}</ul>`:'<p class="empty">暂无待处理活动</p>'}
      ${result.total>result.page_size?pager(page,result.total,result.page_size,'list'):''}
    </section>`;
    bindBack();
    host.querySelectorAll('[data-pending-event]').forEach(button=>button.onclick=()=>{
      focusedEvent=Number(button.dataset.pendingEvent);
      returnToPending=true;
      filter='all';
      opened.clear();
      opened.add(focusedEvent);
      revealDestination=true;
      load();
    });
    host.querySelectorAll('[data-page-kind]').forEach(button=>button.onclick=()=>{
      page=Number(button.dataset.page);load();
    });
  }
  function paint(){
    const funds=data.balance;
    const events=data.items;
    host.innerHTML=`${focusedEvent?backLink():''}<section class="panel activity-heading"><div><span class="field-hint">PR · 活动中心</span><h2>${focusedEvent?'活动详情':'用认可积点，参与精彩活动'}</h2>${focusedEvent?'':'<p>抽奖每次必得奖品 · 拍卖以积点竞价</p>'}</div><div class="activity-wallet"><strong>${esc(funds.available)}<small> 可用积点</small></strong><span>冻结 ${esc(funds.frozen)} 点</span></div></section>
      <div class="activity-toolbar"><div>${[['all','全部活动'],['mine','我的活动'],['sign',`待本人签收 ${data.pending_signatures||0}`],...(data.can_create||data.can_manage?[['manage','活动管理']]:[])].map(([id,name])=>`<button type="button" class="${filter===id?'primary':'secondary'}" data-filter="${id}" aria-pressed="${filter===id}">${name}</button>`).join('')}</div><div><button type="button" class="secondary" data-refresh>刷新</button>${data.can_create?'<button type="button" class="primary" data-create>创建活动</button>':''}</div></div>
      <p class="field-hint">北京时间 · 页面自动刷新。余额不足也可以查看奖池；冻结积点不能再次使用。</p>
      <div class="activity-events">${events.map(event=>eventMarkup(details.get(event.id)||event,funds,opened.has(event.id))).join('')||'<section class="panel empty">暂无符合条件的活动</section>'}</div>${focusedEvent?'':pager(page,data.total,data.page_size,'list')}`;
    bindBack();
    host.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{focusedEvent=null;returnToPending=false;filter=b.dataset.filter;page=1;opened.clear();load();});
    host.querySelector('[data-refresh]').onclick=()=>load();
    host.querySelector('[data-create]')?.addEventListener('click',async()=>{if(await createDialog()){page=1;await load();}});
    bindEventControls(host);
    host.querySelectorAll('details[data-event]').forEach(node=>node.addEventListener('toggle',()=>{
      if(!node.isConnected)return;const id=Number(node.dataset.event);
      if(node.open){opened.add(id);if(!details.has(id)&&!detailLoads.has(id))loadDetail(id);}else opened.delete(id);
    }));
    host.querySelectorAll('[data-page-kind]').forEach(b=>b.onclick=()=>{if(b.dataset.pageKind==='list'){page=Number(b.dataset.page);opened.clear();load();}else{const id=Number(b.dataset.pageEvent);recordViews.get(id).page=Number(b.dataset.page);loadRecords(id);}});
  }
  function bindEventControls(root){
    root.querySelectorAll('[data-prize-photo-view]').forEach(button=>button.onclick=()=>noticeModal(button.dataset.photoName,`<img class="activity-photo-full" src="${esc(portalPath(button.dataset.photoUrl))}" alt="${esc(button.dataset.photoName)}">`,'关闭预览'));
    root.querySelectorAll('[data-detail-retry]').forEach(b=>b.onclick=()=>loadDetail(Number(b.dataset.detailRetry)));
    bindRecordControls(root);
  }
  function bindRecordControls(root){
    root.querySelectorAll('[data-record-state]').forEach(b=>b.onclick=()=>{const id=Number(b.dataset.recordEvent),v=recordViews.get(id);v.status=b.dataset.recordState;v.page=1;loadRecords(id);});
    root.querySelectorAll('[data-record-search]').forEach(form=>{
      const id=Number(form.dataset.recordSearch);
      form.elements.keyword.oninput=()=>{recordViews.get(id).draft=form.elements.keyword.value;};
      form.onsubmit=e=>{e.preventDefault();const v=recordViews.get(id);v.keyword=form.elements.keyword.value.trim();v.page=1;loadRecords(id);};
    });
    root.querySelectorAll('[data-page-kind="records"]').forEach(b=>b.onclick=()=>{const id=Number(b.dataset.pageEvent);recordViews.get(id).page=Number(b.dataset.page);loadRecords(id);});
    root.querySelectorAll('[data-action]').forEach(b=>b.onclick=()=>action(b));
  }
  function updateRecords(id){
    const root=host.querySelector(`[data-records-event="${id}"]`),event=details.get(id);if(!root||!event)return;
    const template=document.createElement('template');template.innerHTML=recordsMarkup(event);
    // Keep interactive nodes alive across responses: a refresh between pointerdown
    // and click must not swallow a user's next state or page selection.
    root.querySelectorAll('[data-record-state]').forEach(button=>{
      const next=template.content.querySelector(`[data-record-state="${button.dataset.recordState}"]`);
      button.className=next.className;button.setAttribute('aria-pressed',next.getAttribute('aria-pressed'));
      button.querySelector('span').textContent=next.querySelector('span').textContent;
    });
    root.querySelector('.activity-record-list').replaceWith(template.content.querySelector('.activity-record-list'));
    const oldPages=root.querySelector('.activity-record-pagination'),newPages=template.content.querySelector('.activity-record-pagination');
    if(oldPages.querySelector('nav')&&newPages.querySelector('nav')){
      const buttons=[...newPages.querySelectorAll('button')];
      oldPages.querySelectorAll('button').forEach((button,i)=>{button.disabled=buttons[i].disabled;button.dataset.page=buttons[i].dataset.page;});
      oldPages.querySelector('span').textContent=newPages.querySelector('span').textContent;
    }else oldPages.replaceChildren(...newPages.childNodes);
    bindRecordControls(root);
  }
  function eventMarkup(event,funds,open){
    const circleNames=event.circle_ids.map(id=>(state.options.employee_circles||[]).find(c=>c.id===id)?.name||'指定圈');
    if(!event.lots)return `<details class="panel activity-event" data-event="${event.id}" ${open?'open':''}><summary><span><b>${esc(event.title)}</b><small>${event.kind==='draw'?'必得抽奖':'积点拍卖'} · ${esc(labels[event.state])} · ${esc(circleNames.join('、')||'全员')}</small></span><span>查看活动 ＋</span></summary><div class="activity-event-body"><p class="field-hint">展开后加载奖池和领取记录</p><button type="button" class="secondary" data-detail-retry="${event.id}">加载详情</button></div></details>`;
    return `<details class="panel activity-event" data-event="${event.id}" ${open?'open':''}><summary><span><b>${esc(event.title)}</b><small>${event.kind==='draw'?'必得抽奖':'积点拍卖'} · ${esc(labels[event.state])} · ${esc(circleNames.join('、')||'全员')}</small></span><span>查看活动 ＋</span></summary>
      <div class="activity-event-body"><p class="activity-copy">${esc(event.description)}</p><p class="field-hint">开放 ${stamp(event.starts_at)} · 原定截止 ${stamp(event.ends_at)}<br>领取截止（含宽限期）${stamp(event.claim_until)} · ${esc(event.location)}</p>
      <p class="field-hint">${event.kind==='draw'?'每次参与必得一份随机奖品，按库存限售。':'出价成功不可自行撤回；被超价立即解冻，成交后扣点。'}待领取逾期可关闭；已发放须由员工本人签字，不能自动算完成。缺货由发起人退款。</p>
      ${event.summary?`<div class="activity-summary"><span>参与 / 成交 <b>${event.summary.records}</b> 笔</span><span>净消耗 <b>${event.summary.net_used}</b> 点</span><span>已退回 <b>${event.summary.refunded}</b> 点</span><span>冻结 <b>${event.summary.frozen}</b> 点</span><span>待领取 <b>${event.summary.ready}</b> 份</span><span>待签收 <b>${event.summary.issued||0}</b> 份</span><span>签收异常 <b>${event.summary.disputed||0}</b> 份</span><span>已签收 <b>${event.summary.claimed}</b> 份</span></div>`:''}
      ${event.owner&&event.state!=='draft'?`<div class="activity-actions"><a class="secondary activity-export" data-activity-export href="${esc(portalPath('/api/activities/'+event.id+'/export'))}">导出核销Excel</a></div>`:''}
      ${event.owner?`<div class="activity-actions">${event.state==='draft'?`<button type="button" class="primary" data-action="publish" data-event="${event.id}">核对并发布</button>`:''}${['draft','published'].includes(event.state)?`<button type="button" class="secondary" data-action="cancel-event" data-event="${event.id}">取消整场</button>`:''}${event.state==='published'?`<button type="button" class="secondary" data-action="complete" data-event="${event.id}">办理结案</button>`:''}</div>`:''}
      <div class="activity-lots">${event.lots.map(lot=>lotMarkup(event,lot,funds)).join('')}</div>${event.manager?recordsMarkup(event):''}</div></details>`;
  }
  function awardMarkup(event,a){
    return `<article class="activity-award"><div><b>凭证 #${a.id} · ${esc(a.prize||'等待开奖')}</b><small>${event.manager?`${esc(a.employee_name)} · ${esc(a.employee_no)} · `:''}${a.cost}积点 · ${esc(labels[a.state])}${a.overdue?'（已过领取期限）':''}${a.reason?' · '+esc(a.reason):''}</small>${a.issued_at?`<small>发放：${esc(a.issued_name)} · ${stamp(a.issued_at)}</small>`:''}</div>
    ${a.evidence_problem?`<p class="error" role="alert">${esc(a.evidence_problem)}</p>`:''}
    ${a.receipt_id?`<button type="button" class="secondary" data-action="view_receipt" data-receipt="${esc(a.receipt_id)}">查看本人签收档案</button>`:''}
    ${!event.manager&&a.state==='issued'?`<div class="activity-actions"><button type="button" class="primary" data-action="sign" data-award="${a.id}">本人签字确认领取</button><button type="button" class="secondary" data-action="dispute" data-award="${a.id}">未收到 / 奖品有误</button></div>`:''}
    ${event.owner&&['claimed','expired'].includes(a.state)?`<button type="button" class="secondary" data-action="correct" data-award="${a.id}">纠正旧核销 / 误关闭</button>`:''}
    ${event.manager&&a.state==='ready'?`<div class="activity-actions"><button type="button" class="primary" data-action="issue" data-award="${a.id}">已发放，等待本人签收</button>${event.owner?`<button type="button" class="secondary" data-action="refund" data-award="${a.id}">缺货退款</button>`:''}${a.overdue?`<button type="button" class="secondary" data-action="expire" data-award="${a.id}">逾期关闭</button>`:''}</div>`:''}
    ${event.owner&&['issued','disputed'].includes(a.state)?`<div class="activity-actions"><button type="button" class="secondary" data-action="recall" data-award="${a.id}">核实后撤回发放</button>${a.state==='disputed'?`<button type="button" class="secondary" data-action="request_sign" data-award="${a.id}">问题解决，请本人签收</button>`:''}</div>`:''}
    ${event.owner&&['issued','disputed','signed'].includes(a.state)?`<button type="button" class="secondary" data-action="return_refund" data-award="${a.id}">核实交付后退款</button>`:''}
    </article>`;
  }
  function recordsMarkup(event){
    const v=recordViews.get(event.id);
    if(!v)return '<p class="field-hint">正在加载领取记录…</p>';
    const r=v.data,counts=r?.counts||{};
    return `<section class="activity-awards activity-records" data-records-event="${event.id}" aria-label="领取核销记录"><header><h3>领取核销</h3><span class="field-hint">按凭证核对，一份一笔</span></header><form class="activity-record-search" data-record-search="${event.id}"><label>查找记录<input name="keyword" value="${esc(v.draft??v.keyword)}" placeholder="凭证号 / 姓名 / 工号" maxlength="80"></label><button class="secondary" type="submit">搜索记录</button></form><div class="activity-state-tabs" aria-label="领取状态">${['ready','issued','disputed','signed','claimed','refunded','pending','expired','all'].map(s=>`<button type="button" class="${v.status===s?'primary':'secondary'}" data-record-state="${s}" data-record-event="${event.id}" aria-pressed="${v.status===s}">${s==='all'?'全部':labels[s]} <span>${s==='all'?Object.values(counts).reduce((a,b)=>a+b,0):counts[s]||0}</span></button>`).join('')}</div><div class="activity-record-list" aria-live="polite">${v.loading?'<p class="empty">正在查询记录…</p>':v.error?`<p class="error" role="alert">${esc(v.error)}，请点击状态或搜索重试。</p>`:r?.items.length?r.items.map(a=>awardMarkup(event,a)).join(''):'<p class="empty">当前条件下暂无记录</p>'}</div><div class="activity-record-pagination">${r?pager(v.page,r.total,r.page_size,'records',event.id):''}</div></section>`;
  }
  async function loadRecords(id,repaint=true){
    const v=recordViews.get(id)||{status:'ready',keyword:'',page:1,token:0};recordViews.set(id,v);
    const token=++v.token,generation=sequence;v.loading=true;v.error='';if(repaint)updateRecords(id);
    try{const r=await api(`/api/activities/${id}/records?`+new URLSearchParams({status:v.status,keyword:v.keyword,page:v.page}));if(!active()||v.token!==token||generation!==sequence)return;v.data=r;if(v.page>1&&!r.items.length){v.page=Math.max(1,Math.ceil(r.total/r.page_size));return await loadRecords(id,repaint);}}
    catch(e){if(!active()||v.token!==token||generation!==sequence)return;v.error=e.message;}
    finally{if(active()&&v.token===token&&generation===sequence){v.loading=false;if(repaint)updateRecords(id);}}
  }
  async function loadDetail(id){
    const generation=sequence,token={};detailLoads.set(id,token);
    try{const event=await api(`/api/activities/${id}`);if(!active()||generation!==sequence||detailLoads.get(id)!==token)return;details.set(id,event);if(event.manager)await loadRecords(id,false);if(active()&&generation===sequence){
      const node=host.querySelector(`details[data-event="${id}"]`);if(!node)return;
      const template=document.createElement('template');template.innerHTML=eventMarkup(event,data.balance,node.open);
      const oldRecords=node.querySelector('.activity-records'),nextBody=template.content.querySelector('.activity-event-body');
      const focus=oldRecords?.contains(document.activeElement)?document.activeElement:null;
      if(oldRecords&&nextBody.querySelector('.activity-records'))nextBody.querySelector('.activity-records').replaceWith(oldRecords);
      node.querySelector('summary').innerHTML=template.content.querySelector('summary').innerHTML;
      node.querySelector('.activity-event-body').replaceWith(nextBody);
      if(oldRecords){updateRecords(id);focus?.focus();}
      bindEventControls(node);
    }}
    catch(e){if(active()&&generation===sequence&&detailLoads.get(id)===token&&e.name!=='AbortError')toast(e.message,true);}
    finally{if(detailLoads.get(id)===token)detailLoads.delete(id);}
  }
  function lotMarkup(event,lot,funds){
    const draw=event.kind==='draw';
    const isOpen=event.state==='published'&&lot.state==='open'&&event.server_time>=event.starts_at&&event.server_time<lot.ends_at;
    const minimum=lot.highest_price?lot.highest_price+lot.step:lot.price;
    const canPay=draw?funds.available>=lot.price:funds.available>=0&&funds.available+(lot.leading?lot.highest_price:0)>=minimum;
    const shortfall=Math.max(0,(draw?lot.price:minimum)-(funds.available+(!draw&&lot.leading?lot.highest_price:0)));
    let reason=!isOpen?(lot.state==='open'?'未开放或已截止':labels[lot.state]):event.manager?'管理人员不参与本人管理的活动':!data.eligible?'当前身份不能消费积点':!event.in_scope?'当前不在参与范围':draw&&lot.remaining<=0?'已售完':draw&&lot.my_count>=lot.per_person?'已达到参与上限':!canPay?`可用积点不足，还差${shortfall}点`:'';
    return `<section class="activity-lot"><div class="activity-lot-title"><h3>${esc(lot.name)}</h3><strong>${draw?lot.price:lot.state==='settled'?lot.highest_price:minimum}<small> ${draw?'积点 / 次':lot.state==='settled'?'积点成交':'积点起出价'}</small></strong></div>
      <ul class="activity-prizes">${lot.prizes.map(p=>`<li>${p.photo_url?`<button type="button" class="activity-prize-photo" data-prize-photo-view data-photo-name="${esc(p.name)}" data-photo-url="${esc(p.photo_url)}" aria-label="查看${esc(p.name)}照片"><img src="${esc(portalPath(p.photo_url))}" alt="${esc(p.name)}" loading="lazy" decoding="async"></button>`:''}<b>${esc(p.name)}</b> × ${p.quantity}${p.description?`<span>${esc(p.description)}</span>`:''}</li>`).join('')}</ul>
      <p class="field-hint">${draw?`剩余 ${Math.max(0,lot.remaining)} 份 · 每人限 ${lot.per_person} 次 · 你已参与 ${lot.my_count} 次`:`当前最高 ${lot.highest_price||'暂无'} · 最低加价 ${lot.step} 点${lot.leading?' · 你当前领先':''}`}<br>${draw?'开奖':'竞拍截止'} ${stamp(lot.ends_at)}${!draw?` · ${lot.extend_seconds?'最后1分钟出价自动顺延1分钟':'不自动延时'}`:''}</p>
      <div class="activity-actions"><button type="button" class="primary" data-action="${draw?'join':'bid'}" data-lot="${lot.id}" ${reason?'disabled':''}>${esc(reason||(draw?'消耗积点参与':'我要出价'))}</button>${event.owner&&lot.state==='open'?`<button type="button" class="secondary" data-action="cancel" data-lot="${lot.id}">取消此${draw?'奖池':'拍品'}</button>`:''}</div>
      ${lot.awards.length?`<div class="activity-awards"><h4>${event.manager?'参与及发奖记录':'我的号码与奖品'}</h4>${lot.awards.map(a=>awardMarkup(event,a)).join('')}</div>`:''}
      ${!draw&&lot.bids.length?`<details class="activity-bids"><summary>${event.manager?'最近竞价记录':'我的最近出价'}</summary>${lot.bids.map(b=>`<p>${esc(b.employee_name||'我')} · ${b.amount} 积点 · ${stamp(b.at)}</p>`).join('')}</details>`:''}</section>`;
  }
  async function action(button){
    const type=button.dataset.action;
    if(type==='view_receipt'){try{await viewActivityReceipt(button.dataset.receipt);}catch(e){if(active()&&e.name!=='AbortError')toast(e.message,true);}return;}
    if(type==='sign'){try{if(await signActivityAward(Number(button.dataset.award))&&active())await load();}catch(e){if(active()&&e.name!=='AbortError')toast(e.message,true);}return;}
    const awardId=Number(button.dataset.award);
    const event=[...details.values()].find(e=>e.id===Number(button.dataset.event)||e.lots.some(l=>l.id===Number(button.dataset.lot)||l.awards.some(a=>a.id===awardId))||recordViews.get(e.id)?.data?.items.some(a=>a.id===awardId));
    const award=recordViews.get(event?.id)?.data?.items.find(a=>a.id===awardId)||event?.lots.flatMap(l=>l.awards).find(a=>a.id===awardId);
    const lot=event?.lots.find(l=>l.id===Number(button.dataset.lot)||l.id===award?.lot_id||l.awards.some(a=>a.id===awardId));
    const requestKey=key();
    let path,body='',title=button.textContent,subtitle='',confirm='确认';
    if(type==='join'){
      path=`lots/${lot.id}/join`;body=input('count','参与次数','number',1,`min="1" max="${Math.min(lot.remaining,lot.per_person-lot.my_count,Math.floor(data.balance.available/lot.price))}"`);
      subtitle=`${esc(lot.name)}：每次${lot.price}积点，每次必得一份奖品。`;confirm='确认消耗积点';
      body+='<p data-cost class="field-hint"></p>';
    }else if(type==='bid'){
      path=`lots/${lot.id}/bid`;body=input('amount','本次出价积点','number',lot.highest_price?lot.highest_price+lot.step:lot.price,'min="1"');
      subtitle='成功后冻结对应积点；被超价释放，成交后扣点。出价不可自行撤回。';confirm='确认出价并冻结';
    }else if(['issue','refund','expire','correct','recall','request_sign','return_refund','dispute'].includes(type)){
      path=`awards/${award.id}/${type==='dispute'?'dispute':'fulfill'}`;subtitle=`凭证 #${award.id} · ${esc(award.employee_name||'')} · ${esc(award.employee_no||'')} · ${esc(award.prize)} · ${award.cost}积点`;
      body=input('reason',type==='issue'?'发放备注':'处理原因','text',type==='issue'?'奖品已交付，等待员工本人签收':'');
      if(type==='issue')body+='<p class="field-hint">请实际交付后确认。此操作不会完成核销，仍需员工本人手机签字。</p>';
      if(['recall','return_refund'].includes(type))body+='<label class="receipt-confirm"><input type="checkbox" name="verified_return">已核实奖品未交付，或实物已收回</label>';
      if(type==='return_refund')body+=`<p class="field-hint">将退回${award.cost}积点；原签名和档案永久保留，不能删除或覆盖。</p>`;
      if(type==='refund')body+=`<p class="field-hint">将退回该次实际消耗的${award.cost}积点。该奖品不能再核销，库存不会自动重新出售。</p>`;
    }else if(type==='cancel'||type==='cancel-event'){
      path=type==='cancel'?`lots/${lot.id}/cancel`:`${event.id}/cancel`;body=input('reason','取消原因');subtitle='仅限尚未开奖或成交部分。抽奖退回参与积点，拍卖释放冻结积点。';
    }else{
      path=`${event.id}/${type}`;subtitle=type==='publish'?'请核对参与范围、奖品库存、时间和规则。发布后不能修改核心交易规则。':'待领取等事项全部处理完毕后才能结案。';
    }
    const success=await formModal(title,subtitle,body,confirm,{lockWhileSubmitting:true,
      onReady(form){if(type==='join'){const update=()=>{form.querySelector('[data-cost]').textContent=`本次消耗 ${(Number(form.elements.count.value)||0)*lot.price} 积点；获得对应数量的奖品。`;};form.elements.count.oninput=update;update();}},
      async onSubmit(form){const payload={request_key:requestKey};if(type==='join')payload.count=integer(form.elements.count.value);if(type==='bid')payload.amount=integer(form.elements.amount.value);
        if(form.elements.verified_return){if(!form.elements.verified_return.checked)throw new Error('请先核实奖品交付或收回情况');payload.verified_return=true;}if(form.elements.reason)payload.reason=required(form.elements.reason.value);if(['issue','refund','expire','correct','recall','request_sign','return_refund','dispute'].includes(type)&&type!=='dispute')payload.action=type;
        await api('/api/activities/'+path,json('POST',payload));toast('操作已完成');}
    });
    if(success&&active())await load();
  }
  async function load(quiet=false){
    const current=++sequence;
    try{
      if(filter==='pending'){
        const result=await api('/api/activities/pending-redemptions?'+new URLSearchParams({page}));
        if(!active()||current!==sequence)return;
        if(page>1&&!result.items.length){page=Math.max(1,Math.ceil(result.total/result.page_size));return load(quiet);}
        const signature=JSON.stringify(result);
        if(displayedFilter!=='pending'||host.dataset.pendingSignature!==signature){
          paintPending(result);
          host.dataset.pendingSignature=signature;
        }
        displayedFilter='pending';
        if(revealDestination){host.scrollIntoView({block:'start'});revealDestination=false;}
        return;
      }
      const query=new URLSearchParams({page:focusedEvent?1:page,view:filter});
      if(focusedEvent)query.set('event_id',String(focusedEvent));
      const result=await api('/api/activities?'+query);
      if(!active()||current!==sequence)return;
      if(!focusedEvent&&page>1&&!result.items.length){page=Math.max(1,Math.ceil(result.total/result.page_size));return await load(quiet);}
      const samePage=data&&data.page===result.page&&displayedFilter===filter&&displayedEvent===focusedEvent
        &&data.items.map(e=>e.id).join(',')===result.items.map(e=>e.id).join(',');
      data=result;displayedFilter=filter;displayedEvent=focusedEvent;detailLoads.clear();
      if(samePage){
        host.querySelector('.activity-wallet').innerHTML=`<strong>${esc(data.balance.available)}<small> 可用积点</small></strong><span>冻结 ${esc(data.balance.frozen)} 点</span>`;
        const signFilter=host.querySelector('[data-filter="sign"]');
        if(signFilter)signFilter.textContent=`待本人签收 ${data.pending_signatures||0}`;
        for(const event of result.items){
          const node=host.querySelector(`details[data-event="${event.id}"]`);if(!node)continue;
          const template=document.createElement('template');template.innerHTML=eventMarkup(event,data.balance,node.open);
          node.querySelector('summary').innerHTML=template.content.querySelector('summary').innerHTML;
          if(!node.open){details.delete(event.id);node.querySelector('.activity-event-body').replaceWith(template.content.querySelector('.activity-event-body'));bindEventControls(node);}
        }
        const nav=host.querySelector('nav[aria-label="活动分页"]');
        if(nav){
          const template=document.createElement('template');template.innerHTML=pager(page,data.total,data.page_size,'list');
          const next=[...template.content.querySelectorAll('button')];nav.querySelectorAll('button').forEach((button,i)=>{button.disabled=next[i].disabled;button.dataset.page=next[i].dataset.page;});
          nav.querySelector('span').textContent=template.content.querySelector('span').textContent;
        }
      }
      else{details.clear();paint();}
      await Promise.all(result.items.filter(e=>opened.has(e.id)).map(e=>loadDetail(e.id)));
      if(active()&&current===sequence&&revealDestination){host.scrollIntoView({block:'start'});revealDestination=false;}
    }
    catch(error){if(!active()||current!==sequence||error.name==='AbortError')return;if(data){if(!quiet)toast(error.message,true);}else{host.innerHTML=`<section class="panel"><p role="alert">${esc(error.message)}</p><button type="button" class="secondary" data-retry>重新加载</button></section>`;host.querySelector('[data-retry]').onclick=()=>load();}}
  }
  async function poll(){if(!active())return;if(!document.hidden&&!document.querySelector('.modal-overlay')&&!host.querySelector('input:focus,textarea:focus,select:focus'))await load(true);if(active())pageTimeout(poll,15000);}
  await load();if(active())pageTimeout(poll,15000);
}
