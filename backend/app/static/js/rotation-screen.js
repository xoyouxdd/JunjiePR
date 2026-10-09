'use strict';
// 休息室大屏：展示各线与休息区；只能点「去休息」和「去轮岗」，点后先确认是本人。

const $app = document.getElementById('rtApp');

function poolSection(title, hint, list, chip, extraClass = '') {
  return `<section class="rt-sect ${extraClass}"><h2>${title}<span class="rt-n">${list.length}</span><span class="rt-hint">${hint}</span></h2><div class="rt-chips${list.length ? '' : ' empty'}">${list.length ? list.map(chip).join('') : '暂无'}</div></section>`;
}

function screenView() {
  const day = RT.data.day;
  if (!day) return '<section class="panel rt-empty"><h2>今天的轮岗还没有开始</h2><p>主管发布后，这里会自动显示。</p></section>';
  if (day.status === 'ended') return '<section class="panel rt-empty"><h2>今天的轮岗已结束</h2></section>';
  const n = nowMin();
  const ps = day.persons;
  const early = (RT.data.settings && RT.data.settings.departEarly) || 1;
  const arrive = ps.filter(p => p.state === 'walkback' || p.state === 'pending').sort((a, b) => (a.walkbackSince || 0) - (b.walkbackSince || 0));
  const ready = ps.filter(p => p.state === 'ready').sort((a, b) => (a.assign ? a.assign.departAt : 9e9) - (b.assign ? b.assign.departAt : 9e9));
  const resting = ps.filter(p => p.state === 'rest').sort((a, b) => a.readyAt - b.readyAt);
  const meals = ps.filter(p => p.state === 'meal').sort((a, b) => a.readyAt - b.readyAt);
  const lines = `<div class="rt-lines">${day.lines.filter(L => L.active || L.posts.some(x => x.occ)).map(L => lineCard(L, n, false)).join('')}</div>`;
  let pool = '';
  pool += poolSection('去轮岗', '到出发时间点自己的名字', ready, p => {
    const a = p.assign;
    let cls = 'rt-chip wait', sub;
    if (!a) { cls = 'rt-chip dim'; sub = '去向安排中'; }
    else {
      const over = n - a.departAt;
      if (over >= 2) cls = 'rt-chip late'; else if (over >= -early) cls = 'rt-chip due';
      sub = over >= -early ? (over >= 1 ? `已超时 ${Math.floor(over)} 分钟` : '现在出发') : `${hm(a.departAt)} 出发`;
    }
    const can = a && n >= a.departAt - early - 0.01;
    return `<button type="button" class="${cls}" data-pid="${esc(p.pid)}" data-act="depart" ${can ? '' : 'aria-disabled="true"'}><span class="rt-nm">${esc(p.name)}</span><span class="rt-go">${goText(p)}</span><span class="rt-sub">${sub}</span>${badges(p)}</button>`;
  }, 'rt-ready');
  pool += poolSection('去休息 / 吃饭', '下线回到休息室点自己的名字', arrive, p => {
    const mins = p.walkbackSince !== null && p.walkbackSince !== undefined ? Math.max(0, Math.floor(n - p.walkbackSince)) : 0;
    let sub = p.state === 'pending' ? 'OP 结束，点击转入休息区' : `下线 ${mins} 分钟`;
    if (p.after === 'done') sub = '今天轮岗结束，点击即可下班';
    return `<button type="button" class="rt-chip${mins >= 10 ? ' due' : ''}" data-pid="${esc(p.pid)}" data-act="arrive"><span class="rt-nm">${esc(p.name)}</span><span class="rt-sub">${sub}</span>${badges(p)}</button>`;
  });
  pool += poolSection('吃饭区', '', meals, p => `<div class="rt-chip meal"><span class="rt-nm">${esc(p.name)}</span><span class="rt-sub">吃饭至 ${hm(p.readyAt)}，剩余 ${Math.max(0, Math.ceil(p.readyAt-n))} 分钟</span>${badges(p)}</div>`);
  pool += poolSection('休息区', '饭后倒计时结束前不能进线', resting, p => {
    const kind = p.state === 'meal' ? '<span class="rt-badge meal">吃饭</span>' : '<span class="rt-badge rest">休息</span>';
    const next = p.assign ? `${goText(p)} <small>${hm(p.assign.departAt)}</small>` : '';
    return `<div class="rt-chip ${p.state}"><span class="rt-nm">${esc(p.name)} ${kind}</span><span class="rt-sub">至 ${hm(p.readyAt)}，还剩 ${Math.max(0, Math.ceil(p.readyAt - n))} 分钟</span><span class="rt-go">${next}</span>${badges(p)}</div>`;
  });
  return `<div class="rt-board">${lines}<div class="rt-pool">${pool}</div></div>`;
}

RT.render = () => {
  if (!RT.data) return;
  renderClock();
  const scroll = [...$app.querySelectorAll('.rt-chips')].map(el => el.scrollTop);
  $app.innerHTML = screenView();
  [...$app.querySelectorAll('.rt-chips')].forEach((el, i) => { el.scrollTop = scroll[i] || 0; });
};

RT.load = async () => {
  const data = await rtApi('/api/rotation/screen/state');
  RT.data = data;
  RT.fetchedAt = Date.now();
  if (!RT.modalOpen) RT.render();
};

$app.addEventListener('click', event => {
  const chip = event.target.closest('[data-pid][data-act]');
  if (!chip) return;
  const p = personOf(chip.dataset.pid);
  if (!p) return;
  if (chip.dataset.act === 'arrive') {
    const note = p.after === 'done' ? '今天轮岗结束，确认后即可下班。' : p.state === 'pending' ? 'OP 结束，确认后转入休息区。' : '确认已回到休息室？系统会安排休息或吃饭。';
    confirmBox(`确认是 ${esc(p.name)}（${esc(p.pid)}）？`, note, '确认去休息', async () => {
      const done = await act('/api/rotation/screen/act', { action: 'arrive', pid: p.pid });
      if (done) {
        const q = personOf(p.pid);
        if (q) toast(q.state === 'meal' ? `${q.name}：吃饭至 ${hm(q.readyAt)}` : q.state === 'rest' ? `${q.name}：休息至 ${hm(q.readyAt)}` : `${q.name}：${STATE_NAME[q.state] || ''}`);
      }
      return done;
    }, { autoCancel: 8000 });
  } else if (chip.dataset.act === 'depart') {
    const a = p.assign;
    if (!a) { toast(`${p.name} 还没有分配去向，请稍等`, true); return; }
    if (chip.getAttribute('aria-disabled') === 'true') { toast(`${p.name} 请在 ${hm(a.departAt)} 出发`, true); return; }
    const how = a.mode === 'push7' ? `到 7 点岗位直接替换 ${esc(a.targetName || '')}` : a.mode === 'chain' ? `从入口岗推进，${esc(a.why || '推下班')}替换 ${esc(a.targetName || '')}` : '从入口岗进线推岗';
    confirmBox(`确认是 ${esc(p.name)}（${esc(p.pid)}）？`, `出发去 <b>${esc(a.line)}</b> 线：${how}。出发后不能撤销。`, '确认去轮岗', () => act('/api/rotation/screen/act', { action: 'depart', pid: p.pid }, `${p.name} 已出发去 ${a.line} 线`), { autoCancel: 8000 });
  }
});

(async () => {
  try {
    await RT.load();
    connectLive();
    startLocalTicker();
  } catch (error) {
    $app.innerHTML = `<section class="panel rt-empty"><h2>无法加载大屏</h2><p>${esc(error.message)}</p></section>`;
  }
})();
