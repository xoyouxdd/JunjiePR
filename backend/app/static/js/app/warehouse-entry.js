import { esc, portalPath } from './format.js';
import { json } from './api.js';
import { newSubmissionKey } from './submission.js';
import { clearWarehouseDraft, compressWarehousePhoto, saveWarehouseDraft, warehouseDraft } from './warehouse-drafts.js';

const quantity = value => Number(value) || 0;
const mounted = new WeakMap();
const inboundBatchPrefix = 'inbound-import-';
const matchKey = value => String(value || '').normalize('NFKC').toLowerCase().replace(/[\s·_-]+/g, '');
const timeFormat = new Intl.DateTimeFormat('zh-CN', {
  dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Shanghai',
});
const formatTime = value => timeFormat.format(value
  ? new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}+08:00`)
  : new Date());

function lookupItems(source, query, category) {
  const keyword = matchKey(query);
  return source.filter(item => item.active !== false && (!category || item.category === category))
    .map(item => {
      const terms = [item.name, item.spec, ...(item.aliases || [])].map(matchKey);
      const rank = !keyword || terms.includes(keyword) ? 0 : terms.some(term => term.startsWith(keyword)) ? 1 : terms.some(term => term.includes(keyword)) ? 2 : -1;
      return { item, rank };
    })
    .filter(result => result.rank >= 0)
    .sort((a, b) => a.rank - b.rank || a.item.name.localeCompare(b.item.name, 'zh-CN'))
    .map(result => result.item);
}

function receiptItem(id, locationId) {
  return {
    id, received: '', damaged: '', direct: '', destination: '', note: '',
    allocations: locationId ? [{ locationId, quantity: '' }] : [],
  };
}

function warehouseEntryData(state) {
  return {
    items: new Map(state.items.map(item => [item.id, item])),
    positions: new Map(state.positions.map(position => [position.id, position])),
    balances: new Map(state.items.flatMap(item => item.locations.map(location => [issueKey(location.position_id, item.id), location.quantity]))),
  };
}

function inboundImportMarkup() {
  return `<div class="we-import"><div class="we-import-actions">
    <a class="wh-button" href="${esc(portalPath('/api/warehouse/inbound/template.xlsx'))}" download>下载 Excel 模板</a>
    <button type="button" class="wh-button" data-we-import>导入 Excel</button>
    <input type="file" data-we-import-file accept=".xlsx" hidden></div>
    <div class="we-import-status" data-we-import-status aria-live="polite" hidden></div>
  </div>`;
}

function showInboundImportStatus(work) {
  const region = work.root.querySelector('[data-we-import-status]');
  if (!region) return;
  const {message, rows = [], error = false} = work.importStatus || {};
  region.hidden = !message;
  region.dataset.error = String(error);
  region.innerHTML = message ? `<p>${esc(message)}</p>${rows.length ? `<ul>${rows.map(row => `<li>第 ${esc(row.row)} 行 · ${esc(row.message)}</li>`).join('')}</ul>` : ''}` : '';
  work.root.querySelector('[data-we-import]').disabled = Boolean(work.importing);
  work.root.querySelector('[data-we-next]').disabled = Boolean(work.importing) || !work.draft.items.length;
}

async function importInbound(work, file) {
  if (!file || work.outbound || work.importing || work.draft.attempted) return;
  work.importing = true;
  work.importStatus = {message:'正在读取 Excel…'};
  showInboundImportStatus(work);
  const current = () => work.root.isConnected && work.session.view === work && !work.draft.checkout;
  try {
    const form = new FormData();
    form.append('file',file);
    const imported = await work.api('/inbound/import',{method:'POST',body:form});
    if (!current()) return;
    const state = await work.api('/bootstrap');
    if (!current()) return;
    if (work.draft.items.length && work.draft.key !== imported.client_id) {
      work.importStatus = {error:true,message:'本单已有其他物资。请先办完本单或移除已选物资，再导入 Excel。'};
      return;
    }
    const data = warehouseEntryData(state);
    const unavailable = imported.items.some(row => data.items.get(row.item_id)?.active !== true ||
      row.locations.some(place => !data.positions.has(place.position_id)));
    if (unavailable) {
      work.importStatus = {error:true,message:'物资或货位资料刚刚更新，请重新导入 Excel。'};
      return;
    }
    Object.assign(work,data);
    if (work.draft.key !== imported.client_id) {
      work.draft.key = imported.client_id;
      work.draft.photos = {};
      work.draft.versions = {};
    }
    work.draft.items = imported.items.map(row => ({...receiptItem(row.item_id),received:String(row.received),
      allocations:row.locations.map(entry => ({locationId:entry.position_id,quantity:String(entry.quantity)})),
    }));
    work.importStatus = {message:`已填入 ${imported.items.length} 种物资。核对后点「下一步」拍照。`};
    work.message = '';
    work.noticeCode = '';
    void saveWarehouseDraft(work.session);
    draw(work);
  } catch (error) {
    if (!current()) return;
    work.importStatus = {error:true,rows:error.detail?.rows,
      message:error.status ? error.message : '暂时无法读取 Excel，请检查连接后重新导入。'};
  } finally {
    work.importing = false;
    if (current()) showInboundImportStatus(work);
  }
}

function numberedInput(value, attributes, min = 0) {
  return `<input type="number" min="${min}" max="1000000" step="1" inputmode="numeric" value="${esc(value)}" ${attributes}>`;
}

function selectedAllocations(work, item) {
  return work.outbound ? item.allocations : item.allocations.filter(row => row.quantity !== '' && Number(row.quantity) !== 0);
}

function groupsOf(work) {
  if (!work.outbound) return work.draft.items.map(item => ({
    id: String(item.id), title: work.items.get(item.id)?.name || '入库物资',
    lines: selectedAllocations(work, item).map(line => ({ ...line, itemId: item.id })),
  }));
  const groups = new Map();
  for (const item of work.draft.items) {
    for (const line of item.allocations) {
      if (!line.locationId) continue;
      const id = String(line.locationId);
      if (!groups.has(id)) groups.set(id, { id, title: work.positions.get(line.locationId)?.label || '取货层位', lines: [] });
      groups.get(id).lines.push({ ...line, itemId: item.id });
    }
  }
  return [...groups.values()].sort((a, b) => Number(a.id) - Number(b.id));
}

function photoSignature(group, work) {
  const lines = group.lines.map(line => `${line.itemId}:${line.locationId}:${line.quantity}`).sort();
  if (!work.outbound) {
    const item = work.draft.items.find(candidate => String(candidate.id) === group.id);
    lines.push(item?.received, item?.damaged, item?.direct);
  }
  return lines.join(';');
}

function issueKey(positionId, itemId) {
  return `${positionId}:${itemId}`;
}

function presentBalance(work, itemId, locationId) {
  return work.balances.get(issueKey(locationId, itemId)) || 0;
}

function locationsFor(work, item) {
  const itemId = item.id;
  const positions = [...work.positions.values()];
  return positions.sort((a, b) => {
    const availableA = presentBalance(work, itemId, a.id) > 0;
    const availableB = presentBalance(work, itemId, b.id) > 0;
    return Number(availableB) - Number(availableA) || a.shelf.localeCompare(b.shelf) || b.level - a.level;
  });
}

function receiptSummary(item) {
  const shelved = item.allocations.reduce((sum, row) => sum + quantity(row.quantity), 0);
  return { shelved, remaining: quantity(item.received) - quantity(item.damaged) - quantity(item.direct) - shelved };
}

function formIssue(work) {
  if (!work.draft.items.length) return '请先添加物资。';
  for (const item of work.draft.items) {
    const name = work.items.get(item.id)?.name || '物资';
    if (!work.items.has(item.id) || work.items.get(item.id).active === false) return `${name} 的档案已停用，请移除后继续。`;
    const selected = new Set();
    for (const row of selectedAllocations(work, item)) {
      if (!work.positions.has(row.locationId) || !Number.isSafeInteger(Number(row.quantity)) || Number(row.quantity) <= 0 || Number(row.quantity) > 1_000_000) {
        return `请为「${name}」选择实际货位并填写正整数数量。`;
      }
      if (selected.has(row.locationId)) return `「${name}」在同一货位的数量请合并填写。`;
      selected.add(row.locationId);
    }
    if (work.outbound) {
      if (!selected.size) return `请添加「${name}」的取货层位。`;
      continue;
    }
    if (!Number.isSafeInteger(Number(item.received)) || Number(item.received) <= 0 || Number(item.received) > 1_000_000) return `请填写「${name}」的实际收到数量。`;
    if ([item.damaged, item.direct].some(value => !Number.isSafeInteger(Number(value)) || Number(value) < 0)) return `请检查「${name}」的破损和直用数量。`;
    if (quantity(item.damaged) && !item.note.trim()) return `请补充「${name}」的破损说明。`;
    if (quantity(item.direct) && !item.destination.trim()) return `请补充「${name}」的直接使用去向。`;
    if (receiptSummary(item).remaining !== 0) return `请核对「${name}」的实收、破损、上架和直用数量。`;
  }
  return '';
}

function allocationMarkup(work, item, row, index) {
  const options = locationsFor(work, item).map(position => {
    const available = presentBalance(work, item.id, position.id);
    return `<option value="${position.id}" ${position.id === row.locationId ? 'selected' : ''}>${esc(position.label)}${work.outbound ? ` · 余 ${available} ${esc(item.unit)}` : ''}</option>`;
  }).join('');
  return `<div class="we-allocation">
    <label>${work.outbound ? '取货层位' : '上架层位'}<select data-we-location="${index}"><option value="">请选择实际层位</option>${options}</select></label>
    <label>数量（${esc(item.unit)}）${numberedInput(row.quantity, `data-we-allocation="${index}"`, 1)}</label>
    <button class="we-icon" type="button" data-we-remove-allocation="${index}" aria-label="移除这一层">×</button>
  </div>`;
}

function selectedMarkup(work, line) {
  const item = work.items.get(line.id) || { id: line.id, name: '档案已移除', unit: '件' };
  const remaining = receiptSummary(line).remaining;
  const hasExtra = line.damaged || line.direct || line.destination || line.note;
  return `<article class="we-selection" data-we-item="${item.id}">
    <div class="we-card-head"><div><h4>${esc(item.name)}</h4>${item.spec ? `<p>${esc(item.spec)}</p>` : ''}</div><button class="we-text-button" type="button" data-we-remove-item>移除</button></div>
    ${work.outbound ? '' : `<label class="we-received">实际收到（${esc(item.unit)}，含破损）${numberedInput(line.received, 'data-we-field="received"', 1)}</label>`}
    <div class="we-allocations">${line.allocations.map((row, index) => allocationMarkup(work, item, row, index)).join('')}</div>
    <button class="we-text-button" type="button" data-we-add-allocation>＋ ${work.outbound ? '加一个取货层' : '加一个上架层'}</button>
    ${work.outbound ? '' : `<details class="we-extra" ${hasExtra ? 'open' : ''}><summary>到货直用或收货异常</summary>
      <div class="we-extra-fields"><label>直用数量${numberedInput(line.direct, 'data-we-field="direct"')}</label>
      <label>实际去向<input maxlength="120" data-we-field="destination" value="${esc(line.destination)}" placeholder="例：交给当班使用"></label>
      <label>破损数量${numberedInput(line.damaged, 'data-we-field="damaged"')}</label>
      <label>少到、破损等说明<input maxlength="500" data-we-field="note" value="${esc(line.note)}"></label></div>
    </details><p class="we-quantity-check" data-we-receipt-check>${esc(receiptMessage(remaining, item.unit, Boolean(line.received)))}</p>`}
  </article>`;
}

function receiptMessage(remaining, unit, hasReceived) {
  if (!hasReceived) return '填写收到数量，再分配到实际货位或直用。';
  if (remaining === 0) return '实收、上架、直用和破损数量已对齐。';
  return remaining > 0 ? `还有 ${remaining} ${unit} 未分配。` : `分配数量比实收多 ${Math.abs(remaining)} ${unit}。`;
}

function renderMatches(work) {
  const matches = lookupItems([...work.items.values()], work.query, work.category);
  const limited = matches.slice(0, work.more ? 40 : 8);
  const node = work.root.querySelector('[data-we-results]');
  if (!node) return;
  node.innerHTML = limited.map(item => {
    const total = [...work.positions.keys()].reduce((sum, positionId) => sum + presentBalance(work, item.id, positionId), 0);
    const selected = work.draft.items.some(line => line.id === item.id);
    return `<button type="button" class="we-result" data-we-add-item="${item.id}">
      <span><strong>${esc(item.name)}</strong><small>${esc([item.spec, item.category].filter(Boolean).join(' · '))}</small></span>
      <span class="we-result-end"><small>${work.outbound ? `余 ${total} ${esc(item.unit)}` : esc(item.unit)}</small><span>${selected ? '已选' : '＋'}</span></span>
    </button>`;
  }).join('') || '<p class="we-empty">未找到符合条件的物资。可以换个关键词或切回全部类目。</p>';
  if (matches.length > 8 && !work.more) node.insertAdjacentHTML('beforeend', `<button class="we-text-button" type="button" data-we-more>展开结果（${matches.length} 项）</button>`);
  if (matches.length > 40 && work.more) node.insertAdjacentHTML('beforeend', '<p class="we-empty">结果较多，可以继续输入名称缩小范围。</p>');
}

function discrepancyMarkup(work, positionId, line) {
  const item = work.items.get(line.itemId);
  const key = issueKey(positionId, line.itemId);
  const report = work.draft.discrepancies[key];
  const booked = presentBalance(work, line.itemId, line.locationId);
  const predicted = booked - quantity(line.quantity);
  return `<div class="we-check-line" data-we-discrepancy="${key}">
    <div class="we-check-item"><strong>${esc(item?.name)}</strong><span>取 ${esc(line.quantity)} ${esc(item?.unit)} · ${predicted < 0 ? `当前账面仅 ${booked} ${esc(item?.unit)}` : `预计剩 ${predicted} ${esc(item?.unit)}`}</span></div>
    <label class="we-check-label"><input type="checkbox" data-we-toggle-report ${report ? 'checked' : ''}> 实际与库存不符</label>
    <div class="we-extra-fields we-report-inputs" ${report ? '' : 'hidden'}>
      <label>实见数量${numberedInput(report?.observed ?? '', 'data-we-observed placeholder="可选"')}</label>
      <label>说明<input maxlength="500" data-we-report-note placeholder="可选" value="${esc(report?.note || '')}"></label>
    </div>
  </div>`;
}

function incomingLines(work, group) {
  const entry = work.draft.items.find(row => String(row.id) === group.id);
  const unit = work.items.get(entry.id)?.unit;
  return `<div class="we-check-item"><strong>实收 ${esc(entry.received)} ${esc(unit)}</strong><span>上架 ${receiptSummary(entry).shelved} · 直用 ${quantity(entry.direct)} · 破损 ${quantity(entry.damaged)}</span></div>
    ${group.lines.map(row => `<p class="we-check-place">${esc(work.positions.get(row.locationId)?.label)} <strong>＋${esc(row.quantity)} ${esc(unit)}</strong></p>`).join('')}
    ${entry.destination ? `<p class="we-check-place">直用去向 <strong>${esc(entry.destination)}</strong></p>` : ''}`;
}

function evidenceMarkup(work, group) {
  const review = work.draft.review.includes(Number(group.id));
  const label = work.outbound ? `${group.title}取货后的现场` : group.title;
  return `<article class="we-evidence" data-we-group="${group.id}">
    <div class="we-card-head"><div><h4>${esc(group.title)}</h4><p>${work.outbound ? `请拍摄${esc(label)}` : '请拍摄本项实收物资'}</p></div><span data-we-photo-status class="we-photo-status"></span></div>
    <div class="we-photo-row">
      <a data-we-preview-link class="we-photo-preview" target="_blank" rel="noopener" aria-label="查看整张照片 · ${esc(label)}" hidden>
        <img data-we-preview alt="${esc(label)}" hidden>
      </a>
      <span class="we-camera-mark" aria-hidden="true"></span>
      <div class="we-photo-actions"><input type="file" accept="image/*" capture="environment" data-we-camera hidden>
        <button type="button" class="secondary" data-we-pick>拍照</button>
        <button type="button" class="we-text-button" data-we-retry hidden>继续上传</button>
      </div></div>
    <div class="we-check-lines">${work.outbound ? group.lines.map(line => discrepancyMarkup(work, group.id, line)).join('') : incomingLines(work, group)}</div>
    <div class="we-review" data-we-review ${review ? '' : 'hidden'}>
      <p>这一层有新收发。请对照上方最新余量，现场照片仍准确时可继续使用。</p>
      <button type="button" class="secondary" data-we-reviewed>本层已重新核对</button>
    </div>
  </article>`;
}

const photoStates = { compressing: '压缩中', queued: '待上传', uploading: '上传中', failed: '上传失败', uploaded: '已完成', invalid: '请重拍' };

function refreshCheckout(work) {
  if (!work.root.isConnected || !work.draft.checkout) return;
  const groups = groupsOf(work);
  const busy = work.busy || work.session.submitting;
  for (const group of groups) {
    const node = work.root.querySelector(`[data-we-group="${group.id}"]`);
    if (!node) continue;
    const photo = work.draft.photos[group.id];
    const available = Boolean(photo && photo.signature === photoSignature(group, work));
    const status = node.querySelector('[data-we-photo-status]');
    status.textContent = (available && photoStates[photo.status]) || '待拍';
    status.dataset.state = available ? photo.status : 'missing';
    if (available && photo.error) status.title = photo.error;
    else status.removeAttribute('title');
    const pick = node.querySelector('[data-we-pick]');
    pick.textContent = available ? '重拍本项' : '拍照';
    pick.disabled = busy || work.draft.attempted || (available && photo.status === 'compressing');
    const retry = node.querySelector('[data-we-retry]');
    retry.hidden = !available || !photo.blob || photo.status !== 'failed';
    retry.disabled = busy || work.draft.attempted;
    const img = node.querySelector('[data-we-preview]');
    const cached = work.previews.get(group.id);
    if (cached && (!available || cached.blob !== photo.blob)) {
      URL.revokeObjectURL(cached.url);
      work.previews.delete(group.id);
    }
    if (available && photo.blob && !work.previews.has(group.id)) {
      work.previews.set(group.id, { blob: photo.blob, url: URL.createObjectURL(photo.blob) });
    }
    const src = available && (work.previews.get(group.id)?.url || (photo.url && portalPath(photo.url)));
    const previewLink = node.querySelector('[data-we-preview-link]');
    previewLink.hidden = !src;
    img.hidden = !src;
    if (src && img.getAttribute('src') !== src) {
      img.src = src;
      previewLink.href = src;
    }
    if (!src) {
      img.removeAttribute('src');
      previewLink.removeAttribute('href');
    }
    node.querySelector('[data-we-review]').hidden = !work.draft.review.includes(Number(group.id));
    node.querySelectorAll('[data-we-reviewed], [data-we-toggle-report], [data-we-observed], [data-we-report-note]').forEach(input => {
      input.disabled = busy || work.draft.attempted;
    });
  }
  const required = groups.length;
  const ready = groups.filter(group => {
    const photo = work.draft.photos[group.id];
    return photo?.status === 'uploaded' && photo.signature === photoSignature(group, work);
  }).length;
  const confirm = work.root.querySelector('[data-we-confirm]');
  if (!confirm) return;
  confirm.disabled = busy || work.noticeCode === 'INBOUND_BATCH_POSTED' || (!work.draft.attempted && (Boolean(work.draft.review.length) || ready !== required || !required));
  confirm.textContent = busy ? '正在核对…' : work.draft.attempted ? '查询提交结果' : work.noticeCode === 'INSUFFICIENT_STOCK' ? '提交报错并保留原单' : `确认${work.outbound ? '出库' : '入库'}`;
  work.root.querySelector('[data-we-edit]').disabled = busy || work.draft.attempted;
  work.root.querySelector('[data-we-progress]').textContent = `照片已传 ${ready} / ${required}`;
  const progress = work.root.querySelector('[data-we-progress-bar]');
  progress.max = required || 1;
  progress.value = ready;
  const message = work.root.querySelector('[data-we-notice]');
  message.textContent = work.message || (work.session.storageError ? '浏览器暂时无法保存本机照片，请保留此页面直到上传完成。' : '');
  message.hidden = !message.textContent;
  if (work.session.storageError) message.dataset.kind = 'warn';
  else message.dataset.kind = work.noticeCode ? 'warn' : 'info';
}

function draw(work) {
  if (!work.root.isConnected) return;
  const outbound = work.outbound;
  const title = outbound ? '出库' : '入库';
  const groups = groupsOf(work);
  const categories = [...new Set([...work.items.values()].map(item => item.category).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'zh-CN'));
  work.root.innerHTML = `<section class="we-page" data-direction="${outbound ? 'out' : 'in'}" aria-label="${title}登记">
    <header class="we-heading">
      ${work.onBack ? '<button type="button" class="we-back" data-we-back>‹ 物资管理</button>' : ''}
      <h2 tabindex="-1">${work.draft.checkout ? `登记${title}` : title}</h2>
      <ol class="we-steps" aria-label="登记进度"><li ${work.draft.checkout ? 'class="is-complete"' : 'aria-current="step"'}>物资和数量</li><li ${work.draft.checkout ? 'aria-current="step"' : ''}>拍照与确认</li></ol>
    </header>
    ${work.draft.checkout ? `<div class="we-identity"><span>经办人 <strong>${esc(work.operator.name)}</strong></span><span>登记时间 <strong>${esc(formatTime())}</strong></span></div>
      <p role="status" class="we-notice" data-we-notice hidden></p>
      <div class="we-checkout">${groups.map(group => evidenceMarkup(work, group)).join('')}</div>
      <div class="we-footer"><button type="button" class="secondary" data-we-edit>返回修改</button><div class="we-progress-block"><span data-we-progress role="status"></span><progress data-we-progress-bar aria-label="照片上传进度" max="1" value="0"></progress></div><button type="button" class="primary" data-we-confirm></button></div>` : `
      <div class="we-entry-grid"><section class="we-lookup" aria-label="查找物资">
        ${outbound ? '' : inboundImportMarkup()}
        <div class="we-search"><label>物资搜索 <input type="search" data-we-search value="${esc(work.query)}" placeholder="名称或常用简称" autocomplete="off"></label>
        <label>类目<select data-we-category><option value="">全部类目</option>${categories.map(category => `<option ${work.category === category ? 'selected' : ''} value="${esc(category)}">${esc(category)}</option>`).join('')}</select></label></div>
        <div class="we-results" data-we-results></div>
      </section><section class="we-order" aria-label="本单物资"><div class="we-order-head"><h4>本单物资</h4><span>${work.draft.items.length} 项</span></div>
        ${work.draft.items.map(line => selectedMarkup(work, line)).join('') || '<p class="we-empty">从搜索结果选择要办理的物资。</p>'}
      </section></div>
      <p role="status" class="we-notice" data-we-notice ${work.message || work.session.storageError ? '' : 'hidden'}>${esc(work.message || (work.session.storageError ? '当前浏览器暂时无法保存在本机续办的记录。' : ''))}</p>
      <div class="we-footer"><span class="we-selected-total">${work.draft.items.length ? `已选 <strong>${work.draft.items.length}</strong> 项` : '选好物资后继续'}</span><button type="button" class="primary" data-we-next ${work.draft.items.length ? '' : 'disabled'}>下一步</button></div>`}
  </section>`;
  if (work.draft.checkout) refreshCheckout(work);
  else {
    renderMatches(work);
    if (!outbound) showInboundImportStatus(work);
  }
}

function loadChanges(work, detail) {
  for (const balance of detail.balances || []) work.balances.set(issueKey(balance.position_id, balance.item_id), balance.quantity);
  for (const change of detail.positions || []) {
    const current = work.positions.get(change.id);
    if (current) work.positions.set(change.id, { ...current, ...change });
  }
  if (detail.code === 'STALE_POSITIONS' && work.outbound) {
    work.draft.review = [...new Set([...work.draft.review, ...(detail.positions || []).map(position => position.id)])];
  }
}

function confirmLayer(work, id) {
  work.draft.versions[id] = work.positions.get(id)?.version;
  work.draft.review = work.draft.review.filter(positionId => positionId !== id);
  if (!work.draft.review.length && work.noticeCode === 'STALE_POSITIONS') {
    work.noticeCode = '';
    work.message = '';
  }
  void saveWarehouseDraft(work.session);
  refreshCheckout(work);
}

async function uploadQueue(work) {
  if (work.session.uploading || navigator.onLine === false) return;
  work.session.uploading = true;
  try {
    for (;;) {
      const entry = Object.entries(work.draft.photos).find(([, photo]) => photo.status === 'queued' && photo.blob);
      if (!entry || navigator.onLine === false) break;
      const [key, photo] = entry;
      photo.status = 'uploading';
      void saveWarehouseDraft(work.session);
      work.session.refresh?.();
      try {
        const body = new FormData();
        body.append('file', photo.blob, 'warehouse.jpg');
        body.append('captured_at', photo.capturedAt);
        const result = await work.api('/photos', { method: 'POST', body });
        if (work.draft.photos[key] !== photo) continue;
        photo.status = 'uploaded';
        photo.id = result.id;
        photo.url = result.url;
        delete photo.error;
      } catch (error) {
        if (work.draft.photos[key] !== photo) continue;
        photo.status = 'failed';
        photo.error = error.message;
      }
      void saveWarehouseDraft(work.session);
      work.session.refresh?.();
    }
  } finally {
    work.session.uploading = false;
  }
}

async function usePhoto(work, group, file) {
  const former = work.draft.photos[group.id];
  if (!file || former?.status === 'compressing') return;
  const photo = { capturedAt: new Date().toISOString(), signature: photoSignature(group, work), status: 'compressing' };
  work.draft.photos[group.id] = photo;
  work.session.refresh?.();
  try {
    const blob = await compressWarehousePhoto(file);
    if (work.draft.photos[group.id] !== photo) return;
    photo.blob = blob;
    photo.status = 'queued';
    if (work.noticeCode === 'PHOTO_ERROR') { work.noticeCode = ''; work.message = ''; }
    void saveWarehouseDraft(work.session);
    if (work.outbound && work.draft.review.includes(Number(group.id))) confirmLayer(work, Number(group.id));
    const next = groupsOf(work).find(entry => entry.id !== group.id && !work.draft.photos[entry.id]);
    if (next && work.root.isConnected) {
      const button = work.root.querySelector(`[data-we-group="${next.id}"] [data-we-pick]`);
      button?.focus({ preventScroll: true });
      button?.scrollIntoView({ block: 'nearest' });
    }
    void uploadQueue(work);
  } catch (error) {
    if (work.draft.photos[group.id] === photo) {
      if (former?.status === 'uploaded') work.draft.photos[group.id] = former;
      else Object.assign(photo, { status: 'invalid', error: error.message });
      work.message = error.message;
      work.noticeCode = 'PHOTO_ERROR';
      void saveWarehouseDraft(work.session);
    }
  }
  work.session.refresh?.();
}

function makePayload(work) {
  const lines = work.draft.items.flatMap(item => selectedAllocations(work, item).map(row => ({ item_id: item.id, position_id: row.locationId, quantity: Number(row.quantity) })));
  const receipts = work.outbound ? [] : work.draft.items.map(item => ({
    item_id: item.id, received: Number(item.received), damaged: quantity(item.damaged), direct_quantity: quantity(item.direct),
    direct_destination: item.destination.trim(), note: item.note.trim(),
  }));
  const photos = groupsOf(work).map(group => ({
    photo_id: work.draft.photos[group.id].id,
    [work.outbound ? 'position_id' : 'item_id']: Number(group.id),
  }));
  const discrepancies = Object.entries(work.draft.discrepancies).map(([key, report]) => {
    const [position_id, item_id] = key.split(':').map(Number);
    return { position_id, item_id, observed_quantity: report.observed === '' ? null : Number(report.observed), note: report.note.trim() };
  });
  return {
    client_id: work.draft.key, kind: work.outbound ? 'outbound' : 'inbound', position_versions: work.draft.versions,
    lines, receipts, photos, discrepancies, hold_if_insufficient: work.noticeCode === 'INSUFFICIENT_STOCK',
  };
}

async function complete(work, result) {
  if (work.session.draft !== work.draft) return;
  work = currentView(work);
  await clearWarehouseDraft(work.session);
  const pending = result.status === 'pending';
  const title = pending ? '已提交差异，等待核对' : work.outbound ? '出库已确认' : '入库已确认';
  if (work.root.isConnected) {
    const balances = (result.balances || []).map(balance => {
      const item = work.items.get(balance.item_id);
      const position = work.positions.get(balance.position_id);
      return `<li><span>${esc(item?.name)} · ${esc(position?.label)}</span><strong>余 ${esc(balance.quantity)} ${esc(item?.unit)}</strong></li>`;
    }).join('');
    work.root.innerHTML = `<section class="we-result-page" role="status"><span class="we-complete-mark" data-pending="${pending}">${pending ? '!' : '✓'}</span>
      <h3>${title}</h3><p>单据 ${esc(result.id)} · ${esc(result.actor_name || work.operator.name)}${result.posted_at ? ` · ${esc(formatTime(result.posted_at))}` : ''}</p>
      ${pending ? '<p>照片和原出库记录已保留，负责人核实后统一补记。</p>' : balances ? `<ul class="we-result-balances">${balances}</ul>` : ''}
      <button type="button" class="primary" data-we-complete>返回物资管理</button></section>`;
    work.root.querySelector('[data-we-complete]').onclick = () => work.onComplete?.(result);
  }
}

function currentView(work) {
  const current = work.session.view;
  return current?.draft === work.draft && current.root.isConnected ? current : work;
}

async function checkOutcome(work) {
  try {
    const result = await work.api(`/transactions/by-client/${encodeURIComponent(work.draft.key)}`);
    if (result?.id) {
      await complete(work, result);
      return true;
    }
  } catch (error) {
    if (error.status !== 404) {
      work.message = '还无法核对提交结果，请恢复连接后查询原单。';
      work.noticeCode = 'RESULT_UNKNOWN';
      return false;
    }
  }
  work.draft.attempted = false;
  void saveWarehouseDraft(work.session);
  return false;
}

async function submit(work) {
  if (work.busy || work.session.submitting) return;
  if (!work.draft.attempted) {
    const invalid = Object.values(work.draft.discrepancies).some(report =>
      (report.observed === '' && !report.note.trim()) || (report.observed !== '' && (!Number.isSafeInteger(Number(report.observed)) || Number(report.observed) < 0 || Number(report.observed) > 1_000_000)));
    if (invalid) {
      work.noticeCode = 'DISCREPANCY_DETAILS';
      work.message = '请为每条账实差异填写实见数量或说明。';
      refreshCheckout(work);
      return;
    }
  }
  work.session.submitting = true;
  work.busy = true;
  refreshCheckout(work);
  if (work.draft.attempted) {
    try {
      if (!(await checkOutcome(work)) && !work.draft.attempted) {
        work.message = '已核对原单状态，保留本单内容，可以继续确认。';
        work.noticeCode = '';
      }
    } finally {
      work.busy = false;
      work.session.submitting = false;
      work.session.refresh?.();
    }
    return;
  }
  const payload = makePayload(work);
  work.draft.attempted = true;
  await saveWarehouseDraft(work.session);
  try {
    const result = await work.api('/transactions', json('POST', payload));
    await complete(work, result);
  } catch (error) {
    const active = currentView(work);
    const detail = error.detail || {};
    if (error.status && error.status < 500) {
      work.draft.attempted = false;
      active.noticeCode = detail.code || 'SUBMIT_ERROR';
      active.message = error.message;
      if (detail.code === 'STALE_POSITIONS' || detail.code === 'INSUFFICIENT_STOCK') loadChanges(active, detail);
      if (detail.code === 'INSUFFICIENT_STOCK') active.message = `${error.message} 已实际取走的物资可随本单报错，交负责人补记。`;
      if (detail.code === 'PHOTO_UNAVAILABLE') {
        const photo = Object.values(work.draft.photos).find(value => value.id === detail.photo_id);
        if (photo) {
          photo.status = photo.blob ? 'queued' : 'invalid';
          delete photo.id;
          delete photo.url;
          if (photo.blob) {
            active.message = '该张照片旧的上传凭证已失效，正在续传原照片。';
            void uploadQueue(active);
          }
        }
      }
      void saveWarehouseDraft(work.session);
      draw(active);
    } else if (!(await checkOutcome(active)) && !work.draft.attempted) {
      active.message = '尚未查到本单，填写内容及照片已保留，可以重试原单。';
      active.noticeCode = 'RESULT_UNKNOWN';
    }
  } finally {
    work.busy = false;
    work.session.submitting = false;
    work.session.refresh?.();
  }
}

export async function renderWarehouseEntry(root, { mode, state, api, onComplete, onBack, prefill }) {
  mounted.get(root)?.abort();
  const controller = new AbortController();
  mounted.set(root, controller);
  const { signal } = controller;
  const outbound = mode === 'out';
  if (!(outbound ? state.permissions.dispatch : state.permissions.manage_warehouse)) {
    root.innerHTML = `<section class="we-page"><p class="we-empty">当前账号没有${outbound ? '出库' : '入库'}办理权限。</p></section>`;
    return;
  }
  const session = await warehouseDraft(state.operator.id, mode);
  if (!root.isConnected || signal.aborted) return;
  const work = {
    root, outbound, api, onComplete, onBack, session, draft: session.draft, operator: state.operator,
    ...warehouseEntryData(state),
    previews: new Map(), query: '', category: '', message: '', noticeCode: '', busy: false,
  };
  session.view = work;
  session.refresh = () => refreshCheckout(work);
  if (prefill && !work.draft.attempted && work.items.has(Number(prefill.itemId))) {
    const id = Number(prefill.itemId);
    const existing = work.draft.items.find(item => item.id === id);
    const place = Number(prefill.locationId);
    if (!existing || (place && !existing.allocations.some(row => row.locationId === place))) {
      if (!existing) work.draft.items.push(receiptItem(id, place));
      else existing.allocations.push({ locationId: place, quantity: '' });
      work.draft.checkout = false;
      void saveWarehouseDraft(session);
    }
  }
  if (work.draft.checkout && !work.draft.attempted && outbound) {
    for (const group of groupsOf(work)) {
      const id = Number(group.id);
      if (work.draft.versions[id] !== work.positions.get(id)?.version && !work.draft.review.includes(id)) work.draft.review.push(id);
    }
  }
  draw(work);
  if (work.draft.attempted && !session.submitting) {
    work.busy = true;
    refreshCheckout(work);
    const completed = await checkOutcome(work);
    work.busy = false;
    if (completed) return;
    refreshCheckout(work);
  }
  const resumeUploads = () => {
    if (!root.isConnected) return;
    for (const photo of Object.values(work.draft.photos)) {
      if (photo.blob && photo.status === 'failed') photo.status = 'queued';
      if (photo.blob && photo.status === 'uploading' && !session.uploading) photo.status = 'queued';
    }
    void uploadQueue(work);
  };
  resumeUploads();
  window.addEventListener('online', resumeUploads, { signal });
  const lifecycle = new MutationObserver(() => {
    if (!root.isConnected) controller.abort();
  });
  signal.addEventListener('abort', () => {
    lifecycle.disconnect();
    work.previews.forEach(value => URL.revokeObjectURL(value.url));
    work.previews.clear();
    if (session.view === work) { session.view = null; session.refresh = null; }
  });
  lifecycle.observe(document.body, { childList: true, subtree: true });

  root.addEventListener('input', event => {
    const target = event.target;
    if (target.matches('[data-we-search]')) {
      work.query = target.value;
      work.more = false;
      renderMatches(work);
      return;
    }
    const itemNode = target.closest('[data-we-item]');
    if (itemNode) {
      const item = work.draft.items.find(row => row.id === Number(itemNode.dataset.weItem));
      if (target.matches('[data-we-field]')) item[target.dataset.weField] = target.value;
      if (target.matches('[data-we-allocation]')) item.allocations[Number(target.dataset.weAllocation)].quantity = target.value;
      if (!outbound) itemNode.querySelector('[data-we-receipt-check]').textContent = receiptMessage(receiptSummary(item).remaining, work.items.get(item.id)?.unit, Boolean(item.received));
      void saveWarehouseDraft(session);
    }
    const reportNode = target.closest('[data-we-discrepancy]');
    const report = reportNode && work.draft.discrepancies[reportNode.dataset.weDiscrepancy];
    if (report && target.matches('[data-we-observed], [data-we-report-note]')) {
      report[target.matches('[data-we-observed]') ? 'observed' : 'note'] = target.value;
      void saveWarehouseDraft(session);
    }
  }, { signal });

  root.addEventListener('change', event => {
    const target = event.target;
    if (target.matches('[data-we-category]')) {
      work.category = target.value;
      work.more = false;
      renderMatches(work);
    } else if (target.matches('[data-we-location]')) {
      const parent = target.closest('[data-we-item]');
      work.draft.items.find(item => item.id === Number(parent.dataset.weItem)).allocations[Number(target.dataset.weLocation)].locationId = Number(target.value);
      void saveWarehouseDraft(session);
    } else if (target.matches('[data-we-import-file]')) {
      const file = target.files?.[0];
      target.value = '';
      void importInbound(work,file);
    } else if (target.matches('[data-we-camera]')) {
      const id = target.closest('[data-we-group]').dataset.weGroup;
      const file = target.files?.[0] || target.files?.item?.(0);
      target.value = '';
      const group = groupsOf(work).find(entry => entry.id === id);
      if (group) void usePhoto(work, group, file);
    } else if (target.matches('[data-we-toggle-report]')) {
      const parent = target.closest('[data-we-discrepancy]');
      const key = parent.dataset.weDiscrepancy;
      if (target.checked) work.draft.discrepancies[key] = { observed: '', note: '' };
      else delete work.draft.discrepancies[key];
      parent.querySelector('.we-report-inputs').hidden = !target.checked;
      if (target.checked) parent.querySelectorAll('[data-we-observed], [data-we-report-note]').forEach(input => { input.value = ''; });
      void saveWarehouseDraft(session);
    }
  }, { signal });

  root.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button || button.disabled) return;
    if (button.matches('[data-we-back]')) { onBack?.(); return; }
    if (button.matches('[data-we-import]')) { root.querySelector('[data-we-import-file]').click(); return; }
    if (button.matches('[data-we-confirm]')) { void submit(work); return; }
    const groupNode = button.closest('[data-we-group]');
    if (groupNode) {
      const id = groupNode.dataset.weGroup;
      if (button.matches('[data-we-pick]')) groupNode.querySelector('[data-we-camera]').click();
      if (button.matches('[data-we-retry]') && work.draft.photos[id]?.blob) {
        work.draft.photos[id].status = 'queued';
        void uploadQueue(work);
      }
      if (button.matches('[data-we-reviewed]')) confirmLayer(work, Number(id));
      return;
    }
    if (button.matches('[data-we-more]')) { work.more = true; renderMatches(work); return; }
    const itemNode = button.closest('[data-we-item]');
    const item = itemNode && work.draft.items.find(row => row.id === Number(itemNode.dataset.weItem));
    let focusItem;
    if (button.matches('[data-we-add-item]')) {
      const id = Number(button.dataset.weAddItem);
      if (!work.draft.items.some(row => row.id === id)) {
        const only = locationsFor(work, work.items.get(id)).filter(position => presentBalance(work, id, position.id) > 0);
        work.draft.items.push(receiptItem(id, only.length === 1 ? only[0].id : null));
      }
      const row = work.draft.items.find(entry => entry.id === id);
      if (!row.allocations.length) row.allocations.push({ locationId: 0, quantity: '' });
      focusItem = id;
    } else if (button.matches('[data-we-remove-item]')) {
      work.draft.items = work.draft.items.filter(row => row !== item);
      if (!work.draft.items.length && work.draft.key.startsWith(inboundBatchPrefix)) {
        work.draft.key = newSubmissionKey();
        work.draft.photos = {};
        work.importStatus = null;
      }
    }
    else if (button.matches('[data-we-add-allocation]')) item.allocations.push({ locationId: 0, quantity: '' });
    else if (button.matches('[data-we-remove-allocation]')) item.allocations.splice(Number(button.dataset.weRemoveAllocation), 1);
    else if (button.matches('[data-we-edit]')) work.draft.checkout = false;
    else if (button.matches('[data-we-next]')) {
      const issue = formIssue(work);
      if (issue) { work.message = issue; draw(work); return; }
      work.draft.checkout = true;
      const groups = groupsOf(work);
      const currentLocations = new Set(work.draft.items.flatMap(entry => selectedAllocations(work, entry).map(line => line.locationId)));
      const validDiscrepancies = new Set(work.draft.items.flatMap(entry => selectedAllocations(work, entry).map(line => issueKey(line.locationId, entry.id))));
      for (const key of Object.keys(work.draft.versions)) if (!currentLocations.has(Number(key))) delete work.draft.versions[key];
      for (const key of Object.keys(work.draft.discrepancies)) if (!validDiscrepancies.has(key)) delete work.draft.discrepancies[key];
      work.draft.review = work.draft.review.filter(id => currentLocations.has(id));
      for (const id of currentLocations) if (work.draft.versions[id] === undefined) work.draft.versions[id] = work.positions.get(id)?.version;
      for (const [key, photo] of Object.entries(work.draft.photos)) {
        const group = groups.find(entry => entry.id === key);
        if (!group || photo.signature !== photoSignature(group, work)) delete work.draft.photos[key];
      }
    } else return;
    work.message = '';
    work.noticeCode = '';
    void saveWarehouseDraft(session);
    draw(work);
    if (button.matches('[data-we-next], [data-we-edit]')) {
      root.closest('.app-shell')?.scrollTo(0, 0);
      window.scrollTo(0, 0);
      root.querySelector('.we-heading h2')?.focus({preventScroll:true});
    }
    if (focusItem) root.querySelector(`[data-we-item="${focusItem}"] select, [data-we-item="${focusItem}"] [data-we-field="received"]`)?.focus();
  }, { signal });
  return () => controller.abort();
}
