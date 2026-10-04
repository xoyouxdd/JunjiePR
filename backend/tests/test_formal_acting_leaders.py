from __future__ import annotations

import os
import tempfile
from datetime import date
from io import BytesIO
from pathlib import Path

from PIL import Image


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-formal-acting-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.v2_database import SessionLocal  # noqa: E402
from app.v2_models import Employee, GroupLeaderAssignment, WorkGroup  # noqa: E402
from app.v2_services import current_leader_for_employee, groups_led_by  # noqa: E402


def login(client: TestClient, account: str, password: str = "1234") -> None:
    client.post("/api/logout")
    response = client.post("/api/login", json={"employee_no": account, "password": password})
    assert response.status_code == 200, response.text


def employee_id(employee_no: str) -> int:
    with SessionLocal() as db:
        return db.query(Employee).filter_by(employee_no=employee_no).one().id


def ta_group_id() -> int:
    with SessionLocal() as db:
        return db.query(WorkGroup).filter_by(name="V2-A-TA组").one().id


def png_bytes() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (4, 4), "white").save(stream, format="PNG")
    return stream.getvalue()


def self_recognize(client: TestClient, recognizer_no: str, key: str) -> int:
    options = client.get("/api/options").json()
    venue_id = next(row["id"] for row in options["recognition_venues"] if row["name"] == "热力追踪")
    circle_id = next(row["id"] for row in options["employee_circles"] if row["name"] == "热力追踪")
    type_id = next(row["id"] for row in options["recognition_types"] if row["code"] == "SAFETY")
    today = date.today().isoformat()
    recognizers = client.get("/api/recognizers", params={"attraction_id": circle_id, "recognition_date": today}).json()
    recognizer = next(row for row in recognizers if row.get("employee_no") == recognizer_no)
    response = client.post(
        "/api/recognitions",
        data={
            "recognition_date": today,
            "occurred_attraction_id": str(venue_id),
            "recognition_type_id": str(type_id),
            "recognizer_employee_id": str(recognizer["id"]),
            "content": "原组长代理组长",
            "idempotency_key": key,
            "same_day_duplicate_confirmed": "true",
        },
        files={"image": ("proof.png", png_bytes(), "image/png")},
    )
    assert response.status_code == 200, response.text
    return response.json()["record"]["id"]


def transfer(client: TestClient, group_id: int, leader_no: str):
    with SessionLocal() as db:
        revision = db.get(WorkGroup, group_id).revision
    return client.post(
        f"/api/hr/groups/{group_id}/transfer",
        json={"new_leader_id": employee_id(leader_no), "reason": "设置组长", "revision": revision, "effective_date": date.today().isoformat()},
    )


def test_formal_and_acting_leaders_split_reviewing_and_viewing() -> None:
    with TestClient(app) as client:
        group_id = ta_group_id()
        login(client, "HR01", "HR123")
        # Only a base 主管 or above can become the formal leader.
        assert transfer(client, group_id, "CMTEST02").status_code == 400
        formal = transfer(client, group_id, "SUPTEST01")
        assert formal.status_code == 200, formal.text
        assert formal.json()["leader_type"] == "formal"
        with SessionLocal() as db:
            types = {row.leader_type for row in db.query(GroupLeaderAssignment).filter_by(group_id=group_id, status="active")}
            assert types == {"formal", "acting"}
            assert current_leader_for_employee(db, employee_id("CMTEST01")).employee_no == "TATEST01"

        login(client, "CMTEST01")
        assert client.get("/api/me").json()["leader_name"] == "测试主管（代理：测试TA主管）"
        record_id = self_recognize(client, "SUPTEST01", "cm-under-acting")

        # The acting leader reviews; the formal leader only views.
        login(client, "SUPTEST01")
        assert record_id not in {row["id"] for row in client.get("/api/reviews").json()["items"]}
        viewed = client.get("/api/member-records", params={"record_type": "recognition"}).json()
        assert record_id in {row["id"] for row in viewed}
        login(client, "TATEST01")
        assert record_id in {row["id"] for row in client.get("/api/reviews").json()["items"]}
        assert client.post(f"/api/reviews/{record_id}", json={"action": "confirm"}).status_code == 200


def test_acting_leader_inside_the_group_is_reviewed_by_its_formal_leader() -> None:
    with TestClient(app) as client:
        group_id = ta_group_id()
        ta_id = employee_id("TATEST01")
        login(client, "HR01", "HR123")
        joined = client.put(f"/api/hr/employees/{ta_id}", json={"group_id": group_id, "leader_id": ta_id, "reason": "代理组长归入所代理的组"})
        assert joined.status_code == 200, joined.text
        with SessionLocal() as db:
            assert current_leader_for_employee(db, ta_id).employee_no == "SUPTEST01"

        login(client, "TATEST01")
        own = self_recognize(client, "GSMTEST01", "acting-own")
        assert own not in {row["id"] for row in client.get("/api/reviews").json()["items"]}
        login(client, "SUPTEST01")
        assert own in {row["id"] for row in client.get("/api/reviews").json()["items"]}

        login(client, "HR01", "HR123")
        ended = client.post(f"/api/hr/groups/{group_id}/end-acting", json={"reason": "代理结束"})
        assert ended.status_code == 200, ended.text
        with SessionLocal() as db:
            assert current_leader_for_employee(db, employee_id("CMTEST01")).employee_no == "SUPTEST01"
            assert group_id not in {group.id for group in groups_led_by(db, ta_id)}
            assert group_id in {group.id for group in groups_led_by(db, employee_id("SUPTEST01"))}
