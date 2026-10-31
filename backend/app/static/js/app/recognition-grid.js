// Enhance only the already-authorized, already-paginated recognition results.
import { attachmentControl, bindFilePreviews } from './files.js';
import { employeeNumber, esc, recognitionScoreNote, recognitionScoreText } from './format.js';
import { sameDayDuplicateBadge, statusBadge } from './records.js';
import { loadUiLibrary } from './ui-libraries.js';

function recognitionDetail(row){
  return `<div class="recognition-inline-detail"><p><strong>${esc(row.recognition_type)} · ${esc(row.recognizer_name)}</strong>${sameDayDuplicateBadge(row)}</p><p>${esc(row.content)}</p>${recognitionScoreNote(row)}${row.review_note?`<p>复核说明：${esc(row.review_note)}</p>`:''}<p class="field-hint">提交时间：${esc(row.submitted_at)}</p>${attachmentControl(row.image_url,'查看认可图片',row.image_preview_kind)}</div>`;
}

function mountRecognitionGrid(panel,rows,onActions){
  const media=window.matchMedia('(max-width: 760px)');
  let disposed=false,table=null,host=null,sequence=0;
  const opened=new Set();
  const remove=()=>{++sequence;table?.destroy();table=null;host?.remove();host=null;panel.classList.remove('has-recognition-grid');};
  const mount=async()=>{
    remove();
    if(disposed||media.matches||!rows.length)return;
    const serial=sequence,Table=await loadUiLibrary('table');
    if(!Table||disposed||serial!==sequence||media.matches||!panel.isConnected)return;
    host=document.createElement('div');host.className='recognition-grid desktop-only';
    host.innerHTML=`<div class="recognition-grid-toolbar"><span class="recognition-page-summary">本页 ${rows.length} 条 · 待复核 ${rows.filter(row=>row.status==='pending').length} 条</span><label>本页查找<input type="search" data-grid-search placeholder="姓名 / 工号 / 类别 / 认可人" autocomplete="off"></label><button type="button" class="secondary" data-grid-pending aria-pressed="false">本页待复核</button><button type="button" class="secondary" data-grid-reset>恢复本页顺序</button></div><p class="field-hint">下方排序和查找仅作用于本页；跨页查询继续使用上方筛选。</p><div data-recognition-grid role="region" aria-label="本页认可记录，可点击表头排序"></div><p class="field-hint" data-grid-count aria-live="polite"></p>`;
    panel.querySelector('[data-entry-native]').before(host);
    try{
      const currentHost=host,grid=currentHost.querySelector('[data-recognition-grid]');
      table=new Table(grid,{data:rows,layout:'fitColumns',renderVertical:'basic',columnDefaults:{headerSort:false,resizable:true},
        rowFormatter:row=>{const element=row.getElement();element.querySelector('.recognition-inline-detail')?.remove();if(opened.has(row.getData().id)){const detail=document.createElement('div');detail.innerHTML=recognitionDetail(row.getData());element.append(detail.firstElementChild);bindFilePreviews(element);}},
        columns:[
          {title:'业务日期',field:'recognition_date',minWidth:104,headerSort:true,sorter:'string'},
          {title:'员工',field:'employee_name',minWidth:110,headerSort:true,formatter:cell=>`${esc(cell.getValue())}<br><small>${esc(cell.getData().employee_no)}</small>`},
          {title:'认可类别 / 认可人',field:'recognition_type',minWidth:150,widthGrow:2,headerSort:true,formatter:cell=>`${esc(cell.getValue())}<br><small>${esc(cell.getData().recognizer_name)}</small>`},
          {title:'分值',field:'credited_fraction',minWidth:85,hozAlign:'right',formatter:cell=>recognitionScoreText(cell.getData())},
          {title:'状态',field:'status',minWidth:85,formatter:cell=>statusBadge(cell.getData())},
          {title:'操作',minWidth:142,formatter:cell=>`<button type="button" class="secondary" data-grid-detail="${Number(cell.getData().id)}" aria-expanded="${opened.has(cell.getData().id)}">${opened.has(cell.getData().id)?'收起':'查看'}</button>${(cell.getData().available_actions||[]).includes('withdraw')?` <button type="button" class="secondary" data-entry-withdraw="${Number(cell.getData().id)}">撤回</button>`:''}`,cellClick:(event,cell)=>{const button=event.target.closest('[data-grid-detail]');if(!button)return;const row=cell.getRow(),id=row.getData().id;if(opened.has(id))opened.delete(id);else opened.add(id);row.reformat();row.normalizeHeight();onActions();}}
        ]});
      table.on('tableBuilt',()=>{
        if(disposed||serial!==sequence||!currentHost.isConnected)return;
        panel.classList.add('has-recognition-grid');
        const search=currentHost.querySelector('[data-grid-search]'),pending=currentHost.querySelector('[data-grid-pending]');
        const filter=()=>{const query=employeeNumber(search.value).toLocaleLowerCase(),onlyPending=pending.getAttribute('aria-pressed')==='true',matches=row=>(!onlyPending||row.status==='pending')&&[row.employee_name,row.employee_no,row.recognition_type,row.recognizer_name].some(value=>String(value||'').toLocaleLowerCase().includes(query));table.setFilter(matches);currentHost.querySelector('[data-grid-count]').textContent=`本页显示 ${rows.filter(matches).length} / ${rows.length} 条`;};
        search.oninput=filter;pending.onclick=()=>{pending.setAttribute('aria-pressed',String(pending.getAttribute('aria-pressed')!=='true'));filter();};
        currentHost.querySelector('[data-grid-reset]').onclick=()=>{search.value='';pending.setAttribute('aria-pressed','false');table.clearSort();filter();};
        filter();onActions();
      });
    }catch(_error){remove();}
  };
  const layout=()=>void mount();media.addEventListener('change',layout);void mount();
  return ()=>{disposed=true;media.removeEventListener('change',layout);remove();};
}

export { mountRecognitionGrid };
