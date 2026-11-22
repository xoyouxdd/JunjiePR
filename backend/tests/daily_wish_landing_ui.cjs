'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium, webkit} = require('./support/browser-runtime.cjs');
const {routeFrontend, staticRoot} = require('./support/frontend-page.cjs');

const html = fs.readFileSync(path.join(staticRoot, 'index.html'), 'utf8')
  .replaceAll('__STATIC_CACHE_VERSION__', 'daily-wish-landing');
const roles = ['CM', 'TR', 'TA_SUPERVISOR', 'SUPERVISOR', 'TA_GSM', 'GSM', 'AM', 'OM', 'HR_CIRCLE', 'HR_ADMIN', 'SYSTEM_ADMIN'];

async function openWorkbench(browser, role, width, entry = '/') {
  const page = await browser.newPage({viewport:{width, height:900}});
  const user = {
    id:1, employee_no:'DEMO', name:'样例员工', role_code:role, base_role_code:role, role_name:role,
    attraction_id:1, attraction_name:'热力追踪', permissions:role==='GSM'?['DATA_VIEW','DEDUCTION_ALL']:[],
  };
  await page.route('**/api/**', route => {
    const {pathname, searchParams} = new URL(route.request().url());
    const routes = {
      '/api/me':user,
      '/api/options':{attractions:[], employee_circles:[]},
      '/api/dashboard':{month:searchParams.get('month'), records:[], total_score:0},
      '/api/action-center':{month:'2026-11', total:0, items:[]},
    };
    return route.fulfill({json:routes[pathname]||{items:[]}});
  });
  await page.goto(await routeFrontend(page,{html,pathname:entry}));
  return page;
}

async function verifyLanding(browser, role, width) {
  const page = await openWorkbench(browser, role, width);
  try {
    const isFrontline = role === 'CM' || role === 'TR';
    const landingTab = isFrontline ? 'home' : 'actionCenter';
    const innerTab = isFrontline ? 'actionCenter' : role === 'GSM' ? 'statistics' : 'announcements';
    const story = page.locator('#themeStory');
    await story.waitFor();
    assert.equal(await page.locator('#app').getAttribute('data-view'), landingTab);
    assert.equal(await story.locator('[data-wish-scratch]').count(), 1);
    if(['CM','GSM'].includes(role)||(width > 760&&role === 'SYSTEM_ADMIN')){
      await story.locator('[data-wish-reveal]').click();
      await story.locator('[data-wish-favorite]').click();
      await story.locator('[data-wish-favorites-toggle]').click();
      const quote = await story.locator('[data-wish-text]').innerText();
      await page.locator(`#tabs [data-tab="${innerTab}"]:visible`).click();
      await story.waitFor({state:'hidden'});
      assert.equal(await story.evaluate(node=>node.getBoundingClientRect().height), 0);
      assert.equal(await page.locator('#app').getAttribute('data-view'), innerTab);
      await page.locator(`#tabs [data-tab="${landingTab}"]:visible`).click();
      await story.waitFor();
      assert.equal(await story.locator('[data-wish-text]').innerText(), quote);
      assert.equal(await story.locator('[data-wish-favorite]').getAttribute('aria-pressed'), 'true');
      await story.getByRole('button',{name:'翻一张'}).click();
      assert.equal(await story.locator('[data-wish-replay-scratch]').isVisible(), true);
    }
  } finally {
    await page.close();
  }
}

(async()=>{
  for(const [engine, options, width] of [
    [chromium,{channel:'msedge'},1280],
    [webkit,{},390],
  ]){
    const browser = await engine.launch({...options,headless:true});
    try {
      for(const role of roles)await verifyLanding(browser, role, width);
      const page = await openWorkbench(browser,'CM',width,'/?view=actionCenter');
      await page.locator('.action-center-page').waitFor();
      assert.equal(await page.locator('#themeStory').isVisible(), false);
      await page.close();
      console.log(`Daily wish is landing-only for ${roles.length} roles at ${width}px; navigation and favorites verified`);
    } finally {
      await browser.close();
    }
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
