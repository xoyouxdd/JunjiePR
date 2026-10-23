"""Authenticated video library, resumable bounded uploads and private playback."""
from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import json
import secrets
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text, or_, and_
from sqlalchemy.orm import Session

from app.v2_auth import current_user, V2User
from app.v2_crypto import token_hash
from app.v2_database import get_db
from app.v2_models import Attraction, Employee
from app.video_models import LearningVideo, VideoCircle, VideoAudiencePolicy, VideoJob, VideoUpload, VideoPlayback, VideoProgress
from app.services.video_access import (scope_ids, upload_scope_ids, can_upload, can_manage, require_manage, require_upload_targets, video_targets, senior,
                                       require_view, audit, NOTICE_TEXT, NOTICE_VERSION, audience_options, validate_audience,
                                       audience_circles, policy_data, recipient_condition, administrator, can_edit_audience, AUDIENCE_LABELS,
                                       publisher_circle, publish_block_reason, can_delete)
from app import video_media as media
from app.video_worker import enqueue

router = APIRouter(prefix="/video-learning", tags=["video-learning"])
_upload_lock = threading.Lock()


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    cover_title: str = Field(default="", max_length=40)
    description: str = Field(default="", max_length=2000)
    attraction_id: int | None = Field(default=None, gt=0)
    attraction_ids: list[int] = Field(default_factory=list, max_length=128)
    all_circles: bool = False
    audience_codes: list[str] | None = Field(default=None, max_length=len(AUDIENCE_LABELS))
    replaces_id: int | None = None

    @field_validator("title", "cover_title", "description")
    @classmethod
    def clean(cls, value):
        value = value.strip()
        if any(ord(c) < 32 and c not in "\n\t" for c in value):
            raise ValueError("文字包含不支持的控制字符")
        return value


class UploadConfig(Config):
    size: int = Field(gt=0, le=media.MAX_BYTES)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class Consent(BaseModel):
    accepted: bool
    notice_version: str


class Progress(BaseModel):
    position: int = Field(ge=0, le=media.MAX_SECONDS)


class Cover(BaseModel):
    second: int = Field(ge=0, le=media.MAX_SECONDS)
    title: str = Field(min_length=1, max_length=40)


class AudienceChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    audience_codes: list[str] = Field(min_length=1, max_length=len(AUDIENCE_LABELS))
    revision: int = Field(ge=0)


def video_or_404(db, video_id):
    row = db.get(LearningVideo, video_id)
    if not row or row.status == "deleted":
        raise HTTPException(404, "视频不存在或已删除")
    return row


def selected_targets(db, body):
    if body.all_circles:
        if body.attraction_ids or body.attraction_id is not None:
            raise HTTPException(400, '全选与指定景点不能同时提交')
        return scope_all_ids(db)
    ids = set(body.attraction_ids)
    if body.attraction_id is not None:
        if ids and body.attraction_id not in ids:
            raise HTTPException(400, '景点范围不一致')
        ids.add(body.attraction_id)
    if not ids or any(id <= 0 for id in ids):
        raise HTTPException(400, '请至少选择一个有效景点圈')
    return ids


def scope_all_ids(db):
    return {r.id for r in db.query(Attraction).filter_by(active=True, employee_circle=True)}


def stored_targets(config):
    return set(config.get('attraction_ids') or [config['attraction_id']])


def configured_audience(db, user, body):
    if body.all_circles:
        raise HTTPException(400, '请选择观看人员层级，不再使用景点圈全选')
    if body.audience_codes is None:
        # Compatibility for the earlier local-only upload form; never broaden it.
        selected = set(body.attraction_ids or ([body.attraction_id] if body.attraction_id else []))
        if not selected:
            raise HTTPException(400,'请至少选择一类观看人员')
        if selected and selected != {user.employee.attraction_id}:
            raise HTTPException(403, '请使用新的观看人员范围选择')
    codes = validate_audience(db,user,body.audience_codes if body.audience_codes is not None else ['local_tr'])
    local = user.employee.attraction_id
    active = scope_all_ids(db)
    local = local if local in active else min(active) if active else None
    return codes, local, audience_circles(db,local,codes)


def payload(db, row, user):
    progress = db.query(VideoProgress).filter_by(video_id=row.id, employee_id=user.id).first()
    cover_job = db.query(VideoJob).filter_by(key=f'cover:{row.id}').first()
    owner = db.get(Employee, row.owner_id)
    targets = video_targets(db, row)
    circles = db.query(Attraction).filter(Attraction.id.in_(targets)).order_by(Attraction.id).all()
    audience = policy_data(db,row)
    reason=publish_block_reason(db,user,row) if can_manage(db,user,row) else ''
    editor_local=publisher_circle(db,user,row) if can_edit_audience(db,user,row) else None
    editor_circle=db.get(Attraction,editor_local) if editor_local else None
    return {"id": row.id, "title": row.title, "cover_title": row.cover_title, "description": row.description,
            "attraction_id": row.attraction_id, "attraction_ids": sorted(targets),
            "attraction_name": '、'.join(c.name for c in circles),
            "target_circles": [{"id": c.id, "name": c.name} for c in circles],
            "audience": audience, "audience_label": '、'.join(AUDIENCE_LABELS[c]+('（'+audience['local_circle_name']+'）' if c.startswith('local_') else '') for c in audience['codes']),
            "audience_editor": {"audience_options":audience_options(db,user,editor_local),"local_circle_name":editor_circle.name if editor_circle else ''} if can_edit_audience(db,user,row) else None,
            "can_edit_audience": can_edit_audience(db,user,row),
            "owner_name": owner.name if owner else "", "version": row.version, "status": row.status, "replaces_id": row.replaces_id,
            "original_bytes": row.original_bytes, "duration": row.duration, "profiles": json.loads(row.profiles),
            "error": row.error, "position": progress.position if progress else 0,
            "can_publish": can_manage(db, user, row) and not reason, "publish_block_reason":reason,
            "can_replace": can_manage(db,user,row) and row.status=='published', "can_delete":can_delete(user,row),
            "cover_second": row.cover_second, "can_manage": can_manage(db, user, row),
            "cover_status": cover_job.status if cover_job else 'ready', "cover_error": cover_job.error if cover_job else '',
            "cover_url": f"/api/video-learning/videos/{row.id}/cover" if (media.video_dir(row.id)/"cover.jpg").is_file() else "",
            "published_at": row.published_at.isoformat() if row.published_at else "",
            "created_at": row.created_at.isoformat()}


@router.get("/options")
def options(db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    ids = scope_ids(db, user)
    upload_ids = upload_scope_ids(db, user)
    local_circle = db.get(Attraction,user.employee.attraction_id) if user.employee.attraction_id else None
    return {"circles": [{"id": r.id, "name": r.name} for r in db.query(Attraction).filter(Attraction.id.in_(ids)).order_by(Attraction.id)],
            "upload_circles": [{"id": r.id, "name": r.name} for r in db.query(Attraction).filter(Attraction.id.in_(upload_ids)).order_by(Attraction.id)],
            "can_upload": bool(upload_ids), "can_manage_history":db.query(LearningVideo.id).filter(LearningVideo.owner_id==user.id,LearningVideo.status!='deleted').first() is not None, "can_select_multiple": senior(user), "tools_ready": media.tools_ready(),
            "audience_options": audience_options(db,user),
            "local_circle_name": local_circle.name if local_circle else '',
            "notice_version": NOTICE_VERSION, "notice": NOTICE_TEXT,
            "max_bytes": media.MAX_BYTES, "max_seconds": media.MAX_SECONDS, "chunk_bytes": media.CHUNK_BYTES}


@router.get("/videos")
def listing(attraction_id: int | None = None, keyword: str = "", mine: bool = False, continued: bool = False,
            offset: int = 0, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    ids = scope_ids(db, user)
    if attraction_id is not None:
        if attraction_id not in ids:
            raise HTTPException(403, "没有查看该景点的权限")
        ids = {attraction_id}
    explicit = db.query(VideoCircle.video_id).filter(VideoCircle.video_id == LearningVideo.id)
    selected = explicit.filter(VideoCircle.attraction_id.in_(ids)).exists()
    global_policy = db.query(VideoAudiencePolicy.video_id).filter(VideoAudiencePolicy.video_id == LearningVideo.id,
        or_(*(VideoAudiencePolicy.codes.contains('"'+code+'"',autoescape=True) for code in ['all_tr','all_lead','all_gsm','am','om','all_people']))).exists()
    audience_scope=or_(global_policy, selected, ~explicit.exists() & LearningVideo.attraction_id.in_(ids))
    if mine and attraction_id is None:
        audience_scope=or_(audience_scope,and_(LearningVideo.owner_id==user.id,LearningVideo.status=='withdrawn'))
    query = db.query(LearningVideo).filter(LearningVideo.status != "deleted", audience_scope)
    if mine:
        query = query.filter(LearningVideo.owner_id == user.id)
    else:
        query = query.filter(LearningVideo.status == "published")
        if not administrator(user):
            query = query.filter(or_(recipient_condition(db,user), (LearningVideo.owner_id == user.id) if can_upload(db,user) else False))
    if keyword.strip():
        query = query.filter(LearningVideo.title.contains(keyword.strip()[:120], autoescape=True))
    if continued:
        query = query.join(VideoProgress, VideoProgress.video_id == LearningVideo.id).filter(VideoProgress.employee_id == user.id)
        query = query.order_by(VideoProgress.updated_at.desc())
    else:
        query = query.order_by(LearningVideo.created_at.desc(), LearningVideo.id.desc())
    total = query.count(); offset = max(0, offset)
    rows = query.offset(offset).limit(24).all()
    return {"items": [payload(db, r, user) for r in rows], "total": total, "next_offset": offset+len(rows) if offset+len(rows)<total else None}


@router.get("/videos/{video_id}")
def detail(video_id: int, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    row = video_or_404(db, video_id)
    if not can_manage(db, user, row) and not can_delete(user,row):
        require_view(db, user, row)
    return payload(db, row, user)


@router.post("/uploads")
def begin_upload(body: UploadConfig, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    codes, local, targets = configured_audience(db,user,body)
    require_upload_targets(db, user, targets)
    if not body.title:
        raise HTTPException(400, "请填写视频标题")
    if not media.tools_ready():
        raise HTTPException(503, "视频处理工具尚未配置，暂不能上传")
    if body.replaces_id:
        old = video_or_404(db, body.replaces_id); require_manage(db, user, old)
    config = body.model_dump(exclude={"size", "fingerprint", "all_circles", "audience_codes"})
    config['attraction_ids'] = sorted(targets)
    config['attraction_id'] = local
    config['audience_codes'] = codes
    config['audience_local'] = local
    config['replaces_audience_revision'] = policy_data(db,old)['revision'] if body.replaces_id else None
    config["cover_title"] = config["cover_title"] or body.title[:40]
    encoded = json.dumps(config, ensure_ascii=False, sort_keys=True)
    with _upload_lock:
        existing = db.query(VideoUpload).filter_by(owner_id=user.id, fingerprint=body.fingerprint, size=body.size, config=encoded).filter(VideoUpload.expires_at > datetime.now()).first()
        if existing:
            part = media.VIDEO_DIR / 'uploads' / (existing.id+'.part')
            if not existing.video_id and existing.offset and (not part.is_file() or part.stat().st_size < existing.offset):
                existing.offset = 0
                db.commit()
            return {"upload_id": existing.id, "offset": existing.offset, "video_id": existing.video_id}
        if db.query(VideoUpload).filter_by(owner_id=user.id, video_id=None).filter(VideoUpload.expires_at > datetime.now()).count() >= 3:
            raise HTTPException(429, "已有3个未完成上传，请先继续或取消")
        try:
            reserved = sum(r.size-r.offset for r in db.query(VideoUpload).filter(VideoUpload.video_id.is_(None), VideoUpload.expires_at > datetime.now()))
            media.ensure_space(body.size + reserved)
        except RuntimeError as exc:
            raise HTTPException(507, str(exc))
        row = VideoUpload(id=secrets.token_hex(24), owner_id=user.id, fingerprint=body.fingerprint,
                          config=encoded, size=body.size, offset=0, expires_at=datetime.now()+timedelta(days=1))
        db.add(row); db.commit()
    return {"upload_id": row.id, "offset": 0, "video_id": None}


def upload_row(db, user, upload_id):
    row = db.get(VideoUpload, upload_id)
    if not row or row.owner_id != user.id or row.expires_at < datetime.now():
        raise HTTPException(404, "上传任务不存在或已过期，请重新选择视频")
    config = json.loads(row.config)
    validate_audience(db,user,config.get('audience_codes',['local_tr']))
    if any(code.startswith('local_') for code in config.get('audience_codes',[])):
        if config.get('audience_local',config['attraction_id']) != user.employee.attraction_id:
            raise HTTPException(403,'所属圈已变化，请重新选择观看范围后上传')
    require_upload_targets(db, user, stored_targets(config))
    return row


@router.get("/uploads/{upload_id}")
def upload_status(upload_id: str, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    row = upload_row(db, user, upload_id)
    return {"offset": row.offset, "size": row.size, "video_id": row.video_id}


@router.put("/uploads/{upload_id}")
async def upload_chunk(upload_id: str, offset: int, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    # Bound bytes even if Content-Length is absent or deliberately false.
    row = upload_row(db, user, upload_id)
    content = bytearray()
    async for piece in request.stream():
        if len(content) + len(piece) > media.CHUNK_BYTES:
            raise HTTPException(413, "单个视频分片不能超过4MB")
        content.extend(piece)
    if offset < 0 or offset % media.CHUNK_BYTES or len(content) != min(media.CHUNK_BYTES, row.size-offset) or not content:
        raise HTTPException(400, "视频分片大小或位置不正确")
    with _upload_lock:
        db.rollback()
        row = upload_row(db, user, upload_id)
        if row.video_id:
            raise HTTPException(409, "视频已经上传完成")
        path = media.VIDEO_DIR / "uploads" / (row.id + ".part")
        path.parent.mkdir(parents=True, exist_ok=True)
        if offset < row.offset:
            with path.open('rb') as source:
                source.seek(offset)
                if source.read(len(content)) == content:
                    return {"offset": row.offset}
            raise HTTPException(409, "重传分片内容与原视频不同，请重新开始")
        if offset != row.offset:
            raise HTTPException(409, "分片进度已变化，请先核对上传状态")
        # A crash between file write and DB commit is safe: overwrite the same
        # offset and discard the uncommitted tail before continuing.
        with path.open('r+b' if path.exists() else 'wb') as target:
            target.seek(offset); target.write(content); target.truncate()
        row.offset += len(content); db.commit()
    return {"offset": row.offset}


@router.delete("/uploads/{upload_id}")
def cancel_upload(upload_id: str, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    with _upload_lock:
        # Removing one's own unfinished bytes must remain possible after a
        # duty expires or the publisher moves circles; this grants no upload.
        row = db.get(VideoUpload, upload_id)
        if not row or row.owner_id != user.id:
            raise HTTPException(404, '上传任务不存在或不属于当前账号')
        if row.video_id:
            raise HTTPException(409, "上传已经完成，请在视频管理中撤下")
        (media.VIDEO_DIR / "uploads" / (row.id + ".part")).unlink(missing_ok=True)
        db.delete(row); db.commit()
    return {"ok": True}


@router.post("/uploads/{upload_id}/finish")
def finish_upload(upload_id: str, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    with _upload_lock:
        row = upload_row(db, user, upload_id)
        if row.video_id:
            return {"video_id": row.video_id}
        if row.offset != row.size:
            raise HTTPException(409, "视频尚未上传完整")
        path = media.VIDEO_DIR / "uploads" / (row.id + ".part")
        chunks = []
        with path.open('rb') as source:
            while piece := source.read(media.CHUNK_BYTES):
                chunks.append(hashlib.sha256(piece).hexdigest())
        if hashlib.sha256(''.join(chunks).encode()).hexdigest() != row.fingerprint:
            raise HTTPException(400, "视频完整性核对失败，请取消后重新上传")
        config = json.loads(row.config)
        if config['replaces_id']:
            old = video_or_404(db, config['replaces_id']); require_manage(db, user, old)
            if policy_data(db,old)['revision'] != config.get('replaces_audience_revision',0):
                raise HTTPException(409, '原视频发布范围已变化，请重新上传')
        targets = stored_targets(config)
        config.pop('attraction_ids', None)
        codes = config.pop('audience_codes',['local_tr'])
        local = config.pop('audience_local',config['attraction_id'])
        replaces_revision = config.pop('replaces_audience_revision',None)
        video = LearningVideo(owner_id=user.id, original_bytes=row.size, **config)
        db.add(video); db.flush()
        db.add(VideoAudiencePolicy(video_id=video.id,local_attraction_id=local,codes=json.dumps(codes),
                                   replaces_revision=replaces_revision))
        db.add_all(VideoCircle(video_id=video.id, attraction_id=id) for id in sorted(targets))
        directory = media.video_dir(video.id); directory.mkdir(parents=True, exist_ok=True)
        # Copy, not rename: a rolled-back transaction leaves resumable bytes intact.
        import shutil
        shutil.copyfile(path, directory / "source.mov")
        enqueue(db, video.id, f"source:{video.id}", "source")
        row.video_id = video.id
        audit(db, user, request, video.id, "上传学习视频", {"audience_codes": codes, "local_attraction_id":local, "bytes": row.size})
        db.commit()
        path.unlink(missing_ok=True)
        return {"video_id": video.id}


@router.post("/videos/{video_id}/retry")
def retry(video_id: int, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    row = video_or_404(db, video_id); require_manage(db, user, row)
    job = db.query(VideoJob).filter_by(key=f"source:{video_id}").first()
    if not job or job.status != 'failed':
        raise HTTPException(409, "当前没有可重试的失败任务")
    job.status = 'queued'; job.error = ''; job.updated_at = datetime.now()
    row.status = 'processing'; row.error = ''
    audit(db, user, request, row.id, "重试视频处理"); db.commit()
    return {"ok": True}


@router.post("/videos/{video_id}/cover")
def choose_cover(video_id: int, body: Cover, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    db.rollback(); db.execute(text('BEGIN IMMEDIATE'))
    row = video_or_404(db, video_id); require_manage(db, user, row)
    if row.status != "ready" or body.second >= row.duration or not body.title.strip():
        raise HTTPException(409, "只能在发布前选择视频范围内的封面画面")
    row.cover_title = body.title.strip(); row.cover_second = body.second
    key = f"cover:{row.id}"
    job = enqueue(db, row.id, key, 'cover')
    if job.status == 'processing':
        raise HTTPException(409, "封面正在处理中，请稍后再试")
    job.status = 'queued'; job.error = ''; job.updated_at = datetime.now()
    audit(db, user, request, row.id, "调整视频封面"); db.commit()
    return {"ok": True}


@router.patch("/videos/{video_id}")
def edit_metadata(video_id: int, body: Config, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    db.rollback(); db.execute(text('BEGIN IMMEDIATE'))
    row = video_or_404(db, video_id); require_manage(db, user, row)
    if row.status != 'ready' or body.replaces_id != row.replaces_id or not body.title:
        raise HTTPException(409, "请在待发布状态编辑内容；所属景点与原版本关联不能更改")
    job = enqueue(db, row.id, f'cover:{row.id}', 'cover')
    if job.status == 'processing':
        raise HTTPException(409, "封面正在处理中，请稍后编辑")
    row.title = body.title; row.cover_title = body.cover_title or body.title[:40]; row.description = body.description
    job.status = 'queued'; job.error = ''; job.updated_at = datetime.now()
    audit(db, user, request, row.id, "编辑学习视频信息"); db.commit()
    return {"ok": True}


@router.patch('/videos/{video_id}/audience')
def change_audience(video_id: int, body: AudienceChange, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    db.rollback(); db.execute(text('BEGIN IMMEDIATE'))
    row = video_or_404(db,video_id)
    if not can_edit_audience(db,user,row):
        raise HTTPException(403,'仅发布者或系统管理员可调整观看范围')
    before = policy_data(db,row)
    if body.revision != before['revision']:
        raise HTTPException(409,'观看范围已被其他操作更新，请刷新后再保存')
    subject_local = publisher_circle(db,user,row)
    codes = validate_audience(db,user,body.audience_codes,subject_local)
    local = subject_local
    if not any(c.startswith('local_') for c in codes):
        local = before['local_attraction_id']
    if row.status == 'withdrawn':
        raise HTTPException(409,'历史已撤下版本不再调整，请修改当前版本')
    policy = db.get(VideoAudiencePolicy,video_id)
    if not policy:
        policy = VideoAudiencePolicy(video_id=video_id,local_attraction_id=local,codes='[]',revision=0)
        db.add(policy)
    policy.codes=json.dumps(codes);policy.local_attraction_id=local;policy.revision=before['revision']+1
    if row.replaces_id:
        policy.replaces_revision=policy_data(db,db.get(LearningVideo,row.replaces_id))['revision']
    db.query(VideoCircle).filter_by(video_id=video_id).delete()
    targets=audience_circles(db,local,codes)
    db.add_all(VideoCircle(video_id=video_id,attraction_id=id) for id in targets)
    # Retain playback audit rows/history. Every request checks the current policy.
    from app.services.audit import write_audit
    write_audit(db,user,'调整学习视频观看范围','video_learning',video_id,before=before,
                after={'codes':codes,'local_attraction_id':local,'revision':policy.revision},
                ip_address=request.client.host if request.client else None)
    db.commit()
    return {'ok':True,'revision':policy.revision}


@router.get("/videos/{video_id}/cover")
def cover_file(video_id: int, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    row = video_or_404(db, video_id)
    if not can_manage(db, user, row) and not can_delete(user,row):
        require_view(db, user, row)
    path = media.video_dir(video_id) / "cover.jpg"
    if not path.is_file():
        raise HTTPException(404, "封面尚未生成")
    return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'private, no-store'})


@router.post("/videos/{video_id}/publish")
def publish(video_id: int, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    # Serialize read/validate/write so competing replacements cannot both
    # publish against the same original version.
    db.rollback()
    db.execute(text('BEGIN IMMEDIATE'))
    row = video_or_404(db, video_id); require_manage(db, user, row)
    reason=publish_block_reason(db,user,row)
    if reason:
        raise HTTPException(403,reason)
    policy = db.get(VideoAudiencePolicy,row.id)
    current_audience = policy_data(db,row)
    if row.status == 'published':
        return {"ok": True}
    pending = db.query(VideoJob).filter_by(video_id=row.id, kind='cover').filter(VideoJob.status != 'ready').first()
    if row.status != 'ready' or pending:
        raise HTTPException(409, "视频和封面处理完成后才能发布")
    if any(not media.shared_path(row,q) for q in ('720','1080')):
        raise HTTPException(409,'请先预览，等待共享品牌水印版本准备完成后发布')
    if row.replaces_id:
        old = video_or_404(db, row.replaces_id); require_manage(db, user, old)
        if policy and policy.replaces_revision != policy_data(db,old)['revision']:
            raise HTTPException(409, '原视频观看范围已更新，请重新核对并保存新版本观看范围')
        if old.status != 'published':
            raise HTTPException(409, "原视频已被更新或撤下，请重新核对版本")
        row.version = old.version+1; old.status = 'withdrawn'
    row.status = 'published'; row.published_at = datetime.now()
    audit(db, user, request, row.id, "发布学习视频", {"version": row.version, "attraction_ids": sorted(video_targets(db, row))}); db.commit()
    return {"ok": True}


@router.post("/videos/{video_id}/withdraw")
def withdraw(video_id: int, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    db.rollback(); db.execute(text('BEGIN IMMEDIATE'))
    row = video_or_404(db, video_id); require_manage(db, user, row)
    if row.status not in ('ready', 'published'):
        raise HTTPException(409, "当前状态不能撤下")
    row.status = 'withdrawn'
    audit(db, user, request, row.id, "撤下学习视频"); db.commit()
    return {"ok": True}


@router.post("/videos/{video_id}/consent")
def consent(video_id: int, body: Consent, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    row = video_or_404(db, video_id); require_view(db, user, row, preview=True)
    if not body.accepted or body.notice_version != NOTICE_VERSION:
        raise HTTPException(400, "请阅读并确认最新保密提示")
    token = str(row.version)+"."+secrets.token_urlsafe(32)
    db.add(VideoPlayback(id=token_hash(token), video_id=video_id, employee_id=user.id,
                         employee_no=user.employee.employee_no, session_hash=token_hash(request.cookies['rc_v2_session']),
                         notice_version=NOTICE_VERSION, expires_at=datetime.now()+timedelta(minutes=30)))
    audit(db, user, request, row.id, "确认视频保密提示", {"notice_version": NOTICE_VERSION, "video_version": row.version})
    db.commit()
    return {"token": token, "expires_in": 1800}


def playback_access(db, user, request, video_id, token):
    video = video_or_404(db, video_id); require_view(db, user, video, preview=True)
    grant = db.get(VideoPlayback, token_hash(token))
    if not grant or grant.video_id != video_id or grant.employee_id != user.id or grant.session_hash != token_hash(request.cookies['rc_v2_session']) or grant.expires_at <= datetime.now() or grant.employee_no != user.employee.employee_no:
        raise HTTPException(403, "播放授权已失效，请重新确认保密提示")
    if token.split(".",1)[0] != str(video.version):
        raise HTTPException(403,"视频版本已变化，请重新确认")
    if grant.notice_version != NOTICE_VERSION:
        raise HTTPException(403, "保密提示已更新，请重新确认")
    return video, grant


def shared_ready(db,video,quality,*,enqueue_missing=False):
    if quality not in ('720','1080'):raise HTTPException(400,'不支持的清晰度')
    path=media.shared_path(video,quality)
    if path:return path,None
    job=db.query(VideoJob).filter_by(key=f'shared:{video.id}:{media.BRAND_REVISION}').first()
    if enqueue_missing:
        job=job or enqueue(db,video.id,f'shared:{video.id}:{media.BRAND_REVISION}','shared')
        if job.status in ('failed','ready'):job.status='queued';job.error=''
        db.commit()
    return None,job


@router.post('/videos/{video_id}/playback')
def prepare(video_id:int,request:Request,token:str,quality:str='720',db:Session=Depends(get_db),user:V2User=Depends(current_user)):
    video,grant=playback_access(db,user,request,video_id,token)
    path,job=shared_ready(db,video,quality,enqueue_missing=True)
    return {'status':'ready' if path else job.status,'mode':'shared'}


@router.get('/videos/{video_id}/playback')
def playback_status(video_id:int,request:Request,token:str,quality:str='720',db:Session=Depends(get_db),user:V2User=Depends(current_user)):
    video,grant=playback_access(db,user,request,video_id,token)
    path,job=shared_ready(db,video,quality)
    return {'status':'ready' if path else job.status if job else 'missing','error':job.error if job else '',
            'mode':'shared','employee_no':grant.employee_no,
            'url':f'/api/video-learning/videos/{video_id}/stream?token={token}&quality={quality}' if path else None}


@router.get('/videos/{video_id}/authorize')
def authorize(video_id:int,request:Request,token:str,quality:str='720',db:Session=Depends(get_db),user:V2User=Depends(current_user)):
    video,grant=playback_access(db,user,request,video_id,token)
    path,_=shared_ready(db,video,quality)
    if not path:raise HTTPException(409,'共享视频尚未准备完成')
    stat=path.stat()
    if grant.expires_at < datetime.now()+timedelta(minutes=20):
        grant.expires_at=datetime.now()+timedelta(minutes=30);db.commit()
    # No token in persistent block keys; login sessions get disjoint namespaces.
    identity=token_hash(str(user.id)+':'+grant.session_hash)[:32]
    return JSONResponse({'employee_no':grant.employee_no,'identity':identity,'video_id':video.id,'version':video.version,
                         'quality':quality,'bytes':stat.st_size,'generation':str(stat.st_mtime_ns)},headers={'Cache-Control':'no-store'})


@router.get('/videos/{video_id}/stream')
def stream(video_id:int,request:Request,token:str,quality:str='720',db:Session=Depends(get_db),user:V2User=Depends(current_user)):
    video,grant=playback_access(db,user,request,video_id,token)
    path,_=shared_ready(db,video,quality)
    if not path:raise HTTPException(409,'共享视频尚未准备完成')
    if grant.expires_at < datetime.now()+timedelta(minutes=20):
        grant.expires_at=datetime.now()+timedelta(minutes=30);db.commit()
    return FileResponse(path,media_type='video/mp4',headers={'Cache-Control':'private, no-store','Referrer-Policy':'no-referrer','Content-Disposition':'inline'})


@router.get('/cache-worker.js',include_in_schema=False)
def cache_worker():
    return FileResponse(Path(__file__).resolve().parent.parent/'static'/'js'/'video-cache-worker.js',media_type='application/javascript',
                        headers={'Cache-Control':'no-cache','Service-Worker-Allowed':'/'})


@router.delete('/videos/{video_id}')
def delete_video(video_id:int,request:Request,db:Session=Depends(get_db),user:V2User=Depends(current_user)):
    db.rollback();db.execute(text('BEGIN IMMEDIATE'))
    row=video_or_404(db,video_id)
    if row.owner_id!=user.id and not administrator(user):raise HTTPException(403,'仅发布者或系统管理员可删除')
    if row.status!='withdrawn':raise HTTPException(409,'请先撤下视频再删除')
    row.status='deleted'
    # Keep row IDs, replacement FKs, access and audit records; remove only this version's files.
    for job in db.query(VideoJob).filter(VideoJob.video_id==video_id,VideoJob.status=='queued'):job.status='cancelled'
    enqueue(db,video_id,f'delete:{video_id}','delete')
    audit(db,user,request,video_id,'删除学习视频',{'version':row.version,'scope':'selected_version','files':'cleanup_queued'})
    db.commit();return {'ok':True,'cleanup':'queued'}


@router.put("/videos/{video_id}/progress")
def progress(video_id: int, body: Progress, request: Request, token: str, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    video, grant = playback_access(db, user, request, video_id, token)
    row = db.query(VideoProgress).filter_by(video_id=video_id, employee_id=user.id).first()
    if not row:
        row = VideoProgress(video_id=video_id, employee_id=user.id); db.add(row)
    row.position = min(body.position, video.duration); row.updated_at = datetime.now()
    grant.expires_at = datetime.now()+timedelta(minutes=30)
    db.commit()
    return {"ok": True}
