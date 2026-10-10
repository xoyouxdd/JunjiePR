"""Periodic catch-up delivery; request-side reconciliation remains authoritative."""
import logging
import threading

from app.v2_database import SessionLocal

_stop = threading.Event()
_thread = None
_guard = threading.Lock()


def start_worker():
    global _thread
    with _guard:
        if _thread and _thread.is_alive():
            return
        _stop.clear()
        def run():
            from app.announcements import lock, sync_current
            while not _stop.wait(60):
                with SessionLocal() as db:
                    try:
                        lock(db)
                        sync_current(db)
                        db.commit()
                    except Exception:
                        db.rollback()
                        logging.getLogger(__name__).exception("公告补收任务失败，将在下一周期重试")
        _thread = threading.Thread(target=run, name="announcement-delivery", daemon=True)
        _thread.start()


def stop_worker():
    _stop.set()
    if _thread:
        _thread.join(timeout=3)
