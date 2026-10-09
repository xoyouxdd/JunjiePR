"""Opt-in test identities and production account safeguards; passwords retain their policy."""
from __future__ import annotations

from datetime import date, timedelta
import os


CIRCLE_HR_ACCOUNTS = (
    ("HR-HEAT", "热力追踪专属HR", "热力追踪"),
    ("HR-DWARF", "矮人迷宫专属HR", "矮人迷宫"),
    ("HR-BEAR", "小熊罐子专属HR", "小熊罐子"),
)


def disable_test_accounts(db) -> None:
    """Quarantine historical demo identities unless an isolated run opts in."""
    if os.environ.get("RECOGNITION_ENABLE_TEST_ACCOUNTS", "").strip() == "1":
        return
    from app.v2_models import Employee, UserAccount, UserSession

    rows = (
        db.query(UserAccount)
        .join(Employee, Employee.id == UserAccount.employee_id)
        .filter((Employee.employee_no.like("%TEST%")) | (Employee.name.like("%测试%")))
        .all()
    )
    for account in rows:
        account.enabled = False
        db.query(UserSession).filter(UserSession.account_id == account.id).delete(synchronize_session=False)
    if rows:
        db.commit()


def seed_test_accounts(db) -> None:
    # Predictable test identities are allowed only in explicitly isolated runs.
    if os.environ.get("RECOGNITION_ENABLE_TEST_ACCOUNTS", "").strip() != "1":
        return
    test_password = os.environ.get("RECOGNITION_TEST_DEFAULT_PASSWORD", "").strip()
    test_admin_password = os.environ.get("RECOGNITION_TEST_ADMIN_PASSWORD", "").strip()
    if not test_password or not test_admin_password:
        raise RuntimeError("启用测试账号时必须显式配置测试账号和测试管理员密码")
    from app.v2_crypto import hash_password
    from app.v2_models import (
        Attraction,
        Employee,
        EmployeeActingDuty,
        EmployeeRoleAssignment,
        GroupLeaderAssignment,
        GroupMembership,
        ManagementScope,
        Role,
        UserAccount,
        WorkGroup,
    )

    if db.query(Employee).count():
        return
    today = date.today()
    attraction_a = db.query(Attraction).filter(Attraction.name == "热力追踪").one()
    attraction_b = db.query(Attraction).filter(Attraction.name == "矮人迷宫").one()
    roles = {role.code: role for role in db.query(Role).all()}
    employees = {}

    def add_employee(employee_no, name, role_code, attraction, password=None, *, temp_end=None, return_role=None, duty=None, duty_end=None):
        employee = Employee(
            employee_no=employee_no,
            name=name,
            attraction_id=attraction.id if attraction else None,
            is_active=True,
            hired_on=today.isoformat(),
        )
        db.add(employee)
        db.flush()
        db.add(
            EmployeeRoleAssignment(
                employee_id=employee.id,
                role_id=roles[role_code].id,
                starts_on=today.isoformat(),
                ends_on=temp_end,
                assignment_type="temporary" if temp_end else "permanent",
                return_role_id=roles[return_role].id if return_role else None,
                status="active",
                reason="V2测试账号初始化",
            )
        )
        if duty:
            db.add(
                EmployeeActingDuty(
                    employee_id=employee.id,
                    role_id=roles[duty].id,
                    starts_on=today.isoformat(),
                    ends_on=duty_end,
                    status="active",
                    reason="V2测试账号初始化",
                )
            )
        db.add(
            UserAccount(
                employee_id=employee.id,
                login_account=employee_no,
                password_hash=hash_password(password or test_password),
                enabled=True,
                must_change_password=False,
            )
        )
        employees[employee_no] = employee
        return employee

    add_employee("CMTEST01", "测试CM甲", "CM", attraction_a)
    add_employee("CMTEST02", "测试CM乙", "CM", attraction_a)
    add_employee("TRTEST01", "测试TR甲", "TR", attraction_a)
    add_employee(
        "TATEST01",
        "测试TA主管",
        "CM",
        attraction_a,
        duty="TA_SUPERVISOR",
        duty_end=(today + timedelta(days=90)).isoformat(),
    )
    add_employee("SUPTEST01", "测试主管", "SUPERVISOR", attraction_a)
    add_employee("TAGSMTEST01", "测试TA GSM", "SUPERVISOR", attraction_a, duty="TA_GSM")
    add_employee("GSMTEST01", "测试GSM", "GSM", attraction_a)
    add_employee("AMTEST01", "测试AM", "AM", attraction_a)
    add_employee("OMTEST01", "测试OM", "OM", None)
    add_employee("HR01", "最高管理员", "SYSTEM_ADMIN", None, test_admin_password)
    db.flush()

    group_supervisor = WorkGroup(name="热力追踪A组", code="A", attraction_id=attraction_a.id, status="active")
    group_ta = WorkGroup(name="热力追踪B组", code="B", attraction_id=attraction_a.id, status="active")
    db.add_all([group_ta, group_supervisor])
    db.flush()
    db.add_all(
        [
            GroupLeaderAssignment(group_id=group_ta.id, leader_employee_id=employees["TATEST01"].id, starts_on=today.isoformat(), status="active", leader_type="acting"),
            GroupLeaderAssignment(group_id=group_supervisor.id, leader_employee_id=employees["SUPTEST01"].id, starts_on=today.isoformat(), status="active"),
            GroupMembership(group_id=group_ta.id, employee_id=employees["CMTEST01"].id, starts_on=today.isoformat(), status="active"),
            GroupMembership(group_id=group_ta.id, employee_id=employees["TRTEST01"].id, starts_on=today.isoformat(), status="active"),
            GroupMembership(group_id=group_supervisor.id, employee_id=employees["CMTEST02"].id, starts_on=today.isoformat(), status="active"),
            # An acting TA主管 stays a member of their own original group.
            GroupMembership(group_id=group_supervisor.id, employee_id=employees["TATEST01"].id, starts_on=today.isoformat(), status="active"),
        ]
    )
    for employee_no in ("TAGSMTEST01", "GSMTEST01", "AMTEST01", "OMTEST01"):
        for attraction in (attraction_a, attraction_b):
            db.add(
                ManagementScope(
                    employee_id=employees[employee_no].id,
                    attraction_id=attraction.id,
                    starts_on=today.isoformat(),
                )
            )
    db.commit()


def ensure_highest_admin_account(db) -> None:
    """Promote HR01 and quarantine a legacy bootstrap credential once."""
    from app.v2_crypto import hash_password, new_temporary_password
    from app.v2_models import Employee, EmployeeRoleAssignment, Role, UserAccount, UserSession

    employee = db.query(Employee).filter(Employee.employee_no == "HR01").first()
    if not employee:
        bootstrap = os.environ.get("RECOGNITION_BOOTSTRAP_ADMIN_PASSWORD", "").strip()
        if not bootstrap or os.environ.get("RECOGNITION_ENABLE_TEST_ACCOUNTS", "").strip() == "1":
            return
        role = db.query(Role).filter(Role.code == "SYSTEM_ADMIN").one()
        today = date.today().isoformat()
        employee = Employee(employee_no="HR01", name="最高管理员", is_active=True, hired_on=today)
        db.add(employee)
        db.flush()
        db.add(
            EmployeeRoleAssignment(
                employee_id=employee.id,
                role_id=role.id,
                starts_on=today,
                assignment_type="permanent",
                status="active",
                reason="安全引导创建最高管理员",
            )
        )
        db.add(
            UserAccount(
                employee_id=employee.id,
                login_account="HR01",
                password_hash=hash_password(bootstrap),
                enabled=True,
                must_change_password=True,
                credential_initialized=True,
            )
        )
        db.commit()
        return
    role = db.query(Role).filter(Role.code == "SYSTEM_ADMIN").one()
    today = date.today().isoformat()
    assignment = (
        db.query(EmployeeRoleAssignment)
        .filter(
            EmployeeRoleAssignment.employee_id == employee.id,
            EmployeeRoleAssignment.status != "cancelled",
            EmployeeRoleAssignment.starts_on <= today,
        )
        .order_by(EmployeeRoleAssignment.starts_on.desc(), EmployeeRoleAssignment.id.desc())
        .first()
    )
    if assignment:
        assignment.role_id = role.id
        assignment.ends_on = None
        assignment.return_role_id = None
        assignment.assignment_type = "permanent"
        assignment.status = "active"
        assignment.reason = "原管理员升级为最高管理员"
    else:
        db.add(
            EmployeeRoleAssignment(
                employee_id=employee.id,
                role_id=role.id,
                starts_on=today,
                assignment_type="permanent",
                status="active",
                reason="原管理员升级为最高管理员",
            )
        )
    employee.name = "最高管理员"
    employee.is_active = True
    account = db.query(UserAccount).filter(UserAccount.employee_id == employee.id).first()
    if account:
        account.enabled = True
        if os.environ.get("RECOGNITION_ENABLE_TEST_ACCOUNTS", "").strip() != "1" and not account.credential_initialized:
            bootstrap = os.environ.get("RECOGNITION_BOOTSTRAP_ADMIN_PASSWORD", "").strip()
            account.password_hash = hash_password(bootstrap or new_temporary_password())
            account.must_change_password = True
            account.credential_initialized = True
            account.password_changed_at = None
            account.failed_attempts = 0
            account.locked_until = None
            db.query(UserSession).filter(UserSession.account_id == account.id).delete(synchronize_session=False)
    db.commit()


def ensure_circle_hr_accounts(db) -> None:
    """Create the three scoped HR accounts once, without resetting changed passwords."""
    from app.v2_crypto import hash_password, new_temporary_password
    from app.v2_models import Attraction, Employee, EmployeeRoleAssignment, Role, UserAccount

    role = db.query(Role).filter(Role.code == "HR_CIRCLE").one()
    today = date.today().isoformat()
    test_mode = os.environ.get("RECOGNITION_ENABLE_TEST_ACCOUNTS", "").strip() == "1"
    for login_account, name, circle_name in CIRCLE_HR_ACCOUNTS:
        circle = db.query(Attraction).filter(Attraction.name == circle_name, Attraction.employee_circle.is_(True)).one()
        employee = db.query(Employee).filter(Employee.employee_no == login_account).first()
        if not employee:
            employee = Employee(
                employee_no=login_account,
                name=name,
                attraction_id=circle.id,
                is_active=True,
                hired_on=today,
            )
            db.add(employee)
            db.flush()
        else:
            employee.name = name
            employee.attraction_id = circle.id
            employee.is_active = True

        assignment = (
            db.query(EmployeeRoleAssignment)
            .filter(
                EmployeeRoleAssignment.employee_id == employee.id,
                EmployeeRoleAssignment.status == "active",
                EmployeeRoleAssignment.starts_on <= today,
            )
            .order_by(EmployeeRoleAssignment.starts_on.desc(), EmployeeRoleAssignment.id.desc())
            .first()
        )
        if not assignment or assignment.role_id != role.id:
            if assignment and assignment.ends_on is None:
                assignment.ends_on = today
            db.add(
                EmployeeRoleAssignment(
                    employee_id=employee.id,
                    role_id=role.id,
                    starts_on=today,
                    assignment_type="permanent",
                    status="active",
                    reason="初始化景点圈HR账号",
                )
            )

        account = db.query(UserAccount).filter(UserAccount.employee_id == employee.id).first()
        if not account:
            db.add(
                UserAccount(
                    employee_id=employee.id,
                    login_account=login_account,
                    password_hash=hash_password(new_temporary_password()),
                    enabled=test_mode,
                    must_change_password=True,
                    credential_initialized=True,
                )
            )
        else:
            account.login_account = login_account
            if test_mode:
                account.enabled = True
            elif not account.credential_initialized:
                account.password_hash = hash_password(new_temporary_password())
                account.must_change_password = True
                account.credential_initialized = True
                account.password_changed_at = None
                account.enabled = False
    db.commit()
