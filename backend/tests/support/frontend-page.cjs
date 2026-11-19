'use strict';

const path = require('node:path');

const staticRoot = path.resolve(__dirname, '../../app/static');
const origin = 'http://fixture.test';
const contentTypes = {
  '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp',
};

async function routeFrontend(page, {html, pathname = '/'}) {
  await page.route(`${origin}/static/**`, async route => {
    const url = new URL(route.request().url());
    const file = path.resolve(staticRoot, decodeURIComponent(url.pathname.slice('/static/'.length)));
    if (!file.startsWith(staticRoot + path.sep)) return route.fulfill({status: 404});
    await route.fulfill({path: file, contentType: contentTypes[path.extname(file)] || 'application/octet-stream'});
  });
  const url = new URL(pathname, origin).href;
  await page.route(url, route => route.fulfill({contentType: 'text/html; charset=utf-8', body: html}));
  return url;
}

module.exports = {routeFrontend, staticRoot};
