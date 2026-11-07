"""Business and transaction coverage for the permanent activity-point ledger."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func

from test_supervisor_performance import app, SessionLocal, employee_id, login
from test_recognition_inbox import active_form, send
from app.activity_points_models import ActivityPointsEntry, RecognitionPointsState
from app.services import activity_points as points
from app.services.identity import TEMPORARY_RECOGNIZER_LABEL
from app.v2_models import Attraction, RecognitionRecord, RecognitionType


def record(db, signer="SUPERVISOR", target="CM", status="confirmed", **changes):
    category = db.query(RecognitionType).filter_by(code="SAFETY").one()
    circle = db.query(Attraction).filter_by(name="热力追踪").one()
    row = RecognitionRecord(employee_id=employee_id("CMTEST01"), employee_no="CMTEST01",
        employee_name="积点测试", employee_role_snapshot=target, employee_role_code_snapshot=target,
        occurred_attraction_id=1, recognition_date="2026-10-05", recognition_month="2026-10",
        recognition_type_id=category.id, recognition_type_name=category.name, content="积点测试",
        recognizer_employee_id=employee_id("SUPTEST01"), recognizer_name="签卡人",
        recognizer_role_snapshot=signer, recognizer_role_code_snapshot=signer,
        recognizer_base_role_code_snapshot=signer, operator_employee_id=employee_id("SUPTEST01"),
        operator_name="代录人", source="manager", fraction=Decimal("0.5"),
        credited_fraction=Decimal("0"), monthly_cap_status="capped_zero", status=status)
    row.home_attraction_id, row.home_attraction_name = circle.id, circle.name
    for key, value in changes.items():
        setattr(row, key, value)
    db.add(row)
    db.flush()
    return row


@pytest.fixture(autouse=True)
def initialized():
    with TestClient(app):
        yield


@pytest.mark.parametrize("signer,target,expected", [
    ("SUPERVISOR", "CM", 1), ("TA_SUPERVISOR", "TR", 1), ("GSM", "CM", 2),
    ("TA_GSM", "SUPERVISOR", 1), ("AM", "CM", 3), ("OM", "CM", 3),
    ("OM", "SUPERVISOR", 3), ("GSM", "GSM", 0), ("SUPERVISOR", "GSM", 0),
    ("HR_ADMIN", "CM", 0), ("GSM", "HR_CIRCLE", 0),
])
def test_levels_caps_and_hr(signer, target, expected):
    with SessionLocal() as db:
        row = record(db, signer, target)
        assert points.sync_recognition(db, row) == expected
        assert points.sync_recognition(db, row) == 0
        assert db.get(RecognitionPointsState, row.id).applied_points == expected
        assert row.credited_fraction == 0 and row.fraction == Decimal("0.5")
        db.rollback()


def test_temporary_permission_and_hr_proxy():
    with SessionLocal() as db:
        row = record(db, "CM", recognizer_role_snapshot=TEMPORARY_RECOGNIZER_LABEL,
                     operator_role_code_snapshot="HR_ADMIN")
        assert points.sync_recognition(db, row) == 1
        assert db.get(RecognitionPointsState, row.id).signer_code == "SUPERVISOR"
        row2 = record(db, "GSM", employee_acting_duty_code="TA_SUPERVISOR")
        assert points.sync_recognition(db, row2) == 2
        db.rollback()


def test_status_transitions_rollback_and_frozen_history():
    with SessionLocal() as db:
        row = record(db, "GSM", status="pending")
        assert points.sync_recognition(db, row) == 0
        row.status = "confirmed"
        assert points.sync_recognition(db, row) == 2
        row.status = "rejected"
        assert points.sync_recognition(db, row) == -2
        row.status = "confirmed"
        assert points.sync_recognition(db, row) == 2
        row.status = "void"
        assert points.sync_recognition(db, row) == -2
        entries = db.query(ActivityPointsEntry).filter_by(recognition_id=row.id).all()
        assert [e.delta for e in entries] == [2, -2, 2, -2]
        assert sum(e.delta for e in entries) == 0
        record_id = row.id
        db.rollback()
        assert db.get(RecognitionPointsState, record_id) is None


def test_backfill_preview_apply_repeat_and_stale_fingerprint():
    with SessionLocal() as db:
        row = record(db, "GSM")
        rejected = record(db, status="rejected")
        before = points.backfill_preview(db)
        assert db.get(RecognitionPointsState, row.id) is None
        points.backfill_apply(db, before["fingerprint"])
        assert db.get(RecognitionPointsState, row.id).applied_points == 2
        assert db.get(RecognitionPointsState, rejected.id).applied_points == 0
        count = db.query(ActivityPointsEntry).count()
        new_preview = points.backfill_preview(db)
        assert points.backfill_apply(db, new_preview["fingerprint"])["added_points"] == 0
        assert db.query(ActivityPointsEntry).count() == count
        row.status = "void"
        db.flush()
        with pytest.raises(HTTPException) as error:
            points.backfill_apply(db, new_preview["fingerprint"])
        assert error.value.status_code == 409
        db.rollback()


def test_missing_history_is_reported_and_old_awards_have_no_expiry():
    with SessionLocal() as db:
        row = record(db, employee_role_code_snapshot=None, recognition_date="1900-01-01", recognition_month="1900-01")
        preview = points.backfill_preview(db)
        assert row.id in [item["recognition_id"] for item in preview["pending_review"]]
        assert points.sync_recognition(db, row) == 0
        row.employee_role_code_snapshot = "CM"
        assert points.sync_recognition(db, row) == 0
        row.recognition_date, row.recognition_month = "2026-09-01", "2026-09"
        assert points.sync_recognition(db, row) == 1
        card = points.personal_card(db, row.employee_id)
        assert card["expires"] is False and card["redemption_enabled"] is True
        assert any(e["recognition_id"] == row.id for e in card["items"])
        db.rollback()


def test_fixed_award_and_self_recognition_do_not_create_points():
    with SessionLocal() as db:
        row = record(db, "GSM", scoring_policy_json=json.dumps({"mode": "fixed"}))
        assert points.sync_recognition(db, row) == 0
        row = record(db, "GSM", recognizer_employee_id=employee_id("CMTEST01"))
        assert points.sync_recognition(db, row) == 0
        db.rollback()


def test_live_api_card_privacy_and_withdrawal():
    with TestClient(app) as client:
        assert client.get("/api/activity-points/me").status_code == 401
        login(client, "CMTEST01")
        before = client.get("/api/activity-points/me").json()["available"]
        assert client.get("/api/activity-points/backfill-preview").status_code == 403
        assert client.post("/api/activity-points/backfill", json={"fingerprint": "0"*64}).status_code == 403
        login(client, "SUPTEST01")
        form = active_form(client, "CMTEST01", "SAFETY", "points-live-api")
        row = send(client, form)
        assert send(client, form)["id"] == row["id"]
        login(client, "CMTEST01")
        response = client.get("/api/activity-points/me")
        assert response.headers["Cache-Control"] == "private, no-store"
        assert response.json()["available"] == before + 1
        assert client.get("/api/activity-points/me?page=0").status_code == 422
        assert client.delete(f"/api/recognitions/{row['id']}").status_code == 200
        assert client.get("/api/activity-points/me").json()["available"] == before
        login(client, "GSMTEST01")
        assert all(item["recognition_id"] != row["id"] for item in client.get("/api/activity-points/me").json()["items"])


def test_administrator_backfill_api_and_excluded_card():
    with TestClient(app) as client:
        response = client.post('/api/login', json={"employee_no": "HR01", "password": "HR123"})
        assert response.status_code == 200
        assert client.get('/api/activity-points/me').json() == {"eligible": False}
        preview = client.get('/api/activity-points/backfill-preview')
        assert preview.status_code == 200, preview.text
        fingerprint = preview.json()['fingerprint']
        response = client.post('/api/activity-points/backfill', json={"fingerprint": fingerprint})
        assert response.status_code == 200, response.text
        preview = client.get('/api/activity-points/backfill-preview').json()
        response = client.post('/api/activity-points/backfill', json={"fingerprint": preview['fingerprint']})
        assert response.status_code == 200 and response.json()['added_points'] == 0


def test_historical_roles_are_resolved_on_business_date_and_then_frozen(monkeypatch):
    calls = []
    def historical(db, employee_id, day):
        calls.append(day)
        return SimpleNamespace(code="CM" if employee_id == employee_id_for_target else "GSM")
    employee_id_for_target = employee_id("CMTEST01")
    with SessionLocal() as db:
        row = record(db, recognizer_role_code_snapshot=None, recognizer_base_role_code_snapshot=None,
                     employee_role_code_snapshot=None)
        monkeypatch.setattr(points, "base_role_at", historical)
        monkeypatch.setattr(points, "role_at", historical)
        assert points.sync_recognition(db, row) == 2
        assert calls and set(calls) == {"2026-10-05"}
        monkeypatch.setattr(points, "base_role_at", lambda *args: SimpleNamespace(code="OM"))
        monkeypatch.setattr(points, "role_at", lambda *args: SimpleNamespace(code="OM"))
        assert points.sync_recognition(db, row) == 0
        assert db.get(RecognitionPointsState, row.id).applied_points == 2
        db.rollback()


def test_legacy_recipient_acting_snapshot_resolves_to_base_identity(monkeypatch):
    with SessionLocal() as db:
        row = record(db, "GSM", target="TA_SUPERVISOR")
        monkeypatch.setattr(points, "base_role_at", lambda *args: SimpleNamespace(code="CM"))
        assert points.sync_recognition(db, row) == 2
        db.rollback()


@pytest.mark.parametrize("circle,target,day,expected", [
    ("热力追踪", "CM", "2026-08-31", 0), ("热力追踪", "CM", "2026-09-01", 2),
    ("热力追踪", "TR", "2026-08-31", 0), ("热力追踪", "TR", "2026-09-01", 2),
    ("热力追踪", "SUPERVISOR", "2026-09-30", 0), ("热力追踪", "SUPERVISOR", "2026-10-01", 1),
    ("矮人迷宫", "CM", "2026-10-31", 0), ("矮人迷宫", "CM", "2026-11-01", 2),
    ("矮人迷宫", "SUPERVISOR", "2026-10-31", 0), ("矮人迷宫", "SUPERVISOR", "2026-11-01", 1),
    ("小熊罐子", "TR", "2026-10-31", 0), ("小熊罐子", "TR", "2026-11-01", 2),
    ("小熊罐子", "SUPERVISOR", "2026-10-31", 0), ("小熊罐子", "SUPERVISOR", "2026-11-01", 1),
])
def test_circle_and_population_start_dates(circle, target, day, expected):
    with SessionLocal() as db:
        home = db.query(Attraction).filter_by(name=circle).one()
        row = record(db, "GSM", target=target, recognition_date=day, recognition_month=day[:7],
                     home_attraction_id=home.id, home_attraction_name=home.name)
        assert points.sync_recognition(db, row) == expected
        assert points.sync_recognition(db, row) == 0
        assert db.get(RecognitionPointsState, row.id).applied_points == expected
        assert row.credited_fraction == 0
        db.rollback()


def test_start_dates_follow_recipient_snapshot_not_current_circle_or_venue():
    with SessionLocal() as db:
        home = db.query(Attraction).filter_by(name="矮人迷宫").one()
        row = record(db, "GSM", home_attraction_id=home.id, home_attraction_name=home.name)
        # CMTEST01 is currently in Heat Pursuit and the event venue is there too.
        assert points.sync_recognition(db, row) == 0
        assert db.get(RecognitionPointsState, row.id).reason == "before_start_date"
        row.recognition_date, row.recognition_month = "2026-11-01", "2026-11"
        assert points.sync_recognition(db, row) == 2
        db.rollback()


def test_temporary_signer_and_acting_recipient_keep_frontline_start_date():
    with SessionLocal() as db:
        temporary = record(db, "CM", recognizer_role_snapshot=TEMPORARY_RECOGNIZER_LABEL,
                           recognition_date="2026-09-01", recognition_month="2026-09")
        assert points.sync_recognition(db, temporary) == 1
        acting = record(db, "GSM", employee_acting_duty_code="TA_SUPERVISOR",
                        recognition_date="2026-09-01", recognition_month="2026-09")
        assert points.sync_recognition(db, acting) == 2
        db.rollback()


def test_missing_circle_is_reported_without_using_todays_circle():
    with SessionLocal() as db:
        row = record(db, "GSM", home_attraction_id=None, home_attraction_name=None)
        assert points.sync_recognition(db, row) == 0
        assert db.get(RecognitionPointsState, row.id).reason == "missing_circle_history"
        assert row.id in [r['recognition_id'] for r in points.backfill_preview(db)['pending_review']]
        row.home_attraction_name = "热力追踪"
        assert points.sync_recognition(db, row) == 2
        db.rollback()


def test_policy_correction_reverses_old_credit_once_and_keeps_pr_unchanged():
    with SessionLocal() as db:
        row = record(db, "GSM", recognition_date="2026-08-31", recognition_month="2026-08")
        db.add(RecognitionPointsState(recognition_id=row.id, employee_id=row.employee_id,
            revision=1, applied_points=2, entitlement=2, source_key=points.source_key(row),
            signer_code="GSM", recipient_code="CM", reason="eligible"))
        db.add(ActivityPointsEntry(employee_id=row.employee_id, recognition_id=row.id, revision=1,
            delta=2, kind="recognition", origin="historical_backfill", business_date=row.recognition_date, detail_json="{}"))
        db.flush()
        original_pr = row.fraction, row.credited_fraction, row.status
        preview = points.backfill_preview(db)
        points.backfill_apply(db, preview['fingerprint'])
        entries = db.query(ActivityPointsEntry).filter_by(recognition_id=row.id).order_by(ActivityPointsEntry.id).all()
        assert [e.delta for e in entries] == [2, -2]
        assert json.loads(entries[-1].detail_json)['reason'] == 'before_start_date'
        assert original_pr == (row.fraction, row.credited_fraction, row.status)
        repeat = points.backfill_preview(db)
        assert points.backfill_apply(db, repeat['fingerprint'])['added_points'] == 0
        assert db.query(ActivityPointsEntry).filter_by(recognition_id=row.id).count() == 2
        db.rollback()
