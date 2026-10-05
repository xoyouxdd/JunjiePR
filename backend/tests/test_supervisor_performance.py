from __future__ import annotations

import os
import tempfile
from datetime import date
from io import BytesIO
from pathlib import Path

from PIL import Image


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-supervisor-performance-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.v2_database import SessionLocal  # noqa: E402
from app.v2_models import Employee, RecognitionRecord  # noqa: E402


def login(client: TestClient, account: str) -> None:
    client.post("/api/logout")
    response = client.post("/api/login", json={"employee_no": account, "password": "1234"})
    assert response.status_code == 200, response.text


def employee_id(employee_no: str) -> int:
    with SessionLocal() as db:
        return db.query(Employee).filter_by(employee_no=employee_no).one().id


def png_bytes() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (4, 4), "white").save(stream, format="PNG")
    return stream.getvalue()


def pdf_bytes() -> bytes:
    image = Image.new("RGB", (300, 400), "white")
    stream = BytesIO()
    image.save(stream, "PDF")
    return stream.getvalue()


def recognize(client: TestClient, *, recognizer_no: str, key: str, target_no: str | None = None, self_image: bool = False):
    options = client.get("/api/options").json()
    venue_id = next(row["id"] for row in options["recognition_venues"] if row["name"] == "热力追踪")
    circle_id = next(row["id"] for row in options["employee_circles"] if row["name"] == "热力追踪")
    type_id = next(row["id"] for row in options["recognition_types"] if row["code"] == "SAFETY")
    today = date.today().isoformat()
    recognizers = client.get("/api/recognizers", params={"attraction_id": circle_id, "recognition_date": today}).json()
    recognizer = next(row for row in recognizers if row.get("employee_no") == recognizer_no)
    data = {
        "recognition_date": today,
        "occurred_attraction_id": str(venue_id),
        "recognition_type_id": str(type_id),
        "recognizer_employee_id": str(recognizer["id"]),
        "content": "主管绩效测试",
        "idempotency_key": key,
        # Several cases reuse the same recognizer and target on one day.
        "same_day_duplicate_confirmed": "true",
    }
    if target_no:
        data["employee_id"] = str(employee_id(target_no))
    files = {"image": ("proof.png", png_bytes(), "image/png")} if self_image else None
    return client.post("/api/recognitions", data=data, files=files)


def deduct(client: TestClient, target_no: str, level_code: str, key: str):
    options = client.get("/api/options").json()
    level = next(row for row in options["deduction_levels"] if row["code"] == level_code)
    return client.post(
        "/api/deductions",
        data={
            "employee_id": str(employee_id(target_no)),
            "deduction_type_id": str(options["deduction_types"][0]["id"]),
            "deduction_level_id": str(level["id"]),
            "occurred_on": date.today().isoformat(),
            "description": "主管扣分测试",
            "idempotency_key": key,
        },
        files={"document": ("statement.pdf", pdf_bytes(), "application/pdf")},
    )


def test_supervisor_self_recognition_goes_to_the_formal_gsm_queue() -> None:
    with TestClient(app) as client:
        login(client, "SUPTEST01")
        me = client.get("/api/me").json()
        assert "SELF_RECOGNITION" in me["permissions"]
        assert recognize(client, recognizer_no="TATEST01", key="sup-self-ta", self_image=True).status_code == 400
        created = recognize(client, recognizer_no="GSMTEST01", key="sup-self-gsm", self_image=True)
        assert created.status_code == 200, created.text
        record = created.json()["record"]
        assert record["status"] == "pending"
        assert client.get("/api/supervisor-reviews").status_code == 403

        # Only a formal GSM reviews it; AM sees only acting TA GSM records, OM none.
        login(client, "AMTEST01")
        assert record["id"] not in {row["id"] for row in client.get("/api/supervisor-reviews").json()["items"]}
        login(client, "OMTEST01")
        assert client.get("/api/supervisor-reviews").status_code == 403
        login(client, "GSMTEST01")
        assert record["id"] in {row["id"] for row in client.get("/api/supervisor-reviews").json()["items"]}
        actions = client.get("/api/action-center").json()["items"]
        assert any(item["type"] == "supervisor_review" for item in actions)
        confirmed = client.post(f"/api/supervisor-reviews/{record['id']}", json={"action": "confirm"})
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["record"]["status"] == "confirmed"

        login(client, "TAGSMTEST01")
        assert client.get("/api/supervisor-reviews").status_code == 403


def test_acting_ta_gsm_is_recognized_by_gsm_am_om_and_reviewed_by_am_only() -> None:
    with TestClient(app) as client:
        login(client, "TAGSMTEST01")
        # Recognizers: formal GSM, AM or OM; not another TA GSM.
        assert recognize(client, recognizer_no="TAGSMTEST01", key="tagsm-self-self", self_image=True).status_code == 400
        created = recognize(client, recognizer_no="GSMTEST01", key="tagsm-self-gsm", self_image=True)
        assert created.status_code == 200, created.text
        record_id = created.json()["record"]["id"]
        with SessionLocal() as db:
            assert db.get(RecognitionRecord, record_id).employee_acting_duty_code == "TA_GSM"

        login(client, "GSMTEST01")
        assert record_id not in {row["id"] for row in client.get("/api/supervisor-reviews").json()["items"]}
        assert client.post(f"/api/supervisor-reviews/{record_id}", json={"action": "confirm"}).status_code == 404
        login(client, "AMTEST01")
        assert record_id in {row["id"] for row in client.get("/api/supervisor-reviews").json()["items"]}
        assert client.post(f"/api/supervisor-reviews/{record_id}", json={"action": "confirm"}).status_code == 200


def test_who_may_credit_supervisors() -> None:
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        # The recognizer differs from the earlier self-registration so the
        # same-day duplicate reminder does not apply.
        credited = recognize(client, recognizer_no="OMTEST01", key="gsm-sup", target_no="SUPTEST01")
        assert credited.status_code == 200, credited.text
        assert credited.json()["record"]["status"] == "confirmed"
        assert recognize(client, recognizer_no="GSMTEST01", key="gsm-tagsm", target_no="TAGSMTEST01").status_code == 403
        found = client.get("/api/employee-targets", params={"usage": "recognition", "scope": "supervisor", "keyword": "测试"}).json()["items"]
        assert {row["employee_no"] for row in found} == {"SUPTEST01"}

        login(client, "TAGSMTEST01")
        assert recognize(client, recognizer_no="TAGSMTEST01", key="tagsm-sup", target_no="SUPTEST01").status_code == 200

        login(client, "SUPTEST01")
        assert recognize(client, recognizer_no="GSMTEST01", key="sup-tagsm", target_no="TAGSMTEST01").status_code == 403

        # Nobody credits a supervisor acting as TA GSM, AM included; AM has no supervisor entry.
        login(client, "AMTEST01")
        assert recognize(client, recognizer_no="GSMTEST01", key="am-sup", target_no="SUPTEST01").status_code == 403
        assert recognize(client, recognizer_no="AMTEST01", key="am-tagsm", target_no="TAGSMTEST01").status_code == 403
        assert client.get("/api/employee-targets", params={"usage": "deduction", "scope": "supervisor", "keyword": "测试"}).status_code == 403
        with SessionLocal() as db:
            tagsm_id = db.query(Employee).filter_by(employee_no="TAGSMTEST01").one().id
        poc = client.post(
            "/api/recognitions/poc",
            data={"recognition_date": date.today().isoformat(), "employee_id": str(tagsm_id), "points": "2", "poc_period_type": "month", "poc_reason": "代理期间"},
        )
        assert poc.status_code == 403, poc.text


def test_who_may_deduct_supervisors() -> None:
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        memo = deduct(client, "SUPTEST01", "MEMO", "gsm-deduct-sup")
        assert memo.status_code == 200, memo.text
        assert deduct(client, "TAGSMTEST01", "STATEMENT", "gsm-deduct-tagsm").status_code == 403

        login(client, "SUPTEST01")
        assert deduct(client, "TAGSMTEST01", "STATEMENT", "sup-deduct-tagsm").status_code == 403

        # Nobody deducts a supervisor acting as TA GSM, AM included.
        login(client, "AMTEST01")
        assert deduct(client, "TAGSMTEST01", "WARNING_1", "am-deduct-tagsm").status_code == 403
        assert deduct(client, "SUPTEST01", "STATEMENT", "am-deduct-sup").status_code == 403
