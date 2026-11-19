"""One scheduler for lightweight maintenance and active material leases."""

import logging
from time import monotonic

from app.background_worker import BackgroundWorker
from app.database.connection import SessionLocal


logger = logging.getLogger(__name__)


def _expire_internship_accounts():
    from app.data_cache import invalidate_data_caches
    from app.services.employee_types import expire_accounts

    with SessionLocal() as db:
        if expire_accounts(db):
            db.commit()
            invalidate_data_caches()


def _deliver_announcements():
    from app.announcements import lock, sync_current

    with SessionLocal() as db:
        lock(db)
        sync_current(db)
        db.commit()


def _run_periodic(stop, tasks, clock=monotonic):
    started = clock()
    deadlines = [started + delay for _, _, delay in tasks]
    while not stop.wait(max(0, min(deadlines) - clock())):
        for index, (run, interval, _) in enumerate(tasks):
            if stop.is_set():
                return
            if clock() < deadlines[index]:
                continue
            try:
                run()
            except Exception:
                logger.exception("后台周期任务 %s 失败，将在下一周期重试", run.__name__)
            deadlines[index] = clock() + interval


def _run(stop):
    from app.activity_worker import process_due
    from app.deduction_materials import refresh_material_leases

    # Period and initial delay match the original independent workers.
    _run_periodic(stop, (
        (refresh_material_leases, 30, 30),
        (_expire_internship_accounts, 60, 0),
        (process_due, 5, 5),
        (_deliver_announcements, 60, 60),
    ))


_worker = BackgroundWorker('periodic-maintenance', _run, join_timeout=5, counted=True)
start_worker = _worker.start
stop_worker = _worker.stop
