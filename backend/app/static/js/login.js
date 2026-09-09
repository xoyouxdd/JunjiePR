const portalBasePath = location.pathname === '/recognition' || location.pathname.startsWith('/recognition/') ? '/recognition' : '';
const portalPath = path => portalBasePath ? `${portalBasePath}${path}` : path;
const LAST_ACCOUNT_KEY = 'junjiepr.login.employee_no';

const form = document.getElementById('loginForm');
const panel = form.closest('.login-panel');
const message = document.getElementById('loginMsg');
const submit = form.querySelector('button[type="submit"]');
const employeeInput = form.querySelector('[name="employee_no"]');
const passwordInput = form.querySelector('[name="password"]');
const passwordToggle = form.querySelector('[data-password-toggle]');

try {
  const saved = localStorage.getItem(LAST_ACCOUNT_KEY);
  if (saved && !employeeInput.value) employeeInput.value = saved;
} catch (_error) {
  /* Private mode may block localStorage; login still works. */
}

employeeInput.addEventListener('keydown', event => {
  if (event.key !== 'Enter') return;
  event.preventDefault();
  passwordInput.focus();
});

passwordToggle?.addEventListener('click', () => {
  const show = passwordInput.type === 'password';
  passwordInput.type = show ? 'text' : 'password';
  passwordToggle.textContent = show ? '隐藏' : '显示';
  passwordToggle.setAttribute('aria-pressed', show ? 'true' : 'false');
  passwordToggle.setAttribute('aria-label', show ? '隐藏密码' : '显示密码');
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  message.textContent = '正在登录…';
  message.className = 'message';
  panel.classList.remove('is-error');
  submit.disabled = true;
  try {
    const data = Object.fromEntries(new FormData(event.target).entries());
    const response = await fetch(portalPath('/api/login'), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      message.textContent = body.detail || '登录失败';
      message.className = 'message error';
      panel.classList.add('is-error');
      return;
    }
    try {
      localStorage.setItem(LAST_ACCOUNT_KEY, String(data.employee_no || '').trim());
    } catch (_error) {
      /* Ignore storage failures after a successful login. */
    }
    location.href = portalPath('/');
  } catch (_error) {
    message.textContent = '网络连接失败，请稍后重试';
    message.className = 'message error';
    panel.classList.add('is-error');
  } finally {
    submit.disabled = false;
  }
});
