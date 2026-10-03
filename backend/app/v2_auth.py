from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import Lock
from time import monotonic

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.v2_crypto import token_hash
from app.v2_database import get_db
from app.v2_models import Employee, Role, UserAccount, UserSession
from app.v2_services import base_role_at, duties_at, process_role_expirations, role_permissions


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
    """`role` is the role in use (an active duty, else the base identity).

    `base_role` decides self-registration and the scoring category;
    permissions are the union of the base identity and every active duty.
    """

    employee: Employee
    account: UserAccount
    role: Role
    permissions: set[str]
    base_role: Role | None = None
    duty_roles: list[Role] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.base_role is None:
            self.base_role = self.role

    @property
    def id(self):
        return self.employee.id

    @property
    def name(self):
        return self.employee.name

    @property
    def role_codes(self) -> set[str]:
        return {self.base_role.code, *(role.code for role in self.duty_roles)}

    def has_role(self, *codes: str) -> bool:
        return bool(self.role_codes.intersection(codes))

    @property
    def display_role_name(self) -> str:
        if self.duty_roles and self.base_role.code not in {role.code for role in self.duty_roles}:
            return f"{self.base_role.name} · 代理{self.duty_roles[0].name}"
        return self.role.name


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
    base_role = base_role_at(db, employee.id)
    duty_roles = duties_at(db, employee.id)
    role = duty_roles[0] if duty_roles else base_role
    if not role or not base_role:
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
    permissions = set(role_permissions(db, base_role.id))
    for duty in duty_roles:
        permissions |= role_permissions(db, duty.id)
    return V2User(employee, account, role, permissions, base_role, duty_roles)


def require_permissions(*permission_codes: str):
    def dependency(user: V2User = Depends(current_user)) -> V2User:
        if not any(code in user.permissions for code in permission_codes):
            raise HTTPException(403, "没有权限执行该操作")
        return user

    return dependency


def require_roles(*role_codes: str):
    def dependency(user: V2User = Depends(current_user)) -> V2User:
        if not user.has_role(*role_codes):
            raise HTTPException(403, "没有权限执行该操作")
        return user

    return dependency
