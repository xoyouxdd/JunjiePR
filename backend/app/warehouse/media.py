from datetime import datetime, timedelta
from io import BytesIO
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.v2_auth import V2User
from app.warehouse.models import WAREHOUSE_TZ, WarehousePhoto, warehouse_now


MAX_UPLOAD = 12 * 1024 * 1024
MAX_PIXELS = 24_000_000
TARGET_BYTES = 160 * 1024
MAX_STORED_BYTES = 256 * 1024
JPEG_PROFILES = ((1440, (79, 72, 65)), (1280, (76, 69, 62)), (1120, (72, 65, 57)), (1000, (70, 63, 55)))


def captured_at(value: str, now: datetime) -> datetime:
    if not value:
        return now
    try:
        captured = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(422, "照片拍摄时间无效") from None
    if captured.tzinfo is not None:
        captured = captured.astimezone(WAREHOUSE_TZ).replace(tzinfo=None)
    return min(captured, now + timedelta(minutes=2))


def jpeg_bytes(upload: UploadFile) -> bytes:
    raw = upload.file.read(MAX_UPLOAD + 1)
    if not raw or len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "照片须小于 12 MB，请重试这张照片")
    try:
        with Image.open(BytesIO(raw)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"} or image.width * image.height > MAX_PIXELS:
                raise HTTPException(422, "请上传手机拍摄的 JPG、PNG 或 WebP 照片")
            oriented = ImageOps.exif_transpose(image)
            oriented.thumbnail((1440, 1440), Image.Resampling.LANCZOS)
            if "A" in oriented.getbands() or "transparency" in oriented.info:
                rgba = oriented.convert("RGBA")
                normalized = Image.new("RGB", rgba.size, "white")
                normalized.paste(rgba, mask=rgba.getchannel("A"))
            else:
                normalized = oriented.convert("RGB")
            for edge, qualities in JPEG_PROFILES:
                normalized.thumbnail((edge, edge), Image.Resampling.LANCZOS)
                readable_fallback = None
                for quality in qualities:
                    encoded = BytesIO()
                    normalized.save(encoded, "JPEG", quality=quality, optimize=True)
                    if encoded.tell() <= TARGET_BYTES:
                        return encoded.getvalue()
                    if readable_fallback is None and encoded.tell() <= MAX_STORED_BYTES:
                        readable_fallback = encoded.getvalue()
                if readable_fallback is not None:
                    return readable_fallback
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(422, "照片无法识别，请重拍这一张") from None
    raise HTTPException(422, "照片压缩后仍过大，请重拍这一张")


def stage_photo(db: Session, user: V2User, content: bytes, capture: str) -> WarehousePhoto:
    now = warehouse_now()
    unlinked = db.query(WarehousePhoto).filter(WarehousePhoto.owner_id == user.id, WarehousePhoto.attached_at.is_(None))
    unlinked.filter(WarehousePhoto.uploaded_at < now - timedelta(days=30)).delete(synchronize_session=False)
    if unlinked.count() >= 260:
        raise HTTPException(429, "未确认单据的照片较多，请先处理已有记录")
    row = WarehousePhoto(id=uuid4().hex, owner_id=user.id, owner_name=user.name, content=content,
                         captured_at=captured_at(capture, now), uploaded_at=now)
    db.add(row)
    db.flush()
    return row
