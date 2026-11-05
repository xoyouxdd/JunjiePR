"""PR performance and recognition-issuer rankings, independent of HTTP routes."""
from __future__ import annotations
from app.employee_numbers import employee_number

from datetime import date
from math import ceil
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from app.date_utils import months_between, parse_iso_date
from app.organization_queries import group_display_metadata_bulk
from app.role_constants import FRONTLINE_CODES, GSM_CODES, LEADER_CODES
from app.score_policy import loa_month_exclusion
from app.v2_models import Attraction, AttendanceMonthlyScore, DeductionRecord, DeductionType, Employee, GroupMembership, RecognitionRecord, RecognitionType, Role, SickLeaveRecord
from app.services.attendance import ensure_month_attendance
from app.services.issuer_rankings import issuer_ranking_payload
from app.services.identity import acting_period_notes, base_roles_at, identity_labels, roles_at

if TYPE_CHECKING:
    from app.v2_auth import V2User

def ranking_employees(db: Session, attraction_id: int | None, on_date: str, role_codes: set[str], *, acting: bool = False) -> tuple[list[Employee], dict[int, Role]]:
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
    # Score rankings follow the base identity on the range end date; the
    # issuing ranking follows the role in use (an acting TA主管 issues as one).
    role_map = (roles_at if acting else base_roles_at)(db, [employee.id for employee in candidates], on_date)
    selected = [employee for employee in candidates if role_map.get(employee.id) and role_map[employee.id].code in role_codes]
    return selected, role_map



def ranking_group_labels(db: Session, employee_ids: list[int], on_date: str) -> dict[int, str]:
    """'热力追踪A组 · 主管 X · 代理主管 Y' for each employee's group on the date."""
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
    display = group_display_metadata_bulk(db, {membership.group_id for membership in membership_by_employee.values()}, on_date)
    labels = {}
    for employee_id, membership in membership_by_employee.items():
        group = display.get(membership.group_id, {})
        labels[employee_id] = " · ".join(part for part in (group.get("name", ""), group.get("label", "")) if part)
    return labels



def pr_ranking_scope(db: Session, user: V2User, attraction_id: int | None, population: str = "frontline", category: str = "overall") -> int | None:
    """Authorize before cache lookup; every ranking and export shares this scope."""
    if user.role.code not in GSM_CODES | {"AM", "OM", "HR_CIRCLE"}:
        raise HTTPException(403, "仅TA GSM、GSM、AM、OM和景点圈HR可以查看PR排名数据")
    if user.role.code == "HR_CIRCLE":
        own_id = user.employee.attraction_id
        circle = db.get(Attraction, own_id) if own_id is not None else None
        if not circle or not circle.active or not circle.employee_circle:
            raise HTTPException(403, "景点圈HR未配置有效的所属景点圈")
        if attraction_id is not None and attraction_id != own_id:
            raise HTTPException(403, "景点圈HR仅可查询和导出所属景点圈的PR排名")
        if population != "frontline" or category not in {"overall", "recognition", "deduction", "absence", "leader", "gsm_leader"}:
            raise HTTPException(403, "景点圈HR仅可查询和导出本圈CM/TR绩效及发放统计，不开放主管个人绩效排名")
        return own_id
    return attraction_id


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
    population: str = "frontline",
) -> dict:
    attraction_id = pr_ranking_scope(db, user, attraction_id, population, category)
    if population not in {"frontline", "supervisor", "all"} or (population == "all" and category != "gsm_leader") or (category == "leader" and population != "frontline"):
        raise HTTPException(400, "排名人群无效")
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
    if category in {"leader", "gsm_leader"}:
        return issuer_ranking_payload(db, user, start_value, end_value, category, subtype_id, keyword, page, page_size, attraction, population)
    keyword_value = employee_number(keyword).lower()
    end_iso = end.isoformat()
    # Score rankings rank one population: CM/TR, or supervisors (主管绩效排行).
    role_codes = LEADER_CODES if category == "leader" else ({"SUPERVISOR"} if population == "supervisor" else FRONTLINE_CODES)
    employees, role_map = ranking_employees(db, attraction_id, end_iso, role_codes, acting=category == "leader")
    if keyword_value:
        employees = [
            employee for employee in employees
            if keyword_value in employee.name.lower() or keyword_value in employee.employee_no.lower()
        ]
    employee_ids = [employee.id for employee in employees]
    group_labels = ranking_group_labels(db, employee_ids, end_iso) if category != "leader" else {}
    aggregates: dict[int, dict] = {employee_id: {} for employee_id in employee_ids}
    subtype_name = ""
    uncapped_ranking = False

    if category == "recognition":
        subtype = db.get(RecognitionType, subtype_id) if subtype_id else None
        if subtype_id and (not subtype or not subtype.active):
            raise HTTPException(400, "请选择有效的认可类型")
        subtype_name = subtype.name if subtype else "全部加分类别"
        uncapped_ranking = bool(subtype and subtype.code in {"SAFETY", "COURTESY", "EFFICIENCY", "SHOW", "INCLUSION"})
        rows = db.query(RecognitionRecord.employee_id, func.count(RecognitionRecord.id), text("SUM(CASE WHEN recognition_date < '2026-09-01' AND COALESCE(monthly_cap_rule_code,'') != 'configured_category' THEN fraction ELSE credited_fraction END)"), func.max(RecognitionRecord.recognition_date), func.sum(RecognitionRecord.fraction)).filter(
            RecognitionRecord.employee_id.in_(employee_ids) if employee_ids else RecognitionRecord.employee_id == -1,
            RecognitionRecord.status == "confirmed", RecognitionRecord.recognition_date >= start_value, RecognitionRecord.recognition_date <= end_value,
        )
        if subtype:
            rows = rows.filter(RecognitionRecord.recognition_type_id == subtype.id)
        rows = rows.group_by(RecognitionRecord.employee_id).all()
        for employee_id, count, score, recent_date, uncapped_score in rows:
            aggregates[employee_id] = {"count": int(count or 0), "score": float(score or 0), "recent_date": recent_date or "", "uncapped_score": float(uncapped_score or 0)}
    elif category == "deduction":
        subtype = db.get(DeductionType, subtype_id) if subtype_id else None
        if subtype_id and (not subtype or not subtype.active):
            raise HTTPException(400, "请选择有效的扣分类型")
        subtype_name = subtype.name if subtype else "全部扣分类型"
        rows = db.query(DeductionRecord.employee_id, func.count(DeductionRecord.id), func.sum(DeductionRecord.points), func.max(DeductionRecord.occurred_on)).filter(
            DeductionRecord.employee_id.in_(employee_ids) if employee_ids else DeductionRecord.employee_id == -1,
            DeductionRecord.status == "active", or_(DeductionRecord.upgrade_role.is_(None), DeductionRecord.upgrade_role != "source_second"), DeductionRecord.occurred_on >= start_value, DeductionRecord.occurred_on <= end_value,
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
    else:
        months = months_between(start, end)
        for month in months:
            ensure_month_attendance(db, month, employee_ids)
        if months:
            db.commit()
        recognition_totals = dict(
            db.query(RecognitionRecord.employee_id, text("SUM(CASE WHEN recognition_date < '2026-09-01' AND COALESCE(monthly_cap_rule_code,'') != 'configured_category' THEN fraction ELSE credited_fraction END)"))
            .filter(
                RecognitionRecord.employee_id.in_(employee_ids) if employee_ids else RecognitionRecord.employee_id == -1,
                RecognitionRecord.status == "confirmed",
                RecognitionRecord.recognition_date >= start_value,
                RecognitionRecord.recognition_date <= end_value,
                ~loa_month_exclusion(RecognitionRecord.employee_id, RecognitionRecord.recognition_month),
            )
            .group_by(RecognitionRecord.employee_id)
            .all()
        )
        deduction_totals = dict(
            db.query(DeductionRecord.employee_id, func.sum(DeductionRecord.points))
            .filter(
                DeductionRecord.employee_id.in_(employee_ids) if employee_ids else DeductionRecord.employee_id == -1,
                DeductionRecord.status == "active",
                or_(DeductionRecord.upgrade_role.is_(None), DeductionRecord.upgrade_role != "source_second"),
                DeductionRecord.occurred_on >= start_value,
                DeductionRecord.occurred_on <= end_value,
                ~loa_month_exclusion(DeductionRecord.employee_id, DeductionRecord.deduction_month),
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
                ~loa_month_exclusion(AttendanceMonthlyScore.employee_id, AttendanceMonthlyScore.attendance_month),
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
    ranked_ids = [employee.id for employee in employees]
    # Score rankings show the identity on the range end date and note any
    # acting-duty window; the issuing ranking keeps the role used when issuing.
    ranking_labels = identity_labels(db, ranked_ids, end_iso) if category != "leader" else {}
    acting_notes = acting_period_notes(db, ranked_ids, start_value, end_value) if category != "leader" else {}
    for employee in employees:
        data = aggregates.get(employee.id, {})
        role = role_map.get(employee.id)
        result_rows.append(
            {
                "employee_id": employee.id,
                "employee_no": employee.employee_no,
                "employee_name": employee.name,
                "role_name": ranking_labels.get(employee.id) or (role.name if role else "未配置"),
                "acting_note": acting_notes.get(employee.id, ""),
                # Supervisors have no group leader of their own.
                "group_label": "" if category == "leader" else ("—" if population == "supervisor" else group_labels.get(employee.id, "未分组")),
                "count": int(data.get("count", 0)),
                "uncapped_score": round(float(data.get("uncapped_score", 0)), 2),
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
    elif uncapped_ranking:
        result_rows.sort(key=lambda row: (-row["uncapped_score"], -row["count"], row["employee_no"]))
    else:
        result_rows.sort(key=lambda row: (-row["score"], -row["count"], row["employee_no"]))
    for index, row in enumerate(result_rows, start=1):
        row["rank"] = index
    total = len(result_rows)
    offset = (page - 1) * page_size
    return {
        "category": category,
        "population": population if category != "leader" else "frontline",
        "subtype_id": subtype_id,
        "subtype_name": subtype_name,
        "uncapped_ranking": uncapped_ranking,
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
