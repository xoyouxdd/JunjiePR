// Run with Node.js and Playwright installed; exercises only a local DOM fixture.
const assert = require('node:assert/strict');
const { chromium } = require('./support/browser-runtime.cjs');
const {routeFrontend} = require('./support/frontend-page.cjs');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 875, height: 900 } });
    const html = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
      <link rel="stylesheet" href="/static/css/style.css">
      <link rel="stylesheet" href="/static/css/workspace-ui.css">
      <main id="app" data-workspace-ui style="padding:20px;max-width:800px"></main><div id="tabs"></div><div id="toast"></div>`;
    await page.goto(await routeFrontend(page,{html}));
    await page.route('**/api/employee-targets?*', route=>route.fulfill({json:{
      items:[{id:17,employee_no:'DEMO17',name:'样例实习生',role_name:'CM',is_intern:true}],total:1,
    }}));
    await page.evaluate(async () => {
      const {state}=await import('/static/js/app/state.js');
      const {deductionForm,bindDeductionUpgradeHint}=await import('/static/js/app/deductions.js');
      const {bindEmployeeSearches}=await import('/static/js/app/employee-picker.js');
      state.me={role_code:'GSM',permissions:['DEDUCTION_ALL']};
      state.options={deduction_levels:[
        {id:1,code:'STATEMENT',name:'声明',points:1},{id:2,code:'MEMO',name:'备忘录',points:3},
        {id:3,code:'WARNING_1',name:'一级警告',points:5}
      ],deduction_types:[
        {id:11,name:'考勤-早打卡',code:'ATT_EARLY_CLOCK',repeat_check:true},
        {id:12,name:'考勤-迟到30分钟内',code:'ATT_LATE_WITHIN_30',repeat_check:true},
        {id:13,name:'安全',code:'SAFETY',repeat_check:false},
        {id:14,name:'违规病假',code:'SICK_LEAVE_VIOLATION',repeat_check:true}
      ]};
      document.getElementById('app').innerHTML=deductionForm();
      bindDeductionUpgradeHint();
      bindEmployeeSearches(document.getElementById('app'));
      window.typeChanges=0;
      document.getElementById('deductionTypeSelect').addEventListener('change',()=>window.typeChanges++);
    });
    const trigger = page.locator('.deduction-type-picker .recognizer-trigger');
    const menu = page.locator('#deductionTypeMenu');
    const note = trigger.locator('.deduction-upgrade-note');
    const levelSelect = page.locator('#deductionLevelSelect');
    await page.locator('[data-employee-search]').fill('DEMO17');
    await page.getByRole('button',{name:/样例实习生/}).click();
    assert.equal(await levelSelect.locator('option[value="2"]').textContent(),'备忘录 · 3.00分');
    assert.equal(await levelSelect.locator('option[value="3"]').textContent(),'备忘录（参考全职一级警告） · 5.00分');
    await page.getByRole('button',{name:'重新选择'}).click();
    assert.equal(await levelSelect.locator('option[value="3"]').textContent(),'一级警告 · 5.00分');
    await levelSelect.selectOption('2');
    assert.match(await trigger.innerText(), /考勤-早打卡.*考勤异常3个月内第2次触发升级/s);
    const styles = await note.evaluate(node => ({ size: parseFloat(getComputedStyle(node).fontSize), color: getComputedStyle(node).color, parent: parseFloat(getComputedStyle(node.parentElement).fontSize) }));
    assert.equal(styles.color, 'rgb(0, 0, 0)');
    assert.ok(styles.size < styles.parent);
    await trigger.click();
    assert.ok(await menu.isVisible());
    assert.equal(await menu.locator('.deduction-upgrade-note').first().evaluate(node => getComputedStyle(node).color), 'rgb(0, 0, 0)');
    await page.locator('[data-deduction-type-option="12"]').click();
    assert.ok(!(await menu.isVisible()));
    assert.equal(await page.evaluate(() => new FormData(document.getElementById('deductionForm')).get('deduction_type_id')), '12');
    assert.equal(await page.evaluate(() => window.typeChanges), 1);
    assert.match(await trigger.innerText(), /迟到早退/);
    await trigger.press('ArrowDown');
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#deductionTypeSelect').inputValue(), '13');
    assert.equal(await trigger.locator('.deduction-upgrade-note').count(), 0);
    const typeBox = await trigger.boundingBox();
    const levelBox = await levelSelect.boundingBox();
    assert.ok(typeBox.x < levelBox.x, 'type and level should be side by side on desktop');
    assert.ok(Math.abs(typeBox.y - levelBox.y) < 1, 'type and level top edges should align');
    assert.ok(Math.abs(typeBox.height - levelBox.height) < 1, 'type and level should share a standard field height');
    await trigger.click();await page.keyboard.press('Escape');
    assert.ok(!(await menu.isVisible()));
    assert.equal(await trigger.getAttribute('aria-expanded'), 'false');
    await trigger.click();await page.mouse.click(10, 10);
    assert.ok(!(await menu.isVisible()));
    await page.evaluate(() => document.getElementById('deductionForm').reset());
    assert.equal(await page.locator('#deductionTypeSelect').inputValue(), '11');
    assert.match(await trigger.innerText(), /考勤异常/);
    await page.setViewportSize({ width: 375, height: 812 });
    const effectiveHint = page.locator('#deductionEffectiveDateHint');
    const mobileType = await trigger.boundingBox();
    const mobileLevel = await levelSelect.boundingBox();
    assert.ok(Math.abs(mobileType.x - mobileLevel.x) < 1 && mobileLevel.y >= mobileType.y + mobileType.height,
      'type and level should stack on mobile');
    assert.ok(!(await effectiveHint.isVisible()));
    await levelSelect.selectOption('2');
    assert.ok(await effectiveHint.isVisible());
    assert.match(await effectiveHint.innerText(), /系统登记当天为生效日/);
    await levelSelect.selectOption('1');
    assert.ok(!(await effectiveHint.isVisible()));
    await trigger.click();
    assert.ok(await menu.isVisible());
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await page.keyboard.press('End');
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#deductionTypeSelect').inputValue(), '14');
    await trigger.click();await page.keyboard.press('Tab');
    assert.ok(!(await menu.isVisible()));
    console.log('PASS: small black notes, native submission ID, change/reset, keyboard, outside close, mobile layout');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
