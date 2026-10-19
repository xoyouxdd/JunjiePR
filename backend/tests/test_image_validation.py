"""New image uploads must be decodable before a database record is accepted."""
import asyncio
import hashlib
import threading
import time
from io import BytesIO

from fastapi import HTTPException, UploadFile
from PIL import Image
import pytest

from app.services import attachments


def picture(format="PNG"):
    stream = BytesIO()
    Image.new("RGB", (4, 4), "white").save(stream, format=format)
    return stream.getvalue()


def save(tmp_path, content, **kwargs):
    return asyncio.run(attachments.save_image_upload(None, UploadFile(BytesIO(content), filename="phone-photo.jpg"),
        1, file_dir=tmp_path, persist=False, **kwargs))


@pytest.mark.parametrize("format,extension", [("PNG", ".png"), ("JPEG", ".jpg"), ("WEBP", ".webp")])
def test_normal_phone_formats_keep_original_bytes(tmp_path, format, extension):
    data = picture(format)
    row = save(tmp_path, data)
    assert row.extension == extension
    assert (tmp_path / row.storage_key).read_bytes() == data


@pytest.mark.parametrize("filename,mime_type", [
    ("IMG_4845.jpeg", "image/jpeg"), ("photo.heic", "image/heic"),
    ("photo.heif", "application/octet-stream"),
])
def test_iphone_heif_is_detected_by_content_and_remains_previewable(tmp_path, filename, mime_type):
    from starlette.datastructures import Headers
    from app.v2_watermark import watermark_image
    data = picture("HEIF")
    upload = UploadFile(BytesIO(data), filename=filename, headers=Headers({"content-type": mime_type}))
    row = asyncio.run(attachments.save_image_upload(None, upload, 1, file_dir=tmp_path, persist=False))
    assert row.extension == ".heic" and row.mime_type == "image/heic"
    assert row.original_filename == filename
    assert row.sha256 == hashlib.sha256(data).hexdigest()
    assert (tmp_path / row.storage_key).read_bytes() == data
    content, preview_type = watermark_image(tmp_path / row.storage_key, "TEST", max_dimension=1920)
    assert preview_type == "image/jpeg"
    with Image.open(BytesIO(content)) as preview:
        preview.load()
        assert preview.format == "JPEG" and preview.size == (4, 4)


@pytest.mark.parametrize("data", [b"\x00\x00\x00\x18ftypheic" + b"\0" * 12, b"\x00\x00\x00\x18ftypavif" + b"\0" * 12])
def test_forged_heif_or_unapproved_avif_is_rejected_and_cleaned(tmp_path, data):
    with pytest.raises(HTTPException) as exc:
        save(tmp_path, data)
    assert exc.value.status_code == 400
    assert not list(tmp_path.iterdir())


def test_heif_pixel_limit_is_not_bypassed(tmp_path, monkeypatch):
    data = picture("HEIF")
    monkeypatch.setattr(attachments, "BUSINESS_IMAGE_MAX_PIXELS", 15)
    with pytest.raises(HTTPException) as exc:
        save(tmp_path, data)
    assert exc.value.status_code == 400 and "像素" in exc.value.detail
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("data", [b"\x89PNG\r\n\x1a\nnot-a-real-picture", picture()[:40], picture("JPEG")[:24]])
def test_truncated_or_forged_images_leave_no_files(tmp_path, data):
    with pytest.raises(HTTPException) as exc:save(tmp_path, data)
    assert exc.value.status_code == 400
    assert not list(tmp_path.iterdir())


def test_oversized_pixel_and_byte_limits_remove_staging_file(tmp_path, monkeypatch):
    monkeypatch.setattr(attachments, "BUSINESS_IMAGE_MAX_PIXELS", 15)
    with pytest.raises(HTTPException) as exc:save(tmp_path, picture())
    assert exc.value.status_code == 400 and "像素" in exc.value.detail
    assert not list(tmp_path.iterdir())
    with pytest.raises(HTTPException):save(tmp_path, picture(), max_bytes=4)
    assert not list(tmp_path.iterdir())


def test_decoder_resource_warning_rejected_and_cleaned(tmp_path, monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 8)
    with pytest.raises(HTTPException) as exc:save(tmp_path, picture())
    assert exc.value.status_code == 400
    assert not list(tmp_path.iterdir())


def test_validation_failure_cleans_staging_and_db_flush_failure_cleans_final(tmp_path, monkeypatch):
    original = attachments.validate_image_file
    def failed(_):raise RuntimeError("decoder failed")
    monkeypatch.setattr(attachments, "validate_image_file", failed)
    with pytest.raises(RuntimeError):save(tmp_path, picture())
    assert not list(tmp_path.iterdir())
    monkeypatch.setattr(attachments, "validate_image_file", original)
    class Reject:
        def add(self, row):pass
        def flush(self):raise RuntimeError("flush failed")
    with pytest.raises(RuntimeError):
        asyncio.run(attachments.save_image_upload(Reject(), UploadFile(BytesIO(picture()), filename="photo.png"), 1, file_dir=tmp_path))
    assert not list(tmp_path.iterdir())


def test_cancelled_upload_cleans_partial_file(tmp_path):
    class CancelledUpload:
        reads = 0
        filename = "phone-photo.png"
        async def read(self, _):
            self.reads += 1
            if self.reads == 1:return b"partial image"
            raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(attachments.save_image_upload(None, CancelledUpload(), 1, file_dir=tmp_path, persist=False))
    assert not list(tmp_path.iterdir())


def test_concurrent_decode_is_bounded_without_losing_valid_uploads(tmp_path, monkeypatch):
    original = attachments.validate_image_file
    lock = threading.Lock()
    active = peak = 0
    def slow_decode(path):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(.05)
            return original(path)
        finally:
            with lock:active -= 1
    monkeypatch.setattr(attachments, "validate_image_file", slow_decode)
    async def concurrent():
        return await asyncio.gather(*(attachments.save_image_upload(None,
            UploadFile(BytesIO(picture()), filename="photo.png"), 1, file_dir=tmp_path, persist=False) for _ in range(4)))
    for _ in range(2):  # Distinct loops must not reuse a limiter bound to a closed loop.
        rows = asyncio.run(concurrent())
        assert all((tmp_path / row.storage_key).is_file() for row in rows)
    assert peak == 2 and active == 0
