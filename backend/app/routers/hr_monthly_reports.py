"""Authorized report preview and editable PPTX export, no business mutations."""
import hashlib
import json
from io import BytesIO
from threading import BoundedSemaphore
import warnings

from fastapi import APIRouter, Depends, Form, File, UploadFile, HTTPException
from fastapi.responses import Response
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.hr_monthly_report import require_report, report_data, TEMPLATES, SECTIONS
from app.hr_monthly_pptx import build_pptx
from app.v2_auth import current_user, V2User
from app.v2_database import get_db
from app.v2_models import Attraction
from app.v2_services import write_audit
from app.v2_watermark import watermark_label
from app.excel_export_utils import content_disposition

class ReportRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def limited(request):
            if request.method == "POST":
                limit = 32 * 1024 * 1024  # Includes multipart headers and config.
                receive = request._receive
                received = 0
                async def bounded_receive():
                    nonlocal received
                    message = await receive()
                    received += len(message.get("body", b""))
                    if received > limit:
                        raise HTTPException(413, "月报上传请求超过32MB")
                    return message
                request._receive = bounded_receive
                try:
                    if int(request.headers.get("content-length", "0")) > limit:
                        raise HTTPException(413, "月报上传请求超过32MB")
                except ValueError as exc:
                    raise HTTPException(400, "上传长度无效") from exc
            return await handler(request)
        return limited


router = APIRouter(route_class=ReportRoute)
EXPORT_SLOTS = BoundedSemaphore(2)


class ReportOptions(BaseModel):
    month: str
    attraction_id: int | None = None
    template: str = "forest"
    sections: list[str] = Field(default_factory=lambda: list(SECTIONS))
    excellent_ids: list[int] = Field(default_factory=list, max_length=100)
    notes: str = Field(default="", max_length=2000)
    birthday: str = Field(default="", max_length=1000)
    photo_caption: str = Field(default="", max_length=150)
    preview_digest: str = ""


def digest(data):
    return hashlib.sha256(json.dumps({k: v for k, v in data.items() if k != "generated_at"}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


@router.get("/hr-monthly-reports/options")
def options(db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    require_report(user)
    return {"templates": [{"id": k, "name": v} for k, v in TEMPLATES.items()], "sections": [{"id": k, "name": v} for k, v in SECTIONS.items()], "attractions": [{"id": r.id, "name": r.name} for r in db.query(Attraction).filter(Attraction.employee_circle.is_(True)).order_by(Attraction.id).all()]}


@router.get("/hr-monthly-reports/preview")
def preview(month: str, attraction_id: int | None = None, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    require_report(user)
    data = report_data(db, month, attraction_id)
    return {"report": data, "digest": digest(data)}


def prepare_photo(raw):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as source:
                if source.format not in {"PNG", "JPEG", "WEBP"} or source.width * source.height > 12_000_000:
                    raise HTTPException(400, "照片仅支持PNG、JPEG、WebP，像素不超过1200万")
                photo = ImageOps.exif_transpose(source).convert("RGB")
                photo.thumbnail((2000, 2000))
                out = BytesIO()
                photo.save(out, "PNG")
                return {"bytes": out.getvalue(), "width": photo.width, "height": photo.height}
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(400, "照片无效或过大") from exc


def generate(db, user, config, raw_photos):
    if not EXPORT_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "月报正在生成，请稍后重试")
    try:
        data = report_data(db, config.month, config.attraction_id)
        current_digest = digest(data)
        if not config.preview_digest or current_digest != config.preview_digest:
            raise HTTPException(409, "数据已变化或尚未预览，请重新预览后导出")
        candidates = {r["employee_id"] for r in data["candidates"]}
        if not set(config.excellent_ids) <= candidates:
            raise HTTPException(400, "优秀员工必须从当前报告候选中确认")
        photos = [prepare_photo(raw) for raw in raw_photos] if "photos" in config.sections else []
        content = build_pptx(data, config.template, set(config.sections), set(config.excellent_ids), config.notes, config.birthday, photos, config.photo_caption, watermark_label(user.employee.employee_no))
        write_audit(db, user.employee, "导出HR月报", "hr_monthly_report_export", config.month, after={"attraction_id": config.attraction_id, "template": config.template, "template_version": "1", "sections": config.sections, "excellent_ids": config.excellent_ids, "photo_count": len(photos), "data_digest": current_digest, "generated_at": data["generated_at"], "draft": data["draft"], "summary": data["summary"]})
        db.commit()
        filename = f'HR月报_{config.month}_{data["scope_name"]}_{TEMPLATES[config.template]}{"_草稿" if data["draft"] else ""}.pptx'
        return Response(content, media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation", headers={"Content-Disposition": content_disposition("hr-monthly-report.pptx", filename), "Cache-Control": "no-store"})
    finally:
        EXPORT_SLOTS.release()


@router.post("/hr-monthly-reports/export")
async def export(config: str = Form(...), photos: list[UploadFile] = File(default=[]), db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    require_report(user)
    try:
        options = ReportOptions.model_validate_json(config)
    except ValueError as exc:
        raise HTTPException(400, "月报参数无效") from exc
    if options.template not in TEMPLATES or not set(options.sections) <= set(SECTIONS):
        raise HTTPException(400, "模板或章节无效")
    if len(photos) > 12:
        raise HTTPException(400, "最多上传12张活动照片")
    raw_photos = []
    total = 0
    try:
        for upload in photos:
            raw = await upload.read(5 * 1024 * 1024 + 1)
            total += len(raw)
            if len(raw) > 5 * 1024 * 1024 or total > 30 * 1024 * 1024:
                raise HTTPException(400, "每张照片不超过5MB，总计不超过30MB")
            raw_photos.append(raw)
        return await run_in_threadpool(generate, db, user, options, raw_photos)
    finally:
        for upload in photos:
            await upload.close()
