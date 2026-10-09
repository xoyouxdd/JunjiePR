// Execute the actual API owner functions without a browser, server or copied implementation.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {frontendFunctionSource} = require('./frontend_source.js');

function harness() {
  const calls = [], controller = new AbortController();
  let nextResponse;
  const context = vm.createContext({
    getViewSignal: () => controller.signal,
    portalPath: value => '/recognition' + value,
    location: {href: ''},
    fetch: async (url, options) => {
      calls.push({url, options});
      if (nextResponse instanceof Error) throw nextResponse;
      return nextResponse;
    },
  });
  for (const name of ['validationErrorMessage', 'apiFallbackMessage', 'readApiBody', 'api']) {
    vm.runInContext(frontendFunctionSource(name), context, {filename: `api.js:${name}`});
  }
  function reply(status, body) {
    nextResponse = {status, ok: status >= 200 && status < 300, text: async () => typeof body === 'string' ? body : JSON.stringify(body)};
  }
  return {context, calls, controller, reply, response: value => {nextResponse = value;}};
}

(async () => {
  const h = harness();
  const proofMessage = '缺勤证明未成功上传，请重新选择文件后提交。';
  const proofDetail = {code: 'SICK_LEAVE_VALIDATION_ERROR', fields: [{field: 'proof', message: proofMessage}, {field: 'unused'}]};
  assert.equal(h.context.validationErrorMessage(proofDetail), proofMessage);
  h.reply(422, {detail: proofDetail});
  await assert.rejects(h.context.api('/api/sick-leaves', {method: 'POST'}), error => {
    assert.equal(error.message, proofMessage);
    assert.equal(error.status, 422);
    assert.equal(JSON.stringify(error.detail), JSON.stringify(proofDetail));
    return true;
  });
  assert.equal(h.calls.at(-1).url, '/recognition/api/sick-leaves');
  assert.equal(h.calls.at(-1).options.signal, undefined, 'Write validation must survive page cancellation');

  const legacy = [{loc: ['body', 'proof']}, {loc: ['body', 'employee_id']}, {loc: ['body', 'proof']}];
  h.reply(422, {detail: legacy});
  await assert.rejects(h.context.api('/api/sick-leaves'), error => error.message === '缺勤证明未成功提交、缺勤员工未成功提交，请检查后重试。' && error.status === 422);
  assert.equal(h.calls.at(-1).options.signal, h.controller.signal, 'Reads inherit the current page cancellation signal');

  h.reply(400, {detail: {message: '后端业务说明', fields: [{message: '不应覆盖业务说明'}]}});
  await assert.rejects(h.context.api('/api/test'), error => error.message === '后端业务说明');
  h.reply(503, '');
  await assert.rejects(h.context.api('/api/test'), error => error.message === '服务暂不可用，请稍后重试。' && error.status === 503);
  h.reply(418, {});
  await assert.rejects(h.context.api('/api/test'), error => error.message === '请求失败（418），请稍后重试。' && error.status === 418);
  h.reply(502, '<html><body>Proxy failed</body></html>');
  await assert.rejects(h.context.api('/api/test'), error => error.message === '服务器返回了无法解析的数据，请稍后重试。');
  h.reply(204, '');
  assert.equal(JSON.stringify(await h.context.api('/api/test')), '{}');

  h.response(new Error('connection closed'));
  await assert.rejects(h.context.api('/api/test'), error => error.message.startsWith('网络连接失败，请检查网络后重试'));
  const abort = Object.assign(new Error('cancelled'), {name: 'AbortError'});
  h.response(abort);
  await assert.rejects(h.context.api('/api/test'), error => error === abort);
  h.response({status: 200, ok: true, text: async () => {throw abort;}});
  await assert.rejects(h.context.api('/api/test'), error => error === abort);
  h.response({status: 200, ok: true, text: async () => {throw new Error('read failed');}});
  await assert.rejects(h.context.api('/api/test'), error => error.message === '读取服务器响应失败，请稍后重试。');
  h.reply(401, {});
  await assert.rejects(h.context.api('/api/test'), error => error.message === '登录已失效');
  assert.equal(h.context.location.href, '/recognition/login');
  console.log('API errors: real-owner proof/legacy fields, status fallbacks, invalid body, network/abort and expired login passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
