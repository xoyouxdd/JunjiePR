'use strict';
// 休息室大屏：展示各线与休息区；只能点「去休息」和「去轮岗」，点后先确认是本人。

const $app = document.getElementById('rtApp');

function screenView() {
  const day = RT.data.day;
  if (!day) return '<section class="panel rt-empty"><h2>今天的轮岗还没有开始</h2><p>主管发布后，这里会自动显示。</p></section>';
  if (day.readOnly) return `<section class="rt-workflow-board"><div class="rt-workflow-heading"><h1>${esc(day.date)} · 轮岗存档</h1></div><p class="field-hint" role="status">旧版记录，仅供查看。</p>${workflowBoardContent(day, nowMin(), false, {readOnly:true})}</section>`;
  if (day.status === 'ended') return '<section class="panel rt-empty"><h2>今天的轮岗已结束</h2></section>';
  return workflowBoardView(nowMin(), false);
}

let renderedScreen;
RT.render = () => {
  if (!RT.data||RT.editingPost) return;
  renderClock();
  const day = RT.data.day;
  document.body.classList.toggle('rt-screen-flow', !!day && (day.readOnly || day.status !== 'ended'));
  const html = screenView();
  if (html === renderedScreen) return;
  renderedScreen = html;
  const scroll = [...$app.querySelectorAll('.rt-lines,.rt-chips')].map(el => el.scrollTop);
  $app.innerHTML = html;
  [...$app.querySelectorAll('.rt-lines,.rt-chips')].forEach((el, i) => { el.scrollTop = scroll[i] || 0; });
};

RT.load = async () => {
  await loadLive(`${rotationScreenApi}/state`);
  if (RT.data?.day?.readOnly && RT.modalOpen) closeModal();
};

(async () => {
  try {
    await RT.load();
    connectLive();
    startLocalTicker();
  } catch (error) {
    $app.innerHTML = `<section class="panel rt-empty"><h2>无法加载大屏</h2><p>${esc(error.message)}</p></section>`;
  }
})();
