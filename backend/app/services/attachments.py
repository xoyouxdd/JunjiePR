"""Bounded attachment storage with an explicit storage-directory dependency.

The service has no database configuration dependency. Callers pass file_dir and
own transaction completion; failed uploads and flushes remove partial files.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.v2_models import StoredFile


BUSINESS_ATTACHMENT_EFFECTIVE_DATE = "2026-09-01"


BUSINESS_ATTACHMENT_MAX_BYTES = 100 * 1024 * 1024


UPLOAD_CHUNK_BYTES = 1024 * 1024


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
    except Exception:
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
    return None


async def save_image_upload(db: Session, upload: UploadFile, uploader_id: int, *, file_dir: Path, max_bytes=BUSINESS_ATTACHMENT_MAX_BYTES, persist: bool = True) -> StoredFile:
    staging_key = f"{uuid4().hex}.upload"
    staging_path = file_dir / staging_key
    file_size, digest = await _stream_upload_to_path(upload, staging_path, max_bytes=max_bytes, empty_message="认可图片不能为空", label="认可图片")
    with staging_path.open("rb") as source:
        detected = detect_image_type(source.read(16))
    if not detected:
        staging_path.unlink(missing_ok=True)
        raise HTTPException(400, "认可图片格式无效，仅支持JPG、PNG、WebP")
    extension, mime_type = detected
    storage_key = f"{uuid4().hex}{extension}"
    path = file_dir / storage_key
    staging_path.replace(path)
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
