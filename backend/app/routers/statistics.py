"""Statistics, rankings and member record endpoints."""
from __future__ import annotations

import json
from calendar import monthrange
from datetime import date
from decimal import Decimal
from io import BytesIO
from math import ceil
from time import monotonic
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, or_, text, update
from sqlalchemy.orm import Session
from app.v2_auth import V2User, current_user, require_permissions
from app.v2_database import get_db
from app.v2_models import Attraction, AttendanceMonthlyScore, AuditLog, DeductionRecord, DeductionType, Employee, EmployeeMonthOrganizationSnapshot, EmployeeLOAPeriod, GroupLeaderAssignment, GroupMembership, ManagementScope, RecognitionRecord, RecognitionType, Role, SickLeaveRecord, WorkGroup
from app.score_queries import employee_month_scores
from app.v2_services import FRONTLINE_CODES, GSM_CODES, LEADER_CODES, direct_member_ids, ensure_month_attendance, loa_excludes_month, recalculate_attendance, role_at, roles_at, write_audit
from app.v2_watermark import watermark_workbook
from app.excel_export_utils import content_disposition
from app.excel_export import build_pr_rankings_workbook, build_statistics_workbook
from app.routers._shared import (
    ATTENDANCE_FILTER_STATUSES,
    DEDUCTION_FILTER_STATUSES,
    PR_RANKING_CACHE_SECONDS,
    RECOGNITION_FILTER_STATUSES,
    STATISTICS_CACHE_SECONDS,
    _pr_ranking_cache_lock,
    _pr_ranking_response_cache,
    _statistics_cache_lock,
    _statistics_response_cache,
    deduction_payload,
    effective_recognition_credit,
    like_escaped_pattern,
    months_between,
    parse_iso_date,
    recognition_payload,
    scoped_hr_attraction_ids,
    sick_leave_payloads,
)

router = APIRouter()


@router.get("/dashboard")
def dashboard(month: str | None = None, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    month = month or date.today().strftime("%Y-%m")
    if user.role.code not in FRONTLINE_CODES:
        return {"month": month, "role": user.role.name}
    recalculate_attendance(db, user.employee, month)
    db.commit()
    score = next(iter(employee_month_scores(db, month, [user.id])), None)
    score = score or {"recognition_score": 0, "attendance_score": 0, "deduction_score": 0, "total_score": 0}
    categories = {
        name: float(total or 0)
        for name, total in db.query(RecognitionRecord.recognition_type_name, text("SUM(CASE WHEN recognition_date < '2026-09-01' THEN fraction ELSE credited_fraction END)"))
        .filter(
            RecognitionRecord.employee_id == user.id,
            RecognitionRecord.recognition_month == month,
            RecognitionRecord.status == "confirmed",
        )
        .group_by(RecognitionRecord.recognition_type_name)
        .all()
    }
    records = (
        db.query(RecognitionRecord)
        .filter(
            RecognitionRecord.employee_id == user.id,
            RecognitionRecord.recognition_month == month,
            RecognitionRecord.status != "void",
        )
        .order_by(RecognitionRecord.submitted_at.desc(), RecognitionRecord.id.desc())
        .all()
    )
    records.sort(key=lambda row: row.status == "confirmed")
    return {
        "month": month,
        "category_scores": categories,
        "recognition_score": float(score["recognition_score"] or 0),
        "attendance_score": float(score["attendance_score"] or 0),
        "deduction_score": float(score["deduction_score"] or 0),
        "total_score": float(score["total_score"] or 0),
        "records": [recognition_payload(row) for row in records],
    }


@router.get("/member-records")
def member_records(
    month: str | None = None,
    keyword: str | None = None,
    status: str | None = None,
    record_type: str = "all",
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("MEMBER_RECORDS")),
):
    if record_type not in {"all", "recognition", "deduction", "sick_leave"}:
        raise HTTPException(400, "记录类型无效")
    member_ids = direct_member_ids(db, user.id)
    empty_condition = RecognitionRecord.id == -1
    result: list[dict] = []
    if record_type in {"all", "recognition"}:
        query = db.query(RecognitionRecord).filter(RecognitionRecord.employee_id.in_(member_ids) if member_ids else empty_condition)
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
) -> dict:
    recognition_rows = [recognition_payload(row) for row in recognitions]
    deduction_rows = [deduction_payload(row) for row in deductions]
    sick_leave_rows = sick_leave_payloads(db, sick_leaves)
    all_records: list[dict] = []

    for row in recognition_rows:
        included = row["status"] == "confirmed"
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
                "included_name": "是" if included else "否",
                "attachment_url": row["image_url"],
                "attachment_name": "认可图片" if row["image_url"] else "",
                "attachment_is_previewable": row["image_is_previewable"],
                "attachment_preview_kind": row["image_preview_kind"],
            }
        )
    for row in deduction_rows:
        included = row["status"] == "active"
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
                "score": -row["points"],
                "score_text": f"-{row['points']:.2f}",
                "included": included,
                "included_name": "是" if included else "否",
                "attachment_url": row["document_url"],
                "attachment_name": "声明PDF",
                "attachment_is_previewable": False,
                "attachment_preview_kind": row["document_preview_kind"],
            }
        )
    for row in sick_leave_rows:
        included = row["status"] == "active"
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
                "included_name": "通过全勤分计算" if included else "否",
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
                "included": attendance.eligible,
                "included_name": "是" if attendance.eligible else "否",
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
        "role_name": (role_at(db, employee.id).name if role_at(db, employee.id) else "未配置"),
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


@router.get("/member-score-summary")
def member_score_summary(
    month: str | None = None,
    keyword: str | None = None,
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("MEMBER_RECORDS")),
):
    month = month or date.today().strftime("%Y-%m")
    try:
        date.fromisoformat(f"{month}-01")
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "月份格式应为YYYY-MM") from exc
    member_ids = direct_member_ids(db, user.id)
    employee_query = db.query(Employee).filter(Employee.id.in_(member_ids) if member_ids else Employee.id == -1)
    if keyword and keyword.strip():
        value = f"%{keyword.strip()}%"
        employee_query = employee_query.filter(or_(Employee.name.like(value), Employee.employee_no.like(value)))
    employees = employee_query.order_by(Employee.name, Employee.employee_no).all()
    selected_ids = [employee.id for employee in employees]
    if not selected_ids:
        return {"month": month, "rows": []}

    ensure_month_attendance(db, month, selected_ids)
    db.commit()
    scores = {
        row["employee_id"]: dict(row)
        for row in employee_month_scores(db, month, selected_ids)
    }
    recognition_groups: dict[int, list[RecognitionRecord]] = {employee_id: [] for employee_id in selected_ids}
    deduction_groups: dict[int, list[DeductionRecord]] = {employee_id: [] for employee_id in selected_ids}
    sick_leave_groups: dict[int, list[SickLeaveRecord]] = {employee_id: [] for employee_id in selected_ids}
    for row in (
        db.query(RecognitionRecord)
        .filter(RecognitionRecord.employee_id.in_(selected_ids), RecognitionRecord.recognition_month == month)
        .order_by(RecognitionRecord.submitted_at.desc(), RecognitionRecord.id.desc())
        .all()
    ):
        recognition_groups[row.employee_id].append(row)
    for row in (
        db.query(DeductionRecord)
        .filter(DeductionRecord.employee_id.in_(selected_ids), DeductionRecord.deduction_month == month)
        .order_by(DeductionRecord.submitted_at.desc(), DeductionRecord.id.desc())
        .all()
    ):
        deduction_groups[row.employee_id].append(row)
    for row in (
        db.query(SickLeaveRecord)
        .filter(SickLeaveRecord.employee_id.in_(selected_ids), SickLeaveRecord.attendance_month == month)
        .order_by(SickLeaveRecord.submitted_at.desc(), SickLeaveRecord.id.desc())
        .all()
    ):
        sick_leave_groups[row.employee_id].append(row)
    attendance_rows = {
        row.employee_id: row
        for row in db.query(AttendanceMonthlyScore)
        .filter(AttendanceMonthlyScore.employee_id.in_(selected_ids), AttendanceMonthlyScore.attendance_month == month)
        .all()
    }
    empty_score = {"recognition_score": 0, "deduction_score": 0, "attendance_score": 0, "total_score": 0}
    return {
        "month": month,
        "rows": [
            member_score_detail_payload(
                db,
                employee,
                scores.get(employee.id, empty_score),
                recognition_groups[employee.id],
                deduction_groups[employee.id],
                sick_leave_groups[employee.id],
                attendance_rows.get(employee.id),
            )
            for employee in employees
        ],
    }


def ranking_employees(db: Session, attraction_id: int | None, on_date: str, role_codes: set[str]) -> tuple[list[Employee], dict[int, Role]]:
    circle_ids = [
        row.id
        for row in db.query(Attraction.id)
        .filter(Attraction.active.is_(True), Attraction.employee_circle.is_(True))
        .order_by(Attraction.id)
        .all()
    ]
    query = db.query(Employee).filter(Employee.is_active.is_(True), Employee.attraction_id.in_(circle_ids or [-1]))
    if attraction_id is not None:
        query = query.filter(Employee.attraction_id == attraction_id)
    candidates = query.order_by(Employee.name, Employee.employee_no).all()
    role_map = roles_at(db, [employee.id for employee in candidates], on_date)
    selected = [employee for employee in candidates if role_map.get(employee.id) and role_map[employee.id].code in role_codes]
    return selected, role_map


def ranking_leader_names(db: Session, employee_ids: list[int], on_date: str) -> dict[int, str]:
    if not employee_ids:
        return {}
    memberships = (
        db.query(GroupMembership)
        .filter(
            GroupMembership.employee_id.in_(employee_ids),
            GroupMembership.status == "active",
            GroupMembership.starts_on <= on_date,
            or_(GroupMembership.ends_on.is_(None), GroupMembership.ends_on >= on_date),
        )
        .order_by(GroupMembership.employee_id, GroupMembership.starts_on.desc(), GroupMembership.id.desc())
        .all()
    )
    membership_by_employee: dict[int, GroupMembership] = {}
    for membership in memberships:
        membership_by_employee.setdefault(membership.employee_id, membership)
    group_ids = {membership.group_id for membership in membership_by_employee.values()}
    assignments = (
        db.query(GroupLeaderAssignment)
        .filter(
            GroupLeaderAssignment.group_id.in_(group_ids),
            GroupLeaderAssignment.status == "active",
            GroupLeaderAssignment.starts_on <= on_date,
            or_(GroupLeaderAssignment.ends_on.is_(None), GroupLeaderAssignment.ends_on >= on_date),
        )
        .order_by(GroupLeaderAssignment.group_id, GroupLeaderAssignment.starts_on.desc(), GroupLeaderAssignment.id.desc())
        .all()
        if group_ids
        else []
    )
    assignment_by_group: dict[int, GroupLeaderAssignment] = {}
    for assignment in assignments:
        assignment_by_group.setdefault(assignment.group_id, assignment)
    leader_ids = {assignment.leader_employee_id for assignment in assignment_by_group.values()}
    leader_names = {
        employee.id: employee.name
        for employee in db.query(Employee).filter(Employee.id.in_(leader_ids)).all()
    } if leader_ids else {}
    return {
        employee_id: leader_names.get(assignment_by_group[membership.group_id].leader_employee_id, "未分配主管")
        if membership.group_id in assignment_by_group
        else "未分配主管"
        for employee_id, membership in membership_by_employee.items()
    }


def gsm_recognizer_ranking_payload(db: Session, start_value: str, end_value: str, subtype_id: int | None, keyword: str, page: int, page_size: int, attraction: Attraction | None) -> dict:
    subtype = db.get(RecognitionType, subtype_id) if subtype_id else None
    if subtype_id and (not subtype or not subtype.active):
        raise HTTPException(400, "请选择有效认可类型")
    query = db.query(RecognitionRecord).filter(
        RecognitionRecord.status == "confirmed",
        RecognitionRecord.recognition_date >= start_value,
        RecognitionRecord.recognition_date <= end_value,
    )
    if attraction:
        query = query.filter(RecognitionRecord.home_attraction_id == attraction.id)
    if subtype:
        query = query.filter(RecognitionRecord.recognition_type_id == subtype.id)
    sql_conditions = ["status='confirmed'", "recognition_date>=:start", "recognition_date<=:end"]
    params: dict[str, object] = {"start": start_value, "end": end_value}
    if attraction:
        sql_conditions.append("home_attraction_id=:attraction_id")
        params["attraction_id"] = attraction.id
    if subtype:
        sql_conditions.append("recognition_type_id=:subtype_id")
        params["subtype_id"] = subtype.id
    snapshot_rows = db.execute(
        text(
            f"""
            WITH filtered AS (
                SELECT id, recognizer_employee_id, recognizer_role_code_snapshot,
                       operator_employee_id, operator_role_code_snapshot,
                       credited_fraction, recognition_date
                FROM recognition_records WHERE {' AND '.join(sql_conditions)}
            ), participants AS (
                SELECT recognizer_employee_id AS employee_id,
                       recognizer_role_code_snapshot AS role_code,
                       credited_fraction, recognition_date
                FROM filtered WHERE recognizer_employee_id != operator_employee_id
                UNION ALL
                SELECT operator_employee_id AS employee_id,
                       operator_role_code_snapshot AS role_code,
                       credited_fraction, recognition_date
                FROM filtered
            )
            SELECT employee_id, role_code, COUNT(*) AS event_count,
                   SUM(credited_fraction) AS score, MAX(recognition_date) AS recent_date
            FROM participants
            WHERE role_code IN ('TA_GSM','GSM')
            GROUP BY employee_id, role_code
            """
        ),
        params,
    ).mappings().all()
    aggregate: dict[int, dict] = {}
    for row in snapshot_rows:
        employee_id = int(row["employee_id"])
        current = aggregate.setdefault(employee_id, {"count": 0, "score": 0.0, "recent_date": "", "role_code": row["role_code"]})
        current["count"] += int(row["event_count"] or 0)
        current["score"] += float(row["score"] or 0)
        if str(row["recent_date"] or "") >= str(current["recent_date"] or ""):
            current["recent_date"] = str(row["recent_date"] or "")
            current["role_code"] = row["role_code"]

    legacy_query = query.filter(
        or_(
            RecognitionRecord.recognizer_role_code_snapshot.is_(None),
            RecognitionRecord.operator_role_code_snapshot.is_(None),
        )
    )
    historical_role_cache: dict[tuple[int, str], Role | None] = {}
    for row in legacy_query.all():
        participants: dict[int, str | None] = {
            row.recognizer_employee_id: row.recognizer_role_code_snapshot,
            row.operator_employee_id: row.operator_role_code_snapshot,
        }
        for participant_id, snap_code in participants.items():
            if snap_code is not None:
                continue
            # Older rows did not have role snapshots. Resolve them by the
            # recognition date, never by the employee's current role.
            cache_key = (participant_id, row.recognition_date)
            if cache_key not in historical_role_cache:
                historical_role_cache[cache_key] = role_at(db, participant_id, row.recognition_date)
            historical_role = historical_role_cache[cache_key]
            code = historical_role.code if historical_role else None
            if code not in GSM_CODES:
                continue
            data = aggregate.setdefault(participant_id, {"count": 0, "score": 0.0, "recent_date": row.recognition_date, "role_code": code})
            data["count"] += 1
            data["score"] += float(row.credited_fraction or 0)
            data["recent_date"] = max(data["recent_date"], row.recognition_date)
    employee_rows = db.query(Employee).filter(Employee.id.in_(aggregate.keys()) if aggregate else Employee.id == -1).all()
    role_names = {
        role.code: role.name
        for role in db.query(Role).filter(Role.code.in_({item["role_code"] for item in aggregate.values()})).all()
    } if aggregate else {}
    value = keyword.strip().lower()
    rows=[]
    for employee in employee_rows:
        if value and value not in employee.name.lower() and value not in employee.employee_no.lower():
            continue
        data=aggregate[employee.id]
        rows.append({"employee_id":employee.id,"employee_no":employee.employee_no,"employee_name":employee.name,"role_name":role_names.get(data["role_code"],data["role_code"]),"leader_name":"","count":int(data["count"]),"score":round(float(data["score"]),2),"recent_date":data["recent_date"],"leave_days":0,"charged_days":0,"recognition_score":0,"deduction_score":0,"attendance_score":0,"total_score":0})
    rows.sort(key=lambda item:(-item["count"],-item["score"],item["employee_no"]))
    for index,item in enumerate(rows,1): item["rank"]=index
    offset=(page-1)*page_size
    return {"category":"gsm_leader","subtype_id":subtype_id,"subtype_name":subtype.name if subtype else "全部加分类别","sort_by":"count","start_date":start_value,"end_date":end_value,"attraction_id":attraction.id if attraction else None,"attraction_name":attraction.name if attraction else "全部景点圈","total":len(rows),"page":page,"page_size":page_size,"pages":max(1,ceil(len(rows)/page_size)),"rows":rows[offset:offset+page_size]}


def pr_ranking_payload(
    db: Session,
    user: V2User,
    start_value: str,
    end_value: str,
    category: str,
    subtype_id: int | None,
    sort_by: str,
    keyword: str,
    page: int,
    page_size: int,
    attraction_id: int | None = None,
) -> dict:
    if user.role.code not in GSM_CODES | {"AM", "OM"}:
        raise HTTPException(403, "仅TA GSM、GSM、AM和OM可以查看PR排名数据")
    start = parse_iso_date(start_value, "开始日期")
    end = parse_iso_date(end_value, "结束日期")
    if start > end:
        raise HTTPException(400, "开始日期不能晚于结束日期")
    if end > date.today():
        raise HTTPException(400, "结束日期不能晚于今天")
    if (end - start).days > 366:
        raise HTTPException(400, "单次排名查询最多支持367天")
    if category not in {"overall", "recognition", "deduction", "absence", "leader", "gsm_leader"}:
        raise HTTPException(400, "排名类型无效")
    if sort_by not in {"score", "count"}:
        raise HTTPException(400, "排名依据无效")

    attraction = None
    if attraction_id is not None:
        attraction = (
            db.query(Attraction)
            .filter(
                Attraction.id == attraction_id,
                Attraction.active.is_(True),
                Attraction.employee_circle.is_(True),
            )
            .first()
        )
        if not attraction:
            raise HTTPException(400, "请选择有效的员工景点圈")
    if category == "gsm_leader":
        return gsm_recognizer_ranking_payload(db, start_value, end_value, subtype_id, keyword, page, page_size, attraction)
    keyword_value = keyword.strip().lower()
    end_iso = end.isoformat()
    role_codes = LEADER_CODES if category == "leader" else FRONTLINE_CODES
    employees, role_map = ranking_employees(db, attraction_id, end_iso, role_codes)
    if keyword_value:
        employees = [
            employee for employee in employees
            if keyword_value in employee.name.lower() or keyword_value in employee.employee_no.lower()
        ]
    employee_ids = [employee.id for employee in employees]
    leader_names = ranking_leader_names(db, employee_ids, end_iso) if category != "leader" else {}
    aggregates: dict[int, dict] = {employee_id: {} for employee_id in employee_ids}
    subtype_name = ""

    if category == "recognition":
        subtype = db.get(RecognitionType, subtype_id) if subtype_id else None
        if subtype_id and (not subtype or not subtype.active):
            raise HTTPException(400, "请选择有效的认可类型")
        subtype_name = subtype.name if subtype else "全部加分类别"
        rows = db.query(RecognitionRecord.employee_id, func.count(RecognitionRecord.id), text("SUM(CASE WHEN recognition_date < '2026-09-01' THEN fraction ELSE credited_fraction END)"), func.max(RecognitionRecord.recognition_date)).filter(
            RecognitionRecord.employee_id.in_(employee_ids) if employee_ids else RecognitionRecord.employee_id == -1,
            RecognitionRecord.status == "confirmed", RecognitionRecord.recognition_date >= start_value, RecognitionRecord.recognition_date <= end_value,
        )
        if subtype:
            rows = rows.filter(RecognitionRecord.recognition_type_id == subtype.id)
        rows = rows.group_by(RecognitionRecord.employee_id).all()
        for employee_id, count, score, recent_date in rows:
            aggregates[employee_id] = {"count": int(count or 0), "score": float(score or 0), "recent_date": recent_date or ""}
    elif category == "deduction":
        subtype = db.get(DeductionType, subtype_id) if subtype_id else None
        if subtype_id and (not subtype or not subtype.active):
            raise HTTPException(400, "请选择有效的扣分类型")
        subtype_name = subtype.name if subtype else "全部扣分类型"
        rows = db.query(DeductionRecord.employee_id, func.count(DeductionRecord.id), func.sum(DeductionRecord.points), func.max(DeductionRecord.occurred_on)).filter(
            DeductionRecord.employee_id.in_(employee_ids) if employee_ids else DeductionRecord.employee_id == -1,
            DeductionRecord.status == "active", DeductionRecord.occurred_on >= start_value, DeductionRecord.occurred_on <= end_value,
        )
        if subtype:
            rows = rows.filter(DeductionRecord.deduction_type_id == subtype.id)
        rows = rows.group_by(DeductionRecord.employee_id).all()
        for employee_id, count, score, recent_date in rows:
            aggregates[employee_id] = {"count": int(count or 0), "score": float(score or 0), "recent_date": recent_date or ""}
    elif category == "absence":
        if subtype_id not in {None, 1, 2}:
            raise HTTPException(400, "缺勤类型无效")
        subtype_name = "违规病假" if subtype_id == 2 else ("病假" if subtype_id == 1 else "全部缺勤类型")
        sick_rows = db.query(SickLeaveRecord).filter(
            SickLeaveRecord.employee_id.in_(employee_ids) if employee_ids else SickLeaveRecord.employee_id == -1,
            SickLeaveRecord.status == "active", SickLeaveRecord.leave_end_date >= start_value, SickLeaveRecord.leave_start_date <= end_value,
        )
        if subtype_id == 1:
            sick_rows = sick_rows.filter(SickLeaveRecord.is_violation.is_(False))
        elif subtype_id == 2:
            sick_rows = sick_rows.filter(SickLeaveRecord.is_violation.is_(True))
        sick_rows = sick_rows.all()
        selected_months: dict[int, set[str]] = {employee_id: set() for employee_id in employee_ids}
        for row in sick_rows:
            data = aggregates.setdefault(row.employee_id, {})
            data["count"] = int(data.get("count", 0)) + 1
            data["leave_days"] = float(data.get("leave_days", 0)) + float(row.leave_days)
            data["charged_days"] = float(data.get("charged_days", 0)) + float(row.charged_days)
            data["recent_date"] = max(str(data.get("recent_date") or ""), row.leave_start_date)
            selected_months.setdefault(row.employee_id, set()).add(row.attendance_month)
        months = months_between(start, end)
        for month in months:
            ensure_month_attendance(db, month, employee_ids)
        if months:
            db.commit()
        attendance_rows = (
            db.query(AttendanceMonthlyScore)
            .filter(
                AttendanceMonthlyScore.employee_id.in_(employee_ids) if employee_ids else AttendanceMonthlyScore.employee_id == -1,
                AttendanceMonthlyScore.attendance_month.in_(months),
            )
            .all()
        )
        for row in attendance_rows:
            if row.attendance_month in selected_months.get(row.employee_id, set()):
                aggregates[row.employee_id]["score"] = float(aggregates[row.employee_id].get("score", 0)) + float(row.sick_deduction or 0)
    elif category == "leader":
        subtype = db.get(RecognitionType, subtype_id) if subtype_id else None
        if subtype_id and (not subtype or not subtype.active):
            raise HTTPException(400, "请选择有效认可类型")
        subtype_name = subtype.name if subtype else "全部加分类别"
        query = db.query(RecognitionRecord).filter(
            *([RecognitionRecord.home_attraction_id == attraction_id] if attraction_id is not None else []),
            RecognitionRecord.status == "confirmed",
            RecognitionRecord.recognition_date >= start_value,
            RecognitionRecord.recognition_date <= end_value,
        )
        if subtype:
            query = query.filter(RecognitionRecord.recognition_type_id == subtype.id)
        for row in query.all():
            # One confirmed record is one independent scoring event. Attribute it to
            # participating current supervisors, while deduplicating only within it.
            for employee_id in {row.recognizer_employee_id, row.operator_employee_id}:
                if employee_id not in aggregates:
                    continue
                data = aggregates[employee_id]
                data["count"] = int(data.get("count", 0)) + 1
                data["score"] = float(data.get("score", 0)) + float(effective_recognition_credit(row))
                data["recent_date"] = max(str(data.get("recent_date") or ""), row.recognition_date)
    else:
        months = months_between(start, end)
        for month in months:
            ensure_month_attendance(db, month, employee_ids)
        if months:
            db.commit()
        recognition_totals = dict(
            db.query(RecognitionRecord.employee_id, text("SUM(CASE WHEN recognition_date < '2026-09-01' THEN fraction ELSE credited_fraction END)"))
            .filter(
                RecognitionRecord.employee_id.in_(employee_ids) if employee_ids else RecognitionRecord.employee_id == -1,
                RecognitionRecord.status == "confirmed",
                RecognitionRecord.recognition_date >= start_value,
                RecognitionRecord.recognition_date <= end_value,
            )
            .group_by(RecognitionRecord.employee_id)
            .all()
        )
        deduction_totals = dict(
            db.query(DeductionRecord.employee_id, func.sum(DeductionRecord.points))
            .filter(
                DeductionRecord.employee_id.in_(employee_ids) if employee_ids else DeductionRecord.employee_id == -1,
                DeductionRecord.status == "active",
                DeductionRecord.occurred_on >= start_value,
                DeductionRecord.occurred_on <= end_value,
            )
            .group_by(DeductionRecord.employee_id)
            .all()
        )
        attendance_totals = dict(
            db.query(AttendanceMonthlyScore.employee_id, func.sum(AttendanceMonthlyScore.final_score))
            .filter(
                AttendanceMonthlyScore.employee_id.in_(employee_ids) if employee_ids else AttendanceMonthlyScore.employee_id == -1,
                AttendanceMonthlyScore.attendance_month.in_(months),
                AttendanceMonthlyScore.eligible.is_(True),
            )
            .group_by(AttendanceMonthlyScore.employee_id)
            .all()
        )
        for employee_id in employee_ids:
            recognition_score = float(recognition_totals.get(employee_id) or 0)
            deduction_score = float(deduction_totals.get(employee_id) or 0)
            attendance_score = float(attendance_totals.get(employee_id) or 0)
            aggregates[employee_id] = {
                "recognition_score": recognition_score,
                "deduction_score": deduction_score,
                "attendance_score": attendance_score,
                "total_score": round(recognition_score + attendance_score - deduction_score, 2),
            }

    result_rows = []
    for employee in employees:
        data = aggregates.get(employee.id, {})
        role = role_map.get(employee.id)
        result_rows.append(
            {
                "employee_id": employee.id,
                "employee_no": employee.employee_no,
                "employee_name": employee.name,
                "role_name": role.name if role else "未配置",
                "leader_name": leader_names.get(employee.id, "") if category != "leader" else "",
                "count": int(data.get("count", 0)),
                "score": round(float(data.get("score", 0)), 2),
                "recent_date": str(data.get("recent_date") or ""),
                "leave_days": round(float(data.get("leave_days", 0)), 1),
                "charged_days": int(data.get("charged_days", 0)),
                "recognition_score": round(float(data.get("recognition_score", 0)), 2),
                "deduction_score": round(float(data.get("deduction_score", 0)), 2),
                "attendance_score": round(float(data.get("attendance_score", 0)), 2),
                "total_score": round(float(data.get("total_score", 0)), 2),
            }
        )

    if category == "overall":
        result_rows.sort(key=lambda row: (-row["total_score"], -row["recognition_score"], row["employee_no"]))
    elif category == "leader":
        result_rows.sort(key=lambda row: (-row["count"], -row["score"], row["employee_no"]))
    elif sort_by == "count":
        result_rows.sort(key=lambda row: (-row["count"], -row["score"], row["employee_no"]))
    else:
        result_rows.sort(key=lambda row: (-row["score"], -row["count"], row["employee_no"]))
    for index, row in enumerate(result_rows, start=1):
        row["rank"] = index
    total = len(result_rows)
    offset = (page - 1) * page_size
    return {
        "category": category,
        "subtype_id": subtype_id,
        "subtype_name": subtype_name,
        "sort_by": sort_by,
        "start_date": start_value,
        "end_date": end_value,
        "attraction_id": attraction_id,
        "attraction_name": attraction.name if attraction else "全部景点圈",
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, ceil(total / page_size)),
        "rows": result_rows[offset:offset + page_size],
    }


@router.get("/pr-rankings")
def pr_rankings(
    start_date: str,
    end_date: str,
    category: str = "overall",
    subtype_id: int | None = None,
    sort_by: str = "score",
    keyword: str = "",
    attraction_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    user: V2User = Depends(current_user),
):
    page = max(1, page)
    page_size = min(max(10, page_size), 100)
    cache_key = (user.id, start_date, end_date, category, subtype_id, sort_by, keyword.strip(), page, page_size, attraction_id)
    with _pr_ranking_cache_lock:
        now = monotonic()
        cached = _pr_ranking_response_cache.get(cache_key)
        if cached and cached[0] > now:
            content = cached[1]
        else:
            payload = pr_ranking_payload(db, user, start_date, end_date, category, subtype_id, sort_by, keyword, page, page_size, attraction_id)
            content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            _pr_ranking_response_cache[cache_key] = (now + PR_RANKING_CACHE_SECONDS, content)
            if len(_pr_ranking_response_cache) > 256:
                _pr_ranking_response_cache.clear()
                _pr_ranking_response_cache[cache_key] = (now + PR_RANKING_CACHE_SECONDS, content)
    return Response(content=content, media_type="application/json")


@router.get("/pr-rankings/export")
def export_pr_rankings(
    start_date: str,
    end_date: str,
    category: str = "overall",
    subtype_id: int | None = None,
    sort_by: str = "score",
    keyword: str = "",
    attraction_id: int | None = None,
    db: Session = Depends(get_db),
    user: V2User = Depends(current_user),
):
    if user.role.code not in {"GSM", "AM", "OM"}:
        raise HTTPException(403, "当前角色仅支持查询PR排名，不能导出景点圈数据")
    data = pr_ranking_payload(db, user, start_date, end_date, category, subtype_id, sort_by, keyword, 1, 5000, attraction_id)
    wb = build_pr_rankings_workbook(db, data, category)
    watermark_workbook(wb, user.employee.employee_no)
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    write_audit(db, user.employee, "导出PR排名", "pr_ranking_export", category, after={"start_date": start_date, "end_date": end_date, "subtype_id": subtype_id, "sort_by": sort_by, "keyword": keyword, "row_count": len(data["rows"])})
    db.commit()
    ascii_filename = f"pr_rankings_{category}_{start_date}_{end_date}.xlsx"
    display_filename = f"PR排名_{category}_{start_date}_{end_date}.xlsx"
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": content_disposition(ascii_filename, display_filename)})


def statistics_hierarchy(db: Session, score_rows: list[dict], month_end: str, user: V2User) -> list[dict]:
    employee_ids = [int(score["employee_id"]) for score in score_rows]
    if not employee_ids:
        return []
    employees = {employee.id: employee for employee in db.query(Employee).filter(Employee.id.in_(employee_ids)).all()}
    employee_roles = roles_at(db, employee_ids, month_end)

    memberships = (
        db.query(GroupMembership)
        .filter(
            GroupMembership.employee_id.in_(employee_ids),
            GroupMembership.status == "active",
            GroupMembership.starts_on <= month_end,
            or_(GroupMembership.ends_on.is_(None), GroupMembership.ends_on >= month_end),
        )
        .order_by(GroupMembership.employee_id, GroupMembership.starts_on.desc(), GroupMembership.id.desc())
        .all()
    )
    membership_by_employee: dict[int, GroupMembership] = {}
    for membership in memberships:
        membership_by_employee.setdefault(membership.employee_id, membership)
    group_ids = {membership.group_id for membership in membership_by_employee.values()}
    groups = {group.id: group for group in db.query(WorkGroup).filter(WorkGroup.id.in_(group_ids)).all()} if group_ids else {}
    leader_assignments = (
        db.query(GroupLeaderAssignment)
        .filter(
            GroupLeaderAssignment.group_id.in_(group_ids),
            GroupLeaderAssignment.status == "active",
            GroupLeaderAssignment.starts_on <= month_end,
            or_(GroupLeaderAssignment.ends_on.is_(None), GroupLeaderAssignment.ends_on >= month_end),
        )
        .order_by(GroupLeaderAssignment.group_id, GroupLeaderAssignment.starts_on.desc(), GroupLeaderAssignment.id.desc())
        .all()
        if group_ids
        else []
    )
    leader_assignment_by_group: dict[int, GroupLeaderAssignment] = {}
    for assignment in leader_assignments:
        leader_assignment_by_group.setdefault(assignment.group_id, assignment)
    leader_ids = {assignment.leader_employee_id for assignment in leader_assignment_by_group.values()}
    leaders = {employee.id: employee for employee in db.query(Employee).filter(Employee.id.in_(leader_ids)).all()} if leader_ids else {}
    leader_roles = roles_at(db, leader_ids, month_end)

    attraction_ids = {score.get("attraction_id") for score in score_rows if score.get("attraction_id") is not None}
    attraction_rows = db.query(Attraction).filter(Attraction.id.in_(attraction_ids)).all() if attraction_ids else []
    attraction_names = {attraction.id: attraction.name for attraction in attraction_rows}
    managers_by_attraction: dict[int | None, dict[str, list[tuple[Employee, Role]]]] = {}
    if attraction_ids:
        scopes = (
            db.query(ManagementScope)
            .filter(
                ManagementScope.attraction_id.in_(attraction_ids),
                ManagementScope.starts_on <= month_end,
                or_(ManagementScope.ends_on.is_(None), ManagementScope.ends_on >= month_end),
            )
            .all()
        )
        manager_ids = {scope.employee_id for scope in scopes}
        manager_employees = {
            employee.id: employee for employee in db.query(Employee).filter(Employee.id.in_(manager_ids)).all()
        } if manager_ids else {}
        manager_roles = roles_at(db, manager_ids, month_end)
        candidates_by_attraction: dict[int, list[tuple[Employee, Role]]] = {}
        for scope in scopes:
            employee = manager_employees.get(scope.employee_id)
            role = manager_roles.get(scope.employee_id)
            if employee and role and role.code in GSM_CODES:
                candidates_by_attraction.setdefault(scope.attraction_id, []).append((employee, role))
        for attraction_id in attraction_ids:
            candidates = candidates_by_attraction.get(attraction_id, [])
            ordered = sorted(candidates, key=lambda item: (item[1].code != "GSM", item[0].employee_no))
            managers_by_attraction[attraction_id] = {
                "gsms": [item for item in ordered if item[1].code == "GSM"],
                "ta_gsms": [item for item in ordered if item[1].code == "TA_GSM"],
            }

    attractions: dict[int | None, dict] = {}
    for score in score_rows:
        employee = employees.get(score["employee_id"])
        if not employee:
            continue
        role = employee_roles.get(employee.id)
        # The team branch represents only scored frontline employees. GSM/TA GSM
        # are rendered once as circle-level management nodes, never as an
        # unassigned employee beneath a supervisor placeholder.
        if not role or role.code not in FRONTLINE_CODES:
            continue
        attraction_key = score.get("attraction_id")
        attraction_node = attractions.setdefault(
            attraction_key,
            {"name": attraction_names.get(attraction_key, "未设置景点圈"), "managers": {}},
        )
        manager_info = managers_by_attraction.get(attraction_key, {"gsms": [], "ta_gsms": []})
        gsms = manager_info["gsms"]
        ta_gsms = manager_info["ta_gsms"]
        manager_key = f"team-{attraction_key}" if len(gsms) > 1 else (gsms[0][0].id if gsms else None)
        manager_node = attraction_node["managers"].setdefault(
            manager_key,
            {
                "gsms": gsms,
                "ta_gsms": ta_gsms,
                "name": (f"主管组（由GSM共同承接：{'、'.join(employee.name for employee, _role in gsms)}）" if len(gsms) > 1 else (gsms[0][0].name if gsms else "未配置GSM")),
                "role_name": "" if len(gsms) != 1 else gsms[0][1].name,
                "leaders": {},
            },
        )
        membership = membership_by_employee.get(employee.id)
        group = groups.get(membership.group_id) if membership else None
        leader_assignment = leader_assignment_by_group.get(group.id) if group else None
        leader = leaders.get(leader_assignment.leader_employee_id) if leader_assignment else None
        leader_role = leader_roles.get(leader.id) if leader else None
        leader_key = f"employee-{leader.id}" if leader else (f"group-{group.id}" if group else "ungrouped")
        leader_node = manager_node["leaders"].setdefault(
            leader_key,
            {
                "name": leader.name if leader else "未配置主管",
                "role_name": leader_role.name if leader_role else "",
                "employees": [],
            },
        )
        leader_node["employees"].append(
            {
                "employee_id": employee.id,
                "employee_no": employee.employee_no,
                "employee_name": employee.name,
                "role_code": role.code if role else "",
                "role_name": role.name if role else "",
                "recognition_score": float(score["recognition_score"] or 0),
                "deduction_score": float(score["deduction_score"] or 0),
                "attendance_score": float(score["attendance_score"] or 0),
                "total_score": float(score["total_score"] or 0),
            }
        )

    result = []
    for attraction_key, attraction in sorted(attractions.items(), key=lambda item: item[1]["name"]):
        attraction_id = f"attraction-{attraction_key or 'none'}"
        result.append({"node_id": attraction_id, "parent_id": "", "level": 0, "node_type": "attraction", "name": attraction["name"]})
        for manager_key, manager in sorted(
            attraction["managers"].items(),
            key=lambda item: (
                -sum(
                    employee["total_score"]
                    for leader in item[1]["leaders"].values()
                    for employee in leader["employees"]
                ),
                item[1]["name"],
                str(item[0]),
            ),
        ):
            manager_id = f"{attraction_id}-gsm-{manager_key or 'none'}"
            gsms = manager["gsms"]
            ta_gsms = manager["ta_gsms"]
            for gsm, gsm_role in gsms:
                result.append({"node_id": f"{manager_id}-employee-{gsm.id}", "parent_id": attraction_id, "level": 1, "node_type": "gsm", "name": gsm.name, "role_name": gsm_role.name})
            # TA GSM is a peer of GSM, not the parent of the supervisor branch.
            # Emit it before the common branch so table order also communicates that relationship.
            for ta_gsm, ta_gsm_role in ta_gsms:
                result.append({"node_id": f"{attraction_id}-ta-gsm-{ta_gsm.id}", "parent_id": attraction_id, "level": 1, "node_type": "gsm", "name": ta_gsm.name, "role_name": ta_gsm_role.name})
            branch_employees = [employee for leader in manager["leaders"].values() for employee in leader["employees"]]
            if len(gsms) > 1:
                result.append({
                    "node_id": manager_id,
                    "parent_id": attraction_id,
                    "level": 1,
                    "node_type": "gsm_team",
                    "name": manager["name"],
                    "role_name": "",
                    "supervisor_count": len(manager["leaders"]),
                    "member_count": len(branch_employees),
                    "cm_count": sum(employee["role_code"] == "CM" for employee in branch_employees),
                    "tr_count": sum(employee["role_code"] == "TR" for employee in branch_employees),
                })
            elif len(gsms) == 1:
                manager_id = f"{manager_id}-employee-{gsms[0][0].id}"
            else:
                result.append({"node_id": manager_id, "parent_id": attraction_id, "level": 1, "node_type": "gsm", "name": manager["name"], "role_name": manager["role_name"]})
            # Sort the supervisor groups and their member rows independently by
            # comprehensive score. Names/numbers only break score ties.
            for leader_key, leader in sorted(
                manager["leaders"].items(),
                key=lambda item: (
                    item[1]["name"] == "未配置主管",
                    -sum(employee["total_score"] for employee in item[1]["employees"]),
                    item[1]["name"],
                    item[0],
                ),
            ):
                leader_id = f"{manager_id}-leader-{leader_key}"
                sorted_employees = sorted(
                    leader["employees"],
                    key=lambda item: (-item["total_score"], item["employee_name"], item["employee_no"]),
                )
                leader_recognition_score = round(sum(employee["recognition_score"] for employee in leader["employees"]), 2)
                leader_deduction_score = round(sum(employee["deduction_score"] for employee in leader["employees"]), 2)
                leader_attendance_score = round(sum(employee["attendance_score"] for employee in leader["employees"]), 2)
                leader_total_score = round(sum(employee["total_score"] for employee in leader["employees"]), 2)
                leader_average_score = round(leader_total_score / len(leader["employees"]), 2) if leader["employees"] else 0
                result.append(
                    {
                        "node_id": leader_id,
                        "parent_id": manager_id,
                        "level": 2,
                        "node_type": "supervisor",
                        "name": leader["name"],
                        "role_name": leader["role_name"],
                        "member_count": len(leader["employees"]),
                        "employee_ids": [employee["employee_id"] for employee in sorted_employees],
                        "recognition_score": leader_recognition_score,
                        "deduction_score": leader_deduction_score,
                        "attendance_score": leader_attendance_score,
                        "total_score": leader_total_score,
                        "average_score": leader_average_score,
                    }
                )
                for employee in sorted_employees:
                    result.append(
                        {
                            "node_id": f"employee-{employee['employee_id']}",
                            "parent_id": leader_id,
                            "level": 3,
                            "node_type": "employee",
                            **employee,
                        }
                    )
    return result


def statistics_payload(
    db: Session,
    month: str,
    attraction_id: int | None = None,
    keyword: str | None = None,
    title: str | None = None,
    user: V2User | None = None,
    *,
    include_records: bool = False,
    include_hierarchy: bool = True,
) -> dict:
    try:
        month_start = date.fromisoformat(f"{month}-01")
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "月份格式应为YYYY-MM") from exc
    month_end = month_start.replace(day=monthrange(month_start.year, month_start.month)[1]).isoformat()
    selected_title = (title or "").strip().upper()
    if selected_title and selected_title not in FRONTLINE_CODES:
        raise HTTPException(400, "Title只能选择CM或TR")
    allowed_attractions = scoped_hr_attraction_ids(db, user) if user else None
    if allowed_attractions is not None:
        if attraction_id is None:
            attraction_id = next(iter(allowed_attractions))
        elif attraction_id not in allowed_attractions:
            raise HTTPException(403, "景点圈HR只能查看所属景点圈数据")
    candidate_query = (
        db.query(Employee, EmployeeMonthOrganizationSnapshot, Attraction)
        .outerjoin(
            EmployeeMonthOrganizationSnapshot,
            and_(
                EmployeeMonthOrganizationSnapshot.employee_id == Employee.id,
                EmployeeMonthOrganizationSnapshot.score_month == month,
            ),
        )
        .outerjoin(Attraction, Attraction.id == Employee.attraction_id)
    )
    if attraction_id:
        candidate_query = candidate_query.filter(
            func.coalesce(EmployeeMonthOrganizationSnapshot.attraction_id, Employee.attraction_id) == attraction_id
        )
    if keyword:
        keyword_value = f"%{keyword.strip()}%"
        candidate_query = candidate_query.filter(or_(Employee.employee_no.like(keyword_value), Employee.name.like(keyword_value)))
    candidate_rows = candidate_query.all()
    if selected_title and candidate_rows:
        candidate_roles = roles_at(db, [row[0].id for row in candidate_rows], month_end)
        candidate_rows = [
            row for row in candidate_rows
            if candidate_roles.get(row[0].id) and candidate_roles[row[0].id].code == selected_title
        ]
    candidate_ids = [row[0].id for row in candidate_rows]

    # Attendance remains materialized for compatibility, but only for employees
    # in the requested scope. The savepoint keeps statistics and export read-only.
    statistics_savepoint = db.begin_nested()
    try:
        ensure_month_attendance(db, month, candidate_ids)
        loa_employee_ids = {
            row[0]
            for row in db.query(EmployeeLOAPeriod.employee_id)
            .filter(
                EmployeeLOAPeriod.employee_id.in_(candidate_ids) if candidate_ids else EmployeeLOAPeriod.employee_id == -1,
                EmployeeLOAPeriod.status != "cancelled",
                EmployeeLOAPeriod.starts_on <= month_end,
                or_(EmployeeLOAPeriod.ends_on.is_(None), EmployeeLOAPeriod.ends_on >= month_start.isoformat()),
            )
            .all()
        }
        if loa_employee_ids:
            for employee in db.query(Employee).filter(Employee.id.in_(loa_employee_ids)).all():
                recalculate_attendance(db, employee, month)
        db.flush()
        scores_by_employee = {
            int(row["employee_id"]): row
            for row in employee_month_scores(db, month, candidate_ids)
        }
        score_rows = []
        for employee, snapshot, current_attraction in candidate_rows:
            score = scores_by_employee.get(employee.id)
            if not score:
                continue
            score_rows.append(
                {
                    **score,
                    "account_deleted_at": employee.account_deleted_at,
                    "attraction_id": snapshot.attraction_id if snapshot else employee.attraction_id,
                    "attraction_name": snapshot.attraction_name if snapshot else (current_attraction.name if current_attraction else ""),
                    "organization_basis": "month_close_snapshot" if snapshot else "current",
                }
            )
        score_rows.sort(key=lambda row: (-float(row["total_score"] or 0), str(row["employee_no"])))
    finally:
        if statistics_savepoint.is_active:
            statistics_savepoint.rollback()
    loa_rows = []
    filtered_score_rows = []
    for row in score_rows:
        if loa_excludes_month(db, int(row["employee_id"]), month):
            loa_rows.append({
                **row,
                "recognition_score": 0,
                "attendance_score": 0,
                "deduction_score": 0,
                "total_score": 0,
                "employment_status": "LOA（当月不参与计分）",
            })
        else:
            filtered_score_rows.append(row)
    score_rows = filtered_score_rows
    loa_period_payloads = []
    if candidate_ids:
        for period in (
            db.query(EmployeeLOAPeriod)
            .filter(
                EmployeeLOAPeriod.employee_id.in_(candidate_ids),
                EmployeeLOAPeriod.status != "cancelled",
                EmployeeLOAPeriod.starts_on <= month_end,
                or_(EmployeeLOAPeriod.ends_on.is_(None), EmployeeLOAPeriod.ends_on >= month_start.isoformat()),
            )
            .order_by(EmployeeLOAPeriod.starts_on, EmployeeLOAPeriod.id)
            .all()
        ):
            loa_period_payloads.append({
                "employee_id": period.employee_id,
                "starts_on": max(period.starts_on, month_start.isoformat()),
                "ends_on": min(period.ends_on or month_end, month_end),
                "note": period.note or "",
            })
    visible_employee_ids = {
        int(row["employee_id"])
        for row in [*score_rows, *loa_rows]
    }
    data_updated_at = ""
    if visible_employee_ids:
        update_values = [
            db.query(func.max(RecognitionRecord.submitted_at)).filter(RecognitionRecord.recognition_month == month, RecognitionRecord.employee_id.in_(visible_employee_ids)).scalar(),
            db.query(func.max(RecognitionRecord.voided_at)).filter(RecognitionRecord.recognition_month == month, RecognitionRecord.employee_id.in_(visible_employee_ids)).scalar(),
            db.query(func.max(DeductionRecord.submitted_at)).filter(DeductionRecord.deduction_month == month, DeductionRecord.employee_id.in_(visible_employee_ids)).scalar(),
            db.query(func.max(DeductionRecord.voided_at)).filter(DeductionRecord.deduction_month == month, DeductionRecord.employee_id.in_(visible_employee_ids)).scalar(),
            db.query(func.max(SickLeaveRecord.submitted_at)).filter(SickLeaveRecord.attendance_month == month, SickLeaveRecord.employee_id.in_(visible_employee_ids)).scalar(),
            db.query(func.max(SickLeaveRecord.voided_at)).filter(SickLeaveRecord.attendance_month == month, SickLeaveRecord.employee_id.in_(visible_employee_ids)).scalar(),
            db.query(func.max(AttendanceMonthlyScore.calculated_at)).filter(AttendanceMonthlyScore.attendance_month == month, AttendanceMonthlyScore.employee_id.in_(visible_employee_ids)).scalar(),
            db.query(func.max(Employee.updated_at)).filter(Employee.id.in_(visible_employee_ids)).scalar(),
        ]
        latest_update = max((value for value in update_values if value), default=None)
        if latest_update:
            data_updated_at = latest_update.strftime("%Y-%m-%d %H:%M")
    recognition_payloads: list[dict] = []
    deduction_payloads: list[dict] = []
    sick_leave_rows_payload: list[dict] = []
    if include_records:
        record_scope = visible_employee_ids or {-1}
        recognition_rows = db.query(RecognitionRecord).filter(RecognitionRecord.recognition_month == month, RecognitionRecord.employee_id.in_(record_scope)).order_by(RecognitionRecord.submitted_at.desc()).all()
        deduction_rows = db.query(DeductionRecord).filter(DeductionRecord.deduction_month == month, DeductionRecord.employee_id.in_(record_scope)).order_by(DeductionRecord.submitted_at.desc()).all()
        sick_leave_rows = db.query(SickLeaveRecord).filter(SickLeaveRecord.attendance_month == month, SickLeaveRecord.employee_id.in_(record_scope)).order_by(SickLeaveRecord.submitted_at.desc()).all()
        all_record_rows = [*recognition_rows, *deduction_rows, *sick_leave_rows]
        record_employee_ids = {int(row.employee_id) for row in all_record_rows}
        employees = {
            employee.id: employee
            for employee in db.query(Employee).filter(Employee.id.in_(record_employee_ids)).all()
        } if record_employee_ids else {}
        historical_roles = roles_at(db, record_employee_ids, month_end) if record_employee_ids else {}
        keyword_value = (keyword or "").strip().casefold()

        def record_is_visible(row) -> bool:
            employee = employees.get(int(row.employee_id))
            if isinstance(row, RecognitionRecord):
                row_attraction_id = row.home_attraction_id
                employee_no = row.employee_no
                employee_name = row.employee_name
            elif isinstance(row, DeductionRecord):
                row_attraction_id = row.attraction_id_snapshot
                employee_no = row.employee_no
                employee_name = row.employee_name
            else:
                row_attraction_id = row.attraction_id_snapshot
                employee_no = row.employee_no_snapshot or (employee.employee_no if employee else "")
                employee_name = row.employee_name_snapshot or (employee.name if employee else "")
            row_attraction_id = row_attraction_id or (employee.attraction_id if employee else None)
            if attraction_id and row_attraction_id != attraction_id:
                return False
            if keyword_value and keyword_value not in f"{employee_no} {employee_name}".casefold():
                return False
            if selected_title:
                snapshot_title = str(getattr(row, "employee_role_snapshot", "") or "").strip().upper()
                historical_role = historical_roles.get(int(row.employee_id))
                row_title = snapshot_title if snapshot_title in FRONTLINE_CODES else (historical_role.code if historical_role else "")
                if row_title != selected_title:
                    return False
            return True

        recognition_payloads = [recognition_payload(row) for row in recognition_rows if record_is_visible(row)]
        deduction_payloads = [deduction_payload(row) for row in deduction_rows if record_is_visible(row)]
        sick_leave_rows_payload = sick_leave_payloads(db, [row for row in sick_leave_rows if record_is_visible(row)])
    attraction_totals: dict[object, dict] = {}
    for row in score_rows:
        key = row.get("attraction_id")
        node = attraction_totals.setdefault(key, {
            "attraction_id": key,
            "attraction_name": row.get("attraction_name") or "未设置景点圈",
            "employee_count": 0,
            "recognition_score": 0.0,
            "attendance_score": 0.0,
            "deduction_score": 0.0,
            "total_score": 0.0,
        })
        node["employee_count"] += 1
        for field in ("recognition_score", "attendance_score", "deduction_score", "total_score"):
            node[field] += float(row[field] or 0)
    for node in attraction_totals.values():
        for field in ("recognition_score", "attendance_score", "deduction_score", "total_score"):
            node[field] = round(node[field], 2)
    payload = {
        "month": month,
        "title": selected_title,
        "filters": {
            "attraction_id": attraction_id or "",
            "title": selected_title,
            "keyword": (keyword or "").strip(),
        },
        "data_updated_at": data_updated_at,
        "organization_basis": "月结封存归属" if any(row.get("organization_basis") == "month_close_snapshot" for row in score_rows) else "当前组织归属（该月尚未月结）",
        "summary": {
            "employee_count": len(score_rows),
            "recognition_score": round(sum(float(row["recognition_score"] or 0) for row in score_rows), 2),
            "attendance_score": round(sum(float(row["attendance_score"] or 0) for row in score_rows), 2),
            "deduction_score": round(sum(float(row["deduction_score"] or 0) for row in score_rows), 2),
            "total_score": round(sum(float(row["total_score"] or 0) for row in score_rows), 2),
        },
        "hierarchy": statistics_hierarchy(db, score_rows, month_end, user) if user and include_hierarchy else [],
        "loa_rows": loa_rows,
        "loa_periods": loa_period_payloads,
        "by_attraction": sorted(attraction_totals.values(), key=lambda item: str(item["attraction_name"])),
    }
    if include_records:
        payload.update(
            {
                "scores": score_rows,
                "recognitions": recognition_payloads,
                "deductions": deduction_payloads,
                "sick_leaves": sick_leave_rows_payload,
            }
        )
    return payload


def statistics_details_payload(db: Session, month: str, employee_ids: list[int]) -> dict[str, dict]:
    if not employee_ids:
        return {}
    score_rows = {
        row["employee_id"]: dict(row)
        for row in employee_month_scores(db, month, employee_ids)
    }
    employees = {employee.id: employee for employee in db.query(Employee).filter(Employee.id.in_(employee_ids)).all()}
    recognition_groups: dict[int, list[RecognitionRecord]] = {employee_id: [] for employee_id in employee_ids}
    deduction_groups: dict[int, list[DeductionRecord]] = {employee_id: [] for employee_id in employee_ids}
    sick_leave_groups: dict[int, list[SickLeaveRecord]] = {employee_id: [] for employee_id in employee_ids}
    for row in (
        db.query(RecognitionRecord)
        .filter(RecognitionRecord.employee_id.in_(employee_ids), RecognitionRecord.recognition_month == month)
        .order_by(RecognitionRecord.submitted_at.desc(), RecognitionRecord.id.desc())
        .all()
    ):
        recognition_groups[row.employee_id].append(row)
    for row in (
        db.query(DeductionRecord)
        .filter(DeductionRecord.employee_id.in_(employee_ids), DeductionRecord.deduction_month == month)
        .order_by(DeductionRecord.submitted_at.desc(), DeductionRecord.id.desc())
        .all()
    ):
        deduction_groups[row.employee_id].append(row)
    for row in (
        db.query(SickLeaveRecord)
        .filter(SickLeaveRecord.employee_id.in_(employee_ids), SickLeaveRecord.attendance_month == month)
        .order_by(SickLeaveRecord.submitted_at.desc(), SickLeaveRecord.id.desc())
        .all()
    ):
        sick_leave_groups[row.employee_id].append(row)
    attendance_rows = {
        row.employee_id: row
        for row in db.query(AttendanceMonthlyScore)
        .filter(AttendanceMonthlyScore.employee_id.in_(employee_ids), AttendanceMonthlyScore.attendance_month == month)
        .all()
    }
    empty_score = {"recognition_score": 0, "deduction_score": 0, "attendance_score": 0, "total_score": 0}
    result: dict[str, dict] = {}
    for employee_id in employee_ids:
        if employee_id not in employees:
            continue
        detail = member_score_detail_payload(
            db,
            employees[employee_id],
            score_rows.get(employee_id, empty_score),
            recognition_groups[employee_id],
            deduction_groups[employee_id],
            sick_leave_groups[employee_id],
            attendance_rows.get(employee_id),
        )
        if loa_excludes_month(db, employee_id, month):
            detail["details"]["recognition_score"] = 0
            detail["details"]["attendance_score"] = 0
            detail["details"]["deduction_score"] = 0
            detail["details"]["total_score"] = 0
            detail["details"]["loa_excluded"] = True
        # Keep the established detail lists while returning the identity fields
        # used by the dedicated statistics-detail page title.
        result[str(employee_id)] = {**detail["details"], **{key: detail[key] for key in ("employee_id", "employee_no", "employee_name", "role_name")}}
    return result


@router.get("/statistics")
def statistics(month: str, attraction_id: int | None = None, keyword: str | None = None, title: str | None = None, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("DATA_VIEW", "DATA_EXPORT"))):
    cache_key = (user.id, user.role.code, month, attraction_id, (keyword or "").strip(), (title or "").strip().upper())
    with _statistics_cache_lock:
        now = monotonic()
        cached = _statistics_response_cache.get(cache_key)
        if cached and cached[0] > now:
            content = cached[1]
        else:
            payload = statistics_payload(db, month, attraction_id, keyword, title, user)
            content = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                default=lambda value: float(value) if isinstance(value, Decimal) else str(value),
            ).encode("utf-8")
            _statistics_response_cache[cache_key] = (now + STATISTICS_CACHE_SECONDS, content)
            if len(_statistics_response_cache) > 128:
                _statistics_response_cache.clear()
                _statistics_response_cache[cache_key] = (now + STATISTICS_CACHE_SECONDS, content)
    return Response(content=content, media_type="application/json")


TREND_DEFAULT_MONTHS = 6
TREND_MAX_MONTHS = 12
TREND_SCORE_FIELDS = ("recognition_score", "attendance_score", "deduction_score", "total_score")


def trend_month_keys(end_month: str, count: int) -> list[str]:
    """Return `count` YYYY-MM keys ending at end_month, oldest first."""
    try:
        anchor = date.fromisoformat(f"{end_month}-01")
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "月份格式应为YYYY-MM") from exc
    keys: list[str] = []
    year, index = anchor.year, anchor.month
    for _ in range(count):
        keys.append(f"{year:04d}-{index:02d}")
        index -= 1
        if index == 0:
            year, index = year - 1, 12
    keys.reverse()
    return keys


def statistics_trend_payload(db: Session, end_month: str, months: int, attraction_id: int | None, title: str | None, user: V2User) -> dict:
    """Month-by-month totals built from statistics_payload so figures match /statistics exactly.

    Scope, organization basis (month snapshot first) and LOA exclusion all come
    from that one function; the trend never re-implements the scoring rules.
    """
    # An explicit out-of-range value is clamped, not silently replaced by the default.
    span = min(max(int(TREND_DEFAULT_MONTHS if months is None else months), 1), TREND_MAX_MONTHS)
    keys = trend_month_keys(end_month, span)
    overall: list[dict] = []
    circles: dict[object, dict] = {}
    for key in keys:
        payload = statistics_payload(db, key, attraction_id, None, title, user, include_hierarchy=False)
        summary = payload["summary"]
        overall.append({"month": key, "employee_count": summary["employee_count"],
                        **{field: summary[field] for field in TREND_SCORE_FIELDS}})
        for node in payload["by_attraction"]:
            circle = circles.setdefault(node["attraction_id"], {
                "attraction_id": node["attraction_id"],
                "attraction_name": node["attraction_name"],
                "points": {},
            })
            circle["points"][key] = {"month": key, "employee_count": node["employee_count"],
                                     **{field: node[field] for field in TREND_SCORE_FIELDS}}
    blank = {"employee_count": 0, **{field: 0.0 for field in TREND_SCORE_FIELDS}}
    series = [
        {
            "attraction_id": circle["attraction_id"],
            "attraction_name": circle["attraction_name"],
            "series": [circle["points"].get(key, {"month": key, **blank}) for key in keys],
        }
        for circle in circles.values()
    ]
    series.sort(key=lambda item: str(item["attraction_name"]))
    return {
        "months": keys,
        "end_month": keys[-1],
        "overall": overall,
        "by_attraction": series,
        "filters": {"attraction_id": attraction_id or "", "title": (title or "").strip().upper(), "months": span},
        "max_months": TREND_MAX_MONTHS,
    }


@router.get("/statistics/trend")
def statistics_trend(month: str, months: int = TREND_DEFAULT_MONTHS, attraction_id: int | None = None, title: str | None = None, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("DATA_VIEW", "DATA_EXPORT"))):
    cache_key = ("trend", user.id, user.role.code, month, months, attraction_id, (title or "").strip().upper())
    with _statistics_cache_lock:
        now = monotonic()
        cached = _statistics_response_cache.get(cache_key)
        if cached and cached[0] > now:
            content = cached[1]
        else:
            payload = statistics_trend_payload(db, month, months, attraction_id, title, user)
            content = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                default=lambda value: float(value) if isinstance(value, Decimal) else str(value),
            ).encode("utf-8")
            _statistics_response_cache[cache_key] = (now + STATISTICS_CACHE_SECONDS, content)
            if len(_statistics_response_cache) > 128:
                _statistics_response_cache.clear()
                _statistics_response_cache[cache_key] = (now + STATISTICS_CACHE_SECONDS, content)
    return Response(content=content, media_type="application/json")


@router.get("/statistics/details")
def statistics_details(
    month: str,
    employee_ids: str,
    effective_only: bool = False,
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("DATA_VIEW", "DATA_EXPORT")),
):
    try:
        date.fromisoformat(f"{month}-01")
        selected_ids = list(dict.fromkeys(int(value) for value in employee_ids.split(",") if value.strip()))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "月份或员工参数格式不正确") from exc
    if not selected_ids or len(selected_ids) > 200:
        raise HTTPException(400, "每次可查询1至200名员工明细")
    employees = db.query(Employee).filter(Employee.id.in_(selected_ids)).all()
    if len(employees) != len(selected_ids):
        raise HTTPException(404, "部分员工不存在")
    allowed_attractions = scoped_hr_attraction_ids(db, user)
    if allowed_attractions is not None and any(employee.attraction_id not in allowed_attractions for employee in employees):
        raise HTTPException(403, "景点圈HR只能查看所属景点圈数据")
    details_savepoint = db.begin_nested()
    try:
        ensure_month_attendance(db, month, selected_ids)
        db.flush()
        details = statistics_details_payload(db, month, selected_ids)
    finally:
        if details_savepoint.is_active:
            details_savepoint.rollback()
    if effective_only:
        for detail in details.values():
            detail["all_records"] = [record for record in detail.get("all_records", []) if record.get("included")]
    return {"month": month, "details": details}


@router.get("/statistics/my-exports")
def my_statistics_exports(
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("DATA_EXPORT")),
):
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.operator_id == user.id, AuditLog.action == "导出统计数据")
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(8)
        .all()
    )
    attraction_ids: set[int] = set()
    parsed_rows: list[tuple[AuditLog, dict]] = []
    for row in rows:
        try:
            details = json.loads(row.after_json or "{}")
        except (TypeError, ValueError):
            details = {}
        attraction_id = details.get("attraction_id")
        if isinstance(attraction_id, int):
            attraction_ids.add(attraction_id)
        parsed_rows.append((row, details))
    attraction_names = {
        attraction.id: attraction.name
        for attraction in db.query(Attraction).filter(Attraction.id.in_(attraction_ids)).all()
    } if attraction_ids else {}
    return {
        "items": [
            {
                "id": row.id,
                "exported_at": row.created_at.strftime("%Y-%m-%d %H:%M"),
                "month": row.entity_id or "",
                "attraction_name": attraction_names.get(details.get("attraction_id"), "全部景点圈"),
                "title": details.get("title") or "CM/TR全部",
                "keyword": details.get("keyword") or "",
                "employee_count": int(details.get("employee_count") or 0),
            }
            for row, details in parsed_rows
        ]
    }


@router.get("/statistics/export")
def export_statistics(month: str, attraction_id: int | None = None, keyword: str | None = None, title: str | None = None, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("DATA_EXPORT"))):
    data = statistics_payload(db, month, attraction_id, keyword, title, user, include_records=True)
    wb, attraction_label, title_label = build_statistics_workbook(
        db,
        data,
        month=month,
        attraction_id=attraction_id,
        keyword=keyword,
        exporter_no=user.employee.employee_no,
        exporter_name=user.name,
        exporter_role_name=user.role.name,
        exporter_role_code=user.role.code,
    )
    watermark_workbook(wb, user.employee.employee_no)
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    write_audit(db, user.employee, "导出统计数据", "statistics_export", month, after={"attraction_id": attraction_id, "title": data["title"], "keyword": keyword or "", "employee_count": len(data["scores"])})
    db.commit()
    ascii_filename = f"recognition_v2_{month.replace('-', '_')}_circle-{attraction_id or 'all'}_{data['title'] or 'all'}.xlsx"
    display_filename = f"认可数据_{month}_{attraction_label}_{title_label}.xlsx"
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": content_disposition(ascii_filename, display_filename)})
