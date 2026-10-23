"""Circle HR receives only local frontline/Lead operator summaries, never raw payloads."""
from datetime import date

from sqlalchemy import and_, or_

from app.services.identity import base_roles_at, duties_at_bulk
from app.v2_models import (AuditLog, Employee, UserAccount, RecognitionRecord, SickLeaveRecord,
                          EmployeeLOAPeriod, EmployeeActingDuty)
from app.services.audit import AUDIT_EMPLOYEE_ENTITY_TYPES


VISIBLE_OPERATOR_CODES = frozenset({'CM', 'TR', 'SUPERVISOR', 'TA_SUPERVISOR'})


def role_codes(db, ids, on_date):
    bases = base_roles_at(db, ids, on_date)
    duties = duties_at_bulk(db, ids, on_date)
    return {pid: ({bases[pid].code} if bases.get(pid) else set()) |
                  {r.code for r in duties.get(pid, [])} for pid in ids}


def permitted(codes):
    # A supervisor acting as TA GSM is still a manager for audit visibility.
    return bool(codes) and codes <= VISIBLE_OPERATOR_CODES


def circle_hr_logs(db, circles, limit):
    from app.v2_models import (DeductionRecord, DeductionUpgradeRequest, DeductionFollowUp,
                              CircleTransferRequest)
    people = {p.id: p for p in db.query(Employee).filter(Employee.attraction_id.in_(circles or {-1})).all()}
    current = role_codes(db, list(people), date.today().isoformat())
    eligible_ids = {pid for pid, p in people.items() if permitted(current.get(pid, set())) or
                    (p.account_deleted_at and not current.get(pid))}
    if not eligible_ids:
        return []
    # NULL object scope remains global-only. No incoming-transfer exception may
    # reveal operations by staff or privileged HR from another circle.
    query = db.query(AuditLog).filter(AuditLog.attraction_id.in_(circles), AuditLog.operator_id.in_(eligible_ids))
    historical = {}
    targets = {}
    target_people = dict(people)
    target_current = dict(current)
    employee_models = {'recognition': RecognitionRecord, 'deduction': DeductionRecord,
                       'sick_leave': SickLeaveRecord, 'user_account': UserAccount,
                       'employee_loa_period': EmployeeLOAPeriod, 'acting_duty': EmployeeActingDuty,
                       'deduction_upgrade': DeductionUpgradeRequest, 'deduction_follow_up': DeductionFollowUp,
                       'circle_transfer': CircleTransferRequest}

    def target_id(row):
        key = (row.entity_type, row.entity_id)
        if key in targets:
            return targets[key]
        known = row.entity_type in AUDIT_EMPLOYEE_ENTITY_TYPES or row.entity_type in employee_models
        if not known:
            return None  # Shared object, not an employee account/profile.
        try:
            value = int(row.entity_id)
        except (TypeError, ValueError):
            return -1
        if row.entity_type in AUDIT_EMPLOYEE_ENTITY_TYPES:
            result = value
        else:
            entity = db.get(employee_models[row.entity_type], value)
            result = entity.employee_id if entity else -1
        targets[key] = result
        return result

    def visible_person(pid, day):
        if pid not in target_people:
            target_people[pid] = db.get(Employee, pid)
            target_current.update(role_codes(db, [pid], date.today().isoformat()))
        p = target_people[pid]
        if not p or p.attraction_id not in circles:
            return False
        codes = target_current.get(pid, set())
        if not permitted(codes) and not (p.account_deleted_at and not codes):
            return False
        key = (pid, day)
        if key not in historical:
            historical[key] = permitted(role_codes(db, [pid], day).get(pid, set()))
        return historical[key]

    rows = []
    cursor = None
    while len(rows) < limit:
        page_query = query
        if cursor:
            stamp, ident = cursor
            page_query = page_query.filter(or_(AuditLog.created_at < stamp,
                and_(AuditLog.created_at == stamp, AuditLog.id < ident)))
        batch = page_query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(256).all()
        if not batch:
            break
        # Resolve historical operator identities per day in batches, not per row.
        for day in {r.created_at.date().isoformat() for r in batch}:
            ids = {r.operator_id for r in batch if r.created_at.date().isoformat() == day and (r.operator_id, day) not in historical}
            for pid, codes in role_codes(db, list(ids), day).items():
                historical[(pid, day)] = permitted(codes)
        for row in batch:
            day = row.created_at.date().isoformat()
            if not historical.get((row.operator_id, day), False):
                continue
            target = target_id(row)
            if target is not None and not visible_person(target, day):
                continue
            rows.append(row)
            if len(rows) == limit:
                break
        cursor = (batch[-1].created_at, batch[-1].id)
    return rows
