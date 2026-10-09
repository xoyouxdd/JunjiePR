"""Monthly scoring, historical population selection and organization hierarchy."""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, text
from sqlalchemy.orm import Session, selectinload

from app.access_policy import scoped_hr_attraction_ids
from app.organization_queries import group_display_metadata_bulk
from app.record_payloads import deduction_payload, recognition_payload, sick_leave_payloads
from app.role_constants import FRONTLINE_CODES, GSM_CODES, SCORED_BASE_CODES
from app.score_queries import employee_month_scores
from app.v2_models import Attraction, AttendanceMonthlyScore, DeductionRecord, Employee, EmployeeMonthOrganizationSnapshot, EmployeeLOAPeriod, GroupMembership, ManagementScope, RecognitionAttachment, RecognitionRecord, Role, SickLeaveRecord, WorkGroup
from app.services.attendance import ensure_month_attendance, recalculate_attendance
from app.services.identity import acting_period_notes, base_roles_at, identity_labels, roles_at
from app.services.performance_details import load_monthly_records

if TYPE_CHECKING:
    from app.v2_auth import V2User

def dashboard_payload(db: Session, month: str, user: V2User) -> dict:
    if user.base_role.code not in SCORED_BASE_CODES:
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
    if score.get("loa_excluded"):
        categories = {name: 0.0 for name in categories}
    records = (
        db.query(RecognitionRecord)
        .options(selectinload(RecognitionRecord.attachments).selectinload(RecognitionAttachment.file))
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



def statistics_hierarchy(db: Session, score_rows: list[dict], month_end: str, user: V2User, *, supervisor_page: bool = False) -> list[dict]:
    """Circle → GSM → group leader → CM/TR; the supervisor page lists the
    circle's supervisors together under its GSM instead of by group."""
    employee_ids = [int(score["employee_id"]) for score in score_rows]
    if not employee_ids:
        return []
    employees = {employee.id: employee for employee in db.query(Employee).filter(Employee.id.in_(employee_ids)).all()}
    employee_roles = base_roles_at(db, employee_ids, month_end)
    employee_labels = identity_labels(db, employee_ids, month_end)

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
    group_labels = group_display_metadata_bulk(db, group_ids, month_end)

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
        # The team branch represents only scored employees of the page's
        # category. GSM/TA GSM are rendered once as circle-level management
        # nodes, never as an unassigned employee beneath a supervisor placeholder.
        if not role or role.code not in ({"SUPERVISOR"} if supervisor_page else FRONTLINE_CODES):
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
                "name": (f"GSM共同管理（{'、'.join(employee.name for employee, _role in gsms)}）" if len(gsms) > 1 else (gsms[0][0].name if gsms else "未配置GSM")),
                "role_name": "" if len(gsms) != 1 else gsms[0][1].name,
                "leaders": {},
            },
        )
        membership = membership_by_employee.get(employee.id)
        group = groups.get(membership.group_id) if membership else None
        if supervisor_page:
            leader_key = "supervisors"
            leader_defaults = {"name": "主管", "role_name": "", "employees": []}
        else:
            # One node per group, titled with its fixed name; the role line
            # shows "主管 X · 代理主管 Y".
            leader_key = f"group-{group.id}" if group else "ungrouped"
            leader_defaults = {
                "name": group.name if group else "未分组",
                "role_name": group_labels.get(group.id, {}).get("label", "") if group else "",
                "code": group.code if group else None,
                "employees": [],
            }
        leader_node = manager_node["leaders"].setdefault(leader_key, leader_defaults)
        leader_node["employees"].append(
            {
                "employee_id": employee.id,
                "employee_no": employee.employee_no,
                "employee_name": employee.name,
                "role_code": role.code if role else "",
                "role_name": employee_labels.get(employee.id) or (role.name if role else ""),
                "acting_note": score.get("acting_note", ""),
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
                    item[1]["name"] == "未分组",
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
    if selected_title and selected_title not in SCORED_BASE_CODES:
        raise HTTPException(400, "Title只能选择CM、TR或主管")
    supervisor_page = selected_title == "SUPERVISOR"
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
    if candidate_rows:
        # A month belongs to one scoring category: the base identity at month
        # end (frozen in the close snapshot once the month is closed).
        candidate_roles = base_roles_at(db, [row[0].id for row in candidate_rows], month_end)

        def month_category_code(row) -> str:
            snapshot = row[1]
            if snapshot is not None and snapshot.base_role_code:
                return snapshot.base_role_code
            role = candidate_roles.get(row[0].id)
            return role.code if role else ""

        wanted = {selected_title} if selected_title else FRONTLINE_CODES
        candidate_rows = [row for row in candidate_rows if month_category_code(row) in wanted]
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
        month_notes = acting_period_notes(db, [int(row["employee_id"]) for row in score_rows], month_start.isoformat(), month_end)
        for row in score_rows:
            row["acting_note"] = month_notes.get(int(row["employee_id"]), "")
    finally:
        if statistics_savepoint.is_active:
            statistics_savepoint.rollback()
    loa_rows = []
    filtered_score_rows = []
    for row in score_rows:
        if int(row["employee_id"]) in loa_employee_ids:
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
        records = load_monthly_records(db, month, visible_employee_ids, include_attendance=False)
        recognition_rows, deduction_rows, sick_leave_rows = records.recognitions, records.deductions, records.sick_leaves
        all_record_rows = [*recognition_rows, *deduction_rows, *sick_leave_rows]
        record_employee_ids = {int(row.employee_id) for row in all_record_rows}
        employees = {
            employee.id: employee
            for employee in db.query(Employee).filter(Employee.id.in_(record_employee_ids)).all()
        } if record_employee_ids else {}
        historical_roles = base_roles_at(db, record_employee_ids, month_end) if record_employee_ids else {}
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
        "hierarchy": statistics_hierarchy(db, score_rows, month_end, user, supervisor_page=supervisor_page) if user and include_hierarchy else [],
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
