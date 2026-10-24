"""Bounded exports through the existing HTTP handlers, never a second renderer.

Task credentials live only in RAM until execution ends. Files expire after 30
minutes; a service restart intentionally invalidates tasks, rather than replaying
an export with stale authorization or silently issuing it twice.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import re
import tempfile
from threading import Lock
from time import monotonic, time, sleep
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError

from app.v2_auth import current_user, V2User
from app.v2_database import get_db, EXPORT_DIR, SessionLocal

router = APIRouter()
_lock = Lock()
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="report-export")
_jobs: dict[str, "Job"] = {}
TTL = 1800
MAX_BODY = 32 * 1024 * 1024
MAX_OUTPUT = 80 * 1024 * 1024
MAX_CACHE = 256 * 1024 * 1024


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

    def payload(self):
        return {"id": self.id, "label": self.label, "status": self.status,
                "error": self.error, "bytes": self.size,
                "elapsed_seconds": round(self.elapsed, 2),
                "download_url": f"/api/export-tasks/{self.id}/file" if self.status == "completed" else None}


def target_kind(target):
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or parts.fragment or not target.startswith("/api/"):
        raise HTTPException(400, "无效导出入口")
    labels = {"/api/statistics/export": "景点数据 Excel",
              "/api/pr-rankings/export": "PR排名 Excel",
              "/api/declaration-statistics/export": "声明统计 Excel",
              "/api/hr-monthly-reports/export": "月报 PPTX"}
    if parts.path in labels:
        return parts, labels[parts.path]
    if re.fullmatch(r"/api/announcements/[1-9]\d*/(?:export|followup-export)", parts.path):
        return parts, "公告签收报告"
    raise HTTPException(400, "此入口不支持后台导出")


def scope_key(db, user, target):
    parts, _ = target_kind(target)
    if getattr(user, "rotation_temporary", False):
        raise HTTPException(403, "轮岗专用账号不可导出")
    path = parts.path
    if path == "/api/statistics/export" and "DATA_EXPORT" not in user.permissions:
        raise HTTPException(403, "没有导出权限")
    if path == "/api/pr-rankings/export" and user.role.code not in {"GSM", "AM", "OM"}:
        raise HTTPException(403, "当前角色不能导出PR排名")
    if path == "/api/declaration-statistics/export" and not {"DECLARATION_STATS_VIEW", "DECLARATION_STATS_EXPORT"} <= user.permissions:
        raise HTTPException(403, "没有声明统计导出权限")
    visible = ()
    if path == "/api/hr-monthly-reports/export":
        from app.hr_monthly_report import require_report
        require_report(user)
    if path.startswith("/api/announcements/"):
        from app.routers.announcements import records, domain, get_announcement
        aid = int(path.split("/")[3])
        raw = parse_qs(parts.query).get("version", [None])[0]
        try:
            version = int(raw) if raw is not None else None
        except ValueError:
            raise HTTPException(422, "公告版本格式无效")
        if path.endswith("followup-export") and not domain.can_view_handover(db, user, get_announcement(db, aid)):
            raise HTTPException(403, "没有跟进报告导出权限")
        data = records(aid, version, db, user)
        # Recheck the *actual* visible receipt scope, including changed group
        # leadership and announcement grants, before serving a completed file.
        visible = tuple(sorted(str(row.get("employee_id", row.get("id"))) for row in data["items"]))
    return (user.id, user.account.id, user.employee.employee_no,
            user.employee.attraction_id, user.role.code, user.base_role.code,
            tuple(sorted(user.permissions)), visible)


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


def run_job(job, application, original_scope, headers, method):
    started = monotonic()
    job.status = "running"
    root = Path(job.directory.name)
    try:
        async def run():
            parts, _ = target_kind(job.target)
            source = open(root / "request", "rb")
            received = False
            status = 500
            response_headers = {}
            error_body = bytearray()
            size = 0
            async def receive():
                nonlocal received
                if not received:
                    chunk = source.read(256 * 1024)
                    if not chunk:
                        received = True
                    return {"type": "http.request", "body": chunk, "more_body": not received}
                # StreamingResponse under ASGI 2.4 doesn't need disconnect polling.
                await asyncio.Event().wait()
            with open(root / "result", "wb") as output:
                async def send(message):
                    nonlocal status, response_headers, size
                    if message["type"] == "http.response.start":
                        status = message["status"]
                        response_headers = {k.decode().lower(): v.decode("latin1") for k, v in message["headers"]}
                    elif message["type"] == "http.response.body":
                        chunk = message.get("body", b"")
                        size += len(chunk)
                        if size > MAX_OUTPUT:
                            raise ValueError("报告超过后台导出容量，请缩小导出范围后重试")
                        if status == 200:
                            output.write(chunk)
                        elif len(error_body) < 8192:
                            error_body.extend(chunk[:8192-len(error_body)])
                scope = {**original_scope, "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                         "method": method, "path": parts.path, "raw_path": parts.path.encode(),
                         "root_path": "", "query_string": parts.query.encode(), "headers": headers}
                try:
                    with SessionLocal() as db:
                        actor = current_user(Request(scope), db)
                        if scope_key(db, actor, job.target) != job.scope:
                            raise ValueError("账号权限或可见范围已变化，请重新导出")
                    await application(scope, receive, send)
                finally:
                    source.close()
            if status != 200:
                try:
                    detail = json.loads(error_body).get("detail")
                except (ValueError, UnicodeError):
                    detail = None
                raise ValueError(detail if isinstance(detail, str) else "导出未完成，请回原页面核对权限、筛选或预览后重试")
            content_type = response_headers.get("content-type", "").split(";")[0]
            if content_type not in {"application/pdf", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.openxmlformats-officedocument.presentationml.presentation"}:
                raise ValueError("服务器未返回有效报告")
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
        job.error = str(exc) if isinstance(exc, ValueError) else "报告制作中断，请回原页面重新导出"
        (root / "result").unlink(missing_ok=True)
        job.status = "failed"
    finally:
        (root / "request").unlink(missing_ok=True)
        headers.clear()  # Session cookie is never persisted and is dropped now.
        original_scope.clear()
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


@router.post("/export-tasks", status_code=202)
async def create_task(request: Request, target: str, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    parts, label = target_kind(target)
    scope = scope_key(db, user, target)
    method = "POST" if parts.path == "/api/hr-monthly-reports/export" else "GET"
    with _lock:
        expire()
        active = [j for j in _jobs.values() if j.status in {"receiving", "queued", "running"}]
        if len(active) >= 4 or sum(j.owner == user.id for j in active) >= 2:
            raise HTTPException(429, "导出队列已满，请等待当前任务完成后再试")
        if len(_jobs) >= 80:
            raise HTTPException(429, "导出缓存暂满，请稍后再试")
        directory = EXPORT_DIR / "background-tasks"
        directory.mkdir(parents=True, exist_ok=True)
        # Only our two named scratch files in resolved, direct child folders.
        # A crash cannot leave unbounded orphaned exports on disk indefinitely.
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
        job = Job(uuid4().hex, user.id, target, label, scope, tempfile.TemporaryDirectory(prefix="task-", dir=directory))
        _jobs[job.id] = job
    try:
        size = 0
        with open(Path(job.directory.name) / "request", "wb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > (MAX_BODY if method == "POST" else 0):
                    raise HTTPException(413, "导出素材过大，照片总计不得超过30MB")
                output.write(chunk)
        headers = [(k, v) for k, v in request.scope["headers"] if k.lower() in {b"cookie", b"host", b"origin", b"content-type", b"user-agent"}]
        headers.append((b"content-length", str(size).encode()))
        original = {k: request.scope[k] for k in ("scheme", "server", "client", "http_version") if k in request.scope}
        job.status = "queued"
        _pool.submit(run_job, job, request.app, original, headers, method)
        return job.payload()
    except BaseException:
        with _lock:
            _jobs.pop(job.id, None)
            job.directory.cleanup()
        raise


@router.get("/export-tasks")
def list_tasks(user: V2User = Depends(current_user)):
    with _lock:
        expire()
        return {"items": [j.payload() for j in reversed(list(_jobs.values())) if j.owner == user.id]}


@router.get("/export-tasks/{key}/file")
def download_task(key: str, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    job = owned(key, user)
    if scope_key(db, user, job.target) != job.scope:
        raise HTTPException(403, "账号权限或可见范围已变化，请重新导出")
    if job.status != "completed":
        raise HTTPException(409, "报告尚未制作完成")
    return FileResponse(Path(job.directory.name) / "result", headers={**job.headers, "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
