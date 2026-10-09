// Password forms, account reset and circle-HR accounts.
import { api, json } from './api.js';
import { beginViewRequest, captureViewContext, registerPageCleanup } from './context.js';
import { confirmModal } from './dialogs.js';
import { bindEmployeeSearches, employeePicker, requireEmployeeSelection } from './employee-picker.js';
import { esc } from './format.js';
import { app, has, state, tabs } from './state.js';
import { toast } from './toast.js';

function passwordRuleState(password){return {minimum:4,length:password.length>=4&&password.length<=64};}

function passwordRuleMarkup(){const rules=[['length','至少4位']];return `<div class="password-rule-box" data-password-rules aria-live="polite"><strong>密码规则</strong><ul>${rules.map(([key,label])=>`<li data-password-rule="${key}"><span aria-hidden="true">○</span>${label}</li>`).join('')}</ul></div>`;}

function passwordFieldsMarkup(){return `<label>当前密码<input name="current_password" type="password" autocomplete="current-password" required></label><label>新密码<input name="new_password" type="password" minlength="4" maxlength="64" autocomplete="new-password" placeholder="请输入符合下方规则的新密码" required></label>${passwordRuleMarkup()}<label>确认新密码<input name="confirm_password" type="password" minlength="4" maxlength="64" autocomplete="new-password" required></label>`;}

function bindPasswordForm(form,onSuccess){const page=captureViewContext();const current=form.querySelector('[name=current_password]'),next=form.querySelector('[name=new_password]'),confirmPassword=form.querySelector('[name=confirm_password]'),button=form.querySelector('button[type=submit]'),rules=form.querySelector('[data-password-rules]');const update=()=>{const result=passwordRuleState(next.value);const required=['length'];rules.querySelectorAll('[data-password-rule]').forEach(item=>{const ok=Boolean(result[item.dataset.passwordRule]);item.classList.toggle('is-met',ok);item.querySelector('span').textContent=ok?'✓':'○';});const valid=required.every(key=>result[key])&&current.value.length>0&&confirmPassword.value.length>0&&next.value===confirmPassword.value;button.disabled=!valid;confirmPassword.setCustomValidity(confirmPassword.value&&next.value!==confirmPassword.value?'两次输入的新密码不一致':'');};[current,next,confirmPassword].forEach(input=>input.addEventListener('input',update));update();form.onsubmit=async event=>{event.preventDefault();if(button.disabled)return;try{await api('/api/password',json('POST',Object.fromEntries(new FormData(form))));page.refresh(onSuccess,form);}catch(error){toast(error.message,true)}};}

function renderPasswordChangeRequired(){
  tabs.innerHTML='';
  document.body.classList.remove('has-nav');
  const heading=document.getElementById('pageHeading');
  if(heading){heading.hidden=true;heading.innerHTML='';}
  app.innerHTML=`<section class="panel"><h2>首次登录请修改密码</h2><p>为保护账号安全，请先完成密码修改后再进入系统。</p><form id="requiredPasswordForm" class="form-stack password-form">${passwordFieldsMarkup()}<button type="submit" class="primary">保存并进入系统</button></form></section>`;
  bindPasswordForm(document.getElementById('requiredPasswordForm'),()=>location.reload());
}

function renderPasswordPage(){
  const request=beginViewRequest();
  if(has('PASSWORD_RESET'))return renderAccountReset();
  app.innerHTML=`<section class="panel"><h2>修改密码</h2><p>修改后请使用新密码重新登录其他设备。</p><form id="passwordPageForm" class="form-stack password-form">${passwordFieldsMarkup()}<button type="submit" class="primary">保存新密码</button></form></section>`;
  bindPasswordForm(document.getElementById('passwordPageForm'),form=>{toast('密码已更新');form.reset();form.querySelector('[name=current_password]')?.focus();form.querySelector('[name=new_password]').dispatchEvent(new Event('input'));});
}

function passwordResetScopeHint(){
  const role=state.me.role_code;
  if(role==='GSM')return '当前权限：可重置本人管理景点圈内的常规账号（CM、TR、TA主管、主管、TA GSM、GSM、AM、OM）；不能重置任何 HR 或系统管理员账号。';
  if(role==='HR_CIRCLE')return '当前权限：仅可重置所属景点圈内的 CM、TR、TA主管、主管账号；不能重置 GSM、TA GSM、AM、OM、HR 或系统管理员账号。';
  if(role==='SYSTEM_ADMIN')return '当前权限：可重置所有常规账号（CM、TR、TA主管、主管、TA GSM、GSM、AM、OM）；景点圈 HR 请在“景点圈HR账号”中单独重置；不能在此处重置系统管理员账号。';
  if(['AM','OM'].includes(role))return '当前权限：可重置所有常规账号（CM、TR、TA主管、主管、TA GSM、GSM、AM、OM）；不能重置任何 HR 或系统管理员账号。';
  return '当前权限：可重置权限范围内的常规员工账号；HR 和系统管理员账号不在此入口处理。';
}

async function renderAccountReset(){
  const request=beginViewRequest();
  const canCorrectName=['HR_CIRCLE','SYSTEM_ADMIN'].includes(state.me.role_code);
  const nameScope=state.me.role_code==='HR_CIRCLE'?'当前权限：仅可修改所属景点圈内 CM、TR、TA主管、主管账号的中文姓名。':'当前权限：可修改普通账号的中文姓名；HR和系统管理员账号不在此入口处理。';
  app.innerHTML=`<div class="section-gap"><section class="panel"><h2>密码管理</h2><p>修改自己的密码，或按当前权限重置其他账号密码。</p><form id="passwordPageForm" class="form-stack password-form">${passwordFieldsMarkup()}<button type="submit" class="primary">保存我的新密码</button></form></section><section class="panel"><h2>密码重置</h2><p>按姓名或工号搜索并选择员工；确认后密码将重置为登录账号后四位，员工首次登录后必须修改。</p><div class="notice"><strong>可重置范围</strong><br>${esc(passwordResetScopeHint())}</div><div id="resetPasswordResult"></div><form id="resetEmployeePassword" class="form-stack">${employeePicker('重置对象（必选）','resetEmployee','password_reset','输入姓名或工号搜索可重置密码的员工','/api/accounts/reset-targets')}<div class="actions form-sticky-actions"><button type="submit" class="warn">重置密码</button></div></form></section>${canCorrectName?`<section class="panel"><h2>账号姓名修改</h2><p>用于更正账号名下的中文姓名。员工号、登录账号、角色、景点圈和历史记录均不会改变。</p><div class="notice"><strong>可修改范围</strong><br>${esc(nameScope)}</div><form id="accountNameForm" class="form-stack"><label>搜索账号<input name="keyword" autocomplete="off" placeholder="输入员工号或当前中文姓名"></label><div id="accountNameResults" class="employee-search-results" hidden></div><input type="hidden" name="employee_id"><div id="accountNameSelected" class="employee-selected" hidden></div><label>新中文姓名<input name="name" maxlength="100" required placeholder="请输入正确的中文姓名" disabled></label><button type="submit" class="primary" disabled>确认修改姓名</button></form></section>`:''}</div>`;
  bindPasswordForm(document.getElementById('passwordPageForm'),form=>{toast('密码已更新');form.reset();form.querySelector('[name=current_password]')?.focus();form.querySelector('[name=new_password]').dispatchEvent(new Event('input'));});
  const resetForm=document.getElementById('resetEmployeePassword');bindEmployeeSearches(resetForm);resetForm.onsubmit=async event=>{event.preventDefault();if(!requireEmployeeSelection(resetForm))return;const body=Object.fromEntries(new FormData(resetForm)),selected=resetForm.querySelector('[data-employee-search]').value;if(!(await confirmModal('确认重置密码',`<p>确认重置 ${esc(selected)} 的密码吗？原登录会话将立即失效，密码将重置为登录账号后四位。</p>`,'确认重置')))return;try{const out=await api('/api/accounts/reset-password',json('POST',body));toast('密码已重置为登录账号后四位');if(!request.isCurrent())return;document.getElementById('resetPasswordResult').innerHTML=`<div class="notice"><strong>${esc(out.employee_name)}</strong> 的重置密码：<code>${esc(out.temporary_password)}</code><br>密码为登录账号后四位；首次登录后必须修改。${out.account_enabled?'':' 该账号当前处于停用状态。'}</div>`;resetForm.reset();resetForm.querySelector('.employee-selected').hidden=true;resetForm.querySelector('[data-employee-search]').value='';}catch(error){toast(error.message,true)}};
  if(!canCorrectName)return;
  const form=document.getElementById('accountNameForm'),keyword=form.elements.keyword,results=document.getElementById('accountNameResults'),selected=document.getElementById('accountNameSelected'),target=form.elements.employee_id,newName=form.elements.name,submit=form.querySelector('button[type=submit]');let timer=null,controller=null,sequence=0;
  const clearSelected=()=>{target.value='';selected.hidden=true;selected.innerHTML='';newName.value='';newName.disabled=true;submit.disabled=true;};
  const choose=row=>{target.value=String(row.id);keyword.value=`${row.name} · ${row.employee_no}`;results.hidden=true;results.innerHTML='';newName.disabled=false;newName.value=row.name;submit.disabled=false;selected.innerHTML=`<div><strong>${esc(row.name)}</strong><span class="badge">${esc(row.role_name)}</span><small>${esc(row.employee_no)} · ${esc(row.attraction_name)}</small></div><button type="button" class="secondary">重新选择</button>`;selected.hidden=false;selected.querySelector('button').onclick=()=>{clearSelected();keyword.value='';keyword.focus();};newName.focus();newName.select();};
  const search=async()=>{const value=keyword.value.trim(),requestId=++sequence;clearSelected();if(!value){results.hidden=true;results.innerHTML='';return;}controller?.abort();controller=new AbortController();results.hidden=false;results.innerHTML='<div class="employee-search-empty">正在搜索…</div>';try{const data=await api('/api/accounts/name-targets?'+new URLSearchParams({keyword:value,limit:'30'}),{signal:controller.signal});if(requestId!==sequence||!request.isCurrent())return;const rows=data.items||[];if(!rows.length){results.innerHTML='<div class="employee-search-empty">没有找到可修改姓名的账号</div>';return;}results.innerHTML=rows.map((row,index)=>`<button type="button" class="employee-search-result" data-name-result="${index}"><span><strong>${esc(row.name)}</strong><em>${esc(row.role_name)}</em></span><small>${esc(row.employee_no)} · ${esc(row.attraction_name)}</small></button>`).join('');results.querySelectorAll('[data-name-result]').forEach(button=>button.onclick=()=>choose(rows[Number(button.dataset.nameResult)]));}catch(error){if(error.name!=='AbortError'&&requestId===sequence&&request.isCurrent()){results.innerHTML='<div class="employee-search-empty">搜索失败，请稍后重试</div>';toast(error.message,true);}}};
  registerPageCleanup(()=>{clearTimeout(timer);controller?.abort();});
  keyword.oninput=()=>{clearTimeout(timer);timer=setTimeout(search,120);};
  form.onsubmit=async event=>{event.preventDefault();if(!target.value||newName.disabled)return;const name=newName.value.trim();if(!name){toast('请输入正确的中文姓名',true);newName.focus();return;}if(!await confirmModal('确认修改账号姓名',`<p>确认将所选账号的中文姓名修改为“${esc(name)}”吗？</p><p>员工号、登录账号和历史记录不会改变。</p>`,'确认修改'))return;try{const out=await api('/api/accounts/update-name',json('POST',{employee_id:Number(target.value),name}));toast(out.unchanged?'姓名未变化':'账号中文姓名已修改');keyword.value='';clearSelected();}catch(error){toast(error.message,true)}};
}

async function renderCircleHrAccounts(resetResult=null){
  const request=beginViewRequest();
  const data=await api('/api/admin/circle-hr-accounts');
  if(!request.isCurrent())return;
  app.innerHTML=`<div class="section-gap"><section class="panel"><h2>景点圈HR账号</h2><p>最高管理员可查看账号状态并重置密码。系统不会显示已设置的原密码；重置后密码为登录账号后四位，首次登录必须修改。</p><div id="circleHrResetResult">${resetResult?`<div class="notice"><strong>${esc(resetResult.employee_name)}</strong> 重置密码：<code>${esc(resetResult.temporary_password)}</code><br>${esc(resetResult.message)}</div>`:''}</div><div class="table-wrap sticky-col"><table><thead><tr><th>账号</th><th>景点圈</th><th>账号状态</th><th>密码状态</th><th>最近登录</th><th>操作</th></tr></thead><tbody>${data.items.map(row=>`<tr><td>${esc(row.login_account)}<small>${esc(row.name)}</small></td><td>${esc(row.attraction_name)}</td><td>${row.account_enabled?'启用':'停用'}</td><td>${esc(row.password_status)}</td><td>${esc(row.last_login_at||'暂无')}</td><td>${row.employee_id?`<button type="button" class="warn" data-circle-hr-reset="${row.employee_id}" data-circle-hr-name="${esc(row.name)}">重置密码</button>`:'账号未生成'}</td></tr>`).join('')}</tbody></table></div></section></div>`;
  app.querySelectorAll('[data-circle-hr-reset]').forEach(button=>button.onclick=async()=>{const confirmed=await confirmModal('重置景点圈HR密码',`<p>确认重置 ${esc(button.dataset.circleHrName)} 的密码吗？</p><p>原登录会话将立即失效，密码将重置为登录账号后四位。</p>`,'确认重置');if(!confirmed)return;try{const result=await api('/api/admin/circle-hr-accounts/'+button.dataset.circleHrReset+'/reset-password',{method:'POST'});toast('景点圈HR密码已重置');await request.refresh(renderCircleHrAccounts,result)}catch(error){toast(error.message,true)}});
}

export { renderAccountReset, renderCircleHrAccounts, renderPasswordChangeRequired, renderPasswordPage };
