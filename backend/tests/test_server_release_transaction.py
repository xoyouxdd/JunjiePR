"""Offline deployment transactions: temporary files and injected boundary actions."""

from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import sqlite3
import stat
import sys
from types import SimpleNamespace
import uuid
import zipfile

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
SCRIPTS = REPOSITORY / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("server_release_under_test", SCRIPTS / "server_release.py")
release = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(release)

COMMIT = "a" * 40
OLD = "2026.10.09.2"
NEW = "2026.10.09.3"


@pytest.fixture(autouse=True)
def forbid_real_external_actions(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Tests must not run production processes, tasks, or HTTP")

    monkeypatch.setattr(release, "run", forbidden)
    monkeypatch.setattr(release.urllib.request, "urlopen", forbidden)


@pytest.fixture
def offline_base():
    # Plain mkdir inherits the workspace ACL; pytest's mode-700 Windows tmp_path
    # is inaccessible to the sandbox token. Preserve each fault-injection tree.
    base = REPOSITORY / "output/refactor-validation/phase3-transaction-sandbox" / uuid.uuid4().hex
    base.mkdir(parents=True)
    assert base.resolve().is_relative_to((REPOSITORY / "output").resolve())
    return base


@pytest.fixture
def live(offline_base):
    root = offline_base / "live"
    for name in ("app", ".venv/Scripts", ".venv/Lib/site-packages", "data_v2", ".deploy-incoming"):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "app/main.py").write_bytes(b"old app")
    (root / "requirements.txt").write_bytes(b"package==1\n")
    (root / "release-manifest.json").write_text(json.dumps({"app_version": OLD}), encoding="utf-8")
    (root / "RELEASE-NOTES.md").write_bytes(b"old release notes")
    (root / ".venv/Scripts/python.exe").write_bytes(b"locked interpreter")
    (root / ".venv/pyvenv.cfg").write_bytes(b"home=original\n")
    (root / ".venv/Lib/site-packages/dependency.py").write_bytes(b"old dependency")
    (root / ".venv/Lib/site-packages/removed.py").write_bytes(b"old removed dependency")
    (root / "data_v2/recognition_v2.db").write_bytes(b"online database")
    return root


def contents(path):
    return {file.relative_to(path).as_posix(): file.read_bytes() for file in path.rglob("*") if file.is_file()}


def package_content(changed=True):
    return {
        "backend/app/main.py": b"new app",
        "backend/requirements.txt": b"package==2\n" if changed else b"package==1\n",
        **{"scripts/" + name: (SCRIPTS / name).read_bytes()
           for name in ("server_release.py", "deployment_runtime.py", "Deploy-RecognitionRelease.ps1")},
    }


def build_package(root, *, changed=True, modify=None, extra=None, symlink=None, duplicate=None):
    files = package_content(changed)
    if modify:
        modify(files)
    manifest = {"app_version": NEW, "git_commit": COMMIT,
                "files": [{"path": name, "size": len(data), "sha256": release.sha(data)}
                          for name, data in files.items()]}
    package = root / ".deploy-incoming/release.zip"
    with zipfile.ZipFile(package, "w") as archive:
        for name, data in files.items():
            if name == symlink:
                entry = zipfile.ZipInfo(name)
                entry.create_system = 3
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(entry, data)
            else:
                archive.writestr(name, data)
        archive.writestr("release-manifest.json", json.dumps(manifest))
        archive.writestr("RELEASE-NOTES.md", b"new release notes")
        if extra:
            # ZipInfo normalizes backslashes on Windows; preserve the hostile
            # raw member spelling to test the reader rather than the writer.
            entry = zipfile.ZipInfo("placeholder")
            entry.filename = entry.orig_filename = extra
            archive.writestr(entry, b"not in manifest")
        if duplicate:
            archive.writestr(duplicate, b"duplicate")
    return package, release.sha(package.read_bytes())


class OfflineActions(release.DeploymentActions):
    def __init__(self, root, fail=None, watchdog=True):
        self.root, self.fail, self.watchdog = root, fail, watchdog
        self.events = []

    def backup_database(self, source, destination):
        self.events.append("backup")
        if self.fail == "backup":
            raise RuntimeError("backup failed")
        destination.write_bytes(source.read_bytes())

    def watchdog_enabled(self):
        self.events.append("watchdog_query")
        return self.watchdog

    def validate_service_task(self):
        self.events.append("service_task")

    def disable_watchdog(self):
        self.events.append("watchdog_disable")

    def enable_watchdog(self):
        self.events.append("watchdog_enable")

    def stop_app(self):
        self.events.append("stop")
        if self.fail == "stop":
            raise RuntimeError("port still listening")
        if self.fail == "drift" and self.events.count("stop") == 1:
            (self.root / ".venv/Lib/site-packages/dependency.py").write_bytes(b"unexpected runtime change")

    def start_app(self, version):
        self.events.append("start:" + version)
        if self.fail == "internal" and version == NEW:
            raise RuntimeError("new internal health failed")
        if self.fail == "old_health" and version == OLD:
            raise RuntimeError("old internal health failed")

    def external_health(self, version):
        self.events.append("external:" + version)
        if self.fail in {"external", "restore", "old_health", "rollback_stop"}:
            if self.fail == "rollback_stop":
                self.fail = "stop"
            raise RuntimeError("new external health failed")

    def install_requirements(self, python, requirements):
        self.events.append("pip")
        assert python == self.root / ".venv/Scripts/python.exe"
        assert requirements == self.root / "requirements.txt"
        (self.root / ".venv/Lib/site-packages/dependency.py").write_bytes(b"new dependency")
        (self.root / ".venv/Lib/site-packages/added.py").write_bytes(b"partially added dependency")
        (self.root / ".venv/Lib/site-packages/removed.py").unlink()
        (self.root / ".venv/pyvenv.cfg").write_bytes(b"mutated runtime metadata")
        if self.fail == "pip":
            raise RuntimeError("pip partially failed")

    def snapshot_environment(self, environment, snapshot):
        self.events.append("snapshot")
        if self.fail == "snapshot":
            raise RuntimeError("snapshot failed")
        return super().snapshot_environment(environment, snapshot)

    def verify_environment(self, environment, snapshot):
        self.events.append("verify_environment")
        return super().verify_environment(environment, snapshot)

    def restore_environment(self, environment, snapshot):
        self.events.append("restore_environment")
        if self.fail == "restore":
            raise RuntimeError("offline restore failed")
        return super().restore_environment(environment, snapshot)


def deploy(root, actions, **kwargs):
    package, digest = build_package(root, **kwargs)
    return release.deploy_release(package, COMMIT, digest, root=root, actions=actions)


def assert_old_release(root):
    assert (root / "app/main.py").read_bytes() == b"old app"
    assert (root / "requirements.txt").read_bytes() == b"package==1\n"
    assert json.loads((root / "release-manifest.json").read_text(encoding="utf-8"))["app_version"] == OLD
    assert (root / "RELEASE-NOTES.md").read_bytes() == b"old release notes"


def test_preflight_uses_exact_validation_without_live_side_effects(live):
    package, digest = build_package(live)
    baseline = contents(live)
    actions = OfflineActions(live)
    manifest = release.deploy_release(package, COMMIT, digest, root=live, actions=actions, preflight=True)
    assert manifest["app_version"] == NEW
    assert actions.events == []
    assert contents(live) == baseline
    assert not (live / ".deploy.lock").exists()


@pytest.mark.parametrize("name", [
    "../escape.py", "/absolute.py", "C:/absolute.py", "backend/app/../../escape.py",
    "backend\\app\\escape.py", "backend/app/module.py:secret", "backend/app/CON.py",
    "backend/app/invalid?.py", "backend/app/invalid\x00suffix.py",
    "backend/app/alias.py.", "backend/app//alias.py", "backend/app/./alias.py",
    "backend/data_v2/recognition_v2.db", "scripts/not-approved.py",
])
def test_preflight_rejects_unsafe_archive_paths_without_boundary_actions(live, name):
    package, digest = build_package(live, extra=name)
    actions = OfflineActions(live)
    with pytest.raises(RuntimeError, match="ZIP path"):
        release.deploy_release(package, COMMIT, digest, root=live, actions=actions, preflight=True)
    assert actions.events == []
    assert_old_release(live)


@pytest.mark.parametrize("script", ["server_release.py", "deployment_runtime.py"])
def test_preflight_rejects_executor_or_helper_from_different_release(live, script):
    package, digest = build_package(live, modify=lambda files: files.__setitem__("scripts/" + script, b"different code"))
    with pytest.raises(RuntimeError, match="Executing deployment script differs"):
        release.deploy_release(package, COMMIT, digest, root=live, preflight=True)
    assert_old_release(live)


def test_preflight_rejects_zip_symlink_and_case_collisions(live):
    package, digest = build_package(live, symlink="backend/app/main.py")
    with pytest.raises(RuntimeError, match="regular file"):
        release.deploy_release(package, COMMIT, digest, root=live, preflight=True)
    package, digest = build_package(live, extra="backend/app/MAIN.py")
    with pytest.raises(RuntimeError, match="case-colliding"):
        release.deploy_release(package, COMMIT, digest, root=live, preflight=True)


def test_preflight_rejects_missing_dependency_helper(live):
    package, digest = build_package(live, modify=lambda files: files.pop("scripts/deployment_runtime.py"))
    with pytest.raises(RuntimeError, match="Required release"):
        release.deploy_release(package, COMMIT, digest, root=live, preflight=True)


def test_preflight_rejects_hash_commit_and_incoming_location(live):
    package, digest = build_package(live)
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        release.deploy_release(package, COMMIT, "0" * 64, root=live, preflight=True)
    with pytest.raises(RuntimeError, match="commit or version mismatch"):
        release.deploy_release(package, "b" * 40, digest, root=live, preflight=True)
    outside = live / "elsewhere.zip"
    outside.write_bytes(package.read_bytes())
    with pytest.raises(RuntimeError, match="incoming directory"):
        release.deploy_release(outside, COMMIT, digest, root=live, preflight=True)


@pytest.mark.parametrize("field,value", [("size", 1000), ("sha256", "0" * 64)])
def test_preflight_rejects_manifest_hash_or_size_mismatch(live, field, value):
    package, _ = build_package(live)
    with zipfile.ZipFile(package) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries["release-manifest.json"])
    manifest["files"][0][field] = value
    entries["release-manifest.json"] = json.dumps(manifest).encode("utf-8")
    with zipfile.ZipFile(package, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    with pytest.raises(RuntimeError, match="Manifest hash/size mismatch"):
        release.deploy_release(package, COMMIT, release.sha(package.read_bytes()), root=live, preflight=True)


def test_changed_requirements_snapshot_before_stop_and_success_commits(live):
    actions = OfflineActions(live)
    receipt = deploy(live, actions)
    assert receipt["status"] == "deployed"
    assert (live / "app/main.py").read_bytes() == b"new app"
    assert (live / "requirements.txt").read_bytes() == b"package==2\n"
    assert (live / ".venv/Lib/site-packages/dependency.py").read_bytes() == b"new dependency"
    assert actions.events.index("snapshot") < actions.events.index("stop")
    assert actions.events.index("stop") < actions.events.index("verify_environment") < actions.events.index("pip")
    assert actions.events[-1] == "watchdog_enable"
    assert (Path(receipt["environment_snapshot"]) / "complete.json").is_file()
    assert len(list((live / "backups").glob("*.db"))) == 1


@pytest.mark.parametrize("failure", ["pip", "internal", "external"])
def test_partial_pip_and_health_failures_restore_environment_and_release(live, failure):
    baseline = contents(live / ".venv")
    actions = OfflineActions(live, failure)
    with pytest.raises(release.DeploymentError) as raised:
        deploy(live, actions)
    assert_old_release(live)
    assert contents(live / ".venv") == baseline
    assert raised.value.rollback_errors == ()
    assert raised.value.__cause__ is raised.value.original_error
    assert actions.events.index("restore_environment") < actions.events.index("start:" + OLD)
    assert actions.events[-1] == "watchdog_enable"
    receipt = json.loads(raised.value.receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "rolled_back"
    assert receipt["original_failure"]["message"] == str(raised.value.original_error)
    assert receipt["rollback_failures"] == []


def test_unchanged_requirements_skip_pip_and_snapshot_on_success_and_rollback(live):
    baseline = contents(live / ".venv")
    actions = OfflineActions(live, "external")
    with pytest.raises(release.DeploymentError):
        deploy(live, actions, changed=False)
    assert_old_release(live)
    assert contents(live / ".venv") == baseline
    assert not {"pip", "snapshot", "restore_environment", "verify_environment"}.intersection(actions.events)
    successful = OfflineActions(live, watchdog=False)
    receipt = deploy(live, successful, changed=False)
    assert receipt["status"] == "deployed"
    assert "pip" not in successful.events
    assert "watchdog_enable" not in successful.events
    assert successful.events.count("watchdog_disable") == 1


@pytest.mark.parametrize("failure", ["pip", "external"])
def test_rollback_never_enables_an_originally_disabled_watchdog(live, failure):
    actions = OfflineActions(live, failure, watchdog=False)
    with pytest.raises(release.DeploymentError) as raised:
        deploy(live, actions)
    assert_old_release(live)
    assert "start:" + OLD in actions.events
    assert "watchdog_enable" not in actions.events
    receipt = json.loads(raised.value.receipt_path.read_text(encoding="utf-8"))
    assert receipt["watchdog_original_enabled"] is False
    assert receipt["watchdog_state"] == "disabled"


def test_failed_environment_restore_preserves_errors_and_withholds_restart(live):
    actions = OfflineActions(live, "restore")
    with pytest.raises(release.DeploymentError) as raised:
        deploy(live, actions)
    assert_old_release(live)
    assert "new external health failed" in str(raised.value.original_error)
    assert [(step, str(error)) for step, error in raised.value.rollback_errors] == [("environment", "offline restore failed")]
    assert "start:" + OLD not in actions.events
    assert "watchdog_enable" not in actions.events
    receipt = json.loads(raised.value.receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "rollback_failed"
    assert receipt["watchdog_left_disabled"] is True
    assert receipt["rollback_failures"][0]["step"] == "environment"


@pytest.mark.parametrize("failure", ["stop", "rollback_stop"])
def test_unconfirmed_port_release_blocks_all_directory_and_environment_restores(live, failure):
    baseline = contents(live / ".venv")
    actions = OfflineActions(live, failure)
    with pytest.raises(release.DeploymentError) as raised:
        deploy(live, actions)
    assert "restore_environment" not in actions.events
    assert "start:" + OLD not in actions.events
    assert "watchdog_enable" not in actions.events
    assert raised.value.rollback_errors[0][0] == "stop"
    if failure == "stop":
        assert_old_release(live)
        assert contents(live / ".venv") == baseline
        assert "pip" not in actions.events
        assert not list((live / ".deploy-rollbacks").glob("*/app"))
    else:
        assert (live / "app/main.py").read_bytes() == b"new app"
        assert list((live / ".deploy-rollbacks").glob("*/app/main.py"))[0].read_bytes() == b"old app"


def test_snapshot_failure_keeps_service_untouched_and_preserves_failure(live):
    actions = OfflineActions(live, "snapshot")
    with pytest.raises(release.DeploymentError) as raised:
        deploy(live, actions)
    assert_old_release(live)
    assert actions.events == ["backup", "snapshot"]
    receipt = json.loads(raised.value.receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "preparation_failed"
    assert receipt["original_failure"]["message"] == "snapshot failed"


def test_runtime_drift_after_snapshot_aborts_before_swapping_or_pip(live):
    actions = OfflineActions(live, "drift")
    with pytest.raises(release.DeploymentError):
        deploy(live, actions)
    assert_old_release(live)
    assert "pip" not in actions.events
    assert "restore_environment" not in actions.events
    assert actions.events[-2:] == ["start:" + OLD, "watchdog_enable"]


def test_second_rename_failure_still_restores_original_app(live, monkeypatch):
    original = Path.rename

    def fail_install(path, target):
        if ".deploy-staging" in path.parts and path.name == "app":
            raise OSError("new app move failed")
        return original(path, target)

    monkeypatch.setattr(Path, "rename", fail_install)
    actions = OfflineActions(live)
    with pytest.raises(release.DeploymentError) as raised:
        deploy(live, actions)
    assert_old_release(live)
    assert raised.value.rollback_errors == ()
    assert "pip" not in actions.events
    assert "start:" + OLD in actions.events


def test_old_health_failure_keeps_watchdog_disabled(live):
    actions = OfflineActions(live, "old_health")
    with pytest.raises(release.DeploymentError) as raised:
        deploy(live, actions)
    assert_old_release(live)
    assert raised.value.rollback_errors[0][0] == "health"
    assert "watchdog_enable" not in actions.events


def test_partial_metadata_publication_restores_all_previous_release_files(live, monkeypatch):
    copy = release.shutil.copy2

    def fail_release_notes(source, destination):
        if ".deploy-staging" in Path(source).parts and Path(source).name == "RELEASE-NOTES.md":
            raise OSError("release notes copy failed after manifest publication")
        return copy(source, destination)

    monkeypatch.setattr(release.shutil, "copy2", fail_release_notes)
    baseline = contents(live / ".venv")
    actions = OfflineActions(live)
    with pytest.raises(release.DeploymentError) as raised:
        deploy(live, actions)
    assert_old_release(live)
    assert contents(live / ".venv") == baseline
    assert raised.value.rollback_errors == ()
    assert "release notes copy failed" in str(raised.value.original_error)


def test_receipt_failure_after_watchdog_enable_disables_it_before_rollback(live, monkeypatch):
    write = release._write_receipt
    calls = 0

    def fail_final_receipt(path, receipt):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("final receipt failed")
        return write(path, receipt)

    monkeypatch.setattr(release, "_write_receipt", fail_final_receipt)
    actions = OfflineActions(live)
    with pytest.raises(release.DeploymentError) as raised:
        deploy(live, actions)
    assert_old_release(live)
    enable = actions.events.index("watchdog_enable")
    assert actions.events[enable + 1:enable + 3] == ["watchdog_disable", "stop"]
    assert raised.value.rollback_errors == ()
    assert actions.events[-1] == "watchdog_enable"


def test_lock_rejects_concurrent_transaction_and_releases_after_failure(live):
    with release.deployment_lock(live):
        with pytest.raises(RuntimeError, match="holds the live-root lock"):
            with release.deployment_lock(live):
                pytest.fail("second transaction acquired the same live-root lock")
    with release.deployment_lock(live):
        pass


def test_online_backup_reads_existing_database_and_is_queryable(offline_base):
    source, destination = offline_base / "live.db", offline_base / "backup.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE evidence(value TEXT)")
        connection.execute("INSERT INTO evidence VALUES ('preserved')")
    release.backup_database(source, destination)
    with sqlite3.connect(destination) as connection:
        assert connection.execute("SELECT value FROM evidence").fetchall() == [("preserved",)]
    with pytest.raises(sqlite3.OperationalError):
        release.backup_database(offline_base / "missing.db", offline_base / "must-not-create.db")
    assert not (offline_base / "missing.db").exists()


@pytest.mark.parametrize("result", [
    SimpleNamespace(returncode=1, stdout="task not found", stderr="not found"),
    SimpleNamespace(returncode=1, stdout="", stderr="access denied"),
    SimpleNamespace(returncode=0, stdout="not XML", stderr=""),
    SimpleNamespace(returncode=0, stdout="<Task><Settings/></Task>", stderr=""),
    SimpleNamespace(returncode=0, stdout="<Task><Settings><Enabled>unknown</Enabled></Settings></Task>", stderr=""),
])
def test_unknown_or_missing_watchdog_state_fails_closed(live, monkeypatch, result):
    monkeypatch.setattr(release, "run", lambda *args, **kwargs: result)
    actions = OfflineActions(live)
    actions.watchdog_enabled = release.DeploymentActions.watchdog_enabled.__get__(actions)
    with pytest.raises(release.DeploymentError) as raised:
        deploy(live, actions)
    assert_old_release(live)
    assert "stop" not in actions.events
    assert "pip" not in actions.events
    assert "watchdog_enable" not in actions.events
    assert "watchdog" in str(raised.value.original_error).lower()


@pytest.mark.parametrize("enabled", ["true", "false"])
def test_watchdog_xml_parses_exact_enabled_state(monkeypatch, enabled):
    xml = f'<Task xmlns="urn:task"><Settings><Enabled>{enabled}</Enabled></Settings></Task>'
    def command(*args, **kwargs):
        assert args == ("schtasks.exe", "/Query", "/TN", release.WATCHDOG, "/XML")
        return SimpleNamespace(returncode=0, stdout=xml, stderr="")
    monkeypatch.setattr(release, "run", command)
    assert release.DeploymentActions().watchdog_enabled() is (enabled == "true")


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("task_name", [release.TASK, release.WATCHDOG])
def test_missing_xml_enabled_reads_registered_task_boolean(monkeypatch, enabled, task_name):
    commands = []

    def command(*args, **kwargs):
        commands.append(args)
        assert kwargs == {"check": False}
        if args[0] == "schtasks.exe":
            assert args == ("schtasks.exe", "/Query", "/TN", task_name, "/XML")
            return SimpleNamespace(returncode=0, stdout='<Task xmlns="urn:task"><Settings/></Task>', stderr="")
        assert args[:4] == ("powershell.exe", "-NoProfile", "-NonInteractive", "-Command")
        assert f"GetTask('{task_name}')" in args[4]
        assert "$task.Enabled" in args[4] and "$task.State" not in args[4]
        return SimpleNamespace(returncode=0, stdout=json.dumps({"task": "\\" + task_name, "enabled": enabled}), stderr="")

    monkeypatch.setattr(release, "run", command)
    assert release.task_enabled(task_name) is enabled
    assert len(commands) == 2


@pytest.mark.parametrize("actual", [
    SimpleNamespace(returncode=1, stdout="", stderr="access denied"),
    SimpleNamespace(returncode=0, stdout="", stderr=""),
    SimpleNamespace(returncode=0, stdout="not JSON", stderr=""),
    SimpleNamespace(returncode=0, stdout="null", stderr=""),
    SimpleNamespace(returncode=0, stdout="[]", stderr=""),
    *[SimpleNamespace(returncode=0, stdout=json.dumps(state), stderr="") for state in [
        {"task": "\\" + release.WATCHDOG},
        {"task": "\\" + release.WATCHDOG, "enabled": None},
        {"task": "\\" + release.WATCHDOG, "enabled": "true"},
        {"task": "\\" + release.WATCHDOG, "enabled": 1},
        {"task": "\\different-task", "enabled": True},
    ]],
])
def test_unreadable_registered_task_state_prevents_app_stop(live, monkeypatch, actual):
    def command(*args, **kwargs):
        if args[0] == "schtasks.exe":
            return SimpleNamespace(returncode=0, stdout="<Task><Settings/></Task>", stderr="")
        return actual

    monkeypatch.setattr(release, "run", command)
    actions = OfflineActions(live)
    actions.watchdog_enabled = release.DeploymentActions.watchdog_enabled.__get__(actions)
    with pytest.raises(release.DeploymentError) as raised:
        deploy(live, actions)
    assert_old_release(live)
    assert "stop" not in actions.events and "watchdog_disable" not in actions.events
    assert "watchdog_enable" not in actions.events and "pip" not in actions.events
    assert release.WATCHDOG in str(raised.value.original_error)


def test_disabled_application_from_registered_task_api_blocks_watchdog_changes(live, monkeypatch):
    def command(*args, **kwargs):
        if args[0] == "schtasks.exe":
            return SimpleNamespace(returncode=0, stdout="<Task><Settings/></Task>", stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"task": "\\" + release.TASK, "enabled": False}), stderr="")

    monkeypatch.setattr(release, "run", command)
    actions = OfflineActions(live)
    actions.validate_service_task = release.DeploymentActions.validate_service_task.__get__(actions)
    with pytest.raises(release.DeploymentError) as raised:
        deploy(live, actions)
    assert_old_release(live)
    assert "disabled" in str(raised.value.original_error)
    assert "watchdog_query" not in actions.events and "watchdog_disable" not in actions.events
    assert "stop" not in actions.events


@pytest.mark.parametrize("xml", ["not XML", "<Task><Settings><Enabled>unknown</Enabled></Settings></Task>"])
def test_invalid_task_xml_never_falls_back_to_api(monkeypatch, xml):
    def command(*args, **kwargs):
        assert args[0] == "schtasks.exe"
        return SimpleNamespace(returncode=0, stdout=xml, stderr="")
    monkeypatch.setattr(release, "run", command)
    with pytest.raises(RuntimeError, match="Cannot establish"):
        release.task_enabled(release.TASK)


def test_disable_watchdog_waits_for_running_instance_to_end(monkeypatch):
    commands, states = [], iter([
        SimpleNamespace(returncode=3, stdout="", stderr=""),
        SimpleNamespace(returncode=0, stdout="WATCHDOG_QUIESCED\n", stderr=""),
    ])

    def command(*args, **kwargs):
        commands.append((args, kwargs))
        return next(states) if args[0] == "powershell.exe" else SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(release, "run", command)
    monkeypatch.setattr(release.time, "sleep", lambda seconds: None)
    release.DeploymentActions().disable_watchdog()
    assert commands[0][0] == ("schtasks.exe", "/Change", "/TN", release.WATCHDOG, "/Disable")
    assert commands[1][0] == ("schtasks.exe", "/End", "/TN", release.WATCHDOG)
    checks = [args[-1] for args, _ in commands if args[0] == "powershell.exe"]
    assert len(checks) == 2
    assert "$task.GetInstances(0)" in checks[0]
    assert "$instances.Count" in checks[0]
    assert "GetRunningTasks" not in checks[0] and "Where-Object" not in checks[0]
    assert "$task.Enabled" in checks[0]
    assert "$task.State -eq 2" in checks[0] and "$task.State -eq 4" in checks[0]
    assert "\\RecognitionCardSystemWatchdog" in checks[0]


def test_unreadable_watchdog_instance_count_prevents_app_stop(live, monkeypatch):
    def command(*args, **kwargs):
        if args[0] == "powershell.exe":
            return SimpleNamespace(returncode=1, stdout="", stderr="Invalid watchdog instance count")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(release, "run", command)
    actions = OfflineActions(live)
    actions.disable_watchdog = release.DeploymentActions.disable_watchdog.__get__(actions)
    with pytest.raises(release.DeploymentError) as raised:
        deploy(live, actions)
    assert_old_release(live)
    assert "stop" not in actions.events and "pip" not in actions.events
    assert "watchdog_enable" not in actions.events
    assert "Invalid watchdog instance count" in str(raised.value.original_error)
    assert raised.value.rollback_errors[0][0] == "watchdog_disable"


def test_watchdog_that_never_quiesces_prevents_app_stop(live, monkeypatch):
    def command(*args, **kwargs):
        if args[0] == "powershell.exe":
            return SimpleNamespace(returncode=3, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(release, "run", command)
    monkeypatch.setattr(release.time, "sleep", lambda seconds: None)
    actions = OfflineActions(live)
    actions.disable_watchdog = release.DeploymentActions.disable_watchdog.__get__(actions)
    with pytest.raises(release.DeploymentError) as raised:
        deploy(live, actions)
    assert_old_release(live)
    assert "stop" not in actions.events
    assert "pip" not in actions.events
    assert "watchdog_enable" not in actions.events
    assert raised.value.rollback_errors[0][0] == "watchdog_disable"


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_disabled_service_task_fails_before_touching_watchdog(live, monkeypatch, missing):
    result = (SimpleNamespace(returncode=1, stdout="", stderr="task not found") if missing else
              SimpleNamespace(returncode=0, stdout="<Task><Settings><Enabled>false</Enabled></Settings></Task>", stderr=""))
    monkeypatch.setattr(release, "run", lambda *args, **kwargs: result)
    actions = OfflineActions(live)
    actions.validate_service_task = release.DeploymentActions.validate_service_task.__get__(actions)
    with pytest.raises(release.DeploymentError) as raised:
        deploy(live, actions)
    assert_old_release(live)
    assert "watchdog_query" not in actions.events
    assert "watchdog_disable" not in actions.events
    assert "stop" not in actions.events
    assert "pip" not in actions.events
    assert release.TASK in str(raised.value.original_error)


def test_helper_loader_executes_current_source_without_creating_cache(offline_base):
    helper = offline_base / "deployment_runtime.py"
    source = b"_MANIFEST = 'manifest.jsox'\n"
    helper.write_bytes(source)
    cache = offline_base / "__pycache__"
    cache.mkdir()
    (cache / "deployment_runtime.stale.pyc").write_bytes(b"unusable stale cache must be ignored")
    before = contents(offline_base)
    module = release._load_runtime_source(helper)
    assert module._MANIFEST == "manifest.jsox"
    assert module.__deployment_source_sha__ == hashlib.sha256(source).hexdigest().upper()
    assert contents(offline_base) == before


def test_changed_helper_after_loading_is_rejected_even_if_package_matches_new_source(live, monkeypatch):
    helper = live / ".deploy-incoming/deployment_runtime.py"
    before, after = b"_MANIFEST = 'manifest.json'\n", b"_MANIFEST = 'manifest.jsox'\n"
    helper.write_bytes(before)
    loaded = release._load_runtime_source(helper)
    helper.write_bytes(after)
    monkeypatch.setattr(release, "deployment_runtime", loaded)
    package, digest = build_package(live, modify=lambda files: files.__setitem__("scripts/deployment_runtime.py", after))
    with pytest.raises(RuntimeError, match="Executing deployment script differs"):
        release.deploy_release(package, COMMIT, digest, root=live, preflight=True)


def test_stdout_disconnect_does_not_interrupt_restoration(live, monkeypatch):
    def disconnected(*args, **kwargs):
        raise BrokenPipeError("SSH stdout disconnected")

    monkeypatch.setattr(release, "print", disconnected, raising=False)
    actions = OfflineActions(live, "pip")
    with pytest.raises(release.DeploymentError) as raised:
        deploy(live, actions)
    assert_old_release(live)
    assert str(raised.value.original_error) == "pip partially failed"
    assert raised.value.rollback_errors == ()
    assert actions.events[-1] == "watchdog_enable"
