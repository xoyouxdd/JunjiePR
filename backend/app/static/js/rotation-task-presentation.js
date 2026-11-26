// Shared by the employee action center, rotation board and rest-room screen.
(() => {
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const legend='<p class="rt-task-legend"><span class="rt-tone-new">蓝色 · 新上岗</span><span class="rt-tone-continue">浅紫 · 继续在岗</span><span class="rt-tone-rest">浅绿 · 去休息</span><span class="rt-tone-special">橙色 · 特殊任务</span></p>';
  function movements(task){
    const before=task.beforePosts||[],after=task.afterPosts||[];
    const rows=[];
    for(const post of after){
      if(!post.occ)continue;
      const original=before.find(p=>p.occ===post.occ);
      rows.push({pid:post.occ,name:post.occName,from:original?.name||'待上岗',to:post.name,
        key:post.key,original,newWorker:!original,seven:post.seven,missed:post.missedCheckIn,
        fixed:post.mode101?.phase==='fixed'?'101 跳过':post.hold?'定岗':'',outcome:'post'});
    }
    for(const post of before){
      if(!post.occ||after.some(p=>p.occ===post.occ))continue;
      const departure=task.departures?.find(p=>p.pid===post.occ);
      const step=task.handoffChain?.find(s=>s.postKey===post.key);
      const route=task.routes?.find(r=>r.pid===post.occ);
      const to=departure?.outcome||step?.outcome||((task.shiftEndPosts||[]).includes(post.key)?'done':null);
      // Older executed receipts omitted routes. Their explicit shift-end list
      // and known task kind still establish the single outgoing person's destination.
      const legacy=task.status==='executed'&&Array.isArray(task.shiftEndPosts)
        ?({push:'rest',push7:'rest',off:'rest',sick:'sick',out:'away'})[task.kind]:null;
      const outcome=to==='pending'?'rest':to||(/病假/.test(route?.to)?'sick':/出圈/.test(route?.to)?'away':/下班/.test(route?.to)?'done':/休息/.test(route?.to)?'rest':legacy||'unknown');
      rows.push({pid:post.occ,name:post.occName,from:post.name,to:({rest:'休息室',done:'下班',sick:'病假',away:'出圈'})[outcome]||'下岗，确认去向',
        original:post,outcome,seven:post.seven,missed:post.missedCheckIn,kind:step?.kind||(task.kind==='push7'&&post.seven?'push7':null)});
    }
    return rows;
  }
  function specials(task,rows){
    const notices=rows.filter(r=>['done','sick','away','unknown'].includes(r.outcome)||r.kind==='push7').map(r=>`${r.name} · ${r.to}${r.kind==='push7'?'（接替7点班）':''}`);
    const openings=task.openingPosts||(task.opening?[task.opening]:[]);
    for(const post of openings)notices.push(`加岗 · ${post.name}`);
    for(const post of task.beforePosts||[])if(!(task.afterPosts||[]).some(p=>p.key===post.key))notices.push(`撤岗 · ${post.name}`);
    if(task.kind==='push7'&&!rows.some(r=>r.kind==='push7'))notices.push('接替7点班 · 按指定岗位执行');
    if((task.beforePosts||[]).some(p=>p.mode101))notices.push('101 模式 · 置灰岗位按指定路线跳过');
    // Preserve any special instructions not covered by a known movement type.
    if(!notices.length)for(const part of task.instructionParts||[])if(part.emphasis)notices.push(part.text);
    return notices.length?`<aside class="rt-task-special" role="note"><strong>特别注意</strong>${[...new Set(notices)].map(text=>`<div>${esc(text)}</div>`).join('')}</aside>`:'';
  }
  function render(task,{batch=false,group='',entryTime=()=>''}={}){
    if(!Array.isArray(task?.beforePosts)||!Array.isArray(task.afterPosts))return '';
    const rows=movements(task),beforeByKey=new Map(task.beforePosts.map(p=>[p.key,p]));
    const badge=post=>(post?.seven?'<small class="rt-badge p7">7点</small>':'')+(post?.missedCheckIn?'<small class="rt-badge rt-missed-attendance">未答到</small>':'');
    const tone=row=>row.outcome==='rest'?'rt-tone-rest':row.outcome!=='post'?'rt-tone-special':row.newWorker?'rt-tone-new':batch?'rt-tone-continue':'';
    const moving=list=>`<div class="rt-movement-list">${list.map(row=>`<div class="rt-movement-row ${tone(row)}" data-movement-pid="${esc(row.pid)}"><div><strong>${esc(row.name)}</strong>${badge(row)}${row.fixed?`<small class="rt-badge post-hold">${esc(row.fixed)}</small>`:''}</div><span class="rt-movement-from">${esc(row.from)}</span><span aria-hidden="true">→</span><b>${esc(row.to)}</b></div>`).join('')}</div>`;
    const table=(posts,after)=>`<div class="rt-task-line"><header class="g-${esc(group)}">${esc(task.line)}</header>${posts.map(post=>{
      const row=rows.find(r=>r.pid===post.occ),original=beforeByKey.get(post.key),changed=after&&post.occ!==original?.occ;
      const departure=!after&&rows.find(r=>r.pid===post.occ&&r.outcome!=='post');
      const opening=(task.openingPosts||(task.opening?[task.opening]:[])).some(p=>p.key===post.key);
      const style=after&&row?tone(row):departure?tone(departure):'';
      return `<div class="rt-task-slot ${style}${changed?' changed':''}${after&&row?.newWorker?' new-worker':''}${opening?' opening-row':''}"><span>${esc(post.name)}${opening?`<small>${after?'加岗':'待加岗'}</small>`:''}</span><div class="rt-task-person"><strong>${esc(post.occName||(post.open===false?'未开岗':'空岗'))}</strong>${entryTime(post)}${badge(post)}</div></div>`;
    }).join('')}</div>`;
    return `<section class="rt-task-comparison${batch?' rt-batch-comparison':' rt-route-comparison'}"><h4>${batch?'整批排岗前后':`${esc(task.line)}线 · 顺推路线`}</h4>${specials(task,rows)}${batch?`${legend}<div class="rt-task-columns"><div><p>执行前 · 原站位</p>${table(task.beforePosts,false)}</div><div><p>执行后 · 最终站位</p>${table(task.afterPosts,true)}</div></div>${rows.some(r=>r.outcome!=='post')?'<h4>下岗去向</h4>'+moving(rows.filter(r=>r.outcome!=='post')):''}`:`<p class="rt-task-route-hint">按顺序接替 · 去休息的人员回休息室确认</p>${moving(rows)}`}</section>`;
  }
  globalThis.RotationTaskPresentation={render,movements,legend};
})();
