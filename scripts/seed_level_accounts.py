"""Legacy local demo initializer. Prefer seed_isolated_test_accounts.py.

Requires an isolated data directory and explicit test-account opt-in.
Existing demo credentials are reset by this legacy tool; passwords are never printed.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.v2_crypto import hash_password  # noqa: E402
from app.v2_database import SessionLocal, init_db  # noqa: E402
from app.v2_models import (  # noqa: E402
    Attraction,
    Employee,
    EmployeeRoleAssignment,
    GroupLeaderAssignment,
    GroupMembership,
    ManagementScope,
    Role,
    UserAccount,
    WorkGroup,
)

LEVEL_ACCOUNTS = (
    ("1", "演示CM", "CM", "1111"),
    ("2", "演示TA主管", "TA_SUPERVISOR", "2222"),
    ("3", "演示GSM", "GSM", "3333"),
    ("4", "演示AM", "AM", "4444"),
    ("5", "演示HR", "HR_ADMIN", "5555"),
    ("6", "热力追踪专属HR", "HR_CIRCLE", "6666"),
)
DEMO_GROUP_NAME = "演示组-热力追踪"


def main() -> None:
    from app.runtime_environment import require_test_initialization
    require_test_initialization()
    init_db()
    db = SessionLocal()
    try:
        today = date.today().isoformat()
        roles = {role.code: role for role in db.query(Role).all()}
        heat = (
            db.query(Attraction)
            .filter(Attraction.name == "热力追踪", Attraction.employee_circle.is_(True))
            .one()
        )
        circle_attractions = (
            db.query(Attraction)
            .filter(Attraction.employee_circle.is_(True), Attraction.active.is_(True))
            .all()
        )
        employees: dict[str, Employee] = {}

        for employee_no, name, role_code, password in LEVEL_ACCOUNTS:
            employee = db.query(Employee).filter(Employee.employee_no == employee_no).first()
            if not employee:
                employee = Employee(employee_no=employee_no, name=name, is_active=True, hired_on=today)
                db.add(employee)
                db.flush()
            employee.name = name
            employee.is_active = True
            if role_code in {"CM", "TA_SUPERVISOR", "HR_CIRCLE"}:
                employee.attraction_id = heat.id
            elif role_code == "GSM" and employee.attraction_id is None:
                # Publishing follows the employee's home circle, not management scopes.
                # Fill the missing demo default without replacing an HR-assigned circle.
                employee.attraction_id = heat.id
            elif role_code == "HR_ADMIN":
                employee.attraction_id = None
            has_role = (
                db.query(EmployeeRoleAssignment)
                .filter(
                    EmployeeRoleAssignment.employee_id == employee.id,
                    EmployeeRoleAssignment.status == "active",
                )
                .first()
            )
            if not has_role:
                db.add(
                    EmployeeRoleAssignment(
                        employee_id=employee.id,
                        role_id=roles[role_code].id,
                        starts_on=today,
                        assignment_type="permanent",
                        status="active",
                        reason="演示等级账号初始化",
                    )
                )
            account = db.query(UserAccount).filter(UserAccount.employee_id == employee.id).first()
            if not account:
                account = UserAccount(employee_id=employee.id, login_account=employee_no)
                db.add(account)
            account.login_account = employee_no
            account.password_hash = hash_password(password)
            account.enabled = True
            account.must_change_password = False
            account.locked_until = None
            account.failed_attempts = 0
            db.flush()
            employees[employee_no] = employee

        for employee_no in ("3", "4"):
            employee = employees[employee_no]
            for attraction in circle_attractions:
                exists = (
                    db.query(ManagementScope)
                    .filter(
                        ManagementScope.employee_id == employee.id,
                        ManagementScope.attraction_id == attraction.id,
                    )
                    .first()
                )
                if not exists:
                    db.add(
                        ManagementScope(
                            employee_id=employee.id,
                            attraction_id=attraction.id,
                            starts_on=today,
                        )
                    )

        group = db.query(WorkGroup).filter(WorkGroup.name == DEMO_GROUP_NAME).first()
        if not group:
            group = WorkGroup(name=DEMO_GROUP_NAME, attraction_id=heat.id, status="active")
            db.add(group)
            db.flush()
        leader = employees["2"]
        if not (
            db.query(GroupLeaderAssignment)
            .filter(
                GroupLeaderAssignment.group_id == group.id,
                GroupLeaderAssignment.leader_employee_id == leader.id,
                GroupLeaderAssignment.status == "active",
            )
            .first()
        ):
            db.add(
                GroupLeaderAssignment(
                    group_id=group.id,
                    leader_employee_id=leader.id,
                    starts_on=today,
                    status="active",
                )
            )
        member = employees["1"]
        if not (
            db.query(GroupMembership)
            .filter(
                GroupMembership.group_id == group.id,
                GroupMembership.employee_id == member.id,
                GroupMembership.status == "active",
            )
            .first()
        ):
            db.add(GroupMembership(group_id=group.id, employee_id=member.id, starts_on=today, status="active"))

        db.commit()
        for employee_no, name, role_code, _ in LEVEL_ACCOUNTS:
            print(f"账号 {employee_no} → {name}（角色 {role_code}）；口令不输出")
    finally:
        db.close()


if __name__ == "__main__":
    main()
