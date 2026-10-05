"""轮岗的身份与权限（测试阶段）。

轮岗目前是独立的测试环境，数据全部为模拟数据，不影响任何真实员工：
- PR 员工账号只决定能否打开「轮岗（测试）」入口：热力追踪的 TR 和 GSM；
- 进入后选择模拟账号操作：大屏（只能点到达和出发）、轮岗主管、轮岗经理，或名单中某个 CM/TR 的工号；
- 休息室大屏设备不登录员工账号，在登录页直接用大屏账号和密码登录。
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
from app.v2_services import managed_attraction_ids, write_audit


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
KIND_MEMBER = "member"
MANAGE_KINDS = {KIND_SUPERVISOR, KIND_MANAGER}
KIND_LABELS = {KIND_SCREEN: "休息室大屏", KIND_SUPERVISOR: "轮岗主管", KIND_MANAGER: "轮岗经理", KIND_MEMBER: "CM/TR"}

# 启动时保证存在；已存在时不改密码。初始密码可由轮岗主管在设置里重置。
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


def can_enter(db: Session, user: V2User) -> bool:
    """「轮岗（测试）」入口：热力追踪的 TR 和 GSM。"""
    attraction_id = rotation_attraction_id(db)
    if user.base_role.code == "TR":
        return user.employee.attraction_id == attraction_id
    if user.has_role("GSM"):
        return user.employee.attraction_id == attraction_id or attraction_id in managed_attraction_ids(db, user.id)
    return False


@dataclass
class RotationActor:
    """当前轮岗会话的身份。kind 为 screen / supervisor / manager / member。"""

    kind: str
    name: str
    account: RotationAccount | None = None
    member_employee_no: str | None = None
    entered_by: Employee | None = None

    @property
    def actor_id(self) -> int | None:
        return self.account.id if self.account else None


# ---------------------------------------------------------------- 专用账号与会话

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


def session_actor(request: Request, db: Session) -> RotationActor | None:
    """当前浏览器的轮岗身份；没有或已失效时返回 None。"""
    session = _session_row(request, db)
    if not session:
        return None
    entered_by = db.get(Employee, session.entered_by_id) if session.entered_by_id else None
    if session.member_employee_no:
        return RotationActor(KIND_MEMBER, session.member_employee_no, member_employee_no=session.member_employee_no, entered_by=entered_by)
    account = db.get(RotationAccount, session.account_id) if session.account_id else None
    if not account or not account.enabled:
        return None
    return RotationActor(account.kind, account.name, account=account, entered_by=entered_by)


def start_session(
    db: Session,
    request: Request,
    response: Response,
    *,
    account: RotationAccount | None = None,
    member_employee_no: str | None = None,
    entered_by: Employee | None = None,
) -> None:
    """替换当前浏览器的轮岗会话。"""
    old_token = request.cookies.get(ACCOUNT_COOKIE)
    if old_token:
        db.query(RotationAccountSession).filter(RotationAccountSession.token_hash == token_hash(old_token)).delete(synchronize_session=False)
    raw_token = new_session_token()
    db.add(
        RotationAccountSession(
            account_id=account.id if account else None,
            member_employee_no=member_employee_no,
            entered_by_id=entered_by.id if entered_by else None,
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


def landing_path(kind: str) -> str:
    return SCREEN_PAGE_PATH if kind == KIND_SCREEN else BOARD_PAGE_PATH


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
    start_session(db, request, response, account=account)
    write_audit(db, None, f"{KIND_LABELS[account.kind]}账号登录", "rotation_account", account.id, after={"account": account.login_account}, ip_address=request.client.host if request.client else None)
    db.commit()
    return {"ok": True, "role": KIND_LABELS[account.kind], "redirect": landing_path(account.kind)}


def validate_account_password(password: str) -> str:
    value = str(password or "")
    if not (ACCOUNT_PASSWORD_MIN <= len(value) <= ACCOUNT_PASSWORD_MAX):
        raise HTTPException(400, f"密码长度需为{ACCOUNT_PASSWORD_MIN}到{ACCOUNT_PASSWORD_MAX}位")
    return value


# ---------------------------------------------------------------- 依赖

def require_entry(db: Session = Depends(get_db), user: V2User = Depends(current_user)) -> V2User:
    if not can_enter(db, user):
        raise HTTPException(403, "没有轮岗测试入口权限")
    return user


def require_actor(request: Request, db: Session = Depends(get_db)) -> RotationActor:
    actor = session_actor(request, db)
    if not actor:
        raise HTTPException(401, "请先选择轮岗模拟账号")
    return actor


def require_screen(actor: RotationActor = Depends(require_actor)) -> RotationActor:
    if actor.kind != KIND_SCREEN:
        raise HTTPException(403, "只有休息室大屏可以操作")
    return actor


def require_manager(actor: RotationActor = Depends(require_actor)) -> RotationActor:
    if actor.kind not in MANAGE_KINDS:
        raise HTTPException(403, "只有轮岗主管或经理可以操作")
    return actor


def require_member(actor: RotationActor = Depends(require_actor)) -> RotationActor:
    if actor.kind != KIND_MEMBER:
        raise HTTPException(403, "请先选择要模拟的 CM/TR 工号")
    return actor
