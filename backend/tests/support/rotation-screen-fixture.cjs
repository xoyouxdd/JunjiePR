'use strict';

const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

const root = path.resolve(__dirname, '../../app/static');
const cssAndScripts = new Map([['.css', 'text/css'], ['.js', 'text/javascript']]);

function createScreenFixture(snapshot, {demo = false} = {}) {
  let html = fs.readFileSync(path.join(root, 'rotation-screen.html'), 'utf8');
  if (demo) html = html.replace('热力追踪 · 休息室', '布局演示 · 模拟数据').replace('</body>', '<script src="/demo.js"></script></body>');

  let state = snapshot;
  function reply(response, type, value, status = 200) {
    response.writeHead(status, {'Content-Type':type + '; charset=utf-8'});
    response.end(value);
  }
  const json = (response, value, status) => reply(response, 'application/json', JSON.stringify(value), status);
  const server = http.createServer((request, response) => {
    const {pathname} = new URL(request.url, 'http://fixture');
    if (request.method === 'GET') {
      if (pathname === '/rotation/screen' || (demo && pathname === '/')) return reply(response, 'text/html', html);
      if (pathname === '/api/rotation-screen/state') return json(response, state);
      if (pathname === '/api/rotation-screen/stream') {
        response.writeHead(200, {'Content-Type':'text/event-stream'});
        return response.write(': fixture stream\n\n');
      }
      if (demo && pathname === '/demo.js') return reply(response, 'text/javascript', `
        window.addEventListener('click', event => {
          if (!event.target.closest('[data-f-arrive],[data-f-board],[data-f-check-in]')) return;
          event.stopImmediatePropagation();
          toast('布局演示，仅展示排版；实际操作请进入轮岗测试。');
        }, true);
      `);
      if (/^\/static\/(?:css|js)\/[\w.-]+\.(?:css|js)$/.test(pathname)) {
        const file = path.join(root, pathname.slice('/static/'.length));
        if (fs.existsSync(file)) return reply(response, cssAndScripts.get(path.extname(file)), fs.readFileSync(file));
      }
    }
    return json(response, {detail:'Unexpected fixture request'}, 404);
  });

  return {
    updateDay(day) { state = {...state, version:state.version + 1, day}; },
    async start(port = 0) {
      await new Promise((resolve, reject) => {
        server.once('error', reject);
        server.listen(port, '127.0.0.1', resolve);
      });
      return `http://127.0.0.1:${server.address().port}`;
    },
    async close() {
      server.closeAllConnections();
      await new Promise(resolve => server.close(resolve));
    },
  };
}

module.exports = {createScreenFixture};
