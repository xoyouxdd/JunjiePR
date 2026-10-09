"""Employee targeting and HR scope checks shared by business operations."""
from __future__ import annotations

from app.role_constants import FRONTLINE_CODES, GSM_CODES, LEADER_CODES, RECOGNIZER_CODES, SCORED_BASE_CODES
from app.services.identity import base_role_at, duty_code_at
from app.services.organization import groups_assigned_to, managed_attraction_ids
from app.v2_auth import V2User
from app.v2_models import Attraction, Employee, Role, SystemAlert, UserAccount, WorkGroup
from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session


CIRCLE_HR_MANAGED_ROLE_CODES = {"CM", "TR", "TA_SUPERVISOR", "SUPERVISOR"}


REGULAR_ACCOUNT_ROLE_CODES = CIRCLE_HR_MANAGED_ROLE_CODES | {"TA_GSM", "GSM", "AM", "OM"}


SCOPED_HR_ROLE_CODE = "HR_CIRCLE"


CIRCLE_HR_SCORE_RULE_ROLE_CODES = CIRCLE_HR_MANAGED_ROLE_CODES & RECOGNIZER_CODES


MATERIAL_COLLABORATOR_CODES = LEADER_CODES | GSM_CODES


EMPLOYEE_TARGET_PERMISSIONS = {
    "recognition": {"EMPLOYEE_ADD"},
    "deduction": {"DEDUCTION_DIRECT", "DEDUCTION_ALL"},
    "attendance": {"SICK_REGISTER"},
    "loa": {"LOA_REGISTER"},
    "circle_transfer": {"HR_MANAGE"},
    "poc": {"POC_ISSUE"},
}


SUPERVISOR_SCORER_CODES = {"GSM", "TA_GSM"}


def scoped_hr_attraction_ids(db: Session, user: V2User) -> set[int] | None:
    """Return the circle restriction for a scoped HR account; global roles remain unrestricted."""
    if user.role.code != SCOPED_HR_ROLE_CODE:
        return None
    if not user.employee.attraction_id:
        raise HTTPException(403, "景点圈HR账号未配置景点圈")
    return {user.employee.attraction_id}


def ensure_scoped_hr_attraction(db: Session, user: V2User, attraction_id: int | None) -> None:
    allowed = scoped_hr_attraction_ids(db, user)
    if allowed is not None and attraction_id not in allowed:
        raise HTTPException(403, "景点圈HR只能操作所属景点圈")


def ensure_scoped_hr_employee(db: Session, user: V2User, employee: Employee) -> None:
    ensure_scoped_hr_attraction(db, user, employee.attraction_id)


def ensure_operational_target_scope(user: V2User, employee: Employee) -> None:
    """Backend authority for deduction/absence target selection.

    Search UI is only a convenience: TA主管 is confined to its own circle;
    主管、TA GSM、GSM may support active frontline staff across circles.
    """
    if employee.id == user.id:
        raise HTTPException(403, "不能对本人登记")
    if user.role.code == "TA_SUPERVISOR" and employee.attraction_id != user.employee.attraction_id:
        raise HTTPException(403, "TA主管仅可登记本景点圈CM/TR")
    if user.role.code not in {"TA_SUPERVISOR", "SUPERVISOR", "TA_GSM", "GSM", "HR_CIRCLE", "SYSTEM_ADMIN"}:
        raise HTTPException(403, "当前角色无权登记该员工")


def visible_system_alerts(db: Session, user: V2User, *, limit: int = 200) -> list[SystemAlert]:
    """Return only alerts inside the caller's existing HR scope, then page."""
    query = db.query(SystemAlert)
    allowed_attractions = scoped_hr_attraction_ids(db, user)
    if allowed_attractions is not None:
        group_ids = [
            row[0]
            for row in db.query(WorkGroup.id).filter(WorkGroup.attraction_id.in_(allowed_attractions)).all()
        ]
        employee_ids = [
            row[0]
            for row in db.query(Employee.id).filter(Employee.attraction_id.in_(allowed_attractions)).all()
        ]
        scope_clauses = []
        if group_ids:
            scope_clauses.append(SystemAlert.group_id.in_(group_ids))
        if employee_ids:
            scope_clauses.append(and_(SystemAlert.group_id.is_(None), SystemAlert.employee_id.in_(employee_ids)))
        query = query.filter(or_(*scope_clauses) if scope_clauses else SystemAlert.id == -1)
    return query.order_by(SystemAlert.status.asc(), SystemAlert.created_at.desc(), SystemAlert.id.desc()).limit(limit).all()


def ensure_enabled_scored_target(db: Session, employee_id: int, action_name: str) -> tuple[Employee, Role]:
    """A scored employee: base CM/TR, or base 主管 (including one acting as TA GSM)."""
    target = db.get(Employee, employee_id)
    target_role = base_role_at(db, employee_id) if target else None
    account_enabled = bool(
        target
        and db.query(UserAccount.id)
        .filter(UserAccount.employee_id == target.id, UserAccount.enabled.is_(True))
        .first()
    )
    if not target or not target.is_active or not account_enabled or not target_role or target_role.code not in SCORED_BASE_CODES:
        raise HTTPException(400, f"只能为在职、账号启用的CM/TR或主管登记{action_name}")
    return target, target_role


def ensure_supervisor_target_allowed(db: Session, user: V2User, target: Employee, on_date: str, action_name: str) -> None:
    if target.id == user.id:
        raise HTTPException(403, "不能对本人登记")
    if duty_code_at(db, target.id, on_date) == "TA_GSM":
        raise HTTPException(403, f"代理TA GSM期间的主管不能由他人{action_name}，请其本人登记后由AM或正式GSM复核")
    if user.role.code not in SUPERVISOR_SCORER_CODES:
        raise HTTPException(403, f"只有正式GSM和TA GSM可以为主管{action_name}")


def ensure_enabled_frontline_target(db: Session, employee_id: int, action_name: str) -> tuple[Employee, Role]:
    target = db.get(Employee, employee_id)
    target_role = base_role_at(db, employee_id) if target else None
    account_enabled = bool(
        target
        and db.query(UserAccount.id)
        .filter(UserAccount.employee_id == target.id, UserAccount.enabled.is_(True))
        .first()
    )
    if not target or not target.is_active or not account_enabled or not target_role or target_role.code not in FRONTLINE_CODES:
        raise HTTPException(400, f"只能为在职、账号启用的CM/TR登记{action_name}")
    return target, target_role


def void_operator_snapshot(db: Session, user: V2User) -> tuple[str, str, str]:
    if user.role.code in FRONTLINE_CODES:
        scope = f"本人账号（{user.employee.employee_no}）"
    elif user.role.code in LEADER_CODES:
        group_names = [group.name for group in groups_assigned_to(db, user.id)]
        scope = f"直属小组：{'、'.join(group_names)}" if group_names else "直属小组：未配置"
    elif user.role.code in GSM_CODES:
        attraction_ids = sorted(managed_attraction_ids(db, user.id))
        attraction_names = [
            row.name
            for row in db.query(Attraction).filter(Attraction.id.in_(attraction_ids)).order_by(Attraction.name).all()
        ] if attraction_ids else []
        scope = f"景点圈：{'、'.join(attraction_names)}" if attraction_names else "景点圈：未配置"
    elif user.role.code == "HR_ADMIN":
        scope = "HR人员与组织管理"
    else:
        scope = "全局系统权限"
    return user.role.code, user.role.name, scope
