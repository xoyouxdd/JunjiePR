"""轮岗的身份与权限。

能操作轮岗的有三类：
- PR 员工账号：TA主管、主管、TA GSM、GSM 有 ROTATION_MANAGE；CM/TR 有 ROTATION_SELF，只看本人；
- 轮岗专用账号（rotation_accounts）：大屏只能点到达和出发；轮岗主管、轮岗经理可做全部轮岗操作。
  专用账号不对应员工，只能访问轮岗接口。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.rotation.models import RotationAccount, RotationAccountSession
from app.security import request_is_https
from app.v2_auth import V2User, current_user
from app.v2_crypto import hash_password, new_session_token, token_hash, verify_password
from app.v2_database import get_db
from app.v2_models import Attraction, Employee
from app.v2_services import write_audit


# 目前只在热力追踪使用。
ROTATION_ATTRACTION_NAME = "热力追踪"

ACCOUNT_COOKIE = "rc_rotation_account"
ACCOUNT_SESSION_DAYS = 30
ACCOUNT_SESSION_TOUCH_INTERVAL = timedelta(minutes=5)
ACCOUNT_LOCK_ATTEMPTS = 5
ACCOUNT_LOCK_MINUTES = 15
ACCOUNT_PASSWORD_MIN = 4
ACCOUNT_PASSWORD_MAX = 64

SCREEN_PAGE_PATH = "/rotation/screen"
BOARD_PAGE_PATH = "/rotation"

KIND_SCREEN = "screen"
KIND_SUPERVISOR = "supervisor"
KIND_MANAGER = "manager"
MANAGE_KINDS = {KIND_SUPERVISOR, KIND_MANAGER}
KIND_LABELS = {KIND_SCREEN: "休息室大屏", KIND_SUPERVISOR: "轮岗主管", KIND_MANAGER: "轮岗经理"}

# 启动时保证存在；已存在时不改密码。初始密码可由主管在轮岗设置里重置。
DEFAULT_ACCOUNTS = (
    ("6666666", KIND_SCREEN, "1243"),
    ("7777777", KIND_SUPERVISOR, "7777"),
    ("8888888", KIND_MANAGER, "8888"),
)


def rotation_attraction_id(db: Session) -> int:
    row = db.query(Attraction.id).filter(Attraction.name == ROTATION_ATTRACTION_NAME).first()
    if not row:
        raise HTTPException(500, f"未找到景点圈：{ROTATION_ATTRACTION_NAME}")
    return row[0]


def can_manage(user: V2User) -> bool:
    return "ROTATION_MANAGE" in user.permissions


def can_view_self(user: V2User) -> bool:
    return "ROTATION_SELF" in user.permissions


@dataclass
class RotationActor:
    """谁在操作轮岗：员工账号或轮岗专用账号。"""

    kind: str  # employee / screen / supervisor / manager
    name: str
    employee: Employee | None = None
    account: RotationAccount | None = None

    @property
    def actor_id(self) -> int:
        return self.employee.id if self.employee else self.account.id

    @property
    def actor_type(self) -> str:
        return "employee" if self.employee else self.kind


# ---------------------------------------------------------------- 专用账号会话

def ensure_rotation_accounts(db: Session) -> None:
    attraction = db.query(Attraction).filter(Attraction.name == ROTATION_ATTRACTION_NAME).first()
    if not attraction:
        return
    for login_account, kind, password in DEFAULT_ACCOUNTS:
        if db.query(RotationAccount).filter(RotationAccount.login_account == login_account).first():
            continue
        db.add(
            RotationAccount(
                login_account=login_account,
                kind=kind,
                name=f"{ROTATION_ATTRACTION_NAME}{KIND_LABELS[kind]}",
                attraction_id=attraction.id,
                password_hash=hash_password(password),
                enabled=True,
            )
        )
    db.commit()


def find_rotation_account(db: Session, login_name: str) -> RotationAccount | None:
    return db.query(RotationAccount).filter(RotationAccount.login_account == login_name).first()


def session_account(request: Request, db: Session) -> RotationAccount | None:
    """当前浏览器的轮岗专用账号；没有或已失效时返回 None。"""
    raw_token = request.cookies.get(ACCOUNT_COOKIE)
    if not raw_token:
        return None
    session = db.query(RotationAccountSession).filter(RotationAccountSession.token_hash == token_hash(raw_token)).first()
    if not session or session.expires_at <= datetime.now():
        return None
    account = db.get(RotationAccount, session.account_id)
    if not account or not account.enabled:
        return None
    now = datetime.now()
    if not session.last_seen_at or session.last_seen_at <= now - ACCOUNT_SESSION_TOUCH_INTERVAL:
        session.last_seen_at = now
        db.commit()
    return account


def rotation_login(db: Session, request: Request, response: Response, account: RotationAccount, password: str) -> dict:
    """员工登录页输入轮岗专用账号时走这里；只发轮岗会话，不发员工会话。"""
    if not account.enabled:
        raise HTTPException(401, "账号或密码/PIN不正确")
    if account.locked_until and account.locked_until > datetime.now():
        raise HTTPException(423, "登录失败次数过多，请稍后再试")
    if not verify_password(password, account.password_hash):
        account.failed_attempts += 1
        if account.failed_attempts >= ACCOUNT_LOCK_ATTEMPTS:
            account.locked_until = datetime.now() + timedelta(minutes=ACCOUNT_LOCK_MINUTES)
            account.failed_attempts = 0
        db.commit()
        raise HTTPException(401, "账号或密码/PIN不正确")
    account.failed_attempts = 0
    account.locked_until = None
    account.last_login_at = datetime.now()
    raw_token = new_session_token()
    db.add(
        RotationAccountSession(
            account_id=account.id,
            token_hash=token_hash(raw_token),
            expires_at=datetime.now() + timedelta(days=ACCOUNT_SESSION_DAYS),
        )
    )
    write_audit(db, None, f"{KIND_LABELS[account.kind]}账号登录", "rotation_account", account.id, after={"account": account.login_account}, ip_address=request.client.host if request.client else None)
    db.commit()
    response.set_cookie(
        ACCOUNT_COOKIE,
        raw_token,
        httponly=True,
        samesite="lax",
        secure=request_is_https(request),
        max_age=ACCOUNT_SESSION_DAYS * 24 * 3600,
    )
    return {"ok": True, "role": KIND_LABELS[account.kind], "redirect": SCREEN_PAGE_PATH if account.kind == KIND_SCREEN else BOARD_PAGE_PATH}


def validate_account_password(password: str) -> str:
    value = str(password or "")
    if not (ACCOUNT_PASSWORD_MIN <= len(value) <= ACCOUNT_PASSWORD_MAX):
        raise HTTPException(400, f"密码长度需为{ACCOUNT_PASSWORD_MIN}到{ACCOUNT_PASSWORD_MAX}位")
    return value


# ---------------------------------------------------------------- 依赖

def require_screen(request: Request, db: Session = Depends(get_db)) -> RotationActor:
    account = session_account(request, db)
    if not account or account.kind != KIND_SCREEN:
        raise HTTPException(401, "请先用大屏账号登录")
    return RotationActor(KIND_SCREEN, account.name, account=account)


def require_manager(request: Request, db: Session = Depends(get_db)) -> RotationActor:
    """轮岗主管/经理账号，或有 ROTATION_MANAGE 的员工。"""
    account = session_account(request, db)
    if account and account.kind in MANAGE_KINDS:
        return RotationActor(account.kind, account.name, account=account)
    user = current_user(request, db)
    if not can_manage(user):
        raise HTTPException(403, "没有轮岗管理权限")
    return RotationActor("employee", user.name, employee=user.employee)


def require_self_viewer(user: V2User = Depends(current_user)) -> V2User:
    if not can_view_self(user):
        raise HTTPException(403, "没有查看个人轮岗的权限")
    return user
