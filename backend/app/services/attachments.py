"""Bounded attachment storage with an explicit storage-directory dependency.

The service has no database configuration dependency. Callers pass file_dir and
own transaction completion; failed uploads and flushes remove partial files.
"""
from __future__ import annotations

import hashlib
import warnings
from pathlib import Path
from uuid import uuid4
from PIL import Image, UnidentifiedImageError
from pillow_heif import get_file_mimetype, register_heif_opener
from anyio import CapacityLimiter, to_thread
from anyio.lowlevel import RunVar

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.v2_models import StoredFile


register_heif_opener()
RECOGNITION_IMAGE_FORMATS = "JPG/JPEG、PNG、WebP、HEIC/HEIF"


BUSINESS_ATTACHMENT_EFFECTIVE_DATE = "2026-09-01"


BUSINESS_ATTACHMENT_MAX_BYTES = 100 * 1024 * 1024


UPLOAD_CHUNK_BYTES = 1024 * 1024

# Follow Pillow's normal safety threshold; do not reject ordinary 48MP photos.
# A byte-size limit alone cannot bound decompressed image resources.
BUSINESS_IMAGE_MAX_PIXELS = 89_478_485
_decode_limiter = RunVar[CapacityLimiter]("recognition_image_decode_limiter")


async def _validate_image_in_worker(path: Path) -> tuple[str, str]:
    # Per-event-loop limiter avoids sharing locks between TestClient lifespans.
    # Bound decoding before reserving a thread, keeping other requests responsive.
    try:
        limiter = _decode_limiter.get()
    except LookupError:
        limiter = CapacityLimiter(2)
        _decode_limiter.set(limiter)
    return await to_thread.run_sync(validate_image_file, path, limiter=limiter)


def validate_image_file(path: Path) -> tuple[str, str]:
    """Verify structure and decode the preview frame before persisting a record."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                detected = {"JPEG": (".jpg", "image/jpeg"), "PNG": (".png", "image/png"),
                            "WEBP": (".webp", "image/webp")}.get(image.format)
                if image.format == "HEIF":
                    with path.open("rb") as source:
                        detected = detect_image_type(source.read(16))
                    if detected and detected[0] not in {".heic", ".heif"}:
                        detected = None
                if not detected:
                    raise HTTPException(400, f"认可图片格式无效，仅支持{RECOGNITION_IMAGE_FORMATS}")
                if image.width * image.height > BUSINESS_IMAGE_MAX_PIXELS:
                    raise HTTPException(400, "认可图片像素过大，请压缩图片尺寸后重新选择")
                image.verify()
            with Image.open(path) as image:
                image.load()
            return detected
    except HTTPException:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(400, "认可图片像素过大，请压缩后重新选择") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise HTTPException(400, f"认可图片已损坏或无法读取，请重新选择{RECOGNITION_IMAGE_FORMATS}图片") from exc


async def _stream_upload_to_path(upload: UploadFile, path: Path, *, max_bytes: int, empty_message: str, label: str) -> tuple[int, str]:
    """Persist a bounded upload without retaining a 100MB request in RAM."""
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("wb") as target:
            while True:
                chunk = await upload.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(400, f"{label}不能超过{max_bytes // 1024 // 1024}MB")
                digest.update(chunk)
                target.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if not total:
        path.unlink(missing_ok=True)
        raise HTTPException(400, empty_message)
    return total, digest.hexdigest()


async def save_upload(db: Session, upload: UploadFile, uploader_id: int, *, file_dir: Path, allowed_extensions: set[str], max_bytes=BUSINESS_ATTACHMENT_MAX_BYTES) -> StoredFile:
    extension = Path(upload.filename or "").suffix.lower()
    if extension not in allowed_extensions:
        raise HTTPException(400, f"文件格式不支持，仅允许：{', '.join(sorted(allowed_extensions))}")
    storage_key = f"{uuid4().hex}{extension}"
    path = file_dir / storage_key
    file_size, digest = await _stream_upload_to_path(upload, path, max_bytes=max_bytes, empty_message="文件不能为空", label="文件")
    record = StoredFile(
        storage_key=storage_key,
        original_filename=upload.filename or storage_key,
        extension=extension,
        mime_type=upload.content_type,
        file_size=file_size,
        sha256=digest,
        uploaded_by=uploader_id,
        status="active",
    )
    db.add(record)
    try:
        db.flush()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return record


def detect_image_type(content: bytes) -> tuple[str, str] | None:
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp", "image/webp"
    if len(content) >= 12 and content[4:8] == b"ftyp":
        mime_type = get_file_mimetype(content)
        if mime_type in {"image/heic", "image/heic-sequence"}:
            return ".heic", "image/heic"
        if mime_type in {"image/heif", "image/heif-sequence"}:
            return ".heif", "image/heif"
    return None


async def save_image_upload(db: Session, upload: UploadFile, uploader_id: int, *, file_dir: Path, max_bytes=BUSINESS_ATTACHMENT_MAX_BYTES, persist: bool = True) -> StoredFile:
    staging_key = f"{uuid4().hex}.upload"
    staging_path = file_dir / staging_key
    file_size, digest = await _stream_upload_to_path(upload, staging_path, max_bytes=max_bytes, empty_message="认可图片不能为空", label="认可图片")
    try:
        with staging_path.open("rb") as source:
            if not detect_image_type(source.read(16)):
                raise HTTPException(400, f"认可图片格式无效，仅支持{RECOGNITION_IMAGE_FORMATS}")
        detected = await _validate_image_in_worker(staging_path)
        extension, mime_type = detected
        storage_key = f"{uuid4().hex}{extension}"
        path = file_dir / storage_key
        staging_path.replace(path)
    except BaseException:
        staging_path.unlink(missing_ok=True)
        raise
    record = StoredFile(
        storage_key=storage_key,
        original_filename=upload.filename or storage_key,
        extension=extension,
        mime_type=mime_type,
        file_size=file_size,
        sha256=digest,
        uploaded_by=uploader_id,
        status="active",
    )
    if not persist:
        return record
    db.add(record)
    try:
        db.flush()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return record


def remove_upload_file(file_row: StoredFile | None, *, file_dir: Path) -> None:
    if not file_row:
        return
    path = (file_dir / file_row.storage_key).resolve()
    if file_dir.resolve() in path.parents:
        path.unlink(missing_ok=True)
