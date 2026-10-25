'use strict';
// Separate field session; ordinary screen account and PR session remain intact.
let adjustmentState=null,adjustLastInput=Date.now(),adjustChecking=false,adjustExitPending=false;
const screenPage=()=>document.body.classList.contains('rotation-screen');
function adjustmentHeader(){
  let host=document.getElementById('rtAdjustment');
  if(!host){host=document.createElement('div');host.id='rtAdjustment';host.className='rt-adjustment';document.querySelector('.rt-top').after(host);}
  const manager=typeof me!=='undefined'&&me&&['supervisor','manager'].includes(me.kind);
  const grantable=adjustmentState?.can_authorize||manager&&me.can_authorize!==false;
  host.innerHTML=adjustmentState?`<strong>${esc(adjustmentState.name)}</strong><span data-adjust-countdown></span><span>操作完毕，请退出调整</span><div class="rt-adjust-buttons"><a class="rt-adjust-link" href="${portalPath('/rotation')}">现场看板</a>${grantable?'<button type="button" class="secondary" data-adjust-grants>TR临时授权</button>':''}<button type="button" class="primary" data-adjust-exit>退出调整</button></div>`:
    `${screenPage()?'<span>休息室大屏</span><button type="button" class="secondary" data-adjust-login>登录调整</button>':''}${manager&&grantable?'<button type="button" class="secondary" data-adjust-grants>TR临时授权</button>':''}`;
  host.hidden=!host.textContent.trim();
  host.querySelector('[data-adjust-login]')?.addEventListener('click',adjustLogin);
  host.querySelector('[data-adjust-exit]')?.addEventListener('click',()=>adjustLogout());
  host.querySelector('[data-adjust-grants]')?.addEventListener('click',adjustGrants);
  adjustmentTick();
}
async function refreshAdjustment(){
  if(formalRotation)return;
  if(adjustChecking)return;
  if(adjustExitPending){await adjustLogout(true);return;}
  adjustChecking=true;
  try{const value=await rtApi('/api/rotation/adjust/status');
    if(adjustmentState&&!value.active){await adjustLogout(true,'授权已到期或被撤销，已退出调整');return;}
    const changed=JSON.stringify(adjustmentState)!==JSON.stringify(value.active?value:null);adjustmentState=value.active?value:null;
    if(changed){adjustmentHeader();if(typeof renderTabs==='function')renderTabs();if(RT.data&&!RT.modalOpen)RT.render();}else if(!adjustmentState)adjustmentHeader();
  }catch(_){/* A failed status read never enables controls. Server checks every write. */}
  finally{adjustChecking=false;}
}
function adjustmentTick(){
  if(!adjustmentState)return;
  const left=Math.max(0,Math.ceil((new Date(adjustmentState.expires_at).getTime()-Date.now())/1000));
  const el=document.querySelector('[data-adjust-countdown]');if(el)el.textContent=`${adjustmentState.temporary?'TR授权剩余':'操作会话剩余'} ${pad2(Math.floor(left/60))}:${pad2(left%60)}`;
  if(!left)void adjustLogout(true,'授权已到期，已退出调整');
  else if(Date.now()-adjustLastInput>120000)void adjustLogout(true,'2分钟未操作，已退出调整');
}
async function adjustLogout(force=false,message='已退出调整，恢复大屏展示'){
  if(!force&&document.querySelector('#rtModal form,#rtModal .rt-edit-preview')){confirmBox('退出调整','有未提交内容，退出后将放弃。','放弃并退出',()=>adjustLogout(true));return false;}
  adjustExitPending=true;
  try{await rtPost('/api/rotation/adjust/logout');adjustExitPending=false;adjustmentState=null;closeModal();adjustmentHeader();toast(message);if(screenPage())await RT.load();else location.href=portalPath('/rotation/screen');return true;}
  catch(e){adjustmentState=null;closeModal();adjustmentHeader();if(typeof renderTabs==='function')renderTabs();if(RT.data)RT.render();toast('已停止本页调整，请联网后重新核实退出状态',true);return false;}
}
function adjustLogin(){
  formBox('登录调整',`<label>身份<select name="mode"><option value="manager">TA LEAD / LEAD及以上</option><option value="tr">TR临时授权</option></select></label><label>本人账号<input name="account" autocomplete="off" required></label><label>密码 / 临时授权码<input name="credential" type="password" autocomplete="off" required></label>`,async values=>{
    try{await rtPost('/api/rotation/adjust/login',values);adjustLastInput=Date.now();await refreshAdjustment();toast('已进入调整，操作完毕请退出');return true;}catch(e){toast(e.message,true);return false;}
  },{okText:'登录调整'});
}
function afterRotationAdjustment(){
  if(!adjustmentState)return;
  setTimeout(()=>openModal('<h3>调整已完成</h3><p>操作完毕，请退出调整。</p><div class="modal-actions"><button type="button" class="secondary" data-close>继续调整</button><button type="button" class="primary" data-adjust-finish>退出调整</button></div>',host=>host.querySelector('[data-adjust-finish]').onclick=()=>adjustLogout(true)),0);
}
async function adjustPreview(body){
  try{const result=await rtPost('/api/rotation/adjust/preview',body);
    setTimeout(()=>confirmBox('确认调整',`<div class="rt-edit-preview"><strong>${esc(result.summary)}</strong><ul>${result.changes.map(x=>`<li>${esc(x)}</li>`).join('')||'<li>保存计划时间；到点执行。</li>'}</ul></div>`,'执行调整',async()=>{
      const ok=await act('/api/rotation/adjust/act',{...body,expected_snapshot:result.expected_snapshot},'调整已同步');return ok;
    }),0);return true;
  }catch(e){toast(e.message,true);return false;}
}
function adjustClosePost(line,i){
  formBox('撤岗方式',`<label>处理方式<select name="closeMode"><option value="push">撤岗并顺推</option><option value="direct">撤岗并下线</option></select></label><label>撤岗时间（留空立即执行）<input name="closeAt" type="time"></label>`,values=>adjustPreview({action:'post',line,i,open:false,...(values.closeAt?{closeAt:values.closeAt}:{}),closeMode:values.closeMode}),{warn:true,okText:'预览影响'});
}
function adjustPostMenu(line,i){
  const L=RT.data.day.lines.find(x=>x.id===line),post=L?.posts[i];if(!post)return;
  const people=RT.data.day.persons.filter(p=>['onpost','notyet','rest','meal','ready','walkback','pending','away'].includes(p.state));
  openModal(`<h3>${esc(line)} · ${esc(post.name)}</h3><p>${post.occ?esc(personOf(post.occ)?.name):post.open?'空岗':'未开放'}</p>${post.hold?`<p class="field-hint">${hm(Math.floor(post.hold.since))}起持续定岗 · ${esc(post.hold.reason)}<br>手动取消前不参与自动调整，零点自动结束。</p>`:''}<div class="rt-actions">${formalRotation&&post.occ&&personOf(post.occ)?.state==='onpost'?`<button type="button" class="${post.hold?'primary':'secondary'}" data-edit-hold>${post.hold?'取消定岗，恢复轮岗':'设置持续定岗'}</button>`:''}<button type="button" class="secondary" data-edit-move>调整人员</button><button type="button" class="secondary" data-edit-order>调整岗位顺序</button><button type="button" class="${post.open?'danger':'primary'}" data-edit-post>${post.open?'撤岗 / 修改撤岗时间':'加岗 / 修改加岗时间'}</button>${!post.open&&!post.opened&&post.openAt?'<button type="button" class="secondary" data-cancel-add>取消待加岗</button>':''}${post.open&&post.closeAt?'<button type="button" class="secondary" data-cancel-close>取消待撤岗</button>':''}<button type="button" class="secondary" data-edit-line>${L.active?'停用':'启用'}整条线</button>${post.occ?'<button type="button" class="secondary" data-edit-person>人员操作</button>':''}<button type="button" class="ghost" data-close>关闭</button></div>`,host=>{
    host.querySelector('[data-edit-hold]')?.addEventListener('click',()=>{
      const body={action:'post_hold',line,i,pid:post.occ,expected_post:post.name,on:!post.hold};
      if(post.hold)confirmBox('取消定岗',`<p>${esc(personOf(post.occ)?.name)}将按当前时间恢复原规则。已到下班、出圈或闭园时间的，会按对应规则处理；不会补推错过的轮次。</p>`,'取消定岗',()=>act('/api/rotation/act',body,'已取消定岗，恢复原规则'));
      else formBox('设置持续定岗',`<p>${esc(line)} · ${esc(post.name)} · ${esc(personOf(post.occ)?.name)}</p><label>定岗说明（选填）<input name="reason" maxlength="100" placeholder="例如现场任务、特殊接待"></label><p class="field-hint">其他岗位跳过此岗继续流转。下班、计划出圈、闭园不自动解除；请结束后手动取消，零点自动结束。</p>`,values=>act('/api/rotation/act',{...body,reason:values.reason},'持续定岗已同步'));
    });
    host.querySelector('[data-edit-post]').onclick=()=>post.open?adjustClosePost(line,i):formBox('加岗时间',`<label>时间<input type="time" name="openAt" value="${!post.opened&&post.openAt?post.openAt:hm(Math.ceil(nowMin()+3))}" required></label>`,v=>adjustPreview({action:'post',line,i,open:true,openAt:v.openAt}),{okText:'预览影响'});
    host.querySelector('[data-cancel-add]')?.addEventListener('click',()=>adjustPreview({action:'post',line,i,open:false,closeMode:'direct'}));
    host.querySelector('[data-cancel-close]')?.addEventListener('click',()=>adjustPreview({action:'post',line,i,open:false,cancelClose:true}));
    host.querySelector('[data-edit-order]').onclick=()=>formBox('选择交换岗位',`<label>交换到<select name="target_i">${L.posts.map((p,j)=>`<option value="${j}" ${i===j?'selected':''}>第${j+1}岗 · ${esc(p.name)}</option>`).join('')}</select></label>`,v=>postSwapForm(line,i,Number(v.target_i)),{okText:'选择交换方式'});
    host.querySelector('[data-edit-move]').onclick=()=>formBox('调整人员',`<label>人员<select name="pid">${people.map(p=>`<option value="${esc(p.pid)}">${esc(p.name)} · ${STATE_NAME[p.state]}</option>`).join('')}</select></label><p>在岗人员可交换；休息、吃饭及饭后休息人员需填写强制安排原因，再预览确认。</p>`,v=>personDrop(v.pid,line,i),{okText:'下一步'});
    host.querySelector('[data-edit-line]').onclick=()=>confirmBox('调整整条线',L.active?'停用后本线人员全部下线。':'启用后按既有岗位配置派人。','确认调整',()=>act('/api/rotation/act',{action:'line',line,active:!L.active},'线路已调整'));
    host.querySelector('[data-edit-person]')?.addEventListener('click',()=>adjustPersonMenu(personOf(post.occ)));
  });
}
function adjustPersonMenu(p){
  if(typeof personMenu==='function'){personMenu(p);return;}
  const actions=[['away','标记暂离'],['leave','离岗'],...(p.state==='away'?[['back','返回']]:[]),...(['rest','ready','meal'].includes(p.state)?[['undo','撤回到达']]:[]),...(p.state==='onpost'?[['fix_remove','直接下线']]:[]),['flag','送失物标记']];
  openModal(`<h3>${esc(p.name)}</h3><p>${STATE_NAME[p.state]}</p><div class="rt-actions">${actions.map(([a,t])=>`<button type="button" class="secondary" data-edit-person-action="${a}">${t}</button>`).join('')}<button type="button" class="ghost" data-close>关闭</button></div>`,host=>host.querySelectorAll('[data-edit-person-action]').forEach(button=>button.onclick=()=>formBox(button.textContent,'<label>操作说明<input name="reason" required maxlength="100"></label>',v=>act('/api/rotation/act',{action:button.dataset.editPersonAction,pid:p.pid,reason:v.reason,flag:'送失物',on:!p.flags.includes('送失物')},'人员已调整'))));
}
async function adjustGrants(){
  try{const data=await rtApi('/api/rotation/adjust/grants');openModal(`<h3>TR临时授权</h3><p>被授权TR拥有与LEAD相同的本圈现场调整权限；下发即开始计时。</p><form class="form-stack" data-grant-form><label>时长<select name="minutes">${[5,10,15,20,25,30,35,40,45].map(n=>`<option value="${n}">${n}分钟</option>`).join('')}</select></label><label class="checkbox-row"><input name="all" type="checkbox">本圈全部有效TR</label><div class="rt-trainer-choices">${data.trainers.map(t=>`<label class="checkbox-row"><input name="employee_ids" value="${t.id}" type="checkbox">${esc(t.name)} · ${esc(t.employee_no)}</label>`).join('')||'暂无有效TR'}</div><button type="submit" class="primary">下发到TR独立通知</button></form><details class="announcement-extra"><summary>当前授权 ${data.items.length}</summary>${data.items.map(g=>`<p>${esc(g.name)} · 至${new Date(g.expires_at).toLocaleTimeString()} <button type="button" class="secondary" data-revoke-grant="${g.id}">撤销</button></p>`).join('')}</details><button type="button" class="secondary" data-close>关闭</button>`,host=>{
    const form=host.querySelector('[data-grant-form]');form.onsubmit=async e=>{e.preventDefault();const button=form.querySelector('[type=submit]');button.disabled=true;try{await rtPost('/api/rotation/adjust/grants',{minutes:Number(form.elements.minutes.value),all:form.elements.all.checked,employee_ids:[...form.querySelectorAll('[name=employee_ids]:checked')].map(x=>Number(x.value))});toast('已下发至TR轮岗协助通知');closeModal();afterRotationAdjustment();}catch(error){toast(error.message,true);button.disabled=false;}};
    host.querySelectorAll('[data-revoke-grant]').forEach(b=>b.onclick=async()=>{try{await rtPost('/api/rotation/adjust/grants/'+b.dataset.revokeGrant+'/revoke',{});toast('授权已撤销');await adjustGrants();}catch(e){toast(e.message,true);}});
  });}catch(e){toast(e.message,true);}
}
document.addEventListener('pointerdown',()=>adjustLastInput=Date.now(),true);
document.addEventListener('keydown',()=>adjustLastInput=Date.now(),true);
document.addEventListener('DOMContentLoaded',()=>{
  if(formalRotation)return;
  void refreshAdjustment();setInterval(refreshAdjustment,5000);setInterval(adjustmentTick,1000);
  const app=document.getElementById('rtApp');
  app.addEventListener('dragstart',e=>{if(!screenPage()||!adjustmentState)return;const post=e.target.closest('[data-drag-post]'),person=e.target.closest('[data-drag-pid]');if(!post&&!person)return;RT.pressing=true;RT.dragging=true;e.dataTransfer.setData('text/plain',post?'post:'+post.dataset.dragPost:'person:'+person.dataset.dragPid);});
  app.addEventListener('dragover',e=>{if(screenPage()&&adjustmentState&&e.target.closest('[data-post]'))e.preventDefault();});
  app.addEventListener('drop',e=>{if(!screenPage()||!adjustmentState)return;const target=e.target.closest('[data-post]');if(!target)return;e.preventDefault();RT.pressing=false;RT.dragging=false;const [line,i]=target.dataset.post.split('#'),raw=e.dataTransfer.getData('text/plain');if(raw.startsWith('post:')){const [source,from]=raw.slice(5).split('#');if(source!==line){toast('岗位交换只能在本线内进行',true);return;}postSwapForm(line,Number(from),Number(i));}else if(raw.startsWith('person:'))personDrop(raw.slice(7),line,Number(i));});
  app.addEventListener('dragend',()=>{RT.pressing=false;RT.dragging=false;});
});
