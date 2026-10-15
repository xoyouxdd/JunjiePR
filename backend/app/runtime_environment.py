"""Environment identity and path guards without importing the database or creating files."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = BASE_DIR / "data_v2"
PRODUCTION_BACKUP_HEALTH_FILE = Path(
    r"C:\Server\zhaojunjie\backups\recognition-card-system-sqlite\backup-health-status.json"
)
PRODUCTION_ROOT = Path(r"C:\Server\zhaojunjie")
ENVIRONMENTS = {"production", "local", "test"}


def within(path: Path, root: Path) -> bool:
    """Resolve links/junctions before comparing, including paths not yet created."""
    return path.resolve().is_relative_to(root.resolve())


def environment_kind(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    explicit = env.get("RECOGNITION_ENVIRONMENT", "").strip().lower()
    if explicit:
        if explicit not in ENVIRONMENTS:
            raise RuntimeError("RECOGNITION_ENVIRONMENT必须为production、local或test")
        return explicit
    configured = env.get("RECOGNITION_V2_DATA_DIR", "").strip()
    # Preserve the production default; an alternate directory is safe-by-default.
    return "local" if configured and Path(configured).resolve() != DEFAULT_DATA_DIR.resolve() else "production"


def data_directory(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    return Path(env.get("RECOGNITION_V2_DATA_DIR") or DEFAULT_DATA_DIR).resolve()


def backup_health_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    configured = env.get("RECOGNITION_BACKUP_HEALTH_STATUS_FILE", "").strip()
    if configured:
        return Path(configured).resolve()
    if environment_kind(env) == "production":
        return PRODUCTION_BACKUP_HEALTH_FILE
    return data_directory(env) / "backups" / "backup-health-status.json"


def validate_runtime_paths(*, data_dir: Path | None = None, health_file: Path | None = None,
                           environ: Mapping[str, str] | None = None) -> None:
    env = os.environ if environ is None else environ
    kind = environment_kind(env)
    if kind == "production":
        if env.get("RECOGNITION_ENABLE_TEST_ACCOUNTS", "").strip() == "1":
            raise RuntimeError("正式环境禁止启用测试账号")
        return
    data = Path(data_dir) if data_dir is not None else data_directory(env)
    if within(data, DEFAULT_DATA_DIR) or within(data, PRODUCTION_ROOT):
        raise RuntimeError("本地/测试环境必须使用独立数据目录，不能使用正式目录")
    health = Path(health_file) if health_file is not None else backup_health_path(env)
    # The backup uses the report's parent; equality with DATA_DIR is not a file.
    if not within(health.parent, data) or health.resolve() == (data / "recognition_v2.db").resolve():
        raise RuntimeError("本地/测试备份健康报告及手动备份必须位于独立数据目录内")
    for name in ("files", "exports", "backups", "recognition_v2.db", "recognition_v2.db-wal", "recognition_v2.db-shm"):
        if not within(data / name, data):
            raise RuntimeError("本地/测试存储文件或目录不能通过链接指向外部目录")


def require_test_initialization(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    if environment_kind(env) not in {"local", "test"} or env.get("RECOGNITION_ENABLE_TEST_ACCOUNTS", "").strip() != "1":
        raise RuntimeError("测试数据初始化需要独立环境及显式测试账号开关")
    if not env.get("RECOGNITION_V2_DATA_DIR", "").strip():
        raise RuntimeError("测试数据初始化必须显式指定独立数据目录")
    validate_runtime_paths(environ=env)
    return data_directory(env)


def environment_label() -> str:
    return {"production": "", "local": "本地测试", "test": "隔离测试"}[environment_kind()]
