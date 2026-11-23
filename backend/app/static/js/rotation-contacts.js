'use strict';

const syncRotationContacts = (() => {
  const trigger = document.getElementById('rtContactsTrigger');
  const panel = document.getElementById('rtContacts');
  const top = panel.parentElement;
  const identity = document.getElementById('rtWho');
  const clock = document.getElementById('rtClock');
  const content = document.getElementById('rtContactsContent');
  const status = document.getElementById('rtContactsStatus');
  const close = panel.querySelector('[data-contacts-close]');
  const request = (options = {}) => rtApi('/api/rotation-board/contacts', {
    cache: 'no-store', preserveBoardOnForbidden: true, ...options,
  });
  let directory = null;
  let draft = null;
  let saving = false;
  let generation = 0;

  function place() {
    if (trigger.hidden) return;
    const header = top.getBoundingClientRect();
    const who = identity.getBoundingClientRect();
    const time = clock.getBoundingClientRect();
    const sameRow = who.bottom > time.top && who.top < time.bottom;
    const inGap = sameRow && time.left - who.right >= trigger.offsetWidth + 24;
    top.classList.toggle('rt-contacts-in-gap', inGap);
    top.style.setProperty('--rt-contacts-trigger-x', `${who.right - header.left + 12}px`);
    top.style.setProperty('--rt-contacts-trigger-y', `${who.top - header.top + who.height / 2}px`);
    if (panel.hidden) return;
    const left = trigger.getBoundingClientRect().left - header.left;
    top.style.setProperty('--rt-contacts-panel-x', `${Math.max(12, Math.min(left, header.width - panel.offsetWidth - 12))}px`);
  }

  const observe = new ResizeObserver(place);
  [top, ...top.children].filter(element => element !== panel)
    .forEach(element => observe.observe(element));

  function render() {
    const items = draft || directory?.items;
    if (!items) {
      content.replaceChildren();
      return;
    }
    const cards = items.map((item, index) => {
      const name = esc(item.name);
      const controls = !draft ? '' : `<div class="rt-contact-actions">
        <button type="button" class="secondary" data-contact-action="up" aria-label="上移 ${name}" ${index > 0 && items[index - 1].pinned === item.pinned ? '' : 'disabled'}>↑</button>
        <button type="button" class="secondary" data-contact-action="down" aria-label="下移 ${name}" ${items[index + 1]?.pinned === item.pinned ? '' : 'disabled'}>↓</button>
        <button type="button" class="secondary" data-contact-action="pin" aria-label="${item.pinned ? '取消置顶' : '置顶'} ${name}">${item.pinned ? '取消置顶' : '置顶'}</button>
      </div>`;
      return `<li data-contact-id="${item.id}">
        <span class="rt-contact-initial" aria-hidden="true">${esc([...item.name][0] || '')}</span>
        <strong>${name}</strong>${item.pinned ? '<small>优先联系人</small>' : ''}${controls}
      </li>`;
    }).join('');
    content.innerHTML = `${items.length
      ? `<ul class="rt-contact-list${draft ? ' editing' : ''}">${cards}</ul>`
      : '<p class="rt-contact-empty">暂无热力追踪培训师</p>'}
      <footer><span>共 ${items.length} 人</span>${draft
        ? '<button type="button" class="secondary" data-contact-action="cancel">取消</button><button type="button" class="primary" data-contact-action="save">保存</button>'
        : directory.can_edit && items.length ? '<button type="button" class="secondary" data-contact-action="edit">编辑</button>' : ''}</footer>`;
  }

  async function load(message = '') {
    const current = ++generation;
    directory = draft = null;
    render();
    status.textContent = message || '正在读取名单…';
    try {
      const result = await request();
      if (current !== generation) return;
      directory = result;
      render();
      status.textContent = message;
    } catch (error) {
      if (current === generation) status.textContent = error.message;
    }
  }

  function setOpen(open, restoreFocus = false) {
    panel.hidden = !open;
    trigger.setAttribute('aria-expanded', String(open));
    if (open) {
      place();
      void load();
      close.focus({preventScroll: true});
    } else {
      ++generation;
      saving = false;
      panel.removeAttribute('aria-busy');
      if (restoreFocus) trigger.focus({preventScroll: true});
    }
  }

  async function save() {
    const current = generation;
    saving = true;
    panel.setAttribute('aria-busy', 'true');
    content.querySelectorAll('button').forEach(button => { button.disabled = true; });
    status.textContent = '正在保存…';
    try {
      const result = await request({method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
        revision: directory.revision,
        order: draft.map(item => item.id),
        pinned: draft.filter(item => item.pinned).map(item => item.id),
      })});
      if (current !== generation) return;
      directory = result;
      draft = null;
      render();
      status.textContent = '已保存联系人顺序';
      content.querySelector('[data-contact-action="edit"]')?.focus();
    } catch (error) {
      if (current !== generation) return;
      if (error.status === 409 || error.status === 403) {
        saving = false;
        panel.removeAttribute('aria-busy');
        await load(error.message);
      } else {
        render();
        status.textContent = error.message;
      }
    } finally {
      if (current === generation) {
        saving = false;
        panel.removeAttribute('aria-busy');
      }
    }
  }

  content.addEventListener('click', event => {
    const button = event.target.closest('[data-contact-action]');
    if (!button || saving) return;
    const action = button.dataset.contactAction;
    if (action === 'edit') {
      draft = directory.items.map(item => ({...item}));
      status.textContent = '';
      render();
      content.querySelector('[data-contact-action="pin"]')?.focus();
    } else if (action === 'cancel') {
      draft = null;
      render();
      content.querySelector('[data-contact-action="edit"]')?.focus();
    } else if (action === 'save') {
      void save();
    } else if (draft) {
      const id = Number(button.closest('[data-contact-id]').dataset.contactId);
      const index = draft.findIndex(item => item.id === id);
      const item = draft[index];
      if (action === 'pin') {
        item.pinned = !item.pinned;
        draft.splice(index, 1);
        const firstUnpinned = draft.findIndex(contact => !contact.pinned);
        draft.splice(firstUnpinned < 0 ? draft.length : firstUnpinned, 0, item);
      } else {
        const next = index + (action === 'up' ? -1 : 1);
        [draft[index], draft[next]] = [draft[next], draft[index]];
      }
      render();
      const row = content.querySelector(`[data-contact-id="${id}"]`);
      (row.querySelector(`[data-contact-action="${action}"]:enabled`) || row.querySelector('[data-contact-action="pin"]')).focus();
    }
  });
  trigger.addEventListener('click', () => { if (!saving) setOpen(panel.hidden, true); });
  close.addEventListener('click', () => { if (!saving) setOpen(false, true); });
  document.addEventListener('click', event => {
    const path = event.composedPath();
    if (!panel.hidden && !saving && !path.includes(panel) && !path.includes(trigger)) setOpen(false);
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !panel.hidden && !saving && !RT.modalOpen) setOpen(false, true);
  });

  return visible => {
    if (!visible && !panel.hidden) setOpen(false);
    trigger.hidden = !visible;
    top.classList.toggle('rt-has-contacts', Boolean(visible));
    if (visible) place();
    else top.classList.remove('rt-contacts-in-gap');
  };
})();
