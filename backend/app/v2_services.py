from __future__ import annotations

import hashlib
import json
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.v2_database import FILE_DIR
from app.v2_models import (
    Attraction,
    AttendanceMonthlyScore,
    AttendanceRule,
    AuditLog,
    Employee,
    EmployeeLOAPeriod,
    EmployeeRoleAssignment,
    GroupLeaderAssignment,
    GroupMembership,
    ManagementScope,
    RecognitionRecord,
    RecognitionScoreRule,
    Role,
    RolePermission,
    SickLeaveRecord,
    StoredFile,
    SystemAlert,
    WorkGroup,
    Permission,
)


FRONTLINE_CODES = {"CM", "TR"}
LEADER_CODES = {"TA_SUPERVISOR", "SUPERVISOR"}
GSM_CODES = {"TA_GSM", "GSM"}
RECOGNIZER_CODES = {"TA_SUPERVISOR", "SUPERVISOR", "TA_GSM", "GSM", "AM", "OM"}
SENIOR_RECOGNIZER_CODES = {"TA_GSM", "GSM", "AM", "OM"}
RECOGNIZER_CIRCLE_ORDER = {"热力追踪": 0, "矮人迷宫": 1, "小熊罐子": 2}
RECOGNIZER_ROLE_ORDER = {
    "TA_SUPERVISOR": 0,
    "SUPERVISOR": 1,
    "TA_GSM": 0,
    "GSM": 1,
    "AM": 2,
    "OM": 3,
}
RECOGNIZER_ELIGIBILITY_START = "2026-08-01"
SCORE_UNIT = Decimal("0.01")
HALF_DAY_DEDUCTION = Decimal("0.25")


def role_at(db: Session, employee_id: int, on_date: str | date | None = None) -> Role | None:
    value = on_date.isoformat() if isinstance(on_date, date) else (on_date or date.today().isoformat())
    assignment = (
        db.query(EmployeeRoleAssignment)
        .filter(
            EmployeeRoleAssignment.employee_id == employee_id,
            EmployeeRoleAssignment.status != "cancelled",
            EmployeeRoleAssignment.starts_on <= value,
            or_(EmployeeRoleAssignment.ends_on.is_(None), EmployeeRoleAssignment.ends_on >= value),
        )
        .order_by(EmployeeRoleAssignment.starts_on.desc(), EmployeeRoleAssignment.id.desc())
        .first()
    )
    if assignment:
        return assignment.role
    latest = (
        db.query(EmployeeRoleAssignment)
        .filter(
            EmployeeRoleAssignment.employee_id == employee_id,
            EmployeeRoleAssignment.status != "cancelled",
            EmployeeRoleAssignment.starts_on <= value,
        )
        .order_by(EmployeeRoleAssignment.starts_on.desc(), EmployeeRoleAssignment.id.desc())
        .first()
    )
    if latest and latest.ends_on and latest.ends_on < value and latest.return_role_id:
        return db.get(Role, latest.return_role_id)
    return None


def roles_at(db: Session, employee_ids: list[int] | set[int], on_date: str | date | None = None) -> dict[int, Role | None]:
    """Resolve many employees' effective roles without issuing one query per employee."""
    ids = list(dict.fromkeys(int(employee_id) for employee_id in employee_ids))
    if not ids:
        return {}
    value = on_date.isoformat() if isinstance(on_date, date) else (on_date or date.today().isoformat())
    assignments = (
        db.query(EmployeeRoleAssignment)
        .filter(
            EmployeeRoleAssignment.employee_id.in_(ids),
            EmployeeRoleAssignment.status != "cancelled",
            EmployeeRoleAssignment.starts_on <= value,
        )
        .order_by(
            EmployeeRoleAssignment.employee_id,
            EmployeeRoleAssignment.starts_on.desc(),
            EmployeeRoleAssignment.id.desc(),
        )
        .all()
    )
    grouped: dict[int, list[EmployeeRoleAssignment]] = {employee_id: [] for employee_id in ids}
    role_ids: set[int] = set()
    resolved_role_ids: dict[int, int | None] = {}
    for assignment in assignments:
        grouped[assignment.employee_id].append(assignment)
        role_ids.add(assignment.role_id)
        if assignment.return_role_id:
            role_ids.add(assignment.return_role_id)
    for employee_id in ids:
        rows = grouped[employee_id]
        active = next((row for row in rows if row.ends_on is None or row.ends_on >= value), None)
        if active:
            resolved_role_ids[employee_id] = active.role_id
        elif rows and rows[0].ends_on and rows[0].ends_on < value and rows[0].return_role_id:
            resolved_role_ids[employee_id] = rows[0].return_role_id
        else:
            resolved_role_ids[employee_id] = None
    roles = {role.id: role for role in db.query(Role).filter(Role.id.in_(role_ids)).all()} if role_ids else {}
    return {employee_id: roles.get(role_id) for employee_id, role_id in resolved_role_ids.items()}


def role_permissions(db: Session, role_id: int) -> set[str]:
    return {
        code
        for (code,) in (
            db.query(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .filter(RolePermission.role_id == role_id)
            .all()
        )
    }


def gsm_candidates_for_attractions_bulk(
    db: Session,
    attraction_ids: list[int] | set[int],
    on_date: str | None = None,
) -> dict[int, list[tuple[Employee, Role]]]:
    """Resolve GSM/TA GSM candidates for many attractions with batched queries."""
    ids = list(dict.fromkeys(int(attraction_id) for attraction_id in attraction_ids))
    if not ids:
        return {}
    value = on_date or date.today().isoformat()
    scopes = (
        db.query(ManagementScope)
        .filter(
            ManagementScope.attraction_id.in_(ids),
            ManagementScope.starts_on <= value,
            or_(ManagementScope.ends_on.is_(None), ManagementScope.ends_on >= value),
        )
        .all()
    )
    employee_ids = {scope.employee_id for scope in scopes}
    employees = (
        {employee.id: employee for employee in db.query(Employee).filter(Employee.id.in_(employee_ids)).all()}
        if employee_ids
        else {}
    )
    roles = roles_at(db, employee_ids, value)
    grouped: dict[int, list[tuple[Employee, Role]]] = {attraction_id: [] for attraction_id in ids}
    for scope in scopes:
        employee = employees.get(scope.employee_id)
        role = roles.get(scope.employee_id)
        if employee and employee.is_active and role and role.code in GSM_CODES:
            grouped[scope.attraction_id].append((employee, role))
    for attraction_id in ids:
        grouped[attraction_id].sort(key=lambda item: (item[1].code != "GSM", item[0].employee_no))
    return grouped


def active_group_leader(db: Session, group_id: int, on_date: str | None = None) -> GroupLeaderAssignment | None:
    value = on_date or date.today().isoformat()
    return (
        db.query(GroupLeaderAssignment)
        .filter(
            GroupLeaderAssignment.group_id == group_id,
            GroupLeaderAssignment.status == "active",
            GroupLeaderAssignment.starts_on <= value,
            or_(GroupLeaderAssignment.ends_on.is_(None), GroupLeaderAssignment.ends_on >= value),
        )
        .order_by(GroupLeaderAssignment.starts_on.desc(), GroupLeaderAssignment.id.desc())
        .first()
    )


def active_group_memberships(db: Session, group_id: int, on_date: str | None = None) -> list[GroupMembership]:
    value = on_date or date.today().isoformat()
    return (
        db.query(GroupMembership)
        .filter(
            GroupMembership.group_id == group_id,
            GroupMembership.status == "active",
            GroupMembership.starts_on <= value,
            or_(GroupMembership.ends_on.is_(None), GroupMembership.ends_on >= value),
        )
        .all()
    )


def groups_led_by(db: Session, leader_id: int) -> list[WorkGroup]:
    today = date.today().isoformat()
    return (
        db.query(WorkGroup)
        .join(GroupLeaderAssignment, GroupLeaderAssignment.group_id == WorkGroup.id)
        .filter(
            GroupLeaderAssignment.leader_employee_id == leader_id,
            GroupLeaderAssignment.status == "active",
            GroupLeaderAssignment.starts_on <= today,
            or_(GroupLeaderAssignment.ends_on.is_(None), GroupLeaderAssignment.ends_on >= today),
        )
        .order_by(WorkGroup.name.asc())
        .all()
    )


def groups_led_by_bulk(db: Session, leader_ids: list[int] | set[int]) -> dict[int, list[WorkGroup]]:
    """Same window semantics as groups_led_by, resolved for many leaders at once."""
    ids = list(dict.fromkeys(int(leader_id) for leader_id in leader_ids))
    if not ids:
        return {}
    today = date.today().isoformat()
    rows = (
        db.query(WorkGroup, GroupLeaderAssignment.leader_employee_id)
        .join(GroupLeaderAssignment, GroupLeaderAssignment.group_id == WorkGroup.id)
        .filter(
            GroupLeaderAssignment.leader_employee_id.in_(ids),
            GroupLeaderAssignment.status == "active",
            GroupLeaderAssignment.starts_on <= today,
            or_(GroupLeaderAssignment.ends_on.is_(None), GroupLeaderAssignment.ends_on >= today),
        )
        .order_by(WorkGroup.name.asc())
        .all()
    )
    grouped: dict[int, list[WorkGroup]] = {leader_id: [] for leader_id in ids}
    for group, leader_id in rows:
        grouped.setdefault(leader_id, []).append(group)
    return grouped


def active_group_leaders_bulk(db: Session, group_ids: list[int] | set[int], on_date: str | None = None) -> dict[int, GroupLeaderAssignment | None]:
    """Same pick as active_group_leader (latest start, then id) for many groups."""
    ids = list(dict.fromkeys(int(group_id) for group_id in group_ids))
    if not ids:
        return {}
    value = on_date or date.today().isoformat()
    rows = (
        db.query(GroupLeaderAssignment)
        .filter(
            GroupLeaderAssignment.group_id.in_(ids),
            GroupLeaderAssignment.status == "active",
            GroupLeaderAssignment.starts_on <= value,
            or_(GroupLeaderAssignment.ends_on.is_(None), GroupLeaderAssignment.ends_on >= value),
        )
        .order_by(
            GroupLeaderAssignment.group_id,
            GroupLeaderAssignment.starts_on.desc(),
            GroupLeaderAssignment.id.desc(),
        )
        .all()
    )
    resolved: dict[int, GroupLeaderAssignment | None] = {group_id: None for group_id in ids}
    for row in rows:
        if resolved.get(row.group_id) is None:
            resolved[row.group_id] = row
    return resolved


def active_group_memberships_bulk(db: Session, group_ids: list[int] | set[int], on_date: str | None = None) -> dict[int, list[GroupMembership]]:
    ids = list(dict.fromkeys(int(group_id) for group_id in group_ids))
    if not ids:
        return {}
    value = on_date or date.today().isoformat()
    rows = (
        db.query(GroupMembership)
        .filter(
            GroupMembership.group_id.in_(ids),
            GroupMembership.status == "active",
            GroupMembership.starts_on <= value,
            or_(GroupMembership.ends_on.is_(None), GroupMembership.ends_on >= value),
        )
        .order_by(GroupMembership.id.asc())
        .all()
    )
    grouped: dict[int, list[GroupMembership]] = {group_id: [] for group_id in ids}
    for row in rows:
        grouped[row.group_id].append(row)
    return grouped


def direct_member_ids(db: Session, leader_id: int) -> set[int]:
    leader = db.get(Employee, leader_id)
    leader_role = role_at(db, leader_id) if leader else None
    if leader and leader_role and leader_role.code == "HR_CIRCLE":
        return {
            employee.id
            for employee in db.query(Employee).filter(
                Employee.attraction_id == leader.attraction_id,
                Employee.is_active.is_(True),
            ).all()
            if (role := role_at(db, employee.id)) and role.code in (FRONTLINE_CODES | LEADER_CODES | GSM_CODES)
        }
    group_ids = [group.id for group in groups_led_by(db, leader_id)]
    if not group_ids:
        return set()
    today = date.today().isoformat()
    return {
        employee_id
        for (employee_id,) in db.query(GroupMembership.employee_id)
        .filter(
            GroupMembership.group_id.in_(group_ids),
            GroupMembership.status == "active",
            GroupMembership.starts_on <= today,
            or_(GroupMembership.ends_on.is_(None), GroupMembership.ends_on >= today),
        )
        .all()
    }


def current_group_for_employee(db: Session, employee_id: int) -> WorkGroup | None:
    today = date.today().isoformat()
    membership = (
        db.query(GroupMembership)
        .filter(
            GroupMembership.employee_id == employee_id,
            GroupMembership.status == "active",
            GroupMembership.starts_on <= today,
            or_(GroupMembership.ends_on.is_(None), GroupMembership.ends_on >= today),
        )
        .order_by(GroupMembership.starts_on.desc(), GroupMembership.id.desc())
        .first()
    )
    return membership.group if membership else None


def current_leader_for_employee(db: Session, employee_id: int) -> Employee | None:
    group = current_group_for_employee(db, employee_id)
    assignment = active_group_leader(db, group.id) if group else None
    return assignment.leader if assignment else None


def active_frontline_employees(db: Session) -> list[Employee]:
    rows = db.query(Employee).filter(Employee.is_active.is_(True)).order_by(Employee.name.asc()).all()
    return [employee for employee in rows if (role_at(db, employee.id) and role_at(db, employee.id).code in FRONTLINE_CODES)]


def managed_attraction_ids(db: Session, employee_id: int, on_date: str | None = None) -> set[int]:
    value = on_date or date.today().isoformat()
    employee = db.get(Employee, employee_id)
    employee_role = role_at(db, employee_id, value) if employee else None
    if employee and employee_role and employee_role.code == "HR_CIRCLE":
        return {employee.attraction_id} if employee.attraction_id else set()
    return {
        attraction_id
        for (attraction_id,) in db.query(ManagementScope.attraction_id)
        .filter(
            ManagementScope.employee_id == employee_id,
            ManagementScope.starts_on <= value,
            or_(ManagementScope.ends_on.is_(None), ManagementScope.ends_on >= value),
        )
        .all()
    }


def recognizer_role_for_date(db: Session, employee_id: int, on_date: str | date | None = None) -> Role | None:
    """Apply the recognition policy start to the employee's current eligible role."""
    eligibility_date = on_date.isoformat() if isinstance(on_date, date) else str(on_date or date.today().isoformat())
    if eligibility_date < RECOGNIZER_ELIGIBILITY_START:
        return None
    current_role = role_at(db, employee_id)
    return current_role if current_role and current_role.code in RECOGNIZER_CODES else None


def recognizer_options(db: Session, attraction_id: int, on_date: str | date | None = None) -> list[dict]:
    eligibility_date = on_date.isoformat() if isinstance(on_date, date) else (str(on_date or date.today().isoformat()))
    if eligibility_date < RECOGNIZER_ELIGIBILITY_START:
        return []
    employees = db.query(Employee).filter(Employee.is_active.is_(True)).order_by(Employee.name.asc()).all()
    # Batch role/circle/score resolution keeps this endpoint at a handful of
    # queries regardless of roster size instead of several per employee.
    roles = roles_at(db, [employee.id for employee in employees])
    circles = {
        circle.id: circle
        for circle in db.query(Attraction).filter(Attraction.employee_circle.is_(True), Attraction.active.is_(True)).all()
    }
    score_rules: dict[int, list[RecognitionScoreRule]] = {}
    for rule in (
        db.query(RecognitionScoreRule)
        .filter(RecognitionScoreRule.active.is_(True), RecognitionScoreRule.effective_date <= eligibility_date)
        .order_by(RecognitionScoreRule.role_id, RecognitionScoreRule.effective_date.desc(), RecognitionScoreRule.id.desc())
        .all()
    ):
        score_rules.setdefault(rule.role_id, []).append(rule)
    fallback_rules: dict[int, RecognitionScoreRule] = {}
    for rule in (
        db.query(RecognitionScoreRule)
        .filter(RecognitionScoreRule.active.is_(True))
        .order_by(RecognitionScoreRule.role_id, RecognitionScoreRule.effective_date.asc())
        .all()
    ):
        fallback_rules.setdefault(rule.role_id, rule)

    def score_for(role_id: int) -> Decimal:
        rule = score_rules.get(role_id, [None])[0] or fallback_rules.get(role_id)
        if not rule:
            raise HTTPException(400, "该认可人角色尚未配置分值")
        return Decimal(str(rule.score)).quantize(SCORE_UNIT)

    result = []
    for employee in employees:
        role = roles.get(employee.id)
        if not role or role.code not in RECOGNIZER_CODES:
            continue
        if role.code in LEADER_CODES:
            circle = circles.get(employee.attraction_id) if employee.attraction_id else None
            if not circle:
                continue
            group_key = f"circle:{circle.id}"
            group_label = circle.name
            group_order = RECOGNIZER_CIRCLE_ORDER.get(circle.name, len(RECOGNIZER_CIRCLE_ORDER))
        elif role.code in SENIOR_RECOGNIZER_CODES:
            group_key = "tagsm_plus"
            group_label = "TAGSM及以上"
            group_order = len(RECOGNIZER_CIRCLE_ORDER)
        else:
            continue
        result.append(
            {
                "id": employee.id,
                "employee_no": employee.employee_no,
                "name": employee.name,
                "role_code": role.code,
                "role_name": role.name,
                "score": float(score_for(role.id)),
                "group_key": group_key,
                "group_label": group_label,
                "group_order": group_order,
                "role_order": RECOGNIZER_ROLE_ORDER[role.code],
            }
        )
    return sorted(result, key=lambda row: (row["group_order"], row["role_order"], row["name"], row["id"]))


def recognition_score_for_role(db: Session, role_id: int, on_date: str) -> Decimal:
    rule = (
        db.query(RecognitionScoreRule)
        .filter(
            RecognitionScoreRule.role_id == role_id,
            RecognitionScoreRule.active.is_(True),
            RecognitionScoreRule.effective_date <= on_date,
        )
        .order_by(RecognitionScoreRule.effective_date.desc(), RecognitionScoreRule.id.desc())
        .first()
    )
    if not rule:
        rule = (
            db.query(RecognitionScoreRule)
            .filter(RecognitionScoreRule.role_id == role_id, RecognitionScoreRule.active.is_(True))
            .order_by(RecognitionScoreRule.effective_date.asc())
            .first()
        )
    if not rule:
        raise HTTPException(400, "该认可人角色尚未配置分值")
    return Decimal(str(rule.score)).quantize(SCORE_UNIT)


def write_audit(
    db: Session,
    operator: Employee | None,
    action: str,
    entity_type: str,
    entity_id=None,
    *,
    before=None,
    after=None,
    reason=None,
    ip_address=None,
) -> None:
    db.add(
        AuditLog(
            operator_id=operator.id if operator else None,
            operator_name=operator.name if operator else None,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            before_json=json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
            after_json=json.dumps(after, ensure_ascii=False, default=str) if after is not None else None,
            reason=reason,
            ip_address=ip_address,
        )
    )


BUSINESS_ATTACHMENT_EFFECTIVE_DATE = "2026-09-01"
BUSINESS_ATTACHMENT_MAX_BYTES = 100 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024


async def _stream_upload_to_path(upload: UploadFile, path: Path, *, max_bytes: int, empty_message: str, label: str) -> tuple[int, str]:
    """Persist a bounded upload without retaining a 100MB request in RAM."""
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("wb") as target:
            while True:
                chunk = await upload.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(400, f"{label}不能超过{max_bytes // 1024 // 1024}MB")
                digest.update(chunk)
                target.write(chunk)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    if not total:
        path.unlink(missing_ok=True)
        raise HTTPException(400, empty_message)
    return total, digest.hexdigest()


async def save_upload(db: Session, upload: UploadFile, uploader_id: int, *, allowed_extensions: set[str], max_bytes=BUSINESS_ATTACHMENT_MAX_BYTES) -> StoredFile:
    extension = Path(upload.filename or "").suffix.lower()
    if extension not in allowed_extensions:
        raise HTTPException(400, f"文件格式不支持，仅允许：{', '.join(sorted(allowed_extensions))}")
    storage_key = f"{uuid4().hex}{extension}"
    path = FILE_DIR / storage_key
    file_size, digest = await _stream_upload_to_path(upload, path, max_bytes=max_bytes, empty_message="文件不能为空", label="文件")
    record = StoredFile(
        storage_key=storage_key,
        original_filename=upload.filename or storage_key,
        extension=extension,
        mime_type=upload.content_type,
        file_size=file_size,
        sha256=digest,
        uploaded_by=uploader_id,
        status="active",
    )
    db.add(record)
    try:
        db.flush()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return record


def detect_image_type(content: bytes) -> tuple[str, str] | None:
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None


async def save_image_upload(db: Session, upload: UploadFile, uploader_id: int, *, max_bytes=BUSINESS_ATTACHMENT_MAX_BYTES) -> StoredFile:
    staging_key = f"{uuid4().hex}.upload"
    staging_path = FILE_DIR / staging_key
    file_size, digest = await _stream_upload_to_path(upload, staging_path, max_bytes=max_bytes, empty_message="认可图片不能为空", label="认可图片")
    with staging_path.open("rb") as source:
        detected = detect_image_type(source.read(16))
    if not detected:
        staging_path.unlink(missing_ok=True)
        raise HTTPException(400, "认可图片格式无效，仅支持JPG、PNG、WebP")
    extension, mime_type = detected
    storage_key = f"{uuid4().hex}{extension}"
    path = FILE_DIR / storage_key
    staging_path.replace(path)
    record = StoredFile(
        storage_key=storage_key,
        original_filename=upload.filename or storage_key,
        extension=extension,
        mime_type=mime_type,
        file_size=file_size,
        sha256=digest,
        uploaded_by=uploader_id,
        status="active",
    )
    db.add(record)
    try:
        db.flush()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return record


def remove_upload_file(file_row: StoredFile | None) -> None:
    if not file_row:
        return
    path = (FILE_DIR / file_row.storage_key).resolve()
    if FILE_DIR.resolve() in path.parents:
        path.unlink(missing_ok=True)


def month_end(month: str) -> str:
    year, month_number = map(int, month.split("-"))
    return f"{year:04d}-{month_number:02d}-{monthrange(year, month_number)[1]:02d}"


def employee_active_on(employee: Employee, on_date: str) -> bool:
    if employee.hired_on and employee.hired_on > on_date:
        return False
    if employee.terminated_on and employee.terminated_on <= on_date:
        return False
    return employee.is_active


def month_bounds(month: str) -> tuple[date, date]:
    start = date.fromisoformat(f"{month}-01")
    return start, start.replace(day=monthrange(start.year, start.month)[1])


def loa_periods_for_month(db: Session, employee_id: int, month: str) -> list[EmployeeLOAPeriod]:
    start, end = month_bounds(month)
    return (
        db.query(EmployeeLOAPeriod)
        .filter(
            EmployeeLOAPeriod.employee_id == employee_id,
            EmployeeLOAPeriod.status != "cancelled",
            EmployeeLOAPeriod.starts_on <= end.isoformat(),
            or_(EmployeeLOAPeriod.ends_on.is_(None), EmployeeLOAPeriod.ends_on >= start.isoformat()),
        )
        .order_by(EmployeeLOAPeriod.starts_on, EmployeeLOAPeriod.id)
        .all()
    )


def full_month_loa(db: Session, employee_id: int, month: str) -> bool:
    start, end = month_bounds(month)
    return any(
        period.starts_on[:7] < month
        and period.starts_on <= start.isoformat()
        and (period.ends_on is None or period.ends_on >= end.isoformat())
        for period in loa_periods_for_month(db, employee_id, month)
    )


def _sick_leave_date_values(rows: list[SickLeaveRecord], month: str) -> dict[date, Decimal]:
    month_start, month_finish = month_bounds(month)
    values: dict[date, Decimal] = {}
    for row in rows:
        current = max(date.fromisoformat(row.leave_start_date), month_start)
        finish = min(date.fromisoformat(row.leave_end_date), month_finish)
        remaining = Decimal(str(row.leave_days))
        while current <= finish and remaining > 0:
            value = min(Decimal("1.0"), remaining)
            values[current] = max(values.get(current, Decimal("0.0")), value)
            remaining -= value
            current += timedelta(days=1)
    return values


def attendance_day_totals(
    db: Session,
    employee_id: int,
    month: str,
    sick_rows: list[SickLeaveRecord] | None = None,
) -> tuple[Decimal, Decimal, bool]:
    if sick_rows is None:
        sick_rows = (
            db.query(SickLeaveRecord)
            .filter(
                SickLeaveRecord.employee_id == employee_id,
                SickLeaveRecord.attendance_month == month,
                SickLeaveRecord.status == "active",
            )
            .all()
        )
    day_values = _sick_leave_date_values(sick_rows, month)
    actual_days = sum(day_values.values(), Decimal("0.0"))
    loa_periods = loa_periods_for_month(db, employee_id, month)
    month_start, month_finish = month_bounds(month)
    is_full_month_loa = any(
        period.starts_on[:7] < month
        and period.starts_on <= month_start.isoformat()
        and (period.ends_on is None or period.ends_on >= month_finish.isoformat())
        for period in loa_periods
    )
    if not is_full_month_loa:
        for period in loa_periods:
            current = max(date.fromisoformat(period.starts_on), month_start)
            finish = min(date.fromisoformat(period.ends_on) if period.ends_on else month_finish, month_finish)
            while current <= finish:
                if current.weekday() < 5:
                    day_values[current] = Decimal("1.0")
                current += timedelta(days=1)
    charged_days = min(Decimal("22.0"), sum(day_values.values(), Decimal("0.0")))
    return actual_days, charged_days, is_full_month_loa


def sick_leave_score_deduction(charged_days: Decimal, daily_deduction: Decimal) -> Decimal:
    whole_days = int(charged_days)
    half_day = charged_days - Decimal(whole_days)
    deduction = daily_deduction * whole_days
    if half_day:
        deduction += HALF_DAY_DEDUCTION
    return deduction.quantize(SCORE_UNIT, rounding=ROUND_HALF_UP)


def recalculate_attendance(db: Session, employee: Employee, month: str) -> AttendanceMonthlyScore:
    end_date = month_end(month)
    end_role = role_at(db, employee.id, end_date)
    eligible = bool(end_role and end_role.code in FRONTLINE_CODES and employee_active_on(employee, end_date))
    rule = (
        db.query(AttendanceRule)
        .filter(AttendanceRule.active.is_(True), AttendanceRule.effective_date <= end_date)
        .order_by(AttendanceRule.effective_date.desc(), AttendanceRule.id.desc())
        .first()
    ) or db.query(AttendanceRule).filter(AttendanceRule.active.is_(True)).first()
    if not rule:
        raise HTTPException(500, "全勤规则未配置")
    sick_rows = (
        db.query(SickLeaveRecord)
        .filter(
            SickLeaveRecord.employee_id == employee.id,
            SickLeaveRecord.attendance_month == month,
            SickLeaveRecord.status == "active",
        )
        .all()
    )
    actual_days, charged_days, is_full_month_loa = attendance_day_totals(db, employee.id, month, sick_rows)
    eligible = eligible and not is_full_month_loa
    base = Decimal(str(rule.base_score)).quantize(SCORE_UNIT)
    bonus = Decimal(str(rule.perfect_bonus)).quantize(SCORE_UNIT)
    daily = Decimal(str(rule.sick_day_deduction)).quantize(SCORE_UNIT)
    threshold = Decimal(str(rule.zero_threshold)).quantize(SCORE_UNIT)
    if not eligible:
        final = Decimal("0.00")
        total_deduction = Decimal("0.00")
    elif charged_days == 0:
        final = base + bonus
        total_deduction = Decimal("0.00")
    else:
        leave_deduction = sick_leave_score_deduction(charged_days, daily)
        total_deduction = bonus + leave_deduction
        final = max(Decimal("0.00"), base - leave_deduction)
        if final <= threshold:
            final = Decimal("0.00")
    row = (
        db.query(AttendanceMonthlyScore)
        .filter(AttendanceMonthlyScore.employee_id == employee.id, AttendanceMonthlyScore.attendance_month == month)
        .first()
    )
    if not row:
        row = AttendanceMonthlyScore(employee_id=employee.id, attendance_month=month)
        db.add(row)
    next_values = {
        "month_end_role_id": end_role.id if end_role else None,
        "eligible": eligible,
        "actual_sick_days": actual_days,
        "charged_sick_days": charged_days,
        "base_score": base if eligible else Decimal("0.00"),
        "perfect_bonus": bonus if eligible else Decimal("0.00"),
        "sick_deduction": total_deduction.quantize(SCORE_UNIT),
        "final_score": final.quantize(SCORE_UNIT, rounding=ROUND_HALF_UP),
        "rule_id": rule.id,
        "calculation_version": 2,
    }
    # Skip the write (and flush) when nothing changed so read paths such as
    # the dashboard do not turn repeated refreshes into database writes.
    if row.id is None or any(getattr(row, name) != value for name, value in next_values.items()):
        for name, value in next_values.items():
            setattr(row, name, value)
        row.calculated_at = datetime.now()
        db.flush()
    return row


def ensure_month_attendance(
    db: Session,
    month: str,
    employee_ids: list[int] | set[int] | None = None,
) -> dict[int, AttendanceMonthlyScore]:
    """Create missing monthly attendance rows in batches and leave current rows untouched.

    Sick-leave and employee-change workflows recalculate their affected employee directly,
    so statistics reads do not need to rewrite every employee on every request.
    """
    employee_query = db.query(Employee)
    if employee_ids is not None:
        ids = list(dict.fromkeys(int(employee_id) for employee_id in employee_ids))
        if not ids:
            return {}
        employee_query = employee_query.filter(Employee.id.in_(ids))
    employees = employee_query.all()
    if not employees:
        return {}

    ids = [employee.id for employee in employees]
    existing_rows = (
        db.query(AttendanceMonthlyScore)
        .filter(
            AttendanceMonthlyScore.attendance_month == month,
            AttendanceMonthlyScore.employee_id.in_(ids),
        )
        .all()
    )
    records = {row.employee_id: row for row in existing_rows}
    missing = [employee for employee in employees if employee.id not in records]
    if not missing:
        return records

    end_date = month_end(month)
    missing_ids = [employee.id for employee in missing]
    end_roles = roles_at(db, missing_ids, end_date)
    rule = (
        db.query(AttendanceRule)
        .filter(AttendanceRule.active.is_(True), AttendanceRule.effective_date <= end_date)
        .order_by(AttendanceRule.effective_date.desc(), AttendanceRule.id.desc())
        .first()
    ) or db.query(AttendanceRule).filter(AttendanceRule.active.is_(True)).first()
    if not rule:
        raise HTTPException(500, "全勤规则未配置")

    sick_rows = (
        db.query(SickLeaveRecord)
        .filter(
            SickLeaveRecord.employee_id.in_(missing_ids),
            SickLeaveRecord.attendance_month == month,
            SickLeaveRecord.status == "active",
        )
        .all()
    )
    actual_days: dict[int, Decimal] = {employee_id: Decimal("0.0") for employee_id in missing_ids}
    charged_days: dict[int, Decimal] = {employee_id: Decimal("0.0") for employee_id in missing_ids}
    base = Decimal(str(rule.base_score)).quantize(SCORE_UNIT)
    bonus = Decimal(str(rule.perfect_bonus)).quantize(SCORE_UNIT)
    daily = Decimal(str(rule.sick_day_deduction)).quantize(SCORE_UNIT)
    threshold = Decimal(str(rule.zero_threshold)).quantize(SCORE_UNIT)
    calculated_at = datetime.now()
    pending_rows: list[dict] = []
    for employee in missing:
        end_role = end_roles.get(employee.id)
        if not end_role or end_role.code not in FRONTLINE_CODES:
            continue
        employee_actual_days, employee_charged_days, is_full_month_loa = attendance_day_totals(
            db,
            employee.id,
            month,
            [row for row in sick_rows if row.employee_id == employee.id],
        )
        actual_days[employee.id] = employee_actual_days
        charged_days[employee.id] = employee_charged_days
        eligible = employee_active_on(employee, end_date) and not is_full_month_loa
        if not eligible:
            final = Decimal("0.00")
            total_deduction = Decimal("0.00")
        elif employee_charged_days == 0:
            final = base + bonus
            total_deduction = Decimal("0.00")
        else:
            leave_deduction = sick_leave_score_deduction(employee_charged_days, daily)
            total_deduction = bonus + leave_deduction
            final = max(Decimal("0.00"), base - leave_deduction)
            if final <= threshold:
                final = Decimal("0.00")
        pending_rows.append(
            {
                "employee_id": employee.id,
                "attendance_month": month,
                "month_end_role_id": end_role.id,
                "eligible": eligible,
                "actual_sick_days": float(actual_days[employee.id]),
                "charged_sick_days": float(employee_charged_days),
                "base_score": float(base if eligible else Decimal("0.00")),
                "perfect_bonus": float(bonus if eligible else Decimal("0.00")),
                "sick_deduction": float(total_deduction.quantize(SCORE_UNIT)),
                "final_score": float(final.quantize(SCORE_UNIT, rounding=ROUND_HALF_UP)),
                "rule_id": rule.id,
                "calculation_version": 2,
                "calculated_at": calculated_at,
            }
        )
    if pending_rows:
        db.execute(
            text(
                """
                INSERT OR IGNORE INTO attendance_monthly_scores (
                    employee_id, attendance_month, month_end_role_id, eligible,
                    actual_sick_days, charged_sick_days, base_score, perfect_bonus,
                    sick_deduction, final_score, rule_id, calculation_version, calculated_at
                ) VALUES (
                    :employee_id, :attendance_month, :month_end_role_id, :eligible,
                    :actual_sick_days, :charged_sick_days, :base_score, :perfect_bonus,
                    :sick_deduction, :final_score, :rule_id, :calculation_version, :calculated_at
                )
                """
            ),
            pending_rows,
        )
        created_rows = (
            db.query(AttendanceMonthlyScore)
            .filter(
                AttendanceMonthlyScore.attendance_month == month,
                AttendanceMonthlyScore.employee_id.in_([row["employee_id"] for row in pending_rows]),
            )
            .all()
        )
        records.update({row.employee_id: row for row in created_rows})
    return records


def create_alert(db: Session, alert_type: str, dedupe_key: str, message: str, *, employee_id=None, group_id=None, due_date=None):
    if db.query(SystemAlert).filter_by(alert_type=alert_type, dedupe_key=dedupe_key).first():
        return
    db.add(
        SystemAlert(
            alert_type=alert_type,
            dedupe_key=dedupe_key,
            employee_id=employee_id,
            group_id=group_id,
            due_date=due_date,
            message=message,
            status="open",
        )
    )


def process_role_expirations(db: Session) -> None:
    today = date.today()
    today_text = today.isoformat()
    temporary_rows = db.query(EmployeeRoleAssignment).filter(EmployeeRoleAssignment.assignment_type == "temporary").all()
    for assignment in temporary_rows:
        if assignment.ends_on:
            end = date.fromisoformat(assignment.ends_on)
            days = (end - today).days
            if days in (30, 7, 1):
                create_alert(
                    db,
                    "temporary_role_expiring",
                    f"{assignment.id}:{days}",
                    f"{assignment.employee.name}的{assignment.role.name}任期将在{days}天后结束",
                    employee_id=assignment.employee_id,
                    due_date=assignment.ends_on,
                )
            if end < today and assignment.status != "expired":
                assignment.status = "expired"
                return_start = (end + timedelta(days=1)).isoformat()
                covering = (
                    db.query(EmployeeRoleAssignment)
                    .filter(
                        EmployeeRoleAssignment.employee_id == assignment.employee_id,
                        EmployeeRoleAssignment.starts_on <= today_text,
                        or_(EmployeeRoleAssignment.ends_on.is_(None), EmployeeRoleAssignment.ends_on >= today_text),
                        EmployeeRoleAssignment.status != "cancelled",
                    )
                    .first()
                )
                if not covering and assignment.return_role_id:
                    db.add(
                        EmployeeRoleAssignment(
                            employee_id=assignment.employee_id,
                            role_id=assignment.return_role_id,
                            starts_on=return_start,
                            assignment_type="permanent",
                            status="active",
                            reason="临时角色到期自动恢复",
                        )
                    )
    db.flush()
    for leader_assignment in db.query(GroupLeaderAssignment).filter(GroupLeaderAssignment.status == "active").all():
        role = role_at(db, leader_assignment.leader_employee_id, today_text)
        if role and role.code in LEADER_CODES:
            continue
        leader_assignment.status = "ended"
        leader_assignment.ends_on = today_text
        group = leader_assignment.group
        group.status = "pending_takeover"
        db.query(RecognitionRecord).filter(
            RecognitionRecord.assigned_reviewer_id == leader_assignment.leader_employee_id,
            RecognitionRecord.status == "pending",
        ).update({RecognitionRecord.assigned_reviewer_id: None}, synchronize_session=False)
        create_alert(
            db,
            "group_pending_takeover",
            str(group.id),
            f"工作组{group.name}的原组长已失去带组资格，请整组移交",
            employee_id=leader_assignment.leader_employee_id,
            group_id=group.id,
        )
    db.commit()
