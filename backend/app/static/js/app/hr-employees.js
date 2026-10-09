// Employee organization, identity editing, account status, workbook import and employee-number change.
import { api, json } from './api.js';
import { beginViewRequest, captureViewContext, registerPageCleanup } from './context.js';
import { confirmModal, formModal, showSaveWarnings } from './dialogs.js';
import { esc, portalPath, today } from './format.js';
import { gotoTab } from './navigation.js';
import { opt } from './records.js';
import { app, state } from './state.js';
import { toast } from './toast.js';

const HR_DUTY_ROLE_CODES=['TA_SUPERVISOR','TA_GSM'];

// 本职可叠加的代理职务：CM/TR → TA主管，主管 → TA GSM。
const HR_DUTY_FOR_BASE={CM:'TA_SUPERVISOR',TR:'TA_SUPERVISOR',SUPERVISOR:'TA_GSM'};

function hrTag(html,tone=''){return `<span class="org-tag ${tone}">${html}</span>`;}

// 组名在本景点圈内去掉圈名显示（热力追踪A组 → A组）；HR改过的名字原样显示。
function hrGroupShortName(group,circleName=''){return circleName&&group.name.startsWith(circleName)&&group.name.length>circleName.length?group.name.slice(circleName.length):group.name;}

// 负责人说明：两位都有就都写，只有一位写一位，都没有写“无负责人”。
function hrGroupLeaderText(group){return [group.formal_leader?`主管 ${group.formal_leader.name}`:'',group.acting_leader?`代理主管 ${group.acting_leader.name}`:''].filter(Boolean).join(' · ')||'无负责人';}

function hrLeaderTags(formal,acting){if(!formal&&!acting)return hrTag('无负责人 · 签卡暂无人复核','warn');return `${formal?hrTag(`<b>主管</b>${esc(formal.name)}`):hrTag('未设置主管','warn')}${acting?hrTag(`<b>代理主管</b>${esc(acting.name)}`,'accent'):''}`;}

function hrIdentityTags(employee){
  if(HR_DUTY_ROLE_CODES.includes(employee.base_role_code))return `${hrTag(esc(employee.base_role_name||employee.role_name))}${hrTag('旧记录：请确认本职','warn')}`;
  return `${hrTag(esc(employee.base_role_name||employee.role_name||'未配置'))}${employee.duty_role_code?hrTag(`代理${esc(employee.duty_role_name)}`,'accent'):''}`;
}

function hrStatusCell(employee){
  if(employee.employment_status==='loa')return hrTag('LOA','warn');
  if(employee.employment_status==='terminated')return hrTag('离职','muted');
  return '在职';
}

function hrEditable(employee){return state.options.roles.some(role=>role.code===employee.role_code);}

function hrPersonHead(groupLabel){return `<div class="hr-person-row hr-person-head" aria-hidden="true"><span>员工</span><span>身份</span><span>${groupLabel}</span><span>状态</span><span></span></div>`;}

function hrPersonRow(employee,groupCell){
  return `<div class="hr-person-row" data-hr-person="${employee.id}" data-status="${esc(employee.employment_status)}" data-search="${esc(`${employee.name} ${employee.employee_no}`.toLowerCase())}"><div class="hr-person-name"><strong>${esc(employee.name)}</strong><small>${esc(employee.employee_no)}</small></div><div class="hr-person-identity">${hrIdentityTags(employee)}</div><div class="hr-person-group">${groupCell}</div><div class="hr-person-status">${hrStatusCell(employee)}</div><div class="hr-person-actions">${hrEditable(employee)?`<button type="button" class="secondary" data-hr-edit="${employee.id}">编辑</button>`:'<span class="not-applicable" title="仅最高管理员可编辑">只读</span>'}</div></div>`;
}

// 组员行唯一可直接修改的项目：所属小组。
function hrMemberGroupCell(employee,circle){
  const current=circle.groups.find(group=>group.id===employee.group_id);
  if(!hrEditable(employee)||!employee.is_active)return esc(current?hrGroupShortName(current,circle.name):'未分组');
  return `<select class="inline-select" data-hr-group-select="${employee.id}" data-original="${employee.group_id||''}" aria-label="${esc(employee.name)}的所属小组"><option value="">未分组</option>${circle.groups.map(group=>`<option value="${group.id}" ${group.id===employee.group_id?'selected':''}>${esc(`${hrGroupShortName(group,circle.name)}（${hrGroupLeaderText(group)}）`)}</option>`).join('')}</select>`;
}

function hrBlock(title,tags,rows,{groupLabel='小组',empty='暂无人员',link=false,collapsed=false,key=''}={}){
  const head=`<span class="hr-block-title">${title}</span>${tags}${link?'<button type="button" class="link-button" data-goto-groups>去小组管理 →</button>':''}`;
  const body=`${hrPersonHead(groupLabel)}${rows||`<div class="empty">${empty}</div>`}`;
  return collapsed?`<details class="hr-block" data-hr-block data-hr-key="${esc(key)}"><summary class="hr-block-head">${head}</summary>${body}</details>`:`<div class="hr-block" data-hr-block><div class="hr-block-head">${head}</div>${body}</div>`;
}

function hrCircleSection(circle){
  const count=value=>hrTag(`${value}人`,'plain');
  // 主管列表和各小组默认折叠（标题上保留负责人与人数），未分组保持展开方便分配。
  const idleSupervisors=circle.supervisors.filter(employee=>!employee.led_group_id).length;
  const supervisors=circle.supervisors.map(employee=>hrPersonRow(employee,employee.led_group_name?esc(employee.led_group_name):hrTag('暂未带组','warn'))).join('');
  const groups=circle.groups.map(group=>hrBlock(`<span title="${esc(group.name)}">${esc(hrGroupShortName(group,circle.name))}</span>`,`${hrLeaderTags(group.formal_leader,group.acting_leader)}${count(group.member_count)}`,group.members.map(employee=>hrPersonRow(employee,hrMemberGroupCell(employee,circle))).join(''),{empty:'暂无组员',link:true,collapsed:true,key:`group-${group.id}`})).join('');
  const unassigned=circle.unassigned.length?hrBlock('未分组',hrTag(`${circle.unassigned.length}人`,'warn'),circle.unassigned.map(employee=>hrPersonRow(employee,hrMemberGroupCell(employee,circle))).join('')):'';
  const managers=circle.managers.length?hrBlock('GSM及以上 / HR',count(circle.managers.length),circle.managers.map(employee=>hrPersonRow(employee,'—')).join(''),{groupLabel:'',collapsed:true}):'';
  const inactive=circle.inactive.length?hrBlock('离职人员',count(circle.inactive.length),circle.inactive.map(employee=>hrPersonRow(employee,'—')).join(''),{groupLabel:'',collapsed:true}):'';
  const managerTags=`${circle.gsm_names.length?hrTag(`<b>景点GSM</b>${esc(circle.gsm_names.join('、'))}`):hrTag('未配置GSM','warn')}${circle.ta_gsm_names.length?hrTag(`<b>代理GSM</b>${esc(circle.ta_gsm_names.join('、'))}`,'accent'):''}`;
  return `<section class="hr-circle" data-hr-circle="${circle.id}"><header class="hr-circle-head"><h3>${esc(circle.name)}</h3>${managerTags}</header>${hrBlock('主管',`${count(circle.supervisors.length)}${idleSupervisors?hrTag(`暂未带组 ${idleSupervisors}人`,'warn'):''}`,supervisors,{groupLabel:'带组',empty:'暂无主管',collapsed:true,key:`supervisors-${circle.id}`})}${groups}${unassigned}${managers}${inactive}</section>`;
}

function hrOrganizationMarkup(organization){
  const circles=organization.circles||[],others=organization.others||[];
  const circleFilter=circles.length>1?`<label>景点圈<select id="hrCircleFilter"><option value="all">全部景点圈</option>${circles.map(circle=>`<option value="${circle.id}">${esc(circle.name)}</option>`).join('')}</select></label>`:'';
  const warnings=(organization.warnings||[]).map(row=>`<div class="notice warn hr-org-warning"><span>${esc(row.message)}。</span><button type="button" class="link-button" data-goto-groups>去小组管理安排 →</button></div>`).join('');
  const otherSection=others.length?`<section class="hr-circle" data-hr-circle="none"><header class="hr-circle-head"><h3>未归属景点圈</h3></header>${hrBlock('其他人员',hrTag(`${others.length}人`,'plain'),others.map(employee=>hrPersonRow(employee,'—')).join(''),{groupLabel:''})}</section>`:'';
  return `<div class="hr-filter hr-org-toolbar">${circleFilter}<label>搜索<input id="hrSearch" placeholder="搜索姓名或员工号"></label><label>人员状态<select id="hrStatusFilter"><option value="all">全部状态</option><option value="active">在职</option><option value="loa">LOA（长期病假）</option><option value="terminated">离职</option></select></label></div>${warnings}<div id="hrOrgTree">${circles.map(hrCircleSection).join('')}${otherSection}</div>`;
}

function hrOrganizationPeople(organization){
  const people=new Map();
  (organization.circles||[]).forEach(circle=>{[...circle.supervisors,...circle.unassigned,...circle.managers,...circle.inactive,...circle.groups.flatMap(group=>group.members)].forEach(employee=>people.set(String(employee.id),employee))});
  (organization.others||[]).forEach(employee=>people.set(String(employee.id),employee));
  return people;
}

// 员工编辑弹窗：身份 / 归属 / 状态与账号。主管带组在小组管理设置。
function hrEditEmployee(employee,organization){const page=captureViewContext();
  const circles=organization.circles||[];
  const baseRoles=state.options.roles.filter(role=>!HR_DUTY_ROLE_CODES.includes(role.code));
  const legacy=HR_DUTY_ROLE_CODES.includes(employee.base_role_code);
  const originalBase=legacy?'':employee.base_role_code;
  const roleName=code=>(state.options.roles.find(role=>role.code===code)||{}).name||code;
  const account=employee.account_deleted_at?`<span class="badge danger" title="${esc(employee.account_deletion_reason||'员工与业务档案已保留')}">账号已删除·留档</span>`:`<select name="enabled"><option value="true" ${employee.account_enabled?'selected':''}>启用</option><option value="false" ${!employee.account_enabled?'selected':''}>停用</option></select>`;
  const archive=employee.account_deletion_eligible?`<button type="button" class="danger" data-delete-login-account title="删除登录凭据与会话，保留员工及所有历史档案">删除登录账号</button>`:'';
  const body=`<fieldset class="hr-edit-section"><legend>身份</legend><label>本职<select name="base_role">${legacy?'<option value="">请选择本职</option>':''}${baseRoles.map(role=>`<option value="${role.code}" ${role.code===originalBase?'selected':''}>${esc(role.name)}</option>`).join('')}</select></label>${legacy?`<p class="field-hint legacy-duty-hint">旧记录：请选择本职（${employee.base_role_code==='TA_GSM'?'主管':'CM/TR'}）后保存，代理职务保留</p>`:''}<label>代理职务<select name="duty"></select></label><label data-duty-end>代理至<input name="role_ends_on" type="date" value="${esc(employee.duty_ends_on||'')}"></label><p class="field-hint" data-duty-hint></p></fieldset><fieldset class="hr-edit-section"><legend>归属</legend><label>景点圈<select name="attraction"><option value="">无</option>${state.options.attractions.map(attraction=>`<option value="${attraction.id}" ${attraction.id===employee.attraction_id?'selected':''}>${esc(attraction.name)}</option>`).join('')}</select></label><label data-group-field>所属小组<select name="group"></select></label><p class="field-hint" data-group-hint></p></fieldset><fieldset class="hr-edit-section"><legend>状态与账号</legend><label>人员状态<select name="employment_status"></select></label><label data-loa-field>LOA开始日期<input name="loa_start_date" type="date" value="${esc(employee.loa_start_date||today())}"></label><label>登录账号${account}</label>${archive}</fieldset><label>修改原因<input name="reason" maxlength="200" placeholder="选填，会写入审计"></label>`;
  return formModal(`编辑员工 · ${employee.name}`,`${esc(employee.employee_no)} · ${esc(employee.attraction_name||'未分配景点圈')}`,body,'保存',{
    onReady:(form,close)=>{
      const base=form.elements.base_role,duty=form.elements.duty,attraction=form.elements.attraction,group=form.elements.group,status=form.elements.employment_status;
      const dutyEnd=form.querySelector('[data-duty-end]'),dutyHint=form.querySelector('[data-duty-hint]'),groupField=form.querySelector('[data-group-field]'),groupHint=form.querySelector('[data-group-hint]'),loaField=form.querySelector('[data-loa-field]');
      const originalDuty=legacy?employee.base_role_code:(employee.duty_role_code||'');
      let dutyChoice=originalDuty,groupChoice=String(employee.group_id||'');
      const sync=()=>{
        const baseCode=base.value,frontline=['CM','TR'].includes(baseCode)||(legacy&&employee.base_role_code==='TA_SUPERVISOR'&&!baseCode);
        const baseChanged=!legacy&&baseCode!==originalBase,available=HR_DUTY_FOR_BASE[baseCode];
        duty.innerHTML=legacy?`<option value="${employee.base_role_code}">代理${esc(roleName(employee.base_role_code))}（保留）</option>`:`<option value="">无</option>${available&&!baseChanged?`<option value="${available}">代理${esc(roleName(available))}</option>`:''}`;
        duty.value=[...duty.options].some(option=>option.value===dutyChoice)?dutyChoice:'';
        duty.disabled=legacy||baseChanged||!available;
        dutyEnd.hidden=!duty.value;
        dutyHint.textContent=baseChanged&&HR_DUTY_FOR_BASE[baseCode]?'更换本职后，请先保存，再设置代理职务。':duty.value?'不填结束日期表示长期代理。结束代理后，他代理的小组自动交回主管。':'';
        groupField.hidden=!frontline;
        const circle=circles.find(row=>String(row.id)===String(attraction.value));
        group.innerHTML=`<option value="">未分组</option>${(circle?circle.groups:[]).map(row=>`<option value="${row.id}">${esc(`${row.name}（${hrGroupLeaderText(row)}）`)}</option>`).join('')}`;
        group.value=[...group.options].some(option=>option.value===groupChoice)?groupChoice:'';
        groupHint.textContent=frontline?'只列本景点圈的小组。':'主管不选小组，带组在小组管理设置。';
        const loaAllowed=frontline,current=status.value||employee.employment_status;
        status.innerHTML=`<option value="active">在职</option>${loaAllowed?'<option value="loa">LOA（长期病假）</option>':''}<option value="terminated">离职</option>`;
        status.value=[...status.options].some(option=>option.value===current)?current:'active';
        loaField.hidden=status.value!=='loa';
      };
      base.onchange=()=>{dutyChoice=base.value===originalBase?originalDuty:'';sync()};
      duty.onchange=()=>{dutyChoice=duty.value;sync()};
      group.onchange=()=>{groupChoice=group.value};
      attraction.onchange=()=>{groupChoice=String(attraction.value)===String(employee.attraction_id)?String(employee.group_id||''):'';sync()};
      status.onchange=sync;sync();
      form.querySelector('[data-delete-login-account]')?.addEventListener('click',async()=>{if(!await confirmModal('删除登录账号',`<p>确认删除 <strong>${esc(employee.name)}（${esc(employee.employee_no)}）</strong> 的登录账号吗？</p><p>登录凭据和当前会话将被移除，不能再登录；员工、当月数据、历史记录、附件、调动和审计档案均会保留并标记“账号已删除·留档”。</p>`,'确认删除登录账号'))return;try{await api('/api/hr/employees/'+employee.id+'/account',json('DELETE',{reason:'离职/停用满7天后删除登录账号，保留员工与业务档案'}));toast('登录账号已删除，业务档案已保留');close();page.refresh(renderHrEmployees)}catch(error){toast(error.message,true)}});
    },
    onSubmit:async form=>{
      const base=form.elements.base_role.value,duty=form.elements.duty.value;
      if(!base)throw new Error('请选择本职');
      const roleCode=legacy?base:(duty||base);
      const frontline=!form.querySelector('[data-group-field]').hidden;
      const enabled=form.elements.enabled;
      const body={role_code:roleCode,attraction_id:form.elements.attraction.value||null,employment_status:form.elements.employment_status.value,loa_start_date:form.elements.loa_start_date.value||'',account_enabled:enabled?enabled.value==='true':false,group_id:frontline?(form.elements.group.value||null):null,reason:form.elements.reason.value.trim()||'HR员工管理编辑'};
      if(HR_DUTY_ROLE_CODES.includes(roleCode))body.role_ends_on=form.elements.role_ends_on.value||'';
      const result=await api('/api/hr/employees/'+employee.id,json('PUT',body));
      toast('已保存');if(page.isCurrent())showSaveWarnings(result);page.refresh(renderHrEmployees);
    },
  });
}

function bindHrOrganization(organization){const page=captureViewContext();
  const people=hrOrganizationPeople(organization),search=document.getElementById('hrSearch'),statusFilter=document.getElementById('hrStatusFilter'),circleFilter=document.getElementById('hrCircleFilter');
  const refresh=()=>{
    const query=search.value.trim().toLowerCase(),status=statusFilter.value,circle=circleFilter?circleFilter.value:'all';
    document.querySelectorAll('[data-hr-circle]').forEach(section=>{section.hidden=circle!=='all'&&section.dataset.hrCircle!==circle;});
    document.querySelectorAll('[data-hr-person]').forEach(row=>{row.hidden=(query&&!row.dataset.search.includes(query))||(status!=='all'&&row.dataset.status!==status);});
    const filtering=Boolean(query)||status!=='all';
    document.querySelectorAll('[data-hr-block]').forEach(block=>{const rows=[...block.querySelectorAll('[data-hr-person]')];block.hidden=filtering&&!rows.some(row=>!row.hidden);if(filtering&&block.tagName==='DETAILS'&&!block.hidden)block.open=true;});
  };
  // 保存后页面会重新渲染，记住已展开的主管栏和小组。
  const opened=state.hrOpenBlocks||(state.hrOpenBlocks=new Set());
  document.querySelectorAll('details[data-hr-key]').forEach(block=>{const key=block.dataset.hrKey;if(key&&opened.has(key))block.open=true;block.addEventListener('toggle',()=>{if(!key)return;if(block.open)opened.add(key);else opened.delete(key)})});
  search.oninput=refresh;statusFilter.onchange=refresh;if(circleFilter)circleFilter.onchange=refresh;
  document.querySelectorAll('[data-goto-groups]').forEach(button=>button.onclick=event=>{event.preventDefault();gotoTab('hrGroups')});
  document.querySelectorAll('[data-hr-edit]').forEach(button=>button.onclick=()=>{const employee=people.get(button.dataset.hrEdit);if(employee)hrEditEmployee(employee,organization)});
  document.querySelectorAll('[data-hr-group-select]').forEach(select=>select.onchange=async()=>{
    const employee=people.get(select.dataset.hrGroupSelect),target=select.selectedOptions[0]?.textContent||'未分组';
    if(!(await confirmModal('调整所属小组',`<p>确认将 ${esc(employee.name)} 调整到“${esc(target)}”？</p><p>待复核的签卡将转给新小组的负责人。</p>`,'确认调整'))){select.value=select.dataset.original;return}
    try{const result=await api('/api/hr/employees/'+employee.id,json('PUT',{group_id:select.value||null,reason:'HR调整小组'}));toast('已调整小组');if(page.isCurrent())showSaveWarnings(result);page.refresh(renderHrEmployees)}catch(error){select.value=select.dataset.original;toast(error.message,true)}
  });
  refresh();
}

function employeeNumberChangePanel(){
  if(!['HR_CIRCLE','SYSTEM_ADMIN'].includes(state.me.role_code))return '';
  const scope=state.me.role_code==='HR_CIRCLE'?'当前权限：仅可变更所属景点圈内 CM、TR、TA主管、主管的员工号。':'当前权限：可变更常规员工账号（CM/TR/TA主管/主管/TA GSM/GSM/AM/OM）的员工号。HR和最高管理员账号不在此入口处理。';
  return `<details class="panel account-status-panel hr-tool-panel"><summary><span><strong>员工号变更</strong><small>实习转正等场景更换员工号，保留同一员工档案</small></span><span class="badge">展开</span></summary><div class="account-status-content"><p>用于实习转正等员工号变更场景。系统保留同一员工档案、角色、景点圈、小组、加扣分、缺勤和审计历史，不会新建第二个账号。</p><div class="notice"><strong>可操作范围</strong><br>${esc(scope)}<br>变更后原账号立即失效，当前登录会话将退出；历史员工号可继续用于此处搜索和导出复查。</div><form id="employeeNumberChangeForm" class="form-stack"><label>搜索员工<input name="keyword" autocomplete="off" placeholder="输入当前/历史员工号或姓名"></label><div id="employeeNumberChangeResults" class="employee-search-results" hidden></div><input type="hidden" name="employee_id"><div id="employeeNumberChangeSelected" class="employee-selected" hidden></div><label>新员工号<input name="new_employee_no" inputmode="numeric" pattern="[0-9]{7}" minlength="7" maxlength="7" required disabled placeholder="请输入7位新员工号"><span class="field-hint">仅限7位数字；系统会校验员工与登录账号均未被占用。</span></label><label>变更原因<input name="reason" maxlength="300" required disabled placeholder="例如：实习转正，更换正式员工号"></label><label class="check-line"><input name="reset_password" type="checkbox" disabled> 同时重置密码为新员工号后四位（首次登录必须修改）</label><button type="submit" class="warn" disabled>确认变更员工号</button></form></div></details>`;
}

function bindEmployeeNumberChange(){const page=captureViewContext();
  const form=document.getElementById('employeeNumberChangeForm');if(!form)return;
  const keyword=form.elements.keyword,results=document.getElementById('employeeNumberChangeResults'),selected=document.getElementById('employeeNumberChangeSelected'),target=form.elements.employee_id,newNumber=form.elements.new_employee_no,reason=form.elements.reason,reset=form.elements.reset_password,submit=form.querySelector('button[type=submit]');let timer=null,controller=null,sequence=0;
  const clearSelected=()=>{target.value='';selected.hidden=true;selected.innerHTML='';newNumber.value='';newNumber.disabled=true;reason.value='';reason.disabled=true;reset.checked=false;reset.disabled=true;submit.disabled=true;};
  const choose=row=>{target.value=String(row.id);keyword.value=`${row.name} · ${row.employee_no}`;results.hidden=true;results.innerHTML='';newNumber.disabled=false;reason.disabled=false;reset.disabled=false;submit.disabled=false;const historic=row.matched_historical_no?`<small>命中历史员工号：${esc(row.matched_historical_no)}</small>`:'';selected.innerHTML=`<div><strong>${esc(row.name)}</strong><span class="badge">${esc(row.role_name)}</span><small>当前员工号：${esc(row.employee_no)} · ${esc(row.attraction_name)}</small>${historic}</div><button type="button" class="secondary">重新选择</button>`;selected.hidden=false;selected.querySelector('button').onclick=()=>{clearSelected();keyword.value='';keyword.focus();};newNumber.focus();};
  const search=async()=>{const value=keyword.value.trim(),requestId=++sequence;clearSelected();if(!value){results.hidden=true;results.innerHTML='';return;}controller?.abort();controller=new AbortController();results.hidden=false;results.innerHTML='<div class="employee-search-empty">正在搜索…</div>';try{const data=await api('/api/hr/employee-number-targets?'+new URLSearchParams({keyword:value,limit:'30'}),{signal:controller.signal});if(requestId!==sequence||!page.isCurrent())return;const rows=data.items||[];if(!rows.length){results.innerHTML='<div class="employee-search-empty">没有找到可变更员工号的账号</div>';return;}results.innerHTML=rows.map((row,index)=>`<button type="button" class="employee-search-result" data-number-result="${index}"><span><strong>${esc(row.name)}</strong><em>${esc(row.role_name)}</em></span><small>当前：${esc(row.employee_no)} · ${esc(row.attraction_name)}${row.matched_historical_no?` · 历史：${esc(row.matched_historical_no)}`:''}</small></button>`).join('');results.querySelectorAll('[data-number-result]').forEach(button=>button.onclick=()=>choose(rows[Number(button.dataset.numberResult)]));}catch(error){if(error.name!=='AbortError'&&requestId===sequence&&page.isCurrent()){results.innerHTML='<div class="employee-search-empty">搜索失败，请稍后重试</div>';toast(error.message,true);}}};
  registerPageCleanup(()=>{clearTimeout(timer);controller?.abort();});
  keyword.oninput=()=>{clearTimeout(timer);timer=setTimeout(search,120);};
  form.onsubmit=async event=>{event.preventDefault();if(!target.value)return;const body={employee_id:Number(target.value),new_employee_no:newNumber.value.trim(),reason:reason.value.trim(),reset_password:reset.checked};if(!/^\d{7}$/.test(body.new_employee_no)){toast('新员工号必须为7位纯数字',true);newNumber.focus();return;}if(!body.reason){toast('请填写员工号变更原因',true);reason.focus();return;}const confirmed=await confirmModal('确认变更员工号',`<p>确认将所选员工的登录员工号变更为 <strong>${esc(body.new_employee_no)}</strong> 吗？</p><p>原员工号将不能登录；当前会话会立即退出。员工档案、景点圈、小组和所有历史记录均保持同一人。</p>${body.reset_password?'<p>密码将同时重置为新员工号后四位，首次登录必须修改。</p>':'<p>密码保持不变。</p>'}`,'确认变更');if(!confirmed)return;try{const out=await api('/api/hr/employees/'+body.employee_id+'/employee-number',json('POST',body));toast(out.unchanged?'员工号未变化':'员工号已变更，原登录会话已失效');page.refresh(renderHrEmployees)}catch(error){toast(error.message,true)}};
}

function accountStatusBadge(label,code){
  const tone=['enabled','changed'].includes(code)?'ok':['locked','disabled','employee_inactive'].includes(code)?'danger':['pending_change','unknown','unprovisioned'].includes(code)?'warn':'';
  return `<span class="badge ${tone}">${esc(label)}</span>`;
}

function accountStatusRows(rows){
  if(!rows.length)return '<tr><td colspan="7" class="empty">没有符合筛选条件的账号</td></tr>';
  return rows.map(row=>`<tr><td>${esc(row.login_account||'未开通')}<br><small>${esc(row.employee_no)}</small></td><td><strong>${esc(row.name)}</strong></td><td>${esc(row.role_name)}</td><td>${esc(row.attraction_name)}</td><td>${accountStatusBadge(row.account_status,row.account_status_code)}</td><td>${accountStatusBadge(row.password_status,row.password_status_code)}${row.password_changed_at?`<br><small>修改于：${esc(row.password_changed_at)}</small>`:''}</td><td>${accountStatusBadge(row.login_status,row.last_login_at?'ok':'warn')}<br><small>${esc(row.last_login_at||'暂无登录记录')}</small></td></tr>`).join('');
}

function accountStatusPanel(){
  return `<details id="accountStatusPanel" class="panel account-status-panel"><summary><span><strong>账号状态与登录情况</strong><small>按需展开查看，不影响员工管理操作。</small></span><span class="badge">展开查看</span></summary><div id="accountStatusContent" class="account-status-content"><p class="field-hint">可按景点圈、账号状态或关键词筛选；仅展示状态和时间，不会显示真实密码。</p></div></details>`;
}

function accountStatusContent(data){
  const scope=data.scope_label||'权限范围内账号',rows=data.items||[];
  const attractions=[...new Set(rows.map(row=>row.attraction_name).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh-CN'));
  return `<div class="record-line"><div><h2>账号状态与登录情况</h2><p>当前可查看：${esc(scope)}。仅展示状态和时间，不会显示真实密码。</p></div><span class="badge">${rows.length} 个账号</span></div><div class="hr-filter account-status-filters"><label>景点圈<select id="accountStatusAttraction"><option value="all">全部景点圈</option>${attractions.map(name=>`<option value="${esc(name)}">${esc(name)}</option>`).join('')}</select></label><label>搜索账号<input id="accountStatusSearch" autocomplete="off" placeholder="姓名、员工号或账号"></label><label>状态筛选<select id="accountStatusFilter"><option value="all">全部状态</option><option value="never_logged_in">从未登录</option><option value="pending_change">待修改初始/重置密码</option><option value="changed">已修改密码</option><option value="unknown">历史密码状态未记录</option><option value="locked">临时锁定</option><option value="disabled">账号已停用</option></select></label></div><p id="accountStatusSummary" class="field-hint"></p><div class="table-wrap sticky-col"><table class="account-status-table"><thead><tr><th>登录账号 / 员工号</th><th>员工</th><th>角色</th><th>景点圈</th><th>账号状态</th><th>密码状态</th><th>最后登录</th></tr></thead><tbody id="accountStatusRows">${accountStatusRows(rows)}</tbody></table></div>`;
}

function bindAccountStatus(data){
  const input=document.getElementById('accountStatusSearch'),filter=document.getElementById('accountStatusFilter'),attraction=document.getElementById('accountStatusAttraction'),target=document.getElementById('accountStatusRows'),summary=document.getElementById('accountStatusSummary'),items=data.items||[];
  if(!input||!filter||!attraction||!target||!summary)return;
  const refresh=()=>{const keyword=input.value.trim().toLowerCase(),mode=filter.value,circle=attraction.value;const rows=items.filter(row=>{const text=[row.login_account,row.employee_no,row.name,row.role_name].join(' ').toLowerCase();if(circle!=='all'&&row.attraction_name!==circle)return false;if(keyword&&!text.includes(keyword))return false;if(mode==='never_logged_in')return !row.last_login_at;if(mode==='pending_change')return row.password_status_code==='pending_change';if(mode==='changed')return row.password_status_code==='changed';if(mode==='unknown')return row.password_status_code==='unknown';if(mode==='locked')return row.account_status_code==='locked';if(mode==='disabled')return row.account_status_code==='disabled';return true;});target.innerHTML=accountStatusRows(rows);summary.textContent=`当前显示 ${rows.length} / ${items.length} 个账号（${circle==='all'?'全部景点圈':circle}）。`};
  input.oninput=refresh;filter.onchange=refresh;attraction.onchange=refresh;refresh();
}

function bindAccountStatusPanel(){const page=captureViewContext();
  const panel=document.getElementById('accountStatusPanel'),content=document.getElementById('accountStatusContent');
  if(!panel||!content)return;
  let loaded=false,loading=false;
  const load=async()=>{if(loaded||loading||!page.isCurrent())return;loading=true;content.innerHTML='<p class="field-hint">正在加载账号状态…</p>';try{const data=await api('/api/hr/account-status');if(!page.isCurrent())return;content.innerHTML=accountStatusContent(data);loaded=true;bindAccountStatus(data);}catch(error){if(!page.isCurrent()||error?.name==='AbortError')return;content.innerHTML='<p class="error-text">账号状态加载失败，请稍后重试。</p><button type="button" class="secondary" id="retryAccountStatus">重新加载</button>';document.getElementById('retryAccountStatus').onclick=()=>{loading=false;load();};}finally{loading=false;}};
  panel.addEventListener('toggle',()=>{if(panel.open)load();});
}

async function renderHrEmployees(){
  const request=beginViewRequest();
  const organization=await api('/api/hr/organization');
  if(!request.isCurrent())return;
  app.innerHTML=`<div class="section-gap"><details class="panel account-status-panel hr-tool-panel"><summary><span><strong>Excel 导入</strong><small>下载模板，填写后整表导入员工</small></span><span class="badge">展开</span></summary><div class="account-status-content"><p>请先下载 V2 模板，填写后整表导入。员工号必须为7位纯数字，员工仅可归属热力追踪、矮人迷宫或小熊罐子景点圈。初始密码留空时，系统使用员工号后四位并要求首次登录修改。</p><form id="importEmployees" enctype="multipart/form-data"><div class="actions native-file-actions"><label class="secondary native-file-trigger" for="importEmployeesWorkbook">选择文件</label><a class="secondary download-link" href="${portalPath('/api/hr/import-template')}">下载导入模板</a><button type="submit" class="primary">导入员工</button></div><input id="importEmployeesWorkbook" class="native-file-input" name="workbook" type="file" accept=".xlsx" required><span class="field-hint" data-import-filename>请选择 Excel 文件（.xlsx）</span></form></div></details><details class="panel account-status-panel hr-tool-panel hr-create-panel"><summary><span class="hr-create-heading"><strong>新建员工账号</strong><small>填写员工基础信息，创建后可在下方员工管理中分配小组</small></span><span class="badge">展开</span></summary><div class="account-status-content"><form id="newEmployee" class="hr-filter"><label class="hr-create-employee-no">员工号<input name="employee_no" inputmode="numeric" pattern="[0-9]{7}" minlength="7" maxlength="7" required placeholder="请输入7位员工号"><span class="field-hint">仅限7位数字，不能重复创建。</span></label><label class="hr-create-name">姓名<input name="name" required placeholder="请输入员工姓名"></label><label class="hr-create-role">角色<select name="role_code">${opt(state.options.roles,'code',x=>x.name)}</select></label><label class="hr-create-attraction">景点圈<select name="attraction_id"><option value="">无</option>${opt(state.options.employee_circles||state.options.attractions)}</select></label><label class="hr-create-password">初始密码<input name="password" placeholder="留空使用员工号后4位"><span class="field-hint">首次登录后必须立即修改密码。</span></label><div class="actions hr-create-actions"><button type="submit" class="primary">创建账号</button></div></form></div></details>${employeeNumberChangePanel()}<section class="panel"><h2>员工管理</h2><p>改人在这里：本职与代理职务、景点圈、所属小组、人员状态和账号。组员行可直接选择所属小组，其他修改请点“编辑”。小组的主管和代理主管请到<button type="button" class="link-button" data-goto-groups>小组管理</button>设置。</p>${hrOrganizationMarkup(organization)}</section>${accountStatusPanel()}</div>`;
  const importForm=document.getElementById('importEmployees'),workbookInput=importForm.querySelector('[name=workbook]'),fileHint=importForm.querySelector('[data-import-filename]');
  workbookInput.onchange=()=>{fileHint.textContent=workbookInput.files[0]?workbookInput.files[0].name:'请选择 Excel 文件（.xlsx）';};
  importForm.onsubmit=async event=>{event.preventDefault();try{const out=await api('/api/hr/import-employees',{method:'POST',body:new FormData(event.target)});toast(`成功导入 ${out.created} 名员工`);request.refresh(renderHrEmployees)}catch(error){toast(error.message,true)}};
  document.getElementById('newEmployee').onsubmit=async event=>{event.preventDefault();try{await api('/api/hr/employees',json('POST',Object.fromEntries(new FormData(event.target))));toast('员工已创建');request.refresh(renderHrEmployees)}catch(error){toast(error.message,true)}};
  bindHrOrganization(organization);
  bindAccountStatusPanel();
  bindEmployeeNumberChange();
}

export { hrGroupShortName, hrTag, renderHrEmployees };
