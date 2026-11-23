"""Original declaration upgrade and cross-month scoring on isolated SQLite."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from threading import Barrier

import fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from test_declaration_admin_corrections import setup, system
from app import deduction_materials
from app.database.connection import get_db
from app.deduction_policy import deduction_counts_for_score
from app.routers import deductions
from app.routers.declaration_statistics import build_declaration_workbook
from app.services.monthly_ranks import sealed_self_score
from app.services.performance_details import load_monthly_records
from app.v2_auth import V2User, current_user, load_user_identity
from app.v2_models import (
    Attraction, AttendanceMonthlyScore, AuditLog, DeductionLevel, DeductionMaterialJob, DeductionOriginalUpgrade,
    DeductionRecord, DeductionType, DeductionUpgradeRequest,
    Employee, EmployeeMonthOrganizationSnapshot, EmployeeRoleAssignment, MonthClosure, Role, StoredFile, UserAccount,
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


def test_formal_gsm_cross_circle_correction_and_intern_snapshot(scenario, system, monkeypatch):
    db, client, formal, admin, source, upgrade_path, upgrade_body = scenario
    _, _, _, roles, _, _, holder, _ = system
    monkeypatch.setattr(deduction_materials, "FILE_DIR", deductions.FILE_DIR)
    employee = db.get(Employee, source.employee_id)
    employee.employee_type = "Intern"
    db.commit()
    levels = {level.code: level.id for level in db.query(DeductionLevel)}
    safety = db.query(DeductionType).filter_by(code="SAFETY").one()
    registration = {
        "employee_id": employee.id, "deduction_type_id": safety.id, "deduction_level_id": levels["STATEMENT"],
        "occurred_on": "2026-11-24", "description": "原实习期安全事件",
    }

    def register(description):
        with fitz.open() as document:
            document.new_page().insert_text((30, 30), "Isolated record")
            evidence = document.tobytes()
        result = client.post("/api/deductions", data={**registration, "description": description},
                             files={"document": ("record.pdf", evidence, "application/pdf")})
        assert result.status_code == 200, result.text
        return db.get(DeductionRecord, result.json()["record"]["id"])

    supervisor = db.query(UserAccount).filter_by(login_account="SUPERVISORpermanent").one()
    holder["user"] = load_user_identity(db, db.get(Employee, supervisor.employee_id), supervisor)
    record = register(registration["description"])
    assert record.submitter_id != formal.id
    assert record.employee_type_snapshot == "Intern"
    original_file = record.document_file_id
    # The snapshot survives a real change in the directory.
    employee.employee_type = "Comp40"
    db.commit()
    path = f"/api/deductions/{record.id}"
    holder["user"] = V2User(formal.employee, formal.account, roles["TA_GSM"], set(formal.permissions))
    assert client.get(path + "/correction-preview", params={"action": "level"}).status_code == 403
    assert client.patch(path + "/level", json={}).status_code == 403
    assert client.post(f"/api/deductions/{source.id}/void", json={"reason": "不授权"}).status_code == 403
    holder["user"] = formal

    def change_request(next_level):
        response = client.get(path + "/correction-preview", params={"action": "level"})
        assert response.status_code == 200, response.text
        preview = response.json()
        return preview, {
            "level_id": levels[next_level], "confirmation": preview["confirmation"], "reason": "更正录入等级",
            "expected_level_id": preview["level_id"], "expected_type_id": preview["type_id"],
            "expected_status": preview["status"],
        }

    label = "备忘录（参考全职一级警告）"
    preview, body = change_request("WARNING_1")
    assert [(level["name"], level["actual_points"]) for level in preview["levels"]] == [
        ("声明", 1), ("备忘录", 3), (label, 5),
    ]
    closure = MonthClosure(closure_month="2026-11", attraction_id=record.attraction_id_snapshot,
                           status="closed", closed_by=admin.id)
    db.add(closure)
    db.commit()
    assert client.patch(path + "/level", json=body).status_code == 423
    db.delete(closure)
    db.commit()
    excluded = client.patch(path + "/level", json={**body, "level_id": levels["WARNING_2"]})
    assert excluded.status_code == 403, excluded.text
    for next_level, points, name in (("WARNING_1", 5, label), ("STATEMENT", 1, "声明"), ("MEMO", 3, "备忘录"), ("WARNING_1", 5, label)):
        _, batch = change_request(next_level)
        response = client.patch(path + "/level", json=batch)
        assert response.status_code == 200, response.text
        db.refresh(record)
        assert (record.points, record.deduction_level_name, record.deduction_month, record.document_file_id) == (
            points, name, "2026-11", original_file,
        )
    data = client.get("/api/declaration-statistics", params={"month": "2026-11", "keyword": f"#{record.id}"}).json()
    assert data["detail_records"][0]["can_gsm_correct"]
    assert data["detail_records"][0]["level_name"] == label
    assert build_declaration_workbook(data)["逐条明细"].cell(2, 6).value == label
    db.refresh(source)
    assert source.employee_type_snapshot is None

    # After the one-time cross-month workflow, ordinary GSM actions are unavailable.
    upgraded = client.post(upgrade_path, json=upgrade_body)
    assert upgraded.status_code == 200, upgraded.text
    assert client.get(upgrade_path.replace("original-upgrade", "correction-preview"), params={"action": "level"}).status_code == 409
    assert client.post(upgrade_path.replace("original-upgrade", "void"), json={"reason": "普通作废"}).status_code == 409


def test_formal_gsm_cannot_correct_void_or_restore_own_deduction(scenario, system):
    db, client, formal, admin, row, _, _ = scenario
    _, _, _, roles, _, _, holder, _ = system
    row.employee_id, row.employee_role_snapshot = formal.id, "SUPERVISOR"
    db.commit()
    path = f"/api/deductions/{row.id}"
    preview = client.get(path + "/correction-preview", params={"action": "level"})
    assert preview.status_code == 403, preview.text
    assert client.patch(path + "/level", json={"reason": "更正本人历史记录"}).status_code == 403
    assert client.post(path + "/void", json={"reason": "作废本人历史记录"}).status_code == 403
    # The existing submitter rule must not bypass the self-record guard.
    row.submitter_id = formal.id
    db.commit()
    assert client.post(path + "/void", json={"reason": "作废本人登记"}).status_code == 403
    data = client.get("/api/declaration-statistics", params={"month": row.deduction_month, "keyword": f"#{row.id}"}).json()
    assert data["detail_records"][0]["can_gsm_correct"] is False
    assert row.status == "active"

    holder["user"] = V2User(admin, None, roles["SYSTEM_ADMIN"], {"SYSTEM_ADMIN"})
    assert client.post(path + "/void", json={"reason": "管理员核对作废"}).status_code == 200
    db.refresh(row)
    holder["user"] = formal
    assert client.get(path + "/correction-preview", params={"action": "restore"}).status_code == 403
    assert client.post(path + "/restore", json={
        "reason": "还原本人记录", "expected_voided_at": row.voided_at.isoformat(),
        "expected_type_id": row.deduction_type_id, "expected_level_id": row.deduction_level_id,
    }).status_code == 403


def test_gsm_restore_checks_same_description_with_different_evidence(scenario, system, monkeypatch):
    db, client, formal, admin, row, _, _ = scenario
    _, _, _, _, _, _, holder, _ = system
    monkeypatch.setattr(deductions, "datetime", datetime)
    monkeypatch.setattr(deduction_materials, "FILE_DIR", deductions.FILE_DIR)
    safety = db.query(DeductionType).filter_by(code="SAFETY").one()
    row.deduction_type_id = safety.id
    row.description = "例行检查"
    db.commit()
    assert row.submitter_id != formal.id
    path = f"/api/deductions/{row.id}"
    holder["user"] = formal
    assert client.post(path + "/void", json={"reason": "核对原事件"}).status_code == 200
    db.refresh(row)
    body = {
        "reason": "还原原单", "expected_voided_at": row.voided_at.isoformat(),
        "expected_type_id": row.deduction_type_id, "expected_level_id": row.deduction_level_id,
    }
    preview = client.get(path + "/correction-preview", params={"action": "restore"}).json()
    assert preview["records"] == []
    with fitz.open() as document:
        document.new_page().insert_text((30, 30), "Evidence for a different incident")
        evidence = document.tobytes()
    distinct = client.post("/api/deductions", data={
        "employee_id": row.employee_id, "deduction_type_id": row.deduction_type_id, "deduction_level_id": row.deduction_level_id,
        "occurred_on": row.occurred_on, "description": row.description,
    }, files={"document": ("distinct.pdf", evidence, "application/pdf")})
    assert distinct.status_code == 200, distinct.text
    assert client.post(path + "/restore", json={**body, "event_confirmation": preview["confirmation"]}).status_code == 409
    preview = client.get(path + "/correction-preview", params={"action": "restore"}).json()
    assert [event["id"] for event in preview["records"]] == [distinct.json()["record"]["id"]]
    confirmed = {**body, "event_confirmation": preview["confirmation"], "different_event_reason": "同日两个独立事件"}
    closure = MonthClosure(closure_month=row.deduction_month, attraction_id=row.attraction_id_snapshot,
                           status="closed", closed_by=admin.id)
    db.add(closure)
    db.commit()
    assert client.post(path + "/restore", json=confirmed).status_code == 423
    db.delete(closure)
    db.commit()
    restored = client.post(path + "/restore", json=confirmed)
    assert restored.status_code == 200, restored.text
    assert db.query(AuditLog).filter_by(action="还原扣分").count() == 1


@pytest.fixture
def restore_material_case(scenario, monkeypatch):
    db, client, _, _, row, _, _ = scenario
    monkeypatch.setattr(deductions, "datetime", datetime)
    monkeypatch.setattr(deduction_materials, "FILE_DIR", deductions.FILE_DIR)
    monkeypatch.setattr(deduction_materials, "SessionLocal", sessionmaker(bind=db.get_bind()))
    safety = db.query(DeductionType).filter_by(code="SAFETY").one()
    row.deduction_type_id = safety.id
    db.commit()
    original_file = db.get(StoredFile, row.document_file_id)
    original_pdf = (deductions.FILE_DIR / original_file.storage_key).read_bytes()
    return db, client, row, original_pdf, original_file.sha256


@pytest.mark.parametrize("different_date", [False, True])
def test_gsm_restore_rejects_same_material_added_to_an_earlier_draft(restore_material_case, different_date):
    db, client, row, original_pdf, original_hash = restore_material_case
    path = f"/api/deductions/{row.id}"
    duplicate = client.post("/api/deductions", data={
        "employee_id": row.employee_id, "deduction_type_id": row.deduction_type_id, "deduction_level_id": row.deduction_level_id,
        "occurred_on": "2026-11-02" if different_date else row.occurred_on, "description": "相同事件改写了说明",
    })
    assert duplicate.status_code == 200, duplicate.text
    duplicate_id = duplicate.json()["record"]["id"]
    candidate = db.get(DeductionRecord, duplicate_id)
    assert candidate.status == "pending_material"
    assert client.post(path + "/void", json={"reason": "重核原单"}).status_code == 200
    db.refresh(row)
    assert candidate.submitted_at < row.voided_at
    attached = client.post(f"/api/deductions/{duplicate_id}/material",
                           files={"document": ("same.pdf", original_pdf, "application/pdf")})
    assert attached.status_code == 200, attached.text
    db.rollback()
    assert deduction_materials.process_next_deduction_material_job()
    db.refresh(candidate)
    assert candidate.status == "active"
    assert db.get(StoredFile, candidate.document_file_id).sha256 != original_hash

    preview = client.get(path + "/correction-preview", params={"action": "restore"})
    assert preview.status_code == 409 and f"#{duplicate_id}" in preview.json()["detail"]
    conflict = client.post(path + "/restore", json={
        "reason": "还原原单", "expected_voided_at": row.voided_at.isoformat(),
        "expected_type_id": row.deduction_type_id, "expected_level_id": row.deduction_level_id,
    })
    assert conflict.status_code == 409, conflict.text
    assert f"#{duplicate_id}" in conflict.json()["detail"]
    db.refresh(row)
    assert row.status == "void"


def test_material_worker_rejects_same_pdf_after_original_is_restored(restore_material_case):
    db, client, row, original_pdf, _ = restore_material_case
    path = f"/api/deductions/{row.id}"
    draft = client.post("/api/deductions", data={
        "employee_id": row.employee_id, "deduction_type_id": row.deduction_type_id, "deduction_level_id": row.deduction_level_id,
        "occurred_on": row.occurred_on, "description": "暂待材料核对",
    })
    assert draft.status_code == 200, draft.text
    draft_id = draft.json()["record"]["id"]
    assert client.post(path + "/void", json={"reason": "重核原单"}).status_code == 200
    db.refresh(row)
    preview = client.get(path + "/correction-preview", params={"action": "restore"})
    assert preview.status_code == 200, preview.text
    assert [item["id"] for item in preview.json()["records"]] == [draft_id]
    restored = client.post(path + "/restore", json={
        "reason": "还原原单", "expected_voided_at": row.voided_at.isoformat(),
        "expected_type_id": row.deduction_type_id, "expected_level_id": row.deduction_level_id,
        "event_confirmation": preview.json()["confirmation"], "different_event_reason": "据初步信息是不同事件",
    })
    assert restored.status_code == 200, restored.text
    attached = client.post(f"/api/deductions/{draft_id}/material",
                           files={"document": ("same.pdf", original_pdf, "application/pdf")})
    assert attached.status_code == 200, attached.text
    db.rollback()
    assert deduction_materials.process_next_deduction_material_job()
    candidate = db.get(DeductionRecord, draft_id)
    assert candidate.status == "material_failed"
    assert f"#{row.id}" in candidate.material_error
    assert db.get(DeductionMaterialJob, candidate.material_job_id).error_code == "duplicate_event_material"
    assert db.query(DeductionRecord.id).filter_by(employee_id=row.employee_id, status="active").all() == [(row.id,)]


def test_direct_pdf_rejects_evidence_from_a_restored_record(restore_material_case):
    db, client, row, original_pdf, _ = restore_material_case
    path = f"/api/deductions/{row.id}"
    assert client.post(path + "/void", json={"reason": "重核原单"}).status_code == 200
    db.refresh(row)
    restored = client.post(path + "/restore", json={
        "reason": "还原原单", "expected_voided_at": row.voided_at.isoformat(),
        "expected_type_id": row.deduction_type_id, "expected_level_id": row.deduction_level_id,
    })
    assert restored.status_code == 200, restored.text
    duplicate = client.post("/api/deductions", data={
        "employee_id": row.employee_id, "deduction_type_id": row.deduction_type_id, "deduction_level_id": row.deduction_level_id,
        "occurred_on": "2026-11-02", "description": "用不同日期重复登记",
    }, files={"document": ("same.pdf", original_pdf, "application/pdf")})
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["detail"]["record"]["id"] == row.id
    assert db.query(DeductionRecord.id).filter_by(employee_id=row.employee_id).all() == [(row.id,)]


@pytest.mark.parametrize("supplement", [False, True])
def test_pair_approval_rejects_evidence_from_a_restored_record(restore_material_case, scenario, supplement):
    db, client, original, original_pdf, original_hash = restore_material_case
    manager = scenario[2]
    path = f"/api/deductions/{original.id}"
    assert client.post(path + "/void", json={"reason": "重核原单"}).status_code == 200
    db.refresh(original)
    restored = client.post(path + "/restore", json={
        "reason": "还原原单", "expected_voided_at": original.voided_at.isoformat(),
        "expected_type_id": original.deduction_type_id, "expected_level_id": original.deduction_level_id,
    })
    assert restored.status_code == 200, restored.text

    attendance = db.query(DeductionType).filter_by(code="SICK_LEAVE_VIOLATION").one()
    memo = db.query(DeductionLevel).filter_by(code="MEMO").one()
    statement = {
        "employee_id": original.employee_id, "deduction_type_id": attendance.id,
        "deduction_level_id": original.deduction_level_id, "occurred_on": "2026-11-05",
        "description": "独立的首次声明",
    }
    with fitz.open() as document:
        document.new_page().insert_text((30, 30), "Independent case")
        evidence = document.tobytes()
    first = client.post("/api/deductions", data=statement, files={"document": ("first.pdf", evidence, "application/pdf")})
    assert first.status_code == 200, first.text
    first_id = first.json()["record"]["id"]
    second = {**statement, "occurred_on": "2026-11-10", "description": "日期不同的第二张声明"}
    duplicate = {"document": ("same.pdf", original_pdf, "application/pdf")}
    if supplement:
        pending = client.post("/api/deductions", data=second)
        assert pending.status_code == 200, pending.text
        second_id = pending.json()["record"]["id"]
        submitted = client.post(f"/api/deductions/{second_id}/material",
                                data={"reviewer_id": manager.id, "expected_first_id": first_id}, files=duplicate)
        assert submitted.status_code == 200, submitted.text
        db.rollback()
        assert deduction_materials.process_next_deduction_material_job()
        second_row = db.get(DeductionRecord, second_id)
        assert db.get(StoredFile, second_row.document_file_id).sha256 != original_hash
    else:
        submitted = client.post("/api/deduction-upgrades", data={**second, "reviewer_id": manager.id}, files=duplicate)
        assert submitted.status_code == 200, submitted.text
        second_id = submitted.json()["request"]["second_record"]["id"]

    work = db.query(DeductionUpgradeRequest).filter_by(second_deduction_id=second_id).one()
    work_path = f"/api/deduction-upgrades/{work.id}/resolve"
    approval = client.post(work_path, json={
        "decision": "approve", "result_level_id": memo.id, "issued_confirmed": True, "handling_note": "核对处分材料",
    })
    assert approval.status_code == 409, approval.text
    assert approval.json()["detail"]["record_id"] == original.id
    db.refresh(work)
    assert work.status == "pending" and work.result_deduction_id is None
    assert db.get(DeductionRecord, second_id).status == "pending_upgrade"
    assert not db.query(AuditLog).filter_by(action="处理声明升级工单", entity_id=str(work.id)).count()
    counted_ids = {row.id for row in db.query(DeductionRecord).filter_by(employee_id=original.employee_id)
                   if deduction_counts_for_score(row)}
    assert counted_ids == {original.id, first_id}
    rejected = client.post(work_path, json={"decision": "reject", "handling_note": "第二张材料重复"})
    assert rejected.status_code == 200, rejected.text


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
