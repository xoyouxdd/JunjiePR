// Inspect only scripts reachable from the application's real HTML entry.
const fs = require('node:fs');
const path = require('node:path');
const staticRoot = path.resolve(__dirname, '../app/static');
const indexPath = path.join(staticRoot, 'index.html');
const importPattern = /^[ \t]*(?:import|export)\s+(?:[\w*$ {},\n]+\s+from\s+)?(['"])([^'"\n]+)\1/gm;
const declarationPattern = /^(?:export[ \t]+)?(?:(?:async[ \t]+)?function\b|const\b|let\b|class\b|import\b|export\b)/gm;

function localPath(specifier, parent, entry = false, imports = {}) {
  if (!entry && Object.keys(imports).length) {
    const pageUrl = 'https://fixture.invalid/';
    const moduleUrl = new URL(path.relative(path.dirname(staticRoot), parent).split(path.sep).join('/') + '/', pageUrl);
    const normalize = (name, base) => /^(\.\.?\/|\/)/.test(name) ? new URL(name, base).href : name;
    const key = normalize(specifier, moduleUrl);
    const mappings = Object.fromEntries(Object.entries(imports).map(([name, target]) => [normalize(name, pageUrl), target]));
    let target = mappings[key];
    if (target === undefined) {
      const prefix = Object.keys(mappings).filter(name => name.endsWith('/') && key.startsWith(name)).sort((a, b) => b.length - a.length)[0];
      if (prefix) target = mappings[prefix] + key.slice(prefix.length);
    }
    if (target !== undefined) return localPath(target, path.dirname(staticRoot), true);
  }
  const url = new URL(specifier, 'https://fixture.invalid/');
  if (url.origin !== 'https://fixture.invalid' || /^[a-z][a-z\d+.-]*:/i.test(specifier) || specifier.startsWith('//')) {
    throw new Error(`Frontend source must be local: ${specifier}`);
  }
  const relative = decodeURIComponent(specifier.split(/[?#]/, 1)[0]);
  if (!entry && !/^(\.\.?\/|\/)/.test(relative)) throw new Error(`Frontend import must be relative: ${specifier}`);
  const candidate = relative.startsWith('/') ? path.resolve(staticRoot, '..', relative.slice(1)) : path.resolve(parent, relative);
  const resolved = fs.realpathSync(candidate);
  const scope = path.relative(staticRoot, resolved);
  if (scope === '..' || scope.startsWith(`..${path.sep}`) || path.isAbsolute(scope)) {
    throw new Error(`Frontend source escapes static root: ${specifier}`);
  }
  return resolved;
}

function frontendSources() {
  const html = fs.readFileSync(indexPath, 'utf8');
  const entries = [];
  let imports = {};
  for (const match of html.matchAll(/<script\b[^>]*type\s*=\s*(['"])importmap\1[^>]*>([\s\S]*?)<\/script\s*>/gi)) {
    imports = {...imports, ...(JSON.parse(match[2]).imports || {})};
  }
  for (const match of html.matchAll(/<script\b([^>]*)>/gi)) {
    const attributes = Object.fromEntries([...match[1].matchAll(/([\w-]+)\s*=\s*(['"])(.*?)\2/g)].map(row => [row[1].toLowerCase(), row[3]]));
    if (attributes.src && (!attributes.type || ['module', 'text/javascript'].includes(attributes.type))) entries.push(attributes.src);
  }
  if (!entries.length) throw new Error('Application HTML has no JS entry');
  const visited = new Set(), sources = new Map();
  function visit(filename) {
    if (visited.has(filename)) return;
    visited.add(filename);
    const source = fs.readFileSync(filename, 'utf8');
    for (const match of source.matchAll(new RegExp(importPattern))) visit(localPath(match[2], path.dirname(filename), false, imports));
    sources.set(filename, source);
  }
  for (const entry of entries) visit(localPath(entry, path.dirname(staticRoot), true));
  return sources;
}

function readFrontendSource() {
  return [...frontendSources().values()].join('\n');
}

function frontendFunctionSource(name) {
  if (!/^[A-Za-z_$][\w$]*$/.test(name)) throw new Error(`Invalid function name: ${name}`);
  const pattern = new RegExp(`^(?:export[ \\t]+)?(?:async[ \\t]+)?function[ \\t]+${name}\\(`, 'gm');
  const matches = [...frontendSources()].flatMap(([filename, source]) => [...source.matchAll(pattern)].map(match => ({filename, source, match})));
  if (matches.length !== 1) throw new Error(`Expected one reachable function ${name}, found ${matches.length}`);
  const {source, match} = matches[0];
  const next = new RegExp(declarationPattern);
  next.lastIndex = match.index + match[0].length;
  const end = next.exec(source);
  return source.slice(match.index, end ? end.index : source.length).replace(/^([ \t]*)export[ \t]+/, '$1');
}

module.exports = {staticRoot, frontendSources, readFrontendSource, frontendFunctionSource};
