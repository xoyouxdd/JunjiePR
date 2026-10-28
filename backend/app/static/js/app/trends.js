// Statistics trend SVG, legend and table presentation.
import { circleTheme, esc, fmt } from './format.js';
import { app } from './state.js';
import { loadUiLibrary } from './ui-libraries.js';

const TREND_SPANS=[3,6,12];
const SCORE_FIELDS=['recognition_score','attendance_score','deduction_score','total_score'];
const displayScore=value=>value==null||value==='-'?'—':fmt(value);

function trendDisplayData(data,mode){
  if(mode!=='average')return data;
  const average=row=>({...row,...Object.fromEntries(SCORE_FIELDS.map(field=>[field,
    Number(row.employee_count)>0?Number(row[field]||0)/Number(row.employee_count):null]))});
  return {...data,overall:data.overall.map(average),by_attraction:data.by_attraction.map(circle=>({...circle,series:circle.series.map(average)}))};
}

const trendLineColor=name=>({heat:'var(--circle-heat)',dwarf:'var(--circle-dwarf)',bear:'var(--circle-bear)'})[circleTheme(name)]||'var(--muted)';

function trendDelta(points,field){if(points.length<2)return null;const last=points[points.length-1][field],prev=points[points.length-2][field];return last==null||prev==null?null:{value:Number(last)-Number(prev),last,prev};}

function trendDeltaHtml(delta,invert=false){if(!delta)return '<span class="trend-delta flat">—</span>';if(Math.abs(delta.value)<0.005)return '<span class="trend-delta flat">与上月持平</span>';const up=delta.value>0,good=invert?!up:up;return `<span class="trend-delta ${good?'good':'bad'}">${up?'↑':'↓'}${fmt(Math.abs(delta.value))} 较上月 · ${good?'改善':'变差'}</span>`;}

function trendChartSvg(months,lines,width=760,average=false){
  if(!months.length)return '<div class="empty">所选范围内没有数据</div>';
  // Drawn in real pixels for the space available, so axis text is not scaled down on phones.
  const w=Math.max(300,Math.min(1200,Math.round(width))),h=w<520?220:260,padL=w<520?42:52,padR=w<520?10:16,padT=18,padB=34,plotW=w-padL-padR,plotH=h-padT-padB;
  const values=lines.flatMap(line=>line.points).filter(value=>value!=null);
  const rawMax=Math.max(0,...values),rawMin=Math.min(0,...values),span=(rawMax-rawMin)||1;
  const max=rawMax+span*0.08,min=rawMin-span*0.08,range=(max-min)||1;
  const px=i=>padL+(months.length<2?plotW/2:i*plotW/(months.length-1));
  const py=v=>padT+plotH*(1-(v-min)/range);
  const ticks=[0,0.25,0.5,0.75,1].map(t=>min+range*t);
  const grid=ticks.map(v=>`<line x1="${padL}" y1="${py(v).toFixed(1)}" x2="${w-padR}" y2="${py(v).toFixed(1)}" stroke="var(--line)" stroke-width="1"/><text x="${padL-8}" y="${(py(v)+4).toFixed(1)}" text-anchor="end" class="trend-axis">${fmt(v)}</text>`).join('');
  const labelStep=Math.max(1,Math.ceil(months.length/Math.max(1,Math.floor(plotW/40))));
  const labels=months.map((month,i)=>(months.length-1-i)%labelStep?'':(i===months.length-1&&i>0?`<text x="${w-2}" y="${h-10}" text-anchor="end" class="trend-axis">${esc(month.slice(2))}</text>`:`<text x="${px(i).toFixed(1)}" y="${h-10}" text-anchor="middle" class="trend-axis">${esc(month.slice(2))}</text>`)).join('');
  const paths=lines.map(line=>{
    const d=line.points.map((v,i)=>v==null?'':`${i&&line.points[i-1]!=null?'L':'M'}${px(i).toFixed(1)} ${py(v).toFixed(1)}`).join(' ');
    const dots=line.points.map((v,i)=>v==null?'':`<circle cx="${px(i).toFixed(1)}" cy="${py(v).toFixed(1)}" r="3" fill="${line.color}"><title>${esc(line.name)} ${esc(months[i])}：${fmt(v)}${average?'分/人':'分'}</title></circle>`).join('');
    return `<path d="${d}" fill="none" stroke="${line.color}" stroke-width="${line.emphasis?2.5:1.8}" stroke-linejoin="round" stroke-linecap="round"${line.emphasis?'':' stroke-dasharray="0"'}/>${dots}`;
  }).join('');
  return `<svg class="trend-chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="月度${average?'人均':'合计'}总分趋势"><line x1="${padL}" y1="${padT}" x2="${padL}" y2="${h-padB}" stroke="var(--line)" stroke-width="1"/>${grid}${labels}${paths}</svg>`;
}

function trendLegendHtml(lines){return `<div class="trend-legend">${lines.map(line=>`<span><i data-trend-color="${esc(line.color)}"></i>${esc(line.name)}</span>`).join('')}</div>`;}

function trendTableHtml(data,average=false){
  const rows=data.overall.map((row,i)=>{
    const prev=i?data.overall[i-1]:null;
    const diff=prev&&row.total_score!=null&&prev.total_score!=null?row.total_score-prev.total_score:null;
    return `<tr><td>${esc(row.month)}</td><td>${row.employee_count}</td><td>${displayScore(row.recognition_score)}</td><td>${displayScore(row.attendance_score)}</td><td>${displayScore(row.deduction_score)}</td><td><strong>${displayScore(row.total_score)}</strong></td><td>${diff===null?'—':`<span class="trend-delta ${Math.abs(diff)<0.005?'flat':diff>0?'good':'bad'}">${Math.abs(diff)<0.005?'持平':`${diff>0?'↑':'↓'}${fmt(Math.abs(diff))} ${diff>0?'改善':'变差'}`}</span>`}</td><td><button type="button" class="secondary" data-trend-month="${esc(row.month)}">查看当月</button></td></tr>`;
  }).join('');
  return `<div class="table-wrap sticky-col"><table><thead><tr><th>月份</th><th>计分人数</th><th>${average?'人均':''}认可</th><th>${average?'人均':''}出勤</th><th>${average?'人均':''}扣分</th><th>${average?'人均':''}总分</th><th>较上月</th><th>统计明细</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function trendResultsHtml(rawData,mode='total'){
  const average=mode==='average',data=trendDisplayData(rawData,mode);
  const latest=data.overall[data.overall.length-1];
  if(!latest)return '<section class="panel"><div class="empty">所选范围内没有数据</div></section>';
  const lines=[{name:'整体',color:'var(--primary)',emphasis:true,points:data.overall.map(row=>row.total_score??null)},
    ...data.by_attraction.map(circle=>({name:circle.attraction_name,color:trendLineColor(circle.attraction_name),points:circle.series.map(point=>point.total_score??null)}))];
  const cards=[['总分','total_score',false],['认可','recognition_score',false],['出勤','attendance_score',false],['扣分','deduction_score',true]]
    .map(([label,field,invert])=>`<div class="trend-card"><span class="trend-card-label">${average?'人均':''}${label}</span><strong>${displayScore(latest[field])}</strong>${trendDeltaHtml(trendDelta(data.overall,field),invert)}</div>`).join('');
  return `<section class="panel compact-trend-panel"><div class="statistics-page-heading"><h3>${esc(data.months[0])} 至 ${esc(data.end_month)} 趋势</h3><span class="field-hint">共 ${data.overall.length} 个月 · 不含未设置景点圈，计分规则不变</span></div><div class="trend-measure" role="group" aria-label="趋势统计口径"><button type="button" class="${average?'secondary':'primary'}" data-trend-mode="total" aria-pressed="${!average}">合计</button><button type="button" class="${average?'primary':'secondary'}" data-trend-mode="average" aria-pressed="${average}">人均</button><span class="field-hint">${average?'各项合计 ÷ 同月计分人数；无人计分时显示 —':'显示各项分数合计'}</span></div><div class="trend-cards">${cards}</div><div class="trend-chart-fallback">${trendChartSvg(data.months,lines,(app.clientWidth||760)-(window.matchMedia('(max-width: 760px)').matches?26:38),average)}${trendLegendHtml(lines)}</div><div class="trend-interactive-chart" role="img" aria-label="月度${average?'人均':'合计'}总分趋势，可切换景点圈图例并查看数值" hidden></div>${trendTableHtml(data,average)}</section>`;
}

// The exact numeric table and SVG stay available if the optional chart fails.
function mountTrendChart(host,rawData,mode='total'){
  const average=mode==='average',data=trendDisplayData(rawData,mode);
  let disposed=false,chart=null,observer=null;
  host.querySelectorAll('[data-trend-color]').forEach(mark=>{mark.style.backgroundColor=mark.dataset.trendColor;});
  void loadUiLibrary('chart').then(Charts=>{
    if(!Charts||disposed||!host.isConnected)return;
    const target=host.querySelector('.trend-interactive-chart'),fallback=host.querySelector('.trend-chart-fallback');
    if(!target||!fallback)return;
    const css=getComputedStyle(host),color=name=>css.getPropertyValue(name).trim();
    const lines=[{name:'整体',color:color('--primary'),points:data.overall.map(row=>row.total_score??null)},...(data.by_attraction||[]).map(circle=>({name:circle.attraction_name,color:color(({heat:'--circle-heat',dwarf:'--circle-dwarf',bear:'--circle-bear'})[circleTheme(circle.attraction_name)]||'--muted'),points:circle.series.map(row=>row.total_score??null)}))];
    try{
      target.hidden=false;
      chart=Charts.init(target,null,{renderer:'canvas'});
      chart.setOption({animation:false,color:lines.map(line=>line.color),textStyle:{fontFamily:'system-ui, Microsoft YaHei, sans-serif'},tooltip:{trigger:'axis',renderMode:'richText',confine:true,valueFormatter:displayScore},legend:{bottom:0,type:'scroll',textStyle:{color:color('--text-secondary')}},grid:{left:60,right:18,top:30,bottom:65},xAxis:{type:'category',data:data.months,axisLabel:{color:color('--text-secondary')},axisLine:{lineStyle:{color:color('--line')}}},yAxis:{type:'value',name:average?'分/人':'分',nameTextStyle:{color:color('--text-secondary')},axisLabel:{color:color('--text-secondary')},splitLine:{lineStyle:{color:color('--line')}}},series:lines.map((line,index)=>({name:line.name,type:'line',data:line.points,connectNulls:false,symbol:['circle','rect','triangle','diamond'][index%4],symbolSize:6,lineStyle:{width:index?1.8:2.5},emphasis:{focus:'series'}}))});
      fallback.hidden=true;
      observer=new ResizeObserver(()=>{if(target.isConnected)chart?.resize();});observer.observe(target);
    }catch(_error){chart?.dispose();chart=null;target.hidden=true;fallback.hidden=false;}
  });
  return ()=>{disposed=true;observer?.disconnect();chart?.dispose();chart=null;};
}

export { TREND_SPANS, mountTrendChart, trendResultsHtml };
