"""Temporary recognizer days. Identity, group membership and permissions are untouched."""
from __future__ import annotations

from datetime import date, datetime
from hashlib import sha256
import json

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import joinedload

from app.access_policy import ensure_scoped_hr_employee, scoped_hr_attraction_ids
from app.services.audit import write_audit
from app.services.identity import base_role_at, base_roles_at, duty_code_at, employed_on, TEMPORARY_RECOGNIZER_LABEL
from app.v2_models import Employee, RecognitionRecord, TemporaryRecognitionQualification, UserAccount


LABEL = TEMPORARY_RECOGNIZER_LABEL


def month_rows(db, employee_id=None):
    query = db.query(TemporaryRecognitionQualification).filter(
        TemporaryRecognitionQualification.qualified_on.startswith(date.today().strftime("%Y-%m-")))
    if employee_id is not None:
        query = query.filter_by(employee_id=employee_id)
    return query.order_by(TemporaryRecognitionQualification.id).all()


def revision(rows):
    return sha256(json.dumps([(r.id, r.status, r.updated_at.isoformat()) for r in rows]).encode()).hexdigest()


def eligible_employee(db, employee):
    role = base_role_at(db, employee.id)
    return bool(employee.is_active and employee.attraction and employee.attraction.active and employee.attraction.employee_circle
                and role and role.code in {"CM", "TR"}
                and db.query(UserAccount.id).filter_by(employee_id=employee.id, enabled=True).first())


def qualification_overview(db, user):
    allowed = scoped_hr_attraction_ids(db, user)
    query = db.query(Employee).options(joinedload(Employee.attraction))
    if allowed is not None:
        query = query.filter(Employee.attraction_id.in_(allowed))
    employees = query.order_by(Employee.employee_no).all()
    roles = base_roles_at(db, [e.id for e in employees])
    enabled = {r[0] for r in db.query(UserAccount.employee_id).filter(UserAccount.enabled.is_(True)).all()}
    rows_by_employee = {}
    for row in month_rows(db):
        rows_by_employee.setdefault(row.employee_id, []).append(row)
    result = []
    for employee in employees:
        own = rows_by_employee.get(employee.id, [])
        role = roles.get(employee.id)
        eligible = bool(employee.is_active and employee.attraction and employee.attraction.active and employee.attraction.employee_circle
                        and role and role.code in {"CM", "TR"} and employee.id in enabled)
        if not eligible and not own:
            continue
        result.append({"id": employee.id, "name": employee.name, "employee_no": employee.employee_no,
                       "attraction_id": employee.attraction_id, "attraction_name": employee.attraction.name if employee.attraction else "未分圈",
                       "eligible": eligible, "revision": revision(own),
                       "dates": sorted(r.qualified_on for r in own if r.status == "active"),
                       "records": [{"date": r.qualified_on, "status": r.status, "reason": r.reason} for r in own]})
    return {"month": date.today().strftime("%Y-%m"), "employees": result}


def update_qualification(db, user, employee_id, payload, ip):
    # Serialize with recognition writes: a referenced day cannot disappear.
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))
    employee = db.get(Employee, employee_id)
    if not employee:
        raise HTTPException(404, "员工不存在")
    ensure_scoped_hr_employee(db, user, employee)
    rows = month_rows(db, employee_id)
    if payload.get("expected_revision") != revision(rows):
        raise HTTPException(409, "资格记录已变化，请刷新后重试")
    values = payload.get("dates")
    reason = str(payload.get("reason") or "").strip()
    if not isinstance(values, list) or len(values) > 31 or not reason or len(reason) > 500:
        raise HTTPException(400, "请选择本月日期并填写修改原因（最多500字）")
    days = set()
    for value in values:
        try:
            parsed = date.fromisoformat(value)
        except (ValueError, TypeError):
            raise HTTPException(400, "资格日期格式无效")
        if parsed.isoformat() != value or value[:7] != date.today().strftime("%Y-%m"):
            raise HTTPException(400, "只能设置本月日期")
        days.add(value)
    old = {r.qualified_on for r in rows if r.status == "active"}
    added, removed = days - old, old - days
    if added and not eligible_employee(db, employee):
        raise HTTPException(400, "仅可为在职、账号启用的CM/TR设置临时主管认可资格")
    # Preserve the same month-close protection used by the employee editor.
    from app.routers._shared import ensure_month_open
    ensure_month_open(db, date.today().strftime("%Y-%m"), employee.attraction_id, "修改临时主管认可资格")
    for day in added:
        base = base_role_at(db, employee_id, day)
        if not base or base.code not in {"CM", "TR"} or not employed_on(employee, day):
            raise HTTPException(400, f"{day}不符合在职CM/TR资格")
        if duty_code_at(db, employee_id, day):
            raise HTTPException(409, f"{day}已有代理职务，无需重复设置临时认可资格")
    if removed and db.query(RecognitionRecord.id).filter(
        RecognitionRecord.recognizer_employee_id == employee_id,
        RecognitionRecord.recognition_date.in_(removed),
        RecognitionRecord.recognizer_role_snapshot == LABEL,
    ).first():
        raise HTTPException(409, "所选日期已有认可引用，请先核对关联认可，不能直接取消资格")
    lookup = {r.qualified_on: r for r in rows}
    for day in sorted(added | removed):
        row = lookup.get(day)
        if row is None:
            row = TemporaryRecognitionQualification(employee_id=employee_id, qualified_on=day)
            db.add(row)
        row.status = "active" if day in days else "cancelled"
        row.reason, row.updated_by, row.updated_at = reason, user.id, datetime.now()
    if added or removed:
        write_audit(db, user.employee, "调整临时主管认可资格", "employee", employee_id,
                    before={"dates": sorted(old)}, after={"dates": sorted(days)}, reason=reason, ip_address=ip)
    db.commit()
    return {"ok": True, "dates": sorted(days)}
