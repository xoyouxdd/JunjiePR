"""Operational announcements, explicit grants and immutable signing records."""
from __future__ import annotations

import base64
import binascii
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Literal
from uuid import uuid4
import warnings
import zipfile
from xml.etree import ElementTree

import fitz
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageStat
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app import announcements as domain
from app.v2_auth import V2User, current_user
from app.v2_database import FILE_DIR, get_db
from app.v2_models import (Announcement, AnnouncementAsset, AnnouncementDelivery, AnnouncementFavorite,
    AnnouncementGrant, AnnouncementHandover, AnnouncementNotification, AnnouncementProject,
    AnnouncementProjectMember, AnnouncementVersion, Employee, StoredFile, UserAccount)
from app.v2_services import remove_upload_file, save_upload, write_audit
from app.routers._shared import client_ip

router = APIRouter(prefix="/announcements", tags=["announcements"])
Tier = Literal["frontline", "lead", "gsm", "am", "hr"]
Category = Literal["安全 & 合规", "演出 & 5S", "排班 & 效率", "礼仪 & MM", "HR & 活动"]


class Attachment(BaseModel):
    file_id: int = Field(gt=0)
    kind: Literal["cover", "image", "attachment"]
    caption: str = Field(default="", max_length=300)


class AnnouncementInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(default="", max_length=500)
    body: str = Field(min_length=1, max_length=20000)
    category: Category
    scope_kind: Literal["circle", "project"] = "circle"
    attraction_ids: list[int] = Field(default_factory=list, max_length=20)
    project_id: int | None = None
    tiers: list[Tier] = Field(min_length=1, max_length=5)
    confirmation_level: Literal[1, 2, 3] = 2
    effective_on: date
    expires_on: date | None = None
    due_on: date | None = None
    attachments: list[Attachment] = Field(default_factory=list, max_length=12)
    change_summary: str = Field(default="", max_length=1000)
    request_key: str = Field(min_length=8, max_length=96)
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def valid(self):
        self.title, self.body, self.summary = self.title.strip(), self.body.strip(), self.summary.strip()
        if not self.title or not self.body or not self.request_key.strip():
            raise ValueError("标题、正文和提交标识不能为空")
        if self.expires_on and self.expires_on < self.effective_on:
            raise ValueError("失效日不能早于生效日")
        if self.due_on and (self.due_on < self.effective_on or self.expires_on and self.due_on > self.expires_on):
            raise ValueError("截止日须位于有效期内")
        if sum(x.kind == "cover" for x in self.attachments) > 1:
            raise ValueError("最多选择一张封面")
        if len({x.file_id for x in self.attachments}) != len(self.attachments):
            raise ValueError("不能重复添加同一文件")
        self.tiers = list(dict.fromkeys(self.tiers))
        self.attraction_ids = sorted(set(self.attraction_ids))
        return self


class ReceiptInput(BaseModel):
    version: int = Field(ge=1)
    action: Literal["read", "confirm"] = "read"
    acknowledged: bool = False
    signature: str | None = Field(default=None, max_length=1_500_000)


class ReasonInput(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    expected_version: int = Field(ge=1)


class HandoverInput(ReasonInput):
    successor_id: int = Field(gt=0)
    force: bool = False


class GrantInput(BaseModel):
    employee_id: int = Field(gt=0)
    scope_kind: Literal["circle", "project"] = "circle"
    project_id: int = Field(default=0, ge=0)
    enabled: bool = True
    attraction_ids: list[int] = Field(default_factory=list, max_length=20)
    tiers: list[Tier] = Field(default_factory=lambda: list(domain.TIERS), min_length=1, max_length=5)
    categories: list[Category] = Field(default_factory=lambda: domain.CATEGORIES, min_length=1, max_length=5)
    starts_on: date
    ends_on: date | None = None
    reason: str = Field(min_length=1, max_length=1000)


class ProjectInput(BaseModel):
    id: int | None = None
    name: str = Field(min_length=1, max_length=100)
    active: bool = True
    employee_ids: list[int] = Field(default_factory=list, max_length=10000)


def require_admin(user=Depends(current_user)):
    if not domain.admin(user):
        raise HTTPException(403, "仅最高管理权限可管理公告授权")
    return user


def get_announcement(db, id):
    row = db.get(Announcement, id)
    if not row:
        raise HTTPException(404, "公告不存在")
    return row


def ensure_owner(announcement, user):
    if announcement.owner_id != user.id:
        raise HTTPException(403, "仅公告负责人可执行；管理员可通过交接接管")


def ensure_version(announcement, expected):
    if announcement.current_version != expected:
        raise HTTPException(409, "公告已更新，请刷新后操作")


def write_version(db, announcement, payload, user, number):
    values = payload.model_dump(mode="json")
    row = AnnouncementVersion(announcement_id=announcement.id, number=number,
        title=payload.title, summary=payload.summary, body=payload.body, category=payload.category,
        scope_kind=payload.scope_kind, project_id=payload.project_id, attraction_ids_json=domain.packed(payload.attraction_ids),
        tiers_json=domain.packed(payload.tiers), confirmation_level=payload.confirmation_level,
        effective_on=payload.effective_on.isoformat(), expires_on=payload.expires_on.isoformat() if payload.expires_on else None,
        due_on=payload.due_on.isoformat() if payload.due_on else None, attachments_json=domain.packed(values["attachments"]),
        change_summary=payload.change_summary, created_by=user.id, request_key=payload.request_key, payload_digest=domain.digest(values))
    db.add(row)
    db.flush()
    return row


@router.get("/options")
def options(db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    config = domain.grant_config(db, user.id)
    return {"categories": domain.CATEGORIES, "tiers": domain.TIERS,
            "circles": [{"id": id, "name": name} for id, name in domain.live_circles(db).items()],
            "publisher": config, "can_publish": bool(config["circles"] or config["projects"]),
            "can_admin": domain.admin(user), "can_follow": user.has_role("SUPERVISOR", "TA_SUPERVISOR", "TA_GSM", "GSM", "AM") or domain.admin(user),
            "counts": domain.summary(db, user)}


@router.get("/summary")
def summary(db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    return domain.summary(db, user)


@router.get("")
def listing(view: Literal["pending", "history", "favorites", "manage"] = "pending", q: str = Query("", max_length=100),
            category: str = "", page: int = Query(1, ge=1), db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    domain.sync_current(db)
    receipts = db.query(AnnouncementDelivery).filter_by(employee_id=user.id).all()
    mine = {r.version_id: r for r in receipts}
    favorites = {r.announcement_id for r in db.query(AnnouncementFavorite).filter_by(employee_id=user.id)}
    items = []
    for row in db.query(Announcement).order_by(Announcement.published_at.desc(), Announcement.id.desc()):
        versions = db.query(AnnouncementVersion).filter_by(announcement_id=row.id).order_by(AnnouncementVersion.number.desc()).all()
        current = next(v for v in versions if v.number == row.current_version)
        receipt = mine.get(current.id)
        version = current
        if view == "pending":
            if not receipt or receipt.status != "pending":
                continue
        elif view in {"history", "favorites"}:
            if view == "favorites" and row.id not in favorites:
                continue
            accessible = [v for v in versions if v.id in mine]
            if not accessible:
                continue
            if view == "history":
                archived = [v for v in accessible if mine[v.id].status != "pending"]
                if not archived:
                    continue
                version = archived[0]
            else:
                version = accessible[0]
        else:
            ids = domain.record_ids(db, user, row, current)
            if row.owner_id != user.id and not domain.admin(user) and ids == set():
                continue
        if category and version.category != category:
            continue
        if q and q.casefold() not in (version.title+version.summary+version.body).casefold():
            continue
        items.append(domain.payload(db, user, row, version))
    total = len(items)
    db.commit()
    return {"items": items[(page-1)*20:page*20], "total": total, "page": page, "pages": max(1, (total+19)//20)}


@router.post("")
def create(payload: AnnouncementInput, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    domain.lock(db)
    values = payload.model_dump(mode="json")
    prior = db.query(Announcement).filter_by(publisher_id=user.id, request_key=payload.request_key).first()
    if prior:
        if prior.payload_digest != domain.digest(values):
            raise HTTPException(409, "提交标识已用于其他公告")
        result = domain.payload(db, user, prior, domain.current_version(db, prior), True)
        db.commit()
        return result
    domain.validate_scope(db, user.id, values)
    domain.validate_attachments(db, user, values["attachments"])
    row = Announcement(publisher_id=user.id, owner_id=user.id, request_key=payload.request_key, payload_digest=domain.digest(values))
    db.add(row)
    db.flush()
    version = write_version(db, row, payload, user, 1)
    domain.sync_current(db, initial_id=row.id)
    write_audit(db, user.employee, "发布公告", "announcement", row.id, after=values, ip_address=client_ip(request))
    result = domain.payload(db, user, row, version, True)
    db.commit()
    return result


@router.post("/{id}/versions")
def revise(id: int, payload: AnnouncementInput, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    domain.lock(db)
    row = get_announcement(db, id)
    ensure_owner(row, user)
    values = payload.model_dump(mode="json")
    retry = db.query(AnnouncementVersion).filter_by(announcement_id=id, request_key=payload.request_key).first()
    if retry:
        if retry.payload_digest != domain.digest(values):
            raise HTTPException(409, "提交标识已用于其他更新")
        result = domain.payload(db, user, row, retry, True)
        db.commit()
        return result
    ensure_version(row, payload.expected_version)
    if row.status != "published":
        raise HTTPException(409, "已撤下公告不能更新，请另发公告")
    if not payload.change_summary.strip():
        raise HTTPException(400, "请填写变更说明")
    previous = domain.current_version(db, row)
    domain.validate_scope(db, user.id, values)
    domain.validate_attachments(db, user, values["attachments"], previous)
    seen = {d.employee_id for d in db.query(AnnouncementDelivery).filter_by(version_id=previous.id) if d.seen_at}
    for delivery in db.query(AnnouncementDelivery).filter_by(version_id=previous.id):
        if not delivery.confirmed_at:
            delivery.status = "superseded"
    row.current_version += 1
    version = write_version(db, row, payload, user, row.current_version)
    for employee_id in seen:
        domain.notify(db, employee_id, id, f"updated:{version.id}", "updated", f"{version.title} 已更新至 V{version.number}，请查看变化。")
    domain.sync_current(db)
    write_audit(db, user.employee, "更新公告", "announcement", id, before={"version": previous.number}, after=values,
                reason=payload.change_summary, ip_address=client_ip(request))
    result = domain.payload(db, user, row, version, True)
    db.commit()
    return result


@router.get("/notifications")
def notifications(page: int = Query(1, ge=1), db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    query = db.query(AnnouncementNotification).filter_by(employee_id=user.id)
    return {"items": [{"id": n.id, "announcement_id": n.announcement_id, "kind": n.kind, "message": n.message,
                       "read_at": n.read_at, "created_at": n.created_at} for n in query.order_by(AnnouncementNotification.id.desc()).offset((page-1)*30).limit(30)],
            "total": query.count(), "page": page}


@router.post("/notifications/{notification_id}/read")
def read_notification(notification_id: int, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    row = db.query(AnnouncementNotification).filter_by(id=notification_id, employee_id=user.id).first()
    if not row:
        raise HTTPException(404, "提醒不存在")
    row.read_at = row.read_at or datetime.now()
    db.commit()
    return {"ok": True}


@router.get("/handovers")
def handovers(db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    rows = db.query(AnnouncementHandover).filter_by(to_id=user.id, status="pending").all()
    return [{"id": h.id, "announcement_id": h.announcement_id, "title": domain.current_version(db, db.get(Announcement, h.announcement_id)).title,
             "from_name": db.get(Employee, h.from_id).name, "to_name": user.name,
             "scope_label": domain.payload(db, user, db.get(Announcement, h.announcement_id), domain.current_version(db, db.get(Announcement, h.announcement_id)))["scope_label"],
             "version": db.get(Announcement, h.announcement_id).current_version,
             "requested_at": h.requested_at, "reason": h.reason} for h in rows]


@router.post("/handovers/{handover_id}/accept")
def accept_handover(handover_id: int, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    domain.lock(db)
    h = db.get(AnnouncementHandover, handover_id)
    if not h or h.to_id != user.id:
        raise HTTPException(404, "交接申请不存在")
    if h.status != "pending":
        raise HTTPException(409, "交接已处理")
    row = get_announcement(db, h.announcement_id)
    if row.owner_id != h.from_id:
        raise HTTPException(409, "负责人已变更")
    domain.validate_scope(db, user.id, domain.version_values(domain.current_version(db, row)))
    row.owner_id = user.id
    h.status, h.completed_at = "accepted", datetime.now()
    domain.notify(db, h.from_id, row.id, f"accepted:{h.id}", "handover", "公告交接已接受")
    write_audit(db, user.employee, "接受公告交接", "announcement", row.id, before={"owner_id": h.from_id}, after={"owner_id": user.id}, reason=h.reason, ip_address=client_ip(request))
    db.commit()
    return {"ok": True}


@router.post("/{id}/handover")
def handover(id: int, payload: HandoverInput, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    domain.lock(db)
    row = get_announcement(db, id)
    if payload.force:
        if not domain.admin(user):
            raise HTTPException(403, "仅最高管理权限可兜底交接")
    else:
        ensure_owner(row, user)
    ensure_version(row, payload.expected_version)
    if not payload.reason.strip() or payload.successor_id == row.owner_id:
        raise HTTPException(400, "请填写原因并选择其他继任者")
    domain.validate_scope(db, payload.successor_id, domain.version_values(domain.current_version(db, row)))
    for old in db.query(AnnouncementHandover).filter_by(announcement_id=id, status="pending"):
        old.status = "cancelled"
    h = AnnouncementHandover(announcement_id=id, from_id=row.owner_id, to_id=payload.successor_id,
                            requested_by=user.id, reason=payload.reason.strip(), status="forced" if payload.force else "pending")
    db.add(h)
    db.flush()
    if payload.force:
        row.owner_id, h.completed_at = payload.successor_id, datetime.now()
    for employee_id in {h.from_id, h.to_id}:
        domain.notify(db, employee_id, id, f"handover:{h.id}", "handover", "公告负责人已交接" if payload.force else "公告交接待继任者接受")
    write_audit(db, user.employee, "兜底公告交接" if payload.force else "申请公告交接", "announcement", id,
                before={"owner_id": h.from_id}, after={"successor_id": h.to_id, "force": payload.force}, reason=payload.reason, ip_address=client_ip(request))
    db.commit()
    return {"ok": True, "handover_id": h.id}


@router.post("/{id}/withdraw")
def withdraw(id: int, payload: ReasonInput, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    domain.lock(db)
    row = get_announcement(db, id)
    ensure_owner(row, user)
    ensure_version(row, payload.expected_version)
    if not payload.reason.strip():
        raise HTTPException(400, "请填写撤下原因")
    if row.status != "withdrawn":
        row.status, row.withdrawn_at, row.withdrawn_reason = "withdrawn", datetime.now(), payload.reason.strip()
        versions = {v.id for v in db.query(AnnouncementVersion).filter_by(announcement_id=id)}
        for delivery in db.query(AnnouncementDelivery).filter(AnnouncementDelivery.version_id.in_(versions)):
            if not delivery.confirmed_at:
                delivery.status = "withdrawn"
            domain.notify(db, delivery.employee_id, id, f"withdrawn:{id}", "withdrawn", f"公告已撤下：{domain.current_version(db, row).title}。{payload.reason.strip()}")
        write_audit(db, user.employee, "撤下公告", "announcement", id, reason=payload.reason, ip_address=client_ip(request))
    db.commit()
    return {"ok": True}


@router.post("/{id}/favorite")
def favorite(id: int, enabled: bool = True, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    domain.lock(db)
    row = get_announcement(db, id)
    versions = db.query(AnnouncementVersion).filter_by(announcement_id=id).all()
    if not any(domain.can_read_version(db, user, row, v) for v in versions):
        raise HTTPException(404, "公告不存在")
    prior = db.query(AnnouncementFavorite).filter_by(announcement_id=id, employee_id=user.id).first()
    if enabled and not prior:
        db.add(AnnouncementFavorite(announcement_id=id, employee_id=user.id))
    elif not enabled and prior:
        db.delete(prior)
    db.commit()
    return {"ok": True}


def signature_bytes(raw):
    try:
        if not raw or not raw.startswith("data:image/png;base64,"):
            raise ValueError()
        content = base64.b64decode(raw.split(",", 1)[1], validate=True)
        if len(content) > 1_000_000:
            raise ValueError()
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as decoded:
                if decoded.format != "PNG" or decoded.width*decoded.height > 2_000_000 or min(decoded.size) < 32:
                    raise ValueError()
                decoded.load()
                white = Image.new("RGBA", decoded.size, "white")
                white.alpha_composite(decoded.convert("RGBA"))
                gray = white.convert("L")
                ink = gray.point(lambda x: 255 if x < 160 else 0)
                bounds = ink.getbbox()
                if not bounds or bounds[2]-bounds[0] < 15 or bounds[3]-bounds[1] < 10 or ImageStat.Stat(gray).stddev[0] < 2:
                    raise ValueError()
        return content
    except (ValueError, OSError, binascii.Error, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(400, "请在签名区域手写有效签名") from exc


@router.post("/{id}/receipt")
def receipt(id: int, payload: ReceiptInput, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    domain.lock(db)
    domain.sync_current(db)
    row = get_announcement(db, id)
    ensure_version(row, payload.version)
    version = domain.current_version(db, row)
    delivery = db.query(AnnouncementDelivery).filter_by(version_id=version.id, employee_id=user.id).first()
    if not delivery:
        raise HTTPException(404, "公告不存在")
    if row.status != "published" or delivery.status not in {"pending", "completed"}:
        raise HTTPException(409, "当前公告无需确认或暂不可确认")
    path = None
    try:
        if not delivery.confirmed_at:
            delivery.seen_at = delivery.seen_at or datetime.now()
            db.query(AnnouncementNotification).filter(AnnouncementNotification.employee_id == user.id,
                AnnouncementNotification.announcement_id == id,
                AnnouncementNotification.kind.in_(["published", "supplement", "updated", "reminder"]),
                AnnouncementNotification.read_at.is_(None)).update({"read_at": datetime.now()}, synchronize_session=False)
            if version.confirmation_level == 1 or payload.action == "confirm":
                if version.confirmation_level > 1 and not payload.acknowledged:
                    raise HTTPException(400, "请明确确认已阅读并知悉")
                if version.confirmation_level == 3:
                    content = signature_bytes(payload.signature)
                    key = f"{uuid4().hex}.png"
                    path = FILE_DIR / key
                    path.write_bytes(content)
                    file = StoredFile(storage_key=key, original_filename="公告签名.png", extension=".png", mime_type="image/png",
                                      file_size=len(content), sha256=domain.hashlib.sha256(content).hexdigest(), uploaded_by=user.id, status="active")
                    db.add(file)
                    db.flush()
                    delivery.signature_file_id = file.id
                    delivery.signing_statement = f"本人{user.name}（{user.employee.employee_no}）已阅读并知悉《{version.title}》V{version.number}的要求。"
                delivery.confirmed_at, delivery.status = datetime.now(), "completed"
                write_audit(db, user.employee, "签收公告", "announcement", id,
                            after={"version": version.number, "level": version.confirmation_level, "delivery_id": delivery.id}, ip_address=client_ip(request))
        result = domain.delivery_payload(db, delivery)
        db.commit()
        return result
    except Exception:
        db.rollback()
        if path:
            path.unlink(missing_ok=True)
        raise


@router.get("/{id}/records")
def records(id: int, version: int | None = None, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    domain.sync_current(db)
    row = get_announcement(db, id)
    revision = db.query(AnnouncementVersion).filter_by(announcement_id=id, number=version or row.current_version).first()
    if not revision:
        raise HTTPException(404, "版本不存在")
    ids = domain.record_ids(db, user, row, revision)
    if ids == set():
        raise HTTPException(403, "没有签收记录查看权限")
    query = db.query(AnnouncementDelivery).filter_by(version_id=revision.id)
    if ids is not None:
        query = query.filter(AnnouncementDelivery.employee_id.in_(ids))
    rows = query.order_by(AnnouncementDelivery.employee_no, AnnouncementDelivery.id).all()
    items, groups = domain.grouped_records(db, rows)
    active = [d for d in rows if d.status not in {"suspended", "exited"}]
    result = {"announcement": domain.payload(db, user, row, revision), "items": items, "groups": groups,
              "group_basis": "当前小组", "handover": domain.handover_overview(db, row, user),
              "counts": {"required": len(active), "completed": sum(bool(d.confirmed_at) for d in active),
                         "pending": sum(not d.confirmed_at for d in active), "suspended": sum(d.status == "suspended" for d in rows),
                         "exited": sum(d.status == "exited" for d in rows)}, "scoped": ids is not None}
    db.commit()
    return result


@router.post("/{id}/remind")
def remind(id: int, payload: ReasonInput, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    domain.lock(db)
    domain.sync_current(db)
    row = get_announcement(db, id)
    ensure_version(row, payload.expected_version)
    version = domain.current_version(db, row)
    ids = domain.record_ids(db, user, row, version)
    if ids == set():
        raise HTTPException(403, "没有提醒权限")
    recipients = [d for d in db.query(AnnouncementDelivery).filter_by(version_id=version.id, status="pending") if ids is None or d.employee_id in ids]
    for d in recipients:
        domain.notify(db, d.employee_id, id, f"remind:{version.id}:{date.today()}", "reminder", f"请查收《{version.title}》V{version.number}")
    write_audit(db, user.employee, "提醒公告查收", "announcement", id, after={"count": len(recipients)}, ip_address=client_ip(request))
    db.commit()
    return {"ok": True, "count": len(recipients)}


@router.get("/{id}/export")
def export(id: int, request: Request, format: Literal["pdf", "xlsx"] = "pdf", version: int | None = None,
           db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    from app.announcement_exports import export_records
    data = records(id, version, db, user)
    announcement = get_announcement(db, id)
    revision = db.query(AnnouncementVersion).filter_by(announcement_id=id, number=data["announcement"]["version"]).one()
    data["announcement"] = domain.payload(db, user, announcement, revision, True)
    try:
        content = export_records(data, format, user)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    write_audit(db, user.employee, "导出公告签收", "announcement", id, after={"version": data["announcement"]["version"], "format": format, "scoped": data["scoped"]}, ip_address=client_ip(request))
    db.commit()
    return Response(content, media_type="application/pdf" if format == "pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="announcement-{id}-v{data["announcement"]["version"]}.{format}"', "Cache-Control": "private, no-store"})


@router.post("/assets")
async def upload_asset(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db), user: V2User = Depends(domain.require_publisher)):
    stored = None
    try:
        stored = await save_upload(db, file, user.id, allowed_extensions={".pdf", ".docx", ".xlsx", ".pptx"}, max_bytes=20*1024*1024)
        path = FILE_DIR / stored.storage_key
        if stored.extension == ".pdf":
            with fitz.open(path) as document:
                if document.is_encrypted or not document.page_count:
                    raise ValueError("PDF不可读取")
            stored.mime_type = "application/pdf"
        else:
            with zipfile.ZipFile(path) as document:
                expected = {".docx": "word/document.xml", ".xlsx": "xl/workbook.xml", ".pptx": "ppt/presentation.xml"}[stored.extension]
                if expected not in document.namelist() or any(i.file_size > 30*1024*1024 for i in document.infolist()) or sum(i.file_size for i in document.infolist()) > 100*1024*1024:
                    raise ValueError("文档不可读取")
                if document.testzip() is not None:
                    raise ValueError("文档校验失败")
                root = ElementTree.fromstring(document.read(expected))
                if root.tag.split("}")[-1] != {".docx": "document", ".xlsx": "workbook", ".pptx": "presentation"}[stored.extension]:
                    raise ValueError("文档格式不匹配")
            stored.mime_type = "application/octet-stream"
        stored.original_filename = Path(stored.original_filename).name[:255]
        db.add(AnnouncementAsset(owner_id=user.id, file_id=stored.id, kind="attachment"))
        write_audit(db, user.employee, "上传公告附件", "stored_file", stored.id, ip_address=client_ip(request))
        result = {"file_id": stored.id, "kind": "attachment", "caption": "", "filename": stored.original_filename, "url": f"/api/files/{stored.id}"}
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        remove_upload_file(stored)
        if isinstance(exc, (ValueError, OSError, zipfile.BadZipFile, fitz.FileDataError, ElementTree.ParseError)):
            raise HTTPException(400, "文件无法读取，请检查PDF或Office文档") from exc
        raise
    finally:
        await file.close()


@router.get("/admin/config")
def admin_config(db: Session = Depends(get_db), user: V2User = Depends(require_admin)):
    employees = db.query(Employee).join(UserAccount, UserAccount.employee_id == Employee.id).filter(Employee.is_active.is_(True), UserAccount.enabled.is_(True)).all()
    return {"employees": [{"id": e.id, "name": e.name, "employee_no": e.employee_no, "role_codes": [r.code for r in domain.employee_roles(db, e.id)],
                           "attraction_id": e.attraction_id, "publisher": domain.grant_config(db, e.id)} for e in employees],
            "grants": [{"id": g.id, "employee_id": g.employee_id, "scope_kind": g.scope_kind, "project_id": g.project_id, "enabled": g.enabled,
                        "attraction_ids": domain.unpack(g.attraction_ids_json), "tiers": domain.unpack(g.tiers_json), "categories": domain.unpack(g.categories_json),
                        "starts_on": g.starts_on, "ends_on": g.ends_on, "reason": g.reason, "changed_at": g.changed_at} for g in db.query(AnnouncementGrant)],
            "projects": [{"id": p.id, "name": p.name, "active": p.active, "employee_ids": [m.employee_id for m in db.query(AnnouncementProjectMember).filter_by(project_id=p.id, active=True)]} for p in db.query(AnnouncementProject)]}


@router.post("/admin/grants")
def grant(payload: GrantInput, request: Request, db: Session = Depends(get_db), user: V2User = Depends(require_admin)):
    domain.lock(db)
    if not payload.reason.strip() or payload.ends_on and payload.ends_on < payload.starts_on:
        raise HTTPException(400, "请填写授权原因及有效日期")
    employee = db.get(Employee, payload.employee_id)
    if not employee or not employee.is_active:
        raise HTTPException(400, "员工不可用")
    if payload.scope_kind == "circle":
        if payload.project_id or not {r.code for r in domain.employee_roles(db, employee.id)} & {"GSM", "TA_GSM", "AM"}:
            raise HTTPException(400, "圈公告发布授权仅授予GSM、TA GSM或AM")
        if not set(payload.attraction_ids) <= domain.live_circles(db).keys() or payload.enabled and not payload.attraction_ids:
            raise HTTPException(400, "请选择有效景点圈")
    else:
        project = db.get(AnnouncementProject, payload.project_id)
        if not project or not project.active or payload.attraction_ids:
            raise HTTPException(400, "请选择有效项目")
    row = db.query(AnnouncementGrant).filter_by(employee_id=payload.employee_id, scope_kind=payload.scope_kind, project_id=payload.project_id).first()
    if not row:
        row = AnnouncementGrant(employee_id=payload.employee_id, scope_kind=payload.scope_kind, project_id=payload.project_id)
        db.add(row)
    before = {"enabled": row.enabled, "attraction_ids": domain.unpack(row.attraction_ids_json)}
    row.enabled, row.starts_on, row.ends_on = payload.enabled, payload.starts_on.isoformat(), payload.ends_on.isoformat() if payload.ends_on else None
    row.attraction_ids_json, row.tiers_json, row.categories_json = domain.packed(sorted(set(payload.attraction_ids))), domain.packed(payload.tiers), domain.packed(payload.categories)
    row.changed_by, row.changed_at, row.reason = user.id, datetime.now(), payload.reason.strip()
    db.flush()
    write_audit(db, user.employee, "配置公告发布授权", "announcement_grant", row.id, before=before, after=payload.model_dump(mode="json"), reason=payload.reason, ip_address=client_ip(request))
    db.commit()
    return {"ok": True, "id": row.id}


@router.post("/admin/projects")
def project(payload: ProjectInput, request: Request, db: Session = Depends(get_db), user: V2User = Depends(require_admin)):
    domain.lock(db)
    if not payload.name.strip():
        raise HTTPException(400, "请填写项目名称")
    row = db.get(AnnouncementProject, payload.id) if payload.id else None
    if payload.id and not row:
        raise HTTPException(404, "项目不存在")
    duplicate = db.query(AnnouncementProject).filter_by(name=payload.name.strip()).first()
    if duplicate and (not row or duplicate.id != row.id):
        raise HTTPException(409, "项目名称已存在")
    ids = set(payload.employee_ids)
    if db.query(Employee).filter(Employee.id.in_(ids), Employee.is_active.is_(True)).count() != len(ids):
        raise HTTPException(400, "项目成员包含不可用员工")
    if not row:
        row = AnnouncementProject(name=payload.name.strip(), created_by=user.id)
        db.add(row)
        db.flush()
    row.name, row.active = payload.name.strip(), payload.active
    existing = {m.employee_id: m for m in db.query(AnnouncementProjectMember).filter_by(project_id=row.id)}
    for employee_id, member in existing.items():
        member.active = employee_id in ids
    for employee_id in ids-existing.keys():
        db.add(AnnouncementProjectMember(project_id=row.id, employee_id=employee_id))
    db.flush()
    domain.sync_current(db)
    write_audit(db, user.employee, "配置公告项目", "announcement_project", row.id, after=payload.model_dump(mode="json"), ip_address=client_ip(request))
    db.commit()
    return {"ok": True, "id": row.id}


@router.get("/successors/{id}")
def successors(id: int, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    row = get_announcement(db, id)
    if not domain.admin(user):
        ensure_owner(row, user)
    version = domain.current_version(db, row)
    result = []
    for employee in db.query(Employee).filter_by(is_active=True):
        if employee.id == row.owner_id:
            continue
        try:
            domain.validate_scope(db, employee.id, domain.version_values(version))
        except HTTPException:
            continue
        result.append({"id": employee.id, "name": employee.name, "employee_no": employee.employee_no})
    return result


@router.get("/{id}")
def detail(id: int, version: int | None = None, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    domain.sync_current(db)
    row = get_announcement(db, id)
    revisions = db.query(AnnouncementVersion).filter_by(announcement_id=id).order_by(AnnouncementVersion.number.desc()).all()
    if version is not None:
        revision = next((v for v in revisions if v.number == version), None)
    else:
        revision = next((v for v in revisions if domain.can_read_version(db, user, row, v)), None)
    if not revision or not domain.can_read_version(db, user, row, revision):
        raise HTTPException(404, "公告不存在")
    result = domain.payload(db, user, row, revision, True)
    db.commit()
    return result
