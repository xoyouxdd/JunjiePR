"""Local poster templates and private, validated announcement artwork."""
from __future__ import annotations

import hashlib
import json
import warnings
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.responses import Response
from datetime import date
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.v2_auth import V2User, require_roles
from app.v2_database import FILE_DIR, get_db
from app.v2_models import AnnouncementMedia, StoredFile
from app.v2_services import remove_upload_file, save_image_upload, write_audit
from app.announcement_posters import PosterError, render_poster, template_options
from app.announcements import require_publisher


router = APIRouter(prefix="/announcement-media", tags=["announcement-media"])
publisher = require_publisher
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
CATEGORIES = ("安全 & 合规", "演出 & 5S", "排班 & 效率", "礼仪 & MM", "HR & 活动")


class PosterRequest(BaseModel):
    title: str = Field(min_length=1, max_length=48)
    summary: str = Field(min_length=1, max_length=180)
    category: Literal["安全 & 合规", "演出 & 5S", "排班 & 效率", "礼仪 & MM", "HR & 活动"]
    layout: Literal["portrait", "landscape"] = "portrait"
    style: Literal["notice", "event", "guide"] = "notice"
    template_id: str = Field(default="", max_length=48)
    scope: str = Field(default="", max_length=60)
    effective_date: date | None = None


@router.get("/options")
def options(user: V2User = Depends(publisher)):
    return {"mode": "local_template", "online_generation_enabled": False,
            "categories": CATEGORIES, "templates": template_options(), "max_bytes": MAX_IMAGE_BYTES,
            "max_pixels": MAX_IMAGE_PIXELS, "formats": ["JPG", "PNG", "WebP"]}


@router.post("/poster")
def compose_poster(payload: PosterRequest, user: V2User = Depends(publisher)):
    values = payload.model_dump()
    values["effective_date"] = payload.effective_date.isoformat() if payload.effective_date else ""
    try:
        image = render_poster(**values)
    except PosterError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(image, media_type="image/png", headers={
        "Cache-Control": "private, no-store", "Content-Disposition": 'inline; filename="announcement-poster.png"'})


def media_payload(db: Session, row: AnnouncementMedia) -> dict:
    file = db.get(StoredFile, row.file_id)
    return {"id": row.id, "file_id": row.file_id, "title": row.title, "alt_text": row.alt_text,
            "source": row.source, "width": row.width, "height": row.height,
            "file_size": file.file_size, "created_at": row.created_at.isoformat(timespec="seconds"),
            "preview_url": f"/api/announcement-media/images/{row.id}", "status": "private_draft"}


@router.get("/images")
def list_images(page: int = Query(1, ge=1), db: Session = Depends(get_db), user: V2User = Depends(publisher)):
    query = db.query(AnnouncementMedia).join(StoredFile, StoredFile.id == AnnouncementMedia.file_id).filter(
        AnnouncementMedia.owner_id == user.id, StoredFile.status == "active")
    total = query.count()
    rows = query.order_by(AnnouncementMedia.id.desc()).offset((page - 1) * 24).limit(24).all()
    return {"items": [media_payload(db, row) for row in rows], "page": page,
            "total": total, "pages": max(1, (total + 23) // 24)}


@router.post("/images")
async def upload_image(
    request: Request,
    image: UploadFile = File(...),
    title: str = Form(..., min_length=1, max_length=120),
    alt_text: str = Form("", max_length=500),
    source: Literal["template", "external_ai", "photo", "illustration"] = Form("template"),
    request_key: str = Form(..., min_length=8, max_length=96),
    db: Session = Depends(get_db), user: V2User = Depends(publisher),
):
    title, alt_text = title.strip(), alt_text.strip()
    if not title or not request_key.strip():
        raise HTTPException(400, "素材标题和提交标识不能为空")
    staged = None
    try:
        try:
            staged = await save_image_upload(db, image, user.id, max_bytes=MAX_IMAGE_BYTES, persist=False)
        except HTTPException as exc:
            raise HTTPException(exc.status_code, str(exc.detail).replace("认可图片", "公告图片")) from exc
        staged.original_filename = staged.original_filename[:255]
        path = FILE_DIR / staged.storage_key
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(path) as decoded:
                    width, height = decoded.size
                    if width * height > MAX_IMAGE_PIXELS:
                        raise HTTPException(400, "图片像素不能超过2000万，请缩小后上传")
                    if decoded.format not in {"JPEG", "PNG", "WEBP"}:
                        raise HTTPException(400, "仅支持JPG、PNG、WebP图片")
                    decoded.verify()
                # Force a complete decode too: a valid header alone is insufficient.
                with Image.open(path) as decoded:
                    decoded.load()
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise HTTPException(400, "图片无法完整读取，请重新保存为JPG、PNG或WebP后上传") from exc
        digest = hashlib.sha256(json.dumps(
            [title, alt_text, source, staged.sha256], ensure_ascii=False).encode("utf-8")).hexdigest()
        existing = db.query(AnnouncementMedia).filter_by(owner_id=user.id, request_key=request_key).first()
        if existing:
            if existing.payload_digest != digest:
                raise HTTPException(409, "本次提交标识已用于其他内容，请重新提交")
            return {"item": media_payload(db, existing), "duplicate": True}
        db.add(staged)
        db.flush()
        row = AnnouncementMedia(owner_id=user.id, file_id=staged.id, title=title, alt_text=alt_text,
                                source=source, request_key=request_key, payload_digest=digest,
                                width=width, height=height)
        db.add(row)
        db.flush()
        write_audit(db, user.employee, "上传公告待用素材", "announcement_media", row.id,
                    after={"title": title, "source": source, "file_id": staged.id},
                    ip_address=request.client.host if request.client else None)
        result = media_payload(db, row)
        db.commit()
        staged = None
        return {"item": result, "duplicate": False}
    except IntegrityError:
        db.rollback()
        existing = db.query(AnnouncementMedia).filter_by(owner_id=user.id, request_key=request_key).first()
        if existing and existing.payload_digest == digest:
            return {"item": media_payload(db, existing), "duplicate": True}
        raise HTTPException(409, "素材提交冲突，请重新提交")
    except Exception:
        db.rollback()
        raise
    finally:
        if staged is not None:
            remove_upload_file(staged)
        await image.close()


@router.get("/images/{media_id}")
def preview_image(media_id: int, db: Session = Depends(get_db), user: V2User = Depends(publisher)):
    row = db.query(AnnouncementMedia).filter_by(id=media_id, owner_id=user.id).first()
    file = db.get(StoredFile, row.file_id) if row else None
    if not file or file.status != "active":
        raise HTTPException(404, "素材不存在")
    path = (FILE_DIR / file.storage_key).resolve()
    if FILE_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "素材文件不存在")
    return FileResponse(path, media_type=file.mime_type,
                        headers={"Cache-Control": "private, no-store", "Content-Disposition": "inline"})
