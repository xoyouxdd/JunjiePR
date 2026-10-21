"""Exercise actual snapshot objects and isolated restores with synthetic data."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

spec = importlib.util.spec_from_file_location('recovery_bundle', Path(__file__).resolve().parents[2] / 'scripts/recovery_bundle.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


@pytest.fixture
def fixture(tmp_path):
    live = tmp_path / 'live'
    (live / 'app').mkdir(parents=True)
    (live / 'app/version.py').write_text('APP_VERSION = "fixture"', encoding='utf-8')
    (live / 'data_v2/files').mkdir(parents=True)
    content = b'synthetic attachment'
    for key in ('one.png', 'two.png'):
        (live / 'data_v2/files' / key).write_bytes(content)
    database = tmp_path / 'online-snapshot.db'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE stored_files(storage_key TEXT, sha256 TEXT, file_size INTEGER, status TEXT)')
        for key in ('one.png', 'two.png'):
            db.execute('INSERT INTO stored_files VALUES(?,?,?,?)', (key, hashlib.sha256(content).hexdigest(), len(content), 'active'))
    (live / 'requirements.txt').write_text('synthetic-dependency==1', encoding='utf-8')
    entries = []
    for name in ('app/version.py', 'requirements.txt'):
        p = live / name
        entries.append({'path': 'backend/' + name, 'size': p.stat().st_size, 'sha256': recovery.digest(p)})
    (live / 'release-manifest.json').write_text(json.dumps({'app_version': 'fixture', 'git_commit': 'fixture', 'files': entries}), encoding='utf-8')
    return live, database, tmp_path / 'backups/recovery'


def test_capture_deduplicates_and_restores_database_code_and_attachments(fixture, tmp_path):
    live, database, store = fixture
    source_hash = recovery.digest(database)
    first = recovery.capture(live, database, store)
    assert first['active_attachments'] == 2
    object_count = len(list((store / 'objects').iterdir()))
    second = recovery.capture(live, database, store)
    assert len(list((store / 'objects').iterdir())) == object_count
    assert recovery.verify(store)['ok']
    result = recovery.restore(store, tmp_path / 'rehearsal', Path(second['manifest']))
    assert result['ok']
    assert recovery.digest(tmp_path / 'rehearsal/data_v2/recognition_v2.db') == source_hash
    assert (tmp_path / 'rehearsal/data_v2/files/two.png').read_bytes() == b'synthetic attachment'
    assert (tmp_path / 'rehearsal/app/version.py').read_bytes() == (live / 'app/version.py').read_bytes()
    assert recovery.digest(database) == source_hash


def test_learning_video_persistence_and_ephemeral_cache_boundaries(fixture,tmp_path):
    live,database,store=fixture
    directory=live/'data_v2/files/learning-videos/videos/1';directory.mkdir(parents=True)
    for name in ('source.mov','master-720.mp4','cover.jpg'):
        (directory/name).write_bytes(b'video')
    (directory/'master-1080.new.mp4').write_bytes(b'partial')
    cache=live/'data_v2/files/learning-videos/playbacks/demo';cache.mkdir(parents=True);(cache/'video.mp4').write_bytes(b'cache')
    uploads=live/'data_v2/files/learning-videos/uploads';uploads.mkdir();(uploads/'demo.part').write_bytes(b'partial')
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE learning_videos(id INTEGER,status TEXT,original_bytes INTEGER,profiles TEXT)')
        db.execute('INSERT INTO learning_videos VALUES(1,?,?,?)',('published',5,json.dumps({'720':{'bytes':5}})))
        db.execute('CREATE TABLE learning_video_circles(video_id INTEGER,attraction_id INTEGER,PRIMARY KEY(video_id,attraction_id))')
        db.executemany('INSERT INTO learning_video_circles VALUES(1,?)',[(1,),(2,),(3,)])
        db.execute('CREATE TABLE learning_video_audiences(video_id INTEGER PRIMARY KEY,local_attraction_id INTEGER,codes TEXT,revision INTEGER,replaces_revision INTEGER)')
        db.execute('INSERT INTO learning_video_audiences VALUES(1,1,?,2,NULL)',(json.dumps(['all_tr','all_lead']),))
    manifest=Path(recovery.capture(live,database,store)['manifest'])
    data=json.loads(manifest.read_text(encoding='utf-8'))
    assert 'data_v2/files/learning-videos/videos/1/source.mov' in data['entries']
    assert 'data_v2/files/learning-videos/videos/1/master-720.mp4' in data['entries']
    assert 'data_v2/files/learning-videos/videos/1/cover.jpg' in data['entries']
    assert not any('/playbacks/' in name or '/uploads/' in name or '.new.mp4' in name for name in data['entries'])
    recovery.restore(store,tmp_path/'video-restore',manifest)
    assert (tmp_path/'video-restore/data_v2/files/learning-videos/videos/1/source.mov').read_bytes()==b'video'
    with sqlite3.connect(tmp_path/'video-restore/data_v2/recognition_v2.db') as db:
        assert db.execute('SELECT attraction_id FROM learning_video_circles WHERE video_id=1 ORDER BY attraction_id').fetchall()==[(1,),(2,),(3,)]
        assert db.execute('SELECT local_attraction_id,codes,revision FROM learning_video_audiences WHERE video_id=1').fetchone()==(1,json.dumps(['all_tr','all_lead']),2)
    (directory/'source.mov').unlink()
    with pytest.raises(ValueError,match='learning video'):
        recovery.capture(live,database,store)


@pytest.mark.parametrize('failure', ['missing', 'content', 'size'])
def test_capture_rejects_missing_or_changed_active_attachment(fixture, failure):
    live, database, store = fixture
    file = live / 'data_v2/files/one.png'
    if failure == 'missing':
        file.unlink()
    elif failure == 'content':
        file.write_bytes(b'x' * file.stat().st_size)
    else:
        file.write_bytes(b'wrong size')
    with pytest.raises(ValueError):
        recovery.capture(live, database, store)
    assert not list((store / 'manifests').glob('*.json'))


def test_existing_corrupt_object_is_never_accepted(fixture):
    live, database, store = fixture
    recovery.capture(live, database, store)
    sha = recovery.digest(live / 'data_v2/files/one.png')
    (store / 'objects' / sha).write_bytes(b'x' * len(b'synthetic attachment'))
    with pytest.raises(ValueError):
        recovery.verify(store)
    with pytest.raises(ValueError):
        recovery.capture(live, database, store)


def test_restore_rejects_traversal_and_existing_destination(fixture, tmp_path):
    live, database, store = fixture
    manifest = Path(recovery.capture(live, database, store)['manifest'])
    with pytest.raises(ValueError):
        recovery.restore(store, live)
    data = json.loads(manifest.read_text(encoding='utf-8'))
    data['entries']['../escape'] = next(iter(data['entries'].values()))
    manifest.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError):
        recovery.restore(store, tmp_path / 'isolated')
    assert not (tmp_path / 'escape').exists()


def test_capture_requires_exact_deployed_source(fixture):
    live, database, store = fixture
    (live / 'app/version.py').write_text('changed source', encoding='utf-8')
    with pytest.raises(ValueError):
        recovery.capture(live, database, store)


def test_capture_rejects_live_database_file(fixture):
    live, database, store = fixture
    live_database = live / 'data_v2/recognition_v2.db'
    live_database.write_bytes(database.read_bytes())
    with pytest.raises(ValueError, match='separate online snapshot'):
        recovery.capture(live, live_database, store)


def test_reparse_sources_are_rejected(fixture, tmp_path):
    live, database, store = fixture
    outside = tmp_path / 'outside'
    outside.mkdir()
    try:
        (live / 'data_v2/files/linked').symlink_to(outside, target_is_directory=True)
    except OSError:
        import sys
        if sys.platform != 'win32':
            raise
        result = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(live / 'data_v2/files/linked'), str(outside)], capture_output=True)
        if result.returncode != 0:
            pytest.skip('Creating a junction is unavailable on this filesystem')
    with pytest.raises(ValueError):
        recovery.capture(live, database, store)


def test_transient_file_access_failure_is_retried_but_integrity_failure_is_not(monkeypatch, fixture):
    live, database, store = fixture
    real_capture = recovery.capture
    attempts = []
    def transient(*args):
        attempts.append(1)
        if len(attempts) == 1:
            raise PermissionError('transient sharing lock')
        return real_capture(*args)
    monkeypatch.setattr(recovery, 'capture', transient)
    monkeypatch.setattr(recovery.time, 'sleep', lambda _: None)
    assert recovery.capture_with_retry(live, database, store)['attempts'] == 2
    assert len(attempts) == 2
    attempts.clear()
    def invalid(*args):
        attempts.append(1)
        raise ValueError('integrity failure')
    monkeypatch.setattr(recovery, 'capture', invalid)
    with pytest.raises(ValueError):
        recovery.capture_with_retry(live, database, store)
    assert len(attempts) == 1


def test_permanent_file_access_failure_stops_after_three_attempts(monkeypatch, fixture):
    attempts = []
    def denied(*args):
        attempts.append(1)
        raise PermissionError('permanent access failure')
    monkeypatch.setattr(recovery, 'capture', denied)
    monkeypatch.setattr(recovery.time, 'sleep', lambda _: None)
    with pytest.raises(PermissionError):
        recovery.capture_with_retry(*fixture)
    assert len(attempts) == 3
