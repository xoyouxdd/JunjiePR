"""Deduction eligibility, repeat categories and upgrade candidate selection."""
from __future__ import annotations

from app.date_utils import add_calendar_months, parse_iso_date, subtract_calendar_months
from app.services.identity import role_at
from app.v2_auth import V2User, load_user_identity
from app.v2_models import DeductionLevel, DeductionRecord, DeductionType, Employee, UserAccount
from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session


ATTENDANCE_DEDUCTION_CODES = {
    "ATT_EARLY_CLOCK",
    "ATT_LATE_WITHIN_30",
    "ATT_LATE_OVER_30",
    "ATT_LATE_CLOCK",
    "ATT_EARLY_LEAVE_WITHIN_30",
    "ATT_EARLY_LEAVE_OVER_30",
    "ATT_MISSING_CLOCK",
    "ATT_REMOTE_CLOCK",
}


REPEAT_CONTROLLED_DEDUCTION_CODES = ATTENDANCE_DEDUCTION_CODES | {"SICK_LEAVE_VIOLATION"}


ATTENDANCE_UPGRADE_GROUPS = (
    frozenset({"ATT_EARLY_CLOCK", "ATT_LATE_CLOCK"}),
    frozenset({"ATT_LATE_WITHIN_30", "ATT_EARLY_LEAVE_WITHIN_30"}),
)


DIRECT_HIDDEN_DEDUCTION_CODES = {"ATT_LATE_OVER_30", "ATT_EARLY_LEAVE_OVER_30"}


DEDUCTION_TYPE_ORDER = (
    "SAFETY",
    "AUDIT",
    "COURTESY",
    "INCLUSION",
    "SHOW",
    "EFFICIENCY",
    "MSP",
    "COMPLAINT",
    "SICK_LEAVE_VIOLATION",
    "OTHER",
    "ATT_EARLY_CLOCK",
    "ATT_LATE_CLOCK",
    "ATT_LATE_OVER_30",
    "ATT_LATE_WITHIN_30",
    "ATT_EARLY_LEAVE_OVER_30",
    "ATT_EARLY_LEAVE_WITHIN_30",
    "ATT_MISSING_CLOCK",
    "ATT_REMOTE_CLOCK",
)


DEDUCTION_LEVEL_ORDER = {"STATEMENT": 1, "MEMO": 2, "WARNING_1": 3, "WARNING_2": 4}


# Only two unused statements participate in automatic upgrading. Results do not.
NEXT_DEDUCTION_LEVEL = {"STATEMENT": "MEMO"}


UPGRADE_REVIEWER_CODES = {"GSM"}

SUPERVISOR_DEDUCTION_SCOPE = "主管（全部等级）"
ACTING_GSM_DEDUCTION_SCOPE = "代理TA GSM的主管（全部等级）"


def upgrade_type_codes(code: str) -> frozenset[str]:
    """Match repeat/upgrade history within the agreed attendance category."""
    return next((group for group in ATTENDANCE_UPGRADE_GROUPS if code in group), frozenset({code}))


def deduction_counts_for_score(row: DeductionRecord) -> bool:
    """The second statement is evidence for an upgrade, not another deduction."""
    return row.status == "active" and row.upgrade_role != "source_second"


def direct_only_deduction_user(user: V2User) -> bool:
    return "DEDUCTION_DIRECT" in user.permissions and "DEDUCTION_ALL" not in user.permissions


def ensure_deduction_type_allowed(user: V2User, deduction_type: DeductionType, db=None, on_date=None) -> None:
    if direct_only_deduction_user(user) and deduction_type.code in DIRECT_HIDDEN_DEDUCTION_CODES:
        raise HTTPException(403, "TA主管、主管不能登记迟到30分钟以上或早退30分钟以上")
    if db is not None:
        from app.services.scoring_policies import deduction_allowed
        from datetime import date
        deduction_allowed(db, user, deduction_type, on_date or date.today().isoformat())


def unused_statement_upgrade_filters(statement_id: int):
    return (
        DeductionRecord.deduction_level_id == statement_id,
        DeductionRecord.status == "active",
        DeductionRecord.upgrade_role.is_(None),
        DeductionRecord.upgrade_request_id.is_(None),
        DeductionRecord.legacy_upgrade_excluded.is_(False),
        or_(DeductionRecord.upgrade_state.is_(None), DeductionRecord.upgrade_state == "eligible"),
    )


def statement_upgrade_candidate(db: Session, employee_id: int, deduction_type_id: int, occurred_on: str) -> DeductionRecord | None:
    """Return a registered unused statement within three months either side of the event."""
    statement = db.query(DeductionLevel).filter_by(code="STATEMENT", active=True).first()
    deduction_type = db.get(DeductionType, deduction_type_id)
    if not statement or not deduction_type:
        return None
    from app.services.scoring_policies import repeats, repeat_codes
    if not repeats(db, deduction_type, occurred_on):
        return None
    event_date = parse_iso_date(occurred_on, "事件日期")
    window_start = subtract_calendar_months(event_date, 3).isoformat()
    window_end = add_calendar_months(event_date, 3).isoformat()
    return (
        db.query(DeductionRecord)
        .join(DeductionType, DeductionType.id == DeductionRecord.deduction_type_id)
        .filter(
            DeductionRecord.employee_id == employee_id,
            DeductionType.code.in_(repeat_codes(db, deduction_type, occurred_on)),
            *unused_statement_upgrade_filters(statement.id),
            DeductionRecord.occurred_on >= window_start,
            DeductionRecord.occurred_on <= window_end,
        )
        .order_by(DeductionRecord.occurred_on.asc(), DeductionRecord.id.asc())
        .first()
    )


def can_review_statement_upgrade(user: V2User) -> bool:
    return user.role.code in UPGRADE_REVIEWER_CODES and "DEDUCTION_ALL" in user.permissions


def _upgrade_reviewer_candidates(db: Session):
    return (
        db.query(Employee, UserAccount)
        .join(UserAccount, UserAccount.employee_id == Employee.id)
        .filter(Employee.is_active.is_(True), UserAccount.enabled.is_(True))
    )


def _eligible_upgrade_identity(db: Session, employee: Employee, account: UserAccount) -> V2User | None:
    role = role_at(db, employee.id)
    if not role or role.code not in UPGRADE_REVIEWER_CODES:
        return None
    try:
        user = load_user_identity(db, employee, account)
    except HTTPException as exc:
        if exc.status_code != 403:
            raise
        return None
    return user if can_review_statement_upgrade(user) else None


def statement_upgrade_reviewer(db: Session, employee_id: int | None) -> V2User | None:
    if employee_id is None:
        return None
    candidate = _upgrade_reviewer_candidates(db).filter(Employee.id == employee_id).first()
    return _eligible_upgrade_identity(db, *candidate) if candidate else None


def checked_upgrade_reviewer(db: Session, employee_id: int | None) -> Employee:
    reviewer = statement_upgrade_reviewer(db, employee_id)
    if not reviewer:
        raise HTTPException(400, "请选择在职、账号启用且有声明审核权限的GSM审核")
    return reviewer.employee


def statement_upgrade_reviewer_options(db: Session) -> list[dict]:
    rows = (
        _upgrade_reviewer_candidates(db)
        .order_by(Employee.name, Employee.employee_no)
        .all()
    )
    result = []
    for employee, account in rows:
        reviewer = _eligible_upgrade_identity(db, employee, account)
        if reviewer:
            result.append({"id": employee.id, "name": employee.name, "employee_no": employee.employee_no,
                           "role_code": reviewer.role.code, "role_name": reviewer.role.name})
    return result
