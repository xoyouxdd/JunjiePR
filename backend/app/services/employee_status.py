"""Employee profile, circle, employment/LOA and account-state commands."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.access_policy import ensure_scoped_hr_attraction
from app.date_utils import parse_iso_date
from app.role_constants import FRONTLINE_CODES
from app.services import loa_commands
from app.services.employee_identity_commands import base_role_after_edit
from app.services.organization import groups_assigned_to
from app.v2_models import Attraction, UserAccount

if TYPE_CHECKING:
    from app.services.employee_commands import EmployeeEditContext


def employee_circle_id(db: Session, value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        attraction_id = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "员工景点圈无效") from exc
    circle = db.get(Attraction, attraction_id)
    if not circle or not circle.active or not circle.employee_circle:
        raise HTTPException(400, "员工只能归属有效景点圈")
    return circle.id


def apply_profile_and_status(context: EmployeeEditContext) -> None:
    requested_status = str(context.payload.get("employment_status") or "").strip().lower()
    if requested_status and requested_status not in {"active", "loa", "terminated"}:
        raise HTTPException(400, "人员状态无效")
    _apply_profile(context)
    _apply_employment_status(context, requested_status)
    _apply_account_status(context)


def _apply_profile(context: EmployeeEditContext) -> None:
    employee, payload = context.employee, context.payload
    if "name" in payload:
        employee.name = str(payload["name"]).strip()
    if "attraction_id" in payload:
        requested_attraction_id = employee_circle_id(context.db, payload["attraction_id"])
        ensure_scoped_hr_attraction(context.db, context.user, requested_attraction_id)
        if requested_attraction_id != employee.attraction_id:
            context.ensure_current_month_open("调出员工")
            context.month_gate(context.db, context.today.strftime("%Y-%m"), requested_attraction_id, "调入员工")
            employee.attraction_id = requested_attraction_id


def _set_activity_state(context: EmployeeEditContext, is_active: bool) -> None:
    employee = context.employee
    terminated_on = None if is_active else (
        employee.terminated_on if not employee.is_active and employee.terminated_on else context.today_text
    )
    if employee.is_active != is_active or employee.terminated_on != terminated_on:
        context.ensure_current_month_open("修改当月人员计分状态")
        context.attendance_state_changed = True
        employee.is_active = is_active
        employee.terminated_on = terminated_on


def _apply_employment_status(context: EmployeeEditContext, requested_status: str) -> None:
    db, employee, payload = context.db, context.employee, context.payload
    if not requested_status:
        if "is_active" in payload:
            if not bool(payload["is_active"]) and groups_assigned_to(db, employee.id):
                raise HTTPException(400, "该员工仍是小组负责人，请先在小组管理中更换")
            _set_activity_state(context, bool(payload["is_active"]))
        return

    current_loa = loa_commands.current_open_period(db, employee.id)
    if requested_status == "loa":
        resulting_base = base_role_after_edit(context)
        if not resulting_base or resulting_base.code not in FRONTLINE_CODES:
            raise HTTPException(400, "仅可将CM/TR演职人员设置为LOA")
        starts_on = parse_iso_date(str(payload.get("loa_start_date") or context.today_text), "LOA开始日期")
        if not current_loa:
            _set_activity_state(context, True)
            change = loa_commands.create_period(
                db, employee, starts_on, None, actor_id=context.user.id, actor_name=context.user.name,
                note=str(payload.get("reason") or "HR设置LOA").strip() or None,
                today=context.loa_today, gate=context.ensure_affected_month_open, operation="设置LOA",
            )
            context.changed_months.update(change.changed_months)
            context.audit(
                "设置LOA（长期病假）", "employee_loa", employee.id,
                after={"starts_on": starts_on.isoformat(), "status": "LOA（长期病假）"},
            )
        return

    if current_loa:
        change = loa_commands.close_period(
            db, employee, current_loa, context.today - timedelta(days=1),
            actor_id=context.user.id, actor_name=context.user.name,
            today=context.loa_today, gate=context.ensure_affected_month_open, operation="结束LOA",
            status="ended", cancel_if_before_start=True,
        )
        context.changed_months.update(change.changed_months)
        context.audit(
            "结束LOA（长期病假）", "employee_loa", employee.id,
            after={"ends_on": current_loa.ends_on, "next_status": requested_status},
        )
    if requested_status == "terminated" and groups_assigned_to(db, employee.id):
        raise HTTPException(400, "该员工仍是小组负责人，请先在小组管理中更换")
    _set_activity_state(context, requested_status == "active")


def _apply_account_status(context: EmployeeEditContext) -> None:
    account = context.db.query(UserAccount).filter(UserAccount.employee_id == context.employee.id).first()
    if account and "account_enabled" in context.payload:
        next_enabled = bool(context.payload["account_enabled"])
        if next_enabled != account.enabled:
            context.ensure_current_month_open("修改员工账号启用状态")
            account.enabled = next_enabled
            account.disabled_at = None if next_enabled else datetime.now()
