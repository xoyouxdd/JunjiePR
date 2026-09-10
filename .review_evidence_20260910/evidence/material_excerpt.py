# Excerpt retrieved from backend/app/deduction_materials.py at
# 1c9da27ac3844c34f7f4aa2980ce2291690d9dea, blob a1b6cc77c7fa0eedd557ca17c14408f5166c7810.
# Original function bodies; dependencies are injected by the isolated harness.

def _restore_upgrade_source(db, job: DeductionMaterialJob) -> None:
    if job.mode not in {"upgrade", "upgrade_pdf_compress"} or not job.first_deduction_id:
        return
    first = db.get(DeductionRecord, job.first_deduction_id)
    if first and not first.upgrade_request_id and first.upgrade_state == "material_processing":
        first.upgrade_role = None
        first.upgrade_state = "eligible"


def _discard_failed_sources(db, job: DeductionMaterialJob) -> None:
    """Remove transient photo originals after a failed conversion.

    The retry action stages a new set of originals.  Retaining old photos after a
    conversion failure would serve no product purpose and would unnecessarily
    extend the lifetime of sensitive material.
    """
    try:
        source_ids = [int(value) for value in json.loads(job.source_file_ids_json or "[]")]
    except (TypeError, ValueError, json.JSONDecodeError):
        source_ids = []
    for source_id in source_ids:
        source = db.get(StoredFile, source_id)
        if not source:
            continue
        try:
            _safe_file_path(source.storage_key).unlink(missing_ok=True)
        except MaterialError:
            pass
        source.status = "conversion_failed"


STALE_PROCESSING_AFTER = timedelta(minutes=15)


def _claimable_material_jobs(now: datetime):
    stale_before = now - STALE_PROCESSING_AFTER
    return or_(
        DeductionMaterialJob.status == "queued",
        and_(DeductionMaterialJob.status == "processing", DeductionMaterialJob.started_at < stale_before),
    )


def claim_next_deduction_material_job(db, *, now: datetime | None = None) -> int | None:
    """Atomically take one queued or stale job. Returns None if another worker won."""
    now = now or datetime.now()
    claimable = _claimable_material_jobs(now)
    candidate_id = (
        db.query(DeductionMaterialJob.id)
        .filter(claimable)
        .order_by(DeductionMaterialJob.created_at.asc(), DeductionMaterialJob.id.asc())
        .limit(1)
        .scalar()
    )
    if not candidate_id:
        return None
    result = db.execute(
        update(DeductionMaterialJob)
        .where(DeductionMaterialJob.id == candidate_id, claimable)
        .values(
            status="processing",
            attempts=DeductionMaterialJob.attempts + 1,
            started_at=now,
            error_code=None,
            error_message=None,
        )
    )
    db.commit()
    if result.rowcount != 1:
        return None
    return int(candidate_id)


def process_next_deduction_material_job() -> bool:
    """Claim and complete one job. Safe to call repeatedly and across restarts."""
    db = SessionLocal()
    job_id: int | None = None
    source_keys: list[str] = []
    try:
        job_id = claim_next_deduction_material_job(db)
        if not job_id:
            return False
        job = db.get(DeductionMaterialJob, job_id)
        if not job:
            return False
        deduction = db.get(DeductionRecord, job.deduction_id)
        output = db.get(StoredFile, job.output_file_id)
        source_ids = [int(value) for value in json.loads(job.source_file_ids_json)]
        sources = [db.get(StoredFile, value) for value in source_ids]
        if not deduction or not output or any(row is None for row in sources):
            raise MaterialError("source_missing", "材料源文件不存在，请重新提交材料")
        key, size, digest = (_make_compressed_pdf(sources[0]) if job.mode in {"pdf_compress", "upgrade_pdf_compress"} else _make_pdf([row for row in sources if row]))
        output.storage_key = key
        output.original_filename = "PDF材料已优化.pdf" if job.mode == "pdf_compress" else "照片材料合成.pdf"
        output.extension = ".pdf"
        output.mime_type = "application/pdf"
        output.file_size = size
        output.sha256 = digest
        output.status = "active"
        source_keys = [row.storage_key for row in sources if row and row.storage_key]
        for source in sources:
            source.status = "converted"
        deduction.material_status = "ready"
        deduction.material_error = None
        deduction.material_revision = int(deduction.material_revision or 0) + 1
        if job.mode in {"upgrade", "upgrade_pdf_compress"}:
            _finalize_upgrade(db, job, deduction)
        else:
            deduction.status = "active"
            follow_ups = db.query(DeductionFollowUp).filter_by(employee_id=deduction.employee_id, deduction_type_id=deduction.deduction_type_id, occurred_on=deduction.occurred_on, status="pending").all()
            for follow_up in follow_ups:
                follow_up.status = "issued"
                follow_up.issued_deduction_id = deduction.id
                follow_up.issued_by = deduction.submitter_id
                follow_up.issued_by_name = deduction.submitter_name
                follow_up.issued_at = datetime.now()
        job.status = "succeeded"
        job.completed_at = datetime.now()
        _write_audit(db, "材料PDF已就绪", deduction, {"material_status": "ready", "source_type": deduction.material_source_type, "source_count": len(sources), "job_id": job.id})
        db.commit()
        for storage_key in source_keys:
            try:
                _safe_file_path(storage_key).unlink(missing_ok=True)
            except MaterialError:
                pass
        return True
    except MaterialError as exc:
        db.rollback()
        job = db.get(DeductionMaterialJob, job_id) if job_id else None
        if job:
            deduction = db.get(DeductionRecord, job.deduction_id)
            if deduction:
                deduction.status = "material_failed"
                deduction.material_status = "failed"
                deduction.material_error = exc.message
                deduction.upgrade_state = "material_failed" if job.mode == "upgrade" else deduction.upgrade_state
                _restore_upgrade_source(db, job)
                _discard_failed_sources(db, job)
                _write_audit(db, "照片材料生成失败", deduction, {"material_status": "failed", "error_code": exc.code, "job_id": job.id})
            job.status, job.error_code, job.error_message, job.completed_at = "failed", exc.code, exc.message, datetime.now()
            db.commit()
        return True
    except Exception:
        db.rollback()
        return True
    finally:
        db.close()
