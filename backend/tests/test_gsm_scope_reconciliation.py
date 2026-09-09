from __future__ import annotations

from datetime import date, timedelta
import os
from pathlib import Path
import tempfile


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-gsm-scope-test-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "test-default"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "test-admin"

from app.v2_database import SessionLocal, ensure_gsm_management_scopes, init_db, synchronize_gsm_management_scope  # noqa: E402
from app.v2_models import Attraction, Employee, EmployeeRoleAssignment, ManagementScope, Role  # noqa: E402


def active_scope_rows(db, employee_id: int, on_date: str):
    return (
        db.query(ManagementScope)
        .filter(
            ManagementScope.employee_id == employee_id,
            ManagementScope.starts_on <= on_date,
            (ManagementScope.ends_on.is_(None) | (ManagementScope.ends_on >= on_date)),
        )
        .all()
    )


def test_scope_reconciliation_moves_and_clears_ineligible_scope() -> None:
    init_db()
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with SessionLocal() as db:
        heat = db.query(Attraction).filter_by(name="热力追踪", employee_circle=True).one()
        dwarf = db.query(Attraction).filter_by(name="矮人迷宫", employee_circle=True).one()
        gsm = db.query(Role).filter_by(code="GSM").one()
        employee = Employee(employee_no="9912601", name="范围同步测试", attraction_id=heat.id, is_active=True)
        db.add(employee)
        db.flush()
        db.add(EmployeeRoleAssignment(employee_id=employee.id, role_id=gsm.id, starts_on=yesterday, status="active"))
        db.add(ManagementScope(employee_id=employee.id, attraction_id=dwarf.id, starts_on=yesterday))
        db.commit()

        assert synchronize_gsm_management_scope(db, employee, "GSM", today)
        db.commit()
        assert [scope.attraction_id for scope in active_scope_rows(db, employee.id, today)] == [heat.id]

        employee.attraction_id = dwarf.id
        assert synchronize_gsm_management_scope(db, employee, "GSM", today)
        db.commit()
        assert [scope.attraction_id for scope in active_scope_rows(db, employee.id, today)] == [dwarf.id]

        employee.attraction_id = None
        assert synchronize_gsm_management_scope(db, employee, "GSM", today)
        db.commit()
        assert active_scope_rows(db, employee.id, today) == []


def test_startup_reconciliation_removes_orphans_and_keeps_one_valid_scope() -> None:
    init_db()
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with SessionLocal() as db:
        heat = db.query(Attraction).filter_by(name="热力追踪", employee_circle=True).one()
        dwarf = db.query(Attraction).filter_by(name="矮人迷宫", employee_circle=True).one()
        gsm = db.query(Role).filter_by(code="GSM").one()
        cm = db.query(Role).filter_by(code="CM").one()

        former = Employee(employee_no="9912602", name="前GSM范围", attraction_id=heat.id, is_active=True)
        inactive = Employee(employee_no="9912603", name="离职GSM范围", attraction_id=heat.id, is_active=False)
        valid = Employee(employee_no="9912604", name="有效GSM范围", attraction_id=heat.id, is_active=True)
        db.add_all((former, inactive, valid))
        db.flush()
        db.add_all((
            EmployeeRoleAssignment(employee_id=former.id, role_id=cm.id, starts_on=yesterday, status="active"),
            EmployeeRoleAssignment(employee_id=inactive.id, role_id=gsm.id, starts_on=yesterday, status="active"),
            EmployeeRoleAssignment(employee_id=valid.id, role_id=gsm.id, starts_on=yesterday, status="active"),
            ManagementScope(employee_id=former.id, attraction_id=heat.id, starts_on=yesterday),
            ManagementScope(employee_id=inactive.id, attraction_id=heat.id, starts_on=yesterday),
            ManagementScope(employee_id=valid.id, attraction_id=dwarf.id, starts_on=yesterday),
            ManagementScope(employee_id=valid.id, attraction_id=heat.id, starts_on=today),
        ))
        db.commit()

        ensure_gsm_management_scopes(db)

        assert active_scope_rows(db, former.id, today) == []
        assert active_scope_rows(db, inactive.id, today) == []
        valid_scopes = active_scope_rows(db, valid.id, today)
        assert len(valid_scopes) == 1
        assert valid_scopes[0].attraction_id == heat.id
