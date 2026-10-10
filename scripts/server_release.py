"""Validated deployment transaction for the fixed production layout.

The CLI remains package/commit/package_sha [--preflight]. Tests inject actions
and a temporary root; the CLI never accepts an alternate production root.
"""

from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import sqlite3
import ssl
import stat
import subprocess
import time
from types import ModuleType
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile

def _load_runtime_source(path: Path):
    """Execute precisely the hashed helper source, bypassing stale .pyc caches."""
    source = path.read_bytes()
    module = ModuleType("deployment_runtime")
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec"), module.__dict__)
    module.__deployment_source_sha__ = hashlib.sha256(source).hexdigest().upper()
    return module


_EXECUTING_SCRIPT_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest().upper()
deployment_runtime = _load_runtime_source(Path(__file__).with_name("deployment_runtime.py"))

ROOT = Path(r"C:\Server\zhaojunjie\recognition-card-system")
TASK = "RecognitionCardSystem"
WATCHDOG = "RecognitionCardSystemWatchdog"
RELEASE_SCRIPTS = {
    "scripts/Deploy-RecognitionRelease.ps1",
    "scripts/server_release.py",
    "scripts/deployment_runtime.py",
}
METADATA_FILES = ("requirements.txt", "release-manifest.json", "RELEASE-NOTES.md")


def log(*parts):
    # An SSH/stdout disconnect must not interrupt restoration of a stopped app.
    # The transaction receipt remains the durable failure record.
    try:
        print(*parts, flush=True)
    except OSError:
        pass


def run(*args, check=True):
    p = subprocess.run(args, capture_output=True, text=True, errors="replace")
    if check and p.returncode:
        raise RuntimeError(f"Command failed ({p.returncode}): {args[0]} {args[1:3]}: {p.stdout} {p.stderr}")
    return p


def sha(data):
    return hashlib.sha256(data).hexdigest().upper()


def health(url, version, external=False, seconds=60):
    context = ssl._create_unverified_context() if external else None
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=8, context=context) as response:
                body = json.load(response)
            if body.get("ok") is True and body.get("version") == version:
                return body
            last = body
        except Exception as exc:
            last = str(exc)
        time.sleep(2)
    raise RuntimeError(f"Health failed: {url}, expected={version}, last={last}")


def port_pid():
    p = run("netstat.exe", "-ano", "-p", "TCP")
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[1].endswith(":18082") and parts[3] == "LISTENING":
            return int(parts[4])
    return None


def stop_app():
    run("schtasks.exe", "/End", "/TN", TASK, check=False)
    for _ in range(10):
        if port_pid() is None:
            return
        time.sleep(1)
    pid = port_pid()
    if pid is not None:
        cmd = f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"
        process = run("powershell.exe", "-NoProfile", "-Command", cmd)
        if "uvicorn app.main:app" not in process.stdout or "--port 18082" not in process.stdout:
            raise RuntimeError(f"Port 18082 process is not the expected app: {pid}")
        run("taskkill.exe", "/PID", str(pid), "/F")
    for _ in range(20):
        if port_pid() is None:
            return
        time.sleep(1)
    raise RuntimeError("Application did not release port 18082")


def start_app(version):
    run("schtasks.exe", "/Run", "/TN", TASK)
    health("http://127.0.0.1:18082/health", version)


def approved_path(root: Path, path: Path) -> Path:
    """Reject escaping paths and existing symlink/junction ancestors."""
    root = Path(root).absolute()
    path = Path(path).absolute()
    if root.resolve() != root:
        raise RuntimeError("Live root must not be a symlink or junction")
    try:
        relative = path.relative_to(root)
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"Path is outside approved live root: {path}") from exc
    current = root
    for part in ("", *relative.parts):
        if part:
            current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RuntimeError(f"Symlink/reparse path is not allowed: {current}")
    return path


def _zip_path(name: str) -> None:
    parts = name.split("/")
    windows = PureWindowsPath(name)
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if (not name or "\\" in name or any(char in name for char in ':<>"|?*')
            or any(ord(char) < 32 for char in name) or windows.drive or windows.is_absolute()
            or PurePosixPath(name).is_absolute()
            or any(part in {"", ".", ".."} or part.endswith((".", " "))
                   or part.split(".", 1)[0].upper() in reserved for part in parts)):
        raise RuntimeError(f"Unsafe ZIP path: {name}")
    if not (name.startswith("backend/app/") or name == "backend/requirements.txt"
            or name in RELEASE_SCRIPTS | {"release-manifest.json", "RELEASE-NOTES.md"}):
        raise RuntimeError(f"ZIP path is outside release whitelist: {name}")


def validate_package(package: Path, commit: str, package_sha: str, root: Path) -> dict:
    """Validate the complete in-memory package and current executor, including preflight."""
    root = approved_path(root, root)
    for name in (".deploy-staging", ".deploy-rollbacks", "backups"):
        approved_path(root, root / name)
    for name in ("app", ".venv", ".venv/Scripts/python.exe", "data_v2/recognition_v2.db", *METADATA_FILES):
        target = approved_path(root, root / name)
        if not target.exists():
            raise RuntimeError(f"Unexpected live layout: missing {name}")
    if not (root / "app").is_dir() or not (root / ".venv").is_dir():
        raise RuntimeError("Unexpected live application/environment layout")
    for name in (".venv/Scripts/python.exe", "data_v2/recognition_v2.db", *METADATA_FILES):
        if not (root / name).is_file():
            raise RuntimeError(f"Unexpected live file layout: {name}")
    current = json.loads((root / "release-manifest.json").read_text(encoding="utf-8-sig"))
    if not isinstance(current.get("app_version"), str) or not current["app_version"]:
        raise RuntimeError("Current manifest has no application version")
    package = approved_path(root, package)
    incoming = approved_path(root, root / ".deploy-incoming")
    if package.parent != incoming or not package.is_file():
        raise RuntimeError("Package path is outside approved incoming directory")
    raw = package.read_bytes()
    if sha(raw) != package_sha.upper():
        raise RuntimeError("Package SHA-256 mismatch")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len({name.casefold() for name in names}) != len(names):
            raise RuntimeError("Duplicate/case-colliding ZIP members")
        for entry in entries:
            # ZipInfo normalizes backslashes on Windows and truncates NULs.
            # Validate the original central-directory spelling before that loss.
            if entry.orig_filename != entry.filename:
                raise RuntimeError(f"Unsafe ZIP path: {entry.orig_filename}")
            _zip_path(entry.filename)
            mode = entry.external_attr >> 16
            if entry.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG}):
                raise RuntimeError(f"ZIP member must be a regular file: {entry.filename}")
        content = {name: archive.read(name) for name in names}
    if not {"release-manifest.json", "RELEASE-NOTES.md"}.issubset(content):
        raise RuntimeError("Package release metadata is missing")
    manifest = json.loads(content["release-manifest.json"])
    if not isinstance(manifest, dict):
        raise RuntimeError("Manifest must be an object")
    if (manifest.get("git_commit") != commit or not isinstance(manifest.get("app_version"), str)
            or not manifest["app_version"]):
        raise RuntimeError("Manifest commit or version mismatch")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise RuntimeError("Manifest files must be a nonempty list")
    declared = []
    for entry in files:
        if not isinstance(entry, dict):
            raise RuntimeError("Manifest file entries must be objects")
        name = entry.get("path")
        if not isinstance(name, str):
            raise RuntimeError("Invalid manifest file path")
        _zip_path(name)
        if name in {"release-manifest.json", "RELEASE-NOTES.md"}:
            raise RuntimeError("Manifest cannot recursively declare release metadata")
        declared.append(name)
        size, digest = entry.get("size"), entry.get("sha256")
        if (type(size) is not int or size < 0 or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)):
            raise RuntimeError(f"Invalid manifest hash/size: {name}")
        if name not in content or len(content[name]) != size or sha(content[name]) != digest.upper():
            raise RuntimeError("Manifest hash/size mismatch: " + name)
    if len({name.casefold() for name in declared}) != len(declared):
        raise RuntimeError("Duplicate manifest file paths")
    if set(names) != set(declared) | {"release-manifest.json", "RELEASE-NOTES.md"}:
        raise RuntimeError("Package contents differ from manifest allowlist")
    if (not RELEASE_SCRIPTS.issubset(declared) or "backend/requirements.txt" not in declared
            or "backend/app/main.py" not in declared):
        raise RuntimeError("Required release application/scripts are missing")
    executors = {"scripts/server_release.py": (Path(__file__), _EXECUTING_SCRIPT_SHA),
                 "scripts/deployment_runtime.py": (Path(deployment_runtime.__file__),
                                                  deployment_runtime.__deployment_source_sha__)}
    for name, (executor, executing_sha) in executors.items():
        if executing_sha != sha(content[name]) or sha(executor.read_bytes()) != executing_sha:
            raise RuntimeError(f"Executing deployment script differs from release manifest: {name}")
    changed = content["backend/requirements.txt"] != (root / "requirements.txt").read_bytes()
    log("MANIFEST_OK", manifest["app_version"], commit, "requirements_changed=" + str(changed))
    return {"manifest": manifest, "content": content, "requirements_changed": changed,
            "current_version": current["app_version"], "package_sha256": sha(raw)}


@contextmanager
def deployment_lock(root: Path):
    """A nonblocking OS lock survives stale files but releases on process exit."""
    path = approved_path(root, root / ".deploy.lock")
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another deployment transaction holds the live-root lock") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def backup_database(source: Path, destination: Path):
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=60)) as live:
        with closing(sqlite3.connect(destination)) as backup:
            live.backup(backup)
            if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("Online SQLite backup quick_check failed")
    log("ONLINE_BACKUP_OK", str(destination), destination.stat().st_size)


def task_enabled(task_name):
    result = run("schtasks.exe", "/Query", "/TN", task_name, "/XML", check=False)
    if result.returncode:
        raise RuntimeError(f"Cannot query required scheduled task {task_name}: {result.stdout} {result.stderr}")
    try:
        document = ET.fromstring(result.stdout)
        enabled = document.find(".//{*}Settings/{*}Enabled")
        if enabled is not None and enabled.text not in {"true", "false"}:
            raise ValueError("Task Settings.Enabled is invalid")
    except (ET.ParseError, ValueError) as exc:
        raise RuntimeError(f"Cannot establish scheduled task enabled state: {task_name}") from exc
    if enabled is not None:
        return enabled.text == "true"

    # Task Scheduler can omit default-valued settings from exported XML.
    # Read the registered task's actual flag, not its localized/running state.
    quoted_name = task_name.replace("'", "''")
    command = (
        "$ErrorActionPreference='Stop';try {"
        "$scheduler=New-Object -ComObject Schedule.Service;$scheduler.Connect();"
        f"$task=$scheduler.GetFolder('\\').GetTask('{quoted_name}');"
        "$enabled=$task.Enabled;"
        "if ($enabled -isnot [bool]) {throw 'Task Enabled is not boolean'};"
        "[pscustomobject]@{task=$task.Path;enabled=$enabled}|ConvertTo-Json -Compress"
        "} catch {Write-Error $_;exit 1}"
    )
    actual = run("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command, check=False)
    try:
        if actual.returncode:
            raise ValueError(f"Task Scheduler API failed: {actual.stdout} {actual.stderr}")
        state = json.loads(actual.stdout)
        expected_path = "\\" + task_name.lstrip("\\")
        if (not isinstance(state, dict) or state.get("task") != expected_path
                or type(state.get("enabled")) is not bool):
            raise ValueError("Task Scheduler API returned an invalid task or Enabled value")
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"Cannot establish scheduled task enabled state: {task_name}") from exc
    log("TASK_STATE_VERIFIED", task_name, "source=TaskSchedulerAPI", f"enabled={state['enabled']}")
    return state["enabled"]


class DeploymentActions:
    """Only boundary effects are injectable; file transactions use real paths."""

    def backup_database(self, source, destination):
        backup_database(source, destination)

    def validate_service_task(self):
        if not task_enabled(TASK):
            raise RuntimeError(f"Required application scheduled task is disabled: {TASK}")

    def watchdog_enabled(self):
        return task_enabled(WATCHDOG)

    def disable_watchdog(self):
        run("schtasks.exe", "/Change", "/TN", WATCHDOG, "/Disable")
        # Disabling the schedule alone does not stop a currently running instance.
        run("schtasks.exe", "/End", "/TN", WATCHDOG, check=False)
        command = (
            "$ErrorActionPreference='Stop';"
            "$scheduler=New-Object -ComObject Schedule.Service;$scheduler.Connect();"
            f"$task=$scheduler.GetFolder('\\').GetTask('{WATCHDOG}');"
            f"if($task.Path -ne '\\{WATCHDOG}'){{throw 'Unexpected watchdog task path'}};"
            # Windows PowerShell can fail while enumerating a COM collection.
            # Query this registered task directly and read Count without enumeration.
            "$instances=$task.GetInstances(0);$running=$instances.Count;"
            "if($running -isnot [int] -or $running -lt 0){throw 'Invalid watchdog instance count'};"
            # TASK_STATE_QUEUED=2 and TASK_STATE_RUNNING=4 must both be absent.
            "if($task.Enabled -or $task.State -eq 2 -or $task.State -eq 4 -or $running -ne 0){exit 3};'WATCHDOG_QUIESCED'"
        )
        for _ in range(20):
            state = run("powershell.exe", "-NoProfile", "-Command", command, check=False)
            if state.returncode == 0 and state.stdout.strip() == "WATCHDOG_QUIESCED":
                return
            if state.returncode not in {0, 3}:
                raise RuntimeError(f"Cannot verify stopped watchdog: {state.stdout} {state.stderr}")
            time.sleep(1)
        raise RuntimeError("Watchdog did not become disabled and quiescent")

    def enable_watchdog(self):
        run("schtasks.exe", "/Change", "/TN", WATCHDOG, "/Enable")

    def stop_app(self):
        stop_app()

    def start_app(self, version):
        start_app(version)

    def external_health(self, version):
        health("https://124.220.229.9:28176/health", version, external=True, seconds=25)

    def install_requirements(self, python, requirements):
        run(str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(requirements))

    def snapshot_environment(self, env, snapshot):
        return deployment_runtime.snapshot_environment(env, snapshot)

    def verify_environment(self, env, snapshot):
        return deployment_runtime.verify_environment(env, snapshot)

    def restore_environment(self, env, snapshot):
        return deployment_runtime.restore_environment(env, snapshot)


class DeploymentError(RuntimeError):
    def __init__(self, original_error, rollback_errors, receipt_path):
        self.original_error = original_error
        self.rollback_errors = tuple(rollback_errors)
        self.receipt_path = receipt_path
        detail = "; ".join(f"{step}: {error}" for step, error in rollback_errors)
        super().__init__(f"Deployment failed: {original_error}; " +
                         (f"rollback incomplete: {detail}" if detail else "rollback completed"))


def _write_receipt(path: Path, receipt: dict):
    temporary = path.with_suffix(".partial")
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def deploy_release(package: Path, commit: str, package_sha: str, *, root=None, actions=None, preflight=False):
    root = approved_path(Path(root) if root is not None else ROOT, Path(root) if root is not None else ROOT)
    actions = actions or DeploymentActions()
    if preflight:
        return validate_package(package, commit, package_sha, root)["manifest"]
    with deployment_lock(root):
        verified = validate_package(package, commit, package_sha, root)
        return _deploy_verified(root, verified, actions)


def _deploy_verified(root: Path, verified: dict, actions: DeploymentActions):
    manifest, content = verified["manifest"], verified["content"]
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    stage = approved_path(root, root / ".deploy-staging" / stamp)
    stage.mkdir(parents=True)
    for name, data in content.items():
        target = approved_path(root, stage.joinpath(*name.split("/")))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    backups = approved_path(root, root / "backups")
    backups.mkdir(exist_ok=True)
    db_backup = approved_path(root, backups / ("recognition_v2-" + stamp + ".db"))
    actions.backup_database(approved_path(root, root / "data_v2/recognition_v2.db"), db_backup)
    rollback = approved_path(root, root / ".deploy-rollbacks" / stamp)
    rollback.mkdir(parents=True)
    for name in METADATA_FILES:
        shutil.copy2(approved_path(root, root / name), approved_path(root, rollback / name))
    environment = approved_path(root, root / ".venv")
    snapshot = approved_path(root, rollback / "venv")
    receipt_path = approved_path(root, rollback / "transaction.json")
    receipt = {"status": "prepared", "app_version": manifest["app_version"],
               "previous_version": verified["current_version"], "git_commit": manifest["git_commit"],
               "package_sha256": verified["package_sha256"], "database_backup": str(db_backup),
               "requirements_changed": verified["requirements_changed"],
               "environment_snapshot": str(snapshot) if verified["requirements_changed"] else None,
               "original_failure": None, "rollback_failures": []}
    _write_receipt(receipt_path, receipt)
    # All preparation is completed while the old application is still available.
    if verified["requirements_changed"]:
        try:
            actions.snapshot_environment(environment, snapshot)
        except BaseException as failure:
            receipt.update(status="preparation_failed", original_failure={
                "type": type(failure).__name__, "message": str(failure)})
            errors = []
            try:
                _write_receipt(receipt_path, receipt)
            except BaseException as exc:
                errors.append(("evidence", exc))
            raise DeploymentError(failure, errors, receipt_path) from failure
        log("ENVIRONMENT_SNAPSHOT_OK", str(snapshot))
    app_moved = False
    stop_attempted = False
    pip_started = False
    watchdog_original_enabled = None
    watchdog_state = "not_checked"
    watchdog_managed = False
    try:
        actions.validate_service_task()
        watchdog_state = "unknown"
        watchdog_original_enabled = actions.watchdog_enabled()
        watchdog_managed = True
        # Even an already disabled schedule may retain a running instance.
        # Remember its original enabled state and quiesce it in either case.
        actions.disable_watchdog()
        watchdog_state = "disabled"
        stop_attempted = True
        actions.stop_app()  # Success means port 18082 is confirmed released.
        log("APP_STOPPED")
        if verified["requirements_changed"]:
            actions.verify_environment(environment, snapshot)
        approved_path(root, root / "app").rename(approved_path(root, rollback / "app"))
        app_moved = True
        approved_path(root, stage / "backend/app").rename(approved_path(root, root / "app"))
        shutil.copy2(approved_path(root, stage / "backend/requirements.txt"), approved_path(root, root / "requirements.txt"))
        if verified["requirements_changed"]:
            pip_started = True  # pip may partially mutate dependencies before returning an error.
            actions.install_requirements(approved_path(root, environment / "Scripts/python.exe"), root / "requirements.txt")
        actions.start_app(manifest["app_version"])
        log("INTERNAL_HEALTH_OK", manifest["app_version"])
        actions.external_health(manifest["app_version"])
        log("EXTERNAL_HEALTH_OK", manifest["app_version"])
        for name in ("release-manifest.json", "RELEASE-NOTES.md"):
            shutil.copy2(approved_path(root, stage / name), approved_path(root, root / name))
        receipt["status"] = "deployed"
        _write_receipt(receipt_path, receipt)
        if watchdog_original_enabled:
            watchdog_state = "unknown"
            actions.enable_watchdog()
            watchdog_state = "enabled"
        receipt.update(watchdog_original_enabled=watchdog_original_enabled,
                       watchdog_state=watchdog_state)
        _write_receipt(receipt_path, receipt)
    except BaseException as failure:
        log("DEPLOY_FAILED_ROLLBACK_START", failure)
        errors = []
        watchdog_safe = True
        if watchdog_managed:
            try:
                # An unsuccessful Enable/Disable command may still have taken effect.
                watchdog_state = "unknown"
                actions.disable_watchdog()
                watchdog_state = "disabled"
            except BaseException as exc:
                watchdog_safe = False
                errors.append(("watchdog_disable", exc))
        stopped_safely = not stop_attempted and watchdog_safe
        if stop_attempted:
            try:
                actions.stop_app()
                stopped_safely = watchdog_safe
            except BaseException as exc:
                errors.append(("stop", exc))
        # Never replace app directories or environment files under a live process.
        if stopped_safely:
            if pip_started:
                try:
                    actions.restore_environment(environment, snapshot)
                    log("ENVIRONMENT_ROLLBACK_OK")
                except BaseException as exc:
                    errors.append(("environment", exc))
            if app_moved:
                try:
                    current_app = approved_path(root, root / "app")
                    if current_app.exists():
                        current_app.rename(approved_path(root, rollback / "failed-app"))
                    approved_path(root, rollback / "app").rename(current_app)
                except BaseException as exc:
                    errors.append(("application", exc))
                for name in METADATA_FILES:
                    try:
                        shutil.copy2(approved_path(root, rollback / name), approved_path(root, root / name))
                    except BaseException as exc:
                        errors.append((name, exc))
            if stop_attempted and not errors:
                try:
                    actions.start_app(verified["current_version"])
                    log("ROLLBACK_HEALTH_OK", verified["current_version"])
                except BaseException as exc:
                    errors.append(("health", exc))
            if watchdog_original_enabled and not errors:
                try:
                    watchdog_state = "unknown"
                    actions.enable_watchdog()
                    watchdog_state = "enabled"
                except BaseException as exc:
                    errors.append(("watchdog", exc))
        receipt.update(status="rollback_failed" if errors else "rolled_back",
                       original_failure={"type": type(failure).__name__, "message": str(failure)},
                       rollback_failures=[{"step": step, "type": type(error).__name__, "message": str(error)}
                                          for step, error in errors],
                       watchdog_original_enabled=watchdog_original_enabled,
                       watchdog_state=watchdog_state,
                       watchdog_left_disabled=(watchdog_state == "disabled"
                                               if watchdog_state in {"enabled", "disabled"} else None))
        try:
            _write_receipt(receipt_path, receipt)
        except BaseException as exc:
            errors.append(("evidence", exc))
        for step, error in errors:
            log("ROLLBACK_FAILED", step, error)
        raise DeploymentError(failure, errors, receipt_path) from failure
    log("DEPLOYED", manifest["app_version"], manifest["git_commit"])
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("commit")
    parser.add_argument("package_sha")
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args(argv)
    deploy_release(args.package, args.commit, args.package_sha, preflight=args.preflight)


if __name__ == "__main__":
    main()
