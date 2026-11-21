import { api } from './api.js';
import { captureViewContext } from './context.js';
import { confirmModal } from './dialogs.js';
import { esc, portalPath, today } from './format.js';
import { requireSubmittedDate, withSubmitLock } from './submission.js';
import { toast } from './toast.js';

const batchPath='/api/recognitions/poc-batch';

export function pocBatchMarkup(){
  return `<details class="poc-batch">
    <summary>一键提交特别贡献</summary>
    <form class="form-stack" data-poc-batch-form>
      <a class="download-link" href="${portalPath(batchPath+'/template')}" download="特别贡献认可导入模板.xlsx">下载 Excel 模板</a>
      <p class="field-hint">每行填一份认可，在表里选择周期和 POC 分值。</p>
      <label>本批认可日期<input name="recognition_date" type="date" value="${today()}" required></label>
      <label>导入填好的模板<input name="workbook" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" required></label>
      <button type="submit" class="primary">导入并核对</button>
      <div data-poc-batch-status role="status"></div>
    </form>
  </details>`;
}

function batchConfirmation(preview){
  const recipients=preview.rows.map(row=>`<li>
    <strong>${esc(row.employee_name)} · ${esc(row.employee_no)}</strong>
    <small>${esc(row.circle)} · ${row.period_type==='quarter'?'季度':'月度'} ${esc(row.period_key)} · +${esc(row.points)} 分</small>
    <p>${esc(row.reason)}</p>
  </li>`).join('');
  return `<p>向 <strong>${preview.recipient_count} 人</strong>发送 <strong>${preview.rows.length} 封</strong>工作台来信。</p>
    <p>认可日期 ${esc(preview.recognition_date)}</p>
    <ol class="poc-batch-recipients">${recipients}</ol>`;
}

export function bindPocBatch(form){
  if(!form)return;
  const status=form.querySelector('[data-poc-batch-status]');
  const context=captureViewContext(form);
  form.addEventListener('submit', async event=>{
    event.preventDefault();
    await withSubmitLock(form,async()=>{
      status.textContent='';
      try{
        const data=new FormData(form);
        requireSubmittedDate(data,form.elements.recognition_date,'本批认可日期');
        const preview=await api(batchPath+'/preview',{method:'POST',body:data});
        if(!context.isCurrent())return;
        if(preview.duplicate){
          status.textContent=`这批 ${preview.sent_count} 封认可来信已发送。`;
          return;
        }
        const confirmed=await confirmModal('确认批量发送特别贡献',batchConfirmation(preview),'确认发送');
        if(!confirmed||!context.isCurrent())return;
        data.set('confirmation',preview.confirmation);
        const result=await api(batchPath+'/send',{method:'POST',body:data});
        const message=`已发送 ${result.sent_count} 封特别贡献来信，员工可在首页拆信查看。`;
        if(context.isCurrent()){
          form.reset();
          status.textContent=message;
        }
        toast(message);
      }catch(error){
        if(!context.isCurrent()||error.name==='AbortError')return;
        const rows=error.detail?.rows;
        const errors=Array.isArray(rows)?`<ul>${rows.map(row=>`<li>第 ${esc(row.row)} 行 · ${esc(row.message)}</li>`).join('')}</ul>`:'';
        status.innerHTML=`<p>${esc(error.message)}</p>${errors}`;
      }
    });
  });
}
