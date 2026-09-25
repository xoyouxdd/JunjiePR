"""Governance, month-close and action-center endpoints."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.v2_auth import V2User, current_user
from app.v2_database import get_db
from app.v2_models import Attraction, CircleTransferRequest, DeductionFollowUp, DeductionRecord, Employee, EmployeeMonthOrganizationSnapshot, GovernanceCase, GroupMembership, MonthClosure, RecognitionRecord
from app.backup_management import backup_todo_dismissed, claim_manual_backup, health_fingerprint, manual_backup_status, run_manual_backup
from app.score_queries import month_score_employee_ids
from app.v2_services import FRONTLINE_CODES, GSM_CODES, LEADER_CODES, current_group_for_employee, current_leader_for_employee, direct_member_ids, managed_attraction_ids, role_at, write_audit
from app.routers._shared import (
    MATERIAL_COLLABORATOR_CODES,
    MONTH_CLOSE_EFFECTIVE_DATE,
    MONTH_CLOSE_ROLE_CODES,
    backup_health_payload,
    client_ip,
    invalidate_data_caches,
    month_close_checklist,
    month_closure_payload,
    month_closure_scope,
    parse_score_month,
    scoped_hr_attraction_ids,
    visible_system_alerts,
)

router = APIRouter()


def ensure_month_close_scope(db: Session, user: V2User, attraction_id: int | None) -> Attraction | None:
    if user.role.code not in MONTH_CLOSE_ROLE_CODES:
        raise HTTPException(403, "仅景点圈HR或最高管理员可以关闭月结")
    if attraction_id is None:
        if user.role.code != "SYSTEM_ADMIN":
            raise HTTPException(400, "请选择要关闭的景点圈")
        return None
    attraction = db.get(Attraction, attraction_id)
    if not attraction or not attraction.active or not attraction.employee_circle:
        raise HTTPException(400, "请选择有效景点圈")
    if user.role.code != "SYSTEM_ADMIN" and attraction.id not in managed_attraction_ids(db, user.id):
        raise HTTPException(403, "只能关闭自己管理范围内的景点圈")
    return attraction


def governance_case_payload(row: GovernanceCase, *, can_resolve: bool = False) -> dict:
    status_names = {"open": "待处理", "resolved": "已处理", "withdrawn": "已撤回"}
    decision_names = {"uphold": "维持原记录", "correction_required": "需要按受控流程更正", "month_reopened": "已重开月结"}
    return {
        "id": row.id,
        "case_type": row.case_type,
        "record_type": row.record_type or "",
        "record_id": row.record_id or 0,
        "attraction_id": row.attraction_id or 0,
        "score_month": row.score_month or "",
        "reason": row.reason,
        "status": row.status,
        "status_name": status_names.get(row.status, row.status),
        "decision": row.decision or "",
        "decision_name": decision_names.get(row.decision, row.decision or ""),
        "resolution": row.resolution or "",
        "submitted_by_name": row.submitted_by_name,
        "submitted_at": row.submitted_at.strftime("%Y-%m-%d %H:%M:%S"),
        "due_at": row.due_at.strftime("%Y-%m-%d %H:%M:%S"),
        "resolved_by_name": row.resolved_by_name or "",
        "resolved_at": row.resolved_at.strftime("%Y-%m-%d %H:%M:%S") if row.resolved_at else "",
        "can_resolve": can_resolve and row.status == "open",
    }


def governance_scope_allows_case(db: Session, user: V2User, row: GovernanceCase) -> bool:
    if user.role.code in {"SYSTEM_ADMIN", "HR_ADMIN"}:
        return True
    allowed = scoped_hr_attraction_ids(db, user)
    return allowed is not None and row.attraction_id in allowed


def appeal_record_for_employee(db: Session, employee_id: int, record_type: str, record_id: int):
    if record_type == "recognition":
        row = db.get(RecognitionRecord, record_id)
        eligible = row and row.employee_id == employee_id and row.status in {"rejected", "void"}
        if not eligible:
            return None
        return row, row.home_attraction_id, row.recognition_month, {row.operator_employee_id, row.reviewed_by, row.voided_by}
    if record_type == "deduction":
        row = db.get(DeductionRecord, record_id)
        eligible = row and row.employee_id == employee_id and row.status == "active"
        if not eligible:
            return None
        return row, row.attraction_id_snapshot, row.deduction_month, {row.submitter_id, row.voided_by}
    return None


def capture_month_organization_snapshots(db: Session, month: str, attraction_id: int | None) -> int:
    """Freeze organization context once; later transfers cannot rewrite closed reports."""
    employee_ids = month_score_employee_ids(db, month)
    if not employee_ids:
        return 0
    employees = db.query(Employee).filter(Employee.id.in_(employee_ids)).all()
    captured = 0
    for employee in employees:
        if attraction_id is not None and employee.attraction_id != attraction_id:
            continue
        existing = db.query(EmployeeMonthOrganizationSnapshot.id).filter_by(employee_id=employee.id, score_month=month).first()
        if existing:
            continue
        attraction = db.get(Attraction, employee.attraction_id) if employee.attraction_id else None
        group = current_group_for_employee(db, employee.id)
        leader = current_leader_for_employee(db, employee.id)
        db.add(
            EmployeeMonthOrganizationSnapshot(
                employee_id=employee.id,
                score_month=month,
                attraction_id=employee.attraction_id,
                attraction_name=attraction.name if attraction else "未设置景点圈",
                group_id=group.id if group else None,
                group_name=group.name if group else "未分组",
                leader_employee_id=leader.id if leader else None,
                leader_name=leader.name if leader else "未配置主管",
            )
        )
        captured += 1
    return captured


def action_center_item(item_type: str, title: str, count: int, tab: str, severity: str, description: str) -> dict:
    return {
        "type": item_type,
        "title": title,
        "count": int(count),
        "tab": tab,
        "severity": severity,
        "description": description,
    }


def action_center_wait_hours(submitted_at: datetime, now: datetime) -> float:
    return round(max(0, (now - submitted_at).total_seconds()) / 3600, 1)


def action_center_recognition_details(db: Session, rows: list[RecognitionRecord], now: datetime) -> list[dict]:
    reviewer_ids = {row.assigned_reviewer_id for row in rows if row.assigned_reviewer_id}
    reviewers = {
        employee.id: employee
        for employee in db.query(Employee).filter(Employee.id.in_(reviewer_ids)).all()
    } if reviewer_ids else {}
    details = []
    for row in rows:
        reviewer = reviewers.get(row.assigned_reviewer_id)
        reviewer_role = role_at(db, reviewer.id) if reviewer else None
        details.append({
            "id": row.id,
            "employee_name": row.employee_name,
            "employee_no": row.employee_no,
            "attraction_name": row.home_attraction_name or "未设置景点圈",
            "recognition_type": row.recognition_type_name,
            "recognition_date": row.recognition_date,
            "submitted_at": row.submitted_at.strftime("%Y-%m-%d %H:%M:%S"),
            "waiting_hours": action_center_wait_hours(row.submitted_at, now),
            "reviewer_name": reviewer.name if reviewer else "未配置主管",
            "reviewer_no": reviewer.employee_no if reviewer else "",
            "reviewer_role_name": reviewer_role.name if reviewer_role else "",
        })
    return details


@router.get("/action-center")
def action_center(db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    """A read-only queue built from existing workflow records and role scopes."""
    items: list[dict] = []
    role_code = user.role.code
    current_month = date.today().strftime("%Y-%m")
    month_to_close = (date.today().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")

    if role_code in FRONTLINE_CODES:
        open_appeals = db.query(GovernanceCase).filter(
            GovernanceCase.case_type == "appeal",
            GovernanceCase.submitted_by == user.id,
            GovernanceCase.status == "open",
        ).count()
        if open_appeals:
            items.append(action_center_item("appeal", "我的申诉待处理", open_appeals, "governance", "warning", "申诉不会改变原记录，请在申诉页查看处理结果。"))

    if role_code in LEADER_CODES:
        member_ids = direct_member_ids(db, user.id)
        pending_reviews = db.query(RecognitionRecord).filter(
            RecognitionRecord.employee_id.in_(member_ids) if member_ids else RecognitionRecord.id == -1,
            RecognitionRecord.status == "pending",
        ).count()
        if pending_reviews:
            items.append(action_center_item("recognition_review", "待复核签卡", pending_reviews, "review", "warning", "直属组员提交的签卡等待复核。"))
        follow_ups = db.query(DeductionFollowUp).filter(
            DeductionFollowUp.supervisor_id == user.id,
            DeductionFollowUp.status == "pending",
        ).count()
        if follow_ups:
            items.append(action_center_item("deduction_follow_up", "重复违规待跟进", follow_ups, "entries", "warning", "请完成声明、备忘录或警告的管理闭环。"))
    if role_code in MATERIAL_COLLABORATOR_CODES:
        pending_materials = db.query(DeductionRecord).filter(
            DeductionRecord.status.in_({"pending_material", "material_failed"}),
        ).count()
        if pending_materials:
            items.append(action_center_item("deduction_material", "待补充声明材料", pending_materials, "entries", "warning", "TA主管、主管、TA GSM和GSM均可协作补充；材料处理成功后该扣分才会正式生效。"))

    if role_code in GSM_CODES:
        attraction_ids = managed_attraction_ids(db, user.id)
        scoped_employee_ids = {
            employee_id for (employee_id,) in db.query(Employee.id).filter(
                Employee.attraction_id.in_(attraction_ids), Employee.is_active.is_(True)
            ).all()
        } if attraction_ids else set()
        pending_follow_ups = db.query(DeductionFollowUp).filter(
            DeductionFollowUp.employee_id.in_(scoped_employee_ids) if scoped_employee_ids else DeductionFollowUp.id == -1,
            DeductionFollowUp.status == "pending",
        ).count()
        if pending_follow_ups:
            items.append(action_center_item("deduction_follow_up", "范围内重复违规待处理", pending_follow_ups, "entries", "warning", "管理范围内存在尚未形成闭环的重复违规。"))

    if role_code in {"HR_CIRCLE", "HR_ADMIN", "SYSTEM_ADMIN"}:
        governance_cases = [
            row for row in db.query(GovernanceCase).filter(GovernanceCase.status == "open").all()
            if governance_scope_allows_case(db, user, row)
        ]
        if governance_cases:
            overdue = sum(row.due_at < datetime.now() for row in governance_cases)
            items.append(action_center_item("governance_case", "申诉与更正待处理", len(governance_cases), "governance", "critical" if overdue else "warning", "处理人不得是原登记、复核或作废操作人；超时事项应优先处理。"))
        open_alerts = [row for row in visible_system_alerts(db, user) if row.status == "open"]
        if open_alerts:
            items.append(action_center_item("system_alert", "系统告警待处理", len(open_alerts), "hrEmployees", "critical", "员工、工作组或规则存在需要核对的告警。"))
        allowed_attractions = scoped_hr_attraction_ids(db, user)
        employee_query = db.query(Employee).filter(Employee.is_active.is_(True), Employee.attraction_id.is_not(None))
        if allowed_attractions is not None:
            employee_query = employee_query.filter(Employee.attraction_id.in_(allowed_attractions))
        ungrouped = 0
        for employee in employee_query.all():
            role = role_at(db, employee.id)
            if role and role.code in FRONTLINE_CODES and not current_group_for_employee(db, employee.id):
                ungrouped += 1
        if ungrouped:
            items.append(action_center_item("ungrouped_employee", "在职CM/TR待分组", ungrouped, "hrEmployees", "warning", "人员尚未归入主管组，影响直属复核和管理范围。"))
        transfers = db.query(CircleTransferRequest).filter(CircleTransferRequest.status == "pending")
        if allowed_attractions is not None:
            transfers = transfers.filter(CircleTransferRequest.target_attraction_id.in_(allowed_attractions))
        pending_transfers = transfers.count()
        if pending_transfers:
            items.append(action_center_item("circle_transfer", "待确认跨圈调动", pending_transfers, "circleTransfers", "warning", "确认后将按既有规则迁移归属、组关系和当月数据。"))
        if role_code == "HR_CIRCLE":
            attraction_ids = allowed_attractions or set()
            open_circles = sum(1 for attraction_id in attraction_ids if not month_closure_scope(db, month_to_close, attraction_id))
            if open_circles:
                items.append(action_center_item("month_close", "上月待月结景点圈", open_circles, "monthClose", "info", f"{month_to_close} 数据待核对；完成月结检查清单后即可关闭月结。"))
        if role_code == "SYSTEM_ADMIN":
            health = backup_health_payload()
            if not health["ok"] and not backup_todo_dismissed(db, health):
                items.append(action_center_item("backup_health", "备份健康异常", max(1, len(health["issues"])), "operations", "critical", "备份巡检报告存在异常，请立即进入系统运营核对。"))
            closed_count = db.query(MonthClosure).filter(
                MonthClosure.closure_month == current_month,
                MonthClosure.status == "closed",
            ).count()
            if closed_count:
                items.append(action_center_item("month_close", "本月已关闭月结", closed_count, "operations", "info", "最高管理员可在系统运营页汇总查看，并通过原月结流程进行复查。"))
            overdue_reviews = db.query(RecognitionRecord).filter(
                RecognitionRecord.status == "pending",
                RecognitionRecord.submitted_at < datetime.now() - timedelta(hours=48),
            ).count()
            if overdue_reviews:
                items.append(action_center_item("overdue_review", "超过48小时未复核签卡", overdue_reviews, "operations", "warning", "站内待复核已超出运营时限，请按现有复核边界跟进。"))

    order = {"critical": 0, "warning": 1, "info": 2}
    items.sort(key=lambda row: (order.get(row["severity"], 9), row["title"]))
    return {"items": items, "total": sum(row["count"] for row in items), "role": role_code, "month": current_month}


@router.post("/admin/backup-health/dismiss")
def dismiss_backup_todo(request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    if user.role.code != "SYSTEM_ADMIN":
        raise HTTPException(403, "只有最高管理员可以清除备份待办")
    health = backup_health_payload()
    if health["ok"]:
        raise HTTPException(409, "当前没有备份异常待办")
    write_audit(db, user.employee, "清除备份待办", "backup_health", after={"fingerprint": health_fingerprint(health)}, ip_address=client_ip(request))
    db.commit()
    return {"ok": True, "message": "本次备份异常待办已移除；新巡检异常或24小时后会重新提醒"}


@router.get("/admin/backup-health/manual-status")
def get_manual_backup_status(db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    if user.role.code != "SYSTEM_ADMIN":
        raise HTTPException(403, "只有最高管理员可以查看手动备份状态")
    return manual_backup_status(db)


@router.post("/admin/backup-health/retry", status_code=202)
def retry_backup(background_tasks: BackgroundTasks, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    if user.role.code != "SYSTEM_ADMIN":
        raise HTTPException(403, "只有最高管理员可以重新备份")
    if backup_health_payload()["ok"]:
        raise HTTPException(409, "当前备份巡检没有异常")
    token = claim_manual_backup(db, user.employee, client_ip(request))
    if not token:
        raise HTTPException(409, "手动备份正在执行或刚刚完成，请稍后重试")
    background_tasks.add_task(run_manual_backup, user.id, client_ip(request), token)
    return {"ok": True, "message": "手动备份已开始；此操作不会创建或修复每日计划任务"}


@router.get("/action-center/{item_type}/details")
def action_center_details(item_type: str, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    """Expose only the concrete records behind a visible action-center item."""
    now = datetime.now()
    role_code = user.role.code
    if item_type == "backup_health":
        if role_code != "SYSTEM_ADMIN":
            raise HTTPException(403, "只有最高管理员可以查看备份健康明细")
        health = backup_health_payload()
        return {
            "type": item_type,
            "title": "备份健康异常明细",
            "items": [
                {"code": issue.get("code", "unknown"), "message": issue.get("message", "")}
                for issue in health.get("issues", [])
            ],
            "checked_at_utc": health.get("checked_at_utc", ""),
            "manual_backup": manual_backup_status(db),
        }

    if item_type == "ungrouped_employee":
        if role_code not in {"HR_CIRCLE", "HR_ADMIN", "SYSTEM_ADMIN"}:
            raise HTTPException(403, "当前账号不能查看待分组员工明细")
        allowed_attractions = scoped_hr_attraction_ids(db, user)
        query = db.query(Employee).filter(Employee.is_active.is_(True), Employee.attraction_id.is_not(None))
        if allowed_attractions is not None:
            query = query.filter(Employee.attraction_id.in_(allowed_attractions))
        attractions = {row.id: row.name for row in db.query(Attraction).all()}
        candidates = query.order_by(Employee.name, Employee.employee_no).all()
        prior_memberships: dict[int, GroupMembership] = {}
        memberships = db.query(GroupMembership).filter(
            GroupMembership.employee_id.in_([employee.id for employee in candidates])
        ).all() if candidates else []
        for membership in memberships:
            existing = prior_memberships.get(membership.employee_id)
            membership_end = membership.ends_on or membership.starts_on
            existing_end = (existing.ends_on or existing.starts_on) if existing else ""
            if not existing or membership_end > existing_end or (membership_end == existing_end and membership.id > existing.id):
                prior_memberships[membership.employee_id] = membership
        rows = []
        for employee in candidates:
            role = role_at(db, employee.id)
            if not role or role.code not in FRONTLINE_CODES or current_group_for_employee(db, employee.id):
                continue
            previous = prior_memberships.get(employee.id)
            since = (previous.ends_on or previous.starts_on) if previous else employee.hired_on
            try:
                waiting_days = max(0, (date.today() - date.fromisoformat(since)).days) if since else None
            except ValueError:
                waiting_days = None
            rows.append({
                "employee_name": employee.name,
                "employee_no": employee.employee_no,
                "role_name": role.name,
                "attraction_name": attractions.get(employee.attraction_id, "未设置景点圈"),
                "hired_on": employee.hired_on or "",
                "unassigned_since": since or "",
                "waiting_days": waiting_days,
                "waiting_basis": "最后结束组员关系" if previous else "入职日期",
            })
        return {"type": item_type, "title": "在职CM/TR待分组明细", "items": rows}

    if item_type == "recognition_review":
        if role_code not in LEADER_CODES:
            raise HTTPException(403, "当前账号不能查看直属签卡复核明细")
        member_ids = direct_member_ids(db, user.id)
        rows = db.query(RecognitionRecord).filter(
            RecognitionRecord.employee_id.in_(member_ids) if member_ids else RecognitionRecord.id == -1,
            RecognitionRecord.status == "pending",
        ).order_by(RecognitionRecord.submitted_at.asc(), RecognitionRecord.id.asc()).limit(500).all()
        return {"type": item_type, "title": "待复核签卡明细", "items": action_center_recognition_details(db, rows, now)}

    if item_type == "overdue_review":
        if role_code != "SYSTEM_ADMIN":
            raise HTTPException(403, "只有最高管理员可以查看超时签卡复核明细")
        rows = db.query(RecognitionRecord).filter(
            RecognitionRecord.status == "pending",
            RecognitionRecord.submitted_at < now - timedelta(hours=48),
        ).order_by(RecognitionRecord.submitted_at.asc(), RecognitionRecord.id.asc()).limit(500).all()
        return {"type": item_type, "title": "超过48小时未复核签卡明细", "items": action_center_recognition_details(db, rows, now)}

    raise HTTPException(404, "该待办暂无可展开的异常明细")


@router.get("/governance/appealable-records")
def appealable_records(db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    if user.role.code not in FRONTLINE_CODES:
        raise HTTPException(403, "仅CM/TR可提交本人记录申诉")
    records: list[dict] = []
    for row in db.query(RecognitionRecord).filter(
        RecognitionRecord.employee_id == user.id,
        RecognitionRecord.status.in_(("rejected", "void")),
    ).order_by(RecognitionRecord.submitted_at.desc()).limit(100).all():
        records.append({"record_type": "recognition", "record_id": row.id, "label": f"认可 · {row.recognition_date} · {row.recognition_type_name} · {row.status}"})
    for row in db.query(DeductionRecord).filter(
        DeductionRecord.employee_id == user.id,
        DeductionRecord.status == "active",
    ).order_by(DeductionRecord.submitted_at.desc()).limit(100).all():
        records.append({"record_type": "deduction", "record_id": row.id, "label": f"扣分 · {row.occurred_on} · {row.deduction_type_name} · -{Decimal(row.points):.2f}分"})
    return {"items": records}


@router.post("/governance/appeals")
def submit_appeal(payload: dict, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    if user.role.code not in FRONTLINE_CODES:
        raise HTTPException(403, "仅CM/TR可提交本人记录申诉")
    record_type = str(payload.get("record_type") or "").strip()
    try:
        record_id = int(payload.get("record_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "请选择需要申诉的记录") from exc
    reason = str(payload.get("reason") or "").strip()
    if len(reason) < 5 or len(reason) > 500:
        raise HTTPException(400, "申诉说明需为5至500字")
    subject = appeal_record_for_employee(db, user.id, record_type, record_id)
    if not subject:
        raise HTTPException(404, "该记录不存在、当前不可申诉或不属于本人")
    _record, attraction_id, score_month, _conflicts = subject
    existing = db.query(GovernanceCase).filter(
        GovernanceCase.case_type == "appeal",
        GovernanceCase.record_type == record_type,
        GovernanceCase.record_id == record_id,
        GovernanceCase.status == "open",
    ).first()
    if existing:
        raise HTTPException(409, "该记录已有待处理申诉")
    row = GovernanceCase(
        case_type="appeal",
        record_type=record_type,
        record_id=record_id,
        attraction_id=attraction_id,
        score_month=score_month,
        subject_employee_id=user.id,
        submitted_by=user.id,
        submitted_by_name=user.name,
        reason=reason,
        due_at=datetime.now() + timedelta(hours=72),
    )
    db.add(row)
    db.flush()
    write_audit(db, user.employee, "提交记录申诉", "governance_case", row.id, after={"record_type": record_type, "record_id": record_id, "score_month": score_month}, reason=reason, ip_address=client_ip(request))
    db.commit()
    return {"ok": True, "case": governance_case_payload(row)}


@router.get("/governance/cases")
def governance_cases(db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    if user.role.code in FRONTLINE_CODES:
        rows = db.query(GovernanceCase).filter(GovernanceCase.submitted_by == user.id).order_by(GovernanceCase.submitted_at.desc()).limit(100).all()
        return {"items": [governance_case_payload(row) for row in rows], "can_review": False}
    if user.role.code not in {"HR_CIRCLE", "HR_ADMIN", "SYSTEM_ADMIN"}:
        raise HTTPException(403, "当前角色没有治理复核权限")
    rows = [
        row for row in db.query(GovernanceCase).order_by(GovernanceCase.status.asc(), GovernanceCase.due_at.asc(), GovernanceCase.id.desc()).limit(500).all()
        if governance_scope_allows_case(db, user, row)
    ]
    return {"items": [governance_case_payload(row, can_resolve=True) for row in rows], "can_review": True}


@router.post("/governance/cases/{case_id}/resolve")
def resolve_governance_case(case_id: int, payload: dict, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    if user.role.code not in {"HR_CIRCLE", "HR_ADMIN", "SYSTEM_ADMIN"}:
        raise HTTPException(403, "当前角色没有治理复核权限")
    row = db.get(GovernanceCase, case_id)
    if not row or not governance_scope_allows_case(db, user, row):
        raise HTTPException(404, "未找到可处理的治理事项")
    if row.status != "open":
        raise HTTPException(409, "该事项已处理")
    decision = str(payload.get("decision") or "").strip()
    if decision not in {"uphold", "correction_required"}:
        raise HTTPException(400, "请选择维持原记录或需要更正")
    resolution = str(payload.get("resolution") or "").strip()
    if len(resolution) < 5 or len(resolution) > 500:
        raise HTTPException(400, "处理说明需为5至500字")
    conflicts: set[int | None] = {row.submitted_by}
    if row.case_type == "appeal":
        subject = appeal_record_for_employee(db, row.subject_employee_id or -1, row.record_type or "", row.record_id or 0)
        if not subject:
            raise HTTPException(409, "原记录状态已变化，请由最高管理员在审计日志中复核")
        _record, _attraction_id, _score_month, record_conflicts = subject
        conflicts.update(record_conflicts)
    if user.id in conflicts:
        raise HTTPException(409, "处理人不得是申诉提交人、原登记人、原复核人或原作废人")
    row.status = "resolved"
    row.decision = decision
    row.resolution = resolution
    row.resolved_by = user.id
    row.resolved_by_name = user.name
    row.resolved_at = datetime.now()
    write_audit(db, user.employee, "处理治理事项", "governance_case", row.id, before={"status": "open"}, after={"status": row.status, "decision": decision}, reason=resolution, ip_address=client_ip(request))
    db.commit()
    return {"ok": True, "case": governance_case_payload(row)}


@router.get("/month-closes/{month}")
def month_close_status(
    month: str,
    attraction_id: int | None = None,
    db: Session = Depends(get_db),
    user: V2User = Depends(current_user),
):
    month = parse_score_month(month)
    ensure_month_close_scope(db, user, attraction_id)
    return month_closure_payload(db, month, attraction_id, user)


@router.post("/month-closes/{month}/close")
def close_month(
    month: str,
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    user: V2User = Depends(current_user),
):
    month = parse_score_month(month)
    raw_attraction_id = payload.get("attraction_id")
    attraction_id = int(raw_attraction_id) if raw_attraction_id not in (None, "") else None
    attraction = ensure_month_close_scope(db, user, attraction_id)
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(400, "关闭月结原因必填")
    effective = month_closure_scope(db, month, attraction_id)
    if effective:
        effective_attraction = db.get(Attraction, effective.attraction_id) if effective.attraction_id else None
        scope_name = effective_attraction.name if effective_attraction else "全部景点圈"
        raise HTTPException(409, f"{month} · {scope_name}已经关闭月结")
    checklist = month_close_checklist(db, month, attraction_id)
    blocking = [item for item in checklist if item["count"]]
    if blocking:
        raise HTTPException(
            409,
            detail={
                "code": "MONTH_CLOSE_CHECKLIST_INCOMPLETE",
                "message": "月结前仍有待办或待跟进事项，请先处理完成。",
                "items": blocking,
            },
        )
    scope_filter = MonthClosure.attraction_id == attraction_id if attraction_id is not None else MonthClosure.attraction_id.is_(None)
    row = db.query(MonthClosure).filter(MonthClosure.closure_month == month, scope_filter).first()
    before = {
        "status": row.status,
        "reopened_at": row.reopened_at.strftime("%Y-%m-%d %H:%M:%S") if row.reopened_at else "",
        "reopen_reason": row.reopen_reason or "",
    } if row else None
    now = datetime.now()
    values = {
        "status": "closed",
        "closed_by": user.id,
        "closed_by_name": user.name,
        "closed_at": now,
        "close_reason": reason,
        "reopened_by": None,
        "reopened_by_name": None,
        "reopened_at": None,
        "reopen_reason": None,
        "updated_at": now,
    }
    if row:
        claimed = db.execute(
            update(MonthClosure)
            .where(MonthClosure.id == row.id, MonthClosure.status == "open")
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            db.rollback()
            raise HTTPException(409, "该月份和景点圈刚刚已由其他管理员关闭月结")
        db.refresh(row)
    else:
        row = MonthClosure(closure_month=month, attraction_id=attraction_id, **values)
        db.add(row)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(409, "该月份和景点圈刚刚已由其他管理员关闭月结") from exc
    scope_name = attraction.name if attraction else "全部景点圈"
    snapshot_count = capture_month_organization_snapshots(db, month, attraction_id)
    write_audit(
        db,
        user.employee,
        "关闭月结",
        "month_close",
        row.id,
        before=before,
        after={"month": month, "attraction_id": attraction_id, "scope": scope_name, "status": "closed", "organization_snapshots_created": snapshot_count, "checklist": "completed", "rule_effective_date": MONTH_CLOSE_EFFECTIVE_DATE},
        reason=reason,
        ip_address=client_ip(request),
    )
    db.commit()
    invalidate_data_caches()
    return {"ok": True, **month_closure_payload(db, month, attraction_id, user)}


@router.post("/month-closes/{month}/reopen")
def reopen_month(
    month: str,
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    user: V2User = Depends(current_user),
):
    month = parse_score_month(month)
    raw_attraction_id = payload.get("attraction_id")
    attraction_id = int(raw_attraction_id) if raw_attraction_id not in (None, "") else None
    attraction = ensure_month_close_scope(db, user, attraction_id)
    if user.role.code not in MONTH_CLOSE_ROLE_CODES:
        raise HTTPException(403, "仅景点圈HR或最高管理员可以临时开放月结")
    if user.role.code == "HR_CIRCLE" and (attraction_id is None or attraction_id not in scoped_hr_attraction_ids(db, user)):
        raise HTTPException(403, "景点圈HR只能临时开放所属景点圈的月结")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(400, "重新开启原因必填")
    scope_filter = MonthClosure.attraction_id == attraction_id if attraction_id is not None else MonthClosure.attraction_id.is_(None)
    row = db.query(MonthClosure).filter(
        MonthClosure.closure_month == month,
        scope_filter,
        MonthClosure.status == "closed",
    ).first()
    if not row:
        raise HTTPException(409, "该月份和景点圈当前没有可重开的月结")
    before = {
        "month": row.closure_month,
        "attraction_id": row.attraction_id,
        "status": row.status,
        "closed_by_name": row.closed_by_name or "",
        "closed_at": row.closed_at.strftime("%Y-%m-%d %H:%M:%S") if row.closed_at else "",
        "close_reason": row.close_reason or "",
    }
    row.status = "open"
    row.reopened_by = user.id
    row.reopened_by_name = user.name
    row.reopened_at = datetime.now()
    row.reopen_reason = reason
    row.updated_at = datetime.now()
    scope_name = attraction.name if attraction else "全部景点圈"
    correction_case = GovernanceCase(
        case_type="month_correction",
        record_type="month_close",
        record_id=row.id,
        attraction_id=attraction_id,
        score_month=month,
        subject_employee_id=None,
        submitted_by=user.id,
        submitted_by_name=user.name,
        reason=reason,
        status="resolved",
        decision="month_reopened",
        resolution="已按景点圈HR受控权限临时开放月结；后续更正仍通过既有受控写入与审计路径完成。",
        resolved_by=user.id,
        resolved_by_name=user.name,
        resolved_at=datetime.now(),
        due_at=datetime.now(),
    )
    db.add(correction_case)
    db.flush()
    write_audit(
        db,
        user.employee,
        "重新开启月结",
        "month_close",
        row.id,
        before=before,
        after={"month": month, "attraction_id": attraction_id, "scope": scope_name, "status": "open", "correction_case_id": correction_case.id},
        reason=reason,
        ip_address=client_ip(request),
    )
    db.commit()
    invalidate_data_caches()
    return {"ok": True, **month_closure_payload(db, month, attraction_id, user)}
