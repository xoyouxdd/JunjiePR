from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_file_selection_uses_native_labels_without_hidden_input_clicks() -> None:
    script = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    stylesheet = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")

    # Native label/input activation stays inside the browser's trusted gesture
    # path, including browsers that block script-triggered hidden file inputs.
    assert 'class="secondary native-file-trigger" for="recognitionCameraInput"' in script
    assert 'class="secondary native-file-trigger" for="recognitionAlbumInput"' in script
    assert 'class="native-file-input" data-material-pdf-input' in script
    assert 'class="native-file-input" data-material-camera-input' in script
    assert 'class="native-file-input" data-material-album-input' in script
    assert "data-evidence-camera-input]')?.click()" not in script
    assert "data-evidence-album-input]')?.click()" not in script
    assert "data-material-pdf]').onclick=()=>pdfInput.click()" not in script
    assert "data-material-camera]').onclick=()=>cameraInput.click()" not in script
    assert "data-material-album]').onclick=()=>albumInput.click()" not in script
    assert ".native-file-input" in stylesheet
    assert ".native-file-trigger" in stylesheet


def test_portal_navigation_keeps_the_recognition_prefix() -> None:
    script = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    login_script = (ROOT / "app" / "static" / "js" / "login.js").read_text(encoding="utf-8")
    index = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    login = (ROOT / "app" / "static" / "login.html").read_text(encoding="utf-8")

    assert "function portalPath(path)" in script
    assert "location.href=portalPath('/api/statistics/export?'+qs)" in script
    assert "location.href=portalPath('/api/pr-rankings/export?'+params())" in script
    assert "location.href=portalPath('/login')" in script
    assert "href=\"${portalPath('/api/hr/import-template')}\"" in script
    assert "location.href = portalPath('/');" in login_script
    assert '<link rel="canonical" href="https://124.220.229.9:28176/">' in index
    assert '<link rel="canonical" href="https://124.220.229.9:28176/login">' in login
    manifest = (ROOT / "app" / "static" / "manifest.json").read_text(encoding="utf-8")
    assert '"start_url": "/"' in manifest
    assert '"scope": "/"' in manifest
