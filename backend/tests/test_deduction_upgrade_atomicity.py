"""Upgrade commands share one transaction across status, evidence and audit."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.routers import deductions
from app.v2_auth import V2User
from app.v2_database import Base
from app.v2_models import (
    Attraction,
    AuditLog,
    DeductionLevel,
    DeductionRecord,
    DeductionType,
    DeductionUpgradeRequest,
    DeductionUpgradeTransfer,
    Employee,
    EmployeeRoleAssignment,
    MonthClosure,
    Role,
    StoredFile,
    UserAccount,
)


REQUEST = Request({"type": "http", "method": "POST", "path": "/upgrade", "headers": [], "client": ("127.0.0.1", 1234)})


@pytest.fixture
def upgrade_db():
    # No app startup or global SessionLocal: every test owns a disposable DB.
    output_dir = Path(__file__).resolve().parents[1] / "output"
    output_dir.mkdir(exist_ok=True)
    # mkdir inherits workspace ACLs; mode=0700 temp directories on Windows
    # can make pytest/SQLite inaccessible to the sandbox's restricted token.
    test_dir = output_dir / f"deduction-upgrade-{uuid4().hex}"
    test_dir.mkdir()
    engine = create_engine(f"sqlite:///{(test_dir / 'upgrade.db').as_posix()}", connect_args={"check_same_thread": False, "timeout": 10})

    @event.listens_for(engine, "connect")
    def pragmas(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    with sessions() as db:
        role = Role(code="GSM", name="GSM", rank=30)
        circle = Attraction(name="test circle", employee_circle=True)
        kind = DeductionType(code="ATT_EARLY_CLOCK", name="考勤")
        statement = DeductionLevel(code="STATEMENT", name="声明", points=Decimal("1"))
        memo = DeductionLevel(code="MEMO", name="备忘录", points=Decimal("2"))
        db.add_all([role, circle, kind, statement, memo])
        db.flush()
        reviewers = [Employee(employee_no=f"GSM{i}", name=f"GSM{i}", attraction_id=circle.id) for i in range(3)]
        target = Employee(employee_no="CM1", name="CM1", attraction_id=circle.id)
        db.add_all([*reviewers, target])
        db.flush()
        for reviewer in reviewers:
            db.add(UserAccount(employee_id=reviewer.id, login_account=reviewer.employee_no, password_hash="unused", enabled=True))
            db.add(EmployeeRoleAssignment(employee_id=reviewer.id, role_id=role.id, starts_on="2020-01-01"))
        material = StoredFile(storage_key="test/source.pdf", original_filename="source.pdf", extension=".pdf", mime_type="application/pdf", file_size=1, sha256="0" * 64, uploaded_by=reviewers[0].id)
        db.add(material)
        db.flush()
        common = dict(employee_id=target.id, employee_no=target.employee_no, employee_name=target.name, employee_role_snapshot="CM", attraction_id_snapshot=circle.id, deduction_type_id=kind.id, deduction_type_name=kind.name, deduction_level_id=statement.id, deduction_level_name=statement.name, occurred_on="2099-10-09", deduction_month="2099-10", description="source", document_file_id=material.id, submitter_id=reviewers[0].id, submitter_name=reviewers[0].name, submitter_role_snapshot="GSM", permission_scope_snapshot="test", upgrade_state="pending")
        first = DeductionRecord(**common, points=Decimal("1"), status="active", upgrade_role="source_first")
        second = DeductionRecord(**common, points=Decimal("0"), status="pending_upgrade", upgrade_role="source_second")
        db.add_all([first, second])
        db.flush()
        upgrade = DeductionUpgradeRequest(employee_id=target.id, deduction_type_id=kind.id, first_deduction_id=first.id, second_deduction_id=second.id, reviewer_id=reviewers[0].id, reviewer_name=reviewers[0].name, submitted_by=reviewers[0].id, submitted_by_name=reviewers[0].name)
        db.add(upgrade)
        db.flush()
        first.upgrade_request_id = second.upgrade_request_id = upgrade.id
        ids = dict(request=upgrade.id, reviewer=reviewers[0].id, next_reviewer=reviewers[1].id, third_reviewer=reviewers[2].id, first=first.id, second=second.id, memo=memo.id, statement=statement.id, circle=circle.id)
        db.commit()
    try:
        yield sessions, ids
    finally:
        engine.dispose()


def user_for(db, reviewer_id):
    return V2User(db.get(Employee, reviewer_id), db.query(UserAccount).filter_by(employee_id=reviewer_id).one(), db.query(Role).filter_by(code="GSM").one(), {"DEDUCTION_ALL"})


def approval(ids):
    return {"decision": "approve", "result_level_id": ids["memo"], "handling_note": "已完成真实开具", "issued_confirmed": True}


def invoke(db, ids, command, reviewer_id=None):
    user = user_for(db, reviewer_id or ids["reviewer"])
    if command.startswith("transfer"):
        target_id = ids["third_reviewer"] if command == "transfer_third" else ids["next_reviewer"]
        return deductions.transfer_deduction_upgrade(ids["request"], {"reviewer_id": target_id, "reason": "交接"}, REQUEST, db, user)
    payload = approval(ids) if command == "approve" else {"decision": "reject", "handling_note": "审核不通过"}
    return deductions.resolve_deduction_upgrade(ids["request"], payload, REQUEST, db, user)


def race(sessions, ids, commands):
    barrier = Barrier(len(commands))

    def run(command):
        with sessions() as db:
            user = user_for(db, ids["reviewer"])
            cached = db.get(DeductionUpgradeRequest, ids["request"])
            assert cached.status == "pending"
            barrier.wait(timeout=5)
            try:
                if command.startswith("transfer"):
                    target = ids["third_reviewer"] if command == "transfer_third" else ids["next_reviewer"]
                    deductions.transfer_deduction_upgrade(ids["request"], {"reviewer_id": target, "reason": "交接"}, REQUEST, db, user)
                else:
                    payload = approval(ids) if command == "approve" else {"decision": "reject", "handling_note": "审核不通过"}
                    deductions.resolve_deduction_upgrade(ids["request"], payload, REQUEST, db, user)
                return command, 200
            except HTTPException as exc:
                assert not db.in_transaction(), "failed command must release its writer lock"
                return command, exc.status_code

    with ThreadPoolExecutor(max_workers=len(commands)) as executor:
        return list(executor.map(run, commands))


def assert_result(db, ids, status):
    row = db.get(DeductionUpgradeRequest, ids["request"])
    first, second = db.get(DeductionRecord, ids["first"]), db.get(DeductionRecord, ids["second"])
    assert row.status == status
    results = db.query(DeductionRecord).filter_by(upgrade_role="result").all()
    assert len(results) == (1 if status == "approved" else 0)
    assert db.query(StoredFile).count() == (2 if status == "approved" else 1)
    if status == "approved":
        assert row.result_deduction_id == results[0].id
        assert results[0].points == Decimal("2")
        assert results[0].status == "active"
        assert first.upgrade_state == "source_first"
        assert second.upgrade_state == "source_second"
        assert second.status == "active"
    elif status == "rejected":
        assert first.upgrade_request_id is None
        assert first.upgrade_state == "eligible"
        assert second.status == "void"
        assert second.upgrade_state == "rejected"
    else:
        assert first.upgrade_state == second.upgrade_state == "pending"
        assert second.status == "pending_upgrade"


def test_stale_pending_read_cannot_create_second_result(upgrade_db):
    sessions, ids = upgrade_db
    with sessions() as first_db, sessions() as stale_db:
        cached = stale_db.get(DeductionUpgradeRequest, ids["request"])
        assert cached.status == "pending"
        assert invoke(first_db, ids, "approve")["ok"]
        with pytest.raises(HTTPException) as error:
            invoke(stale_db, ids, "approve")
        assert error.value.status_code == 404
        assert not stale_db.in_transaction()
    with sessions() as db:
        assert_result(db, ids, "approved")
        assert db.query(AuditLog).count() == 1


@pytest.mark.parametrize("commands", [("approve", "approve"), ("approve", "reject"), ("reject", "reject")])
def test_concurrent_resolutions_commit_only_one_outcome(upgrade_db, commands):
    sessions, ids = upgrade_db
    outcomes = race(sessions, ids, commands)
    assert sorted(code for _, code in outcomes) == [200, 404]
    winner = next(command for command, code in outcomes if code == 200)
    with sessions() as db:
        assert_result(db, ids, "approved" if winner == "approve" else "rejected")
        assert db.query(AuditLog).count() == 1


@pytest.mark.parametrize("commands", [("transfer", "approve"), ("transfer", "reject"), ("transfer", "transfer_third")])
def test_transfer_and_resolution_share_current_assignee(upgrade_db, commands):
    sessions, ids = upgrade_db
    outcomes = race(sessions, ids, commands)
    assert sorted(code for _, code in outcomes) == [200, 404]
    winner = next(command for command, code in outcomes if code == 200)
    with sessions() as db:
        if winner.startswith("transfer"):
            assert_result(db, ids, "pending")
            current_id = ids["third_reviewer"] if winner == "transfer_third" else ids["next_reviewer"]
            assert db.get(DeductionUpgradeRequest, ids["request"]).reviewer_id == current_id
            assert db.query(DeductionUpgradeTransfer).count() == 1
            assert db.query(AuditLog).count() == 1
            assert invoke(db, ids, "approve", current_id)["ok"]
        else:
            assert_result(db, ids, "approved" if winner == "approve" else "rejected")
            assert db.query(DeductionUpgradeTransfer).count() == 0
            assert db.query(AuditLog).count() == 1


def test_cached_assignee_cannot_resolve_after_transfer(upgrade_db):
    sessions, ids = upgrade_db
    with sessions() as stale_db, sessions() as transfer_db:
        cached = stale_db.get(DeductionUpgradeRequest, ids["request"])
        assert cached.reviewer_id == ids["reviewer"]
        assert invoke(transfer_db, ids, "transfer")["ok"]
        with pytest.raises(HTTPException) as error:
            invoke(stale_db, ids, "approve")
        assert error.value.status_code == 404
        assert invoke(stale_db, ids, "approve", ids["next_reviewer"])["ok"]
    with sessions() as db:
        assert_result(db, ids, "approved")
        assert db.query(DeductionUpgradeTransfer).count() == 1
        assert db.query(AuditLog).count() == 2


@pytest.mark.parametrize("command", ["approve", "reject", "transfer"])
def test_command_failure_rolls_back_all_effects(upgrade_db, monkeypatch, command):
    sessions, ids = upgrade_db

    def failed_audit(*_args, **_kwargs):
        raise RuntimeError("audit persistence failed")

    with monkeypatch.context() as patch:
        patch.setattr(deductions, "write_audit", failed_audit)
        with sessions() as db:
            with pytest.raises(RuntimeError, match="audit persistence failed"):
                invoke(db, ids, command)
            assert not db.in_transaction()
    with sessions() as db:
        assert_result(db, ids, "pending")
        assert db.get(DeductionUpgradeRequest, ids["request"]).reviewer_id == ids["reviewer"]
        assert db.query(AuditLog).count() == 0
        assert db.query(DeductionUpgradeTransfer).count() == 0
        assert invoke(db, ids, command)["ok"]


def test_commit_failure_removes_result_and_placeholder(upgrade_db, monkeypatch):
    sessions, ids = upgrade_db
    with sessions() as db:
        def failed_commit():
            raise RuntimeError("commit persistence failed")

        with monkeypatch.context() as patch:
            patch.setattr(db, "commit", failed_commit)
            with pytest.raises(RuntimeError, match="commit persistence failed"):
                invoke(db, ids, "approve")
        assert not db.in_transaction()
    with sessions() as db:
        assert_result(db, ids, "pending")
        assert db.query(AuditLog).count() == 0
        assert invoke(db, ids, "approve")["ok"]


@pytest.mark.parametrize("failure", ["unconfirmed", "wrong_level", "closed_month"])
def test_validation_failure_leaves_request_pending_and_unlocks(upgrade_db, failure):
    sessions, ids = upgrade_db
    with sessions() as db:
        payload = approval(ids)
        expected = 400
        if failure == "unconfirmed":
            payload["issued_confirmed"] = False
        elif failure == "wrong_level":
            payload["result_level_id"] = ids["statement"]
        else:
            db.add(MonthClosure(closure_month="2099-10", attraction_id=ids["circle"], status="closed"))
            db.commit()
            expected = 423
        with pytest.raises(HTTPException) as error:
            deductions.resolve_deduction_upgrade(ids["request"], payload, REQUEST, db, user_for(db, ids["reviewer"]))
        assert error.value.status_code == expected
        assert not db.in_transaction()
    with sessions() as db:
        assert_result(db, ids, "pending")
        assert db.query(AuditLog).count() == 0
