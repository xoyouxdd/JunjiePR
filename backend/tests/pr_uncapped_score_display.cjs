const assert = require('node:assert/strict');
const vm = require('node:vm');
const {frontendFunctionSource} = require('./frontend_source.js');
const helper = frontendFunctionSource('prRecognitionCount');
const context = vm.createContext({esc: String, fmt: value => Number(value).toFixed(2)});
vm.runInContext(helper, context);
const render = (score, enabled = true) => context.prRecognitionCount(
  {count: 17, score, uncapped_score: 9}, {uncapped_ranking: enabled}
);
for (const score of [0, 4, 4.99]) assert.equal(render(score), '17');
for (const score of [5, '5.00', 6]) assert.match(render(score), /（未封顶9\.00分）/);
assert.equal(render(5, false), '17');
assert.equal(render(undefined), '17');
console.log('未封顶分数展示边界检查通过');
