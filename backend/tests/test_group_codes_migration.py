from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-group-codes-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.v2_database import SessionLocal, ensure_group_codes  # noqa: E402
from app.v2_models import AuditLog, Employee, EmployeeRoleAssignment, GroupLeaderAssignment, Role, SystemAlert, WorkGroup  # noqa: E402
from app.v2_services import group_code_for_index, group_code_index  # noqa: E402


def test_code_letters_roll_over_after_z() -> None:
    assert [group_code_for_index(index) for index in (0, 25, 26, 27, 51, 52)] == ["A", "Z", "AA", "AB", "AZ", "BA"]
    assert all(group_code_index(group_code_for_index(index)) == index for index in range(80))


def test_existing_groups_get_fixed_names_in_pinyin_order_with_alerts() -> None:
    today = date.today().isoformat()
    with TestClient(app):
        with SessionLocal() as db:
            heat_id = db.query(Employee).filter_by(employee_no="SUPTEST01").one().attraction_id
            supervisor = db.query(Role).filter_by(code="SUPERVISOR").one()

            def person(employee_no: str, name: str) -> Employee:
                employee = Employee(employee_no=employee_no, name=name, attraction_id=heat_id, is_active=True)
                db.add(employee)
                db.flush()
                db.add(EmployeeRoleAssignment(employee_id=employee.id, role_id=supervisor.id, starts_on=today, status="active"))
                return employee

            wang, chen = person("MIGWANG01", "王主管"), person("MIGCHEN01", "陈主管")
            gsm = db.query(Employee).filter_by(employee_no="GSMTEST01").one()
            legacy = {}
            for key, leader in (("wang", wang), ("chen", chen), ("gsm", gsm), ("wang2", wang)):
                group = WorkGroup(name=f"旧组{key}", attraction_id=heat_id, status="active")
                db.add(group)
                db.flush()
                db.add(GroupLeaderAssignment(group_id=group.id, leader_employee_id=leader.id, starts_on=today, status="active", leader_type="formal"))
                legacy[key] = group.id
            db.commit()

            ensure_group_codes(db)

            names = {key: db.get(WorkGroup, group_id).name for key, group_id in legacy.items()}
            # Seeded A/B keep their letters; 测(ce) < 陈(chen) < 王(wang), ties by id.
            assert names == {"gsm": "热力追踪C组", "chen": "热力追踪D组", "wang": "热力追踪E组", "wang2": "热力追踪F组"}
            renamed = db.query(AuditLog).filter_by(action="小组统一命名", entity_id=str(legacy["chen"])).one()
            assert "陈主管工作组" in renamed.before_json and "热力追踪D组" in renamed.after_json
            messages = [row.message for row in db.query(SystemAlert).filter_by(alert_type="group_structure").all()]
            assert any("王主管" in message and "一人只能负责一个小组" in message for message in messages)
            assert any("热力追踪C组" in message and "本职不是主管" in message for message in messages)
            # Running it again changes nothing.
            ensure_group_codes(db)
            assert db.get(WorkGroup, legacy["wang"]).name == "热力追踪E组"
