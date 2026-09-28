import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import ssl
import subprocess
import sys
import time
import urllib.request
import uuid
import zipfile

ROOT = Path(r"C:\Server\zhaojunjie\recognition-card-system")
TASK = "RecognitionCardSystem"
WATCHDOG = "RecognitionCardSystemWatchdog"


def log(*parts):
    print(*parts, flush=True)


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("commit")
    parser.add_argument("package_sha")
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if not ROOT.is_dir() or not (ROOT / "app").is_dir() or not (ROOT / "requirements.txt").is_file():
        raise RuntimeError("Unexpected live layout")
    if not (ROOT / ".venv" / "Scripts" / "python.exe").is_file():
        raise RuntimeError("Production Python missing")
    package = args.package.resolve()
    if package.parent != (ROOT / ".deploy-incoming").resolve():
        raise RuntimeError("Package path is outside approved incoming directory")
    if sha(package.read_bytes()) != args.package_sha.upper():
        raise RuntimeError("Package SHA-256 mismatch")
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("release-manifest.json"))
        if manifest["git_commit"] != args.commit or not manifest["app_version"]:
            raise RuntimeError("Manifest commit or version mismatch")
        allowed = {x["path"] for x in manifest["files"]} | {"release-manifest.json", "RELEASE-NOTES.md"}
        if set(names) != allowed | {"scripts/Deploy-RecognitionRelease.ps1"}:
            raise RuntimeError("Package contents differ from manifest allowlist")
        if any(name.startswith("/") or ".." in Path(name).parts or "\\" in name for name in names):
            raise RuntimeError("Unsafe ZIP path")
        for entry in manifest["files"]:
            data = archive.read(entry["path"])
            if len(data) != entry["size"] or sha(data) != entry["sha256"].upper():
                raise RuntimeError("Manifest hash/size mismatch: " + entry["path"])
        req = archive.read("backend/requirements.txt")
        old_req = (ROOT / "requirements.txt").read_bytes()
        log("MANIFEST_OK", manifest["app_version"], args.commit, "requirements_changed=" + str(req != old_req))
        if args.preflight:
            return
        stage = ROOT / ".deploy-staging" / uuid.uuid4().hex
        stage.mkdir(parents=True)
        for name in names:
            target = stage.joinpath(*name.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))

    current_version = json.loads((ROOT / "release-manifest.json").read_text(encoding="utf-8-sig"))["app_version"]
    backup_dir = ROOT / "backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    db_source = ROOT / "data_v2" / "recognition_v2.db"
    db_backup = backup_dir / ("recognition_v2-" + stamp + ".db")
    source = sqlite3.connect(db_source, timeout=60)
    destination = sqlite3.connect(db_backup)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    check = sqlite3.connect(db_backup)
    try:
        if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("Online SQLite backup quick_check failed")
    finally:
        check.close()
    log("ONLINE_BACKUP_OK", str(db_backup), db_backup.stat().st_size)

    rollback = ROOT / ".deploy-rollbacks" / stamp
    rollback.mkdir(parents=True)
    shutil.copy2(ROOT / "requirements.txt", rollback / "requirements.txt")
    shutil.copy2(ROOT / "release-manifest.json", rollback / "release-manifest.json")
    shutil.copy2(ROOT / "RELEASE-NOTES.md", rollback / "RELEASE-NOTES.md")
    app_moved = False
    stopped = False
    watchdog_disabled = False
    try:
        watchdog = run("schtasks.exe", "/Query", "/TN", WATCHDOG, "/XML", check=False)
        if watchdog.returncode == 0 and "<Enabled>false</Enabled>" not in watchdog.stdout:
            run("schtasks.exe", "/Change", "/TN", WATCHDOG, "/Disable")
            watchdog_disabled = True
        stopped = True
        stop_app()
        log("APP_STOPPED")
        (ROOT / "app").rename(rollback / "app")
        app_moved = True
        (stage / "backend" / "app").rename(ROOT / "app")
        shutil.copy2(stage / "backend" / "requirements.txt", ROOT / "requirements.txt")
        run(str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(ROOT / "requirements.txt"))
        start_app(manifest["app_version"])
        log("INTERNAL_HEALTH_OK", manifest["app_version"])
        health("https://124.220.229.9:28176/health", manifest["app_version"], external=True, seconds=25)
        log("EXTERNAL_HEALTH_OK", manifest["app_version"])
        shutil.copy2(stage / "release-manifest.json", ROOT / "release-manifest.json")
        shutil.copy2(stage / "RELEASE-NOTES.md", ROOT / "RELEASE-NOTES.md")
        log("DEPLOYED", manifest["app_version"], args.commit)
    except Exception:
        log("DEPLOY_FAILED_ROLLBACK_START")
        if stopped:
            try:
                stop_app()
            except Exception as exc:
                log("ROLLBACK_STOP_WARNING", exc)
        if app_moved:
            failed = rollback / "failed-app"
            if (ROOT / "app").exists():
                (ROOT / "app").rename(failed)
            (rollback / "app").rename(ROOT / "app")
            shutil.copy2(rollback / "requirements.txt", ROOT / "requirements.txt")
            shutil.copy2(rollback / "release-manifest.json", ROOT / "release-manifest.json")
            shutil.copy2(rollback / "RELEASE-NOTES.md", ROOT / "RELEASE-NOTES.md")
        if stopped:
            try:
                start_app(current_version)
                log("ROLLBACK_HEALTH_OK", current_version)
            except Exception as exc:
                log("ROLLBACK_HEALTH_FAILED", exc)
        raise
    finally:
        if watchdog_disabled:
            run("schtasks.exe", "/Change", "/TN", WATCHDOG, "/Enable", check=False)


if __name__ == "__main__":
    main()
