from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy.exc import OperationalError

TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-material-recovery-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from app import deduction_materials as materials  # noqa: E402
from app.deduction_materials import (  # noqa: E402
    MaterialError,
    claim_next_deduction_material_job,
    cleanup_pending_material_sources,
    process_next_deduction_material_job,
    worker_tick,
)
from app.v2_database import FILE_DIR, SessionLocal, ensure_material_job_claim_generation, init_db  # noqa: E402
from app.v2_models import AuditLog, DeductionLevel, DeductionMaterialJob, DeductionRecord, DeductionType, Employee, StoredFile  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

init_db()


def _queued_job(*, started_at: datetime | None = None, status: str = "queued") -> tuple[int, Path]:
    FILE_DIR.mkdir(parents=True, exist_ok=True)
    source_key = f"recovery-src-{uuid4().hex}.jpg"
    source_path = FILE_DIR / source_key
    source_path.write_bytes(b"source-bytes")
    with SessionLocal() as db:
        employee = db.query(Employee).filter_by(employee_no="CMTEST01").one()
        deduction_type = db.query(DeductionType).first()
        level = db.query(DeductionLevel).first()
        source = StoredFile(
            storage_key=source_key,
            original_filename="a.jpg",
            extension=".jpg",
            file_size=source_path.stat().st_size,
            sha256="b" * 64,
            uploaded_by=employee.id,
            status="processing_source",
        )
        output = StoredFile(
            storage_key=f"recovery-out-{uuid4().hex}.pdf",
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
            description="材料恢复测试",
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
        return job.id, source_path


def _write_output(name: str) -> tuple[str, int, str]:
    FILE_DIR.mkdir(parents=True, exist_ok=True)
    path = FILE_DIR / name
    path.write_bytes(b"%PDF-1.4 " + name.encode("ascii"))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return name, path.stat().st_size, digest


def _job_state(job_id: int) -> DeductionMaterialJob:
    with SessionLocal() as db:
        job = db.get(DeductionMaterialJob, job_id)
        assert job is not None
        db.expunge(job)
        return job


def test_claim_writes_generation_and_token() -> None:
    job_id, _ = _queued_job()
    with SessionLocal() as db:
        claimed = claim_next_deduction_material_job(db)
    assert claimed == job_id
    job = _job_state(job_id)
    assert job.status == "processing"
    assert job.claim_generation == 1
    assert job.claim_token
    assert len(job.claim_token) == 32


def test_failure_commit_fault_keeps_source_and_processing() -> None:
    job_id, source_path = _queued_job()
    original_commit = materials._commit
    original_make = materials._make_pdf
    commits = {"n": 0}

    def boom(db) -> None:
        commits["n"] += 1
        if commits["n"] >= 2:
            raise OperationalError("COMMIT", {}, Exception("disk"))
        original_commit(db)

    def fail_convert(_sources):
        raise MaterialError("conversion_failed", "受控转换失败")

    materials._commit = boom
    materials._make_pdf = fail_convert
    try:
        assert process_next_deduction_material_job() is False
    finally:
        materials._commit = original_commit
        materials._make_pdf = original_make
    job = _job_state(job_id)
    assert source_path.exists()
    assert job.status == "processing"
    with SessionLocal() as db:
        deduction = db.get(DeductionRecord, job.deduction_id)
        assert deduction is not None
        assert deduction.material_status == "processing"
        audits = db.query(AuditLog).filter_by(entity_type="deduction", entity_id=str(deduction.id)).count()
        assert audits == 0


def test_success_precommit_fault_preserves_source() -> None:
    job_id, source_path = _queued_job()
    original_commit = materials._commit
    original_make = materials._make_pdf
    commits = {"n": 0}

    def boom(db) -> None:
        commits["n"] += 1
        if commits["n"] >= 2:
            raise OperationalError("COMMIT", {}, Exception("disk"))
        original_commit(db)

    def fake_pdf(_sources):
        return _write_output(f"generated-{uuid4().hex}.pdf")

    materials._commit = boom
    materials._make_pdf = fake_pdf
    try:
        assert process_next_deduction_material_job() is False
    finally:
        materials._commit = original_commit
        materials._make_pdf = original_make
    job = _job_state(job_id)
    assert source_path.exists()
    assert job.status == "processing"


def test_failed_conversion_persists_then_deletes_source() -> None:
    job_id, source_path = _queued_job()
    original_make = materials._make_pdf

    def fail_convert(_sources):
        raise MaterialError("conversion_failed", "受控转换失败")

    materials._make_pdf = fail_convert
    try:
        assert process_next_deduction_material_job() is True
    finally:
        materials._make_pdf = original_make
    job = _job_state(job_id)
    assert job.status == "failed"
    assert job.source_cleanup_status == "done"
    assert not source_path.exists()
    with SessionLocal() as db:
        deduction = db.get(DeductionRecord, job.deduction_id)
        assert deduction is not None
        assert deduction.material_status == "failed"
        assert deduction.status == "material_failed"


def test_stale_owner_cannot_overwrite_new_owner() -> None:
    job_id, source_path = _queued_job()
    original_make = materials._make_pdf
    pause = threading.Event()
    proceed = threading.Event()
    results: dict[str, bool] = {}

    def fake_pdf(_sources):
        name = threading.current_thread().name
        if name == "worker-A":
            pause.set()
            assert proceed.wait(timeout=10)
            return _write_output("worker_A_late.pdf")
        return _write_output("worker_B.pdf")

    materials._make_pdf = fake_pdf

    def run_a() -> None:
        results["A"] = process_next_deduction_material_job()

    def run_b() -> None:
        results["B"] = process_next_deduction_material_job()

    try:
        worker_a = threading.Thread(target=run_a, name="worker-A")
        worker_a.start()
        assert pause.wait(timeout=10)
        with SessionLocal() as db:
            db.query(DeductionMaterialJob).filter_by(id=job_id).update(
                {"started_at": datetime.now() - timedelta(minutes=16)}
            )
            db.commit()
        worker_b = threading.Thread(target=run_b, name="worker-B")
        worker_b.start()
        worker_b.join(timeout=10)
        proceed.set()
        worker_a.join(timeout=10)
    finally:
        materials._make_pdf = original_make

    assert results.get("B") is True
    job = _job_state(job_id)
    assert job.status == "succeeded"
    with SessionLocal() as db:
        output = db.get(StoredFile, job.output_file_id)
        deduction = db.get(DeductionRecord, job.deduction_id)
        assert output is not None
        assert output.storage_key == "worker_B.pdf"
        assert deduction is not None
        assert deduction.material_status == "ready"
        audits = db.query(AuditLog).filter_by(entity_type="deduction", entity_id=str(deduction.id), action="材料PDF已就绪").count()
        assert audits == 1
    assert not (FILE_DIR / "worker_A_late.pdf").exists()
    assert (FILE_DIR / "worker_B.pdf").exists()
    assert not source_path.exists()


def test_worker_tick_survives_unexpected_error() -> None:
    original = materials.process_next_deduction_material_job

    def boom() -> bool:
        raise RuntimeError("boom")

    materials.process_next_deduction_material_job = boom
    try:
        assert worker_tick() is False
    finally:
        materials.process_next_deduction_material_job = original


def test_cleanup_pending_is_idempotent() -> None:
    job_id, source_path = _queued_job()
    original_make = materials._make_pdf
    materials._make_pdf = lambda _sources: _write_output(f"ok-{uuid4().hex}.pdf")
    try:
        assert process_next_deduction_material_job() is True
    finally:
        materials._make_pdf = original_make
    assert not source_path.exists()
    assert cleanup_pending_material_sources() is False
    job = _job_state(job_id)
    assert job.source_cleanup_status == "done"


def test_old_job_table_gains_claim_columns(tmp_path: Path) -> None:
    dbfile = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{dbfile.as_posix()}")
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.execute(
            text(
                "CREATE TABLE deduction_material_jobs ("
                "id INTEGER PRIMARY KEY, deduction_id INTEGER NOT NULL, "
                "output_file_id INTEGER NOT NULL, source_file_ids_json TEXT NOT NULL, "
                "mode VARCHAR(30) NOT NULL DEFAULT 'deduction', reviewer_id INTEGER, "
                "first_deduction_id INTEGER, status VARCHAR(30) NOT NULL DEFAULT 'queued', "
                "error_code VARCHAR(50), error_message TEXT, attempts INTEGER NOT NULL DEFAULT 0, "
                "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, started_at DATETIME, completed_at DATETIME)"
            )
        )
        db.commit()
        ensure_material_job_claim_generation(db)
        columns = {row[1] for row in db.execute(text("PRAGMA table_info(deduction_material_jobs)"))}
        assert "claim_generation" in columns
        assert "claim_token" in columns
        assert "source_cleanup_status" in columns
        ensure_material_job_claim_generation(db)
        names = {row[1] for row in db.execute(text("PRAGMA index_list(deduction_material_jobs)"))}
        assert "ix_deduction_material_job_cleanup" in names
