const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
vm.runInThisContext(fs.readFileSync(path.join(__dirname,'../app/static/js/rotation-task-presentation.js'),'utf8'));
const post=(key,occ,name=occ)=>({key,name:key,occ,occName:name,open:true});
const before=[post('tower','old1'),post('guest','old2'),post('end','old3')];
const after=[post('tower','new'),post('guest','old1'),post('end','old2')];
const task={line:'A1',beforePosts:before,afterPosts:after,routes:[{pid:'old3',to:'下岗，回休息室确认休息／吃饭'}]};
const original=JSON.stringify(task),ui=RotationTaskPresentation;
assert.deepEqual(ui.movements(task).map(r=>[r.name,r.to]),[['new','tower'],['old1','guest'],['old2','end'],['old3','休息室']]);
assert(!ui.render(task).includes('rt-task-columns'));
assert(ui.render(task).includes('rt-tone-rest'));
const batch={...task,departures:[{pid:'old3',outcome:'done'}]};
const html=ui.render(batch,{batch:true});
for(const token of ['rt-tone-new','rt-tone-continue','rt-tone-special','整批排岗前后','old3 · 下班'])assert(html.includes(token),token);
assert.equal(ui.movements(batch).at(-1).to,'下班','Explicit departure beats a legacy rest route');
for(const [outcome,to] of [['pending','休息室'],['sick','病假'],['away','出圈']]){
 const t={...task,departures:[{pid:'old3',outcome}]};
 assert.equal(ui.movements(t).at(-1).to,to);
 assert(ui.render(t,{batch:true}).includes(outcome==='pending'?'rt-tone-rest':'特别注意'));
}
const reordered={...task,beforePosts:[before[2],before[0],before[1]],afterPosts:after.slice().reverse()};
assert.deepEqual(ui.movements(reordered).find(r=>r.pid==='old1').from,'tower');
const unsafe={...task,line:'<img onerror=bad>',afterPosts:[post('tower','new','<script>bad</script>')]};
assert(!ui.render(unsafe).includes('<script>'));assert(ui.render(unsafe).includes('&lt;script&gt;'));
assert.equal(ui.render(null),'');assert.equal(ui.render({beforePosts:[],afterPosts:null}),'');
assert(ui.render({...task,beforePosts:before.map((p,i)=>({...p,mode101:i===0?{phase:'fixed'}:null}))}).includes('101 模式'));
assert.equal(JSON.stringify(task),original,'Presentation never changes stored tasks');
// Historical executed receipts omitted routes; known kind + explicit shift-end
// evidence restores the destination without guessing for malformed snapshots.
for(const [kind,outcome] of [['push','rest'],['push7','rest'],['off','rest'],['sick','sick'],['out','away']]){
 const receipt={status:'executed',kind,line:'A1',beforePosts:before,afterPosts:after,shiftEndPosts:[]};
 assert.equal(ui.movements(receipt).at(-1).outcome,outcome);
 assert(ui.render(receipt).includes(outcome==='rest'?'rt-tone-rest':'rt-tone-special'));
 assert.equal(ui.movements({...receipt,shiftEndPosts:['end']}).at(-1).outcome,'done');
}
assert.equal(ui.movements({status:'executed',beforePosts:before,afterPosts:after}).at(-1).outcome,'unknown');
console.log('Task presentation: ordinary routes, batch tones, explicit rest/done/sick/away, stable post keys, 101, escaped text, missing data and immutable snapshots PASS');
