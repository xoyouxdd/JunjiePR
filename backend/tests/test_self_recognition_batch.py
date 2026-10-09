"""Batch registration remains independent recognitions, not a new business table."""
import json
import os
import tempfile
from datetime import date
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor

os.environ["RECOGNITION_V2_DATA_DIR"] = tempfile.mkdtemp(prefix="pr-batch-")
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from PIL import Image
from fastapi.testclient import TestClient
from app.main import app
from app.v2_database import SessionLocal, FILE_DIR
from app.v2_models import RecognitionRecord, RecognitionAttachment, StoredFile


def login(client, account):
    client.post("/api/logout")
    assert client.post("/api/login", json={"employee_no": account, "password": "1234"}).status_code == 200


def picture():
    out = BytesIO()
    Image.new("RGB", (4, 4), "white").save(out, format="PNG")
    return out.getvalue()


def entry(client, content="独立表现", code="SAFETY"):
    opts = client.get("/api/options").json()
    return {"recognition_date": date.today().isoformat(), "content": content,
            "occurred_attraction_id": next(x["id"] for x in opts["recognition_venues"] if x["name"] == "热力追踪"),
            "recognition_type_id": next(x["id"] for x in opts["recognition_types"] if x["code"] == code),
            "recognizer_employee_id": next(x["id"] for x in client.get("/api/recognizers", params={"attraction_id": client.get("/api/me").json()["attraction_id"], "recognition_date": date.today().isoformat()}).json() if x["employee_no"] == "GSMTEST01")}


def send(client, items, key):
    return client.post("/api/recognitions/batch", data={"entries": json.dumps(items), "idempotency_key": key},
                       files={"image": ("evidence.png", picture(), "image/png")})


def test_five_rows_one_photo_and_retry_conflict():
    with TestClient(app) as client:
        login(client, "CMTEST01")
        items = [dict(entry(client, f"表现{i}"), same_day_duplicate_confirmed=True) for i in range(5)]
        response = send(client, items, "five")
        assert response.status_code == 200, response.text
        records = response.json()["records"]
        assert len({r["id"] for r in records}) == 5
        assert all(r["status"] == "pending" for r in records)
        with SessionLocal() as db:
            attachments = db.query(RecognitionAttachment).filter(RecognitionAttachment.recognition_id.in_([r["id"] for r in records])).all()
            assert len(attachments) == 5
            assert len({r.file_id for r in attachments}) == 1
        retry = send(client, items, "five")
        assert retry.status_code == 200, retry.text
        assert [r["id"] for r in retry.json()["records"]] == [r["id"] for r in records]
        items[0]["content"] = "修改内容"
        assert send(client, items, "five").status_code == 409
        mine = client.get("/api/dashboard").json()["records"]
        assert all(r["id"] in {x["id"] for x in mine} for r in records)


def test_invalid_second_row_rolls_back_rows_photo_and_receipts():
    with TestClient(app) as client:
        login(client, "TRTEST01")
        with SessionLocal() as db:
            before = (db.query(RecognitionRecord).count(), db.query(StoredFile).count())
        files = set(FILE_DIR.iterdir())
        bad = dict(entry(client), content="字" * 21)
        result = send(client, [entry(client), bad], "invalid")
        assert result.status_code == 400, result.text
        assert result.json()["detail"]["entry_index"] == 2
        with SessionLocal() as db:
            assert before == (db.query(RecognitionRecord).count(), db.query(StoredFile).count())
        assert set(FILE_DIR.iterdir()) == files
        assert send(client, [entry(client)], "invalid").status_code == 200


def test_size_permission_and_no_target_override():
    with TestClient(app) as client:
        login(client, "TATEST01")
        item = entry(client, "TA本人")
        assert send(client, [item], "ta").status_code == 200
        assert send(client, [], "empty").status_code == 400
        assert send(client, [item] * 6, "six").status_code == 400
        assert send(client, [dict(item, employee_id=1)], "override").status_code == 400
        login(client, "SUPTEST01")
        assert send(client, [item], "supervisor").status_code == 403


def test_in_batch_duplicates_require_confirmation_and_quota_is_atomic():
    with TestClient(app) as client:
        login(client, "CMTEST02")
        items = [entry(client, "同批1", "EFFICIENCY"), entry(client, "同批2", "EFFICIENCY")]
        result = send(client, items, "duplicate")
        assert result.status_code == 409
        assert result.json()["detail"]["code"] == "SAME_DAY_RECOGNITION_DUPLICATE"
        assert result.json()["detail"]["entry_index"] == 2
        items[1]["same_day_duplicate_confirmed"] = True
        assert send(client, items, "duplicate").status_code == 200
        letter = entry(client, "表扬信", "COMMENDATION_LETTER")
        letter["recognizer_employee_id"] = "special:COMMENDATION_LETTER"
        letter["same_day_duplicate_confirmed"] = True
        result = send(client, [letter, letter], "quota")
        assert result.status_code == 409, result.text
        assert result.json()["detail"]["entry_index"] == 2
        assert send(client, [letter], "quota").status_code == 200


def test_concurrent_reviews_cap_and_statistics():
    with TestClient(app) as client:
        login(client, "TRTEST01")
        items = [dict(entry(client, f"上限{i}", "COURTESY"), same_day_duplicate_confirmed=True) for i in range(5)]
        result = send(client, items, "cap")
        assert result.status_code == 200, result.text
        ids = [r["id"] for r in result.json()["records"]]
        def confirm(record_id):
            with TestClient(app) as reviewer:
                login(reviewer, "TATEST01")
                return reviewer.post(f"/api/reviews/{record_id}", json={"action": "confirm"})
        with ThreadPoolExecutor(max_workers=5) as pool:
            responses = list(pool.map(confirm, ids))
        assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
        with SessionLocal() as db:
            rows = db.query(RecognitionRecord).filter(RecognitionRecord.id.in_(ids)).all()
            assert all(r.status == "confirmed" for r in rows)
            assert sum(r.credited_fraction for r in rows) == min(sum(r.fraction for r in rows), 5)
            assert sum(r.credited_fraction for r in rows) <= 5
        login(client, "TRTEST01")
        dashboard = client.get("/api/dashboard").json()
        mine = dashboard["records"]
        assert len([r for r in mine if r["id"] in ids]) == 5
        assert dashboard["category_scores"]["礼仪"] <= 5
        login(client, "GSMTEST01")
        params = {"start_date": date.today().replace(day=1).isoformat(), "end_date": date.today().isoformat(),
                  "category": "recognition", "subtype_id": items[0]["recognition_type_id"], "keyword": "TRTEST01"}
        ranking = client.get("/api/pr-rankings", params=params)
        assert ranking.status_code == 200, ranking.text
        ranked = ranking.json()["rows"][0]
        assert ranked["count"] == 5
        assert ranked["score"] == dashboard["category_scores"]["礼仪"]
        exported = client.get("/api/pr-rankings/export", params=params)
        assert exported.status_code == 200, exported.text
        from openpyxl import load_workbook
        sheet = load_workbook(BytesIO(exported.content))["PR排名数据"]
        assert sheet.cell(2, 8).value == ranked["count"]
        assert sheet.cell(2, 9).value == ranked["score"]
        assert sheet.cell(2, 11).value == ranked["uncapped_score"]


def test_later_link_reviewer_can_view_shared_photo():
    with TestClient(app) as client:
        login(client, "CMTEST01")
        result = send(client, [dict(entry(client, f"材料{i}", "SHOW"), same_day_duplicate_confirmed=True) for i in range(2)], "photo")
        assert result.status_code == 200, result.text
        records = result.json()["records"]
        login(client, "CMTEST02")
        other_id = client.get("/api/me").json()["id"]
        with SessionLocal() as db:
            row = db.get(RecognitionRecord, records[1]["id"])
            row.reviewed_by = other_id
            file_id = row.attachments[0].file_id
            db.commit()
        assert client.get(f"/api/files/{file_id}?preview=true").status_code == 200
        login(client, "TRTEST01")
        assert client.get(f"/api/files/{file_id}?preview=true").status_code == 403


def test_legacy_attachment_migration_preserves_rows_and_is_repeatable():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    from app.v2_database import ensure_recognition_shared_evidence
    engine = create_engine("sqlite:///:memory:")
    with Session(engine) as db:
        db.execute(text("CREATE TABLE recognition_records (id INTEGER PRIMARY KEY)"))
        db.execute(text("CREATE TABLE stored_files (id INTEGER PRIMARY KEY)"))
        db.execute(text("INSERT INTO recognition_records VALUES (1),(2)"))
        db.execute(text("INSERT INTO stored_files VALUES (1)"))
        db.execute(text("""CREATE TABLE recognition_attachments (
            id INTEGER PRIMARY KEY, recognition_id INTEGER NOT NULL,
            file_id INTEGER NOT NULL UNIQUE, attachment_type VARCHAR(30) NOT NULL,
            sort_order INTEGER NOT NULL, created_at DATETIME NOT NULL,
            UNIQUE(recognition_id, attachment_type))"""))
        db.execute(text("INSERT INTO recognition_attachments VALUES(9,1,1,'evidence',1,'2026-10-01')"))
        before = db.execute(text("SELECT * FROM recognition_attachments")).all()
        ensure_recognition_shared_evidence(db)
        assert db.execute(text("SELECT * FROM recognition_attachments")).all() == before
        ensure_recognition_shared_evidence(db)
        db.execute(text("INSERT INTO recognition_attachments VALUES(10,2,1,'evidence',1,'2026-10-09')"))
        assert db.execute(text("SELECT COUNT(*) FROM recognition_attachments")).scalar() == 2
        assert not db.execute(text("PRAGMA foreign_key_check")).all()


def test_concurrent_retries_and_independent_withdrawal():
    with TestClient(app) as client:
        login(client, "CMTEST02")
        items = [dict(entry(client, f"并发{i}", "INCLUSION"), same_day_duplicate_confirmed=True) for i in range(2)]
        def submit(_):
            with TestClient(app) as browser:
                login(browser, "CMTEST02")
                return send(browser, items, "concurrent-retry")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, range(2)))
        assert all(x.status_code == 200 for x in results), [x.text for x in results]
        ids = [r["id"] for r in results[0].json()["records"]]
        assert ids == [r["id"] for r in results[1].json()["records"]]
        response = client.delete(f"/api/recognitions/{ids[0]}")
        assert response.status_code == 200, response.text
        with SessionLocal() as db:
            assert db.get(RecognitionRecord, ids[0]).status == "void"
            other = db.get(RecognitionRecord, ids[1])
            assert other.status == "pending"
            file_id = other.attachments[0].file_id
        assert client.get(f"/api/files/{file_id}?preview=true").status_code == 200
