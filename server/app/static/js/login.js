const portalBasePath = location.pathname === '/recognition' || location.pathname.startsWith('/recognition/') ? '/recognition' : '';
const portalPath = path => portalBasePath ? `${portalBasePath}${path}` : path;

document.getElementById('loginForm').addEventListener('submit', async event => {
  event.preventDefault();
  const message = document.getElementById('loginMsg');
  const submit = event.target.querySelector('button[type="submit"]');
  message.textContent = '正在登录…';
  message.className = 'message';
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
      return;
    }
    location.href = portalPath('/');
  } catch (_error) {
    message.textContent = '网络连接失败，请稍后重试';
    message.className = 'message error';
  } finally {
    submit.disabled = false;
  }
});
