"""轮岗的身份与权限：主管（TA主管及以上）、CM/TR 本人、休息室大屏账号。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.rotation.models import RotationScreenAccount, RotationScreenSession
from app.security import request_is_https
from app.v2_auth import V2User, current_user
from app.v2_crypto import hash_password, new_session_token, token_hash, verify_password
from app.v2_database import get_db
from app.v2_models import Attraction
from app.v2_services import managed_attraction_ids, write_audit


# 目前只在热力追踪使用。
ROTATION_ATTRACTION_NAME = "热力追踪"

SCREEN_COOKIE = "rc_rotation_screen"
SCREEN_PAGE_PATH = "/rotation/screen"
SCREEN_SESSION_DAYS = 30
SCREEN_SESSION_TOUCH_INTERVAL = timedelta(minutes=5)
SCREEN_LOCK_ATTEMPTS = 5
SCREEN_LOCK_MINUTES = 15
SCREEN_PASSWORD_MIN = 4
SCREEN_PASSWORD_MAX = 64

# 休息室大屏账号：初始密码由主管在轮岗设置中重置。
DEFAULT_SCREEN_ACCOUNT = "6666666"
DEFAULT_SCREEN_PASSWORD = "1243"


def rotation_attraction_id(db: Session) -> int:
    row = db.query(Attraction.id).filter(Attraction.name == ROTATION_ATTRACTION_NAME).first()
    if not row:
        raise HTTPException(500, f"未找到景点圈：{ROTATION_ATTRACTION_NAME}")
    return row[0]


def can_manage(db: Session, user: V2User, attraction_id: int) -> bool:
    """TA主管及以上，且本人属于该景点圈或管理范围包含该景点圈；AM、OM 管理全部景点圈。"""
    if "ROTATION_MANAGE" not in user.permissions:
        return False
    if user.has_role("AM", "OM") or user.employee.attraction_id == attraction_id:
        return True
    return attraction_id in managed_attraction_ids(db, user.id)


def can_view_self(user: V2User) -> bool:
    return "ROTATION_SELF" in user.permissions


def require_manager(db: Session = Depends(get_db), user: V2User = Depends(current_user)) -> V2User:
    if not can_manage(db, user, rotation_attraction_id(db)):
        raise HTTPException(403, "没有轮岗管理权限")
    return user


def require_self_viewer(user: V2User = Depends(current_user)) -> V2User:
    if not can_view_self(user):
        raise HTTPException(403, "没有查看个人轮岗的权限")
    return user


# ---------------------------------------------------------------- 休息室大屏账号

@dataclass
class ScreenUser:
    account: RotationScreenAccount

    @property
    def name(self) -> str:
        return self.account.name

    @property
    def attraction_id(self) -> int:
        return self.account.attraction_id


def ensure_screen_account(db: Session) -> None:
    """每次启动保证热力追踪有一个大屏账号；已存在时不动密码。"""
    if db.query(RotationScreenAccount).filter(RotationScreenAccount.login_account == DEFAULT_SCREEN_ACCOUNT).first():
        return
    attraction = db.query(Attraction).filter(Attraction.name == ROTATION_ATTRACTION_NAME).first()
    if not attraction:
        return
    db.add(
        RotationScreenAccount(
            login_account=DEFAULT_SCREEN_ACCOUNT,
            name=f"{ROTATION_ATTRACTION_NAME}休息室大屏",
            attraction_id=attraction.id,
            password_hash=hash_password(DEFAULT_SCREEN_PASSWORD),
            enabled=True,
        )
    )
    db.commit()


def find_screen_account(db: Session, login_name: str) -> RotationScreenAccount | None:
    return db.query(RotationScreenAccount).filter(RotationScreenAccount.login_account == login_name).first()


def create_screen_session(db: Session, account: RotationScreenAccount, raw_token: str) -> None:
    db.add(
        RotationScreenSession(
            account_id=account.id,
            token_hash=token_hash(raw_token),
            expires_at=datetime.now() + timedelta(days=SCREEN_SESSION_DAYS),
        )
    )


def current_screen(request: Request, db: Session = Depends(get_db)) -> ScreenUser:
    raw_token = request.cookies.get(SCREEN_COOKIE)
    if not raw_token:
        raise HTTPException(401, "请先用大屏账号登录")
    session = db.query(RotationScreenSession).filter(RotationScreenSession.token_hash == token_hash(raw_token)).first()
    if not session or session.expires_at <= datetime.now():
        raise HTTPException(401, "大屏登录已过期，请重新登录")
    account = db.get(RotationScreenAccount, session.account_id)
    if not account or not account.enabled:
        raise HTTPException(401, "大屏账号不可用")
    now = datetime.now()
    if not session.last_seen_at or session.last_seen_at <= now - SCREEN_SESSION_TOUCH_INTERVAL:
        session.last_seen_at = now
        db.commit()
    return ScreenUser(account)


def screen_login(db: Session, request: Request, response: Response, account: RotationScreenAccount, password: str) -> dict:
    """员工登录页输入大屏账号时走这里；成功后只发大屏会话，不发员工会话。"""
    if not account.enabled:
        raise HTTPException(401, "账号或密码/PIN不正确")
    if account.locked_until and account.locked_until > datetime.now():
        raise HTTPException(423, "登录失败次数过多，请稍后再试")
    if not verify_password(password, account.password_hash):
        account.failed_attempts += 1
        if account.failed_attempts >= SCREEN_LOCK_ATTEMPTS:
            account.locked_until = datetime.now() + timedelta(minutes=SCREEN_LOCK_MINUTES)
            account.failed_attempts = 0
        db.commit()
        raise HTTPException(401, "账号或密码/PIN不正确")
    account.failed_attempts = 0
    account.locked_until = None
    account.last_login_at = datetime.now()
    raw_token = new_session_token()
    create_screen_session(db, account, raw_token)
    write_audit(db, None, "大屏账号登录", "rotation_screen", account.id, after={"account": account.login_account}, ip_address=request.client.host if request.client else None)
    db.commit()
    response.set_cookie(
        SCREEN_COOKIE,
        raw_token,
        httponly=True,
        samesite="lax",
        secure=request_is_https(request),
        max_age=SCREEN_SESSION_DAYS * 24 * 3600,
    )
    return {"ok": True, "role": "休息室大屏", "redirect": SCREEN_PAGE_PATH}


def validate_screen_password(password: str) -> str:
    value = str(password or "")
    if not (SCREEN_PASSWORD_MIN <= len(value) <= SCREEN_PASSWORD_MAX):
        raise HTTPException(400, f"大屏密码长度需为{SCREEN_PASSWORD_MIN}到{SCREEN_PASSWORD_MAX}位")
    return value
