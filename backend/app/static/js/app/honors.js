// Account-backed, self-only honor collection. Presentation never changes scores.
import { api, json } from './api.js';
import { esc, memberHomeMonthLabel } from './format.js';
import { bindCelebration } from './celebration.js';

const skins=[
  ['sea','星海','星环 · 深蓝展台'],['gold','鎏金','压印 · 金属底座'],
  ['moon','月白','拱廊 · 浅色石台'],['ice','冰晶','棱面 · 晶体底座'],
  ['jade','墨玉','玉台 · 细金边框'],['copper','赤铜','铆纹 · 铜质台座'],
  ['forest','林光','叶影 · 木质展台'],['minimal','简洁','留白 · 勋章本体'],
];

function sourceCard(record){
  return `<article class="honor-source"><p>${esc(record.date)} · ${esc(record.category)} · 已确认</p><blockquote>${esc(record.content)}</blockquote>${record.poc_reason?`<p class="honor-poc">${esc(record.poc_reason)}</p>`:''}<small>认可人：${esc(record.recognizer)} · 记录 #${esc(record.id)}</small>${record.cap_reason?`<p class="field-hint">已确认未全部计分：${esc(record.cap_reason)}</p>`:''}</article>`;
}

function mountHonors(host,{month,cardHTML,rank,bindCeremony,onMonth,isCurrent}){
  let disposed=false,cleanup=null,data=null,view='collection',mode='letter',all=false,saving=false,loadToken=0;
  const valid=()=>!disposed&&host.isConnected&&isCurrent();
  const frame=()=>`<section class="honor-hub" aria-label="我的荣誉"><header class="honor-heading"><div><h3>我的荣誉</h3><p>留住每个月，也留住被认可的那一刻。</p></div><span>仅自己可见</span></header><div class="honor-tabs" role="tablist" aria-label="荣誉内容">${[['collection','荣誉典藏'],['memory','高光回顾'],['skins','自由装扮']].map(([id,label])=>`<button type="button" role="tab" id="honor-tab-${id}" data-honor-tab="${id}" aria-selected="${id===view}" aria-controls="honor-view" tabindex="${id===view?0:-1}">${label}</button>`).join('')}</div><div class="honor-content" id="honor-view" role="tabpanel" aria-labelledby="honor-tab-${view}"></div><p class="honor-feedback" role="status" aria-live="polite"></p></section>`;
  function feedback(message){const node=host.querySelector('.honor-feedback');if(node)node.textContent=message;}
  function collection(){
    const history=(data?.history||[]).map(item=>`<button type="button" class="honor-history-item" data-honor-month="${esc(item.month)}"${item.month===month?' aria-current="date"':''}><span>${esc(memberHomeMonthLabel(item.month))}</span><strong>${esc(item.status==='ranked'?item.label:'等待留影')}</strong></button>`).join('');
    return `<div class="honor-layout"><div class="honor-display" data-honor-skin="${esc(data?.skin||'sea')}">${cardHTML}</div><aside class="honor-history"><h4>每个月，都有自己的留影</h4>${history||'<p class="field-hint">正在整理本人历史…</p>'}<p class="field-hint">显示最近12个月，其他月份可通过首页月份选择查看。</p><button type="button" class="secondary" data-honor-open-memory>看看本月回顾</button></aside></div>`;
  }
  function memory(){
    if(!data)return '<p class="empty">正在整理本月认可…</p>';
    const records=data.records||[],selected=records.find(row=>row.id===data.selected_record_id)||records[0];
    const quote=selected?`${sourceCard(selected)}<p class="field-hint">此处展示原文，可在右侧选择其他记录。</p>`:'<p class="empty">本月暂无已确认的认可，新的记录会留在这里。</p>';
    const letter=data.letter;
    const letterHTML=letter?`<article class="honor-letter"><small>FZ · MONTHLY MEMORY</small><div class="honor-letter-date">${esc(month.slice(5))}<span> / ${esc(month.slice(0,4))}</span></div><h4>${esc(letter.title)}</h4>${letter.paragraphs.map(text=>`<p>${esc(text)}</p>`).join('')}<footer>FZ团队工作台 · ${esc(memberHomeMonthLabel(month))}留影</footer><details><summary>这段寄语参考了${letter.source_ids.length}条认可</summary>${letter.source_ids.map(id=>records.find(row=>row.id===id)).filter(Boolean).map(sourceCard).join('')}</details><button type="button" class="secondary" data-honor-favorite aria-pressed="${data.favorite}">${data.favorite?'已收藏本月回顾':'收藏本月回顾'}</button><p class="field-hint">根据本月有效认可整理，原文可逐条核对。</p></article>`:`<div class="honor-letter honor-empty"><h4>${data.letter_state==='waiting'?'本月，等待留影':'等待一份新的认可'}</h4><p>${data.letter_state==='waiting'?'本月回顾将在有效月结后生成；重开期间暂不展示寄语。':'本月暂无已确认的有效认可，暂不生成月度寄语。'}</p></div>`;
    return `<div class="honor-memory-layout"><section><div class="honor-memory-tabs">${[['letter','月度寄语'],['quote','精选原文']].map(([id,label])=>`<button type="button" class="secondary" data-honor-mode="${id}" aria-pressed="${mode===id}">${label}</button>`).join('')}</div>${mode==='letter'?letterHTML:quote}</section><aside><h4>本月认可 · ${records.length}条</h4>${records.slice(0,all?records.length:3).map(row=>`<button type="button" class="honor-record-choice" data-honor-record="${row.id}" aria-pressed="${selected?.id===row.id}"><small>${esc(row.date)} · ${esc(row.category)} · 已确认</small><span>${esc(row.content)}</span>${row.poc_reason?`<span>${esc(row.poc_reason)}</span>`:''}<small>${selected?.id===row.id?'已选为高光原文':'选择这条原文'}${row.cap_reason?' · 封顶认可仍保留':''}</small></button>`).join('')}${records.length>3?`<button type="button" class="secondary" data-honor-all>${all?'收起':'查看全部'+records.length+'条认可'}</button>`:''}<p class="field-hint">具体行动与场景优先展示；全部有效认可保留，最终由你选择。此顺序不参与计分或排名。</p></aside></div>`;
  }
  function wardrobe(){
    return `<div class="honor-layout"><div class="honor-display" data-honor-skin="${esc(data?.skin||'sea')}">${cardHTML}</div><aside><div class="honor-skin-grid">${skins.map(([id,name,desc])=>`<button type="button" class="honor-skin-choice" data-honor-skin-choice="${id}" aria-pressed="${data?.skin===id}"><i class="honor-swatch" data-swatch="${id}" aria-hidden="true"></i><span><strong>${name}</strong><small>${desc}</small></span></button>`).join('')}</div><p class="field-hint">八套装扮均可直接使用，由喜好决定，与勋章档位无关。未生成勋章时也能预选装扮。</p></aside></div>`;
  }
  function paint({autoplay=false}={}){
    if(!valid())return;
    cleanup?.();cleanup=null;
    host.innerHTML=frame();
    const target=host.querySelector('.honor-content');
    target.innerHTML=view==='memory'?memory():view==='skins'?wardrobe():collection();
    if(saving)host.querySelectorAll('[data-honor-record],[data-honor-favorite],[data-honor-skin-choice]').forEach(button=>button.disabled=true);
    if(view!=='memory'&&rank.status==='ranked'){
      const button=document.createElement('button');button.type='button';button.className='secondary';button.dataset.celebrate='';button.textContent='庆祝一下 · 随机';target.querySelector('.honor-display')?.append(button);
      const ceremony=bindCeremony(target,{autoplay}),celebration=bindCelebration(target);cleanup=()=>{ceremony?.();celebration();};
    }
  }
  async function load(){
    const token=++loadToken;
    try{const result=await api('/api/self-honors?month='+encodeURIComponent(month));if(!valid()||token!==loadToken)return;if(result.month!==month)throw new Error('返回的数据月份不一致');data=result;
      // Initial data fills the history and skin without restarting an active ceremony.
      const display=host.querySelector('.honor-display');if(display)display.dataset.honorSkin=data.skin;
      if(view==='collection'){const current=host.querySelector('.honor-history');const temp=document.createElement('div');temp.innerHTML=collection();current?.replaceWith(temp.querySelector('.honor-history'));}else paint();
      feedback('');
    }catch(error){if(valid()&&error.name!=='AbortError'){feedback('回顾读取失败：'+error.message);const node=host.querySelector('.honor-feedback');const retry=document.createElement('button');retry.type='button';retry.className='secondary';retry.dataset.honorRetry='';retry.textContent='重试读取';node.append(' ',retry);}}
  }
  async function save(changes){
    if(saving||!data)return;saving=true;++loadToken;host.setAttribute('aria-busy','true');
    const focused=document.activeElement;
    const restore=focused?.hasAttribute('data-honor-favorite')?'[data-honor-favorite]':focused?.dataset.honorRecord?`[data-honor-record="${Number(focused.dataset.honorRecord)}"]`:null;
    host.querySelectorAll('[data-honor-record],[data-honor-favorite],[data-honor-skin-choice]').forEach(button=>button.disabled=true);
    try{const result=await api('/api/self-honors?month='+encodeURIComponent(month),json('PUT',changes));if(!valid())return;if(result.month!==month)throw new Error('返回的数据月份不一致');data=result;
      if(changes.skin){host.querySelector('.honor-display')?.setAttribute('data-honor-skin',data.skin);host.querySelectorAll('[data-honor-skin-choice]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.honorSkinChoice===data.skin)));}
      else{if(changes.selected_record_id)mode='quote';paint();}
      feedback('已保存，仅自己可见');
    }catch(error){if(valid())feedback('保存失败：'+error.message+'，请重试。');}
    finally{saving=false;if(valid()){host.removeAttribute('aria-busy');host.querySelectorAll('[data-honor-record],[data-honor-favorite],[data-honor-skin-choice]').forEach(button=>button.disabled=false);if(restore)host.querySelector(restore)?.focus({preventScroll:true});}}
  }
  function click(event){
    const button=event.target.closest('button');if(!button||!host.contains(button))return;
    if(button.dataset.honorTab){view=button.dataset.honorTab;paint();host.querySelector(`[data-honor-tab="${view}"]`).focus({preventScroll:true});}
    else if(button.hasAttribute('data-honor-open-memory')){view='memory';paint();}
    else if(button.dataset.honorSkinChoice)save({skin:button.dataset.honorSkinChoice});
    else if(button.dataset.honorRecord)save({selected_record_id:Number(button.dataset.honorRecord)});
    else if(button.hasAttribute('data-honor-favorite'))save({favorite:!data.favorite});
    else if(button.dataset.honorMode){mode=button.dataset.honorMode;paint();}
    else if(button.hasAttribute('data-honor-all')){all=!all;paint();}
    else if(button.dataset.honorMonth&&button.dataset.honorMonth!==month)onMonth(button.dataset.honorMonth);
    else if(button.hasAttribute('data-honor-retry'))load();
  }
  function keydown(event){if(!event.target.matches('[data-honor-tab]')||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const tabs=[...host.querySelectorAll('[data-honor-tab]')],index=tabs.indexOf(event.target),next=event.key==='Home'?0:event.key==='End'?2:(index+(event.key==='ArrowRight'?1:2))%3;tabs[next].click();}
  host.addEventListener('click',click);host.addEventListener('keydown',keydown);
  paint({autoplay:true});load();
  return ()=>{disposed=true;++loadToken;cleanup?.();host.removeEventListener('click',click);host.removeEventListener('keydown',keydown);};
}

export { mountHonors };
