"""Versioned recovery manifests and immutable shared objects; never restores over live data."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import sys
import time
from uuid import uuid4


def checked(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    for parent in reversed((path, *path.parents)):
        if not parent.exists():
            continue
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Recovery paths cannot contain links or reparse points')
    return path


def digest(path: Path) -> str:
    checked(path)
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def relative(value: str) -> str:
    if not isinstance(value, str) or '\\' in value or ':' in value:
        raise ValueError('Unsafe recovery entry')
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(p in {'..', '.'} for p in value.split('/')):
        raise ValueError('Unsafe recovery entry')
    if path.as_posix() != value:
        raise ValueError('Noncanonical recovery entry')
    return value


def put_object(source: Path, objects: Path, expected: str | None = None) -> dict:
    checked(source)
    checked(objects)
    if expected and not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('Invalid expected file hash')
    if expected:
        cached = checked(objects / expected)
        if cached.exists():
            if cached.stat().st_size != source.stat().st_size or digest(cached) != expected:
                raise ValueError('Existing recovery object failed integrity check')
            return {'sha256': expected, 'size': cached.stat().st_size}
    partial = checked(objects / ('.partial-' + uuid4().hex))
    checksum = hashlib.sha256()
    try:
        with source.open('rb') as src, partial.open('xb') as dest:
            while chunk := src.read(1024 * 1024):
                checksum.update(chunk)
                dest.write(chunk)
        actual = checksum.hexdigest()
        if expected and actual != expected:
            raise ValueError('Attachment content does not match the database snapshot')
        final = checked(objects / actual)
        size = partial.stat().st_size
        if final.exists():
            if final.stat().st_size != size or digest(final) != actual:
                raise ValueError('Recovery object collision or corruption')
        else:
            partial.rename(final)
        return {'sha256': actual, 'size': size}
    finally:
        if partial.exists():
            partial.unlink()


def walk_files(root: Path):
    checked(root)
    for entry in sorted(root.iterdir()):
        checked(entry)
        if entry.name in {'__pycache__', '.pytest_cache'}:
            continue
        if entry.is_dir():
            yield from walk_files(entry)
        elif entry.is_file() and entry.suffix != '.pyc':
            yield entry
        else:
            raise ValueError('Unsupported recovery source')


def capture(live: Path, database: Path, store: Path, *, test_mode=False) -> dict:
    started = time.monotonic()
    live, database, store = checked(live), checked(database), checked(store)
    if store.is_relative_to(live) or live.is_relative_to(store):
        raise ValueError('Recovery store must be separate from the live application')
    if database.is_relative_to(live):
        raise ValueError('Use a separate online snapshot, never copy the live database file')
    if not database.is_file():
        raise ValueError('An existing online database snapshot is required')
    objects = checked(store / 'objects')
    manifests = checked(store / 'manifests')
    objects.mkdir(parents=True, exist_ok=True)
    manifests.mkdir(exist_ok=True)
    entries = {}
    for tree in ('app', 'data_v2/files'):
        source_root = checked(live / tree)
        if not source_root.is_dir():
            raise ValueError('Required application or attachment directory is missing')
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        if [r[0] for r in db.execute('PRAGMA quick_check')] != ['ok']:
            raise ValueError('Database snapshot integrity check failed')
        expected_files = db.execute("SELECT storage_key,sha256,file_size FROM stored_files WHERE status='active'").fetchall()
        has_videos = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='learning_videos'").fetchone()
        videos = db.execute("SELECT id,status,original_bytes,profiles FROM learning_videos").fetchall() if has_videos else []
    expected = {}
    for key, sha, size in expected_files:
        key = relative(key)
        source = checked(live / 'data_v2/files' / key)
        if not source.is_file() or source.stat().st_size != size:
            raise ValueError('An active attachment is missing or has an unexpected size')
        expected['data_v2/files/' + key] = sha.lower()
    for video_id, status, original_bytes, profiles in videos:
        directory = live / 'data_v2/files/learning-videos/videos' / str(int(video_id))
        files = {'source.mov': original_bytes}
        if status in {'ready', 'published', 'withdrawn'}:
            files.update({f'master-{quality}.mp4': profile['bytes'] for quality, profile in json.loads(profiles).items()})
            files['cover.jpg'] = None
        for filename, size in files.items():
            source = checked(directory / relative(filename))
            if not source.is_file() or (size is not None and source.stat().st_size != size):
                raise ValueError('A learning video source or published derivative is missing or has an unexpected size')
            expected[relative(source.relative_to(live).as_posix())] = digest(source)
    provenance = live / 'release-manifest.json'
    if not provenance.is_file() and not test_mode:
        raise ValueError('The deployed version manifest is required')
    release = json.loads(provenance.read_text(encoding='utf-8-sig')) if provenance.is_file() else {}
    if not test_mode:
        for item in release['files']:
            name = item['path']
            if name.startswith('backend/app/') or name == 'backend/requirements.txt':
                source = checked(live / relative(name.removeprefix('backend/')))
                if source.stat().st_size != item['size'] or digest(source) != item['sha256'].lower():
                    raise ValueError('Deployed source differs from its release manifest')
    entries['data_v2/recognition_v2.db'] = put_object(database, objects)
    for tree in ('app', 'data_v2/files'):
        for source in walk_files(live / tree):
            name = relative(source.relative_to(live).as_posix())
            # Only this feature's regenerable personal cache and incomplete
            # uploads are excluded. Existing active attachments always win.
            if name not in expected and (name.startswith('data_v2/files/learning-videos/playbacks/')
                or name.startswith('data_v2/files/learning-videos/uploads/')
                or (name.startswith('data_v2/files/learning-videos/videos/') and (source.name == 'cover.new.jpg' or re.fullmatch(r'(?:frame(?:-[0-9a-f]{32})?\.jpg|master-(?:720|1080)(?:\.[0-9a-f]{32})?\.new\.mp4)', source.name)))):
                continue
            entries[name] = put_object(source, objects, expected.get(name))
    for name in ('requirements.txt', 'release-manifest.json'):
        source = live / name
        if source.exists():
            entries[name] = put_object(source, objects)
    if not test_mode:
        for item in release['files']:
            name = item['path']
            if name.startswith('backend/app/') or name == 'backend/requirements.txt':
                captured = entries.get(name.removeprefix('backend/'))
                if not captured or captured['size'] != item['size'] or captured['sha256'] != item['sha256'].lower():
                    raise ValueError('Application source changed during recovery capture')
    # Task XML/runtime inventory is exported by the PowerShell wrapper; no secret values.
    config = live / 'ops/recovery-config'
    if config.is_dir():
        for source in walk_files(config):
            name = 'recovery-config/' + source.relative_to(config).as_posix()
            entries[relative(name)] = put_object(source, objects)
    result = {'schema_version': 1, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
              'app_version': release.get('app_version'), 'git_commit': release.get('git_commit'),
              'entries': entries, 'active_attachment_count': len(expected_files),
              'source_database_snapshot': database.name}
    name = 'recovery-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:8] + '.json'
    target = checked(manifests / name)
    partial = target.with_suffix('.partial')
    partial.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    partial.rename(target)
    return {'ok': True, 'manifest': str(target), 'app_version': result['app_version'],
            'entries': len(entries), 'active_attachments': len(expected_files),
            'logical_bytes': sum(e['size'] for e in entries.values()),
            'elapsed_seconds': round(time.monotonic() - started, 2)}


def load_manifest(store: Path, manifest: Path | None = None, max_age_hours=26):
    store = checked(store)
    if manifest is None:
        files = sorted(checked(store / 'manifests').glob('recovery-*.json'))
        if not files:
            raise ValueError('No completed recovery manifest exists')
        manifest = files[-1]
    manifest = checked(manifest)
    if manifest.parent != store / 'manifests':
        raise ValueError('Recovery manifest escaped its dedicated store')
    data = json.loads(manifest.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or not data.get('entries'):
        raise ValueError('Invalid recovery manifest')
    created = datetime.fromisoformat(data['created_at_utc'])
    age = (datetime.now(timezone.utc) - created).total_seconds() / 3600
    if age < -0.1 or age > max_age_hours:
        raise ValueError('Recovery manifest is stale or has a future timestamp')
    if 'data_v2/recognition_v2.db' not in data['entries']:
        raise ValueError('Recovery manifest has no database')
    seen = set()
    for name, entry in data['entries'].items():
        relative(name)
        if name.casefold() in seen:
            raise ValueError('Duplicate case-insensitive recovery path')
        seen.add(name.casefold())
        sha = entry['sha256']
        if not re.fullmatch('[0-9a-f]{64}', sha) or not isinstance(entry['size'], int) or entry['size'] < 0:
            raise ValueError('Invalid recovery object metadata')
        source = checked(store / 'objects' / sha)
        if not source.is_file() or source.stat().st_size != entry['size'] or digest(source) != sha:
            raise ValueError('Recovery object failed integrity check')
    return manifest, data


def verify(store: Path, manifest=None, max_age_hours=26):
    path, data = load_manifest(store, manifest, max_age_hours)
    return {'ok': True, 'manifest': path.name, 'app_version': data.get('app_version'),
            'entries': len(data['entries']), 'active_attachments': data['active_attachment_count']}


def capture_with_retry(live: Path, database: Path, store: Path) -> dict:
    # Windows scanners and sharing locks can briefly deny file access. Retry
    # only that OS error; integrity failures and permanent access failures fail.
    for attempt in range(3):
        try:
            result = capture(live, database, store)
            result['attempts'] = attempt + 1
            return result
        except PermissionError:
            if attempt == 2:
                raise
            time.sleep(2 ** (attempt + 1))


def restore(store: Path, destination: Path, manifest=None):
    # Require a brand-new dedicated directory and prohibit the entire production tree.
    store, destination = checked(store), checked(destination)
    production = Path(r'C:\Server\zhaojunjie').absolute()
    if destination.exists() or destination.is_relative_to(production) or destination.is_relative_to(store.absolute()) or store.absolute().is_relative_to(destination):
        raise ValueError('Restore requires a new isolated directory outside production and backup roots')
    started = time.monotonic()
    path, data = load_manifest(store, manifest, max_age_hours=24 * 3650)
    destination.mkdir(parents=True)
    for name, entry in data['entries'].items():
        target = checked(destination / relative(name))
        if not target.is_relative_to(destination):
            raise ValueError('Restore target escaped its dedicated root')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(checked(store / 'objects' / entry['sha256']), target)
        if digest(target) != entry['sha256']:
            raise ValueError('Restored file failed integrity check')
    return {'ok': True, 'manifest': path.name, 'destination': str(destination),
            'entries': len(data['entries']), 'elapsed_seconds': round(time.monotonic() - started, 2)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('capture', 'verify', 'restore'))
    parser.add_argument('--store', required=True, type=Path)
    parser.add_argument('--live-root', type=Path)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--max-age-hours', type=float, default=26)
    args = parser.parse_args()
    try:
        if args.action == 'capture':
            if not args.live_root or not args.database:
                parser.error('capture requires --live-root and --database')
            result = capture_with_retry(args.live_root, args.database, args.store)
        elif args.action == 'verify':
            result = verify(args.store, args.manifest, args.max_age_hours)
        else:
            if not args.destination:
                parser.error('restore requires --destination')
            result = restore(args.store, args.destination, args.manifest)
        print(json.dumps(result))
    except Exception as error:
        # Avoid disclosing attachment keys, account data or configuration contents.
        print(json.dumps({'ok': False, 'error': type(error).__name__, 'message': 'Recovery operation failed validation'}))
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
