// Statistics trend SVG, legend and table presentation.
import { circleTheme, esc, fmt } from './format.js';
import { app } from './state.js';

const TREND_SPANS=[3,6,12];

const trendLineColor=name=>({heat:'var(--circle-heat)',dwarf:'var(--circle-dwarf)',bear:'var(--circle-bear)'})[circleTheme(name)]||'var(--muted)';

function trendDelta(points,field){if(points.length<2)return null;const last=Number(points[points.length-1][field]||0),prev=Number(points[points.length-2][field]||0);return {value:last-prev,last,prev};}

function trendDeltaHtml(delta,invert=false){if(!delta||Math.abs(delta.value)<0.005)return '<span class="trend-delta flat">与上月持平</span>';const up=delta.value>0,good=invert?!up:up;return `<span class="trend-delta ${good?'good':'bad'}">${up?'↑':'↓'}${fmt(Math.abs(delta.value))} 较上月 · ${good?'改善':'变差'}</span>`;}

function trendChartSvg(months,lines,width=760){
  if(!months.length)return '<div class="empty">所选范围内没有数据</div>';
  // Drawn in real pixels for the space available, so axis text is not scaled down on phones.
  const w=Math.max(300,Math.min(1200,Math.round(width))),h=w<520?220:260,padL=w<520?42:52,padR=w<520?10:16,padT=18,padB=34,plotW=w-padL-padR,plotH=h-padT-padB;
  const values=lines.flatMap(line=>line.points);
  const rawMax=Math.max(0,...values),rawMin=Math.min(0,...values),span=(rawMax-rawMin)||1;
  const max=rawMax+span*0.08,min=rawMin-span*0.08,range=(max-min)||1;
  const px=i=>padL+(months.length<2?plotW/2:i*plotW/(months.length-1));
  const py=v=>padT+plotH*(1-(v-min)/range);
  const ticks=[0,0.25,0.5,0.75,1].map(t=>min+range*t);
  const grid=ticks.map(v=>`<line x1="${padL}" y1="${py(v).toFixed(1)}" x2="${w-padR}" y2="${py(v).toFixed(1)}" stroke="var(--line)" stroke-width="1"/><text x="${padL-8}" y="${(py(v)+4).toFixed(1)}" text-anchor="end" class="trend-axis">${fmt(v)}</text>`).join('');
  const labelStep=Math.max(1,Math.ceil(months.length/Math.max(1,Math.floor(plotW/40))));
  const labels=months.map((month,i)=>(months.length-1-i)%labelStep?'':(i===months.length-1&&i>0?`<text x="${w-2}" y="${h-10}" text-anchor="end" class="trend-axis">${esc(month.slice(2))}</text>`:`<text x="${px(i).toFixed(1)}" y="${h-10}" text-anchor="middle" class="trend-axis">${esc(month.slice(2))}</text>`)).join('');
  const paths=lines.map(line=>{
    const d=line.points.map((v,i)=>`${i?'L':'M'}${px(i).toFixed(1)} ${py(v).toFixed(1)}`).join(' ');
    const dots=line.points.map((v,i)=>`<circle cx="${px(i).toFixed(1)}" cy="${py(v).toFixed(1)}" r="3" fill="${line.color}"><title>${esc(line.name)} ${esc(months[i])}：${fmt(v)}分</title></circle>`).join('');
    return `<path d="${d}" fill="none" stroke="${line.color}" stroke-width="${line.emphasis?2.5:1.8}" stroke-linejoin="round" stroke-linecap="round"${line.emphasis?'':' stroke-dasharray="0"'}/>${dots}`;
  }).join('');
  return `<svg class="trend-chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="月度总分趋势"><line x1="${padL}" y1="${padT}" x2="${padL}" y2="${h-padB}" stroke="var(--line)" stroke-width="1"/>${grid}${labels}${paths}</svg>`;
}

function trendLegendHtml(lines){return `<div class="trend-legend">${lines.map(line=>`<span><i style="background:${line.color}"></i>${esc(line.name)}</span>`).join('')}</div>`;}

function trendTableHtml(data){
  const rows=data.overall.map((row,i)=>{
    const prev=i?data.overall[i-1]:null;
    const diff=prev?row.total_score-prev.total_score:null;
    return `<tr><td>${esc(row.month)}</td><td>${row.employee_count}</td><td>${fmt(row.recognition_score)}</td><td>${fmt(row.attendance_score)}</td><td>${fmt(row.deduction_score)}</td><td><strong>${fmt(row.total_score)}</strong></td><td>${diff===null?'—':`<span class="trend-delta ${Math.abs(diff)<0.005?'flat':diff>0?'good':'bad'}">${Math.abs(diff)<0.005?'持平':`${diff>0?'↑':'↓'}${fmt(Math.abs(diff))} ${diff>0?'改善':'变差'}`}</span>`}</td></tr>`;
  }).join('');
  return `<div class="table-wrap sticky-col"><table><thead><tr><th>月份</th><th>计分人数</th><th>认可</th><th>出勤</th><th>扣分</th><th>总分</th><th>较上月</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function trendResultsHtml(data){
  const latest=data.overall[data.overall.length-1];
  if(!latest)return '<section class="panel"><div class="empty">所选范围内没有数据</div></section>';
  const lines=[{name:'整体',color:'var(--primary)',emphasis:true,points:data.overall.map(row=>Number(row.total_score||0))},
    ...data.by_attraction.map(circle=>({name:circle.attraction_name,color:trendLineColor(circle.attraction_name),points:circle.series.map(point=>Number(point.total_score||0))}))];
  const cards=[['总分','total_score',false],['认可','recognition_score',false],['出勤','attendance_score',false],['扣分','deduction_score',true]]
    .map(([label,field,invert])=>`<div class="trend-card"><span class="trend-card-label">${label}</span><strong>${fmt(latest[field])}</strong>${trendDeltaHtml(trendDelta(data.overall,field),invert)}</div>`).join('');
  return `<section class="panel"><div class="statistics-page-heading"><h3>${esc(data.months[0])} 至 ${esc(data.end_month)} 趋势</h3><span class="field-hint">共 ${data.overall.length} 个月 · 不含未设置景点圈，计分规则不变</span></div><div class="trend-cards">${cards}</div>${trendChartSvg(data.months,lines,(app.clientWidth||760)-(window.matchMedia('(max-width: 760px)').matches?26:38))}${trendLegendHtml(lines)}${trendTableHtml(data)}</section>`;
}

export { TREND_SPANS, trendResultsHtml };
