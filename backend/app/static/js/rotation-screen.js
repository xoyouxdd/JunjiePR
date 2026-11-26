'use strict';
// 休息室大屏使用正式轮岗状态和设备确认入口。

const $app = document.getElementById('rtApp');
const screenSummary = document.getElementById('rtScreenSummary');
const screenTotals = document.getElementById('rtScreenTotals');
const screenWide = window.matchMedia('(min-width:1200px)');
const screenPriority = ['exception','checkin','ready','pending'];
const screenSecondary = ['meal','rest','away','other'];

function screenOrder(people, minute) {
  const number = value => Number.isFinite(value) ? value : Infinity;
  return people.sort((a,b) => number(minute(a))-number(minute(b)));
}

function screenSections(day, pools) {
  const sections = new Map(pools.map(([key,title,people]) => [key,{title,people}]));
  const checkin = day.persons.filter(p => p.canBatchCheckIn);
  const checkinIds = new Set(checkin.map(p => p.pid));
  const ready = [];
  for (const key of ['meal','rest']) {
    const section = sections.get(key);
    ready.push(...section.people.filter(p => p.canDepart));
    section.people = screenOrder(section.people.filter(p => !p.canDepart &&
      (!checkinIds.has(p.pid) || key === 'rest' && plannedRestLabel(p))), p => p.readyAt ?? p.start);
  }
  const other = sections.get('other');
  other.people = other.people.filter(p => !checkinIds.has(p.pid));
  sections.set('checkin', {title:'班前签到',people:screenOrder(checkin,p => p.start)});
  sections.set('ready', {title:'待上岗',people:screenOrder(ready,p => p.task?.deadlineAt)});
  sections.set('pending', {title:'待确认休息',people:screenOrder(sections.get('pending').people,p => p.walkbackSince)});
  sections.set('exception', {title:'已超时',people:screenOrder(sections.get('exception').people,p => p.exception?.at)});
  return sections;
}

function renderScreenSummary(sections) {
  screenSummary.hidden = !sections;
  document.body.classList.toggle('rt-screen-has-summary', !!sections);
  if (!sections) return;
  for (const count of screenSummary.querySelectorAll('[data-screen-count]')) {
    const key = count.dataset.screenCount;
    const people = sections.get(key).people.length;
    count.parentElement.classList.toggle('has-items', people > 0);
    if (key === 'checkin') count.parentElement.hidden = people === 0;
    if (count.textContent !== String(people)) count.textContent = people;
  }
}

screenSummary.addEventListener('click', event => {
  const link = event.target.closest('[data-screen-metric]');
  if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  const pool = document.getElementById(`screen-pool-${link.dataset.screenMetric}`);
  if (!pool) return;
  event.preventDefault();
  const top = screenSummary.closest('.rt-top');
  const offset = getComputedStyle(top).position === 'sticky' ? top.offsetHeight : 0;
  pool.querySelector('[data-screen-heading]').focus({preventScroll:true});
  window.scrollTo({top:window.scrollY+pool.getBoundingClientRect().top-offset-12});
});

function screenCards(key, people, n) {
  return `<div class="rt-chips${people.length?'':' empty'}">${people.map(p => forecastPoolCard(p,key,n,{screen:true})).join('')||'暂无'}</div>`;
}

function screenPool(key, {title,people}, n, previewLimit=people.length) {
  if (key === 'checkin' && !people.length) return '';
  const attrs = `id="screen-pool-${key}" class="rt-sect${people.length?'':' is-empty'}" data-f-pool="${key}"`;
  const heading = `${title}<span class="rt-n">${people.length}</span>`;
  if (key !== 'other') {
    const remaining = people.length-previewLimit;
    const more = remaining > 0 ? `<details class="rt-screen-more" data-screen-detail="more:${key}"><summary><span>展开其余 ${remaining} 人</span><span>收起其余人员</span></summary>${screenCards(key,people.slice(previewLimit),n)}</details>` : '';
    return `<section ${attrs}><h2 data-screen-heading="${key}" tabindex="-1">${heading}</h2>${screenCards(key,people.slice(0,previewLimit),n)}${more}</section>`;
  }
  const ended = people.filter(p => p.state === 'done').length;
  const waiting = people.filter(p => p.state === 'notyet').length;
  const remaining = people.length-ended-waiting;
  const description = [[waiting,'待到班'],[ended,'已下班'],[remaining,'其他']].filter(([count]) => count).map(([count,label]) => `${label} ${count}`).join(' · ');
  if (!people.length) return `<section ${attrs}><h2>${heading}</h2>${screenCards(key,people,n)}</section>`;
  return `<details ${attrs} data-screen-detail="other"><summary><h2>${heading}<small>${description}</small></h2></summary>${screenCards(key,people,n)}</details>`;
}

function screenView(day, sections, n) {
  if (!day) return '<section class="panel rt-empty"><h2>今天的轮岗还没有开始</h2><p>主管发布后，这里会自动显示。</p></section>';
  if (day.readOnly) return `<section class="rt-workflow-board"><div class="rt-workflow-heading"><h1>${esc(day.date)} · 轮岗存档</h1></div><p class="field-hint" role="status">旧版记录，仅供查看。</p>${workflowBoardContent(day, n, false, {readOnly:true})}</section>`;
  if (day.status === 'ended') return '<section class="panel rt-empty"><h2>今天的轮岗已结束</h2></section>';
  if (!sections) return workflowBoardView(n, false);
  return `<section class="rt-workflow-board rt-screen-live">
    <header class="rt-workflow-heading rt-screen-heading"></header>
    <p class="field-hint rt-screen-prestart" hidden>首班到班前显示预排，实际占岗和计时从开班开始。</p>
    <div class="rt-screen-batch"></div>
    <div class="rt-board">
      <div class="rt-pool rt-screen-priority"></div>
      <div class="rt-lines"></div>
      <div class="rt-pool rt-screen-secondary"></div>
    </div>
  </section>`;
}

const renderedScreenRegions = new WeakMap();
const screenFocusKeys = ['data-screen-heading','data-f-arrive','data-f-board','data-f-check-in','data-rotation-batch'];

function replaceScreenRegion(region, html) {
  if (renderedScreenRegions.get(region) === html) return;
  renderedScreenRegions.set(region, html);
  const current = document.activeElement;
  const active = region.contains(current) ? current : null;
  const detailKey = active?.closest('summary')?.parentElement.dataset.screenDetail;
  const controlKey = screenFocusKeys.find(key => active?.hasAttribute(key));
  const controlId = controlKey ? active.getAttribute(controlKey) : null;
  const open = new Set([...region.querySelectorAll('[data-screen-detail][open]')].map(el => el.dataset.screenDetail));
  region.innerHTML = html;
  for (const details of region.querySelectorAll('[data-screen-detail]')) {
    if (open.has(details.dataset.screenDetail)) details.open = true;
    if (detailKey === details.dataset.screenDetail) details.querySelector('summary').focus({preventScroll:true});
  }
  if (controlKey) {
    const next = [...region.querySelectorAll(`[${controlKey}]`)].find(el => el.getAttribute(controlKey) === controlId);
    next?.focus({preventScroll:true});
  }
}

function renderScreenCountdowns(n) {
  for (const timer of $app.querySelectorAll('[data-screen-deadline]')) {
    const remaining = Number(timer.dataset.screenDeadline)-n;
    const seconds = Math.max(1,Math.ceil(remaining*60-1e-6));
    const due = remaining <= 1;
    const text = remaining <= 0 ? '确认时间已到，正在同步'
      : `${due?'即将超时 · ':''}剩 ${seconds<60?`${seconds}秒`:`${Math.floor(seconds/60)}分${pad2(seconds%60)}秒`}`;
    if (timer.textContent !== text) timer.textContent = text;
    timer.classList.toggle('rt-screen-due',due);
  }
}

RT.render = () => {
  if (!RT.data||RT.editingPost) return;
  RT.mode101StatusAllowed = true;
  renderClock();
  const day = RT.data.day;
  document.body.classList.toggle('rt-screen-flow', !!day && (day.readOnly || day.status !== 'ended'));
  const pools = day?.ruleVersion === 2 && !day.readOnly && day.status !== 'ended' ? forecastPoolData(day) : null;
  const sections = pools ? screenSections(day, pools) : null;
  const n = nowMin();
  renderScreenSummary(sections);
  renderRotationTotals(screenTotals, day, n, pools);
  replaceScreenRegion($app,screenView(day,sections,n));
  if (!sections) return;
  const prestart = day.persons.length > 0 && day.persons.every(p => n < p.start);
  const heading = $app.querySelector('.rt-screen-heading');
  heading.classList.toggle('rt-screen-prestart-heading',prestart);
  replaceScreenRegion(heading,`<h1>${prestart?'预排看板':'轮岗看板'}</h1>${rotationEndLabel(day)}`);
  $app.querySelector('.rt-screen-prestart').hidden = !prestart;
  const limit = screenWide.matches ? 3 : 4;
  replaceScreenRegion($app.querySelector('.rt-lines'),day.lines.map(line => forecastLineCard(line,n,false)).join(''));
  replaceScreenRegion($app.querySelector('.rt-screen-priority'),screenPriority.map(key => screenPool(key,sections.get(key),n,key==='exception'?undefined:limit)).join(''));
  replaceScreenRegion($app.querySelector('.rt-screen-secondary'),screenSecondary.map(key => screenPool(key,sections.get(key),n)).join(''));
  renderScreenCountdowns(n);
  // Older cached workflow scripts may not include the batch notice yet.
  const batchNotice=typeof rotationBatchNotice==='function'?rotationBatchNotice(day,n):'';
  replaceScreenRegion($app.querySelector('.rt-screen-batch'),batchNotice);
};

const renderScreenResize = () => RT.render();
if (screenWide.addEventListener) screenWide.addEventListener('change',renderScreenResize);
else screenWide.addListener(renderScreenResize);

RT.load = async () => {
  await loadLive(`${rotationScreenApi}/state`);
  if (RT.data?.day?.readOnly && RT.modalOpen) closeModal();
};

(async () => {
  try {
    await RT.load();
  } catch (error) {
    $app.innerHTML = `<section class="panel rt-empty"><h2>无法加载大屏</h2><p>${esc(error.message)}</p></section>`;
    if (error.status === 401 || error.status === 403) return;
  }
  connectLive();
  startLocalTicker();
})();
