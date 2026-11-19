"""Attachment storage bound to the configured data directory.

``services.attachments`` takes ``file_dir`` explicitly; these wrappers read the
module-level ``FILE_DIR`` at call time so tests can point it at a temporary
directory with ``monkeypatch.setattr(upload_storage, "FILE_DIR", ...)``.
"""
from __future__ import annotations

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.database.connection import FILE_DIR
from app.services import attachments as _attachments
from app.services.attachments import BUSINESS_ATTACHMENT_MAX_BYTES
from app.v2_models import StoredFile


async def save_upload(
    db: Session,
    upload: UploadFile,
    uploader_id: int,
    *,
    allowed_extensions: set[str],
    max_bytes=BUSINESS_ATTACHMENT_MAX_BYTES,
) -> StoredFile:
    return await _attachments.save_upload(
        db, upload, uploader_id, file_dir=FILE_DIR,
        allowed_extensions=allowed_extensions, max_bytes=max_bytes,
    )


async def save_image_upload(
    db: Session,
    upload: UploadFile,
    uploader_id: int,
    *,
    max_bytes=BUSINESS_ATTACHMENT_MAX_BYTES,
    persist: bool = True,
) -> StoredFile:
    return await _attachments.save_image_upload(
        db, upload, uploader_id, file_dir=FILE_DIR,
        max_bytes=max_bytes, persist=persist,
    )


def remove_upload_file(file_row: StoredFile | None) -> None:
    _attachments.remove_upload_file(file_row, file_dir=FILE_DIR)
