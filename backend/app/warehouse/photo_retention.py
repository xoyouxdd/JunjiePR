"""Keep the metadata of formerly displayed photos after clearing their image data."""
from calendar import monthrange
from datetime import datetime

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.database.connection import write_transaction
from app.warehouse.models import (
    WarehouseIssue, WarehousePhoto, WarehousePosition, WarehousePositionPhotoRevision, WarehousePositionPhotoTask,
    WarehouseTransaction, WarehouseTransactionPhoto, warehouse_now,
)


def record_location_photo(db: Session, position: WarehousePosition, photo: WarehousePhoto) -> None:
    revision = WarehousePositionPhotoRevision
    number = db.query(func.max(revision.number)).filter_by(position_id=position.id).scalar() or 0
    # Only the active photo is known when upgrading an existing installation.
    if number == 0 and position.latest_photo_id:
        number = 1
        db.add(revision(position_id=position.id, number=number, photo_id=position.latest_photo_id))
    db.add(revision(position_id=position.id, number=number + 1, photo_id=photo.id))


def two_months_after(taken: datetime) -> datetime:
    year, month = divmod(taken.year * 12 + taken.month - 1 + 2, 12)
    month += 1
    return taken.replace(year=year, month=month, day=min(taken.day, monthrange(year, month)[1]))


def clear_old_location_photos(db: Session, now: datetime | None = None) -> int:
    with write_transaction(db):
        now = now or warehouse_now()
        revision = WarehousePositionPhotoRevision
        current = db.query(revision.position_id, func.max(revision.number).label("number")).group_by(
            revision.position_id
        ).subquery()
        replacements = current.c.number - revision.number
        unsettled_order = db.query(WarehouseTransactionPhoto.photo_id).join(
            WarehouseTransaction, WarehouseTransaction.id == WarehouseTransactionPhoto.transaction_id,
        ).outerjoin(WarehouseIssue, WarehouseIssue.transaction_id == WarehouseTransaction.id).filter(
            or_(WarehouseTransaction.status == "pending", WarehouseIssue.status != "closed")
        )
        open_issue = db.query(WarehouseIssue.photo_id).filter(
            WarehouseIssue.status != "closed", WarehouseIssue.photo_id.is_not(None),
        )
        open_photo_report = db.query(WarehousePositionPhotoTask.photo_id).filter(
            WarehousePositionPhotoTask.closed_at.is_(None), WarehousePositionPhotoTask.photo_id.is_not(None),
        )
        candidates = db.query(revision, WarehousePhoto, replacements.label("replacements")).join(
            current, current.c.position_id == revision.position_id,
        ).join(WarehousePhoto, WarehousePhoto.id == revision.photo_id).join(
            WarehousePosition, WarehousePosition.id == revision.position_id,
        ).filter(
            revision.cleared_at.is_(None),
            or_(replacements >= 8, and_(replacements >= 4, replacements <= 6)),
            WarehousePosition.latest_photo_id != revision.photo_id,
            ~revision.photo_id.in_(unsettled_order),
            ~revision.photo_id.in_(open_issue),
            ~revision.photo_id.in_(open_photo_report),
        ).order_by(replacements.desc()).limit(100).all()
        removed = 0
        for row, photo, count in candidates:
            if count >= 8 or now > two_months_after(photo.captured_at):
                photo.content = b""
                row.cleared_at = now
                removed += 1
        db.commit()
    return removed
