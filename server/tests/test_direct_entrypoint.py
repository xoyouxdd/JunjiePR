from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_direct_entrypoint_assets_and_notifications_do_not_use_legacy_prefix():
    """The published direct address must not reintroduce /recognition navigation."""
    index = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    login = (ROOT / "app" / "static" / "login.html").read_text(encoding="utf-8")
    manifest = (ROOT / "app" / "static" / "manifest.json").read_text(encoding="utf-8")
    router = (ROOT / "app" / "routers" / "v2.py").read_text(encoding="utf-8")

    assert 'href="https://124.220.229.9:28176/"' in index
    assert 'href="https://124.220.229.9:28176/login"' in login
    assert '"start_url": "/"' in manifest
    assert '"scope": "/"' in manifest
    assert 'target_path="/recognition/"' not in router
    assert router.count('target_path="/"') >= 2
