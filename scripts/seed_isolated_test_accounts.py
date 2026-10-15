"""Create a fresh, isolated all-role fixture; never reset an existing database/password.

Requires RECOGNITION_ENVIRONMENT=local/test, an explicit separate DATA_DIR,
RECOGNITION_ENABLE_TEST_ACCOUNTS=1 and both test-password environment variables.
Only account identities/roles are printed. The script is excluded from release ZIPs.
"""
from __future__ import annotations

from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def main() -> dict:
    from app.runtime_environment import require_test_initialization

    data = require_test_initialization()
    if os.environ.get("RECOGNITION_ENVIRONMENT", "").strip().lower() not in {"local", "test"}:
        raise RuntimeError("初始化演示身份必须显式声明local或test环境")
    password = os.environ.get("RECOGNITION_TEST_DEFAULT_PASSWORD", "").strip()
    admin_password = os.environ.get("RECOGNITION_TEST_ADMIN_PASSWORD", "").strip()
    if any(not 4 <= len(value) <= 64 for value in (password, admin_password)):
        raise RuntimeError("请在当前进程设置两项测试口令，长度4至64位；不会输出口令")
    manifest_path = data / "test-fixture.json"
    database = data / "recognition_v2.db"
    if manifest_path.is_file() and database.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("fixture_version") != 1:
            raise RuntimeError("测试夹具版本不匹配，请使用新的空目录")
        return {**manifest, "reused": True}
    if data.exists() and any(data.iterdir()):
        raise RuntimeError("仅允许在全新空目录初始化，禁止覆盖已有数据库或材料")
    data.mkdir(parents=True, exist_ok=True)
    # An interrupted run stays visibly incomplete; never silently reseed/overwrite it.
    initializing = data / ".test-fixture-initializing"
    with initializing.open("x", encoding="utf-8") as stream:
        stream.write("isolated fixture initialization in progress")

    from app.v2_database import SessionLocal, init_db
    from app.v2_models import (Attraction, Employee, EmployeeActingDuty,
                               EmployeeRoleAssignment, Role, UserAccount)
    from app.v2_crypto import hash_password
    from app.services.identity import base_role_at, duties_at, role_at

    init_db()  # Reuse the application's CM/TR, formal/acting leaders and scope fixtures.
    today = date.today()
    with SessionLocal() as db:
        roles = {role.code: role for role in db.query(Role).all()}
        circle = db.query(Attraction).filter_by(name="热力追踪").one()
        other = db.query(Attraction).filter_by(name="矮人迷宫").one()
        def add(number, name, base, attraction, *, duty=None, starts=None, ends=None):
            employee = Employee(employee_no=number, name=name, is_active=True,
                                attraction_id=attraction.id if attraction else None,
                                hired_on=today.isoformat())
            db.add(employee)
            db.flush()
            db.add(EmployeeRoleAssignment(employee_id=employee.id, role_id=roles[base].id,
                starts_on=today.isoformat(), status="active", assignment_type="permanent",
                reason="独立测试夹具"))
            if duty:
                db.add(EmployeeActingDuty(employee_id=employee.id, role_id=roles[duty].id,
                    starts_on=starts, ends_on=ends, status="active", reason="独立测试夹具"))
            db.add(UserAccount(employee_id=employee.id, login_account=number,
                password_hash=hash_password(admin_password if base in {"HR_ADMIN", "SYSTEM_ADMIN"} else password),
                enabled=True, must_change_password=False, credential_initialized=True))

        add("HRADMINTEST01", "测试HR管理员", "HR_ADMIN", None)
        add("CIRCLEHRTEST01", "测试本圈HR", "HR_CIRCLE", circle)
        add("CMTESTCROSS01", "测试其他圈CM", "CM", other)
        add("TAFUTURETEST01", "测试未生效代理", "CM", circle, duty="TA_SUPERVISOR",
            starts=(today + timedelta(days=1)).isoformat(), ends=(today + timedelta(days=30)).isoformat())
        add("TAEXPIREDTEST01", "测试已到期代理", "TR", circle, duty="TA_SUPERVISOR",
            starts=(today - timedelta(days=10)).isoformat(), ends=(today - timedelta(days=1)).isoformat())
        db.commit()
        accounts = []
        for account in db.query(UserAccount).join(Employee).filter(
                Employee.employee_no.contains("TEST") | (Employee.employee_no == "HR01")).all():
            employee = account.employee
            accounts.append({"login_account": account.login_account, "name": employee.name,
                "attraction_id": employee.attraction_id,
                "role": role_at(db, employee.id).code, "base_role": base_role_at(db, employee.id).code,
                "duty_roles": [role.code for role in duties_at(db, employee.id)],
                "credential_source": "admin environment variable" if account.login_account in {"HR01", "HRADMINTEST01"}
                                     else "default environment variable"})
        manifest = {"fixture_version": 1, "created_on": today.isoformat(), "accounts": accounts}
        if {row["role"] for row in accounts} != set(roles):
            raise RuntimeError("测试角色覆盖不完整，保留未完成标记，请检查后使用新的空目录")
    partial = data / "test-fixture.json.partial"
    partial.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    partial.replace(manifest_path)
    initializing.unlink()
    return {**manifest, "reused": False}


if __name__ == "__main__":
    try:
        print(json.dumps(main(), ensure_ascii=False, indent=2))
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
