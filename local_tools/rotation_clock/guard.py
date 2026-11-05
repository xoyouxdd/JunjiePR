"""Select a dedicated simulator database before importing the application."""
import json
import os
from pathlib import Path
from contextlib import contextmanager

MARKER = '.rotation-simulator.json'


@contextmanager
def exclusive_data(path):
    """Two simulator processes may not advance the same database independently."""
    stream = (path / '.simulator.lock').open('a+b')
    try:
        stream.seek(0); stream.write(b'0'); stream.flush(); stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        stream.close()
        raise RuntimeError('此独立数据目录已有模拟器在运行，请使用其他空目录') from exc
    try:
        yield
    finally:
        stream.close()


def prepare(directory):
    path = Path(directory).resolve()
    from app.runtime_environment import validate_runtime_paths
    env = {**os.environ, 'RECOGNITION_ENVIRONMENT': 'local',
           'RECOGNITION_V2_DATA_DIR': str(path),
           'RECOGNITION_BACKUP_HEALTH_STATUS_FILE': str(path / 'backups/backup-health-status.json')}
    validate_runtime_paths(environ=env)
    if path.exists() and any(path.iterdir()):
        verify(path)
    else:
        path.mkdir(parents=True, exist_ok=True)
        (path / MARKER).write_text(json.dumps({'kind': 'rotation-simulator-v1', 'path': str(path)}), encoding='utf-8')
    for key in ('RECOGNITION_ENVIRONMENT', 'RECOGNITION_V2_DATA_DIR', 'RECOGNITION_BACKUP_HEALTH_STATUS_FILE'):
        os.environ[key] = env[key]
    os.environ['RECOGNITION_ROTATION_SIMULATOR'] = '1'
    return path


def verify(path=None):
    path = Path(path or os.environ.get('RECOGNITION_V2_DATA_DIR', '')).resolve()
    try:
        value = json.loads((path / MARKER).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise RuntimeError('仅允许使用本模拟器创建的独立空目录，禁止接入已有业务数据库') from exc
    if value != {'kind': 'rotation-simulator-v1', 'path': str(path)}:
        raise RuntimeError('模拟器数据目录标识不匹配')
    return path


def require_local():
    from app.runtime_environment import environment_kind, validate_runtime_paths
    if environment_kind() not in {'local', 'test'} or os.environ.get('RECOGNITION_ROTATION_SIMULATOR') != '1':
        raise RuntimeError('轮岗测试扩展只能由独立本地入口加载')
    path = verify()
    validate_runtime_paths(data_dir=path)
    from app.database.connection import DATA_DIR
    if path != DATA_DIR.resolve():
        raise RuntimeError('数据库已先于模拟器初始化，拒绝连接')
    return path
