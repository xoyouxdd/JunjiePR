"""Build a production-safe source package without runtime data or uploads."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[1]
BACKEND_ROOT = WORKSPACE / "backend"
SCRIPTS_ROOT = WORKSPACE / "scripts"
DOCS_ROOT = WORKSPACE / "docs"
EXCLUDED_PARTS = {"data_v2", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules", ".git"}
EXCLUDED_SUFFIXES = {".db", ".sqlite", ".log", ".pyc", ".pem", ".key", ".zip"}
EXCLUDED_NAMES = {".env", "secrets.json", ".coverage"}
BACKEND_TOP_LEVEL = {"app", "tests", "README.md", "requirements.txt"}
SCRIPT_FILES = {
    "Install-DailyBackupHealthTask.ps1",
    "Install-DailyBackupTask.ps1",
    "Invoke-SqliteOnlineBackup.ps1",
    "README.md",
    "Reset-NeverLoggedInInitialPasswords.py",
    "Run-MonthlyRestoreRehearsal.ps1",
    "Start-LocalJunjiePR.ps1",
    "Test-SqliteBackupHealth.ps1",
    "Test-SqliteBackupRestore.ps1",
    "build_release.py",
    "purge_legacy_attendance.py",
    "verify_readiness.py",
}


def read_app_version() -> str:
    text = (BACKEND_ROOT / "app" / "version.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not match:
        raise RuntimeError("APP_VERSION missing from app/version.py")
    return match.group(1)


def package_name(version: str, day: str | None = None) -> str:
    del day  # Version already encodes the calendar day and edition.
    return f"recognition-v{version}.zip"


def read_latest_changelog_version() -> str:
    text = (BACKEND_ROOT / "app" / "changelog.py").read_text(encoding="utf-8")
    match = re.search(r'"version"\s*:\s*"([^"]+)"', text)
    if not match:
        raise RuntimeError("latest changelog version missing")
    return match.group(1)


def broken_markdown_links() -> list[str]:
    broken: list[str] = []
    for document in [WORKSPACE / "README.md", *sorted(DOCS_ROOT.rglob("*.md"))]:
        text_value = document.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text_value):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE) or target.startswith("#"):
                continue
            clean_target = target.split("#", 1)[0]
            if clean_target and not (document.parent / clean_target).resolve().exists():
                broken.append(f"{document.relative_to(WORKSPACE).as_posix()} -> {target}")
    return broken


def ensure_release_contract(version: str) -> None:
    latest = read_latest_changelog_version()
    if latest != version:
        raise RuntimeError(f"APP_VERSION {version} does not match latest changelog {latest}")
    broken = broken_markdown_links()
    if broken:
        raise RuntimeError("broken documentation links: " + "; ".join(broken))


def should_include(path: Path, root: Path | None = None) -> bool:
    root = root or BACKEND_ROOT
    relative = path.relative_to(root)
    if any(part in EXCLUDED_PARTS or part.startswith(".codex-local-data-") for part in relative.parts):
        return False
    if path.name in EXCLUDED_NAMES or path.name.startswith(".env"):
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    return True


def release_sources() -> list[tuple[Path, str]]:
    """Return an explicit, reviewable package whitelist."""
    sources: list[tuple[Path, str]] = []
    for path in sorted(BACKEND_ROOT.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"release source must not contain symlinks: {path}")
        if not path.is_file() or not should_include(path, BACKEND_ROOT):
            continue
        relative = path.relative_to(BACKEND_ROOT)
        if relative.parts[0] in BACKEND_TOP_LEVEL:
            sources.append((path, (Path("backend") / relative).as_posix()))
    for path in sorted(SCRIPTS_ROOT.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"release source must not contain symlinks: {path}")
        if not path.is_file() or not should_include(path, SCRIPTS_ROOT):
            continue
        relative = path.relative_to(SCRIPTS_ROOT)
        if relative.parts[0] in SCRIPT_FILES or relative.parts[0] == "tests":
            sources.append((path, (Path("scripts") / relative).as_posix()))
    for path in sorted(DOCS_ROOT.rglob("*.md")):
        if path.is_symlink():
            raise RuntimeError(f"release source must not contain symlinks: {path}")
        sources.append((path, (Path("docs") / path.relative_to(DOCS_ROOT)).as_posix()))
    sources.append((WORKSPACE / "README.md", "README.md"))
    return sources


def git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=WORKSPACE, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def ensure_clean_checkout() -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=WORKSPACE, text=True, capture_output=True, check=True
    )
    if result.stdout.strip():
        raise RuntimeError("release package requires a clean Git checkout")


def build_manifest(sources: list[tuple[Path, str]], version: str, commit: str) -> dict:
    files = []
    for path, archive_name in sources:
        content = path.read_bytes()
        files.append({"path": archive_name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest().upper()})
    return {
        "app_version": version,
        "git_commit": commit,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }


def main() -> None:
    ensure_clean_checkout()
    version = read_app_version()
    ensure_release_contract(version)
    commit = git_commit()
    destination = WORKSPACE / package_name(version)
    temp_path = destination.with_name(destination.name + ".partial")
    if temp_path.exists():
        temp_path.unlink()
    sources = release_sources()
    manifest = build_manifest(sources, version, commit)
    with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, archive_name in sources:
            content = path.read_bytes()
            archive.writestr(archive_name, content)
        archive.writestr(
            "release-manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
    temp_path.replace(destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest().upper()
    print(f"{destination.name} {digest}")


if __name__ == "__main__":
    main()
