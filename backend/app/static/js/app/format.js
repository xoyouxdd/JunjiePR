// Shared formatting, score labels, local calendar dates and portal-relative URLs.


const circleTheme = name => ({'热力追踪':'heat','矮人迷宫':'dwarf','小熊罐子':'bear'})[String(name||'')] || 'all';

const portalBasePath = location.pathname === '/recognition' || location.pathname.startsWith('/recognition/') ? '/recognition' : '';

function portalPath(path) {
  const value = String(path || '');
  if (!value || !value.startsWith('/') || /^\/\//.test(value) || /^[a-z][a-z0-9+.-]*:/i.test(value)) return value;
  if (!portalBasePath || value === portalBasePath || value.startsWith(`${portalBasePath}/`)) return value;
  return `${portalBasePath}${value}`;
}

const esc = value => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));

const pad2 = value => String(value).padStart(2,'0');

const today = () => {const value=new Date();return `${value.getFullYear()}-${pad2(value.getMonth()+1)}-${pad2(value.getDate())}`;};

const monthNow = () => today().slice(0, 7);

const monthStart = () => `${monthNow()}-01`;

const prefersReducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function memberHomeMonths(){
  const now=new Date();
  return [0,1,2].map(offset=>{
    const value=new Date(now.getFullYear(),now.getMonth()-offset,1);
    return `${value.getFullYear()}-${pad2(value.getMonth()+1)}`;
  });
}

function memberHomeMonthLabel(month){
  const match=String(month||'').match(/^(\d{4})-(\d{2})$/);
  return match?`${match[1]}年${Number(match[2])}月`:String(month||'');
}

const fmt = n => {const value=Number(n||0);return (Math.abs(value)<0.005?0:value).toFixed(2);};

function recognitionScoreText(r){const original=fmt(r.fraction);if(r.status==='confirmed'){const credited=fmt(r.credited_fraction ?? r.fraction);if(credited!==original)return `计入${credited}分（原始${original}）`;return `${credited}分`;}return `${original}分`;}

function recognitionScoreNote(r){return r.monthly_cap_reason?`<small>${esc(r.monthly_cap_reason)}</small>`:'';}

function fmtDeduction(value){return Number(value||0)===0?'0.00':`-${fmt(value)}`}

function actingNoteMarkup(row){return row.acting_note?`<small class="field-hint acting-note">${esc(row.acting_note)}</small>`:'';}

export { actingNoteMarkup, circleTheme, esc, fmt, fmtDeduction, memberHomeMonthLabel, memberHomeMonths, monthNow, monthStart, pad2, portalPath, prefersReducedMotion, recognitionScoreNote, recognitionScoreText, today };
