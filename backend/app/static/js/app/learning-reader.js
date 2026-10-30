// One PDF page at a time, rendered in the browser from the shared original bytes.
import { portalPath } from './format.js';
const base='/static/vendor/pdfjs-5.6.205/';
export async function mountLearningPdf(host,bytes,isCurrent){
 const pdfjs=await import(portalPath(base+'pdf.min.mjs'));
 if(!isCurrent()||!host.isConnected)return ()=>{};
 pdfjs.GlobalWorkerOptions.workerSrc=portalPath(base+'pdf.worker.min.mjs');
 const task=pdfjs.getDocument({data:new Uint8Array(bytes),cMapUrl:portalPath(base+'cmaps/'),cMapPacked:true,
  standardFontDataUrl:portalPath(base+'standard_fonts/'),wasmUrl:portalPath(base+'wasm/'),
  isEvalSupported:false,useWasm:false,isOffscreenCanvasSupported:false});
 let closed=false,document,pageNumber=1,renderTask,observer,width=0,sequence=0;
 function dispose(){closed=true;observer?.disconnect();renderTask?.cancel();void task.destroy().catch(()=>{});}
 // Attach cleanup before any PDF load finishes.
 host.learningPdfDispose=dispose;
 try{
  document=await task.promise;if(closed||!isCurrent()||!host.isConnected){dispose();return dispose;}
  host.innerHTML='<div class="learning-pdf-controls"><button type="button" data-pdf-prev>上一页</button><span data-pdf-page aria-live="polite"></span><button type="button" data-pdf-next>下一页</button></div><canvas class="learning-pdf-canvas" aria-label="学习材料页面"></canvas><p data-pdf-error role="alert"></p>';
  const canvas=host.querySelector('canvas'),prev=host.querySelector('[data-pdf-prev]'),next=host.querySelector('[data-pdf-next]');
  async function render(){const seq=++sequence;renderTask?.cancel();prev.disabled=pageNumber<=1;next.disabled=pageNumber>=document.numPages;host.querySelector('[data-pdf-page]').textContent=pageNumber+' / '+document.numPages;
   try{const page=await document.getPage(pageNumber);if(closed||seq!==sequence||!isCurrent())return;
    const available=Math.max(1,host.clientWidth),ratio=Math.min(window.devicePixelRatio||1,2),natural=page.getViewport({scale:1});
    const viewport=page.getViewport({scale:available/natural.width});canvas.width=Math.ceil(viewport.width*ratio);canvas.height=Math.ceil(viewport.height*ratio);
    renderTask=page.render({canvasContext:canvas.getContext('2d'),viewport,transform:ratio===1?null:[ratio,0,0,ratio,0,0]});
    await renderTask.promise;if(seq===sequence&&!closed)canvas.dataset.pdfRendered='true';
   }catch(error){if(error.name!=='RenderingCancelledException'&&!closed&&isCurrent())host.querySelector('[data-pdf-error]').textContent='页面未能显示，请重新打开材料。';}
  }
  prev.onclick=()=>{if(pageNumber>1){pageNumber--;void render();}};next.onclick=()=>{if(pageNumber<document.numPages){pageNumber++;void render();}};
  observer=new ResizeObserver(()=>{if(Math.abs(host.clientWidth-width)>1){width=host.clientWidth;void render();}});observer.observe(host);
  await render();return dispose;
 }catch(error){dispose();throw error;}
}
