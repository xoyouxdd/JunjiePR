"""Original declaration upgrade and cross-month scoring on isolated SQLite."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from threading import Barrier

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from test_declaration_admin_corrections import setup, system
from app.database.connection import get_db
from app.routers import deductions
from app.routers.declaration_statistics import build_declaration_workbook
from app.services.monthly_ranks import sealed_self_score
from app.services.performance_details import load_monthly_records
from app.v2_auth import V2User, current_user, load_user_identity
from app.v2_models import (
    Attraction, AttendanceMonthlyScore, AuditLog, DeductionLevel, DeductionOriginalUpgrade, DeductionRecord,
    Employee, EmployeeMonthOrganizationSnapshot, EmployeeRoleAssignment, MonthClosure, Role, UserAccount,
)


@pytest.fixture
def scenario(setup, monkeypatch):
    db, client, holder, admin, _, _, source, _, _, target, _ = setup

    class DecisionClock(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 12, 2, 10, 30)

    monkeypatch.setattr(deductions, "datetime", DecisionClock)
    # Add real account/role assignments through the existing test fixture.
    # Its admin is in circle 0; the formal GSM works in circle 1.
    circle = db.query(Attraction).filter_by(name="段位测试圈2").one()
    db.add(UserAccount(employee_id=target.id, login_account=target.employee_no, password_hash="test"))
    db.commit()

    def operator(role_code, assignment_type="permanent"):
        account_no = role_code + assignment_type
        role = db.query(Role).filter_by(code=role_code).one()
        person = Employee(employee_no=account_no, name=account_no, attraction_id=circle.id,
                          is_active=True, hired_on="2026-01-01")
        db.add(person)
        db.flush()
        db.add(EmployeeRoleAssignment(employee_id=person.id, role_id=role.id, starts_on="2026-01-01",
                                      assignment_type=assignment_type, status="active"))
        account = UserAccount(employee_id=person.id, login_account=account_no, password_hash="test")
        db.add(account)
        db.commit()
        return load_user_identity(db, person, account)

    formal = operator("GSM")
    for actor in (operator("TA_GSM"), operator("SUPERVISOR"), operator("GSM", "temporary")):
        holder["user"] = actor
        denied = client.get(f"/api/deductions/{source.id}/original-upgrade")
        assert denied.status_code == 403, denied.text
    assert formal.employee.attraction_id != target.attraction_id
    holder["user"] = formal
    path = f"/api/deductions/{source.id}/original-upgrade"
    preview = client.get(path)
    assert preview.status_code == 200, preview.text
    assert preview.json()["effective_on"] == "2026-12-02"
    memo = db.query(DeductionLevel).filter_by(code="MEMO").one()
    body = {
        "level_id": memo.id, "confirmation": preview.json()["confirmation"],
        "mod_name": "MOD测试", "mod_confirmed_on": "2026-12-01", "reason": "线下核实原事件",
    }
    return db, client, formal, admin, source, path, body


def test_original_number_blocks_second_registration_and_cross_month_counts_once(scenario):
    db, client, formal, admin, source, path, body = scenario
    identity = (source.id, source.document_file_id, source.submitter_id, source.submitted_at, source.description)
    event_query = {"employee_id": source.employee_id, "occurred_on": source.occurred_on}
    check = client.get("/api/deductions/event-check", params=event_query)
    assert check.status_code == 200, check.text
    assert not check.json()["same_original"]
    assert [record["id"] for record in check.json()["records"]] == [source.id]
    registration = {
        **event_query, "deduction_type_id": source.deduction_type_id,
        "deduction_level_id": source.deduction_level_id, "description": "另一位登记人复查",
    }
    original_level_id = source.deduction_level_id
    scoring_circle = formal.employee.attraction_id
    db.add(EmployeeMonthOrganizationSnapshot(
        employee_id=source.employee_id, score_month="2026-11", attraction_id=scoring_circle,
    ))
    db.commit()
    for month, circle_id in (
        ("2026-11", source.attraction_id_snapshot), ("2026-12", source.attraction_id_snapshot), ("2026-11", scoring_circle),
    ):
        closure = MonthClosure(closure_month=month, attraction_id=circle_id, status="closed", closed_by=admin.id)
        db.add(closure)
        db.commit()
        response = client.post(path, json=body)
        assert response.status_code == 423, response.text
        db.refresh(source)
        assert source.deduction_month == "2026-11" and source.points == 1
        assert db.get(DeductionOriginalUpgrade, source.id) is None
        db.delete(closure)
        db.commit()

    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    db.refresh(source)
    assert source.deduction_month == "2026-12" and source.occurred_on == "2026-12-02"
    assert source.incident_on == event_query["occurred_on"]
    assert (source.id, source.document_file_id, source.submitter_id, source.submitted_at, source.description) == identity
    assert db.query(DeductionRecord).count() == 1
    assert db.query(AuditLog).filter_by(action="原单升级").count() == 1

    for month, level_id, record_count, score in (
        ("2026-11", original_level_id, 0, 0), ("2026-12", body["level_id"], 1, 3)
    ):
        response = client.get("/api/declaration-statistics", params={
            "month": month, "level_id": level_id, "keyword": f"#{source.id}", "status": "current",
        })
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total_records"] == record_count
        assert len(data["detail_records"]) == 1
        detail = data["detail_records"][0]
        assert detail["id"] == source.id and detail["actual_points"] == score
        assert detail["original_upgrade"]["mod_name"] == body["mod_name"]
        assert detail["original_month_history"] == (record_count == 0)
        assert detail["document_url"].endswith(f"/{source.document_file_id}")
        assert build_declaration_workbook(data)["逐条明细"].max_row == record_count + 1
        monthly = load_monthly_records(db, month, [source.employee_id], include_attendance=False)
        assert sum(record.points for record in monthly.deductions) == score

    refreshed = client.get("/api/deductions/event-check", params=event_query).json()
    assert [row["id"] for row in refreshed["records"]] == [source.id]
    exact = client.get("/api/deductions/event-check", params={**event_query, "original_statement_id": source.id})
    assert exact.json()["same_original"]
    for route in ("/api/deductions", "/api/deduction-upgrades"):
        response = client.post(route, data={
            **registration, "original_statement_id": source.id, "reviewer_id": formal.id,
            "occurred_on": "2026-12-03",
        })
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "SAME_DEDUCTION_EVENT"
    assert db.query(DeductionRecord).count() == 1
    stale = client.post("/api/deductions", data={
        **registration, "event_confirmation": check.json()["confirmation"],
        "different_event_reason": "现场另一起安全事件",
    })
    assert stale.status_code == 409, stale.text
    unconfirmed = client.post("/api/deductions", data={**registration, "event_confirmation": refreshed["confirmation"]})
    assert unconfirmed.status_code == 409, unconfirmed.text
    assert db.query(DeductionRecord).count() == 1
    different = client.post("/api/deductions", data={
        **registration, "event_confirmation": refreshed["confirmation"], "different_event_reason": "现场另一起安全事件",
    })
    assert different.status_code == 200, different.text
    assert db.query(DeductionRecord).count() == 2
    assert db.query(AuditLog).filter(AuditLog.after_json.contains("现场另一起安全事件")).count() == 1


def test_reopen_upgrade_and_reclose_preserves_other_employees_scores(scenario, system, monkeypatch):
    db, client, formal, admin, source, upgrade_path, upgrade_body = scenario
    _, _, _, roles, make_employee, _, holder, _ = system

    class MonthCloseClock(date):
        @classmethod
        def today(cls):
            return cls(2026, 12, 2)

    monkeypatch.setattr("app.month_closure.date", MonthCloseClock)
    month = source.deduction_month
    original_id, material_id = source.id, source.document_file_id
    peer = make_employee("同圈员工")
    db.add(AttendanceMonthlyScore(employee_id=peer.id, attendance_month=month, eligible=True, final_score=72))
    db.commit()
    scope = {"attraction_id": source.attraction_id_snapshot}
    close_path = f"/api/month-closes/{month}"
    administrator = V2User(admin, None, roles["SYSTEM_ADMIN"], {"SYSTEM_ADMIN"})
    holder["user"] = administrator

    closed = client.post(f"{close_path}/close", json={**scope, "reason": "原月结算"})
    assert closed.status_code == 200, closed.text
    before = sealed_self_score(db, month, source.employee_id)
    peer_before = sealed_self_score(db, month, peer.id)
    assert before.deduction_score == 1
    peer_total = peer_before.total_score
    assert peer_total == 72

    holder["user"] = formal
    blocked = client.get(upgrade_path)
    assert blocked.status_code == 423, blocked.text
    holder["user"] = administrator
    reopened = client.post(f"{close_path}/reopen", json={**scope, "reason": "更正原声明"})
    assert reopened.status_code == 200, reopened.text
    assert sealed_self_score(db, month, peer.id) is None
    assert db.query(AttendanceMonthlyScore).filter_by(employee_id=peer.id, attendance_month=month).one().final_score == 72

    holder["user"] = formal
    preview = client.get(upgrade_path)
    assert preview.status_code == 200, preview.text
    upgraded = client.post(upgrade_path, json={
        **upgrade_body, "confirmation": preview.json()["confirmation"],
    })
    assert upgraded.status_code == 200, upgraded.text
    holder["user"] = administrator
    reclosed = client.post(f"{close_path}/close", json={**scope, "reason": "原单升级后重新结算"})
    assert reclosed.status_code == 200, reclosed.text
    assert sealed_self_score(db, month, peer.id).total_score == peer_total
    assert sealed_self_score(db, month, source.employee_id).deduction_score == 0
    scored = load_monthly_records(db, "2026-12", [source.employee_id], include_attendance=False).deductions
    assert [(item.id, item.document_file_id, item.points) for item in scored] == [(original_id, material_id, 3)]


def test_two_concurrent_confirmations_create_one_result(scenario, tmp_path):
    source_db, _, formal, _, source, route, body = scenario
    actor_id, account_id, record_id = formal.id, formal.account.id, source.id
    source_db.rollback()
    engine = create_engine(f"sqlite:///{tmp_path / 'two-confirmations.sqlite'}", connect_args={"timeout": 10})
    original, copy = source_db.get_bind().raw_connection(), engine.raw_connection()
    try:
        original.driver_connection.backup(copy.driver_connection)
    finally:
        copy.close()
        original.close()
    sessions = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(deductions.router, prefix="/api")

    def session_dependency():
        with sessions() as session:
            yield session

    def current_operator():
        with sessions() as session:
            return load_user_identity(session, session.get(Employee, actor_id), session.get(UserAccount, account_id))

    app.dependency_overrides[get_db] = session_dependency
    app.dependency_overrides[current_user] = current_operator
    ready = Barrier(2)

    def confirm():
        with TestClient(app) as client:
            ready.wait(timeout=10)
            response = client.post(route, json=body)
            return response.status_code

    try:
        with ThreadPoolExecutor(max_workers=2) as threads:
            jobs = [threads.submit(confirm) for _ in range(2)]
            assert sorted(job.result(timeout=20) for job in jobs) == [200, 409]
        with sessions() as db:
            assert db.query(DeductionRecord).count() == 1
            assert db.query(DeductionOriginalUpgrade).count() == 1
            assert db.get(DeductionRecord, record_id).points == 3
            assert db.query(AuditLog).filter_by(action="原单升级").count() == 1
    finally:
        engine.dispose()
