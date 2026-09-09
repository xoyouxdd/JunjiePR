"""Build a production-safe source package without runtime data or uploads."""

from __future__ import annotations

import hashlib
import shutil
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
DESTINATION = WORKSPACE / "recognition-v2295-candidate-20260904-001.zip"
EXCLUDED_PARTS = {"data_v2", "__pycache__", ".pytest_cache"}
EXCLUDED_SUFFIXES = {".db", ".sqlite", ".log", ".pyc", ".pem", ".key", ".zip"}


def should_include(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    return not any(part in EXCLUDED_PARTS for part in relative.parts) and path.suffix.lower() not in EXCLUDED_SUFFIXES


def main() -> None:
    if DESTINATION.exists():
        DESTINATION.unlink()
    with zipfile.ZipFile(DESTINATION, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(ROOT.rglob("*")):
            if path.is_file() and should_include(path):
                archive.write(path, path.relative_to(ROOT).as_posix())
    digest = hashlib.sha256(DESTINATION.read_bytes()).hexdigest().upper()
    print(f"{DESTINATION.name} {digest}")


if __name__ == "__main__":
    main()

