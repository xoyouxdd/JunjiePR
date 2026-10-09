"""One-shot legacy identity and group migrations; uncertain rows remain HR alerts."""
from __future__ import annotations

from datetime import date

from sqlalchemy import text


ACTING_DUTY_BASE_CODES = {"TA_SUPERVISOR": {"CM", "TR"}, "TA_GSM": {"SUPERVISOR"}}


def ensure_acting_duty_columns_and_migrate(db) -> None:
    required_columns = {
        "recognition_records": {
            "operator_base_role_code_snapshot": "VARCHAR(30)",
            "recognizer_base_role_code_snapshot": "VARCHAR(30)",
            "employee_acting_duty_code": "VARCHAR(30)",
        },
        "recognition_reviews": {
            "reviewer_role_code": "VARCHAR(30)",
            "reviewer_base_role_code": "VARCHAR(30)",
        },
        "deduction_records": {
            "submitter_role_code_snapshot": "VARCHAR(30)",
            "submitter_base_role_code_snapshot": "VARCHAR(30)",
            "employee_acting_duty_code": "VARCHAR(30)",
        },
        "employee_month_organization_snapshots": {
            "base_role_code": "VARCHAR(30)",
            "scoring_category": "VARCHAR(20)",
            "acting_duty_code": "VARCHAR(30)",
            "acting_days": "INTEGER",
        },
        "audit_logs": {"attraction_id": "INTEGER"},
    }
    for table_name, columns in required_columns.items():
        existing = {row[1] for row in db.execute(text(f"PRAGMA table_info({table_name})"))}
        for name, definition in columns.items():
            if name not in existing:
                db.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_recognition_records_employee_acting_duty_code ON recognition_records (employee_acting_duty_code)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_logs_attraction_id ON audit_logs (attraction_id)"))
    db.commit()
    migrate_legacy_duty_assignments(db)


def ensure_group_leader_types(db) -> None:
    """Label current group leaders: a base 主管 or above is the formal leader
    (原组长); anyone else running a group (an acting TA主管) is its acting leader."""
    from app.v2_models import GroupLeaderAssignment
    from app.v2_services import formal_leader_eligible

    existing = {row[1] for row in db.execute(text("PRAGMA table_info(group_leader_assignments)"))}
    if "leader_type" not in existing:
        db.execute(text("ALTER TABLE group_leader_assignments ADD COLUMN leader_type VARCHAR(20) NOT NULL DEFAULT 'formal'"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_group_leader_assignments_leader_type ON group_leader_assignments (leader_type)"))
    db.commit()
    today = date.today().isoformat()
    for row in db.query(GroupLeaderAssignment).filter(GroupLeaderAssignment.status == "active").all():
        if not formal_leader_eligible(db, row.leader_employee_id, today):
            row.leader_type = "acting"
    db.commit()


def legacy_group_display_name(db, group, today: str) -> str:
    """The name HR saw before fixed group names: '<leader>工作组' after the
    leader (主管 first, else whoever runs it), or the stored name."""
    from app.v2_services import active_group_leader, group_leader_of_type

    leader = group_leader_of_type(db, group.id, "formal", today) or active_group_leader(db, group.id, today)
    return f"{leader.leader.name}工作组" if leader else group.name


def group_name_sort_key(name: str) -> bytes:
    # GB2312 orders its common characters by pinyin.
    return name.encode("gbk", errors="replace")


def ensure_group_codes(db) -> None:
    """Give every open group a fixed name "<circle><letter>组".

    Letters follow the pinyin order of the names HR saw before.  Each rename
    goes to the audit log; a 主管 or 代理主管 who holds more than one group,
    or a 主管 whose base identity is not 主管, becomes an HR alert.
    """
    from app.v2_models import Attraction, GroupLeaderAssignment, WorkGroup
    from app.v2_services import base_role_at, create_alert, group_code_for_index, group_code_index, group_display_name, next_group_code, write_audit

    existing = {row[1] for row in db.execute(text("PRAGMA table_info(work_groups)"))}
    if "code" not in existing:
        db.execute(text("ALTER TABLE work_groups ADD COLUMN code VARCHAR(8)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_work_groups_code ON work_groups (code)"))
    db.commit()
    today = date.today().isoformat()
    circles = {row.id: row.name for row in db.query(Attraction).all()}
    open_groups = db.query(WorkGroup).filter(WorkGroup.status != "closed", WorkGroup.code.is_(None)).all()
    by_circle: dict[int, list[tuple[str, WorkGroup]]] = {}
    for group in open_groups:
        by_circle.setdefault(group.attraction_id, []).append((legacy_group_display_name(db, group, today), group))
    for attraction_id, rows in by_circle.items():
        rows.sort(key=lambda item: (group_name_sort_key(item[0]), item[1].id))
        first = group_code_index(next_group_code(db, attraction_id))
        for index, (old_name, group) in enumerate(rows, start=first):
            stored_name = group.name
            group.code = group_code_for_index(index)
            group.name = group_display_name(circles.get(attraction_id, ""), group.code)
            group.revision += 1
            db.flush()
            write_audit(db, None, "小组统一命名", "work_group", group.id, before={"name": old_name, "stored_name": stored_name}, after={"name": group.name, "code": group.code}, reason="2026-10 小组改为景点圈+字母命名")
    db.flush()

    active_rows = db.query(GroupLeaderAssignment).filter(GroupLeaderAssignment.status == "active").all()
    held: dict[tuple[int, str], list[GroupLeaderAssignment]] = {}
    for row in active_rows:
        if row.group.status == "closed":
            continue
        held.setdefault((row.leader_employee_id, row.leader_type), []).append(row)
    for (employee_id, leader_type), rows in held.items():
        label = "主管" if leader_type == "formal" else "代理主管"
        if len(rows) > 1:
            names = "、".join(sorted(row.group.name for row in rows))
            create_alert(
                db,
                "group_structure",
                f"multiple:{leader_type}:{employee_id}",
                f"{rows[0].leader.name}同时是{names}的{label}，一人只能负责一个小组，请在小组管理中调整",
                employee_id=employee_id,
            )
        if leader_type == "formal":
            base = base_role_at(db, employee_id, today)
            if not base or base.code != "SUPERVISOR":
                for row in rows:
                    create_alert(
                        db,
                        "group_structure",
                        f"formal_base:{row.id}",
                        f"{row.group.name}的主管{row.leader.name}本职不是主管，请在小组管理中更换",
                        employee_id=employee_id,
                        group_id=row.group_id,
                    )
    db.commit()


def resequence_all_group_codes(db) -> None:
    """Re-letter every circle's open groups from A without gaps (one-off)."""
    from app.v2_models import WorkGroup
    from app.v2_services import resequence_group_codes

    for (attraction_id,) in db.query(WorkGroup.attraction_id).distinct().all():
        resequence_group_codes(db, attraction_id, None, "2026-10 小组从A开始重新排列字母")
    db.commit()


def migrate_legacy_duty_assignments(db) -> list[dict]:
    """Split legacy TA主管/TA GSM role rows into base identity + acting duty.

    A row is converted only when its base identity is certain (its return role,
    else the identity held right before it, else the first one after it).  A current TA主管 also rejoins the
    group they belonged to before the duty when that group is unambiguous.
    Everything uncertain is left untouched and listed as an HR alert.
    """
    from app.v2_models import (
        Employee,
        EmployeeActingDuty,
        EmployeeRoleAssignment,
        GroupLeaderAssignment,
        GroupMembership,
        Role,
        WorkGroup,
    )
    from app.v2_services import create_alert

    roles = {role.code: role for role in db.query(Role).all()}
    duty_role_ids = {roles[code].id: code for code in ACTING_DUTY_BASE_CODES if code in roles}
    if not duty_role_ids:
        return []
    today = date.today().isoformat()
    unresolved: list[dict] = []
    legacy_rows = (
        db.query(EmployeeRoleAssignment)
        .filter(EmployeeRoleAssignment.role_id.in_(duty_role_ids), EmployeeRoleAssignment.status != "cancelled")
        .order_by(EmployeeRoleAssignment.employee_id, EmployeeRoleAssignment.starts_on, EmployeeRoleAssignment.id)
        .all()
    )
    for row in legacy_rows:
        duty_code = duty_role_ids[row.role_id]
        allowed = ACTING_DUTY_BASE_CODES[duty_code]
        base = row.return_role if row.return_role and row.return_role.code in allowed else None
        if base is None:
            previous = (
                db.query(EmployeeRoleAssignment)
                .filter(
                    EmployeeRoleAssignment.employee_id == row.employee_id,
                    EmployeeRoleAssignment.id != row.id,
                    EmployeeRoleAssignment.status != "cancelled",
                    EmployeeRoleAssignment.starts_on <= row.starts_on,
                    EmployeeRoleAssignment.role_id.notin_(duty_role_ids),
                )
                .order_by(EmployeeRoleAssignment.starts_on.desc(), EmployeeRoleAssignment.id.desc())
                .first()
            )
            base = previous.role if previous and previous.role.code in allowed else None
        if base is None:
            # Rows imported on go-live day have no earlier identity; the base
            # identity the employee returned to afterwards is the best evidence.
            following = (
                db.query(EmployeeRoleAssignment)
                .filter(
                    EmployeeRoleAssignment.employee_id == row.employee_id,
                    EmployeeRoleAssignment.id != row.id,
                    EmployeeRoleAssignment.status != "cancelled",
                    EmployeeRoleAssignment.starts_on >= row.starts_on,
                    EmployeeRoleAssignment.role_id.notin_(duty_role_ids),
                )
                .order_by(EmployeeRoleAssignment.starts_on.asc(), EmployeeRoleAssignment.id.asc())
                .first()
            )
            base = following.role if following and following.role.code in allowed else None
        if base is None:
            unresolved.append({"employee_id": row.employee_id, "reason": f"无法确定代理{roles[duty_code].name}前的本职身份"})
            continue
        db.add(
            EmployeeActingDuty(
                employee_id=row.employee_id,
                role_id=row.role_id,
                starts_on=row.starts_on,
                ends_on=row.ends_on,
                status="ended" if row.ends_on and row.ends_on < today else "active",
                reason=f"由旧角色记录#{row.id}迁移：{row.reason or ''}".strip(),
                created_by=row.created_by,
            )
        )
        row.role_id = base.id
        row.return_role_id = None
        row.ends_on = None
        row.assignment_type = "permanent"
        row.reason = f"{row.reason or ''}（代理职务已拆分，本职{base.name}）".strip()
    db.flush()

    # Rejoin the pre-duty group for current TA主管 who lost their membership.
    current_duties = (
        db.query(EmployeeActingDuty)
        .filter(
            EmployeeActingDuty.role_id == roles["TA_SUPERVISOR"].id,
            EmployeeActingDuty.status == "active",
            EmployeeActingDuty.starts_on <= today,
            (EmployeeActingDuty.ends_on.is_(None)) | (EmployeeActingDuty.ends_on >= today),
        )
        .all()
        if "TA_SUPERVISOR" in roles
        else []
    )
    for duty in current_duties:
        employee = db.get(Employee, duty.employee_id)
        if not employee or not employee.is_active:
            continue
        has_current = (
            db.query(GroupMembership.id)
            .filter(
                GroupMembership.employee_id == employee.id,
                GroupMembership.status == "active",
                GroupMembership.starts_on <= today,
                (GroupMembership.ends_on.is_(None)) | (GroupMembership.ends_on >= today),
            )
            .first()
        )
        if has_current:
            continue
        led_group_ids = {
            group_id
            for (group_id,) in db.query(GroupLeaderAssignment.group_id).filter(
                GroupLeaderAssignment.leader_employee_id == employee.id,
                GroupLeaderAssignment.status == "active",
            )
        }
        previous = (
            db.query(GroupMembership)
            .filter(GroupMembership.employee_id == employee.id, GroupMembership.status == "ended", GroupMembership.ends_on.isnot(None))
            .order_by(GroupMembership.ends_on.desc(), GroupMembership.id.desc())
            .all()
        )
        latest = [item for item in previous if item.ends_on == previous[0].ends_on] if previous else []
        candidate_ids = {item.group_id for item in latest}
        group = db.get(WorkGroup, next(iter(candidate_ids))) if len(candidate_ids) == 1 else None
        leader = (
            db.query(GroupLeaderAssignment)
            .filter(
                GroupLeaderAssignment.group_id == group.id,
                GroupLeaderAssignment.status == "active",
                GroupLeaderAssignment.starts_on <= today,
                (GroupLeaderAssignment.ends_on.is_(None)) | (GroupLeaderAssignment.ends_on >= today),
            )
            .first()
            if group
            else None
        )
        if (
            not group
            or group.status != "active"
            or group.attraction_id != employee.attraction_id
            or group.id in led_group_ids
            or not leader
            or leader.leader_employee_id == employee.id
        ):
            unresolved.append({"employee_id": employee.id, "reason": "代理TA主管期间无法确定应恢复的原小组"})
            continue
        db.add(
            GroupMembership(
                group_id=group.id,
                employee_id=employee.id,
                starts_on=today,
                status="active",
                reason="代理职务迁移：恢复原小组",
            )
        )
    for item in unresolved:
        employee = db.get(Employee, item["employee_id"])
        create_alert(
            db,
            "acting_duty_migration",
            f"{item['employee_id']}:{item['reason']}",
            f"{employee.name if employee else item['employee_id']}：{item['reason']}，请HR核对后手动设置",
            employee_id=item["employee_id"],
        )
    db.commit()
    return unresolved
