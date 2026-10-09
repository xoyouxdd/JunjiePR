"""Offline, in-place rollback of an existing Windows virtual environment.

The snapshot includes runtime files and empty directories. Generated __pycache__
trees and adjacent .pyc/.pyo files with .py source are excluded and cleared during
restore; source-less bytecode is preserved. File content, permission bits and
nanosecond modification times are verified. Directory modes are preserved, but
their changing modification times, access/creation times, ownership and ACLs are
deliberately excluded.
ACLs remain those of the existing environment/its parent (they are not cloned).
The environment stays at its original path, preserving Windows launchers.

Snapshots are usable only after an atomic completion marker commits a verified
manifest. Failed snapshots/restores remain on disk for diagnosis or retry;
neither a partial copy nor a failed final verification means rollback succeeded.
No package manager, network operation or recursive environment deletion is used.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import uuid


class EnvironmentSnapshotError(RuntimeError):
    """A snapshot, its path boundary, or restoration could not be verified."""


_MANIFEST = "manifest.json"
_COMPLETE = "complete.json"
_POLICY = {"bytecode": "exclude-generated-clear-on-restore", "metadata": "file-mode-and-mtime_ns-directory-mode",
           "acl": "inherited-not-copied"}
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _unsafe(message: str) -> EnvironmentSnapshotError:
    return EnvironmentSnapshotError(message)


def _check_node(path: Path) -> os.stat_result:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
        raise _unsafe(f"Link/reparse point is forbidden: {path}")
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise _unsafe(f"Unsupported filesystem entry: {path}")
    return info


def _checked_path(path: Path, *, required: bool = True) -> Path:
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise _unsafe(f"An absolute, non-traversing path is required: {path}")
    # Check every existing ancestor before resolve(), which would hide junctions.
    for part in reversed((path, *path.parents)):
        try:
            _check_node(part)
        except FileNotFoundError:
            if part == path and required:
                raise _unsafe(f"Required path is missing: {path}")
    if path.resolve(strict=False) != path:
        raise _unsafe(f"Resolved path differs from the approved path: {path}")
    return path


def _paths(env_path: Path, snapshot_path: Path, *, snapshot_required: bool) -> tuple[Path, Path]:
    env = _checked_path(env_path)
    snapshot = _checked_path(snapshot_path, required=snapshot_required)
    if env.name != ".venv" or not env.is_dir():
        raise _unsafe("Only an existing .venv directory can be snapshotted/restored")
    if (snapshot.name != "venv" or snapshot.parent.parent.name != ".deploy-rollbacks"
            or snapshot.parent.parent.parent != env.parent):
        raise _unsafe("Snapshot must be .deploy-rollbacks/<stamp>/venv beside .venv")
    if snapshot_required and not snapshot.is_dir():
        raise _unsafe("Snapshot is not a directory")
    return env, snapshot


def _relative_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or ":" in name or "\0" in name:
        raise _unsafe(f"Unsafe manifest path: {name!r}")
    parts = name.split("/")
    if any(part in {"", ".", ".."} or part.endswith((" ", ".")) for part in parts):
        raise _unsafe(f"Unsafe manifest path: {name!r}")
    if PurePosixPath(name).is_absolute():
        raise _unsafe(f"Unsafe manifest path: {name!r}")
    return name


def _child(root: Path, name: str, *, required: bool = True) -> Path:
    target = root.joinpath(*_relative_name(name).split("/"))
    _checked_path(target, required=required)
    if not target.is_relative_to(root) or target == root:
        raise _unsafe(f"Target escaped the approved root: {target}")
    return target


def _metadata(info: os.stat_result) -> dict:
    return {"mode": stat.S_IMODE(info.st_mode), "mtime_ns": info.st_mtime_ns}


def _file_entry(path: Path) -> dict:
    before = _check_node(path)
    if not stat.S_ISREG(before.st_mode):
        raise _unsafe(f"Expected a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise _unsafe(f"File changed while opening: {path}")
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    after = _check_node(path)
    if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode)):
        raise _unsafe(f"File changed while reading: {path}")
    return {"size": after.st_size, "sha256": digest.hexdigest(), **_metadata(after)}


def _is_generated_cache(path: Path, root: Path) -> bool:
    if any(part.casefold() == "__pycache__" for part in path.relative_to(root).parts):
        return True
    return path.suffix.lower() in {".pyc", ".pyo"} and path.with_suffix(".py").is_file()


def _scan(root: Path, *, include_cache: bool = False) -> dict:
    _checked_path(root)
    if not root.is_dir():
        raise _unsafe(f"Expected a directory: {root}")
    result = {"directories": {"": {"mode": stat.S_IMODE(_check_node(root).st_mode)}}, "files": {}}
    pending = [root]
    seen: set[str] = set()
    while pending:
        parent = pending.pop()
        _checked_path(parent)
        for entry in sorted(parent.iterdir()):
            name = _relative_name(entry.relative_to(root).as_posix())
            folded = os.path.normcase(name)
            if folded in seen:
                raise _unsafe(f"Aliased filesystem path: {name}")
            seen.add(folded)
            path = _child(root, name)
            info = _check_node(path)
            ignored = not include_cache and _is_generated_cache(path, root)
            if stat.S_ISDIR(info.st_mode):
                if not ignored:
                    result["directories"][name] = {"mode": stat.S_IMODE(info.st_mode)}
                pending.append(path)
            elif not ignored:
                result["files"][name] = _file_entry(path)
    return result


def _set_metadata(path: Path, metadata: dict) -> None:
    _checked_path(path)
    info = _check_node(path)
    if stat.S_IMODE(info.st_mode) != metadata["mode"]:
        os.chmod(path, metadata["mode"])
    if "mtime_ns" in metadata and info.st_mtime_ns != metadata["mtime_ns"]:
        os.utime(path, ns=(info.st_atime_ns, metadata["mtime_ns"]))


def _copy_file(source: Path, target: Path, expected: dict) -> None:
    _checked_path(source)
    _checked_path(target, required=False)
    digest = hashlib.sha256()
    total = 0
    with source.open("rb") as incoming, target.open("xb") as outgoing:
        for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
            outgoing.write(chunk)
            digest.update(chunk)
            total += len(chunk)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    if total != expected["size"] or digest.hexdigest() != expected["sha256"]:
        raise _unsafe(f"Source changed/corrupted while copying: {source}")
    _set_metadata(target, expected)
    if _file_entry(target) != expected:
        raise _unsafe(f"Copied file failed verification: {target}")


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _atomic_json(path: Path, value: dict) -> None:
    temp = path.with_name(path.name + ".partial-" + uuid.uuid4().hex)
    _checked_path(temp, required=False)
    with temp.open("xb") as stream:
        stream.write(_canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    _checked_path(path, required=False)
    os.replace(temp, path)


def _validate_manifest(manifest: dict, env: Path, snapshot: Path) -> dict:
    if (not isinstance(manifest, dict) or manifest.get("schema") != 1
            or manifest.get("environment") != str(env) or manifest.get("snapshot") != str(snapshot)
            or manifest.get("policy") != _POLICY):
        raise _unsafe("Snapshot manifest identity/policy mismatch")
    inventory = manifest.get("inventory")
    if not isinstance(inventory, dict) or set(inventory) != {"files", "directories"}:
        raise _unsafe("Invalid snapshot inventory")
    names: set[str] = set()
    for kind in ("directories", "files"):
        entries = inventory[kind]
        if not isinstance(entries, dict):
            raise _unsafe("Invalid snapshot entries")
        for name, entry in entries.items():
            if not (kind == "directories" and name == ""):
                _relative_name(name)
                normalized = os.path.normcase(name)
                if normalized in names:
                    raise _unsafe(f"Duplicate snapshot path: {name}")
                names.add(normalized)
            fields = {"mode"} | ({"mtime_ns", "size", "sha256"} if kind == "files" else set())
            if not isinstance(entry, dict) or set(entry) != fields:
                raise _unsafe(f"Invalid snapshot metadata: {name}")
            if type(entry["mode"]) is not int or not 0 <= entry["mode"] <= 0o7777:
                raise _unsafe(f"Invalid snapshot metadata values: {name}")
            if kind == "files" and (type(entry["mtime_ns"]) is not int or entry["mtime_ns"] < 0
                    or type(entry["size"]) is not int or entry["size"] < 0
                    or not isinstance(entry["sha256"], str) or len(entry["sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in entry["sha256"])):
                raise _unsafe(f"Invalid snapshot file digest: {name}")
    if "" not in inventory["directories"]:
        raise _unsafe("Snapshot root metadata is missing")
    for name in (*inventory["files"], *inventory["directories"]):
        if name:
            parent = PurePosixPath(name).parent.as_posix()
            if ("" if parent == "." else parent) not in inventory["directories"]:
                raise _unsafe(f"Snapshot parent directory is missing: {name}")
    return inventory


def _read_snapshot(env: Path, snapshot: Path) -> dict:
    try:
        if {path.name for path in snapshot.iterdir()} != {"files", _MANIFEST, _COMPLETE}:
            raise _unsafe("Snapshot is incomplete or has unexpected entries")
        manifest_path = _child(snapshot, _MANIFEST)
        marker_path = _child(snapshot, _COMPLETE)
        raw = manifest_path.read_bytes()
        marker = json.loads(marker_path.read_bytes())
        if (not isinstance(marker, dict) or set(marker) != {"schema", "size", "sha256"}
                or marker["schema"] != 1 or marker["size"] != len(raw)
                or marker["sha256"] != hashlib.sha256(raw).hexdigest()):
            raise _unsafe("Snapshot manifest commit/hash mismatch")
        manifest = json.loads(raw)
        inventory = _validate_manifest(manifest, env, snapshot)
        payload = _child(snapshot, "files")
        if _scan(payload, include_cache=True) != inventory:
            raise _unsafe("Snapshot payload content/metadata differs from its manifest")
        return manifest
    except (OSError, ValueError, TypeError) as exc:
        raise _unsafe(f"Cannot verify snapshot: {exc}") from exc


def snapshot_environment(env_path: Path, snapshot_path: Path) -> dict:
    """Copy and commit a verified snapshot; refuse links and existing snapshots."""
    try:
        env, snapshot = _paths(env_path, snapshot_path, snapshot_required=False)
        if snapshot.exists():
            raise _unsafe("Snapshot path already exists; refusing to overwrite evidence")
        inventory = _scan(env)
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        _checked_path(snapshot.parent)
        snapshot.mkdir()
        payload = snapshot / "files"
        payload.mkdir()
        for name in sorted(inventory["directories"], key=lambda name: (name.count("/"), name)):
            if name:
                _child(payload, name, required=False).mkdir()
        for name, entry in sorted(inventory["files"].items()):
            _copy_file(_child(env, name), _child(payload, name, required=False), entry)
        for name in sorted(inventory["directories"], key=lambda name: (name.count("/"), name), reverse=True):
            _set_metadata(payload if not name else _child(payload, name), inventory["directories"][name])
        if _scan(env) != inventory or _scan(payload) != inventory:
            raise _unsafe("Environment changed during snapshot, or its copy is incomplete")
        receipt = {"schema": 1, "environment": str(env), "snapshot": str(snapshot),
                   "policy": dict(_POLICY), "inventory": inventory}
        _atomic_json(snapshot / _MANIFEST, receipt)
        raw = (snapshot / _MANIFEST).read_bytes()
        _atomic_json(snapshot / _COMPLETE, {"schema": 1, "size": len(raw),
                                          "sha256": hashlib.sha256(raw).hexdigest()})
        _read_snapshot(env, snapshot)
        return receipt
    except OSError as exc:
        raise _unsafe(f"Snapshot failed: {exc}") from exc


def verify_environment(env_path: Path, snapshot_path: Path) -> dict:
    """Read-only full snapshot and environment verification; raise on mismatch."""
    try:
        env, snapshot = _paths(env_path, snapshot_path, snapshot_required=True)
        manifest = _read_snapshot(env, snapshot)
        if _scan(env) != manifest["inventory"]:
            raise _unsafe("Environment differs from the committed snapshot")
        return {"verified": True, "files": len(manifest["inventory"]["files"]),
                "directories": len(manifest["inventory"]["directories"]), "snapshot": str(snapshot)}
    except OSError as exc:
        raise _unsafe(f"Environment verification failed: {exc}") from exc


def _unlink(env: Path, name: str) -> None:
    path = _child(env, name)
    info = _check_node(path)
    if not stat.S_ISREG(info.st_mode):
        raise _unsafe(f"Refusing to unlink a non-file: {path}")
    # pip can install read-only files; this only affects already-approved extras.
    if not info.st_mode & stat.S_IWUSR:
        os.chmod(path, stat.S_IMODE(info.st_mode) | stat.S_IWUSR)
    _child(env, name).unlink()


def restore_environment(env_path: Path, snapshot_path: Path) -> dict:
    """Restore files in place, then verify the entire inventory before success.

    Unchanged file bytes are reused, so a running, locked python.exe is never
    replaced just to restore the environment. An unexpected link, locked changed
    file, damaged snapshot or any partial restore prevents a success result.
    """
    try:
        env, snapshot = _paths(env_path, snapshot_path, snapshot_required=True)
        inventory = _read_snapshot(env, snapshot)["inventory"]  # Validate everything before mutation.
        current = _scan(env, include_cache=True)
        copied = reused = deleted = 0
        for name in sorted(set(current["files"]) - set(inventory["files"])):
            _unlink(env, name)
            deleted += 1
        for name in sorted(set(current["directories"]) - set(inventory["directories"]),
                           key=lambda name: (name.count("/"), name), reverse=True):
            _child(env, name).rmdir()  # Only already-inventoried empty directories; never rmtree(.venv).
        for name in sorted(inventory["directories"], key=lambda name: (name.count("/"), name)):
            if name:
                path = _child(env, name, required=False)
                if not path.exists():
                    path.mkdir()
        payload = _child(snapshot, "files")
        for name, expected in sorted(inventory["files"].items()):
            target = _child(env, name, required=False)
            if target.exists():
                live = _file_entry(target)
                if (live["size"], live["sha256"]) == (expected["size"], expected["sha256"]):
                    _set_metadata(target, expected)
                    reused += 1
                    continue
            # Windows derives executable permission bits from the extension.
            # Keep it on the private temporary file so mode verification is real.
            temp_name = name + ".restore-" + uuid.uuid4().hex + target.suffix
            temp = _child(env, temp_name, required=False)
            _copy_file(_child(payload, name), temp, expected)
            if target.exists() and not _check_node(target).st_mode & stat.S_IWUSR:
                os.chmod(target, stat.S_IMODE(_check_node(target).st_mode) | stat.S_IWUSR)
            _child(env, name, required=False)
            _child(env, temp_name)
            os.replace(temp, target)
            copied += 1
        for name in sorted(inventory["directories"], key=lambda name: (name.count("/"), name), reverse=True):
            _set_metadata(env if not name else _child(env, name), inventory["directories"][name])
        result = verify_environment(env, snapshot)
        return {**result, "copied": copied, "reused": reused, "deleted": deleted}
    except OSError as exc:
        raise _unsafe(f"Environment restore failed; recovery is incomplete: {exc}") from exc
