from __future__ import annotations

import os
import tempfile
import time
from datetime import date
from io import BytesIO
from pathlib import Path

from PIL import Image


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-deduction-photo-pdf-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def login(client: TestClient) -> None:
    response = client.post("/api/login", json={"employee_no": "TATEST01", "password": "1234"})
    assert response.status_code == 200, response.text


def photo_bytes() -> bytes:
    image = Image.new("RGB", (900, 1200), "white")
    result = BytesIO()
    image.save(result, "JPEG", quality=85)
    image.close()
    return result.getvalue()


def pdf_bytes() -> bytes:
    image = Image.new("RGB", (300, 400), "white")
    result = BytesIO()
    image.save(result, "PDF")
    image.close()
    return result.getvalue()


def test_photo_material_creates_one_pending_record_then_activates_after_pdf_generation() -> None:
    with TestClient(app) as client:
        login(client)
        options = client.get("/api/options").json()
        target = next(row for row in client.get("/api/employee-targets", params={"usage": "deduction", "keyword": "CMTEST01"}).json()["items"] if row["employee_no"] == "CMTEST01")
        deduction_type = options["deduction_types"][0]
        statement = next(row for row in options["deduction_levels"] if row["code"] == "STATEMENT")
        submitted = client.post(
            "/api/deductions",
            data={
                "employee_id": str(target["id"]),
                "deduction_type_id": str(deduction_type["id"]),
                "deduction_level_id": str(statement["id"]),
                "occurred_on": date.today().isoformat(),
                "description": "照片材料转PDF测试",
            },
            files=[("document_images", ("camera.jpg", photo_bytes(), "image/jpeg"))],
        )
        assert submitted.status_code == 200, submitted.text
        record = submitted.json()["record"]
        assert record["status"] == "material_processing"
        assert record["material_status"] == "processing"
        assert not record["document_url"]

        for _ in range(50):
            current = client.get(f"/api/deductions/{record['id']}/material-status")
            assert current.status_code == 200, current.text
            record = current.json()["record"]
            if record["material_status"] != "processing":
                break
            time.sleep(0.1)
        assert record["material_status"] == "ready"
        assert record["status"] == "active"
        assert record["document_url"]
        generated = client.get(record["document_url"])
        assert generated.status_code == 200
        assert generated.content.startswith(b"%PDF")


def test_overpage_pdf_is_rejected_without_orphan_source_files() -> None:
    import fitz

    from app.v2_database import FILE_DIR

    document = fitz.open()
    for _ in range(7):
        document.new_page()
    too_long = document.tobytes()
    document.close()
    FILE_DIR.mkdir(parents=True, exist_ok=True)
    before = {path.name for path in FILE_DIR.iterdir() if path.is_file()}
    with TestClient(app) as client:
        login(client)
        options = client.get("/api/options").json()
        target = next(row for row in client.get("/api/employee-targets", params={"usage": "deduction", "keyword": "CMTEST01"}).json()["items"] if row["employee_no"] == "CMTEST01")
        deduction_type = options["deduction_types"][0]
        statement = next(row for row in options["deduction_levels"] if row["code"] == "STATEMENT")
        rejected = client.post(
            "/api/deductions",
            data={
                "employee_id": str(target["id"]),
                "deduction_type_id": str(deduction_type["id"]),
                "deduction_level_id": str(statement["id"]),
                "occurred_on": date.today().isoformat(),
                "description": "超页PDF应被拒收",
            },
            files={"document": ("too-long.pdf", too_long, "application/pdf")},
        )
        assert rejected.status_code == 400, rejected.text
    after = {path.name for path in FILE_DIR.iterdir() if path.is_file()}
    assert after == before


def test_photo_picker_keeps_camera_and_album_as_distinct_mobile_entries() -> None:
    source = (Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "data-material-camera-input" in source
    assert "data-material-album-input" in source
    camera_fragment = source.split("data-material-camera-input", 1)[1][:180]
    album_fragment = source.split("data-material-album-input", 1)[1][:180]
    assert 'capture="environment"' in camera_fragment
    assert 'capture="environment"' not in album_fragment


def test_photo_picker_supports_pre_submission_delete_replace_and_local_thumbnails() -> None:
    source = (Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    for marker in (
        "data-material-remove-photo",
        "data-material-replace-photos",
        "data-material-remove-pdf",
        "data-material-replace-pdf",
        "URL.createObjectURL",
        "URL.revokeObjectURL",
        "未上传时可先提交，主管可后续补充",
        "材料已就绪",
        "submitDateCleared",
    ):
        assert marker in source


def test_photo_material_for_statement_upgrade_creates_review_work_order_after_conversion() -> None:
    with TestClient(app) as client:
        login(client)
        options = client.get("/api/options").json()
        target = next(row for row in client.get("/api/employee-targets", params={"usage": "deduction", "keyword": "CMTEST02"}).json()["items"] if row["employee_no"] == "CMTEST02")
        deduction_type = next(row for row in options["deduction_types"] if row["repeat_check"])
        statement = next(row for row in options["deduction_levels"] if row["code"] == "STATEMENT")
        form = {
            "employee_id": str(target["id"]),
            "deduction_type_id": str(deduction_type["id"]),
            "deduction_level_id": str(statement["id"]),
            "occurred_on": date.today().isoformat(),
            "description": "A1",
        }
        first = client.post("/api/deductions", data=form, files={"document": ("first.pdf", pdf_bytes(), "application/pdf")})
        assert first.status_code == 200, first.text
        first_record = first.json()["record"]
        for _ in range(50):
            current = client.get(f"/api/deductions/{first_record['id']}/material-status")
            assert current.status_code == 200, current.text
            first_record = current.json()["record"]
            if first_record["material_status"] != "processing":
                break
            time.sleep(0.1)
        assert first_record["status"] == "active"
        preview = client.get("/api/deduction-upgrades/preview", params={"employee_id": target["id"], "deduction_type_id": deduction_type["id"], "occurred_on": date.today().isoformat()})
        assert preview.status_code == 200 and preview.json()["eligible"], preview.text
        reviewer = preview.json()["reviewers"][0]
        upgrade = dict(form, description="A2", reviewer_id=str(reviewer["id"]))
        submitted = client.post("/api/deduction-upgrades", data=upgrade, files=[("document_images", ("second.jpg", photo_bytes(), "image/jpeg"))])
        assert submitted.status_code == 200, submitted.text
        record = submitted.json()["record"]
        assert submitted.json()["processing"] is True
        for _ in range(50):
            current = client.get(f"/api/deductions/{record['id']}/material-status")
            assert current.status_code == 200, current.text
            record = current.json()["record"]
            if record["material_status"] != "processing":
                break
            time.sleep(0.1)
        assert record["material_status"] == "ready"
        assert record["status"] == "pending_upgrade"
        client.post("/api/logout")
        client.post("/api/login", json={"employee_no": reviewer["employee_no"], "password": "1234"})
        pending = client.get("/api/deduction-upgrades/pending")
        assert pending.status_code == 200, pending.text
        assert any(row["second_record"]["id"] == record["id"] for row in pending.json()["items"])


def test_pending_material_blocks_duplicate_and_submitter_can_void() -> None:
    with TestClient(app) as client:
        login(client)
        options = client.get("/api/options").json()
        target = next(
            row
            for row in client.get("/api/employee-targets", params={"usage": "deduction", "keyword": "CMTEST01"}).json()["items"]
            if row["employee_no"] == "CMTEST01"
        )
        deduction_type = next(row for row in options["deduction_types"] if row["repeat_check"])
        statement = next(row for row in options["deduction_levels"] if row["code"] == "STATEMENT")
        form = {
            "employee_id": str(target["id"]),
            "deduction_type_id": str(deduction_type["id"]),
            "deduction_level_id": str(statement["id"]),
            "occurred_on": date.today().isoformat(),
            "description": "待补材料重复拦截测试",
        }
        first = client.post("/api/deductions", data=form)
        assert first.status_code == 200, first.text
        record = first.json()["record"]
        assert record["status"] == "pending_material"

        duplicate = client.post("/api/deductions", data={**form, "description": "重复登记"})
        assert duplicate.status_code == 409, duplicate.text
        detail = duplicate.json()["detail"]
        assert detail["code"] == "PENDING_MATERIAL_EXISTS"
        assert detail["record"]["id"] == record["id"]

        preview = client.get(
            "/api/deduction-upgrades/preview",
            params={
                "employee_id": target["id"],
                "deduction_type_id": deduction_type["id"],
                "occurred_on": form["occurred_on"],
            },
        )
        assert preview.status_code == 409, preview.text
        assert preview.json()["detail"]["code"] == "PENDING_MATERIAL_EXISTS"

        voided = client.post(f"/api/deductions/{record['id']}/void", json={"reason": "材料未补齐，撤回重填"})
        assert voided.status_code == 200, voided.text

        retry = client.post("/api/deductions", data={**form, "description": "作废后重新登记"})
        assert retry.status_code == 200, retry.text
        assert retry.json()["record"]["status"] == "pending_material"
        cleanup = client.post(
            f"/api/deductions/{retry.json()['record']['id']}/void",
            json={"reason": "测试清理待补材料记录"},
        )
        assert cleanup.status_code == 200, cleanup.text


def test_pending_material_can_be_supplemented_when_no_previous_job_exists() -> None:
    """A material-later record has no old job and must still accept photos."""
    with TestClient(app) as client:
        login(client)
        options = client.get("/api/options").json()
        target = next(
            row
            for row in client.get("/api/employee-targets", params={"usage": "deduction", "keyword": "CMTEST01"}).json()["items"]
            if row["employee_no"] == "CMTEST01"
        )
        submitted = client.post(
            "/api/deductions",
            data={
                "employee_id": str(target["id"]),
                "deduction_type_id": str(options["deduction_types"][0]["id"]),
                "deduction_level_id": str(next(row for row in options["deduction_levels"] if row["code"] == "STATEMENT")["id"]),
                "occurred_on": date.today().isoformat(),
                "description": "补材料无旧任务热修复测试",
            },
        )
        assert submitted.status_code == 200, submitted.text
        record = submitted.json()["record"]
        assert record["status"] == "pending_material"

        supplemented = client.post(
            f"/api/deductions/{record['id']}/material",
            files=[("document_images", ("supplement.jpg", photo_bytes(), "image/jpeg"))],
        )
        assert supplemented.status_code == 200, supplemented.text
        assert supplemented.json()["record"]["status"] == "material_processing"
        assert supplemented.json()["record"]["material_status"] == "processing"


def test_pending_material_is_visible_and_repairable_by_another_supervisor() -> None:
    with TestClient(app) as client:
        login(client)
        options = client.get("/api/options").json()
        target = next(row for row in client.get("/api/employee-targets", params={"usage": "deduction", "keyword": "CMTEST01"}).json()["items"] if row["employee_no"] == "CMTEST01")
        deduction_type = options["deduction_types"][0]
        statement = next(row for row in options["deduction_levels"] if row["code"] == "STATEMENT")
        submitted = client.post(
            "/api/deductions",
            data={
                "employee_id": str(target["id"]),
                "deduction_type_id": str(deduction_type["id"]),
                "deduction_level_id": str(statement["id"]),
                "occurred_on": date.today().isoformat(),
                "description": "待补材料协作测试",
            },
        )
        assert submitted.status_code == 200, submitted.text
        record = submitted.json()["record"]
        assert record["status"] == "pending_material"
        own_queue = client.get("/api/deductions/pending-materials")
        assert own_queue.status_code == 200, own_queue.text
        assert any(row["id"] == record["id"] and row["submitter_id"] == client.get("/api/me").json()["id"] for row in own_queue.json()["items"])
        client.post("/api/logout")
        assert client.post("/api/login", json={"employee_no": "SUPTEST01", "password": "1234"}).status_code == 200
        queue = client.get("/api/deductions/pending-materials")
        assert queue.status_code == 200, queue.text
        assert any(row["id"] == record["id"] for row in queue.json()["items"])
        status = client.get(f"/api/deductions/{record['id']}/material-status")
        assert status.status_code == 200, status.text


def test_no_material_submission_shows_follow_up_material_reminder() -> None:
    source = (Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "声明已登记" in source
    assert "请在“待办”或“我的登记记录”中点击“补充材料”完成上传。" in source
    assert "handlePendingMaterialConflict" in source
    assert "const collaboration=(pendingMaterials.items||[]);" in source
    assert "我提交" in source
    assert "协作补充" in source
    assert "const ownVoid=own?` <button data-entry-void=\"${row.id}\" class=\"secondary\">作废</button>`:'';" in source
