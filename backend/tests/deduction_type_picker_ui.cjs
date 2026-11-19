// Run with Node.js and Playwright installed; exercises only a local DOM fixture.
const assert = require('node:assert/strict');
const { chromium } = require('./support/browser-runtime.cjs');
const {routeFrontend} = require('./support/frontend-page.cjs');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const html = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
      <link rel="stylesheet" href="/static/css/style.css">
      <main id="app" style="padding:20px;max-width:800px"></main><div id="tabs"></div><div id="toast"></div>`;
    await page.goto(await routeFrontend(page,{html}));
    await page.evaluate(async () => {
      const {state}=await import('/static/js/app/state.js');
      const {deductionForm,bindDeductionUpgradeHint}=await import('/static/js/app/deductions.js');
      state.me={permissions:['DEDUCTION_ALL']};
      state.options={deduction_levels:[{id:1,code:'STATEMENT',name:'声明',points:1}],deduction_types:[
        {id:11,name:'考勤-早打卡',code:'ATT_EARLY_CLOCK',repeat_check:true},
        {id:12,name:'考勤-迟到30分钟内',code:'ATT_LATE_WITHIN_30',repeat_check:true},
        {id:13,name:'安全',code:'SAFETY',repeat_check:false},
        {id:14,name:'违规病假',code:'SICK_LEAVE_VIOLATION',repeat_check:true}
      ]};
      document.getElementById('app').innerHTML=deductionForm();
      bindDeductionUpgradeHint();
      window.typeChanges=0;
      document.getElementById('deductionTypeSelect').addEventListener('change',()=>window.typeChanges++);
    });
    const trigger = page.locator('.deduction-type-picker .recognizer-trigger');
    const menu = page.locator('#deductionTypeMenu');
    const note = trigger.locator('.deduction-upgrade-note');
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
    await trigger.click();await page.keyboard.press('Escape');
    assert.ok(!(await menu.isVisible()));
    assert.equal(await trigger.getAttribute('aria-expanded'), 'false');
    await trigger.click();await page.mouse.click(10, 10);
    assert.ok(!(await menu.isVisible()));
    await page.evaluate(() => document.getElementById('deductionForm').reset());
    assert.equal(await page.locator('#deductionTypeSelect').inputValue(), '11');
    assert.match(await trigger.innerText(), /考勤异常/);
    await page.setViewportSize({ width: 375, height: 812 });
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
