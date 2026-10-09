const assert = require('node:assert/strict');
const path = require('node:path');
const vm = require('node:vm');

const {frontendSources, staticRoot} = require('./frontend_source.js');

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
  const app = {
    _html: '',
    get innerHTML() { return this._html; },
    set innerHTML(value) {
      this._html = value;
      elements.clear();
      for (const id of value.matchAll(/id="([^"]+)"/g)) elements.set(id[1], {innerHTML: '', isConnected: true});
    },
    contains(node) { return node?.isConnected === true; },
    querySelectorAll(selector) {
      if (selector === '[data-circle-hr-reset]' && this._html.includes('circleHrResetResult')) return [button];
      return [];
    }
  };
  const context = {
    AbortController, URLSearchParams, FormData, console, setTimeout, clearTimeout,
    location: {pathname: '/', search: ''},
    window: {matchMedia: () => ({matches: false, addEventListener() {}, removeEventListener() {}})},
    document: {getElementById: id => id === 'app' ? app : id === 'tabs' ? {innerHTML: ''} : elements.get(id), querySelectorAll: () => []},
    confirmModal: async () => true, promptModal: async () => '原因',
    toast: (...args) => notices.push(args),
    fetch(url, options) {
      const completion = deferred();
      calls.push({url, options, completion});
      // Deliberately ignore abort: ownership checks must also cover an already-delivered response.
      return completion.promise;
    }
  };
  const realm = vm.createContext(context), sources = frontendSources(), modules = new Map();
  const moduleRoot = path.join(staticRoot, 'js/app');
  const mocked = new Set(['dialogs.js', 'files.js', 'navigation.js', 'toast.js']);
  function load(filename) {
    if (modules.has(filename)) return modules.get(filename);
    assert(sources.has(filename), `Fixture module must be reachable from index: ${filename}`);
    let source = sources.get(filename), module;
    if (mocked.has(path.basename(filename))) {
      // Only browser UI boundaries are mocked. Transport, lifecycle and all tested
      // handlers use the same imported bindings as the shipped modules.
      const list = /export\s*{([^}]+)}/.exec(source);
      assert(list, `Missing named exports in ${filename}`);
      const names = list[1].split(',').map(name => name.trim());
      module = new vm.SyntheticModule(names, function() {
        for (const name of names) {
          const fn = ['confirmModal', 'promptModal', 'toast'].includes(name) ? (...args) => context[name](...args) : () => {};
          this.setExport(name, fn);
        }
      }, {context: realm, identifier: filename});
    } else {
      // Expose the two private binders solely to reuse their existing isolated
      // action scenarios. No runtime statement or function body is replaced.
      if (path.basename(filename) === 'reviews.js') source += '\nexport {bindReviewActions};\n';
      if (path.basename(filename) === 'entries.js') source += '\nexport {bindEntryActions};\n';
      module = new vm.SourceTextModule(source, {context: realm, identifier: filename});
    }
    modules.set(filename, module);
    return module;
  }
  const names = ['context', 'state', 'accounts', 'hr', 'hr-employees', 'reviews', 'entries', 'deduction-materials'];
  const entry = new vm.SourceTextModule(names.map((name, index) => `import * as owner${index} from './${name}.js'; export {owner${index}};`).join('\n'), {context: realm, identifier: path.join(moduleRoot, 'fixture.js')});
  await entry.link((specifier, importing) => load(path.resolve(path.dirname(importing.identifier), specifier.split(/[?#]/, 1)[0])));
  await entry.evaluate();
  for (let index = 0; index < names.length; index++) Object.assign(context, entry.namespace[`owner${index}`]);
  Object.assign(context.state, {tab, me: {role_code: 'SYSTEM_ADMIN', permissions: []}, options: {}});
  context.configureViews({
    circleHrAccounts: context.renderCircleHrAccounts, logs: context.renderLogs,
    hrEmployees: context.renderHrEmployees, circleTransfers: context.renderCircleTransfers,
    review: context.renderFrontlineReview, supervisorReview: context.renderSupervisorReview,
    home: () => {}, entries: () => {},
  });
  const reply = (call, value, status = 200) => call.completion.resolve({status, ok: status >= 200 && status < 300, text: async () => JSON.stringify(value)});
  const navigate = async nextTab => {
    context.state.tab = nextTab;
    const pending = context.render();
    await tick();
    return {pending, call: calls.at(-1)};
  };
  const requestContext = () => context.captureViewContext();
  return {context, app, calls, notices, button, reply, navigate, requestContext};
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
  assert(h.calls[0].options.signal.aborted, 'Navigation cancels read-only requests');
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
  const review = {dataset: {review: '9', action: 'reject'}};
  h.app.querySelectorAll = selector => selector === '[data-review]' ? [review] : [];
  h.context.promptModal = () => note.promise;
  h.context.bindReviewActions();
  const mutation = review.onclick();
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
  const button = {dataset: {entryWithdraw: '9'}};
  h.context.document.querySelectorAll = selector => selector === '[data-entry-withdraw]' ? [button] : [];
  let reloads = 0;
  h.context.bindEntryActions(() => { reloads++; h.context.beginViewRequest(); });
  const mutation = button.onclick();
  await tick();
  const post = h.calls[0];
  const logs = await h.navigate('logs'), id = h.requestContext();
  h.reply(post, {});
  await mutation;
  assert.equal(reloads, 0);
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
