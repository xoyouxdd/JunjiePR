'use strict';
// This file is served only by the local launcher, outside application static assets.
(() => {
  const endpoint='/__local/rotation-clock';
  let busy=false,latest=null,latestAt=Date.now();
  const badge=document.createElement('span');
  badge.id='localSimulationBadge';badge.className='local-clock-status';badge.setAttribute('role','status');
  const top=document.querySelector('.rt-top');if(top)top.append(badge);else document.body.prepend(badge);
  const current=()=>typeof RT!=='undefined'&&RT.data?.clock||latest;
  const safe=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function status(){
    const c=current();
    if(typeof RT!=='undefined'&&RT.data&&typeof renderClock==='function')renderClock();
    let stamp='';
    if(c?.mode==='sim'){
      const minute=typeof RT!=='undefined'&&RT.data?.clock===c?nowMin():c.minute+(c.paused?0:(Date.now()-latestAt)/60000*c.speed);
      const simulated=new Date(c.date+'T00:00:00Z');simulated.setUTCDate(simulated.getUTCDate()+Math.floor(minute/1440));
      const seconds=Math.floor(minute*60+1e-6)%86400,pad=value=>String(value).padStart(2,'0');
      stamp=`${simulated.toISOString().slice(0,10)} ${pad(Math.floor(seconds/3600))}:${pad(Math.floor(seconds/60)%60)}:${pad(seconds%60)}`;
    }
    badge.textContent=c?.mode==='sim'?`本地模拟时间 ${stamp} · ${c.paused?'已暂停':'运行中'} · ×${c.speed}`:'独立本地测试 · 尚未应用模拟时间';
    const toggle=document.querySelector('[data-local-clock="toggle"]');
    if(toggle){toggle.textContent=c?.paused?'继续':'暂停';toggle.disabled=busy||c?.mode!=='sim';}
    document.querySelectorAll('[data-local-clock="jump"],#lcSpeed').forEach(el=>el.disabled=busy||c?.mode!=='sim');
    const work=document.querySelector('[data-local-workday]');
    if(work&&c)work.textContent=`轮岗工作日 ${c.date} · 回拨保留已执行操作，已结束日期不会自动恢复`;
  }
  async function post(body){
    const response=await fetch(endpoint,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const out=await response.json();if(!response.ok)throw new Error(out.detail||'本地测试时钟操作失败');
    if(out.clock){latest=out.clock;latestAt=Date.now();}
    return out;
  }
  async function refresh(){
    if(typeof RT!=='undefined')await RT.load();
    status();
  }
  async function act(action,extra={}){
    if(busy)return false;
    busy=true;status();
    try{
      await post({action,...extra});
      await refresh();
      if(action==='set')toast('已应用本地模拟时间并暂停；已执行操作不会回撤');
      if(action==='restart')location.reload();
      return true;
    }catch(error){toast(error.message,true);return false;}
    finally{busy=false;status();}
  }
  function restart(){
    confirmBox('从当天初始班表重新开始', '<p>这会清理当前景点圈、当前轮岗工作日的测试站位、任务、计时和轮岗记录，并恢复保存的初始班表。</p><p>原始班表、其他日期及其他业务数据保留。调时间不会执行此清理。</p>', '继续核对重测日期',async()=>{
      try{
        const preview=await post({action:'restart-preview'});
        formBox('再次确认：清理本次轮岗测试结果',`<p>${safe(preview.message)}</p><label>输入轮岗工作日 ${safe(preview.date)}<input name="confirm_date" required autocomplete="off" placeholder="YYYY-MM-DD"></label>`,async values=>{
          if(values.confirm_date!==preview.date){toast('日期不一致，未清理任何结果',true);return false;}
          return await act('restart',{...values,restart_token:preview.restart_token});
        },{warn:true,okText:'确认清理并从初始班表开始'});
      }catch(error){toast(error.message,true);return false;}
    },{warn:true});
  }
  const bar=document.getElementById('rtClockBar'),tabs=document.getElementById('rtTabs');
  if(bar&&tabs&&typeof formalRotation!=='undefined'&&formalRotation&&!personalRotation){
    const row=document.createElement('div');row.className='local-clock-tabrow';tabs.before(row);row.append(tabs,bar);
    renderClockBar=()=>{
      if(!me?.capabilities?.can_manage){bar.hidden=true;return;}
      const c=RT.data.clock;
      bar.hidden=false;
      if(!bar.dataset.localClock){
        bar.dataset.localClock='1';
        bar.innerHTML=`<div class="local-clock-inputs"><label>日期<input type="date" id="lcDate" value="${safe(c.calendarDate||c.date)}"></label><label>时间<input type="time" step="1" id="lcTime" value="${safe(hms(c.minute))}"></label><button class="secondary" data-local-clock="set">应用时间</button></div><div class="local-clock-actions"><button class="secondary" data-local-clock="toggle">继续</button><label>倍速<select id="lcSpeed">${[1,5,10,30,60].map(s=>`<option value="${s}">×${s}</option>`).join('')}</select></label><button class="secondary" data-local-clock="jump" data-minutes="1">快进1分钟</button><button class="secondary" data-local-clock="jump" data-minutes="10">快进10分钟</button><button class="danger" data-local-clock="restart">从当天初始班表重新开始</button></div><small data-local-workday></small>`;
        bar.querySelector('#lcSpeed').value=String(c.speed);
        bar.addEventListener('click',event=>{
          const button=event.target.closest('[data-local-clock]');if(!button||busy)return;
          const action=button.dataset.localClock;
          if(action==='restart'){restart();return;}
          if(action==='set')void act('set',{date:bar.querySelector('#lcDate').value,time:bar.querySelector('#lcTime').value,speed:Number(bar.querySelector('#lcSpeed').value)});
          else if(action==='toggle')void act(current()?.paused?'resume':'pause');
          else if(action==='jump')void act('jump',{minutes:Number(button.dataset.minutes)});
        });
        bar.querySelector('#lcSpeed').onchange=event=>void act('speed',{speed:Number(event.target.value)});
      }
      // SSE refreshes must not eat a date/time the operator is editing.
      if(document.activeElement!==bar.querySelector('#lcSpeed'))bar.querySelector('#lcSpeed').value=String(c.speed);
      status();
    };
    if(typeof RT!=='undefined'&&RT.data&&me)renderClockBar();
  }
  if(typeof RT==='undefined'){
    let polling=false;
    const poll=async()=>{
      if(polling)return;
      const host=document.querySelector('[data-formal-rotation-todo]');if(host?.dataset.scopeLoading)return;
      const circle=host?.dataset.rotationCircle;polling=true;
      try{
        const response=await fetch(endpoint+'/state'+(circle?'?attraction_id='+encodeURIComponent(circle):''),{credentials:'same-origin'});
        if(response.ok&&circle===document.querySelector('[data-formal-rotation-todo]')?.dataset.rotationCircle){latest=(await response.json()).clock;latestAt=Date.now();}
      }catch{}finally{polling=false;}
      status();
    };
    void poll();setInterval(poll,2000);
  }else{
    const render=RT.render;RT.render=(...args)=>{const result=render(...args);status();return result;};
  }
  status();setInterval(status,500);
})();
