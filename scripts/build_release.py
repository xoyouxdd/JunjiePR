"""Build a production-safe source package without runtime data or uploads."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import subprocess
import sys
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
BACKEND_TOP_LEVEL = {"app", "requirements.txt"}
SCRIPT_FILES = {
    "Deploy-RecognitionRelease.ps1",
    "server_release.py",
    "deployment_runtime.py",
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
    documents = [
        WORKSPACE / "README.md",
        BACKEND_ROOT / "README.md",
        SCRIPTS_ROOT / "README.md",
        *sorted(DOCS_ROOT.rglob("*.md")),
    ]
    for document in documents:
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


def render_release_notes(version: str) -> bytes:
    """Render the user-facing current release from the single in-app source."""
    # Import only after the version gate above has established the current block.
    import sys

    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))
    from app.changelog import RELEASES

    release = RELEASES[0]
    if release["version"] != version:
        raise RuntimeError("cannot generate notes for a non-current release")
    lines = [f"# 更新记录 V{version}", "", f"发布日期：{release.get('date', '')}", ""]
    for item in release.get("items") or []:
        audiences = "、".join(item.get("audiences") or ["all"])
        permissions = "、".join(item.get("permissions") or []) or "无额外权限"
        lines.extend([f"## {item['summary']}", "", item.get("detail") or "", "", f"可见角色：{audiences}", f"所需权限：{permissions}", ""])
    return ("\n".join(lines)).encode("utf-8")


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


def checked_source(path: Path):
    """Check ancestors before following or descending into a Windows junction."""
    path = path.absolute()
    for entry in reversed((path, *path.parents)):
        info = entry.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise RuntimeError(f"release source must not contain symlinks or reparse points: {entry}")
    return info


def source_files(path: Path, root: Path):
    """Walk only approved source roots, checking entries before recursion."""
    info = checked_source(path)
    if not should_include(path, root):
        return
    if stat.S_ISDIR(info.st_mode):
        for child in sorted(path.iterdir()):
            yield from source_files(child, root)
    elif stat.S_ISREG(info.st_mode):
        yield path
    else:
        raise RuntimeError(f"unsupported release source type: {path}")


def release_sources() -> list[tuple[Path, str]]:
    """Return an explicit whitelist without traversing unrelated runtime trees."""
    checked_source(BACKEND_ROOT)
    checked_source(SCRIPTS_ROOT)
    sources: list[tuple[Path, str]] = []
    missing_scripts = {f"scripts/{name}" for name in SCRIPT_FILES if not (SCRIPTS_ROOT / name).exists()}
    if missing_scripts:
        raise RuntimeError("required deployment scripts missing: " + ", ".join(sorted(missing_scripts)))
    for name in sorted(BACKEND_TOP_LEVEL):
        source_root = BACKEND_ROOT / name
        if not source_root.exists() and not source_root.is_symlink():
            continue
        for path in source_files(source_root, BACKEND_ROOT):
            sources.append((path, (Path("backend") / path.relative_to(BACKEND_ROOT)).as_posix()))
    for name in sorted(SCRIPT_FILES):
        path = SCRIPTS_ROOT / name
        if not stat.S_ISREG(checked_source(path).st_mode):
            raise RuntimeError(f"deployment script must be a regular file: {path}")
        sources.append((path, f"scripts/{name}"))
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


def run_test_suite() -> None:
    """Run the whole suite before packaging.

    The project has no CI, so this is the only place that can still stop a
    broken build from being packaged and shipped.
    """
    result = subprocess.run([sys.executable, str(BACKEND_ROOT / "tests" / "run_all.py")], cwd=str(BACKEND_ROOT))
    if result.returncode != 0:
        raise RuntimeError("full test suite failed; refusing to build a release package")


def main() -> None:
    ensure_clean_checkout()
    run_test_suite()
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
        archive.writestr("RELEASE-NOTES.md", render_release_notes(version))
    temp_path.replace(destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest().upper()
    print(f"{destination.name} {digest}")


if __name__ == "__main__":
    main()
