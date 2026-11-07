import { api } from './api.js';
import { bindImagePreviews, imagePreviewButton } from './files.js';
import { esc, fmt, memberHomeMonthLabel, portalPath } from './format.js';

const endpoint = '/api/recognition-inbox';
const unreadLabel = count => `${count} 封未读`;
const senderLabel = item => [item.sender, item.sender_role].filter(Boolean).join(' · ');

function envelope(item, recipient) {
  return `<div class="rf-curtain" data-letter-envelope>
    <p class="rf-eyebrow">${esc(item.recognition_type)}${item.reissued?' · 重新发卡':''}</p>
    <h2>一封写给你的认可</h2>
    <p class="rf-caption">${esc(senderLabel(item))}</p>
    <button type="button" class="rf-open" data-unseal aria-label="拆开信封">
      <span class="rf-envelope" aria-hidden="true">
        <span class="rf-base"></span>
        <span class="rf-hinge"><span class="rf-flap"></span><span class="rf-lining"></span></span>
        <span class="rf-insert"><span>一封认可</span><strong>致 ${esc(recipient)}</strong><span class="rf-rule"></span></span>
        <span class="rf-pocket"></span><span class="rf-front-name">致 ${esc(recipient)}</span>
        <span class="rf-seal">FZ</span>
      </span>
      <span class="rf-tap">轻触拆封 <span aria-hidden="true">↗</span></span>
    </button>
  </div>`;
}

function letterPaper(detail, recipient) {
  if(detail.cancelled){
    return `<div class="rf-letterhead"><p class="rf-eyebrow">撤回通知</p><h2>原认可已撤回</h2>
      <p class="rf-caption">${esc([senderLabel(detail),detail.recognition_type,detail.cancelled_at?.replace('T',' ')].filter(Boolean).join(' · '))}</p></div>
      <p>原签卡已取消，原记录的分值不再计入积分。</p>
      ${detail.cancel_reason?`<blockquote><small>撤回原因</small><p>${esc(detail.cancel_reason)}</p></blockquote>`:''}`;
  }
  const meta = [senderLabel(detail), detail.recognition_date, detail.venue].filter(Boolean).join(' · ');
  const score = Number(detail.credited_score);
  const scoreLabel = score > 0 ? `+${fmt(score)} 分` : '0.00 分';
  const scoreMonth = memberHomeMonthLabel(detail.recognition_month);
  const image = detail.image_url && detail.image_preview_kind === 'image'
    ? `<figure><img src="${esc(portalPath(detail.image_url+'?preview=1'))}" alt="认可照片"><figcaption>${imagePreviewButton(detail.image_url,'查看大图')}</figcaption></figure>`
    : '';
  return `<div class="rf-letterhead">
      <p class="rf-eyebrow">${esc(detail.recognition_type)}${detail.reissued?' · 重新发卡':''}</p>
      <h2>${esc(recipient)}，收到一份认可</h2>
      <p class="rf-caption">${esc(meta)}</p>
      ${detail.reissued?'<p class="rf-caption">原签卡已撤回，请以本次重新发出的认可为准。</p>':''}
      ${detail.signer && detail.signer!==detail.sender ? `<p class="rf-caption">签卡人 ${esc(detail.signer)}</p>` : ''}
      ${detail.poc_period ? `<p class="rf-caption">认可周期 ${esc(detail.poc_period)}</p>` : ''}
    </div>
    <blockquote><small>${detail.poc_period?'特别贡献原因':'认可原文'}</small><p>${esc(detail.content)}</p></blockquote>
    ${image}
    <div class="rf-thanks"><small>感谢寄语</small><p>${esc(detail.thanks)}</p></div>
    <div class="rf-credit"><div><strong>实际增加</strong><small>${esc(scoreMonth)} PR 积分</small></div><strong>${scoreLabel}</strong>
      ${detail.score_note ? `<small class="rf-score-note">${esc(detail.score_note)}</small>` : ''}
    </div>`;
}

function mountRecognitionInbox(host, home, request, recipient) {
  let data;
  let page = 1;
  let view = 'home';
  let generation = 0;
  let openedLetter;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const current = token => host.isConnected && request.isCurrent() && token===generation;
  const focus = selector => host.querySelector(selector)?.focus({preventScroll:true});
  const setView = next => {
    view = next;
    home.hidden = view!=='home';
    return ++generation;
  };

  function renderHomeEntry() {
    const count = data.unread_count;
    host.innerHTML = `<button type="button" class="recognition-mail-entry" data-inbox-entry>
      <span class="recognition-mail-icon" aria-hidden="true"><i></i></span>
      <span class="recognition-mail-copy"><strong>认可收件箱</strong><small>${count?'来自上级的认可，等你拆开':'回看你收到的认可'}</small></span>
      <span class="recognition-mail-tail"><span class="recognition-mail-count">${unreadLabel(count)}</span><span class="recognition-mail-arrow" aria-hidden="true">›</span></span>
    </button>`;
  }

  function renderList() {
    const items = data.items.map(item => `<button type="button" class="recognition-inbox-item" data-letter-id="${item.id}" data-unread="${item.unread}">
      <strong>${esc(item.recognition_type)}${item.cancelled?' · 已撤回':item.reissued?' · 重新发卡':''}</strong><span class="badge ${item.unread?'warn':''}">${item.unread?'未读':'已读'}</span>
      <small>${esc(senderLabel(item))} · ${esc(item.submitted_at.replace('T',' '))}</small>
    </button>`).join('');
    const pageCount = Math.max(1,Math.ceil(data.total/data.page_size));
    host.innerHTML = `<section class="panel recognition-inbox-panel">
      <header><button type="button" class="secondary" data-inbox-home>‹ 返回首页</button><span class="badge warn" role="status">${unreadLabel(data.unread_count)}</span></header>
      <h2>认可收件箱</h2>
      <div class="recognition-inbox-list" aria-label="认可来信列表">${items || '<p class="empty">还没有收到认可来信。</p>'}</div>
      ${pageCount>1 ? `<div class="recognition-inbox-pages">
        <button type="button" class="secondary" data-inbox-page="${data.page-1}" ${data.page===1?'disabled':''}>上一页</button>
        <span>第 ${data.page} / ${pageCount} 页</span>
        <button type="button" class="secondary" data-inbox-page="${data.page+1}" ${data.page===pageCount?'disabled':''}>下一页</button>
      </div>` : ''}
    </section>`;
  }

  async function load(next, nextPage=1, fromEntry=false, moveFocus=true) {
    const token = setView(next);
    if (next==='list') host.innerHTML = '<section class="panel"><button type="button" class="secondary" data-inbox-home>‹ 返回首页</button><p role="status">正在加载来信…</p></section>';
    try {
      const result = await api(`${endpoint}?page=${nextPage}`);
      if (!current(token)) return;
      data = result;
      page = result.page;
      if (fromEntry && data.unread_count===1 && data.items[0]?.unread) {
        open(data.items[0]);
      } else if (next==='list') {
        renderList();
        if (moveFocus) focus('[data-letter-id], [data-inbox-home]');
      } else {
        renderHomeEntry();
        if (moveFocus) focus('[data-inbox-entry]');
      }
    } catch (error) {
      if (!current(token)) return;
      host.innerHTML = `<section class="panel">
        ${next==='list'?'<button type="button" class="secondary" data-inbox-home>‹ 返回首页</button>':''}
        <p class="error" role="alert">${esc(error.message)}</p><button type="button" class="secondary" data-inbox-retry>重试认可收件箱</button>
      </section>`;
      if (moveFocus) focus('[data-inbox-retry]');
    }
  }

  function open(item) {
    setView('letter');
    openedLetter = item;
    host.innerHTML = `<section class="panel recognition-inbox-panel recognition-letter-view">
      <header><button type="button" class="secondary" data-inbox-list>‹ 返回列表</button></header>
      <div class="rf-stage">
        ${envelope(item,recipient)}
        <article class="rf-paper" aria-label="认可详情" hidden></article>
      </div>
      <p data-letter-status class="field-hint" role="status" hidden></p>
      <button type="button" class="secondary rf-replay" data-replay hidden>↻ 重播拆信</button>
    </section>`;
    if (item.unread&&!item.cancelled) focus('[data-unseal]');
    else void reveal(false);
  }

  async function reveal(animate) {
    const token = ++generation;
    const letter = openedLetter;
    const stage = host.querySelector('.rf-stage');
    const curtain = stage.querySelector('[data-letter-envelope]');
    const paper = stage.querySelector('.rf-paper');
    const seal = curtain.querySelector('[data-unseal]');
    const status = host.querySelector('[data-letter-status]');
    const replay = host.querySelector('[data-replay]');
    seal.disabled = true;
    replay.hidden = true;
    status.hidden = false;
    status.textContent = '正在打开来信…';
    try {
      const detail = await api(`${endpoint}/${letter.id}/read`,{method:'POST'});
      if (!current(token)) return;
      status.hidden = true;
      curtain.hidden = false;
      paper.hidden = true;
      const closedHeight = stage.getBoundingClientRect().height;
      paper.innerHTML = letterPaper(detail,recipient);
      paper.hidden = false;
      bindImagePreviews(paper);
      if (animate && !detail.cancelled && !reducedMotion.matches) {
        const openHeight = stage.getBoundingClientRect().height;
        paper.inert = true;
        stage.classList.add('rf-moving');
        stage.animate?.([{height:closedHeight+'px'},{height:openHeight+'px'}],{
          duration:830, delay:130, easing:'cubic-bezier(.22,.7,.2,1)', fill:'backwards',
        });
        const animations = stage.getAnimations?.({subtree:true});
        if (animations) await Promise.all(animations.map(animation=>animation.finished.catch(()=>{})));
        else await new Promise(resolve=>setTimeout(resolve,1550));
        if (!current(token)) return;
      }
      curtain.hidden = true;
      paper.inert = false;
      stage.classList.remove('rf-moving');
      replay.hidden = Boolean(detail.cancelled);
      focus(detail.cancelled?'[data-inbox-list]':'[data-replay]');
    } catch (error) {
      if (!current(token)) return;
      seal.disabled = false;
      curtain.hidden = false;
      paper.hidden = true;
      status.textContent = error.message;
      focus('[data-unseal]');
    }
  }

  host.onclick = event => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.hasAttribute('data-inbox-entry')) void load('list',1,true);
    else if (button.hasAttribute('data-inbox-home')) void load('home');
    else if (button.hasAttribute('data-inbox-list')) void load('list',1);
    else if (button.hasAttribute('data-inbox-page')) void load('list',Number(button.dataset.inboxPage));
    else if (button.hasAttribute('data-inbox-retry')) void load(view,page);
    else if (button.hasAttribute('data-unseal') || button.hasAttribute('data-replay')) void reveal(true);
    else if (button.hasAttribute('data-letter-id')) open(data.items.find(item=>item.id===Number(button.dataset.letterId)));
  };

  void load('home',1,false,false);
}

export { mountRecognitionInbox };
