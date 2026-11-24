import {esc} from './format.js';

const searchKey = value => String(value ?? '').normalize('NFKC').toLowerCase().replace(/[\s·_-]+/g,'');
const catalogKey = value => String(value ?? '').trim().replace(/\s+/g,' ').normalize('NFKC').toLowerCase();
const names = item => [item.name,...(item.aliases || [])];

export function warehouseMatches(items, word, category) {
  const term = searchKey(word);
  return items.filter(item => item.id > 0 && item.active !== false && (!category || item.category === category))
    .map(item => {
      const labels = names(item).map(searchKey);
      const terms = [...labels,searchKey(item.spec)];
      const rank = !term || labels.includes(term) ? 0 : terms.some(value => value.startsWith(term)) ? 1
        : terms.some(value => value.includes(term)) ? 2 : -1;
      return {item,rank};
    })
    .filter(row => row.rank >= 0)
    .sort((a,b) => a.rank - b.rank || a.item.name.localeCompare(b.item.name,'zh-CN'))
    .map(row => row.item);
}

export function exactWarehouseName(item, word) {
  return names(item).some(value => catalogKey(value) === catalogKey(word));
}

function oneEditApart(a,b) {
  if (a.length > b.length) [a,b] = [b,a];
  if (b.length - a.length > 1) return false;
  let index = 0;
  while (index < a.length && a[index] === b[index]) index += 1;
  return a.slice(index + Number(a.length === b.length)) === b.slice(index + 1);
}

export function receivingSuggestions(items, word) {
  const term = searchKey(word);
  if (!term) return [];
  const matches = warehouseMatches(items,word);
  if (matches.length || term.length < 2) return matches;
  return warehouseMatches(items,'').filter(item => names(item).some(value => oneEditApart(term,searchKey(value))));
}

export function warehouseSuggestionButtons(items, attribute) {
  return items.slice(0,8).map(item => `<button type="button" class="wh-name-option" ${attribute}="${esc(item.id)}">
    <strong>${esc(item.name)}</strong><small>${[item.spec,item.category,item.unit].filter(Boolean).map(esc).join(' · ')}</small></button>`).join('');
}
