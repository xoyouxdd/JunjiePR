'use strict';

const assert = require('node:assert/strict');
const {chromium} = require('./support/browser-runtime.cjs');
const {routeFrontend} = require('./support/frontend-page.cjs');

(async () => {
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 900}});
    const errors = [];
    const requests = [];
    let conflict = false;
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/**', async route => {
      const request = route.request();
      const url = new URL(request.url());
      const key = url.pathname;
      requests.push([request.method(), key, Object.fromEntries(url.searchParams), request.postData()]);
      if (key === '/api/files/1') return route.fulfill({contentType: 'image/svg+xml',
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="80"><rect width="200" height="80"/></svg>'});
      if (key === '/api/files/2') return route.fulfill({status: 204});
      const fixture = {
        '/api/action-center': {total: 2},
        '/api/deduction-upgrades/pending': {items: [{}], notices: [{}]},
        '/api/deduction-upgrades/recovery': {items: [{}]},
        '/api/sick-leaves/overlap-check': conflict
          ? {conflict: true, detail: {message: '已有缺勤登记，日期有交集', records: []}}
          : {conflict: false},
        '/api/sick-leaves': {attendance_score: 12},
        '/api/sick-leave-imports/records': {items: [
          {id: 1, record_type: 'sick_leave', employee_name: '有效病假', status: 'active', leave_days: 1},
          {id: 2, record_type: 'sick_leave', employee_name: '历史覆盖病假', status: 'covered', leave_days: 1},
        ]},
      };
      if (!(key in fixture)) return route.fulfill({status: 404, json: {detail: 'Unexpected fixture request'}});
      return route.fulfill({json: fixture[key]});
    });
    const url = await routeFrontend(page, {html: `<!doctype html>
      <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
      <link rel="stylesheet" href="/static/css/style.css">
      <main id="app"></main><nav id="tabs"></nav><div id="toast"></div>
      <span data-action-center-badge hidden></span>`});
    await page.goto(url);
    const actual = await page.evaluate(async () => {
      const {state} = await import('/static/js/app/state.js');
      const feedback = await import('/static/js/app/feedback.js');
      const navigation = await import('/static/js/app/navigation.js');
      const absence = await import('/static/js/app/absence.js');
      const records = await import('/static/js/app/records.js');
      const {bindDateSubmissionFallbacks, requireSubmittedDate} = await import('/static/js/app/submission.js');
      const {deductionMaterialPickerMarkup} = await import('/static/js/app/deduction-materials.js');
      const storage = [];
      navigator.setAppBadge = value => { storage.push(value); return Promise.resolve(); };
      navigator.clearAppBadge = () => { storage.push(0); return Promise.resolve(); };
      const registration = {record: {status: 'confirmed', fraction: 1, credited_fraction: .5, employee_name: '示例员工'}};
      state.options = {attractions: [], employee_circles: []};
      state.me = {id: 1, employee_no: '1234567', role_code: 'CM', permissions: []};
      const frontlineFeedback = feedback.showPerformanceRegistrationFeedback('recognition', registration);
      state.me = {...state.me, role_code: 'GSM', permissions: ['DEDUCTION_ALL','SICK_LEAVE_IMPORT','SICK_REGISTER']};
      const duplicateFeedback = feedback.showPerformanceRegistrationFeedback('recognition', {duplicate: true});
      const duplicateDialog = !!document.querySelector('.performance-feedback');
      const variants = [
        ['recognition', registration.record],
        ['recognition', {status: 'pending', fraction: 1}],
        ['deduction', {status: 'active', points: 2}],
        ['deduction', {status: 'pending_material', material_status: 'missing'}],
        ['deduction', {status: 'material_processing', material_status: 'processing'}],
        ['upgrade', {status: 'pending_upgrade', points: 0}],
      ].map(([kind, record]) => {
        const {score, status, title, active} = feedback.performanceFeedbackData(kind, record);
        return {score, status, title, active};
      });
      feedback.showPerformanceRegistrationFeedback('recognition', registration);
      await navigation.refreshActionBadge();
      navigation.setActionBadge(120);
      const largeCount = document.querySelector('[data-action-center-badge]').textContent;
      navigation.setActionBadge(0);
      const badge = document.querySelector('[data-action-center-badge]');
      const rows = [
        {record_type: 'sick_leave', status: 'active'},
        {record_type: 'sick_leave', status: 'covered'},
        {record_type: 'deduction', status: 'covered'},
        {record_type: 'sick_leave', status: 'void'},
        {record_type: 'sick_leave', status: 'covered'},
      ];
      const {visible, covered} = records.splitCoveredSickRecords(rows);
      const sample = {id: 1, recognition_date: '2026-10-08', fraction: 1};
      const draft = records.recognitionCard(sample, 0, false);
      const history = records.recognitionCard(sample, 0, true);
      const inputs = document.createElement('div');
      inputs.innerHTML = deductionMaterialPickerMarkup();
      document.body.append(inputs);
      const nativePickers = [...inputs.querySelectorAll('input[type=file]')].map(input => ({
        linked: !!input.labels?.length, capture: input.getAttribute('capture'),
      }));
      inputs.remove();
      const dateForm = document.createElement('form');
      dateForm.innerHTML = '<input type="date" name="occurred_on">';
      const date = dateForm.elements.occurred_on;
      date.value = '2026-10-08';
      bindDateSubmissionFallbacks(dateForm);
      const payload = new FormData();
      requireSubmittedDate(payload, date, '事件日期');
      date.value = '';
      date.dispatchEvent(new Event('input'));
      let emptyDateError;
      try { requireSubmittedDate(new FormData(), date, '事件日期'); }
      catch (error) { emptyDateError = error.message; }
      const roles = ['CM','TR','SUPERVISOR','HR_ADMIN','HR_CIRCLE','SYSTEM_ADMIN'].map(role => {
        state.me.role_code = role;
        return [role, navigation.menuItems().map(([id]) => id)];
      });
      state.me.role_code = 'GSM';
      window.sharedBehavior = absence;
      return {frontlineFeedback, duplicateFeedback, duplicateDialog, variants, storage, largeCount,
        badgeHidden: badge.hidden, visible: visible.length, covered: covered.map(row => rows.indexOf(row)),
        empty: records.coveredSickDisclosure([], 'unused'), roles, draft, history, nativePickers,
        submittedDate: payload.get('occurred_on'), emptyDateError};
    });
    assert.equal(actual.frontlineFeedback, false);
    assert.equal(actual.duplicateFeedback, true);
    assert.equal(actual.duplicateDialog, false);
    assert.equal(actual.variants[0].score, '+0.50 分');
    assert.deepEqual(actual.variants.map(row => row.status), ['已生效','待审核','已生效','待补材料','材料处理中','待升级审核']);
    assert.deepEqual(actual.variants.map(row => row.active), [true,false,true,false,false,false]);
    assert.equal(actual.variants[2].title, '登记成功，感谢你的认真记录。');
    assert.equal(actual.variants[5].score, '审核后确定');
    assert.deepEqual(actual.storage, [5,99,0]);
    assert.equal(actual.largeCount, '99+');
    assert.equal(actual.badgeHidden, true);
    assert.deepEqual(requests.map(([, path]) => path).sort(), ['/api/action-center','/api/deduction-upgrades/pending','/api/deduction-upgrades/recovery']);
    assert.equal(actual.visible, 3);
    assert.deepEqual(actual.covered, [1,4]);
    assert.equal(actual.empty, '');
    assert.match(actual.draft, /withdraw-icon/);
    assert.doesNotMatch(actual.history, /withdraw-icon/);
    assert.equal(actual.nativePickers.length, 3);
    assert(actual.nativePickers.every(input => input.linked));
    assert.equal(actual.nativePickers.filter(input => input.capture === 'environment').length, 1);
    assert.equal(actual.submittedDate, '2026-10-08');
    assert.match(actual.emptyDateError, /事件日期/);
    assert(actual.roles.every(([, tabs]) => tabs.includes('password')));
    const menus = Object.fromEntries(actual.roles);
    assert(menus.CM.includes('home') && menus.TR.includes('home'));
    assert(menus.SUPERVISOR.includes('members') && menus.SUPERVISOR.includes('review'));
    assert(!menus.HR_ADMIN.includes('monthClose'));
    assert(menus.HR_CIRCLE.includes('monthClose') && menus.SYSTEM_ADMIN.includes('monthClose'));
    const modal = page.getByRole('dialog');
    await modal.getByRole('button', {name: '关闭反馈'}).click();
    await modal.waitFor({state: 'detached'});

    await page.evaluate(() => window.sharedBehavior.renderSickLeaveImport());
    await page.getByRole('tab', {name: '单人病假登记（备用）'}).click();
    const backup = page.locator('#singleSickLeaveForm');
    assert.equal(await backup.locator('[data-employee-search]').isVisible(), true);
    assert.equal(await backup.locator('[data-global-search]').getAttribute('data-global-search'), '1');
    assert.equal(await backup.locator('[data-global-search]').getAttribute('data-employee-endpoint'), '/api/sick-leave-imports/employee-targets');
    assert.equal(await backup.locator('input[type=file], [name=proof]').count(), 0);
    for (const field of ['leave_type','leave_start_date','leave_end_date','leave_days','note']){
      assert.equal(await backup.locator(`[name=${field}]`).count(), 1);
    }
    assert.equal(await backup.locator('[name=leave_days]').getAttribute('step'), '0.5');
    assert.equal(await backup.locator('.form-sticky-actions').count(), 1);
    const folded = page.locator('.covered-sick-disclosure');
    await folded.waitFor();
    assert.equal(await folded.evaluate(node => node.open), false);
    assert.match(await folded.innerText(), /已覆盖病假（1 条）/);
    assert.equal(await page.getByText('历史覆盖病假', {exact: false}).first().isVisible(), false);
    await folded.locator('summary').click();
    assert.match(await folded.innerText(), /历史覆盖病假/);
    assert.equal(await folded.evaluate(node => getComputedStyle(node.querySelector('summary span'), '::before').content), '"收起"');

    await page.evaluate(() => window.sharedBehavior.renderAbsence());
    const absence = page.locator('#sickForm');
    const dateInput = field => absence.locator(`[name=${field}]`);
    await dateInput('leave_start_date').fill('2026-10-08');
    await dateInput('leave_end_date').fill('2026-10-07');
    assert.match(await absence.locator('#sickDateError').innerText(), /结束日期不能早于开始日期/);
    assert.equal(await dateInput('leave_end_date').evaluate(node => node.checkValidity()), false);
    await dateInput('leave_end_date').fill('2026-10-08');
    await absence.locator('[name=proof]').setInputFiles({name: 'proof.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.7')});
    conflict = true;
    await absence.evaluate(node => {
      node.querySelector('[name=employee_id]').value = '99';
      node.querySelector('[data-employee-search]').dataset.selected = '99';
      node.querySelector('[name=employee_id]').dispatchEvent(new Event('change', {bubbles: true}));
    });
    await absence.getByText('已有缺勤登记，日期有交集', {exact: true}).waitFor();
    assert.equal(await dateInput('leave_start_date').evaluate(node => node.checkValidity()), false);
    assert(requests.some(([, path, query]) => path === '/api/sick-leaves/overlap-check' && query.employee_id === '99'));
    assert(!requests.some(([method, path]) => method === 'POST' && path === '/api/sick-leaves'));
    conflict = false;
    await dateInput('leave_end_date').fill('2026-10-09');
    await dateInput('leave_end_date').fill('2026-10-08');
    const posted = page.waitForRequest(request => request.method() === 'POST' && new URL(request.url()).pathname === '/api/sick-leaves');
    await absence.getByRole('button', {name: '提交缺勤登记'}).click();
    const body = (await posted).postData();
    for (const [field, value] of Object.entries({employee_id: '99', leave_start_date: '2026-10-08',
      leave_end_date: '2026-10-08', leave_days: '1'})) {
      assert(body.includes(`name="${field}"\r\n\r\n${value}`), field);
    }
    assert(body.includes('name="proof"; filename="proof.pdf"'));

    await page.evaluate(async () => {
      const {deductionMaterialPickerMarkup, bindDeductionMaterialPicker} = await import('/static/js/app/deduction-materials.js');
      const host = document.getElementById('app');
      host.innerHTML = deductionMaterialPickerMarkup();
      bindDeductionMaterialPicker(host);
    });
    const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+Xdy8AAAAASUVORK5CYII=', 'base64');
    const album = page.locator('[data-material-album-input]');
    const image = name => ({name, mimeType: 'image/png', buffer: png});
    await album.setInputFiles([image('first.png'), image('second.png')]);
    assert.equal(await page.locator('[data-material-preview]').count(), 2);
    assert.match(await page.locator('[data-material-preview]').first().getAttribute('src'), /^blob:/);
    await page.getByRole('button', {name: '删除第1张照片'}).click();
    assert.match(await page.locator('[data-material-summary]').innerText(), /second.png/);
    assert.doesNotMatch(await page.locator('[data-material-summary]').innerText(), /first.png/);
    await page.locator('[data-material-replace-photos]').dispatchEvent('click');
    assert.equal(await page.locator('[data-material-preview]').count(), 0);
    await album.setInputFiles(image('replacement.png'));
    assert.match(await page.locator('[data-material-summary]').innerText(), /replacement.png/);
    await page.locator('[data-material-pdf-input]').setInputFiles({name: 'new.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.7')});
    assert.match(await page.locator('[data-material-summary]').innerText(), /new.pdf/);
    assert.equal(await page.locator('[data-material-preview]').count(), 0);
    await page.locator('[data-material-remove-pdf]').click();
    assert.match(await page.locator('[data-material-summary]').innerText(), /请选择一种材料方式/);

    await page.evaluate(async () => {
      const {attachmentControl, bindFilePreviews} = await import('/static/js/app/files.js');
      const host = document.getElementById('app');
      host.innerHTML = attachmentControl('/api/files/1', '横向材料', 'image')
        + attachmentControl('/api/files/2', '声明 PDF', 'pdf');
      bindFilePreviews(host);
    });
    await page.getByRole('button', {name: '横向材料'}).click();
    const picture = page.locator('[data-image-preview-image].is-landscape');
    await picture.waitFor();
    assert.equal(await picture.evaluate(node => node.naturalWidth), 200);
    await page.getByRole('button', {name: '关闭图片预览'}).click();
    await page.locator('.image-preview-dialog').waitFor({state: 'detached'});
    const pdfRequest = page.waitForRequest(request => request.url().endsWith('/api/files/2?preview=1'));
    await page.getByRole('button', {name: '声明 PDF'}).click();
    assert.equal((await pdfRequest).isNavigationRequest(), true);
    assert.equal(await page.locator('.pdf-preview-dialog').count(), 0);
    assert.deepEqual(errors, []);
    console.log('Real frontend modules: role menus, badge totals, accessible feedback, covered sick leave and date/overlap guards passed.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
