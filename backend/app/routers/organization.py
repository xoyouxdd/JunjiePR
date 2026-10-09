"""Work group, group leader, circle transfer and org tree endpoints.

Groups are named entities ("热力追踪A组").  HR places CM/TR members in a group
from 员工管理, and sets each group's 主管 (a base 主管) and 代理主管 (an
acting TA主管) from 小组管理.  Choosing a person never creates a group.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.v2_auth import V2User, require_permissions
from app.v2_database import get_db
from app.v2_models import Attraction, CircleTransferRequest, DeductionFollowUp, DeductionRecord, Employee, GroupLeaderAssignment, GroupMembership, GroupTransfer, GroupTransferMember, RecognitionRecord, Role, SickLeaveRecord, SystemAlert, WorkGroup
from app.v2_services import (
    FRONTLINE_CODES,
    active_group_memberships,
    active_group_memberships_bulk,
    base_role_at,
    base_roles_at,
    current_group_for_employee,
    current_leader_for_employee,
    duties_at_bulk,
    group_acting_eligible,
    group_code_sort_key,
    group_display_name,
    group_leader_of_type,
    group_supervisor_eligible,
    gsm_candidates_for_attractions_bulk,
    identity_labels,
    next_group_code,
    recalculate_attendance,
    sync_pending_reviewers,
    write_audit,
)
from app.routers._shared import (
    client_ip,
    employee_payloads,
    ensure_month_open,
    ensure_scoped_hr_attraction,
    ensure_scoped_hr_employee,
    group_display_metadata_bulk,
    invalidate_data_caches,
    scoped_hr_attraction_ids,
)

router = APIRouter()

# Base identities placed in groups, and those listed as 主管.  Legacy
# TA主管 / TA GSM rows whose base could not be resolved are placed by role.
MEMBER_BASE_CODES = FRONTLINE_CODES | {"TA_SUPERVISOR"}
SUPERVISOR_BASE_CODES = {"SUPERVISOR", "TA_GSM"}
LEADER_TYPE_LABELS = {"formal": "主管", "acting": "代理主管"}


def gsm_candidates_for_attraction(db: Session, attraction_id: int, on_date: str | None = None) -> list[tuple[Employee, Role]]:
    return gsm_candidates_for_attractions_bulk(db, [attraction_id], on_date).get(attraction_id, [])


def scoped_circles(db: Session, user: V2User) -> list[Attraction]:
    allowed = scoped_hr_attraction_ids(db, user)
    query = db.query(Attraction).filter(Attraction.active.is_(True), Attraction.employee_circle.is_(True))
    if allowed is not None:
        query = query.filter(Attraction.id.in_(allowed))
    return query.order_by(Attraction.name).all()


def open_groups_by_circle(db: Session, attraction_ids: list[int]) -> dict[int, list[WorkGroup]]:
    grouped: dict[int, list[WorkGroup]] = {attraction_id: [] for attraction_id in attraction_ids}
    if not attraction_ids:
        return grouped
    for group in db.query(WorkGroup).filter(WorkGroup.status != "closed", WorkGroup.attraction_id.in_(attraction_ids)).all():
        grouped[group.attraction_id].append(group)
    for rows in grouped.values():
        rows.sort(key=lambda group: (group_code_sort_key(group.code), group.name, group.id))
    return grouped


def circle_gsm_names(db: Session, attraction_ids: list[int], on_date: str) -> dict[int, dict[str, list[str]]]:
    candidates = gsm_candidates_for_attractions_bulk(db, attraction_ids, on_date)
    return {
        attraction_id: {
            "gsm_names": [employee.name for employee, role in rows if role.code == "GSM"],
            "ta_gsm_names": [employee.name for employee, role in rows if role.code == "TA_GSM"],
        }
        for attraction_id, rows in candidates.items()
    }


def leader_ref(display: dict, leader_type: str) -> dict | None:
    employee_id = display.get(f"{leader_type}_leader_id")
    return {"id": employee_id, "name": display.get(f"{leader_type}_leader_name", "")} if employee_id else None


def active_leader_assignments(db: Session, employee_ids: list[int] | set[int]) -> list[GroupLeaderAssignment]:
    """Today's leader assignments of these employees in open groups."""
    ids = list(employee_ids)
    if not ids:
        return []
    today = date.today().isoformat()
    return (
        db.query(GroupLeaderAssignment)
        .join(WorkGroup, WorkGroup.id == GroupLeaderAssignment.group_id)
        .filter(
            GroupLeaderAssignment.leader_employee_id.in_(ids),
            GroupLeaderAssignment.status == "active",
            GroupLeaderAssignment.starts_on <= today,
            or_(GroupLeaderAssignment.ends_on.is_(None), GroupLeaderAssignment.ends_on >= today),
            WorkGroup.status != "closed",
        )
        .all()
    )


@router.get("/hr/organization")
def hr_organization(db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    """员工管理: per circle, its 主管, its groups with members, and the unassigned."""
    today_value = date.today().isoformat()
    allowed_attractions = scoped_hr_attraction_ids(db, user)
    # Hide departed employees whose login account has been archived only in
    # this management tree; retain employee/history rows for other queries.
    employee_query = db.query(Employee).filter(
        or_(Employee.is_active.is_(True), Employee.account_deleted_at.is_(None))
    )
    if allowed_attractions is not None:
        employee_query = employee_query.filter(Employee.attraction_id.in_(allowed_attractions))
    employees = employee_query.order_by(Employee.name, Employee.employee_no).all()
    payloads = employee_payloads(db, employees, today_value)
    circles = scoped_circles(db, user)
    circle_ids = [circle.id for circle in circles]
    groups_by_circle = open_groups_by_circle(db, circle_ids)
    all_group_ids = [group.id for rows in groups_by_circle.values() for group in rows]
    group_display = group_display_metadata_bulk(db, all_group_ids, today_value)
    gsm_names = circle_gsm_names(db, circle_ids, today_value)
    led = {}
    for assignment in active_leader_assignments(db, [employee.id for employee in employees]):
        led.setdefault((assignment.leader_employee_id, assignment.leader_type), assignment.group)

    def with_leading(employee_id: int) -> dict:
        payload = dict(payloads[employee_id])
        formal = led.get((employee_id, "formal"))
        acting = led.get((employee_id, "acting"))
        payload["led_group_id"] = formal.id if formal else None
        payload["led_group_name"] = formal.name if formal else ""
        payload["acting_group_id"] = acting.id if acting else None
        payload["acting_group_name"] = acting.name if acting else ""
        return payload

    rows = []
    placed: set[int] = set()
    warnings = []
    for circle in circles:
        circle_employees = [employee for employee in employees if employee.attraction_id == circle.id]
        active = [employee for employee in circle_employees if employee.is_active]
        supervisors = [with_leading(employee.id) for employee in active if payloads[employee.id]["base_role_code"] in SUPERVISOR_BASE_CODES]
        members = [employee for employee in active if payloads[employee.id]["base_role_code"] in MEMBER_BASE_CODES]
        open_ids = {group.id for group in groups_by_circle.get(circle.id, [])}
        group_rows = []
        for group in groups_by_circle.get(circle.id, []):
            display = group_display.get(group.id, {})
            group_members = [with_leading(employee.id) for employee in members if payloads[employee.id]["group_id"] == group.id]
            group_rows.append(
                {
                    "id": group.id,
                    "code": group.code,
                    "name": group.name,
                    "formal_leader": leader_ref(display, "formal"),
                    "acting_leader": leader_ref(display, "acting"),
                    "member_count": len(group_members),
                    "members": group_members,
                }
            )
        unassigned = [with_leading(employee.id) for employee in members if payloads[employee.id]["group_id"] not in open_ids]
        managers = [
            payloads[employee.id] for employee in active
            if payloads[employee.id]["base_role_code"] not in (SUPERVISOR_BASE_CODES | MEMBER_BASE_CODES)
        ]
        inactive = [payloads[employee.id] for employee in circle_employees if not employee.is_active]
        for employee in circle_employees:
            placed.add(employee.id)
            payload = payloads[employee.id]
            if employee.is_active and payload["duty_role_code"] == "TA_SUPERVISOR" and not led.get((employee.id, "acting")):
                warnings.append({"employee_id": employee.id, "employee_name": employee.name, "attraction_id": circle.id, "message": f"{employee.name}已是代理TA主管，但还没有代理任何小组"})
        rows.append(
            {
                "id": circle.id,
                "name": circle.name,
                **gsm_names.get(circle.id, {"gsm_names": [], "ta_gsm_names": []}),
                "supervisors": supervisors,
                "groups": group_rows,
                "unassigned": unassigned,
                "managers": managers,
                "inactive": inactive,
            }
        )
    others = [payloads[employee.id] for employee in employees if employee.id not in placed]
    return {"circles": rows, "others": others, "warnings": warnings}


@router.get("/hr/group-options")
def group_options(db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    """Open groups of every employee circle, for choosing a member's group.

    Not limited to the HR's own circle: the target HR of a circle transfer
    chooses from the target circle."""
    circles = db.query(Attraction).filter(Attraction.active.is_(True), Attraction.employee_circle.is_(True)).all()
    groups_by_circle = open_groups_by_circle(db, [circle.id for circle in circles])
    display = group_display_metadata_bulk(db, [group.id for rows in groups_by_circle.values() for group in rows])
    return [
        {
            "id": group.id,
            "code": group.code,
            "name": group.name,
            "attraction_id": group.attraction_id,
            "leader_label": display.get(group.id, {}).get("label", ""),
        }
        for circle in sorted(circles, key=lambda row: row.name)
        for group in groups_by_circle.get(circle.id, [])
    ]


def circle_transfer_payload(
    db: Session,
    row: CircleTransferRequest,
    *,
    groups: dict[int, WorkGroup] | None = None,
    leaders: dict[int, Employee] | None = None,
) -> dict:
    group_map = groups or {}
    leader_map = leaders or {}
    source_group = group_map.get(row.source_group_id) if row.source_group_id else None
    source_leader = leader_map.get(row.source_leader_id) if row.source_leader_id else None
    target_group = group_map.get(row.target_group_id) if row.target_group_id else None
    target_leader = leader_map.get(row.target_leader_id) if row.target_leader_id else None
    if row.source_group_id and not source_group:
        source_group = db.get(WorkGroup, row.source_group_id)
    if row.target_group_id and not target_group:
        target_group = db.get(WorkGroup, row.target_group_id)
    try:
        migrated_counts = json.loads(row.migrated_record_counts_json or "{}")
    except (TypeError, ValueError):
        migrated_counts = {}
    return {
        "id": row.id,
        "employee_id": row.employee_id,
        "employee_no": row.employee_no,
        "employee_name": row.employee_name,
        "source_attraction_id": row.source_attraction_id,
        "source_attraction_name": row.source_attraction_name,
        "target_attraction_id": row.target_attraction_id,
        "target_attraction_name": row.target_attraction_name,
        "source_group_name": source_group.name if source_group else "未分组",
        "source_leader_name": source_leader.name if source_leader else "未设置",
        "target_group_id": row.target_group_id,
        "target_group_name": target_group.name if target_group else "",
        "target_leader_id": row.target_leader_id,
        "target_leader_name": target_leader.name if target_leader else "",
        "reason": row.reason,
        "status": row.status,
        "status_name": {"pending": "待目标HR确认", "completed": "已生效", "rejected": "已拒绝", "cancelled": "已撤回"}.get(row.status, row.status),
        "requested_by_name": row.requested_by_name,
        "requested_at": row.requested_at.strftime("%Y-%m-%d %H:%M:%S"),
        "reviewed_by_name": row.reviewed_by_name or "",
        "reviewed_at": row.reviewed_at.strftime("%Y-%m-%d %H:%M:%S") if row.reviewed_at else "",
        "review_note": row.review_note or "",
        "completed_at": row.completed_at.strftime("%Y-%m-%d %H:%M:%S") if row.completed_at else "",
        "migrated_record_counts": migrated_counts,
    }


@router.get("/hr/circle-transfers")
def circle_transfers(db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    allowed_attractions = scoped_hr_attraction_ids(db, user)
    transfer_rows = db.query(CircleTransferRequest).order_by(CircleTransferRequest.requested_at.desc(), CircleTransferRequest.id.desc()).limit(500).all()
    if allowed_attractions is not None:
        transfer_rows = [
            row for row in transfer_rows
            if row.source_attraction_id in allowed_attractions or row.target_attraction_id in allowed_attractions
        ]
    circles = (
        db.query(Attraction)
        .filter(Attraction.active.is_(True), Attraction.employee_circle.is_(True))
        .order_by(Attraction.name)
        .all()
    )
    group_ids = {row.source_group_id for row in transfer_rows if row.source_group_id} | {
        row.target_group_id for row in transfer_rows if row.target_group_id
    }
    leader_ids = {row.source_leader_id for row in transfer_rows if row.source_leader_id} | {
        row.target_leader_id for row in transfer_rows if row.target_leader_id
    }
    groups = (
        {group.id: group for group in db.query(WorkGroup).filter(WorkGroup.id.in_(group_ids)).all()}
        if group_ids
        else {}
    )
    leaders = (
        {leader.id: leader for leader in db.query(Employee).filter(Employee.id.in_(leader_ids)).all()}
        if leader_ids
        else {}
    )
    return {
        "items": [circle_transfer_payload(db, row, groups=groups, leaders=leaders) for row in transfer_rows],
        "target_circles": [{"id": row.id, "name": row.name} for row in circles],
    }


@router.post("/hr/circle-transfers")
def create_circle_transfer(payload: dict, request: Request, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    employee = db.get(Employee, int(payload.get("employee_id") or 0))
    employee_role = base_role_at(db, employee.id) if employee else None
    if not employee or not employee.is_active or not employee_role or employee_role.code not in FRONTLINE_CODES:
        raise HTTPException(400, "只能为在职CM/TR发起景点圈调动")
    ensure_scoped_hr_employee(db, user, employee)
    source = db.get(Attraction, employee.attraction_id) if employee.attraction_id else None
    target = db.get(Attraction, int(payload.get("target_attraction_id") or 0))
    if not source or not source.employee_circle or not target or not target.active or not target.employee_circle:
        raise HTTPException(400, "原景点圈或目标景点圈无效")
    if source.id == target.id:
        raise HTTPException(400, "目标景点圈不能与当前景点圈相同")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(400, "调动原因必填")
    existing = db.query(CircleTransferRequest).filter(
        CircleTransferRequest.employee_id == employee.id,
        CircleTransferRequest.status == "pending",
    ).first()
    if existing:
        raise HTTPException(409, "该员工已有待确认的景点圈调动申请")
    source_group = current_group_for_employee(db, employee.id)
    source_leader = current_leader_for_employee(db, employee.id)
    row = CircleTransferRequest(
        employee_id=employee.id,
        employee_no=employee.employee_no,
        employee_name=employee.name,
        source_attraction_id=source.id,
        source_attraction_name=source.name,
        target_attraction_id=target.id,
        target_attraction_name=target.name,
        source_group_id=source_group.id if source_group else None,
        source_leader_id=source_leader.id if source_leader else None,
        reason=reason,
        status="pending",
        requested_by=user.id,
        requested_by_name=user.name,
    )
    db.add(row)
    db.flush()
    write_audit(
        db,
        user.employee,
        "发起跨景点圈调动",
        "circle_transfer",
        row.id,
        after=circle_transfer_payload(db, row),
        reason=reason,
        ip_address=client_ip(request),
    )
    db.commit()
    return {"ok": True, "transfer": circle_transfer_payload(db, row)}


def complete_circle_transfer(db: Session, row: CircleTransferRequest, target_group: WorkGroup, reviewer: V2User, request: Request) -> dict:
    employee = db.get(Employee, row.employee_id)
    if not employee or employee.attraction_id != row.source_attraction_id:
        raise HTTPException(409, "员工景点圈已经变化，请撤回申请后重新发起")
    today_value = date.today().isoformat()
    current_month = today_value[:7]
    ensure_month_open(db, current_month, row.source_attraction_id, "迁出员工及当月数据")
    ensure_month_open(db, current_month, row.target_attraction_id, "迁入员工及当月数据")
    old_memberships = (
        db.query(GroupMembership)
        .filter(GroupMembership.employee_id == employee.id, GroupMembership.status == "active")
        .all()
    )
    for membership in old_memberships:
        membership.status = "ended"
        membership.ends_on = max(membership.starts_on, today_value)
        membership.reason = f"跨景点圈调动至{row.target_attraction_name}"
    db.add(
        GroupMembership(
            group_id=target_group.id,
            employee_id=employee.id,
            starts_on=today_value,
            status="active",
            reason=f"由{row.source_attraction_name}调入",
        )
    )
    employee.attraction_id = row.target_attraction_id
    employee.updated_at = datetime.now()
    db.flush()
    # The target group may have no leader yet: pending reviews then wait
    # unassigned and follow-ups stay with their current supervisor.
    target_leader = current_leader_for_employee(db, employee.id)
    pending_reviews = db.query(RecognitionRecord).filter(
        RecognitionRecord.employee_id == employee.id,
        RecognitionRecord.status == "pending",
    ).update({RecognitionRecord.assigned_reviewer_id: target_leader.id if target_leader else None}, synchronize_session=False)
    pending_follow_ups = db.query(DeductionFollowUp).filter(
        DeductionFollowUp.employee_id == employee.id,
        DeductionFollowUp.status == "pending",
    ).update(
        {DeductionFollowUp.supervisor_id: target_leader.id, DeductionFollowUp.supervisor_name: target_leader.name},
        synchronize_session=False,
    ) if target_leader else 0
    recognition_count = db.query(RecognitionRecord).filter(
        RecognitionRecord.employee_id == employee.id,
        RecognitionRecord.recognition_month == current_month,
    ).update(
        {RecognitionRecord.home_attraction_id: row.target_attraction_id, RecognitionRecord.home_attraction_name: row.target_attraction_name},
        synchronize_session=False,
    )
    deduction_count = db.query(DeductionRecord).filter(
        DeductionRecord.employee_id == employee.id,
        DeductionRecord.deduction_month == current_month,
    ).update(
        {
            DeductionRecord.attraction_id_snapshot: row.target_attraction_id,
            DeductionRecord.employee_group_id_snapshot: target_group.id,
        },
        synchronize_session=False,
    )
    sick_leave_count = db.query(SickLeaveRecord).filter(
        SickLeaveRecord.employee_id == employee.id,
        SickLeaveRecord.attendance_month == current_month,
    ).update({SickLeaveRecord.attraction_id_snapshot: row.target_attraction_id}, synchronize_session=False)
    recalculate_attendance(db, employee, current_month)
    migrated_counts = {
        "recognitions": recognition_count,
        "deductions": deduction_count,
        "sick_leaves": sick_leave_count,
        "pending_reviews": pending_reviews,
        "pending_follow_ups": pending_follow_ups,
    }
    row.target_group_id = target_group.id
    row.target_leader_id = target_leader.id if target_leader else None
    row.status = "completed"
    row.reviewed_by = reviewer.id
    row.reviewed_by_name = reviewer.name
    row.reviewed_at = datetime.now()
    row.completed_at = datetime.now()
    row.migrated_record_counts_json = json.dumps(migrated_counts, ensure_ascii=False)
    write_audit(
        db,
        reviewer.employee,
        "确认跨景点圈调动并同步迁移数据",
        "circle_transfer",
        row.id,
        before={"employee_no": employee.employee_no, "attraction": row.source_attraction_name, "group_id": row.source_group_id},
        after={"attraction": row.target_attraction_name, "group_id": target_group.id, "group": target_group.name, "reviewer": target_leader.name if target_leader else "", "migrated": migrated_counts},
        reason=row.reason,
        ip_address=client_ip(request),
    )
    invalidate_data_caches()
    return migrated_counts


@router.post("/hr/circle-transfers/{transfer_id}/review")
def review_circle_transfer(transfer_id: int, payload: dict, request: Request, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    row = db.get(CircleTransferRequest, transfer_id)
    if not row or row.status != "pending":
        raise HTTPException(404, "待确认调动申请不存在")
    allowed_attractions = scoped_hr_attraction_ids(db, user)
    if allowed_attractions is not None and row.target_attraction_id not in allowed_attractions:
        raise HTTPException(403, "只有目标景点圈HR可以确认该调动")
    action = str(payload.get("action") or "").strip().lower()
    note = str(payload.get("review_note") or "").strip()
    if action == "reject":
        if not note:
            raise HTTPException(400, "拒绝原因必填")
        row.status = "rejected"
        row.reviewed_by = user.id
        row.reviewed_by_name = user.name
        row.reviewed_at = datetime.now()
        row.review_note = note
        write_audit(db, user.employee, "拒绝跨景点圈调动", "circle_transfer", row.id, after=circle_transfer_payload(db, row), reason=note, ip_address=client_ip(request))
        db.commit()
        return {"ok": True, "transfer": circle_transfer_payload(db, row)}
    if action != "accept":
        raise HTTPException(400, "审批动作无效")
    target_group = db.get(WorkGroup, int(payload.get("target_group_id") or 0))
    if not target_group or target_group.status == "closed" or target_group.attraction_id != row.target_attraction_id:
        raise HTTPException(400, "请选择目标景点圈内的小组")
    row.review_note = note or None
    migrated_counts = complete_circle_transfer(db, row, target_group, user, request)
    db.commit()
    warnings = [] if row.target_leader_id else [f"{target_group.name}还没有负责人，{row.employee_name}的签卡暂时无人复核，请到小组管理设置"]
    return {"ok": True, "transfer": circle_transfer_payload(db, row), "migrated_record_counts": migrated_counts, "warnings": warnings}


@router.post("/hr/circle-transfers/{transfer_id}/cancel")
def cancel_circle_transfer(transfer_id: int, request: Request, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    row = db.get(CircleTransferRequest, transfer_id)
    if not row or row.status != "pending":
        raise HTTPException(404, "待确认调动申请不存在")
    allowed_attractions = scoped_hr_attraction_ids(db, user)
    if allowed_attractions is not None and row.source_attraction_id not in allowed_attractions:
        raise HTTPException(403, "只有原景点圈HR可以撤回该申请")
    row.status = "cancelled"
    write_audit(db, user.employee, "撤回跨景点圈调动", "circle_transfer", row.id, after=circle_transfer_payload(db, row), reason=row.reason, ip_address=client_ip(request))
    db.commit()
    return {"ok": True, "transfer": circle_transfer_payload(db, row)}


@router.get("/hr/groups")
def hr_groups(db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    """小组管理: per circle, its groups with 主管/代理主管 and who may fill them."""
    today_value = date.today().isoformat()
    circles = scoped_circles(db, user)
    circle_ids = [circle.id for circle in circles]
    groups_by_circle = open_groups_by_circle(db, circle_ids)
    group_ids = [group.id for rows in groups_by_circle.values() for group in rows]
    group_display = group_display_metadata_bulk(db, group_ids, today_value)
    memberships = active_group_memberships_bulk(db, group_ids, today_value)
    gsm_names = circle_gsm_names(db, circle_ids, today_value)
    staff = (
        db.query(Employee)
        .filter(Employee.is_active.is_(True), Employee.attraction_id.in_(circle_ids))
        .order_by(Employee.name, Employee.employee_no)
        .all()
        if circle_ids
        else []
    )
    staff_ids = [employee.id for employee in staff]
    base_roles = base_roles_at(db, staff_ids, today_value)
    duties = duties_at_bulk(db, staff_ids, today_value)
    labels = identity_labels(db, staff_ids, today_value)
    by_id = {employee.id: employee for employee in staff}
    led = {}
    for assignment in active_leader_assignments(db, staff_ids):
        led[(assignment.leader_employee_id, assignment.leader_type)] = assignment.group
    member_group = {row.employee_id: group_id for group_id, rows in memberships.items() for row in rows}

    def candidate(employee: Employee, leader_type: str) -> dict:
        group = led.get((employee.id, leader_type))
        return {
            "id": employee.id,
            "name": employee.name,
            "employee_no": employee.employee_no,
            "role_label": labels.get(employee.id, ""),
            "group_id": group.id if group else None,
            "group_name": group.name if group else "",
        }

    result = []
    for circle in circles:
        circle_staff = [employee for employee in staff if employee.attraction_id == circle.id]
        group_rows = []
        for group in groups_by_circle.get(circle.id, []):
            display = group_display.get(group.id, {})
            members = [by_id.get(row.employee_id) or row.employee for row in memberships.get(group.id, [])]
            formal = leader_ref(display, "formal")
            if formal:
                formal["role_label"] = labels.get(formal["id"]) or identity_labels(db, [formal["id"]], today_value).get(formal["id"], "")
            acting = leader_ref(display, "acting")
            if acting:
                acting["role_label"] = labels.get(acting["id"]) or identity_labels(db, [acting["id"]], today_value).get(acting["id"], "")
            group_rows.append(
                {
                    "id": group.id,
                    "code": group.code,
                    "name": group.name,
                    "status": group.status,
                    "revision": group.revision,
                    "formal_leader": formal,
                    "acting_leader": acting,
                    "member_count": len(members),
                    "members": [
                        {"id": member.id, "employee_no": member.employee_no, "name": member.name, "role_label": labels.get(member.id) or ""}
                        for member in sorted(members, key=lambda item: (item.name, item.employee_no))
                    ],
                }
            )
        supervisors = [candidate(employee, "formal") for employee in circle_staff if (base_roles.get(employee.id) and base_roles[employee.id].code == "SUPERVISOR")]
        acting_candidates = [
            candidate(employee, "acting") for employee in circle_staff
            if any(duty.code == "TA_SUPERVISOR" for duty in duties.get(employee.id, []))
        ]
        unassigned_count = sum(
            1 for employee in circle_staff
            if base_roles.get(employee.id) and base_roles[employee.id].code in MEMBER_BASE_CODES and employee.id not in member_group
        )
        result.append(
            {
                "id": circle.id,
                "name": circle.name,
                **gsm_names.get(circle.id, {"gsm_names": [], "ta_gsm_names": []}),
                "next_code": next_group_code(db, circle.id),
                "groups": group_rows,
                "supervisors": supervisors,
                "acting_candidates": acting_candidates,
                "unassigned_count": unassigned_count,
            }
        )
    return {"circles": result}


def optional_employee_id(value) -> int | None:
    try:
        return int(value) if value not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        raise HTTPException(400, "负责人编号无效")


def apply_group_leaders(db: Session, group: WorkGroup, supervisor_id: int | None, acting_id: int | None, operator: Employee, reason: str, ip_address: str | None) -> dict:
    """Set the group's 主管 and 代理主管 from today; members never move."""
    today_value = date.today().isoformat()
    members = active_group_memberships(db, group.id)
    member_ids = {member.employee_id for member in members}
    plan = {"formal": supervisor_id, "acting": acting_id}
    changes: dict[str, tuple[GroupLeaderAssignment | None, Employee | None]] = {}
    for leader_type, employee_id in plan.items():
        label = LEADER_TYPE_LABELS[leader_type]
        current = group_leader_of_type(db, group.id, leader_type)
        if (current.leader_employee_id if current else None) == employee_id:
            continue
        person = None
        if employee_id:
            person = db.get(Employee, employee_id)
            if not person or not person.is_active:
                raise HTTPException(400, f"{label}必须是在职员工")
            if person.attraction_id != group.attraction_id:
                raise HTTPException(400, f"{label}必须与小组属于同一景点圈")
            if leader_type == "formal":
                if not group_supervisor_eligible(db, person.id):
                    raise HTTPException(400, "主管必须是本职主管")
                if person.id in member_ids:
                    raise HTTPException(400, f"{person.name}是本组组员，不能担任本组主管")
            elif not group_acting_eligible(db, person.id):
                raise HTTPException(400, "代理主管必须是当前有代理TA主管职务的员工")
            other = next(
                (row for row in active_leader_assignments(db, [person.id]) if row.leader_type == leader_type and row.group_id != group.id),
                None,
            )
            if other:
                raise HTTPException(400, f"{person.name}已是{other.group.name}的{label}，一人只能负责一个小组")
        changes[leader_type] = (current, person)
    if not changes:
        raise HTTPException(400, "负责人没有变化")
    before = {}
    after = {}
    for leader_type, (current, person) in changes.items():
        label = LEADER_TYPE_LABELS[leader_type]
        before[label] = current.leader.name if current else "无"
        after[label] = person.name if person else "无"
        if current:
            current.status = "ended"
            current.ends_on = max(current.starts_on, today_value)
        if person:
            transfer = GroupTransfer(
                group_id=group.id,
                old_leader_id=current.leader_employee_id if current else None,
                new_leader_id=person.id,
                attraction_id=group.attraction_id,
                effective_date=today_value,
                member_count=len(members),
                pending_review_count=0,
                reason=reason,
                operator_id=operator.id,
            )
            db.add(transfer)
            db.flush()
            for member in members:
                db.add(GroupTransferMember(transfer_id=transfer.id, employee_id=member.employee_id, employee_no=member.employee.employee_no, employee_name=member.employee.name))
            db.add(GroupLeaderAssignment(group_id=group.id, leader_employee_id=person.id, starts_on=today_value, status="active", leader_type=leader_type, transfer_id=transfer.id))
    db.flush()
    sync_pending_reviewers(db, group.id)
    has_leader = bool(group_leader_of_type(db, group.id, "formal") or group_leader_of_type(db, group.id, "acting"))
    if has_leader:
        group.status = "active"
        db.query(SystemAlert).filter(SystemAlert.group_id == group.id, SystemAlert.status == "open").update(
            {SystemAlert.status: "handled", SystemAlert.handled_by: operator.id, SystemAlert.handled_at: datetime.now()},
            synchronize_session=False,
        )
    group.revision += 1
    write_audit(db, operator, "设置小组负责人", "work_group", group.id, before=before, after={**after, "小组": group.name, "组员人数": len(members)}, reason=reason, ip_address=ip_address)
    return after


def group_leader_warnings(db: Session, group: WorkGroup) -> list[str]:
    """Hints for a group allowed to run without a full set of leaders."""
    member_ids = {member.employee_id for member in active_group_memberships(db, group.id)}
    if not member_ids:
        return []
    formal = group_leader_of_type(db, group.id, "formal")
    acting = group_leader_of_type(db, group.id, "acting")
    if not formal and not acting:
        return [f"{group.name}没有主管和代理主管，组员的签卡暂时无人复核"]
    if acting and not formal and acting.leader_employee_id in member_ids:
        return [f"{group.name}没有主管，代理主管{acting.leader.name}本人的签卡暂时无人复核"]
    return []


@router.post("/hr/groups")
def create_group(payload: dict, request: Request, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    """New group with the circle's next letter; 主管/代理主管 are optional."""
    circle = db.get(Attraction, int(payload.get("attraction_id") or 0))
    if not circle or not circle.active or not circle.employee_circle:
        raise HTTPException(400, "请选择有效的景点圈")
    ensure_scoped_hr_attraction(db, user, circle.id)
    reason = str(payload.get("reason") or "").strip() or "HR新建小组"
    code = next_group_code(db, circle.id)
    group = WorkGroup(name=group_display_name(circle.name, code), code=code, attraction_id=circle.id, status="active")
    db.add(group)
    db.flush()
    write_audit(db, user.employee, "新建小组", "work_group", group.id, after={"name": group.name, "code": code}, reason=reason, ip_address=client_ip(request))
    supervisor_id = optional_employee_id(payload.get("supervisor_id"))
    acting_id = optional_employee_id(payload.get("acting_id"))
    if supervisor_id or acting_id:
        apply_group_leaders(db, group, supervisor_id, acting_id, user.employee, reason, client_ip(request))
    db.commit()
    invalidate_data_caches()
    return {"ok": True, "id": group.id, "name": group.name, "code": code}


@router.post("/hr/groups/{group_id}/leaders")
def set_group_leaders(group_id: int, payload: dict, request: Request, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    group = db.get(WorkGroup, group_id)
    if not group or group.status == "closed":
        raise HTTPException(404, "小组不存在或已关闭")
    ensure_scoped_hr_attraction(db, user, group.attraction_id)
    if int(payload.get("revision") or 0) != group.revision:
        raise HTTPException(409, "小组已被其他操作修改，请刷新后重试")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(400, "调整原因必填")
    after = apply_group_leaders(
        db,
        group,
        optional_employee_id(payload.get("supervisor_id")),
        optional_employee_id(payload.get("acting_id")),
        user.employee,
        reason,
        client_ip(request),
    )
    warnings = group_leader_warnings(db, group)
    db.commit()
    invalidate_data_caches()
    return {"ok": True, "leaders": after, "warnings": warnings}


@router.post("/hr/groups/{group_id}/rename")
def rename_group(group_id: int, payload: dict, request: Request, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    """HR may rename a group; the letter code and every record stay with the group."""
    group = db.get(WorkGroup, group_id)
    if not group or group.status == "closed":
        raise HTTPException(404, "小组不存在或已关闭")
    ensure_scoped_hr_attraction(db, user, group.attraction_id)
    if int(payload.get("revision") or 0) != group.revision:
        raise HTTPException(409, "小组已被其他操作修改，请刷新后重试")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "组名不能为空")
    if len(name) > 30:
        raise HTTPException(400, "组名最多30个字")
    if name == group.name:
        raise HTTPException(400, "组名没有变化")
    if db.query(WorkGroup.id).filter(WorkGroup.name == name, WorkGroup.status != "closed", WorkGroup.id != group.id).first():
        raise HTTPException(400, f"已有名为“{name}”的小组")
    old_name = group.name
    group.name = name
    group.revision += 1
    write_audit(db, user.employee, "修改小组名称", "work_group", group.id, before={"name": old_name}, after={"name": name}, reason=str(payload.get("reason") or "").strip() or "HR修改组名", ip_address=client_ip(request))
    db.commit()
    invalidate_data_caches()
    return {"ok": True, "name": name}


@router.post("/hr/groups/{group_id}/close")
def close_empty_group(group_id: int, payload: dict, request: Request, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    """Close a group with no members; its leader assignments end, history stays."""
    group = db.get(WorkGroup, group_id)
    if not group or group.status == "closed":
        raise HTTPException(404, "小组不存在或已关闭")
    ensure_scoped_hr_attraction(db, user, group.attraction_id)
    if int(payload.get("revision") or 0) != group.revision:
        raise HTTPException(409, "小组已被其他操作修改，请刷新后重试")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(400, "关闭原因必填")
    if active_group_memberships(db, group.id):
        raise HTTPException(400, "该组仍有组员，只能关闭没有组员的小组")
    today_value = date.today().isoformat()
    ended_leaders = []
    for assignment in (
        db.query(GroupLeaderAssignment)
        .filter(GroupLeaderAssignment.group_id == group.id, GroupLeaderAssignment.status == "active")
        .all()
    ):
        assignment.status = "ended"
        assignment.ends_on = max(assignment.starts_on, today_value)
        ended_leaders.append(assignment.leader.name)
    # The letter is freed for the next new group; history keeps a marked name.
    old_name = group.name
    group.status = "closed"
    group.code = None
    group.name = f"{old_name}（已关闭）"
    group.revision += 1
    db.query(SystemAlert).filter(SystemAlert.group_id == group.id, SystemAlert.status == "open").update(
        {SystemAlert.status: "handled", SystemAlert.handled_by: user.id, SystemAlert.handled_at: datetime.now()},
        synchronize_session=False,
    )
    write_audit(db, user.employee, "关闭空小组", "work_group", group.id, before={"name": old_name, "leaders": ended_leaders, "status": "active", "member_count": 0}, after={"name": group.name, "status": "closed"}, reason=reason, ip_address=client_ip(request))
    db.commit()
    invalidate_data_caches()
    return {"ok": True}
