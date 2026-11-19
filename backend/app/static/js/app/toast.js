// Accessible toast messages.


const toastEl = document.getElementById('toast');

let toastTimer = null;
// One timing scale for every page (rotation pages included): errors stay until read or 8s.
const TOAST_DURATION = { status: 2400, encouragement: 5600, error: 8000 };

function toast(message, bad=false, duration=0, tone='') { clearTimeout(toastTimer); toastEl.setAttribute('role',bad?'alert':'status'); toastEl.setAttribute('aria-live',bad?'assertive':'polite'); toastEl.textContent=message; toastEl.classList.toggle('error',bad); toastEl.classList.toggle('encouragement',tone==='encouragement'); toastEl.classList.add('show'); if(bad){toastEl.onclick=()=>{toastEl.classList.remove('show');toastEl.onclick=null;};duration=Math.max(duration||0,TOAST_DURATION.error);}else{toastEl.onclick=null;duration=duration||TOAST_DURATION[tone]||TOAST_DURATION.status;} toastTimer=setTimeout(()=>toastEl.classList.remove('show'),duration); }

export { TOAST_DURATION, toast };
