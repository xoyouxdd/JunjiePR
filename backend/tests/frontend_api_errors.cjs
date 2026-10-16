// Execute the actual API owner functions without a browser, server or copied implementation.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {frontendFunctionSource} = require('./frontend_source.js');

function harness() {
  const calls = [], controller = new AbortController();
  const timers = new Map();let nextTimer=0;
  let nextResponse;
  const context = vm.createContext({
    AbortController, setTimeout:(callback,delay)=>{timers.set(++nextTimer,{callback,delay});return nextTimer;},clearTimeout:id=>timers.delete(id),
    getViewSignal: () => controller.signal,
    portalPath: value => '/recognition' + value,
    location: {href: ''},
    fetch: async (url, options) => {
      calls.push({url, options});
      if (nextResponse instanceof Error) throw nextResponse;
      if (typeof nextResponse === 'function') return nextResponse(options);
      return nextResponse;
    },
  });
  for (const name of ['validationErrorMessage', 'apiFallbackMessage', 'readApiBody', 'api']) {
    vm.runInContext(frontendFunctionSource(name), context, {filename: `api.js:${name}`});
  }
  function reply(status, body) {
    nextResponse = {status, ok: status >= 200 && status < 300, text: async () => typeof body === 'string' ? body : JSON.stringify(body)};
  }
  return {context, calls, controller, reply, response: value => {nextResponse = value;},expire:()=>[...timers.values()].forEach(timer=>timer.callback()),timers};
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
  assert(h.calls.at(-1).options.signal instanceof AbortSignal, 'Reads have a cancellable timeout signal');

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
  const slow=harness();
  slow.response(options=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('aborted'),{name:'AbortError'})))));
  const waiting=slow.context.api('/api/slow');slow.expire();
  await assert.rejects(waiting,error=>error.message.includes('等待超时'));
  assert.equal(slow.timers.size,0,'Timeout cleanup must run after rejection');
  const cancelled=harness();
  cancelled.response(options=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('aborted'),{name:'AbortError'})))));
  const abandoned=cancelled.context.api('/api/old-page');cancelled.controller.abort();
  await assert.rejects(abandoned,error=>error.name==='AbortError');
  assert.equal(cancelled.timers.size,0,'Navigation cancellation clears the read timer');
  for(const method of ['GET','get']){
    const custom=harness(),parent=new AbortController();
    let added=0,removed=0;
    const add=parent.signal.addEventListener.bind(parent.signal),remove=parent.signal.removeEventListener.bind(parent.signal);
    parent.signal.addEventListener=(...args)=>{added++;return add(...args);};
    parent.signal.removeEventListener=(...args)=>{removed++;return remove(...args);};
    custom.response(options=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Object.assign(new Error('aborted'),{name:'AbortError'})))));
    const pending=custom.context.api('/api/custom',{method,signal:parent.signal});
    const child=custom.calls[0].options.signal;
    assert.notEqual(child,parent.signal,'Reads combine cancellation with a distinct timeout signal');
    assert.deepEqual([...custom.timers.values()].map(timer=>timer.delay),[30000]);
    custom.controller.abort();
    assert.equal(child.aborted,false,'Explicit caller signal takes precedence over page signal');
    parent.abort();await assert.rejects(pending,error=>error.name==='AbortError');
    assert.equal(added,1);assert.equal(removed,1);assert.equal(custom.timers.size,0);
  }
  const already=harness();already.controller.abort();
  already.response(options=>{assert.equal(options.signal.aborted,true);throw Object.assign(new Error('already aborted'),{name:'AbortError'});});
  await assert.rejects(already.context.api('/api/already-aborted'),error=>error.name==='AbortError');
  assert.equal(already.timers.size,0);
  const writing=harness();let finishWrite;
  writing.response(()=>new Promise(resolve=>{finishWrite=resolve;}));
  const write=writing.context.api('/api/write-survives',{method:'POST'});
  assert.equal(writing.calls[0].options.signal,undefined);assert.equal(writing.timers.size,0);
  writing.controller.abort();writing.expire();
  finishWrite({status:200,ok:true,text:async()=>'{"saved":true}'});
  assert.equal((await write).saved,true,'Navigation and read timeout must not cancel a pending write');
  h.response(new Error('network dropped'));
  await assert.rejects(h.context.api('/api/write',{method:'POST'}),error=>error.message.includes('提交结果暂未确认'));
  h.reply(200,'<html>lost response</html>');
  await assert.rejects(h.context.api('/api/write',{method:'POST'}),error=>error.message.includes('先查看相关记录'));
  console.log('API errors: real-owner proof/legacy fields, status fallbacks, invalid body, network/abort and expired login passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
