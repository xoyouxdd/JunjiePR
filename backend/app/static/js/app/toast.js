// Accessible toast messages.


const toastEl = document.getElementById('toast');

let toastTimer = null;
// One timing scale for every page (rotation pages included): errors stay until read or 8s.
const TOAST_DURATION = { status: 2400, success: 2600, warn: 4200, encouragement: 5600, error: 8000 };

function toast(message, bad=false, duration=0, tone='') {
  clearTimeout(toastTimer);
  const resolvedTone = bad ? '' : (tone || (/^(?:已|保存成功|提交成功|操作成功|更新成功|导出成功)/.test(String(message || '').trim()) ? 'success' : ''));
  toastEl.setAttribute('role', bad ? 'alert' : 'status');
  toastEl.setAttribute('aria-live', bad ? 'assertive' : 'polite');
  toastEl.textContent = message;
  toastEl.classList.toggle('error', bad);
  toastEl.classList.toggle('success', resolvedTone === 'success');
  toastEl.classList.toggle('warn', resolvedTone === 'warn');
  toastEl.classList.toggle('encouragement', resolvedTone === 'encouragement');
  toastEl.classList.add('show');
  if (bad) {
    toastEl.onclick = () => { toastEl.classList.remove('show'); toastEl.onclick = null; };
    duration = Math.max(duration || 0, TOAST_DURATION.error);
  } else {
    toastEl.onclick = null;
    duration = duration || TOAST_DURATION[resolvedTone] || TOAST_DURATION.status;
  }
  toastTimer = setTimeout(() => toastEl.classList.remove('show'), duration);
}

export { TOAST_DURATION, toast };
