from datetime import datetime, timedelta
from decimal import Decimal
import os
from pathlib import Path
import tempfile

TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-sick-import-test-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.routers import sick_leave_import  # noqa: E402
from app.routers.sick_leave_import import _reconcile, _system_number  # noqa: E402
from app.v2_database import ROLE_PERMISSION_CODES, SessionLocal  # noqa: E402
from app.v2_models import SickLeaveRecord  # noqa: E402


def test_legacy_manual_sick_registration_stays_disabled_for_production_roles():
    assert all("SICK_REGISTER" not in permissions for permissions in ROLE_PERMISSION_CODES.values())


def test_source_id_drops_exactly_one_leading_digit():
    assert _system_number("01727264") == "1727264"
    assert _system_number("01278490") == "1278490"


def test_reconcile_combines_paid_sick_leave_types():
    records = [
        {"source_id": "01727264", "leave_type": "法定病假", "hours": Decimal("8")},
        {"source_id": "01727264", "leave_type": "全薪病假", "hours": Decimal("4")},
        {"source_id": "01727264", "leave_type": "无薪病假", "hours": Decimal("8")},
    ]
    summaries = {
        ("01727264", "病假时间总计"): Decimal("12"),
        ("01727264", "无薪病假"): Decimal("8"),
    }

    assert _reconcile(records, summaries) == []


def test_reconcile_blocks_a_summary_difference():
    records = [{"source_id": "01727264", "leave_type": "法定病假", "hours": Decimal("8")}]

    errors = _reconcile(records, {("01727264", "病假时间总计"): Decimal("4")})

    assert len(errors) == 1
    assert "总数区对账不一致" in errors[0]


def test_commit_rechecks_loa_added_after_preview():
    with TestClient(app) as client:
        login = client.post("/api/login", json={"employee_no": "GSMTEST01", "password": "1234"})
        assert login.status_code == 200, login.text
        target = next(
            row for row in client.get("/api/employee-targets", params={"usage": "loa", "keyword": "测试CM甲"}).json()["items"]
            if row["employee_no"] == "CMTEST01"
        )
        user_id = client.get("/api/me").json()["id"]
        token = "stale-loa-review"
        sick_leave_import._store_preview(token, {
            "created_at": datetime.now(), "filename": "review.xls", "content": b"",
            "month": "2099-08", "matched": [{
                "employee_id": target["id"], "date": "2099-08-20", "days": Decimal("1"), "leave_type": "法定病假",
            }],
            "unmatched": [], "loa_protected": [], "errors": [], "user_id": user_id,
        })
        created = client.post("/api/loa-periods", json={
            "employee_id": target["id"], "starts_on": "2099-08-20", "ends_on": "2099-08-20",
        })
        assert created.status_code == 200, created.text
        committed = client.post("/api/sick-leave-imports/commit", json={"token": token})
        assert committed.status_code == 409, committed.text
        assert "重新上传文件预检" in committed.json()["detail"]
        with SessionLocal() as db:
            assert db.query(SickLeaveRecord).filter(
                SickLeaveRecord.employee_id == target["id"],
                SickLeaveRecord.leave_start_date == "2099-08-20",
                SickLeaveRecord.status == "active",
                SickLeaveRecord.import_source == "monthly_transaction_import",
            ).count() == 0
        sick_leave_import._drop_preview(token)


def test_preview_rejects_workbooks_above_size_limit():
    with TestClient(app) as client:
        login = client.post("/api/login", json={"employee_no": "GSMTEST01", "password": "1234"})
        assert login.status_code == 200, login.text
        response = client.post("/api/sick-leave-imports/preview", files={
            "workbook": ("too-large.xls", b"x" * (sick_leave_import._MAX_WORKBOOK_BYTES + 1)),
        })
        assert response.status_code == 413, response.text


def test_preview_cache_prunes_expired_and_oldest_entries(monkeypatch):
    monkeypatch.setattr(sick_leave_import, "_MAX_CACHED_PREVIEWS", 2)
    monkeypatch.setattr(sick_leave_import, "_MAX_CACHED_BYTES", 8)
    sick_leave_import._PREVIEWS.clear()
    now = datetime.now()
    for token, age in (("expired", timedelta(minutes=21)), ("first", timedelta(seconds=3)),
                       ("second", timedelta(seconds=2)), ("third", timedelta(seconds=1))):
        sick_leave_import._store_preview(token, {
            "created_at": now - age, "content": b"1234", "user_id": 1,
        })
    assert set(sick_leave_import._PREVIEWS) == {"second", "third"}
    assert sick_leave_import._get_preview("expired", 1) is None
    assert sick_leave_import._get_preview("second", 2) is None
    sick_leave_import._PREVIEWS.clear()
