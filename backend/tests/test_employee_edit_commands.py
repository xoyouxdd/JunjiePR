"""Combined employee edits commit or roll back as one isolated SQLite unit."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database.connection import Base
from app.month_closure import ensure_month_open
from app.routers import employees as routes
from app.services import attendance, identity, organization
from app.services.employee_commands import EmployeeEditContext, apply_employee_edit
from app.v2_auth import V2User
from app.v2_models import (
    AttendanceMonthlyScore, AttendanceRule, Attraction, AuditLog, Employee,
    EmployeeActingDuty, EmployeeLOAPeriod, EmployeeMonthOrganizationSnapshot,
    EmployeeRoleAssignment, GroupLeaderAssignment, GroupMembership, MonthClosure,
    RecognitionRecord, RecognitionType, Role, UserAccount, WorkGroup,
)


TODAY = date(2026, 10, 9)


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(TODAY.year, TODAY.month, TODAY.day)


@pytest.fixture
def context(monkeypatch):
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    for module in (routes, identity, organization, attendance):
        monkeypatch.setattr(module, "date", FixedDate)
    monkeypatch.setattr(routes, "loa_today", lambda: TODAY)
    monkeypatch.setattr(routes, "invalidate_data_caches", lambda: None)
    sessions = sessionmaker(bind=engine, autoflush=False)
    with sessions() as db:
        roles = {
            code: Role(code=code, name=code, rank=rank, attendance_eligible=code in {"CM", "TR"})
            for code, rank in (("CM", 10), ("TR", 10), ("SUPERVISOR", 25), ("TA_SUPERVISOR", 20), ("GSM", 40), ("TA_GSM", 35), ("SYSTEM_ADMIN", 90))
        }
        old_circle = Attraction(name="原景点圈", employee_circle=True)
        new_circle = Attraction(name="目标景点圈", employee_circle=True)
        recognition_type = RecognitionType(code="SAFETY", name="安全")
        db.add_all([old_circle, new_circle, recognition_type, *roles.values()])
        db.flush()
        employee = Employee(employee_no="EMP-COMMAND", name="原姓名", attraction_id=old_circle.id, hired_on="2020-01-01")
        actor = Employee(employee_no="ADMIN-COMMAND", name="操作人", attraction_id=old_circle.id)
        formal = Employee(employee_no="SUP-COMMAND", name="主管", attraction_id=old_circle.id)
        db.add_all([employee, actor, formal])
        db.flush()
        db.add_all([
            EmployeeRoleAssignment(employee_id=employee.id, role_id=roles["CM"].id, starts_on="2020-01-01"),
            EmployeeRoleAssignment(employee_id=formal.id, role_id=roles["SUPERVISOR"].id, starts_on="2020-01-01"),
            AttendanceRule(effective_date="2020-01-01"),
        ])
        old_group = WorkGroup(name="原景点圈A组", code="A", attraction_id=old_circle.id)
        peer_group = WorkGroup(name="原景点圈B组", code="B", attraction_id=old_circle.id)
        target_group = WorkGroup(name="目标景点圈A组", code="A", attraction_id=new_circle.id)
        db.add_all([old_group, peer_group, target_group])
        db.flush()
        membership = GroupMembership(group_id=old_group.id, employee_id=employee.id, starts_on="2020-01-01")
        account = UserAccount(employee_id=employee.id, login_account=employee.employee_no, password_hash="unused-test-hash")
        snapshot = EmployeeMonthOrganizationSnapshot(
            employee_id=employee.id, score_month="2026-09", attraction_id=old_circle.id,
            attraction_name=old_circle.name, group_id=old_group.id, group_name=old_group.name,
            base_role_code="CM", scoring_category="frontline",
        )
        db.add_all([membership, account, snapshot])
        db.add_all(GroupLeaderAssignment(group_id=group.id, leader_employee_id=formal.id, starts_on="2020-01-01") for group in (old_group, peer_group, target_group))
        db.commit()
        user = V2User(employee=actor, account=SimpleNamespace(), role=roles["SYSTEM_ADMIN"], permissions={"HR_MANAGE", "SYSTEM_ADMIN"})
        request = Request({"type": "http", "client": ("127.0.0.1", 12345), "headers": []})
        yield SimpleNamespace(
            db=db, employee=employee, actor=actor, formal=formal, roles=roles,
            old_circle=old_circle, new_circle=new_circle, old_group=old_group,
            peer_group=peer_group, target_group=target_group, membership=membership,
            account=account, snapshot=snapshot, recognition_type=recognition_type,
            user=user, request=request,
        )
    engine.dispose()


def edit(context, payload):
    return routes.update_employee(context.employee.id, payload, context.request, context.db, context.user)


def pending_record(context, employee=None, reviewer=None):
    employee = employee or context.employee
    row = RecognitionRecord(
        employee_id=employee.id, employee_no=employee.employee_no, employee_name=employee.name,
        employee_role_snapshot="CM", employee_role_code_snapshot="CM",
        home_attraction_id=context.old_circle.id, home_attraction_name=context.old_circle.name,
        occurred_attraction_id=context.old_circle.id, recognition_date=TODAY.isoformat(), recognition_month="2026-10",
        recognition_type_id=context.recognition_type.id, recognition_type_name="安全", content="组合编辑验证",
        recognizer_employee_id=context.formal.id, recognizer_name=context.formal.name, recognizer_role_snapshot="SUPERVISOR",
        operator_employee_id=employee.id, operator_name=employee.name,
        source="self", fraction=Decimal("1"), status="pending",
        assigned_reviewer_id=(reviewer or context.formal).id,
    )
    context.db.add(row)
    context.db.commit()
    return row


def test_combined_identity_circle_group_and_account_change_preserves_snapshots(context):
    record = pending_record(context)
    result = edit(context, {
        "name": "新姓名", "attraction_id": context.new_circle.id, "role_code": "TR",
        "group_id": context.target_group.id, "employment_status": "active", "account_enabled": False,
        "reason": "岗位调整",
    })
    assert result == {"ok": True, "warnings": []}
    assert context.employee.name == "新姓名"
    assert context.employee.attraction_id == context.new_circle.id
    assert identity.base_role_at(context.db, context.employee.id).code == "TR"
    assert organization.current_group_for_employee(context.db, context.employee.id).id == context.target_group.id
    assert context.membership.status == "ended"
    assert context.account.enabled is False and context.account.disabled_at is not None
    assert record.assigned_reviewer_id == context.formal.id
    assert record.home_attraction_id == context.old_circle.id
    assert context.snapshot.attraction_id == context.old_circle.id
    assert context.snapshot.group_id == context.old_group.id and context.snapshot.base_role_code == "CM"
    score = context.db.query(AttendanceMonthlyScore).filter_by(employee_id=context.employee.id, attendance_month="2026-10").one()
    assert score.final_score > 0
    audit = context.db.query(AuditLog).filter_by(action="修改员工").one()
    assert json.loads(audit.before_json)["name"] == "原姓名"
    assert json.loads(audit.after_json)["name"] == "新姓名"
    assert audit.reason == "岗位调整"


def test_late_invalid_group_rolls_back_loa_duty_account_profile_scores_and_audits(context):
    with pytest.raises(HTTPException) as rejected:
        edit(context, {
            "name": "不应保存", "employment_status": "loa", "loa_start_date": "2026-10-01",
            "role_code": "TA_SUPERVISOR", "account_enabled": False, "group_id": context.target_group.id,
        })
    assert rejected.value.status_code == 400
    assert context.employee.name == "原姓名" and context.employee.is_active is True
    assert context.account.enabled is True and context.account.disabled_at is None
    assert context.membership.status == "active"
    assert identity.base_role_at(context.db, context.employee.id).code == "CM"
    for model in (EmployeeLOAPeriod, EmployeeActingDuty, AttendanceMonthlyScore, AuditLog):
        assert context.db.query(model).count() == 0


def test_complete_unchanged_hr_form_allows_name_change_in_closed_month(context):
    context.db.add(MonthClosure(closure_month="2026-10", attraction_id=context.old_circle.id, status="closed"))
    context.db.commit()
    result = edit(context, {
        "name": "名称更正", "employment_status": "active", "is_active": True,
        "role_code": "CM", "role_ends_on": "", "attraction_id": context.old_circle.id,
        "group_id": context.old_group.id, "account_enabled": True,
    })
    assert result["ok"] is True
    assert context.employee.name == "名称更正"
    assert context.db.query(EmployeeRoleAssignment).filter_by(employee_id=context.employee.id).count() == 1
    assert context.db.query(GroupMembership).filter_by(employee_id=context.employee.id).count() == 1
    assert context.db.query(AttendanceMonthlyScore).count() == 0


@pytest.mark.parametrize("field,value", [("role_code", "TR"), ("account_enabled", False), ("employment_status", "terminated")])
def test_real_changes_gate_closed_month_and_roll_back_prior_name_change(context, field, value):
    context.db.add(MonthClosure(closure_month="2026-10", attraction_id=context.old_circle.id, status="closed"))
    context.db.commit()
    with pytest.raises(HTTPException) as rejected:
        edit(context, {"name": "不应保存", field: value})
    assert rejected.value.status_code == 423
    assert context.employee.name == "原姓名"
    assert context.employee.is_active is True and context.account.enabled is True
    assert identity.base_role_at(context.db, context.employee.id).code == "CM"
    assert context.db.query(AuditLog).count() == 0


def test_acting_duty_keeps_original_membership_then_excludes_self_when_regrouping(context):
    record = pending_record(context)
    assert edit(context, {"role_code": "TA_SUPERVISOR", "group_id": None})["ok"] is True
    assert context.membership.status == "active"
    assert identity.base_role_at(context.db, context.employee.id).code == "CM"
    context.db.add(GroupLeaderAssignment(
        group_id=context.peer_group.id, leader_employee_id=context.employee.id,
        leader_type="acting", starts_on=TODAY.isoformat(),
    ))
    context.db.commit()
    assert edit(context, {"role_code": "TA_SUPERVISOR", "group_id": context.peer_group.id})["ok"] is True
    assert record.assigned_reviewer_id == context.formal.id
    assert record.assigned_reviewer_id != context.employee.id
    assert organization.current_group_for_employee(context.db, context.employee.id).id == context.peer_group.id


def test_demotion_ends_leadership_preserves_group_and_restores_single_historic_group(context):
    assignment = context.db.query(EmployeeRoleAssignment).filter_by(employee_id=context.employee.id).one()
    assignment.role_id = context.roles["SUPERVISOR"].id
    context.membership.status = "ended"
    context.membership.ends_on = "2026-09-30"
    leader = context.db.query(GroupLeaderAssignment).filter_by(group_id=context.old_group.id).one()
    leader.leader_employee_id = context.employee.id
    member = Employee(employee_no="MEMBER-COMMAND", name="组员", attraction_id=context.old_circle.id)
    context.db.add(member)
    context.db.flush()
    context.db.add(GroupMembership(employee_id=member.id, group_id=context.old_group.id, starts_on="2020-01-01"))
    context.db.commit()
    record = pending_record(context, member, context.employee)
    result = edit(context, {"role_code": "CM", "group_id": None})
    assert result["ok"] is True and result["warnings"]
    assert leader.status == "ended"
    assert context.old_group.status != "closed" and context.old_group.revision == 2
    assert record.assigned_reviewer_id is None
    assert organization.current_group_for_employee(context.db, context.employee.id).id == context.old_group.id
    assert context.db.query(GroupMembership).filter_by(employee_id=member.id, status="active").count() == 1
    assert context.db.query(AuditLog).filter_by(action="卸任小组主管").count() == 1


def test_command_leaves_commit_to_caller(context):
    command = EmployeeEditContext(
        db=context.db, employee=context.employee, payload={"name": "暂存姓名", "account_enabled": False},
        user=context.user, existing_role=context.roles["CM"], existing_base_role=context.roles["CM"],
        today=TODAY, loa_today=TODAY, month_gate=ensure_month_open,
    )
    apply_employee_edit(command)
    assert context.employee.name == "暂存姓名" and context.account.enabled is False
    context.db.rollback()
    assert context.employee.name == "原姓名" and context.account.enabled is True


def make_supervisor(context):
    assignment = context.db.query(EmployeeRoleAssignment).filter_by(employee_id=context.employee.id).one()
    assignment.role_id = context.roles["SUPERVISOR"].id
    context.membership.status = "ended"
    context.membership.ends_on = "2026-09-30"
    leader = context.db.query(GroupLeaderAssignment).filter_by(group_id=context.old_group.id).one()
    leader.leader_employee_id = context.employee.id
    context.db.commit()
    return leader


@pytest.mark.parametrize("base_code", ["CM", "TR"])
def test_simultaneous_frontline_base_change_and_loa_uses_resulting_base(context, base_code):
    leader = make_supervisor(context)
    result = edit(context, {
        "role_code": base_code, "employment_status": "loa", "loa_start_date": "2026-10-01",
        "group_id": None, "name": "调整后姓名", "account_enabled": False,
    })
    assert result["ok"] is True
    assert identity.base_role_at(context.db, context.employee.id).code == base_code
    assert context.db.query(EmployeeLOAPeriod).filter_by(employee_id=context.employee.id, status="active").count() == 1
    assert context.employee.name == "调整后姓名" and context.employee.is_active is True
    assert context.account.enabled is False
    assert leader.status == "ended" and context.old_group.revision == 2
    assert organization.current_group_for_employee(context.db, context.employee.id).id == context.old_group.id
    assert context.snapshot.base_role_code == "CM" and context.snapshot.group_id == context.old_group.id
    score = context.db.query(AttendanceMonthlyScore).filter_by(employee_id=context.employee.id, attendance_month="2026-10").one()
    assert score.final_score == 0
    assert context.db.query(AuditLog).filter_by(action="设置LOA（长期病假）").count() == 1
    assert context.db.query(AuditLog).filter_by(action="修改员工").count() == 1


def test_simultaneous_supervisor_base_change_and_loa_is_rejected_and_rolled_back(context):
    with pytest.raises(HTTPException) as rejected:
        edit(context, {"role_code": "SUPERVISOR", "employment_status": "loa", "name": "不应保存", "account_enabled": False})
    assert rejected.value.status_code == 400
    assert identity.base_role_at(context.db, context.employee.id).code == "CM"
    assert context.employee.name == "原姓名" and context.account.enabled is True
    assert context.membership.status == "active"
    for model in (EmployeeLOAPeriod, AttendanceMonthlyScore, AuditLog):
        assert context.db.query(model).count() == 0


def test_simultaneous_acting_duty_and_loa_preserves_frontline_base_and_group(context):
    assert edit(context, {"role_code": "TA_SUPERVISOR", "employment_status": "loa", "group_id": None})["ok"] is True
    assert identity.base_role_at(context.db, context.employee.id).code == "CM"
    assert identity.role_at(context.db, context.employee.id).code == "TA_SUPERVISOR"
    assert context.membership.status == "active"
    assert context.db.query(EmployeeLOAPeriod).count() == 1
    assert context.db.query(EmployeeActingDuty).count() == 1


def test_simultaneous_legacy_duty_confirmation_and_loa_uses_confirmed_base(context):
    assignment = context.db.query(EmployeeRoleAssignment).filter_by(employee_id=context.employee.id).one()
    assignment.role_id = context.roles["TA_SUPERVISOR"].id
    acting = GroupLeaderAssignment(group_id=context.peer_group.id, leader_employee_id=context.employee.id, leader_type="acting", starts_on="2020-01-01")
    context.db.add(acting)
    context.db.commit()
    assert edit(context, {"role_code": "CM", "employment_status": "loa", "group_id": None})["ok"] is True
    assert identity.base_role_at(context.db, context.employee.id).code == "CM"
    assert identity.role_at(context.db, context.employee.id).code == "TA_SUPERVISOR"
    assert context.membership.status == "active" and acting.status == "active"
    assert context.db.query(EmployeeLOAPeriod).count() == 1
    assert context.db.query(EmployeeActingDuty).count() == 1


def test_late_group_failure_after_simultaneous_frontline_and_loa_rolls_back_leadership(context):
    leader = make_supervisor(context)
    with pytest.raises(HTTPException) as rejected:
        edit(context, {
            "role_code": "CM", "employment_status": "loa", "name": "不应保存",
            "account_enabled": False, "group_id": context.target_group.id,
        })
    assert rejected.value.status_code == 400
    assert "景点圈" in str(rejected.value.detail)
    assert identity.base_role_at(context.db, context.employee.id).code == "SUPERVISOR"
    assert leader.status == "active" and context.old_group.revision == 1
    assert context.employee.name == "原姓名" and context.account.enabled is True
    assert context.membership.status == "ended"
    for model in (EmployeeLOAPeriod, AttendanceMonthlyScore, AuditLog):
        assert context.db.query(model).count() == 0


def mark_terminated(context):
    context.employee.is_active = False
    context.employee.terminated_on = "2026-09-30"
    context.db.commit()


def test_terminated_complete_unchanged_group_form_allows_name_correction_in_closed_month(context):
    mark_terminated(context)
    context.db.add(MonthClosure(closure_month="2026-10", attraction_id=context.old_circle.id, status="closed"))
    context.db.commit()
    result = edit(context, {
        "name": "离职姓名更正", "role_code": "CM", "employment_status": "terminated",
        "attraction_id": context.old_circle.id, "group_id": context.old_group.id, "account_enabled": True,
    })
    assert result["ok"] is True
    assert context.employee.name == "离职姓名更正" and context.employee.is_active is False
    assert context.employee.terminated_on == "2026-09-30"
    assert context.membership.status == "active"
    assert context.db.query(GroupMembership).filter_by(employee_id=context.employee.id).count() == 1
    assert context.db.query(AttendanceMonthlyScore).count() == 0


@pytest.mark.parametrize("has_current_group", [True, False])
def test_terminated_real_new_group_is_rejected_and_prior_edits_roll_back(context, has_current_group):
    mark_terminated(context)
    if not has_current_group:
        context.membership.status = "ended"
        context.membership.ends_on = "2026-09-30"
        context.db.commit()
    with pytest.raises(HTTPException) as rejected:
        edit(context, {"name": "不应保存", "account_enabled": False, "group_id": context.peer_group.id})
    assert rejected.value.status_code == 400 and "离职员工" in str(rejected.value.detail)
    assert context.employee.name == "原姓名" and context.account.enabled is True
    assert context.employee.is_active is False
    assert context.membership.status == ("active" if has_current_group else "ended")
    assert context.db.query(GroupMembership).filter_by(employee_id=context.employee.id).count() == 1
    assert context.db.query(AuditLog).count() == 0


def test_terminated_reactivation_and_regrouping_in_same_form_is_allowed(context):
    mark_terminated(context)
    assert edit(context, {"employment_status": "active", "group_id": context.peer_group.id})["ok"] is True
    assert context.employee.is_active is True and context.employee.terminated_on is None
    assert organization.current_group_for_employee(context.db, context.employee.id).id == context.peer_group.id
    assert context.membership.status == "ended"


def test_terminated_historic_group_restore_is_also_rejected(context):
    leader = make_supervisor(context)
    leader.status = "ended"
    leader.ends_on = "2026-09-30"
    context.db.commit()
    mark_terminated(context)
    with pytest.raises(HTTPException) as rejected:
        edit(context, {"role_code": "CM", "group_id": None, "name": "不应保存"})
    assert rejected.value.status_code == 400 and "离职员工" in str(rejected.value.detail)
    assert identity.base_role_at(context.db, context.employee.id).code == "SUPERVISOR"
    assert context.employee.name == "原姓名" and context.employee.is_active is False
    assert context.membership.status == "ended"
    assert context.db.query(GroupMembership).filter_by(employee_id=context.employee.id).count() == 1
    assert context.db.query(AuditLog).count() == 0
