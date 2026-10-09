"""PR performance and recognition-issuer rankings, independent of HTTP routes."""
from __future__ import annotations

from datetime import date
from math import ceil
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from app.date_utils import months_between, parse_iso_date
from app.organization_queries import group_display_metadata_bulk
from app.record_payloads import effective_recognition_credit
from app.role_constants import FRONTLINE_CODES, GSM_CODES, LEADER_CODES
from app.score_policy import loa_month_exclusion
from app.v2_models import Attraction, AttendanceMonthlyScore, DeductionRecord, DeductionType, Employee, GroupMembership, RecognitionRecord, RecognitionType, Role, SickLeaveRecord
from app.services.attendance import ensure_month_attendance
from app.services.identity import acting_period_notes, base_roles_at, identity_labels, role_at, roles_at

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
        rows.append({"employee_id":employee.id,"employee_no":employee.employee_no,"employee_name":employee.name,"role_name":role_names.get(data["role_code"],data["role_code"]),"group_label":"","count":int(data["count"]),"score":round(float(data["score"]),2),"recent_date":data["recent_date"],"leave_days":0,"charged_days":0,"recognition_score":0,"deduction_score":0,"attendance_score":0,"total_score":0})
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
    population: str = "frontline",
) -> dict:
    if user.role.code not in GSM_CODES | {"AM", "OM"}:
        raise HTTPException(403, "仅TA GSM、GSM、AM和OM可以查看PR排名数据")
    if population not in {"frontline", "supervisor"}:
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
    if category == "gsm_leader":
        return gsm_recognizer_ranking_payload(db, start_value, end_value, subtype_id, keyword, page, page_size, attraction)
    keyword_value = keyword.strip().lower()
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
        rows = db.query(RecognitionRecord.employee_id, func.count(RecognitionRecord.id), text("SUM(CASE WHEN recognition_date < '2026-09-01' THEN fraction ELSE credited_fraction END)"), func.max(RecognitionRecord.recognition_date), func.sum(RecognitionRecord.fraction)).filter(
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
    elif category == "leader":
        subtype = db.get(RecognitionType, subtype_id) if subtype_id else None
        if subtype_id and (not subtype or not subtype.active):
            raise HTTPException(400, "请选择有效认可类型")
        subtype_name = subtype.name if subtype else "全部加分类别"
        excluded_issuers: set[int] = set()
        query = db.query(RecognitionRecord).filter(
            *([RecognitionRecord.home_attraction_id == attraction_id] if attraction_id is not None else []),
            RecognitionRecord.status == "confirmed",
            RecognitionRecord.recognition_date >= start_value,
            RecognitionRecord.recognition_date <= end_value,
        )
        if subtype:
            query = query.filter(RecognitionRecord.recognition_type_id == subtype.id)
        historical_role_cache: dict[tuple[int, str], Role | None] = {}

        def acted_as_leader(employee_id: int, snapshot_code: str | None, on_date: str) -> bool:
            # Records keep the role actually used; older rows resolve it by date.
            if snapshot_code is None:
                key = (employee_id, on_date)
                if key not in historical_role_cache:
                    historical_role_cache[key] = role_at(db, employee_id, on_date)
                snapshot_code = historical_role_cache[key].code if historical_role_cache[key] else None
            return snapshot_code in LEADER_CODES

        for row in query.all():
            # One confirmed record is one independent scoring event. Attribute it to
            # the supervisors who issued it in that role, deduplicating only within
            # it. A leader's own self-submitted record is not an issued recognition,
            # and a former TA主管 keeps the records issued during the duty.
            participants: set[int] = set()
            if acted_as_leader(row.recognizer_employee_id, row.recognizer_role_code_snapshot, row.recognition_date):
                participants.add(row.recognizer_employee_id)
            if row.source != "self" and acted_as_leader(row.operator_employee_id, row.operator_role_code_snapshot, row.recognition_date):
                participants.add(row.operator_employee_id)
            participants.discard(row.employee_id)
            for employee_id in participants:
                if employee_id not in aggregates:
                    if employee_id in excluded_issuers:
                        continue
                    issuer = db.get(Employee, employee_id)
                    if (
                        not issuer
                        or (attraction_id is not None and issuer.attraction_id != attraction_id)
                        or (keyword_value and keyword_value not in issuer.name.lower() and keyword_value not in issuer.employee_no.lower())
                    ):
                        excluded_issuers.add(employee_id)
                        continue
                    employees.append(issuer)
                    role_map[employee_id] = role_at(db, employee_id, row.recognition_date)
                    aggregates[employee_id] = {}
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
