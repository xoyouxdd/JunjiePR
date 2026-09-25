from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_direct_entrypoint_assets_do_not_use_legacy_prefix():
    """The published direct address must not reintroduce /recognition navigation."""
    index = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    login = (ROOT / "app" / "static" / "login.html").read_text(encoding="utf-8")
    manifest = (ROOT / "app" / "static" / "manifest.json").read_text(encoding="utf-8")
    router = "\n".join(_p.read_text(encoding="utf-8") for _p in sorted((ROOT / "app" / "routers").glob("*.py")))

    assert 'href="https://124.220.229.9:28176/"' in index
    assert 'href="https://124.220.229.9:28176/login"' in login
    assert '"start_url": "/"' in manifest
    assert '"scope": "/"' in manifest
    assert 'target_path="/recognition/"' not in router


def test_home_screen_identity_is_consistent_on_login_and_app_pages():
    static = ROOT / "app" / "static"
    manifest = (static / "manifest.json").read_text(encoding="utf-8")
    assert '"name": "FZPR登记"' in manifest
    assert '"short_name": "FZPR登记"' in manifest
    assert (static / "images" / "fzpr-icon.png").is_file()
    for page_name in ("login.html", "index.html"):
        page = (static / page_name).read_text(encoding="utf-8")
        assert '<title>FZPR登记</title>' in page
        assert '<meta name="apple-mobile-web-app-title" content="FZPR登记">' in page
        assert 'href="static/manifest.json?v=__STATIC_CACHE_VERSION__"' in page
        assert 'href="static/images/fzpr-icon.png?v=__STATIC_CACHE_VERSION__"' in page
