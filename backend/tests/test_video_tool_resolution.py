"""Private deployment tools must not require global environment changes."""
import os
from pathlib import Path
import tempfile

os.environ["RECOGNITION_V2_DATA_DIR"] = tempfile.mkdtemp(prefix="fz-video-tool-resolution-")

import pytest
from app import video_media as media


def private_tool(tmp_path, name):
    path = tmp_path / (name + (".exe" if os.name == "nt" else ""))
    path.write_bytes(b"tool fixture, never executed")
    return path


def test_explicit_configuration_precedes_private_tool_and_path(tmp_path, monkeypatch):
    private_tool(tmp_path, "ffmpeg")
    configured = tmp_path / "configured.exe"
    configured.write_bytes(b"configured fixture")
    monkeypatch.setattr(media, "PROJECT_TOOL_DIR", tmp_path)
    monkeypatch.setenv("RECOGNITION_FFMPEG", str(configured))
    monkeypatch.setattr(media.shutil, "which", lambda name: pytest.fail("PATH must not be consulted"))
    assert media.tool("ffmpeg") == str(configured)


@pytest.mark.parametrize("name", ["ffmpeg", "ffprobe"])
def test_project_tools_are_used_before_path(tmp_path, monkeypatch, name):
    expected = private_tool(tmp_path, name)
    monkeypatch.setattr(media, "PROJECT_TOOL_DIR", tmp_path)
    monkeypatch.delenv("RECOGNITION_" + name.upper(), raising=False)
    monkeypatch.setattr(media.shutil, "which", lambda value: pytest.fail("PATH must not be consulted"))
    assert media.tool(name) == str(expected)


def test_invalid_explicit_configuration_still_uses_private_tool(tmp_path, monkeypatch):
    expected = private_tool(tmp_path, "ffprobe")
    monkeypatch.setattr(media, "PROJECT_TOOL_DIR", tmp_path)
    monkeypatch.setenv("RECOGNITION_FFPROBE", str(tmp_path / "missing.exe"))
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    assert media.tool("ffprobe") == str(expected)


def test_existing_path_remains_a_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "PROJECT_TOOL_DIR", tmp_path)
    monkeypatch.delenv("RECOGNITION_FFMPEG", raising=False)
    monkeypatch.setattr(media.shutil, "which", lambda name: "/existing/bin/ffmpeg")
    assert media.tool("ffmpeg") == "/existing/bin/ffmpeg"


def test_missing_one_tool_is_not_reported_ready(tmp_path, monkeypatch):
    private_tool(tmp_path, "ffmpeg")
    monkeypatch.setattr(media, "PROJECT_TOOL_DIR", tmp_path)
    for name in ("RECOGNITION_FFMPEG", "RECOGNITION_FFPROBE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="FFmpeg和FFprobe"):
        media.tool("ffprobe")
    assert not media.tools_ready()


def test_private_pair_reports_ready_without_mutating_path(tmp_path, monkeypatch):
    private_tool(tmp_path, "ffmpeg")
    private_tool(tmp_path, "ffprobe")
    before = os.environ.get("PATH")
    monkeypatch.setattr(media, "PROJECT_TOOL_DIR", tmp_path)
    for name in ("RECOGNITION_FFMPEG", "RECOGNITION_FFPROBE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    assert media.tools_ready()
    assert os.environ.get("PATH") == before
