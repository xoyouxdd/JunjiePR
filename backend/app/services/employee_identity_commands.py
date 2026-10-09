"""Base-role assignments, acting duties and legacy-duty confirmation."""
from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy import or_

from app.access_policy import CIRCLE_HR_MANAGED_ROLE_CODES
from app.date_utils import parse_iso_date
from app.role_constants import DUTY_BASE_CODES, DUTY_ROLE_CODES
from app.services.employee_groups import relinquish_ineligible_leadership
from app.services.role_lifecycle import resolve_acting_duty_migration_alerts
from app.v2_models import EmployeeActingDuty, EmployeeRoleAssignment, Role

if TYPE_CHECKING:
    from app.services.employee_commands import EmployeeEditContext


def _allowed_role(context: EmployeeEditContext, code: str, label: str = "角色") -> Role:
    role = context.db.query(Role).filter(Role.code == code).first()
    if not role:
        raise HTTPException(400, "角色不存在")
    if "SYSTEM_ADMIN" not in context.user.permissions and role.code not in CIRCLE_HR_MANAGED_ROLE_CODES:
        raise HTTPException(403, f"景点圈HR只能设置{label}为CM、TR、TA主管或主管")
    return role


def base_role_after_edit(context: EmployeeEditContext) -> Role | None:
    """Read the resulting base without applying identity or leadership changes.

    Status validation runs first, but LOA eligibility belongs to the identity
    that this whole form will save. A selected duty keeps the existing base;
    confirming a legacy duty's base selects that real base and keeps the duty.
    """
    code = str(context.payload.get("role_code") or "")
    current_role, base = context.existing_role, context.existing_base_role
    if base and base.code in DUTY_ROLE_CODES and code in DUTY_BASE_CODES[base.code]:
        return _allowed_role(context, code)
    if not code or not current_role or code == current_role.code or code in DUTY_ROLE_CODES:
        return base
    return _allowed_role(context, code)


def _active_duty(context: EmployeeEditContext) -> EmployeeActingDuty | None:
    return (
        context.db.query(EmployeeActingDuty)
        .filter(
            EmployeeActingDuty.employee_id == context.employee.id,
            EmployeeActingDuty.status == "active",
            EmployeeActingDuty.starts_on <= context.today_text,
            or_(EmployeeActingDuty.ends_on.is_(None), EmployeeActingDuty.ends_on >= context.today_text),
        )
        .order_by(EmployeeActingDuty.starts_on.desc(), EmployeeActingDuty.id.desc())
        .first()
    )


def _term_end(context: EmployeeEditContext) -> str | None:
    ends_on = str(context.payload.get("role_ends_on") or "").strip() or None
    if ends_on:
        parse_iso_date(ends_on, "代理职务结束日期")
        if ends_on < context.today_text:
            raise HTTPException(400, "代理职务结束日期不能早于今天")
    return ends_on


def _adjust_duty_term(context: EmployeeEditContext, active_duty: EmployeeActingDuty | None, role_code: str, ends_on: str | None) -> None:
    if role_code in DUTY_ROLE_CODES and active_duty and active_duty.role.code == role_code and ends_on != active_duty.ends_on:
        context.ensure_current_month_open("调整代理职务期限")
        before_end = active_duty.ends_on
        active_duty.ends_on = ends_on
        context.audit("调整代理职务期限", "acting_duty", active_duty.id, before={"ends_on": before_end}, after={"ends_on": ends_on})


def _resolve_legacy_base(context: EmployeeEditContext, legacy_role: Role, base_code: str, ends_on: str | None) -> Role:
    """Confirm the real base while preserving an unresolved old acting duty."""
    context.ensure_current_month_open("确认旧代理记录的本职")
    context.attendance_state_changed = True
    new_base = _allowed_role(context, base_code)
    assignment = (
        context.db.query(EmployeeRoleAssignment)
        .filter(
            EmployeeRoleAssignment.employee_id == context.employee.id,
            EmployeeRoleAssignment.role_id == legacy_role.id,
            EmployeeRoleAssignment.status != "cancelled",
            EmployeeRoleAssignment.starts_on <= context.today_text,
            or_(EmployeeRoleAssignment.ends_on.is_(None), EmployeeRoleAssignment.ends_on >= context.today_text),
        )
        .order_by(EmployeeRoleAssignment.starts_on.desc(), EmployeeRoleAssignment.id.desc())
        .first()
    )
    legacy_end = assignment.ends_on if assignment else None
    if assignment:
        if assignment.starts_on >= context.today_text:
            assignment.status = "cancelled"
        else:
            assignment.ends_on = (context.today - timedelta(days=1)).isoformat()
            assignment.status = "expired"
        assignment.return_role_id = None
    context.db.add(EmployeeRoleAssignment(
        employee_id=context.employee.id, role_id=new_base.id,
        starts_on=context.today_text, assignment_type="permanent", status="active",
        reason=str(context.payload.get("reason") or "HR确认旧代理记录的本职"), created_by=context.user.id,
    ))
    duty = EmployeeActingDuty(
        employee_id=context.employee.id, role_id=legacy_role.id,
        starts_on=context.today_text,
        ends_on=ends_on or (legacy_end if legacy_end and legacy_end >= context.today_text else None),
        status="active", reason="HR确认本职后延续原代理职务", created_by=context.user.id,
    )
    context.db.add(duty)
    context.db.flush()
    context.audit(
        "确认旧代理记录的本职", "acting_duty", duty.id,
        before={"role": legacy_role.name},
        after={"base_role": new_base.name, "duty": legacy_role.name, "ends_on": duty.ends_on},
    )
    resolve_acting_duty_migration_alerts(context.db, context.employee.id)
    return new_base


def _end_duty(context: EmployeeEditContext, duty: EmployeeActingDuty | None) -> None:
    if not duty:
        return
    if duty.starts_on >= context.today_text:
        duty.status = "cancelled"
    else:
        duty.status = "ended"
        duty.ends_on = (context.today - timedelta(days=1)).isoformat()
    context.audit("结束代理职务", "acting_duty", duty.id, after={"role": duty.role.name, "status": duty.status, "ends_on": duty.ends_on})


def _start_duty(context: EmployeeEditContext, role: Role, ends_on: str | None) -> None:
    duty = EmployeeActingDuty(
        employee_id=context.employee.id, role_id=role.id, starts_on=context.today_text,
        ends_on=ends_on, status="active",
        reason=str(context.payload.get("reason") or "HR设置代理职务"), created_by=context.user.id,
    )
    context.db.add(duty)
    context.db.flush()
    context.audit(
        "设置代理职务", "acting_duty", duty.id,
        after={"role": role.name, "base_role": context.existing_base_role.name, "starts_on": context.today_text, "ends_on": ends_on},
    )


def _replace_base_role(context: EmployeeEditContext, role: Role, ends_on: str | None) -> None:
    assignment = (
        context.db.query(EmployeeRoleAssignment)
        .filter(EmployeeRoleAssignment.employee_id == context.employee.id, EmployeeRoleAssignment.status == "active")
        .order_by(EmployeeRoleAssignment.starts_on.desc())
        .first()
    )
    if assignment:
        assignment.ends_on = context.today_text
        assignment.status = "expired"
    return_role = context.db.query(Role).filter(Role.code == str(context.payload.get("return_role_code") or "")).first() if ends_on else None
    if ends_on:
        if not return_role:
            raise HTTPException(400, "临时角色必须选择有效的到期恢复角色")
        _allowed_role(context, return_role.code, "到期恢复角色")
    context.db.add(EmployeeRoleAssignment(
        employee_id=context.employee.id, role_id=role.id, starts_on=context.today_text,
        ends_on=ends_on, assignment_type="temporary" if ends_on else "permanent",
        return_role_id=return_role.id if return_role else None, status="active",
        reason=str(context.payload.get("reason") or "HR变更角色"), created_by=context.user.id,
    ))


def apply_identity_changes(context: EmployeeEditContext) -> tuple[Role | None, Role | None]:
    new_role_code = str(context.payload.get("role_code") or "")
    current_role = context.existing_role
    resulting_base = context.existing_base_role
    active_duty = _active_duty(context)
    ends_on = _term_end(context)
    _adjust_duty_term(context, active_duty, new_role_code, ends_on)
    legacy_role = resulting_base if resulting_base and resulting_base.code in DUTY_ROLE_CODES else None
    if legacy_role and new_role_code in DUTY_BASE_CODES[legacy_role.code]:
        return legacy_role, _resolve_legacy_base(context, legacy_role, new_role_code, ends_on)
    if not new_role_code or not current_role or new_role_code == current_role.code:
        return current_role, resulting_base

    context.ensure_current_month_open("修改员工身份")
    context.attendance_state_changed = True
    new_role = _allowed_role(context, new_role_code)
    is_duty = new_role.code in DUTY_ROLE_CODES
    if is_duty and (not resulting_base or resulting_base.code not in DUTY_BASE_CODES[new_role.code]):
        allowed_names = "CM/TR" if new_role.code == "TA_SUPERVISOR" else "主管"
        raise HTTPException(400, f"代理{new_role.name}的本职必须是{allowed_names}，请先调整本职身份")
    next_base_code = resulting_base.code if is_duty else new_role.code
    relinquish_ineligible_leadership(context, current_role, new_role, next_base_code)
    _end_duty(context, active_duty)
    if is_duty:
        _start_duty(context, new_role, ends_on)
    elif not resulting_base or new_role.code != resulting_base.code:
        _replace_base_role(context, new_role, ends_on)
        resulting_base = new_role
    return new_role, resulting_base
