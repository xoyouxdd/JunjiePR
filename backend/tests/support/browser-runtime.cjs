'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const drive = 'D:\\';
let base = os.tmpdir();
if (process.platform === 'win32' && fs.existsSync(drive) &&
    path.parse(base).root.toLowerCase() !== drive.toLowerCase()) {
  base = path.join(drive, 'JunjiePR-testing', 'scratch', 'native');
  fs.mkdirSync(base, { recursive: true });
}
const tempRoot = fs.mkdtempSync(path.join(fs.realpathSync(base), 'pr-browser-'));
for (const key of ['TEMP', 'TMP', 'TMPDIR']) {
  process.env[key] = tempRoot;
}

const log = fs.openSync(path.join(tempRoot, 'test.log'), 'a');
for (const stream of [process.stdout, process.stderr]) {
  const write = stream.write;
  stream.write = function (chunk, encoding, callback) {
    fs.writeSync(log, typeof chunk === 'string' ? Buffer.from(chunk, typeof encoding === 'string' ? encoding : 'utf8') : chunk);
    return write.call(this, chunk, encoding, callback);
  };
}
let crashed = false;
process.on('uncaughtExceptionMonitor', error => {
  crashed = true;
  fs.writeSync(log, `${error.stack || error}\n`);
});
process.on('exit', code => {
  fs.closeSync(log);
  if (!crashed && code === 0) {
    try {
      if (fs.realpathSync(tempRoot) !== tempRoot || fs.lstatSync(tempRoot).isSymbolicLink()) {
        throw new Error('test directory path changed');
      }
      fs.rmSync(tempRoot, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
      return;
    } catch (error) {
      fs.writeSync(2, `Could not remove test temporary files: ${error.message}\n`);
    }
  }
  fs.writeSync(2, `Test artifacts retained: ${tempRoot}\n`);
});

const playwright = require('playwright');

// A dedicated connection per call keeps fixture contexts separate. Closing the
// returned browser closes this connection, while the domain runner owns the
// shared browser process. Direct fixture launches still use local Playwright.
if (process.env.JUNJIEPR_BROWSER_SUITE === '1') {
  if (!process.send) throw new Error('Shared browser fixture requires its suite IPC channel');
  process.channel.unref();
  let nextId = 0;
  let pending = 0;
  function requestBrowser(engine, options) {
    return new Promise((resolve, reject) => {
      const id = nextId++;
      pending++;
      process.channel?.ref();
      const finish = (error, endpoint) => {
        process.off('message', receive);
        process.off('disconnect', disconnected);
        if (--pending === 0) process.channel?.unref();
        if (error) reject(error);
        else resolve(endpoint);
      };
      const receive = reply => {
        if (reply.id === id) finish(reply.error ? new Error(reply.error) : null, reply.endpoint);
      };
      const disconnected = () => finish(new Error('Shared browser suite disconnected'));
      process.on('message', receive);
      process.once('disconnect', disconnected);
      process.send({id, engine, options}, error => {
        if (error && process.listeners('message').includes(receive)) finish(error);
      });
    });
  }
  for (const engine of ['chromium', 'webkit', 'firefox']) {
    const type = playwright[engine];
    type.launch = async (options = {}) => {
      const {slowMo, ...settings} = options;
      const endpoint = await requestBrowser(engine, settings);
      return type.connect(endpoint, {slowMo, timeout: settings.timeout});
    };
  }
}

module.exports = playwright;
