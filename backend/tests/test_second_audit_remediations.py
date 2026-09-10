import os
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-second-audit-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from app.main import app
from app.score_queries import employee_month_scores
from app.v2_crypto import hash_password
from app.v2_database import SessionLocal, seed_reference_data
from app.v2_models import (
    AuditLog,
    DeductionLevel,
    DeductionRecord,
    DeductionType,
    Employee,
    RecognitionScoreRule,
    Role,
    StoredFile,
    SystemAlert,
    UserAccount,
)
from app.v2_services import recognition_score_for_role


ROOT = Path(__file__).resolve().parents[1]


def test_render_navigation_cancels_stale_read_requests_without_recursive_redraw() -> None:
    script = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "let renderAbortController=null;" in script
    assert "renderAbortController?.abort();" in script
    assert "renderAbortController=controller;" in script
    assert "requestOptions.signal=renderAbortController.signal" in script
    assert "clearPageResources();" in script
    assert "pageTimeout(poll,1500)" in script
    assert "if(generation!==renderGeneration) return render();" not in script


def test_manager_recognition_can_submit_without_a_self_evidence_control() -> None:
    script = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "function recognitionSubmissionData(form,imageRequired=true)" in script
    assert "if(imageRequired&&!file)" in script
    assert "recognitionSubmissionData(e.target,has('SELF_RECOGNITION')&&!has('EMPLOYEE_ADD'))" in script


def test_hr_batch_save_button_has_a_stable_template_contract() -> None:
    script = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert '<button type="button" id="hrBatchLeaderSave" class="primary">' in script
    assert "hrBatchSave.id='hrBatchLeaderSave'" not in script


def test_score_rule_ui_describes_global_scope_and_is_read_only_for_circle_hr() -> None:
    script = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "const canEdit=state.me.role_code==='SYSTEM_ADMIN'" in script
    assert "这是全系统统一分值规则" in script
    assert "本页面为只读" in script
    assert 'type="number" min="0" step="0.01"' in script


def test_required_recognition_fields_are_not_hidden_as_more_options() -> None:
    script = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")

    recognition_panel = script[script.index("const recognitionPanel="):script.index("const recognitionSection=")]
    assert 'name="occurred_attraction_id"' in recognition_panel
    assert 'name="recognizer_employee_id"' in recognition_panel
    assert '<details class="form-more">' not in recognition_panel


def test_mobile_review_and_material_status_keep_decision_fields_distinct() -> None:
    script = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "function recognitionScoreText(r)" in script
    assert "认可人：${esc(r.recognizer_name)} · ${recognitionScoreText(r)}" in script
    assert "签卡人：${esc(row.recognizer_name)}" in script
    assert "attachmentControl(row.image_url,'查看材料'" in script
    assert ".filter(row=>!row.dedicated_entry)" in script
    assert "businessActive=businessStatus==='active'" in script
    assert "是否计分以业务状态为准" in script
    assert "if(error?.name==='AbortError'||!container?.isConnected)return" in script


def test_mobile_fixed_actions_and_motion_preferences_have_shared_rules() -> None:
    css = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    script = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert ".hr-batch-leader-bar { bottom: calc(64px + env(safe-area-inset-bottom))" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "behavior:prefersReducedMotion()?'auto':'smooth'" in script


def test_role_navigation_only_exposes_supported_month_close_and_statistics_entries() -> None:
    script = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "else if (r==='HR_CIRCLE')" in script
    assert "else if (r==='HR_ADMIN')" in script
    hr_admin_branch = script[script.index("else if (r==='HR_ADMIN')"):script.index("else if (has('DATA_VIEW'))")]
    assert "monthClose" not in hr_admin_branch
    assert "if(has('DATA_VIEW')&&!items.some(([id])=>id==='statistics'))" in script


def _login(client: TestClient, account: str = "TATEST01") -> None:
    response = client.post("/api/login", json={"employee_no": account, "password": "1234"})
    assert response.status_code == 200, response.text


def _image_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (8, 8), "white").save(output, format="PNG")
    return output.getvalue()


def _pdf_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (20, 30), "white").save(output, format="PDF")
    return output.getvalue()


def test_recognition_deduction_and_sick_leave_replays_bind_business_payloads() -> None:
    with TestClient(app) as client:
        _login(client)
        options = client.get("/api/options").json()
        target = next(
            row for row in client.get("/api/employee-targets", params={"usage": "recognition", "keyword": "CMTEST01"}).json()["items"]
            if row["employee_no"] == "CMTEST01"
        )
        me = client.get("/api/me").json()
        recognition = {
            "recognition_date": date.today().isoformat(),
            "occurred_attraction_id": str(next(row["id"] for row in options["recognition_venues"] if row["name"] == "热力追踪")),
            "recognition_type_id": str(next(row["id"] for row in options["recognition_types"] if row["code"] == "SAFETY")),
            "recognizer_employee_id": str(me["id"]),
            "content": "幂等业务载荷验证",
            "employee_id": str(target["id"]),
            "idempotency_key": "audit-recognition-replay",
        }
        first = client.post("/api/recognitions", data=recognition)
        replay = client.post("/api/recognitions", data=recognition)
        conflict = client.post("/api/recognitions", data={**recognition, "content": "不同认可内容"})
        assert first.status_code == 200, first.text
        assert replay.status_code == 200 and replay.json().get("duplicate") is True
        assert replay.json()["record"]["id"] == first.json()["record"]["id"]
        assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "IDEMPOTENCY_PAYLOAD_CONFLICT"

        statement = next(row for row in options["deduction_levels"] if row["code"] == "STATEMENT")
        deduction = {
            "employee_id": str(target["id"]),
            "deduction_type_id": str(options["deduction_types"][0]["id"]),
            "deduction_level_id": str(statement["id"]),
            "occurred_on": "2098-06-01",
            "description": "幂等扣分验证",
            "idempotency_key": "audit-deduction-replay",
        }
        first = client.post("/api/deductions", data=deduction, files={"document": ("statement.pdf", _pdf_bytes(), "application/pdf")})
        replay = client.post("/api/deductions", data=deduction, files={"document": ("statement.pdf", _pdf_bytes(), "application/pdf")})
        conflict = client.post("/api/deductions", data={**deduction, "description": "不同扣分内容"}, files={"document": ("statement.pdf", _pdf_bytes(), "application/pdf")})
        assert first.status_code == 200, first.text
        assert replay.status_code == 200 and replay.json().get("duplicate") is True
        assert replay.json()["record"]["id"] == first.json()["record"]["id"]
        assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "IDEMPOTENCY_PAYLOAD_CONFLICT"

        sick = {
            "employee_id": str(target["id"]),
            "leave_start_date": "2098-07-01",
            "leave_end_date": "2098-07-01",
            "leave_days": "1",
            "note": "幂等缺勤验证",
            "idempotency_key": "audit-sick-replay",
        }
        first = client.post("/api/sick-leaves", data=sick, files={"proof": ("proof.png", _image_bytes(), "image/png")})
        replay = client.post("/api/sick-leaves", data=sick, files={"proof": ("proof.png", _image_bytes(), "image/png")})
        conflict = client.post("/api/sick-leaves", data={**sick, "note": "不同缺勤内容"}, files={"proof": ("proof.png", _image_bytes(), "image/png")})
        assert first.status_code == 200, first.text
        assert replay.status_code == 200 and replay.json().get("duplicate") is True
        assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "IDEMPOTENCY_PAYLOAD_CONFLICT"


def test_restore_rehearsal_test_tracks_the_repository_scripts_directory() -> None:
    source = (ROOT / "tests" / "test_long_test_operations.py").read_text(encoding="utf-8")

    assert 'REPOSITORY_ROOT / "scripts" / "Run-MonthlyRestoreRehearsal.ps1"' in source
    assert 'ROOT / "ops"' not in source


def test_scoped_month_score_query_matches_the_legacy_view_business_rules() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        for statement in (
            "CREATE TABLE employees (id INTEGER PRIMARY KEY, employee_no TEXT, name TEXT)",
            "CREATE TABLE recognition_records (employee_id INTEGER, recognition_month TEXT, recognition_date TEXT, status TEXT, fraction NUMERIC, credited_fraction NUMERIC)",
            "CREATE TABLE attendance_monthly_scores (employee_id INTEGER, attendance_month TEXT, eligible INTEGER, final_score NUMERIC)",
            "CREATE TABLE deduction_records (employee_id INTEGER, deduction_month TEXT, status TEXT, points NUMERIC)",
        ):
            connection.execute(text(statement))
        connection.execute(text("INSERT INTO employees VALUES (1,'0000001','甲'),(2,'0000002','乙')"))
        connection.execute(text("INSERT INTO recognition_records VALUES (1,'2026-09','2026-09-02','confirmed',9,1.25),(1,'2026-09','2026-09-03','rejected',5,5),(2,'2026-08','2026-08-20','confirmed',2,0)"))
        connection.execute(text("INSERT INTO attendance_monthly_scores VALUES (1,'2026-09',1,12),(2,'2026-09',0,12)"))
        connection.execute(text("INSERT INTO deduction_records VALUES (1,'2026-09','active',0.5),(1,'2026-09','void',8),(2,'2026-09','active',1)"))
    with Session(engine) as session:
        rows = {row["employee_id"]: row for row in employee_month_scores(session, "2026-09", [1, 2])}

    assert rows[1]["recognition_score"] == 1.25
    assert rows[1]["attendance_score"] == 12
    assert rows[1]["deduction_score"] == 0.5
    assert rows[1]["total_score"] == 12.75
    assert rows[2]["recognition_score"] == 0
    assert rows[2]["attendance_score"] == 0
    assert rows[2]["deduction_score"] == 1
    assert rows[2]["total_score"] == -1


def test_startup_seed_keeps_custom_scores_and_legacy_attendance_history() -> None:
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    today = date.today().isoformat()
    next_month = (date.today().replace(day=28) + timedelta(days=8)).replace(day=1).isoformat()
    with SessionLocal() as db:
        role = db.query(Role).filter_by(code="SUPERVISOR").one()
        assert recognition_score_for_role(db, role.id, today) == Decimal("0.50")
        db.add(RecognitionScoreRule(role_id=role.id, score=Decimal("2.00"), effective_date=yesterday, active=True))
        employee = db.query(Employee).filter_by(employee_no="CMTEST01").one()
        level = db.query(DeductionLevel).first()
        stored = StoredFile(
            storage_key="legacy-attendance-keep.bin",
            original_filename="keep.bin",
            extension=".bin",
            file_size=1,
            sha256="a" * 64,
            uploaded_by=employee.id,
            status="active",
        )
        db.add(stored)
        db.flush()
        legacy_type = DeductionType(code="ATTENDANCE", name="旧考勤扣分", active=True)
        db.add(legacy_type)
        db.flush()
        record = DeductionRecord(
            employee_id=employee.id,
            employee_no=employee.employee_no,
            employee_name=employee.name,
            employee_role_snapshot="CM",
            deduction_type_id=legacy_type.id,
            deduction_type_name=legacy_type.name,
            deduction_level_id=level.id,
            deduction_level_name=level.name,
            points=Decimal("1.00"),
            occurred_on=yesterday,
            deduction_month=yesterday[:7],
            description="保留旧考勤扣分",
            document_file_id=stored.id,
            submitter_id=employee.id,
            submitter_name=employee.name,
            submitter_role_snapshot="CM",
            permission_scope_snapshot="test",
            status="active",
        )
        db.add(record)
        db.flush()
        db.add(AuditLog(action="旧考勤", entity_type="deduction", entity_id=str(record.id), operator_name="测试"))
        db.commit()
        before_count = db.query(RecognitionScoreRule).filter_by(role_id=role.id).count()
        record_id, type_id = record.id, legacy_type.id
        seed_reference_data(db)
        assert db.query(RecognitionScoreRule).filter_by(role_id=role.id).count() == before_count
        assert recognition_score_for_role(db, role.id, yesterday) == Decimal("2.00")
        assert recognition_score_for_role(db, role.id, today) == Decimal("2.00")
        assert recognition_score_for_role(db, role.id, next_month) == Decimal("2.00")
        assert db.query(RecognitionScoreRule).filter_by(role_id=role.id, effective_date=today).first() is None
        assert db.get(DeductionType, type_id) is not None
        assert db.get(DeductionRecord, record_id) is not None
        assert db.query(AuditLog).filter_by(entity_type="deduction", entity_id=str(record_id)).first() is not None
        assert db.query(StoredFile).filter_by(storage_key="legacy-attendance-keep.bin").first() is not None


def test_ordinary_recognition_rejects_poc_and_dedicated_entry_still_works() -> None:
    today = date.today().isoformat()
    with TestClient(app) as client:
        _login(client, "TATEST01")
        options = client.get("/api/options").json()
        poc = next(row for row in options["recognition_types"] if row["code"] == "POC")
        safety = next(row for row in options["recognition_types"] if row["code"] == "SAFETY")
        assert poc["dedicated_entry"] is True
        assert safety["dedicated_entry"] is False
        target = next(
            row
            for row in client.get("/api/employee-targets", params={"usage": "recognition", "keyword": "CMTEST01"}).json()["items"]
            if row["employee_no"] == "CMTEST01"
        )
        me = client.get("/api/me").json()
        venue_id = next(row["id"] for row in options["recognition_venues"] if row["name"] == "热力追踪")
        denied = client.post(
            "/api/recognitions",
            data={
                "recognition_date": today,
                "occurred_attraction_id": str(venue_id),
                "recognition_type_id": str(poc["id"]),
                "recognizer_employee_id": str(me["id"]),
                "content": "普通入口绕行POC",
                "employee_id": str(target["id"]),
                "idempotency_key": "audit-poc-bypass",
            },
        )
        assert denied.status_code == 400, denied.text
        assert "POC" in str(denied.json()["detail"])
        assert client.post(
            "/api/recognitions/poc",
            data={
                "recognition_date": today,
                "employee_id": str(target["id"]),
                "points": "2",
                "poc_period_type": "month",
                "poc_reason": "无权限绕行",
            },
        ).status_code == 403
        ordinary = client.post(
            "/api/recognitions",
            data={
                "recognition_date": "2098-08-01",
                "occurred_attraction_id": str(venue_id),
                "recognition_type_id": str(safety["id"]),
                "recognizer_employee_id": str(me["id"]),
                "content": "普通加分仍可用",
                "employee_id": str(target["id"]),
                "idempotency_key": "audit-ordinary-ok",
            },
        )
        assert ordinary.status_code == 200, ordinary.text
        client.post("/api/logout")
        _login(client, "GSMTEST01")
        issued = client.post(
            "/api/recognitions/poc",
            data={
                "recognition_date": today,
                "employee_id": str(target["id"]),
                "points": "2",
                "poc_period_type": "month",
                "poc_reason": "合法POC特别贡献",
                "idempotency_key": "audit-poc-legal",
            },
        )
        assert issued.status_code == 200, issued.text
        still_denied = client.post(
            "/api/recognitions",
            data={
                "recognition_date": today,
                "occurred_attraction_id": str(venue_id),
                "recognition_type_id": str(poc["id"]),
                "recognizer_employee_id": str(client.get("/api/me").json()["id"]),
                "content": "GSM也不可走普通入口",
                "employee_id": str(target["id"]),
                "idempotency_key": "audit-poc-gsm-bypass",
            },
        )
        assert still_denied.status_code == 400, still_denied.text


def test_circle_hr_alerts_are_filtered_before_the_page_limit() -> None:
    now = datetime.now()
    with SessionLocal() as db:
        heat = db.query(Employee).filter_by(employee_no="CMTEST01").one()
        dwarf = db.query(Employee).filter_by(employee_no="HR-DWARF").one()
        assert heat.attraction_id != dwarf.attraction_id
        db.query(SystemAlert).delete()
        db.add(
            SystemAlert(
                alert_type="test_home_circle",
                dedupe_key="home-keep",
                employee_id=heat.id,
                message="本圈必须可见的告警",
                status="open",
                created_at=now - timedelta(days=1),
            )
        )
        db.flush()
        db.add_all(
            [
                SystemAlert(
                    alert_type="test_other_circle",
                    dedupe_key=f"other-{index}",
                    employee_id=dwarf.id,
                    message=f"其他圈告警{index}",
                    status="open",
                    created_at=now,
                )
                for index in range(220)
            ]
        )
        hr = db.query(Employee).filter_by(employee_no="HR-HEAT").one()
        account = db.query(UserAccount).filter_by(employee_id=hr.id).one()
        account.password_hash = hash_password("1234")
        account.enabled = True
        account.must_change_password = False
        db.commit()
    with TestClient(app) as client:
        _login(client, "HR-HEAT")
        messages = [row["message"] for row in client.get("/api/hr/alerts").json()]
        assert "本圈必须可见的告警" in messages
        assert not any(message.startswith("其他圈告警") for message in messages)
