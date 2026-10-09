// Accessible toast messages.


const toastEl = document.getElementById('toast');

let toastTimer = null;

function toast(message, bad=false, duration=2400, tone='') { clearTimeout(toastTimer); toastEl.setAttribute('role',bad?'alert':'status'); toastEl.setAttribute('aria-live',bad?'assertive':'polite'); toastEl.textContent=message; toastEl.classList.toggle('error',bad); toastEl.classList.toggle('encouragement',tone==='encouragement'); toastEl.classList.add('show'); if(bad){toastEl.onclick=()=>{toastEl.classList.remove('show');toastEl.onclick=null;};duration=Math.max(duration||0,8000);}else{toastEl.onclick=null;} toastTimer=setTimeout(()=>toastEl.classList.remove('show'),duration); }

export { toast };
