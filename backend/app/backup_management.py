"""Admin-only manual SQLite backup and one-report action-center dismissal."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import logging
from pathlib import Path
import uuid

from sqlalchemy import or_, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.operations_health import BACKUP_HEALTH_STATUS_PATH
from app.runtime_environment import validate_runtime_paths
from app.sqlite_backup import create_online_backup
from app.database.connection import DB_PATH, SessionLocal
from app.v2_models import AuditLog, Employee, SystemJobRun
from app.services.audit import write_audit


JOB_TYPE = "manual_sqlite_backup"
JOB_KEY = "single_active_job"
DISMISS_ACTION = "清除备份待办"
DISMISS_HOURS = 24
STALE_JOB_HOURS = 2
SUCCESS_COOLDOWN_MINUTES = 10
logger = logging.getLogger(__name__)


def health_fingerprint(health: dict) -> str:
    content = {"checked_at_utc": health.get("checked_at_utc"), "issues": health.get("issues", [])}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def backup_todo_dismissed(db: Session, health: dict) -> bool:
    if health.get("ok"):
        return False
    latest = (
        db.query(AuditLog)
        .filter(AuditLog.action == DISMISS_ACTION, AuditLog.entity_type == "backup_health")
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .first()
    )
    if not latest or latest.created_at < datetime.now() - timedelta(hours=DISMISS_HOURS):
        return False
    try:
        return json.loads(latest.after_json or "{}").get("fingerprint") == health_fingerprint(health)
    except (ValueError, TypeError):
        return False


def manual_backup_status(db: Session) -> dict:
    row = db.query(SystemJobRun).filter_by(job_type=JOB_TYPE, idempotency_key=JOB_KEY).first()
    if not row:
        return {"status": "idle", "started_at": "", "completed_at": "", "file_name": ""}
    try:
        result = json.loads(row.result or "{}")
    except (ValueError, TypeError):
        result = {}
    status = "interrupted" if row.status == "running" and row.started_at < datetime.now() - timedelta(hours=STALE_JOB_HOURS) else row.status
    return {
        "status": status,
        "started_at": row.started_at.isoformat() if row.started_at else "",
        "completed_at": row.completed_at.isoformat() if row.completed_at else "",
        "file_name": str(result.get("file_name") or ""),
    }


def claim_manual_backup(db: Session, operator: Employee, ip_address: str | None) -> str | None:
    now = datetime.now()
    token = uuid.uuid4().hex
    db.execute(
        insert(SystemJobRun).values(
            job_type=JOB_TYPE, business_date=now.date().isoformat(), idempotency_key=JOB_KEY,
            status="idle", started_at=now,
        ).on_conflict_do_nothing(index_elements=["job_type", "idempotency_key"])
    )
    db.commit()
    claimed = db.execute(
        update(SystemJobRun)
        .where(SystemJobRun.job_type == JOB_TYPE, SystemJobRun.idempotency_key == JOB_KEY)
        .where(or_(SystemJobRun.status != "running", SystemJobRun.started_at < now - timedelta(hours=STALE_JOB_HOURS)))
        .where(or_(SystemJobRun.status != "completed", SystemJobRun.completed_at.is_(None), SystemJobRun.completed_at < now - timedelta(minutes=SUCCESS_COOLDOWN_MINUTES)))
        .values(status="running", business_date=now.date().isoformat(), started_at=now, completed_at=None, result=json.dumps({"token": token}))
    ).rowcount
    if not claimed:
        db.rollback()
        return None
    write_audit(db, operator, "发起手动数据库备份", "backup_job", JOB_KEY, ip_address=ip_address)
    db.commit()
    return token


def _create_backup() -> str:
    """Create one verified snapshot in the configured backup directory; never prune old files."""
    source_path = Path(DB_PATH).resolve()
    validate_runtime_paths(data_dir=source_path.parent, health_file=BACKUP_HEALTH_STATUS_PATH)
    backup_root = BACKUP_HEALTH_STATUS_PATH.parent.resolve()
    if not source_path.is_file():
        raise FileNotFoundError("Source database is missing")
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S-%f")[:19]
    file_name = f"recognition_v2-{stamp}.db"
    create_online_backup(source_path, backup_root / file_name, retention_days=30)
    return file_name


def run_manual_backup(operator_id: int, ip_address: str | None, token: str) -> None:
    try:
        file_name = _create_backup()
    except Exception:
        logger.exception("Manual SQLite backup failed")
        status, result, action = "failed", {"error": "backup_failed"}, "手动数据库备份失败"
    else:
        status, result, action = "completed", {"file_name": file_name}, "手动数据库备份完成"
    with SessionLocal() as db:
        row = db.query(SystemJobRun).filter_by(job_type=JOB_TYPE, idempotency_key=JOB_KEY).first()
        if row and json.loads(row.result or "{}").get("token") == token:
            row.status = status
            row.result = json.dumps(result, ensure_ascii=False)
            row.completed_at = datetime.now()
        write_audit(db, db.get(Employee, operator_id), action, "backup_job", JOB_KEY, after=result, ip_address=ip_address)
        db.commit()
