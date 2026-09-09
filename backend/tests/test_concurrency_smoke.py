from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import os
from pathlib import Path
import tempfile
from threading import Barrier

from fastapi.testclient import TestClient


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-concurrency-test-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from app.main import app  # noqa: E402
from app.v2_database import SessionLocal  # noqa: E402
from app.v2_crypto import hash_password  # noqa: E402
from app.v2_models import Employee, RecognitionRecord, UserAccount  # noqa: E402


def login(client: TestClient, account: str) -> None:
    response = client.post("/api/login", json={"employee_no": account, "password": "1234"})
    assert response.status_code == 200, response.text


def activate_circle_hr() -> None:
    with SessionLocal() as db:
        employee = db.query(Employee).filter_by(employee_no="HR-HEAT").one()
        account = db.query(UserAccount).filter_by(employee_id=employee.id).one()
        account.password_hash = hash_password("1234")
        account.enabled = True
        account.must_change_password = False
        db.commit()


def concurrent_calls(count: int, callback):
    barrier = Barrier(count)

    def run_one(_index: int):
        barrier.wait(timeout=10)
        return callback()

    with ThreadPoolExecutor(max_workers=count) as executor:
        return list(executor.map(run_one, range(count)))


def recognition_form(client: TestClient) -> dict:
    options = client.get("/api/options").json()
    target = client.get("/api/employee-targets", params={"usage": "recognition", "keyword": "CMTEST01"}).json()["items"][0]
    me = client.get("/api/me").json()
    return {
        "recognition_date": date.today().isoformat(),
        "occurred_attraction_id": str(next(row["id"] for row in options["recognition_venues"] if row["name"] == "热力追踪")),
        "recognition_type_id": str(next(row["id"] for row in options["recognition_types"] if row["code"] == "SAFETY")),
        "recognizer_employee_id": str(me["id"]),
        "content": "并发冒烟测试",
        "employee_id": str(target["id"]),
        "idempotency_key": "concurrency-recognition-001",
    }


def create_pending_self_recognition() -> int:
    with TestClient(app) as client:
        login(client, "CMTEST01")
        options = client.get("/api/options").json()
        heat_id = next(row["id"] for row in options["employee_circles"] if row["name"] == "热力追踪")
        recognizers = client.get("/api/recognizers", params={"attraction_id": heat_id, "recognition_date": date.today().isoformat()}).json()
        recognizer_id = next(row["id"] for row in recognizers if row["employee_no"] == "TATEST01")
        response = client.post(
            "/api/recognitions",
            data={
                "recognition_date": date.today().isoformat(),
                "occurred_attraction_id": str(next(row["id"] for row in options["recognition_venues"] if row["name"] == "热力追踪")),
                "recognition_type_id": str(next(row["id"] for row in options["recognition_types"] if row["code"] == "SAFETY")),
                "recognizer_employee_id": str(recognizer_id),
                "content": "并发复核测试",
                # The preceding idempotency test intentionally creates a
                # manager record for the same employee/type/recognizer/day.
                # This separate self-entry is a deliberate second record.
                "same_day_duplicate_confirmed": "true",
                "idempotency_key": "concurrency-review-source-001",
            },
            files={"image": ("review.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\xf9\x80\x8f\x00\x00\x00\x00IEND\xaeB`\x82", "image/png")},
        )
        assert response.status_code == 200, response.text
        assert response.json()["record"]["status"] == "pending"
        return response.json()["record"]["id"]


def test_concurrent_recognition_submission_is_idempotent() -> None:
    with TestClient(app) as seed:
        login(seed, "TATEST01")
        payload = recognition_form(seed)

    def submit():
        with TestClient(app) as client:
            login(client, "TATEST01")
            return client.post("/api/recognitions", data=payload)

    responses = concurrent_calls(2, submit)
    assert all(response.status_code == 200 for response in responses), [response.text for response in responses]
    assert len({response.json()["record"]["id"] for response in responses}) == 1
    with SessionLocal() as db:
        assert db.query(RecognitionRecord).filter_by(content="并发冒烟测试").count() == 1


def test_concurrent_review_and_month_close_are_conflict_safe() -> None:
    with TestClient(app) as seed:
        login(seed, "TATEST01")
        circles = seed.get("/api/options").json()["employee_circles"]
        heat_id = next(row["id"] for row in circles if row["name"] == "热力追踪")
    recognition_id = create_pending_self_recognition()
    activate_circle_hr()

    def review():
        with TestClient(app) as client:
            login(client, "TATEST01")
            return client.post(f"/api/reviews/{recognition_id}", json={"action": "confirm"})

    review_responses = concurrent_calls(2, review)
    assert sorted(response.status_code for response in review_responses) == [200, 409]

    def close_month():
        with TestClient(app) as client:
            login(client, "HR-HEAT")
            return client.post("/api/month-closes/2099-12/close", json={"attraction_id": heat_id, "reason": "并发月结冒烟"})

    close_responses = concurrent_calls(2, close_month)
    assert sorted(response.status_code for response in close_responses) == [200, 409]
