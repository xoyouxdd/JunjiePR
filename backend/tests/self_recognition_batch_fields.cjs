const assert = require('node:assert/strict');
const vm = require('node:vm');
const {readFrontendSource, frontendFunctionSource} = require('./frontend_source.js');
const source = readFrontendSource();
const context = {submittedDateValue: input => input.value || input.dataset.submitDate || ''};
vm.createContext(context);
vm.runInContext(frontendFunctionSource('selfRecognitionBatchItems'), context);
function card(index) {
  const values = {recognition_date: `2026-10-0${index}`, recognition_type_id: String(index),
    content: `内容${index}`, occurred_attraction_id: String(index), recognizer_employee_id: String(index)};
  return {dataset: {}, _isLoading: () => false, querySelector: selector => {
    const name = selector.match(/name="(.*?)"/)[1];
    return {value: values[name], dataset: {}};
  }};
}
const cards = [1,2,3,4,5].map(card);
const items = JSON.parse(JSON.stringify(context.selfRecognitionBatchItems(cards)));
assert.equal(items.length, 5);
assert.deepEqual(items.map(x => x.recognizer_employee_id), ['1','2','3','4','5']);
assert.deepEqual(items.map(x => x.recognition_date), ['2026-10-01','2026-10-02','2026-10-03','2026-10-04','2026-10-05']);
cards[0].dataset.duplicateConfirmedPayload = JSON.stringify(items[0]);
assert.equal(context.selfRecognitionBatchItems(cards)[0].same_day_duplicate_confirmed, true);
cards[0].dataset.duplicateConfirmedPayload = 'different';
assert.equal(context.selfRecognitionBatchItems(cards)[0].same_day_duplicate_confirmed, undefined);
cards[1]._isLoading = () => true;
assert.throws(() => context.selfRecognitionBatchItems(cards), /第2条/);
const fallback = card(1);
fallback.querySelector = selector => selector.includes('recognition_date') ?
  {value: '', dataset: {submitDate: '2026-10-09'}} : {value: '1', dataset: {}};
assert.equal(context.selfRecognitionBatchItems([fallback])[0].recognition_date, '2026-10-09');
assert(source.includes("data.set('entries',JSON.stringify(items))"));
assert(source.includes("if(!file)throw new Error(recognitionImageIssueMessage(form))"));
console.log('Batch field serialization: independent values, date fallback, confirmation retry and loading guard passed.');
