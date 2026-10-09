"""Filesystem-only runtime rollback regressions; never import/use the real venv."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("deployment_runtime", ROOT / "scripts" / "deployment_runtime.py")
runtime = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(runtime)


@pytest.fixture
def tree():
    # Plain mkdir uses the project ACL, avoiding Windows tempfile's restrictive
    # mode-700 fixture ACL. Keep isolated trees as fault-injection evidence.
    base = ROOT / "output" / "refactor-validation" / "phase3-runtime-sandbox" / uuid.uuid4().hex
    base.mkdir(parents=True)
    assert base.resolve().is_relative_to((ROOT / "output").resolve())
    env = base / ".venv"
    (env / "Scripts").mkdir(parents=True)
    (env / "Lib" / "site-packages" / "oldpkg").mkdir(parents=True)
    (env / "empty-directory").mkdir()
    (env / "Scripts" / "python.exe").write_bytes(b"fake locked Python launcher")
    (env / "Scripts" / "old-launcher.exe").write_bytes(b"fake launcher with embedded original path")
    (env / "pyvenv.cfg").write_text("home = fake-python\n", encoding="utf-8")
    (env / "Lib" / "site-packages" / "oldpkg" / "__init__.py").write_bytes(b"version = 'old'\n")
    (env / "Lib" / "site-packages" / "oldpkg" / "removed.py").write_bytes(b"preserve removed file\n")
    snapshot = base / ".deploy-rollbacks" / "test-stamp" / "venv"
    return base, env, snapshot


def test_snapshot_commits_bound_inventory_and_read_only_verification(tree):
    _, env, snapshot = tree
    before = runtime._scan(env, include_cache=True)
    receipt = runtime.snapshot_environment(env, snapshot)
    assert receipt["environment"] == str(env)
    assert receipt["snapshot"] == str(snapshot)
    assert receipt["inventory"] == before
    assert json.loads(json.dumps(receipt)) == receipt
    assert runtime.verify_environment(env, snapshot)["verified"] is True
    assert runtime._scan(env, include_cache=True) == before


def test_partial_install_restores_changes_additions_removals_and_empty_directories(tree):
    _, env, snapshot = tree
    receipt = runtime.snapshot_environment(env, snapshot)
    original_launcher = (env / "Scripts" / "old-launcher.exe").read_bytes()
    package = env / "Lib" / "site-packages" / "oldpkg"
    (package / "__init__.py").write_bytes(b"version = 'new'\n")
    (package / "removed.py").unlink()
    (env / "empty-directory").rmdir()
    extra = env / "Lib" / "site-packages" / "newpkg"
    extra.mkdir()
    (extra / "installed.py").write_bytes(b"partially installed wheel")
    (extra / "empty").mkdir()
    with pytest.raises(runtime.EnvironmentSnapshotError, match="differs"):
        runtime.verify_environment(env, snapshot)
    result = runtime.restore_environment(env, snapshot)
    assert result["verified"] is True and result["copied"] == 2 and result["deleted"] == 1
    assert runtime._scan(env, include_cache=True) == receipt["inventory"]
    assert (env / "Scripts" / "old-launcher.exe").read_bytes() == original_launcher
    assert not extra.exists()


def test_file_directory_type_changes_restore_original_types(tree):
    _, env, snapshot = tree
    runtime.snapshot_environment(env, snapshot)
    expected_file = env / "Lib" / "site-packages" / "oldpkg" / "removed.py"
    expected_file.unlink()
    expected_file.mkdir()
    (expected_file / "new.py").write_bytes(b"nested")
    expected_dir = env / "empty-directory"
    expected_dir.rmdir()
    expected_dir.write_bytes(b"directory replaced by file")
    assert runtime.restore_environment(env, snapshot)["verified"]
    assert expected_file.read_bytes() == b"preserve removed file\n"
    assert expected_dir.is_dir()


def test_unchanged_running_python_is_never_replaced(tree, monkeypatch):
    _, env, snapshot = tree
    runtime.snapshot_environment(env, snapshot)
    original_replace = runtime.os.replace
    python = env / "Scripts" / "python.exe"
    inode = python.stat().st_ino
    (env / "pyvenv.cfg").write_bytes(b"pip wrote something")
    destinations = []

    def lock_python(source, target):
        destinations.append(Path(target))
        if Path(target) == python:
            raise PermissionError("running Python is locked")
        return original_replace(source, target)

    monkeypatch.setattr(runtime.os, "replace", lock_python)
    assert runtime.restore_environment(env, snapshot)["verified"]
    assert python not in destinations and python.stat().st_ino == inode


def test_changed_locked_python_prevents_success_and_can_retry(tree, monkeypatch):
    _, env, snapshot = tree
    runtime.snapshot_environment(env, snapshot)
    python = env / "Scripts" / "python.exe"
    python.write_bytes(b"wrong interpreter")
    original_replace = runtime.os.replace

    def fail(source, target):
        if Path(target) == python:
            raise PermissionError("file lock")
        return original_replace(source, target)

    monkeypatch.setattr(runtime.os, "replace", fail)
    with pytest.raises(runtime.EnvironmentSnapshotError, match="incomplete"):
        runtime.restore_environment(env, snapshot)
    assert python.read_bytes() == b"wrong interpreter"
    with pytest.raises(runtime.EnvironmentSnapshotError):
        runtime.verify_environment(env, snapshot)
    monkeypatch.setattr(runtime.os, "replace", original_replace)
    assert runtime.restore_environment(env, snapshot)["verified"]
    assert not list(env.rglob("*.restore-*"))


def test_regular_file_metadata_is_restored_without_replacing_identical_bytes(tree, monkeypatch):
    _, env, snapshot = tree
    file = env / "pyvenv.cfg"
    expected = file.stat()
    runtime.snapshot_environment(env, snapshot)
    os.utime(file, ns=(expected.st_atime_ns, expected.st_mtime_ns + 1_000_000_000))
    original_replace = runtime.os.replace

    def no_file_replace(source, target):
        assert Path(target) != file
        return original_replace(source, target)

    monkeypatch.setattr(runtime.os, "replace", no_file_replace)
    assert runtime.restore_environment(env, snapshot)["verified"]
    assert file.stat().st_mtime_ns == expected.st_mtime_ns


def test_generated_caches_are_ignored_when_live_and_cleared_on_restore(tree):
    _, env, snapshot = tree
    package = env / "Lib" / "site-packages" / "oldpkg"
    cache = package / "__pycache__"
    cache.mkdir()
    (cache / "__init__.cpython-312.pyc").write_bytes(b"old generated")
    (package / "__init__.pyc").write_bytes(b"old adjacent cache")
    (package / "sourceless.pyc").write_bytes(b"runtime sourceless bytecode")
    receipt = runtime.snapshot_environment(env, snapshot)
    assert not any("__pycache__" in name or name.endswith("/__init__.pyc")
                   for name in receipt["inventory"]["files"])
    assert "Lib/site-packages/oldpkg/sourceless.pyc" in receipt["inventory"]["files"]
    (cache / "__init__.cpython-312.pyc").write_bytes(b"new generated")
    (cache / "more.pyc").write_bytes(b"new generated")
    (package / "__init__.pyc").write_bytes(b"new adjacent cache")
    assert runtime.verify_environment(env, snapshot)["verified"]
    assert runtime.restore_environment(env, snapshot)["verified"]
    assert not cache.exists() and not (package / "__init__.pyc").exists()
    assert (package / "sourceless.pyc").read_bytes() == b"runtime sourceless bytecode"


@pytest.mark.parametrize("damage", ["payload", "metadata", "manifest", "marker", "missing-marker", "extra-file", "extra-cache"])
def test_corrupt_snapshot_is_rejected_before_any_environment_change(tree, damage):
    _, env, snapshot = tree
    runtime.snapshot_environment(env, snapshot)
    (env / "pyvenv.cfg").write_bytes(b"new requirements environment")
    if damage == "payload":
        (snapshot / "files" / "pyvenv.cfg").write_bytes(b"corrupt")
    elif damage == "metadata":
        file = snapshot / "files" / "pyvenv.cfg"
        os.utime(file, ns=(file.stat().st_atime_ns, file.stat().st_mtime_ns + 1_000_000_000))
    elif damage == "manifest":
        (snapshot / "manifest.json").write_bytes(b"{}")
    elif damage == "marker":
        (snapshot / "complete.json").write_bytes(b"{}")
    elif damage == "missing-marker":
        (snapshot / "complete.json").unlink()
    elif damage == "extra-file":
        (snapshot / "files" / "extra.py").write_bytes(b"uncommitted")
    elif damage == "extra-cache":
        (snapshot / "files" / "__pycache__").mkdir()
        (snapshot / "files" / "__pycache__" / "extra.pyc").write_bytes(b"uncommitted cache")
    before = runtime._scan(env, include_cache=True)
    with pytest.raises(runtime.EnvironmentSnapshotError):
        runtime.restore_environment(env, snapshot)
    assert runtime._scan(env, include_cache=True) == before


def test_failed_snapshot_commit_is_not_usable_and_keeps_original_environment(tree, monkeypatch):
    _, env, snapshot = tree
    before = runtime._scan(env, include_cache=True)
    original_replace = runtime.os.replace

    def fail_complete(source, target):
        if Path(target).name == "complete.json":
            raise OSError("simulated manifest commit failure")
        return original_replace(source, target)

    monkeypatch.setattr(runtime.os, "replace", fail_complete)
    with pytest.raises(runtime.EnvironmentSnapshotError, match="Snapshot failed"):
        runtime.snapshot_environment(env, snapshot)
    assert runtime._scan(env, include_cache=True) == before
    assert not (snapshot / "complete.json").exists()
    with pytest.raises(runtime.EnvironmentSnapshotError):
        runtime.restore_environment(env, snapshot)


def test_mid_copy_source_change_prevents_snapshot_commit(tree, monkeypatch):
    _, env, snapshot = tree
    original_copy = runtime._copy_file

    def change_source(source, target, expected):
        original_copy(source, target, expected)
        if source == env / "pyvenv.cfg":
            source.write_bytes(b"concurrent environment mutation")

    monkeypatch.setattr(runtime, "_copy_file", change_source)
    with pytest.raises(runtime.EnvironmentSnapshotError, match="changed during snapshot"):
        runtime.snapshot_environment(env, snapshot)
    assert not (snapshot / "complete.json").exists()


def test_delete_failure_prevents_success_before_replacing_runtime_files(tree, monkeypatch):
    _, env, snapshot = tree
    runtime.snapshot_environment(env, snapshot)
    extra = env / "new-file.py"
    extra.write_bytes(b"new")
    original_unlink = Path.unlink

    def fail_unlink(path, *args, **kwargs):
        if path == extra:
            raise PermissionError("file locked for deletion")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(runtime.EnvironmentSnapshotError, match="incomplete"):
        runtime.restore_environment(env, snapshot)
    assert extra.exists()


@pytest.mark.parametrize("location", ["outside", "different-project", "traversal", "wrong-env-name"])
def test_rejects_unapproved_snapshot_or_environment_boundaries(tree, location):
    base, env, snapshot = tree
    if location == "outside":
        snapshot = base / "elsewhere" / "venv"
    elif location == "different-project":
        snapshot = base / "other-project" / ".deploy-rollbacks" / "stamp" / "venv"
    elif location == "traversal":
        snapshot = base / ".deploy-rollbacks" / ".." / ".deploy-rollbacks" / "stamp" / "venv"
    else:
        alternate = base / "other-env"
        alternate.mkdir()
        env = alternate
    with pytest.raises(runtime.EnvironmentSnapshotError):
        runtime.snapshot_environment(env, snapshot)
    assert not snapshot.exists()


def _directory_link(link: Path, target: Path):
    if os.name == "nt":
        result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
                                capture_output=True, text=True)
        if result.returncode:
            pytest.skip("Directory junction unavailable: " + result.stderr)
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("location", ["environment-root", "child", "cache", "snapshot-root", "rollback-parent"])
def test_reparse_point_escape_fails_closed_without_touching_external_target(tree, location):
    base, env, snapshot = tree
    outside = base / "external"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"must remain unchanged")
    if location == "environment-root":
        alternate_env = base / "other-project" / ".venv"
        alternate_env.parent.mkdir()
        _directory_link(alternate_env, env)
        env = alternate_env
        snapshot = env.parent / ".deploy-rollbacks" / "stamp" / "venv"
        action = lambda: runtime.snapshot_environment(env, snapshot)
    elif location in {"child", "cache"}:
        runtime.snapshot_environment(env, snapshot)
        _directory_link(env / ("__pycache__" if location == "cache" else "new-link"), outside)
        action = lambda: runtime.restore_environment(env, snapshot)
    elif location == "snapshot-root":
        runtime.snapshot_environment(env, snapshot)
        alternate = snapshot.parent / "saved-snapshot"
        snapshot.rename(alternate)
        _directory_link(snapshot, alternate)
        action = lambda: runtime.restore_environment(env, snapshot)
    else:
        _directory_link(base / ".deploy-rollbacks", outside)
        action = lambda: runtime.snapshot_environment(env, snapshot)
    with pytest.raises(runtime.EnvironmentSnapshotError, match="Link/reparse"):
        action()
    assert sentinel.read_bytes() == b"must remain unchanged"


def test_reparse_attribute_is_rejected_even_when_not_a_symbolic_link(tree, monkeypatch):
    _, env, _ = tree
    original_lstat = Path.lstat

    def reparse(path, *args, **kwargs):
        original = original_lstat(path, *args, **kwargs)
        if path == env:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_file_attributes=0x400)
        return original

    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(runtime.EnvironmentSnapshotError, match="Link/reparse"):
        runtime._checked_path(env)


def test_existing_snapshot_evidence_cannot_be_overwritten(tree):
    _, env, snapshot = tree
    runtime.snapshot_environment(env, snapshot)
    original = (snapshot / "manifest.json").read_bytes()
    with pytest.raises(runtime.EnvironmentSnapshotError, match="already exists"):
        runtime.snapshot_environment(env, snapshot)
    assert (snapshot / "manifest.json").read_bytes() == original


def test_final_verification_failure_is_not_reported_as_restored(tree, monkeypatch):
    _, env, snapshot = tree
    runtime.snapshot_environment(env, snapshot)
    (env / "pyvenv.cfg").write_bytes(b"new")

    def fail(*args):
        raise runtime.EnvironmentSnapshotError("injected final verification failure")

    monkeypatch.setattr(runtime, "verify_environment", fail)
    with pytest.raises(runtime.EnvironmentSnapshotError, match="final verification"):
        runtime.restore_environment(env, snapshot)
