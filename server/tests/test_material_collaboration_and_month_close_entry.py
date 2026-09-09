from __future__ import annotations

import os
import tempfile
from datetime import date
from io import BytesIO
from pathlib import Path

from PIL import Image


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-material-collaboration-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.v2_crypto import hash_password  # noqa: E402
from app.v2_database import SessionLocal  # noqa: E402
from app.v2_models import UserAccount, UserSession  # noqa: E402


def login(client: TestClient, account: str, password: str = "1234") -> None:
    response = client.post("/api/login", json={"employee_no": account, "password": password})
    assert response.status_code == 200, response.text


def make_pdf() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (16, 16), "white").save(stream, format="PDF")
    return stream.getvalue()


def restore_circle_hr_test_login() -> None:
    """Circle HR accounts intentionally use one-time passwords when seeded."""
    with SessionLocal() as db:
        account = db.query(UserAccount).filter_by(login_account="HR-HEAT").one()
        account.password_hash = hash_password("1234")
        account.enabled = True
        account.must_change_password = False
        account.failed_attempts = 0
        account.locked_until = None
        db.query(UserSession).filter_by(account_id=account.id).delete(synchronize_session=False)
        db.commit()


def create_pending_material(client: TestClient) -> dict:
    options = client.get("/api/options").json()
    target = next(
        row
        for row in client.get("/api/employee-targets", params={"usage": "deduction", "keyword": "CMTEST01"}).json()["items"]
        if row["employee_no"] == "CMTEST01"
    )
    response = client.post(
        "/api/deductions",
        data={
            "employee_id": str(target["id"]),
            "deduction_type_id": str(options["deduction_types"][0]["id"]),
            "deduction_level_id": str(next(row for row in options["deduction_levels"] if row["code"] == "STATEMENT")["id"]),
            "occurred_on": date.today().isoformat(),
            "description": "TA GSM/GSM协作补材料测试",
        },
    )
    assert response.status_code == 200, response.text
    record = response.json()["record"]
    assert record["status"] == "pending_material"
    return record


def test_tagsm_and_gsm_can_filter_and_complete_shared_pending_material() -> None:
    with TestClient(app) as client:
        login(client, "TATEST01")
        record = create_pending_material(client)
        own_queue = client.get("/api/deductions/pending-materials", params={"scope": "mine"})
        assert own_queue.status_code == 200, own_queue.text
        assert own_queue.json()["scope"] == "mine"
        assert any(row["id"] == record["id"] for row in own_queue.json()["items"])

        client.post("/api/logout")
        login(client, "TAGSMTEST01")
        all_queue = client.get("/api/deductions/pending-materials", params={"scope": "all"})
        assert all_queue.status_code == 200, all_queue.text
        assert all_queue.json()["scope"] == "all"
        assert any(row["id"] == record["id"] for row in all_queue.json()["items"])
        status = client.get(f"/api/deductions/{record['id']}/material-status")
        assert status.status_code == 200, status.text
        supplemented = client.post(
            f"/api/deductions/{record['id']}/material",
            files={"document": ("statement.pdf", make_pdf(), "application/pdf")},
        )
        assert supplemented.status_code == 200, supplemented.text
        assert supplemented.json()["record"]["material_status"] == "processing"


def test_circle_hr_has_no_new_pending_material_queue_or_submission_permission() -> None:
    with TestClient(app) as client:
        login(client, "TATEST01")
        record = create_pending_material(client)
        client.post("/api/logout")
        restore_circle_hr_test_login()
        login(client, "HR-HEAT")
        assert client.get("/api/deductions/pending-materials", params={"scope": "all"}).status_code == 403
        blocked = client.post(
            f"/api/deductions/{record['id']}/material",
            files={"document": ("statement.pdf", make_pdf(), "application/pdf")},
        )
        assert blocked.status_code == 403


def test_circle_hr_month_close_action_opens_last_month_close_page() -> None:
    with TestClient(app) as client:
        restore_circle_hr_test_login()
        login(client, "HR-HEAT")
        items = client.get("/api/action-center").json()["items"]
        month_close = next(row for row in items if row["type"] == "month_close")
        assert month_close["title"] == "上月待月结景点圈"
        assert month_close["tab"] == "monthClose"
        assert date.today().replace(day=1).strftime("%Y-%m") not in month_close["description"]


def test_violation_sick_leave_without_statement_material_enters_the_same_collaboration_queue() -> None:
    with TestClient(app) as client:
        login(client, "TATEST01")
        target = next(
            row
            for row in client.get("/api/employee-targets", params={"usage": "attendance", "keyword": "CMTEST02"}).json()["items"]
            if row["employee_no"] == "CMTEST02"
        )
        submitted = client.post(
            "/api/sick-leaves",
            data={
                "employee_id": str(target["id"]),
                "leave_start_date": date.today().isoformat(),
                "leave_end_date": date.today().isoformat(),
                "leave_days": "1",
                "note": "违规病假待补声明材料测试",
                "is_violation": "true",
            },
            files={"proof": ("sick-proof.pdf", make_pdf(), "application/pdf")},
        )
        assert submitted.status_code == 200, submitted.text
        payload = submitted.json()
        assert payload["violation"]["status"] == "pending_material"
        assert payload["violation"]["material_status"] == "missing"

        client.post("/api/logout")
        login(client, "GSMTEST01")
        queue = client.get("/api/deductions/pending-materials", params={"scope": "all"})
        assert queue.status_code == 200, queue.text
        assert any(row["id"] == payload["violation"]["id"] for row in queue.json()["items"])


def test_material_scope_ui_and_month_close_copy_are_present() -> None:
    source = (Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "data-pending-material-scope" in source
    assert "我的景点圈" in source
    assert "全部景点圈" in source
    assert "TA GSM和GSM均可协作补充" in source
    router = (Path(__file__).resolve().parents[1] / "app" / "routers" / "v2.py").read_text(encoding="utf-8")
    assert '"上月待月结景点圈"' in router
    assert '"monthClose"' in router
