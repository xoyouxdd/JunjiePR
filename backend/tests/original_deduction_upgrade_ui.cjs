'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium, webkit} = require('./support/browser-runtime.cjs');
const {routeFrontend} = require('./support/frontend-page.cjs');

const html = `<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="/static/css/style.css">
<main id="app" data-workspace-ui></main><div id="tabs"></div><div id="toast"></div>
<script type="module">
import {state} from '/static/js/app/state.js';
import {configureViews} from '/static/js/app/context.js';
import {renderDeclarationStatistics} from '/static/js/app/declarations.js';
import {checkDeductionEvent} from '/static/js/app/deduction-event.js';
state.me = {role_code:'GSM', base_role_code:'GSM', permissions:['DECLARATION_STATS_VIEW','DEDUCTION_ALL']};
configureViews({declarationStatistics:renderDeclarationStatistics});
window.checkEvent = (reference='') => {
  const fields = new FormData();
  fields.set('employee_id', '17');
  fields.set('occurred_on', '2026-11-21');
  if(reference) fields.set('original_statement_id', reference);
  return checkDeductionEvent(fields, {isCurrent: () => true}).then(ok => ({
    ok, reason: fields.get('different_event_reason'), confirmation: fields.get('event_confirmation'),
  }));
};
state.declarationRecordFocus = {id:210, month:'2026-11'};
await renderDeclarationStatistics();
</script></html>`;

const incident = {
  id: 210, incident_on: '2026-11-21', month: '2026-11',
  type_name: '安全', level_name: '声明', status: 'active', description: '设备钥匙遗留在作业现场，已回收。',
};
const decision = {
  original_month: '2026-11', original_level: '声明', original_points: 1,
  result_level: '备忘录', result_points: 3, decided_by: '测试GSM', decided_at: '2026-12-02 10:30:00',
  mod_name: '测试MOD', mod_confirmed_on: '2026-12-01', reason: 'MOD 线下确认实际开具备忘录。',
};
const levelOptions = [{id:1, code:'STATEMENT', name:'声明'}, {id:2, code:'MEMO', name:'备忘录'}];
const confirmation = 'a'.repeat(64);

async function exercise(browser, width, captures) {
  const page = await browser.newPage({viewport:{width, height:width===320?568:920}, reducedMotion:'reduce'});
  const errors = [], writes = [], statisticsQueries = [];
  let upgraded = false;
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url());
    const {pathname, searchParams} = url;
    if (pathname.endsWith('/event-check')) {
      const exact = searchParams.has('original_statement_id');
      if (exact) assert.equal(searchParams.get('original_statement_id'), '210');
      return route.fulfill({json:{same_original:exact, records:[incident], confirmation}});
    }
    if (pathname.endsWith('/original-upgrade')) {
      if (request.method() === 'POST') {
        upgraded = true;
        writes.push(request.postDataJSON());
        return route.fulfill({json:{ok:true}});
      }
      return route.fulfill({json:{
        record:incident, original_points:1, effective_on:'2026-12-02', effective_month:'2026-12',
        levels:[{id:2, name:'备忘录', points:3, actual_points:3}], confirmation,
      }});
    }
    assert.equal(pathname, '/api/declaration-statistics');
    statisticsQueries.push(url.search);
    const month = searchParams.get('month'), history = upgraded && month === '2026-11';
    const row = {
      ...incident, employee_id:17, employee_name:'样例员工', employee_no:'DEMO017',
      attraction_id:3, attraction_name:'样例景点圈', type_id:1,
      level_id:upgraded?2:1, level_code:upgraded?'MEMO':'STATEMENT',
      group_level_id:history?1:upgraded?2:1, level_name:upgraded?'备忘录':'声明',
      incident_on:upgraded?incident.incident_on:null,
      occurred_on:upgraded?'2026-12-02':incident.incident_on,
      deduction_month:upgraded?'2026-12':'2026-11',
      raw_status:'active', business_status:'已生效', actual_points:history?0:upgraded?3:1,
      original_month_history:history, original_upgrade:upgraded?decision:null,
      score_status:history?'已转至 2026-12 · 本月不计分':'已扣分',
      can_upgrade_original:!upgraded, document_url:'/api/files/42',
      material_status:'材料已就绪', submitter_name:'测试主管', submitted_at:'2026-11-21 09:00:00',
    };
    const counted = history?[]:[row];
    return route.fulfill({json:{
      month, total_records:counted.length, employee_count:counted.length,
      levels:levelOptions.map(level=>({...level,count:counted.filter(item=>item.level_id===level.id).length})),
      attractions:[{id:3,name:'样例景点圈'}], types:[{id:1,name:'安全'}], circles:[],
      statuses:{已生效:counted.length}, records:counted, detail_records:[row],
    }});
  });
  const locator = page.getByRole('dialog');
  const capture = async (key, element) => {
    if(captures) captures[key] = (await element.screenshot({animations:'disabled'})).toString('base64');
  };
  try {
    await page.goto(await routeFrontend(page,{html}));
    const original = page.getByRole('button',{name:'原单升级', exact:true});
    await original.waitFor();
    assert.match(statisticsQueries[0], /keyword=%23210/);
    await original.click();
    assert.equal(await locator.locator('[data-upgrade-points]').innerText(), '—');
    await locator.locator('[name=level_id]').selectOption('2');
    assert.equal(await locator.locator('[data-upgrade-level]').innerText(), '备忘录');
    assert.equal(await locator.locator('[data-upgrade-points]').innerText(), '3.00');
    assert.match(await locator.innerText(), /2026-11 留档、不计分，2026-12 按升级后的处分计一次分/);
    assert.ok(await locator.evaluate(dialog=>dialog.scrollWidth===dialog.clientWidth));
    const confirmRows = await locator.locator('.original-upgrade-confirmation label').evaluateAll(labels => labels.map(label => label.getBoundingClientRect().top));
    assert.equal(confirmRows[0] < confirmRows[1], width <= 480);
    await locator.locator('[name=mod_name]').fill(decision.mod_name);
    await locator.locator('[name=mod_confirmed_on]').fill(decision.mod_confirmed_on);
    await locator.locator('[name=reason]').fill(decision.reason);
    await capture('UPGRADE', locator);
    await locator.getByRole('button',{name:'确认原单升级'}).click();
    await locator.waitFor({state:'detached'});
    assert.deepEqual(writes, [{
      level_id:2, confirmation, mod_name:decision.mod_name,
      mod_confirmed_on:decision.mod_confirmed_on, reason:decision.reason,
    }]);
    const record = page.locator('[data-declaration-record="210"]');
    await record.getByText('已原单升级',{exact:true}).waitFor();
    assert.match(await record.innerText(), /本月不计分/);
    assert.match(await record.locator('.declaration-original-month-score').innerText(), /生效月 2026-12/);
    assert.equal(await record.locator('a[download]').getAttribute('href'), '/api/files/42');
    await record.getByText('原单升级记录',{exact:true}).click();
    assert.match(await record.innerText(), /测试MOD/);
    await capture('HISTORY', record);

    await page.evaluate(() => { window.result = checkEvent(); });
    await locator.getByRole('heading',{name:'可能是同一起事件'}).waitFor();
    assert.match(await locator.innerText(), /原单 #210/);
    assert.ok(await locator.evaluate(dialog=>dialog.scrollWidth===dialog.clientWidth));
    await capture('DUPLICATE', locator);
    assert.equal(await locator.locator('[name=different_event_reason]').isVisible(), false);
    assert.equal(await locator.locator('[type=submit]').isVisible(), false);
    await locator.getByText('确认为不同事件',{exact:true}).click();
    await locator.getByRole('button',{name:'确认是不同事件，继续登记'}).click();
    assert.equal(await locator.count(),1);
    await locator.locator('[name=different_event_reason]').fill('另一起独立事件');
    await locator.getByRole('button',{name:'确认是不同事件，继续登记'}).click();
    assert.deepEqual(await page.evaluate(()=>window.result), {ok:true, reason:'另一起独立事件', confirmation});
    await locator.waitFor({state:'detached'});

    await page.evaluate(() => { window.result = checkEvent('#210'); });
    await locator.getByRole('heading',{name:'同一原单已登记'}).waitFor();
    assert.match(await locator.innerText(), /本次登记已拦截/);
    assert.equal(await locator.getByRole('button',{name:'确认是不同事件，继续登记'}).count(),0);
    await locator.getByRole('button',{name:'查看原单'}).click();
    assert.deepEqual(await page.evaluate(()=>window.result), {ok:false, reason:null, confirmation:null});
    await locator.waitFor({state:'detached'});
    await record.getByText('已原单升级',{exact:true}).waitFor();
    assert.match(statisticsQueries.at(-1), /keyword=%23210/);
    assert.deepEqual(errors, []);
  } finally {
    await page.close();
  }
}

(async()=>{
  const captures = {};
  for (const [engine, options, browserName] of [[chromium,{channel:'msedge'},'Edge'],[webkit,{},'WebKit']]) {
    const browser = await engine.launch({...options, headless:true});
    try {
      for (const width of [1280, 390, 320]) {
        await exercise(browser, width, browserName === 'Edge' && width === 390 && process.argv[2] ? captures : null);
      }
      console.log(browserName + ': original upgrade, prior month detail and duplicate dialogs verified at 1280 / 390 / 320 px');
    } finally {
      await browser.close();
    }
  }
  if(process.argv[2]){
    let preview = fs.readFileSync(process.argv[2], 'utf8');
    for(const [key, data] of Object.entries(captures)) preview = preview.replace(`PREVIEW_${key}`, 'data:image/png;base64,'+data);
    fs.writeFileSync(process.argv[2], preview);
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
