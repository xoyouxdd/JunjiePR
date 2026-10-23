"""One durable queue consumer; codec work runs in bounded external processes."""
from datetime import datetime, timedelta
import json
import logging
import os
import re
import threading
from sqlalchemy import text

from app.v2_database import SessionLocal
from app.video_models import LearningVideo, VideoJob, VideoPlayback, VideoUpload
from app import video_media as media

_stop = threading.Event()
_guard = threading.Lock()
_thread = None


def enqueue(db, video_id, key, kind, payload=None):
    existing = db.query(VideoJob).filter_by(key=key).first()
    if existing:
        return existing
    row = VideoJob(video_id=video_id, key=key, kind=kind, payload=json.dumps(payload or {}))
    db.add(row); db.flush()
    return row


def run_one(stop=None):
    with SessionLocal() as db:
        # An interrupted job must not remain permanently processing. Never
        # concurrently reclaim a live 30-minute codec process.
        for stale in db.query(VideoJob).filter(VideoJob.status == "processing", VideoJob.updated_at < datetime.now()-timedelta(hours=2)).all():
            stale.status = 'failed'; stale.error = '处理任务中断，请重新处理'
            if stale.kind == 'source':
                video = db.get(LearningVideo, stale.video_id)
                if video and video.status not in ('deleted','withdrawn'):
                    video.status = 'failed'; video.error = stale.error
        db.commit()
        row = db.query(VideoJob).filter_by(status="queued").order_by(VideoJob.id).first()
        if not row:
            return False
        changed = db.query(VideoJob).filter_by(id=row.id, status="queued").update({"status": "processing", "updated_at": datetime.now()})
        db.commit()
        if not changed:
            return False
        video = db.get(LearningVideo, row.video_id)
        try:
            if row.kind == 'delete':
                media.erase_video_files(video.id)
            elif row.kind == 'playback':
                row.status='cancelled';db.commit();return True
            elif video.status == 'deleted':
                row.status='cancelled';db.commit();return True
            elif row.kind in ('source','shared'):
                info, profiles, second = media.process_source(video, stop)
                video.duration = info['duration']; video.profiles = json.dumps(profiles)
                video.cover_second = second
                if row.kind == 'source':video.status = 'ready'
                video.error = ''
            elif row.kind == "cover":
                media.build_cover(video.id, video.cover_title, video.cover_second, stop)
            else:
                raise RuntimeError('未知视频任务类型')
            row.status = "ready"; row.error = ""
        except Exception as exc:
            logging.getLogger(__name__).exception("Video job %s failed", row.id)
            row.status = "failed"
            row.error = str(exc) if isinstance(exc, RuntimeError) else "视频处理失败，请重试或联系管理员"
            if row.kind == "source":
                video.status = "failed"; video.error = row.error
        values={k:getattr(video,k) for k in ('duration','profiles','cover_second','status','error')}
        job_status,job_error=row.status,row.error
        db.rollback();db.execute(text('BEGIN IMMEDIATE'))
        db.refresh(video);db.refresh(row)
        if video.status=='deleted' and row.kind!='delete':
            row.status='cancelled';row.error='视频已删除'
            deletion=enqueue(db,video.id,f'delete:{video.id}','delete')
            if deletion.status in ('ready','failed','cancelled'):deletion.status='queued'
        else:
            if row.kind in ('source','shared'):
                # A concurrent withdrawal must never be reversed by a transcode.
                for k in ('duration','profiles','cover_second','error'):setattr(video,k,values[k])
                if row.kind=='source' and video.status not in ('withdrawn','deleted'):video.status=values['status']
            row.status=job_status;row.error=job_error
        row.updated_at=datetime.now();db.commit();return True


def cleanup():
    cutoff = datetime.now()-timedelta(days=7)
    with SessionLocal() as db:
        for job in db.query(VideoJob).filter_by(kind='delete',status='failed'):
            job.status='queued';job.updated_at=datetime.now()
        for row in db.query(VideoJob).filter(VideoJob.kind == "playback", VideoJob.status.in_(["ready", "failed", "cancelled"]), VideoJob.updated_at < cutoff).all():
            key = row.key.removeprefix("playback:")
            path = media.playback_path(key)
            # Only delete this queue's SHA-256 directory under its own root.
            parent = path.parent
            cache_root = (media.VIDEO_DIR / 'playbacks').resolve()
            if len(key) == 64 and all(c in "0123456789abcdef" for c in key) and parent.is_dir():
                if not parent.resolve().is_relative_to(cache_root) or parent.is_symlink():
                    raise RuntimeError('Unsafe video cache directory')
                for filename in ('video.mp4', 'video.new.mp4', 'watermark.txt', 'font.ttf'):
                    (parent / filename).unlink(missing_ok=True)
                for temporary in parent.glob('video.*.new.mp4'):
                    if re.fullmatch(r'video\.[0-9a-f]{32}\.new\.mp4', temporary.name):
                        temporary.unlink()
                parent.rmdir()
            db.delete(row)
        for row in db.query(VideoUpload).filter(VideoUpload.expires_at < datetime.now()).all():
            path = media.VIDEO_DIR / "uploads" / (row.id + ".part")
            path.unlink(missing_ok=True); db.delete(row)
        db.query(VideoPlayback).filter(VideoPlayback.expires_at < datetime.now()-timedelta(days=1)).delete()
        db.commit()
    # Failed/interrupted codec attempts never share a temporary filename.
    # Only old, precisely named derivative files are eligible for removal.
    source_root = media.VIDEO_DIR / 'videos'
    if source_root.is_dir():
        for directory in source_root.iterdir():
            if not directory.is_dir() or not directory.name.isdigit() or directory.is_symlink():
                continue
            for temporary in directory.iterdir():
                if re.fullmatch(r'(?:master-(?:720|1080)\.[0-9a-f]{32}\.new\.mp4|frame-[0-9a-f]{32}\.jpg)', temporary.name) and datetime.fromtimestamp(temporary.stat().st_mtime) < cutoff:
                    temporary.unlink()


def start_worker():
    global _thread
    if os.getenv("RECOGNITION_VIDEO_WORKER", "1") == "0":
        return
    with _guard:
        if _thread and _thread.is_alive():
            return
        _stop.clear()
        # The app uses one process/worker. Recover jobs interrupted by the
        # previous application instance immediately, not two hours later.
        with SessionLocal() as db:
            db.query(VideoJob).filter_by(status='processing').update({'status':'queued'})
            db.commit()
        def work():
            ticks = 0
            while not _stop.is_set():
                try:
                    busy = run_one(_stop)
                    ticks += 1
                    if ticks % 120 == 1:
                        cleanup()
                except Exception:
                    logging.getLogger(__name__).exception("Video queue failure")
                    busy = False
                _stop.wait(.5 if busy else 3)
        _thread = threading.Thread(target=work, daemon=True, name="video-learning-queue")
        _thread.start()


def stop_worker():
    with _guard:
        _stop.set()
        if _thread:
            _thread.join(timeout=4)
