import {api as portalApi, json} from './api.js';
import {esc, portalPath} from './format.js';
import {compressWarehousePhoto} from './warehouse-drafts.js';
import {renderWarehouseEntry} from './warehouse-entry.js';

const moduleActions = [
  ['out', '出库', '选取货层位，按层拍照'],
  ['in', '入库', '登记实收、上架和直用'],
  ['query', '查物资', '找位置、查余量和现场照'],
  ['assessment', '物资评估', '看库存预警和领用趋势'],
];
const levels = {empty:'缺货 · 立即核对', low:'低库存', near:'接近提醒线', healthy:'库存健康', unset:'未设置提醒线'};
const numberFormat = new Intl.NumberFormat('zh-CN', {maximumFractionDigits:2});
const fmt = value => value === null || value === undefined ? '—' : numberFormat.format(value);
const key = value => String(value ?? '');
const same = (left, right) => key(left) === key(right);
const normalize = value => key(value).normalize('NFKC').toLowerCase().replace(/\s+/g, '');
const timeLabel = value => key(value).replace('T',' ').slice(0,16);
const actionAttrs = (action, id = '') => `data-wh-action="${esc(action)}" data-wh-id="${esc(id)}"`;
const button = (label, action, id = '', tone = 'plain') => `<button type="button" class="wh-button wh-button--${esc(tone)}" ${actionAttrs(action,id)}>${esc(label)}</button>`;
const empty = (title, detail = '') => `<div class="wh-empty"><strong>${esc(title)}</strong>${detail ? `<p>${esc(detail)}</p>` : ''}</div>`;
const tag = (text, tone = 'neutral') => `<span class="wh-tag wh-tag--${esc(tone)}">${esc(text)}</span>`;

function options(rows, current) {
  return rows.map(([value,label]) => `<option value="${esc(value)}"${same(value,current) ? ' selected' : ''}>${esc(label)}</option>`).join('');
}

function quantity(item, value) {
  return value == null ? '—' : `${fmt(value)}${item?.unit ? ` ${item.unit}` : ''}`;
}

function trustedPhotoPath(photo) {
  const url = typeof photo === 'string' ? photo : photo?.url;
  return typeof url === 'string' && /^\/api\/warehouse\/photos\/[a-zA-Z0-9_-]+$/.test(url) ? portalPath(url) : '';
}

function photoMarkup(photo, alt) {
  const path = trustedPhotoPath(photo);
  return path ? `<a class="wh-photo" href="${esc(path)}" target="_blank" rel="noopener"><img src="${esc(path)}" alt="${esc(alt)}" loading="lazy" decoding="async"></a>` : '';
}

export async function renderWarehouse(root, context = {}) {
  const {api:request = portalApi, isCurrent = () => true, openTaskId, initialView, onChanged = () => {}} = context;
  const api = (path, config) => request(`/api/warehouse${path}`, config);
  const shell = document.createElement('section');
  shell.className = 'warehouse-workbench';
  shell.innerHTML = '<header class="wh-header"></header><div class="wh-notice" role="status" hidden></div><div class="wh-body"></div><dialog class="wh-dialog"><div class="wh-dialog-body"></div></dialog>';
  root.replaceChildren(shell);
  const header = shell.querySelector('.wh-header');
  const body = shell.querySelector('.wh-body');
  const notice = shell.querySelector('.wh-notice');
  const dialog = shell.querySelector('dialog');
  const dialogBody = dialog.querySelector('.wh-dialog-body');
  let state;
  let view = 'home';
  let revision = 0;
  let entryCleanup;
  let dialogRevision = 0;
  const viewData = {};
  const finder = {word:'', category:'', shelf:'', location:'', item:''};
  const active = () => isCurrent() && shell.isConnected;
  const manager = () => Boolean(state?.permissions?.manage_warehouse);
  const hr = () => Boolean(state?.permissions?.manage_assignments);
  const dispatch = () => Boolean(state?.permissions?.dispatch || manager());
  const canRead = () => Boolean(dispatch() || hr());
  const operator = () => state?.operator ?? {};
  const catalog = () => state?.items ?? state?.catalog ?? [];
  const locations = () => state?.positions ?? [];
  const owners = () => state?.managers ?? state?.owners ?? [];
  const itemById = id => catalog().find(item => same(item.id ?? item.item_id,id));
  const locById = id => locations().find(location => same(location.id ?? location.location_id,id));
  const locationId = location => location.id ?? location.location_id;
  const itemId = item => item.id ?? item.item_id;
  const shelfOf = location => key(location.shelf ?? location.rack);
  const locationName = location => location?.label || `${shelfOf(location)} 架第 ${location.layer ?? location.level} 层`;
  const photoOf = location => location?.photo ?? location?.latest_photo;
  const binsOf = item => (item.locations ?? item.bins ?? []).map(row => {
    const id = row.position_id;
    return {row, id, location:locById(id)};
  }).filter(entry => entry.location);
  const stockOf = item => item.total ?? binsOf(item).reduce((total, entry) => total + Number(entry.row.quantity ?? 0), 0);

  function tell(message, type = 'ok') {
    notice.textContent = message;
    notice.dataset.type = type;
    notice.hidden = !message;
  }

  async function refresh() {
    const content = await api('/bootstrap');
    if (active()) state = content;
  }

  function closeDialog() {
    dialogRevision += 1;
    if (dialog.open) dialog.close();
    dialogBody.replaceChildren();
  }

  function openDialog(title, content) {
    dialogRevision += 1;
    dialogBody.innerHTML = `<div class="wh-dialog-heading"><h3>${esc(title)}</h3><button type="button" class="wh-icon-button" aria-label="关闭" ${actionAttrs('close-dialog')}>×</button></div><div class="wh-dialog-error" role="alert" hidden></div>${content}`;
    if (!dialog.open) dialog.showModal();
    return dialogRevision;
  }

  function dialogError(message) {
    const box = dialogBody.querySelector('.wh-dialog-error');
    if (box) { box.textContent = message; box.hidden = !message; }
  }

  function heading(title, description, extras = '') {
    header.hidden = view === 'out' || view === 'in';
    header.innerHTML = `<div class="wh-heading-text">${view === 'home' ? '' : button('‹ 物资管理','home')}<h2>${esc(title)}</h2>${description ? `<p>${esc(description)}</p>` : ''}</div>${extras ? `<div class="wh-header-actions">${extras}</div>` : ''}`;
  }

  function renderHome() {
    header.hidden = true;
    const activeItems = catalog().filter(item => item.active !== false);
    const locationsInUse = new Set(activeItems.flatMap(item => binsOf(item).filter(entry => entry.row.quantity > 0).map(entry => key(entry.id))));
    const shelves = [...new Set(locations().map(shelfOf))].filter(Boolean).sort();
    const list = shelves.map(shelf => {
      const layers = locations().filter(location => shelfOf(location) === shelf).sort((left,right) => left.level - right.level);
      const used = layers.filter(layer => locationsInUse.has(key(locationId(layer)))).length;
      return `<li class="wh-rack" aria-label="${esc(shelf)} 架，${used} 层在用，共 ${layers.length} 层">
        <span class="wh-rack-label">${esc(shelf)} 架</span>
        <span class="wh-rack-frame" aria-hidden="true">${layers.map(layer => `<i class="wh-rack-level${locationsInUse.has(key(locationId(layer))) ? ' is-stocked' : ''}"></i>`).join('')}</span>
      </li>`;
    }).join('');
    body.innerHTML = `<section class="wh-overview">
      <div class="wh-overview-main"><h2>物资管理</h2>
        <dl class="wh-overview-stats"><div><dt>物资种类</dt><dd>${fmt(activeItems.length)}</dd></div>
          <div><dt>在用层位</dt><dd>${fmt(locationsInUse.size)} <span>/ ${fmt(locations().length)}</span></dd></div></dl>
      </div>
      ${list ? `<ul class="wh-rack-list" aria-label="各货架层位使用概况">${list}</ul>` : ''}
    </section>
    <div class="wh-home-layout"><div class="wh-module-grid" aria-label="物资管理功能">${moduleActions.map(([action,title,details]) => {
      const authorized = action === 'in' ? manager() : action === 'out' ? dispatch() : canRead();
      return `<button type="button" class="wh-module wh-module--${action}" ${actionAttrs(action)}${authorized ? '' : ' disabled'}>
        <span class="wh-module-symbol" aria-hidden="true"><i></i></span>
        <span class="wh-module-name">${title}</span>
        <span class="wh-module-description">${authorized ? details : action === 'in' ? '由仓库负责人办理' : '需要仓储操作权限'}</span>
        <span class="wh-module-arrow" aria-hidden="true">↗</span></button>`;
    }).join('')}</div>
      <aside class="wh-owners wh-panel"><div class="wh-section-heading"><h3>仓库负责人</h3>${hr() ? `<button type="button" class="wh-icon-button wh-icon-button--accent" aria-label="添加仓库负责人" ${actionAttrs('owner-dialog')}>＋</button>` : ''}</div>
        ${owners().length ? `<ul class="wh-owner-list">${owners().map(owner => `<li><span class="wh-owner-avatar" aria-hidden="true">${esc(Array.from(owner.name || '人')[0])}</span><div><strong>${esc(owner.name)}</strong>${owner.role ? `<small>${esc(owner.role)}</small>` : ''}</div>${hr() ? button('撤下','owner-remove',owner.id) : ''}</li>`).join('')}</ul>` : empty('尚未安排仓库负责人', hr() ? '点击加号添加。' : '')}
        ${manager() ? `<div class="wh-owner-tools">${button('负责人待办','tasks')}${button('物资档案与库存','catalog')}</div>` : ''}
      </aside></div>`;
  }

  function matches(item, term) {
    if (!term) return true;
    const words = [item.name, item.specification ?? item.spec, item.code, ...(Array.isArray(item.aliases) ? item.aliases : key(item.aliases).split(/[、,，]/))];
    return words.some(word => normalize(word).includes(term));
  }

  function renderQuery() {
    heading('查物资','按名称搜索，或直接查看货架各层。');
    const categories = Array.from(new Set(catalog().map(item => item.category).filter(Boolean))).sort();
    const shelves = Array.from(new Set(locations().map(shelfOf))).filter(Boolean).sort();
    body.innerHTML = `<div class="wh-panel wh-query-filters"><div class="wh-filter-row"><label>物资名称<input type="search" autocomplete="off" data-wh-field="query-word" placeholder="输入名称或简称" value="${esc(finder.word)}"></label><label>类目<select data-wh-field="query-category">${options([['','全部类目'], ...categories.map(category => [category,category])],finder.category)}</select></label></div><div class="wh-shelf-row" aria-label="按货架查看"><span>货架</span>${[['','全部'], ...shelves.map(shelf => [shelf,`${shelf} 架`])].map(([shelf,label]) => `<button type="button" class="wh-filter-chip" aria-pressed="${same(finder.shelf,shelf)}" ${actionAttrs('query-shelf',shelf)}>${esc(label)}</button>`).join('')}</div><div class="wh-layer-row" data-wh-query-layers></div></div><div data-wh-query-results></div>`;
    renderQueryResults();
  }

  function renderQueryResults() {
    const region = body.querySelector('[data-wh-query-results]');
    const layers = body.querySelector('[data-wh-query-layers]');
    if (!region || !layers) return;
    for (const chip of body.querySelectorAll('[data-wh-action="query-shelf"]')) chip.setAttribute('aria-pressed',String(same(chip.dataset.whId,finder.shelf)));
    const activeLocations = locations().filter(location => !finder.shelf || same(finder.shelf,shelfOf(location)));
    layers.innerHTML = finder.shelf ? [['','全部层位'], ...activeLocations.map(location => [locationId(location),`第 ${location.level} 层`])].map(([id,label]) => `<button type="button" class="wh-filter-chip" aria-pressed="${same(finder.location,id)}" ${actionAttrs('query-location',id)}>${esc(label)}</button>`).join('') : '';
    const term = normalize(finder.word);
    const results = catalog().filter(item => item.active !== false && matches(item,term) && (!finder.category || same(item.category,finder.category)) && ((!finder.shelf && !finder.location) || binsOf(item).some(entry => activeLocations.includes(entry.location) && (!finder.location || same(finder.location,entry.id))))).sort((left,right) => (normalize(right.name) === term) - (normalize(left.name) === term) || left.name.localeCompare(right.name,'zh-CN'));
    const layer = locById(finder.location);
    region.innerHTML = `${layer ? locationPhoto(layer) : ''}<div class="wh-result-heading"><h3>物资</h3><span aria-live="polite">${results.length} 项</span></div>${results.length ? `<div class="wh-results">${results.map(item => queryItem(item,activeLocations)).join('')}</div>` : empty(term ? '未找到匹配的物资' : '当前范围内没有物资',term ? '换个关键词或清除类目后再试。' : '')}`;
  }

  function locationPhoto(location) {
    const photo = photoOf(location);
    const image = photoMarkup(photo,`${locationName(location)}现场照片`);
    if (!image) return '';
    const photoTime = photo?.updated_at ?? photo?.captured_at;
    const stale = photo?.stale;
    return `<div class="wh-location-photo"><div><strong>${esc(locationName(location))}现场照片</strong>${stale ? tag('拍照后库存有变动','near') : ''}<small>${[photo?.updated_by,timeLabel(photoTime)].filter(Boolean).map(esc).join(' · ')}</small>${photo?.transaction_id ? `<small>关联单据 ${esc(photo.transaction_id)}</small>` : ''}</div>${image}</div>`;
  }

  function queryItem(item, activeLocations) {
    const id = itemId(item);
    const bins = binsOf(item).filter(entry => activeLocations.includes(entry.location) && (!finder.location || same(finder.location,entry.id)));
    const isOpen = same(finder.item,id) || !!finder.location;
    return `<article class="wh-goods"><button type="button" class="wh-goods-summary" aria-expanded="${isOpen}" ${actionAttrs('query-item',id)}><span><strong>${esc(item.name)}</strong><small>${[item.specification ?? item.spec,item.category,item.unit].filter(Boolean).map(esc).join(' · ')}</small></span><span class="wh-goods-quantity"><small>总余量</small><strong>${esc(quantity(item,stockOf(item)))}</strong></span><span class="wh-goods-arrow" aria-hidden="true">${isOpen ? '⌃' : '⌄'}</span></button>${!isOpen ? '' : `<div class="wh-goods-detail">${bins.length ? bins.map(({row,id:binId,location}) => {
      const amount = row.quantity ?? row.stock ?? 0;
      const outbound = Number(amount) > 0 ? dispatch() ? `<button type="button" class="wh-button wh-button--primary" ${actionAttrs('out-bin',id)} data-wh-location="${esc(binId)}">去出库</button>` : '' : tag('缺货','empty');
      return `<div class="wh-bin"><div class="wh-bin-summary"><div class="wh-bin-location"><strong>${esc(locationName(location))}</strong>${row.reported ? tag('待核对','near') : ''}${location.updated_at ? `<small>库存更新 ${esc(timeLabel(location.updated_at))}</small>` : ''}</div>
        <div class="wh-bin-stock"><small>可领</small><strong>${esc(quantity(item,amount))}</strong></div>
        <div class="wh-bin-actions">${outbound}${dispatch() ? `<button type="button" class="wh-button wh-button--plain" ${actionAttrs('report',id)} data-wh-location="${esc(binId)}">报错</button>` : ''}</div></div>${finder.location ? '' : locationPhoto(location)}</div>`;
    }).join('') : empty('暂无可查看的货位') }</div>`}</article>`;
  }

  function ownerDialog() {
    if (hr()) openDialog('添加仓库负责人','<form class="wh-form" data-wh-form="owner-search"><label>姓名或员工号<input type="search" name="query" autocomplete="off" required placeholder="输入姓名或员工号"></label><button class="wh-button wh-button--primary">搜索</button></form><div class="wh-person-results" data-wh-owner-results aria-live="polite"></div>');
  }

  function reportDialog(itemIdValue, locationIdValue) {
    const item = itemById(itemIdValue);
    const location = locById(locationIdValue);
    if (!dispatch() || !item || !location) return;
    openDialog('报告货位差异',`<form class="wh-form" data-wh-form="report" data-wh-item="${esc(itemIdValue)}" data-wh-location="${esc(locationIdValue)}"><p>${esc(item.name)} · ${esc(locationName(location))}</p><label>问题<select name="reason"><option value="misplaced">位置不符</option><option value="missing">找不到货</option><option value="mismatch">数量不符</option><option value="map">图上位置有误</option></select></label><label>实见数量（如已清点）<input type="number" min="0" inputmode="numeric" step="1" name="observed_quantity"></label><label>说明<textarea name="note" rows="2" maxlength="240"></textarea></label><button class="wh-button wh-button--primary">提交报错</button></form>`);
  }

  async function renderAssessment(stillHere) {
    heading('物资评估','先看库存状态，再看每种物资的领用与补货建议。');
    const report = await api('/assessment');
    if (!stillHere()) return;
    const rows = Array.isArray(report.items) ? report.items : [];
    const risk = {empty:0,low:1,near:2,healthy:3,unset:4};
    rows.sort((left,right) => (risk[left.level] ?? 4) - (risk[right.level] ?? 4) || left.name.localeCompare(right.name,'zh-CN'));
    const riskRows = rows.filter(row => ['empty','low','near'].includes(row.level));
    const suggestions = rows.filter(row => !riskRows.includes(row) && row.suggested_point != null && row.stock <= row.suggested_point);
    const rowMarkup = row => `<article class="wh-assessment-row wh-assessment-row--${esc(row.level)}"><div class="wh-assessment-head"><div><strong>${esc(row.name)}</strong><small>可领 ${esc(quantity(row,row.stock))}</small></div>${tag(levels[row.level] || levels.unset,row.level || 'unset')}</div><div class="wh-assessment-numbers"><div><small>建议补货点</small><strong>${row.suggested_point == null ? '待评估' : esc(quantity(row,row.suggested_point))}</strong></div><div><small>强提醒触发点</small><strong>${row.minimum == null ? '缺货时' : `低于 ${esc(quantity(row,row.minimum))}`}</strong></div></div>${row.suggested_point != null && row.stock <= row.suggested_point ? `<p class="wh-annotation">已到建议补货点${row.suggested_quantity != null ? ` · 建议补 ${esc(quantity(row,row.suggested_quantity))}` : ''}</p>` : ''}${manager() ? `<div class="wh-row-end">${button('设置预警与补货','rule',row.item_id)}</div>` : ''}</article>`;
    const counts = rows.reduce((totals,row) => {
      totals[row.level] = (totals[row.level] || 0) + 1;
      return totals;
    },{});
    const statusSummary = [['empty','缺货'],['low','低库存'],['near','接近提醒线'],['healthy','库存健康']].map(([state,label]) =>
      `<div class="wh-stock-stat wh-stock-stat--${state}"><dt>${label}</dt><dd>${fmt(counts[state] || 0)}</dd></div>`).join('');
    body.innerHTML = `${rows.length ? `<dl class="wh-stock-overview" aria-label="库存状态汇总">${statusSummary}</dl>` : ''}
      <section class="wh-alert-section"><div class="wh-section-heading"><h3>库存预警</h3><span>${riskRows.length} 项需关注${counts.unset ? ` · ${counts.unset} 项待设置提醒线` : ''}</span></div>${riskRows.length ? `<div class="wh-assessment-grid">${riskRows.map(rowMarkup).join('')}</div>` : empty('暂无低库存或缺货')}</section>
      ${suggestions.length ? `<section class="wh-alert-section"><div class="wh-section-heading"><h3>补货参考</h3></div><div class="wh-assessment-grid">${suggestions.map(rowMarkup).join('')}</div></section>` : ''}
      <section class="wh-evaluation-section"><div class="wh-section-heading"><h3>领用与补货</h3><span>${rows.length} 种物资</span></div>${rows.length ? `<div class="wh-results">${rows.map(row => usageDetails(row,manager())).join('')}</div>` : empty('完成建档和期初盘点后查看')}</section>`;
    viewData.assessment = rows;
  }

  function usageDetails(row, canManage) {
    const months = row.months ?? [];
    const peak = Math.max(1,...months.map(month => Number(month.usage ?? 0)));
    const usage = (title,value) => `<div><small>${title}</small><strong>${esc(quantity(row,value))}</strong></div>`;
    const point = row.suggested_point == null ? '待评估' : quantity(row,row.suggested_point);
    return `<details class="wh-goods wh-usage"><summary><span><strong>${esc(row.name)}</strong><small>已记录 ${fmt(row.days_tracked)} 天 · 可领 ${esc(quantity(row,row.stock))}</small></span>${tag(levels[row.level] || levels.unset,row.level || 'unset')}</summary><div class="wh-usage-content"><div class="wh-usage-numbers">${usage('近 7 天领用',row.usage_7)}${usage('近 30 天领用',row.usage_30)}${usage('近 7 天直用',row.direct_7)}${usage('近 30 天直用',row.direct_30)}</div><div class="wh-usage-notes"><span>近 30 天缺货 ${fmt(row.days_out_of_stock)} 天</span><span>日均领用 ${esc(quantity(row,row.daily_average))}</span><span>预计可用 ${fmt(row.predicted_days)} 天</span><span>建议补货点 ${esc(point)}</span><span>强提醒 ${row.minimum == null ? '余量为 0 时' : `低于 ${esc(quantity(row,row.minimum))}`}</span><span>建议补货量 ${esc(quantity(row,row.suggested_quantity))}</span></div>${row.estimate_reason ? `<p class="wh-annotation">${esc(row.estimate_reason)}</p>` : ''}${months.length ? `<div class="wh-monthly" aria-label="月度领用">${months.map(month => `<div class="wh-month-row"><span>${esc(month.month)}</span><span class="wh-month-bar" aria-hidden="true" style="--wh-fill:${Math.max(0,Math.min(100,(Number(month.usage ?? 0) / peak) * 100))}%"></span><strong>${esc(quantity(row,month.usage))}</strong><small>直用 ${esc(quantity(row,month.direct))}</small></div>`).join('')}</div>` : ''}${canManage ? `<div class="wh-row-end">${button('设置预警与补货','rule',row.item_id)}</div>` : ''}</div></details>`;
  }

  function ruleDialog(id) {
    const row = viewData.assessment?.find(entry => same(entry.item_id,id));
    if (!row || !manager()) return;
    const field = (label,name,value) => `<label>${label}<input type="number" name="${name}" min="0" step="1" inputmode="numeric" value="${esc(value ?? '')}"></label>`;
    openDialog(`设置 · ${row.name}`,`<form class="wh-form" data-wh-form="rule" data-wh-item="${esc(id)}"><div class="wh-form-grid">${field('最低库存线','minimum',row.minimum)}${field('黄色提醒上限','yellow_upper',row.yellow_upper)}${field('补货到货天数','lead_days',row.lead_days)}${field('预留天数','safety_days',row.safety_days)}${field('目标可用天数','target_days',row.target_days)}</div><p>库存低于最低库存线时发送强提醒，等于最低库存线时为黄色。</p><button class="wh-button wh-button--primary">保存设置</button></form>`);
  }

  async function renderTasks(stillHere) {
    heading('负责人待办','');
    const [issues,alerts] = await Promise.all([api('/issues'),api('/alerts')]);
    if (!stillHere()) return;
    viewData.issues = issues.items ?? [];
    viewData.alerts = alerts.items ?? [];
    const outstanding = viewData.issues.filter(issue => issue.status !== 'closed');
    const completed = viewData.issues.filter(issue => issue.status === 'closed');
    body.innerHTML = `<section class="wh-task-section"><div class="wh-section-heading"><h3>货位异常</h3><span>${outstanding.length} 项待处理</span></div>${outstanding.length ? `<div class="wh-results">${outstanding.map(issueCard).join('')}</div>` : empty('暂无货位异常待办')}</section><section class="wh-task-section"><div class="wh-section-heading"><h3>库存预警</h3><span>${viewData.alerts.length} 项</span></div>${viewData.alerts.length ? `<div class="wh-results">${viewData.alerts.map(alertCard).join('')}</div>` : empty('暂无库存强提醒')}</section>${completed.length ? `<section class="wh-task-section"><div class="wh-section-heading"><h3>最近处理</h3></div><div class="wh-results">${completed.slice(0,10).map(issueCard).join('')}</div></section>` : ''}`;
    if (openTaskId) body.querySelector('[aria-current="true"]')?.scrollIntoView({block:'nearest'});
  }

  function issueCard(issue) {
    const {id,status,assignee_name,reporter_name,opened_at,transaction_status,transaction_id} = issue;
    const mine = same(issue.assignee_id,operator().id);
    const progress = status === 'closed' ? '已处理' : status === 'claimed' ? `${assignee_name || '负责人'}处理中` : '待接手';
    const controls = status === 'open' ? button('接手处理','issue-claim',id,'primary') : status === 'claimed' && mine ? `${button('复点并处理','issue-resolve',id,'primary')}${button('转交','issue-transfer',id)}` : '';
    return `<article class="wh-task-card" ${same(openTaskId,id) ? 'aria-current="true"' : ''}><div class="wh-task-title"><div><strong>${esc(issue.title || '货位异常')}</strong><small>${[reporter_name,timeLabel(opened_at),transaction_id ? `单据 ${transaction_id}` : ''].filter(Boolean).map(esc).join(' · ')}</small></div>${tag(progress,status === 'open' ? 'low' : status === 'closed' ? 'healthy' : 'near')}</div>${transaction_status === 'pending' ? '<p class="wh-annotation">实物已取走，原出库待补记。</p>' : ''}<div class="wh-task-lines">${(issue.lines ?? []).map(line => { const item = itemById(line.item_id); const place = locById(line.position_id); const seen = line.observed_quantity == null ? '' : ` · 实见 ${quantity(item,line.observed_quantity)}`; return `<span>${esc(item?.name || '物资')} · ${esc(place ? locationName(place) : '货位')} · 账面 ${esc(quantity(item,line.book_quantity))}${esc(seen)}</span>`; }).join('')}</div>${issue.note ? `<p>${esc(issue.note)}</p>` : ''}${issue.resolution ? `<p>${esc(issue.resolution)}</p>` : ''}${issue.photo_id ? photoMarkup(`/api/warehouse/photos/${issue.photo_id}`,'报告现场照片') : ''}${controls ? `<div class="wh-row-end">${controls}</div>` : ''}</article>`;
  }

  function alertCard(alert) {
    const level = alert.level || alert.severity || 'low';
    const mine = same(alert.assignee_id,operator().id);
    const assigned = Boolean(alert.assignee_id);
    const waiting = alert.status === 'waiting_restock';
    const id = alert.item_id;
    const item = itemById(id);
    const controls = [!assigned ? button('接手处理','alert-claim',id,'primary') : '',mine && !waiting ? button('已核实，待补货','alert-wait',id,'primary') : '',mine ? button('转交','alert-transfer',id) : ''].filter(Boolean).join('');
    return `<article class="wh-task-card wh-task-card--${esc(level)}"><div class="wh-task-title"><div><strong>${esc(alert.name ?? item?.name ?? '物资')}</strong><small>可领 ${esc(quantity(item,alert.stock ?? item?.total))}${alert.assignee_name ? ` · ${esc(alert.assignee_name)}处理中` : ''}</small></div>${tag(waiting ? '已核实，待补货' : levels[level] || '低库存',level)}</div>${controls ? `<div class="wh-row-end">${controls}</div>` : ''}</article>`;
  }

  function transferDialog(kind,id) {
    const candidates = owners().filter(owner => !same(owner.id,operator().id));
    openDialog('转交负责人', candidates.length ? `<form class="wh-form" data-wh-form="${kind}-transfer" data-wh-id="${esc(id)}"><label>转交给<select name="employee_id" required>${options(candidates.map(owner => [owner.id,owner.name]))}</select></label><button class="wh-button wh-button--primary">确认转交</button></form>` : empty('暂无其他仓库负责人'));
  }

  async function resolveDialog(id) {
    const issue = viewData.issues?.find(entry => same(entry.id,id));
    if (!issue || !same(issue.assignee_id,operator().id)) return;
    await refresh();
    if (!active() || view !== 'tasks') return;
    const pending = issue.transaction_status === 'pending';
    const lines = issue.lines ?? [];
    const itemIds = Array.from(new Set(lines.map(line => line.item_id)));
    openDialog('复点并处理',`<form class="wh-form" data-wh-form="issue-resolve" data-wh-id="${esc(id)}">${pending ? '<p>核对已取走数量和期间收发，填写目前现场的实际剩余量，系统一起处理纠错与原单补记。</p>' : '<p>需要改账时填入各货位实盘余量。图上位置修正或经核实账实一致时，可直接记录处理结果。</p>'}<div class="wh-form" data-wh-observations>${lines.map(line => observationField(line,pending)).join('')}</div>${itemIds.length ? `<details class="wh-extra-observation"><summary>补充复点货位</summary><div class="wh-form-grid"><label>物资<select data-wh-extra-item>${options(itemIds.map(item => [item,itemById(item)?.name || '物资']))}</select></label><label>层位<select data-wh-extra-position>${options(locations().map(location => [locationId(location),locationName(location)]))}</select></label></div>${button('添加该货位','extra-observation')}</details>` : ''}<label>处理结果<textarea name="reason" required maxlength="240" rows="3"></textarea></label><button class="wh-button wh-button--primary">确认处理</button></form>`);
  }

  function observationField(line, required = false) {
    const item = itemById(line.item_id);
    const position = locById(line.position_id);
    const current = binsOf(item || {}).find(entry => same(entry.id,line.position_id))?.row.quantity ?? 0;
    return `<label class="wh-observation"><span>${esc(item?.name || '物资')} · ${esc(position ? locationName(position) : '货位')}<small>当前账面 ${esc(quantity(item,current))}${line.observed_quantity == null ? '' : ` · 报告实见 ${esc(quantity(item,line.observed_quantity))}`}</small></span><input type="number" min="0" step="1" inputmode="numeric" data-wh-observation data-wh-item="${esc(line.item_id)}" data-wh-position="${esc(line.position_id)}" data-wh-version="${esc(position?.version)}" aria-label="${esc(`${item?.name || '物资'} ${position ? locationName(position) : ''}实盘剩余量`)}"${required ? ' required' : ''}></label>`;
  }

  function addObservation(form) {
    const item = Number(form.querySelector('[data-wh-extra-item]').value);
    const position = Number(form.querySelector('[data-wh-extra-position]').value);
    const section = form.querySelector('[data-wh-observations]');
    const existing = Array.from(section.querySelectorAll('[data-wh-observation]')).find(input => same(input.dataset.whItem,item) && same(input.dataset.whPosition,position));
    if (existing) { existing.focus(); return; }
    section.insertAdjacentHTML('beforeend',observationField({item_id:item,position_id:position}));
    section.lastElementChild.querySelector('input')?.focus();
  }

  function renderCatalog() {
    heading('物资档案与库存','',`${button('查收发记录','history')}${button('更新货位照片','position-photo')}${button('添加物资','new-item','','primary')}`);
    body.innerHTML = catalog().length ? `<div class="wh-results">${catalog().map(item => {
      const id = itemId(item);
      const bins = binsOf(item);
      return `<article class="wh-item-card"><div class="wh-task-title"><div><strong>${esc(item.name)}</strong><small>${[item.spec,item.category,item.unit].filter(Boolean).map(esc).join(' · ')}</small></div>${item.active === false ? tag('已停用') : ''}</div>${bins.length ? `<div class="wh-bin-pills">${bins.map(entry => `<span>${esc(locationName(entry.location))} · ${esc(quantity(item,entry.row.quantity))}</span>`).join('')}</div>` : '<p>尚未录入货位库存。</p>'}<div class="wh-row-end">${button('编辑资料','edit-item',id)}${button('期初盘点或调整','adjust',id)}</div></article>`;
    }).join('')}</div>` : empty('尚无物资档案','先添加物资，再按实际货位记录期初库存。');
  }

  function itemDialog(id) {
    const item = id ? itemById(id) : null;
    if (!manager() || id && !item) return;
    openDialog(item ? `编辑 · ${item.name}` : '添加物资',`<form class="wh-form" data-wh-form="catalog" data-wh-item="${esc(id)}"><div class="wh-form-grid"><label>名称<input name="name" value="${esc(item?.name || '')}" required maxlength="100"></label><label>规格<input name="spec" value="${esc(item?.spec || '')}" maxlength="100"></label><label>类目<input name="category" value="${esc(item?.category || '')}" maxlength="50"></label><label>单位<input name="unit" value="${esc(item?.unit || '')}" required maxlength="20" placeholder="箱、个、件"></label></div><label>简称（用顿号分隔）<input name="aliases" value="${esc(item?.aliases?.join('、') ?? '')}" maxlength="240"></label>${item ? `<label class="wh-check"><input type="checkbox" name="active"${item.active !== false ? ' checked' : ''}>在用</label>` : ''}<button class="wh-button wh-button--primary">保存物资</button></form>`);
  }

  async function adjustDialog(id) {
    if (!manager()) return;
    await refresh();
    if (!active() || view !== 'catalog') return;
    const item = itemById(id);
    if (!item) return;
    const positionOptions = locations().map(location => [locationId(location),locationName(location)]);
    openDialog(`库存盘点 · ${item.name}`,`<form class="wh-form" data-wh-form="adjust" data-wh-item="${esc(id)}"><label>层位<select name="position_id" data-wh-field="adjust-position" required>${options([['','请选择层位'], ...positionOptions])}</select></label><div class="wh-annotation" data-wh-adjust-current hidden></div><label>类型<select name="kind"><option value="opening">期初盘点</option><option value="adjustment">库存调整</option></select></label><label>实盘余量（${esc(item.unit)}）<input type="number" name="quantity" min="0" step="1" inputmode="numeric" required></label><label>原因<textarea name="reason" rows="2" maxlength="240" required></textarea></label><button class="wh-button wh-button--primary">确认入账</button></form>`);
  }

  function positionPhotoDialog() {
    if (!manager()) return;
    openDialog('更新货位现况照',`<form class="wh-form" data-wh-form="position-photo"><label>层位<select name="position_id" required>${options([['','请选择层位'], ...locations().map(location => [locationId(location),locationName(location)])])}</select></label><label>现场照片<input type="file" accept="image/*" capture="environment" name="file" required></label><button class="wh-button wh-button--primary">更新照片</button></form>`);
  }

  async function renderHistory(stillHere) {
    heading('收发与调整记录','',button('物资档案与库存','catalog'));
    const report = await api('/transactions');
    if (!stillHere()) return;
    const labels = {inbound:'入库',outbound:'出库',opening:'期初盘点',adjustment:'库存调整',correction:'单据纠错'};
    const statuses = {posted:'已入账',pending:'待核对',draft:'待确认',closed:'已关闭'};
    const rows = report.items ?? [];
    body.innerHTML = rows.length ? `<div class="wh-results">${rows.map(row => `<article class="wh-item-card"><div class="wh-task-title"><div><strong>${labels[row.kind] || '仓储单据'} · ${esc(row.id)}</strong><small>${[row.actor_name,timeLabel(row.posted_at ?? row.created_at)].filter(Boolean).map(esc).join(' · ')}</small></div>${tag(statuses[row.status] || row.status,row.status === 'posted' ? 'healthy' : 'near')}</div><div class="wh-row-end">${button('查看单据','history-record',row.id)}</div></article>`).join('')}</div>` : empty('暂无出入库和调整记录');
  }

  async function historyDialog(id) {
    const version = openDialog('仓储单据',empty('正在载入单据'));
    const row = await api(`/transactions/${encodeURIComponent(id)}`);
    if (!active() || version !== dialogRevision || !dialog.open) return;
    const lines = row.lines ?? [];
    const receipts = row.receipts ?? [];
    const photos = row.photos ?? [];
    const movements = row.movements ?? [];
    viewData.historyRecord = row;
    const grouped = [...lines, ...receipts].map(line => {
      const item = itemById(line.item_id);
      const place = locById(line.position_id);
      const details = [];
      if (place) details.push(locationName(place));
      if (line.quantity !== undefined) details.push(quantity(item,line.quantity));
      const movement = movements.find(entry => same(entry.item_id,line.item_id) && same(entry.position_id,line.position_id));
      if (movement?.position_id != null && lines.includes(line)) details.push(`余量 ${fmt(movement.quantity_before)} → ${fmt(movement.quantity_after)}`);
      if (line.received != null && receipts.includes(line)) details.push(`实收 ${quantity(item,line.received)}`);
      if (line.damaged) details.push(`破损 ${quantity(item,line.damaged)}`);
      if (line.direct_quantity) details.push(`直用 ${quantity(item,line.direct_quantity)}`);
      if (line.direct_destination) details.push(line.direct_destination);
      const canCorrect = row.status === 'posted' && ['inbound','outbound'].includes(row.kind) && (lines.includes(line) || line.direct_quantity);
      return `<div class="wh-history-line"><div><strong>${esc(item?.name ?? line.item_name ?? '物资')}</strong><small>${details.map(esc).join(' · ')}</small></div>${canCorrect ? `<button type="button" class="wh-button wh-button--plain" ${actionAttrs('correct-record',line.item_id)} data-wh-location="${esc(line.position_id ?? '')}">${lines.includes(line) ? '纠正数量' : '纠正直用量'}</button>` : ''}</div>`;
    });
    openDialog(`单据 · ${row.id}`,`<p>${[row.actor_name,timeLabel(row.posted_at ?? row.created_at)].filter(Boolean).map(esc).join(' · ')}</p>${row.reason ? `<p>${esc(row.reason)}</p>` : ''}${row.related_id ? `<p>关联原单 ${esc(row.related_id)}</p>` : ''}<div class="wh-transaction-lines">${grouped.join('')}</div>${photos.length ? `<div class="wh-transaction-photos">${photos.map(photo => photoMarkup(photo,'本单照片')).join('')}</div>` : ''}`);
  }

  async function correctDialog(itemValue, placeValue) {
    const row = viewData.historyRecord;
    const item = itemById(itemValue);
    if (!manager() || !row || !item) return;
    await refresh();
    if (!active() || view !== 'history' || !dialog.open || viewData.historyRecord !== row) return;
    const place = locById(placeValue);
    openDialog('纠正原单数量',`<form class="wh-form" data-wh-form="correct" data-wh-id="${esc(row.id)}" data-wh-item="${esc(itemValue)}" data-wh-location="${esc(placeValue)}" data-wh-version="${esc(place?.version)}"><p>原单 ${esc(row.id)} · ${esc(item.name)} · ${esc(place ? locationName(place) : '到货直用')}</p><label>纠正后的单据总量（${esc(item.unit)}）<input type="number" name="quantity" min="0" step="1" inputmode="numeric" required></label><label>纠正原因<textarea name="reason" rows="3" required maxlength="240"></textarea></label><button class="wh-button wh-button--primary">确认纠错</button></form>`);
  }

  async function show(next, prefill) {
    if (!active()) return;
    if (['tasks','catalog','history','in'].includes(next) && !manager()) next = 'home';
    if (next === 'out' && !dispatch() || ['query','assessment'].includes(next) && !canRead()) next = 'home';
    if (entryCleanup) { entryCleanup(); entryCleanup = undefined; }
    closeDialog();
    view = next;
    const renderId = ++revision;
    const stillHere = () => active() && renderId === revision;
    tell('');
    if (next === 'home') { renderHome(); return; }
    if (next === 'query') { renderQuery(); return; }
    heading({assessment:'物资评估',tasks:'负责人待办',catalog:'物资档案与库存',history:'收发与调整记录',in:'入库',out:'出库'}[next] || '物资管理','');
    body.innerHTML = empty('正在载入');
    if (next === 'assessment') return renderAssessment(stillHere);
    if (next === 'tasks') return renderTasks(stillHere);
    if (next === 'catalog') { renderCatalog(); return; }
    if (next === 'history') return renderHistory(stillHere);
    if (next === 'out' || next === 'in') {
      header.querySelector('h2').classList.add('wh-visually-hidden');
      const host = document.createElement('div');
      body.replaceChildren(host);
      const clean = await renderWarehouseEntry(host,{mode:next,state,api,prefill,onBack:() => show('home').catch(handleError),onComplete:async result => {
        if (!stillHere()) return;
        await refresh();
        if (!stillHere()) return;
        await show('home');
        tell(result?.status === 'pending' ? `已提交差异待办，原出库单 ${result.id} 待核对。` : result?.id ? `已确认单据 ${result.id}` : '单据已提交。');
        changed();
      }});
      if (typeof clean === 'function') {
        if (stillHere()) entryCleanup = clean;
        else clean();
      }
    }
  }

  function changed() {
    Promise.resolve().then(onChanged).catch(() => {});
  }

  function handleError(error) {
    if (error?.name === 'AbortError' || !active()) return;
    if (dialog.open) dialogError(error?.message || '操作暂未完成，请重试。');
    else tell(error?.message || '操作暂未完成，请重试。','error');
  }

  async function commit(path, method, payload, message) {
    await api(path,payload === undefined ? {method} : json(method,payload));
    if (!active()) return;
    const current = view;
    await refresh();
    if (!active() || view !== current) return;
    await show(current);
    tell(message);
    changed();
  }

  async function click(control) {
    const {whAction:action,whId:id,whLocation:locationIdValue} = control.dataset;
    if (['home','out','in','query','assessment','tasks','catalog','history'].includes(action)) return show(action);
    if (action === 'out-bin') return show('out',{itemId:Number(id),locationId:Number(locationIdValue)});
    if (action === 'query-shelf') { finder.shelf = id; finder.location = ''; finder.item = ''; renderQueryResults(); return; }
    if (action === 'query-location') { finder.location = id; finder.item = ''; renderQueryResults(); return; }
    if (action === 'query-item') { finder.item = same(finder.item,id) ? '' : id; renderQueryResults(); body.querySelector(`[data-wh-action="query-item"][data-wh-id="${CSS.escape(id)}"]`)?.focus(); return; }
    if (action === 'close-dialog') { closeDialog(); return; }
    if (action === 'owner-dialog') { ownerDialog(); return; }
    if (action === 'owner-add' && hr()) return commit('/managers','POST',{employee_id:Number(id)},'已添加仓库负责人。');
    if (action === 'owner-remove' && hr()) {
      const owner = owners().find(person => same(person.id,id));
      if (owner) openDialog('撤下负责人',`<form class="wh-form" data-wh-form="owner-remove" data-wh-id="${esc(id)}"><p>确认撤下 ${esc(owner.name)} 的仓库负责人权限。</p><button class="wh-button wh-button--primary">确认撤下</button></form>`);
      return;
    }
    if (action === 'report') { reportDialog(id,locationIdValue); return; }
    if (action === 'rule') { ruleDialog(id); return; }
    if (action === 'new-item' || action === 'edit-item') { itemDialog(id); return; }
    if (action === 'adjust') return adjustDialog(id);
    if (action === 'position-photo') { positionPhotoDialog(); return; }
    if (action === 'history-record') return historyDialog(id);
    if (action === 'correct-record') return correctDialog(id,locationIdValue);
    if (action === 'issue-claim') return commit(`/issues/${encodeURIComponent(id)}/claim`,'POST',{},'已接手待办。');
    if (action === 'issue-transfer' || action === 'alert-transfer') { transferDialog(action.split('-')[0],id); return; }
    if (action === 'issue-resolve') return resolveDialog(id);
    if (action === 'extra-observation') { addObservation(control.closest('form')); return; }
    if (action === 'alert-claim' || action === 'alert-wait') return commit(`/alerts/${encodeURIComponent(id)}`,'PATCH',{status:action === 'alert-claim' ? 'processing' : 'waiting_restock'},action === 'alert-claim' ? '已接手预警。' : '已标记待补货。');
  }

  const numeric = value => value == null || value === '' ? null : Number(value);

  async function submit(form) {
    const {whForm:type,whItem:item,whLocation:location,whId:id} = form.dataset;
    const fields = new FormData(form);
    const get = name => fields.get(name);
    if (type === 'owner-search') {
      const query = key(get('query')).trim();
      if (!query) return;
      const ticket = dialogRevision;
      const region = dialogBody.querySelector('[data-wh-owner-results]');
      region.innerHTML = '<p>正在查找…</p>';
      const result = await api(`/manager-candidates?q=${encodeURIComponent(query)}`);
      if (!active() || ticket !== dialogRevision || !dialog.open) return;
      const rows = result.items ?? [];
      region.innerHTML = rows.length ? `<ul class="wh-owner-list">${rows.map(person => `<li><div><strong>${esc(person.name)}</strong><small>${[person.employee_no,person.role].filter(Boolean).map(esc).join(' · ')}</small></div>${owners().some(owner => same(owner.id,person.id)) ? tag('已安排','healthy') : button('添加','owner-add',person.id,'primary')}</li>`).join('')}</ul>` : empty('未找到符合条件的账号');
      return;
    }
    if (type === 'owner-remove') return commit(`/managers/${encodeURIComponent(id)}`,'DELETE',undefined,'已撤下仓库负责人。');
    if (type === 'report') return commit('/issues','POST',{item_id:Number(item),position_id:Number(location),reason:get('reason'),observed_quantity:numeric(get('observed_quantity')),note:get('note')},'已报告，仓库负责人将收到待办。');
    if (type === 'rule') {
      const values = Object.fromEntries(['minimum','yellow_upper','lead_days','safety_days','target_days'].map(name => [name,numeric(get(name))]));
      return commit(`/assessment/${encodeURIComponent(item)}`,'PUT',values,'已保存评估设置。');
    }
    if (type === 'catalog') {
      const payload = {name:get('name'),spec:get('spec'),category:get('category'),unit:get('unit'),aliases:key(get('aliases')).split(/[、,，]/).map(name => name.trim()).filter(Boolean),active:!item || get('active') === 'on'};
      return commit(item ? `/items/${encodeURIComponent(item)}` : '/items',item ? 'PUT' : 'POST',payload,'已保存物资档案。');
    }
    if (type === 'adjust') {
      const positionId = Number(get('position_id'));
      const place = locById(positionId);
      if (!place) throw new Error('请选择层位。');
      return commit('/adjustments','POST',{item_id:Number(item),position_id:positionId,quantity:Number(get('quantity')),reason:get('reason'),kind:get('kind'),version:place.version},'库存已按实盘入账。');
    }
    if (type === 'correct') return commit(`/transactions/${encodeURIComponent(id)}/correct`,'POST',{item_id:Number(item),position_id:numeric(location),quantity:Number(get('quantity')),version:numeric(form.dataset.whVersion),reason:get('reason')},'已保留原单并记录纠错。');
    if (type === 'position-photo') {
      const file = get('file');
      if (!file?.size) throw new Error('请先拍摄照片。');
      const capturedAt = new Date().toISOString();
      const captured = await compressWarehousePhoto(file);
      const upload = new FormData();
      upload.append('file',captured,'position.jpg');
      upload.append('captured_at',capturedAt);
      const photo = await api('/photos',{method:'POST',body:upload});
      return commit(`/positions/${encodeURIComponent(get('position_id'))}/photo`,'PUT',{photo_id:photo.id},'已更新货位照片。');
    }
    if (type === 'issue-transfer') return commit(`/issues/${encodeURIComponent(id)}/transfer`,'POST',{employee_id:Number(get('employee_id'))},'已转交待办。');
    if (type === 'alert-transfer') {
      const state = viewData.alerts.find(alert => same(alert.item_id,id))?.status ?? 'processing';
      return commit(`/alerts/${encodeURIComponent(id)}`,'PATCH',{status:state,target_employee_id:Number(get('employee_id'))},'已转交预警。');
    }
    if (type === 'issue-resolve') {
      const observations = Array.from(form.querySelectorAll('[data-wh-observation]')).filter(input => input.value !== '').map(input => ({item_id:Number(input.dataset.whItem),position_id:Number(input.dataset.whPosition),quantity:Number(input.value),version:Number(input.dataset.whVersion)}));
      return commit(`/issues/${encodeURIComponent(id)}/resolve`,'POST',{reason:get('reason'),observations},'已记录复点结果并处理待办。');
    }
  }

  shell.addEventListener('click',event => {
    const control = event.target.closest('[data-wh-action]');
    if (!control || control.disabled) return;
    control.disabled = true;
    Promise.resolve(click(control)).catch(handleError).finally(() => { control.disabled = false; });
  });
  shell.addEventListener('submit',event => {
    const form = event.target;
    if (!form.matches('[data-wh-form]')) return;
    event.preventDefault();
    dialogError('');
    const submitButton = form.querySelector('button:not([type="button"])');
    if (submitButton?.disabled) return;
    if (submitButton) submitButton.disabled = true;
    submit(form).catch(handleError).finally(() => { if (submitButton) submitButton.disabled = false; });
  });
  shell.addEventListener('input',event => {
    if (event.target.dataset.whField === 'query-word') { finder.word = event.target.value; finder.item = ''; renderQueryResults(); }
  });
  shell.addEventListener('change',event => {
    const field = event.target.dataset.whField;
    if (field === 'query-category') { finder.category = event.target.value; finder.item = ''; renderQueryResults(); }
    if (field === 'adjust-position') {
      const place = locById(event.target.value);
      const item = itemById(event.target.form.dataset.whItem);
      const amount = binsOf(item || {}).find(entry => same(entry.id,event.target.value))?.row.quantity ?? 0;
      const hint = event.target.form.querySelector('[data-wh-adjust-current]');
      hint.textContent = place ? `当前账面 ${quantity(item,amount)}` : '';
      hint.hidden = !place;
    }
  });

  heading('物资管理','');
  body.innerHTML = empty('正在载入物资管理');
  try {
    await refresh();
    if (active()) await show(initialView === 'tasks' || openTaskId ? 'tasks' : 'home');
  } catch (error) { body.replaceChildren(); handleError(error); }
}
