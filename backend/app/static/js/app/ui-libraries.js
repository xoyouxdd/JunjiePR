// Optional, same-origin UI assets. No CDN, API calls or eager downloads.
const pendingLibraries=new Map();

function libraryUrl(path){
  const url=new URL('../../vendor/'+path,import.meta.url);
  const version=new URL(import.meta.url).searchParams.get('v');
  if(version)url.searchParams.set('v',version);
  return url.href;
}

function loadAsset(tag,path){
  return new Promise((resolve,reject)=>{
    const node=document.createElement(tag);
    const timer=setTimeout(()=>{node.remove();reject(new Error('界面组件加载超时'));},10000);
    node.onload=()=>{clearTimeout(timer);resolve(node);};
    node.onerror=()=>{clearTimeout(timer);node.remove();reject(new Error('界面组件暂时不可用'));};
    if(tag==='link'){node.rel='stylesheet';node.href=libraryUrl(path);}
    else{node.src=libraryUrl(path);node.async=true;}
    document.head.append(node);
  });
}

function loadUiLibrary(name){
  if(pendingLibraries.has(name))return pendingLibraries.get(name);
  const assets=name==='table'?
    [['link','tabulator-6.3.1/tabulator.min.css'],['script','tabulator-6.3.1/tabulator.min.js']]:
    [['script','echarts-6.0.0/echarts.workbench.min.js']];
  const loading=Promise.all(assets.map(([tag,path])=>loadAsset(tag,path)))
    .then(()=>name==='table'?window.Tabulator:window.echarts)
    .catch(()=>{pendingLibraries.delete(name);return null;});
  pendingLibraries.set(name,loading);
  return loading;
}

export { loadUiLibrary };
