"""Exercise the single-warehouse trial through the normal APIs and an isolated SQLite file."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, time, timedelta
from hashlib import sha256
from io import BytesIO
from random import Random
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
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


def add_item(client, name, quantity=0, position=1, *, spec=""):
    item = post(client, "/items", {"name": name, "spec": spec, "unit": "箱", "category": "消耗品", "aliases": [name[-2:]]})
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
        "photos": [{"position_id": pos, "photo_id": picture(client, "#" + sha256(f"{key}:{pos}".encode()).hexdigest()[:6])}
                   for pos in locations],
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
    assert len(get(picker, "/bootstrap")["positions"]) == 25
    inbound = {
        "client_id": "arrive-split", "kind": "inbound",
        "lines": [line(first, 1, 4), line(first, 2, 3)],
        "receipts": [{"item_id": first, "received": 10, "expected_unit": "箱", "direct_quantity": 3, "direct_destination": "现场"}],
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
    for place in get(picker, "/bootstrap")["positions"]:
        if place["id"] in {1, 2}:
            assert place["photo"] is None and place["needs_photo"]
    assert saved["deferred_photo_positions"] == [1, 2]
    totals = {row["item_id"]: row for row in get(owner, "/assessment")["items"]}
    assert totals[first]["direct_today"] == 3
    assert totals[first]["usage_today"] == 2


def test_inbound_excel_import_reports_rows_and_posts_the_batch_only_once(trial):
    clients, _, _, _ = trial
    owner, peer, picker = (clients[key] for key in ("owner", "peer", "picker"))
    first = add_item(owner, "进口纸卡", 1)
    second = post(owner, "/items", {"name": "物资袋", "spec": "大号", "unit": "个"})["id"]
    ground = next(row for row in get(owner, "/bootstrap")["positions"] if row["kind"] == "ground")
    template_route = PREFIX + "/inbound/template.xlsx"
    upload_route = PREFIX + "/inbound/import"
    assert picker.get(template_route).status_code == 403
    template = owner.get(template_route)
    assert template.is_success and "attachment" in template.headers["content-disposition"]

    def xlsx(rows, source=template.content):
        book = load_workbook(BytesIO(source))
        assert book.sheetnames == ["批量入库"]
        for index, row in enumerate(rows, 4):
            for column, value in enumerate(row, 1):
                book.active.cell(index, column, value)
        output = BytesIO()
        book.save(output)
        return output.getvalue()

    def upload(client, file):
        return client.post(upload_route, files={"file": ("inbound.xlsx", file, template.headers["content-type"])})

    rows = [("纸卡", "a1", 2), ("进口纸卡", "地面临时储存区", 4), ("物资袋", "B 架第 1 层", "3"), ("暂未到货", None, 0)]
    invalid = xlsx([*rows, ("进口纸卡", "Z1", 2), ("物资袋", "B2", 2.5),
                    ("进口纸卡", "A1", 1), ("进口纸卡", "A2", "=1+1")])
    errors = upload(owner, invalid)
    assert errors.status_code == 400
    assert [entry["row"] for entry in errors.json()["detail"]["rows"]] == [8, 9, 10, 11]
    data = xlsx(rows)
    assert upload(picker, data).status_code == 403
    assert upload(owner, template.content).status_code == 400
    preview = assert_json(upload(owner, data))
    assert preview["skipped_zero"] == 1
    assert assert_json(upload(peer, data)) == preview
    assert preview["items"] == [
        {"item_id": first, "received": 6, "expected_unit": "箱", "locations": [{"position_id": 1, "quantity": 2}, {"position_id": ground["id"], "quantity": 4}]},
        {"item_id": second, "received": 3, "expected_unit": "个", "locations": [{"position_id": 4, "quantity": 3}]},
    ]
    assert item_stock(owner, first)["total"] == 1 and item_stock(owner, second)["total"] == 0

    body = {
        "client_id": preview["client_id"], "kind": "inbound",
        "receipts": [{field: row[field] for field in ("item_id", "received", "expected_unit")} for row in preview["items"]],
        "lines": [{**place, "item_id": row["item_id"]} for row in preview["items"] for place in row["locations"]],
    }
    assert owner.post(PREFIX + "/transactions", json=body).status_code == 422
    assert item_stock(owner, first)["total"] == 1
    plans = [(client, {**body, "photos": [{"item_id": item, "photo_id": picture(client)} for item in (first, second)]})
             for client in (owner, peer)]

    def confirm(plan):
        client, payload = plan
        return client.post(PREFIX + "/transactions", json=payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(confirm, plans))
    assert sorted(response.status_code for response in results) == [200, 409]
    winner = next(index for index, response in enumerate(results) if response.is_success)
    saved = results[winner].json()
    assert len(saved["photos"]) == 2
    assert assert_json(confirm(plans[winner]))["id"] == saved["id"]
    assert results[1 - winner].json()["detail"]["transaction_id"] == saved["id"]
    assert upload(owner, data).json()["detail"]["code"] == "INBOUND_BATCH_POSTED"
    assert upload(peer, data).status_code == 409
    assert item_stock(owner, first)["total"] == 7 and item_stock(owner, second)["total"] == 3
    new_input = xlsx(rows, owner.get(template_route).content)
    assert assert_json(upload(owner, new_input))["client_id"] != preview["client_id"]


def test_first_receipt_creates_catalog_only_when_confirmed_and_keeps_idempotency(trial):
    clients, _, _, _ = trial
    owner, picker = clients["owner"], clients["picker"]
    request = {"name":"首批物资（箱）", "position_id":1, "quantity":4}
    assert picker.post(PREFIX + "/inbound/single", json=request).status_code == 403
    row = post(owner, "/inbound/single", request)
    assert row["new_item"] == {"name":request["name"], "unit":"箱", "spec":""}
    assert get(owner, "/bootstrap")["items"] == []
    body = {"client_id":"first-receipt", "kind":"inbound", "new_items":[{**row["new_item"], "item_id":row["item_id"]}],
            "lines":[line(row["item_id"],1,4)], "receipts":[{"item_id":row["item_id"], "received":4, "expected_unit":row["expected_unit"]}]}
    assert owner.post(PREFIX + "/transactions", json=body).status_code == 422
    assert get(owner, "/bootstrap")["items"] == []
    body["photos"] = [{"item_id":row["item_id"], "photo_id":picture(owner)}]
    posted = post(owner, "/transactions", body)
    assert post(owner, "/transactions", body)["id"] == posted["id"]
    item_id = posted["lines"][0]["item_id"]
    assert item_id > 0 and posted["photos"][0]["item_id"] == item_id
    assert item_stock(owner, item_id)["total"] == 4
    existing = post(owner, "/inbound/single", {**request, "quantity":2})
    assert existing["item_id"] == item_id and "new_item" not in existing
    assert owner.post(PREFIX + "/inbound/single", json={**request, "name":"首批"}).status_code == 400
    selected = post(owner, "/inbound/single", {**request, "item_id":item_id})
    assert selected["item_id"] == item_id


def test_last_active_owner_must_have_a_successor_before_removal(trial):
    clients, _, owner_account, peer_account = trial
    hr, owner, peer, picker = (clients[key] for key in ("hr", "owner", "peer", "picker"))
    item_id = add_item(owner, "交接物资", 1)
    issue = post(picker, "/issues", {"item_id": item_id, "position_id": 1, "reason": "mismatch"})
    post(owner, f"/issues/{issue['id']}/claim", {})
    owner_path = f"{PREFIX}/managers/{owner_account['id']}"
    assert owner.delete(owner_path).status_code == 403
    assert_json(hr.delete(f"{PREFIX}/managers/{peer_account['id']}"))

    blocked = hr.delete(owner_path)
    assert blocked.status_code == 409 and "安排接任人" in blocked.json()["detail"]
    assert get(owner, "/issues")["items"][0]["assignee_id"] == owner_account["id"]
    assert warehouse_tasks(owner)[0]["count"] == 1

    post(hr, "/managers", {"employee_id": peer_account["id"]})
    remaining = assert_json(hr.delete(owner_path))["managers"]
    assert [person["id"] for person in remaining] == [peer_account["id"]]
    assert owner.get(PREFIX + "/inventory/export.xlsx").status_code == 403
    assert get(peer, "/issues")["items"][0]["status"] == "open"
    assert warehouse_tasks(peer)[0]["count"] == 1


def test_missing_stock_is_reconciled_once_by_shared_owner_task(trial):
    clients, _, _, peer_account = trial
    owner, peer, picker = clients["owner"], clients["peer"], clients["picker"]
    item_id = add_item(owner, "补记卡", 1)
    body = outbound(picker, "picked-unposted", [line(item_id, 1, 3)], [1],
                    reported=[{"item_id": item_id, "position_id": 1, "observed_quantity": 2}], hold=True)
    pending = post(picker, "/transactions", body)
    assert pending["status"] == "pending"
    assert item_stock(owner, item_id)["total"] == 1
    calibration = {
        "client_id": "hold-cannot-be-calibrated", "version": versions(owner, 1)[1],
        "reason": "先复点", "items": [{"item_id": item_id, "quantity": 2}],
    }
    adjustment = {**line(item_id, 1, 2), "version": calibration["version"], "reason": "先复点"}
    blocked_calibration = owner.post(PREFIX + "/positions/1/calibration", json=calibration)
    blocked_adjustment = owner.post(PREFIX + "/adjustments", json=adjustment)
    assert blocked_calibration.status_code == blocked_adjustment.status_code == 409
    assert blocked_calibration.json() == blocked_adjustment.json()
    assert "待补记出库" in blocked_adjustment.json()["detail"]
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
    version = versions(owner, 1)[1]
    assert post(owner, "/adjustments", {**adjustment, "version": version})["quantity"] == 2
    assert post(owner, "/positions/1/calibration", {**calibration, "version": version})["status"] == "posted"


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
    new_photo = picture(picker, "gold")
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
    retry = post(picker, "/transactions", older)
    assert retry["photos"][0]["id"] == old_photo and retry["deferred_photo_positions"] == [1]
    layer = next(row for row in get(picker, "/bootstrap")["positions"] if row["id"] == 1)
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


def test_expanded_layout_keeps_legacy_stock_and_supports_calibration_and_excel(trial):
    from app.warehouse.models import WarehousePosition, seed_positions

    clients, factory, _, _ = trial
    owner, picker = clients["owner"], clients["picker"]
    with factory() as db:
        db.query(WarehousePosition).filter(WarehousePosition.id > 9).delete(synchronize_session=False)
        db.commit()
    first = add_item(owner, "测试卡", 8, 1)
    second = add_item(owner, "=安全文本", 3, 7, spec="+大号")
    with factory() as db:
        for _ in range(2):
            seed_positions(db)
            db.commit()
    positions = get(owner, "/bootstrap")["positions"]
    assert len(positions) == 25
    assert [(row["shelf"], row["level"]) for row in positions[:4]] == [("A",4),("A",3),("A",2),("A",1)]
    assert [row["zone"] for row in positions if row["level"] == 4] == ["左侧","左侧","中间","中间","右侧","右侧"]
    ground = positions[-1]
    assert ground["kind"] == "ground" and ground["label"] == "地面临时储存区"
    position_id = ground["id"]
    assert item_stock(owner, first)["locations"][0]["position_id"] == 1
    assert item_stock(owner, second)["locations"][0]["position_id"] == 7

    calibration = {
        "client_id": "ground-calibration", "version": ground["version"], "reason": "地面临时区清点",
        "items": [{"item_id": first, "quantity": 4}, {"item_id": second, "quantity": 2}],
        "photo_id": picture(owner),
    }
    route = f"/positions/{position_id}/calibration"
    assert picker.post(PREFIX + route, json=calibration).status_code == 403
    assert picker.get(PREFIX + "/inventory/export.xlsx").status_code == 403
    confirmed = post(owner, route, calibration)
    assert confirmed["kind"] == "calibration" and confirmed["photos"][0]["position_id"] == position_id
    assert post(owner, route, calibration)["id"] == confirmed["id"]
    assert owner.post(PREFIX + route, json={**calibration, "client_id": "stale-count"}).json()["detail"]["code"] == "STALE_POSITIONS"
    assert item_stock(owner, first)["total"] == 12 and item_stock(owner, second)["total"] == 5
    ground_saved = next(row for row in get(owner, "/bootstrap")["positions"] if row["id"] == position_id)
    assert ground_saved["photo"]["id"] == calibration["photo_id"] and not ground_saved["photo"]["stale"]
    post(picker, "/transactions", outbound(picker, "ground-out", [line(first, position_id, 1)], [position_id]))
    assert item_stock(owner, first)["total"] == 11
    post(owner, "/adjustments", {**line(second, 7, 3), "version": versions(owner, 7)[7], "reason": "复核旧货位"})
    post(picker, "/issues", {"item_id": second, "position_id": position_id, "reason": "mismatch"})

    # A later stock entry without a new photo must be exported as a stale photo.
    post(owner, route, {**calibration, "client_id": "ground-count-only", "version": versions(owner, position_id)[position_id],
                         "items": [{"item_id": first, "quantity": 2}], "photo_id": None})

    xlsx = owner.get(PREFIX + "/inventory/export.xlsx")
    assert xlsx.is_success and "attachment" in xlsx.headers["content-disposition"]
    book = load_workbook(BytesIO(xlsx.content))
    assert book.sheetnames == ["库存现场表"]
    sheet = book.active
    template = load_workbook(BytesIO(owner.get(PREFIX + "/inbound/template.xlsx").content))
    export_fields = tuple(cell.value for cell in sheet[1][:3])
    template_fields = tuple(cell.value for cell in template.active[3])
    assert export_fields[:2] == template_fields[:2] == ("物资名称", "货位")
    assert (export_fields[2], template_fields[2]) == ("账面数量", "入库数量")
    assert all(cell.data_type != "f" for row in sheet.iter_rows(min_row=2) for cell in row[:2])
    details = list(sheet.iter_rows(min_row=2, values_only=True))
    assert sum(row[2] for row in details if row[0] == "测试卡") == 10
    assert sum(row[2] for row in details if row[0] == "=安全文本") == 5
    ground_rows = [row for row in details if row[1] == "地面临时储存区"]
    assert len(ground_rows) == 2 and {row[2] for row in ground_rows} == {2}
    assert ground_rows[0][4] == "待核对"
    assert "测试GSM" in ground_rows[0][6].replace(" ", "") and "拍照后库存有变动" in ground_rows[0][6]
    assert any(row[1] == "A 架第 4 层" and row[6] == "暂无现场照片" for row in details)
    image, = sheet._images
    assert image.anchor._from.col == 5
    assert sheet.cell(image.anchor._from.row + 1, 2).value == "地面临时储存区"
    assert Image.open(BytesIO(image._data())).format == "JPEG"

    # Copy the identifying cells as text and enter a new arrival quantity.
    for column, value in enumerate(ground_rows[0][:2], 1):
        cell = template.active.cell(4, column, value)
        cell.data_type = "s"
    template.active["C4"] = 2
    output = BytesIO()
    template.save(output)
    preview = assert_json(owner.post(PREFIX + "/inbound/import", files={"file": ("inbound.xlsx", output.getvalue())}))
    assert preview["items"] == [{"item_id": second, "received": 2, "expected_unit": "箱",
                                 "locations": [{"position_id": position_id, "quantity": 2}]}]
    assert item_stock(owner, second)["total"] == 5
def test_deferred_receipt_groups_current_photos_and_closes_only_covered_positions(trial, monkeypatch):
    clients, _, owner_person, _ = trial
    owner, picker, peer, staff = (clients[key] for key in ("owner", "picker", "peer", "staff"))
    first = add_item(owner, "补照纸袋")
    second = add_item(owner, "补照卡片")
    photo_early = picture(picker)

    # Import a generated workbook through the same endpoint used by the receiving page.
    import_route = PREFIX + "/inbound/import"
    template = owner.get(PREFIX + "/inbound/template.xlsx")
    book = load_workbook(BytesIO(template.content))
    for number, values in enumerate([("补照纸袋", "A1", 5), ("补照卡片", "A1", 2), ("补照纸袋", "B1", 3)], 4):
        for column, value in enumerate(values, 1):
            book.active.cell(number, column, value)
    output = BytesIO()
    book.save(output)
    upload = {"file": ("inbound.xlsx", output.getvalue(), template.headers["content-type"])}
    preview = assert_json(owner.post(import_route, files=upload))
    payload = {
        "kind": "inbound", "client_id": preview["client_id"], "defer_location_photos": True,
        "receipts": [{"item_id": first, "received": 8, "expected_unit": "箱"}, {"item_id": second, "received": 2, "expected_unit": "箱"}],
        "lines": [line(first, 1, 5), line(second, 1, 2), line(first, 4, 3)],
    }
    for invalid in (
        {**payload, "client_id": "manual-receipt-defer"},
        {**payload, "receipts": [{**payload["receipts"][0], "received": 9, "damaged": 1, "note": "破损"}, payload["receipts"][1]]},
        {**payload, "receipts": [{**payload["receipts"][0], "received": 9, "direct_quantity": 1, "direct_destination": "现场"}, payload["receipts"][1]]},
    ):
        assert owner.post(PREFIX + "/transactions", json=invalid).status_code == 422
    assert get(owner, "/photo-tasks")["items"] == []
    saved = post(owner, "/transactions", payload)
    assert saved["deferred_photo_positions"] == [1, 4]
    assert post(owner, "/transactions", payload)["id"] == saved["id"]
    assert peer.post(PREFIX + "/transactions", json=payload).status_code == 409
    assert owner.post(import_route, files=upload).status_code == 409
    assert item_stock(owner, first)["total"] == 8

    def tasks(client=picker):
        return {entry["position_id"]: entry for entry in get(client, "/photo-tasks")["items"]}

    def submit_photo(position, task, photo_id):
        return picker.put(PREFIX + f"/positions/{position}/photo-task", json={
            "photo_id": photo_id, "version": task["version"], "requested_at": task["requested_at"],
        })

    initial = tasks()
    assert list(sorted(initial)) == [1, 4]
    assert all(entry["owner_name"] == owner_person["name"] for entry in initial.values())
    assert staff.get(PREFIX + "/photo-tasks").status_code == 403
    assert submit_photo(1, initial[1], photo_early).status_code == 409

    # A second receipt at A1 updates its task instead of adding a duplicate.
    another = {**payload, "client_id": "inbound-import-" + uuid4().hex,
               "receipts": [{"item_id": first, "received": 1, "expected_unit": "箱"}], "lines": [line(first, 1, 1)]}
    post(owner, "/transactions", another)
    updated = tasks()
    assert set(updated) == {1, 4} and updated[1]["version"] > initial[1]["version"]
    assert updated[4] == initial[4]
    assert submit_photo(1, initial[1], picture(picker)).status_code == 409

    held = post(picker, "/transactions", outbound(picker, "pending-photo-out", [line(first, 1, 7)], [1], hold=True))
    assert held["status"] == "pending" and set(tasks()) == {1, 4}
    assert tasks()[1] == updated[1]
    posted = post(picker, "/transactions", outbound(picker, "valid-photo-out", [line(second, 1, 1)], [1]))
    assert posted["status"] == "posted" and set(tasks()) == {4}
    positions = {entry["id"]: entry for entry in get(picker, "/bootstrap")["positions"]}
    current = positions[1]["photo"]
    assert current["transaction_id"] == posted["id"] and current["stale"] is False
    assert positions[4]["needs_photo"] is True

    from app.warehouse import photo_tasks
    next_shift = datetime.fromisoformat(tasks()[4]["requested_at"]) + timedelta(days=1)
    monkeypatch.setattr(photo_tasks, "warehouse_now", lambda: next_shift)
    owner_alert = next(row for row in assert_json(owner.get("/api/action-center"))["items"]
                       if row["type"] == "warehouse_photo_followup")
    assert owner_alert["title"] == "交班待补货位照" and owner_alert["count"] == 1
    assert all(row["type"] != "warehouse_photo_followup"
               for row in assert_json(picker.get("/api/action-center"))["items"])

    before = item_stock(owner, first)["total"]
    supplemental = submit_photo(4, tasks()[4], picture(picker))
    assert assert_json(supplemental)["photo"]["updated_by"]
    assert tasks() == {} and item_stock(owner, first)["total"] == before
    post(picker, "/positions/1/photo-task/report", {"photo_id": current["id"], "note": "货位标识未入镜"})
    assert set(tasks()) == {1}
    assert item_stock(owner, first)["total"] == before
    assert get(picker, f"/transactions/{posted['id']}")["photos"][0]["id"] == current["id"]


def test_inventory_move_and_count_only_calibration_keep_inventory_consistent(trial):
    clients, _, _, _ = trial
    owner, picker = clients["owner"], clients["picker"]
    ground = 25
    item = add_item(owner, "移位试点物资", 4, ground)
    revision = versions(owner, ground, 1)
    payload = {"client_id": "move-ground-to-a1", "item_id": item, "from_position_id": ground, "to_position_id": 1,
               "quantity": 3, "from_version": revision[ground], "to_version": revision[1]}
    assert picker.post(PREFIX + "/moves", json=payload).status_code == 403
    assert owner.post(PREFIX + "/moves", json={**payload, "quantity": 5}).status_code == 409
    assert get(owner, "/photo-tasks")["items"] == []
    saved = post(owner, "/moves", payload)
    assert saved["kind"] == "move" and post(owner, "/moves", payload)["id"] == saved["id"]
    assert set(row["position_id"] for row in get(owner, "/photo-tasks")["items"]) == {1, ground}
    stock = item_stock(owner, item)
    assert stock["total"] == 4
    assert {row["position_id"]: row["quantity"] for row in stock["locations"]} == {1: 3, ground: 1}

    counted = {"client_id": "ground-recount", "version": versions(owner, ground)[ground],
               "items": [{"item_id": item, "quantity": 2}], "reason": "复点后按实际剩余量入账"}
    calibration = post(owner, f"/positions/{ground}/calibration", counted)
    assert post(owner, f"/positions/{ground}/calibration", counted)["id"] == calibration["id"]
    assert item_stock(owner, item)["total"] == 5
    assert set(row["position_id"] for row in get(owner, "/photo-tasks")["items"]) == {1, ground}


def test_inbound_whole_shelf_photo_reuses_receipt_and_updates_only_the_selected_location(trial):
    from app.warehouse.models import warehouse_now

    clients, _, _, _ = trial
    owner = clients["owner"]
    item = add_item(owner, "整层收货用卡")
    started_at = warehouse_now().isoformat() + "+08:00"
    evidence = picture(owner)
    payload = {
        "kind": "inbound", "client_id": "receipt-whole-shelf",
        "receipts": [{"item_id": item, "received": 3, "expected_unit": "箱"}],
        "lines": [line(item, 1, 2), line(item, 4, 1)],
        "photos": [{"photo_id": evidence, "item_id": item, "position_id": 1, "position_started_at": started_at}],
    }
    wrong = {**payload, "photos": [{**payload["photos"][0], "position_id": 2}]}
    assert owner.post(PREFIX + "/transactions", json=wrong).status_code == 422
    saved = post(owner, "/transactions", payload)
    assert post(owner, "/transactions", payload)["id"] == saved["id"]
    locations = {row["id"]: row for row in get(owner, "/bootstrap")["positions"]}
    assert locations[1]["photo"]["transaction_id"] == saved["id"]
    assert locations[1]["photo"]["id"] == evidence and not locations[1]["photo"]["stale"]
    assert locations[4]["photo"] is None and item_stock(owner, item)["total"] == 3

    # An earlier image remains evidence on the new receipt, but cannot replace the shelf photo.
    older = picture(owner, captured_at=(warehouse_now() - timedelta(minutes=10)).isoformat())
    late = {**payload, "client_id": "receipt-late-shelf", "photos": [{**payload["photos"][0], "photo_id": older}]}
    assert post(owner, "/transactions", late)["photos"][0]["id"] == older
    locations = {row["id"]: row for row in get(owner, "/bootstrap")["positions"]}
    assert locations[1]["photo"]["id"] == evidence and locations[1]["photo"]["stale"]


def test_location_photo_retention_respects_replacements_age_and_open_evidence(trial):
    from app.warehouse.models import WarehouseIssue
    from app.warehouse.photo_retention import clear_old_location_photos, two_months_after

    clients, factory, owner_person, _ = trial
    owner = clients["owner"]
    taken = datetime(2026, 1, 31, 9)
    checkpoints = [taken + timedelta(minutes=i) for i in range(6)]
    checkpoints += [datetime(2026, 3, 30, 9) + timedelta(minutes=i) for i in range(4)]
    recent = {}
    colors = ("red", "navy", "green", "purple", "black", "pink", "tan", "yellow", "orange", "teal")
    for index, (date, color) in enumerate(zip(checkpoints, colors)):
        photo = picture(owner, color, captured_at=date.isoformat())
        position = assert_json(owner.put(PREFIX + "/positions/1/photo", json={"photo_id": photo}))
        assert position["photo"]["id"] == photo
        recent[9 - index] = (photo, position["photo"]["transaction_id"])
    # A different shelf's current picture is kept indefinitely.
    second_shelf = picture(owner, captured_at=taken.isoformat())
    assert_json(owner.put(PREFIX + "/positions/4/photo", json={"photo_id": second_shelf}))
    retained, transaction = recent[9]
    with factory() as db:
        issue = WarehouseIssue(reporter_id=owner_person["id"], reporter_name=owner_person["name"],
                               transaction_id=transaction, reason="mismatch", status="open")
        db.add(issue)
        db.commit()
        issue_id = issue.id

    assert two_months_after(datetime(2025, 12, 31, 15)) == datetime(2026, 2, 28, 15)
    boundary = two_months_after(checkpoints[3])
    with factory() as db:
        assert clear_old_location_photos(db, datetime(2026, 3, 30, 10)) == 1  # 8 replacements, under two months.
        assert clear_old_location_photos(db, boundary) == 0
    assert owner.get(PREFIX + f"/photos/{retained}").is_success
    assert owner.get(PREFIX + f"/photos/{recent[6][0]}").is_success

    with factory() as db:
        issue = db.get(WarehouseIssue, issue_id)
        issue.status = "closed"
        db.commit()
        assert clear_old_location_photos(db, datetime(2026, 4, 1, 9)) == 4
        assert clear_old_location_photos(db, datetime(2026, 4, 1, 9)) == 0
    for count, (photo, order_id) in recent.items():
        saved = get(owner, f"/transactions/{order_id}")["photos"][0]
        status = owner.get(PREFIX + f"/photos/{photo}").status_code
        if count in {4, 5, 6, 8, 9}:
            assert status == 410 and saved["url"] is None and saved["cleared_at"]
            assert saved["updated_by"] == owner_person["name"] and saved["position_id"] == 1
        else:
            assert status == 200 and saved["url"] and not saved.get("cleared_at")
    assert owner.get(PREFIX + f"/photos/{second_shelf}").is_success


def test_photo_report_notes_export_and_calibration_stay_consistent(trial):
    clients, _, _, _ = trial
    owner, picker = clients["owner"], clients["picker"]
    item = add_item(owner, "拍照与校准复核", 5)
    current = picture(owner)
    assert_json(owner.put(PREFIX + "/positions/1/photo", json={"photo_id": current}))
    older = picture(owner)
    version = versions(owner, 1)[1]
    post(picker, "/positions/1/photo-task/report", {"photo_id": current, "note": "标签看不清"})
    post(picker, "/positions/1/photo-task/report", {"photo_id": current, "note": "补充：疑似拍错货位"})
    task, = get(owner, "/photo-tasks")["items"]
    assert [entry["note"] for entry in task["reports"]] == ["补充：疑似拍错货位", "标签看不清"]
    assert all(entry["reporter_name"] and entry["reported_at"] for entry in task["reports"])
    assert get(picker, "/photo-tasks")["items"][0]["reports"] == []

    workbook = load_workbook(BytesIO(owner.get(PREFIX + "/inventory/export.xlsx").content))
    row = next(cells for cells in workbook.active.values if cells[1] == "A 架第 1 层")
    assert "待补最新现场照" in row[6] and "拍照后库存有变动" not in row[6]
    assert versions(owner, 1)[1] == version

    calibration = {"client_id": "atomic-photo-calibration", "version": version,
                   "items": [{"item_id": item, "quantity": 4}], "photo_id": older, "reason": "复点现场"}
    rejected = owner.post(PREFIX + "/positions/1/calibration", json=calibration)
    assert rejected.status_code == 409 and rejected.json()["detail"]["code"] == "STALE_LOCATION_PHOTO"
    assert versions(owner, 1)[1] == version and item_stock(owner, item)["total"] == 5
    invalid_replace = owner.put(PREFIX + "/positions/1/photo", json={"photo_id": older})
    assert invalid_replace.status_code == 409
    assert get(owner, "/photo-tasks")["items"] == [task]

    fresh = picture(owner, "blue")
    saved = post(owner, "/positions/1/calibration", {**calibration, "photo_id": fresh})
    assert item_stock(owner, item)["total"] == 4 and get(owner, "/photo-tasks")["items"] == []
    position = next(row for row in get(owner, "/bootstrap")["positions"] if row["id"] == 1)
    assert position["photo"]["id"] == fresh and position["photo"]["transaction_id"] == saved["id"]


def test_unknown_capture_time_keeps_receipt_evidence_without_setting_shelf_photo(trial):
    from app.warehouse.models import warehouse_now

    clients, _, _, _ = trial
    owner = clients["owner"]
    item = add_item(owner, "无时间信息的入库照片")
    evidence = picture(owner, captured_at="unknown")
    result = post(owner, "/transactions", {
        "kind": "inbound", "client_id": "unknown-time-inbound",
        "receipts": [{"item_id": item, "received": 1, "expected_unit": "箱"}], "lines": [line(item, 1, 1)],
        "photos": [{"photo_id": evidence, "item_id": item, "position_id": 1,
                    "position_started_at": warehouse_now().isoformat() + "+08:00"}],
    })
    assert result["photos"][0]["id"] == evidence and result["photos"][0]["captured_at"] is None
    assert result["deferred_photo_positions"] == [1]
    shelf = next(row for row in get(owner, "/bootstrap")["positions"] if row["id"] == 1)
    assert shelf["photo"] is None and shelf["needs_photo"]


def test_excel_unit_suffix_matches_existing_archive_in_both_directions(trial):
    clients, _, _, _ = trial
    owner = clients["owner"]
    primary = add_item(owner, "酒精棉片", 4, 1)
    legacy = post(owner, "/items", {"name": "创可贴（盒）", "unit": "盒"})["id"]
    template = owner.get(PREFIX + "/inbound/template.xlsx")

    def import_rows(rows):
        book = load_workbook(BytesIO(template.content))
        for index, row in enumerate(rows, 4):
            for column, value in enumerate(row, 1):
                book.active.cell(index, column, value)
        output = BytesIO()
        book.save(output)
        return owner.post(PREFIX + "/inbound/import", files={
            "file": ("unit-suffix.xlsx", output.getvalue(), template.headers["content-type"]),
        })

    assert import_rows([("酒精棉片（件）", "A1", 3)]).status_code == 400
    data = assert_json(import_rows([("酒精棉片 （ 箱 ）", "A1", 3), ("创可贴", "B1", 2)]))
    assert [row["item_id"] for row in data["items"]] == [primary, legacy]
    assert assert_json(owner.post(PREFIX + "/inbound/single", json={
        "name": "酒精棉片(箱)", "position_id": 1, "quantity": 3,
    }))["item_id"] == primary
    body = {"client_id": data["client_id"], "kind": "inbound", "defer_location_photos": True,
            "receipts": [{key: row[key] for key in ("item_id", "received", "expected_unit")} for row in data["items"]],
            "lines": [{**location, "item_id": row["item_id"]} for row in data["items"] for location in row["locations"]]}
    post(owner, "/transactions", body)
    assert set(it["id"] for it in get(owner, "/bootstrap")["items"]) == {primary, legacy}
    assert item_stock(owner, primary)["total"] == 7 and item_stock(owner, legacy)["total"] == 2


def test_warehouse_bootstrap_stock_and_photo_share_snapshot(trial, monkeypatch):
    from app.warehouse import core
    clients, _, _, _ = trial
    owner, picker = clients["owner"], clients["picker"]
    item = add_item(owner, "并发取货复查", 5)
    checkout = outbound(picker, "snapshot-checkout", [line(item, 1, 1)], [1])
    balances = core.balances_by_item
    receipts = []

    def post_after_balances(db):
        old = balances(db)
        if not receipts:
            receipts.append(post(picker, "/transactions", checkout))
        return old

    monkeypatch.setattr(core, "balances_by_item", post_after_balances)
    before = get(owner, "/bootstrap")
    old_item = next(row for row in before["items"] if row["id"] == item)
    old_shelf = next(row for row in before["positions"] if row["id"] == 1)
    assert old_item["total"] == 5 and old_shelf["photo"] is None and not old_shelf["needs_photo"]
    after = get(owner, "/bootstrap")
    new_item = next(row for row in after["items"] if row["id"] == item)
    new_shelf = next(row for row in after["positions"] if row["id"] == 1)
    assert new_item["total"] == 4 and new_shelf["photo"]["transaction_id"] == receipts[0]["id"]
    assert new_shelf["version"] > old_shelf["version"] and not new_shelf["photo"]["stale"]


def test_empty_shelf_rejects_old_photos_and_checkout_still_posts(trial):
    from app.warehouse.models import warehouse_now
    clients, _, _, _ = trial
    owner = clients["owner"]
    item = add_item(owner, "首次上架用卡")
    other = add_item(owner, "第二项用卡")
    now = warehouse_now()
    initial = picture(owner, captured_at=(now - timedelta(days=120)).isoformat())
    payload = {"kind": "inbound", "client_id": "first-shelf-old-photo",
               "receipts": [{"item_id": item, "received": 2, "expected_unit": "箱"}],
               "lines": [line(item, 1, 2)],
               "photos": [{"item_id": item, "position_id": 1, "photo_id": initial, "position_started_at": now.isoformat() + "+08:00"}]}
    saved = post(owner, "/transactions", payload)
    assert saved["deferred_photo_positions"] == [1] and saved["photos"][0]["id"] == initial
    assert post(owner, "/transactions", payload)["id"] == saved["id"]

    checkout_started = warehouse_now()
    earlier = picture(owner, captured_at=(checkout_started - timedelta(seconds=10)).isoformat())
    latest = picture(owner)
    saved = post(owner, "/transactions", {
        **payload, "client_id": "photo-after-checkout", "photo_started_at": checkout_started.isoformat() + "+08:00",
        "receipts": [{"item_id": item, "received": 2, "expected_unit": "箱"},
                     {"item_id": other, "received": 2, "expected_unit": "箱"}],
        "photos": [{"item_id": item, "position_id": 4, "photo_id": earlier, "position_started_at": checkout_started.isoformat() + "+08:00"},
                   {"item_id": other, "position_id": 5, "photo_id": latest, "position_started_at": checkout_started.isoformat() + "+08:00"}],
        "lines": [line(item, 4, 2), line(other, 5, 2)],
    })
    assert saved["deferred_photo_positions"] == [4]
    shelves = {row["id"]: row for row in get(owner, "/bootstrap")["positions"]}
    assert shelves[1]["photo"] is None and shelves[1]["needs_photo"]
    assert shelves[4]["photo"] is None and shelves[4]["needs_photo"]
    assert shelves[5]["photo"]["id"] == latest and not shelves[5]["needs_photo"]
    assert item_stock(owner, item)["total"] == 4


def test_inbound_preview_unit_is_checked_before_posting(trial):
    from app.warehouse.models import WarehouseTransaction
    from app.warehouse.schemas import TransactionInput
    from app.warehouse.service import fingerprint

    clients, factory, _, _ = trial
    owner, peer = clients["owner"], clients["peer"]
    item = add_item(owner, "未入库的纸杯")
    template = owner.get(PREFIX + "/inbound/template.xlsx")
    workbook = load_workbook(BytesIO(template.content))
    workbook.active["A4"], workbook.active["B4"], workbook.active["C4"] = "未入库的纸杯（箱）", "A1", 3
    output = BytesIO()
    workbook.save(output)
    preview = assert_json(owner.post(PREFIX + "/inbound/import", files={"file": ("unit.xlsx", output.getvalue())}))
    row, = preview["items"]
    assert row["expected_unit"] == "箱"
    assert_json(peer.put(PREFIX + f"/items/{item}", json={"name": "未入库的纸杯", "unit": "个"}))
    payload = {"kind": "inbound", "client_id": preview["client_id"], "defer_location_photos": True,
               "receipts": [{key: row[key] for key in ("item_id", "received", "expected_unit")}],
               "lines": [line(item, 1, 3)]}
    response = owner.post(PREFIX + "/transactions", json=payload)
    assert response.status_code == 409 and response.json()["detail"]["code"] == "INBOUND_UNIT_CHANGED"
    old_request = {**payload, "receipts": [{"item_id": item, "received": row["received"]}]}
    rejected = owner.post(PREFIX + "/transactions", json=old_request)
    assert rejected.status_code == 409 and rejected.json()["detail"]["code"] == "INBOUND_UNIT_REQUIRED"
    assert item_stock(owner, item)["total"] == 0 and get(owner, "/photo-tasks")["items"] == []
    assert owner.get(PREFIX + f"/transactions/by-client/{preview['client_id']}").status_code == 404
    assert_json(peer.put(PREFIX + f"/items/{item}", json={"name": "未入库的纸杯", "unit": "箱"}))
    saved = post(owner, "/transactions", payload)
    assert post(owner, "/transactions", payload)["id"] == saved["id"]
    with factory() as db:
        # Simulate an original receipt posted before the unit field existed.
        previous = db.get(WarehouseTransaction, saved["id"])
        previous.fingerprint = fingerprint(TransactionInput.model_validate(old_request))
        db.commit()
    assert post(owner, "/transactions", old_request)["id"] == saved["id"]
    assert item_stock(owner, item)["total"] == 3


def test_duplicate_photo_does_not_count_as_replacement_or_close_a_new_report(trial):
    from app.warehouse.models import WarehousePhoto, WarehousePositionPhotoReuse, WarehousePositionPhotoRevision
    from app.warehouse.photo_retention import clear_old_location_photos

    clients, factory, _, _ = trial
    owner, picker = clients["owner"], clients["picker"]
    route = PREFIX + "/positions/1/photo"
    first = picture(owner)
    assert_json(owner.put(route, json={"photo_id": first}))
    green = picture(owner, "green")
    valid = assert_json(owner.put(route, json={"photo_id": green}))["photo"]
    assert assert_json(owner.put(route, json={"photo_id": green}))["photo_reused"]
    for _ in range(8):
        repeat = picture(owner, "green")
        reused = assert_json(owner.put(route, json={"photo_id": repeat}))
        assert reused["photo_reused"] and reused["photo"] == valid
        assert assert_json(owner.put(route, json={"photo_id": repeat})) == reused
    assert owner.put(PREFIX + "/positions/2/photo", json={"photo_id": repeat}).status_code == 409
    with factory() as db:
        assert db.query(WarehousePositionPhotoRevision).filter_by(position_id=1).count() == 2
        assert db.query(WarehousePositionPhotoReuse).filter_by(position_id=1).count() == 8
        assert db.query(WarehousePhoto).filter_by(attached_at=None).count() == 0
        assert clear_old_location_photos(db) == 0
    assert owner.get(PREFIX + f"/photos/{first}").status_code == 200

    post(picker, "/positions/1/photo-task/report", {"photo_id": green, "note": "需要重拍"})
    task, = get(picker, "/photo-tasks")["items"]
    repeated = picker.put(PREFIX + "/positions/1/photo-task", json={
        "photo_id": picture(picker, "green"), "version": task["version"], "requested_at": task["requested_at"],
    })
    assert repeated.status_code == 409 and get(picker, "/photo-tasks")["items"] == [task]
