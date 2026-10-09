"""Explicit one-off ATTENDANCE maintenance, never part of application startup."""
from __future__ import annotations

from app.database.connection import FILE_DIR


def legacy_attendance_cleanup_preview(db) -> dict:
    """Describe leftover ATTENDANCE catalog rows without deleting them."""
    from app.v2_models import AuditLog, DeductionRecord, DeductionType, StoredFile

    legacy_attendance = db.query(DeductionType).filter(DeductionType.code == "ATTENDANCE").first()
    if not legacy_attendance:
        return {"found": False, "type_id": None, "record_ids": [], "file_keys": [], "audit_count": 0}
    legacy_records = db.query(DeductionRecord).filter(DeductionRecord.deduction_type_id == legacy_attendance.id).all()
    legacy_ids = [str(row.id) for row in legacy_records]
    legacy_files = [db.get(StoredFile, row.document_file_id) for row in legacy_records]
    audit_count = 0
    if legacy_ids:
        audit_count = (
            db.query(AuditLog)
            .filter(AuditLog.entity_type == "deduction", AuditLog.entity_id.in_(legacy_ids))
            .count()
        )
    return {
        "found": True,
        "type_id": legacy_attendance.id,
        "record_ids": [row.id for row in legacy_records],
        "file_keys": [file_row.storage_key for file_row in legacy_files if file_row],
        "audit_count": audit_count,
    }


def purge_legacy_attendance(db, *, apply: bool = False) -> dict:
    """One-off ATTENDANCE cleanup. Default is dry-run; startup must not call this."""
    from app.v2_models import AuditLog, DeductionRecord, DeductionType, StoredFile

    preview = legacy_attendance_cleanup_preview(db)
    preview["mode"] = "apply" if apply else "dry-run"
    preview["applied"] = False
    if not apply or not preview["found"]:
        return preview
    legacy_attendance = db.query(DeductionType).filter(DeductionType.code == "ATTENDANCE").first()
    legacy_records = db.query(DeductionRecord).filter(DeductionRecord.deduction_type_id == legacy_attendance.id).all()
    legacy_ids = [str(row.id) for row in legacy_records]
    legacy_files = [db.get(StoredFile, row.document_file_id) for row in legacy_records]
    if legacy_ids:
        db.query(AuditLog).filter(AuditLog.entity_type == "deduction", AuditLog.entity_id.in_(legacy_ids)).delete(synchronize_session=False)
    for row in legacy_records:
        db.delete(row)
    db.flush()
    for file_row in legacy_files:
        if file_row:
            db.delete(file_row)
    db.delete(legacy_attendance)
    db.commit()
    for storage_key in preview["file_keys"]:
        path = (FILE_DIR / storage_key).resolve()
        if FILE_DIR.resolve() in path.parents:
            path.unlink(missing_ok=True)
    preview["applied"] = True
    return preview
