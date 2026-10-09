"""Domain service regressions against explicit in-memory/storage dependencies."""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import shutil
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app import v2_services
from app.role_constants import SUPERVISOR_SCORING_START_MONTH
from app.services import attachments, attendance, audit, identity, organization, role_lifecycle
from app.v2_database import Base
from app.v2_models import (
    Attraction, AttendanceMonthlyScore, AttendanceRule, AuditLog, Employee,
    EmployeeActingDuty, EmployeeLOAPeriod, EmployeeRoleAssignment,
    GroupLeaderAssignment, GroupMembership, Role, SickLeaveRecord, StoredFile,
    SystemAlert, WorkGroup,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as session:
        session.add_all([
            Attraction(id=1, name="测试圈", employee_circle=True),
            Role(id=1, code="CM", name="CM", rank=10),
            Role(id=2, code="SUPERVISOR", name="主管", rank=20),
            Role(id=3, code="TA_SUPERVISOR", name="TA主管", rank=20),
            Role(id=4, code="TA_GSM", name="TA GSM", rank=30),
            Role(id=5, code="GSM", name="GSM", rank=30),
            AttendanceRule(id=1, effective_date="2020-01-01"),
        ])
        for employee_id in range(1, 7):
            session.add(Employee(
                id=employee_id, employee_no=f"E{employee_id}", name=f"员工{employee_id}",
                attraction_id=1, hired_on="2020-01-01", is_active=True,
            ))
            session.add(EmployeeRoleAssignment(
                employee_id=employee_id, role_id=2 if employee_id == 2 else 1,
                starts_on="2020-01-01",
            ))
        session.commit()
        yield session
    engine.dispose()


@pytest.fixture
def storage_dir():
    # pytest's owner-only temporary directory permissions cannot be reopened by the
    # restricted Windows runner. Keep storage within this project's test output.
    base = (Path(__file__).resolve().parents[2] / "output" / "refactor-validation" / "domain-storage").resolve()
    directory = base / uuid4().hex
    directory.mkdir(parents=True)
    try:
        yield directory
    finally:
        assert directory.resolve().parent == base
        shutil.rmtree(directory)


def add_sick_leave(db, employee_id, month, days):
    db.add(SickLeaveRecord(
        employee_id=employee_id, attendance_month=month,
        leave_start_date=f"{month}-01", leave_end_date=attendance.month_end(month),
        leave_days=Decimal(days), charged_days=Decimal(days), submitted_by=2,
        submitted_by_name="主管", status="active",
    ))


def test_acting_role_keeps_base_scoring_and_bulk_labels(db):
    db.add_all([
        EmployeeActingDuty(employee_id=1, role_id=3, starts_on="2026-10-01", ends_on="2026-10-31"),
        EmployeeActingDuty(employee_id=2, role_id=4, starts_on="2026-10-01", ends_on="2026-10-31"),
    ])
    db.commit()
    resolved = identity.roles_at(db, [1, 2], "2026-10-15")
    assert [resolved[i].code for i in (1, 2)] == ["TA_SUPERVISOR", "TA_GSM"]
    bases = identity.base_roles_at(db, [1, 2], "2026-10-15")
    assert [identity.scoring_category(bases[i]) for i in (1, 2)] == ["frontline", "supervisor"]
    assert identity.identity_labels(db, [1, 2], "2026-10-15") == {
        1: "CM · 代理TA主管", 2: "主管 · 代理TA GSM",
    }
    for employee_id in (1, 2):
        assert identity.role_at(db, employee_id, "2026-10-15") is resolved[employee_id]
        assert identity.can_lead_on(db, employee_id, "2026-10-15")
    assert identity.role_at(db, 1, "2026-11-01").code == "CM"


def test_temporal_base_role_return_and_cancelled_duties_agree_in_bulk(db):
    old = db.query(EmployeeRoleAssignment).filter_by(employee_id=3).one()
    old.ends_on = "2026-09-30"
    db.add(EmployeeRoleAssignment(
        employee_id=3, role_id=2, starts_on="2026-10-01", ends_on="2026-10-31",
        assignment_type="temporary", return_role_id=1,
    ))
    db.add(EmployeeActingDuty(
        employee_id=3, role_id=4, starts_on="2026-10-01", status="cancelled",
    ))
    db.commit()
    for when, expected in (("2026-09-15", "CM"), ("2026-10-15", "SUPERVISOR"), ("2026-11-01", "CM")):
        assert identity.base_role_at(db, 3, when).code == expected
        assert identity.base_roles_at(db, [3, 3], when)[3].code == expected
        assert identity.roles_at(db, [3], when)[3].code == expected


def test_acting_leader_operates_group_and_formal_leader_reviews_acting_member(db):
    db.add_all([
        WorkGroup(id=1, name="测试圈A组", code="A", attraction_id=1),
        EmployeeActingDuty(employee_id=1, role_id=3, starts_on="2020-01-01"),
        GroupLeaderAssignment(group_id=1, leader_employee_id=2, starts_on="2020-01-01", leader_type="formal"),
        GroupLeaderAssignment(group_id=1, leader_employee_id=1, starts_on="2020-01-01", leader_type="acting"),
        GroupMembership(group_id=1, employee_id=1, starts_on="2020-01-01"),
        GroupMembership(group_id=1, employee_id=3, starts_on="2020-01-01"),
    ])
    db.commit()
    assert organization.active_group_leader(db, 1).leader_employee_id == 1
    assert organization.current_leader_for_employee(db, 1).id == 2
    assert organization.current_leader_for_employee(db, 3).id == 1
    assert organization.direct_member_ids(db, 1) == {3}
    assert organization.direct_member_ids(db, 2) == {1}
    assert organization.direct_member_ids(db, 2, include_overseen=True) == {1, 3}
    assert organization.groups_led_by_bulk(db, [1, 2]) == {1: [db.get(WorkGroup, 1)], 2: []}


@pytest.mark.parametrize("days,expected_score,expected_deduction", [
    ("0", "12.00", "0.00"), ("0.5", "9.75", "2.25"),
    ("1", "9.55", "2.45"), ("1.5", "9.30", "2.70"),
    ("22", "0.00", "11.90"), ("25", "0.00", "11.90"),
])
def test_single_and_bulk_attendance_share_scores_and_do_not_rewrite_reads(db, days, expected_score, expected_deduction):
    if Decimal(days):
        add_sick_leave(db, 1, "2026-10", days)
        add_sick_leave(db, 3, "2026-10", days)
    db.commit()
    single = attendance.recalculate_attendance(db, db.get(Employee, 1), "2026-10")
    db.commit()
    bulk = attendance.ensure_month_attendance(db, "2026-10", [3])[3]
    db.commit()
    fields = ("eligible", "actual_sick_days", "charged_sick_days", "base_score", "perfect_bonus", "sick_deduction", "final_score")
    assert [getattr(single, field) for field in fields] == [getattr(bulk, field) for field in fields]
    assert single.final_score == Decimal(expected_score)
    assert single.sick_deduction == Decimal(expected_deduction)
    calculated_at = single.calculated_at
    statements = []
    def capture(_conn, _cursor, statement, _params, _ctx, _many):
        statements.append(statement)
    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        assert attendance.recalculate_attendance(db, db.get(Employee, 1), "2026-10") is single
        assert attendance.ensure_month_attendance(db, "2026-10", [1])[1] is single
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert single.calculated_at == calculated_at
    assert not any(statement.lstrip().upper().startswith(("UPDATE", "INSERT")) for statement in statements)


@pytest.mark.parametrize("employee_id,terminated,loa", [(1, False, True), (1, True, False), (2, False, True)])
def test_ineligible_months_zero_out_single_and_bulk_attendance(db, employee_id, terminated, loa):
    employee = db.get(Employee, employee_id)
    if terminated:
        employee.terminated_on = "2026-10-31"
    if loa:
        db.add(EmployeeLOAPeriod(
            employee_id=employee_id, starts_on="2026-10-15", ends_on="2026-10-15",
            status="ended", created_by=2, created_by_name="主管",
        ))
    add_sick_leave(db, employee_id, "2026-10", "0.5")
    db.commit()
    bulk = attendance.ensure_month_attendance(db, "2026-10", [employee_id])[employee_id]
    db.commit()
    assert not bulk.eligible and bulk.final_score == 0
    before = (bulk.actual_sick_days, bulk.charged_sick_days, bulk.sick_deduction, bulk.final_score)
    single = attendance.recalculate_attendance(db, employee, "2026-10")
    assert before == (single.actual_sick_days, single.charged_sick_days, single.sick_deduction, single.final_score)
    if loa:
        assert attendance.full_month_loa(db, employee_id, "2026-10")
        assert (single.actual_sick_days, single.charged_sick_days) == (0, 0)


def test_supervisor_attendance_start_and_batch_query_strategy(db):
    assert SUPERVISOR_SCORING_START_MONTH == "2026-10"
    assert not attendance.attendance_scored_role(db.get(Role, 2), "2026-09")
    assert attendance.attendance_scored_role(db.get(Role, 2), "2026-10")
    assert 2 not in attendance.ensure_month_attendance(db, "2026-09", [2])
    statements = []
    def capture(_conn, _cursor, statement, _params, _ctx, _many):
        statements.append(statement)
    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        records = attendance.ensure_month_attendance(db, "2026-10", list(range(1, 7)))
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert set(records) == set(range(1, 7))
    assert sum("FROM employee_role_assignments" in sql for sql in statements) == 1
    assert sum("FROM sick_leave_records" in sql for sql in statements) == 1
    assert sum("INSERT OR IGNORE INTO attendance_monthly_scores" in sql for sql in statements) == 1


def test_audit_routes_employee_circle_and_leaves_commit_to_caller(db):
    employee = db.get(Employee, 1)
    audit.write_audit(db, employee, "人员变更", "employee", 1, before={"姓名": "原名"}, after={"姓名": "新名"})
    pending = next(row for row in db.new if isinstance(row, AuditLog))
    assert pending.attraction_id == 1
    assert pending.operator_name == employee.name and '新名' in pending.after_json
    assert audit.audit_attraction_id(db, "employee", "invalid") is None
    assert audit.audit_attraction_id(db, "settings", 1) is None
    db.flush()
    assert db.query(AuditLog).count() == 1
    db.rollback()
    assert db.query(AuditLog).count() == 0


def test_attachments_keep_type_detection_stream_limit_and_explicit_storage(db, storage_dir):
    png = b"\x89PNG\r\n\x1a\n" + b"example-payload"
    image = asyncio.run(attachments.save_image_upload(
        db, UploadFile(BytesIO(png), filename="misnamed.jpg"), 1,
        file_dir=storage_dir, persist=False,
    ))
    assert image.extension == ".png" and image.mime_type == "image/png"
    assert image.id is None and image not in db.new
    assert (storage_dir / image.storage_key).read_bytes() == png
    attachments.remove_upload_file(image, file_dir=storage_dir)
    assert not list(storage_dir.iterdir())
    assert attachments.BUSINESS_ATTACHMENT_MAX_BYTES == 100 * 1024 * 1024
    with pytest.raises(HTTPException, match="不能超过"):
        asyncio.run(attachments.save_upload(
            db, UploadFile(BytesIO(b"oversized"), filename="proof.pdf"), 1,
            file_dir=storage_dir, allowed_extensions={".pdf"}, max_bytes=4,
        ))
    assert not list(storage_dir.iterdir()) and db.query(StoredFile).count() == 0
    with pytest.raises(HTTPException, match="格式无效"):
        asyncio.run(attachments.save_image_upload(
            db, UploadFile(BytesIO(b"not-an-image"), filename="proof.png"), 1,
            file_dir=storage_dir,
        ))
    assert not list(storage_dir.iterdir())


def test_attachment_flush_failure_removes_file_and_path_guard_stays_inside_directory(storage_dir):
    class RejectingSession:
        def add(self, row):
            self.row = row
        def flush(self):
            raise RuntimeError("rejected flush")
    with pytest.raises(RuntimeError, match="rejected flush"):
        asyncio.run(attachments.save_upload(
            RejectingSession(), UploadFile(BytesIO(b"pdf-content"), filename="proof.pdf"), 1,
            file_dir=storage_dir, allowed_extensions={".pdf"},
        ))
    assert not list(storage_dir.iterdir())
    inside = storage_dir / "files"
    inside.mkdir()
    outside = storage_dir / "keep.pdf"
    outside.write_bytes(b"keep")
    attachments.remove_upload_file(StoredFile(storage_key="../keep.pdf"), file_dir=inside)
    assert outside.read_bytes() == b"keep"


def test_compatibility_attachment_boundary_honors_legacy_file_dir(db, storage_dir, monkeypatch):
    # The explicit wrapper is the existing injection boundary; the new service
    # receives its storage directory as a parameter and never reads this module.
    monkeypatch.setattr(v2_services, "FILE_DIR", storage_dir)
    record = asyncio.run(v2_services.save_upload(
        db, UploadFile(BytesIO(b"content"), filename="proof.pdf"), 1, allowed_extensions={".pdf"},
    ))
    assert (storage_dir / record.storage_key).read_bytes() == b"content"
    v2_services.remove_upload_file(record)
    assert not list(storage_dir.iterdir())
    assert v2_services.base_role_at is identity.base_role_at
    assert v2_services.recalculate_attendance is attendance.recalculate_attendance
    assert v2_services.full_month_loa is attendance.full_month_loa
    assert v2_services.write_audit is audit.write_audit


def test_role_expiry_preserves_base_supervisor_and_formal_group_leadership(db):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    db.add_all([
        WorkGroup(id=1, name="测试圈A组", code="A", attraction_id=1),
        GroupLeaderAssignment(group_id=1, leader_employee_id=2, starts_on="2020-01-01", leader_type="formal"),
        EmployeeActingDuty(employee_id=2, role_id=4, starts_on="2020-01-01", ends_on=yesterday),
        SystemAlert(alert_type="acting_duty_migration", dedupe_key="employee:2", employee_id=2, message="确认本职"),
    ])
    db.commit()
    role_lifecycle.process_role_expirations(db)
    assert db.query(EmployeeActingDuty).one().status == "ended"
    assert identity.base_role_at(db, 2).code == "SUPERVISOR"
    assert organization.active_group_leader(db, 1).leader_employee_id == 2
    assert db.get(WorkGroup, 1).status != "pending_takeover"
    alert = db.query(SystemAlert).filter_by(alert_type="acting_duty_migration").one()
    assert alert.status == "handled" and alert.handled_at is not None
