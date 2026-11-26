'use strict';
const staffingExpanded=new Set();
document.addEventListener('toggle',event=>{
  const detail=event.target;
  if(!detail.matches?.('[data-staffing-detail]')||!detail.isConnected)return;
  if(detail.open)staffingExpanded.add(detail.dataset.staffingDetail);else staffingExpanded.delete(detail.dataset.staffingDetail);
},true);
function staffingWarningView(day){
  const points=day.staffingWarnings||[];
  if(!points.length)return '';
  const worst=points.reduce((a,b)=>b.gap>a.gap?b:a);
  const key='staffing:'+day.date;
  const details=(id,body,cls='')=>`<details class="${cls}" data-staffing-detail="${esc(id)}" ${staffingExpanded.has(id)?'open':''}>${body}</details>`;
  const rows=points.map(point=>{
    const people=point.leaving.map(p=>`<li><span>${esc(p.line)} · ${esc(p.post)}</span><strong>${esc(p.name)}</strong><time>${hm(p.end)}下班</time></li>`).join('');
    const uncertain=point.uncertain.map(p=>`<li><strong>${esc(p.name)}</strong><span>${esc(p.reason)}</span></li>`).join('');
    const counts=`需接替 ${point.leaving.length} 人${point.uncertain.length?` · 待确认 ${point.uncertain.length} 人`:''}`;
    return `<section class="rt-staffing-point"><div class="rt-staffing-point-heading"><strong>${hm(point.at)}</strong><span>保留 ${point.requiredPosts} 岗 · 可在岗 ${point.available} 人${point.gap?` · 缺 ${point.gap} 人`:''}</span></div>${point.leaving.length||point.uncertain.length?details(key+':'+Math.round(point.at),`<summary>${counts}<span class="rt-staffing-expand">查看人员</span></summary>${people?`<ul class="rt-staffing-people">${people}</ul>`:''}${uncertain?`<p class="field-hint">待确认，未计入可在岗人数</p><ul class="rt-staffing-people">${uncertain}</ul>`:''}`,'rt-staffing-person-details'):''}</section>`;
  }).join('');
  const basis=details(key+':basis','<summary>计算口径</summary><p>预估未来60分钟，按当前开岗、班次和已确认餐休计算。未签到、未确认休息不计入可在岗人数；未来加撤岗另行核对。</p>','rt-staffing-basis');
  return details(key,`<summary><span class="rt-staffing-label">${worst.gap?'人力预警':'接班提醒'}</span><time>${hm(worst.at)}</time><span>保留 ${worst.requiredPosts} 岗</span><strong>${worst.gap?`预计缺 ${worst.gap} 人`:`需接替 ${worst.leaving.length} 人`}</strong><span class="rt-staffing-open-label">详情</span><span class="rt-staffing-close-label">收起</span></summary><div class="rt-staffing-details">${rows}${basis}</div>`,'rt-staffing-warning');
}

function forecastTransferForm(lineId){
  if(!forecastOperator()||!RT.data.day.postTransferFairnessAvailable)return;
  const day=RT.data.day,source=day.lines.find(line=>line.id===lineId);
  const targets=day.lines.filter(line=>line.id!==lineId&&line.active);
  const posts=source.posts.filter(post=>post.open&&!post.hold&&!post.mode101&&!post.openRequest&&!post.closeAfterRelief);
  if(source.posts.length<=1||!posts.length||!targets.length){toast('请选择可移岗位，原线至少留一岗',true);return;}
  const snapshot=day.forecastSnapshot;
  let latest=null;
  formBox('跨线移岗',`<label>原岗位<select name="post_key">${posts.map(post=>`<option value="${esc(post.key)}">${esc(post.name)} · ${esc(day.persons.find(p=>p.pid===post.occ)?.name||'空岗')}</option>`).join('')}</select></label><label>目标线路<select name="target_line">${targets.map(line=>`<option>${esc(line.id)}</option>`).join('')}</select></label><label>插入位置<select name="target_i"></select></label><div data-transfer-advice aria-live="polite"></div><div data-transfer-preview></div><p class="field-hint">岗位和人一起移，原计时保留。建议可调整。</p>`,
    async(values,form)=>{
      if(!latest||form.querySelector('[type=submit]').disabled)return false;
      const button=form.querySelector('[type=submit]');button.disabled=true;
      const result=await forecastRun({action:'post_transfer',line:lineId,post_key:values.post_key,target_line:values.target_line,target_i:Number(values.target_i),expected_snapshot:snapshot,expected_preview:latest.digest});
      if(!result&&form.isConnected)button.disabled=false;
      return result;
    },
    {okText:'确认移岗',onOpen:host=>{
      const form=host.querySelector('[data-form]');
      let previousTarget=null,revision=0,timer;
      const select=form.querySelector('[name=target_i]'),advice=form.querySelector('[data-transfer-advice]'),button=form.querySelector('[type=submit]');
      const geometry=()=>{
        const target=targets.find(line=>line.id===form.querySelector('[name=target_line]').value);
        const post=posts.find(post=>post.key===form.querySelector('[name=post_key]').value),index=Number(select.value);
        const render=(line,list)=>`<div class="rt-task-line"><header class="g-${esc(line.group||'')}">${esc(line.id)} · ${line.posts.length} → ${list.length} 岗</header>${list.map(slot=>`<div class="rt-task-slot${slot===post?' rt-transfer-moved':''}"><span>${esc(slot.name)}</span><strong>${esc(day.persons.find(p=>p.pid===slot.occ)?.name||'空岗')}</strong></div>`).join('')}</div>`;
        const destination=[...target.posts];destination.splice(index,0,post);
        form.querySelector('[data-transfer-preview]').innerHTML=`<div class="rt-task-columns">${render(source,source.posts.filter(slot=>slot!==post))}${render(target,destination)}</div>`;
      };
      const update=(automatic=false)=>{
        const current=++revision;clearTimeout(timer);latest=null;button.disabled=true;
        const target=targets.find(line=>line.id===form.querySelector('[name=target_line]').value);
        if(previousTarget!==target.id){select.innerHTML=Array.from({length:target.posts.length+1},(_,i)=>`<option value="${i}">第 ${i+1} 岗${i===target.posts.length?'（末尾）':''}</option>`).join('');previousTarget=target.id;}
        geometry();advice.textContent='核对顺序…';
        const body={line:lineId,post_key:form.querySelector('[name=post_key]').value,target_line:target.id,expected_snapshot:snapshot};
        if(!automatic)body.target_i=Number(select.value);
        timer=setTimeout(async()=>{
          try{
            const result=await rtPost('/api/rotation-board/post-transfer-preview',body);
            if(current!==revision||!form.isConnected)return;
            latest=result;select.value=String(result.selectedIndex);geometry();
            const delayed=result.impacts.filter(row=>row.delta>0);
            const names=delayed.slice(0,2).map(row=>`${esc(row.name)} +${row.delta}推`).join(' · ')+(delayed.length>2?` 等${delayed.length}人`:'');
            const details=result.impacts.map(row=>`<li><strong>${esc(row.name)}</strong><span>连续 ${row.minutes===null?'待核实':Math.floor(row.minutes)+'分'}</span><span>${esc(row.beforeLine)}第${row.beforePosition}岗 → ${esc(row.afterLine)}第${row.afterPosition}岗</span><span>${row.beforePushes} → ${row.afterPushes}推休息</span></li>`).join('');
            const rest=projection=>projection.rest.map(row=>esc(row.name)).join(' → ')||'暂无';
            advice.innerHTML=`<div class="rt-transfer-advice"><strong>建议第 ${result.recommendedIndex+1} 岗</strong>${result.selectedIndex!==result.recommendedIndex?'<button type="button" class="secondary" data-transfer-adopt>采用建议</button>':''}<span>${names||'顺推次数不增'}</span></div><details class="rt-transfer-details"><summary>休息顺序详情</summary><p class="field-hint">普通顺推参考；特殊任务另算。</p><ul>${details}</ul><p>接班模拟 · ${result.before.crewCount}人</p><p>原：${rest(result.before)}<br>后：${rest(result.after)}</p><p class="field-hint">按已知接班人员估算${result.before.limited||result.after.limited?'，部分未排':''}，现场可调整。</p></details>`;
            advice.querySelector('[data-transfer-adopt]')?.addEventListener('click',()=>{select.value=String(result.recommendedIndex);update(false);});
            button.disabled=false;
          }catch(error){
            if(current!==revision||!form.isConnected)return;
            advice.innerHTML=`<p class="field-hint">${esc(error.message)}</p><button type="button" class="secondary" data-transfer-retry>重试</button>`;
            advice.querySelector('[data-transfer-retry]').onclick=()=>update(automatic);
          }
        },120);
      };
      form.addEventListener('change',event=>update(event.target.name!=='target_i'));update(true);
    }});
}
