// Owns page generations, latest-request guards, cancellation, timers and registered page rendering.
import { esc } from './format.js';
import { app, state } from './state.js';

let renderGeneration=0;

let viewRequestId=0;

let renderAbortController=null;

// Capture the owner before an await; a completed write must not repaint a newer view.
function captureViewContext(root=app,{latest=true}={}){
  const generation=renderGeneration,tab=state.tab,id=viewRequestId;
  return {
    isCurrent(){return generation===renderGeneration&&tab===state.tab&&(!latest||id===viewRequestId)&&(root===app||app.contains(root));},
    refresh(view,...args){if(!this.isCurrent())return;return view(...args);}
  };
}

function beginViewRequest(){
  ++viewRequestId;
  const context=captureViewContext();
  return {
    ...context,
    write(html){if(!this.isCurrent())return false;app.innerHTML=html;return true;}
  };
}

const activePageTimers=new Set();

const activePageCleanups=new Set();

function pageTimeout(callback,delay){const timer=setTimeout(()=>{activePageTimers.delete(timer);callback();},delay);activePageTimers.add(timer);return timer;}

function registerPageCleanup(callback){activePageCleanups.add(callback);return callback;}

function clearPageResources(){activePageTimers.forEach(clearTimeout);activePageTimers.clear();activePageCleanups.forEach(callback=>{try{callback();}catch(_){}});activePageCleanups.clear();}

async function render(){
  const generation=++renderGeneration;
  clearPageResources();
  renderAbortController?.abort();
  const controller=new AbortController();
  renderAbortController=controller;
  app.innerHTML='<section class="empty skeleton" aria-busy="true">正在加载…</section>';
  let viewContext;
  try {
    const pending=(registeredViews[state.tab] || registeredViews.home)();
    viewContext=captureViewContext();
    await pending;
    if(generation!==renderGeneration) return;
  } catch(e) {
    if(generation!==renderGeneration||(viewContext&&!viewContext.isCurrent())||e?.name==='AbortError') return;
    app.innerHTML=`<section class="panel"><div class="error">${esc(e.message)}</div></section>`;
  }
}

// Composition owns the page table; feature modules never import the entry point.
let registeredViews=Object.freeze({});
function configureViews(views){registeredViews=Object.freeze({...views});}
function getViewSignal(){return renderAbortController?.signal;}

export { beginViewRequest, captureViewContext, clearPageResources, configureViews, getViewSignal, pageTimeout, registerPageCleanup, render };
