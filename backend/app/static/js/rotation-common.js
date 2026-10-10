'use strict';
// 轮岗页面（主管看板、个人页、休息室大屏）共用：接口、提示、弹窗、时钟、实时同步、线卡片。

const portalBasePath = location.pathname === '/recognition' || location.pathname.startsWith('/recognition/') ? '/recognition' : '';
function portalPath(path) {
  const value = String(path || '');
  if (!value.startsWith('/') || value.startsWith('//')) return value;
  if (!portalBasePath || value.startsWith(`${portalBasePath}/`)) return value;
  return `${portalBasePath}${value}`;
}

const esc = value => String(value ?? '').replace(/[&<>'"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]));
const pad2 = value => String(value).padStart(2, '0');
function hm(minute) {
  if (minute === null || minute === undefined) return '--:--';
  const m = ((Math.round(minute) % 1440) + 1440) % 1440;
  return `${pad2(Math.floor(m / 60))}:${pad2(m % 60)}`;
}
function hms(minute) {
  const s = Math.max(0, Math.floor(minute * 60));
  return `${pad2(Math.floor(s / 3600) % 24)}:${pad2(Math.floor(s / 60) % 60)}:${pad2(s % 60)}`;
}

const STATE_NAME = {
  notyet: '未到班次', op: 'OP', pending: 'OP结束', onpost: '在岗', walkback: '下线途中', rest: '休息', meal: '吃饭',
  ready: '待出发', heading: '前往中', away: '暂离', done: '已下班', excluded: '不轮岗',
};

const RT = {
  data: null,
  fetchedAt: 0,
  loading: false,
  queued: false,
  modalOpen: false,
  pressing: false,
  render: () => {},
  load: async () => {},
};

function departureEarly() {
  return RT.data?.settings?.departEarly ?? 1;
}

function canDepartNow(p, n) {
  return p.state === 'ready' && !!p.assign && n >= (p.readyAt ?? n) - 0.01
    && n >= (p.assign.notBefore ?? 0) - 0.01
    && n >= p.assign.departAt - departureEarly() - 0.01;
}

// 基础休息结束后，等待定时开岗的人员仍显示在休息区。
function poolState(p, n) {
  if (p.state === 'ready' && ((p.readyAt ?? n) > n + 0.01 || (p.assign && !canDepartNow(p, n)))) return 'rest';
  return p.state;
}

function restUntil(p) {
  return Math.max(p.readyAt ?? 0, p.assign?.departAt ?? 0);
}

async function rtApi(path, options = {}) {
  let res;
  try {
    res = await fetch(portalPath(path), { credentials: 'same-origin', ...options });
  } catch (_) {
    throw new Error('网络连接失败，请检查网络后重试');
  }
  const body = await res.json().catch(() => ({}));
  if (res.status === 401) {
    location.href = portalPath(document.body.classList.contains('rotation-screen') ? '/login' : '/');
    throw new Error('请重新进入轮岗');
  }
  if (!res.ok) {
    const detail = body.detail;
    throw new Error(typeof detail === 'string' ? detail : (detail && detail.message) || '操作失败，请稍后重试');
  }
  return body;
}
const rtPost = (path, body) => rtApi(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });

let toastTimer = null;
function toast(message, bad = false) {
  const el = document.getElementById('toast');
  clearTimeout(toastTimer);
  el.textContent = message;
  el.classList.toggle('error', bad);
  el.setAttribute('role', bad ? 'alert' : 'status');
  el.classList.add('show');
  toastTimer = setTimeout(() => el.classList.remove('show'), bad ? 6000 : 2600);
}

// 当前分钟：模拟时钟按倍速走，暂停时不动。
function nowMin() {
  const clock = RT.data && RT.data.clock;
  if (!clock) return 0;
  if (clock.paused) return clock.minute;
  return Math.min(1440, clock.minute + (Date.now() - RT.fetchedAt) / 60000 * (clock.speed || 1));
}

function renderClock() {
  const el = document.getElementById('rtClock');
  const clock = RT.data && RT.data.clock;
  if (!el || !clock) return;
  const sim = clock.mode === 'sim';
  el.innerHTML = `<span class="rt-date">${esc(clock.date)}</span><b>${hms(nowMin())}</b>${sim ? `<span class="rt-sim">${clock.paused ? '模拟 · 暂停' : `模拟 ×${clock.speed}`}</span>` : ''}`;
}

// ---------------------------------------------------------------- 弹窗

function closeModal() {
  document.getElementById('rtModal').innerHTML = '';
  RT.modalOpen = false;
  RT.render();
}
function openModal(html, bind) {
  const host = document.getElementById('rtModal');
  host.innerHTML = `<div class="modal-overlay rt-overlay"><section class="modal-card rt-card" role="dialog" aria-modal="true">${html}</section></div>`;
  RT.modalOpen = true;
  const overlay = host.querySelector('.rt-overlay');
  overlay.addEventListener('click', event => { if (event.target === overlay) closeModal(); });
  host.querySelectorAll('[data-close]').forEach(button => { button.onclick = closeModal; });
  if (bind) bind(host);
  const first = host.querySelector('input, select, textarea, button.primary, button');
  if (first) first.focus();
}
document.addEventListener('keydown', event => { if (event.key === 'Escape' && RT.modalOpen) closeModal(); });

function confirmBox(title, message, okText, run, { warn = false, autoCancel = 0 } = {}) {
  let timer = null;
  openModal(`<h3>${title}</h3><div class="modal-message">${message}</div><div class="modal-actions"><button type="button" class="secondary" data-close>取消</button><button type="button" class="${warn ? 'danger' : 'primary'}" data-ok>${esc(okText)}</button></div>`, host => {
    const ok = host.querySelector('[data-ok]');
    ok.onclick = async () => {
      clearTimeout(timer);
      ok.disabled = true;
      const done = await run();
      if (done !== false) closeModal(); else ok.disabled = false;
    };
    if (autoCancel) timer = setTimeout(() => { if (RT.modalOpen) closeModal(); }, autoCancel);
  });
}

function formBox(title, fields, run, { warn = false, okText = '确定' } = {}) {
  openModal(`<h3>${title}</h3><form class="form-stack" data-form>${fields}<div class="modal-actions"><button type="button" class="secondary" data-close>取消</button><button type="submit" class="${warn ? 'danger' : 'primary'}">${esc(okText)}</button></div></form>`, host => {
    host.querySelector('[data-form]').onsubmit = async event => {
      event.preventDefault();
      const values = Object.fromEntries(new FormData(event.target).entries());
      if (await run(values) !== false) closeModal();
    };
  });
}

async function act(path, body, okMessage) {
  try {
    await rtPost(path, body);
    if (okMessage) toast(okMessage);
    await RT.load();
    return true;
  } catch (error) {
    toast(error.message, true);
    return false;
  }
}

// ---------------------------------------------------------------- 实时同步

function scheduleLoad() {
  if (RT.loading) { RT.queued = true; return; }
  RT.loading = true;
  RT.load().catch(error => toast(error.message, true)).finally(() => {
    RT.loading = false;
    if (RT.queued) { RT.queued = false; scheduleLoad(); }
  });
}

function connectLive() {
  let poll = null;
  const startPolling = () => { if (!poll) poll = setInterval(scheduleLoad, 5000); };
  if (!window.EventSource) { startPolling(); return; }
  const source = new EventSource(portalPath('/api/rotation/stream'));
  source.onmessage = () => { scheduleLoad(); if (poll) { clearInterval(poll); poll = null; } };
  source.onerror = () => startPolling();
}

// 手指按下期间不重绘，避免刷新吞掉点击。
document.addEventListener('pointerdown', () => { RT.pressing = true; }, true);
document.addEventListener('pointerup', () => { setTimeout(() => { RT.pressing = false; }, 150); }, true);
document.addEventListener('pointercancel', () => { RT.pressing = false; }, true);

function startLocalTicker() {
  setInterval(() => {
    renderClock();
    if (!RT.modalOpen && !RT.pressing) RT.render(true);
  }, 1000);
}

// ---------------------------------------------------------------- 线卡片

function personOf(pid) {
  return RT.data && RT.data.day ? RT.data.day.persons.find(p => p.pid === pid) : null;
}

function goText(p) {
  const a = p.assign;
  if (!a) return '';
  if (a.mode === 'push7') return `→ ${esc(a.line)} <small>推7点 替 ${esc(a.targetName || '')}</small>`;
  if (a.mode === 'chain') return `→ ${esc(a.line)} <small>${esc(a.why || '推下班')} 替 ${esc(a.targetName || '')}</small>`;
  return `→ ${esc(a.line)}`;
}

function preparingText(kind) {
  return kind === '推7点' ? '准备休息' : kind === '推出圈' ? '准备出圈' : kind ? '准备下班' : '';
}

function badges(p) {
  const out = [];
  if (p.mealEligible && !p.ate) out.push('<span class="rt-badge meal">未休饭</span>');
  if (p.breakKind === 'meal_rest' && p.state === 'rest') out.push('<span class="rt-badge rest">饭后剩余休息时间</span>');
  if (p.preparing) out.push(`<span class="rt-badge prep">${preparingText(p.preparing)}</span>`);
  if (p.flags.includes('推7点下来')) out.push('<span class="rt-badge p7">推7点下来</span>');
  if (p.flags.includes('推7点') && p.state !== 'onpost') out.push('<span class="rt-badge p7">推7点</span>');
  if (p.flags.includes('送失物')) out.push('<span class="rt-badge lost">送失物</span>');
  if (p.tags.includes('嚎叫节')) out.push('<span class="rt-badge howl">嚎叫节</span>');
  return out.length ? `<span class="rt-badges">${out.join('')}</span>` : '';
}

function lineCard(L, n, ops) {
  const persons = RT.data.day.persons;
  const incoming = persons.filter(p => p.assign && p.assign.line === L.id).sort((a, b) => a.assign.departAt - b.assign.departAt);
  const openIdx = L.posts.map((x, i) => (x.open ? i : -1)).filter(i => i >= 0);
  const exitIndex = openIdx.length ? openIdx[openIdx.length - 1] : -1;
  const rows = L.posts.map((x, i) => {
    let cls = 'rt-post', who, mins = '';
    if (!L.active) { cls += ' closed'; who = '待命'; }
    else if (!x.open) { cls += ' closed'; who = x.openAt && !x.opened ? `${esc(x.openAt)} 开` : '未开'; }
    else if (!x.occ) { cls += ' vacant'; who = '空岗'; }
    else {
      const p = personOf(x.occ);
      who = esc(p ? p.name : x.occ);
      if (p && p.flags.includes('7点岗') && !p.flags.includes('推7点下来')) who += '<span class="rt-badge p7">7点</span>';
      if (p && p.preparing) { cls += ' prep'; who += `<span class="rt-badge prep">${preparingText(p.preparing)}</span>`; }
      if (p && p.state === 'notyet') mins = `${hm(p.start)} 开始`;
      else if (p && p.lineStart !== null && p.lineStart !== undefined) mins = `${Math.max(0, Math.floor(n - p.lineStart))} 分`;
    }
    if (i === exitIndex) cls += ' exit';
    const attrs = ops ? ` data-post="${esc(L.id)}#${i}" tabindex="0" role="button"${x.occ && personOf(x.occ)?.state === 'notyet' ? ` draggable="true" data-drag-pid="${esc(x.occ)}"` : ''}` : '';
    return `<div class="${cls}"${attrs}><span class="rt-pn">${esc(x.name)}</span><span class="rt-who-name">${who}</span><span class="rt-mins">${mins}</span></div>`;
  }).join('');
  const inc = incoming.length ? `<div class="rt-incoming">即将进线：${incoming.slice(0, 3).map(p => `<b>${esc(p.name)}</b> ${p.state === 'heading' ? '前往中' : hm(p.assign.departAt)}`).join('，')}${incoming.length > 3 ? ` 等 ${incoming.length} 人` : ''}</div>` : '';
  const occupied = L.posts.filter(x => x.open && x.occ).length;
  const opened = L.posts.filter(x => x.open).length;
  const head = ops ? ` data-line="${esc(L.id)}" tabindex="0" role="button"` : '';
  return `<div class="rt-line${L.active ? '' : ' off'}"><div class="rt-line-head g-${esc(L.group || '')}"${head}><span class="rt-lid">${esc(L.id)}</span><span>${L.active ? `${occupied}/${opened} 岗` : '待命线'}</span></div>${rows}${inc}</div>`;
}
