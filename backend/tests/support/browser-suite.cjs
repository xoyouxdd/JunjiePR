'use strict';

const {fork} = require('node:child_process');
const path = require('node:path');
const playwright = require('playwright');

const engines = new Set(['chromium', 'webkit', 'firefox']);
const testsDirectory = path.resolve(__dirname, '..');

async function runSuite(names) {
  const servers = new Map();
  let launches = 0;
  try {
    const results = [];
    for (const name of names) {
      if (!/^[a-z0-9_]+\.cjs$/.test(name)) throw new Error(`Invalid fixture name: ${name}`);
      console.log(`\n=== ${name} (browser) ===`);
      let child;
      try {
        child = fork(path.join(testsDirectory, name), [], {
          stdio: ['ignore', 'inherit', 'inherit', 'ipc'],
          env: {...process.env, JUNJIEPR_BROWSER_SUITE: '1'},
        });
      } catch (error) {
        console.error(error);
        results.push({name, passed: false});
        continue;
      }
      child.on('message', async ({id, engine, options}) => {
        let response;
        try {
          if (!engines.has(engine)) throw new Error(`Unknown Playwright engine: ${engine}`);
          const key = JSON.stringify([engine, Object.entries(options).sort(([a], [b]) => a.localeCompare(b))]);
          if (!servers.has(key)) {
            servers.set(key, playwright[engine].launchServer({...options, host: '127.0.0.1', port: 0})
              .then(server => {
                launches++;
                server.once('close', () => servers.delete(key));
                return server;
              }).catch(error => {
                servers.delete(key);
                throw error;
              }));
          }
          response = {id, endpoint: (await servers.get(key)).wsEndpoint()};
        } catch (error) {
          response = {id, error: error.message};
        }
        if (child.connected) child.send(response, () => {});
      });
      const passed = await new Promise(resolve => {
        child.once('error', error => { console.error(error); resolve(false); });
        child.once('exit', code => resolve(code === 0));
      });
      results.push({name, passed});
    }
    return {results, launches};
  } finally {
    const results = await Promise.allSettled([...servers.values()]);
    await Promise.all(results.filter(result => result.status === 'fulfilled').map(result => result.value.close()));
  }
}

runSuite(process.argv.slice(2)).then(({results, launches}) => {
  console.log(`Shared browser launches: ${launches} for ${results.length} fixtures`);
  console.log('BROWSER_RESULTS ' + JSON.stringify(results));
  process.exitCode = results.some(result => !result.passed) ? 1 : 0;
}).catch(error => { console.error(error); process.exitCode = 1; });
