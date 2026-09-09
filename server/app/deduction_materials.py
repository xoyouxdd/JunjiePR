"""Durable, bounded mobile-photo to PDF processing for deduction materials."""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import or_
import fitz

from app.v2_database import FILE_DIR, SessionLocal
from app.v2_models import AuditLog, DeductionFollowUp, DeductionMaterialJob, DeductionRecord, DeductionUpgradeRequest, Employee, StoredFile

try:  # HEIC is optional at import time but bundled in the application runtime.
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:  # pragma: no cover - upload validation returns a clear error
    pass


MAX_PHOTOS = 6
MATERIAL_RULE_EFFECTIVE_DATE = "2026-09-01"
MAX_PHOTO_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_OUTPUT_BYTES = 100 * 1024 * 1024
MAX_PDF_UPLOAD_BYTES = 100 * 1024 * 1024
PDF_HIGH_QUALITY_OPTIMIZATION_THRESHOLD = 30 * 1024 * 1024
MAX_PDF_PAGES = 6
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
_worker_started = False
_worker_lock = threading.Lock()
_worker_stop = threading.Event()


class MaterialError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _safe_file_path(storage_key: str) -> Path:
    path = (FILE_DIR / storage_key).resolve()
    if FILE_DIR.resolve() not in path.parents:
        raise MaterialError("material_path_invalid", "材料文件路径无效")
    return path


def _verify_image_path(path: Path) -> None:
    try:
        with Image.open(path) as source:
            width, height = source.size
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise MaterialError("image_dimensions", "照片分辨率过大，请选择40MP以内的照片")
            source.verify()
    except MaterialError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise MaterialError("image_invalid", "照片无法读取，请重新拍摄或选择图片") from exc


async def stage_photo_materials(db, uploads: list[UploadFile], uploader_id: int) -> list[StoredFile]:
    rows = [item for item in uploads if item and item.filename]
    if not rows:
        raise HTTPException(400, "请拍照或从相册选择至少1张声明材料")
    if len(rows) > MAX_PHOTOS:
        raise HTTPException(400, f"一次最多选择{MAX_PHOTOS}张照片")
    total = 0
    staged: list[StoredFile] = []
    current_path: Path | None = None
    try:
        for upload in rows:
            extension = Path(upload.filename or "").suffix.lower()
            if extension not in PHOTO_EXTENSIONS:
                raise HTTPException(400, "照片仅支持 JPG/JPEG、PNG、HEIC、WebP")
            storage_key = f"material-source-{uuid4().hex}{extension}"
            path = _safe_file_path(storage_key)
            current_path = path
            FILE_DIR.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            file_size = 0
            try:
                with path.open("wb") as target:
                    while True:
                        chunk = await upload.read(1024 * 1024)
                        if not chunk:
                            break
                        file_size += len(chunk)
                        if file_size > MAX_PHOTO_BYTES:
                            raise HTTPException(400, "单张照片不能超过25MB")
                        digest.update(chunk)
                        target.write(chunk)
            except Exception:
                path.unlink(missing_ok=True)
                raise
            if not file_size:
                path.unlink(missing_ok=True)
                raise HTTPException(400, "照片不能为空，请重新选择")
            total += file_size
            if total > MAX_TOTAL_BYTES:
                path.unlink(missing_ok=True)
                raise HTTPException(400, "全部照片合计不能超过100MB")
            _verify_image_path(path)
            row = StoredFile(
                storage_key=storage_key,
                original_filename=Path(upload.filename or storage_key).name,
                extension=extension,
                mime_type=upload.content_type or "image/*",
                file_size=file_size,
                sha256=digest.hexdigest(),
                uploaded_by=uploader_id,
                status="processing_source",
            )
            db.add(row)
            db.flush()
            staged.append(row)
            current_path = None
        return staged
    except Exception:
        if current_path:
            current_path.unlink(missing_ok=True)
        for row in staged:
            _safe_file_path(row.storage_key).unlink(missing_ok=True)
        raise


async def stage_pdf_material(db, upload: UploadFile, uploader_id: int) -> StoredFile:
    """Store a bounded PDF source for asynchronous compression.

    The source is never exposed through the normal attachment endpoint and is
    removed after either a successful conversion or a failed job.
    """
    if not upload or not upload.filename or Path(upload.filename).suffix.lower() != ".pdf":
        raise HTTPException(400, "请选择可读取的PDF文件")
    storage_key = f"material-pdf-source-{uuid4().hex}.pdf"
    path = _safe_file_path(storage_key)
    FILE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    file_size = 0
    try:
        with path.open("wb") as target:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                file_size += len(chunk)
                if file_size > MAX_PDF_UPLOAD_BYTES:
                    raise HTTPException(400, "PDF不能超过100MB")
                digest.update(chunk)
                target.write(chunk)
        if not file_size:
            raise HTTPException(400, "PDF不能为空，请重新选择")
        document = fitz.open(path)
        page_count = document.page_count
        document.close()
    except Exception as exc:
        path.unlink(missing_ok=True)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(400, "PDF无法读取，请重新选择文件") from exc
    if page_count < 1 or page_count > MAX_PDF_PAGES:
        raise HTTPException(400, f"当前PDF共{page_count}页，最多支持{MAX_PDF_PAGES}页，请拆分后重新提交")
    row = StoredFile(
        storage_key=storage_key,
        original_filename=Path(upload.filename).name,
        extension=".pdf",
        mime_type="application/pdf",
        file_size=file_size,
        sha256=digest.hexdigest(),
        uploaded_by=uploader_id,
        status="processing_source",
    )
    db.add(row)
    db.flush()
    return row


def create_pdf_placeholder(db, uploader_id: int) -> StoredFile:
    row = StoredFile(
        storage_key=f"material-pending-{uuid4().hex}.pdf",
        original_filename="照片材料正在生成.pdf",
        extension=".pdf",
        mime_type="application/pdf",
        file_size=0,
        sha256="0" * 64,
        uploaded_by=uploader_id,
        status="pending_conversion",
    )
    db.add(row)
    db.flush()
    return row


def queue_photo_material_job(db, deduction: DeductionRecord, output: StoredFile, sources: list[StoredFile], *, mode: str = "deduction", reviewer_id: int | None = None, first_deduction_id: int | None = None) -> DeductionMaterialJob:
    job = DeductionMaterialJob(
        deduction_id=deduction.id,
        output_file_id=output.id,
        source_file_ids_json=json.dumps([row.id for row in sources]),
        mode=mode,
        reviewer_id=reviewer_id,
        first_deduction_id=first_deduction_id,
        status="queued",
    )
    db.add(job)
    db.flush()
    deduction.material_status = "processing"
    deduction.material_error = None
    deduction.material_source_type = "photos"
    deduction.material_job_id = job.id
    return job


def _make_pdf(source_rows: list[StoredFile]) -> tuple[str, int, str]:
    pages: list[Image.Image] = []
    output_key = f"deduction-photo-pdf-{uuid4().hex}.pdf"
    output_path = _safe_file_path(output_key)
    temporary_path = output_path.with_suffix(".tmp")
    try:
        for row in source_rows:
            path = _safe_file_path(row.storage_key)
            if not path.exists():
                raise MaterialError("source_missing", "原照片已失效，请重新提交材料")
            with Image.open(path) as opened:
                opened.load()
                if opened.width * opened.height > MAX_IMAGE_PIXELS:
                    raise MaterialError("image_dimensions", "照片分辨率过大，请重新提交材料")
                image = ImageOps.exif_transpose(opened)
                if image.mode in {"RGBA", "LA"}:
                    background = Image.new("RGB", image.size, "white")
                    background.paste(image, mask=image.getchannel("A"))
                    image = background
                elif image.mode != "RGB":
                    image = image.convert("RGB")
                image.thumbnail((1654, 2339), Image.Resampling.LANCZOS)
                page = Image.new("RGB", (1654, 2339), "white")
                page.paste(image, ((page.width - image.width) // 2, (page.height - image.height) // 2))
                pages.append(page)
        if not pages:
            raise MaterialError("source_missing", "未找到可转换的照片")
        FILE_DIR.mkdir(parents=True, exist_ok=True)
        pages[0].save(temporary_path, "PDF", save_all=True, append_images=pages[1:], resolution=200.0, quality=90)
        size = temporary_path.stat().st_size
        if size > MAX_OUTPUT_BYTES:
            raise MaterialError("pdf_too_large", "生成的PDF超过100MB，请减少照片数量或重新选择")
        temporary_path.replace(output_path)
        content = output_path.read_bytes()
        return output_key, size, hashlib.sha256(content).hexdigest()
    except MaterialError:
        raise
    except Exception as exc:
        raise MaterialError("conversion_failed", "照片生成PDF失败，请重新提交材料") from exc
    finally:
        temporary_path.unlink(missing_ok=True)
        for page in pages:
            page.close()


def _make_compressed_pdf(source_row: StoredFile) -> tuple[str, int, str]:
    """Keep text/vector PDFs lossless first; only rasterize if the source exceeds the 100MB delivery bound."""
    source_path = _safe_file_path(source_row.storage_key)
    if not source_path.exists():
        raise MaterialError("source_missing", "原PDF已失效，请重新提交材料")
    try:
        source = fitz.open(source_path)
        if source.page_count < 1 or source.page_count > MAX_PDF_PAGES:
            raise MaterialError("pdf_pages", f"当前PDF共{source.page_count}页，最多支持{MAX_PDF_PAGES}页")
        # First retain vector/text content where possible. If that is still too
        # large, create a readable 144dpi scan copy with conservative JPEG quality.
        compact_key = f"deduction-pdf-compressed-{uuid4().hex}.pdf"
        compact_path = _safe_file_path(compact_key)
        temp_path = compact_path.with_suffix(".tmp")
        try:
            source.save(temp_path, garbage=4, deflate=True, clean=True)
            if temp_path.stat().st_size <= MAX_OUTPUT_BYTES:
                temp_path.replace(compact_path)
                content = compact_path.read_bytes()
                return compact_key, len(content), hashlib.sha256(content).hexdigest()
            temp_path.unlink(missing_ok=True)
            for scale, quality in ((2.0, 90), (1.7, 86), (1.5, 82), (1.2, 78)):
                output = fitz.open()
                for page in source:
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                    target = output.new_page(width=page.rect.width, height=page.rect.height)
                    target.insert_image(target.rect, stream=pixmap.tobytes("jpeg", jpg_quality=quality))
                output.save(temp_path, garbage=4, deflate=True)
                output.close()
                if temp_path.stat().st_size <= MAX_OUTPUT_BYTES:
                    temp_path.replace(compact_path)
                    content = compact_path.read_bytes()
                    return compact_key, len(content), hashlib.sha256(content).hexdigest()
                temp_path.unlink(missing_ok=True)
            raise MaterialError("pdf_too_large", "PDF无法自动优化至100MB，请拆分后重新提交")
        finally:
            temp_path.unlink(missing_ok=True)
            source.close()
    except MaterialError:
        raise
    except Exception as exc:
        raise MaterialError("pdf_compression_failed", "PDF压缩失败，请重新选择文件") from exc


def _write_audit(db, action: str, deduction: DeductionRecord, after: dict) -> None:
    db.add(AuditLog(operator_id=None, operator_name="系统材料任务", action=action, entity_type="deduction", entity_id=str(deduction.id), after_json=json.dumps(after, ensure_ascii=False, default=str)))


def _finalize_upgrade(db, job: DeductionMaterialJob, deduction: DeductionRecord) -> None:
    first = db.get(DeductionRecord, job.first_deduction_id) if job.first_deduction_id else None
    reviewer = db.get(Employee, job.reviewer_id) if job.reviewer_id else None
    if not first or not reviewer:
        raise MaterialError("upgrade_unavailable", "升级声明状态已变化，请重新登记")
    request = (
        db.query(DeductionUpgradeRequest)
        .filter_by(
            first_deduction_id=first.id,
            second_deduction_id=deduction.id,
            status="awaiting_material",
        )
        .first()
    )
    if request:
        if first.upgrade_request_id != request.id or deduction.upgrade_request_id != request.id:
            raise MaterialError("upgrade_unavailable", "升级声明关联状态已变化，请重新登记")
        request.status = "pending"
        request.reviewer_id, request.reviewer_name = reviewer.id, reviewer.name
    elif first.upgrade_request_id:
        raise MaterialError("upgrade_unavailable", "升级声明已被其他工单使用，请重新登记")
    else:
        request = DeductionUpgradeRequest(
            employee_id=deduction.employee_id,
            deduction_type_id=deduction.deduction_type_id,
            first_deduction_id=first.id,
            second_deduction_id=deduction.id,
            reviewer_id=reviewer.id,
            reviewer_name=reviewer.name,
            submitted_by=deduction.submitter_id,
            submitted_by_name=deduction.submitter_name,
            status="pending",
        )
        db.add(request)
        db.flush()
    first.upgrade_request_id = request.id
    first.upgrade_role, first.upgrade_state = "source_first", "pending"
    deduction.upgrade_request_id = request.id
    deduction.upgrade_role, deduction.upgrade_state = "source_second", "pending"
    deduction.status = "pending_upgrade"


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


def process_next_deduction_material_job() -> bool:
    """Claim and complete one job. Safe to call repeatedly and across restarts."""
    db = SessionLocal()
    job_id: int | None = None
    try:
        stale_before = datetime.now() - timedelta(minutes=15)
        job = (
            db.query(DeductionMaterialJob)
            .filter(or_(DeductionMaterialJob.status == "queued", (DeductionMaterialJob.status == "processing") & (DeductionMaterialJob.started_at < stale_before)))
            .order_by(DeductionMaterialJob.created_at.asc(), DeductionMaterialJob.id.asc())
            .first()
        )
        if not job:
            return False
        job_id = job.id
        job.status = "processing"
        job.attempts = int(job.attempts or 0) + 1
        job.started_at = datetime.now()
        job.error_code = None
        job.error_message = None
        db.commit()

        job = db.get(DeductionMaterialJob, job_id)
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
        for source in sources:
            _safe_file_path(source.storage_key).unlink(missing_ok=True)
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
        if job_id:
            job = db.get(DeductionMaterialJob, job_id)
            if job:
                deduction = db.get(DeductionRecord, job.deduction_id)
                if deduction:
                    deduction.status, deduction.material_status, deduction.material_error = "material_failed", "failed", "照片生成PDF失败，请重新提交材料"
                    _restore_upgrade_source(db, job)
                    _discard_failed_sources(db, job)
                    _write_audit(db, "照片材料生成失败", deduction, {"material_status": "failed", "error_code": "conversion_failed", "job_id": job.id})
                job.status, job.error_code, job.error_message, job.completed_at = "failed", "conversion_failed", "照片生成PDF失败，请重新提交材料", datetime.now()
                db.commit()
        return True
    finally:
        db.close()


def start_deduction_material_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True

    def run() -> None:
        while not _worker_stop.is_set():
            processed = process_next_deduction_material_job()
            _worker_stop.wait(0.35 if processed else 1.5)

    threading.Thread(target=run, name="deduction-photo-pdf-worker", daemon=True).start()
