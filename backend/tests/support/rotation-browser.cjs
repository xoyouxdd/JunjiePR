'use strict';

const fs = require('node:fs');
const path = require('node:path');

const staticRoot = path.resolve(__dirname, '../../app/static');
const origin = 'http://rotation-fixture.test';
const contentTypes = {'.css':'text/css', '.html':'text/html', '.js':'text/javascript'};

async function serveRotationFixture(page, pages, api) {
  await page.route(`${origin}/**`, async route => {
    const {pathname} = new URL(route.request().url());
    if (pathname.startsWith('/api/')) {
      const result = await api(pathname, route.request());
      return route.fulfill({status:result.status ?? 200, json:result.json});
    }
    if (Object.hasOwn(pages, pathname)) {
      return route.fulfill({contentType:'text/html; charset=utf-8', body:pages[pathname]});
    }
    if (!pathname.startsWith('/static/')) return route.fulfill({status:404});
    const asset = path.resolve(staticRoot, '.' + decodeURIComponent(pathname.slice('/static'.length)));
    if (!asset.startsWith(staticRoot + path.sep) || !fs.existsSync(asset)) return route.fulfill({status:404});
    return route.fulfill({path:asset, contentType:contentTypes[path.extname(asset)]});
  });
}

async function openRotationPage(page, pagePath) {
  const identity = {kind:'member', name:'测试员工', employee_no:'1', kind_label:'员工', attraction_name:'热力追踪', capabilities:{can_manage:false}};
  const initial = {version:0, clock:{date:'2026-10-11', minute:495, paused:true, mode:'sim'},
    day:null, settings:{}, person:null, notices:[], confirmations:[]};
  await page.addInitScript(() => {
    // A silent stream keeps polling from replacing the test's synthetic day.
    window.EventSource = class { constructor() { window.fixtureRotationStarted = true; } };
  });
  const url = new URL(pagePath, origin);
  const html = fs.readFileSync(path.join(staticRoot, `${url.pathname.slice(1)}.html`), 'utf8');
  await serveRotationFixture(page, {[url.pathname]:html}, pathname => {
    if (pathname==='/api/rotation-board/whoami') return {json:identity};
    if (pathname==='/api/rotation-board/me' || pathname==='/api/rotation-board/board') return {json:initial};
    throw new Error(`Unexpected bootstrap request: ${pathname}`);
  });
  await page.goto(url.href);
  await page.waitForFunction(() => window.fixtureRotationStarted || document.querySelector('.rt-empty h2')?.textContent === '无法进入轮岗');
  const startupError = await page.locator('.rt-empty h2').allTextContents();
  if (startupError.includes('无法进入轮岗')) throw new Error(await page.locator('#rtApp').innerText());
}

module.exports = {openRotationPage, origin, serveRotationFixture};
