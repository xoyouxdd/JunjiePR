const assert = require('node:assert/strict');
const {loadAppModules} = require('./support/esm-loader.cjs');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
const tick = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
const hrData = {items: [{employee_id: 7, login_account: 'HR1', name: 'HR甲', attraction_name: '热力追踪', account_enabled: true, password_status: '已修改'}]};
const resetResult = {employee_name: 'HR甲', temporary_password: '4321', message: '原会话已失效'};

async function harness(tab = 'circleHrAccounts') {
  const calls = [], notices = [], elements = new Map();
  const button = {dataset: {circleHrReset: '7', circleHrName: 'HR甲'}};
  const reviewButton = {dataset: {review: '9', action: 'reject'}};
  const withdrawButton = {dataset: {entryWithdraw: '9'}, hasAttribute: () => false};
  function addElements(markup) {
    for (const id of markup.matchAll(/id="([^"]+)"/g)) {
      elements.set(id[1], {
        set innerHTML(value) { this.markup = value; addElements(value); },
        get innerHTML() { return this.markup || ''; },
        isConnected: true,
        addEventListener() {},
      });
    }
  }
  const app = {
    _html: '',
    get innerHTML() { return this._html; },
    set innerHTML(value) {
      this._html = value;
      for (const element of elements.values()) element.isConnected = false;
      elements.clear();
      addElements(value);
    },
    contains(node) { return node?.isConnected === true; },
    querySelectorAll(selector) {
      if (selector === '[data-circle-hr-reset]' && this._html.includes('circleHrResetResult')) return [button];
      if (selector === '[data-review]' && this._html.includes('data-review="9"')) return [reviewButton];
      if (selector === '[data-review="9"]' && this._html.includes('data-review="9"')) return [reviewButton];
      return [];
    }
  };
  const context = {
    AbortController, URLSearchParams, FormData: class extends FormData {constructor() {super();}}, console, setTimeout, clearTimeout,
    location: {pathname: '/', search: ''},
    window: {matchMedia: () => ({matches: false, addEventListener() {}, removeEventListener() {}})},
    document: {
      getElementById: id => id === 'app' ? app : id === 'tabs' ? {innerHTML: ''} : elements.get(id),
      querySelector: () => null,
      querySelectorAll: selector => selector === '[data-entry-withdraw]' && elements.get('entryResults')?.innerHTML.includes('data-entry-withdraw="9"') ? [withdrawButton] : [],
    },
    confirmModal: async () => true, promptModal: async () => '原因',
    toast: (...args) => notices.push(args),
    fetch(url, options) {
      const completion = deferred();
      calls.push({url, options, completion});
      // Deliberately ignore abort: ownership checks must also cover an already-delivered response.
      return completion.promise;
    }
  };
  const noop = () => {};
  const mocks = {
    'dialogs.js': {confirmModal: (...args) => context.confirmModal(...args), promptModal: (...args) => context.promptModal(...args), bindDialogLayer: noop, formModal: noop, showSaveWarnings: noop, selectModal: noop},
    'files.js': {attachmentControl: noop, bindFilePreviews: noop, imagePreviewButton: noop},
    'navigation.js': {gotoTab: noop, menuItems: noop, refreshActionBadge: noop, renderTabs: noop},
    'recognition-grid.js': {mountRecognitionGrid: noop},
    'toast.js': {toast: context.toast},
  };
  const names = ['context.js', 'state.js', 'accounts.js', 'hr.js', 'hr-employees.js', 'reviews.js', 'entries.js'];
  Object.assign(context, ...Object.values(await loadAppModules(names, context, mocks)));
  Object.assign(context.state, {tab, me: {role_code: 'SYSTEM_ADMIN', permissions: []}, options: {}});
  context.configureViews({
    circleHrAccounts: context.renderCircleHrAccounts, logs: context.renderLogs,
    hrEmployees: context.renderHrEmployees, circleTransfers: context.renderCircleTransfers,
    review: context.renderFrontlineReview, supervisorReview: context.renderSupervisorReview,
    home: () => {}, entries: context.renderEntries,
  });
  const reply = (call, value, status = 200) => call.completion.resolve({status, ok: status >= 200 && status < 300, text: async () => JSON.stringify(value)});
  const navigate = async nextTab => {
    context.state.tab = nextTab;
    const pending = context.render();
    await tick();
    return {pending, call: calls.at(-1)};
  };
  const requestContext = () => context.captureViewContext();
  return {context, app, calls, notices, button, reviewButton, withdrawButton, reply, navigate, requestContext};
}

async function initialHr(h) {
  const pending = h.context.render();
  h.reply(h.calls[0], hrData);
  await pending;
  assert.equal(typeof h.button.onclick, 'function');
}

async function postSurvivesNavigation() {
  const h = await harness();
  await initialHr(h);
  const mutation = h.button.onclick();
  await tick();
  const post = h.calls[1];
  assert.equal(post.options.method, 'POST');
  assert.equal(post.options.signal, undefined, 'Writes must complete even after the page is left');
  const logs = await h.navigate('logs');
  const currentId = h.requestContext();
  assert.equal(logs.call.options.signal.aborted, false, 'The destination read remains active');
  h.reply(post, resetResult);
  await mutation;
  assert.equal(h.calls.length, 3, 'Old POST completion must not reload its former page');
  assert(currentId.isCurrent(), 'Old callback must not invalidate the new page request');
  assert(h.app.innerHTML.includes('正在加载'));
  h.reply(logs.call, []);
  await logs.pending;
  assert(h.app.innerHTML.includes('审计日志'));
  assert(!h.app.innerHTML.includes('4321'));
  assert(h.notices.some(([text]) => text.includes('已重置')), 'Completed write still reports success');
}

async function oldGetCannotOverwriteNewView() {
  for (const renderer of ['renderCircleHrAccounts','renderHrEmployees','renderCircleTransfers']) {
    const h = await harness(renderer === 'renderHrEmployees' ? 'hrEmployees' : renderer === 'renderCircleTransfers' ? 'circleTransfers' : 'circleHrAccounts');
    const oldPending = h.context[renderer]();
    const oldCalls = h.calls.slice();
    const logs = await h.navigate('logs');
    const id = h.requestContext();
    for (const call of oldCalls) h.reply(call, renderer === 'renderCircleTransfers' ? (call.url.includes('group-options') ? [] : {items: []}) : hrData);
    await oldPending;
    assert(id.isCurrent(), 'Old completion must leave the current page request valid');
    assert(h.app.innerHTML.includes('正在加载'), renderer);
    h.reply(logs.call, []);
    await logs.pending;
    assert(h.app.innerHTML.includes('审计日志'), renderer);
  }
}

async function sameTabRefreshInvalidatesOldMutation() {
  const h = await harness();
  await initialHr(h);
  const mutation = h.button.onclick();
  await tick();
  const post = h.calls[1];
  const fresh = h.context.renderCircleHrAccounts();
  const freshCall = h.calls.at(-1), id = h.requestContext();
  h.reply(post, resetResult);
  await mutation;
  assert.equal(h.calls.length, 3);
  assert(id.isCurrent(), 'Old completion must leave the current page request valid');
  h.reply(freshCall, hrData);
  await fresh;
  assert(h.app.innerHTML.includes('景点圈HR账号'));
  assert(!h.app.innerHTML.includes('4321'));
}

async function olderRenderErrorCannotReplaceNewRefresh() {
  const h = await harness();
  const oldRender = h.context.render();
  const oldCall = h.calls[0];
  const fresh = h.context.renderCircleHrAccounts(), freshCall = h.calls[1];
  const id = h.requestContext();
  oldCall.completion.reject(new Error('Old response failed'));
  await oldRender;
  assert(id.isCurrent(), 'Old completion must leave the current page request valid');
  assert(h.app.innerHTML.includes('正在加载'), 'An old same-tab failure must not replace a newer pending view');
  h.reply(freshCall, hrData);
  await fresh;
  assert(h.app.innerHTML.includes('景点圈HR账号'));
}

async function refreshResultRemainsPageBound() {
  for (const leaveDuringRefresh of [false, true]) {
    const h = await harness();
    await initialHr(h);
    const mutation = h.button.onclick();
    await tick();
    h.reply(h.calls[1], resetResult);
    await tick();
    const refresh = h.calls[2];
    assert.equal(refresh.url, '/api/admin/circle-hr-accounts');
    let logs;
    if (leaveDuringRefresh) logs = await h.navigate('logs');
    h.reply(refresh, hrData);
    await mutation;
    if (logs) {
      assert(h.app.innerHTML.includes('正在加载'));
      h.reply(logs.call, []);
      await logs.pending;
      assert(h.app.innerHTML.includes('审计日志'));
      assert(!h.app.innerHTML.includes('4321'));
    } else {
      assert(h.app.innerHTML.includes('4321'), 'Still-present page must display the completed reset result');
    }
  }
}

async function oldReviewRetainsQueueAndCannotRepaint() {
  const h = await harness('review'), note = deferred();
  const queue = h.context.renderFrontlineReview();
  h.reply(h.calls[0], {items: [{id: 9, status: 'pending'}]});
  await queue;
  h.context.promptModal = () => note.promise;
  const mutation = h.reviewButton.onclick();
  const supervisor = await h.navigate('supervisorReview');
  const logs = await h.navigate('logs');
  const id = h.requestContext();
  note.resolve('实际原因');
  await tick();
  const post = h.calls.at(-1);
  assert.equal(post.url, '/api/reviews/9', 'A prompted action must retain its original review queue');
  h.reply(post, {record: {id: 9, status: 'rejected'}});
  await mutation;
  assert(id.isCurrent(), 'Old completion must leave the current page request valid');
  h.reply(supervisor.call, {items: []});
  await supervisor.pending;
  h.reply(logs.call, []);
  await logs.pending;
  assert(h.app.innerHTML.includes('审计日志'));
}

async function oldEntryRefreshCannotClaimNewRequest() {
  const h = await harness('entries');
  const entries = h.context.renderEntries();
  h.reply(h.calls[0], {items: [{id: 9, status: 'pending', record_type: 'recognition', available_actions: ['withdraw']}], page: 1, page_size: 100, total: 1});
  await entries;
  const mutation = h.withdrawButton.onclick();
  await tick();
  const post = h.calls[1];
  assert.equal(post.url, '/api/recognitions/9');
  assert.equal(post.options.method, 'DELETE');
  const logs = await h.navigate('logs'), id = h.requestContext();
  h.reply(post, {});
  await mutation;
  assert.equal(h.calls.length, 3, 'An old withdrawal must not reload the former entries page');
  assert(id.isCurrent(), 'Old completion must leave the current page request valid');
  h.reply(logs.call, []);
  await logs.pending;
  assert(h.app.innerHTML.includes('审计日志'));
}

(async () => {
  await postSurvivesNavigation();
  await oldGetCannotOverwriteNewView();
  await sameTabRefreshInvalidatesOldMutation();
  await olderRenderErrorCannotReplaceNewRefresh();
  await refreshResultRemainsPageBound();
  await oldReviewRetainsQueueAndCannotRepaint();
  await oldEntryRefreshCannotClaimNewRequest();
  console.log('View lifecycle: real ES-module GET/POST races, same-tab refresh, queue identity and completed writes passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
