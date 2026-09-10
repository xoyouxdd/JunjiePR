import os
import tempfile
from datetime import date
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

    assert "认可人：${esc(r.recognizer_name)} · ${fmt(r.fraction)}分" in script
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
