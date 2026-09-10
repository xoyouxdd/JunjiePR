from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-material-claim-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from app.deduction_materials import claim_next_deduction_material_job  # noqa: E402
from app.v2_database import SessionLocal, init_db  # noqa: E402
from app.v2_models import DeductionLevel, DeductionMaterialJob, DeductionRecord, DeductionType, Employee, StoredFile  # noqa: E402

init_db()


def _queued_job(*, started_at: datetime | None = None, status: str = "queued") -> int:
    with SessionLocal() as db:
        employee = db.query(Employee).filter_by(employee_no="CMTEST01").one()
        deduction_type = db.query(DeductionType).first()
        level = db.query(DeductionLevel).first()
        source = StoredFile(
            storage_key=f"claim-src-{uuid4().hex}.jpg",
            original_filename="a.jpg",
            extension=".jpg",
            file_size=1,
            sha256="b" * 64,
            uploaded_by=employee.id,
            status="active",
        )
        output = StoredFile(
            storage_key=f"claim-out-{uuid4().hex}.pdf",
            original_filename="out.pdf",
            extension=".pdf",
            file_size=0,
            sha256="0" * 64,
            uploaded_by=employee.id,
            status="pending_conversion",
        )
        db.add_all([source, output])
        db.flush()
        record = DeductionRecord(
            employee_id=employee.id,
            employee_no=employee.employee_no,
            employee_name=employee.name,
            employee_role_snapshot="CM",
            deduction_type_id=deduction_type.id,
            deduction_type_name=deduction_type.name,
            deduction_level_id=level.id,
            deduction_level_name=level.name,
            points=Decimal("1.00"),
            occurred_on=date.today().isoformat(),
            deduction_month=date.today().isoformat()[:7],
            description="材料领取测试",
            document_file_id=output.id,
            submitter_id=employee.id,
            submitter_name=employee.name,
            submitter_role_snapshot="CM",
            permission_scope_snapshot="test",
            status="material_processing",
            material_status="processing",
        )
        db.add(record)
        db.flush()
        job = DeductionMaterialJob(
            deduction_id=record.id,
            output_file_id=output.id,
            source_file_ids_json=json.dumps([source.id]),
            mode="deduction",
            status=status,
            started_at=started_at,
            attempts=1 if status == "processing" else 0,
        )
        db.add(job)
        db.commit()
        return job.id


def test_two_workers_can_claim_only_one_queued_job() -> None:
    job_id = _queued_job()
    barrier = threading.Barrier(2)
    claimed: list[int | None] = []

    def run() -> None:
        barrier.wait(timeout=5)
        with SessionLocal() as db:
            claimed.append(claim_next_deduction_material_job(db))

    workers = [threading.Thread(target=run) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert claimed.count(job_id) == 1
    assert claimed.count(None) == 1
    with SessionLocal() as db:
        job = db.get(DeductionMaterialJob, job_id)
        assert job is not None
        assert job.status == "processing"
        assert job.attempts == 1


def test_stale_processing_job_can_be_reclaimed_once() -> None:
    stale = datetime.now() - timedelta(minutes=20)
    job_id = _queued_job(status="processing", started_at=stale)
    with SessionLocal() as db:
        first = claim_next_deduction_material_job(db)
        second = claim_next_deduction_material_job(db)
    assert first == job_id
    assert second is None
    with SessionLocal() as db:
        job = db.get(DeductionMaterialJob, job_id)
        assert job is not None
        assert job.status == "processing"
        assert job.attempts == 2
