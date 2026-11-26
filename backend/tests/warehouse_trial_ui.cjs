'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');
const {spawn,spawnSync} = require('node:child_process');
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
    assert.equal(await owner.page.locator('.wh-overview .wh-rack').count(), 6);
    assert.deepEqual(await owner.page.locator('.wh-rack').first().locator('[data-wh-level]').allTextContents(),['4','3','2','1']);
    await owner.page.locator('.wh-floor-summary').getByText('地面临时储存区').waitFor();
    for (const width of [320,390,1280]) {
      await owner.page.setViewportSize({width,height:900});
      assert(await owner.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'workspace overflow at ' + width);
      const firstLayer = owner.page.locator('.wh-rack-level[data-wh-id="1"]');
      const quick = owner.page.getByRole('region',{name:'货位快捷物资'});
      await firstLayer.click();
      assert.equal(await firstLayer.getAttribute('aria-expanded'),'true');
      await quick.getByText('厕所卡',{exact:true}).waitFor();
      await quick.getByText('本层 6 箱',{exact:true}).waitFor();
      assert.equal(await quick.getByRole('listitem').count(),2);
      assert(await owner.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'quick inventory overflow at ' + width);
      await owner.page.screenshot({path:path.join(artifacts,'home-' + width + '.png'),fullPage:true});
      await firstLayer.click();
      await quick.waitFor({state:'hidden'});
    }
    await owner.page.locator('.wh-floor-summary').click();
    await owner.page.getByText('此处暂无在库物资',{exact:true}).waitFor();
    await owner.page.getByRole('region',{name:'货位快捷物资'}).getByRole('button',{name:'查看详情'}).click();
    await owner.page.getByRole('heading',{name:'地面临时储存区'}).waitFor();
    await owner.page.getByRole('button',{name:'‹ 物资管理'}).click();
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

    const inbound = await selectItem(owner.page, first, 'WC卡', 3, 3, 10);
    const assignment = inbound.locator('[data-we-receipt-check]');
    assert.equal(await assignment.textContent(),'已上架 3 / 10 箱 · 还差 7 箱');
    await inbound.getByText('直用／破损',{exact:true}).click();
    await inbound.locator('[data-we-field="direct"]').fill('3');
    await inbound.locator('[data-we-field="destination"]').fill('现场使用');
    await inbound.locator('[data-we-field="note"]').fill('原收货说明');
    await inbound.getByRole('button',{name:/添加货位/}).click();
    const secondLocation = inbound.locator('[data-we-location="1"]');
    assert(await secondLocation.evaluate(element => document.activeElement === element),'new location should receive focus');
    const secondAllocation = inbound.locator('[data-we-allocation="1"]');
    await secondAllocation.fill('4');
    assert.equal(await assignment.textContent(),'上架 3 · 直用 3 · 破损 0，共 6 / 10 箱 · 有数量未选货位');
    await secondLocation.selectOption('3');
    assert.equal(await assignment.textContent(),'同一货位的数量请合并');
    await secondLocation.selectOption('24');
    await secondAllocation.fill('5');
    assert.equal(await assignment.textContent(),'上架 8 · 直用 3 · 破损 0，共 11 / 10 箱 · 超出 1 箱');
    await secondAllocation.fill('4');
    assert.equal(await assignment.textContent(),'上架 7 · 直用 3 · 破损 0，共 10 / 10 箱');
    await owner.page.setViewportSize({width:320,height:844});
    assert(await owner.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),'split allocation at 320px');
    await owner.page.setViewportSize({width:390,height:844});
    await owner.page.screenshot({path:path.join(artifacts,'inbound-allocations-390.png'),fullPage:true});
    await selectItem(owner.page,secondItem,'回卡',4,1,1);
    await owner.page.locator('[data-we-next]').click();
    assert.equal(await owner.page.locator('[data-we-group]').count(),2);
    await attach(owner.page,first);
    await uploaded(owner.page,first);
    await attach(owner.page,secondItem);
    await uploaded(owner.page,secondItem);
    const reviewNote = group(owner.page,first).locator('[data-we-receipt-note]');
    assert.equal(await reviewNote.inputValue(),'原收货说明');
    await reviewNote.fill('拍照时补记');
    await group(owner.page,secondItem).getByRole('button',{name:'移出本单'}).click();
    await group(owner.page,secondItem).waitFor({state:'detached'});
    assert.equal(await reviewNote.inputValue(),'拍照时补记');
    await uploaded(owner.page,first);
    await owner.page.setViewportSize({width:320,height:844});
    assert(await owner.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),'receipt controls at 320px');
    await owner.page.setViewportSize({width:390,height:844});
    await owner.page.screenshot({path:path.join(artifacts,'inbound-390.png'),fullPage:true});
    const photoPreview = owner.page.locator('[data-we-preview-link]');
    await photoPreview.waitFor();
    const photoTabOpened = owner.context.waitForEvent('page');
    await photoPreview.click();
    const photoTab = await photoTabOpened;
    await photoTab.locator('img').waitFor();
    assert(await photoTab.locator('img').evaluate(image => image.naturalWidth >= 600 && image.naturalHeight >= 420));
    await photoTab.close();
    const postedInbound = owner.page.waitForResponse(response =>
      response.url().endsWith(prefix + '/transactions') && response.request().method() === 'POST');
    await owner.page.locator('[data-we-confirm]').click();
    await owner.page.getByRole('heading',{name:'入库成功'}).waitFor();
    const savedInbound = await json(await owner.request.get(base + prefix + '/transactions/' + (await json(await postedInbound)).id));
    assert.deepEqual(savedInbound.receipts.map(row => [row.item_id,row.note]),[[first,'拍照时补记']]);
    assert.deepEqual(savedInbound.photos.map(row => row.item_id),[first]);
    assert.deepEqual(savedInbound.lines.map(row => [row.item_id,row.position_id,row.quantity]),[[first,3,3],[first,24,4]]);
    await owner.page.locator('[data-we-complete]').click();
    assert.equal((await state()).items.find(row => row.id === first).total,13);
    assert.equal((await state()).items.find(row => row.id === secondItem).total,3);
    await owner.page.locator('.wh-rack-level[data-wh-id="3"]').click();
    const quickReceipt = owner.page.getByRole('region',{name:'货位快捷物资'});
    await quickReceipt.getByText('暂无整层照',{exact:true}).waitFor();
    await quickReceipt.getByRole('img',{name:'厕所卡最近入库照'}).waitFor();

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
    await attach(picker.page,1);
    await uploaded(picker.page,1);
    const before = (await state()).items.find(row => row.id === first).total;
    await picker.page.locator('[data-we-confirm]').click();
    await picker.page.getByRole('heading',{name:'出库成功'}).waitFor();
    await picker.page.locator('[data-we-complete]').click();
    assert.equal((await state()).items.find(row => row.id === first).total,before - 2);

    await picker.page.locator('.wh-module[data-wh-action="query"]').click();
    await picker.page.locator('[data-wh-field="query-word"]').fill('WC卡');
    const match = picker.page.locator('[data-wh-action="query-item"][data-wh-id="' + first + '"]');
    if (await match.getAttribute('aria-expanded') !== 'true') await match.click();
    await picker.page.locator('.wh-bin').first().waitFor();
    assert.equal(await picker.page.locator('.wh-bin').count(),3);
    await picker.page.getByText('A 架第 1 层现场照片',{exact:true}).waitFor();
    await picker.page.screenshot({path:path.join(artifacts,'query-390.png'),fullPage:true});
    const reportAction = picker.page.locator('[data-wh-action="report"][data-wh-location="1"]');
    await picker.page.locator('.wh-bin').filter({has:reportAction}).getByText('更多',{exact:true}).click();
    await reportAction.click();
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
    await owner.page.getByText('补货估算',{exact:true}).click();
    for (const [field,value] of Object.entries({minimum:3,lead_days:4,safety_days:2,target_days:14})) {
      await owner.page.locator('dialog [name="' + field + '"]').fill(String(value));
    }
    await owner.page.getByRole('button',{name:'保存设置',exact:true}).click();
    await owner.page.getByText('已保存评估设置。',{exact:true}).waitFor();
    await setStock(first,1,1);
    await setStock(first,3,0);
    await setStock(first,24,0);
    await open(owner);
    const firstStockSlot = owner.page.locator('.wh-rack-level[data-wh-id="1"]');
    assert.equal(await firstStockSlot.getAttribute('data-stock-level'),'low');
    await firstStockSlot.click();
    await owner.page.getByRole('region',{name:'货位快捷物资'}).getByText(/全仓 1 箱 · 最低 3 箱/).waitFor();
    await open(owner,'assessment');
    await owner.page.locator('.wh-assessment-row--low').getByText('缺少',{exact:true}).waitFor();
    await owner.page.screenshot({path:path.join(artifacts,'assessment-390.png'),fullPage:true});
    await setStock(first,1,0);
    await owner.page.goto(base + '/?view=actionCenter');
    await owner.page.getByText('物资缺货 · 立即核对',{exact:true}).waitFor();
    await setStock(first,1,6);
    const cleared = await json(await owner.request.get(base + '/api/action-center'));
    assert(!cleared.items.some(row => row.type === 'warehouse_followup'));

    await open(owner);
    await owner.page.locator('[data-wh-action="catalog"]').click();
    await owner.page.locator('[data-wh-action="calibrate"]').click();
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
    await owner.page.getByRole('button',{name:'保存校准',exact:true}).click();
    await owner.page.getByText('已更新货位照片。',{exact:true}).waitFor();
    const uploadedPhoto = await owner.page.evaluate(() => window.warehousePhotoUpload);
    assert(largePhoto.length > 1024 * 1024 && uploadedPhoto.size <= 500 * 1024 && uploadedPhoto.type === 'image/jpeg');
    const locationPhoto = (await state()).positions.find(row => row.id === 7).photo;
    const storedPhoto = await owner.request.get(base + locationPhoto.url);
    assert(storedPhoto.ok());
    const storedSize = (await storedPhoto.body()).length;
    assert(storedSize <= 256 * 1024);
    await owner.page.locator('[data-wh-action="calibrate"]').click();
    await owner.page.locator('dialog [name=position_id]').selectOption('25');
    await owner.page.locator('[data-wh-calibration-choice]').selectOption(String(first));
    await owner.page.getByRole('button',{name:'加入复点',exact:true}).click();
    await owner.page.locator('[data-wh-calibration-item="' + first + '"]').fill('2');
    await owner.page.locator('dialog [name=file]').setInputFiles({name:'ground.jpg',mimeType:'image/jpeg',buffer:photo});
    await owner.page.locator('dialog [name=reason]').fill('地面区复点');
    await owner.page.getByRole('button',{name:'保存校准',exact:true}).click();
    await owner.page.getByText('已保存货位校准。',{exact:true}).waitFor();
    const pendingDownload = owner.page.waitForEvent('download');
    await owner.page.getByRole('link',{name:'导出库存 Excel'}).click();
    const workbook = await pendingDownload;
    assert.match(workbook.suggestedFilename(),/\.xlsx$/);
    assert.equal(fs.readFileSync(await workbook.path()).subarray(0,2).toString(),'PK');
    await open(owner,'query');
    await owner.page.locator('[data-wh-field="query-shelf"]').selectOption('G');
    await owner.page.getByText('地面临时储存区现场照片',{exact:true}).waitFor();
    await owner.page.locator('.wh-bin-stock').filter({hasText:'2 箱'}).waitFor();
    await owner.page.screenshot({path:path.join(artifacts,'ground-390.png'),fullPage:true});

    const batchWorkbook = await owner.request.get(base + prefix + '/inbound/template.xlsx');
    assert(batchWorkbook.ok());
    const receiptFile = path.join(process.env.TEMP,'warehouse-deferred-receipt.xlsx');
    fs.writeFileSync(receiptFile,await batchWorkbook.body());
    const fillWorkbook = spawnSync(path.join(backend,'.venv/Scripts/python.exe'),['-X','utf8','-c',
      'import sys; from openpyxl import load_workbook; p=sys.argv[1]; w=load_workbook(p); w.active["A4"]="\u5395\u6240\u5361"; w.active["B4"]="B1"; w.active["C4"]=3; w.save(p)',
      receiptFile], {cwd:backend,windowsHide:true});
    assert.equal(fillWorkbook.status,0,fillWorkbook.stderr?.toString());
    await open(owner,'in');
    await owner.page.locator('[data-we-import-file]').setInputFiles(receiptFile);
    await owner.page.locator('[data-we-next]:enabled').click();
    await group(owner.page,first).getByRole('button',{name:'移出本单'}).click();
    await owner.page.locator('[data-we-next]:disabled').waitFor();
    await owner.page.locator('[data-we-import-file]').setInputFiles(receiptFile);
    await owner.page.locator('[data-we-next]:enabled').click();
    const deferred = owner.page.locator('[data-we-defer-photos]');
    assert(await deferred.isChecked());
    await owner.page.locator('.we-photo-option').getByText('B 架第 1 层',{exact:true}).waitFor();
    await group(owner.page,first).locator('[data-we-receipt-note]').fill('异常说明需要物资照');
    assert.equal(await deferred.count(),0);
    assert(await group(owner.page,first).getByRole('button',{name:'拍照',exact:true}).isVisible());
    assert(await owner.page.locator('[data-we-confirm]').isDisabled());
    await group(owner.page,first).locator('[data-we-receipt-note]').fill('');
    assert(!(await deferred.isChecked()));
    await deferred.check();
    await deferred.uncheck();
    await owner.page.locator('[data-we-camera]').waitFor({state:'attached'});
    assert(await owner.page.locator('[data-we-confirm]').isDisabled());
    await deferred.check();
    await owner.page.locator('[data-we-confirm]').click();
    await owner.page.getByText('1 个货位已列入补照待办',{exact:true}).waitFor();

    await owner.page.goto(base + '/?view=actionCenter');
    await owner.page.locator('[data-action-type=warehouse_photo_followup]').click();
    await owner.page.getByRole('heading',{name:'负责人待办'}).waitFor();
    await owner.page.locator('[data-wh-action=photo-complete][data-wh-id="4"]').waitFor();
    await open(picker);
    await picker.page.getByRole('button',{name:'货位待补照',exact:true}).click();
    await picker.page.locator('[data-wh-action=photo-complete][data-wh-id="4"]').click();
    await picker.page.locator('dialog [name=file]').setInputFiles({name:'shelf.jpg',mimeType:'image/jpeg',buffer:photo});
    await picker.page.getByRole('button',{name:'保存现场照',exact:true}).click();
    await picker.page.getByText('现场照已更新，补照待办已完成。',{exact:true}).waitFor();
    await picker.page.getByText('暂无待补照货位',{exact:true}).waitFor();
    assert.equal((await state()).items.find(row => row.id === first).locations.find(row => row.position_id === 4).quantity,3);

    await open(picker,'query');
    await picker.page.locator('[data-wh-field="query-shelf"]').selectOption('B');
    await picker.page.locator('[data-wh-field="query-location"]').selectOption('4');
    await picker.page.getByRole('button',{name:'照片报错',exact:true}).click();
    await picker.page.locator('dialog [name=note]').fill('货位标识不清楚');
    await picker.page.getByRole('button',{name:'提交报错',exact:true}).click();
    await picker.page.getByText('待补最新现场照',{exact:true}).waitFor();
    assert((await state()).positions.find(row => row.id === 4).needs_photo);
    fs.unlinkSync(receiptFile);

    await picker.page.getByRole('button',{name:'照片报错',exact:true}).click();
    await picker.page.locator('dialog [name=note]').fill('补充：实际拍的是相邻层');
    await picker.page.getByRole('button',{name:'提交报错',exact:true}).click();
    await picker.page.getByText('已报告，仓库负责人可在待办跟进。',{exact:true}).waitFor();
    await open(owner);
    await owner.page.getByRole('button',{name:/^负责人待办/}).click();
    const feedback = owner.page.getByRole('list',{name:'照片报错记录'});
    await feedback.getByText('货位标识不清楚',{exact:true}).waitFor();
    await feedback.getByText('补充：实际拍的是相邻层',{exact:true}).waitFor();

    const staleFile = path.join(process.env.TEMP,'old-shelf-file.jpg');
    fs.writeFileSync(staleFile,photo);
    const staleTime = new Date('2026-01-15T04:00:00Z');
    fs.utimesSync(staleFile,staleTime,staleTime);
    const copiedExifFile = path.join(process.env.TEMP,'copied-shelf-exif.jpg');
    const createImage = spawnSync(path.join(backend,'.venv/Scripts/python.exe'),['-X','utf8','-c',
      'import sys; from PIL import Image; e=Image.Exif(); e[34665]={36867:"2026:01:15 12:00:00",36881:"+08:00"}; Image.new("RGB",(640,480),"tan").save(sys.argv[1],exif=e)',
      copiedExifFile],{cwd:backend,windowsHide:true});
    assert.equal(createImage.status,0,createImage.stderr?.toString());
    await picker.page.getByRole('button',{name:'补拍',exact:true}).click();
    const originalShelfPhoto = (await state()).positions.find(row => row.id === 4).photo.id;
    for (const filename of [staleFile,copiedExifFile]) {
      await picker.page.locator('dialog [name=file]').setInputFiles(filename);
      const [rejected] = await Promise.all([
        picker.page.waitForResponse(resp => resp.url().endsWith('/positions/4/photo-task') && resp.request().method() === 'PUT'),
        picker.page.getByRole('button',{name:'保存现场照',exact:true}).click(),
      ]);
      assert.equal(rejected.status(),409);
    }
    const stillPending = (await state()).positions.find(row => row.id === 4);
    assert(stillPending.needs_photo && stillPending.photo.id === originalShelfPhoto);
    await picker.page.locator('dialog [name=file]').setInputFiles({name:'new-shelf.png',mimeType:'image/png',buffer:largePhoto});
    await picker.page.getByRole('button',{name:'保存现场照',exact:true}).click();
    await picker.page.getByText('现场照已更新，补照待办已完成。',{exact:true}).waitFor();

    await open(owner,'in');
    await selectItem(owner.page,third,'等候卡',5,1,1);
    await owner.page.locator('[data-we-next]').click();
    await group(owner.page,third).locator('[data-we-photo-location]').selectOption('5');
    await attach(owner.page,third);
    await uploaded(owner.page,third);
    await owner.page.locator('[data-we-confirm]').click();
    await owner.page.getByRole('heading',{name:'入库成功'}).waitFor();
    await owner.page.locator('[data-we-complete]').click();
    const quickShelf = owner.page.locator('.wh-rack-level[data-wh-id="5"]');
    const quickPhoto = owner.page.getByRole('region',{name:'货位快捷物资'});
    await quickShelf.click();
    await quickPhoto.getByText('等候时间卡',{exact:true}).waitFor();
    await quickPhoto.getByText(/更新于/).waitFor();
    const thumbnail = quickPhoto.getByRole('img',{name:'B 架第 2 层现场照片'});
    await thumbnail.waitFor();
    await owner.page.waitForFunction(() => document.querySelector('#wh-quick-location img')?.naturalWidth);
    assert(await owner.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert.equal(await quickPhoto.locator('.wh-photo-position').textContent(),'B 架第 2 层');
    await quickPhoto.getByRole('link',{name:'B 架第 2 层现场照片'}).click();
    await owner.page.locator('dialog .wh-photo-full img').waitFor();
    assert.equal(await owner.page.locator('dialog .wh-photo-position').textContent(),'B 架第 2 层');
    await owner.page.getByRole('button',{name:'关闭',exact:true}).click();
    await open(owner,'in');
    await selectItem(owner.page,third,'等候卡',6,1,1);
    await owner.page.locator('[data-we-next]').click();
    await group(owner.page,third).locator('[data-we-photo-location]').selectOption('6');
    await group(owner.page,third).locator('[data-we-camera]').setInputFiles(staleFile);
    await uploaded(owner.page,third);
    await owner.page.locator('[data-we-confirm]').click();
    await owner.page.getByText('1 个货位已列入补照待办',{exact:true}).waitFor();
    const newShelf = (await state()).positions.find(row => row.id === 6);
    assert(newShelf.needs_photo && !newShelf.photo);

    await open(owner,'in');
    await selectItem(owner.page,first,'WC卡',9,1,1);
    await selectItem(owner.page,secondItem,'回卡',9,1,1);
    await selectItem(owner.page,third,'等候卡',8,1,1);
    await owner.page.locator('[data-we-next]').click();
    const earlyPhoto = path.join(process.env.TEMP,'before-quantity-edit.jpg');
    fs.writeFileSync(earlyPhoto,photo);
    await group(owner.page,first).locator('[data-we-photo-location]').selectOption('9');
    await group(owner.page,first).locator('[data-we-camera]').setInputFiles(earlyPhoto);
    await uploaded(owner.page,first);
    await attach(owner.page,secondItem);
    await uploaded(owner.page,secondItem);
    await group(owner.page,third).locator('[data-we-photo-location]').selectOption('8');
    await attach(owner.page,third);
    await uploaded(owner.page,third);
    await owner.page.locator('[data-we-edit]').click();
    const revisedItem = owner.page.locator('[data-we-item="' + secondItem + '"]');
    await revisedItem.locator('[data-we-field="received"]').fill('2');
    await revisedItem.locator('[data-we-allocation="0"]').fill('2');
    await owner.page.locator('[data-we-next]').click();
    await uploaded(owner.page,first);
    await uploaded(owner.page,third);
    await group(owner.page,secondItem).locator('[data-we-photo-status][data-state=missing]').waitFor();
    await attach(owner.page,secondItem);
    await uploaded(owner.page,secondItem);
    await owner.page.locator('[data-we-confirm]').click();
    await owner.page.getByText('1 个货位已列入补照待办',{exact:true}).waitFor();
    const adjusted = (await state()).positions;
    assert(adjusted.find(row => row.id === 9).needs_photo && !adjusted.find(row => row.id === 9).photo);
    assert(adjusted.find(row => row.id === 8).photo && !adjusted.find(row => row.id === 8).needs_photo);

    const unitItem = await json(await owner.request.post(base + prefix + '/items',{data:{name:'纸杯',unit:'箱'}}));
    const unitTemplate = await owner.request.get(base + prefix + '/inbound/template.xlsx');
    assert(unitTemplate.ok());
    const unitWorkbook = path.join(process.env.TEMP,'changed-unit.xlsx');
    fs.writeFileSync(unitWorkbook,await unitTemplate.body());
    const fillUnit = spawnSync(path.join(backend,'.venv/Scripts/python.exe'),['-X','utf8','-c',
      'import sys; from openpyxl import load_workbook; p=sys.argv[1]; w=load_workbook(p); w.active["A4"]="\u7eb8\u676f"; w.active["B4"]="A4"; w.active["C4"]=3; w.save(p)',
      unitWorkbook],{cwd:backend,windowsHide:true});
    assert.equal(fillUnit.status,0,fillUnit.stderr?.toString());
    await open(owner,'in');
    await owner.page.locator('[data-we-import-file]').setInputFiles(unitWorkbook);
    await owner.page.locator('[data-we-next]:enabled').click();
    await json(await peer.request.put(base + prefix + '/items/' + unitItem.id,{data:{name:'纸杯',unit:'个'}}));
    await owner.page.locator('[data-we-confirm]').click();
    await owner.page.getByText(/单位由箱改为个/).waitFor();
    assert.equal((await state()).items.find(row => row.id === unitItem.id).total,0);
    await owner.page.locator('[data-we-edit]').click();
    assert.equal(await owner.page.locator('[data-we-item="' + unitItem.id + '"] [data-we-field="received"]').inputValue(),'3');

    const movedItem = await item('移位测试带',[],14,2);
    const stays = await item('原位保留物资',[],14,4);
    await open(owner);
    const rackSlot = (page,id) => page.locator('.wh-rack-level[data-wh-id="' + id + '"]');
    await rackSlot(owner.page,14).click();
    const quickRegion = owner.page.getByRole('region',{name:'货位快捷物资'});
    const movedRow = quickRegion.getByRole('listitem').filter({hasText:'移位测试带'});
    await movedRow.getByText('更多',{exact:true}).click();
    await movedRow.getByRole('button',{name:'移位',exact:true}).click();
    assert.equal(await owner.page.locator('dialog [name=from]').inputValue(),'14');
    await owner.page.locator('dialog [name=to]').selectOption('19');
    await owner.page.locator('dialog [name=quantity]').fill('2');
    await owner.page.getByRole('button',{name:'确认移位',exact:true}).click();
    await movedRow.waitFor({state:'detached'});
    await quickRegion.getByText('原位保留物资',{exact:true}).waitFor();
    assert.equal((await state()).items.find(row => row.id === stays).total,4);

    await json(await owner.request.put(base + prefix + '/assessment/' + movedItem,{data:{minimum:2}}));
    await open(owner);
    const dest = rackSlot(owner.page,19);
    assert.equal(await dest.getAttribute('data-stock-level'),'healthy');
    await dest.click();
    await quickRegion.getByText('移位测试带',{exact:true}).waitFor();
    await quickRegion.getByRole('button',{name:'去出库',exact:true}).click();
    await owner.page.locator('[data-we-item="' + movedItem + '"] [data-we-allocation="0"]').fill('1');
    await owner.page.locator('[data-we-next]').click();
    await attach(owner.page,19);
    await uploaded(owner.page,19);
    await owner.page.locator('[data-we-confirm]').click();
    await owner.page.getByRole('heading',{name:'出库成功'}).waitFor();
    await owner.page.locator('[data-we-complete]').click();
    await dest.waitFor();
    assert.equal(await dest.getAttribute('data-stock-level'),'low');
    assert((await json(await owner.request.get(base + '/api/action-center'))).items.some(row => row.type === 'warehouse_followup'));

    await setStock(movedItem,19,0);
    const alertForMovedItem = async () => (await json(await owner.request.get(base + prefix + '/alerts'))).items
      .find(alert => alert.item_id === movedItem);
    assert.equal((await alertForMovedItem()).level,'empty');
    const issue = await json(await picker.request.post(base + prefix + '/issues',{data:{item_id:movedItem,position_id:19,reason:'mismatch'}}));
    await open(owner,'assessment');
    const archiveWarning = owner.page.locator('.wh-assessment-row--empty [data-wh-action="archive-item"][data-wh-id="' + movedItem + '"]');
    await archiveWarning.click();
    await owner.page.getByText(/还有待核对差异或待补记出库/).waitFor();
    await owner.page.getByRole('button',{name:'打开负责人待办'}).click();
    await owner.page.getByRole('heading',{name:'负责人待办'}).waitFor();
    await json(await owner.request.post(base + prefix + '/issues/' + issue.id + '/claim',{data:{}}));
    await json(await owner.request.post(base + prefix + '/issues/' + issue.id + '/resolve',{data:{reason:'核对完成'}}));
    await open(owner,'assessment');
    await archiveWarning.click();
    await owner.page.locator('dialog [name=employee_no]').fill('SUPTEST01');
    await owner.page.getByRole('button',{name:'确认归档',exact:true}).click();
    await archiveWarning.waitFor({state:'detached'});
    assert.equal(await alertForMovedItem(),undefined);
    assert.equal((await state()).items.find(row => row.id === movedItem).active,false);

    await open(owner);
    await owner.page.locator('[data-wh-action="catalog"]').click();
    await owner.page.locator('[data-wh-action="archive-item"][data-wh-id="' + unitItem.id + '"]').click();
    await owner.page.locator('dialog [name=employee_no]').fill('TATEST01');
    await owner.page.getByRole('button',{name:'确认归档',exact:true}).click();
    await owner.page.getByText('请输入当前登录账号本人的员工号',{exact:true}).waitFor();
    await owner.page.locator('dialog [name=employee_no]').fill('SUPTEST01');
    await owner.page.getByRole('button',{name:'确认归档',exact:true}).click();
    await owner.page.getByText('已归档物资。',{exact:true}).waitFor();
    await owner.page.locator('details.wh-archived > summary').click();
    await owner.page.locator('details.wh-archived').getByRole('heading',{name:'纸杯',exact:true}).waitFor();
    assert.deepEqual(errors,[]);
    process.stdout.write('PASS warehouse UI: assignment, inbound and direct use, outbound background photo resume and stale check, deferred Excel photos and photo reports, query/discrepancy transfer, alert cycle, responsive 320/390/1280.\n');
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
