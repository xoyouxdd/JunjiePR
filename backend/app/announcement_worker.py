"""Periodic catch-up delivery; request-side reconciliation remains authoritative."""
import logging
from app.background_worker import BackgroundWorker
from app.database.connection import SessionLocal

def _run(stop):
    from app.announcements import lock, sync_current
    while not stop.wait(60):
        with SessionLocal() as db:
            try:
                lock(db)
                sync_current(db)
                db.commit()
            except Exception:
                db.rollback()
                logging.getLogger(__name__).exception("公告补收任务失败，将在下一周期重试")


_worker = BackgroundWorker("announcement-delivery", _run, join_timeout=3)
start_worker = _worker.start
stop_worker = _worker.stop
