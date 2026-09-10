from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_visual_theme_uses_existing_identity_only():
    script = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "const circleTheme" in script
    assert "state.me?.attraction_name" in script
    assert "applyCircleTheme();" in script
    assert "fetch(" not in script[script.index("const circleTheme") : script.index("const circleTheme") + 400]


def test_visual_theme_css_and_login_marker_are_present():
    css = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    login = (ROOT / "app" / "static" / "login.html").read_text(encoding="utf-8")
    index = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    login_js = (ROOT / "app" / "static" / "js" / "login.js").read_text(encoding="utf-8")
    assert 'body[data-circle-theme="heat"]' in css
    assert 'body[data-circle-theme="dwarf"]' in css
    assert 'body[data-circle-theme="bear"]' in css
    assert "login-subtitle" in login
    assert "app-topbar" in index and "app-identity" in index
    assert 'interactive-widget=resizes-content' in login
    assert 'class="login-hero"' in login
    assert 'spellcheck="false"' in login
    assert 'enterkeyhint="next"' in login
    assert 'enterkeyhint="go"' in login
    assert 'data-password-toggle' in login
    assert 'align-items: start' not in css[css.index('.login-body {\n    min-height: 100svh'):css.index('.login-panel { padding: 20px 16px')]
    assert '.login-panel input:focus' in css
    assert 'box-shadow: 0 0 0 3px rgba(18,103,216,.15)' in css
    assert '.login-panel .primary:active' in css
    assert '.login-panel label:focus-within' in css
    assert '#loginMsg.message.error' in css
    assert 'linear-gradient(to bottom, #102033 0 38%, #f7f9fc 38%)' in css
    assert '@media (min-width: 761px)' in css
    assert '.login-shell .login-panel' in css
    assert 'junjiepr.login.employee_no' in login_js
    assert "passwordInput.focus()" in login_js
    assert "panel.classList.add('is-error')" in login_js
    assert "input:focus" not in css.split('.login-panel input:focus')[0].split('input, select, textarea')[1][:400]
    script = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "内部资料 ·" in script
    assert "CONFIDENTIAL" not in script
    assert "installPageBindHint" in script
    assert "本页内容绑定" in script
    assert "外传可追溯" not in script
    assert "rgba(66, 78, 94, .08)" in css
    assert "#pageBindHint" in css


def test_hr_new_employee_form_has_responsive_field_and_action_layout() -> None:
    script = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")

    assert "hr-create-panel" in script
    assert "hr-create-heading" in script
    assert "hr-create-actions" in script
    assert "创建账号" in script
    assert "grid-template-columns: minmax(210px, 1.2fr)" in css
    assert "@media (min-width: 761px) and (max-width: 1180px)" in css
    assert "#newEmployee .hr-create-actions button { width: 100%" in css
