// Presentation only. Permissions, list ordering, search and signing stay on the server.
import { esc } from './format.js';
import { navIcon } from './navigation.js';

const personalViews=[['pending','待查收'],['history','已查收'],['favorites','收藏'],['messages','消息']];
const compactCount=value=>Number(value)>99?'99+':String(Number(value)||0);

function announcementSkeleton(message='正在读取公告…'){
  return `<div class="ann-loading" role="status"><span class="ann-sr-only">${esc(message)}</span>${[0,1,2].map(()=>'<div class="ann-skeleton" aria-hidden="true"><i></i><b></b><span></span><span></span></div>').join('')}</div>`;
}

function announcementEmpty(view,{q='',category='',canPublish=false}={}){
  const filtered=Boolean(q||category);
  const states={
    pending:['check','待查收已清空','新的工作公告会出现在这里。已完成的内容，可随时在已查收中回看。'],
    history:['inbox','还没有查收记录','完成公告查收后，记录会留在这里，方便日后查阅。'],
    favorites:['bookmark','常用公告，留在手边','在公告详情中点击“收藏”，需要时就能更快找到。'],
    messages:['announcements','暂无新消息','公告更新、查收提醒和交接消息会显示在这里。'],
    manage:['inbox',canPublish?'准备好发布第一份公告':'当前范围暂无公告',canPublish?'把工作要求写清楚，让团队及时收到。':'有权查看的公告及签收记录会显示在这里。'],
    training:['review','暂无待跟进的培训','你被授权负责的有效培训公告会显示在这里。'],
  };
  const [icon,title,description]=filtered?['search','没有找到相关公告',q?`未找到与“${q}”匹配的内容。可以换个关键词，或清除筛选再看看。`:'当前分类暂无公告，可以切换其他分类。']:(states[view]||states.manage);
  const action=filtered?'<button type="button" class="secondary ann-state-action" data-ann-clear>清除搜索与筛选</button>':view==='manage'&&canPublish?'<button type="button" class="primary ann-state-action" data-ann-empty-publish>发布公告</button>':['pending','favorites'].includes(view)?'<button type="button" class="secondary ann-state-action" data-ann-empty-history>查看已查收</button>':'';
  return `<section class="ann-empty" aria-label="${esc(title)}"><div class="ann-empty-symbol" aria-hidden="true">${navIcon(icon)}</div><h3>${esc(title)}</h3><p>${esc(description)}</p>${action}</section>`;
}

function announcementError(message,{back=false}={}){
  return `<section class="ann-empty ann-error" role="alert"><div class="ann-empty-symbol" aria-hidden="true">${navIcon('rotationTest')}</div><h3>暂时未能加载</h3><p>${esc(message||'请检查网络连接后重试。')}</p><div class="ann-state-actions"><button type="button" class="primary" data-ann-retry>重新加载</button>${back?'<button type="button" class="secondary" data-ann-back>返回列表</button>':''}</div></section>`;
}

function announcementShell(options,ui,views){
  const workspaces=views.filter(([key])=>!personalViews.some(([id])=>id===key));
  return `<div class="announcement-center section-gap"><section class="panel ann-center-panel"><header class="ann-center-header"><div class="ann-center-identity"><h2>公告中心</h2><p data-ann-center-status>${options.counts.pending?`<strong data-ann-pending>${esc(options.counts.pending)}</strong> 条公告待查收`:'当前无待查收'}</p></div><div class="ann-header-actions">${options.can_publish?'<button type="button" class="primary ann-publish" data-ann-new>'+navIcon('register')+'<span>发布<span class="ann-desktop-word">公告</span></span></button>':''}${workspaces.length===1?`<button type="button" class="secondary ann-workspace-direct" data-ann-view="${workspaces[0][0]}" aria-label="${esc(workspaces[0][1])}">${navIcon('grid')}<span>${esc(workspaces[0][1])}</span></button>`:workspaces.length?`<details class="ann-workspaces"><summary aria-label="公告工作区" title="公告工作区">${navIcon('grid')}<span class="ann-workspace-label">工作区</span></summary><div class="ann-workspace-menu"><p>公告工作区</p>${workspaces.map(([key,label])=>`<button type="button" class="secondary" data-ann-view="${key}"><span>${label}</span>${navIcon('chevron')}</button>`).join('')}</div></details>`:''}</div></header><nav class="announcement-tabs ann-personal-tabs" aria-label="公告栏目" role="tablist">${personalViews.map(([key,label])=>`<button type="button" role="tab" id="ann-tab-${key}" aria-controls="announcementContent" data-ann-view="${key}"><span>${label}</span>${key==='pending'||key==='messages'?`<span class="ann-tab-count" data-ann-${key==='pending'?'pending':'messages'} data-ann-compact title="${esc(options.counts[key==='pending'?'pending':'messages']||0)}">${compactCount(options.counts[key==='pending'?'pending':'messages'])}</span>`:''}</button>`).join('')}</nav>${options.can_publish?`<div class="ann-draft-bar" data-ann-draft-bar ${ui.draft?'':'hidden'}><span>有一份未发布的草稿</span><button type="button" class="secondary" data-ann-resume>继续草稿</button></div>`:''}<div id="ann-current-workspace" class="ann-current-workspace" data-ann-current-workspace hidden></div><div id="announcementContent" class="announcement-content" aria-label="公告内容"></div><span class="ann-sr-only" data-ann-live role="status" aria-live="polite"></span></section></div>`;
}

function announcementToolbar(options,ui){
  return `<div class="ann-feed-tools"><form class="announcement-search" role="search"><label class="ann-search-field">${navIcon('search')}<input name="q" type="search" aria-label="搜索公告" placeholder="搜索标题、重点或正文" maxlength="100" value="${esc(ui.q)}" autocomplete="off" enterkeyhint="search"></label><button type="submit" class="secondary">搜索</button></form><div class="ann-filter-row"><details class="ann-category-picker" ${ui.filtersOpen?'open':''}><summary>${navIcon('filter')}<span>${esc(ui.category||'全部分类')}</span><span class="ann-disclosure" aria-hidden="true">⌄</span></summary><div class="announcement-filters">${['',...options.categories].map(category=>`<button type="button" class="secondary" data-ann-category="${esc(category)}" aria-pressed="${ui.category===category}">${esc(category||'全部分类')}${ui.category===category?navIcon('check'):''}</button>`).join('')}</div></details><span class="ann-result-count" data-ann-total>正在读取</span>${ui.q||ui.category?'<button type="button" class="ann-clear-filter secondary" data-ann-clear>清除</button>':''}</div></div><div class="ann-feed-results" aria-busy="true">${announcementSkeleton()}</div>`;
}

export { announcementEmpty, announcementError, announcementShell, announcementSkeleton, announcementToolbar };
