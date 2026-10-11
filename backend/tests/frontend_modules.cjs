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
const reviewRecord = {id: 71, employee_name: '复核测试主管', employee_no: 'SUP71',
  recognition_date: '2026-10-08', submitted_at: '2026-10-11 12:50:34',
  recognition_type: '演出', recognizer_name: '测试GSM', fraction: 1, credited_fraction: 1,
  content: '补录认可日期展示验证', status: 'pending', status_name: '待复核'};
let trainingDemo = false, trainingSigned = false;
const trainingAnnouncement = {id: 51, version: 1, current_version: 1, title: '培训签字验证', summary: '',
  category: '安全 & 合规', scope_kind: 'circle', attraction_ids: [1], tiers: ['frontline'], scope_label: '热力追踪',
  confirmation_level: 3, training_required: true, trainer_scope: 'circle', trainer_ids: [8], trainer_circle_id: 1,
  status: 'published', effective: true, effective_on: '2026-10-01', due_on: null, expires_on: null,
  published_at: '2026-10-11', updated_at: '2026-10-11', author: '测试GSM', attachments: [],
  versions: [{number: 1, accessible: true}], body: '培训后签字', body_lines: [{text: '培训后签字', changed: false}]};
function demoAnnouncement() {return {...trainingAnnouncement, can_edit: mode === 'publisher',
  can_manage: mode === 'publisher', can_train: mode === 'trainer', can_followup_export: mode === 'publisher',
  receipt: mode === 'member' ? {status: trainingSigned ? 'completed' : 'pending', confirmed_at: trainingSigned ? '2026-10-11T13:00:00' : null} : null};}
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
    sendJson(response, ['publisher','trainer'].includes(mode) ? {...user, role_code: mode === 'trainer' ? 'TR' : 'GSM', base_role_code: mode === 'trainer' ? 'TR' : 'GSM', role_codes: [mode === 'trainer' ? 'TR' : 'GSM']} : mode === 'member' ? {...user, name: '测试员工', role_code: 'CM', base_role_code: 'CM',
      role_name: 'CM', permissions: ['SELF_RECOGNITION']} : {...user, must_change_password: mode === 'password'});
  } else if (route === '/api/rotation/adjust/mine') sendJson(response, {items: []});
  else if (route === '/api/options') sendJson(response, options);
  else if (route === '/api/action-center') sendJson(response, {month: '2026-10', items: [], total: 0});
  else if (route === '/api/deduction-upgrades/pending') sendJson(response, {items:[]});
  else if (route === '/api/announcements/options' && trainingDemo) sendJson(response, {can_publish: mode === 'publisher', can_follow: mode === 'publisher', can_train: mode === 'trainer', can_admin: false,
    counts: {pending: 1,messages: 0,handovers: 0}, categories: ['安全 & 合规'], circles: [{id:1,name:'热力追踪'},{id:2,name:'小熊罐子'}],
    tiers: {frontline:'CM / TR',lead:'LEAD'}, publisher: {circles:[1],projects:[],tiers:['frontline','lead'],categories:['安全 & 合规']}});
  else if (route === '/api/announcements/training-options') sendJson(response, {own_circle_id:1,items:[{id:8,name:'本圈培训师',employee_no:'TR8',attraction_id:1},{id:9,name:'跨圈培训师',employee_no:'TR9',attraction_id:2}]});
  else if (route === '/api/announcements/summary') sendJson(response, {pending:1,messages:0,handovers:0});
  else if (route === '/api/announcements/51') sendJson(response, demoAnnouncement());
  else if (route === '/api/announcements/51/receipt') {
    const chunks=[]; for await (const chunk of request) chunks.push(chunk);
    const body=JSON.parse(Buffer.concat(chunks).toString());
    if (body.action==='confirm') {assert.equal(body.training_code,'123456');assert(body.signature?.startsWith('data:image/png;base64,'));trainingSigned=true;}
    sendJson(response, demoAnnouncement().receipt);
  } else if (route === '/api/announcements/51/training/verify') sendJson(response, {ok:true,trainer_name:'本圈培训师',expires_at:'2026-10-11T19:00:00'});
  else if (route === '/api/announcements/51/training/codes') {
    const chunks=[];for await(const chunk of request)chunks.push(chunk);
    assert.equal(JSON.parse(Buffer.concat(chunks).toString()).hours,6);
    sendJson(response,{id:1,code:'123456',expires_at:'2026-10-11T19:00:00'});
  } else if (route === '/api/announcements/51/training') sendJson(response,{title:trainingAnnouncement.title,version:1,items:[],groups:[],sessions:[]});
  else if (route === '/api/announcements/51/records') sendJson(response,{announcement:demoAnnouncement(),items:[],groups:[],counts:{required:0,completed:0,pending:0,suspended:0,exited:0},handover:{owner:{name:'测试GSM',employee_no:'GSM1'},history:[]}});
  else if (route === '/api/announcements/options') sendJson(response, {can_publish: false, can_follow: false,
    can_admin: false, counts: {pending: 0, messages: 0, handovers: 0}, categories: ['通用']});
  else if (route === '/api/announcements') sendJson(response, {items: trainingDemo ? [demoAnnouncement()] : [], page: 1, pages: 1, total: trainingDemo ? 1 : 0});
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
  else if (route === '/api/supervisor-reviews' || route === '/api/reviews') {
    const history = url.searchParams.get('view') === 'history';
    sendJson(response, {items: [{...reviewRecord, status: history ? 'confirmed' : 'pending',
      status_name: history ? '已确认' : '待复核'}], total: 1, limit: 200, offset: 0, has_more: false});
  } else if (route === '/api/supervisor-reviews/71' || route === '/api/reviews/71') {
    sendJson(response, {record: {...reviewRecord}});
  }
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
    // Load the real review module and APIs for both queues, with different
    // business/submission dates. Check responsive layouts and history row updates.
    mode = 'admin';
    for (const width of [390, 1280]) {
      const reviewPage = await browser.newPage({viewport: {width, height: 900}});
      const reviewErrors = [];
      reviewPage.on('pageerror', error => reviewErrors.push(error.message));
      await reviewPage.goto(`${origin}/`);
      await reviewPage.locator('#app h2').filter({hasText: '待办中心'}).waitFor();
      for (const renderer of ['renderSupervisorReview', 'renderFrontlineReview']) {
        await reviewPage.evaluate(async name => {
          const entryUrl = new URL(document.querySelector('script[type="module"][src]').src);
          const runtime = await import(new URL('./app/context.js', entryUrl));
          const reviews = await import(new URL('./app/reviews.js', entryUrl));
          runtime.clearPageResources();
          await reviews[name]();
        }, renderer);
        const row = reviewPage.locator('[data-review-row="71"]');
        const checkDates = async () => {
          assert.equal(await row.locator('strong').filter({hasText: '认可日期：2026-10-08'}).count(), 1);
          assert.equal(await row.locator('small').filter({hasText: '提交时间：2026-10-11 12:50:34'}).count(), 1);
          assert.doesNotMatch(await row.innerText(), /认可日期：2026-10-11/);
        };
        await checkDates();
        assert.equal(await reviewPage.locator('.work-card').count(), width === 390 ? 1 : 0);
        if (width === 1280) assert.equal(await reviewPage.locator('thead th').first().innerText(), '认可日期 / 提交时间');
        await reviewPage.getByRole('button', {name: '查看已处理历史', exact: true}).click();
        await row.getByRole('button', {name: '还原', exact: true}).waitFor();
        await checkDates();
        await row.getByRole('button', {name: '还原', exact: true}).click();
        await row.getByRole('button', {name: '确认', exact: true}).waitFor();
        await checkDates();
        if (width === 390) {
          assert.equal(await reviewPage.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        }
      }
      assert.deepEqual(reviewErrors, []);
      checks.push(`review business date versus submission time, both queues, history and row replacement at ${width}px`);
      await reviewPage.close();
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
    trainingDemo = true;
    for (const trainingMode of ['publisher','trainer','member']) {
      mode=trainingMode;
      const page=await browser.newPage({viewport:{width:trainingMode==='publisher'?1280:390,height:900}});
      const errors=[];page.on('pageerror',e=>errors.push(e.message));
      await page.goto(`${origin}/`);
      await page.locator('#tabs [data-tab="announcements"]').first().waitFor({state:'attached'});
      await page.evaluate(async()=>{
        const entryUrl=new URL(document.querySelector('script[type="module"][src]').src);
        const {clearPageResources}=await import(new URL('./app/context.js',entryUrl));clearPageResources();
        const {renderAnnouncements}=await import(new URL('./app/announcements.js',entryUrl));await renderAnnouncements();
      });
      await page.locator('[data-ann-open="51"]').waitFor();
      if(mode==='publisher'){
        await page.locator('[data-ann-new]').click();
        await page.locator('#announcementPublishForm').waitFor();
        await page.locator('[name=confirmation_level]').selectOption('3');
        await page.locator('[name=training_required]').check();
        assert.equal(await page.locator('[name=trainer_ids]').count(),1);
        await page.locator('[name=trainer_scope]').selectOption('all');
        assert.equal(await page.locator('[name=trainer_ids]').count(),2);
        await page.locator('[name=trainer_ids][value="9"]').check();
        await page.locator('[name=title]').fill('培训码公告');await page.locator('[name=body]').fill('学习后签字');
        await page.getByRole('button',{name:'预览',exact:true}).click();
        assert.match(await page.locator('#announcementPublishReview').innerText(),/培训码＋签字.*跨圈培训师/);
        fs.mkdirSync(path.resolve(__dirname,'../../.handover_review'),{recursive:true});
        await page.screenshot({path:path.resolve(__dirname,'../../.handover_review/announcement-training-publish-20261011.png'),fullPage:true});
        await page.locator('[data-ann-back]').click();
        await page.locator('[data-ann-view=manage]').click();await page.locator('[data-ann-records="51"]').click();
        await page.getByRole('link',{name:'跟进Excel'}).waitFor();
        assert.match(await page.getByRole('link',{name:'跟进Excel'}).getAttribute('href'),/followup-export\?version=1/);
      }else if(mode==='trainer'){
        await page.locator('[data-ann-view=training]').click();await page.locator('[data-ann-training="51"]').click();
        await page.locator('[name=hours]').selectOption('6');await page.getByRole('button',{name:'生成培训码',exact:true}).click();
        await page.locator('[data-ann-current-code]').filter({hasText:'123456'}).waitFor();
        await page.screenshot({path:path.resolve(__dirname,'../../.handover_review/announcement-training-tr-20261011.png'),fullPage:true});
      }else{
        await page.locator('[data-ann-open="51"]').click();
        await page.locator('#announcementReceiptForm').waitFor();
        assert.equal(await page.locator('#announcementSignature').isVisible(),false);
        await page.locator('[name=training_code]').fill('123456');await page.locator('[data-ann-verify-code]').click();
        await page.locator('#announcementSignature').waitFor({state:'visible'});
        assert(await page.locator('#announcementSignature').evaluate(el=>el.width)>100);
        await page.screenshot({path:path.resolve(__dirname,'../../.handover_review/announcement-training-member-20261011.png'),fullPage:true});
        await page.locator('[name=acknowledged]').check();
        const box=await page.locator('#announcementSignature').boundingBox();
        await page.mouse.move(box.x+20,box.y+30);await page.mouse.down();await page.mouse.move(box.x+140,box.y+100,{steps:8});await page.mouse.up();
        await page.getByRole('button',{name:'提交签字',exact:true}).click();
        await page.locator('[data-ann-view=history][aria-pressed=true]').waitFor();assert(trainingSigned);
      }
      assert.deepEqual(errors,[]);if(mode!=='publisher')assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      checks.push(`announcement training ${mode}: real module DOM flow`);await page.close();
    }
    trainingDemo=false;
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
