// Entry into the independent rotation test pages.
import { api, json } from './api.js';
import { beginViewRequest } from './context.js';
import { esc, portalPath } from './format.js';
import { app } from './state.js';
import { toast } from './toast.js';

// 轮岗（测试）：输入模拟账号后进入独立的轮岗页面；数据全为模拟，不影响正式功能。
async function renderRotationTest(){
  const request=beginViewRequest();
  const quick=[['6666666','休息室大屏'],['7777777','轮岗主管'],['8888888','轮岗经理']];
  if(!request.write(`<section class="panel rotation-entry"><div class="rotation-entry-heading"><h2>轮岗</h2><span class="rotation-test-badge">测试</span></div><p>轮岗功能正在测试，所有数据均为模拟数据，不影响正式的签卡、待办和统计。</p><form id="rotationEnterForm" class="form-stack"><label>模拟账号<input name="account" inputmode="numeric" autocomplete="off" maxlength="50" placeholder="输入模拟账号，或名单中 CM/TR 的工号" required></label><button type="submit" class="primary">进入</button></form><div class="rotation-entry-quick">${quick.map(([no,label])=>`<button type="button" class="secondary" data-rotation-account="${no}"><span>${esc(label)}</span><small>${no}</small></button>`).join('')}</div><p class="field-hint">输入名单中某位 CM/TR 的工号，可查看他的个人轮岗和待办。</p></section>`))return;
  const enter=async account=>{try{const out=await api('/api/rotation/enter',json('POST',{account}));location.href=portalPath(out.redirect);}catch(error){toast(error.message,true);}};
  const form=document.getElementById('rotationEnterForm');
  form.onsubmit=event=>{event.preventDefault();const value=form.account.value.trim();if(value)void enter(value);};
  app.querySelectorAll('[data-rotation-account]').forEach(button=>button.onclick=()=>enter(button.dataset.rotationAccount));
}

export { renderRotationTest };
