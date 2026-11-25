import {api as portalApi, json} from './api.js';
import {esc, portalPath} from './format.js';
import {newSubmissionKey} from './submission.js';
import {compressWarehousePhoto,warehousePhotoTime} from './warehouse-drafts.js';
import {renderWarehouseEntry} from './warehouse-entry.js';
import {warehouseMatches, warehouseSuggestionButtons} from './warehouse-search.js';

const moduleActions = [
  ['out', '出库', '选取货层位，按层拍照'],
  ['in', '入库', '登记实收、上架和直用'],
  ['query', '查物资', '找位置、查余量和现场照'],
  ['assessment', '物资评估', '看库存预警和领用趋势'],
];
const levels = {empty:'缺货', low:'缺少', healthy:'足量', unset:'未设库存线'};
const shortStock = level => level === 'low' || level === 'empty';
const visibleBalance = row => Number(row?.quantity ?? 0) > 0 || Boolean(row?.reported);
const numberFormat = new Intl.NumberFormat('zh-CN', {maximumFractionDigits:2});
const fmt = value => value === null || value === undefined ? '—' : numberFormat.format(value);
const key = value => String(value ?? '');
const same = (left, right) => key(left) === key(right);
const normalize = value => key(value).normalize('NFKC').toLowerCase().replace(/\s+/g, '');
const timeLabel = value => key(value).replace('T',' ').slice(0,16);
const actionAttrs = (action, id = '') => `data-wh-action="${esc(action)}" data-wh-id="${esc(id)}"`;
const button = (label, action, id = '', tone = 'plain') => `<button type="button" class="wh-button wh-button--${esc(tone)}" ${actionAttrs(action,id)}>${esc(label)}</button>`;
const ownerShortcut = (label, action) => `<button type="button" class="wh-owner-shortcut wh-owner-shortcut--${action}" ${actionAttrs(action)}><span class="wh-owner-shortcut-icon" aria-hidden="true"></span><strong>${esc(label)}</strong><span class="wh-owner-shortcut-arrow" aria-hidden="true">›</span></button>`;
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
  let quickLocation = '';
  let quickRequest = 0;
  const viewData = {};
  const finder = {word:'', category:'', shelf:'', location:'', item:'', selected:''};
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
  const ground = location => location?.kind === 'ground';
  const locationName = location => location?.label || `${shelfOf(location)} 架第 ${location.layer ?? location.level} 层`;
  const photoOf = location => location?.photo ?? location?.latest_photo;
  const binsOf = item => (item.locations ?? item.bins ?? []).map(row => {
    const id = row.position_id;
    return {row, id, location:locById(id)};
  }).filter(entry => entry.location);
  const stockOf = item => item.total ?? binsOf(item).reduce((total, entry) => total + Number(entry.row.quantity ?? 0), 0);
  const activeItems = () => catalog().filter(item => item.active !== false);
  const rowsAt = (place, items = activeItems()) => items.flatMap(item => {
    const row = item.locations?.find(balance => same(balance.position_id,locationId(place)));
    return visibleBalance(row) ? [{item,row}] : [];
  });

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
    header.classList.toggle('wh-header--catalog',view === 'catalog');
    header.innerHTML = `<div class="wh-heading-text">${view === 'home' ? '' : button('‹ 物资管理','home')}<h2>${esc(title)}</h2>${description ? `<p>${esc(description)}</p>` : ''}</div>${extras ? `<div class="wh-header-actions">${extras}</div>` : ''}`;
  }

  function renderHome() {
    header.hidden = true;
    const items = activeItems();
    const states = new Map(locations().map(place => {
      const stocked = rowsAt(place,items).filter(({row}) => row.quantity > 0);
      const level = stocked.some(({item}) => shortStock(item.stock_level)) ? 'low'
        : stocked.length && stocked.every(({item}) => item.stock_level === 'healthy') ? 'healthy'
        : stocked.length ? 'unset' : 'vacant';
      return [locationId(place),level];
    }));
    const locationsInUse = [...states.values()].filter(level => level !== 'vacant').length;
    const shelves = [...new Set(locations().filter(location => !ground(location)).map(shelfOf))].filter(Boolean).sort();
    const racks = shelves.map(shelf => {
      const layers = locations().filter(location => shelfOf(location) === shelf).sort((left,right) => right.level - left.level);
      const used = layers.filter(layer => states.get(locationId(layer)) !== 'vacant').length;
      return {zone:layers[0].zone, markup:`<li class="wh-rack" aria-label="${esc(shelf)} 架，${used} 层在用，共 ${layers.length} 层">
        <span class="wh-rack-label">${esc(shelf)} 架</span>
        <span class="wh-rack-frame">${layers.map(layer => {
          const level = states.get(locationId(layer));
          return `<button type="button" data-wh-level="${layer.level}" data-stock-level="${level}" class="wh-rack-level" aria-label="${esc(locationName(layer))} · ${levels[level] || '空置'}" aria-controls="wh-quick-location" aria-expanded="false" ${actionAttrs('quick-location',locationId(layer))}>${layer.level}</button>`;
        }).join('')}</span>
      </li>`};
    });
    const floor = locations().find(ground);
    const floorLevel = floor ? states.get(locationId(floor)) : 'vacant';
    const outOfStock = items.filter(item => item.stock_level === 'empty');
    const layout = ['左侧','中间','右侧'].map(zone => `<div class="wh-rack-zone"><span>${zone}</span><ul class="wh-rack-list" aria-label="${zone}货架">${racks.filter(rack => rack.zone === zone).map(rack => rack.markup).join('')}</ul></div>`).join('');
    body.innerHTML = `<section class="wh-overview">
      <div class="wh-overview-main"><h2>物资管理</h2>
        <dl class="wh-overview-stats"><div><dt>物资种类</dt><dd>${fmt(items.length)}</dd></div>
          <div><dt>在用层位</dt><dd>${fmt(locationsInUse)} <span>/ ${fmt(locations().length)}</span></dd></div></dl>
      </div>
      <div class="wh-layout"><div class="wh-rack-zones" aria-label="从左往右的货架布局">${layout}</div>
        ${floor ? `<button type="button" class="wh-floor-summary" data-stock-level="${floorLevel}" aria-controls="wh-quick-location" aria-expanded="false" ${actionAttrs('quick-location',locationId(floor))}><span>${esc(locationName(floor))}</span><span>${levels[floorLevel] || '空置'} <span aria-hidden="true">⌄</span></span></button>` : ''}
        <div class="wh-rack-legend" aria-label="货位颜色">${[['healthy','足量'],['low','缺少'],['unset','未设库存线'],['vacant','空置']].map(([level,label]) => `<span><i data-stock-level="${level}" aria-hidden="true"></i>${label}</span>`).join('')}</div>
        <section class="wh-quick-location" id="wh-quick-location" aria-label="货位快捷物资" aria-live="polite" hidden></section>
      </div>
    </section>
    ${outOfStock.length ? `<details class="wh-panel wh-home-short"><summary>全仓缺货 · ${outOfStock.length} 种</summary><ul>${outOfStock.map(item => `<li>${button(item.name,'query-stock-item',itemId(item))}<span>${esc(quantity(item,0))}</span></li>`).join('')}</ul></details>` : ''}
    <div class="wh-home-layout"><div class="wh-module-grid" aria-label="物资管理功能">${moduleActions.map(([action,title,details]) => {
      const authorized = action === 'in' ? manager() : action === 'out' ? dispatch() : canRead();
      return `<button type="button" class="wh-module wh-module--${action}" ${actionAttrs(action)}${authorized ? '' : ' disabled'}>
        <span class="wh-module-symbol" aria-hidden="true"><i></i></span>
        <span class="wh-module-name">${title}</span>
        <span class="wh-module-description">${authorized ? details : action === 'in' ? '由仓库负责人办理' : '需要仓储操作权限'}</span>
        <span class="wh-module-arrow" aria-hidden="true">↗</span></button>`;
    }).join('')}</div>
      ${ownerPanel()}</div>`;
  }

  function ownerPanel() {
    const people = owners();
    return `<aside class="wh-owners wh-panel" aria-labelledby="wh-owner-title">
      <div class="wh-owner-heading"><h3 id="wh-owner-title">仓库负责人</h3>${hr() ? `<button type="button" class="wh-icon-button wh-icon-button--accent" aria-label="添加仓库负责人" ${actionAttrs('owner-dialog')}>＋</button>` : `<small>${fmt(people.length)} 人</small>`}</div>
      ${people.length ? `<ul class="wh-owner-list">${people.map(owner => `<li><span class="wh-owner-avatar" aria-hidden="true">${esc(Array.from(owner.name || '人')[0])}</span><div class="wh-owner-identity"><strong>${esc(owner.name)}</strong>${owner.role ? `<small>${esc(owner.role)}</small>` : ''}</div>${hr() ? button('撤下','owner-remove',owner.id) : same(owner.id,operator().id) ? '<span class="wh-owner-self">本人</span>' : ''}</li>`).join('')}</ul>` : empty('尚未安排仓库负责人')}
      ${manager() ? `<div class="wh-owner-tools">${ownerShortcut('负责人待办','tasks')}${ownerShortcut('物资档案与库存','catalog')}</div>`
        : dispatch() && state.photo_task_count ? `<div class="wh-owner-tools">${ownerShortcut('货位待补照','tasks')}</div>` : ''}
    </aside>`;
  }

  function renderQuickLocation(place, loading = false) {
    const panel = body.querySelector('#wh-quick-location');
    if (!panel) return;
    for (const control of body.querySelectorAll('[data-wh-action="quick-location"]')) {
      control.setAttribute('aria-expanded', String(same(control.dataset.whId,quickLocation)));
    }
    panel.hidden = !place;
    if (!place) { panel.replaceChildren(); return; }
    panel.innerHTML = loading ? '<div class="wh-quick-heading"><h3>' + esc(locationName(place)) + '</h3></div><p>正在读取库存…</p>'
      : locationDetails(place,rowsAt(place),true);
  }

  async function selectQuickLocation(id) {
    quickLocation = same(quickLocation,id) ? '' : id;
    const sequence = ++quickRequest;
    const current = () => active() && view === 'home' && sequence === quickRequest;
    tell('');
    renderQuickLocation(locById(quickLocation),true);
    if (!quickLocation) return;
    try {
      const latest = await api('/bootstrap');
      if (!current()) return;
      state = latest;
      renderQuickLocation(locById(quickLocation));
    } catch {
      if (!current()) return;
      renderQuickLocation(locById(quickLocation));
      tell('库存刷新失败，快捷列表为上次读取的账面余量。','error');
    }
  }

  function renderQuery() {
    heading('查物资','');
    const categories = Array.from(new Set(activeItems().map(item => item.category).filter(Boolean))).sort();
    const shelves = new Map(locations().map(place => [shelfOf(place), ground(place) ? locationName(place) : shelfOf(place) + ' 架']));
    body.innerHTML = '<div class="wh-query-filters">' +
      '<div class="wh-query-name"><label>物资名称<input type="search" autocomplete="off" data-wh-field="query-word" placeholder="名称或关键词" value="' + esc(finder.word) + '"></label>' +
      '<div class="wh-name-options" data-wh-query-options role="group" aria-label="已有物资建议" hidden></div></div>' +
      '<div class="wh-query-selects"><label>货架<select data-wh-field="query-shelf">' + options([['','全部货架'], ...shelves],finder.shelf) + '</select></label>' +
      '<label>类目<select data-wh-field="query-category">' + options([['','全部类目'], ...categories.map(category => [category,category])],finder.category) + '</select></label>' +
      '<label data-wh-query-layers hidden>层位<select data-wh-field="query-location"></select></label></div></div><div data-wh-query-results></div>';
    renderQueryResults();
  }

  function renderQueryResults() {
    const region = body.querySelector('[data-wh-query-results]');
    const layers = body.querySelector('[data-wh-query-layers]');
    if (!region || !layers) return;
    const activeLocations = locations().filter(place => !finder.shelf || same(finder.shelf,shelfOf(place)));
    layers.hidden = !finder.shelf || !activeLocations.some(place => !ground(place));
    if (!layers.hidden) layers.querySelector('select').innerHTML =
      options([['','全部层位'], ...activeLocations.map(place => [locationId(place),'第 ' + place.level + ' 层'])],finder.location);
    const term = normalize(finder.word);
    const results = warehouseMatches(catalog(),finder.word,finder.category).filter(item =>
      (!finder.selected || same(itemId(item),finder.selected)) && ((!finder.shelf && !finder.location) ||
      binsOf(item).some(entry => visibleBalance(entry.row) && activeLocations.includes(entry.location) &&
        (!finder.location || same(finder.location,entry.id)))));
    const suggestions = body.querySelector('[data-wh-query-options]');
    suggestions.hidden = !term || Boolean(finder.selected) || !results.length;
    suggestions.innerHTML = suggestions.hidden ? '' : warehouseSuggestionButtons(results,'data-wh-query-pick');
    const layer = locById(finder.location);
    if (layer) {
      region.innerHTML = '<div class="wh-panel wh-shared-location">' + locationDetails(layer,rowsAt(layer,results)) + '</div>';
      return;
    }
    region.innerHTML = results.length ? '<div class="wh-results">' + results.map(item => queryItem(item,activeLocations,results.length === 1)).join('') + '</div>'
      : empty(term ? '未找到匹配的物资' : '此处暂无在库物资',term ? '换个关键词或清除筛选后再试。' : '');
  }

  function locationPhoto(location) {
    const photo = photoOf(location);
    const image = photoMarkup(photo,locationName(location) + '现场照片');
    if (!image && !location.needs_photo) return '<p class="wh-location-empty-photo">暂无整层照</p>';
    const photoTime = photo?.updated_at ?? photo?.captured_at;
    const status = location.needs_photo ? tag('待补最新现场照','near') : photo?.stale ? tag('拍照后库存有变动','near') : '';
    const actions = dispatch() ? [
      location.needs_photo ? button('补拍','photo-complete',locationId(location)) : '',
      photo?.id ? button('照片报错','photo-report',locationId(location)) : '',
    ].join('') : '';
    return '<div class="wh-location-photo"><div><strong>' + esc(locationName(location)) + '现场照片</strong>' + status +
      (photoTime ? '<small>更新于 ' + esc(timeLabel(photoTime)) + (photo?.updated_by ? ' · ' + esc(photo.updated_by) : '') + '</small>' : '') +
      actions + '</div>' + image + '</div>';
  }

  function receiptPhoto(item) {
    const photo = item.receipt_photo;
    const image = photoMarkup(photo,item.name + '最近入库照');
    if (!image) return '';
    const date = photo.captured_at ? '拍摄 ' + timeLabel(photo.captured_at) : '入库 ' + timeLabel(photo.posted_at);
    return '<div class="wh-receipt-photo">' + image + '<div><strong>最近入库照</strong><small>' + esc(date) + '</small></div></div>';
  }

  function locationDetails(place, rows, quick = false, nested = false, showQuantity = true) {
    const id = locationId(place);
    const title = nested ? 'h4' : 'h3';
    const headerAction = quick ? button('查看详情','quick-query',id) : '';
    const count = quick ? rows.slice(0,4) : rows;
    const items = count.map(({item,row}) => {
      const itemKey = itemId(item);
      const action = (label,type,tone = 'plain') => '<button type="button" class="wh-button wh-button--' + tone + '" ' +
        actionAttrs(type,itemKey) + ' data-wh-location="' + esc(id) + '">' + label + '</button>';
      const actions = [
        dispatch() ? action('报错','report') : '',
        manager() && row.quantity > 0 ? action('移位','move') : '',
        manager() ? button('盘点与校准','calibrate',id) : '',
      ].filter(Boolean);
      const minimum = shortStock(item.stock_level) && item.minimum != null ?
        '<small>全仓 ' + esc(quantity(item,stockOf(item))) + ' · 最低 ' + esc(quantity(item,item.minimum)) + '</small>' : '';
      return '<li class="wh-bin"><div class="wh-location-item-head"><div><strong>' + esc(item.name) + '</strong>' +
        (row.reported ? tag('待核对','near') : '') + (shortStock(item.stock_level) ? tag(levels[item.stock_level],item.stock_level) : '') + minimum +
        '</div>' + (showQuantity ? '<strong class="wh-bin-stock">' + esc(quantity(item,row.quantity)) + '</strong>' : '') +
        '</div>' + receiptPhoto(item) +
        '<div class="wh-location-actions">' + (dispatch() && row.quantity > 0 ? action('去出库','out-bin','primary') : '') +
        (actions.length ? '<details class="wh-bin-more"><summary>更多</summary><div>' + actions.join('') + '</div></details>' : '') +
        '</div></li>';
    }).join('');
    const emptyAction = manager() && !rows.length ? button('盘点与校准','calibrate',id) : '';
    return '<div class="wh-quick-heading"><' + title + '>' + esc(locationName(place)) + '</' + title + '>' + headerAction + '</div>' +
      locationPhoto(place) + (items ? '<ul class="wh-location-list" aria-label="账面余量">' + items + '</ul>' :
        '<div class="wh-location-empty"><p>此处暂无在库物资</p>' + emptyAction + '</div>') +
      (rows.length > count.length ? '<p>共 ' + fmt(rows.length) + ' 种物资</p>' : '');
  }

  function queryItem(item, activeLocations, onlyResult) {
    const id = itemId(item);
    const bins = binsOf(item).filter(entry => visibleBalance(entry.row) && activeLocations.includes(entry.location))
      .sort((a,b) => shelfOf(a.location).localeCompare(shelfOf(b.location)) || b.location.level - a.location.level);
    const isOpen = same(finder.item,id) || (finder.item === '' && onlyResult && !!finder.word.trim());
    const total = finder.shelf ? bins.reduce((sum,entry) => sum + Number(entry.row.quantity ?? 0),0) : stockOf(item);
    const stateLabel = levels[item.stock_level];
    const detail = bins.length ? bins.map(({row,location}) => '<div class="wh-shared-location">' +
      locationDetails(location,[{item,row}],false,true,bins.length !== 1) + '</div>').join('') : empty('暂无在库货位');
    return '<article class="wh-goods"><button type="button" class="wh-goods-summary" aria-expanded="' + isOpen + '" ' +
      actionAttrs('query-item',id) + '><span><strong>' + esc(item.name) + '</strong>' +
      (stateLabel ? tag(stateLabel,item.stock_level) : '') + '</span><span class="wh-goods-quantity"><small>' +
      (finder.shelf ? '本货架余量' : '全仓余量') + '</small><strong>' + esc(quantity(item,total)) + '</strong></span>' +
      '<span class="wh-goods-arrow" aria-hidden="true">' + (isOpen ? '⌃' : '⌄') + '</span></button>' +
      (isOpen ? '<div class="wh-goods-detail">' + detail + '</div>' : '') + '</article>';
  }

  function ownerDialog() {
    if (hr()) openDialog('添加仓库负责人','<form class="wh-form" data-wh-form="owner-search"><label>姓名或员工号<input type="search" name="query" autocomplete="off" required placeholder="输入姓名或员工号"></label><button class="wh-button wh-button--primary">搜索</button></form><div class="wh-person-results" data-wh-owner-results aria-live="polite"></div>');
  }

  function photoReportDialog(id) {
    const place = locById(id);
    const photo = photoOf(place);
    if (!dispatch() || !photo?.id) return;
    openDialog(`照片报错 · ${locationName(place)}`,`<form class="wh-form" data-wh-form="photo-report" data-wh-location="${esc(id)}" data-wh-photo="${esc(photo.id)}"><label>说明<textarea name="note" rows="2" maxlength="240" placeholder="例如：拍错了层位"></textarea></label><button class="wh-button wh-button--primary">提交报错</button></form>`);
  }

  function reportDialog(itemIdValue, locationIdValue) {
    const item = itemById(itemIdValue);
    const location = locById(locationIdValue);
    if (!dispatch() || !item || !location) return;
    openDialog('报告货位差异',`<form class="wh-form" data-wh-form="report" data-wh-item="${esc(itemIdValue)}" data-wh-location="${esc(locationIdValue)}" data-wh-photo="${esc(photoOf(location)?.id)}"><p>${esc(item.name)} · ${esc(locationName(location))}</p><label>问题<select name="reason"><option value="misplaced">位置不符</option><option value="missing">找不到货</option><option value="mismatch">数量不符</option><option value="map">图上位置有误</option>${photoOf(location) ? '<option value="photo">现场照不清楚或拍错</option>' : ''}</select></label><label>实见数量（如已清点）<input type="number" min="0" inputmode="numeric" step="1" name="observed_quantity"></label><label>说明<textarea name="note" rows="2" maxlength="240"></textarea></label><button class="wh-button wh-button--primary">提交报错</button></form>`);
  }

  async function renderAssessment(stillHere) {
    heading('物资评估','先看库存状态，再看每种物资的领用与补货建议。');
    const report = await api(manager() ? '/assessment?include_archived=true' : '/assessment');
    if (!stillHere()) return;
    const items = report.items ?? [];
    const archived = items.filter(row => row.active === false);
    const rows = items.filter(row => row.active !== false);
    const risk = {empty:0,low:1,healthy:2,unset:3};
    rows.sort((left,right) => (risk[left.level] ?? 4) - (risk[right.level] ?? 4) || left.name.localeCompare(right.name,'zh-CN'));
    const riskRows = rows.filter(row => shortStock(row.level));
    const suggestions = rows.filter(row => !riskRows.includes(row) && row.suggested_point != null && row.stock <= row.suggested_point);
    const rowMarkup = row => `<article class="wh-assessment-row wh-assessment-row--${esc(row.level)}"><div class="wh-assessment-head"><div><strong>${esc(row.name)}</strong><small>可领 ${esc(quantity(row,row.stock))}</small></div>${tag(levels[row.level] || levels.unset,row.level || 'unset')}</div><div class="wh-assessment-numbers"><div><small>建议补货点</small><strong>${row.suggested_point == null ? '待评估' : esc(quantity(row,row.suggested_point))}</strong></div><div><small>强提醒触发点</small><strong>${row.minimum == null ? '缺货时' : `低于 ${esc(quantity(row,row.minimum))}`}</strong></div></div>${row.suggested_point != null && row.stock <= row.suggested_point ? `<p class="wh-annotation">已到建议补货点${row.suggested_quantity != null ? ` · 建议补 ${esc(quantity(row,row.suggested_quantity))}` : ''}</p>` : ''}${manager() ? `<div class="wh-row-end">${button('设置预警与补货','rule',row.item_id)}${row.stock === 0 ? button('归档物资','archive-item',row.item_id) : ''}</div>` : ''}</article>`;
    const counts = rows.reduce((totals,row) => {
      totals[row.level] = (totals[row.level] || 0) + 1;
      return totals;
    },{});
    const statusSummary = [['low','缺少',riskRows.length],['healthy','足量',counts.healthy || 0]].map(([state,label,count]) =>
      `<div class="wh-stock-stat wh-stock-stat--${state}"><dt>${label}</dt><dd>${fmt(count)}</dd></div>`).join('');
    body.innerHTML = `${rows.length ? `<dl class="wh-stock-overview" aria-label="库存状态汇总">${statusSummary}</dl>` : ''}
      <section class="wh-alert-section"><div class="wh-section-heading"><h3>库存预警</h3><span>${riskRows.length} 项需关注${counts.unset ? ` · ${counts.unset} 项待设置提醒线` : ''}</span></div>${riskRows.length ? `<div class="wh-assessment-grid">${riskRows.map(rowMarkup).join('')}</div>` : empty('暂无低库存或缺货')}</section>
      ${suggestions.length ? `<section class="wh-alert-section"><div class="wh-section-heading"><h3>补货参考</h3></div><div class="wh-assessment-grid">${suggestions.map(rowMarkup).join('')}</div></section>` : ''}
      <section class="wh-evaluation-section"><div class="wh-section-heading"><h3>领用与补货</h3><span>${rows.length} 种物资</span></div>${rows.length ? `<div class="wh-results">${rows.map(row => usageDetails(row,manager())).join('')}</div>` : empty('完成建档和期初盘点后查看')}</section>
      ${archived.length ? `<details class="wh-archived"><summary>已归档物资的领用记录</summary><div class="wh-results">${archived.map(row => usageDetails(row,false)).join('')}</div></details>` : ''}`;
    viewData.assessment = rows;
  }

  function usageDetails(row, canManage) {
    const months = row.months ?? [];
    const peak = Math.max(1,...months.map(month => Number(month.usage ?? 0)));
    const usage = (title,value) => `<div><small>${title}</small><strong>${esc(quantity(row,value))}</strong></div>`;
    const point = row.suggested_point == null ? '待评估' : quantity(row,row.suggested_point);
    const active = row.active !== false;
    const estimate = active ? `<div class="wh-usage-notes"><span>近 30 天缺货 ${fmt(row.days_out_of_stock)} 天</span><span>日均领用 ${esc(quantity(row,row.daily_average))}</span><span>预计可用 ${fmt(row.predicted_days)} 天</span><span>建议补货点 ${esc(point)}</span><span>强提醒 ${row.minimum == null ? '余量为 0 时' : `低于 ${esc(quantity(row,row.minimum))}`}</span><span>建议补货量 ${esc(quantity(row,row.suggested_quantity))}</span></div>${row.estimate_reason ? `<p class="wh-annotation">${esc(row.estimate_reason)}</p>` : ''}` : '';
    return `<details class="wh-goods wh-usage"><summary><span><strong>${esc(row.name)}</strong>${active ? `<small>已记录 ${fmt(row.days_tracked)} 天 · 可领 ${esc(quantity(row,row.stock))}</small>` : ''}</span>${active ? tag(levels[row.level] || levels.unset,row.level || 'unset') : tag('已归档')}</summary><div class="wh-usage-content"><div class="wh-usage-numbers">${usage('近 7 天领用',row.usage_7)}${usage('近 30 天领用',row.usage_30)}${usage('近 7 天直用',row.direct_7)}${usage('近 30 天直用',row.direct_30)}</div>${estimate}${months.length ? `<div class="wh-monthly" aria-label="月度领用">${months.map(month => `<div class="wh-month-row"><span>${esc(month.month)}</span><span class="wh-month-bar" aria-hidden="true" style="--wh-fill:${Math.max(0,Math.min(100,(Number(month.usage ?? 0) / peak) * 100))}%"></span><strong>${esc(quantity(row,month.usage))}</strong><small>直用 ${esc(quantity(row,month.direct))}</small></div>`).join('')}</div>` : ''}${canManage ? `<div class="wh-row-end">${button('设置预警与补货','rule',row.item_id)}</div>` : ''}</div></details>`;
  }

  function ruleDialog(id) {
    const row = viewData.assessment?.find(entry => same(entry.item_id,id));
    if (!row || !manager()) return;
    const field = (label,name,value) => `<label>${esc(label)}<input type="number" name="${name}" min="${name === 'safety_days' ? 0 : 1}" step="1" inputmode="numeric" value="${esc(value ?? '')}"></label>`;
    openDialog(`库存预警 · ${row.name}`,`<form class="wh-form" data-wh-form="rule" data-wh-item="${esc(id)}">
      ${field('最低库存线（' + row.unit + '）','minimum',row.minimum)}
      <div class="wh-rule-legend">${tag('达到最低线 · 足量','healthy')}${tag('低于最低线 · 缺少','low')}</div>
      <details class="wh-rule-more"><summary>补货估算</summary><div class="wh-form-grid">${field('补货到货天数','lead_days',row.lead_days)}${field('预留天数','safety_days',row.safety_days)}${field('目标可用天数','target_days',row.target_days)}</div></details>
      <button class="wh-button wh-button--primary">保存设置</button></form>`);
  }

  async function renderTasks(stillHere) {
    heading(manager() ? '负责人待办' : '货位待补照','');
    const [issues,alerts,photos] = await Promise.all([manager() ? api('/issues') : {},manager() ? api('/alerts') : {},api('/photo-tasks')]);
    if (!stillHere()) return;
    viewData.issues = issues.items ?? [];
    viewData.alerts = alerts.items ?? [];
    const outstanding = viewData.issues.filter(issue => issue.status !== 'closed');
    const completed = viewData.issues.filter(issue => issue.status === 'closed');
    const photoCards = (photos.items ?? []).map(task => {
      const reports = task.reports ?? [];
      const feedback = reports.length ? `<ul class="wh-photo-reports" aria-label="照片报错记录">${reports.map(report =>
        `<li><p>${esc(report.note || '报告现场照片有误')}</p><small>${esc(report.reporter_name)} · ${esc(timeLabel(report.reported_at))}</small></li>`
      ).join('')}</ul>` : '';
      return `<article class="wh-task-card"><div class="wh-task-title"><div><strong>${esc(locationName(locById(task.position_id)))}</strong><small>${task.owner_name ? `跟进人 ${esc(task.owner_name)} · ` : ''}${esc(timeLabel(task.requested_at))}</small></div>${manager() && task.handover_due ? tag('交班待跟进','near') : ''}</div>${feedback}<div class="wh-row-end">${button('补拍整层','photo-complete',task.position_id,'primary')}</div></article>`;
    });
    const photoSection = `<section class="wh-task-section"><div class="wh-section-heading"><h3>补拍货位现场照</h3><span>${photoCards.length} 个货位</span></div>${photoCards.length ? `<div class="wh-results">${photoCards.join('')}</div>` : empty('暂无待补照货位')}</section>`;
    body.innerHTML = photoSection + (manager() ? `<section class="wh-task-section"><div class="wh-section-heading"><h3>货位异常</h3><span>${outstanding.length} 项待处理</span></div>${outstanding.length ? `<div class="wh-results">${outstanding.map(issueCard).join('')}</div>` : empty('暂无货位异常待办')}</section><section class="wh-task-section"><div class="wh-section-heading"><h3>库存预警</h3><span>${viewData.alerts.length} 项</span></div>${viewData.alerts.length ? `<div class="wh-results">${viewData.alerts.map(alertCard).join('')}</div>` : empty('暂无库存强提醒')}</section>${completed.length ? `<section class="wh-task-section"><div class="wh-section-heading"><h3>最近处理</h3></div><div class="wh-results">${completed.slice(0,10).map(issueCard).join('')}</div></section>` : ''}` : '');
    if (openTaskId) body.querySelector('[aria-current="true"]')?.scrollIntoView({block:'nearest'});
  }

  async function photoTaskDialog(id) {
    const place = locById(id);
    if (!dispatch() || !place) return;
    const currentView = revision;
    const result = await api('/photo-tasks');
    if (!active() || revision !== currentView) return;
    const task = result.items.find(row => same(row.position_id,id));
    if (!task) { await refresh(); if (active() && revision === currentView) await show(view); return; }
    openDialog(`补拍 · ${locationName(place)}`,`<form class="wh-form" data-wh-form="photo-task" data-wh-id="${esc(id)}" data-wh-version="${task.version}" data-wh-requested="${esc(task.requested_at)}"><p>拍清货位标识和整层现况。</p><label>现场照片<input type="file" accept="image/*" capture="environment" name="file" required></label><button class="wh-button wh-button--primary">保存现场照</button></form>`);
  }

  function issueCard(issue) {
    const {id,status,assignee_name,reporter_name,opened_at,transaction_status,transaction_id} = issue;
    const mine = same(issue.assignee_id,operator().id);
    const progress = status === 'closed' ? '已处理' : status === 'claimed' ? `${assignee_name || '负责人'}处理中` : '待接手';
    const controls = status === 'open' ? button('接手处理','issue-claim',id,'primary') : status === 'claimed' && mine ? `${button('复点并处理','issue-resolve',id,'primary')}${button('转交','issue-transfer',id)}` : '';
    return `<article class="wh-task-card" ${same(openTaskId,id) ? 'aria-current="true"' : ''}><div class="wh-task-title"><div><strong>${esc(issue.title || '货位异常')}</strong><small>${[reporter_name,timeLabel(opened_at),transaction_id ? `单据 ${transaction_id}` : ''].filter(Boolean).map(esc).join(' · ')}</small></div>${tag(progress,status === 'open' ? 'low' : status === 'closed' ? 'healthy' : 'near')}</div>${transaction_status === 'pending' ? '<p class="wh-annotation">实物已取走，原出库待补记。</p>' : ''}<div class="wh-task-lines">${(issue.lines ?? []).map(line => { const item = itemById(line.item_id); const place = locById(line.position_id); const seen = line.observed_quantity == null ? '' : ` · 实见 ${quantity(item,line.observed_quantity)}`; return `<span>${esc(item?.name || '物资')} · ${esc(place ? locationName(place) : '货位')} · 账面 ${esc(quantity(item,line.book_quantity))}${esc(seen)}</span>`; }).join('')}</div>${issue.note ? `<p>${esc(issue.note)}</p>` : ''}${issue.resolution ? `<p>${esc(issue.resolution)}</p>` : ''}${issue.photo_cleared ? '<small>照片已按保留规则清理</small>' : issue.photo_id ? photoMarkup(`/api/warehouse/photos/${issue.photo_id}`,'报告现场照片') : ''}${controls ? `<div class="wh-row-end">${controls}</div>` : ''}</article>`;
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
    const items = activeItems();
    const archived = catalog().filter(item => item.active === false);
    heading('物资档案与库存',`${fmt(items.length)} 种物资`,`<div class="wh-catalog-primary">
      <a class="wh-button wh-export" href="${esc(portalPath('/api/warehouse/inventory/export.xlsx'))}" download><span aria-hidden="true">↓</span> 导出库存 Excel</a>
      ${button('盘点与校准','calibrate','','primary')}
    </div><div class="wh-catalog-utilities">${button('查收发记录','history')}${button('添加物资','new-item')}</div>`);
    const card = item => {
      const id = itemId(item);
      const bins = binsOf(item).filter(({row}) => visibleBalance(row));
      return `<article class="wh-item-card wh-catalog-card">
        <div class="wh-catalog-item"><h3>${esc(item.name)}</h3>${item.active === false ? tag('已归档') : ''}<small>${[item.spec,item.category].filter(Boolean).map(esc).join(' · ')}</small></div>
        <div class="wh-catalog-places">${bins.length ? `<ul aria-label="货位余量">${bins.map(({location,row}) => `<li><span>${esc(locationName(location))}</span><strong>${esc(quantity(item,row.quantity))}</strong>${row.reported ? '<span class="wh-catalog-review">待核对</span>' : ''}</li>`).join('')}</ul>` : '<small>暂无在库货位</small>'}</div>
        <div class="wh-catalog-stock"><small>账面库存</small><strong>${fmt(stockOf(item))} <span>${esc(item.unit)}</span></strong></div>
        <div class="wh-catalog-row-actions">${button('编辑资料','edit-item',id)}${item.active !== false && bins.some(({row}) => row.quantity > 0) ? button('移位','move',id) : ''}
          ${item.active === false ? button('恢复使用','restore-item',id) : stockOf(item) === 0 ? button('归档物资','archive-item',id) : ''}</div>
      </article>`;
    };
    body.innerHTML = (items.length ? `<div class="wh-results">${items.map(card).join('')}</div>` : empty('暂无在用物资')) +
      (archived.length ? `<details class="wh-archived"><summary>已归档 · ${fmt(archived.length)} 种</summary><div class="wh-results">${archived.map(card).join('')}</div></details>` : '');
  }

  function itemDialog(id) {
    const item = id ? itemById(id) : null;
    if (!manager() || id && !item) return;
    openDialog(item ? `编辑 · ${item.name}` : '添加物资',`<form class="wh-form" data-wh-form="catalog" data-wh-item="${esc(id)}"><div class="wh-form-grid"><label>名称<input name="name" value="${esc(item?.name || '')}" required maxlength="100"></label><label>规格<input name="spec" value="${esc(item?.spec || '')}" maxlength="100"></label><label>类目<input name="category" value="${esc(item?.category || '')}" maxlength="50"></label><label>单位<input name="unit" value="${esc(item?.unit || '')}" required maxlength="20" placeholder="箱、个、件"></label></div><label>简称（用顿号分隔）<input name="aliases" value="${esc(item?.aliases?.join('、') ?? '')}" maxlength="240"></label><button class="wh-button wh-button--primary">保存物资</button></form>`);
  }

  async function archiveDialog(id) {
    if (!manager()) return;
    const source = revision;
    await refresh();
    if (!active() || source !== revision || !manager()) return;
    const item = itemById(id);
    if (!item || item.active === false) { tell('该物资已归档。'); return; }
    if (stockOf(item) !== 0) { tell('该物资仍有库存在货位，请核对全仓余量。','error'); return; }
    if (binsOf(item).some(({row}) => row.reported)) {
      openDialog(`归档 · ${item.name}`,`<p>该物资还有待核对差异或待补记出库。请在负责人待办结清后归档。</p>
        <div class="wh-row-end">${button('打开负责人待办','tasks','','primary')}</div>`);
      return;
    }
    openDialog(`归档 · ${item.name}`,`<form class="wh-form" data-wh-form="archive" data-wh-item="${esc(id)}">
      <p>已核对全仓余量为 0。归档会结束该物资的缺货提醒，历史单据保留，可在物资档案恢复使用。</p>
      <label>本人登录员工号<input name="employee_no" autocomplete="off" maxlength="40" required></label>
      <button class="wh-button wh-button--primary">确认归档</button></form>`);
  }

  async function moveDialog(id, fromId) {
    if (!manager()) return;
    const sourceView = view;
    await refresh();
    if (!active() || view !== sourceView) return;
    const item = itemById(id);
    if (!item) return;
    const stocked = binsOf(item).filter(({row}) => row.quantity > 0);
    if (!stocked.length) return;
    openDialog(`移位 · ${item.name}`,`<form class="wh-form" data-wh-form="move" data-wh-item="${esc(id)}">
      <p>物资实际移到新货位后登记。原货位和新货位会各生成一条补照待办。</p>
      <label>从<select name="from" required>${options([['','请选择原货位'], ...stocked.map(({id:binId,location,row}) => [binId,`${locationName(location)} · ${quantity(item,row.quantity)}`])],fromId)}</select></label>
      <label>到<select name="to" required>${options([['','请选择实际移入货位'], ...locations().map(place => [locationId(place),locationName(place)])])}</select></label>
      <label>移位数量（${esc(item.unit)}）<input type="number" min="1" max="1000000" step="1" inputmode="numeric" name="quantity" required></label>
      <button class="wh-button wh-button--primary">确认移位</button></form>`);
    dialogBody.querySelector('[data-wh-form="move"]').dataset.whClient = newSubmissionKey();
  }

  async function calibrationDialog(id) {
    if (!manager()) return;
    const source = view;
    await refresh();
    if (!active() || source !== view) return;
    const choices = locations().map(place => [locationId(place),locationName(place)]);
    openDialog('盘点与校准',`<form class="wh-form" data-wh-form="calibration">
      <label>货位<select name="position_id" data-wh-field="calibration-position" required>${options([['','请选择实际货位'], ...choices],id)}</select></label>
      <div class="wh-form" data-wh-calibration-items></div>
      <label>最新现场照 <small>可选</small><input type="file" accept="image/*" capture="environment" name="file"></label><p class="wh-annotation">只登记实盘余量时，系统会生成该货位的补照待办。</p>
      <label>校准原因 <small>填写实盘数量时必填</small><textarea name="reason" rows="2" maxlength="240"></textarea></label>
      <button class="wh-button wh-button--primary">保存校准</button></form>`);
    const form = dialogBody.querySelector('[data-wh-form="calibration"]');
    form.dataset.whClient = newSubmissionKey();
    renderCalibrationItems(form, id);
  }

  function calibrationRow(item, position) {
    const balance = binsOf(item).find(entry => same(entry.id,position.id))?.row.quantity ?? 0;
    return `<label class="wh-observation"><span>${esc(item.name)}<small>账面 ${esc(quantity(item,balance))}</small></span>
      <input type="number" min="0" max="1000000" step="1" inputmode="numeric" data-wh-calibration-item="${esc(itemId(item))}" aria-label="${esc(item.name)}实盘余量" placeholder="实盘余量"></label>`;
  }

  function renderCalibrationItems(form, id) {
    const place = locById(id);
    const host = form.querySelector('[data-wh-calibration-items]');
    form.dataset.whVersion = place?.version ?? '';
    host.replaceChildren();
    if (!place) return;
    const items = catalog().filter(item => item.active !== false);
    const present = items.filter(item => binsOf(item).some(entry => same(entry.id,id)));
    const presentIds = new Set(present.map(itemId));
    const other = items.filter(item => !presentIds.has(itemId(item)));
    host.innerHTML = `<p class="wh-annotation">只填写已清点的物资，填 0 表示现场已清空。</p>
      <div class="wh-form" data-wh-calibration-rows>${present.map(item => calibrationRow(item,place)).join('')}</div>
      ${other.length ? `<div class="wh-form-grid"><label>同货位其他物资<select data-wh-calibration-choice>${options(other.map(item => [itemId(item),item.name]))}</select></label>${button('加入复点','calibration-add')}</div>` : ''}`;
  }

  async function renderHistory(stillHere) {
    heading('收发与调整记录','',button('物资档案与库存','catalog'));
    const report = await api('/transactions');
    if (!stillHere()) return;
    const labels = {inbound:'入库',outbound:'出库',opening:'期初盘点',adjustment:'库存调整',correction:'单据纠错',calibration:'货位校准',photo:'更新现场照',move:'货位移位'};
    const statuses = {posted:'已入账',pending:'待核对',draft:'待确认',closed:'已关闭'};
    const rows = report.items ?? [];
    body.innerHTML = rows.length ? `<div class="wh-results">${rows.map(row => `<article class="wh-item-card wh-history-card"><div class="wh-task-title"><div><strong>${labels[row.kind] || '仓储单据'} · ${esc(row.id)}</strong><small>${[row.actor_name,timeLabel(row.posted_at ?? row.created_at)].filter(Boolean).map(esc).join(' · ')}</small></div>${tag(statuses[row.status] || row.status,row.status === 'posted' ? 'healthy' : 'near')}</div><div class="wh-row-end">${button('查看单据','history-record',row.id)}</div></article>`).join('')}</div>` : empty('暂无出入库和调整记录');
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
      if (line.note && receipts.includes(line)) details.push(`收货说明 ${line.note}`);
      const canCorrect = row.status === 'posted' && ['inbound','outbound'].includes(row.kind) && (lines.includes(line) || line.direct_quantity);
      return `<div class="wh-history-line"><div><strong>${esc(item?.name ?? line.item_name ?? '物资')}</strong><small>${details.map(esc).join(' · ')}</small></div>${canCorrect ? `<button type="button" class="wh-button wh-button--plain" ${actionAttrs('correct-record',line.item_id)} data-wh-location="${esc(line.position_id ?? '')}">${lines.includes(line) ? '纠正数量' : '纠正直用量'}</button>` : ''}</div>`;
    });
    const evidence = photos.map(photo => {
      if (!photo.cleared_at) return photoMarkup(photo,'本单照片');
      const place = locById(photo.position_id);
      const details = [place ? locationName(place) : '',photo.updated_by,timeLabel(photo.captured_at)].filter(Boolean);
      return `<div class="wh-cleared-photo"><strong>照片已按保留规则清理</strong><small>${details.map(esc).join(' · ')}</small></div>`;
    }).join('');
    openDialog(`单据 · ${row.id}`,`<p>${[row.actor_name,timeLabel(row.posted_at ?? row.created_at)].filter(Boolean).map(esc).join(' · ')}</p>${row.reason ? `<p>${esc(row.reason)}</p>` : ''}${row.related_id ? `<p>关联原单 ${esc(row.related_id)}</p>` : ''}<div class="wh-transaction-lines">${grouped.join('')}</div>${photos.length ? `<div class="wh-transaction-photos">${evidence}</div>` : ''}`);
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
    if (['catalog','history','in'].includes(next) && !manager()) next = 'home';
    if (next === 'tasks' && !dispatch()) next = 'home';
    if (next === 'out' && !dispatch() || ['query','assessment'].includes(next) && !canRead()) next = 'home';
    const openOwnerView = view !== next && ['tasks','catalog','history'].includes(next);
    if (entryCleanup) { entryCleanup(); entryCleanup = undefined; }
    quickLocation = '';
    quickRequest += 1;
    closeDialog();
    view = next;
    const renderId = ++revision;
    const stillHere = () => active() && renderId === revision;
    tell('');
    if (next === 'home') { renderHome(); return; }
    if (next === 'query') { renderQuery(); return; }
    heading({assessment:'物资评估',tasks:manager() ? '负责人待办' : '货位待补照',catalog:'物资档案与库存',history:'收发与调整记录',in:'入库',out:'出库'}[next] || '物资管理','');
    body.innerHTML = empty('正在载入');
    if (openOwnerView) {
      shell.closest('.app-shell')?.scrollTo(0,0);
      window.scrollTo(0,0);
    }
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
        tell(result?.status === 'pending' ? `已提交差异待办，原出库单 ${result.id} 待核对。` : result?.deferred_photo_positions?.length ? `已确认${next === 'out' ? '出库' : '入库'}，${result.deferred_photo_positions.length} 个货位待补照。` : result?.id ? `已确认单据 ${result.id}` : '单据已提交。');
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
    const result = await api(path,payload === undefined ? {method} : json(method,payload));
    if (!active()) return;
    const current = view;
    const selected = current === 'home' ? quickLocation : '';
    await refresh();
    if (!active() || view !== current) return;
    await show(current);
    if (selected) {
      quickLocation = selected;
      renderQuickLocation(locById(selected));
    }
    tell(typeof message === 'function' ? message(result) : message);
    changed();
  }

  async function click(control) {
    const {whAction:action,whId:id,whLocation:locationIdValue} = control.dataset;
    if (['home','out','in','query','assessment','tasks','catalog','history'].includes(action)) return show(action);
    if (action === 'quick-location' && canRead()) return selectQuickLocation(id);
    if (action === 'quick-query' && canRead()) {
      const place = locById(id);
      if (!place) return;
      Object.assign(finder,{word:'',category:'',shelf:shelfOf(place),location:id,item:'',selected:''});
      return show('query');
    }
    if (action === 'query-stock-item' && canRead()) {
      const item = itemById(id);
      if (!item) return;
      Object.assign(finder,{word:item.name,category:'',shelf:'',location:'',item:id,selected:id});
      return show('query');
    }
    if (action === 'out-bin') return show('out',{itemId:Number(id),locationId:Number(locationIdValue)});
    if (action === 'query-item') { finder.item = control.getAttribute('aria-expanded') === 'true' ? null : id; renderQueryResults(); body.querySelector(`[data-wh-action="query-item"][data-wh-id="${CSS.escape(id)}"]`)?.focus(); return; }
    if (action === 'close-dialog') { closeDialog(); return; }
    if (action === 'owner-dialog') { ownerDialog(); return; }
    if (action === 'owner-add' && hr()) return commit('/managers','POST',{employee_id:Number(id)},'已添加仓库负责人。');
    if (action === 'owner-remove' && hr()) {
      const owner = owners().find(person => same(person.id,id));
      if (owner) openDialog('撤下负责人',`<form class="wh-form" data-wh-form="owner-remove" data-wh-id="${esc(id)}"><p>确认撤下 ${esc(owner.name)} 的仓库负责人权限。</p><button class="wh-button wh-button--primary">确认撤下</button></form>`);
      return;
    }
    if (action === 'report') { reportDialog(id,locationIdValue); return; }
    if (action === 'photo-report') return photoReportDialog(id);
    if (action === 'photo-complete') return photoTaskDialog(id);
    if (action === 'move') return moveDialog(id,locationIdValue);
    if (action === 'rule') { ruleDialog(id); return; }
    if (action === 'new-item' || action === 'edit-item') { itemDialog(id); return; }
    if (action === 'archive-item') return archiveDialog(id);
    if (action === 'restore-item' && manager()) return commit(`/items/${encodeURIComponent(id)}/restore`,'POST',{},'已恢复使用。');
    if (action === 'calibrate') return calibrationDialog(id);
    if (action === 'calibration-add') {
      const form = control.form;
      const choice = form.querySelector('[data-wh-calibration-choice]');
      const item = itemById(choice.value);
      const place = locById(form.elements.position_id.value);
      if (item && place) {
        form.querySelector('[data-wh-calibration-rows]').insertAdjacentHTML('beforeend',calibrationRow(item,place));
        choice.selectedOptions[0].remove();
        if (!choice.options.length) choice.closest('.wh-form-grid').hidden = true;
      }
      return;
    }
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
    if (type === 'photo-report' || type === 'report' && get('reason') === 'photo') {
      return commit(`/positions/${encodeURIComponent(location)}/photo-task/report`,'POST',{photo_id:form.dataset.whPhoto,note:get('note')},'已报告，仓库负责人可在待办跟进。');
    }
    if (type === 'report') return commit('/issues','POST',{item_id:Number(item),position_id:Number(location),reason:get('reason'),observed_quantity:numeric(get('observed_quantity')),note:get('note')},'已报告，仓库负责人将收到待办。');
    if (type === 'rule') {
      const values = Object.fromEntries(['minimum','lead_days','safety_days','target_days'].map(name => [name,numeric(get(name))]));
      return commit(`/assessment/${encodeURIComponent(item)}`,'PUT',values,'已保存评估设置。');
    }
    if (type === 'catalog') {
      const payload = {name:get('name'),spec:get('spec'),category:get('category'),unit:get('unit'),aliases:key(get('aliases')).split(/[、,，]/).map(name => name.trim()).filter(Boolean)};
      return commit(item ? `/items/${encodeURIComponent(item)}` : '/items',item ? 'PUT' : 'POST',payload,'已保存物资档案。');
    }
    if (type === 'archive') return commit(`/items/${encodeURIComponent(item)}/archive`,'POST',{employee_no:get('employee_no')},'已归档物资。');
    if (type === 'move' && manager()) {
      const from = locById(get('from'));
      const to = locById(get('to'));
      if (!from || !to || same(from.id,to.id)) throw new Error('请选择不同的原货位和实际移入货位。');
      return commit('/moves','POST',{client_id:form.dataset.whClient,item_id:Number(item),from_position_id:from.id,to_position_id:to.id,
        from_version:from.version,to_version:to.version,quantity:Number(get('quantity'))},'移位已登记，两个货位待补照。');
    }
    if (type === 'correct') return commit(`/transactions/${encodeURIComponent(id)}/correct`,'POST',{item_id:Number(item),position_id:numeric(location),quantity:Number(get('quantity')),version:numeric(form.dataset.whVersion),reason:get('reason')},'已保留原单并记录纠错。');
    if (type === 'calibration' && manager()) {
      const positionId = Number(get('position_id'));
      const version = Number(form.dataset.whVersion);
      const counted = Array.from(form.querySelectorAll('[data-wh-calibration-item]')).filter(input => input.value !== '')
        .map(input => ({item_id:Number(input.dataset.whCalibrationItem),quantity:Number(input.value)}));
      const file = get('file');
      if (!counted.length && !file?.size) throw new Error('请填写实盘数量或上传现况照。');
      const reason = key(get('reason')).trim();
      if (counted.length && !reason) throw new Error('请填写数量校准原因。');
      if (file?.size && !form.dataset.whPhotoId) {
        const photo = await uploadPositionPhoto(file);
        form.dataset.whPhotoId = photo.id;
      }
      if (!counted.length && !reason) {
        return commit(`/positions/${positionId}/photo`,'PUT',{photo_id:form.dataset.whPhotoId},
          result => result.photo_reused ? '照片相同，已沿用当前现场照。' : '已更新货位照片。');
      }
      try {
        return await commit(`/positions/${positionId}/calibration`,'POST',{
          client_id:form.dataset.whClient, version,
          reason, items:counted, photo_id:form.dataset.whPhotoId || null,
        },'已保存货位校准。');
      } catch (error) {
        if (error.detail?.code !== 'STALE_POSITIONS') throw error;
        await refresh();
        if (form.isConnected) {
          resetCalibrationPhoto(form);
          renderCalibrationItems(form, positionId);
        }
        throw new Error('该货位刚有变动，已更新账面余量，请重新复点后保存。');
      }
    }
    if (type === 'photo-task') {
      const file = get('file');
      if (!file?.size) throw new Error('请先拍摄照片。');
      if (!form.dataset.whPhotoId) {
        const photo = await uploadPositionPhoto(file);
        form.dataset.whPhotoId = photo.id;
      }
      return commit(`/positions/${encodeURIComponent(id)}/photo-task`,'PUT',{
        photo_id:form.dataset.whPhotoId,version:Number(form.dataset.whVersion),requested_at:form.dataset.whRequested,
      },'现场照已更新，补照待办已完成。');
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

  async function uploadPositionPhoto(file) {
    const [captured,capturedAt] = await Promise.all([compressWarehousePhoto(file),warehousePhotoTime(file)]);
    const upload = new FormData();
    upload.append('file',captured,'position.jpg');
    upload.append('captured_at',capturedAt);
    return api('/photos',{method:'POST',body:upload});
  }

  function resetCalibrationPhoto(form) {
    form.elements.file.value = '';
    delete form.dataset.whPhotoId;
  }

  shell.addEventListener('click',event => {
    const suggested = event.target.closest('[data-wh-query-pick]');
    if (suggested && view === 'query') {
      const id = suggested.dataset.whQueryPick;
      const item = itemById(id);
      if (!item) return;
      finder.word = item.name;
      finder.selected = finder.item = id;
      body.querySelector('[data-wh-field="query-word"]').value = item.name;
      renderQueryResults();
      body.querySelector(`[data-wh-action="query-item"][data-wh-id="${CSS.escape(id)}"]`)?.focus();
      return;
    }
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
    const calibrating = form.dataset.whForm === 'calibration';
    if (calibrating) form.inert = true;
    submit(form).catch(handleError).finally(() => {
      if (calibrating) form.inert = false;
      if (submitButton) submitButton.disabled = false;
    });
  });
  shell.addEventListener('input',event => {
    if (event.target.dataset.whField === 'query-word') { finder.word = event.target.value; finder.item = finder.selected = ''; renderQueryResults(); }
  });
  shell.addEventListener('change',event => {
    const field = event.target.dataset.whField;
    if (field === 'calibration-position') {
      resetCalibrationPhoto(event.target.form);
      renderCalibrationItems(event.target.form,event.target.value);
    }
    if (event.target.matches('[name="file"]') && ['calibration','photo-task'].includes(event.target.form?.dataset.whForm)) {
      delete event.target.form.dataset.whPhotoId;
    }
    if (field === 'query-category') { finder.category = event.target.value; finder.item = ''; renderQueryResults(); }
    if (field === 'query-shelf') {
      const floor = locations().find(place => ground(place) && shelfOf(place) === event.target.value);
      finder.shelf = event.target.value; finder.location = floor ? key(locationId(floor)) : ''; finder.item = '';
      renderQueryResults();
    }
    if (field === 'query-location') { finder.location = event.target.value; finder.item = ''; renderQueryResults(); }
  });

  heading('物资管理','');
  body.innerHTML = empty('正在载入物资管理');
  try {
    await refresh();
    if (active()) await show(initialView === 'tasks' || openTaskId ? 'tasks' : 'home');
  } catch (error) { body.replaceChildren(); handleError(error); }
}
