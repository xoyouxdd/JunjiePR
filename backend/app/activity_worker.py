"""Recoverable periodic draw/auction settlement, safe across processes."""
import logging
from sqlalchemy import text
from app.background_worker import BackgroundWorker
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


def _run(stop):
    while not stop.wait(5):
        try:
            process_due()
        except Exception:
            logging.getLogger(__name__).exception('活动结算扫描失败，将重试')


_worker = BackgroundWorker('activity-settlement', _run, join_timeout=2, counted=True)
start_worker = _worker.start
stop_worker = _worker.stop
