"""Batched directory, group labels and employee search queries."""
from __future__ import annotations

from app.access_policy import REGULAR_ACCOUNT_ROLE_CODES
from app.role_constants import SCORED_BASE_CODES
from app.services.identity import base_roles_at, duties_at_bulk, identity_labels, roles_at
from app.services.organization import ACTING_LEADER_FIRST, group_leader_label
from app.v2_models import Attraction, Employee, EmployeeActingDuty, EmployeeLOAPeriod, GroupLeaderAssignment, GroupMembership, Role, UserAccount, WorkGroup
from datetime import date, timedelta
from sqlalchemy import or_, text
from sqlalchemy.orm import Session


def group_display_metadata_bulk(db: Session, group_ids: list[int] | set[int], on_date: str | None = None) -> dict[int, dict]:
    """Group names with their 主管 and 代理主管 on a date.

    Group names are fixed ("热力追踪A组"); leaders are reported separately.
    `label` reads "主管 X · 代理主管 Y" (only the ones set).
    """
    ids = list(dict.fromkeys(int(group_id) for group_id in group_ids if group_id))
    if not ids:
        return {}
    value = on_date or date.today().isoformat()
    groups = {row.id: row for row in db.query(WorkGroup).filter(WorkGroup.id.in_(ids)).all()}
    typed_leaders: dict[tuple[int, str], Employee] = {}
    for assignment, leader in (
        db.query(GroupLeaderAssignment, Employee)
        .join(Employee, Employee.id == GroupLeaderAssignment.leader_employee_id)
        .filter(
            GroupLeaderAssignment.group_id.in_(ids),
            GroupLeaderAssignment.status == "active",
            GroupLeaderAssignment.starts_on <= value,
            or_(GroupLeaderAssignment.ends_on.is_(None), GroupLeaderAssignment.ends_on >= value),
        )
        .order_by(GroupLeaderAssignment.starts_on.desc(), GroupLeaderAssignment.id.desc())
    ):
        typed_leaders.setdefault((assignment.group_id, assignment.leader_type), leader)
    result: dict[int, dict] = {}
    for group_id in ids:
        group = groups.get(group_id)
        formal_leader = typed_leaders.get((group_id, "formal"))
        acting_leader = typed_leaders.get((group_id, "acting"))
        formal_name = formal_leader.name if formal_leader else ""
        acting_name = acting_leader.name if acting_leader else ""
        result[group_id] = {
            "name": group.name if group else "",
            "code": group.code if group else None,
            "formal_leader_id": formal_leader.id if formal_leader else None,
            "formal_leader_name": formal_name,
            "acting_leader_id": acting_leader.id if acting_leader else None,
            "acting_leader_name": acting_name,
            "label": group_leader_label(formal_name, acting_name),
        }
    return result


def search_employee_targets(
    db: Session,
    *,
    keyword: str = "",
    attraction_id: int | None = None,
    limit: int = 30,
    role_codes: tuple[str, ...] = ("CM", "TR"),
) -> dict:
    """Return active, enabled targets of the given base roles in one bounded SQLite query."""
    if not role_codes or not set(role_codes) <= SCORED_BASE_CODES:
        raise ValueError("unsupported target role codes")
    role_filter = ", ".join(f"'{code}'" for code in role_codes)
    today_value = date.today().isoformat()
    normalized = str(keyword or "").strip()
    escaped = normalized.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    params = {
        "today": today_value,
        "keyword": normalized,
        "pattern": f"%{escaped}%",
        "prefix": f"{escaped}%",
        "attraction_id": attraction_id,
        "limit": max(1, min(int(limit or 30), 500)),
    }
    rows = db.execute(
        text(
            f"""
            WITH ranked_roles AS (
                SELECT era.employee_id, era.role_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY era.employee_id
                           ORDER BY era.starts_on DESC, era.id DESC
                       ) AS row_number
                FROM employee_role_assignments era
                WHERE era.status != 'cancelled'
                  AND era.starts_on <= :today
                  AND (era.ends_on IS NULL OR era.ends_on >= :today)
            ), ranked_memberships AS (
                SELECT gm.employee_id, gm.group_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY gm.employee_id
                           ORDER BY gm.starts_on DESC, gm.id DESC
                       ) AS row_number
                FROM group_memberships gm
                WHERE gm.status = 'active'
                  AND gm.starts_on <= :today
                  AND (gm.ends_on IS NULL OR gm.ends_on >= :today)
            )
            SELECT e.id, e.employee_no, e.name,
                   r.code AS role_code, r.name AS role_name,
                   e.attraction_id, COALESCE(a.name, '') AS attraction_name,
                   wg.id AS group_id, COALESCE(wg.name, '') AS group_name,
                   COUNT(*) OVER() AS total_count
            FROM employees e
            JOIN ranked_roles rr ON rr.employee_id = e.id AND rr.row_number = 1
            JOIN roles r ON r.id = rr.role_id AND r.active = 1 AND r.code IN ({role_filter})
            JOIN user_accounts ua ON ua.employee_id = e.id AND ua.enabled = 1
            LEFT JOIN attractions a ON a.id = e.attraction_id
            LEFT JOIN ranked_memberships rm ON rm.employee_id = e.id AND rm.row_number = 1
            LEFT JOIN work_groups wg ON wg.id = rm.group_id
            WHERE e.is_active = 1
              AND (:attraction_id IS NULL OR e.attraction_id = :attraction_id)
              AND (
                  :keyword = ''
                  OR e.name LIKE :pattern ESCAPE '!'
                  OR e.employee_no LIKE :pattern ESCAPE '!'
              )
            ORDER BY
                CASE
                    WHEN e.employee_no = :keyword THEN 0
                    WHEN e.name = :keyword THEN 1
                    WHEN e.employee_no LIKE :prefix ESCAPE '!' THEN 2
                    WHEN e.name LIKE :prefix ESCAPE '!' THEN 3
                    ELSE 4
                END,
                e.name ASC, e.employee_no ASC
            LIMIT :limit
            """
        ),
        params,
    ).mappings().all()
    total = int(rows[0]["total_count"]) if rows else 0
    group_display = group_display_metadata_bulk(db, {row["group_id"] for row in rows if row["group_id"]}, today_value)
    return {
        "items": [
            {
                "id": row["id"],
                "employee_no": row["employee_no"],
                "name": row["name"],
                "role_code": row["role_code"],
                "role_name": row["role_name"],
                "attraction_id": row["attraction_id"],
                "attraction_name": row["attraction_name"],
                "group_id": row["group_id"],
                "group_name": group_display.get(row["group_id"], {}).get("name", row["group_name"]),
            }
            for row in rows
        ],
        "total": total,
        "limit": params["limit"],
    }


def login_account_archive_state(employee: Employee, account: UserAccount | None, role: Role | None) -> dict:
    """Return non-sensitive eligibility for removing only a login account.

    The employee primary key and every business row remain untouched.  Both
    the directory and the write endpoint use this same policy so the seven-day
    rule cannot be bypassed by calling the API directly.
    """
    result = {"deleted": bool(employee.account_deleted_at), "eligible": False, "reason": "", "eligible_on": ""}
    if employee.account_deleted_at:
        result["reason"] = "账号已删除，业务档案已保留"
        return result
    if not account:
        result["reason"] = "该员工尚未开通登录账号"
        return result
    if not role or role.code not in REGULAR_ACCOUNT_ROLE_CODES:
        result["reason"] = "HR和最高管理员账号不支持在此删除"
        return result
    reference_day: date | None = None
    if not employee.is_active:
        try:
            reference_day = date.fromisoformat(str(employee.terminated_on or ""))
        except ValueError:
            result["reason"] = "离职日期未记录，暂不能删除登录账号"
            return result
    elif not account.enabled:
        if not account.disabled_at:
            result["reason"] = "停用时间未记录，暂不能删除登录账号"
            return result
        reference_day = account.disabled_at.date()
    else:
        result["reason"] = "需先离职或停用账号"
        return result
    eligible_on = reference_day + timedelta(days=7)
    result["eligible_on"] = eligible_on.isoformat()
    if date.today() < eligible_on:
        result["reason"] = f"{eligible_on.isoformat()} 起可删除登录账号"
        return result
    result["eligible"] = True
    result["reason"] = "可删除登录账号；员工及所有业务档案会保留"
    return result


def employee_payloads(db: Session, employees: list[Employee], on_date: str | None = None) -> dict[int, dict]:
    """Build the HR employee directory with a bounded set of batch queries."""
    if not employees:
        return {}
    value = on_date or date.today().isoformat()
    employee_ids = [employee.id for employee in employees]
    role_map = roles_at(db, employee_ids, value)
    base_role_map = base_roles_at(db, employee_ids, value)
    duty_map = duties_at_bulk(db, employee_ids, value)
    duty_ends = {
        row.employee_id: row.ends_on or ""
        for row in db.query(EmployeeActingDuty).filter(
            EmployeeActingDuty.employee_id.in_(employee_ids),
            EmployeeActingDuty.status != "cancelled",
            EmployeeActingDuty.starts_on <= value,
            or_(EmployeeActingDuty.ends_on.is_(None), EmployeeActingDuty.ends_on >= value),
        )
    }
    labels = identity_labels(db, employee_ids, value)
    accounts = {
        account.employee_id: account
        for account in db.query(UserAccount).filter(UserAccount.employee_id.in_(employee_ids)).all()
    }
    loa_periods = (
        db.query(EmployeeLOAPeriod)
        .filter(
            EmployeeLOAPeriod.employee_id.in_(employee_ids),
            EmployeeLOAPeriod.status != "cancelled",
            EmployeeLOAPeriod.starts_on <= value,
            or_(EmployeeLOAPeriod.ends_on.is_(None), EmployeeLOAPeriod.ends_on >= value),
        )
        .order_by(EmployeeLOAPeriod.employee_id, EmployeeLOAPeriod.starts_on.desc(), EmployeeLOAPeriod.id.desc())
        .all()
    )
    loa_by_employee: dict[int, EmployeeLOAPeriod] = {}
    for period in loa_periods:
        loa_by_employee.setdefault(period.employee_id, period)
    attraction_ids = {employee.attraction_id for employee in employees if employee.attraction_id}
    attractions = {
        attraction.id: attraction
        for attraction in db.query(Attraction).filter(Attraction.id.in_(attraction_ids)).all()
    } if attraction_ids else {}
    memberships = (
        db.query(GroupMembership)
        .filter(
            GroupMembership.employee_id.in_(employee_ids),
            GroupMembership.status == "active",
            GroupMembership.starts_on <= value,
            or_(GroupMembership.ends_on.is_(None), GroupMembership.ends_on >= value),
        )
        .order_by(GroupMembership.employee_id, GroupMembership.starts_on.desc(), GroupMembership.id.desc())
        .all()
    )
    membership_by_employee: dict[int, GroupMembership] = {}
    for membership in memberships:
        membership_by_employee.setdefault(membership.employee_id, membership)
    group_ids = {membership.group_id for membership in membership_by_employee.values()}
    groups = {
        group.id: group
        for group in db.query(WorkGroup).filter(WorkGroup.id.in_(group_ids)).all()
    } if group_ids else {}
    leader_assignments = (
        db.query(GroupLeaderAssignment)
        .filter(
            GroupLeaderAssignment.group_id.in_(group_ids),
            GroupLeaderAssignment.status == "active",
            GroupLeaderAssignment.starts_on <= value,
            or_(GroupLeaderAssignment.ends_on.is_(None), GroupLeaderAssignment.ends_on >= value),
        )
        .order_by(GroupLeaderAssignment.group_id, ACTING_LEADER_FIRST, GroupLeaderAssignment.starts_on.desc(), GroupLeaderAssignment.id.desc())
        .all()
    ) if group_ids else []
    leader_assignment_by_group: dict[int, GroupLeaderAssignment] = {}
    for assignment in leader_assignments:
        leader_assignment_by_group.setdefault(assignment.group_id, assignment)
    leader_ids = {assignment.leader_employee_id for assignment in leader_assignment_by_group.values()}
    leaders = {
        leader.id: leader
        for leader in db.query(Employee).filter(Employee.id.in_(leader_ids)).all()
    } if leader_ids else {}
    group_display = group_display_metadata_bulk(db, group_ids, value)
    result: dict[int, dict] = {}
    for employee in employees:
        role = role_map.get(employee.id)
        account = accounts.get(employee.id)
        membership = membership_by_employee.get(employee.id)
        group = groups.get(membership.group_id) if membership else None
        leader_assignment = leader_assignment_by_group.get(group.id) if group else None
        leader = leaders.get(leader_assignment.leader_employee_id) if leader_assignment else None
        display = group_display.get(group.id, {}) if group else {}
        attraction = attractions.get(employee.attraction_id)
        loa_period = loa_by_employee.get(employee.id)
        archive_state = login_account_archive_state(employee, account, role)
        result[employee.id] = {
        "id": employee.id,
        "employee_no": employee.employee_no,
        "name": employee.name,
        "role_code": role.code if role else "",
        "role_name": role.name if role else "未配置",
        "base_role_code": base_role_map[employee.id].code if base_role_map.get(employee.id) else "",
        "base_role_name": base_role_map[employee.id].name if base_role_map.get(employee.id) else "未配置",
        "duty_role_code": duty_map[employee.id][0].code if duty_map.get(employee.id) else "",
        "duty_role_name": duty_map[employee.id][0].name if duty_map.get(employee.id) else "",
        "duty_ends_on": duty_ends.get(employee.id, ""),
        "role_label": labels.get(employee.id, "未配置"),
        "attraction_id": employee.attraction_id,
        "attraction_name": attraction.name if attraction else "",
        "group_id": group.id if group else None,
        "group_name": display.get("name", group.name if group else ""),
        "group_code": group.code if group else None,
        "group_leader_label": display.get("label", ""),
        "leader_id": leader.id if leader else None,
        "leader_name": leader.name if leader else "",
        "formal_leader_name": display.get("formal_leader_name", ""),
        "acting_leader_name": display.get("acting_leader_name", ""),
        "is_active": employee.is_active,
        "employment_status": "loa" if employee.is_active and loa_period else "active" if employee.is_active else "terminated",
        "loa_start_date": loa_period.starts_on if loa_period else "",
        "account_enabled": bool(account and account.enabled),
        "login_account": account.login_account if account else "",
        "account_deleted_at": employee.account_deleted_at.strftime("%Y-%m-%d %H:%M:%S") if employee.account_deleted_at else "",
        "account_deleted_by_name": employee.account_deleted_by_name or "",
        "account_deletion_eligible": archive_state["eligible"],
        "account_deletion_reason": archive_state["reason"],
        "account_deletion_eligible_on": archive_state["eligible_on"],
        }
    return result
