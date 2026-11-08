from __future__ import annotations

import os
import tempfile
import asyncio
import importlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import BytesIO
from pathlib import Path
from threading import Barrier

from PIL import Image
import pytest


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-material-concurrent-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.v2_database import ROLE_PERMISSION_CODES, SessionLocal  # noqa: E402
from app.v2_models import DeductionMaterialJob, DeductionRecord, DeductionUpgradeRequest, StoredFile  # noqa: E402

# Test the retained legacy route under a test-only grant, not a production role change.
ROLE_PERMISSION_CODES["TA_SUPERVISOR"] = (*ROLE_PERMISSION_CODES["TA_SUPERVISOR"], "SICK_REGISTER")


def login(client: TestClient, account: str, password: str = "1234") -> None:
    response = client.post("/api/login", json={"employee_no": account, "password": password})
    assert response.status_code == 200, response.text


def make_pdf() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (16, 16), "white").save(stream, format="PDF")
    return stream.getvalue()


@pytest.mark.parametrize(
    "upgrade, retry_after_failure, before_work_order, month",
    [
        pytest.param(False, False, False, "2098-01", id="ordinary"),
        pytest.param(True, False, False, "2098-05", id="upgrade"),
        pytest.param(False, True, False, "2098-09", id="ordinary-retry"),
        pytest.param(True, True, False, "2099-01", id="existing-upgrade-retry"),
        pytest.param(True, True, True, "2099-05", id="upgrade-before-work-order-retry"),
        pytest.param("supplement", False, False, "2097-01", id="supplement-converts-to-upgrade"),
        pytest.param("supplement", True, False, "2097-05", id="supplement-upgrade-failure-retry"),
    ],
)
def test_lead_and_gsm_cannot_supplement_the_same_declaration_twice(
    monkeypatch, upgrade: bool, retry_after_failure: bool, before_work_order: bool, month: str,
) -> None:
    from app import deduction_materials
    from app.deduction_policy import deduction_counts_for_score
    from app.v2_database import FILE_DIR

    process_next_deduction_material_job = deduction_materials.process_next_deduction_material_job
    routes = importlib.import_module("app.routers.deductions")
    monkeypatch.setattr(importlib.import_module("app.main"), "start_deduction_material_worker", lambda: None)
    with TestClient(app) as lead, TestClient(app) as manager:
        login(lead, "TATEST01")
        login(manager, "GSMTEST01")
        options = manager.get("/api/options").json()
        target = next(row for row in manager.get("/api/employee-targets", params={"usage": "deduction", "keyword": "CMTEST01"}).json()["items"] if row["employee_no"] == "CMTEST01")
        category = next(row for row in options["deduction_types"] if row["code"] == "SICK_LEAVE_VIOLATION")
        statement = next(row for row in options["deduction_levels"] if row["code"] == "STATEMENT")
        memo = next(row for row in options["deduction_levels"] if row["code"] == "MEMO")
        form = dict(employee_id=target["id"], deduction_type_id=category["id"], deduction_level_id=statement["id"],
                    occurred_on=month + "-01", description="Concurrent material test")
        pdf = make_pdf()
        if upgrade:
            first = manager.post("/api/deductions", data=form, files={"document": ("first.pdf", pdf, "application/pdf")})
            assert first.status_code == 200, first.text
            first_record = first.json()["record"]
            reviewer = next(row for row in lead.get("/api/sick-leaves/violation-upgrade-preview", params={
                "employee_id": target["id"], "leave_start_date": month + "-05",
            }).json()["reviewers"] if row["employee_no"] == "GSMTEST01")
            if upgrade == "supplement":
                submitted = manager.post("/api/deductions", data={**form, "occurred_on": month + "-05"})
                assert submitted.status_code == 200, submitted.text
                record = submitted.json()["record"]
            elif before_work_order:
                image = BytesIO()
                Image.new("RGB", (16, 16), "white").save(image, format="JPEG")
                submitted = lead.post("/api/deduction-upgrades", data={
                    **form, "occurred_on": month + "-05", "reviewer_id": reviewer["id"],
                }, files={"document_images": ("first-attempt.jpg", image.getvalue(), "image/jpeg")})
                assert submitted.status_code == 200, submitted.text
                record = submitted.json()["record"]
                # Explicitly reproduce an old queued job created before work
                # orders were reserved during asynchronous entry.
                with SessionLocal() as db:
                    legacy = db.get(DeductionRecord, record["id"])
                    legacy_first = db.get(DeductionRecord, first_record["id"])
                    old_order_id = legacy.upgrade_request_id
                    if old_order_id:
                        legacy.upgrade_request_id = legacy_first.upgrade_request_id = None
                        db.query(DeductionUpgradeRequest).filter_by(id=old_order_id).delete()
                        db.commit()
            else:
                submitted = lead.post("/api/sick-leaves", data={
                    "employee_id": target["id"], "leave_start_date": month + "-05", "leave_end_date": month + "-05",
                    "leave_days": "1", "is_violation": "true", "violation_reviewer_id": reviewer["id"],
                }, files={"proof": ("proof.pdf", pdf, "application/pdf")})
                assert submitted.status_code == 200, submitted.text
                record = submitted.json()["violation"]
        else:
            submitted = manager.post("/api/deductions", data=form)
            assert submitted.status_code == 200, submitted.text
            record = submitted.json()["record"]
        assert record["status"] == ("material_processing" if before_work_order else "pending_material")
        record_id = record["id"]
        path = f"/api/deductions/{record_id}/material"
        upgrade_fields = {"reviewer_id": reviewer["id"], "expected_first_id": first_record["id"]} if upgrade == "supplement" else {}
        if upgrade == "supplement":
            preview = manager.get(f"/api/deductions/{record_id}/material-upgrade-preview").json()
            assert preview["eligible"] and preview["default_reviewer_id"] == reviewer["id"]
        queue = "/api/deductions/pending-materials?scope=all"
        failed_job_id = original_order_id = None
        if retry_after_failure:
            if not before_work_order:
                first_attempt = lead.post(path, data=upgrade_fields, files={"document": ("first-attempt.pdf", pdf, "application/pdf")})
                assert first_attempt.status_code == 200, first_attempt.text

            def fail_conversion(*_args):
                raise deduction_materials.MaterialError("test_conversion_failure", "材料生成失败测试")

            converter = "_make_pdf" if before_work_order else "_make_compressed_pdf"
            with monkeypatch.context() as failure:
                failure.setattr(deduction_materials, converter, fail_conversion)
                assert process_next_deduction_material_job()
            with SessionLocal() as db:
                failed_job = db.query(DeductionMaterialJob).filter_by(deduction_id=record_id).one()
                failed_job_id = failed_job.id
                assert failed_job.status == "failed" and failed_job.source_cleanup_status == "done"
                row = db.get(DeductionRecord, record_id)
                assert row.status == "material_failed" and row.material_status == "failed"
                assert not deduction_counts_for_score(row)
                failed_revision = row.material_revision
                if upgrade:
                    original_order_id = row.upgrade_request_id
                    first = db.get(DeductionRecord, first_record["id"])
                    assert deduction_counts_for_score(first)
                    if before_work_order:
                        assert original_order_id is None and first.upgrade_state == "eligible"
                    else:
                        assert db.get(DeductionUpgradeRequest, original_order_id).status == "awaiting_material"
            invalid = manager.post(path, files={"document": ("invalid.pdf", b"invalid PDF", "application/pdf")})
            assert invalid.status_code == 400, invalid.text
            with SessionLocal() as db:
                row = db.get(DeductionRecord, record_id)
                assert (row.status, row.material_revision) == ("material_failed", failed_revision)
                assert db.query(DeductionMaterialJob).filter_by(deduction_id=record_id).one().id == failed_job_id
        for client in (lead, manager):
            assert record_id in {row["id"] for row in client.get(queue).json()["items"]}

        # Both requests must reach upload staging after reading the old state.
        barrier = Barrier(2, timeout=10)
        stage_name = "stage_photo_materials" if before_work_order else "stage_pdf_material"
        stage_material = getattr(routes, stage_name)

        async def synchronized_stage(*args, **kwargs):
            await asyncio.to_thread(barrier.wait)
            return await stage_material(*args, **kwargs)

        monkeypatch.setattr(routes, stage_name, synchronized_stage)
        files = (
            [("document_images", (f"material-{number}.jpg", image.getvalue(), "image/jpeg")) for number in (1, 2)]
            if before_work_order else {"document": ("material.pdf", pdf, "application/pdf")}
        )
        source_count = 2 if before_work_order else 1
        before_paths = {path.name for path in FILE_DIR.iterdir()}
        with SessionLocal() as db:
            before_files = db.query(StoredFile).count()
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(client.post, path, data=upgrade_fields, files=files) for client in (lead, manager)]
            responses = sorted((future.result(timeout=30) for future in pending), key=lambda r: r.status_code)
        assert [r.status_code for r in responses] == [200, 409], [r.text for r in responses]
        assert "已被处理" in responses[1].json()["detail"]
        assert responses[0].json()["record"]["status"] == "material_processing"
        with SessionLocal() as db:
            jobs = db.query(DeductionMaterialJob).filter_by(deduction_id=record_id).order_by(DeductionMaterialJob.id).all()
            assert [job.status for job in jobs] == (["failed", "queued"] if retry_after_failure else ["queued"])
            job = jobs[-1]
            job_id = job.id
            assert db.query(StoredFile).count() == before_files + source_count + 1  # sources and one output placeholder
        assert len({path.name for path in FILE_DIR.iterdir()} - before_paths) == source_count
        for client in (lead, manager):
            assert record_id not in {row["id"] for row in client.get(queue).json()["items"]}
            assert client.post(path, files={"document": ("stale.pdf", pdf, "application/pdf")}).status_code == 409

        assert process_next_deduction_material_job()
        assert not process_next_deduction_material_job()
        with SessionLocal() as db:
            assert db.get(DeductionMaterialJob, job_id).status == "succeeded"
            row = db.get(DeductionRecord, record_id)
            assert row.status == ("pending_upgrade" if upgrade else "active")
            assert row.material_status == "ready"
            if upgrade:
                work_order = db.query(DeductionUpgradeRequest).filter_by(second_deduction_id=record_id).one()
                work_order_id = work_order.id
                if original_order_id is not None:
                    assert work_order_id == original_order_id
                assert work_order.status == "pending"
                assert not deduction_counts_for_score(row)

        if upgrade:
            resolve_path = f"/api/deduction-upgrades/{work_order_id}/resolve"
            decision = dict(decision="approve", result_level_id=memo["id"], issued_confirmed=True, handling_note="Concurrent material approval test")
            approved = manager.post(resolve_path, json=decision)
            assert approved.status_code == 200, approved.text
            assert manager.post(resolve_path, json=decision).status_code == 404
        with SessionLocal() as db:
            rows = db.query(DeductionRecord).filter_by(employee_id=target["id"], deduction_month=month).all()
            counted = [row for row in rows if deduction_counts_for_score(row)]
            if upgrade:
                assert len(rows) == 3
                work_order = db.get(DeductionUpgradeRequest, work_order_id)
                result = db.get(DeductionRecord, work_order.result_deduction_id)
                assert work_order.status == "approved" and result.upgrade_role == "result"
                assert {row.id for row in counted} == {first_record["id"], result.id}
                assert sum(float(row.points) for row in counted) == statement["points"] + memo["points"]
            else:
                assert len(rows) == 1 and [row.id for row in counted] == [record_id]
                assert float(counted[0].points) == statement["points"]
