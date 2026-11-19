"""Stable HTML and PWA contracts for the real sign-in entry."""
from html.parser import HTMLParser
import json
from pathlib import Path

from app.branding import BRAND_EN


STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


class Shell(HTMLParser):
    def __init__(self, filename):
        super().__init__()
        self.tags = []
        self.title = ""
        self.text = []
        self.in_title = False
        self.feed((STATIC / filename).read_text(encoding="utf-8"))

    def find(self, tag, **attrs):
        return [values for name, values in self.tags
                if name == tag and all(values.get(key) == value for key, value in attrs.items())]

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif data.strip():
            self.text.append(data.strip())


def test_mobile_login_has_native_keyboard_and_password_controls():
    login = Shell("login.html")
    username, = login.find("input", name="employee_no")
    password, = login.find("input", name="password")
    viewport, = login.find("meta", name="viewport")
    assert username["autocomplete"] == "username"
    assert username["spellcheck"] == "false"
    assert username["enterkeyhint"] == "next"
    assert password["autocomplete"] == "current-password"
    assert password["type"] == "password"
    assert password["enterkeyhint"] == "go"
    assert "interactive-widget=resizes-content" in viewport["content"]
    toggle, = [button for button in login.find("button") if "data-password-toggle" in button]
    assert toggle["type"] == "button"
    assert toggle["aria-pressed"] == "false"
    assert toggle["aria-label"]


def test_canonical_entry_and_pwa_keep_root_deployment_urls():
    for page, suffix in (("index.html", "/"), ("login.html", "/login")):
        shell = Shell(page)
        canonical, = shell.find("link", rel="canonical")
        assert canonical["href"] == "https://124.220.229.9:28176" + suffix
        assert shell.title == "FZ团队工作台"
        assert shell.find("meta", name="apple-mobile-web-app-title", content="FZ工作台")
        assert shell.find("link", rel="manifest", href="static/manifest.json?v=__STATIC_CACHE_VERSION__")
        assert shell.find("link", rel="apple-touch-icon", href="static/images/fzpr-icon.png?v=__STATIC_CACHE_VERSION__")
        assert BRAND_EN in shell.text
    manifest = json.loads((STATIC / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["start_url"] == manifest["scope"] == "/"
    assert (manifest["name"], manifest["short_name"]) == ("FZ团队工作台", "FZ工作台")
    assert (STATIC / "images/fzpr-icon.png").is_file()


def test_rotation_shells_share_the_workbench_identity():
    for page in ("rotation-board.html", "rotation-screen.html"):
        shell = Shell(page)
        assert shell.title.endswith(f" · {BRAND_EN}")
        assert BRAND_EN in shell.text
