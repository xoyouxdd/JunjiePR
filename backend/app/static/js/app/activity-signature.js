import { api, json } from './api.js';
import { esc, portalPath } from './format.js';
import { formModal, noticeModal } from './dialogs.js';
import { newSubmissionKey } from './submission.js';
import { toast } from './toast.js';

const time=value=>String(value||'').replace('T',' ').slice(0,19);
function facts(snapshot){return `<dl class="receipt-facts"><div><dt>员工</dt><dd>${esc(snapshot.employee_name)} · ${esc(snapshot.employee_no)}</dd></div><div><dt>活动 / 凭证</dt><dd>${esc(snapshot.activity)} · #${snapshot.award_id}</dd></div><div><dt>奖品</dt><dd>${esc(snapshot.prize)} × ${snapshot.quantity} · ${snapshot.cost}积点</dd></div><div><dt>发放</dt><dd>${esc(snapshot.issued_name)} · ${esc(time(snapshot.issued_at))}</dd></div></dl>`;}

export async function signActivityAward(awardId){
  const context=await api(`/api/activities/awards/${awardId}/signing`),snapshot=context.snapshot;
  const requestKey=newSubmissionKey();let cleanup;
  try{return await formModal('本人确认领取并签字','请核对实物，只有本人收到且确认无误后再签字。',`${facts(snapshot)}
    ${snapshot.photo_url?`<img class="receipt-prize-photo" src="${esc(portalPath(snapshot.photo_url))}" alt="${esc(snapshot.prize)}">`:''}
    <p class="field-hint">请在下面手写签名（支持手指或触控笔）。</p>
    <canvas class="receipt-signature-pad" width="900" height="540" aria-label="本人手写签名区域"></canvas>
    <button type="button" class="secondary" data-signature-clear>清除重签</button>
    <label class="receipt-confirm"><input type="checkbox" name="accepted">${esc(context.confirmation)}</label>
    <p class="field-hint">签收后保存签名、奖品信息和时间；不会再次扣积点。</p>`, '提交签收并归档', {
      lockWhileSubmitting:true,
      onReady(form){
        const canvas=form.querySelector('canvas'),ctx=canvas.getContext('2d');
        let busy=false,ink=false,point=null;
        const clear=()=>{if(busy)return;ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);ink=false;point=null;};
        clear();ctx.strokeStyle='#162b43';ctx.lineWidth=7;ctx.lineCap='round';ctx.lineJoin='round';
        const position=event=>{const rect=canvas.getBoundingClientRect();return {x:(event.clientX-rect.left)*canvas.width/rect.width,y:(event.clientY-rect.top)*canvas.height/rect.height};};
        canvas.onpointerdown=event=>{if(busy)return;event.preventDefault();point={...position(event),id:event.pointerId};canvas.setPointerCapture(event.pointerId);};
        canvas.onpointermove=event=>{if(busy||!point||point.id!==event.pointerId)return;event.preventDefault();const p=position(event);ctx.beginPath();ctx.moveTo(point.x,point.y);ctx.lineTo(p.x,p.y);ctx.stroke();if(Math.hypot(p.x-point.x,p.y-point.y)>1)ink=true;point={...p,id:event.pointerId};};
        canvas.onpointerup=canvas.onpointercancel=()=>{point=null;};
        form.querySelector('[data-signature-clear]').onclick=clear;
        form.signaturePayload=()=>{if(!form.elements.accepted.checked)throw new Error('请确认本人已收到上述奖品');if(!ink)throw new Error('请手写签名后提交');busy=true;form.elements.accepted.disabled=true;return canvas.toDataURL('image/png');};
        form.signatureUnlock=()=>{busy=false;form.elements.accepted.disabled=false;};
        cleanup=()=>{canvas.onpointerdown=canvas.onpointermove=canvas.onpointerup=canvas.onpointercancel=null;canvas.width=1;canvas.height=1;};
      },
      async onSubmit(form){
        const signature=form.signaturePayload();
        try{await api(`/api/activities/awards/${awardId}/sign`,json('POST',{request_key:requestKey,
          revision:context.revision,snapshot_hash:context.snapshot_hash,accepted:true,signature}));toast('签收已归档');}
        finally{form.signatureUnlock();}
      }
    });}finally{cleanup?.();}
}

export async function viewActivityReceipt(id){
  const data=await api(`/api/activities/receipts/${id}`);
  await noticeModal('本人签收档案',`${facts(data.snapshot)}
    <p>签收时间：${esc(time(data.signed_at))}</p><p>${esc(data.confirmation)}</p>
    ${data.current_state==='refunded'?'<p class="field-hint">该奖品后续已退款，原签收档案保留。</p>':''}
    <img class="receipt-signature-image" src="${esc(portalPath(data.signature_url))}" alt="${esc(data.snapshot.employee_name)}的领取签名">
    <p class="receipt-archive-id">档案：${esc(data.id)}</p><details><summary>查看档案校验摘要</summary><code class="receipt-archive-id">${esc(data.evidence_hash)}</code></details>`, '关闭档案');
}
