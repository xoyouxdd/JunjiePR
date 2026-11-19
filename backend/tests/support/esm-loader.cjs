const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appRoot = path.resolve(__dirname, '../../app/static/js/app');

// Load complete production modules and let Node resolve their actual imports.
// Only explicitly supplied browser boundaries are replaced in isolated tests.
async function loadAppModules(entries, globals, mocks = {}) {
  const context = vm.createContext(globals);
  const modules = new Map();

  function load(filename) {
    if (modules.has(filename)) return modules.get(filename);
    const relative = path.relative(appRoot, filename);
    if (!relative || relative.startsWith('..' + path.sep) || path.isAbsolute(relative) || path.extname(relative) !== '.js') {
      throw new Error(`Unexpected app module: ${filename}`);
    }
    const options = {context, identifier: filename};
    const bindings = Object.hasOwn(mocks, relative) ? mocks[relative] : null;
    const module = bindings
      ? new vm.SyntheticModule(Object.keys(bindings), function () {
        for (const [name, value] of Object.entries(bindings)) this.setExport(name, value);
      }, options)
      : new vm.SourceTextModule(fs.readFileSync(filename, 'utf8'), options);
    modules.set(filename, module);
    return module;
  }

  const source = entries.map((name, i) =>
    `export * as owner${i} from ${JSON.stringify('./' + name)};`).join('\n');
  const entry = new vm.SourceTextModule(source, {context, identifier: path.join(appRoot, '<test-entry>')});
  await entry.link((specifier, importer) => load(path.resolve(path.dirname(importer.identifier), specifier)));
  await entry.evaluate();
  return Object.fromEntries(entries.map((name, i) => [name, entry.namespace[`owner${i}`]]));
}

module.exports = {loadAppModules};
