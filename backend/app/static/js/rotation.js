'use strict';
// 轮岗（测试）：轮岗主管/经理的看板、初始化、名单、记录、设置；模拟 CM/TR 时的个人页和待办。

const $app = document.getElementById('rtApp');
const $tabs = document.getElementById('rtTabs');
const $bar = document.getElementById('rtClockBar');
const TABS = [['board', '看板'], ['draft', '每日班表'], ['roster', '名单'], ['records', '记录'], ['settings', '设置']];
let me = null;
let tab = 'board';
try { tab = localStorage.getItem('rotation.tab') || 'board'; } catch (_) { /* 无痕模式不保存 */ }
if (!TABS.some(([id]) => id === tab)) tab = 'board';
const view = { logDate: null, log: null, record: null, recordNo: '', rosterDate: null, roster: null, config: null, accounts: null };

const isManager = () => formalRotation?!!me?.capabilities?.can_operate&&!personalRotation:me && (me.kind === 'supervisor' || me.kind === 'manager') && (!me.adjustment || typeof adjustmentState!=='undefined' && !!adjustmentState && !adjustExitPending);

// 正在输入时不重绘，避免吞掉输入内容。
function editing() {
  const el = document.activeElement;
  return el && $app.contains(el) && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName);
}

// ---------------------------------------------------------------- 顶栏与测试时钟

function renderWho() {
  const who = document.getElementById('rtWho');
  if(formalRotation){who.textContent=`${me.name}（${me.employee_no}）· ${me.kind_label} · ${me.attraction_name}`;return;}
  const label = me.kind === 'member' ? `${me.name}（${me.employee_no}）· 模拟 CM/TR` : `${me.name} · ${me.kind_label}`;
  who.textContent = label;
}

function renderClockBar() {
  if (formalRotation || !isManager() || me.adjustment) { $bar.hidden = true; return; }
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
    if (action === 'next') toast(out.waiting && out.waiting.length ? `等待个人或大屏确认：${out.waiting.join('；')}` : '已推进到当天结束');
    await RT.load();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderTabs() {
  if (!isManager()) { $tabs.hidden = true; return; }
  $tabs.hidden = false;
  $tabs.innerHTML = TABS.filter(([id])=>formalRotation?(me.capabilities.can_manage||id==='board'):!me.adjustment||['board','records'].includes(id)).map(([id, label]) => `<button type="button" class="${id === tab ? 'active' : ''}" data-tab="${id}" ${id === tab ? 'aria-current="page"' : ''}>${label}</button>`).join('');
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
  if(!RT.data)return;
  if(tab==='draft')void loadDaily(daily.date||RT.data.clock.date,daily.time);
  if (tab === 'records' && !view.log) loadLog(view.logDate || RT.data.clock.date);
  if (tab === 'roster' && !view.roster) loadRoster(view.rosterDate || RT.data.clock.date);
  if (tab === 'settings' && !view.config) loadSettings();
  RT.render();
}

// ---------------------------------------------------------------- 看板

function boardView() {
  const d = RT.data.day;
  if (!d) {
    if(formalRotation)return `<section class="panel rt-empty"><h2>${esc(RT.data.clock.date)} 尚未启用轮岗</h2><p>${me.capabilities.can_manage?'请在「名单」上传正式班表，并在「每日班表」核对预排。':'请等待主管发布当日班表。'}</p></section>`;
    const draft = RT.data.draft;
    return `<section class="panel rt-empty"><h2>${esc(RT.data.clock.date)} 还没有发布轮岗</h2><p>${draft ? '草稿已生成，到「每日班表」检查并发布后看板开始运转。' : '先在「名单」上传名单，再到「每日班表」生成当天的初始轮岗。测试时先用上方测试时钟设定日期和时间。'}</p></section>`;
  }
  if (d.status === 'ended') return `<section class="panel rt-empty"><h2>${esc(d.date)} 的轮岗已结束</h2><p>${!formalRotation||me.capabilities.can_records?'可在「记录」查看当天的操作日志和个人轮岗记录。':'当天轮岗安排已结束。'}</p></section>`;
  const n = nowMin();
  const alerts = d.alerts.map(a => `<div class="rt-alert ${esc(a.level)}">${esc(a.msg)}</div>`).join('') + d.notices.slice(-3).map(x => `<div class="rt-alert">${hm(x.t)} ${esc(x.msg)}</div>`).join('');
  return `${isManager()&&!me.adjustment&&(!formalRotation||me.capabilities.can_manage)?`<section class="panel"><div class="rt-row"><strong>当天运行 · ${esc(d.date)}</strong><span>闭园 ${esc(d.closeAt||'未设置')}</span><button type="button" class="secondary" data-live-close>调整闭园时间</button></div></section>`:''}${alerts ? `<div class="rt-alerts">${alerts}</div>` : ''}<div class="rt-board"><div class="rt-lines">${d.lines.map(L => lineCard(L, n, isManager())).join('')}</div><div class="rt-pool">${poolView(n)}</div></div>`;
}

function poolView(n) {
  const posted=new Set(RT.data.day.lines.flatMap(line=>line.posts.map(post=>post.occ).filter(Boolean)));
  const ps = RT.data.day.persons.filter(p=>!posted.has(p.pid));
  const displayed=new Set();
  const by = states => ps.filter(p => states.includes(poolState(p, n)));
  const section = (title, list, chip,grouped=false,extra='') => {list.forEach(p=>displayed.add(p.pid));return `<section class="rt-sect"><h2>${title}<span class="rt-n">${list.length}</span></h2><div class="rt-chips${list.length ? '' : ' empty'}">${list.length ? grouped?groupedChips(list,chip,n,isManager()&&title==='待出发'):list.map(chip).join('') : '暂无'}</div>${extra}</section>`;};
  const button = (p, sub, extra = '', cls = '') => `<button type="button" class="rt-chip ${cls}" data-pid="${esc(p.pid)}"${isManager()&&['notyet','onpost','rest','meal','ready'].includes(p.state)?` draggable="true" data-drag-pid="${esc(p.pid)}"`:''}><span class="rt-nm">${esc(p.name)}${extra}</span><span class="rt-go">${goText(p)}</span><span class="rt-sub">${sub}</span>${p.assign?`<span class="rt-sub">${assignmentPlanText(p,n)}</span>`:''}${badges(p)}</button>`;
  let html = '';
  const upcoming=ps.filter(p=>p.upcomingShift&&!waitingInitialRest(p)&&!opWaiting(p));
  if(upcoming.length)html += section('即将上班 · 预计人数',upcoming,p=>button(p,`${hm(p.start)}–${hm(p.end)} · 班次未开始${p.previewAssign?` · 预计进入 ${esc(p.previewAssign.line)}线 · ${esc(p.previewAssign.entryPost||'入口岗')}`:''}`,'','dim'));
  html += section('待出发', by(['ready']).filter(p=>!p.entryHold).sort((a, b) => (a.assign ? a.assign.departAt : 9e9) - (b.assign ? b.assign.departAt : 9e9)), p => {
    if (!p.assign) return button(p, '等待分配', '', 'dim');
    const over = n - p.assign.departAt;
    return button(p, over >= 1 ? `已超时 ${Math.floor(over)} 分钟` : '现在出发', '', over >= 2 ? 'late' : 'due');
  },true,departureForecast(ps,n));
  html += section('待到大屏点去休息', ps.filter(p=>['walkback','pending'].includes(p.state)||opWaiting(p)).sort((a, b) => (a.walkbackSince || 0) - (b.walkbackSince || 0)), p => button(p, opWaiting(p)?'OP · 可提前确认去休息':p.state === 'pending' ? 'OP 结束待定' : `下线 ${Math.max(0, Math.floor(n - (p.walkbackSince || n)))} 分钟（${esc(p.downReason || '推岗')}）`));
  html += section('吃饭区', by(['meal']).sort((a,b)=>a.readyAt-b.readyAt), p=>button(p, `吃饭至 ${hm(p.readyAt)}，剩余 ${Math.max(0,Math.ceil(p.readyAt-n))} 分钟`, '', 'meal'));
  const resting=by(['rest']).filter(p=>!p.entryHold).sort((a,b)=>restUntil(a)-restUntil(b));
  html += section(`休息区 <span class="rt-hint">${restSummary(resting)}</span>`, resting, p=>button(p, waitingInitialRest(p)?`班次未开始 · ${hm(p.scheduledRestStart)}开始休息`:`${p.breakKind === 'meal_rest' ? '饭后剩余休息时间：' : ''}至 ${hm(restUntil(p))}，剩余 ${Math.max(0,Math.ceil(restUntil(p)-n))} 分钟`, '', 'rest'),true);
  const outside=ps.filter(outsideKind);outside.forEach(p=>displayed.add(p.pid));
  html+=`<section class="rt-sect rt-outside"><h2>圈外人员<span class="rt-n">${outside.length}</span></h2>${['病假','出圈','OJT'].map(kind=>`<div data-outside-kind="${kind}"><h3>${kind==='出圈'?'出圈（含暂离）':kind}</h3><div class="rt-chips">${outside.filter(p=>outsideKind(p)===kind).map(p=>button(p,esc(p.leaveReason||p.away?.reason||p.label||kind)+(p.away?.until!=null?` · ${hm(p.away.until)} 回`:''),'','dim')).join('')||'暂无'}</div></div>`).join('')}</section>`;
  if(ps.some(p=>p.entryHold))html += section('待本人到场重新领取安排',ps.filter(p=>p.entryHold),p=>button(p,'同行任务已调整 · 不记为缺勤','','dim'));
  const others = by(['op', 'notyet', 'excluded', 'done']).filter(p=>!displayed.has(p.pid)).sort((a, b) => a.start - b.start);
  html += `<section class="rt-sect"><h2>其他人员<span class="rt-n">${others.length}</span><span class="rt-hint">OP、未到岗、不轮岗、已下班</span></h2><div class="rt-scroll"><table class="rt-table">${others.map(p => `<tr data-pid="${esc(p.pid)}" tabindex="0"><td>${esc(p.name)}</td><td>${hm(p.start)}-${hm(p.end)}</td><td>${STATE_NAME[p.state] || ''}${p.label ? `：${esc(p.label)}` : ''}</td></tr>`).join('')}</table></div></section>`;
  return html;
}

function personMenu(p) {
  const active = RT.data.day.lines.filter(L => L.active);
  const state = poolState(p, nowMin());
  const info = `<dl class="rt-kv">
    <dt>工号</dt><dd>${esc(p.pid)}</dd>
    <dt>班次</dt><dd>${hm(p.start)}-${hm(p.end)}${p.mealEligible ? '（有吃饭）' : '（只休息）'}${p.ate ? '，已吃饭' : ''}</dd>
    <dt>状态</dt><dd>${STATE_NAME[state] || ''}${state === 'rest' ? `至 ${hm(restUntil(p))}` : ''}${p.line ? `：${esc(p.line)} ${esc(p.post || '')}` : ''}${p.away ? `：${esc(p.away.reason)}` : ''}${p.preparing ? `（${preparingText(p.preparing)}）` : ''}</dd>
    ${p.postHold?`<dt>持续定岗</dt><dd>${hm(Math.floor(p.postHold.since))}起 · ${esc(p.postHold.reason)}；手动取消或零点结束。</dd>`:''}
    <dt>去向</dt><dd>${p.assign ? `${goText(p)}，${hm(p.assign.departAt)} 出发` : '无'}</dd>
    <dt>今天去过</dt><dd>${esc(p.visited.join('、') || '无')}</dd>
    ${p.absences.length ? `<dt>固定暂离</dt><dd>${p.absences.map(a => `${hm(a.start)}-${hm(a.end)} ${esc(a.label)}${a.missed ? '（错过）' : ''}`).join('；')}</dd>` : ''}
    ${p.note ? `<dt>备注</dt><dd>${esc(p.note)}</dd>` : ''}</dl>`;
  const actions = [];
  if (p.state === 'walkback' || p.state === 'pending'||opWaiting(p)) actions.push(['arrive', '代点去休息', 'primary']);
  if (canDepartNow(p, nowMin())&&(!p.entryGroup||me.employee_no===p.pid)) actions.push(['depart', p.entryGroup?'确认上岗安排':'代点去轮岗', 'primary']);
  if (['rest', 'meal', 'ready'].includes(p.state) && p.state !== 'ready') actions.push(['undo', '撤回去休息', 'secondary']);
  if (['rest', 'meal', 'ready'].includes(p.state)) actions.push(['reassign', '改派去向', 'secondary']);
  if (['walkback', 'pending', 'rest', 'meal', 'ready', 'heading', 'onpost'].includes(p.state)) actions.push(['away', '标记暂离（专项任务）', 'secondary']);
  if (p.state === 'away') actions.push(['back', '回池', 'primary']);
  if (p.state === 'heading') actions.push(['fix_undo_depart', '更正：撤销出发', 'secondary']);
  if (p.state === 'onpost') actions.push(['fix_remove', '更正：移出岗位', 'secondary']);
  if (['walkback', 'pending', 'rest', 'meal', 'ready', 'away', 'onpost'].includes(p.state)) actions.push(['fix_place', '更正：放入指定岗位', 'secondary']);
  if (p.state !== 'done' && p.state !== 'excluded') actions.push(['lost', p.flags.includes('送失物') ? '取消送失物' : '标记送失物', 'secondary']);
  if (p.state !== 'done') actions.push(['leave', '离岗（病假、早退）', 'danger']);
  if(!formalRotation||me.capabilities.can_records)actions.push(['record', '查看轮岗记录', 'secondary']);
  openModal(`<h3>${esc(p.name)}</h3>${info}<div class="rt-actions">${actions.map(([k, t, c]) => `<button type="button" class="${c}" data-a="${k}">${t}</button>`).join('')}<button type="button" class="ghost" data-close>关闭</button></div>`, host => {
    host.querySelectorAll('[data-a]').forEach(btn => {
      btn.onclick = async () => {
        const k = btn.dataset.a;
        if(formalRotation&&['arrive','depart'].includes(k)){confirmRotation(p,k,'/api/rotation/act');return;}
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
  if(typeof adjustPostMenu==='function'){const [line,i]=key.split('#');adjustPostMenu(line,Number(i));return;}
  const [lid, idx] = key.split('#');
  const L = RT.data.day.lines.find(l => l.id === lid);
  const x = L.posts[Number(idx)];
  if (!L.active) { lineMenu(lid); return; }
  const occ = x.occ ? personOf(x.occ) : null;
  const actions = [];
  if (x.open) {
    if (occ) actions.push(['person', `查看 ${esc(occ.name)}`, 'secondary']);
    actions.push(['close', `撤岗${occ ? '（在岗的人下线）' : ''}`, 'danger']);
    if (!occ) {
      actions.unshift(['open', '安排补岗', 'primary']);
      actions.push(['place', '更正：放入一个人', 'secondary']);
    }
  } else {
    actions.push(['open', '加岗（默认3分钟后）', 'primary']);
    if (x.openAt && !x.opened) actions.push(['time', '修改加岗时间', 'secondary']);
  }
  openModal(`<h3>${esc(lid)} ${esc(x.name)}</h3><p class="modal-message">${x.open ? (occ ? `在岗：${esc(occ.name)}` : '空岗') : '未开放'}</p><div class="rt-actions">${actions.map(([k, t, c]) => `<button type="button" class="${c}" data-a="${k}">${t}</button>`).join('')}<button type="button" class="ghost" data-close>关闭</button></div>`, host => {
    host.querySelectorAll('[data-a]').forEach(btn => {
      btn.onclick = async () => {
        const k = btn.dataset.a;
        if (k === 'person') { personMenu(occ); return; }
        if (k === 'open' || k === 'time') { postScheduleForm(lid, Number(idx), x, k === 'time'); return; }
        if (k === 'close') { if (await act('/api/rotation/act', { action: 'post', line: lid, i: Number(idx), open: false }, '已撤岗')) closeModal(); return; }
        const cand = RT.data.day.persons.filter(p => ['walkback', 'pending', 'rest', 'ready', 'away', 'notyet'].includes(p.state) && p.breakKind !== 'meal_rest');
        if (!cand.length) { toast('池子里没有人可以放入', true); return; }
        formBox(`放入 ${esc(lid)} ${esc(x.name)}`, `<label>人员<select name="pid">${cand.map(p => `<option value="${esc(p.pid)}">${esc(p.name)}（${STATE_NAME[p.state]}）</option>`).join('')}</select></label><label>调整原因<input type="text" name="reason" maxlength="100" required></label>`, f => act('/api/rotation/act', { action: personOf(f.pid)?.state === 'notyet' ? 'plan_place' : 'fix_place', pid: f.pid, line: lid, i: Number(idx), reason: f.reason }));
      };
    });
  });
}

function postScheduleForm(lid, index, post, editingTime = false) {
  const time = editingTime ? post.openAt : hm(Math.ceil(nowMin() + 3));
  formBox(`${editingTime ? '修改加岗时间' : '安排加岗'} · ${esc(lid)} ${esc(post.name)}`,
    `<label>加岗时间<input type="time" name="openAt" value="${esc(time)}" required></label><p class="field-hint">默认3分钟后；安排下一位可上岗人员，从入口依次推进补岗。</p>`,
    f => act('/api/rotation/act', { action: 'post', line: lid, i: index, open: true, openAt: f.openAt }, '加岗时间已保存，系统自动安排人员'));
}

function lineMenu(lid) {
  const L = RT.data.day.lines.find(l => l.id === lid);
  confirmBox(`${L.active ? '停用' : '启用'} ${esc(lid)} 线`, L.active ? '停用后，这条线上的人全部下线，已分配到这条线的人会重新分配。' : '启用后按岗位配置开岗，系统开始往这条线派人。', L.active ? '停用' : '启用', () => act('/api/rotation/act', { action: 'line', line: lid, active: !L.active }), { warn: L.active });
}

$app.addEventListener('click', event => {
  if (!isManager() || tab !== 'board' || !RT.data.day || RT.data.day.status !== 'live') return;
  const groupButton=event.target.closest('[data-group-depart]');
  if(groupButton){const p=personOf(groupButton.dataset.groupDepart);if(p)confirmRotation(p,'depart','/api/rotation/act',true);return;}
  if(event.target.closest('[data-live-close]')&&!me.adjustment){formBox('调整当天闭园时间','<label>闭园时间<input type="time" name="close" value="'+esc(RT.data.day.closeAt||'')+'" required></label>',f=>act('/api/rotation/act',{action:'set_close',close:f.close},'闭园时间已更新'));return;}
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
  if(tab==='draft')return;
  const source = event.target.closest('[data-drag-pid],tr[data-pid]');
  const pid = source?.dataset.dragPid || source?.dataset.pid;
  if (!isManager() || tab !== 'board' || (!event.target.closest('[data-drag-post]')&&!['notyet','onpost','rest','meal','ready'].includes(personOf(pid)?.state))) {event.preventDefault(); return;}
  RT.pressing = true;
  RT.dragging = true;
  event.dataTransfer.setData('text/plain',event.target.closest('[data-drag-post]')?'post:'+event.target.closest('[data-drag-post]').dataset.dragPost:pid);
});
$app.addEventListener('dragover', event => {
  if (isManager() && tab === 'board' && event.target.closest('[data-post]')) event.preventDefault();
});
$app.addEventListener('drop', async event => {
  const target = event.target.closest('[data-post]');
  if (!isManager() || tab !== 'board' || !target) return;
  event.preventDefault(); RT.pressing = false; RT.dragging = false;
  const [line, index] = target.dataset.post.split('#');
  const raw=event.dataTransfer.getData('text/plain');if(raw.startsWith('post:')){const [source,from]=raw.slice(5).split('#');postSwapForm(source,Number(from),Number(index),line);}else personDrop(raw,line,Number(index));
});
$app.addEventListener('dragend', () => {RT.pressing = false; RT.dragging = false;});

// ---------------------------------------------------------------- 初始化（草稿）

function initialDraftView(data) {
  const draft=data.draft;
  if(!draft)return `<section class="panel"><h2>${esc(data.date)} · 生成预排</h2><button type="button" class="primary" data-d="generate">生成草稿</button></section>`;
  const plan=draft.plan,persons=Object.values(draft.persons).sort((a,b)=>a.start-b.start||a.name.localeCompare(b.name,'zh')),rot=persons.filter(p=>p.role==='rotation');
  const option=(selected,list)=>'<option value="">（空）</option>'+list.map(p=>`<option value="${esc(p.pid)}" ${p.pid===selected?'selected':''}>${esc(p.name)} ${hm(p.start)}-${hm(p.end)}</option>`).join('');
  const placements=draft.lines.filter(l=>!l.standby).map(l=>`<details><summary>${esc(l.id)}线 · 选择预排人员</summary>${l.posts.map((x,i)=>{const key=`${l.id}#${i}`,kind=x.seven||plan.seven[key]?'seven':'crew';return `<label>${esc(x.name)}<select data-draft="${kind}" data-key="${esc(key)}">${option(plan[kind][key],kind==='seven'?rot.filter(p=>p.start===420):rot)}</select></label>`;}).join('')}</details>`).join('');
  const early=rot.filter(p=>p.start>=420&&p.start<=435),lates=rot.filter(p=>p.start>=720),sevenCount=Object.keys(plan.seven).length;
  const listSelect=(kind,n,pool)=>Array.from({length:n},(_,i)=>`<select data-list="${kind}">${option(plan[kind][i],pool)}</select>`).join(' ');
  const roleSelect=p=>`<select data-role="${esc(p.pid)}">${[['rotation','轮岗'],['op','OP'],['excluded','不轮岗']].map(([v,t])=>`<option value="${v}" ${p.role===v?'selected':''}>${t}</option>`).join('')}</select>`;
  return `<section class="panel"><h2>${esc(draft.date)} · ${data.prestart_live?'开班前看板':'预排看板'}</h2>
    <p class="field-hint">${data.prestart_live?'今天首班开始前与看板同步；首班开始后固定整份班表，之后在看板调整。':'未来日期只保存预排，不提前开始计时。岗位和人员可拖拽，也可依次点击交换。'}</p>
    ${dailyBoard(data,true)}
    <details class="rt-daily-settings"><summary>预排设置 · 岗位人员、闭园时间、任务与角色</summary>
      ${draft.draftAlerts.map(a=>`<div class="rt-warn">${esc(a)}</div>`).join('')}
      <div class="rt-row"><label class="rt-daily-field">闭园时间<input type="time" id="draftClose" value="${esc(draft.closeAt||'')}"></label></div>
      ${placements}
      <h3>推7点和送失物</h3><p class="field-hint">保留本周次数推荐；7点岗位仅安排07:00班次，07:15人员负责接替。</p>
      <div class="rt-row"><span>推7点</span>${sevenCount?listSelect('push7',sevenCount,early):'今天没有7点岗位'}</div>
      <div class="rt-row"><span>送失物</span>${listSelect('lost',Math.max(2,plan.lost.length),lates)}</div>
      <h3>人员角色</h3><p class="field-hint">OP及不轮岗设置仅影响轮岗安排。</p>
      <div class="rt-scroll tall"><table class="rt-table"><thead><tr><th>姓名</th><th>工号</th><th>班次</th><th>备注</th><th>角色</th></tr></thead><tbody>${persons.map(p=>`<tr><td>${esc(p.name)}</td><td>${esc(p.pid)}</td><td>${hm(p.start)}–${hm(p.end)}</td><td>${esc([p.note,p.label,p.mark].filter(Boolean).join('；'))}</td><td>${roleSelect(p)}</td></tr>`).join('')}</tbody></table></div>
      ${!data.prestart_live?'<div class="rt-row"><button type="button" class="secondary" data-d="generate">重新生成草稿</button><button type="button" class="ghost" data-d="discard">放弃草稿</button></div>':''}
    </details>
    ${data.date===data.today&&!data.prestart_live?'<button type="button" class="primary" data-d="publish">发布并开始轮岗</button>':''}
  </section>`;
}

const daily={month:'',date:'',calendar:null,data:null,time:'',loading:false,error:'',request:0,busy:false,source:null,mode:'people'};
async function loadDaily(date=daily.date||RT.data.clock.date,time=daily.time,prepare=true){
  const liveVersion=RT.data.version;const generation=++daily.request;daily.date=date;daily.month=date.slice(0,7);daily.time=time;daily.source=null;daily.loading=true;daily.error='';RT.render(false,true);
  try{
    const [month,loaded]=await Promise.all([rtApi('/api/rotation/daily?month='+encodeURIComponent(daily.month)),rtApi('/api/rotation/daily/'+date+(time?'?at='+encodeURIComponent(time):''))]);
    if(generation!==daily.request)return;let data=loaded;
    if(prepare&&data.date>data.today&&data.can_generate&&data.revision===0){try{await rtPost('/api/rotation/daily/'+date,{action:'draft_generate',revision:0});}catch(error){const latest=await rtApi('/api/rotation/daily/'+date);if(!latest.revision)throw error;}data=await rtApi('/api/rotation/daily/'+date);}
    if(generation!==daily.request)return;daily.calendar={...month,days:month.days.map(d=>d.date===date?{...d,status:data.draft?'draft':data.day?.status||d.status}:d)};daily.data=data;daily.liveVersion=liveVersion;
  }catch(error){if(generation===daily.request){daily.error=error.message;daily.data=null;daily.calendar=null;}}
  finally{if(generation===daily.request){daily.loading=false;if(tab==='draft'){RT.render(false,true);}}}
}
function dailyBoard(data,editable=false){
  const d=data.day;if(!d)return '';
  const people=Object.fromEntries(d.persons.map(p=>[p.pid,p]));
  const seated=new Set(d.lines.flatMap(l=>l.posts.map(x=>x.occ).filter(Boolean)));
  const lines=d.lines.map(l=>`<article class="rt-line${l.active?'':' off'}"><header class="rt-line-head g-${esc(l.group||'')}"><strong class="rt-lid">${esc(l.id)}</strong><span>${l.posts.filter(x=>x.open).length}/${l.posts.length} 岗</span></header>${l.posts.map((x,i)=>{const key=`${l.id}#${i}`,p=people[x.occ],can=editable&&!l.standby;return `<div class="rt-post rt-daily-slot${x.open?'':' closed'}" ${can?`data-daily-slot="${esc(key)}"`:''}>${can?`<button type="button" class="rt-pn rt-daily-post" draggable="true" data-daily-post="${esc(key)}" aria-label="交换岗位 ${esc(x.name)}">${esc(x.name)}</button><button type="button" class="rt-who-name rt-daily-person" draggable="true" data-daily-person="${esc(key)}" aria-label="交换人员 ${esc(key)}">${esc(p?.name||'空岗')}</button>`:`<span class="rt-pn">${esc(x.name)}</span><span class="rt-who-name">${esc(p?.name||'空岗')}</span>`}<span class="rt-mins">${x.open?(p?'预排':'空岗'):'未开岗'}</span></div>`;}).join('')}</article>`).join('');
  const unseated=d.persons.filter(p=>!seated.has(p.pid));
  const section=(title,list)=>`<section class="rt-sect"><h2>${title}<span class="rt-n">${list.length}</span></h2><div class="rt-chips${list.length?'':' empty'}">${list.length?list.map(p=>{const can=editable&&p.role==='rotation';return `<${can?'button type="button" draggable="true" data-daily-person="person:'+esc(p.pid)+'"':'div'} class="rt-chip"><span class="rt-nm">${esc(p.name)}</span><span class="rt-sub">${hm(p.start)}–${hm(p.end)} · ${p.role==='op'?'OP':p.role==='excluded'?'不轮岗':p.state==='notyet'?(data.frozen?'开班预排':'班次未开始'):esc(STATE_NAME[p.state]||'待安排')}</span></${can?'button':'div'}>`;}).join(''):'暂无'}</div></section>`;
  return `<div class="rt-board rt-daily-board"><div class="rt-lines">${lines}</div><div class="rt-pool">${section('待出发',unseated.filter(p=>p.state==='ready'))}${section('待到大屏点去休息',unseated.filter(p=>['walkback','pending','op'].includes(p.state)))}${section('吃饭区',unseated.filter(p=>p.state==='meal'))}${section('休息区',unseated.filter(p=>p.state==='rest'))}${section('圈外人员',unseated.filter(p=>p.state==='away'))}${section('其他人员',unseated.filter(p=>!['ready','walkback','pending','op','meal','rest','away'].includes(p.state)))}</div></div>`;
}
function dailyHistory(data){
  const note=data.opening_source==='late_publish'?`首班之后才启用轮岗，展示 ${hm(data.as_of)} 启用时固定的开班安排。`:data.opening_source==='unstarted_draft'?'此日期保留了未启用的预排，仅供回顾。':data.opening_source==='opening'?`首班 ${hm(data.as_of)} 开始时已固定，后续看板调整不改变本班表。`:data.opening_source==='initial_draft'?'此日期没有首班固定快照，展示当时保存的初始预排。':data.opening_source==='earliest_record'?'此日期没有首班固定快照，展示最早保存记录，仅供回顾。':'此日期没有开班快照，仅保留末次状态供回顾。';
  return `<section class="panel"><h2>${esc(data.date)} · 开班安排回顾</h2><p class="field-hint">只读 · ${esc(note)} 实际轮岗变化请到记录查看。</p>${dailyBoard(data)}</section>`;
}
function draftView(){
  const data=daily.data,calendar=daily.calendar;
  const controls=`<section class="panel rt-daily"><header><h2>每日班表</h2><p class="field-hint">预排未来班表；首班前与今日看板同步，首班开始后固定，只读回顾。</p></header><div class="rt-row"><label>月份<input type="month" data-daily-month value="${esc(daily.month||RT.data.clock.date.slice(0,7))}"></label><label>日期<input type="date" data-daily-date value="${esc(daily.date||RT.data.clock.date)}"></label><button type="button" class="secondary" data-daily-reload>刷新班表</button></div>${calendar?`<div class="rt-daily-calendar" role="group" aria-label="本月每日班表">${['一','二','三','四','五','六','日'].map(d=>`<span class="rt-daily-weekday">${d}</span>`).join('')}${'<span aria-hidden="true"></span>'.repeat((new Date(calendar.month+'-01T12:00:00').getDay()+6)%7)}${calendar.days.map(d=>`<button type="button" class="${d.date===daily.date?'primary':'secondary'}" data-daily-date-button="${d.date}" aria-pressed="${d.date===daily.date}"><strong>${Number(d.date.slice(-2))}</strong><small>${d.status==='draft'?'预排':d.status==='live'?'已启用':d.status==='ended'?'已结束':d.has_roster?'有班表':'无班表'}</small></button>`).join('')}</div>`:''}${daily.loading?'<p role="status">正在读取班表…</p>':''}${daily.error?`<p role="alert" class="rt-warn">${esc(daily.error)}，可刷新重试。</p>`:''}</section>`;
  if(!data||daily.loading)return controls;
  const roster=`<section class="panel"><details><summary>上传班表 · ${data.roster.people.length}人${data.roster.file_name?' · '+esc(data.roster.file_name):''}</summary><div class="rt-scroll"><table class="rt-table"><thead><tr><th>姓名</th><th>工号</th><th>班次原文</th></tr></thead><tbody>${data.roster.people.map(p=>`<tr><td>${esc(p.name)}</td><td>${esc(p.employee_no)}</td><td>${esc(p.cell)}</td></tr>`).join('')}</tbody></table></div></details></section>`;
  if(data.can_edit){
    return controls+roster+`<section class="panel"><h3>调整当日预排</h3><p class="field-hint">拖拽或依次点击两张卡片交换。岗位与人员均可跨线交换；岗位交换连同预排人员移动，7点岗位仍校验班次。</p><div class="rt-row"><button type="button" class="${daily.mode==='people'?'primary':'secondary'}" data-daily-mode="people">人员交换</button><button type="button" class="${daily.mode==='posts'?'primary':'secondary'}" data-daily-mode="posts">岗位交换</button><span role="status" data-daily-selection>${daily.source?'已选第一个位置，请选择目标':'选择或拖拽一个位置开始'}</span></div>${data.roster_changed?'<p class="rt-warn">上传班表已更新，当前预排仍为原安排，不会自动启用；请重新生成并核对。</p>':''}</section>`+initialDraftView(data);
  }
  if(data.day||data.times.length||data.legacy_history){
    return controls+roster+dailyHistory(data);
  }
  if(data.can_generate)return controls+roster+`<section class="panel"><h3>这一天已有班表</h3><p class="field-hint">生成预排后即可调整岗位和人员，不会提前计时。</p><button type="button" class="primary" data-d="generate">生成这一天的预排</button></section>`;
  return controls+roster+'<section class="panel rt-empty"><h3>暂无可查看的轮岗</h3><p class="field-hint">没有历史快照时不会根据现在的规则补造过去记录；新日期请先上传班表。</p></section>';
}
async function dailySend(body){
  if(daily.busy||!daily.data)return;daily.busy=true;const date=daily.date,revision=daily.data.revision,planRevision=daily.data.plan_revision,request=daily.request;
  try{await rtPost('/api/rotation/daily/'+date,{...body,revision,plan_revision:planRevision});toast('每日班表已保存');if(daily.request===request&&daily.date===date)await loadDaily(date,'',body.action!=='draft_discard');await RT.load();}
  catch(error){toast(error.message,true);if(daily.request===request&&daily.date===date)await loadDaily(date,daily.time,false);}
  finally{daily.busy=false;daily.source=null;}
}
function dailyLabel(key,mode){
  if(key.startsWith('person:'))return daily.data?.draft?.persons[key.slice(7)]?.name||'待安排人员';
  const [line,index]=key.split('#'),draft=daily.data?.draft,post=draft?.lines.find(l=>l.id===line)?.posts[Number(index)];
  const pid=draft?.plan.seven[key]||draft?.plan.crew[key];
  return `${line}线 · ${post?.name||key}${mode==='people'?' · '+(draft?.persons[pid]?.name||'空岗'):''}`;
}
function dailyChoose(key,mode=daily.mode){
  if(daily.busy||!daily.data?.can_edit)return;
  if(!daily.source||daily.mode!==mode){daily.source=key;daily.mode=mode;$app.querySelectorAll('[data-daily-person],[data-daily-post]').forEach(b=>b.classList.toggle('is-selected',mode==='people'?b.dataset.dailyPerson===key:b.dataset.dailyPost===key));const text=$app.querySelector('[data-daily-selection]');if(text)text.textContent='已选 '+dailyLabel(key,mode)+'，请选择目标';return;}
  const source=daily.source;daily.source=null;$app.querySelectorAll('.is-selected').forEach(b=>b.classList.remove('is-selected'));if(source===key)return;
  const revision=daily.data.revision,date=daily.date;
  confirmBox(mode==='posts'?'交换两个岗位':'交换两处人员',`${esc(dailyLabel(source,mode))} ↔ ${esc(dailyLabel(key,mode))}。保存这一天的预排；今天首班前同步看板，未到班次不计时。`,'确认交换',()=>{
    if(daily.date!==date||daily.data?.revision!==revision){toast('班表已变化，请重新选择',true);return;}
    return dailySend({action:mode==='posts'?'draft_swap_posts':'draft_swap_people',source,target:key});
  });
}
function bindDraft(){
  const month=$app.querySelector('[data-daily-month]'),date=$app.querySelector('[data-daily-date]');
  if(month)month.onchange=()=>{if(month.value)void loadDaily(month.value+'-01','');};
  if(date)date.onchange=()=>{if(date.value)void loadDaily(date.value,'');};
  $app.querySelector('[data-daily-reload]')?.addEventListener('click',()=>void loadDaily(daily.date||RT.data.clock.date,daily.time));
  $app.querySelectorAll('[data-daily-date-button]').forEach(b=>b.onclick=()=>void loadDaily(b.dataset.dailyDateButton,''));
  $app.querySelector('[data-daily-time]')?.addEventListener('change',e=>void loadDaily(daily.date,e.target.value));
  $app.querySelector('[data-daily-latest]')?.addEventListener('click',()=>void loadDaily(daily.date,''));
  $app.querySelectorAll('[data-daily-mode]').forEach(b=>b.onclick=()=>{daily.mode=b.dataset.dailyMode;daily.source=null;RT.render(false,true);});
  $app.querySelectorAll('[data-daily-person],[data-daily-post]').forEach(b=>{
    const mode=b.dataset.dailyPerson?'people':'posts',key=b.dataset.dailyPerson||b.dataset.dailyPost;
    let touchStart=null,suppressClickUntil=0;
    b.onclick=()=>{if(Date.now()>=suppressClickUntil)dailyChoose(key,mode);};
    b.onpointerdown=e=>{if(e.pointerType!=='touch'||daily.busy)return;touchStart={x:e.clientX,y:e.clientY};RT.dragging=true;b.setPointerCapture(e.pointerId);};
    b.onpointerup=e=>{if(!touchStart)return;const moved=Math.hypot(e.clientX-touchStart.x,e.clientY-touchStart.y)>12;touchStart=null;RT.dragging=false;RT.pressing=false;if(!moved)return;suppressClickUntil=Date.now()+500;e.preventDefault();const target=document.elementFromPoint(e.clientX,e.clientY)?.closest('[data-daily-slot]');if(target){daily.source=key;daily.mode=mode;dailyChoose(target.dataset.dailySlot,mode);}};
    b.onpointercancel=()=>{touchStart=null;RT.dragging=false;RT.pressing=false;};
    b.ondragstart=e=>{if(daily.busy){e.preventDefault();return;}RT.dragging=true;e.dataTransfer.setData('text/plain',JSON.stringify({mode,key}));};
    b.ondragend=()=>{RT.dragging=false;RT.pressing=false;};
  });
  $app.querySelectorAll('[data-daily-slot]').forEach(el=>{
    el.ondragover=e=>e.preventDefault();
    el.ondrop=e=>{e.preventDefault();RT.dragging=false;RT.pressing=false;try{const source=JSON.parse(e.dataTransfer.getData('text/plain'));if(!['people','posts'].includes(source.mode)||typeof source.key!=='string')return;daily.source=source.key;daily.mode=source.mode;dailyChoose(el.dataset.dailySlot,source.mode);}catch(_){toast('请选择有效的岗位或人员',true);}};
  });
  const send=body=>dailySend(body);
  $app.querySelectorAll('[data-d]').forEach(b=>b.onclick=()=>{
    if(b.dataset.d==='generate')void send({action:'draft_generate'});
    if(b.dataset.d==='discard')confirmBox('放弃预排','仅移除此日未发布预排，上传班表保留；再次打开未来日期会重新生成预排。','确认放弃',()=>send({action:'draft_discard'}));
    if(b.dataset.d==='publish')confirmBox('发布当天轮岗','将开始运行当天轮岗，未来日期不允许提前发布。','确认发布',()=>send({action:'publish'}));
  });
  const close=document.getElementById('draftClose');if(close)close.onchange=()=>void send({action:'draft_close',close:close.value});
  $app.querySelectorAll('[data-draft]').forEach(n=>n.onchange=()=>void send({action:'draft_set',kind:n.dataset.draft,key:n.dataset.key,pid:n.value}));
  $app.querySelectorAll('[data-list]').forEach(n=>n.onchange=()=>void send({action:'draft_list',kind:n.dataset.list,pids:[...$app.querySelectorAll(`[data-list="${n.dataset.list}"]`)].map(x=>x.value)}));
  $app.querySelectorAll('[data-role]').forEach(n=>n.onchange=()=>void send({action:'draft_role',pid:n.dataset.role,role:n.value}));
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
    <p class="field-hint">支持 ZTP 周班表，或标准模板（列：工号、姓名、日期、班次、备注；班次如 07:15-16:15）。按天上传会覆盖按周上传的同一天。${formalRotation?'以班表为准加入轮岗，跨圈直接识别；账号异常集中确认加入或跳过，未注册人员建立独立轮岗身份。':'名单为模拟数据，只用于轮岗测试。'}</p>
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
    if(RT.modalOpen)return;
    const data = new FormData(form);
    if (!data.get('date')) data.delete('date');
    const button=form.querySelector('[type=submit]');if(button.disabled)return;button.disabled=true;
    const perform=async()=>{
      try{
        const out=await rtApi('/api/rotation/roster/upload',{method:'POST',body:data,timeoutMs:120000});
        toast(`已导入 ${out.start_date}${out.end_date!==out.start_date?` 至 ${out.end_date}`:''}，共 ${out.entry_count} 条`);
        form.reset();await RT.load();await loadRoster(out.start_date);return true;
      }catch(error){toast(error.message,true);return false;}
    };
    try {
      if(formalRotation){
        const check=new FormData();for(const [key,value] of data.entries())check.append(key,value);check.set('preview','true');
        const batch=await rtApi('/api/rotation/roster/upload',{method:'POST',body:check});
        data.set('expected_digest',batch.digest);
        if(batch.warnings?.length){
          openModal(`<h3>以下人员存在账号异常</h3><p>正常人员照常导入。勾选异常人员可仅加入轮岗，不会启用其工作台账号。</p><div class="rt-scroll">${batch.warnings.map(p=>`<label class="rt-roster-exception"><input type="checkbox" data-exception-no="${esc(p.employee_no)}" value="${esc(p.employee_no)}" checked><span><strong>${esc(p.name)} · ${esc(p.employee_no)}</strong><small>${esc(p.reasons.join('、'))}</small></span></label>`).join('')}</div>${batch.new.some(p=>batch.identities[p.employee_no]?.temporary)?`<p>另有 ${batch.new.filter(p=>batch.identities[p.employee_no]?.temporary).length} 位未注册人员，将创建独立轮岗身份。</p>`:''}<p class="field-hint">已有登录账号使用原密码；无登录账号使用工号后四位。情况仅在后台轮岗记录中标注。</p><div class="modal-actions"><button type="button" class="ghost" data-close>返回检查</button><button type="button" class="secondary" data-skip-exceptions>跳过异常人员</button><button type="button" class="primary" data-include-exceptions>确认加入轮岗</button></div>`,host=>{
            const commit=async(skipAll)=>{
              data.set('confirm_exceptions','true');
              data.set('skip_employee_nos',JSON.stringify([...host.querySelectorAll('[data-exception-no]')].filter(el=>skipAll||!el.checked).map(el=>el.value)));
              host.querySelectorAll('button,input').forEach(el=>el.disabled=true);
              if(await perform())closeModal();else host.querySelectorAll('button,input').forEach(el=>el.disabled=false);
            };
            host.querySelector('[data-include-exceptions]').onclick=()=>commit(false);
            host.querySelector('[data-skip-exceptions]').onclick=()=>commit(true);
          });return;
        }
        if(batch.new.length){
          confirmBox('确认班表人员',`<p>将为 ${batch.new.length} 位未注册人员创建仅可确认本人轮岗待办的临时账号。</p><div class="rt-scroll"><ul>${batch.new.map(p=>`<li>${esc(p.name)} · ${esc(p.employee_no)}</li>`).join('')}</ul></div><p class="field-hint">完整工号登录，密码为后四位；不进入正式员工统计。已有临时身份复用，历史记录保留。</p>`,'确认导入',perform);return;
        }
      }
      await perform();
    } catch (error) { toast(error.message, true); }
    finally{button.disabled=false;}
  };
  $app.querySelectorAll('[data-rdate]').forEach(button => { button.onclick = () => loadRoster(button.dataset.rdate); });
}

// ---------------------------------------------------------------- 记录

const EVENT_NAME = {
  force_place:'强制安排上岗',entry_group:'整组上岗',entry_ready:'本人确认到位',entry_solo:'调整单人上岗',entry_adjust:'调整协同组合',entry_return:'本人到场重新领取安排',
  post_hold:'设置持续定岗',post_resume:'结束定岗', publish: '发布', crew_start: '开园上岗', push: '推岗进线', push7: '推 7 点', chain_out: '推下班/推出圈', pushed_off: '下线', pushed_out: '被推出',
  arrive: '去休息', arrive_done: '去休息后下班', arrive_away: '去休息后转暂离', depart: '去轮岗', undo_arrive: '撤回去休息', away: '暂离/出圈', back: '回池',
  initial_rest:'班次开始自动休息', reassign: '改派', leave: '离岗', flag: '标记', move:'现场人员调整',post_order:'岗位顺序调整',post_schedule_close:'计划撤岗',post_open: '开岗', post_close: '撤岗', line_on: '启用线', line_off: '停用线', set_close: '改闭园时间',
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
  if(item.type==='force_place'&&d.previous)parts.push(`${({meal:'吃饭',meal_rest:'饭后休息',rest:'休息'})[d.previous.kind]||'休息'}中断，剩余 ${Math.ceil(d.previous.remaining||0)} 分钟${d.replaced?'，原人员下线待确认休息':''}`);
  if(item.type==='post_order'&&d.target_line)parts.push(`跨线至 ${d.target_line}`);
  if(item.type==='post_order'&&d.mode)parts.push(d.mode==='posts'?'只交换岗位':d.mode==='with_people'?'岗位和人员一起交换':'调整岗位顺序');
  if(item.type==='entry_group')parts.push(`${d.participants?.length||0}人整组执行`);
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
  const recordBox = rec ? `<h3>${esc(rec.employee_no)}${rec.segments.some(s=>s.temporary)||rec.events.some(e=>e.temporary)?' · 含临时身份记录':''} 本周（${esc(rec.week[0])} 至 ${esc(rec.week[1])}）</h3>
    <div class="rt-row">${Object.entries(rec.totals).sort().map(([line, m]) => `<span class="rt-pill">${esc(line)} ${Math.round(m)} 分钟</span>`).join('') || '<span class="field-hint">本周还没有在线记录</span>'}</div>
    ${rec.duties.length ? `<p class="field-hint">本周任务：${rec.duties.map(d => `${esc(d.date)} ${d.kind === 'push7' ? '推 7 点' : '送失物'}`).join('；')}</p>` : ''}
    <div class="rt-scroll"><table class="rt-table"><thead><tr><th>日期</th><th>线</th><th>开始</th><th>结束</th><th>分钟</th></tr></thead><tbody>${rec.segments.map(s => `<tr><td>${esc(s.date)}${(s.identity_labels?.length?s.identity_labels:s.temporary?['临时']:[]).map(label=>`（${esc(label)}）`).join('')}</td><td>${esc(s.line)}</td><td>${hm(s.start)}</td><td>${hm(s.end)}</td><td>${Math.round(s.minutes)}</td></tr>`).join('')}</tbody></table></div>` : '';
  return `<section class="panel"><h2>个人轮岗记录</h2><form id="recordForm" class="rt-row"><label><span class="rt-label-text">工号</span><input name="no" value="${esc(view.recordNo)}" maxlength="50" required></label><button type="submit" class="primary">查询</button></form>${recordBox}</section>
    <section class="panel"><h2>操作日志</h2><div class="rt-row"><label><span class="rt-label-text">日期</span><input type="date" id="logDate" value="${esc(view.logDate || RT.data.clock.date)}"></label><button type="button" class="secondary" id="logLoad">查看</button></div>
    <div class="rt-scroll tall"><table class="rt-table"><thead><tr><th>时间</th><th>操作</th><th>人员</th><th>线/岗位</th><th>说明</th><th>操作人</th></tr></thead><tbody>${(view.log || []).map(item => `<tr><td>${esc(item.time)}</td><td>${esc(EVENT_NAME[item.type] || item.type)}</td><td>${esc(item.name || '')}${(item.identity_labels?.length?item.identity_labels:item.temporary?['临时']:[]).map(label=>`（${esc(label)}）`).join('')}</td><td>${esc([item.line, item.post].filter(Boolean).join(' '))}</td><td>${esc(detailText(item))}</td><td>${esc(item.actor)}${item.actor_temporary?'（临时）':''}</td></tr>`).join('') || '<tr><td colspan="6">暂无记录</td></tr>'}</tbody></table></div></section>`;
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
    view.accounts = formalRotation?[]:(await rtApi('/api/rotation/accounts')).items;
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
    <section class="panel" ${formalRotation?'hidden':''}><h2>轮岗专用账号</h2><p class="field-hint">在登录页直接登录时使用的密码；测试入口里切换模拟账号不需要密码。</p>
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
  const action = p && (['walkback', 'pending'].includes(p.state)||opWaiting(p)) ? 'arrive' : p?.assign ? 'depart' : null;
  const can = action === 'arrive' || (action === 'depart' && canDepartNow(p, n));
  const actionButton = action ? `<div class="rt-actions"><button type="button" class="primary" data-member-act="${action}" ${can && !confirmationBlocked() ? '' : 'disabled'}>${RT.pendingConfirmation ? '正在核实确认结果' : RT.submitting ? '正在提交' : action === 'arrive' ? '确认已到休息室' : can ? '去轮岗' : '等待出发'}</button></div>` : '';
  const todo = `<section class="panel rt-todo"><div class="rt-todo-head"><h2>待办</h2><span class="rotation-test-badge">测试</span></div>
    ${open.length ? open.map(x => `<article class="rt-notice ${esc(x.kind)}"><strong>${esc(x.title)}</strong>${x.body ? `<p>${esc(x.body)}</p>` : ''}</article>`).join('') : '<p class="field-hint">当前没有轮岗待办。</p>'}
    ${action === 'depart' ? `<div class="rt-route">${assignmentDescription(p.assign)}</div>` : ''}${actionButton}
    ${done.length ? `<details><summary>已完成 ${done.length} 项</summary>${done.map(x => `<p class="rt-notice-done">${esc(x.updated_at || '')} ${esc(x.title)}</p>`).join('')}</details>` : ''}</section>`;
  if (!p) return `${todo}<section class="panel rt-empty"><h2>今天没有你的轮岗</h2><p>名单发布后这里会显示你的状态和所在线。</p></section>`;
  const state = poolState(p, n);
  let status = STATE_NAME[state] || '';
  if (p.state === 'onpost') status = `在 ${esc(p.line)} 线 ${esc(p.post || '')}，已站 ${Math.max(0, Math.floor(n - (p.lineStart || n)))} 分钟`;
  if (state === 'rest' || state === 'meal') {
    const until = state === 'meal' ? p.readyAt : restUntil(p);
    status = `${state === 'meal' ? '吃饭' : '休息'}至 ${hm(until)}，还剩 ${Math.max(0, Math.ceil(until - n))} 分钟`;
  }
  if (waitingInitialRest(p)) status = `班次未开始 · ${hm(p.scheduledRestStart)}自动开始休息`;
  if (p.state === 'heading') status = `前往 ${esc(p.assign ? p.assign.line : '')} 线，约 ${hm(p.arriveAt)} 到岗`;
  if (p.state === 'away' && p.away) status = `暂离：${esc(p.away.reason)}${p.away.until !== null && p.away.until !== undefined ? `，${hm(p.away.until)} 回` : ''}`;
  const next = p.assign ? `<p>下一步：${goText(p)}，${hm(p.assign.departAt)} 出发</p>` : '';
  const line = d.line ? `<section class="panel"><h2>${esc(d.line.id)} 线</h2><div class="rt-line">${d.line.posts.map(x => `<div class="rt-post${x.occ === p.pid ? ' me' : ''}${x.open ? '' : ' closed'}"><span class="rt-pn">${esc(x.name)}</span><span class="rt-who-name">${x.open ? esc(x.occName || '空岗') : '未开'}${x.hold?'<span class="rt-badge post-hold">定岗</span>':''}</span></div>`).join('')}</div></section>` : '';
  const today = (d.today || []).map(s => `<span class="rt-pill">${esc(s.line)} ${hm(s.start)}-${hm(s.end)}</span>`).join('');
  const week = Object.entries(d.week_minutes || {}).sort().map(([lineId, m]) => `<span class="rt-pill">${esc(lineId)} ${Math.round(m)} 分钟</span>`).join('');
  const results = d.confirmations || [];
  const resultCard = r => `<article class="rt-confirmation"><strong>${hm(r.minute)} · ${r.action === 'depart' ? '已确认进线' : '已确认到达休息室'}</strong>${r.action === 'depart' ? `<p>进入 ${esc(r.line)} 线 · ${esc(r.post)}</p>${r.assignment?.targetName ? `<p>${esc(r.assignment.why || (r.assignment.mode === 'push7' ? '推7点' : '替换'))}：${esc(r.assignment.targetName)}<br>原岗位：${esc(r.assignment.targetLine || r.assignment.line)} · ${esc(r.assignment.targetPost)}</p>` : ''}${r.assignment?.mode === 'chain' ? '<p class="field-hint">从入口岗依次推进，推至目标人员所在岗位。</p>' : ''}` : `<p>${STATE_NAME[r.state] || ''}${['rest','meal','ready'].includes(r.state) && r.readyAt ? `至 ${hm(r.readyAt)}` : ''}</p>`}<small>${r.source === 'member' ? '个人待办确认' : '大屏确认'}</small></article>`;
  const confirmations = results.length ? `<section class="panel"><h2>本次确认</h2>${resultCard(results[0])}${results.length > 1 ? `<details><summary>此前确认 ${results.length - 1} 条</summary>${results.slice(1).map(resultCard).join('')}</details>` : ''}</section>` : '';
  return `${todo}<section class="panel"><h2>${esc(p.name)}</h2><p class="rt-status">${status}</p>${next}<p class="field-hint">班次 ${hm(p.start)}-${hm(p.end)}${p.mealEligible ? (p.ate ? '，已吃饭' : '，有吃饭') : ''}${badges(p)}</p></section>${confirmations}${line}
    <section class="panel"><h2>今天去过的线</h2><div class="rt-row">${today || '<span class="field-hint">暂无</span>'}</div><h2>本周各线累计</h2><div class="rt-row">${week || '<span class="field-hint">暂无</span>'}</div></section>`;
}

// ---------------------------------------------------------------- 渲染与加载

RT.render = (fromTicker,forceDaily=false) => {
  if (!RT.data || !me || RT.dragging) return;
  renderClock();
  if (personalRotation || me.kind === 'member'&&!formalRotation) {
    const expanded = [...$app.querySelectorAll('details')].map(el => el.open);
    $app.innerHTML = memberView();
    [...$app.querySelectorAll('details')].forEach((el, i) => { el.open = expanded[i] || false; });
    return;
  }
  // Refresh an idle editable daily board on live changes or the first-shift boundary.
  if(tab==='draft'&&!forceDaily&&daily.data?.can_edit&&daily.date<=RT.data.clock.date&&!daily.loading&&!daily.busy&&!editing()){
    const boundary=daily.date<RT.data.clock.date||nowMin()>=daily.data.first_start;
    if(boundary||!daily.source&&!fromTicker&&daily.liveVersion!==RT.data.version){void loadDaily(daily.date,'',false);return;}
  }
  // 每秒刷新只更新看板；表单页在正在输入或由计时器触发时不重绘
  if (fromTicker && tab !== 'board') return;
  // A delayed roster read must not replace a file the manager has already selected.
  if(tab==='roster'&&$app.querySelector('#rosterForm [type=file]')?.files?.length)return;
  // Live-board SSE must not replace the calendar or a pending daily-plan selection.
  if(tab==='draft'&&!forceDaily&&$app.querySelector('.rt-daily'))return;
  if (editing()&&!(forceDaily&&tab==='draft')) return;
  if (!fromTicker) renderClockBar();
  const html = tab === 'board' ? boardView() : tab === 'draft' ? draftView() : tab === 'roster' ? rosterView() : tab === 'records' ? recordsView() : settingsView();
  const expanded = tab==='draft'?[...$app.querySelectorAll('details')].map(el=>el.open):[];
  const scroll = [...$app.querySelectorAll('.rt-chips, .rt-scroll')].map(el => el.scrollTop);
  $app.innerHTML = html;
  if (tab === 'board') $app.querySelectorAll('tr[data-pid]').forEach(row => {
    row.draggable = personOf(row.dataset.pid)?.state === 'notyet';
  });
  [...$app.querySelectorAll('.rt-chips, .rt-scroll')].forEach((el, i) => { el.scrollTop = scroll[i] || 0; });
  if(formalRotation&&!isManager()&&!personalRotation)$app.querySelectorAll('[data-pid],[data-post],[data-line],[draggable]').forEach(el=>{el.removeAttribute('tabindex');el.draggable=false;if(el.tagName==='BUTTON')el.disabled=true;});
  if (tab === 'draft'){[...$app.querySelectorAll('details')].forEach((el,i)=>{el.open=expanded[i]||false;});bindDraft();}
  if (tab === 'roster') bindRoster();
  if (tab === 'records') bindRecords();
  if (tab === 'settings') bindSettings();
};

RT.load = async () => {
  await loadLive(personalRotation||me.kind === 'member'&&!formalRotation ? '/api/rotation/me' : '/api/rotation/board');
};

$app.addEventListener('click', event => {
  const button = event.target.closest('[data-member-act]');
  if (!button || button.disabled || !personalRotation&&me?.kind !== 'member' || !RT.data?.person) return;
  confirmRotation(RT.data.person, button.dataset.memberAct, '/api/rotation/me/act');
});

document.getElementById('rtSwitch').onclick = async () => {
  if(formalRotation){location.href=portalPath('/?view=actionCenter');return;}
  storePendingConfirmation(null);
  try { await rtPost('/api/rotation/logout'); } catch (_) { /* 退出失败也回到入口 */ }
  location.href = portalPath('/');
};

(async () => {
  try {
    me = await rtApi('/api/rotation/whoami');
    if(formalRotation){if(!me.capabilities.can_manage||personalRotation)tab='board';document.getElementById('rtSwitch').textContent='返回工作台';}
    if (me.kind === 'screen') { location.href = portalPath('/rotation/screen'); return; }
    if (me.adjustment&&!['board','records'].includes(tab))tab='board';
    renderWho();
    await RT.load();
    renderTabs();
    if (isManager()) openTab();
    connectLive();
    startLocalTicker();
  } catch (error) {
    $app.innerHTML = `<section class="panel rt-empty"><h2>无法进入轮岗</h2><p>${esc(error.message)}</p></section>`;
  }
})();
