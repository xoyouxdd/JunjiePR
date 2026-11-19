"""Shared online SQLite snapshots for the application and operations scripts.

This module uses only the standard library so the deployment transaction can
load it from the verified release package before replacing the live app.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile


def _open_source(database: Path) -> sqlite3.Connection:
    return sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=60)


def _quick_check(connection: sqlite3.Connection, label: str) -> None:
    rows = [row[0] for row in connection.execute("PRAGMA quick_check")]
    if rows != ["ok"]:
        raise RuntimeError(f"{label} SQLite quick_check failed: " + "; ".join(rows))


def check_source(database: Path) -> None:
    with closing(_open_source(database)) as connection:
        _quick_check(connection, "Source")


def _temporary_sibling(destination: Path) -> Path:
    with NamedTemporaryFile(dir=destination.parent, prefix=f".{destination.name}.", suffix=".partial", delete=False) as file:
        return Path(file.name)


def _require_new(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Backup target already exists: {path}")


def create_online_backup(source: Path, destination: Path, *, retention_days: int | None = None) -> dict:
    """Publish a verified snapshot and, when requested, its managed manifest.

    The caller chooses the paths and handles authorization and retention. The
    deployment backup only requires the copied database to pass quick_check.
    Managed copies also check the source before backing it up.
    """
    manifest = destination.with_name(destination.name + ".manifest.json") if retention_days is not None else None
    _require_new(destination)
    if manifest is not None:
        _require_new(manifest)
    temporary_database = _temporary_sibling(destination)
    temporary_manifest = None
    try:
        with closing(_open_source(source)) as live:
            if manifest is not None:
                _quick_check(live, "Source")
            with closing(sqlite3.connect(temporary_database, timeout=60)) as backup:
                live.backup(backup, pages=256, sleep=0.10)
                backup.commit()
                _quick_check(backup, "Backup")

        result = {"size_bytes": temporary_database.stat().st_size,
                  "quick_check": "ok", "sqlite_version": sqlite3.sqlite_version}
        if manifest is not None:
            with temporary_database.open("rb") as file:
                result["sha256"] = hashlib.file_digest(file, "sha256").hexdigest()
            temporary_manifest = _temporary_sibling(manifest)
            temporary_manifest.write_text(json.dumps({
                "schema_version": 1,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "source_database": "data_v2/recognition_v2.db",
                "backup_file": destination.name,
                **result,
                "retention_days": retention_days,
            }, ensure_ascii=False, indent=2), encoding="utf-8")

        _require_new(destination)
        if manifest is not None:
            _require_new(manifest)
        temporary_database.rename(destination)
        if temporary_manifest is not None:
            try:
                temporary_manifest.rename(manifest)
            except OSError:
                destination.unlink(missing_ok=True)
                raise
        return result
    finally:
        temporary_database.unlink(missing_ok=True)
        if temporary_manifest is not None:
            temporary_manifest.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check").add_argument("source", type=Path)
    backup = commands.add_parser("backup")
    backup.add_argument("source", type=Path)
    backup.add_argument("destination", type=Path)
    backup.add_argument("retention_days", type=int)
    args = parser.parse_args()
    if args.command == "check":
        check_source(args.source)
        result = {"quick_check": "ok"}
    else:
        result = create_online_backup(args.source, args.destination, retention_days=args.retention_days)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
