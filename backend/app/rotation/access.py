"""Formal rotation identities and rest-room screen device sessions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.rotation.models import RotationAccount, RotationAccountSession
from app.security import request_is_https
from app.v2_crypto import hash_password, new_session_token, token_hash, verify_password
from app.database.connection import get_db
from app.v2_models import Attraction, Employee
from app.services.audit import write_audit


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
KIND_SCREEN = "screen"
SCREEN_LABEL = "休息室大屏"
DEFAULT_SCREEN_ACCOUNT = "6666666"
DEFAULT_SCREEN_PASSWORD = "1243"


def rotation_attraction_id(db: Session) -> int:
    row = db.query(Attraction.id).filter(Attraction.name == ROTATION_ATTRACTION_NAME).first()
    if not row:
        raise HTTPException(500, f"未找到景点圈：{ROTATION_ATTRACTION_NAME}")
    return row[0]


@dataclass
class RotationActor:
    """正式轮岗操作人，可以是员工或大屏设备。"""

    kind: str
    name: str
    account: RotationAccount | None = None
    member_employee_no: str | None = None
    entered_by: Employee | None = None
    temporary: bool = False
    formal: bool = False

    @property
    def actor_id(self) -> int | None:
        if self.formal and self.entered_by:
            return self.entered_by.id
        return self.account.id if self.account else None


# ---------------------------------------------------------------- 专用账号与会话

def ensure_rotation_accounts(db: Session) -> None:
    attraction = db.query(Attraction).filter(Attraction.name == ROTATION_ATTRACTION_NAME).first()
    if not attraction:
        return
    if db.query(RotationAccount.id).filter_by(login_account=DEFAULT_SCREEN_ACCOUNT).first():
        return
    db.add(RotationAccount(
        login_account=DEFAULT_SCREEN_ACCOUNT,
        kind=KIND_SCREEN,
        name=f"{ROTATION_ATTRACTION_NAME}{SCREEN_LABEL}",
        attraction_id=attraction.id,
        password_hash=hash_password(DEFAULT_SCREEN_PASSWORD),
        enabled=True,
    ))
    db.commit()


def find_screen_account(db: Session, login_name: str) -> RotationAccount | None:
    return db.query(RotationAccount).filter_by(login_account=login_name, kind=KIND_SCREEN).first()


def _session_row(request: Request, db: Session) -> RotationAccountSession | None:
    raw_token = request.cookies.get(ACCOUNT_COOKIE)
    if not raw_token:
        return None
    session = db.query(RotationAccountSession).filter(RotationAccountSession.token_hash == token_hash(raw_token)).first()
    if not session or session.expires_at <= datetime.now():
        return None
    now = datetime.now()
    if not session.last_seen_at or session.last_seen_at <= now - ACCOUNT_SESSION_TOUCH_INTERVAL:
        session.last_seen_at = now
        db.commit()
    return session


def start_session(
    db: Session,
    request: Request,
    response: Response,
    account: RotationAccount,
) -> None:
    """替换当前浏览器的轮岗会话。"""
    old_token = request.cookies.get(ACCOUNT_COOKIE)
    if old_token:
        db.query(RotationAccountSession).filter(RotationAccountSession.token_hash == token_hash(old_token)).delete(synchronize_session=False)
    raw_token = new_session_token()
    db.add(
        RotationAccountSession(
            account_id=account.id,
            token_hash=token_hash(raw_token),
            expires_at=datetime.now() + timedelta(days=ACCOUNT_SESSION_DAYS),
        )
    )
    response.set_cookie(
        ACCOUNT_COOKIE,
        raw_token,
        httponly=True,
        samesite="lax",
        secure=request_is_https(request),
        max_age=ACCOUNT_SESSION_DAYS * 24 * 3600,
    )


def rotation_login(db: Session, request: Request, response: Response, account: RotationAccount, password: str) -> dict:
    """登录页直接输入专用账号和密码（休息室大屏设备用）；只发轮岗会话，不发员工会话。"""
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
    # A display must not inherit the previous user's employee or temporary login.
    from app.v2_models import UserSession
    from app.rotation.temporary import TemporarySession, COOKIE as TEMP_COOKIE
    for cookie, model in [('rc_v2_session', UserSession), (TEMP_COOKIE, TemporarySession)]:
        raw = request.cookies.get(cookie)
        if raw:
            db.query(model).filter_by(token_hash=token_hash(raw)).delete(synchronize_session=False)
        response.delete_cookie(cookie, secure=request_is_https(request), httponly=True, samesite='lax')
    start_session(db, request, response, account=account)
    write_audit(db, None, f"{SCREEN_LABEL}账号登录", "rotation_account", account.id, after={"account": account.login_account}, ip_address=request.client.host if request.client else None)
    db.commit()
    return {"ok": True, "role": SCREEN_LABEL, "redirect": SCREEN_PAGE_PATH}


def validate_account_password(password: str) -> str:
    value = str(password or "")
    if not (ACCOUNT_PASSWORD_MIN <= len(value) <= ACCOUNT_PASSWORD_MAX):
        raise HTTPException(400, f"密码长度需为{ACCOUNT_PASSWORD_MIN}到{ACCOUNT_PASSWORD_MAX}位")
    return value


def require_screen_device(request: Request, db: Session = Depends(get_db)) -> RotationActor:
    session = _session_row(request, db)
    if not session:
        raise HTTPException(401, "请先登录休息室大屏")
    if session.entered_by_id is not None or session.member_employee_no:
        raise HTTPException(403, "请使用休息室大屏账号登录")
    account = db.get(RotationAccount, session.account_id) if session.account_id else None
    if not account or not account.enabled:
        raise HTTPException(401, "请先登录休息室大屏")
    if account.kind != KIND_SCREEN:
        raise HTTPException(403, "只有休息室大屏可以操作")
    db.info["rotation_formal"] = True
    return RotationActor(KIND_SCREEN, account.name, account=account, formal=True)
