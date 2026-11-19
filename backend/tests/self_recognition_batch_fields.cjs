const assert = require('node:assert/strict');
const {loadAppModules} = require('./support/esm-loader.cjs');

const input = (value, dataset = {}) => ({value, dataset, getAttribute: () => null});
function card(index) {
  const values = {recognition_date: `2026-10-0${index}`, recognition_type_id: String(index),
    content: `内容${index}`, occurred_attraction_id: String(index), recognizer_employee_id: String(index)};
  return {dataset: {}, _isLoading: () => false, querySelector: selector => {
    const name = selector.match(/name="(.*?)"/)[1];
    return input(values[name]);
  }};
}
(async () => {
  const {'registration.js': {selfRecognitionBatchItems}} = await loadAppModules(['registration.js'], {
    document: {getElementById: () => null},
    location: {pathname: '/'},
  });
  const cards = [1,2,3,4,5].map(card);
  const items = selfRecognitionBatchItems(cards);
  assert.equal(items.length, 5);
  assert.deepEqual(items.map(x => x.recognizer_employee_id), ['1','2','3','4','5']);
  assert.deepEqual(items.map(x => x.recognition_date), ['2026-10-01','2026-10-02','2026-10-03','2026-10-04','2026-10-05']);
  cards[0].dataset.duplicateConfirmedPayload = JSON.stringify(items[0]);
  cards[0].dataset.duplicateConfirmedToken = 'snapshot';
  assert.equal(selfRecognitionBatchItems(cards)[0].same_day_duplicate_confirmed, true);
  assert.equal(selfRecognitionBatchItems(cards)[0].same_day_duplicate_token, 'snapshot');
  cards[0].dataset.duplicateConfirmedPayload = 'different';
  assert.equal(selfRecognitionBatchItems(cards)[0].same_day_duplicate_confirmed, undefined);
  assert.equal(selfRecognitionBatchItems(cards)[0].same_day_duplicate_token, undefined);
  cards[1]._isLoading = () => true;
  assert.throws(() => selfRecognitionBatchItems(cards), /第2条/);
  const fallback = card(1);
  fallback.querySelector = selector => selector.includes('recognition_date') ?
    input('', {submitDate: '2026-10-09'}) : input('1');
  assert.equal(selfRecognitionBatchItems([fallback])[0].recognition_date, '2026-10-09');
  // frontend_modules.cjs exercises the full multipart submission on the page.
  console.log('Batch field serialization: independent values, date fallback, confirmation retry and loading guard passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
