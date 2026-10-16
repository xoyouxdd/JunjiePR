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
let trainingDemo = false, trainingSigned = false, trainingSeen = false;
const trainingAnnouncement = {id: 51, version: 1, current_version: 1, title: '培训签字验证', summary: '',
  category: '安全 & 合规', scope_kind: 'circle', attraction_ids: [1], tiers: ['frontline'], scope_label: '热力追踪',
  confirmation_level: 3, training_required: true, trainer_scope: 'circle', trainer_ids: [8], trainer_circle_id: 1,
  status: 'published', effective: true, effective_on: '2026-10-01', due_on: null, expires_on: null,
  published_at: '2026-10-11', updated_at: '2026-10-11', author: '测试GSM', attachments: [],
  versions: [{number: 1, accessible: true}], body: '培训后签字', body_lines: [{text: '培训后签字', changed: false}]};
function demoAnnouncement() {return {...trainingAnnouncement, can_edit: mode === 'publisher',
  can_manage: mode === 'publisher', can_train: mode === 'trainer', can_followup_export: mode === 'publisher',
  receipt: mode === 'member' ? {status: trainingSigned ? 'completed' : 'pending', seen_at:trainingSeen?'2026-10-11T12:00:00':null, confirmed_at: trainingSigned ? '2026-10-11T13:00:00' : null} : null};}
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
  else if (/^\/api\/month-closes\/\d{4}-\d{2}$/.test(route)) {
    const month = route.split('/').pop(), allowed = month <= '2026-09';
    sendJson(response, {month, attraction_name: '全部景点圈', is_closed: false, can_close: allowed,
      can_reopen: false, checklist: [], close_period_allowed: allowed, latest_close_month: '2026-09',
      close_block_reason: allowed ? '' : '仅可关闭上月及更早月份，当月和未来月份不能月结。'});
  }
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
    if (body.action==='read') trainingSeen=true;
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
  else if (route === '/api/changelog') sendJson(response, {app_version: version, role_name: '测试角色', releases: []});
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
      await page.locator('.tabs-desktop [data-open-about]').click();
      await page.locator('[data-about-version]').filter({hasText: version}).waitFor();
      assert.match(await page.locator('[data-about-dialog]').textContent(), /建设与维护\s*赵君杰/);
      assert.match(await page.locator('[data-about-dialog]').textContent(), /共同参与\s*张森/);
      assert.equal(await page.locator('[data-about-dialog] img').evaluate(img => img.complete && img.naturalWidth > 0), true);
      await page.keyboard.press('Shift+Tab');
      assert.equal(await page.locator('[data-about-updates]').evaluate(node => node === document.activeElement), true);
      await page.keyboard.press('Escape');
      await page.locator('[data-about-dialog]').waitFor({state: 'detached'});
      assert.equal(await page.locator('.tabs-desktop [data-open-about]').evaluate(node => node === document.activeElement), true);
      await page.locator('.tabs-desktop [data-open-about]').click();
      await page.locator('[data-about-updates]').click();
      await page.locator('#app h2').filter({hasText: '更新记录'}).waitFor();
      await page.locator('[data-about-dialog]').waitFor({state: 'detached'});
      await page.locator('.tabs-desktop [data-tab="actionCenter"]').click();
      await page.locator('.tabs-desktop [data-tab="monthClose"]').click();
      await page.locator('#monthCloseForm [name="month"]').fill('2026-10');
      await page.locator('#monthCloseForm [type="submit"]').click();
      await page.locator('#monthCloseResult .notice').filter({hasText: '当月和未来月份不能月结'}).waitFor();
      assert.equal(await page.locator('[data-month-close]').count(), 0);
      assert.match(await page.locator('#monthCloseResult').textContent(), /未月结/);
      await page.locator('#monthCloseForm [name="month"]').fill('2026-08');
      await page.locator('#monthCloseForm [type="submit"]').click();
      await page.locator('[data-month-close]').waitFor();
      await page.locator('.tabs-desktop [data-tab="actionCenter"]').click();
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
    for (const width of [320, 390]) {
      await member.setViewportSize({width, height: 844});
      await member.locator('[data-open-more]').click();
      await member.locator('.mobile-more-drawer [data-open-about]').click();
      await member.locator('[data-about-version]').filter({hasText: version}).waitFor();
      await member.locator('.mobile-more-drawer').waitFor({state: 'hidden'});
      assert.equal(await member.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await member.locator('[data-about-close]').click();
      await member.locator('[data-about-dialog]').waitFor({state: 'detached'});
      assert.equal(await member.locator('[data-open-more]').evaluate(node => node === document.activeElement), true);
      assert.equal(await member.locator('.app-frame').evaluate(node => node.inert), false);
    }
    checks.push('about dialog: desktop, 320/390px mobile, names, live version, logo, focus, Escape and existing changelog');
    assert.equal(await member.locator('#tabs [data-tab="hrEmployees"]').count(), 0, 'Members cannot navigate to HR tools');
    await member.locator('.tabs-mobile [data-tab="register"]').click();
    await member.locator('[data-batch-entry] [name="recognizer_employee_id"] option[value="7"]').waitFor({state: 'attached'});
    await member.locator('[data-batch-entry] [name="content"]').fill('未提交保护验证');
    await member.locator('.tabs-mobile [data-tab="home"]').click();
    await member.locator('[data-modal-cancel]').click();
    await member.locator('.modal-overlay').waitFor({state:'detached'});
    assert.equal(await member.locator('[data-batch-entry] [name="content"]').inputValue(),'未提交保护验证');
    await member.locator('.tabs-mobile [data-tab="home"]').click();
    await member.locator('[data-modal-confirm]').click();
    await member.locator('.modal-overlay').waitFor({state:'detached'});
    await member.locator('.tabs-mobile [data-tab="register"]').click();
    await member.locator('[data-batch-entry] [name="recognizer_employee_id"] option[value="7"]').waitFor({state:'attached'});
    await member.locator('[data-batch-entry] [name="content"]').fill('前端模块上传验证');
    await member.locator('[data-batch-entry] [name="occurred_attraction_id"]').selectOption('1');
    await member.locator('[data-batch-entry] [name="recognizer_employee_id"]').selectOption('7');
    await member.locator('#recognitionAlbumInput').setInputFiles({name: 'fixture.png', mimeType: 'image/png',
      buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jQ8kAAAAASUVORK5CYII=', 'base64')});
    await member.route('**/api/recognitions/batch',route=>route.abort('connectionfailed'),{times:1});
    await member.locator('[data-batch-submit]').click();
    await member.waitForFunction(()=>document.getElementById('toast').textContent.includes('提交结果暂未确认'));
    const failedKey=await member.locator('#recognitionForm').getAttribute('data-submission-key');
    assert(failedKey);
    assert.equal(await member.locator('[data-batch-entry] [name="content"]').inputValue(),'前端模块上传验证');
    await member.locator('#toast').click();
    await member.locator('[data-batch-submit]').click();
    await member.waitForFunction(() => document.getElementById('toast').textContent.includes('已提交 1 条认可'));
    assert.equal(uploads.length, 1, 'One batch submit must make one request');
    assert.equal(uploads[0].entries[0].content, '前端模块上传验证');
    assert.match(uploads[0].entries[0].recognition_date, /^\d{4}-\d{2}-\d{2}$/);
    assert(uploads[0].key && uploads[0].imageSize > 0);
    assert.equal(uploads[0].key,failedKey,'A retry after an uncertain result must preserve the idempotency key');
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
    mode='member';
    const retryPage=await browser.newPage({viewport:{width:390,height:844}});
    await retryPage.route('**/api/options',route=>route.abort('connectionfailed'),{times:1});
    await retryPage.goto(origin+'/');
    await retryPage.locator('#retryInitialize').waitFor();
    assert.equal(new URL(retryPage.url()).pathname,'/');
    await retryPage.locator('#retryInitialize').click();
    await retryPage.locator('.tabs-mobile [data-tab="register"]').waitFor();
    await retryPage.evaluate(async()=>{
      const {withSubmitLock}=await import('./static/js/app/submission.js');
      const form=document.createElement('form');form.id='submitStatusFixture';form.innerHTML='<button>提交测试</button>';
      document.getElementById('app').append(form);
      window.statusLock=withSubmitLock(form,()=>new Promise(resolve=>window.finishStatusLock=resolve));
    });
    assert.equal(await retryPage.locator('#submitStatusFixture button').isDisabled(),true);
    assert.equal(await retryPage.locator('#submitStatusFixture button').textContent(),'正在提交…');
    await retryPage.evaluate(async()=>{window.finishStatusLock();await window.statusLock;});
    assert.equal(await retryPage.locator('#submitStatusFixture button').textContent(),'提交测试');
    await retryPage.close();
    checks.push('mobile unsaved leave cancel/confirm, uncertain write keeps content/key, startup retry and submit state');

    mode='admin';
    const workPage=await browser.newPage({viewport:{width:390,height:844}});
    const work={id:19,employee_name:'合成员工',employee_no:'SYN19',occurred_on:'2020-01-03',summary:'合成跟进事项',responsible:'负责主管'};
    await workPage.route('**/api/action-center',route=>route.fulfill({json:{month:'2026-10',total:2,items:[{type:'deduction_follow_up',title:'范围内重复违规待处理',count:2,tab:'entries',severity:'warning',description:'合成角色事项'}]}}));
    await workPage.route('**/api/action-center/deduction_follow_up/details*',route=>route.fulfill({json:{type:'deduction_follow_up',title:'重复违规待跟进明细',items:[work],total:2,page:Number(new URL(route.request().url()).searchParams.get('page')||1),page_size:1}}));
    await workPage.goto(origin+'/');
    await workPage.locator('[data-action-type="deduction_follow_up"]').click();
    await workPage.locator('.action-center-detail').getByText('负责人：负责主管').waitFor();
    await workPage.locator('[data-work-next]').click();
    await workPage.locator('.pagination').filter({hasText:'第2页'}).waitFor();
    assert.equal(await workPage.locator('[data-follow-up-register]').count(),0);
    assert.equal(await workPage.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await workPage.setViewportSize({width:1280,height:900});
    await workPage.route(/\/api\/month-closes\/2026-08$/,route=>route.fulfill({json:{month:'2026-08',attraction_name:'热力追踪',is_closed:false,can_close:true,checklist:[{code:'deduction_material',name:'待补材料',count:2,target:null,responsible:'请联系登记人'}]}}));
    await workPage.route('**/api/month-closes/2026-08/blockers/deduction_material*',route=>route.fulfill({json:{items:[work],total:2,page:Number(new URL(route.request().url()).searchParams.get('page')||1),page_size:1}}));
    await workPage.locator('.tabs-desktop [data-tab="monthClose"]').click();
    await workPage.locator('#monthCloseForm [name="month"]').fill('2026-08');
    await workPage.locator('#monthCloseForm [type="submit"]').click();
    await workPage.locator('[data-month-details="deduction_material"]').click();
    await workPage.locator('[data-month-detail-host]').getByText('负责人：负责主管').waitFor();
    await workPage.locator('[data-work-next]').click();
    await workPage.locator('[data-month-detail-host] .pagination').filter({hasText:'第2页'}).waitFor();
    await workPage.close();
    checks.push('mobile scoped follow-up and HR month blockers show owner, history, pagination and no delegated handling');

    const queuePage=await browser.newPage({viewport:{width:390,height:844}});
    const queueScopes=[];
    await queuePage.route('**/api/me',route=>route.fulfill({json:{...user,role_code:'SUPERVISOR',base_role_code:'SUPERVISOR',permissions:['EMPLOYEE_ADD']}}));
    await queuePage.route('**/api/action-center',route=>route.fulfill({json:{month:'2026-10',total:201,items:[{type:'deduction_material',title:'待补充声明材料',count:201,tab:'entries',severity:'warning',description:'合成材料事项'}]}}));
    await queuePage.route('**/api/my-entries?*',route=>route.fulfill({json:{items:[],total:0,page:1,page_size:100}}));
    await queuePage.route('**/api/deductions/pending-materials?*',route=>{const qs=new URL(route.request().url()).searchParams;queueScopes.push(Object.fromEntries(qs));return route.fulfill({json:{items:[],total:201,page:Number(qs.get('page')),page_size:50}});});
    await queuePage.goto(origin+'/');
    await queuePage.locator('[data-action-type="deduction_material"]').click();
    await queuePage.locator('[data-pending-material-scope]').waitFor();
    assert.equal(await queuePage.locator('[data-pending-material-scope]').inputValue(),'all');
    await queuePage.locator('[data-material-next]').click();
    await queuePage.waitForFunction(()=>document.querySelector('[data-material-prev]')?.disabled===false);
    assert(queueScopes.some(qs=>qs.scope==='all'&&qs.page==='2'));
    const filteredResponse=queuePage.waitForResponse(response=>response.url().includes('pending-materials')&&response.url().includes('material_failed'));
    await queuePage.locator('[data-pending-material-status]').selectOption('material_failed');
    await filteredResponse;
    assert(queueScopes.some(qs=>qs.status==='material_failed'&&qs.page==='1'));
    assert.equal(await queuePage.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await queuePage.close();
    checks.push('mobile material todo opens all circles, pages beyond 200 and resets page on failure filtering');
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
        await page.locator('[data-modal-confirm]').click();
        await page.locator('.modal-overlay').waitFor({state:'detached'});
        await page.locator('[data-ann-view=manage]').click();await page.locator('[data-ann-records="51"]').click();
        const followupExport=page.getByRole('link',{name:/下载培训跟进 Excel/});
        await followupExport.waitFor();
        assert.match(await followupExport.getAttribute('href'),/followup-export\?version=1/);
      }else if(mode==='trainer'){
        await page.locator('[data-ann-view=training]').click();await page.locator('[data-ann-training="51"]').click();
        await page.locator('[name=hours]').selectOption('6');await page.getByRole('button',{name:'生成培训码',exact:true}).click();
        await page.locator('[data-ann-current-code]').filter({hasText:'123456'}).waitFor();
        await page.screenshot({path:path.resolve(__dirname,'../../.handover_review/announcement-training-tr-20261011.png'),fullPage:true});
      }else{
        assert.match(await page.locator('.announcement-card-footer').innerText(),/待阅读/);
        await page.locator('[data-ann-open="51"]').click();
        await page.locator('#announcementReceiptForm').waitFor();
        assert.equal(await page.locator('[data-ann-progress]').textContent(),'待培训');
        assert.equal(await page.locator('#announcementSignature').isVisible(),false);
        await page.locator('[name=training_code]').fill('123456');await page.locator('[data-ann-verify-code]').click();
        await page.locator('#announcementSignature').waitFor({state:'visible'});
        assert.equal(await page.locator('[data-ann-progress]').textContent(),'待签字');
        assert(await page.locator('#announcementSignature').evaluate(el=>el.width)>100);
        await page.screenshot({path:path.resolve(__dirname,'../../.handover_review/announcement-training-member-20261011.png'),fullPage:true});
        await page.locator('[name=acknowledged]').check();
        const box=await page.locator('#announcementSignature').boundingBox();
        await page.mouse.move(box.x+20,box.y+30);await page.mouse.down();await page.mouse.move(box.x+140,box.y+100,{steps:8});await page.mouse.up();
        await page.getByRole('button',{name:'提交签字',exact:true}).click();
        await page.locator('[data-ann-view=history][aria-pressed=true]').waitFor();assert(trainingSigned);
        assert.match(await page.locator('.announcement-card-footer').innerText(),/已完成/);
        const progress=await page.evaluate(async()=>{
          const {announcementProgress}=await import('./static/js/app/announcements.js');
          const base={status:'published',version:1,current_version:1,effective:true,effective_on:'2026-01-01',confirmation_level:3,training_required:true,receipt:{status:'pending',seen_at:'2026-01-01'}};
          return [
            announcementProgress({...base,receipt:{status:'pending'}}),
            announcementProgress(base),
            announcementProgress({...base,receipt:{...base.receipt,training_verified_at:'2026-01-01'}}),
            announcementProgress({...base,training_required:false,confirmation_level:2}),
            announcementProgress({...base,receipt:{status:'completed'}}),
            announcementProgress({...base,status:'withdrawn'}),
            announcementProgress({...base,current_version:2}),
            announcementProgress({...base,receipt:{status:'suspended'}}),
          ];
        });
        assert.deepEqual(progress,['待阅读','待培训','待签字','待确认','已完成','已撤下','历史版本','暂缓']);
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
