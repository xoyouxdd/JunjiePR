// Run with Node.js and Playwright installed; exercises only a local DOM fixture.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const source = fs.readFileSync(path.join(__dirname, '../app/static/js/app.js'), 'utf8');
  const css = fs.readFileSync(path.join(__dirname, '../app/static/css/style.css'), 'utf8');
  const functions = source.slice(source.indexOf('function deductionUpgradeGroup('), source.indexOf('function deductionForm('));
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await page.setContent(`<style>${css}</style><main style="padding:20px"><section class="panel" style="max-width:800px"><form id="testForm"><label>扣分类型<select id="deductionTypeSelect" name="deduction_type_id"><option value="11">考勤-早打卡（升级）</option><option value="12">考勤-迟到30分钟内（升级）</option><option value="13">安全</option><option value="14">违规病假（升级）</option></select><span id="deductionUpgradeHint"></span></label><button type="button" id="outside">其他字段</button></form></section></main>`);
    await page.addScriptTag({ content: `
      const state={options:{deduction_types:[
        {id:11,name:'考勤-早打卡',code:'ATT_EARLY_CLOCK',repeat_check:true},
        {id:12,name:'考勤-迟到30分钟内',code:'ATT_LATE_WITHIN_30',repeat_check:true},
        {id:13,name:'安全',code:'SAFETY',repeat_check:false},
        {id:14,name:'违规病假',code:'SICK_LEAVE_VIOLATION',repeat_check:true}
      ]}};
      const esc=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
      const cleanups=[];const registerPageCleanup=callback=>cleanups.push(callback);
      ${functions}
      bindDeductionTypePicker(document.getElementById('deductionTypeSelect'));
      window.typeChanges=0;document.getElementById('deductionTypeSelect').addEventListener('change',()=>window.typeChanges++);
    ` });
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
    assert.equal(await page.evaluate(() => new FormData(document.getElementById('testForm')).get('deduction_type_id')), '12');
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
    await page.evaluate(() => document.getElementById('testForm').reset());
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
