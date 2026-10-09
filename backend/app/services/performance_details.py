"""Batch employee records and assemble the shared member/statistics details."""
from __future__ import annotations

from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from app.record_payloads import (
    ATTENDANCE_FILTER_STATUSES, DEDUCTION_FILTER_STATUSES,
    RECOGNITION_FILTER_STATUSES, deduction_payload, recognition_payload,
    sick_leave_payloads,
)
from app.search_utils import like_escaped_pattern
from app.score_policy import loa_month_exclusion
from app.score_queries import employee_month_scores
from app.v2_models import AttendanceMonthlyScore, DeductionRecord, Employee, RecognitionAttachment, RecognitionRecord, SickLeaveRecord
from app.services.attendance import ensure_month_attendance
from app.services.identity import acting_period_notes, identity_label, identity_labels

def member_records_payload(
    db: Session,
    member_ids: list[int],
    month: str | None = None,
    keyword: str | None = None,
    status: str | None = None,
    record_type: str = "all",
) -> list[dict]:
    if record_type not in {"all", "recognition", "deduction", "sick_leave"}:
        raise HTTPException(400, "记录类型无效")
    empty_condition = RecognitionRecord.id == -1
    result: list[dict] = []
    if record_type in {"all", "recognition"}:
        query = db.query(RecognitionRecord).options(
            selectinload(RecognitionRecord.attachments).selectinload(RecognitionAttachment.file)
        ).filter(RecognitionRecord.employee_id.in_(member_ids) if member_ids else empty_condition)
        if month:
            query = query.filter(RecognitionRecord.recognition_month == month)
        if keyword:
            value = like_escaped_pattern(keyword)
            query = query.filter(or_(RecognitionRecord.employee_name.like(value, escape="\\"), RecognitionRecord.employee_no.like(value, escape="\\")))
        if status in RECOGNITION_FILTER_STATUSES:
            query = query.filter(RecognitionRecord.status == status)
        elif status in ATTENDANCE_FILTER_STATUSES:
            query = query.filter(RecognitionRecord.id == -1)
        result.extend(recognition_payload(row) for row in query.order_by(RecognitionRecord.submitted_at.desc()).limit(1000).all())
    if record_type in {"all", "deduction"}:
        query = db.query(DeductionRecord).filter(DeductionRecord.employee_id.in_(member_ids) if member_ids else DeductionRecord.id == -1)
        if month:
            query = query.filter(DeductionRecord.deduction_month == month)
        if keyword:
            value = like_escaped_pattern(keyword)
            query = query.filter(or_(DeductionRecord.employee_name.like(value, escape="\\"), DeductionRecord.employee_no.like(value, escape="\\")))
        if status in DEDUCTION_FILTER_STATUSES:
            query = query.filter(DeductionRecord.status == status)
        elif status in RECOGNITION_FILTER_STATUSES or status in ATTENDANCE_FILTER_STATUSES:
            query = query.filter(DeductionRecord.id == -1)
        result.extend(deduction_payload(row) for row in query.order_by(DeductionRecord.submitted_at.desc()).limit(1000).all())
    if record_type in {"all", "sick_leave"}:
        query = db.query(SickLeaveRecord).filter(SickLeaveRecord.employee_id.in_(member_ids) if member_ids else SickLeaveRecord.id == -1)
        if month:
            query = query.filter(SickLeaveRecord.attendance_month == month)
        if keyword:
            value = like_escaped_pattern(keyword)
            query = query.join(Employee, Employee.id == SickLeaveRecord.employee_id).filter(or_(Employee.name.like(value, escape="\\"), Employee.employee_no.like(value, escape="\\")))
        if status in ATTENDANCE_FILTER_STATUSES:
            query = query.filter(SickLeaveRecord.status == status)
        elif status in RECOGNITION_FILTER_STATUSES:
            query = query.filter(SickLeaveRecord.id == -1)
        result.extend(sick_leave_payloads(db, query.order_by(SickLeaveRecord.submitted_at.desc()).limit(1000).all()))
    priority = {
        ("recognition", "pending"): 0,
        ("recognition", "rejected"): 0,
        ("deduction", "active"): 1,
        ("sick_leave", "active"): 1,
        ("recognition", "confirmed"): 2,
        ("deduction", "void"): 3,
        ("sick_leave", "void"): 3,
    }
    result.sort(key=lambda row: row["submitted_at"], reverse=True)
    result.sort(key=lambda row: priority.get((row["record_type"], row["status"]), 9))
    return result


def member_score_detail_payload(
    db: Session,
    employee: Employee,
    score: dict,
    recognitions: list[RecognitionRecord],
    deductions: list[DeductionRecord],
    sick_leaves: list[SickLeaveRecord],
    attendance: AttendanceMonthlyScore | None,
    *,
    role_name: str | None = None,
    sick_leave_rows: list[dict] | None = None,
) -> dict:
    recognition_rows = [recognition_payload(row) for row in recognitions]
    deduction_rows = [deduction_payload(row) for row in deductions]
    if sick_leave_rows is None:
        sick_leave_rows = sick_leave_payloads(db, sick_leaves)
    all_records: list[dict] = []
    loa_excluded = bool(score.get("loa_excluded"))

    for row in recognition_rows:
        included = row["status"] == "confirmed" and not loa_excluded
        all_records.append(
            {
                "record_type": "recognition",
                "record_type_name": "加分",
                "business_date": row["recognition_date"],
                "submitted_at": row["submitted_at"],
                "title": row["recognition_type"],
                "content": f"{row['content']} · 认可人：{row['recognizer_name']}",
                "operator_name": row["operator_name"],
                "status": row["status"],
                "status_name": row["status_name"],
                "reason": row["review_note"],
                "score": row["credited_fraction"],
                "score_text": f"+{row['credited_fraction']:.2f}",
                "included": included,
                "included_name": "LOA（当月不计分）" if loa_excluded else ("是" if included else "否"),
                "attachment_url": row["image_url"],
                "attachment_name": "认可图片" if row["image_url"] else "",
                "attachment_is_previewable": row["image_is_previewable"],
                "attachment_preview_kind": row["image_preview_kind"],
            }
        )
    for row in deduction_rows:
        included = row["status"] == "active" and row.get("upgrade_role") != "source_second" and not loa_excluded
        all_records.append(
            {
                "record_type": "deduction",
                "record_type_name": "扣分",
                "business_date": row["occurred_on"],
                "submitted_at": row["submitted_at"],
                "title": f"{row['deduction_type']} · {row['deduction_level']}",
                "content": row["description"] + (f" · 作废原因：{row['void_reason']}" if row["void_reason"] else ""),
                "operator_name": row["submitter_name"],
                "status": row["status"],
                "status_name": row["status_name"],
                "reason": row["void_reason"],
                "score": -row["points"] if included else 0,
                "score_text": f"-{row['points']:.2f}" if included else "0.00",
                "included": included,
                "included_name": "LOA（当月不计分）" if loa_excluded else ("是" if included else "否"),
                "attachment_url": row["document_url"],
                "attachment_name": "声明PDF",
                "attachment_is_previewable": False,
                "attachment_preview_kind": row["document_preview_kind"],
            }
        )
    for row in sick_leave_rows:
        included = row["status"] == "active" and not loa_excluded
        all_records.append(
            {
                "record_type": "sick_leave",
                "record_type_name": "病假",
                "business_date": row["leave_start_date"],
                "submitted_at": row["submitted_at"],
                "title": f"{row['leave_start_date']} 至 {row['leave_end_date']}",
                "content": f"实际{row['leave_days']:.1f}天，计费{row['charged_days']}天" + (f" · {row['note']}" if row["note"] else "") + (f" · 作废原因：{row['void_reason']}" if row["void_reason"] else ""),
                "operator_name": row["submitter_name"],
                "status": row["status"],
                "status_name": row["status_name"],
                "reason": row["void_reason"],
                "score": None,
                "score_text": "影响全勤" if included else "不影响",
                "included": included,
                "included_name": "LOA（当月不计分）" if loa_excluded else ("通过全勤分计算" if included else "否"),
                "attachment_url": row["proof_url"],
                "attachment_name": "病假证明",
                "attachment_is_previewable": row["proof_is_previewable"],
                "attachment_preview_kind": row["proof_preview_kind"],
            }
        )

    attendance_payload = None
    if attendance:
        attendance_payload = {
            "eligible": attendance.eligible,
            "actual_sick_days": float(attendance.actual_sick_days),
            "charged_sick_days": attendance.charged_sick_days,
            "base_score": float(attendance.base_score),
            "perfect_bonus": float(attendance.perfect_bonus),
            "sick_deduction": float(attendance.sick_deduction),
            "final_score": float(attendance.final_score),
            "calculated_at": attendance.calculated_at.strftime("%Y-%m-%d %H:%M:%S"),
        }
        all_records.append(
            {
                "record_type": "attendance",
                "record_type_name": "全勤分",
                "business_date": attendance.attendance_month,
                "submitted_at": attendance_payload["calculated_at"],
                "title": "月度全勤分",
                "content": (
                    f"基础分{attendance_payload['base_score']:.2f}，全勤奖励{attendance_payload['perfect_bonus']:.2f}，"
                    f"病假扣减{attendance_payload['sick_deduction']:.2f}"
                ),
                "operator_name": "系统",
                "status": "calculated" if attendance.eligible else "ineligible",
                "status_name": "已计算" if attendance.eligible else "不适用",
                "reason": "",
                "score": attendance_payload["final_score"],
                "score_text": f"+{attendance_payload['final_score']:.2f}",
                "included": attendance.eligible and not loa_excluded,
                "included_name": "LOA（当月不计分）" if loa_excluded else ("是" if attendance.eligible else "否"),
                "attachment_url": "",
                "attachment_name": "",
                "attachment_is_previewable": False,
                "attachment_preview_kind": "",
            }
        )

    all_records.sort(key=lambda row: (row["business_date"], row["submitted_at"]), reverse=True)
    return {
        "employee_id": employee.id,
        "employee_no": employee.employee_no,
        "employee_name": employee.name,
        "role_name": role_name if role_name is not None else identity_label(db, employee.id),
        "recognition_score": float(score.get("recognition_score") or 0),
        "deduction_score": float(score.get("deduction_score") or 0),
        "attendance_score": float(score.get("attendance_score") or 0),
        "total_score": float(score.get("total_score") or 0),
        "details": {
            "recognitions": recognition_rows,
            "deductions": deduction_rows,
            "sick_leaves": sick_leave_rows,
            "attendance": attendance_payload,
            "all_records": all_records,
        },
    }


@dataclass(frozen=True)
class MonthlyRecordBatch:
    recognitions: list[RecognitionRecord]
    deductions: list[DeductionRecord]
    sick_leaves: list[SickLeaveRecord]
    attendance: dict[int, AttendanceMonthlyScore]


def load_monthly_records(
    db: Session,
    month: str,
    employee_ids: list[int] | set[int],
    *,
    include_attendance: bool = True,
) -> MonthlyRecordBatch:
    """Load each record table once for a whole selected employee population."""
    if not employee_ids:
        return MonthlyRecordBatch([], [], [], {})
    recognitions = (
        db.query(RecognitionRecord)
        .options(selectinload(RecognitionRecord.attachments).selectinload(RecognitionAttachment.file))
        .filter(RecognitionRecord.employee_id.in_(employee_ids), RecognitionRecord.recognition_month == month)
        .order_by(RecognitionRecord.submitted_at.desc(), RecognitionRecord.id.desc())
        .all()
    )
    deductions = (
        db.query(DeductionRecord)
        .filter(DeductionRecord.employee_id.in_(employee_ids), DeductionRecord.deduction_month == month)
        .order_by(DeductionRecord.submitted_at.desc(), DeductionRecord.id.desc())
        .all()
    )
    sick_leaves = (
        db.query(SickLeaveRecord)
        .filter(SickLeaveRecord.employee_id.in_(employee_ids), SickLeaveRecord.attendance_month == month)
        .order_by(SickLeaveRecord.submitted_at.desc(), SickLeaveRecord.id.desc())
        .all()
    )
    attendance = {
        row.employee_id: row
        for row in db.query(AttendanceMonthlyScore)
        .filter(AttendanceMonthlyScore.employee_id.in_(employee_ids), AttendanceMonthlyScore.attendance_month == month)
        .all()
    } if include_attendance else {}
    return MonthlyRecordBatch(recognitions, deductions, sick_leaves, attendance)


def monthly_member_score_rows(
    db: Session,
    month: str,
    employee_ids: list[int],
    *,
    employees: list[Employee] | None = None,
    score_loader: Callable = employee_month_scores,
) -> list[dict]:
    """One shared score/record/identity assembly for both member detail screens."""
    if not employee_ids:
        return []
    scores = {row["employee_id"]: dict(row) for row in score_loader(db, month, employee_ids)}
    if employees is None:
        employees = db.query(Employee).filter(Employee.id.in_(employee_ids)).all()
    employee_map = {employee.id: employee for employee in employees}
    records = load_monthly_records(db, month, employee_ids)
    recognition_groups: dict[int, list[RecognitionRecord]] = {employee_id: [] for employee_id in employee_ids}
    deduction_groups: dict[int, list[DeductionRecord]] = {employee_id: [] for employee_id in employee_ids}
    sick_leave_groups: dict[int, list[SickLeaveRecord]] = {employee_id: [] for employee_id in employee_ids}
    sick_payload_groups: dict[int, list[dict]] = {employee_id: [] for employee_id in employee_ids}
    for row in records.recognitions:
        recognition_groups[row.employee_id].append(row)
    for row in records.deductions:
        deduction_groups[row.employee_id].append(row)
    for row in records.sick_leaves:
        sick_leave_groups[row.employee_id].append(row)
    # Payload lookups include attachment/submitter metadata: run them once for
    # the whole batch rather than once per employee.
    for record, payload in zip(records.sick_leaves, sick_leave_payloads(db, records.sick_leaves)):
        sick_payload_groups[record.employee_id].append(payload)
    labels = identity_labels(db, employee_ids)
    notes = acting_period_notes(db, employee_ids, f"{month}-01", f"{month}-{monthrange(int(month[:4]), int(month[5:]))[1]:02d}")
    empty_score = {"recognition_score": 0, "deduction_score": 0, "attendance_score": 0, "total_score": 0}
    return [
        {
            **member_score_detail_payload(
                db, employee_map[employee_id], scores.get(employee_id, empty_score),
                recognition_groups[employee_id], deduction_groups[employee_id],
                sick_leave_groups[employee_id], records.attendance.get(employee_id),
                role_name=labels.get(employee_id, "未配置"),
                sick_leave_rows=sick_payload_groups[employee_id],
            ),
            "acting_note": notes.get(employee_id, ""),
        }
        for employee_id in employee_ids if employee_id in employee_map
    ]


def member_score_summary_payload(
    db: Session,
    month: str,
    member_ids: list[int],
    keyword: str | None = None,
    *,
    score_loader: Callable = employee_month_scores,
) -> dict:
    try:
        date.fromisoformat(f"{month}-01")
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "月份格式应为YYYY-MM") from exc
    query = db.query(Employee).filter(Employee.id.in_(member_ids) if member_ids else Employee.id == -1)
    if keyword and keyword.strip():
        value = f"%{keyword.strip()}%"
        query = query.filter(or_(Employee.name.like(value), Employee.employee_no.like(value)))
    employees = query.order_by(Employee.name, Employee.employee_no).all()
    selected_ids = [employee.id for employee in employees]
    if not selected_ids:
        return {"month": month, "rows": []}
    ensure_month_attendance(db, month, selected_ids)
    db.commit()
    rows = monthly_member_score_rows(db, month, selected_ids, employees=employees, score_loader=score_loader)
    rows.sort(key=lambda row: (-row["total_score"], row["employee_no"], row["employee_id"]))
    return {"month": month, "rows": rows}


def statistics_details_payload(db: Session, month: str, employee_ids: list[int]) -> dict[str, dict]:
    excluded_ids = {
        row[0]
        for row in db.query(Employee.id)
        .filter(Employee.id.in_(employee_ids), loa_month_exclusion(Employee.id, month))
        .all()
    } if employee_ids else set()
    result: dict[str, dict] = {}
    for detail in monthly_member_score_rows(db, month, employee_ids):
        employee_id = detail["employee_id"]
        if employee_id in excluded_ids:
            for field in ("recognition_score", "attendance_score", "deduction_score", "total_score"):
                detail["details"][field] = 0
            detail["details"]["loa_excluded"] = True
        result[str(employee_id)] = {
            **detail["details"],
            **{key: detail[key] for key in ("employee_id", "employee_no", "employee_name", "role_name")},
            "acting_note": detail["acting_note"],
        }
    return result
