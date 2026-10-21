const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {createHash} = require('node:crypto');
const {chromium, webkit, firefox} = require('playwright');

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
  recognition_types: [{id: 1, name: '安全', code: 'safety'}, {id: 2,name:'表扬信',code:'LETTER',fixed_score:1,monthly_limit:1}], deduction_types: []};
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
  else if (route === '/api/announcements/51/records') {
    const groups=[{id:1,formal_leader_name:'测试组长<甲>',acting_leader_name:'测试代理乙'},
      {id:2,formal_leader_name:'正式组长丙',acting_leader_name:''},
      {id:3,formal_leader_name:'',acting_leader_name:''},
      {id:4,formal_leader_name:'',acting_leader_name:'代理组长丁'}].map(g=>({...g,key:'group:'+g.id,name:'测试小组'+g.id,attraction_name:'热力追踪',required:1,completed:0}));
    const items=groups.map(g=>({id:g.id,employee_name:'合成人员',employee_no:'SYN'+g.id,group_key:g.key,status:'pending',role_label:'CM'}));
    sendJson(response,{announcement:demoAnnouncement(),items,groups,counts:{required:4,completed:0,pending:4,suspended:0,exited:0},handover:{owner:{name:'测试GSM',employee_no:'GSM1'},history:[]}});
  }
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
  else if (route === '/api/recognizers') sendJson(response, [
    {id: 7, name: '主管测试员', role_name: '主管', score: 1, group_key:'circle:1',group_label:'热力追踪',group_order:0},
    {id: 8, name: '迷宫测试员', role_name: 'TA主管', score: .5, group_key:'circle:2',group_label:'矮人迷宫',group_order:1},
    {id: 9, name: '小熊测试员', role_name: '主管', score: .5, group_key:'circle:3',group_label:'小熊罐子',group_order:2},
    {id: 10, name: '经理测试员', role_name: 'GSM', score: 1, group_key:'tagsm_plus',group_label:'TAGSM及以上',group_order:3},
    {id:'special:LETTER',name:'表扬信',role_name:'固定分值',score:1,special:true},
  ]);
  else if (route === '/api/recognitions/batch') {
    const chunks = []; for await (const chunk of request) chunks.push(chunk);
    const form = await new Request('http://fixture/upload', {method: 'POST', headers: request.headers,
      body: Buffer.concat(chunks)}).formData();
    uploads.push({entries: JSON.parse(form.get('entries')), key: form.get('idempotency_key'),
      imageName: form.get('image').name, imageSize: form.get('image').size,
      imageBytes:Array.from(new Uint8Array(await form.get('image').arrayBuffer())),clientImageReady:form.get('image_client_ready')});
    sendJson(response, {records: JSON.parse(form.get('entries')).map((_,i)=>({id:99+i})), encouragement_options: []});
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
    const engine=process.env.FRONTEND_TEST_ENGINE||'edge';
    const browserType={edge:chromium,webkit,firefox}[engine];
    assert(browserType,`Unknown frontend test engine: ${engine}`);
    browser = await browserType.launch({headless: true, ...(engine==='edge'&&executablePath ? {executablePath} : {})});
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
      assert.equal(await page.locator('#tabs [data-tab="changelog"]').count(),0);
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
      assert.equal(await page.locator('#pageHeading h1').textContent(),'更新记录');
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
      await page.locator('#announcementContent .ann-empty').filter({hasText: '待查收已清空'}).waitFor();
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
    assert.equal(await member.locator('#tabs [data-tab="changelog"]').count(),0);
    await member.locator('[data-open-more]').click();
    await member.locator('.mobile-more-drawer [data-open-about]').click();
    await member.locator('[data-about-updates]').click();
    await member.locator('#app h2').filter({hasText:'更新记录'}).waitFor();
    await member.locator('[data-about-dialog]').waitFor({state:'detached'});
    checks.push('about dialog: desktop, 320/390px mobile, sole changelog entry, names, live version, logo, focus and Escape');
    assert.equal(await member.locator('#tabs [data-tab="hrEmployees"]').count(), 0, 'Members cannot navigate to HR tools');
    await member.locator('.tabs-mobile [data-tab="register"]').click();
    await member.locator('[data-batch-entry] [name="recognizer_employee_id"] option[value="7"]').waitFor({state: 'attached'});
    const signer = member.locator('[data-batch-entry]').first().locator('[name="recognizer_employee_id"]');
    assert.deepEqual(await signer.locator('optgroup').evaluateAll(nodes=>nodes.map(node=>node.label)),
      ['热力追踪','矮人迷宫','小熊罐子','TAGSM及以上']);
    assert.deepEqual(await signer.locator('optgroup option').evaluateAll(nodes=>nodes.map(node=>node.value)),['7','8','9','10']);
    const firstCard=member.locator('[data-batch-entry]').first(), picker=firstCard.locator('[data-batch-recognizer]');
    await picker.locator(':scope > summary').click();
    assert.equal(await picker.locator('.batch-recognizer-group[open]').count(),0,'Groups start collapsed');
    assert.deepEqual(await picker.locator('.batch-recognizer-group > summary').allTextContents(),['热力追踪⌄','矮人迷宫⌄','小熊罐子⌄','TAGSM及以上⌄']);
    const secondGroup=picker.locator('.batch-recognizer-group').nth(1);
    await secondGroup.locator(':scope > summary').click();
    await secondGroup.locator('[data-batch-recognizer-option="8"]').waitFor({state:'visible'});
    await secondGroup.locator(':scope > summary').click();
    assert.equal(await secondGroup.getAttribute('open'),null,'Groups can close without selecting');
    await secondGroup.locator(':scope > summary').click();
    await secondGroup.locator('[data-batch-recognizer-option="8"]').click();
    assert.equal(await picker.getAttribute('open'),null,'Selecting closes the picker');
    assert.equal(await signer.inputValue(),'8');
    assert((await picker.locator(':scope > summary').innerText()).includes('迷宫测试员'));
    assert((await member.locator('[data-entry-score]').innerText()).includes('0.50分'));
    await picker.locator(':scope > summary').click();
    assert.equal(await picker.locator('.batch-recognizer-group[open]').count(),0,'Groups reset after choosing');
    await picker.locator(':scope > summary').press('Escape');
    assert.equal(await picker.getAttribute('open'),null);
    await firstCard.locator('[name=recognition_type_id]').selectOption('2');
    assert.equal(await signer.inputValue(),'special:LETTER','Fixed score keeps automatic special recognizer');
    assert.equal(await picker.locator('.batch-recognizer-group').count(),0);
    await firstCard.locator('[name=recognition_type_id]').selectOption('1');
    for(const width of [320,375,390,430]){
      await member.setViewportSize({width,height:844});
      const boxes=await firstCard.evaluate(card=>{const date=card.querySelector('[name=recognition_date]').getBoundingClientRect(),type=card.querySelector('[name=recognition_type_id]').getBoundingClientRect();return {bottom:date.bottom,top:type.top,left:date.left,right:type.right,width:innerWidth};});
      assert(boxes.top>=boxes.bottom,'Date and category have separate mobile rows');
      assert(boxes.left>=0&&boxes.right<=boxes.width,'Date and category stay inside viewport');
    }
    await member.setViewportSize({width:390,height:844});
    await signer.selectOption('8');
    await member.locator('[data-batch-add]').click();
    const secondSigner=member.locator('[data-batch-entry]').nth(1).locator('[name="recognizer_employee_id"]');
    await secondSigner.locator('optgroup option[value="10"]').waitFor({state:'attached'});
    assert.equal(await secondSigner.locator('optgroup').count(),4);
    await secondSigner.selectOption('10');
    assert.equal(await signer.inputValue(),'8','Batch entries keep independent signer choices');
    await member.locator('[data-batch-entry]').nth(1).locator('[data-entry-remove]').click();
    checks.push('CM/TR batch: collapsed signer groups, expand/close, choose, Escape, special type, independent rows, 320/375/390/430px date layout');
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
    const pickerCompatibility=await member.evaluate(async()=>{
      const {selectedEvidenceFile,readableEvidenceFile,rememberEvidenceFile,clearEvidenceFile}=await import('./static/js/app/submission.js');
      const input=document.getElementById('recognitionAlbumInput'),file=input.files[0];
      Object.defineProperty(input,'files',{configurable:true,get:()=>({0:file,item:()=>{throw new Error('item bridge unavailable');}})});
      const indexedFirst=selectedEvidenceFile(input)===file;
      Object.defineProperty(input,'files',{configurable:true,get:()=>({length:1,item:()=>file})});
      const itemFallback=selectedEvidenceFile(input)===file;
      Object.defineProperty(input,'files',{configurable:true,get:()=>{throw new Error('synthetic native picker bridge');}});
      input.dispatchEvent(new Event('change',{bubbles:true}));
      const recovered=readableEvidenceFile(input.form);
      const empty=document.createElement('form');empty.innerHTML='<input type="file" name="image_album" data-evidence-input>';
      document.body.append(empty);Object.defineProperty(empty.firstChild,'value',{get:()=> 'C:\\fakepath\\unreadable.jpg'});
      const pathRejected=readableEvidenceFile(empty)===null;empty.remove();
      const volatile=document.createElement('form');volatile.innerHTML='<input type="file" name="image_album" data-evidence-input>';
      document.body.append(volatile);const volatileInput=volatile.firstChild;let selection='C:\\fakepath\\fixture.png';
      Object.defineProperty(volatileInput,'value',{configurable:true,get:()=>selection,set:value=>{selection=value;}});
      Object.defineProperty(volatileInput,'files',{configurable:true,get:()=>[file]});rememberEvidenceFile(volatileInput);
      Object.defineProperty(volatileInput,'files',{configurable:true,get:()=>{throw new Error('picker lost access after selection');}});
      const retainedSelection=readableEvidenceFile(volatile)===file;
      rememberEvidenceFile(volatileInput);const changedSelectionRejected=readableEvidenceFile(volatile)===null;
      Object.defineProperty(volatileInput,'files',{configurable:true,get:()=>[file]});rememberEvidenceFile(volatileInput);
      Object.defineProperty(volatileInput,'files',{configurable:true,get:()=>[]});clearEvidenceFile(volatileInput);
      const clearedSelectionRejected=readableEvidenceFile(volatile)===null;volatile.remove();
      return {retainedSelection,changedSelectionRejected,clearedSelectionRejected,indexedFirst,itemFallback,pathRejected,name:recovered.name,bytes:Array.from(new Uint8Array(await recovered.arrayBuffer()))};
    });
    assert(pickerCompatibility.retainedSelection);
    assert(pickerCompatibility.changedSelectionRejected,'A failed new selection cannot reuse the old file');
    assert(pickerCompatibility.clearedSelectionRejected,'A cleared selection cannot reuse the old file');
    assert(pickerCompatibility.indexedFirst);
    assert(pickerCompatibility.itemFallback);
    assert(pickerCompatibility.pathRejected,'A path string must never be accepted as image content');
    assert.equal(pickerCompatibility.name,'fixture.png');
    assert.deepEqual(pickerCompatibility.bytes,Array.from(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jQ8kAAAAASUVORK5CYII=','base64')));
    await member.locator('[data-batch-add]').click();
    const secondUploadEntry=member.locator('[data-batch-entry]').nth(1);
    await secondUploadEntry.locator('[name="content"]').fill('第二条共用照片验证');
    await secondUploadEntry.locator('[name="occurred_attraction_id"]').selectOption('1');
    await secondUploadEntry.locator('[name="recognizer_employee_id"]').selectOption('7');
    await member.route('**/api/recognitions/batch',route=>route.abort('connectionfailed'),{times:1});
    await member.locator('[data-batch-submit]').click();
    await member.waitForFunction(()=>document.getElementById('toast').textContent.includes('提交结果暂未确认'));
    const failedKey=await member.locator('#recognitionForm').getAttribute('data-submission-key');
    assert(failedKey);
    assert.equal(await member.locator('[data-batch-entry] [name="content"]').first().inputValue(),'前端模块上传验证');
    await member.locator('#toast').click();
    await member.locator('[data-batch-submit]').click();
    await member.waitForFunction(() => document.getElementById('toast').textContent.includes('已提交 2 条认可'));
    assert.equal(uploads.length, 1, 'One batch submit must make one request');
    assert.equal(uploads[0].entries[0].content, '前端模块上传验证');
    assert.equal(uploads[0].entries.length,2);
    assert.equal(uploads[0].entries[1].content,'第二条共用照片验证');
    assert.match(uploads[0].entries[0].recognition_date, /^\d{4}-\d{2}-\d{2}$/);
    assert(uploads[0].key && uploads[0].imageSize > 0);
    assert.equal(uploads[0].key,failedKey,'A retry after an uncertain result must preserve the idempotency key');
    assert.equal(uploads[0].imageName, 'fixture.png');
    assert.deepEqual(uploads[0].imageBytes,pickerCompatibility.bytes,'The server must receive the exact original image bytes');
    assert.equal(uploads[0].clientImageReady,'1');
    await member.locator('#recognitionAlbumInput').setInputFiles({name:'fixture.png',mimeType:'image/png',buffer:Buffer.from(pickerCompatibility.bytes)});
    const invalidReplacement=await member.evaluate(async()=>{
      const {readableEvidenceFile}=await import('./static/js/app/submission.js');
      const camera=document.getElementById('recognitionCameraInput');
      Object.defineProperty(camera,'value',{configurable:true,get:()=> 'C:\\fakepath\\unreadable.jpg'});
      Object.defineProperty(camera,'files',{configurable:true,get:()=>[new File([],'unreadable.jpg',{type:'image/jpeg'})]});
      camera.dispatchEvent(new Event('change',{bubbles:true}));
      return {oldCleared:document.getElementById('recognitionAlbumInput').value==='',noOldFile:readableEvidenceFile(camera.form)===null};
    });
    assert(invalidReplacement.oldCleared&&invalidReplacement.noOldFile,'An unreadable replacement must not silently submit the previous photo');
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
    assert.deepEqual(memberErrors, []); checks.push('mobile member registration: native FileList.item/form fallback, exact image bytes, two entries, date fallback and idempotency');
    await member.close();
    mode='member';
    const retryPage=await browser.newPage({viewport:{width:390,height:844}});
    await retryPage.route('**/api/options',route=>route.abort('connectionfailed'),{times:1});
    await retryPage.goto(origin+'/');
    await retryPage.locator('#retryInitialize').waitFor();
    assert.equal(new URL(retryPage.url()).pathname,'/');
    await retryPage.locator('#retryInitialize').click();
    await retryPage.locator('.tabs-mobile [data-tab="register"]').waitFor();
    // Open a real view and wait for it, rather than racing an asynchronous home render.
    await retryPage.locator('.tabs-mobile [data-tab="register"]').click();
    await retryPage.locator('[data-batch-entry] [name="recognizer_employee_id"] option[value="7"]').waitFor({state:'attached'});
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
    // Changing scope invalidates both actions and pending responses from the old scope.
    await workPage.locator('#monthCloseForm [name="month"]').fill('2026-07');
    assert.equal(await workPage.locator('[data-month-close]').count(),0);
    let releaseMonth;
    const monthGate=new Promise(resolve=>releaseMonth=resolve);
    await workPage.route(/\/api\/month-closes\/2026-07(?:\?|$)/,async route=>{await monthGate;await route.fulfill({json:{month:'2026-07',attraction_name:'旧范围',is_closed:false,can_close:true,checklist:[]}});});
    const oldMonthResponse=workPage.waitForResponse(response=>response.url().includes('/month-closes/2026-07'));
    await workPage.locator('#monthCloseForm [type="submit"]').click();
    await workPage.locator('#monthCloseForm [name="month"]').fill('2026-06');
    releaseMonth();await oldMonthResponse;
    await workPage.waitForLoadState('networkidle');
    assert.match(await workPage.locator('#monthCloseResult').textContent(),/筛选条件已变化/);
    assert.equal(await workPage.locator('[data-month-close]').count(),0);
    await workPage.locator('#monthCloseForm [type="submit"]').click();
    await workPage.locator('[data-month-close]').click();
    await workPage.locator('[data-modal-input]').fill('合成原因');
    await workPage.evaluate(()=>{const input=document.querySelector('#monthCloseForm [name="month"]');input.value='2026-05';input.dispatchEvent(new Event('input',{bubbles:true}));});
    const writesBefore=requests.filter(r=>r.method==='POST'&&r.url.includes('/month-closes/')).length;
    await workPage.locator('[data-modal-confirm]').click();
    await workPage.locator('.modal-overlay').waitFor({state:'detached'});
    assert.equal(requests.filter(r=>r.method==='POST'&&r.url.includes('/month-closes/')).length,writesBefore,'An outdated confirmation cannot write to the old month');
    await workPage.locator('#monthCloseForm [type="submit"]').click();
    await workPage.locator('[data-month-close]').waitFor();
    await workPage.evaluate(()=>{const b=document.querySelector('[data-month-close]');b.click();b.click();});
    assert.equal(await workPage.locator('[data-modal-input]').count(),1,'Rapid clicks must open only one confirmation');
    let releaseClose;const closeGate=new Promise(resolve=>releaseClose=resolve);
    await workPage.route('**/api/month-closes/2026-05/close',async route=>{await closeGate;await route.fulfill({status:409,json:{detail:'合成冲突，请重新查询'}});});
    const closeStarted=workPage.waitForRequest(request=>request.method()==='POST'&&request.url().includes('/2026-05/close'));
    await workPage.locator('[data-modal-input]').fill('合成关闭原因');
    await workPage.locator('[data-modal-confirm]').click();await closeStarted;
    await workPage.locator('.modal-overlay').waitFor({state:'detached'});
    await workPage.locator('#monthCloseForm [type="submit"]').click();
    await workPage.locator('[data-month-close]').click();
    assert.equal(await workPage.locator('[data-modal-input]').count(),0,'Requery cannot bypass an in-flight month-close write lock');
    const closeFailed=workPage.waitForResponse(response=>response.url().includes('/2026-05/close'));
    releaseClose();await closeFailed;await workPage.waitForLoadState('networkidle');
    await workPage.locator('[data-month-close]').click();
    await workPage.locator('[data-modal-input]').waitFor();
    await workPage.locator('[data-modal-cancel]').click();
    checks.push('month-close filter invalidates old actions, delayed reads and outdated confirmation');
    await workPage.route('**/api/hr-monthly-reports/options',route=>route.fulfill({json:{attractions:[],templates:[{id:'formal',name:'正式',description:'合成模板'}],sections:[{id:'photos',name:'活动照片'}]}}));
    await workPage.route('**/api/hr-monthly-reports/preview?*',route=>route.fulfill({json:{digest:'synthetic',report:{month:new URL(route.request().url()).searchParams.get('month'),attraction_id:null,scope_name:'合成范围',draft:true,organization_basis:'合成口径',summary:{employee_count:0,recognition_count:0,deduction_count:0,recognition_rate:null,recognition_score:0,deduction_score:0},warnings:[],circles:[],candidates:[],deductions:[],recognitions:[],issuers:[],groups:[],rankings:[]}}}));
    await workPage.evaluate(async()=>{const {state}=await import('./static/js/app/state.js');state.me.permissions.push('HR_MONTHLY_REPORT');const {renderTabs,gotoTab}=await import('./static/js/app/navigation.js');renderTabs();gotoTab('hrMonthlyReport');});
    await workPage.locator('#hrReportExport:not([disabled])').waitFor();
    await workPage.locator('.report-materials > summary').click();
    await workPage.locator('#hrReportNotes').fill('合成月报说明保留验证');
    await workPage.locator('#hrReportPhotos').setInputFiles({name:'synthetic.jpg',mimeType:'image/jpeg',buffer:Buffer.from('synthetic-photo')});
    await workPage.locator('.tabs-desktop [data-tab="actionCenter"]').click();
    await workPage.locator('[data-modal-cancel]').click();
    // Dismissal resolves before the 160ms exit animation removes the layer.
    // Wait for that lifecycle to finish before testing another leave dialog.
    await workPage.locator('.modal-overlay').waitFor({state:'detached'});
    assert.equal(await workPage.locator('#hrReportNotes').inputValue(),'合成月报说明保留验证');
    assert.equal(await workPage.locator('#hrReportPhotos').evaluate(input=>input.files.length),1);
    let releaseExport;const exportGate=new Promise(resolve=>releaseExport=resolve);let exportRequests=0;
    await workPage.route('**/api/hr-monthly-reports/export',async route=>{exportRequests++;await exportGate;await route.fulfill({status:500,json:{detail:'合成导出失败'}});});
    await workPage.locator('#hrReportExport').click();
    await workPage.locator('#hrReportFilter [name="month"]').fill('2026-04');
    await workPage.locator('#hrReportFilter [type="submit"]').click();
    await workPage.locator('#hrReportPreview h3').filter({hasText:'2026-04'}).waitFor();
    assert(await workPage.locator('#hrReportExport').isDisabled(),'A new preview must not unlock an active export');
    await workPage.evaluate(()=>document.getElementById('hrReportExport').onclick());
    assert.equal(exportRequests,1);
    releaseExport();await workPage.locator('#hrReportExport:not([disabled])').waitFor();
    assert.equal(await workPage.locator('#hrReportNotes').inputValue(),'合成月报说明保留验证');
    assert.equal(await workPage.locator('#hrReportPhotos').evaluate(input=>input.files.length),1);
    await workPage.locator('.tabs-desktop [data-tab="actionCenter"]').click();
    await workPage.locator('[data-modal-confirm]').click();
    await workPage.locator('#hrReportFilter').waitFor({state:'detached'});
    checks.push('monthly report draft leave protection, failure retains text/files, preview cannot unlock active export');
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
        if(await page.locator('.ann-workspaces>summary').count())await page.locator('.ann-workspaces>summary').click();await page.locator('[data-ann-view=manage]').click();await page.locator('[data-ann-records="51"]').click();
        const followupExport=page.getByRole('link',{name:/下载培训跟进 Excel/});
        await followupExport.waitFor();
        assert.match(await followupExport.getAttribute('href'),/followup-export\?version=1/);
        let exportRequests=0,exportFailure=false,releaseExport;
        const exportGate=new Promise(resolve=>{releaseExport=resolve;});
        await page.route('**/api/announcements/51/export?**',async route=>{
          exportRequests++;
          if(exportFailure){await route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:'签字凭据摘要不匹配，导出已停止'})});return;}
          await exportGate;
          await route.fulfill({status:200,contentType:'application/pdf',headers:{'Content-Disposition':'attachment; filename="announcement-51-v1.pdf"'},body:'%PDF-1.4\npreview fixture'});
        });
        const pdfExport=page.locator('.announcement-export-pdf');
        await pdfExport.click();
        await page.getByText('正在生成报告…',{exact:true}).waitFor();
        assert.equal(await pdfExport.getAttribute('aria-disabled'),'true');
        await pdfExport.evaluate(el=>el.click());
        await page.locator('.announcement-export-excel').evaluate(el=>el.click());
        const downloadResult=page.waitForEvent('download');releaseExport();
        assert.equal((await downloadResult).suggestedFilename(),'announcement-51-v1.pdf');
        await page.getByRole('link',{name:/下载签收 PDF/}).waitFor();
        assert.equal(exportRequests,1);
        exportFailure=true;
        await pdfExport.click();
        await page.getByText('签字凭据摘要不匹配，导出已停止',{exact:true}).waitFor();
        assert.equal(await pdfExport.getAttribute('aria-disabled'),null);
        assert.equal(exportRequests,2);
        checks.push('announcement report download busy state, duplicate-click guard, filename and integrity failure');
        const leaders=page.locator('.announcement-group-leaders');
        assert.deepEqual(await leaders.allTextContents(),['组长：测试组长<甲>　｜　代理组长：测试代理乙','组长：正式组长丙','负责人未设置','代理组长：代理组长丁']);
        for(const width of [320,390,1280]){
          await page.setViewportSize({width,height:900});
          assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
        }
        await page.locator('[data-ann-record-group]').selectOption('group:2');
        assert.equal(await leaders.count(),1);
        assert.equal(await leaders.first().textContent(),'组长：正式组长丙');
        await page.locator('[data-ann-record-group]').selectOption('');
      }else if(mode==='trainer'){
        if(await page.locator('.ann-workspaces>summary').count())await page.locator('.ann-workspaces>summary').click();await page.locator('[data-ann-view=training]').click();await page.locator('[data-ann-training="51"]').click();
        await page.locator('[name=hours]').selectOption('6');await page.getByRole('button',{name:'生成培训码',exact:true}).click();
        await page.locator('[data-ann-current-code]').filter({hasText:'123456'}).waitFor();
        await page.screenshot({path:path.resolve(__dirname,'../../.handover_review/announcement-training-tr-20261011.png'),fullPage:true});
      }else{
        assert.match(await page.locator('.ann-receipt-state').innerText(),/待阅读/);
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
        await page.locator('#announcementSignature').scrollIntoViewIfNeeded();const box=await page.locator('#announcementSignature').boundingBox();
        await page.mouse.move(box.x+20,box.y+30);await page.mouse.down();await page.mouse.move(box.x+140,box.y+100,{steps:8});await page.mouse.up();
        await page.getByRole('button',{name:'提交签字',exact:true}).click();
        try{await page.locator('[data-ann-view=history][aria-pressed=true]').waitFor();}catch(error){await page.screenshot({path:path.resolve(__dirname,'../../.handover_review/announcement-submit-failure.png'),fullPage:true});console.error(await page.locator('#toast').innerText(),await page.locator('#announcementContent').innerText());throw error;}assert(trainingSigned);
        assert.match(await page.locator('.ann-receipt-state').innerText(),/已完成/);
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
    mode='admin';
    const personnel=await browser.newPage({viewport:{width:1280,height:900}});
    const person={id:77,employee_no:'SYN77',name:'合成人员',role_code:'CM',base_role_code:'CM',base_role_name:'CM',
      attraction_id:1,attraction_name:'热力追踪',employment_status:'active',is_active:true,account_enabled:true,
      login_account:'SYN77',account_deletion_eligible:false,account_deletion_reason:'请先停用并保存，连续满7天后可删除'};
    let archived=false,editBody;
    await personnel.route('**/api/hr/organization',route=>route.fulfill({json:{circles:[{id:1,name:'热力追踪',gsm_names:[],ta_gsm_names:[],groups:[],supervisors:[],managers:[],inactive:[],unassigned:archived?[]:[person]}],others:[],warnings:[]}}));
    await personnel.route('**/api/hr/employees/77',route=>{assert.equal(route.request().method(),'PUT');editBody=route.request().postDataJSON();person.account_enabled=editBody.account_enabled;person.account_deletion_reason='2026-10-24 12:00:00 起可删除（须连续停用满7天）';return route.fulfill({json:{ok:true,warnings:[]}});});
    await personnel.route('**/api/hr/employees/77/account',route=>{assert.equal(route.request().method(),'DELETE');archived=true;return route.fulfill({json:{ok:true,archive_retained:true}});});
    await personnel.goto(origin+'/');
    await personnel.locator('.tabs-desktop [data-tab="hrEmployees"]').click();
    await personnel.locator('[data-hr-edit="77"]').click();
    await personnel.locator('[data-modal-form]').waitFor();
    assert.equal(await personnel.locator('[name="employment_status"] option[value="terminated"]').count(),0);
    assert.equal(await personnel.locator('[data-delete-login-account]').isDisabled(),true);
    await personnel.setViewportSize({width:390,height:844});
    assert.equal(await personnel.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await personnel.locator('[name="enabled"]').selectOption('false');
    await personnel.locator('[data-modal-form] button[type="submit"]').click();
    await personnel.locator('[data-modal-form]').waitFor({state:'detached'});
    assert.equal(editBody.account_enabled,false);assert.equal(editBody.employment_status,'active');
    await personnel.locator('[data-hr-edit="77"]').click();
    assert.equal(await personnel.locator('[data-delete-login-account]').isDisabled(),true);
    assert.match(await personnel.locator('[data-modal-form]').textContent(),/连续停用满7天/);
    await personnel.locator('[data-modal-cancel]').click();
    await personnel.locator('[data-modal-form]').waitFor({state:'detached'});
    person.account_deletion_eligible=true;person.account_deletion_reason='可删除，历史数据留档';
    await personnel.evaluate(async()=>{const {renderHrEmployees}=await import('./static/js/app/hr-employees.js');await renderHrEmployees();});
    await personnel.locator('[data-hr-edit="77"]').click();
    assert.equal(await personnel.locator('[data-delete-login-account]').textContent(),'删除');
    await personnel.locator('[data-delete-login-account]').click();
    await personnel.locator('[data-modal-confirm]').click();
    await personnel.locator('[data-modal-form]').waitFor({state:'detached'});
    await personnel.locator('[data-hr-person="77"]').waitFor({state:'detached'});
    assert(archived);await personnel.close();
    checks.push('employee editor: no departure option, disable/save, wait reason, delete confirmation and removal at mobile width');
    mode = 'password';
    const password = await browser.newPage();
    await password.goto(`${origin}/`);
    await password.locator('#requiredPasswordForm').waitFor();
    assert.equal(await password.locator('#tabs [data-tab]').count(), 0);
    assert.equal(await password.locator('#requiredPasswordForm button[type="submit"]').isDisabled(), true);
    checks.push('first-login password gate'); await password.close();
    assert.deepEqual(missingApis, [], 'Every fixture business request must have an intentional mock');
    console.log(JSON.stringify({status: 'passed', engine, modules: moduleFiles.length, checks, uploads: uploads.length}, null, 2));
  } finally {
    releaseReset?.();
    if (browser) await browser.close();
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => {console.error(error); process.exitCode = 1;});
