'use strict';

// Runs a real employee workbench against a disposable rotation database.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const { spawn } = require('node:child_process');
const { chromium } = require('./support/browser-runtime.cjs');

const date = '2026-10-04';
const password = 'daily-fixture-2026';
const fixtureRoot = process.env.ROTATION_REMINDER_TEST_ROOT || os.tmpdir();
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function ok(response) {
  assert(response.ok(), await response.text());
  return response.json();
}

async function main() {
  const socket = net.createServer();
  await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  const backend = path.resolve(__dirname, '..');
  const work = fs.mkdtempSync(path.join(fixtureRoot, 'rotation-check-in-reminder-'));
  const base = 'http://127.0.0.1:' + port;
  const server = spawn(path.join(backend, '.venv/Scripts/python.exe'),
    [path.join(__dirname, 'rotation_daily_fixture.py'), work, String(port)],
    { cwd: backend, windowsHide: true, env: { ...process.env, ROTATION_FORECAST_FIXTURE: '1', PYTHONUTF8: '1' } });
  let log = '', browser, passed = false;
  server.stdout.on('data', data => log += data);
  server.stderr.on('data', data => log += data);
  try {
    let ready = false;
    for (let i = 0; i < 100 && server.exitCode === null; i++) {
      try { if ((await fetch(base + '/health')).ok) { ready = true; break; } } catch {}
      await pause(300);
    }
    assert(ready, log);
    browser = await chromium.launch({ channel: 'msedge', headless: true });
    const manager = await browser.newContext();
    const login = (context, employee_no, credential = password) =>
      context.request.post(base + '/api/login', { data: { employee_no, password: credential } }).then(ok);
    await login(manager, 'GSMTEST01');
    const clock = async time => {
      const url = base + '/__fixture/rotation-clock';
      await ok(await manager.request.post(url, { data: { action: 'set', date, time: time.slice(0, 5) } }));
      const seconds = Number(time.slice(6));
      if (seconds) await ok(await manager.request.post(url, { data: { action: 'jump', minutes: seconds / 60 } }));
    };
    await clock('13:14:59');
    for (const pid of ['CMTEST01', 'CMTEST02', 'TRTEST01']) {
      await ok(await manager.request.post(base + '/api/rotation-board/act', {
        data: { action: 'add_person', pid, start: '13:30', end: '22:00' }
      }));
    }

    const errors = [];
    const employee = async pid => {
      const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
      await login(context, pid);
      const releaseUrl = base + '/api/changelog/announcement';
      const announcement = await ok(await context.request.get(releaseUrl));
      if (!announcement.read && announcement.release?.items?.length) {
        await ok(await context.request.post(releaseUrl + '/read', { data: { version: announcement.release.version } }));
      }
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(base + '/?view=actionCenter');
      await page.locator('.rt-personal-task').waitFor();
      return {
        context, page,
        banner: page.locator('.rt-shift-reminder'),
        sign: page.getByRole('button', { name: '已到', exact: true }),
        member: () => context.request.get(base + '/api/rotation-board/me').then(ok),
      };
    };
    const expectCount = async (member, count) => {
      await member.page.waitForFunction(expected => {
        const todo = document.querySelector('[data-action-center-todo-count]');
        const total = document.querySelector('[data-action-center-total-count]');
        const nav = [...document.querySelectorAll('[data-action-center-badge]')];
        return todo?.textContent === expected && total?.textContent === expected && nav.length > 0 &&
          nav.every(badge => badge.textContent === expected && badge.hidden === (expected === '0'));
      }, String(count));
      const queue = await ok(await member.context.request.get(base + '/api/action-center'));
      assert.equal(queue.total, count);
      assert.equal(queue.items.find(item => item.type === 'rotation_step')?.count ?? 0, count);
    };
    const first = await employee('CMTEST01');
    assert.equal(await first.banner.isVisible(), false);
    await expectCount(first, 0);
    await clock('13:15:00');
    await first.banner.getByText('请确认已到', { exact: true }).waitFor();
    await first.banner.getByText('13:30 班 · 13:27 截止').waitFor();
    await first.banner.getByText('12:00', { exact: true }).waitFor();
    await expectCount(first, 1);
    await clock('13:25:35');
    await first.banner.getByText('签到即将截止').waitFor();
    await first.banner.getByText('01:25', { exact: true }).waitFor();
    assert(await first.banner.evaluate(node => node.classList.contains('rt-shift-reminder-urgent')));

    const post = '**/api/rotation-board/me/act';
    await first.page.route(post, route => route.fulfill({ status: 409, json: { detail: '模拟提交未成功' } }), { times: 1 });
    await first.sign.click();
    await first.page.getByText('模拟提交未成功', { exact: true }).waitFor();
    assert.equal((await first.member()).person.batchCheckedIn, false);
    assert.equal(await first.banner.getByText('上班卡打了吗？').count(), 0);
    await expectCount(first, 1);
    await first.sign.click();
    await first.banner.getByText('上班卡打了吗？').waitFor();
    assert.equal((await first.member()).person.batchCheckedIn, true);
    await expectCount(first, 0);
    await first.page.reload();
    await first.banner.getByText('上班卡打了吗？').waitFor();
    for (const width of [320, 390, 768, 1440]) {
      await first.page.setViewportSize({ width, height: 900 });
      assert(await first.banner.evaluate(node => node.scrollWidth <= node.clientWidth + 1), 'reminder overflow at ' + width);
    }
    await first.banner.getByRole('button', { name: '已打卡', exact: true }).click();
    await first.banner.waitFor({ state: 'hidden' });
    await first.page.reload();
    await first.page.getByText(/已到确认成功 · 等待排岗/).waitFor();
    assert.equal(await first.banner.isVisible(), false);

    const second = await employee('CMTEST02');
    // The database commits, but the response is lost. The existing receipt recovery must show exactly one prompt.
    await second.page.route(post, async route => {
      const response = await route.fetch();
      assert(response.ok(), await response.text());
      await route.abort('connectionreset');
    }, { times: 1 });
    await second.sign.click();
    await second.banner.getByText('上班卡打了吗？').waitFor();
    assert.equal((await second.member()).person.batchCheckedIn, true);
    await expectCount(second, 0);
    await second.banner.getByRole('button', { name: '稍后处理', exact: true }).click();
    await second.banner.waitFor({ state: 'hidden' });
    await second.page.reload();
    await second.page.getByText(/已到确认成功 · 等待排岗/).waitFor();
    assert.equal(await second.banner.isVisible(), false);

    const third = await employee('TRTEST01');
    await third.sign.waitFor();
    await expectCount(third, 1);
    await clock('13:27:00');
    await third.banner.waitFor({ state: 'hidden' });
    await expectCount(third, 0);
    assert.equal((await third.member()).person.batchCheckedIn, false);
    for (const signed of [first, second]) {
      const { person, confirmations } = await signed.member();
      assert.equal(person.state, 'onpost', person.pid + ' was not assigned by the batch');
      assert(person.line && person.post);
      assert.equal(confirmations.filter(receipt => receipt.action === 'batch_check_in').length, 1);
      assert.equal(await signed.banner.isVisible(), false);
    }

    const board = await manager.newPage();
    await board.goto(base + '/rotation-board');
    await board.locator('.rt-board').waitFor();
    assert.equal(await board.locator('.rt-shift-reminder').count(), 0);
    const display = await browser.newContext();
    await login(display, '6666666', '1243');
    const screen = await display.newPage();
    await screen.goto(base + '/rotation/screen');
    await screen.locator('#rtScreenTotals').waitFor();
    assert.equal(await screen.locator('.rt-shift-reminder').count(), 0);
    assert.deepEqual(errors, []);
    console.log('Employee check-in reminder: live todo counts, failed/recovered success, both dismissals, batch assignment and screen isolation PASS');
    passed = true;
  } finally {
    if (browser) await browser.close();
    if (server.exitCode === null) {
      const exited = new Promise(resolve => server.once('exit', resolve));
      server.kill();
      await Promise.race([exited, pause(2500)]);
    }
    if (passed) {
      const resolved = fs.realpathSync(work);
      assert.equal(path.dirname(resolved), fs.realpathSync(fixtureRoot));
      fs.rmSync(resolved, { recursive: true, force: true, maxRetries: 4, retryDelay: 150 });
    } else {
      fs.writeFileSync(path.join(work, 'server.log'), log);
      console.error('Fixture retained:', work);
    }
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
