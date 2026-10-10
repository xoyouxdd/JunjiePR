'use strict';
// 轮岗（测试）：轮岗主管/经理的看板、初始化、名单、记录、设置；模拟 CM/TR 时的个人页和待办。

const $app = document.getElementById('rtApp');
const $tabs = document.getElementById('rtTabs');
const $bar = document.getElementById('rtClockBar');
const TABS = [['board', '看板'], ['draft', '初始化'], ['roster', '名单'], ['records', '记录'], ['settings', '设置']];
let me = null;
let tab = 'board';
try { tab = localStorage.getItem('rotation.tab') || 'board'; } catch (_) { /* 无痕模式不保存 */ }
if (!TABS.some(([id]) => id === tab)) tab = 'board';
const view = { logDate: null, log: null, record: null, recordNo: '', rosterDate: null, roster: null, config: null, accounts: null };

const isManager = () => me && (me.kind === 'supervisor' || me.kind === 'manager');

// 正在输入时不重绘，避免吞掉输入内容。
function editing() {
  const el = document.activeElement;
  return el && $app.contains(el) && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName);
}

// ---------------------------------------------------------------- 顶栏与测试时钟

function renderWho() {
  const who = document.getElementById('rtWho');
  const label = me.kind === 'member' ? `${me.name}（${me.employee_no}）· 模拟 CM/TR` : `${me.name} · ${me.kind_label}`;
  who.textContent = label;
}

function renderClockBar() {
  if (!isManager()) { $bar.hidden = true; return; }
  const c = RT.data.clock;
  const sim = c.mode === 'sim';
  const dates = RT.data.rosterDates || [];
  $bar.hidden = false;
  $bar.innerHTML = `<span class="rotation-test-badge">测试时钟</span>
    <label>日期<input type="date" id="tcDate" value="${esc(c.date)}" list="tcDates"></label>
    <datalist id="tcDates">${dates.map(d => `<option value="${esc(d)}">`).join('')}</datalist>
    <label>时间<input type="time" id="tcTime" value="${hm(nowMin())}"></label>
    <button type="button" class="secondary" data-tc="set">设定模拟时间</button>
    <button type="button" class="danger" data-tc="reset">初始化到04:00（测试）</button>
    ${sim ? `<button type="button" class="secondary" data-tc="${c.paused ? 'resume' : 'pause'}">${c.paused ? '继续' : '暂停'}</button>
    <label>倍速<select id="tcSpeed">${[1, 5, 10, 30, 60].map(s => `<option value="${s}" ${Number(c.speed) === s ? 'selected' : ''}>×${s}</option>`).join('')}</select></label>
    <button type="button" class="secondary" data-tc="jump">快进 10 分钟</button>
    <button type="button" class="primary" data-tc="next">下一步</button>
    <button type="button" class="ghost" data-tc="real">回到真实时间</button>` : '<span class="field-hint">当前为真实时间</span>'}`;
  $bar.querySelectorAll('[data-tc]').forEach(button => {
    button.onclick = () => clockAction(button.dataset.tc);
  });
  const speed = document.getElementById('tcSpeed');
  if (speed) speed.onchange = () => clockAction('speed', { speed: Number(speed.value) });
}

async function clockAction(action, extra = {}) {
  if (action === 'reset' && !extra.confirmed) {
    confirmBox('初始化到04:00', '撤回当天拖拽、加撤岗位及所有运行记录，保留原班表，恢复04:00预排并暂停时钟。仅用于测试。', '确认初始化', () => clockAction('reset', {confirmed: true}), {warn: true});
    return;
  }
  const body = { action, ...extra };
  if (action === 'set') {
    body.date = document.getElementById('tcDate').value;
    body.time = document.getElementById('tcTime').value;
  }
  if (action === 'jump') body.minutes = 10;
  try {
    const out = await rtPost('/api/rotation/test-clock', body);
    if (action === 'next') toast(out.waiting && out.waiting.length ? `需要在大屏操作：${out.waiting.join('；')}` : '已推进到当天结束');
    await RT.load();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderTabs() {
  if (!isManager()) { $tabs.hidden = true; return; }
  $tabs.hidden = false;
  $tabs.innerHTML = TABS.map(([id, label]) => `<button type="button" class="${id === tab ? 'active' : ''}" data-tab="${id}" ${id === tab ? 'aria-current="page"' : ''}>${label}</button>`).join('');
  $tabs.querySelectorAll('[data-tab]').forEach(button => {
    button.onclick = () => {
      tab = button.dataset.tab;
      try { localStorage.setItem('rotation.tab', tab); } catch (_) { /* 忽略 */ }
      renderTabs();
      openTab();
    };
  });
}

function openTab() {
  if (tab === 'records' && !view.log) loadLog(view.logDate || RT.data.clock.date);
  if (tab === 'roster' && !view.roster) loadRoster(view.rosterDate || RT.data.clock.date);
  if (tab === 'settings' && !view.config) loadSettings();
  RT.render();
}

// ---------------------------------------------------------------- 看板

function boardView() {
  const d = RT.data.day;
  if (!d) {
    const draft = RT.data.draft;
    return `<section class="panel rt-empty"><h2>${esc(RT.data.clock.date)} 还没有发布轮岗</h2><p>${draft ? '草稿已生成，到「初始化」检查并发布后看板开始运转。' : '先在「名单」上传名单，再到「初始化」生成当天的初始轮岗。测试时先用上方测试时钟设定日期和时间。'}</p></section>`;
  }
  if (d.status === 'ended') return `<section class="panel rt-empty"><h2>${esc(d.date)} 的轮岗已结束</h2><p>可在「记录」查看当天的操作日志和个人轮岗记录。</p></section>`;
  const n = nowMin();
  const alerts = d.alerts.map(a => `<div class="rt-alert ${esc(a.level)}">${esc(a.msg)}</div>`).join('') + d.notices.slice(-3).map(x => `<div class="rt-alert">${hm(x.t)} ${esc(x.msg)}</div>`).join('');
  return `${alerts ? `<div class="rt-alerts">${alerts}</div>` : ''}<div class="rt-board"><div class="rt-lines">${d.lines.map(L => lineCard(L, n, true)).join('')}</div><div class="rt-pool">${poolView(n)}</div></div>`;
}

function poolView(n) {
  const ps = RT.data.day.persons;
  const by = states => ps.filter(p => states.includes(p.state));
  const section = (title, list, chip) => `<section class="rt-sect"><h2>${title}<span class="rt-n">${list.length}</span></h2><div class="rt-chips${list.length ? '' : ' empty'}">${list.length ? list.map(chip).join('') : '暂无'}</div></section>`;
  const button = (p, sub, extra = '', cls = '') => `<button type="button" class="rt-chip ${cls}" data-pid="${esc(p.pid)}"><span class="rt-nm">${esc(p.name)}${extra}</span><span class="rt-go">${goText(p)}</span><span class="rt-sub">${sub}</span>${badges(p)}</button>`;
  let html = '';
  html += section('待出发', by(['ready']).sort((a, b) => (a.assign ? a.assign.departAt : 9e9) - (b.assign ? b.assign.departAt : 9e9)), p => {
    if (!p.assign) return button(p, '等待分配', '', 'dim');
    const over = n - p.assign.departAt;
    return button(p, over >= 0 ? (over >= 1 ? `已超时 ${Math.floor(over)} 分钟` : '现在出发') : `${hm(p.assign.departAt)} 出发`, '', over >= 2 ? 'late' : over >= 0 ? 'due' : 'wait');
  });
  html += section('待到大屏点去休息', by(['walkback', 'pending']).sort((a, b) => (a.walkbackSince || 0) - (b.walkbackSince || 0)), p => button(p, p.state === 'pending' ? 'OP 结束待定' : `下线 ${Math.max(0, Math.floor(n - (p.walkbackSince || n)))} 分钟（${esc(p.downReason || '推岗')}）`));
  html += section('吃饭区', by(['meal']).sort((a,b)=>a.readyAt-b.readyAt), p=>button(p, `吃饭至 ${hm(p.readyAt)}，剩余 ${Math.max(0,Math.ceil(p.readyAt-n))} 分钟`, '', 'meal'));
  html += section('休息区', by(['rest']).sort((a,b)=>a.readyAt-b.readyAt), p=>button(p, `${p.breakKind === 'meal_rest' ? '饭后剩余休息时间：' : ''}至 ${hm(p.readyAt)}，剩余 ${Math.max(0,Math.ceil(p.readyAt-n))} 分钟`, '', 'rest'));
  html += section('暂离和出圈', by(['away']), p => button(p, `${esc(p.away ? p.away.reason : '')}${p.away && p.away.until !== null && p.away.until !== undefined ? `，${hm(p.away.until)} 回` : ''}`, '', 'dim'));
  const others = by(['op', 'notyet', 'excluded', 'done']).sort((a, b) => a.start - b.start);
  html += `<section class="rt-sect"><h2>其他人员<span class="rt-n">${others.length}</span><span class="rt-hint">OP、未到岗、不轮岗、已下班</span></h2><div class="rt-scroll"><table class="rt-table">${others.map(p => `<tr data-pid="${esc(p.pid)}" tabindex="0"><td>${esc(p.name)}</td><td>${hm(p.start)}-${hm(p.end)}</td><td>${STATE_NAME[p.state] || ''}${p.label ? `：${esc(p.label)}` : ''}</td></tr>`).join('')}</table></div></section>`;
  return html;
}

function personMenu(p) {
  const active = RT.data.day.lines.filter(L => L.active);
  const info = `<dl class="rt-kv">
    <dt>工号</dt><dd>${esc(p.pid)}</dd>
    <dt>班次</dt><dd>${hm(p.start)}-${hm(p.end)}${p.mealEligible ? '（有吃饭）' : '（只休息）'}${p.ate ? '，已吃饭' : ''}</dd>
    <dt>状态</dt><dd>${STATE_NAME[p.state] || ''}${p.line ? `：${esc(p.line)} ${esc(p.post || '')}` : ''}${p.away ? `：${esc(p.away.reason)}` : ''}${p.preparing ? `（${preparingText(p.preparing)}）` : ''}</dd>
    <dt>去向</dt><dd>${p.assign ? `${goText(p)}，${hm(p.assign.departAt)} 出发` : '无'}</dd>
    <dt>今天去过</dt><dd>${esc(p.visited.join('、') || '无')}</dd>
    ${p.absences.length ? `<dt>固定暂离</dt><dd>${p.absences.map(a => `${hm(a.start)}-${hm(a.end)} ${esc(a.label)}${a.missed ? '（错过）' : ''}`).join('；')}</dd>` : ''}
    ${p.note ? `<dt>备注</dt><dd>${esc(p.note)}</dd>` : ''}</dl>`;
  const actions = [];
  if (p.state === 'walkback' || p.state === 'pending') actions.push(['arrive', '代点去休息', 'primary']);
  if (p.state === 'ready' && p.assign) actions.push(['depart', '代点去轮岗', 'primary']);
  if (['rest', 'meal', 'ready'].includes(p.state) && p.state !== 'ready') actions.push(['undo', '撤回去休息', 'secondary']);
  if (['rest', 'meal', 'ready'].includes(p.state)) actions.push(['reassign', '改派去向', 'secondary']);
  if (['walkback', 'pending', 'rest', 'meal', 'ready', 'heading', 'onpost'].includes(p.state)) actions.push(['away', '标记暂离（专项任务）', 'secondary']);
  if (p.state === 'away') actions.push(['back', '回池', 'primary']);
  if (p.state === 'heading') actions.push(['fix_undo_depart', '更正：撤销出发', 'secondary']);
  if (p.state === 'onpost') actions.push(['fix_remove', '更正：移出岗位', 'secondary']);
  if (['walkback', 'pending', 'rest', 'meal', 'ready', 'away', 'onpost'].includes(p.state)) actions.push(['fix_place', '更正：放入指定岗位', 'secondary']);
  if (p.state !== 'done' && p.state !== 'excluded') actions.push(['lost', p.flags.includes('送失物') ? '取消送失物' : '标记送失物', 'secondary']);
  if (p.state !== 'done') actions.push(['leave', '离岗（病假、早退）', 'danger']);
  actions.push(['record', '查看轮岗记录', 'secondary']);
  openModal(`<h3>${esc(p.name)}</h3>${info}<div class="rt-actions">${actions.map(([k, t, c]) => `<button type="button" class="${c}" data-a="${k}">${t}</button>`).join('')}<button type="button" class="ghost" data-close>关闭</button></div>`, host => {
    host.querySelectorAll('[data-a]').forEach(btn => {
      btn.onclick = async () => {
        const k = btn.dataset.a;
        const run = body => act('/api/rotation/act', { pid: p.pid, ...body });
        if (['arrive', 'depart', 'undo', 'back'].includes(k)) { if (await run({ action: k })) closeModal(); }
        else if (k === 'lost') { if (await run({ action: 'flag', flag: '送失物', on: !p.flags.includes('送失物') })) closeModal(); }
        else if (k === 'record') { closeModal(); tab = 'records'; renderTabs(); view.recordNo = p.pid; await loadRecord(p.pid); openTab(); }
        else if (k === 'reassign') formBox(`改派 ${esc(p.name)}`, `<label>去哪条线<select name="line">${active.map(L => `<option ${p.assign && p.assign.line === L.id ? 'selected' : ''}>${esc(L.id)}</option>`).join('')}</select></label>`, f => run({ action: 'reassign', line: f.line }));
        else if (k === 'away') formBox(`${esc(p.name)} 暂离`, '<label>原因<input type="text" name="reason" value="专项任务" maxlength="50" required></label>', f => run({ action: 'away', reason: f.reason }));
        else if (k === 'leave') formBox(`${esc(p.name)} 离岗`, '<label>原因<input type="text" name="reason" value="病假" maxlength="50" required></label><p class="field-hint">离岗后今天不再分配；如在岗位上会留下空岗。</p>', f => run({ action: 'leave', reason: f.reason }), { warn: true });
        else if (k === 'fix_undo_depart' || k === 'fix_remove') formBox(btn.textContent, '<label>更正原因（必填，记入日志）<input type="text" name="reason" maxlength="100" required></label>', f => run({ action: k, reason: f.reason }));
        else if (k === 'fix_place') {
          const options = [];
          RT.data.day.lines.forEach(L => L.active && L.posts.forEach((x, i) => { if (x.open && !x.occ) options.push(`<option value="${esc(L.id)}#${i}">${esc(L.id)} ${esc(x.name)}</option>`); }));
          if (!options.length) { toast('现在没有空岗，请先移出岗位上的人或开岗', true); return; }
          formBox('放入岗位', `<label>空岗<select name="post">${options.join('')}</select></label><label>更正原因（必填）<input type="text" name="reason" maxlength="100" required></label>`, f => { const [line, i] = f.post.split('#'); return run({ action: 'fix_place', line, i, reason: f.reason }); });
        }
      };
    });
  });
}

function postMenu(key) {
  const [lid, idx] = key.split('#');
  const L = RT.data.day.lines.find(l => l.id === lid);
  const x = L.posts[Number(idx)];
  if (!L.active) { lineMenu(lid); return; }
  const occ = x.occ ? personOf(x.occ) : null;
  const actions = [];
  if (x.open) {
    if (occ) actions.push(['person', `查看 ${esc(occ.name)}`, 'secondary']);
    actions.push(['close', `撤岗${occ ? '（在岗的人下线）' : ''}`, 'danger']);
    if (!occ) actions.push(['place', '更正：放入一个人', 'secondary']);
  } else actions.push(['open', '开岗', 'primary']);
  openModal(`<h3>${esc(lid)} ${esc(x.name)}</h3><p class="modal-message">${x.open ? (occ ? `在岗：${esc(occ.name)}` : '空岗') : '未开放'}</p><div class="rt-actions">${actions.map(([k, t, c]) => `<button type="button" class="${c}" data-a="${k}">${t}</button>`).join('')}<button type="button" class="ghost" data-close>关闭</button></div>`, host => {
    host.querySelectorAll('[data-a]').forEach(btn => {
      btn.onclick = async () => {
        const k = btn.dataset.a;
        if (k === 'person') { personMenu(occ); return; }
        if (k === 'open' || k === 'close') { if (await act('/api/rotation/act', { action: 'post', line: lid, i: Number(idx), open: k === 'open' }, k === 'open' ? '已开岗' : '已撤岗')) closeModal(); return; }
        const cand = RT.data.day.persons.filter(p => ['walkback', 'pending', 'rest', 'ready', 'away', 'notyet'].includes(p.state) && p.breakKind !== 'meal_rest');
        if (!cand.length) { toast('池子里没有人可以放入', true); return; }
        formBox(`放入 ${esc(lid)} ${esc(x.name)}`, `<label>人员<select name="pid">${cand.map(p => `<option value="${esc(p.pid)}">${esc(p.name)}（${STATE_NAME[p.state]}）</option>`).join('')}</select></label><label>调整原因<input type="text" name="reason" maxlength="100" required></label>`, f => act('/api/rotation/act', { action: personOf(f.pid)?.state === 'notyet' ? 'plan_place' : 'fix_place', pid: f.pid, line: lid, i: Number(idx), reason: f.reason }));
      };
    });
  });
}

function lineMenu(lid) {
  const L = RT.data.day.lines.find(l => l.id === lid);
  confirmBox(`${L.active ? '停用' : '启用'} ${esc(lid)} 线`, L.active ? '停用后，这条线上的人全部下线，已分配到这条线的人会重新分配。' : '启用后按岗位配置开岗，系统开始往这条线派人。', L.active ? '停用' : '启用', () => act('/api/rotation/act', { action: 'line', line: lid, active: !L.active }), { warn: L.active });
}

$app.addEventListener('click', event => {
  if (!isManager() || tab !== 'board' || !RT.data.day || RT.data.day.status !== 'live') return;
  const person = event.target.closest('[data-pid]');
  if (person) { const p = personOf(person.dataset.pid); if (p) personMenu(p); return; }
  const post = event.target.closest('[data-post]');
  if (post) { postMenu(post.dataset.post); return; }
  const line = event.target.closest('[data-line]');
  if (line) lineMenu(line.dataset.line);
});
$app.addEventListener('keydown', event => {
  if (event.key === 'Enter' && event.target.matches('[data-post],[data-line],tr[data-pid]')) event.target.click();
});

// 测试预排拖拽：主管/经理可调整未到班次的人员，已开始计时的人员不参与。
$app.addEventListener('dragstart', event => {
  const source = event.target.closest('[data-drag-pid],tr[data-pid]');
  const pid = source?.dataset.dragPid || source?.dataset.pid;
  if (!isManager() || tab !== 'board' || personOf(pid)?.state !== 'notyet') {event.preventDefault(); return;}
  RT.pressing = true;
  RT.dragging = true;
  event.dataTransfer.setData('text/plain', pid);
});
$app.addEventListener('dragover', event => {
  if (isManager() && tab === 'board' && event.target.closest('[data-post]')) event.preventDefault();
});
$app.addEventListener('drop', async event => {
  const target = event.target.closest('[data-post]');
  if (!isManager() || tab !== 'board' || !target) return;
  event.preventDefault(); RT.pressing = false; RT.dragging = false;
  const [line, index] = target.dataset.post.split('#');
  await act('/api/rotation/act', {action:'plan_place', pid:event.dataTransfer.getData('text/plain'), line, i:Number(index)}, '预排已调整，班次开始后计时');
});
$app.addEventListener('dragend', () => {RT.pressing = false; RT.dragging = false;});

// ---------------------------------------------------------------- 初始化（草稿）

function draftView() {
  const clockDay = RT.data.clock.date;
  const live = RT.data.day;
  const draft = RT.data.draft;
  const head = `<section class="panel"><h2>生成 ${esc(clockDay)} 的初始轮岗</h2>
    <p class="field-hint">按名单生成当天的初始草稿：开园岗位、7 点岗位、推 7 点、送失物。检查调整后点「发布」，看板开始运转。测试时先用上方测试时钟切换日期。</p>
    ${RT.data.roster.available ? `<div class="rt-row">${live ? '<button type="button" class="danger" data-d="regenerate">清空这一天并重新生成（测试）</button>' : `<button type="button" class="primary" data-d="generate">${draft ? '重新生成草稿' : '生成草稿'}</button>`}</div>` : '<div class="rt-warn">这一天还没有名单，请先在「名单」上传。</div>'}</section>`;
  const liveBox = live && live.status === 'live' ? `<section class="panel"><h2>正在运行：${esc(live.date)}</h2>
    <div class="rt-row"><label>闭园时间<input type="time" id="liveClose" value="${esc(live.closeAt || '')}"></label><button type="button" class="secondary" data-d="setClose">保存</button><span class="field-hint">${live.closed ? '已闭园。' : ''}到点后所有岗位撤下，线上的人回休息室后转为闭园后工作。</span></div>
    ${live.draftAlerts.map(a => `<div class="rt-warn">${esc(a)}</div>`).join('')}</section>` : '';
  if (!draft) return head + liveBox;
  const plan = draft.plan;
  const persons = Object.values(draft.persons).sort((a, b) => a.start - b.start || a.name.localeCompare(b.name, 'zh'));
  const rot = persons.filter(p => p.role === 'rotation');
  const option = (selected, list) => '<option value="">（空）</option>' + list.map(p => `<option value="${esc(p.pid)}" ${p.pid === selected ? 'selected' : ''}>${esc(p.name)} ${hm(p.start)}-${hm(p.end)}</option>`).join('');
  const lines = draft.lines.map(L => `<div class="rt-line"><div class="rt-line-head g-${esc(L.group || '')}"><span class="rt-lid">${esc(L.id)}</span><span>${L.standby ? '待命线' : ''}</span></div>${L.posts.map((x, i) => {
    const key = `${L.id}#${i}`;
    let ctl;
    if (L.standby) ctl = '<span class="field-hint">待命</span>';
    else if (plan.seven[key] || x.seven) ctl = `<select data-draft="seven" data-key="${esc(key)}">${option(plan.seven[key], rot.filter(p => p.start <= 420 || p.pid === plan.seven[key]))}</select>`;
    else if (x.openAt) ctl = `<span class="field-hint">${esc(x.openAt)} 开岗，由池子补人</span>`;
    else ctl = `<select data-draft="crew" data-key="${esc(key)}">${option(plan.crew[key], rot)}</select>`;
    return `<div class="rt-dline"><span>${esc(x.name)}${x.seven ? ' <span class="rt-badge p7">7点</span>' : ''}</span>${ctl}</div>`;
  }).join('')}</div>`).join('');
  const early = rot.filter(p => p.start >= 420 && p.start <= 435);
  const lates = rot.filter(p => p.start >= 720);
  const sevenCount = Object.keys(plan.seven).length;
  const listSelect = (kind, n, pool) => Array.from({ length: n }, (_, i) => `<select data-list="${kind}">${option(plan[kind][i], pool)}</select>`).join(' ');
  const roleSelect = p => `<select data-role="${esc(p.pid)}">${[['rotation', '轮岗'], ['op', 'OP'], ['excluded', '不轮岗']].map(([v, t]) => `<option value="${v}" ${p.role === v ? 'selected' : ''}>${t}</option>`).join('')}</select>`;
  return `${head}${liveBox}
    <section class="panel"><h2>${esc(draft.date)} 初始轮岗草稿</h2>
      ${draft.draftAlerts.map(a => `<div class="rt-warn">${esc(a)}</div>`).join('')}
      <div class="rt-row"><label>闭园时间<input type="time" id="draftClose" value="${esc(draft.closeAt || '')}"></label><span class="field-hint">${draft.openTime ? `名单营业时间 ${esc(draft.openTime)}-${esc(draft.closeAt)}` : '名单里没有营业时间，使用默认值'}</span></div>
      <p class="field-hint">7 点岗位由 07:00 上班的人站岗；07:15 推 7 点的人到岗直接替换。开园岗位由 07:15 班次填满，其余人员到岗后进入池子由系统分配。</p>
      <div class="rt-lines">${lines}</div></section>
    <section class="panel"><h2>推 7 点和送失物</h2><p class="field-hint">系统已按本周做过的次数从少到多推荐，可以手动更换。</p>
      <div class="rt-row"><span class="rt-label">推 7 点</span>${sevenCount ? listSelect('push7', sevenCount, early) : '<span class="field-hint">今天没有 7 点岗位</span>'}</div>
      <div class="rt-row"><span class="rt-label">送失物</span>${listSelect('lost', Math.max(2, plan.lost.length), lates)}</div></section>
    <section class="panel"><h2>人员角色</h2><p class="field-hint">OP 开始不进入轮岗，07:15 后到大屏点「去休息」转入休息区。带训、长期专项等可设为不轮岗。</p>
      <div class="rt-scroll tall"><table class="rt-table"><thead><tr><th>姓名</th><th>工号</th><th>班次</th><th>备注</th><th>角色</th></tr></thead><tbody>${persons.map(p => `<tr><td>${esc(p.name)}</td><td>${esc(p.pid)}</td><td>${hm(p.start)}-${hm(p.end)}</td><td>${esc([p.note, p.label, p.mark].filter(Boolean).join('；'))}</td><td>${roleSelect(p)}</td></tr>`).join('')}</tbody></table></div></section>
    <section class="panel"><div class="rt-row"><button type="button" class="primary" data-d="publish">发布并开始轮岗</button><button type="button" class="ghost" data-d="discard">放弃草稿</button><span class="field-hint">发布后大屏开始显示，草稿不能再修改。</span></div></section>`;
}

function bindDraft() {
  const send = (body, okMessage) => act('/api/rotation/draft', { date: RT.data.clock.date, ...body }, okMessage);
  $app.querySelectorAll('[data-d]').forEach(button => {
    button.onclick = () => {
      const k = button.dataset.d;
      if (k === 'generate') send({ action: 'draft_generate' }, '草稿已生成');
      if (k === 'regenerate') confirmBox('清空并重新生成', '会清空这一天已有的轮岗、日志和待办，仅用于测试。', '清空并重新生成', () => act('/api/rotation/draft', { action: 'draft_generate', date: RT.data.clock.date, force: true }, '已重新生成草稿'), { warn: true });
      if (k === 'publish') confirmBox('发布轮岗', '发布后看板和大屏开始运转。', '发布', () => act('/api/rotation/draft', { action: 'publish', date: RT.data.clock.date }, '已发布'));
      if (k === 'discard') confirmBox('放弃草稿', '草稿会被删除，可以重新生成。', '放弃', () => act('/api/rotation/draft', { action: 'draft_discard', date: RT.data.clock.date }, '已放弃草稿'), { warn: true });
      if (k === 'setClose') act('/api/rotation/act', { action: 'set_close', close: document.getElementById('liveClose').value }, '闭园时间已更新');
    };
  });
  const close = document.getElementById('draftClose');
  if (close) close.onchange = () => send({ action: 'draft_close', close: close.value });
  $app.querySelectorAll('[data-draft]').forEach(select => { select.onchange = () => send({ action: 'draft_set', kind: select.dataset.draft, key: select.dataset.key, pid: select.value }); });
  $app.querySelectorAll('[data-list]').forEach(select => {
    select.onchange = () => {
      const kind = select.dataset.list;
      send({ action: 'draft_list', kind, pids: [...$app.querySelectorAll(`[data-list="${kind}"]`)].map(x => x.value) });
    };
  });
  $app.querySelectorAll('[data-role]').forEach(select => { select.onchange = () => send({ action: 'draft_role', pid: select.dataset.role, role: select.value }); });
}

// ---------------------------------------------------------------- 名单

async function loadRoster(day) {
  view.rosterDate = day;
  try { view.roster = await rtApi(`/api/rotation/roster?date=${encodeURIComponent(day)}`); } catch (error) { toast(error.message, true); }
  RT.render();
}

function rosterView() {
  const dates = RT.data.rosterDates || [];
  const r = view.roster;
  const kindName = { rotation: '轮岗', excluded: '不轮岗', off: '不在本区' };
  return `<section class="panel"><h2>上传名单</h2>
    <p class="field-hint">支持 ZTP 周班表，或标准模板（列：工号、姓名、日期、班次、备注；班次如 07:15-16:15，备注可写 0900-1200 SSEI）。按天上传的会覆盖按周上传的同一天。名单为模拟数据，只用于轮岗测试。</p>
    <form id="rosterForm" class="rt-row">
      <input type="file" name="file" accept=".xlsx" required>
      <label><input type="radio" name="scope" value="week" checked> 按周</label>
      <label><input type="radio" name="scope" value="day"> 按天</label>
      <label>日期（按天时）<input type="date" name="date"></label>
      <button type="submit" class="primary">上传</button>
    </form></section>
    <section class="panel"><h2>已有名单</h2>
      <div class="rt-row">${dates.length ? dates.map(d => `<button type="button" class="${r && r.date === d ? 'primary' : 'secondary'}" data-rdate="${esc(d)}">${esc(d)}</button>`).join('') : '<span class="field-hint">还没有上传名单</span>'}</div>
      ${r ? (r.available ? `<p class="field-hint">${esc(r.date)} 使用：${r.scope === 'day' ? '按天' : '按周'}名单「${esc(r.file_name)}」${r.hours ? `，营业时间 ${esc(r.hours.open)}-${esc(r.hours.close)}` : ''}，共 ${r.people.length} 人</p>
        <div class="rt-scroll tall"><table class="rt-table"><thead><tr><th>工号</th><th>姓名</th><th>班次原文</th><th>解析</th></tr></thead><tbody>${r.people.map(p => `<tr><td>${esc(p.employee_no)}</td><td>${esc(p.name)}</td><td>${esc(p.cell)}</td><td>${kindName[p.kind] || ''}${p.label ? `：${esc(p.label)}` : ''}</td></tr>`).join('')}</tbody></table></div>` : `<p class="field-hint">${esc(r.date)} 没有名单。</p>`) : ''}</section>`;
}

function bindRoster() {
  const form = document.getElementById('rosterForm');
  form.onsubmit = async event => {
    event.preventDefault();
    const data = new FormData(form);
    if (!data.get('date')) data.delete('date');
    try {
      const out = await rtApi('/api/rotation/roster/upload', { method: 'POST', body: data });
      toast(`已导入 ${out.start_date}${out.end_date !== out.start_date ? ` 至 ${out.end_date}` : ''}，共 ${out.entry_count} 条`);
      form.reset();
      await RT.load();
      await loadRoster(out.start_date);
    } catch (error) { toast(error.message, true); }
  };
  $app.querySelectorAll('[data-rdate]').forEach(button => { button.onclick = () => loadRoster(button.dataset.rdate); });
}

// ---------------------------------------------------------------- 记录

const EVENT_NAME = {
  publish: '发布', crew_start: '开园上岗', push: '推岗进线', push7: '推 7 点', chain_out: '推下班/推出圈', pushed_off: '下线', pushed_out: '被推出',
  arrive: '去休息', arrive_done: '去休息后下班', arrive_away: '去休息后转暂离', depart: '去轮岗', undo_arrive: '撤回去休息', away: '暂离/出圈', back: '回池',
  reassign: '改派', leave: '离岗', flag: '标记', post_open: '开岗', post_close: '撤岗', line_on: '启用线', line_off: '停用线', set_close: '改闭园时间',
  fix_undo_depart: '更正：撤销出发', fix_remove: '更正：移出岗位', fix_place: '更正：放入岗位', add_person: '临时加人', shift_end: '下班', shift_end_onpost: '在岗到点下班',
  shift_end_unconfirmed: '未到大屏确认即下班', park_close: '闭园', absence_missed: '错过出圈', assign_cleared: '去向作废', target_gone: '替换对象已离开',
  push7_substitute: '补选推 7 点', day_end: '当天收尾',
};

function detailText(item) {
  const d = item.detail || {};
  const parts = [];
  if (d.reason) parts.push(d.reason);
  if (d.kind) parts.push(d.kind === 'meal' ? '吃饭' : '休息');
  if (d.why) parts.push(d.why);
  if (d.line && item.type === 'depart') parts.push(`去 ${d.line}`);
  if (d.close) parts.push(`闭园 ${d.close}`);
  if (d.flag) parts.push(`${d.on ? '标记' : '取消'}${d.flag}`);
  return parts.join('，');
}

async function loadLog(day) {
  view.logDate = day;
  try { view.log = (await rtApi(`/api/rotation/log?date=${encodeURIComponent(day)}`)).items; } catch (error) { toast(error.message, true); }
  RT.render();
}

async function loadRecord(no) {
  view.recordNo = no;
  try { view.record = await rtApi(`/api/rotation/person?employee_no=${encodeURIComponent(no)}&date=${encodeURIComponent(view.logDate || RT.data.clock.date)}`); } catch (error) { toast(error.message, true); }
  RT.render();
}

function recordsView() {
  const rec = view.record;
  const recordBox = rec ? `<h3>${esc(rec.employee_no)} 本周（${esc(rec.week[0])} 至 ${esc(rec.week[1])}）</h3>
    <div class="rt-row">${Object.entries(rec.totals).sort().map(([line, m]) => `<span class="rt-pill">${esc(line)} ${Math.round(m)} 分钟</span>`).join('') || '<span class="field-hint">本周还没有在线记录</span>'}</div>
    ${rec.duties.length ? `<p class="field-hint">本周任务：${rec.duties.map(d => `${esc(d.date)} ${d.kind === 'push7' ? '推 7 点' : '送失物'}`).join('；')}</p>` : ''}
    <div class="rt-scroll"><table class="rt-table"><thead><tr><th>日期</th><th>线</th><th>开始</th><th>结束</th><th>分钟</th></tr></thead><tbody>${rec.segments.map(s => `<tr><td>${esc(s.date)}</td><td>${esc(s.line)}</td><td>${hm(s.start)}</td><td>${hm(s.end)}</td><td>${Math.round(s.minutes)}</td></tr>`).join('')}</tbody></table></div>` : '';
  return `<section class="panel"><h2>个人轮岗记录</h2><form id="recordForm" class="rt-row"><label>工号<input name="no" value="${esc(view.recordNo)}" maxlength="50" required></label><button type="submit" class="primary">查询</button></form>${recordBox}</section>
    <section class="panel"><h2>操作日志</h2><div class="rt-row"><label>日期<input type="date" id="logDate" value="${esc(view.logDate || RT.data.clock.date)}"></label><button type="button" class="secondary" id="logLoad">查看</button></div>
    <div class="rt-scroll tall"><table class="rt-table"><thead><tr><th>时间</th><th>操作</th><th>人员</th><th>线/岗位</th><th>说明</th><th>操作人</th></tr></thead><tbody>${(view.log || []).map(item => `<tr><td>${esc(item.time)}</td><td>${esc(EVENT_NAME[item.type] || item.type)}</td><td>${esc(item.name || '')}</td><td>${esc([item.line, item.post].filter(Boolean).join(' '))}</td><td>${esc(detailText(item))}</td><td>${esc(item.actor)}</td></tr>`).join('') || '<tr><td colspan="6">暂无记录</td></tr>'}</tbody></table></div></section>`;
}

function bindRecords() {
  document.getElementById('recordForm').onsubmit = event => { event.preventDefault(); const no = new FormData(event.target).get('no').trim(); if (no) loadRecord(no); };
  document.getElementById('logLoad').onclick = () => loadLog(document.getElementById('logDate').value);
}

// ---------------------------------------------------------------- 设置

const SETTING_LABELS = [
  ['walkMin', '默认路程（分钟）'], ['breakMin', '休息时长（分钟）'], ['mealMin', '吃饭时长（分钟）'], ['mealThreshold', '班次超过多少分钟才有吃饭'],
  ['mealEarliest', '最早安排吃饭时间'], ['mealAfterStart', '上班满多少分钟才开始安排吃饭'], ['mealForceAfterStart', '上班满多少分钟仍未吃饭强制安排'],
  ['mealReserveLeft', '吃饭剩余多少分钟转入饭后休息区（仍需等倒计时）'], ['mealWarnLeft', '离下班或闭园不到多少分钟未吃饭提醒'], ['offLead', '下班/出圈前多少分钟被推'],
  ['noBoardBefore', '到岗时距下班/出圈不超过多少分钟不再上岗'], ['endLead', '提前多少分钟开始安排推下班/出圈的人'], ['outWarnAfter', '过了出圈时间多少分钟没人可推提醒'],
  ['departEarly', '可提前几分钟点去轮岗'], ['futureWait', '等定时开岗最多等多少分钟'], ['readyNotify', '超过出发时间多少分钟提醒'],
  ['walkBackWarn', '下线多少分钟未点去休息提醒'], ['unassignedWarn', '待出发多少分钟没有去向提醒'], ['lineOpenAt', '开园岗位开始时间'],
  ['opReleaseAt', 'OP 转待定时间'], ['parkClose', '默认闭园时间'], ['closeStopPush', '闭园前多少分钟起不再派人'], ['lostCount', '每天送失物人数'],
];

async function loadSettings() {
  try {
    view.config = await rtApi('/api/rotation/config');
    view.accounts = (await rtApi('/api/rotation/accounts')).items;
  } catch (error) { toast(error.message, true); }
  RT.render();
}

function settingsView() {
  const cfg = view.config;
  if (!cfg) return '<section class="empty">正在加载…</section>';
  const field = ([key, label]) => {
    const value = cfg.settings[key];
    const isTime = typeof cfg.defaults[key] === 'string';
    return `<label>${label}<input name="${key}" ${isTime ? 'type="time"' : 'type="number" step="1" min="0"'} value="${esc(value)}" required></label>`;
  };
  return `<section class="panel"><h2>轮岗参数</h2><form id="settingsForm" class="rt-form-grid">${SETTING_LABELS.map(field).join('')}<div class="rt-row"><button type="submit" class="primary">保存参数</button></div></form></section>
    <section class="panel"><h2>线和岗位</h2><p class="field-hint">每条线按顺序列出岗位，第一个是入口岗，最后一个是出口岗。可选：openAt 定时开岗、closeAt 定时撤岗、seven 7 点岗位顺序、standby 待命线、walk 路程分钟。</p>
      <form id="linesForm"><textarea name="lines" rows="16" class="rt-json" spellcheck="false">${esc(JSON.stringify(cfg.lines, null, 2))}</textarea><div class="rt-row"><button type="submit" class="primary">保存线和岗位</button></div></form></section>
    <section class="panel"><h2>轮岗专用账号</h2><p class="field-hint">在登录页直接登录时使用的密码；测试入口里切换模拟账号不需要密码。</p>
      <table class="rt-table"><thead><tr><th>账号</th><th>类型</th><th>最近登录</th><th></th></tr></thead><tbody>${(view.accounts || []).map(a => `<tr><td>${esc(a.login_account)}</td><td>${esc(a.kind_label)}${a.locked ? '（已锁定）' : ''}</td><td>${esc(a.last_login_at || '从未')}</td><td><button type="button" class="secondary" data-reset="${esc(a.login_account)}">重置密码</button></td></tr>`).join('')}</tbody></table></section>`;
}

function bindSettings() {
  const sform = document.getElementById('settingsForm');
  if (!sform) return;
  sform.onsubmit = async event => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(sform).entries());
    try { await rtApi('/api/rotation/config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ settings: values }) }); toast('参数已保存'); view.config = null; await loadSettings(); } catch (error) { toast(error.message, true); }
  };
  document.getElementById('linesForm').onsubmit = async event => {
    event.preventDefault();
    let lines;
    try { lines = JSON.parse(new FormData(event.target).get('lines')); } catch (_) { toast('线和岗位不是有效的 JSON', true); return; }
    try { await rtApi('/api/rotation/config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ lines }) }); toast('已保存，下次生成草稿时生效'); view.config = null; await loadSettings(); } catch (error) { toast(error.message, true); }
  };
  $app.querySelectorAll('[data-reset]').forEach(button => {
    button.onclick = () => formBox(`重置 ${esc(button.dataset.reset)} 的密码`, '<label>新密码<input name="password" type="text" minlength="4" maxlength="64" autocomplete="off" required></label><p class="field-hint">重置后该账号原有的登录全部失效。</p>', f => act(`/api/rotation/accounts/${encodeURIComponent(button.dataset.reset)}/password`, { new_password: f.password }, '密码已重置'));
  });
}

// ---------------------------------------------------------------- 模拟 CM/TR 个人页

function memberView() {
  const d = RT.data;
  const p = d.person;
  const n = nowMin();
  const open = d.notices.filter(x => x.status === 'open');
  const done = d.notices.filter(x => x.status !== 'open');
  const todo = `<section class="panel rt-todo"><div class="rt-todo-head"><h2>待办</h2><span class="rotation-test-badge">测试</span></div>
    ${open.length ? open.map(x => `<article class="rt-notice ${esc(x.kind)}"><strong>${esc(x.title)}</strong>${x.body ? `<p>${esc(x.body)}</p>` : ''}</article>`).join('') : '<p class="field-hint">当前没有轮岗待办。</p>'}
    ${done.length ? `<details><summary>已完成 ${done.length} 项</summary>${done.map(x => `<p class="rt-notice-done">${esc(x.updated_at || '')} ${esc(x.title)}</p>`).join('')}</details>` : ''}</section>`;
  if (!p) return `${todo}<section class="panel rt-empty"><h2>今天没有你的轮岗</h2><p>名单发布后这里会显示你的状态和所在线。</p></section>`;
  let status = STATE_NAME[p.state] || '';
  if (p.state === 'onpost') status = `在 ${esc(p.line)} 线 ${esc(p.post || '')}，已站 ${Math.max(0, Math.floor(n - (p.lineStart || n)))} 分钟`;
  if (p.state === 'rest' || p.state === 'meal') status = `${p.state === 'meal' ? '吃饭' : '休息'}至 ${hm(p.readyAt)}，还剩 ${Math.max(0, Math.ceil(p.readyAt - n))} 分钟`;
  if (p.state === 'heading') status = `前往 ${esc(p.assign ? p.assign.line : '')} 线，约 ${hm(p.arriveAt)} 到岗`;
  if (p.state === 'away' && p.away) status = `暂离：${esc(p.away.reason)}${p.away.until !== null && p.away.until !== undefined ? `，${hm(p.away.until)} 回` : ''}`;
  const next = p.assign ? `<p>下一步：${goText(p)}，${hm(p.assign.departAt)} 出发</p>` : '';
  const line = d.line ? `<section class="panel"><h2>${esc(d.line.id)} 线</h2><div class="rt-line">${d.line.posts.map(x => `<div class="rt-post${x.occ === p.pid ? ' me' : ''}${x.open ? '' : ' closed'}"><span class="rt-pn">${esc(x.name)}</span><span class="rt-who-name">${x.open ? esc(x.occName || '空岗') : '未开'}</span></div>`).join('')}</div></section>` : '';
  const today = (d.today || []).map(s => `<span class="rt-pill">${esc(s.line)} ${hm(s.start)}-${hm(s.end)}</span>`).join('');
  const week = Object.entries(d.week_minutes || {}).sort().map(([lineId, m]) => `<span class="rt-pill">${esc(lineId)} ${Math.round(m)} 分钟</span>`).join('');
  return `${todo}<section class="panel"><h2>${esc(p.name)}</h2><p class="rt-status">${status}</p>${next}<p class="field-hint">班次 ${hm(p.start)}-${hm(p.end)}${p.mealEligible ? (p.ate ? '，已吃饭' : '，有吃饭') : ''}${badges(p)}</p></section>${line}
    <section class="panel"><h2>今天去过的线</h2><div class="rt-row">${today || '<span class="field-hint">暂无</span>'}</div><h2>本周各线累计</h2><div class="rt-row">${week || '<span class="field-hint">暂无</span>'}</div></section>`;
}

// ---------------------------------------------------------------- 渲染与加载

RT.render = fromTicker => {
  if (!RT.data || !me || RT.dragging) return;
  renderClock();
  if (me.kind === 'member') {
    if (fromTicker && $app.querySelector('details[open]')) return;
    $app.innerHTML = memberView();
    return;
  }
  // 每秒刷新只更新看板；表单页在正在输入或由计时器触发时不重绘
  if (fromTicker && tab !== 'board') return;
  if (editing()) return;
  if (!fromTicker) renderClockBar();
  const html = tab === 'board' ? boardView() : tab === 'draft' ? draftView() : tab === 'roster' ? rosterView() : tab === 'records' ? recordsView() : settingsView();
  const scroll = [...$app.querySelectorAll('.rt-chips, .rt-scroll')].map(el => el.scrollTop);
  $app.innerHTML = html;
  if (tab === 'board') $app.querySelectorAll('tr[data-pid]').forEach(row => {
    row.draggable = personOf(row.dataset.pid)?.state === 'notyet';
  });
  [...$app.querySelectorAll('.rt-chips, .rt-scroll')].forEach((el, i) => { el.scrollTop = scroll[i] || 0; });
  if (tab === 'draft') bindDraft();
  if (tab === 'roster') bindRoster();
  if (tab === 'records') bindRecords();
  if (tab === 'settings') bindSettings();
};

RT.load = async () => {
  const data = await rtApi(me.kind === 'member' ? '/api/rotation/me' : '/api/rotation/board');
  RT.data = data;
  RT.fetchedAt = Date.now();
  if (!RT.modalOpen) RT.render();
};

document.getElementById('rtSwitch').onclick = async () => {
  try { await rtPost('/api/rotation/logout'); } catch (_) { /* 退出失败也回到入口 */ }
  location.href = portalPath('/');
};

(async () => {
  try {
    me = await rtApi('/api/rotation/whoami');
    if (me.kind === 'screen') { location.href = portalPath('/rotation/screen'); return; }
    renderWho();
    renderTabs();
    await RT.load();
    if (isManager()) openTab();
    connectLive();
    startLocalTicker();
  } catch (error) {
    $app.innerHTML = `<section class="panel rt-empty"><h2>无法进入轮岗</h2><p>${esc(error.message)}</p></section>`;
  }
})();
