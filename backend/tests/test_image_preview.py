from __future__ import annotations

from datetime import date
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import tempfile

os.environ["RECOGNITION_V2_DATA_DIR"] = tempfile.mkdtemp(prefix="recognition-preview-test-")
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from fastapi.testclient import TestClient  # noqa: E402
import fitz  # noqa: E402
from PIL import Image  # noqa: E402

from app.main import app  # noqa: E402
from app.v2_database import FILE_DIR, SessionLocal  # noqa: E402
from app.v2_models import Attraction, AuditLog, Employee, RecognitionAttachment, RecognitionRecord, RecognitionType, StoredFile  # noqa: E402
from app.v2_preview_cache import watermarked_preview_cache  # noqa: E402


def login(client: TestClient, employee_no: str) -> None:
    response = client.post("/api/login", json={"employee_no": employee_no, "password": "1234"})
    assert response.status_code == 200, response.text


def test_authorized_inline_preview_is_cached_and_member_ui_uses_it() -> None:
    source = BytesIO()
    with Image.new("RGB", (2400, 1200), (230, 235, 240)) as image:
        image.save(source, format="JPEG", quality=90)
    source_bytes = source.getvalue()
    today = date.today().isoformat()
    month = today[:7]

    with TestClient(app) as client:
        with SessionLocal() as db:
            circle = db.query(Attraction).filter_by(name="热力追踪", employee_circle=True, active=True).one()
            venue = db.query(Attraction).filter_by(name="热力追踪", recognition_venue=True, active=True).one()
            recognition_type = db.query(RecognitionType).filter_by(code="SAFETY").one()
            member = db.query(Employee).filter_by(employee_no="CMTEST01").one()
            operator = db.query(Employee).filter_by(employee_no="TATEST01").one()
            storage_key = "preview-fixture.jpg"
            file_row = StoredFile(
                storage_key=storage_key,
                original_filename="preview-fixture.jpg",
                extension=".jpg",
                mime_type="image/jpeg",
                file_size=len(source_bytes),
                sha256=sha256(source_bytes).hexdigest(),
                uploaded_by=operator.id,
            )
            db.add(file_row)
            db.flush()
            record = RecognitionRecord(
                employee_id=member.id,
                employee_no=member.employee_no,
                employee_name=member.name,
                employee_role_snapshot="CM",
                home_attraction_id=circle.id,
                home_attraction_name=circle.name,
                occurred_attraction_id=venue.id,
                recognition_date=today,
                recognition_month=month,
                recognition_type_id=recognition_type.id,
                recognition_type_name=recognition_type.name,
                content="图片预览缓存测试",
                recognizer_employee_id=operator.id,
                recognizer_name=operator.name,
                recognizer_role_snapshot="TA_SUPERVISOR",
                operator_employee_id=operator.id,
                operator_name=operator.name,
                source="supervisor",
                fraction=Decimal("0.50"),
                status="confirmed",
            )
            db.add(record)
            db.flush()
            db.add(RecognitionAttachment(recognition_id=record.id, file_id=file_row.id))
            db.commit()
            file_id = file_row.id
        FILE_DIR.joinpath(storage_key).write_bytes(source_bytes)
        watermarked_preview_cache.clear()

        login(client, "CMTEST01")
        first = client.get(f"/api/files/{file_id}?preview=1")
        assert first.status_code == 200, first.text
        assert first.headers["content-disposition"].startswith("inline;")
        assert first.headers["x-preview-cache"] == "MISS"
        with Image.open(BytesIO(first.content)) as image:
            assert max(image.size) <= 1920

        second = client.get(f"/api/files/{file_id}?preview=1")
        assert second.status_code == 200
        assert second.headers["x-preview-cache"] == "HIT"
        assert second.content == first.content

        client.post("/api/logout")
        login(client, "TATEST01")
        manager_preview = client.get(f"/api/files/{file_id}?preview=1")
        assert manager_preview.status_code == 200
        assert manager_preview.headers["x-preview-cache"] == "MISS"
        assert manager_preview.content != first.content

        download = client.get(f"/api/files/{file_id}")
        assert download.status_code == 200
        assert download.headers["content-disposition"].startswith("attachment;")
        assert download.headers["x-preview-cache"] == "BYPASS"
        with Image.open(BytesIO(download.content)) as image:
            assert max(image.size) == 2400

        with SessionLocal() as db:
            assert db.query(StoredFile).filter_by(id=file_id).count() == 1
            assert db.query(AuditLog).filter_by(entity_type="recognition", entity_id=str(file_id), action="查看带水印材料").count() == 3

    script = (Path(__file__).parents[1] / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    router = (Path(__file__).parents[1] / "app" / "routers" / "v2.py").read_text(encoding="utf-8")
    assert "preview=1" in script
    assert "function memberAttachment(record)" in script
    assert "bindFilePreviews(host)" in script
    assert "image-preview-v1-1920" in router
    assert '"Content-Disposition": f"{\'inline\' if inline_preview else \'attachment\'};' in router


def test_authorized_pdf_preview_is_inline_and_keeps_download_separate() -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Protected PDF preview")
    source_bytes = document.tobytes()
    document.close()

    with TestClient(app) as client:
        with SessionLocal() as db:
            member = db.query(Employee).filter_by(employee_no="CMTEST01").one()
            storage_key = "preview-fixture.pdf"
            file_row = StoredFile(
                storage_key=storage_key,
                original_filename="preview-fixture.pdf",
                extension=".pdf",
                mime_type="application/pdf",
                file_size=len(source_bytes),
                sha256=sha256(source_bytes).hexdigest(),
                uploaded_by=member.id,
            )
            db.add(file_row)
            db.commit()
            file_id = file_row.id
        FILE_DIR.joinpath(storage_key).write_bytes(source_bytes)

        login(client, "CMTEST01")
        preview = client.get(f"/api/files/{file_id}?preview=1")
        assert preview.status_code == 200, preview.text
        assert preview.headers["content-type"].startswith("application/pdf")
        assert preview.headers["content-disposition"].startswith("inline;")
        assert preview.headers["x-preview-cache"] == "MISS"
        assert preview.content.startswith(b"%PDF")

        download = client.get(f"/api/files/{file_id}")
        assert download.status_code == 200
        assert download.headers["content-disposition"].startswith("attachment;")

        with SessionLocal() as db:
            assert db.query(AuditLog).filter_by(entity_type="stored_file", entity_id=str(file_id), action="查看带水印材料").count() == 1
