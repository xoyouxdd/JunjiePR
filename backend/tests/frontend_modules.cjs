const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {createHash} = require('node:crypto');
const {chromium} = require('playwright');

// Serve the real app shell and its complete native-module graph. All API data
// below is synthetic; the test does not start the backend or open its database.
const staticRoot = path.resolve(__dirname, '../app/static');
const version = 'frontend-module-fixture';
const html = fs.readFileSync(path.join(staticRoot, 'index.html'), 'utf8');
const renderedHtml = html.replaceAll('__STATIC_CACHE_VERSION__', version);
const policySource = fs.readFileSync(path.join(staticRoot, '../security.py'), 'utf8');
const policyTuple = /CONTENT_SECURITY_POLICY\s*=\s*"; "\.join\(\s*\(([\s\S]*?)\)\s*\)/.exec(policySource);
assert(policyTuple, 'Update the fixture policy reader if the production CSP representation changes');
const basePolicy = [...policyTuple[1].matchAll(/"([^"\r\n]+)"/g)].map(match => match[1]).join('; ');
const importMapText = /<script type="importmap">([\s\S]*?)<\/script>/.exec(renderedHtml)[1];
const mapHash = createHash('sha256').update(importMapText.replace(/\r\n?/g, '\n')).digest('base64');
for (const lineEnding of ['\r\n', '\r']) {
  const changedMap = importMapText.replace(/\r\n?/g, '\n').replace(/\n/g, lineEnding);
  assert.equal(createHash('sha256').update(changedMap.replace(/\r\n?/g, '\n')).digest('base64'), mapHash,
    'HTML line-ending normalization must keep the import-map hash stable');
}
const contentSecurityPolicy = basePolicy.replace("script-src 'self'", `script-src 'self' 'sha256-${mapHash}'`);
const entryMatch = /<script\b[^>]*type="module"[^>]*src="([^"]+)"/.exec(html);
assert(entryMatch, 'The app shell must load a native ES module entry');
const entry = path.resolve(staticRoot, entryMatch[1].split('?')[0].replace(/^static\//, ''));
const importMap = JSON.parse(/<script type="importmap">([\s\S]*?)<\/script>/.exec(html)[1]);
const graph = new Map();
function readGraph(file, ancestors = []) {
  assert(!ancestors.includes(file), `Circular module import: ${[...ancestors, file].join(' -> ')}`);
  if (graph.has(file)) return;
  assert(file.startsWith(staticRoot + path.sep), `Module escapes static root: ${file}`);
  const source = fs.readFileSync(file, 'utf8');
  const dependencies = [...source.matchAll(/^import\s+.*?from\s+['"]([^'"]+)['"];?$/gm)]
    .map(match => path.resolve(path.dirname(file), match[1]));
  graph.set(file, dependencies);
  for (const dependency of dependencies) readGraph(dependency, [...ancestors, file]);
}
readGraph(entry);
const moduleFiles = [...graph.keys()].filter(file => file !== entry);
assert(moduleFiles.length > 1, 'Business code must be split into reachable modules');
const onDiskModules = fs.readdirSync(path.join(staticRoot, 'js/app'))
  .filter(name => name.endsWith('.js')).map(name => path.join(staticRoot, 'js/app', name));
assert.deepEqual([...moduleFiles].sort(), onDiskModules.sort(), 'No orphaned business modules');
for (const file of moduleFiles) {
  const key = './static/' + path.relative(staticRoot, file).split(path.sep).join('/');
  assert.equal(importMap.imports[key], `${key}?v=__STATIC_CACHE_VERSION__`, `Version mapping missing for ${key}`);
}
assert.equal(Object.keys(importMap.imports).length, moduleFiles.length);

const user = {id: 1, name: '测试管理员', employee_no: '1000001', role_name: '系统管理员',
  role_code: 'SYSTEM_ADMIN', base_role_code: 'SYSTEM_ADMIN', permissions: [],
  attraction_id: 1, attraction_name: '热力追踪', must_change_password: false};
const options = {roles: [{code: 'CM', name: 'CM'}], attractions: [{id: 1, name: '热力追踪'}],
  employee_circles: [{id: 1, name: '热力追踪'}], recognition_venues: [{id: 1, name: '热力追踪'}],
  recognition_types: [{id: 1, name: '安全', code: 'safety'}], deduction_types: []};
let mode = 'admin', delayedReset = false, releaseReset, resetStarted;
const requests = [], uploads = [], missingApis = [];
function sendJson(response, value) {
  response.writeHead(200, {'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store'});
  response.end(JSON.stringify(value));
}
async function fixtureRequest(request, response) {
  const url = new URL(request.url, 'http://fixture');
  requests.push({url: request.url, method: request.method});
  const route = url.pathname.replace(/^\/recognition(?=\/|$)/, '') || '/';
  if (route === '/favicon.ico') {response.writeHead(204); response.end(); return;}
  if (route === '/') {
    response.writeHead(200, {'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': contentSecurityPolicy});
    response.end(url.searchParams.has('fixture_crlf') ? renderedHtml.replace(/\r\n?/g, '\n').replace(/\n/g, '\r\n') : renderedHtml);
    return;
  }
  if (route.startsWith('/static/')) {
    const file = path.resolve(staticRoot, '.' + route.slice('/static'.length));
    if (!file.startsWith(staticRoot + path.sep) || !fs.existsSync(file)) {
      response.writeHead(404); response.end(); return;
    }
    const types = {'.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png'};
    response.writeHead(200, {'Content-Type': types[path.extname(file)] || 'application/octet-stream'});
    response.end(fs.readFileSync(file)); return;
  }
  if (route === '/api/me') {
    sendJson(response, mode === 'member' ? {...user, name: '测试员工', role_code: 'CM', base_role_code: 'CM',
      role_name: 'CM', permissions: ['SELF_RECOGNITION']} : {...user, must_change_password: mode === 'password'});
  } else if (route === '/api/options') sendJson(response, options);
  else if (route === '/api/action-center') sendJson(response, {month: '2026-10', items: [], total: 0});
  else if (route === '/api/announcements/options') sendJson(response, {can_publish: false, can_follow: false,
    can_admin: false, counts: {pending: 0, messages: 0, handovers: 0}, categories: ['通用']});
  else if (route === '/api/announcements') sendJson(response, {items: [], page: 1, pages: 1, total: 0});
  else if (route === '/api/announcement-media/options') sendJson(response, {templates: [], categories: ['通用']});
  else if (route === '/api/changelog/announcement') sendJson(response, {read: true});
  else if (route === '/api/admin/circle-hr-accounts') sendJson(response, {items: [{employee_id: 7,
    login_account: 'HR0001', name: 'HR测试员', attraction_name: '热力追踪', account_enabled: true, password_status: '已修改'}]});
  else if (route === '/api/admin/circle-hr-accounts/7/reset-password') {
    if (delayedReset) await new Promise(resolve => {releaseReset = resolve; resetStarted();});
    sendJson(response, {employee_name: 'HR测试员', temporary_password: '0001', message: '测试重置完成'});
  } else if (route === '/api/admin/logs') sendJson(response, [{time: '2026-10-09', operator: '测试管理员',
    action: 'fixture', entity: 'synthetic', reason: '前端模块验证'}]);
  else if (route === '/api/hr/organization') sendJson(response, {circles: [], others: [], warnings: []});
  else if (route === '/api/dashboard') sendJson(response, {attendance_score: 12, total_score: 12, records: []});
  else if (route === '/api/recognizers') sendJson(response, [{id: 7, name: '主管测试员', role_name: '主管', score: 1}]);
  else if (route === '/api/recognitions/batch') {
    const chunks = []; for await (const chunk of request) chunks.push(chunk);
    const form = await new Request('http://fixture/upload', {method: 'POST', headers: request.headers,
      body: Buffer.concat(chunks)}).formData();
    uploads.push({entries: JSON.parse(form.get('entries')), key: form.get('idempotency_key'),
      imageName: form.get('image').name, imageSize: form.get('image').size});
    sendJson(response, {records: [{id: 99}], encouragement_options: []});
  } else {
    missingApis.push(route); response.writeHead(404, {'Content-Type': 'application/json'});
    response.end(JSON.stringify({detail: `Unexpected fixture API: ${route}`}));
  }
}

const executableCandidates = [process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE, chromium.executablePath(),
  process.platform === 'win32' && path.join(process.env.PROGRAMFILES || 'C:/Program Files', 'Google/Chrome/Application/chrome.exe'),
  process.platform === 'win32' && path.join(process.env['PROGRAMFILES(X86)'] || 'C:/Program Files (x86)', 'Microsoft/Edge/Application/msedge.exe')].filter(Boolean);
const executablePath = executableCandidates.find(file => fs.existsSync(file));
const server = http.createServer((request, response) => fixtureRequest(request, response).catch(error => {
  response.writeHead(500); response.end(String(error));
}));
const checks = [];
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  let browser;
  try {
    browser = await chromium.launch({headless: true, ...(executablePath ? {executablePath} : {})});
    for (const pagePath of ['/', '/recognition/', '/recognition', '/recognition/?fixture_crlf=1']) {
      mode = 'admin';
      const page = await browser.newPage({viewport: {width: 1280, height: 900}});
      const errors = [], loadedModules = [];
      await page.addInitScript(() => {
        window.fixtureCspViolations = [];
        document.addEventListener('securitypolicyviolation', event => {
          window.fixtureCspViolations.push({directive: event.effectiveDirective, blockedURI: event.blockedURI});
        });
      });
      page.on('pageerror', error => errors.push(error.message));
      page.on('request', request => {if (/\/static\/js\/app\/.*\.js/.test(request.url())) loadedModules.push(request.url());});
      await page.goto(`${origin}${pagePath}`);
      await page.locator('#app h2').filter({hasText: '待办中心'}).waitFor();
      assert.equal(await page.locator('#tabs [data-tab="register"]').count(), 0, 'Admin registration is permission-gated');
      await page.locator('.tabs-desktop [data-tab="announcements"]').click();
      await page.locator('#announcementContent .empty').filter({hasText: '暂无公告'}).waitFor();
      assert.equal(await page.locator('#pageHeading h1').textContent(), '公告中心');
      await page.evaluate(async () => {
        const entryUrl = new URL(document.querySelector('script[type="module"][src]').src);
        const editor = await import(new URL('./app/announcement-media.js', entryUrl));
        const container = document.createElement('div');
        document.getElementById('announcementContent').append(container);
        await editor.renderAnnouncementPosterEditor({container, isCurrent: () => container.isConnected,
          seed: {title: '模块合并测试', summary: '公告配图测试', category: '通用', scope: '热力追踪', effective_on: '2026-10-10'},
          onUse: () => {}, onClose: () => container.remove()});
      });
      await page.locator('#announcementPosterForm').waitFor();
      assert.equal(await page.locator('#announcementPosterForm [name="title"]').inputValue(), '模块合并测试');
      await page.locator('[data-poster-back]').click();
      await page.locator('#announcementPosterForm').waitFor({state: 'detached'});
      await page.locator('.tabs-desktop [data-tab="hrEmployees"]').click();
      await page.locator('#app h2').filter({hasText: '员工管理'}).waitFor();
      await page.locator('.tabs-desktop [data-tab="circleHrAccounts"]').click();
      await page.locator('[data-circle-hr-reset]').click();
      await page.locator('[data-modal-confirm]').click();
      await page.locator('#circleHrResetResult').filter({hasText: '0001'}).waitFor();
      await page.locator('.modal-overlay').waitFor({state: 'detached'});
      delayedReset = true;
      const started = new Promise(resolve => {resetStarted = resolve;});
      await page.locator('[data-circle-hr-reset]').click();
      await page.locator('[data-modal-confirm]').click();
      await started;
      await page.locator('.tabs-desktop [data-tab="logs"]').click();
      await page.locator('#app h2').filter({hasText: '审计日志'}).waitFor();
      const completed = page.waitForResponse(response => response.url().endsWith('/7/reset-password'));
      releaseReset(); releaseReset = null; delayedReset = false;
      await completed;
      await page.waitForFunction(() => document.getElementById('toast').textContent === '景点圈HR密码已重置');
      assert.equal(await page.locator('#circleHrResetResult').count(), 0, 'A completed old write must not repaint the new page');
      assert.equal(await page.locator('.tabs-desktop [data-tab="logs"][aria-current="page"]').count(), 1);
      await page.evaluate(async () => {
        const entryUrl = new URL(document.querySelector('script[type="module"][src]').src);
        const runtime = await import(new URL('./app/context.js', entryUrl));
        window.fixtureTimerFired = false; window.fixtureCleanupFired = false;
        runtime.pageTimeout(() => {window.fixtureTimerFired = true;}, 500);
        runtime.registerPageCleanup(() => {window.fixtureCleanupFired = true;});
      });
      await page.locator('.tabs-desktop [data-tab="actionCenter"]').click();
      await page.locator('#app h2').filter({hasText: '待办中心'}).waitFor();
      await page.waitForTimeout(650);
      assert.deepEqual(await page.evaluate(() => [window.fixtureTimerFired, window.fixtureCleanupFired]), [false, true]);
      assert.equal(new Set(loadedModules.map(url => new URL(url).pathname)).size, moduleFiles.length);
      assert(loadedModules.every(url => new URL(url).searchParams.get('v') === version), 'All module requests must use the shell cache version');
      assert.deepEqual(await page.evaluate(() => window.fixtureCspViolations), [], 'App and import map must load under the production CSP');
      const inline = await page.evaluate(async () => {
        const script = document.createElement('script');
        script.textContent = 'window.fixtureUnauthorizedInlineExecuted = true;';
        document.head.append(script);
        await new Promise(resolve => setTimeout(resolve, 100));
        return {executed: Boolean(window.fixtureUnauthorizedInlineExecuted), violations: window.fixtureCspViolations};
      });
      assert.equal(inline.executed, false, 'The import map hash must not authorize arbitrary inline JavaScript');
      assert(inline.violations.some(row => row.directive.startsWith('script-src') && row.blockedURI === 'inline'));
      assert.deepEqual(errors, [], `Module/DOM errors at ${pagePath}`);
      checks.push(`admin navigation, versioned imports, strict CSP, stale-write guard and cleanup at ${pagePath}`);
      await page.close();
    }
    mode = 'member';
    const member = await browser.newPage({viewport: {width: 390, height: 844}});
    const memberErrors = []; member.on('pageerror', error => memberErrors.push(error.message));
    await member.goto(`${origin}/recognition/`);
    await member.locator('#homeMonthSelect').waitFor();
    assert.equal(await member.locator('#tabs [data-tab="hrEmployees"]').count(), 0, 'Members cannot navigate to HR tools');
    await member.locator('.tabs-mobile [data-tab="register"]').click();
    await member.locator('[data-batch-entry] [name="recognizer_employee_id"] option[value="7"]').waitFor({state: 'attached'});
    await member.locator('[data-batch-entry] [name="content"]').fill('前端模块上传验证');
    await member.locator('[data-batch-entry] [name="occurred_attraction_id"]').selectOption('1');
    await member.locator('[data-batch-entry] [name="recognizer_employee_id"]').selectOption('7');
    await member.locator('#recognitionAlbumInput').setInputFiles({name: 'fixture.png', mimeType: 'image/png',
      buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jQ8kAAAAASUVORK5CYII=', 'base64')});
    await member.locator('[data-batch-submit]').click();
    await member.waitForFunction(() => document.getElementById('toast').textContent.includes('已提交 1 条认可'));
    assert.equal(uploads.length, 1, 'One batch submit must make one request');
    assert.equal(uploads[0].entries[0].content, '前端模块上传验证');
    assert.match(uploads[0].entries[0].recognition_date, /^\d{4}-\d{2}-\d{2}$/);
    assert(uploads[0].key && uploads[0].imageSize > 0);
    assert.equal(uploads[0].imageName, 'fixture.png');
    const fallback = await member.evaluate(async () => {
      const submission = await import('./static/js/app/submission.js');
      const form = document.createElement('form');
      const input = document.createElement('input'); input.type = 'date'; input.name = 'recognition_date';
      input.dataset.submitDate = '2026-10-09'; form.append(input);
      const data = submission.submissionData(form), repeated = submission.submissionData(form);
      input.dataset.submitDateCleared = '1';
      let failure = ''; try {submission.recognitionSubmissionData(form);} catch (error) {failure = error.message;}
      return {date: data.get('recognition_date'), key: data.get('idempotency_key'),
        repeatedKey: repeated.get('idempotency_key'), cleared: submission.submittedDateValue(input), failure,
        invalidDate: submission.normalizeSubmitDate('2026-02-30')};
    });
    assert.equal(fallback.date, '2026-10-09'); assert.equal(fallback.key, fallback.repeatedKey);
    assert.equal(fallback.cleared, ''); assert.equal(fallback.invalidDate, '');
    assert.match(fallback.failure, /请先选择认可图片/);
    assert.deepEqual(memberErrors, []); checks.push('mobile member registration, multipart image, date fallback and idempotency');
    await member.close();
    mode = 'password';
    const password = await browser.newPage();
    await password.goto(`${origin}/`);
    await password.locator('#requiredPasswordForm').waitFor();
    assert.equal(await password.locator('#tabs [data-tab]').count(), 0);
    assert.equal(await password.locator('#requiredPasswordForm button[type="submit"]').isDisabled(), true);
    checks.push('first-login password gate'); await password.close();
    assert.deepEqual(missingApis, [], 'Every fixture business request must have an intentional mock');
    console.log(JSON.stringify({status: 'passed', modules: moduleFiles.length, checks, uploads: uploads.length}, null, 2));
  } finally {
    releaseReset?.();
    if (browser) await browser.close();
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
