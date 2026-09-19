"""Login account, password and account-name endpoints."""
from __future__ import annotations

from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session
from app.v2_auth import V2User, require_permissions
from app.v2_crypto import account_reset_password, hash_password
from app.v2_database import CIRCLE_HR_ACCOUNTS, get_db
from app.v2_models import Attraction, Employee, Role, UserAccount, UserSession
from app.v2_services import managed_attraction_ids, role_at, roles_at, write_audit
from app.routers._shared import (
    CIRCLE_HR_MANAGED_ROLE_CODES,
    REGULAR_ACCOUNT_ROLE_CODES,
    SCOPED_HR_ROLE_CODE,
    client_ip,
    ensure_scoped_hr_employee,
    invalidate_data_caches,
    login_account_archive_state,
)

router = APIRouter()


def account_password_status(account: UserAccount | None) -> tuple[str, str]:
    """Return a non-sensitive, evidence-based password state for HR views."""
    if not account:
        return "未开通账号", "unprovisioned"
    if account.must_change_password:
        return "待本人修改初始/重置密码", "pending_change"
    if account.password_changed_at:
        return "已修改密码", "changed"
    return "历史状态未记录", "unknown"


def account_login_state(account: UserAccount | None, employee: Employee) -> tuple[str, str]:
    if employee.account_deleted_at:
        return "账号已删除·留档", "archived"
    if not account:
        return "未开通账号", "unprovisioned"
    if not employee.is_active:
        return "员工已离职（不可登录）", "employee_inactive"
    if not account.enabled:
        return "账号已停用", "disabled"
    if account.locked_until and account.locked_until > datetime.now():
        return f"临时锁定至 {account.locked_until.strftime('%Y-%m-%d %H:%M')}", "locked"
    return "账号已启用", "enabled"


@router.get("/hr/account-status")
def hr_account_status(request: Request, db: Session = Depends(get_db), user: V2User = Depends(require_permissions("HR_MANAGE"))):
    """Return approved account-status metadata without exposing passwords.

    This is deliberately separate from employee-management edit scope: a
    scoped HR can read CM/TR/TALEAD/Lead status across all three circles, but
    their existing edit endpoints remain limited to their own circle.
    """
    if user.role.code == SCOPED_HR_ROLE_CODE:
        permitted_roles = CIRCLE_HR_MANAGED_ROLE_CODES
        scope_label = "全部景点圈的 CM/TR、TALEAD、Lead"
    elif user.role.code == "SYSTEM_ADMIN":
        permitted_roles = REGULAR_ACCOUNT_ROLE_CODES
        scope_label = "全部常规账号"
    else:
        raise HTTPException(403, "仅景点圈HR和最高管理员可以查看账号状态")

    employees = db.query(Employee).order_by(Employee.name, Employee.employee_no).all()
    role_map = roles_at(db, [employee.id for employee in employees])
    target_employees = [employee for employee in employees if role_map.get(employee.id) and role_map[employee.id].code in permitted_roles]
    target_ids = [employee.id for employee in target_employees]
    accounts = {
        account.employee_id: account
        for account in db.query(UserAccount).filter(UserAccount.employee_id.in_(target_ids)).all()
    } if target_ids else {}
    attraction_ids = {employee.attraction_id for employee in target_employees if employee.attraction_id}
    attractions = {
        row.id: row.name
        for row in db.query(Attraction).filter(Attraction.id.in_(attraction_ids)).all()
    } if attraction_ids else {}
    items = []
    for employee in target_employees:
        account = accounts.get(employee.id)
        role = role_map[employee.id]
        account_status, account_status_code = account_login_state(account, employee)
        password_status, password_status_code = account_password_status(account)
        items.append(
            {
                "employee_id": employee.id,
                "employee_no": employee.employee_no,
                "name": employee.name,
                "role_code": role.code,
                "role_name": role.name,
                "attraction_name": attractions.get(employee.attraction_id, "未分配景点圈"),
                "login_account": account.login_account if account else "",
                "account_status": account_status,
                "account_status_code": account_status_code,
                "password_status": password_status,
                "password_status_code": password_status_code,
                "password_changed_at": account.password_changed_at.strftime("%Y-%m-%d %H:%M:%S") if account and account.password_changed_at else "",
                "login_status": "已登录" if account and account.last_login_at else "从未登录",
                "last_login_at": account.last_login_at.strftime("%Y-%m-%d %H:%M:%S") if account and account.last_login_at else "",
            }
        )
    write_audit(
        db,
        user.employee,
        "查看账号状态",
        "account_status",
        after={"scope": scope_label, "returned_count": len(items)},
        ip_address=client_ip(request),
    )
    db.commit()
    return {"scope_label": scope_label, "items": items}


def resolved_reset_password(account: UserAccount) -> str:
    """Apply the documented "登录账号后四位" reset rule with the policy length guard."""
    try:
        return account_reset_password(account.login_account)
    except ValueError as exc:
        raise HTTPException(
            400,
            f"该账号的登录账号“{account.login_account}”不足四位，无法按“登录账号后四位”规则重置密码，"
            "否则新密码会低于系统密码长度下限、本人也无法自行改回。请管理员先规范该账号的登录账号，"
            "或改用其他方式处理该账号。",
        ) from exc


@router.post("/accounts/reset-password")
def reset_employee_password(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("PASSWORD_RESET")),
):
    employee_no = str(payload.get("employee_no") or "").strip()
    name = str(payload.get("name") or "").strip()
    if not employee_no or not name:
        raise HTTPException(400, "员工号和姓名必填")
    employee = db.query(Employee).filter(Employee.employee_no == employee_no).first()
    if not employee or employee.name != name:
        raise HTTPException(400, "员工号或姓名不匹配")
    target_role = role_at(db, employee.id)
    if not target_role:
        raise HTTPException(400, "该员工当前没有有效角色")
    ensure_scoped_hr_employee(db, user, employee)
    if target_role.code in {"HR_ADMIN", "HR_CIRCLE", "SYSTEM_ADMIN"}:
        raise HTTPException(403, "HR管理员、景点圈HR和系统管理员账号不能在此处重置")
    if user.role.code == SCOPED_HR_ROLE_CODE and target_role.code not in CIRCLE_HR_MANAGED_ROLE_CODES:
        raise HTTPException(403, "景点圈HR只能重置LEAD及以下员工密码")
    if user.role.code == "GSM" and employee.attraction_id not in managed_attraction_ids(db, user.id):
        raise HTTPException(403, "只能重置本人管理景点圈内的员工密码")
    account = db.query(UserAccount).filter(UserAccount.employee_id == employee.id).first()
    if not account:
        raise HTTPException(400, "该员工尚未开通登录账号")
    reset_password = resolved_reset_password(account)
    account.password_hash = hash_password(reset_password)
    account.failed_attempts = 0
    account.locked_until = None
    account.must_change_password = True
    account.credential_initialized = True
    account.password_changed_at = None
    db.query(UserSession).filter(UserSession.account_id == account.id).delete(synchronize_session=False)
    write_audit(
        db,
        user.employee,
        "重置账号密码",
        "user_account",
        account.id,
        after={
            "employee_no": employee.employee_no,
            "employee_name": employee.name,
            "role": target_role.name,
            "account_enabled": account.enabled,
            "password_rule": "登录账号后四位",
            "must_change_password": True,
            "sessions_revoked": True,
        },
        ip_address=client_ip(request),
    )
    db.commit()
    return {
        "ok": True,
        "employee_no": employee.employee_no,
        "employee_name": employee.name,
        "account_enabled": account.enabled,
        "password_rule": "登录账号后四位",
        "temporary_password": reset_password,
        "must_change_password": True,
        "sessions_revoked": True,
    }


@router.get("/accounts/name-targets")
def account_name_targets(
    keyword: str = "",
    limit: int = 30,
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("HR_MANAGE")),
):
    """Return only accounts whose current display name this HR user may correct."""
    if user.role.code not in {SCOPED_HR_ROLE_CODE, "SYSTEM_ADMIN"}:
        raise HTTPException(403, "仅景点圈HR和最高管理员可以修改账号中文姓名")
    normalized = str(keyword or "").strip()
    if not normalized:
        return {"items": [], "total": 0}
    escaped = normalized.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    pattern = f"%{escaped}%"
    candidates = (
        db.query(Employee)
        .join(UserAccount, UserAccount.employee_id == Employee.id)
        .filter(
            or_(Employee.employee_no.like(pattern, escape="!"), Employee.name.like(pattern, escape="!")),
        )
        .order_by(Employee.name, Employee.employee_no)
        .limit(max(1, min(int(limit or 30), 50)))
        .all()
    )
    roles = roles_at(db, [employee.id for employee in candidates])
    allowed = []
    for employee in candidates:
        role = roles.get(employee.id)
        if not role or role.code in {"HR_ADMIN", "HR_CIRCLE", "SYSTEM_ADMIN"}:
            continue
        if user.role.code == SCOPED_HR_ROLE_CODE:
            if employee.attraction_id != user.employee.attraction_id or role.code not in CIRCLE_HR_MANAGED_ROLE_CODES:
                continue
        allowed.append(
            {
                "id": employee.id,
                "employee_no": employee.employee_no,
                "name": employee.name,
                "role_code": role.code,
                "role_name": role.name,
                "attraction_name": employee.attraction.name if employee.attraction else "未分配景点圈",
            }
        )
    return {"items": allowed, "total": len(allowed)}


@router.post("/accounts/update-name")
def update_account_name(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("HR_MANAGE")),
):
    if user.role.code not in {SCOPED_HR_ROLE_CODE, "SYSTEM_ADMIN"}:
        raise HTTPException(403, "仅景点圈HR和最高管理员可以修改账号中文姓名")
    employee_id = int(payload.get("employee_id") or 0)
    new_name = str(payload.get("name") or "").strip()
    if not employee_id or not new_name:
        raise HTTPException(400, "请选择账号并填写中文姓名")
    if len(new_name) > 100:
        raise HTTPException(400, "中文姓名不能超过100个字符")
    employee = db.get(Employee, employee_id)
    if not employee:
        raise HTTPException(404, "账号不存在")
    account = db.query(UserAccount).filter(UserAccount.employee_id == employee.id).first()
    role = role_at(db, employee.id)
    if not account or not role:
        raise HTTPException(400, "该员工尚未配置有效登录账号或角色")
    ensure_scoped_hr_employee(db, user, employee)
    if role.code in {"HR_ADMIN", "HR_CIRCLE", "SYSTEM_ADMIN"}:
        raise HTTPException(403, "HR和系统管理员账号不能在此处修改姓名")
    if user.role.code == SCOPED_HR_ROLE_CODE and role.code not in CIRCLE_HR_MANAGED_ROLE_CODES:
        raise HTTPException(403, "景点圈HR只能修改本圈CM、TR、TA主管或主管的姓名")
    old_name = employee.name
    if old_name == new_name:
        return {"ok": True, "employee_id": employee.id, "employee_no": employee.employee_no, "name": employee.name, "unchanged": True}
    employee.name = new_name
    employee.updated_at = datetime.now()
    write_audit(
        db,
        user.employee,
        "修改账号中文姓名",
        "employee",
        employee.id,
        before={"employee_no": employee.employee_no, "name": old_name, "role": role.name},
        after={"employee_no": employee.employee_no, "name": new_name, "role": role.name},
        reason="HR账号姓名纠错",
        ip_address=client_ip(request),
    )
    db.commit()
    invalidate_data_caches()
    return {"ok": True, "employee_id": employee.id, "employee_no": employee.employee_no, "name": employee.name, "unchanged": False}


def ensure_login_account_archive_target(db: Session, user: V2User, employee: Employee) -> tuple[Role, UserAccount, dict]:
    """Authorize credential removal without ever deleting the employee archive."""
    if user.role.code not in {SCOPED_HR_ROLE_CODE, "SYSTEM_ADMIN"}:
        raise HTTPException(403, "仅景点圈HR和最高管理员可以删除登录账号")
    ensure_scoped_hr_employee(db, user, employee)
    role = role_at(db, employee.id)
    if not role or role.code not in REGULAR_ACCOUNT_ROLE_CODES:
        raise HTTPException(403, "HR和最高管理员账号不能在此删除")
    if user.role.code == SCOPED_HR_ROLE_CODE and role.code not in CIRCLE_HR_MANAGED_ROLE_CODES:
        raise HTTPException(403, "景点圈HR只能删除本圈CM、TR、TA主管或主管的登录账号")
    account = db.query(UserAccount).filter(UserAccount.employee_id == employee.id).first()
    state = login_account_archive_state(employee, account, role)
    if not account:
        raise HTTPException(400, state["reason"] or "该员工没有可删除的登录账号")
    if not state["eligible"]:
        raise HTTPException(400, state["reason"])
    return role, account, state


@router.delete("/hr/employees/{employee_id}/account")
def archive_employee_login_account(
    employee_id: int,
    request: Request,
    payload: dict | None = None,
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("HR_MANAGE")),
):
    """Remove only credentials and sessions after seven days; keep all data."""
    employee = db.get(Employee, employee_id)
    if not employee:
        raise HTTPException(404, "员工不存在")
    role, account, _state = ensure_login_account_archive_target(db, user, employee)
    before = {
        "employee_no": employee.employee_no,
        "employee_name": employee.name,
        "role": role.name,
        "login_account": account.login_account,
        "employee_active": employee.is_active,
        "account_enabled": account.enabled,
    }
    revoked_sessions = db.query(UserSession).filter(UserSession.account_id == account.id).delete(synchronize_session=False)
    db.delete(account)
    employee.account_deleted_at = datetime.now()
    employee.account_deleted_by_name = user.name
    employee.updated_at = datetime.now()
    write_audit(
        db,
        user.employee,
        "删除停用登录账号",
        "employee_login_archive",
        employee.id,
        before=before,
        after={
            "employee_no": employee.employee_no,
            "employee_name": employee.name,
            "role": role.name,
            "login_removed": True,
            "sessions_revoked": int(revoked_sessions),
            "employee_archive_retained": True,
            "business_history_retained": True,
        },
        reason=str((payload or {}).get("reason") or "离职/停用满7天后删除登录账号，保留员工与业务档案"),
        ip_address=client_ip(request),
    )
    db.commit()
    invalidate_data_caches()
    return {"ok": True, "employee_id": employee.id, "sessions_revoked": int(revoked_sessions), "archive_retained": True}


@router.get("/admin/circle-hr-accounts")
def circle_hr_accounts(db: Session = Depends(get_db), user: V2User = Depends(require_permissions("SYSTEM_ADMIN"))):
    """Show the three scoped HR accounts without exposing stored passwords."""
    login_accounts = [item[0] for item in CIRCLE_HR_ACCOUNTS]
    employees = {
        employee.employee_no: employee
        for employee in db.query(Employee).filter(Employee.employee_no.in_(login_accounts)).all()
    }
    employee_ids = [employee.id for employee in employees.values()]
    accounts = {
        account.employee_id: account
        for account in db.query(UserAccount).filter(UserAccount.employee_id.in_(employee_ids)).all()
    } if employee_ids else {}
    items = []
    for login_account, name, circle_name in CIRCLE_HR_ACCOUNTS:
        employee = employees.get(login_account)
        account = accounts.get(employee.id) if employee else None
        items.append(
            {
                "employee_id": employee.id if employee else None,
                "login_account": login_account,
                "name": name,
                "attraction_name": circle_name,
                "account_enabled": bool(account and account.enabled),
                "must_change_password": bool(account.must_change_password) if account else True,
                "last_login_at": account.last_login_at.strftime("%Y-%m-%d %H:%M:%S") if account and account.last_login_at else "",
                "password_status": "待首次修改" if not account or account.must_change_password else "已设置（不可读取）",
            }
        )
    return {"items": items}


@router.post("/admin/circle-hr-accounts/{employee_id}/reset-password")
def reset_circle_hr_password(
    employee_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: V2User = Depends(require_permissions("SYSTEM_ADMIN")),
):
    employee = db.get(Employee, employee_id)
    role = role_at(db, employee.id) if employee else None
    if not employee or not role or role.code != "HR_CIRCLE":
        raise HTTPException(404, "景点圈HR账号不存在")
    account = db.query(UserAccount).filter(UserAccount.employee_id == employee.id).first()
    if not account:
        raise HTTPException(400, "该景点圈HR尚未开通登录账号")
    reset_password = resolved_reset_password(account)
    account.password_hash = hash_password(reset_password)
    account.failed_attempts = 0
    account.locked_until = None
    account.must_change_password = True
    account.credential_initialized = True
    account.password_changed_at = None
    account.enabled = True
    db.query(UserSession).filter(UserSession.account_id == account.id).delete(synchronize_session=False)
    write_audit(
        db,
        user.employee,
        "重置景点圈HR密码",
        "user_account",
        account.id,
        after={
            "employee_no": employee.employee_no,
            "employee_name": employee.name,
            "attraction_name": employee.attraction.name if employee.attraction else "",
            "password_rule": "登录账号后四位",
            "must_change_password": True,
        },
        reason="最高管理员重置景点圈HR账号密码",
        ip_address=client_ip(request),
    )
    db.commit()
    return {
        "ok": True,
        "employee_no": employee.employee_no,
        "employee_name": employee.name,
        "temporary_password": reset_password,
        "must_change_password": True,
        "message": "密码已重置为登录账号后四位，请要求账号本人登录后立即修改。",
    }
