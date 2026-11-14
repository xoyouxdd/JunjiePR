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

module.exports = require('playwright');
