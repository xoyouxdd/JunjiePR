'use strict';
// 休息室大屏：展示各线与休息区；只能点「去休息」和「去轮岗」，点后先确认是本人。

const $app = document.getElementById('rtApp');

function poolSection(title, hint, list, chip, extraClass = '', grouped = false) {
  const content=grouped?groupedChips(list,chip,nowMin(),extraClass==='rt-ready'):list.map(chip).join('');
  return `<section class="rt-sect ${extraClass}${list.length ? '' : ' is-empty'}"><h2>${title}<span class="rt-n">${list.length}</span></h2>${hint&&list.length?`<p class="rt-pool-hint">${hint}</p>`:''}<div class="rt-chips${list.length ? '' : ' empty'}">${list.length ? content : '暂无'}</div></section>`;
}

function screenView() {
  const day = RT.data.day;
  if (!day) return '<section class="panel rt-empty"><h2>今天的轮岗还没有开始</h2><p>主管发布后，这里会自动显示。</p></section>';
  if (day.status === 'ended') return '<section class="panel rt-empty"><h2>今天的轮岗已结束</h2></section>';
  const n = nowMin();
  const posted=new Set(day.lines.flatMap(line=>line.posts.map(post=>post.occ).filter(Boolean)));
  const ps = day.persons.filter(p=>!posted.has(p.pid));
  const drag=p=>typeof adjustmentState!=='undefined'&&adjustmentState?` draggable="true" data-drag-pid="${esc(p.pid)}"`:'';
  const early = departureEarly();
  const arrive = ps.filter(p => p.state === 'walkback' || p.state === 'pending'||opWaiting(p)).sort((a, b) => (a.walkbackSince || 0) - (b.walkbackSince || 0));
  const ready = ps.filter(p => poolState(p, n) === 'ready').sort((a, b) => (a.assign ? a.assign.departAt : 9e9) - (b.assign ? b.assign.departAt : 9e9));
  const resting = ps.filter(p => poolState(p, n) === 'rest').sort((a, b) => restUntil(a) - restUntil(b));
  const meals = ps.filter(p => p.state === 'meal').sort((a, b) => a.readyAt - b.readyAt);
  const lines = `<div class="rt-lines">${day.lines.filter(L => L.active || L.posts.some(x => x.occ)).map(L => lineCard(L, n, typeof adjustmentState !== 'undefined' && !!adjustmentState)).join('')}</div>`;
  let pool = '';
  pool += poolSection('待出发', '点本人姓名确认进线', ready, p => {
    const a = p.assign;
    let cls = 'rt-chip wait', sub;
    if (!a) { cls = 'rt-chip dim'; sub = '去向安排中'; }
    else {
      const over = n - a.departAt;
      if (over >= 2) cls = 'rt-chip late'; else if (over >= -early) cls = 'rt-chip due';
      sub = over >= -early ? (over >= 1 ? `已超时 ${Math.floor(over)} 分钟` : '现在出发') : `${hm(a.departAt)} 出发`;
    }
    const can = (p.entryGroup?groupCanDepart(p.entryGroup,n):canDepartNow(p, n)) && !confirmationBlocked();
    return `<button type="button" class="${cls}" data-pid="${esc(p.pid)}" data-act="depart"${drag(p)} ${can ? '' : 'aria-disabled="true"'}><span class="rt-nm">${esc(p.name)}</span><span class="rt-go">${goText(p)}</span><span class="rt-sub">${sub}</span>${badges(p)}</button>`;
  }, 'rt-ready',true);
  pool += poolSection('到休息室确认', '到达后点本人姓名', arrive, p => {
    const mins = p.walkbackSince !== null && p.walkbackSince !== undefined ? Math.max(0, Math.floor(n - p.walkbackSince)) : 0;
    let sub = opWaiting(p)?'OP · 可提前确认去休息':p.state === 'pending' ? 'OP 结束，点击转入休息区' : `下线 ${mins} 分钟`;
    if (p.after === 'done') sub = '今天轮岗结束，点击即可下班';
    return `<button type="button" class="rt-chip${mins >= 10 ? ' due' : ''}" data-pid="${esc(p.pid)}" data-act="arrive"><span class="rt-nm">${esc(p.name)}</span><span class="rt-sub">${sub}</span>${badges(p)}</button>`;
  }, 'rt-arrivals');
  pool += poolSection('休息区', restSummary(resting), resting, p => {
    if(waitingInitialRest(p))return `<div class="rt-chip rest"><span class="rt-nm">${esc(p.name)}</span><span class="rt-sub">班次未开始 · ${hm(p.scheduledRestStart)}开始休息</span><span class="rt-go">班次 ${hm(p.start)}–${hm(p.end)}</span></div>`;
    const kind = p.state === 'meal' ? '<span class="rt-badge meal">吃饭</span>' : '<span class="rt-badge rest">休息</span>';
    const next = p.assign ? `${goText(p)} <small>${hm(p.assign.departAt)}</small>` : '';
    return `<div class="rt-chip rest"${drag(p)}><span class="rt-nm">${esc(p.name)} ${kind}</span><span class="rt-sub">至 ${hm(restUntil(p))}，还剩 ${Math.max(0, Math.ceil(restUntil(p) - n))} 分钟</span><span class="rt-go">${next}</span>${badges(p)}</div>`;
  }, 'rt-resting',true);
  pool += poolSection('吃饭区', '', meals, p => `<div class="rt-chip meal"${drag(p)}><span class="rt-nm">${esc(p.name)}</span><span class="rt-sub">至 ${hm(p.readyAt)} · 剩 ${Math.max(0, Math.ceil(p.readyAt-n))} 分钟</span>${badges(p)}</div>`, 'rt-meals');
  const outside=ps.filter(outsideKind);
  pool+=`<section class="rt-sect rt-outside${outside.length?'':' is-empty'}"><h2>圈外人员<span class="rt-n">${outside.length}</span></h2><div class="rt-chips rt-outside-scroll${outside.length?'':' empty'}">${['病假','出圈','OJT'].map(kind=>`<div data-outside-kind="${kind}"${outside.length?'':' hidden'}><h3>${kind==='出圈'?'出圈（含暂离）':kind}</h3><div class="rt-outside-members">${outside.filter(p=>outsideKind(p)===kind).map(p=>`<div class="rt-chip dim"><span class="rt-nm">${esc(p.name)}</span><span class="rt-sub">${esc(p.leaveReason||p.away?.reason||p.label||kind)}</span></div>`).join('')||'暂无'}</div></div>`).join('')}${outside.length?'':'暂无'}</div></section>`;
  return `<div class="rt-board"><section class="rt-line-area" aria-label="轮岗线路"><div class="rt-area-heading"><h1>轮岗线路</h1><span>${day.lines.filter(L=>L.active).length}条启用 <span class="rt-schedule-key"><i class="add"></i>待加 <i class="remove"></i>待撤</span></span></div>${lines}</section><div class="rt-pool" aria-label="休息室人员">${pool}</div></div>`;
}

RT.render = () => {
  if (!RT.data) return;
  renderClock();
  const scroll = [...$app.querySelectorAll('.rt-lines,.rt-chips')].map(el => el.scrollTop);
  $app.innerHTML = screenView();
  [...$app.querySelectorAll('.rt-lines,.rt-chips')].forEach((el, i) => { el.scrollTop = scroll[i] || 0; });
};

RT.load = async () => {
  await loadLive('/api/rotation/screen/state');
};

$app.addEventListener('click', event => {
  const groupButton=event.target.closest('[data-group-depart]');
  if(groupButton){const p=personOf(groupButton.dataset.groupDepart);if(p)confirmRotation(p,'depart','/api/rotation/screen/act',true);return;}
  if(typeof adjustmentState !== 'undefined' && adjustmentState && event.target.closest('[data-post]')) {
    const [line,i]=event.target.closest('[data-post]').dataset.post.split('#');adjustPostMenu(line,Number(i));return;
  }
  const chip = event.target.closest('[data-pid][data-act]');
  if (!chip) return;
  const p = personOf(chip.dataset.pid);
  if (!p) return;
  if(typeof adjustmentState !== 'undefined' && adjustmentState) {adjustPersonMenu(p);return;}
  if (chip.dataset.act === 'arrive') {
    confirmRotation(p, 'arrive', '/api/rotation/screen/act');
  } else if (chip.dataset.act === 'depart') {
    const a = p.assign;
    if (!a) { toast(`${p.name} 还没有分配去向，请稍等`, true); return; }
    if (chip.getAttribute('aria-disabled') === 'true') { toast(`${p.name} 请在 ${hm(a.departAt)} 出发`, true); return; }
    confirmRotation(p, 'depart', '/api/rotation/screen/act',!!p.entryGroup);
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
