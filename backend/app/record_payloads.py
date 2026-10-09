"""Stable recognition, deduction and absence response projections."""
from __future__ import annotations

from app.deduction_policy import deduction_counts_for_score
from app.file_types import is_previewable_image, preview_kind
from app.recognition_policy import effective_recognition_credit
from app.v2_models import DeductionFollowUp, DeductionRecord, Employee, RecognitionRecord, SickLeaveRecord, StoredFile
from sqlalchemy.orm import Session


RECOGNITION_FILTER_STATUSES = frozenset({"pending", "rejected", "confirmed"})


ATTENDANCE_FILTER_STATUSES = frozenset({"active", "covered", "void"})


DEDUCTION_FILTER_STATUSES = frozenset({"active", "void", "pending_upgrade", "pending_material", "material_processing", "material_failed"})


def recognition_payload(row: RecognitionRecord) -> dict:
    status_names = {"pending": "待复核", "confirmed": "已确认", "rejected": "不通过", "void": "已撤回"}
    attachment = row.attachments[0] if row.attachments else None
    attachment_file = attachment.file if attachment else None
    return {
        "record_type": "recognition",
        "id": row.id,
        "employee_id": row.employee_id,
        "employee_no": row.employee_no,
        "employee_name": row.employee_name,
        "recognition_date": row.recognition_date,
        "recognition_type": row.recognition_type_name,
        "content": row.content,
        "recognizer_name": row.recognizer_name,
        "recognizer_role": row.recognizer_role_snapshot,
        "operator_id": row.operator_employee_id,
        "operator_name": row.operator_name,
        "source": row.source,
        "entry_label": f"{row.operator_name}录入" if row.source == "manager" else "",
        "fraction": float(row.fraction or 0),
        "credited_fraction": float(effective_recognition_credit(row)),
        "monthly_cap_status": row.monthly_cap_status or "not_applicable",
        "monthly_cap_reason": row.monthly_cap_reason or "",
        "monthly_cap_limit": float(row.monthly_cap_limit or 0),
        "monthly_cap_confirmed_before": float(row.monthly_cap_confirmed_before or 0),
        "poc_period_type": row.poc_period_type or "",
        "poc_period_key": row.poc_period_key or "",
        "poc_reason": row.poc_reason or "",
        "status": row.status,
        "status_name": status_names.get(row.status, row.status),
        "submitted_at": row.submitted_at.strftime("%Y-%m-%d %H:%M:%S"),
        "review_note": row.review_note or "",
        "same_day_duplicate": bool(row.same_day_duplicate_group),
        "same_day_duplicate_sequence": int(row.same_day_duplicate_sequence or 0),
        "same_day_duplicate_label": (f"今日已有同类登记（第{int(row.same_day_duplicate_sequence or 0)}次）" if row.same_day_duplicate_group else ""),
        "voided_by_name": row.voided_by_name or "",
        "voided_by_role_code": row.voided_by_role_code or "",
        "voided_by_role_name": row.voided_by_role_name or "",
        "void_permission_scope": row.void_permission_scope_snapshot or "",
        "voided_from_status": row.voided_from_status or "",
        "voided_at": row.voided_at.strftime("%Y-%m-%d %H:%M:%S") if row.voided_at else "",
        "void_reason": row.void_reason or "",
        "has_image": bool(attachment),
        "image_url": f"/api/files/{attachment.file_id}" if attachment else "",
        "image_is_previewable": is_previewable_image(attachment_file),
        "image_preview_kind": preview_kind(attachment_file),
        "available_actions": ["withdraw"] if row.status != "void" else [],
    }


def deduction_payload(row: DeductionRecord) -> dict:
    status_names = {"active": "已扣分", "void": "已作废", "pending_upgrade": "待升级审核", "pending_material": "待补充材料（未扣分）", "material_processing": "材料生成中", "material_failed": "材料生成失败"}
    upgrade_state_names = {"pending": "待升级审核", "source_first": "已升级（原声明）", "source_second": "已用于升级，不计分", "result": "升级结果", "rejected": "审核不通过"}
    return {
        "record_type": "deduction",
        "id": row.id,
        "employee_id": row.employee_id,
        "employee_no": row.employee_no,
        "employee_name": row.employee_name,
        "deduction_type": row.deduction_type_name,
        "deduction_level": row.deduction_level_name,
        "points": float(row.points),
        "actual_points": float(row.points) if deduction_counts_for_score(row) else 0.0,
        "occurred_on": row.occurred_on,
        "description": row.description,
        "submitter_id": row.submitter_id,
        "submitter_name": row.submitter_name,
        "status": row.status,
        "status_name": status_names.get(row.status, row.status),
        "submitted_at": row.submitted_at.strftime("%Y-%m-%d %H:%M:%S"),
        "void_reason": row.void_reason or "",
        "voided_by_name": row.voided_by_name or "",
        "voided_by_role_code": row.voided_by_role_code or "",
        "voided_by_role_name": row.voided_by_role_name or "",
        "void_permission_scope": row.void_permission_scope_snapshot or "",
        "voided_from_status": row.voided_from_status or "",
        "voided_at": row.voided_at.strftime("%Y-%m-%d %H:%M:%S") if row.voided_at else "",
        "material_status": row.material_status or "ready",
        "material_error": row.material_error or "",
        "material_source_type": row.material_source_type or "pdf",
        "material_job_id": row.material_job_id or 0,
        "material_uploaded_by_name": row.material_uploaded_by_name or "",
        "material_uploaded_at": row.material_uploaded_at.strftime("%Y-%m-%d %H:%M:%S") if row.material_uploaded_at else "",
        "material_revision": int(row.material_revision or 1),
        "legacy_upgrade_excluded": bool(row.legacy_upgrade_excluded),
        "legacy_upgrade_note": row.legacy_upgrade_note or "",
        "document_url": "" if row.upgrade_role == "result" or (row.material_status or "ready") != "ready" else f"/api/files/{row.document_file_id}",
        # Declarations are accepted only as PDF documents when the record is created.
        "document_preview_kind": "" if row.upgrade_role == "result" or (row.material_status or "ready") != "ready" else "pdf",
        "upgrade_request_id": row.upgrade_request_id or 0,
        "upgrade_role": row.upgrade_role or "",
        "upgrade_state": row.upgrade_state or "",
        "upgrade_state_name": upgrade_state_names.get(row.upgrade_state or "", ""),
        "available_actions": (["supplement_material"] if row.status in {"pending_material", "material_failed"} else []) + (["void"] if row.status == "active" and not row.upgrade_request_id else []),
    }


def deduction_follow_up_payload(row: DeductionFollowUp) -> dict:
    status_names = {"pending": "待经理跟进", "issued": "已开具"}
    return {
        "record_type": "follow_up",
        "id": row.id,
        "employee_id": row.employee_id,
        "employee_no": row.employee_no,
        "employee_name": row.employee_name,
        "deduction_type": row.deduction_type_name,
        "occurred_on": row.occurred_on,
        "status": row.status,
        "status_name": status_names.get(row.status, row.status),
        "submitted_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "issued_by_name": row.issued_by_name or "",
        "issued_at": row.issued_at.strftime("%Y-%m-%d %H:%M:%S") if row.issued_at else "",
        "issued_deduction_id": row.issued_deduction_id,
        "available_actions": [],
    }


def sick_leave_payload(db: Session, row: SickLeaveRecord, *, employees: dict[int, Employee] | None = None, files: dict[int, StoredFile] | None = None) -> dict:
    employee = (employees or {}).get(row.employee_id) or db.get(Employee, row.employee_id)
    proof_file = (files or {}).get(row.proof_file_id) or (db.get(StoredFile, row.proof_file_id) if row.proof_file_id else None)
    status_names = {"active": "已生效", "covered": "已覆盖", "void": "已作废"}
    return {
        "record_type": "sick_leave",
        "id": row.id,
        "employee_id": row.employee_id,
        "employee_no": row.employee_no_snapshot or (employee.employee_no if employee else ""),
        "employee_name": row.employee_name_snapshot or (employee.name if employee else "未知员工"),
        "attendance_month": row.attendance_month,
        "leave_start_date": row.leave_start_date,
        "leave_end_date": row.leave_end_date,
        "leave_days": float(row.leave_days),
        "charged_days": float(row.charged_days),
        "leave_type": row.leave_type or "病假",
        "import_source": row.import_source or "manual",
        "note": row.note or "",
        "submitter_id": row.submitted_by,
        "submitter_name": row.submitted_by_name,
        "status": row.status,
        "status_name": status_names.get(row.status, row.status),
        "submitted_at": row.submitted_at.strftime("%Y-%m-%d %H:%M:%S"),
        "void_reason": row.void_reason or "",
        "voided_by_name": row.voided_by_name or "",
        "voided_by_role_code": row.voided_by_role_code or "",
        "voided_by_role_name": row.voided_by_role_name or "",
        "void_permission_scope": row.void_permission_scope_snapshot or "",
        "voided_from_status": row.voided_from_status or "",
        "voided_at": row.voided_at.strftime("%Y-%m-%d %H:%M:%S") if row.voided_at else "",
        "proof_url": f"/api/files/{row.proof_file_id}" if row.proof_file_id else "",
        "proof_is_previewable": bool(proof_file and is_previewable_image(proof_file)),
        "proof_preview_kind": preview_kind(proof_file) if proof_file else "",
        "is_violation": bool(row.is_violation),
        "violation_deduction_id": row.violation_deduction_id or 0,
        "available_actions": ["void"] if row.status == "active" else [],
    }


def sick_leave_payloads(db: Session, rows: list[SickLeaveRecord]) -> list[dict]:
    """Serialize many sick-leave rows with batched employee/proof lookups."""
    employee_ids = {row.employee_id for row in rows}
    file_ids = {row.proof_file_id for row in rows if row.proof_file_id}
    employees = (
        {employee.id: employee for employee in db.query(Employee).filter(Employee.id.in_(employee_ids)).all()}
        if employee_ids
        else {}
    )
    files = (
        {file_row.id: file_row for file_row in db.query(StoredFile).filter(StoredFile.id.in_(file_ids)).all()}
        if file_ids
        else {}
    )
    return [sick_leave_payload(db, row, employees=employees, files=files) for row in rows]
