'use strict';
// Exercise the actual page helpers with delayed responses and unknown writes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.join(__dirname, '../app/static/js');
function context() {
  const elements = new Map();
  const element = () => ({textContent:'', className:'', innerHTML:'', addEventListener(){},
    querySelectorAll(){return [];}, classList:{toggle(){},add(){},remove(){}}, setAttribute(){}});
  const storage = new Map();
  const ctx = vm.createContext({location:{pathname:'/rotation'}, Date, console,
    localStorage:{getItem(){return null;}},
    sessionStorage:{setItem(k,v){storage.set(k,v);},getItem(k){return storage.get(k);},removeItem(k){storage.delete(k);}},
    AbortController, encodeURIComponent, setTimeout:()=>0, clearTimeout(){},
    document:{getElementById(id){if(!elements.has(id))elements.set(id,element());return elements.get(id);},
      addEventListener(){},body:{classList:{contains(){return false;}}}},
    window:{addEventListener(){}}, messages:[], run:null,
  });
  vm.runInContext(fs.readFileSync(path.join(root,'rotation-common.js'),'utf8'),ctx);
  const source = fs.readFileSync(path.join(root,'rotation.js'),'utf8');
  vm.runInContext(source.slice(0,source.lastIndexOf('(async () => {')),ctx);
  vm.runInContext(`
    toast=(message,bad)=>messages.push({message,bad});
    closeModal=()=>{RT.modalOpen=false;};
    scheduleLoad=()=>{};
    confirmBox=(title,message,label,fn)=>{run=fn; RT.modalOpen=true;};
    RT.sync='live'; RT.lastSyncedAt=Date.now();
    RT.data={version:2, clock:{date:'2026-10-11',minute:495,paused:true}, settings:{departEarly:0},
      person:{pid:'1',name:'员工1',state:'ready',readyAt:480,flags:[],tags:[],start:435,end:1000,
        confirmationTokens:{depart:'a'.repeat(64)},
        assign:{line:'A',entryPost:'入口',mode:'chain',why:'推下班',targetName:'员工2',targetLine:'A',targetPost:'出口',departAt:495}},
      notices:[{status:'open',kind:'depart',title:'现在去轮岗'}], confirmations:[],week_minutes:{}};
    RT.load=async()=>{};
  `,ctx);
  return ctx;
}
async function main() {
  {
    const c=context();
    const html=vm.runInContext('memberView()',c);
    assert.match(html,/data-member-act="depart"/);
    assert.match(html,/员工2.*原岗位 A · 出口/s);
    assert.match(html,/入口岗依次推进/);
    vm.runInContext('RT.data.clock.minute=485;',c);
    assert.match(vm.runInContext('memberView()',c),/data-member-act="depart" disabled/);
    vm.runInContext(`RT.data.confirmations=[{minute:490,action:'depart',line:'A',post:'入口',source:'screen',
      assignment:{mode:'chain',why:'推下班',targetName:'员工2',targetLine:'A',targetPost:'原出口'}}];
      RT.data.person.assign.targetPost='新岗位';`,c);
    assert.match(vm.runInContext('memberView()',c),/原岗位：A · 原出口/);
  }
  {
    const c=context();
    vm.runInContext(`rtApi=async()=>({duplicate:false,result:{line:'A',post:'入口'}});
      RT.load=async()=>{throw new Error('view unavailable');};
      confirmRotation(RT.data.person,'depart','/api/rotation/me/act');`,c);
    assert.equal(await c.run(),true);
    assert.equal(vm.runInContext('RT.pendingConfirmation',c),null);
    assert.ok(c.messages.some(x=>x.message.includes('确认成功')));
  }
  {
    const c=context();
    vm.runInContext(`writes=0; rtApi=async()=>{writes++;const e=new Error('timeout');e.uncertain=true;throw e;};
      confirmRotation(RT.data.person,'depart','/api/rotation/me/act');`,c);
    assert.equal(await c.run(),false);
    assert.equal(vm.runInContext('confirmationBlocked()',c),true);
    const saved=vm.runInContext('RT.pendingConfirmation.token',c);
    assert.equal(saved,'a'.repeat(64));
    vm.runInContext(`reads=[]; rtApi=async path=>{reads.push(path);return {confirmed:true,result:{}};};`,c);
    await vm.runInContext('checkPendingConfirmation()',c);
    assert.equal(c.writes,1,'Result verification must not repeat the mutation');
    assert.match(c.reads[0],/\/confirmation\?token=/);
    assert.equal(vm.runInContext('RT.pendingConfirmation',c),null);
  }
  {
    const c=context();
    vm.runInContext(`rtApi=async()=>{const e=new Error('安排已更新');e.status=409;throw e;};
      confirmRotation(RT.data.person,'depart','/api/rotation/me/act');`,c);
    await c.run();
    assert.equal(vm.runInContext('RT.pendingConfirmation',c),null);
    assert.equal(vm.runInContext('RT.modalOpen',c),false);
  }
  {
    const c=context();
    vm.runInContext('responses=[];rtApi=()=>new Promise(resolve=>responses.push(resolve));RT.render=()=>{};',c);
    const first=vm.runInContext("loadLive('/state')",c);
    const second=vm.runInContext("loadLive('/state')",c);
    c.responses[1]({version:4,clock:{},marker:'new'});
    await second;
    c.responses[0]({version:3,clock:{},marker:'old'});
    await first;
    assert.equal(vm.runInContext('RT.data.marker',c),'new');
    vm.runInContext(`RT.sync='offline';acceptLiveData({version:5,clock:{}});`,c);
    assert.equal(vm.runInContext('RT.sync',c),'polling');
  }
  console.log('rotation confirmation: mobile UI, snapshots, unknown results, conflicts and response ordering passed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
