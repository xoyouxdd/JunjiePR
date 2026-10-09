// API transport, validation messages and portal URL handling; GET requests inherit the active page signal.
import { getViewSignal } from './context.js';
import { portalPath } from './format.js';

function validationErrorMessage(detail){if(!detail||typeof detail!=='object')return '';if(detail.code==='SICK_LEAVE_VALIDATION_ERROR'&&Array.isArray(detail.fields)){return detail.fields.map(item=>item?.message).filter(Boolean).join(' ');}if(Array.isArray(detail)){const names={employee_id:'缺勤员工',leave_start_date:'开始日期',leave_end_date:'结束日期',leave_days:'缺勤天数',proof:'缺勤证明'};const fields=[...new Set(detail.map(item=>item?.loc?.[item.loc.length-1]).filter(Boolean))];return fields.length?`${fields.map(field=>`${names[field]||field}未成功提交`).join('、')}，请检查后重试。`:'';}return ''}

function apiFallbackMessage(status){const messages={400:'提交内容不符合要求，请检查页面提示后重试。',403:'当前账号没有执行此操作的权限，请确认操作范围或联系管理员。',404:'所需记录不存在，或已被其他人处理，请刷新页面后重试。',409:'数据刚刚被其他操作更新，请刷新页面确认最新状态后再试。',413:'上传文件过大，请选择不超过100MB的业务材料后重试。',422:'提交内容不完整，请确认已选择员工、日期和必填材料后重试。',429:'操作过于频繁，请稍候再试。',500:'系统暂时无法处理本次操作，请稍后重试；若持续发生请记录操作时间并联系管理员。',502:'服务正在恢复，请稍后重试。',503:'服务暂不可用，请稍后重试。'};return messages[status]||`请求失败（${status}），请稍后重试。`;}

async function readApiBody(res){
  let text='';
  try{ text=await res.text(); }
  catch(error){ if(error?.name==='AbortError')throw error; throw new Error('读取服务器响应失败，请稍后重试。'); }
  if(!text) return {};
  try{ return JSON.parse(text); }
  catch{ throw new Error('服务器返回了无法解析的数据，请稍后重试。'); }
}

async function api(url, options={}) {
  const requestOptions={...options};
  if(!requestOptions.signal&&(!requestOptions.method||requestOptions.method==='GET')&&getViewSignal())requestOptions.signal=getViewSignal();
  let res;
  try { res = await fetch(portalPath(url), requestOptions); }
  catch(error) { if(error?.name==='AbortError')throw error; throw new Error('网络连接失败，请检查网络后重试；若持续发生请记录操作时间并联系管理员。'); }
  if (res.status === 401) { location.href=portalPath('/login'); throw new Error('登录已失效'); }
  const body = await readApiBody(res);
  if (!res.ok) { const detail=body.detail; const fallback=apiFallbackMessage(res.status); const error=new Error(typeof detail==='object'?(detail.message||validationErrorMessage(detail)||fallback):(detail||fallback)); error.detail=detail; error.status=res.status; throw error; }
  return body;
}

const json = (method, body) => ({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});

export { api, json };
