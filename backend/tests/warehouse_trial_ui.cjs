'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {chromium} = require('./support/browser-runtime.cjs');

const backend = path.resolve(__dirname, '..');
const artifacts = path.resolve(backend, process.env.WAREHOUSE_UI_ARTIFACTS || '../output/warehouse-trial');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const prefix = '/api/warehouse';

async function json(response) {
  assert(response.ok(), await response.text());
  return response.json();
}

async function reservePort() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

async function main() {
  const port = await reservePort();
  const base = 'http://127.0.0.1:' + port;
  const dataDir = path.join(process.env.TEMP, 'warehouse-data');
  const server = spawn(path.join(backend, '.venv/Scripts/python.exe'),
    ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port)], {
      cwd: backend, windowsHide: true,
      env: {
        ...process.env, RECOGNITION_ENVIRONMENT:'test', RECOGNITION_V2_DATA_DIR:dataDir,
        RECOGNITION_BACKUP_HEALTH_STATUS_FILE:path.join(dataDir,'backups','health.json'),
        RECOGNITION_ENABLE_TEST_ACCOUNTS:'1', RECOGNITION_TEST_DEFAULT_PASSWORD:'1234',
        RECOGNITION_TEST_ADMIN_PASSWORD:'HR123',
      },
    });
  let log = '';
  server.stdout.on('data', chunk => { log += chunk; });
  server.stderr.on('data', chunk => { log += chunk; });
  let browser;
  try {
    let health;
    for (let attempt = 0; attempt < 120; attempt++) {
      try {
        const response = await fetch(base + '/health');
        if (response.ok) { health = await response.json(); break; }
      } catch { /* the process is starting */ }
      assert(server.exitCode === null, log);
      await wait(250);
    }
    assert(health, log);
    browser = await chromium.launch({channel:'msedge',headless:true});
    fs.mkdirSync(artifacts,{recursive:true});
    const errors = [];

    async function signIn(employee, password = '1234', viewport = {width:390,height:844}) {
      const context = await browser.newContext({viewport, deviceScaleFactor:1, isMobile:true, hasTouch:true});
      await json(await context.request.post(base + '/api/login',{data:{employee_no:employee,password}}));
      await context.request.post(base + '/api/changelog/announcement/read',{data:{version:health.version}});
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      return {context, page, request:context.request};
    }

    const hr = await signIn('HR01','HR123',{width:1220,height:900});
    await hr.page.goto(base + '/?view=warehouse');
    await hr.page.getByRole('button',{name:'添加仓库负责人'}).click();
    await hr.page.locator('[name=query]').fill('SUPTEST01');
    await hr.page.getByRole('button',{name:'搜索',exact:true}).click();
    await hr.page.locator('[data-wh-owner-results]').getByRole('button',{name:'添加',exact:true}).click();
    await hr.page.locator('.wh-owners').getByText('测试主管',{exact:true}).waitFor();
    const second = (await json(await hr.request.get(base + prefix + '/manager-candidates?q=' + encodeURIComponent('测试TA主管')))).items[0];
    await json(await hr.request.post(base + prefix + '/managers',{data:{employee_id:second.id}}));

    const owner = await signIn('SUPTEST01');
    const picker = await signIn('GSMTEST01');
    const peer = await signIn('TATEST01');

    async function state(context = owner) {
      return json(await context.request.get(base + prefix + '/bootstrap'));
    }

    async function setStock(itemId, positionId, quantity, kind = 'adjustment', writer = owner) {
      const position = (await state(writer)).positions.find(row => row.id === positionId);
      return json(await writer.request.post(base + prefix + '/adjustments',{data:{
        item_id:itemId, position_id:positionId, quantity, version:position.version, reason:'隔离验收实点', kind,
      }}));
    }

    async function item(name, aliases, location, quantity) {
      const row = await json(await owner.request.post(base + prefix + '/items',{data:{name, aliases, unit:'箱', category:'消耗卡片'}}));
      await setStock(row.id, location, quantity, 'opening');
      return row.id;
    }

    const first = await item('厕所卡',['WC卡'],1,6);
    const secondItem = await item('返回卡',['回卡'],1,3);
    const third = await item('等候时间卡',['等候卡'],2,4);

    const open = async (handle, view) => {
      await handle.page.goto(base + '/?view=warehouse');
      await handle.page.locator('.wh-module').first().waitFor();
      if (view) await handle.page.locator('.wh-module[data-wh-action="' + view + '"]').click();
    };

    await open(owner);
    assert.equal(await owner.page.locator('.wh-overview .wh-rack').count(), 3);
    for (const width of [320,390,1280]) {
      await owner.page.setViewportSize({width,height:900});
      assert(await owner.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'workspace overflow at ' + width);
      await owner.page.screenshot({path:path.join(artifacts,'home-' + width + '.png'),fullPage:true});
    }
    await owner.page.setViewportSize({width:390,height:844});
    await owner.page.locator('.wh-module[data-wh-action="in"]').click();
    await owner.page.locator('[data-we-back]').click();
    await owner.page.locator('.wh-module[data-wh-action="in"]').click();

    async function selectItem(page, id, word, position, quantity, received) {
      await page.locator('[data-we-search]').fill(word);
      await page.locator('[data-we-add-item="' + id + '"]').click();
      const card = page.locator('[data-we-item="' + id + '"]');
      if (received !== undefined) await card.locator('[data-we-field="received"]').fill(String(received));
      await card.locator('[data-we-location="0"]').selectOption(String(position));
      await card.locator('[data-we-allocation="0"]').fill(String(quantity));
      return card;
    }

    const photo = Buffer.from(await owner.page.evaluate(() => {
      const canvas = Object.assign(document.createElement('canvas'),{width:600,height:420});
      const drawing = canvas.getContext('2d');
      drawing.fillStyle = '#b39872'; drawing.fillRect(0,0,600,420);
      drawing.fillStyle = '#433529'; drawing.fillRect(25,305,550,33);
      for (let row = 0; row < 3; row++) {
        drawing.fillStyle = ['#dcc49a','#e3d5b6','#d8baa5'][row];
        drawing.fillRect(50 + row * 165, 144, 150, 161);
      }
      drawing.font = '28px sans-serif'; drawing.fillStyle = '#433529';
      drawing.fillText('WAREHOUSE TEST',50,90);
      return canvas.toDataURL('image/jpeg',0.8).split(',')[1];
    }), 'base64');

    const group = (page,id) => page.locator('[data-we-group="' + id + '"]');
    const attach = (page,id) => group(page,id).locator('[data-we-camera]').setInputFiles({
      name:'test-shelf.jpg', mimeType:'image/jpeg', buffer:photo,
    });
    const uploaded = (page,id) => group(page,id).locator('[data-we-photo-status][data-state=uploaded]').waitFor();

    const inbound = await selectItem(owner.page, first, 'WC卡', 3, 7, 10);
    await inbound.getByText('到货直用或收货异常',{exact:true}).click();
    await inbound.locator('[data-we-field="direct"]').fill('3');
    await inbound.locator('[data-we-field="destination"]').fill('现场使用');
    await owner.page.locator('[data-we-next]').click();
    assert.equal(await owner.page.locator('[data-we-group]').count(),1);
    await owner.page.screenshot({path:path.join(artifacts,'inbound-390.png'),fullPage:true});
    await attach(owner.page,first);
    await uploaded(owner.page,first);
    const photoPreview = owner.page.locator('[data-we-preview-link]');
    await photoPreview.waitFor();
    const photoTabOpened = owner.context.waitForEvent('page');
    await photoPreview.click();
    const photoTab = await photoTabOpened;
    await photoTab.locator('img').waitFor();
    assert(await photoTab.locator('img').evaluate(image => image.naturalWidth >= 600 && image.naturalHeight >= 420));
    await photoTab.close();
    await owner.page.locator('[data-we-confirm]').click();
    await owner.page.getByRole('heading',{name:'入库已确认'}).waitFor();
    await owner.page.locator('[data-we-complete]').click();
    assert.equal((await state()).items.find(row => row.id === first).total,13);

    await open(picker,'out');
    await selectItem(picker.page,first,'WC卡',1,2);
    await selectItem(picker.page,secondItem,'回卡',1,1);
    await selectItem(picker.page,third,'等候卡',2,1);
    await picker.page.locator('[data-we-next]').click();
    assert.equal(await picker.page.locator('[data-we-group]').count(),2);
    assert(await picker.page.locator('[data-we-confirm]').isDisabled());
    let release, firstStarted;
    const began = new Promise(resolve => { firstStarted = resolve; });
    let uploadAttempts = 0;
    await picker.page.route('**/api/warehouse/photos', async route => {
      uploadAttempts++;
      if (uploadAttempts === 1) { firstStarted(); await new Promise(resolve => { release = resolve; }); }
      if (uploadAttempts === 2) return route.abort('failed');
      return route.continue();
    });
    await attach(picker.page,1);
    await began;
    await attach(picker.page,2);
    release();
    await uploaded(picker.page,1);
    await group(picker.page,2).locator('[data-we-photo-status][data-state=failed]').waitFor();
    await picker.page.reload();
    await picker.page.locator('.wh-module[data-wh-action="out"]').click();
    await uploaded(picker.page,1);
    await uploaded(picker.page,2);
    assert.equal(await picker.page.locator('[data-we-progress-bar]').getAttribute('value'), '2');
    const confirmation = await picker.page.locator('[data-we-confirm]').boundingBox();
    const navigation = await picker.page.locator('.tabs-mobile').boundingBox();
    assert(confirmation && navigation && confirmation.y + confirmation.height <= navigation.y,
      'mobile confirmation should fit above workbench navigation');
    assert.equal(uploadAttempts,3,'the completed photo must survive reload');

    await setStock(first,1,7);
    await picker.page.locator('[data-we-confirm]').click();
    await group(picker.page,1).locator('[data-we-reviewed]').waitFor();
    assert.equal(await picker.page.locator('[data-we-reviewed]:visible').count(),1);
    await picker.page.screenshot({path:path.join(artifacts,'outbound-recheck-390.png'),fullPage:true});
    await group(picker.page,1).locator('[data-we-reviewed]').click();
    const before = (await state()).items.find(row => row.id === first).total;
    await picker.page.locator('[data-we-confirm]').click();
    await picker.page.getByRole('heading',{name:'出库已确认'}).waitFor();
    await picker.page.locator('[data-we-complete]').click();
    assert.equal((await state()).items.find(row => row.id === first).total,before - 2);

    await picker.page.locator('.wh-module[data-wh-action="query"]').click();
    await picker.page.locator('[data-wh-field="query-word"]').fill('WC卡');
    await picker.page.locator('[data-wh-action="query-item"][data-wh-id="' + first + '"]').click();
    await picker.page.locator('.wh-bin').first().waitFor();
    assert.equal(await picker.page.locator('.wh-bin').count(),2);
    await picker.page.getByText('A 架第 1 层现场照片',{exact:true}).waitFor();
    await picker.page.screenshot({path:path.join(artifacts,'query-390.png'),fullPage:true});
    await picker.page.locator('[data-wh-action="report"][data-wh-location="1"]').click();
    await picker.page.locator('[name=observed_quantity]').fill('4');
    await picker.page.getByRole('button',{name:'提交报错',exact:true}).click();
    await picker.page.getByText('待核对',{exact:true}).waitFor();
    const pickerTasks = await json(await picker.request.get(base + '/api/action-center'));
    assert(!pickerTasks.items.some(row => row.type === 'warehouse_followup'));

    await owner.page.goto(base + '/?view=actionCenter');
    await owner.page.locator('[data-action-type=warehouse_followup]').click();
    await owner.page.getByRole('heading',{name:'负责人待办'}).waitFor();
    await owner.page.locator('[data-wh-action="issue-claim"]').click();
    await owner.page.locator('[data-wh-action="issue-transfer"]').click();
    await owner.page.locator('dialog [name=employee_id]').selectOption(String(second.id));
    await owner.page.getByRole('button',{name:'确认转交',exact:true}).click();
    await peer.page.goto(base + '/?view=actionCenter');
    await peer.page.locator('[data-action-type=warehouse_followup]').click();
    await peer.page.locator('[data-wh-action="issue-resolve"]').click();
    await peer.page.locator('[data-wh-observation]').fill('4');
    await peer.page.locator('dialog [name=reason]').fill('现场复点后确认余量');
    await peer.page.getByRole('button',{name:'确认处理',exact:true}).click();
    await peer.page.getByText('已记录复点结果并处理待办。',{exact:true}).waitFor();

    await open(owner,'assessment');
    await owner.page.locator('.wh-usage summary').filter({hasText:'厕所卡'}).click();
    await owner.page.locator('[data-wh-action="rule"][data-wh-id="' + first + '"]').click();
    for (const [field,value] of Object.entries({minimum:3,yellow_upper:5,lead_days:4,safety_days:2,target_days:14})) {
      await owner.page.locator('dialog [name="' + field + '"]').fill(String(value));
    }
    await owner.page.getByRole('button',{name:'保存设置',exact:true}).click();
    await owner.page.getByText('已保存评估设置。',{exact:true}).waitFor();
    await setStock(first,1,1);
    await setStock(first,3,0);
    await open(owner,'assessment');
    await owner.page.locator('.wh-assessment-row--low').getByText('低库存',{exact:true}).waitFor();
    await owner.page.screenshot({path:path.join(artifacts,'assessment-390.png'),fullPage:true});
    await setStock(first,1,0);
    await owner.page.goto(base + '/?view=actionCenter');
    await owner.page.getByText('物资缺货 · 立即核对',{exact:true}).waitFor();
    await setStock(first,1,6);
    const cleared = await json(await owner.request.get(base + '/api/action-center'));
    assert(!cleared.items.some(row => row.type === 'warehouse_followup'));

    await open(owner);
    await owner.page.locator('[data-wh-action="catalog"]').click();
    await owner.page.locator('[data-wh-action="position-photo"]').click();
    const largePhoto = Buffer.from(await owner.page.evaluate(() => {
      const canvas = Object.assign(document.createElement('canvas'), {width:1600,height:1100});
      const drawing = canvas.getContext('2d');
      const frame = drawing.createImageData(canvas.width,canvas.height);
      let seed = 1939;
      for (let i = 0; i < frame.data.length; i += 4) {
        seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5;
        frame.data[i] = seed & 255;
        frame.data[i + 1] = (seed >>> 8) & 255;
        frame.data[i + 2] = (seed >>> 16) & 255;
        frame.data[i + 3] = 255;
      }
      drawing.putImageData(frame,0,0);
      return canvas.toDataURL('image/png').split(',')[1];
    }), 'base64');
    await owner.page.evaluate(() => {
      const original = window.fetch;
      window.fetch = (url, options) => {
        if (new URL(url,location.href).pathname === '/api/warehouse/photos' && options?.method === 'POST') {
          const file = options.body.get('file');
          window.warehousePhotoUpload = {size:file.size,type:file.type};
        }
        return original(url,options);
      };
    });
    await owner.page.locator('dialog [name=position_id]').selectOption('7');
    await owner.page.locator('dialog [name=file]').setInputFiles({name:'detailed.png',mimeType:'image/png',buffer:largePhoto});
    await owner.page.getByRole('button',{name:'更新照片',exact:true}).click();
    await owner.page.getByText('已更新货位照片。',{exact:true}).waitFor();
    const uploadedPhoto = await owner.page.evaluate(() => window.warehousePhotoUpload);
    assert(largePhoto.length > 1024 * 1024 && uploadedPhoto.size <= 500 * 1024 && uploadedPhoto.type === 'image/jpeg');
    const locationPhoto = (await state()).positions.find(row => row.id === 7).photo;
    const storedPhoto = await owner.request.get(base + locationPhoto.url);
    assert(storedPhoto.ok());
    const storedSize = (await storedPhoto.body()).length;
    assert(storedSize <= 256 * 1024);
    assert.deepEqual(errors,[]);
    process.stdout.write('PASS warehouse UI: assignment, inbound and direct use, outbound background photo resume and stale check, query/discrepancy transfer, alert cycle, responsive 320/390/1280.\n');
    process.stdout.write('Manual shelf photo bytes: original ' + largePhoto.length + ', browser ' + uploadedPhoto.size + ', storage ' + storedSize + '.\n');
  } catch (error) {
    process.stderr.write(log.slice(-6500) + '\n');
    throw error;
  } finally {
    await browser?.close();
    if (server.exitCode === null) server.kill();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
