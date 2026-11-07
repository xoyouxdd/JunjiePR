"""Recoverable periodic draw/auction settlement, safe across processes."""
import logging
from threading import Event, Lock, Thread
from sqlalchemy import text
from app.database.connection import SessionLocal
from app.activities_models import Activity, ActivityLot
from app.services.activities import now, settle_lot


def process_due():
    with SessionLocal() as db:
        ids = [row[0] for row in db.query(ActivityLot.id).join(Activity, Activity.id == ActivityLot.activity_id).filter(
            ActivityLot.state == 'open', Activity.state == 'published', ActivityLot.ends_at <= now())]
    for lot_id in ids:
        with SessionLocal() as db:
            try:
                db.execute(text('BEGIN IMMEDIATE'))
                lot = db.get(ActivityLot, lot_id)
                settle_lot(db, lot, db.get(Activity, lot.activity_id))
                db.commit()
            except Exception:
                db.rollback()
                logging.getLogger(__name__).exception('奖池/拍品%s结算失败，将重试', lot_id)


_lock, _stop, _thread, _users = Lock(), Event(), None, 0


def start_worker():
    global _thread, _stop, _users
    with _lock:
        _users += 1
        if _thread and _thread.is_alive():
            return
        _stop = Event()
        stop = _stop
        def run():
            while not stop.wait(5):
                try:
                    process_due()
                except Exception:
                    logging.getLogger(__name__).exception('活动结算扫描失败，将重试')
        _thread = Thread(target=run, name='activity-settlement', daemon=True)
        _thread.start()


def stop_worker():
    global _users, _thread
    with _lock:
        _users = max(0, _users - 1)
        if not _users:
            _stop.set()
            if _thread:
                _thread.join(timeout=2)
            _thread = None
