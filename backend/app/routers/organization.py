"""Work group, leader, circle transfer and org tree endpoints."""
from __future__ import annotations

import json
from datetime import date, datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.v2_auth import V2User, require_permissions
from app.v2_database import get_db
from app.v2_models import Attraction, CircleTransferRequest, DeductionFollowUp, DeductionRecord, Employee, GroupLeaderAssignment, GroupMembership, GroupTransfer, GroupTransferMember, RecognitionRecord, Role, SickLeaveRecord, SystemAlert, WorkGroup
from app.v2_services import FRONTLINE_CODES, LEADER_CODES, active_group_leader, active_group_leaders_bulk, active_group_memberships, active_group_memberships_bulk, current_group_for_employee, current_leader_for_employee, groups_led_by, groups_led_by_bulk, gsm_candidates_for_attractions_bulk, recalculate_attendance, role_at, roles_at, write_audit
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


def gsm_candidates_for_attraction(db: Session, attraction_id: int, on_date: str | None = None) -> list[tuple[Employee, Role]]:
    return gsm_candidates_for_attractions_bulk(db, [attraction_id], on_date).get(attraction_id, [])


@router.get("/hr/organization")
def hr_organization(db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    today_value = date.today().isoformat()
    allowed_attractions = scoped_hr_attraction_ids(db, user)
    employee_query = db.query(Employee)
    attraction_query = db.query(Attraction)
    if allowed_attractions is not None:
        employee_query = employee_query.filter(Employee.attraction_id.in_(allowed_attractions))
        attraction_query = attraction_query.filter(Attraction.id.in_(allowed_attractions))
    employees = employee_query.order_by(Employee.name, Employee.employee_no).all()
    payloads = employee_payloads(db, employees, today_value)
    roles = roles_at(db, [employee.id for employee in employees], today_value)
    rows = []
    represented: set[int] = set()

    for attraction in attraction_query.order_by(Attraction.name).all():
        attraction_employees = [employee for employee in employees if employee.attraction_id == attraction.id and employee.is_active]
        if not attraction_employees:
            continue
        attraction_node = f"hr-attraction-{attraction.id}"
        rows.append({"node_id": attraction_node, "parent_id": "", "level": 0, "node_type": "attraction", "name": attraction.name})
        gsm_candidates = gsm_candidates_for_attraction(db, attraction.id, today_value)
        gsm_managers = [(employee, role) for employee, role in gsm_candidates if role.code == "GSM"]
        ta_gsm_managers = [(employee, role) for employee, role in gsm_candidates if role.code == "TA_GSM"]
        management_parent = attraction_node
        if len(gsm_managers) == 1:
            primary_gsm, _primary_gsm_role = gsm_managers[0]
            management_parent = f"hr-gsm-{attraction.id}-{primary_gsm.id}"
            rows.append({"node_id": management_parent, "parent_id": attraction_node, "level": 1, "node_type": "employee", "hierarchy_role": "gsm", "employee": payloads[primary_gsm.id]})
            represented.add(primary_gsm.id)
        elif len(gsm_managers) > 1:
            # Parallel GSMs jointly own every supervisor group in the circle.
            # A single team node prevents duplicating supervisors under each GSM.
            for gsm, _gsm_role in gsm_managers:
                gsm_node = f"hr-gsm-{attraction.id}-{gsm.id}"
                rows.append({"node_id": gsm_node, "parent_id": attraction_node, "level": 1, "node_type": "employee", "hierarchy_role": "gsm", "employee": payloads[gsm.id]})
                represented.add(gsm.id)
            management_parent = f"hr-gsm-team-{attraction.id}"
            manager_names = "、".join(gsm.name for gsm, _gsm_role in gsm_managers)
            rows.append({"node_id": management_parent, "parent_id": attraction_node, "level": 1, "node_type": "gsm_team", "name": f"主管组（由GSM共同承接：{manager_names}）"})
        else:
            management_parent = f"hr-gsm-team-{attraction.id}"
            rows.append({"node_id": management_parent, "parent_id": attraction_node, "level": 1, "node_type": "gsm_team", "name": "主管组（未配置GSM）"})

        leaders = [employee for employee in attraction_employees if roles[employee.id] and roles[employee.id].code in LEADER_CODES]
        assigned_frontline: set[int] = set()
        for leader in sorted(leaders, key=lambda employee: (employee.name, employee.employee_no)):
            leader_node = f"hr-leader-{leader.id}"
            rows.append({"node_id": leader_node, "parent_id": management_parent, "level": 2, "node_type": "employee", "hierarchy_role": "supervisor", "employee": payloads[leader.id]})
            represented.add(leader.id)
            leader_members = [
                member for member in attraction_employees
                if payloads[member.id]["leader_id"] == leader.id
                and roles.get(member.id)
                and roles[member.id].code in FRONTLINE_CODES
            ]
            for member in sorted(leader_members, key=lambda employee: (employee.name, employee.employee_no)):
                rows.append({"node_id": f"hr-employee-{member.id}", "parent_id": leader_node, "level": 3, "node_type": "employee", "hierarchy_role": "frontline", "employee": payloads[member.id]})
                assigned_frontline.add(member.id)
                represented.add(member.id)

        unassigned = [
            employee for employee in attraction_employees
            if roles[employee.id] and roles[employee.id].code in FRONTLINE_CODES and employee.id not in assigned_frontline
        ]
        if unassigned:
            placeholder = f"hr-unassigned-{attraction.id}"
            rows.append({"node_id": placeholder, "parent_id": management_parent, "level": 2, "node_type": "placeholder", "name": "未分配主管", "member_count": len(unassigned)})
            for employee in sorted(unassigned, key=lambda item: (item.name, item.employee_no)):
                rows.append({"node_id": f"hr-employee-{employee.id}", "parent_id": placeholder, "level": 3, "node_type": "employee", "hierarchy_role": "frontline", "employee": payloads[employee.id]})
                represented.add(employee.id)

        # TA GSM is a peer in the GSM layer and never owns a supervisor branch.
        for gsm, _gsm_role in ta_gsm_managers:
            gsm_node = f"hr-gsm-{attraction.id}-{gsm.id}"
            rows.append({"node_id": gsm_node, "parent_id": attraction_node, "level": 1, "node_type": "employee", "hierarchy_role": "gsm", "employee": payloads[gsm.id]})
            represented.add(gsm.id)

    other_employees = [employee for employee in employees if employee.id not in represented]
    if other_employees:
        other_node = "hr-other-employees"
        rows.append({"node_id": other_node, "parent_id": "", "level": 0, "node_type": "other", "name": "其他管理人员 / 未归属员工"})
        for employee in other_employees:
            rows.append({"node_id": f"hr-other-{employee.id}", "parent_id": other_node, "level": 1, "node_type": "employee", "hierarchy_role": "other", "employee": payloads[employee.id]})
    return {"rows": rows}


@router.post("/hr/employees/batch-leaders")
def batch_assign_unclassified_leaders(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("HR_MANAGE")),
):
    """Assign multiple currently unclassified CM/TR employees in one atomic transaction."""
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise HTTPException(400, "请至少选择一名未分类组员的组长")
    if len(raw_items) > 200:
        raise HTTPException(400, "单次最多保存200名组员")
    reason = str(payload.get("reason") or "HR未分类组员批量分组").strip()[:200]
    today_value = date.today().isoformat()
    seen_employee_ids: set[int] = set()
    plans: list[dict] = []

    # Validate the full batch before creating or ending any relationship.
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            raise HTTPException(400, f"第{index}项格式无效")
        try:
            employee_id = int(item.get("employee_id") or 0)
            leader_id = int(item.get("leader_id") or 0)
            requested_group_id = int(item.get("group_id") or 0) or None
        except (TypeError, ValueError):
            raise HTTPException(400, f"第{index}项员工、组长或工作组编号无效")
        if not employee_id or not leader_id:
            raise HTTPException(400, f"第{index}项必须选择员工和组长")
        if employee_id in seen_employee_ids:
            raise HTTPException(400, f"第{index}项员工重复")
        seen_employee_ids.add(employee_id)

        employee = db.get(Employee, employee_id)
        employee_role = role_at(db, employee_id) if employee else None
        if not employee:
            raise HTTPException(404, f"第{index}项员工不存在")
        ensure_scoped_hr_employee(db, user, employee)
        if not employee.is_active or not employee_role or employee_role.code not in FRONTLINE_CODES:
            raise HTTPException(400, f"{employee.name}不是在职CM/TR，不能批量分组")
        if not employee.attraction_id:
            raise HTTPException(400, f"{employee.name}未配置景点圈")
        ensure_month_open(db, date.today().strftime("%Y-%m"), employee.attraction_id, "批量调整组长")

        current_membership = (
            db.query(GroupMembership)
            .filter(
                GroupMembership.employee_id == employee.id,
                GroupMembership.status == "active",
                GroupMembership.starts_on <= today_value,
                or_(GroupMembership.ends_on.is_(None), GroupMembership.ends_on >= today_value),
            )
            .order_by(GroupMembership.starts_on.desc(), GroupMembership.id.desc())
            .first()
        )
        current_group = db.get(WorkGroup, current_membership.group_id) if current_membership else None
        current_assignment = active_group_leader(db, current_group.id) if current_group else None
        if current_assignment:
            raise HTTPException(409, f"{employee.name}已分配组长，请刷新页面后重试")

        leader = db.get(Employee, leader_id)
        leader_role = role_at(db, leader_id) if leader else None
        if not leader or not leader.is_active or not leader_role or leader_role.code not in LEADER_CODES:
            raise HTTPException(400, f"{employee.name}选择的组长不是在职TA主管或主管")
        if leader.attraction_id != employee.attraction_id:
            raise HTTPException(400, f"{employee.name}与所选组长不属于同一景点圈")

        target_group = db.get(WorkGroup, requested_group_id) if requested_group_id else None
        if target_group:
            target_assignment = active_group_leader(db, target_group.id)
            if target_group.status == "closed" or target_group.attraction_id != employee.attraction_id:
                raise HTTPException(400, f"{employee.name}选择的工作组无效")
            if not target_assignment or target_assignment.leader_employee_id != leader.id:
                raise HTTPException(409, f"{employee.name}选择的工作组组长已经变化，请刷新后重试")
        else:
            existing_groups = [group for group in groups_led_by(db, leader.id) if group.attraction_id == employee.attraction_id]
            if len(existing_groups) > 1:
                raise HTTPException(409, f"{leader.name}有多个工作组，请刷新后选择具体工作组")
            target_group = existing_groups[0] if existing_groups else None

        plans.append(
            {
                "employee": employee,
                "leader": leader,
                "current_membership": current_membership,
                "current_group": current_group,
                "target_group": target_group,
            }
        )

    created_groups: dict[int, WorkGroup] = {}
    changes = []
    try:
        for plan in plans:
            employee = plan["employee"]
            leader = plan["leader"]
            target_group = plan["target_group"]
            if not target_group:
                target_group = created_groups.get(leader.id)
                if not target_group:
                    target_group = WorkGroup(
                        name=f"{leader.name}工作组",
                        attraction_id=employee.attraction_id,
                        status="active",
                    )
                    db.add(target_group)
                    db.flush()
                    db.add(
                        GroupLeaderAssignment(
                            group_id=target_group.id,
                            leader_employee_id=leader.id,
                            starts_on=today_value,
                            status="active",
                        )
                    )
                    created_groups[leader.id] = target_group

            current_membership = plan["current_membership"]
            if current_membership:
                current_membership.status = "ended"
                current_membership.ends_on = today_value
            db.add(
                GroupMembership(
                    group_id=target_group.id,
                    employee_id=employee.id,
                    starts_on=today_value,
                    status="active",
                    reason=reason,
                )
            )
            db.query(RecognitionRecord).filter(
                RecognitionRecord.employee_id == employee.id,
                RecognitionRecord.status == "pending",
            ).update({RecognitionRecord.assigned_reviewer_id: leader.id}, synchronize_session=False)
            change = {
                "employee_id": employee.id,
                "employee_no": employee.employee_no,
                "employee_name": employee.name,
                "group_id": target_group.id,
                "group_name": target_group.name,
                "leader_id": leader.id,
                "leader_name": leader.name,
            }
            changes.append(change)
            write_audit(
                db,
                user.employee,
                "批量调整未分类组员组长",
                "employee_group",
                employee.id,
                before={
                    "group_id": plan["current_group"].id if plan["current_group"] else None,
                    "leader_id": None,
                    "leader_name": "未分配",
                },
                after={"group_id": target_group.id, "leader_id": leader.id, "leader_name": leader.name},
                reason=reason,
                ip_address=client_ip(request),
            )
        write_audit(
            db,
            user.employee,
            "批量保存未分类组员",
            "employee_group_batch",
            None,
            after={"count": len(changes), "changes": changes},
            reason=reason,
            ip_address=client_ip(request),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    invalidate_data_caches()
    return {"ok": True, "updated": len(changes), "changes": changes}


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
    group_display = group_display_metadata_bulk(
        db,
        [group.id for group in (source_group, target_group) if group],
    )
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
        "source_group_name": group_display.get(source_group.id, {}).get("name", source_group.name) if source_group else "未分组",
        "source_leader_name": source_leader.name if source_leader else "未分配",
        "target_group_id": row.target_group_id,
        "target_group_name": group_display.get(target_group.id, {}).get("name", target_group.name) if target_group else "",
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
    employee_role = role_at(db, employee.id) if employee else None
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


def complete_circle_transfer(db: Session, row: CircleTransferRequest, target_leader: Employee, target_group: WorkGroup, reviewer: V2User, request: Request) -> dict:
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
    pending_reviews = db.query(RecognitionRecord).filter(
        RecognitionRecord.employee_id == employee.id,
        RecognitionRecord.status == "pending",
    ).update({RecognitionRecord.assigned_reviewer_id: target_leader.id}, synchronize_session=False)
    pending_follow_ups = db.query(DeductionFollowUp).filter(
        DeductionFollowUp.employee_id == employee.id,
        DeductionFollowUp.status == "pending",
    ).update(
        {DeductionFollowUp.supervisor_id: target_leader.id, DeductionFollowUp.supervisor_name: target_leader.name},
        synchronize_session=False,
    )
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
    row.target_leader_id = target_leader.id
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
        after={"attraction": row.target_attraction_name, "group_id": target_group.id, "leader": target_leader.name, "migrated": migrated_counts},
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
    target_leader = db.get(Employee, int(payload.get("target_leader_id") or 0))
    target_role = role_at(db, target_leader.id) if target_leader else None
    if not target_leader or not target_leader.is_active or target_leader.attraction_id != row.target_attraction_id or not target_role or target_role.code not in LEADER_CODES:
        raise HTTPException(400, "请选择目标景点圈内在职TA主管或主管")
    target_group_id = int(payload.get("target_group_id") or 0)
    target_group = db.get(WorkGroup, target_group_id) if target_group_id else None
    if target_group:
        if target_group.status == "closed" or target_group.attraction_id != row.target_attraction_id:
            raise HTTPException(400, "目标工作组无效")
        assignment = active_group_leader(db, target_group.id)
        if not assignment or assignment.leader_employee_id != target_leader.id:
            raise HTTPException(400, "目标工作组与所选LEAD不匹配")
    else:
        target_group = WorkGroup(name=f"{target_leader.name}工作组", attraction_id=row.target_attraction_id, status="active")
        db.add(target_group)
        db.flush()
        db.add(GroupLeaderAssignment(group_id=target_group.id, leader_employee_id=target_leader.id, starts_on=date.today().isoformat(), status="active"))
    row.review_note = note or None
    migrated_counts = complete_circle_transfer(db, row, target_leader, target_group, user, request)
    db.commit()
    return {"ok": True, "transfer": circle_transfer_payload(db, row), "migrated_record_counts": migrated_counts}


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
    allowed_attractions = scoped_hr_attraction_ids(db, user)
    group_query = db.query(WorkGroup).filter(WorkGroup.status != "closed")
    if allowed_attractions is not None:
        group_query = group_query.filter(WorkGroup.attraction_id.in_(allowed_attractions))
    groups = group_query.order_by(WorkGroup.name).all()
    group_ids = [group.id for group in groups]
    group_display = group_display_metadata_bulk(db, group_ids)
    leader_assignments = active_group_leaders_bulk(db, group_ids)
    memberships = active_group_memberships_bulk(db, group_ids)
    referenced_employees = {assignment.leader_employee_id for assignment in leader_assignments.values() if assignment}
    for member_rows in memberships.values():
        referenced_employees.update(row.employee_id for row in member_rows)
    employees = (
        {employee.id: employee for employee in db.query(Employee).filter(Employee.id.in_(referenced_employees)).all()}
        if referenced_employees
        else {}
    )
    attractions = {attraction.id: attraction.name for attraction in db.query(Attraction).all()}
    rows = []
    for group in groups:
        leader_assignment = leader_assignments.get(group.id)
        members = memberships.get(group.id, [])
        leader = employees.get(leader_assignment.leader_employee_id) if leader_assignment else None
        display = group_display.get(group.id, {})
        rows.append(
            {
                "id": group.id,
                "name": display.get("name", group.name),
                "stored_name": group.name,
                "previous_leader_name": display.get("previous_leader_name", ""),
                "previous_leader_until": display.get("previous_leader_until", ""),
                "attraction_id": group.attraction_id,
                "attraction_name": attractions.get(group.attraction_id, ""),
                "status": group.status,
                "revision": group.revision,
                "leader_id": leader_assignment.leader_employee_id if leader_assignment else None,
                "leader_name": leader.name if leader else "待接管",
                "member_count": len(members),
                "members": [
                    {
                        "id": employee.id,
                        "employee_no": employee.employee_no,
                        "name": employee.name,
                    }
                    for employee in (employees.get(row.employee_id) for row in members)
                    if employee
                ],
            }
        )
    return rows


@router.get("/hr/leader-options")
def leader_options(db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    allowed_attractions = scoped_hr_attraction_ids(db, user)
    employee_query = db.query(Employee).filter(Employee.is_active.is_(True))
    if allowed_attractions is not None:
        employee_query = employee_query.filter(Employee.attraction_id.in_(allowed_attractions))
    employees = employee_query.order_by(Employee.name).all()
    roles = roles_at(db, [employee.id for employee in employees])
    leaders = [employee for employee in employees if (roles.get(employee.id) or None) and roles[employee.id].code in LEADER_CODES]
    led_groups = groups_led_by_bulk(db, [leader.id for leader in leaders])
    group_display = group_display_metadata_bulk(db, {group.id for rows in led_groups.values() for group in rows})
    gsm_by_attraction = gsm_candidates_for_attractions_bulk(db, {leader.attraction_id for leader in leaders if leader.attraction_id})
    attraction_names = {attraction.id: attraction.name for attraction in db.query(Attraction).all()}
    result = []
    for employee in leaders:
        role = roles[employee.id]
        groups = [group for group in led_groups.get(employee.id, []) if group.attraction_id == employee.attraction_id]
        if not groups:
            groups = [None]
        candidates = gsm_by_attraction.get(employee.attraction_id) if employee.attraction_id else None
        gsm, gsm_role = candidates[0] if candidates else (None, None)
        for group in groups:
            result.append(
                {
                    "id": employee.id,
                    "name": employee.name,
                    "employee_no": employee.employee_no,
                    "role_name": role.name,
                    "attraction_id": employee.attraction_id,
                    "attraction_name": attraction_names.get(employee.attraction_id, "") if employee.attraction_id else "",
                    "group_id": group.id if group else None,
                    "group_name": group_display.get(group.id, {}).get("name", group.name) if group else "新建工作组",
                    "gsm_id": gsm.id if gsm else None,
                    "gsm_name": gsm.name if gsm else "未配置GSM",
                    "gsm_role_name": gsm_role.name if gsm_role else "",
                }
            )
    return result


@router.post("/hr/groups/{group_id}/transfer")
def transfer_group(group_id: int, payload: dict, request: Request, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    group = db.get(WorkGroup, group_id)
    if not group:
        raise HTTPException(404, "工作组不存在")
    ensure_scoped_hr_attraction(db, user, group.attraction_id)
    if int(payload.get("revision") or 0) != group.revision:
        raise HTTPException(409, "工作组已被其他操作修改，请刷新后重试")
    new_leader = db.get(Employee, int(payload.get("new_leader_id") or 0))
    new_role = role_at(db, new_leader.id) if new_leader else None
    if not new_leader or not new_leader.is_active or not new_role or new_role.code not in LEADER_CODES:
        raise HTTPException(400, "新组长必须是在职TA主管或主管")
    if new_leader.attraction_id != group.attraction_id:
        raise HTTPException(400, "新组长必须与工作组属于同一景点圈")
    effective_date = str(payload.get("effective_date") or date.today().isoformat())
    if effective_date != date.today().isoformat():
        raise HTTPException(400, "当前版本整组移交仅支持当天生效")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(400, "移交原因必填")
    old_assignment = active_group_leader(db, group.id)
    if old_assignment and old_assignment.leader_employee_id == new_leader.id:
        raise HTTPException(400, "新旧组长不能相同")
    members = active_group_memberships(db, group.id)
    pending_count = db.query(RecognitionRecord).filter(
        RecognitionRecord.employee_id.in_([member.employee_id for member in members]) if members else RecognitionRecord.id == -1,
        RecognitionRecord.status == "pending",
    ).count()
    transfer = GroupTransfer(
        group_id=group.id,
        old_leader_id=old_assignment.leader_employee_id if old_assignment else None,
        new_leader_id=new_leader.id,
        attraction_id=group.attraction_id,
        effective_date=effective_date,
        member_count=len(members),
        pending_review_count=pending_count,
        reason=reason,
        operator_id=user.id,
    )
    db.add(transfer)
    db.flush()
    if old_assignment:
        old_assignment.status = "ended"
        old_assignment.ends_on = effective_date
    db.add(GroupLeaderAssignment(group_id=group.id, leader_employee_id=new_leader.id, starts_on=effective_date, status="active", transfer_id=transfer.id))
    for member in members:
        db.add(GroupTransferMember(transfer_id=transfer.id, employee_id=member.employee_id, employee_no=member.employee.employee_no, employee_name=member.employee.name))
    member_ids = [member.employee_id for member in members]
    if member_ids:
        db.query(RecognitionRecord).filter(RecognitionRecord.employee_id.in_(member_ids), RecognitionRecord.status == "pending").update({RecognitionRecord.assigned_reviewer_id: new_leader.id}, synchronize_session=False)
    group.status = "active"
    group.revision += 1
    db.query(SystemAlert).filter(SystemAlert.group_id == group.id, SystemAlert.status == "open").update({SystemAlert.status: "handled", SystemAlert.handled_by: user.id, SystemAlert.handled_at: datetime.now()}, synchronize_session=False)
    write_audit(db, user.employee, "整组移交", "work_group", group.id, before={"leader": old_assignment.leader.name if old_assignment else "待接管"}, after={"leader": new_leader.name, "member_count": len(members)}, reason=reason, ip_address=client_ip(request))
    db.commit()
    invalidate_data_caches()
    return {"ok": True, "transfer_id": transfer.id}
