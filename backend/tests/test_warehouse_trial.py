"""Exercise the single-warehouse trial through the normal APIs and an isolated SQLite file."""
from contextlib import ExitStack
from datetime import datetime, time, timedelta
from io import BytesIO
from random import Random

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


PREFIX = "/api/warehouse"


@pytest.fixture
def trial(tmp_path, monkeypatch):
    monkeypatch.setenv("RECOGNITION_ENVIRONMENT", "test")
    monkeypatch.setenv("RECOGNITION_V2_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("RECOGNITION_BACKUP_HEALTH_STATUS_FILE", raising=False)
    monkeypatch.setenv("RECOGNITION_ENABLE_TEST_ACCOUNTS", "1")
    monkeypatch.setenv("RECOGNITION_TEST_DEFAULT_PASSWORD", "1234")
    monkeypatch.setenv("RECOGNITION_TEST_ADMIN_PASSWORD", "HR123")
    from app.database.accounts import seed_test_accounts
    from app.database.connection import Base, get_db
    from app.database.reference_data import seed_reference_data
    from app.main import app
    from app.warehouse.models import seed_positions

    engine = create_engine(f"sqlite:///{(tmp_path / 'warehouse.db').as_posix()}")

    @event.listens_for(engine, "connect")
    def sqlite_pragmas(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")

    factory = sessionmaker(bind=engine, autoflush=False)
    Base.metadata.create_all(engine)
    with factory() as db:
        seed_reference_data(db)
        seed_test_accounts(db)
        seed_positions(db)
        db.commit()

    def database():
        with factory() as db:
            yield db

    monkeypatch.setitem(app.dependency_overrides, get_db, database)
    with ExitStack() as stack:
        clients = {}
        for key, account in {
            "hr": "HR01", "owner": "SUPTEST01", "peer": "TATEST01",
            "picker": "GSMTEST01", "staff": "CMTEST01",
        }.items():
            client = TestClient(app)
            stack.callback(client.close)
            assert_json(client.post("/api/login", json={
                "employee_no": account, "password": "HR123" if key == "hr" else "1234",
            }))
            clients[key] = client

        assert clients["staff"].get(PREFIX + "/bootstrap").status_code == 403
        assert not assert_json(clients["staff"].get("/api/me"))["warehouse_entry"]
        assert assert_json(clients["hr"].get("/api/me"))["warehouse_entry"]
        assert clients["picker"].get(PREFIX + "/manager-candidates", params={"q": "测试"}).status_code == 403
        owner = assert_json(clients["hr"].get(PREFIX + "/manager-candidates", params={"q": "SUPTEST01"}))["items"][0]
        peer = assert_json(clients["hr"].get(PREFIX + "/manager-candidates", params={"q": "测试TA主管"}))["items"][0]
        for person in (owner, peer):
            assert_json(clients["hr"].post(PREFIX + "/managers", json={"employee_id": person["id"]}))
        yield clients, factory, owner, peer
    engine.dispose()


def assert_json(response):
    assert response.is_success, response.text
    return response.json()


def get(client, path):
    return assert_json(client.get(PREFIX + path))


def post(client, path, content):
    return assert_json(client.post(PREFIX + path, json=content))


def picture(client, color="gold", captured_at=None):
    output = BytesIO()
    Image.new("RGB", (640, 480), color).save(output, "JPEG", quality=80)
    data = {"captured_at": captured_at} if captured_at else None
    return assert_json(client.post(PREFIX + "/photos", data=data, files={
        "file": ("fixture.jpg", output.getvalue(), "image/jpeg"),
    }))["id"]


def versions(client, *ids):
    return {row["id"]: row["version"] for row in get(client, "/bootstrap")["positions"] if row["id"] in ids}


def add_item(client, name, quantity=0, position=1):
    item = post(client, "/items", {"name": name, "unit": "箱", "category": "消耗品", "aliases": [name[-2:]]})
    if quantity:
        post(client, "/adjustments", {
            "item_id": item["id"], "position_id": position, "quantity": quantity,
            "version": versions(client, position)[position], "reason": "试运营期初实点", "kind": "opening",
        })
    return item["id"]


def line(item, position, quantity):
    return {"item_id": item, "position_id": position, "quantity": quantity}


def outbound(client, key, lines, locations, *, reported=None, hold=False):
    return {
        "client_id": key, "kind": "outbound", "lines": lines,
        "position_versions": versions(client, *locations),
        "photos": [{"position_id": pos, "photo_id": picture(client)} for pos in locations],
        "discrepancies": reported or [], "hold_if_insufficient": hold,
    }


def item_stock(client, item_id):
    return next(row for row in get(client, "/bootstrap")["items"] if row["id"] == item_id)


def warehouse_tasks(client):
    return [row for row in assert_json(client.get("/api/action-center"))["items"] if row["type"] == "warehouse_followup"]


def test_inbound_direct_use_outbound_grouping_stale_and_retry(trial):
    clients, _, _, _ = trial
    owner, picker = clients["owner"], clients["picker"]
    first = add_item(owner, "厕所卡", 6)
    second = add_item(owner, "返回卡", 4, 2)
    third = add_item(owner, "等候卡", 4)
    assert len(get(picker, "/bootstrap")["positions"]) == 9
    inbound = {
        "client_id": "arrive-split", "kind": "inbound",
        "lines": [line(first, 1, 4), line(first, 2, 3)],
        "receipts": [{"item_id": first, "received": 10, "direct_quantity": 3, "direct_destination": "现场"}],
        "photos": [{"item_id": first, "photo_id": picture(owner)}],
        "position_versions": versions(owner, 1, 2),
    }
    assert picker.post(PREFIX + "/transactions", json=inbound).status_code == 403
    body = outbound(picker, "two-shelves-three-items", [line(first, 1, 2), line(second, 2, 1), line(third, 1, 1)], [1, 2])
    missing = {**body, "photos": body["photos"][:1]}
    assert picker.post(PREFIX + "/transactions", json=missing).status_code in {400, 422}
    assert item_stock(owner, first)["total"] == 6
    post(owner, "/transactions", inbound)
    assert item_stock(owner, first)["total"] == 13
    stale = picker.post(PREFIX + "/transactions", json=body)
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["code"] == "STALE_POSITIONS"
    assert {row["id"] for row in detail["positions"]} == {1, 2}
    body["position_versions"] = versions(picker, 1, 2)
    saved = post(picker, "/transactions", body)
    assert post(picker, "/transactions", body)["id"] == saved["id"]
    assert get(picker, "/transactions/by-client/two-shelves-three-items")["id"] == saved["id"]
    item = item_stock(owner, first)
    assert item["total"] == 11
    assert {(row["position_id"], row["quantity"]) for row in item["locations"]} >= {(1, 8), (2, 3)}
    assert item_stock(owner, second)["total"] == 3
    assert item_stock(owner, third)["total"] == 3
    for attachment in body["photos"]:
        assert picker.get(f"{PREFIX}/photos/{attachment['photo_id']}").is_success
        assert clients["staff"].get(f"{PREFIX}/photos/{attachment['photo_id']}").status_code == 403
    totals = {row["item_id"]: row for row in get(owner, "/assessment")["items"]}
    assert totals[first]["direct_today"] == 3
    assert totals[first]["usage_today"] == 2


def test_missing_stock_is_reconciled_once_by_shared_owner_task(trial):
    clients, _, _, peer_account = trial
    owner, peer, picker = clients["owner"], clients["peer"], clients["picker"]
    item_id = add_item(owner, "补记卡", 1)
    body = outbound(picker, "picked-unposted", [line(item_id, 1, 3)], [1],
                    reported=[{"item_id": item_id, "position_id": 1, "observed_quantity": 2}], hold=True)
    pending = post(picker, "/transactions", body)
    assert pending["status"] == "pending"
    assert item_stock(owner, item_id)["total"] == 1
    report = get(owner, "/issues")["items"][0]
    assert report["transaction_id"] == pending["id"]
    assert warehouse_tasks(owner)[0]["strong_reminder"]
    assert warehouse_tasks(peer)[0]["count"] == 1
    assert not warehouse_tasks(picker)
    issue_path = f"/issues/{report['id']}"
    post(owner, issue_path + "/claim", {})
    assert peer.post(PREFIX + issue_path + "/claim", json={}).status_code == 409
    post(owner, issue_path + "/transfer", {"employee_id": peer_account["id"]})
    assert get(owner, "/issues")["items"][0]["assignee_id"] == peer_account["id"]
    resolution = {
        "reason": "实物已取走三箱，现存两箱",
        "observations": [{"item_id": item_id, "position_id": 1, "quantity": 2, "version": versions(peer, 1)[1]}],
    }
    resolved = post(peer, issue_path + "/resolve", resolution)
    repeated = post(peer, issue_path + "/resolve", resolution)
    assert repeated["id"] == resolved["id"] and repeated["closed_at"] == resolved["closed_at"]
    assert get(picker, "/transactions/by-client/picked-unposted")["status"] == "posted"
    assert item_stock(owner, item_id)["total"] == 2
    item = get(owner, "/assessment")["items"][0]
    assert item["usage_today"] == 3
    assert not warehouse_tasks(owner)
    assert not warehouse_tasks(peer)


def test_threshold_alert_cycle_and_per_item_forecast(trial):
    from app.warehouse.assessment import assessment_payload
    from app.warehouse.models import WarehouseItem, WarehouseMovement, warehouse_now

    clients, factory, _, peer_account = trial
    owner, peer, picker = clients["owner"], clients["peer"], clients["picker"]
    item_id = add_item(owner, "库存预警卡", 18)
    new_id = add_item(owner, "新物资", 5)
    threshold = {"minimum": 3, "yellow_upper": 5, "lead_days": 4, "safety_days": 2, "target_days": 14}
    assert picker.put(f"{PREFIX}/assessment/{item_id}", json=threshold).status_code == 403
    assert_json(owner.put(f"{PREFIX}/assessment/{item_id}", json=threshold))
    post(picker, "/transactions", outbound(picker, "fifteen-used", [line(item_id, 1, 15)], [1]))
    with factory() as db:
        start = datetime.combine(warehouse_now().date() - timedelta(days=31), time.min)
        db.get(WarehouseItem, item_id).opened_at = start
        movement = db.query(WarehouseMovement).filter_by(item_id=item_id, kind="outbound").one()
        movement.occurred_at = movement.usage_at = start + timedelta(days=15)
        db.query(WarehouseMovement).filter_by(item_id=item_id, kind="opening").update({
            WarehouseMovement.occurred_at: start, WarehouseMovement.usage_at: start,
        })
        db.commit()
        rows = {row["item_id"]: row for row in assessment_payload(db)["items"]}
        assert rows[item_id]["stock"] == 3
        assert rows[item_id]["daily_average"] == 0.5
        assert rows[item_id]["suggested_point"] == 3
        assert rows[item_id]["coverage_days"] == 6
        assert rows[item_id]["suggested_quantity"] == 6
        assert rows[new_id]["suggested_point"] is None
    assert not warehouse_tasks(owner)
    post(picker, "/transactions", outbound(picker, "below-threshold", [line(item_id, 1, 1)], [1]))
    alerts = get(owner, "/alerts")["items"]
    assert len(alerts) == 1 and alerts[0]["level"] == "low"
    alert_id = alerts[0]["id"]
    assert not warehouse_tasks(picker)
    assert_json(owner.patch(f"{PREFIX}/alerts/{item_id}", json={"status": "processing"}))
    assert peer.patch(f"{PREFIX}/alerts/{item_id}", json={"status": "processing"}).status_code == 409
    post(picker, "/transactions", outbound(picker, "last-two", [line(item_id, 1, 2)], [1]))
    alert = get(owner, "/alerts")["items"][0]
    assert alert["id"] == alert_id and alert["level"] == "empty"
    assert_json(owner.patch(f"{PREFIX}/alerts/{item_id}", json={
        "status": "waiting_restock", "target_employee_id": peer_account["id"],
    }))
    assert get(peer, "/alerts")["items"][0]["status"] == "waiting_restock"
    assert warehouse_tasks(peer)[0]["count"] == 1
    post(peer, "/adjustments", {
        "item_id": item_id, "position_id": 1, "quantity": 5,
        "version": versions(peer, 1)[1], "reason": "现场复核确认实存",
    })
    assert get(owner, "/alerts")["items"] == []
    assert not warehouse_tasks(peer)


def test_older_photo_uploaded_later_stays_with_its_order(trial):
    from app.warehouse.models import warehouse_now

    clients, _, _, _ = trial
    owner, picker = clients["owner"], clients["picker"]
    item_id = add_item(owner, "货位照片检验", 8)
    old_versions = versions(picker, 1)
    now = warehouse_now()
    new_photo = picture(picker, "gold", (now - timedelta(minutes=1)).isoformat() + "+08:00")
    recent = {
        "client_id": "new-photo", "kind": "outbound", "lines": [line(item_id, 1, 1)],
        "position_versions": old_versions, "photos": [{"position_id": 1, "photo_id": new_photo}],
    }
    new_order = post(picker, "/transactions", recent)
    old_photo = picture(picker, "green", (now - timedelta(minutes=5)).isoformat() + "+08:00")
    older = {
        **recent, "client_id": "older-photo", "photos": [{"position_id": 1, "photo_id": old_photo}],
    }
    stale = picker.post(PREFIX + "/transactions", json=older)
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "STALE_POSITIONS"
    older["position_versions"] = versions(picker, 1)
    assert post(picker, "/transactions", older)["photos"][0]["id"] == old_photo
    layer = get(picker, "/bootstrap")["positions"][0]
    assert layer["photo"]["id"] == new_photo and layer["photo"]["transaction_id"] == new_order["id"]
    assert layer["photo"]["stale"] and item_stock(owner, item_id)["total"] == 6


def test_photo_storage_limit_with_high_detail_image(trial):
    from app.warehouse.media import MAX_STORED_BYTES

    clients, _, _, _ = trial
    owner = clients["owner"]
    width, height = 1800, 1200
    source = BytesIO()
    Image.frombytes("RGB", (width, height), Random(26).randbytes(width * height * 3)).save(source, "PNG")
    original = source.getvalue()
    assert len(original) > 5 * 1024 * 1024
    photo = assert_json(owner.post(PREFIX + "/photos", files={"file": ("detailed.png", original, "image/png")}))
    stored = owner.get(photo["url"])
    assert stored.is_success and stored.headers["content-type"] == "image/jpeg"
    assert len(stored.content) <= MAX_STORED_BYTES
    with Image.open(BytesIO(stored.content)) as image:
        assert max(image.size) <= 1440
        assert abs(image.width * height - image.height * width) <= width
