"""Bounded exports using the same report builders and scope checks as direct downloads.

Task credentials live only in RAM until execution ends. Files expire after 30
minutes; a service restart intentionally invalidates tasks, rather than replaying
an export with stale authorization or silently issuing it twice.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import logging
from pathlib import Path
import re
import tempfile
from threading import Lock
from time import monotonic, time, sleep
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import TypeAdapter, ValidationError
from python_multipart.multipart import parse_options_header
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
from starlette.datastructures import Headers, UploadFile
from starlette.formparsers import FormParser, MultiPartException, MultiPartParser

from app.excel_export_utils import STATISTICS_WORKBOOK_INTERRUPTED
from app.hr_monthly_report import require_report
from app.http_utils import client_ip
from app.routers import announcements, declaration_statistics, hr_monthly_reports, statistics
from app.services.performance_rankings import pr_ranking_scope
from app.v2_auth import current_user, session_user, V2User
from app.database.connection import get_db, EXPORT_DIR, SessionLocal

router = APIRouter()
_lock = Lock()
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="report-export")
_jobs: dict[str, "Job"] = {}
TTL = 1800
MAX_BODY = 32 * 1024 * 1024
MAX_OUTPUT = 80 * 1024 * 1024
MAX_CACHE = 256 * 1024 * 1024
STATISTICS_EXPORT_PATH = "/api/statistics/export"
INVALID_EXPORT = "导出未完成，请回原页面核对权限、筛选或预览后重试"
MONTHLY_REPORT = "/api/hr-monthly-reports/export"
OPTIONAL_INTEGER = TypeAdapter(int | None)


@dataclass
class Job:
    id: str
    owner: int
    target: str
    label: str
    scope: tuple
    directory: tempfile.TemporaryDirectory
    created: float = field(default_factory=monotonic)
    status: str = "receiving"
    error: str = ""
    size: int = 0
    elapsed: float = 0
    finished: float = 0
    headers: dict = field(default_factory=dict)
    retry_filters: dict[str, str] | None = None
    retry_task_id: str | None = None

    def payload(self):
        data = {"id": self.id, "label": self.label, "status": self.status,
                "error": self.error, "bytes": self.size, "elapsed_seconds": round(self.elapsed, 2),
                "download_url": f"/api/export-tasks/{self.id}/file" if self.status == "completed" else None}
        if self.status == "failed" and self.retry_filters is not None:
            data["retry_filters"] = self.retry_filters
            data["retry_task_id"] = self.retry_task_id
        return data


def target_kind(target):
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or parts.fragment or not target.startswith("/api/"):
        raise HTTPException(400, "无效导出入口")
    labels = {STATISTICS_EXPORT_PATH: "景点数据 Excel",
              "/api/pr-rankings/export": "PR排名 Excel",
              "/api/declaration-statistics/export": "声明统计 Excel",
              "/api/hr-monthly-reports/export": "月报 PPTX"}
    if parts.path in labels:
        if parts.path == "/api/pr-rankings/export" and query_args(parts).get("category") == "points":
            return parts, "积点榜 Excel"
        return parts, labels[parts.path]
    if re.fullmatch(r"/api/announcements/[1-9]\d*/(?:export|followup-export)", parts.path):
        return parts, "公告签收报告"
    raise HTTPException(400, "此入口不支持后台导出")


def query_args(parts):
    return dict(parse_qsl(parts.query, keep_blank_values=True))


def optional_int(params, key, message):
    try:
        return OPTIONAL_INTEGER.validate_python(params.get(key))
    except ValidationError as exc:
        raise HTTPException(422, message) from exc


def export_scope(db, user, parts):
    """Resolve permission and receipt scope once per authorization boundary."""
    if user.rotation_temporary:
        raise HTTPException(403, "轮岗专用账号不可导出")
    path = parts.path
    visible = ()
    data = None
    if path == "/api/statistics/export":
        statistics.require_statistics_export(user)
    elif path == "/api/pr-rankings/export":
        statistics.require_rankings_export(user)
        params = query_args(parts)
        attraction_id = optional_int(params, "attraction_id", "景点圈参数无效")
        pr_ranking_scope(db, user, attraction_id, params.get("population", "frontline"), params.get("category", "overall"))
    elif path == "/api/declaration-statistics/export":
        declaration_statistics.require_declaration_export(user)
    elif path == MONTHLY_REPORT:
        require_report(user)
    else:
        aid = int(path.split("/")[3])
        version = optional_int(query_args(parts), "version", "公告版本格式无效")
        data = announcements.announcement_export_records(db, user, aid, version, followup=path.endswith("/followup-export"))
        # Recheck the *actual* visible receipt scope, including changed group
        # leadership and announcement grants, before serving a completed file.
        visible = tuple(sorted(str(row.get("employee_id", row.get("id"))) for row in data["items"]))
    return (user.id, user.account.id, user.employee.employee_no, user.employee.attraction_id,
            user.role.code, user.base_role.code, tuple(sorted(user.permissions)), visible), data


def checked_query(model, params):
    try:
        return model.model_validate(params)
    except ValidationError as exc:
        raise ValueError(INVALID_EXPORT) from exc


async def monthly_report(root, content_type, db, user):
    """Read the bounded request file using the same form parsers as Starlette."""
    async def stream():
        with (root / "request").open("rb") as source:
            while chunk := source.read(256 * 1024):
                yield chunk
        yield b""

    media_type, _ = parse_options_header(content_type)
    headers = Headers({"Content-Type": content_type})
    try:
        if media_type == b"multipart/form-data":
            form = await MultiPartParser(headers, stream()).parse()
        elif media_type == b"application/x-www-form-urlencoded":
            form = await FormParser(headers, stream()).parse()
        else:
            raise ValueError(INVALID_EXPORT)
    except MultiPartException as exc:
        raise ValueError(exc.message) from exc
    try:
        config = form.get("config")
        photos = form.getlist("photos")
        if not isinstance(config, str) or not config or any(not isinstance(photo, UploadFile) for photo in photos):
            raise ValueError(INVALID_EXPORT)
        return await hr_monthly_reports.export(config, photos, db, user)
    finally:
        await form.close()


async def render_report(parts, db, user, announcement_data, root, content_type, audit_ip):
    path, params = parts.path, query_args(parts)
    if path == "/api/statistics/export":
        query = checked_query(statistics.StatisticsExportQuery, params)
        return statistics.export_statistics(**query.model_dump(), db=db, user=user)
    if path == "/api/pr-rankings/export":
        query = checked_query(statistics.RankingsExportQuery, params)
        return statistics.export_pr_rankings(**query.model_dump(), db=db, user=user)
    if path == "/api/declaration-statistics/export":
        query = checked_query(declaration_statistics.DeclarationExportQuery, params)
        return declaration_statistics.export_declaration_statistics(**query.model_dump(), db=db, user=user, audit_ip=audit_ip)
    if path == MONTHLY_REPORT:
        return await monthly_report(root, content_type, db, user)
    followup = path.endswith("/followup-export")
    format = "xlsx" if followup else checked_query(announcements.AnnouncementExportQuery, params).format
    return announcements.export_report(int(path.split("/")[3]), format, db, user, announcement_data, followup=followup, audit_ip=audit_ip)


def expire():
    for key, job in list(_jobs.items()):
        if job.status in {"completed", "failed"} and monotonic() - (job.finished or job.created) > TTL:
            job.directory.cleanup()
            del _jobs[key]


def owned(key, user):
    with _lock:
        expire()
        job = _jobs.get(key)
        if job is None or job.owner != user.id:
            raise HTTPException(404, "导出任务不存在、已过期或服务已重启，请重新导出")
        return job


def run_job(job, raw_token, content_type, audit_ip):
    started = monotonic()
    job.status = "running"
    root = Path(job.directory.name)
    try:
        async def run():
            parts, _ = target_kind(job.target)
            with SessionLocal() as db:
                actor = session_user(db, raw_token, parts.path)
                scope, announcement_data = export_scope(db, actor, parts)
                if scope != job.scope:
                    raise ValueError("账号权限或可见范围已变化，请重新导出")
                response = await render_report(parts, db, actor, announcement_data, root, content_type, audit_ip)
            if response.status_code != 200:
                raise ValueError(INVALID_EXPORT)
            response_headers = response.headers
            media_type = response_headers.get("content-type", "").split(";")[0]
            if media_type not in {"application/pdf", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.openxmlformats-officedocument.presentationml.presentation"}:
                raise ValueError("服务器未返回有效报告")
            size = 0

            async def chunks():
                if isinstance(response, StreamingResponse):
                    async for chunk in response.body_iterator:
                        yield chunk
                else:
                    yield response.body

            with open(root / "result", "wb") as output:
                async for chunk in chunks():
                    size += len(chunk)
                    if size > MAX_OUTPUT:
                        raise ValueError("报告超过后台导出容量，请缩小导出范围后重试")
                    output.write(chunk)
            job.headers = {k: v for k, v in response_headers.items() if k in {"content-type", "content-disposition"}}
            job.size = size
        for attempt in range(4):
            try:
                asyncio.run(run())
                break
            except OperationalError as exc:
                # Only allowlisted exports are replayed. Existing handlers
                # close/rollback their failed session before control returns.
                # A successful handler commits before emitting its file, so
                # this never retries a completed business write or user action.
                if attempt == 3 or not any(text in str(exc.orig).lower() for text in ("database is locked", "database is busy")):
                    raise
                sleep(.15 * 2**attempt)
        job.status = "completed"
    except Exception as exc:
        logging.getLogger(__name__).exception("Background export failed: %s", job.id)
        if isinstance(exc, HTTPException):
            detail = exc.detail
            if (exc.status_code == 503 and urlsplit(job.target).path == STATISTICS_EXPORT_PATH
                    and isinstance(detail, dict) and detail.get("code") == STATISTICS_WORKBOOK_INTERRUPTED):
                job.retry_filters = detail["filters"]
                detail = detail["message"]
            job.error = detail if isinstance(detail, str) else INVALID_EXPORT
        else:
            job.error = str(exc) if isinstance(exc, ValueError) else "报告制作中断，请回原页面重新导出"
        (root / "result").unlink(missing_ok=True)
        job.status = "failed"
    finally:
        (root / "request").unlink(missing_ok=True)
        job.elapsed = monotonic() - started
        job.finished = monotonic()
        with _lock:
            # Generated-file cache is bounded independently of queue length.
            completed = [j for j in _jobs.values() if j.status == "completed"]
            size = sum(j.size for j in completed)
            for old in sorted(completed, key=lambda j: j.finished):
                if size <= MAX_CACHE:
                    break
                old.directory.cleanup()
                _jobs.pop(old.id, None)
                size -= old.size


def reserve_task(user_id, target, label, scope):
    """Reserve a queue slot while the caller holds the task lock."""
    active = [j for j in _jobs.values() if j.status in {"receiving", "queued", "running"}]
    if len(active) >= 4 or sum(j.owner == user_id for j in active) >= 2:
        raise HTTPException(429, "导出队列已满，请等待当前任务完成后再试")
    if len(_jobs) >= 80:
        raise HTTPException(429, "导出缓存暂满，请稍后再试")
    directory = EXPORT_DIR / "background-tasks"
    directory.mkdir(parents=True, exist_ok=True)
    # Only our two named scratch files in resolved, direct child folders.
    active_dirs = {Path(j.directory.name) for j in _jobs.values()}
    for child in directory.iterdir():
        if child in active_dirs or child.is_symlink() or not child.is_dir() or child.resolve().parent != directory.resolve():
            continue
        if re.fullmatch(r"task-[a-z0-9_]+", child.name) and time() - child.stat().st_mtime > TTL:
            for name in ("request", "result"):
                (child / name).unlink(missing_ok=True)
            try:
                child.rmdir()
            except OSError:
                pass
    job = Job(uuid4().hex, user_id, target, label, scope, tempfile.TemporaryDirectory(prefix="task-", dir=directory))
    _jobs[job.id] = job
    return job


def enqueue_task(job, request):
    job.status = "queued"
    _pool.submit(run_job, job, request.cookies.get("rc_v2_session"), request.headers.get("content-type", ""), client_ip(request))


@router.post("/export-tasks", status_code=202)
async def create_task(request: Request, target: str, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    parts, label = target_kind(target)
    scope, _ = export_scope(db, user, parts)
    size_limit = MAX_BODY if parts.path == MONTHLY_REPORT else 0
    with _lock:
        expire()
        job = reserve_task(user.id, target, label, scope)
    try:
        size = 0
        with open(Path(job.directory.name) / "request", "wb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > size_limit:
                    raise HTTPException(413, "导出素材过大，照片总计不得超过30MB")
                output.write(chunk)
        enqueue_task(job, request)
        return job.payload()
    except BaseException:
        with _lock:
            _jobs.pop(job.id, None)
            job.directory.cleanup()
        raise


@router.post("/export-tasks/{key}/retry", status_code=202)
def retry_task(key: str, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    source = owned(key, user)
    parts, _ = target_kind(source.target)
    scope, _ = export_scope(db, user, parts)
    if scope != source.scope:
        raise HTTPException(403, "账号权限或可见范围已变化，请重新导出")
    with _lock:
        expire()
        if _jobs.get(key) is not source:
            raise HTTPException(404, "导出任务已过期，请回原页面重新导出")
        if source.status != "failed" or source.retry_filters is None:
            raise HTTPException(409, "此任务无法按原条件重试，请查看任务状态")
        if source.retry_task_id is not None:
            existing = _jobs.get(source.retry_task_id)
            if existing is None:
                raise HTTPException(409, "已提交的重试任务已失效，请回原页面重新导出")
            return existing.payload()
        job = reserve_task(user.id, source.target, source.label, scope)
        try:
            (Path(job.directory.name) / "request").touch()
            enqueue_task(job, request)
        except BaseException:
            _jobs.pop(job.id, None)
            job.directory.cleanup()
            raise
        source.retry_task_id = job.id
        return job.payload()


@router.get("/export-tasks")
def list_tasks(user: V2User = Depends(current_user)):
    with _lock:
        expire()
        return {"items": [j.payload() for j in reversed(list(_jobs.values())) if j.owner == user.id]}


@router.get("/export-tasks/{key}/file")
def download_task(key: str, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    job = owned(key, user)
    parts, _ = target_kind(job.target)
    scope, _ = export_scope(db, user, parts)
    if scope != job.scope:
        raise HTTPException(403, "账号权限或可见范围已变化，请重新导出")
    if job.status != "completed":
        raise HTTPException(409, "报告尚未制作完成")
    return FileResponse(Path(job.directory.name) / "result", headers={**job.headers, "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
