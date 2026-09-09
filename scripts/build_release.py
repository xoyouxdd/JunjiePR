"""Build a production-safe source package without runtime data or uploads."""

from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[1]
BACKEND_ROOT = WORKSPACE / "backend"
SCRIPTS_ROOT = WORKSPACE / "scripts"
EXCLUDED_PARTS = {"data_v2", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules", ".git"}
EXCLUDED_SUFFIXES = {".db", ".sqlite", ".log", ".pyc", ".pem", ".key", ".zip"}
EXCLUDED_NAMES = {".env", "secrets.json", ".coverage"}


def read_app_version() -> str:
    text = (BACKEND_ROOT / "app" / "version.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not match:
        raise RuntimeError("APP_VERSION missing from app/version.py")
    return match.group(1)


def package_name(version: str, day: str | None = None) -> str:
    del day  # Version already encodes the calendar day and edition.
    return f"recognition-v{version}.zip"


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


def main() -> None:
    version = read_app_version()
    destination = WORKSPACE / package_name(version)
    temp_path = destination.with_name(destination.name + ".partial")
    if temp_path.exists():
        temp_path.unlink()
    with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in (BACKEND_ROOT, SCRIPTS_ROOT):
            for path in sorted(source.rglob("*")):
                if path.is_file() and should_include(path, source):
                    archive.write(path, (Path(source.name) / path.relative_to(source)).as_posix())
    temp_path.replace(destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest().upper()
    print(f"{destination.name} {digest}")


if __name__ == "__main__":
    main()
