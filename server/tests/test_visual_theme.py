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
    assert 'body[data-circle-theme="heat"]' in css
    assert 'body[data-circle-theme="dwarf"]' in css
    assert 'body[data-circle-theme="bear"]' in css
    assert "login-subtitle" in login
    assert "app-topbar" in index and "app-identity" in index
