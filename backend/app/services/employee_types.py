"""Optional identity markers, independent of roles, scoring and group membership."""
from datetime import datetime, timedelta, timezone
import logging
from threading import Event, Lock, Thread

from fastapi import HTTPException
from sqlalchemy import select

from app.date_utils import parse_iso_date
from app.v2_models import Employee, UserAccount, UserSession


def business_today():
    return datetime.now(timezone(timedelta(hours=8))).date()


def normalize(employee_type, ends_on):
    if employee_type is not None and not isinstance(employee_type, str):
        raise HTTPException(400, '员工Type必须为文字')
    kind = (employee_type or '').strip()
    if len(kind) > 80:
        raise HTTPException(400, '员工Type不能超过80个字符')
    if kind.lower() == 'intern':
        kind = 'Intern'
    end = str(ends_on or '').strip()
    if end:
        end = parse_iso_date(end, '实习结束日期').isoformat()
        if kind != 'Intern':
            raise HTTPException(400, '实习结束日期仅可用于Intern，请先核对Type')
    return kind or None, end or None


def is_due(employee_type, ends_on):
    return bool((employee_type or '').strip().lower() == 'intern' and ends_on and ends_on < business_today().isoformat())


def expire_accounts(db, employee_id=None):
    """Caller commits. CAS avoids duplicate audits and preserves manual-disable time."""
    from app.services.audit import write_audit
    due = select(Employee.id).where(Employee.employee_type == 'Intern',
        Employee.internship_ends_on < business_today().isoformat(), Employee.account_deleted_at.is_(None))
    query = db.query(UserAccount).filter(UserAccount.enabled.is_(True), UserAccount.employee_id.in_(due))
    if employee_id is not None:
        query = query.filter(UserAccount.employee_id == employee_id)
    count = 0
    for account in query.all():
        stamp = datetime.now()
        updated = db.query(UserAccount).filter(UserAccount.id == account.id, UserAccount.enabled.is_(True),
            UserAccount.employee_id.in_(due)).update({'enabled': False, 'disabled_at': stamp}, synchronize_session=False)
        if not updated:
            continue
        account.enabled = False
        account.disabled_at = stamp
        db.query(UserSession).filter(UserSession.account_id == account.id).delete(synchronize_session=False)
        employee = db.get(Employee, account.employee_id)
        write_audit(db, None, '实习到期自动停用账号', 'employee', employee.id,
                    after={'employee_type': employee.employee_type, 'internship_ends_on': employee.internship_ends_on,
                           'account_enabled': False, 'disabled_at': stamp.isoformat()},
                    reason='实习结束日期次日起停用；连续停用满72小时后按原规则删除')
        count += 1
    return count


def enforce_account_expiry(db, employee_id):
    if expire_accounts(db, employee_id):
        db.commit()
        from app.data_cache import invalidate_data_caches
        invalidate_data_caches()
        return True
    return False


_lock = Lock()
_stop = Event()
_thread = None
_users = 0


def start_worker():
    global _thread, _users, _stop
    with _lock:
        _users += 1
        if _thread and _thread.is_alive():
            return
        _stop = Event()
        stop = _stop
        def run():
            from app.database.connection import SessionLocal
            while not stop.is_set():
                try:
                    with SessionLocal() as db:
                        if expire_accounts(db):
                            db.commit()
                            from app.data_cache import invalidate_data_caches
                            invalidate_data_caches()
                except Exception:
                    logging.getLogger(__name__).exception('实习账号到期检查失败，将自动重试')
                stop.wait(60)
        _thread = Thread(target=run, name='internship-expiry', daemon=True)
        _thread.start()


def stop_worker():
    global _users, _thread
    with _lock:
        _users = max(0, _users - 1)
        if _users:
            return
        _stop.set()
        if _thread:
            _thread.join(timeout=5)
            _thread = None
