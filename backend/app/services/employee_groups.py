"""Group membership and leadership effects of an HR employee edit."""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy import or_

from app.role_constants import DUTY_ROLE_CODES, FRONTLINE_CODES, LEADER_CODES
from app.services.organization import (
    active_group_memberships, current_leader_for_employee, group_leader_of_type,
    groups_assigned_to, sync_pending_reviewers,
)
from app.v2_models import GroupMembership, RecognitionRecord, Role, WorkGroup

if TYPE_CHECKING:
    from app.services.employee_commands import EmployeeEditContext


def relinquish_ineligible_leadership(
    context: EmployeeEditContext, current_role: Role, new_role: Role, next_base_code: str,
) -> None:
    keeps = {"formal": next_base_code == "SUPERVISOR", "acting": new_role.code == "TA_SUPERVISOR"}
    for group in groups_assigned_to(context.db, context.employee.id):
        for leader_type in ("formal", "acting"):
            assignment = group_leader_of_type(context.db, group.id, leader_type, context.today_text)
            if not assignment or assignment.leader_employee_id != context.employee.id or keeps[leader_type]:
                continue
            other_type = "acting" if leader_type == "formal" else "formal"
            if active_group_memberships(context.db, group.id, context.today_text) and not group_leader_of_type(
                context.db, group.id, other_type, context.today_text,
            ):
                context.warnings.append(f"{group.name}现在没有负责人，组员的签卡暂时无人复核，请到小组管理设置")
            assignment.status = "ended"
            assignment.ends_on = max(assignment.starts_on, context.today_text)
            group.revision += 1
            context.db.flush()
            sync_pending_reviewers(context.db, group.id)
            label = "主管" if leader_type == "formal" else "代理主管"
            context.audit(
                f"卸任小组{label}", "work_group", group.id,
                before={label: context.employee.name}, after={label: "无"},
                reason=f"身份由{current_role.name}变更为{new_role.name}",
            )


def _current_membership(context: EmployeeEditContext) -> GroupMembership | None:
    return (
        context.db.query(GroupMembership)
        .filter(
            GroupMembership.employee_id == context.employee.id,
            GroupMembership.status == "active",
            GroupMembership.starts_on <= context.today_text,
            or_(GroupMembership.ends_on.is_(None), GroupMembership.ends_on >= context.today_text),
        )
        .order_by(GroupMembership.starts_on.desc(), GroupMembership.id.desc())
        .first()
    )


def _requested_or_historic_group(context: EmployeeEditContext, requested_group_id: int | None) -> WorkGroup | None:
    target_group = context.db.get(WorkGroup, requested_group_id) if requested_group_id else None
    if requested_group_id:
        if not target_group or target_group.status == "closed" or target_group.attraction_id != context.employee.attraction_id:
            raise HTTPException(400, "请选择员工所在景点圈内的小组")
        formal = group_leader_of_type(context.db, target_group.id, "formal", context.today_text)
        if formal and formal.leader_employee_id == context.employee.id:
            raise HTTPException(400, "员工不能成为自己所带小组的组员")
        # An acting supervisor may be a member; reviewer resolution excludes self.

    new_role_code = str(context.payload.get("role_code") or "")
    if not target_group and new_role_code and context.existing_base_role and context.existing_base_role.code in LEADER_CODES:
        candidates: list[WorkGroup] = []
        history = context.db.query(GroupMembership).filter(
            GroupMembership.employee_id == context.employee.id,
            GroupMembership.status.in_(("ended", "active")),
        ).order_by(GroupMembership.starts_on.desc(), GroupMembership.id.desc()).all()
        for historic in history:
            group = historic.group
            if group and group.status != "closed" and group.attraction_id == context.employee.attraction_id and group not in candidates:
                candidates.append(group)
        if len(candidates) == 1:
            target_group = candidates[0]
        elif len(candidates) > 1:
            raise HTTPException(409, "该员工在本景点圈有多个历史小组，请选择小组后再保存")
    return target_group


def _move_membership(
    context: EmployeeEditContext, current_membership: GroupMembership | None, target_group: WorkGroup | None,
) -> None:
    current_group = current_membership.group if current_membership else None
    if (target_group.id if target_group else None) == (current_group.id if current_group else None):
        return
    # A complete HR form can post the terminated employee's unchanged group.
    # Only adding a new membership is joining, including automatic restoration.
    if target_group and not context.employee.is_active:
        raise HTTPException(400, "离职员工不能加入小组")
    context.ensure_current_month_open("调整员工小组")
    pending = context.db.query(RecognitionRecord).filter(
        RecognitionRecord.employee_id == context.employee.id, RecognitionRecord.status == "pending",
    )
    if not target_group and pending.count():
        raise HTTPException(400, "该员工还有待复核记录，必须选择小组")
    if current_membership:
        current_membership.status = "ended"
        current_membership.ends_on = context.today_text
    reviewer = None
    if target_group:
        context.db.add(GroupMembership(
            group_id=target_group.id, employee_id=context.employee.id,
            starts_on=context.today_text, status="active",
            reason=str(context.payload.get("reason") or "HR调整小组"),
        ))
        context.db.flush()
        reviewer = current_leader_for_employee(context.db, context.employee.id)
        pending.update({RecognitionRecord.assigned_reviewer_id: reviewer.id if reviewer else None}, synchronize_session=False)
        if not reviewer:
            context.warnings.append(f"{target_group.name}没有可复核{context.employee.name}的负责人，其签卡暂时无人复核，请到小组管理设置")
    context.audit(
        "调整员工小组", "employee_group", context.employee.id,
        before={"group_id": current_group.id if current_group else None, "group_name": current_group.name if current_group else "未分组"},
        after={"group_id": target_group.id if target_group else None, "group_name": target_group.name if target_group else "未分组", "reviewer": reviewer.name if reviewer else ""},
        reason=str(context.payload.get("reason") or "HR员工管理页面调整"),
    )


def apply_group_membership(context: EmployeeEditContext, resulting_role: Role | None, resulting_base_role: Role | None) -> None:
    if "group_id" not in context.payload:
        return
    requested_group_id = int(context.payload["group_id"]) if context.payload.get("group_id") else None
    current_membership = _current_membership(context)
    # Receiving an acting duty keeps the employee's group when HR posts an empty
    # group field; a simultaneous circle change still ends the old membership.
    if (
        resulting_role is not None
        and resulting_role.code in DUTY_ROLE_CODES
        and not requested_group_id
        and not (current_membership and current_membership.group.attraction_id != context.employee.attraction_id)
    ):
        return
    if not resulting_base_role or resulting_base_role.code not in FRONTLINE_CODES:
        if requested_group_id:
            raise HTTPException(400, "只有CM/TR可以加入小组")
        if current_membership:
            context.ensure_current_month_open("调整员工小组")
            current_membership.status = "ended"
            current_membership.ends_on = context.today_text
        return
    target_group = _requested_or_historic_group(context, requested_group_id)
    _move_membership(context, current_membership, target_group)
