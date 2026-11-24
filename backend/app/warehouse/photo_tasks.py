"""Shared follow-up for current location photos. Inventory updates own the write transaction."""
from collections import defaultdict
from datetime import datetime, time, timedelta
from os import getenv

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.v2_models import AuditLog, Employee
from app.warehouse.access import active_owner_ids, can_dispatch
from app.warehouse.models import WarehousePhoto, WarehousePosition, WarehousePositionPhotoTask, warehouse_now


PHOTO_REPORT_ACTION = "报告货位照片问题"


def request_location_photo(db: Session, position: WarehousePosition, now: datetime,
                           owner_id: int | None, transaction_id: int | None = None) -> WarehousePositionPhotoTask:
    task = db.get(WarehousePositionPhotoTask, position.id)
    if task is None:
        task = WarehousePositionPhotoTask(position_id=position.id, opened_at=now, owner_id=owner_id)
        db.add(task)
    elif task.closed_at is not None:
        task.opened_at, task.owner_id = now, owner_id
        task.photo_id = None
    task.target_version = position.version
    task.requested_at = now
    task.source_transaction_id = transaction_id
    task.closed_at = task.closed_by_id = None
    position.latest_photo_version = None
    db.flush()
    return task


def sync_open_photo_version(db: Session, position: WarehousePosition) -> None:
    task = db.get(WarehousePositionPhotoTask, position.id)
    if task is not None and task.closed_at is None:
        task.target_version = position.version


def photo_covers_open_task(db: Session, position_id: int, photo: WarehousePhoto) -> bool:
    task = db.get(WarehousePositionPhotoTask, position_id)
    return task is None or task.closed_at is not None or photo.captured_at >= task.requested_at


def close_photo_task(db: Session, position: WarehousePosition, photo: WarehousePhoto, now: datetime) -> None:
    task = db.get(WarehousePositionPhotoTask, position.id)
    if (task is not None and task.closed_at is None and task.target_version == position.version
            and position.latest_photo_version == position.version and photo.captured_at >= task.requested_at):
        task.closed_at, task.closed_by_id, task.photo_id = now, photo.owner_id, photo.id


def release_owner_photo_tasks(db: Session, employee_id: int, replacement_id: int) -> None:
    for task in db.query(WarehousePositionPhotoTask).filter_by(owner_id=employee_id, closed_at=None):
        task.owner_id = replacement_id


def open_photo_tasks(db: Session):
    return db.query(WarehousePositionPhotoTask).filter(WarehousePositionPhotoTask.closed_at.is_(None))


def handover_cutoff(now: datetime) -> datetime:
    try:
        clock = time.fromisoformat(getenv("RECOGNITION_WAREHOUSE_HANDOVER_AT", "20:00"))
        if clock.tzinfo is not None:
            clock = time(20)
    except ValueError:
        clock = time(20)
    today = datetime.combine(now.date(), clock)
    return today if now >= today else today - timedelta(days=1)


def photo_reports(db: Session, photo_ids: set[str]) -> dict[str, list[dict]]:
    reports = defaultdict(list)
    if not photo_ids:
        return reports
    photo_id = func.json_extract(AuditLog.after_json, "$.photo_id")
    rows = db.query(
        photo_id, AuditLog.operator_name, AuditLog.created_at,
        func.json_extract(AuditLog.after_json, "$.note"),
        func.json_extract(AuditLog.after_json, "$.reported_at"),
    ).filter(
        AuditLog.action == PHOTO_REPORT_ACTION, AuditLog.entity_type == "warehouse_position",
        photo_id.in_(photo_ids),
    ).order_by(AuditLog.id.desc())
    for image, person, created, note, reported_at in rows:
        reports[image].append({"reporter_name": person, "note": note, "reported_at": reported_at or created.isoformat()})
    return reports


def photo_task_list(db: Session, *, include_reports: bool = False) -> list[dict]:
    rows = open_photo_tasks(db).order_by(WarehousePositionPhotoTask.opened_at, WarehousePositionPhotoTask.position_id).all()
    if not rows:
        return []
    owners = active_owner_ids(db)
    fallback = min(owners, default=None)
    names = dict(db.query(Employee.id, Employee.name).filter(Employee.id.in_(owners)).all())
    cutoff = handover_cutoff(warehouse_now())
    reports = photo_reports(db, {task.photo_id for task in rows if task.photo_id}) if include_reports else {}
    return [{
        "position_id": task.position_id,
        "version": task.target_version,
        "requested_at": task.requested_at.isoformat(),
        "owner_name": names.get(task.owner_id if task.owner_id in owners else fallback),
        "source_transaction_id": task.source_transaction_id,
        "handover_due": task.opened_at <= cutoff,
        "reports": reports.get(task.photo_id, []),
    } for task in rows]


def photo_reminder_counts(db: Session, user) -> tuple[int, int]:
    if not can_dispatch(user) or user.id not in active_owner_ids(db):
        return 0, 0
    query = open_photo_tasks(db)
    count = query.count()
    if not count:
        return 0, 0
    return count, query.filter(WarehousePositionPhotoTask.opened_at <= handover_cutoff(warehouse_now())).count()
