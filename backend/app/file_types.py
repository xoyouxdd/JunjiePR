"""Protected attachment preview format classification."""
from __future__ import annotations

from app.v2_models import StoredFile


PREVIEW_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"})


PREVIEW_PDF_EXTENSIONS = frozenset({".pdf"})


def is_previewable_image(file_row: StoredFile | None) -> bool:
    """Only active image files use the protected in-page viewer."""
    return bool(file_row and file_row.status == "active" and file_row.extension.lower() in PREVIEW_IMAGE_EXTENSIONS)


def preview_kind(file_row: StoredFile | None) -> str:
    """Return the safe, browser-supported in-page preview type for a stored file."""
    if not file_row or file_row.status != "active":
        return ""
    suffix = file_row.extension.lower()
    if suffix in PREVIEW_IMAGE_EXTENSIONS:
        return "image"
    if suffix in PREVIEW_PDF_EXTENSIONS:
        return "pdf"
    return ""
