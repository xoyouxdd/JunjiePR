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
    # Seeded: 热力追踪A组 (主管 SUPTEST01), 热力追踪B组 (代理主管 TATEST01).
    with SessionLocal() as db:
        return db.query(WorkGroup).filter_by(name="热力追踪B组").one().id


def heat_circle_id(client: TestClient) -> int:
    return next(row["id"] for row in client.get("/api/options").json()["employee_circles"] if row["name"] == "热力追踪")


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
            "content": "主管与代理主管",
            "idempotency_key": key,
            "same_day_duplicate_confirmed": "true",
        },
        files={"image": ("proof.png", png_bytes(), "image/png")},
    )
    assert response.status_code == 200, response.text
    return response.json()["record"]["id"]


def set_leaders(client: TestClient, group_id: int, supervisor_no: str | None, acting_no: str | None):
    with SessionLocal() as db:
        revision = db.get(WorkGroup, group_id).revision
    return client.post(
        f"/api/hr/groups/{group_id}/leaders",
        json={
            "supervisor_id": employee_id(supervisor_no) if supervisor_no else None,
            "acting_id": employee_id(acting_no) if acting_no else None,
            "reason": "设置负责人",
            "revision": revision,
        },
    )


def test_formal_and_acting_leaders_split_reviewing_and_viewing() -> None:
    with TestClient(app) as client:
        group_id = ta_group_id()
        login(client, "HR01", "HR123")
        # Only a base 主管 can be 主管, and one 主管 leads one group.
        assert set_leaders(client, group_id, "CMTEST02", "TATEST01").status_code == 400
        assert set_leaders(client, group_id, "GSMTEST01", "TATEST01").status_code == 400
        assert set_leaders(client, group_id, "SUPTEST01", "TATEST01").status_code == 400  # already leads A组
        formal = set_leaders(client, group_id, "TAGSMTEST01", "TATEST01")
        assert formal.status_code == 200, formal.text
        with SessionLocal() as db:
            types = {row.leader_type for row in db.query(GroupLeaderAssignment).filter_by(group_id=group_id, status="active")}
            assert types == {"formal", "acting"}
            assert current_leader_for_employee(db, employee_id("CMTEST01")).employee_no == "TATEST01"
            assert db.get(WorkGroup, group_id).name == "热力追踪B组"

        login(client, "CMTEST01")
        me = client.get("/api/me").json()
        assert me["group_name"] == "热力追踪B组"
        assert me["group_leader_label"] == "主管 测试TA GSM · 代理主管 测试TA主管"
        record_id = self_recognize(client, "SUPTEST01", "cm-under-acting")

        # The 代理主管 reviews; the 主管 only views.
        login(client, "TAGSMTEST01")
        assert record_id not in {row["id"] for row in client.get("/api/reviews").json()["items"]}
        viewed = client.get("/api/member-records", params={"record_type": "recognition"}).json()
        assert record_id in {row["id"] for row in viewed}
        login(client, "TATEST01")
        assert record_id in {row["id"] for row in client.get("/api/reviews").json()["items"]}
        assert client.post(f"/api/reviews/{record_id}", json={"action": "confirm"}).status_code == 200


def test_groups_get_the_next_letter_and_close_only_when_empty() -> None:
    with TestClient(app) as client:
        login(client, "HR01", "HR123")
        circle_id = heat_circle_id(client)
        created = client.post("/api/hr/groups", json={"attraction_id": circle_id})
        assert created.status_code == 200, created.text
        empty_id, code = created.json()["id"], created.json()["code"]
        assert created.json()["name"] == f"热力追踪{code}组"
        with SessionLocal() as db:
            empty_revision = db.get(WorkGroup, empty_id).revision
            busy_id = ta_group_id()
            busy_revision = db.get(WorkGroup, busy_id).revision

        assert client.post(f"/api/hr/groups/{busy_id}/close", json={"reason": "关闭", "revision": busy_revision}).status_code == 400
        closed = client.post(f"/api/hr/groups/{empty_id}/close", json={"reason": "多余空组", "revision": empty_revision})
        assert closed.status_code == 200, closed.text
        circle = next(row for row in client.get("/api/hr/groups").json()["circles"] if row["id"] == circle_id)
        assert empty_id not in {row["id"] for row in circle["groups"]}
        assert empty_id not in {row["id"] for row in client.get("/api/hr/group-options").json()}
        # A closed group's letter is not reused.
        again = client.post("/api/hr/groups", json={"attraction_id": circle_id})
        assert again.status_code == 200, again.text
        assert again.json()["code"] != code
        with SessionLocal() as db:
            assert db.get(WorkGroup, empty_id).status == "closed"


def test_acting_leader_inside_the_group_is_reviewed_by_its_formal_leader() -> None:
    with TestClient(app) as client:
        group_id = ta_group_id()
        ta_id = employee_id("TATEST01")
        login(client, "HR01", "HR123")
        joined = client.put(f"/api/hr/employees/{ta_id}", json={"group_id": group_id, "reason": "代理主管归入所代理的组"})
        assert joined.status_code == 200, joined.text
        with SessionLocal() as db:
            assert current_leader_for_employee(db, ta_id).employee_no == "TAGSMTEST01"

        login(client, "TATEST01")
        own = self_recognize(client, "GSMTEST01", "acting-own")
        assert own not in {row["id"] for row in client.get("/api/reviews").json()["items"]}
        login(client, "TAGSMTEST01")
        assert own in {row["id"] for row in client.get("/api/reviews").json()["items"]}

        login(client, "HR01", "HR123")
        # The 主管 must stay while the 代理主管 is a member of the group.
        assert set_leaders(client, group_id, None, "TATEST01").status_code == 400
        ended = set_leaders(client, group_id, "TAGSMTEST01", None)
        assert ended.status_code == 200, ended.text
        with SessionLocal() as db:
            assert current_leader_for_employee(db, employee_id("CMTEST01")).employee_no == "TAGSMTEST01"
            assert group_id not in {group.id for group in groups_led_by(db, ta_id)}
            assert group_id in {group.id for group in groups_led_by(db, employee_id("TAGSMTEST01"))}
