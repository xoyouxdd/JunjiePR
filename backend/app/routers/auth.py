"""Login, session, password and changelog endpoints."""
from __future__ import annotations

from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.v2_auth import V2User, current_user
from app.v2_crypto import hash_password, new_session_token, token_hash, verify_password
from app.v2_database import get_db
from app.v2_models import Employee, UserAccount, UserSession
from app.v2_services import current_group_for_employee, current_leader_for_employee, direct_member_ids, role_at, write_audit
from app.changelog import visible_releases
from app.version import APP_VERSION
from app.security import password_policy_error, request_is_https
from app.routers._shared import client_ip, group_display_metadata_bulk

router = APIRouter()


def user_payload(db: Session, user: V2User) -> dict:
    leader = current_leader_for_employee(db, user.id)
    group = current_group_for_employee(db, user.id)
    display = group_display_metadata_bulk(db, [group.id]).get(group.id, {}) if group else {}
    return {
        "id": user.id,
        "employee_no": user.employee.employee_no,
        "name": user.name,
        "role_code": user.role.code,
        "role_name": user.role.name,
        "attraction_id": user.employee.attraction_id,
        "attraction_name": user.employee.attraction.name if user.employee.attraction else "",
        "leader_name": leader.name if leader else ("待接管" if group and group.status == "pending_takeover" else "未分配"),
        "group_name": display.get("name", group.name if group else ""),
        "previous_group_leader_name": display.get("previous_leader_name", ""),
        "previous_group_leader_until": display.get("previous_leader_until", ""),
        "permissions": sorted(user.permissions),
        "member_count": len(direct_member_ids(db, user.id)),
        "must_change_password": user.account.must_change_password,
    }


@router.post("/login")
def login(payload: dict, request: Request, response: Response, db: Session = Depends(get_db)):
    login_name = str(payload.get("employee_no") or "").strip()
    password = str(payload.get("password") or "")
    account = (
        db.query(UserAccount)
        .join(Employee, Employee.id == UserAccount.employee_id)
        .filter(or_(UserAccount.login_account == login_name, Employee.employee_no == login_name))
        .first()
    )
    if not account or not account.enabled:
        raise HTTPException(401, "账号或密码/PIN不正确")
    if account.locked_until and account.locked_until > datetime.now():
        raise HTTPException(423, "登录失败次数过多，请稍后再试")
    if not verify_password(password, account.password_hash):
        account.failed_attempts += 1
        if account.failed_attempts >= 5:
            account.locked_until = datetime.now() + timedelta(minutes=15)
            account.failed_attempts = 0
        db.commit()
        raise HTTPException(401, "账号或密码/PIN不正确")
    employee = account.employee
    if not employee.is_active:
        raise HTTPException(403, "员工账号已停用")
    role = role_at(db, employee.id)
    if not role:
        raise HTTPException(403, "当前未配置有效角色")
    account.failed_attempts = 0
    account.locked_until = None
    account.last_login_at = datetime.now()
    raw_token = new_session_token()
    db.add(
        UserSession(
            account_id=account.id,
            token_hash=token_hash(raw_token),
            expires_at=datetime.now() + timedelta(hours=12),
        )
    )
    write_audit(db, employee, "登录", "user_account", account.id, ip_address=client_ip(request))
    db.commit()
    response.set_cookie(
        "rc_v2_session",
        raw_token,
        httponly=True,
        samesite="lax",
        secure=request_is_https(request),
        max_age=12 * 3600,
    )
    return {"ok": True, "role": role.name}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    raw_token = request.cookies.get("rc_v2_session")
    if raw_token:
        db.query(UserSession).filter(UserSession.token_hash == token_hash(raw_token)).delete(synchronize_session=False)
        db.commit()
    response.delete_cookie("rc_v2_session", secure=request_is_https(request), httponly=True, samesite="lax")
    return {"ok": True}


@router.get("/me")
def me(db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    return user_payload(db, user)


@router.get("/changelog")
def changelog(user: V2User = Depends(current_user)):
    return {
        "app_version": APP_VERSION,
        "role_code": user.role.code,
        "role_name": user.role.name,
        "releases": visible_releases(user.role.code, user.permissions),
    }


@router.post("/password")
def change_password(payload: dict, request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    current = str(payload.get("current_password") or "")
    new = str(payload.get("new_password") or "")
    confirm = str(payload.get("confirm_password") or "")
    if not verify_password(current, user.account.password_hash):
        raise HTTPException(400, "当前密码/PIN不正确")
    if new != confirm:
        raise HTTPException(400, "两次输入的新密码必须一致")
    policy_error = password_policy_error(new, user.role.code)
    if policy_error:
        raise HTTPException(400, policy_error)
    user.account.password_hash = hash_password(new)
    user.account.must_change_password = False
    user.account.credential_initialized = True
    user.account.password_changed_at = datetime.now()
    current_raw_token = request.cookies.get("rc_v2_session")
    current_token_hash = token_hash(current_raw_token) if current_raw_token else ""
    db.query(UserSession).filter(
        UserSession.account_id == user.account.id,
        UserSession.token_hash != current_token_hash,
    ).delete(synchronize_session=False)
    write_audit(db, user.employee, "修改密码", "user_account", user.account.id, ip_address=client_ip(request))
    db.commit()
    return {"ok": True}


@router.post("/security/screenshot-event")
def record_screenshot_event(request: Request, db: Session = Depends(get_db), user: V2User = Depends(current_user)):
    write_audit(
        db,
        user.employee,
        "检测到截图按键",
        "user_account",
        user.account.id,
        after={"employee_no": user.employee.employee_no, "detection": "print_screen_key"},
        ip_address=client_ip(request),
    )
    db.commit()
    return {"ok": True, "message": "系统已记录截图按键事件；页面访问和导出均受审计，请勿分享敏感数据。"}
