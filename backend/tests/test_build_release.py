from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def load_build_release():
    spec = importlib.util.spec_from_file_location("build_release", ROOT / "scripts" / "build_release.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_release_package_excludes_local_secrets_and_uses_app_version(tmp_path: Path) -> None:
    mod = load_build_release()
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    (tmp_path / "secrets.json").write_text("{}", encoding="utf-8")
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("home = x", encoding="utf-8")
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "main.py").write_text("ok", encoding="utf-8")
    (tmp_path / "app" / "service.log").write_text("log", encoding="utf-8")
    data = tmp_path / "data_v2"
    data.mkdir()
    (data / "recognition_v2.db").write_bytes(b"db")
    local_data = tmp_path / ".codex-local-data-test"
    local_data.mkdir()
    (local_data / "runtime.json").write_text("{}", encoding="utf-8")

    assert mod.should_include(tmp_path / ".env", tmp_path) is False
    assert mod.should_include(tmp_path / "secrets.json", tmp_path) is False
    assert mod.should_include(venv / "pyvenv.cfg", tmp_path) is False
    assert mod.should_include(tmp_path / "app" / "service.log", tmp_path) is False
    assert mod.should_include(data / "recognition_v2.db", tmp_path) is False
    assert mod.should_include(local_data / "runtime.json", tmp_path) is False
    assert mod.should_include(app_dir / "main.py", tmp_path) is True
    version = mod.read_app_version()
    assert version == "2026.09.10.3"
    assert mod.package_name(version, "20260910") == "recognition-v2026.09.10.3.zip"
