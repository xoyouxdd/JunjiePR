from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from time import monotonic

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.v2_crypto import token_hash
from app.v2_database import get_db
from app.v2_models import Employee, Role, UserAccount, UserSession
from app.v2_services import process_role_expirations, role_at, role_permissions


ROLE_EXPIRATION_CHECK_INTERVAL_SECONDS = 300
SESSION_TOUCH_INTERVAL = timedelta(minutes=5)
_role_expiration_check_lock = Lock()
_last_role_expiration_check = monotonic()


def maybe_process_role_expirations(db: Session) -> None:
    global _last_role_expiration_check
    now = monotonic()
    if now - _last_role_expiration_check < ROLE_EXPIRATION_CHECK_INTERVAL_SECONDS:
        return
    with _role_expiration_check_lock:
        now = monotonic()
        if now - _last_role_expiration_check < ROLE_EXPIRATION_CHECK_INTERVAL_SECONDS:
            return
        process_role_expirations(db)
        _last_role_expiration_check = now


@dataclass
class V2User:
    employee: Employee
    account: UserAccount
    role: Role
    permissions: set[str]

    @property
    def id(self):
        return self.employee.id

    @property
    def name(self):
        return self.employee.name


def current_user(request: Request, db: Session = Depends(get_db)) -> V2User:
    raw_token = request.cookies.get("rc_v2_session")
    if not raw_token:
        raise HTTPException(401, "请先登录")
    session = db.query(UserSession).filter(UserSession.token_hash == token_hash(raw_token)).first()
    if not session or session.expires_at <= datetime.now():
        raise HTTPException(401, "登录已过期，请重新登录")
    account = db.get(UserAccount, session.account_id)
    if not account or not account.enabled:
        raise HTTPException(401, "账号不可用")
    employee = db.get(Employee, account.employee_id)
    if not employee or not employee.is_active:
        raise HTTPException(401, "员工账号已停用")
    maybe_process_role_expirations(db)
    role = role_at(db, employee.id)
    if not role:
        raise HTTPException(403, "当前未配置有效角色")
    if account.must_change_password and request.url.path not in {"/api/me", "/api/password", "/api/logout"}:
        raise HTTPException(
            status_code=403,
            detail={"code": "PASSWORD_CHANGE_REQUIRED", "message": "请先修改密码后再使用系统功能"},
        )
    now = datetime.now()
    if not session.last_seen_at or session.last_seen_at <= now - SESSION_TOUCH_INTERVAL:
        session.last_seen_at = now
        db.commit()
    return V2User(employee, account, role, role_permissions(db, role.id))


def require_permissions(*permission_codes: str):
    def dependency(user: V2User = Depends(current_user)) -> V2User:
        if not any(code in user.permissions for code in permission_codes):
            raise HTTPException(403, "没有权限执行该操作")
        return user

    return dependency


def require_roles(*role_codes: str):
    def dependency(user: V2User = Depends(current_user)) -> V2User:
        if user.role.code not in role_codes:
            raise HTTPException(403, "没有权限执行该操作")
        return user

    return dependency
