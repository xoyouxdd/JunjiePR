// Local, deterministic photo editing. Only the confirmed compressed JPEG is uploaded.
import { api } from './api.js';
import { toast } from './toast.js';

const MAX_BYTES=500*1024, MAX_SIDE=1600;
const states=new WeakMap();
const range=(name,label,min,max,value,step=1)=>`<label>${label}<input type="range" data-photo-control="${name}" min="${min}" max="${max}" value="${value}" step="${step}"></label>`;

export function prizePhotoFields(){return `<div class="prize-photo" data-prize-photo-editor>
  <div class="activity-actions"><button type="button" class="secondary" data-photo-pick="camera">拍照</button><button type="button" class="secondary" data-photo-pick="album">相册 / 选图</button></div>
  <input type="file" accept="image/*" capture="environment" data-photo-file="camera" hidden>
  <input type="file" accept="image/*" data-photo-file="album" hidden>
  <figure class="prize-photo-result" hidden><img alt="已确认的奖品照片"><figcaption data-photo-size></figcaption><div class="activity-actions"><button type="button" class="secondary" data-photo-edit>重新裁剪</button><button type="button" class="secondary" data-photo-remove>移除照片</button></div></figure>
  <div class="prize-photo-edit" data-photo-edit-panel hidden>
    <p class="field-hint">拖动照片调整位置，也可用下方滑块精确调整。预览下方显示奖品名称。</p>
    <canvas data-photo-canvas aria-label="奖品裁剪预览"></canvas><p class="prize-photo-caption" data-photo-name>奖品名称</p>
    <label>画面比例<select data-photo-control="ratio"><option value="1">方形</option><option value="1.3333333333">横图 4:3</option><option value="original">完整图片</option></select></label>
    ${range('zoom','放大裁剪',1,3,1,.01)}${range('x','左右位置',0,100,50)}${range('y','上下位置',0,100,50)}
    <details class="prize-photo-adjust"><summary>可选：亮度与色彩调整</summary>${range('brightness','亮度（100为原样）',80,120,100)}${range('saturation','色彩（100为原样）',80,120,100)}<p class="field-hint">仅作轻度调整，请保持奖品真实颜色和外观。</p></details>
    <div class="activity-actions"><button type="button" class="secondary" data-photo-reset>重置裁剪与调色</button><button type="button" class="secondary" data-photo-cancel>取消裁剪</button><button type="button" class="primary" data-photo-apply>确认照片并压缩</button></div>
  </div><p class="field-hint" data-photo-status role="status">照片选填；确认后自动压缩，不上传原始照片。</p>
  </div>`;}

function editorState(editor){
  let state=states.get(editor);
  if(!state){state={editor,source:null,confirmed:null,blob:null,url:null,photoId:null,version:0,busy:false,editing:false,disposed:false};states.set(editor,state);}
  return state;
}
const control=(s,name)=>s.editor.querySelector(`[data-photo-control="${name}"]`);
const status=(s,text)=>{s.editor.querySelector('[data-photo-status]').textContent=text;};
function caption(s){const label=s.editor.closest('.activity-prize-editor')?.querySelector('[name=prize_name]')?.value.trim();s.editor.querySelector('[data-photo-name]').textContent=label||'奖品名称';}
function reset(s){for(const [name,value] of Object.entries({ratio:'1',zoom:1,x:50,y:50,brightness:100,saturation:100}))control(s,name).value=value;}
function restore(s){if(s.confirmed){s.source=s.confirmed.source;for(const [name,value] of Object.entries(s.confirmed.values))control(s,name).value=value;}else s.source=null;}
function geometry(s){
  const w=s.source.width,h=s.source.height,r=control(s,'ratio').value==='original'?w/h:Number(control(s,'ratio').value),z=Number(control(s,'zoom').value);
  const cw=(w/h>r?h*r:w)/z,ch=cw/r;
  return {x:(w-cw)*Number(control(s,'x').value)/100,y:(h-ch)*Number(control(s,'y').value)/100,w:cw,h:ch,r};
}
function draw(s,canvas,limit){
  const crop=geometry(s),scale=Math.min(1,limit/Math.max(crop.w,crop.h));
  canvas.width=Math.max(1,Math.round(crop.w*scale));canvas.height=Math.max(1,Math.round(crop.h*scale));
  const ctx=canvas.getContext('2d',{willReadFrequently:true});
  if(!ctx)throw new Error('当前浏览器无法处理图片，请更换浏览器重试');
  ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);
  ctx.drawImage(s.source,crop.x,crop.y,crop.w,crop.h,0,0,canvas.width,canvas.height);
  const brightness=Number(control(s,'brightness').value)/100,saturation=Number(control(s,'saturation').value)/100;
  // Pixel adjustment also works in mobile browsers without CanvasRenderingContext2D.filter.
  if(brightness!==1||saturation!==1){
    const frame=ctx.getImageData(0,0,canvas.width,canvas.height),pixels=frame.data;
    for(let i=0;i<pixels.length;i+=4){const grey=.2126*pixels[i]+.7152*pixels[i+1]+.0722*pixels[i+2];for(let c=0;c<3;c++)pixels[i+c]=(grey+(pixels[i+c]-grey)*saturation)*brightness;}
    ctx.putImageData(frame,0,0);
  }
}
function preview(s){if(!s.source||s.disposed)return;draw(s,s.editor.querySelector('canvas'),420);caption(s);}
function setEditing(s,value){s.editing=value;s.editor.querySelector('[data-photo-edit-panel]').hidden=!value;}
function dispose(s){s.disposed=true;s.version++;if(s.url)URL.revokeObjectURL(s.url);s.url=null;s.source=null;s.confirmed=null;s.blob=null;}

async function choose(s,file){
  if(!file)return;
  if(s.busy)throw new Error('照片正在处理中，请稍候');
  if(file.size>25*1024*1024)throw new Error('照片超过25MB，请选择较小的图片');
  if(file.type==='image/svg+xml'||/\.svg$/i.test(file.name))throw new Error('请选择照片格式，不支持SVG图片');
  s.busy=true;const version=++s.version;status(s,'正在读取照片…');
  const url=URL.createObjectURL(file),image=new Image();
  try{
    await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(new Error('无法读取照片，请选择JPG、PNG、WebP，或重新拍照'));image.src=url;});
    if(s.disposed||version!==s.version)return;
    if(!image.naturalWidth||image.naturalWidth*image.naturalHeight>60_000_000)throw new Error('照片像素过大，请先缩小后选择');
    const scale=Math.min(1,2048/Math.max(image.naturalWidth,image.naturalHeight)),source=document.createElement('canvas');
    source.width=Math.max(1,Math.round(image.naturalWidth*scale));source.height=Math.max(1,Math.round(image.naturalHeight*scale));
    const ctx=source.getContext('2d');if(!ctx)throw new Error('当前浏览器无法读取照片');
    ctx.drawImage(image,0,0,source.width,source.height);
    s.source=source;reset(s);setEditing(s,true);preview(s);status(s,'请确认裁剪效果；未确认的照片不会保存。');
  }finally{URL.revokeObjectURL(url);s.busy=false;}
}

const jpeg=(canvas,quality)=>new Promise((resolve,reject)=>canvas.toBlob(blob=>blob?resolve(blob):reject(new Error('照片压缩失败，请重新选择')), 'image/jpeg',quality));
async function apply(s){
  if(s.busy||!s.source)return;
  s.busy=true;status(s,'正在压缩照片…');const version=s.version;
  try{
    const confirmed={source:s.source,values:Object.fromEntries(['ratio','zoom','x','y','brightness','saturation'].map(name=>[name,control(s,name).value]))};
    let canvas=document.createElement('canvas');draw(s,canvas,MAX_SIDE);let blob;
    for(let pass=0;pass<8;pass++){
      for(const quality of [.86,.76,.66,.56,.46]){blob=await jpeg(canvas,quality);if(blob.size<=MAX_BYTES)break;}
      if(blob.size<=MAX_BYTES)break;
      const smaller=document.createElement('canvas');smaller.width=Math.max(1,Math.floor(canvas.width*.8));smaller.height=Math.max(1,Math.floor(canvas.height*.8));smaller.getContext('2d').drawImage(canvas,0,0,smaller.width,smaller.height);canvas=smaller;
    }
    if(s.disposed||version!==s.version)return;
    if(blob.size>MAX_BYTES)throw new Error('压缩失败，请尝试缩小裁剪范围');
    if(s.url)URL.revokeObjectURL(s.url);
    s.blob=blob;s.photoId=null;s.url=URL.createObjectURL(blob);s.confirmed=confirmed;
    const result=s.editor.querySelector('.prize-photo-result');result.hidden=false;result.querySelector('img').src=s.url;
    result.querySelector('img').alt=s.editor.closest('.activity-prize-editor').querySelector('[name=prize_name]').value||'奖品照片';
    s.editor.querySelector('[data-photo-size]').textContent=`${canvas.width} × ${canvas.height} · ${Math.ceil(blob.size/1024)} KB`;
    setEditing(s,false);status(s,'照片已确认，保存活动时上传。');
  }finally{s.busy=false;}
}

export function bindPrizePhotos(form){
  const owned=new Set();let disposed=false;
  const get=editor=>{const s=editorState(editor);owned.add(s);return s;};
  const onChange=event=>{
    const input=event.target.closest('[data-photo-file]');if(!input)return;
    const s=get(input.closest('[data-prize-photo-editor]')),file=input.files?.[0]||input.files?.item?.(0);input.value='';
    choose(s,file).catch(error=>{if(!s.disposed){status(s,error.message);toast(error.message,true);}});
  };
  const onInput=event=>{
    const editor=event.target.closest('[data-prize-photo-editor]');
    if(editor&&event.target.matches('[data-photo-control]')){const s=get(editor);if(!s.busy)preview(s);}
    if(event.target.matches('[name=prize_name]')){const target=event.target.closest('.activity-prize-editor').querySelector('[data-prize-photo-editor]');if(target){const s=get(target);caption(s);target.querySelector('.prize-photo-result img').alt=event.target.value||'奖品照片';}}
  };
  const onClick=event=>{
    const button=event.target.closest('button'),editor=button?.closest('[data-prize-photo-editor]');if(!editor)return;
    const s=get(editor);if(s.busy){toast('照片正在处理中，请稍候');return;}
    if(button.hasAttribute('data-photo-pick'))editor.querySelector(`[data-photo-file="${button.dataset.photoPick}"]`).click();
    if(button.hasAttribute('data-photo-edit')){restore(s);setEditing(s,true);preview(s);}
    if(button.hasAttribute('data-photo-reset')){reset(s);preview(s);}
    if(button.hasAttribute('data-photo-cancel')){restore(s);setEditing(s,false);status(s,s.blob?'保留上一张已确认的照片。':'未保存照片，可重新拍照或选图。');}
    if(button.hasAttribute('data-photo-remove')){if(s.url)URL.revokeObjectURL(s.url);s.url=null;s.blob=null;s.photoId=null;s.confirmed=null;s.source=null;editor.querySelector('.prize-photo-result').hidden=true;setEditing(s,false);status(s,'照片已移除。');}
    if(button.hasAttribute('data-photo-apply'))apply(s).catch(error=>{if(!s.disposed){status(s,error.message);toast(error.message,true);}});
  };
  // Dragging complements the sliders, which remain keyboard-accessible.
  let drag=null;
  const down=event=>{
    if(!event.target.matches('[data-photo-canvas]'))return;
    const s=get(event.target.closest('[data-prize-photo-editor]'));if(s.busy||!s.source)return;
    drag={s,id:event.pointerId,x:event.clientX,y:event.clientY,px:Number(control(s,'x').value),py:Number(control(s,'y').value)};
    event.target.setPointerCapture(event.pointerId);
  };
  const move=event=>{if(!drag||drag.id!==event.pointerId||drag.s.busy)return;const rect=event.target.getBoundingClientRect();control(drag.s,'x').value=Math.max(0,Math.min(100,drag.px-(event.clientX-drag.x)/rect.width*100));control(drag.s,'y').value=Math.max(0,Math.min(100,drag.py-(event.clientY-drag.y)/rect.height*100));preview(drag.s);};
  const up=()=>{drag=null;};
  form.addEventListener('change',onChange);form.addEventListener('input',onInput);form.addEventListener('click',onClick);
  form.addEventListener('pointerdown',down);form.addEventListener('pointermove',move);form.addEventListener('pointerup',up);form.addEventListener('pointercancel',up);
  const observer=new MutationObserver(()=>{for(const s of owned)if(!form.contains(s.editor)){dispose(s);owned.delete(s);}});observer.observe(form,{childList:true,subtree:true});
  return ()=>{if(disposed)return;disposed=true;observer.disconnect();owned.forEach(dispose);form.removeEventListener('change',onChange);form.removeEventListener('input',onInput);form.removeEventListener('click',onClick);form.removeEventListener('pointerdown',down);form.removeEventListener('pointermove',move);form.removeEventListener('pointerup',up);form.removeEventListener('pointercancel',up);};
}

export async function uploadPrizePhotos(form){
  const editors=[...form.querySelectorAll('[data-prize-photo-editor]')],result=new Map();
  for(const editor of editors){const s=states.get(editor);if(s&&(s.busy||s.editing))throw new Error('请先确认或取消正在编辑的奖品照片');}
  const locked=[...form.querySelectorAll('input,select,textarea,button')].map(node=>[node,node.disabled]);locked.forEach(([node])=>{node.disabled=true;});
  try{
    for(const editor of editors){
      const s=states.get(editor);if(!s?.blob)continue;
      if(!s.photoId){status(s,'正在上传压缩照片…');const body=new FormData();body.append('file',s.blob,'prize.jpg');const photo=await api('/api/activities/photos',{method:'POST',body});s.photoId=photo.id;status(s,'照片已上传。');}
      result.set(editor.closest('.activity-prize-editor'),s.photoId);
    }
    return result;
  }finally{locked.forEach(([node,disabled])=>{node.disabled=disabled;});}
}
