from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

os.environ["RECOGNITION_V2_DATA_DIR"] = tempfile.mkdtemp(prefix="recognition-sick-backup-")
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from app.main import app  # noqa: E402
from app.routers import sick_leave_import  # noqa: E402
from app.score_queries import employee_month_scores  # noqa: E402
from app.v2_crypto import hash_password  # noqa: E402
from app.v2_database import ROLE_PERMISSION_CODES, SessionLocal  # noqa: E402
from app.v2_models import Attraction, AttendanceMonthlyScore, AuditLog, Employee, EmployeeLOAPeriod, EmployeeRoleAssignment, MonthClosure, Role, SickLeaveRecord, StoredFile, UserAccount  # noqa: E402


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value


def login(client, account="GSMTEST01"):
    response = client.post("/api/login", json={"employee_no": account, "password": "HR123" if account == "HR01" else "1234"})
    assert response.status_code == 200, response.text


def employee_id(account="CMTEST01"):
    with SessionLocal() as db:
        return db.query(Employee).filter_by(employee_no=account).one().id


def form(target, day="2099-04-02", **kwargs):
    return {"employee_id": str(target), "leave_start_date": day, "leave_end_date": day,
            "leave_type": "法定病假", "leave_days": "0.5", **kwargs}


def extra_employee(account, role_code, circle, *, active=True, with_account=False):
    with SessionLocal() as db:
        found = db.query(Employee).filter_by(employee_no=account).first()
        if found:
            return found.id
        attraction = db.query(Attraction).filter_by(name=circle).one()
        role = db.query(Role).filter_by(code=role_code).one()
        row = Employee(employee_no=account, name="备用测试" + account, attraction_id=attraction.id, is_active=active, hired_on="2020-01-01")
        db.add(row)
        db.flush()
        db.add(EmployeeRoleAssignment(employee_id=row.id, role_id=role.id, starts_on="2020-01-01", status="active"))
        if with_account:
            db.add(UserAccount(employee_id=row.id, login_account=account, password_hash=hash_password("1234"), enabled=True, must_change_password=False))
        db.commit()
        return row.id


def test_global_fuzzy_search_includes_other_roles_and_employees_without_login(client):
    target = extra_employee("BACKUPSEARCH", "SUPERVISOR", "矮人迷宫")
    inactive = extra_employee("BACKUPINACTIVE", "CM", "矮人迷宫", active=False)
    login(client)
    assert client.get("/api/sick-leave-imports/employee-targets").json()["items"] == []
    for keyword in ("BACKUPSE", "备用测试BACKUPSEARCH"):
        response = client.get("/api/sick-leave-imports/employee-targets", params={"keyword": keyword})
        assert response.status_code == 200, response.text
        row = next(row for row in response.json()["items"] if row["id"] == target)
        assert row["role_code"] == "SUPERVISOR" and row["attraction_name"] == "矮人迷宫"
    assert client.get("/api/sick-leave-imports/employee-targets", params={"keyword": "%"}).json()["items"] == []
    assert all(row["id"] != inactive for row in client.get("/api/sick-leave-imports/employee-targets", params={"keyword": "BACKUP"}).json()["items"])
    with SessionLocal() as db:
        circle = db.query(Attraction).filter_by(name="热力追踪").one().id
    assert client.get("/api/sick-leave-imports/employee-targets", params={"keyword": "BACKUPSEARCH", "attraction_id": circle}).json()["items"] == []


@pytest.mark.parametrize("account", ["CMTEST01", "TATEST01", "SUPTEST01", "TAGSMTEST01", "AMTEST01", "OMTEST01"])
def test_roles_without_the_import_entrance_cannot_search_or_submit(client, account):
    login(client, account)
    assert client.get("/api/sick-leave-imports/employee-targets", params={"keyword": "CM"}).status_code == 403
    assert client.post("/api/sick-leave-imports/single", data=form(employee_id())).status_code == 403
    assert "SICK_REGISTER" not in ROLE_PERMISSION_CODES[client.get("/api/me").json()["role_code"]]


def test_single_entry_needs_no_proof_and_updates_shared_scores_audit_and_export(client):
    target = employee_id()
    day = date.today().isoformat()
    month = day[:7]
    login(client)
    with SessionLocal() as db:
        files_before = db.query(StoredFile).count()
    response = client.post("/api/sick-leave-imports/single", data=form(target, day, note="补登记", idempotency_key="backup-half-day"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["record"]["import_source"] == "single_backup" and body["record"]["proof_url"] == ""
    assert body["score_eligible"] and body["attendance_score"] == 9.75
    with SessionLocal() as db:
        stored = db.get(SickLeaveRecord, body["record"]["id"])
        assert stored.proof_file_id is None and stored.employee_no_snapshot == "CMTEST01"
        assert stored.attraction_id_snapshot is not None and stored.note == "补登记"
        assert db.query(StoredFile).count() == files_before
        score = db.query(AttendanceMonthlyScore).filter_by(employee_id=target, attendance_month=month).one()
        assert score.actual_sick_days == Decimal("0.5") and score.final_score == Decimal("9.75")
        assert employee_month_scores(db, month, [target])[0]["attendance_score"] == 9.75
        assert db.query(AuditLog).filter_by(action="单人备用病假登记", entity_id=str(stored.id)).count() == 1
    records = client.get("/api/sick-leave-imports/records", params={"month": month}).json()["items"]
    assert any(row["id"] == body["record"]["id"] and row["submitter_name"] == "测试GSM" for row in records)
    entries = client.get("/api/my-entries", params={"month": month, "record_type": "sick_leave"}).json()["items"]
    assert all(row["id"] != body["record"]["id"] for row in entries)
    ranking = client.get("/api/pr-rankings", params={"start_date": month + "-01", "end_date": day, "category": "absence", "keyword": "CMTEST01"})
    assert ranking.status_code == 200, ranking.text
    assert next(row for row in ranking.json()["rows"] if row["employee_id"] == target)["leave_days"] == 0.5
    exported = client.get("/api/statistics/export", params={"month": month})
    assert exported.status_code == 200, exported.text
    workbook = load_workbook(BytesIO(exported.content), data_only=True)
    assert any("CMTEST01" in str(cell.value) for sheet in workbook for row in sheet for cell in row)


def test_idempotent_replay_and_overlap_do_not_duplicate_records(client):
    target = employee_id()
    login(client)
    data = form(target, "2099-05-02", idempotency_key="backup-replay")
    first = client.post("/api/sick-leave-imports/single", data=data)
    second = client.post("/api/sick-leave-imports/single", data=data)
    assert first.status_code == second.status_code == 200
    assert second.json()["duplicate"] and second.json()["record"]["id"] == first.json()["record"]["id"]
    changed = client.post("/api/sick-leave-imports/single", data={**data, "leave_days": "1"})
    assert changed.status_code == 409 and changed.json()["detail"]["code"] == "IDEMPOTENCY_PAYLOAD_CONFLICT"
    overlap = client.post("/api/sick-leave-imports/single", data={**data, "idempotency_key": "backup-other"})
    assert overlap.status_code == 409 and overlap.json()["detail"]["code"] == "SICK_LEAVE_DATE_OVERLAP"
    with SessionLocal() as db:
        assert db.query(SickLeaveRecord).filter_by(employee_id=target, attendance_month="2099-05").count() == 1


def test_circle_hr_can_submit_across_circles_and_see_own_backup_records(client):
    extra_employee("BACKUPHR", "HR_CIRCLE", "热力追踪", with_account=True)
    target = extra_employee("BACKUPCROSS", "GSM", "矮人迷宫")
    login(client, "BACKUPHR")
    assert any(row["id"] == target for row in client.get("/api/sick-leave-imports/employee-targets", params={"keyword": "BACKUPCROSS"}).json()["items"])
    response = client.post("/api/sick-leave-imports/single", data=form(target, "2099-06-02"))
    assert response.status_code == 200, response.text
    assert not response.json()["score_eligible"] and response.json()["attendance_score"] == 0
    record_id = response.json()["record"]["id"]
    assert any(row["id"] == record_id for row in client.get("/api/sick-leave-imports/records", params={"month": "2099-06"}).json()["items"])
    # Another circle's ordinary file history is still restricted.
    with SessionLocal() as db:
        row = db.get(SickLeaveRecord, record_id)
        foreign = SickLeaveRecord(employee_id=target, attendance_month="2099-06", leave_start_date="2099-06-03", leave_end_date="2099-06-03", leave_days=1, charged_days=1, import_source="monthly_transaction_import", status="active", attraction_id_snapshot=row.attraction_id_snapshot, submitted_by=employee_id("GSMTEST01"), submitted_by_name="测试GSM")
        db.add(foreign)
        db.commit()
        foreign_id = foreign.id
    rows = client.get("/api/sick-leave-imports/records", params={"month": "2099-06"}).json()["items"]
    assert all(row["id"] != foreign_id for row in rows)


def test_highest_admin_entrance_can_submit(client):
    login(client, "HR01")
    assert client.post("/api/sick-leave-imports/single", data=form(employee_id("TRTEST01"), "2099-07-02")).status_code == 200


@pytest.mark.parametrize("change", [
    {"leave_end_date": "2099-08-01"}, {"leave_end_date": "2099-09-02"},
    {"leave_days": "0"}, {"leave_days": "0.3"}, {"leave_days": "2"},
    {"leave_days": "NaN"}, {"leave_days": "Infinity"}, {"leave_days": "bad"},
    {"leave_type": "其他"}, {"employee_id": "99999999"}, {"note": "字" * 301},
])
def test_invalid_input_is_rejected_without_writes(client, change):
    login(client)
    with SessionLocal() as db:
        before = db.query(SickLeaveRecord).count()
    response = client.post("/api/sick-leave-imports/single", data=form(employee_id(), "2099-08-02", **change))
    assert response.status_code == 400, response.text
    with SessionLocal() as db:
        assert db.query(SickLeaveRecord).count() == before


def test_loa_month_close_and_multiday_confirmation_are_checked(client):
    login(client)
    target = employee_id()
    data = form(target, "2099-09-02", leave_end_date="2099-09-03", leave_days="1")
    assert client.post("/api/sick-leave-imports/single", data=data).status_code == 409
    with SessionLocal() as db:
        gsm = employee_id("GSMTEST01")
        db.add(EmployeeLOAPeriod(employee_id=target, starts_on="2099-09-02", ends_on="2099-09-04", status="active", created_by=gsm, created_by_name="测试GSM"))
        circle = db.get(Employee, target).attraction_id
        db.add(MonthClosure(closure_month="2099-10", attraction_id=circle, status="closed"))
        db.commit()
    protected = client.post("/api/sick-leave-imports/single", data={**data, "rest_day_confirmed": "true"})
    assert protected.status_code == 409 and protected.json()["detail"]["code"] == "SICK_LEAVE_LOA_CONFLICT"
    closed = client.post("/api/sick-leave-imports/single", data=form(target, "2099-10-02"))
    assert closed.status_code == 423 and closed.json()["detail"]["code"] == "MONTH_CLOSED"
    confirmed = client.post("/api/sick-leave-imports/single", data=form(target, "2099-11-02", leave_end_date="2099-11-03", leave_days="1.5", rest_day_confirmed="true"))
    assert confirmed.status_code == 200, confirmed.text


def test_full_month_file_covers_backup_even_when_employee_is_absent_from_file(client):
    login(client)
    target, absent = employee_id(), employee_id("CMTEST02")
    month = "2099-12"
    ids = []
    for person in (target, absent):
        response = client.post("/api/sick-leave-imports/single", data=form(person, month + "-02"))
        assert response.status_code == 200, response.text
        ids.append(response.json()["record"]["id"])
    user = client.get("/api/me").json()["id"]
    token = "backup-covered-by-file"
    sick_leave_import._store_preview(token, {"created_at": datetime.now(), "filename": "month.xls", "content": b"", "month": month, "matched": [{"employee_id": target, "date": month + "-03", "days": Decimal("1"), "leave_type": "全薪病假"}], "unmatched": [], "loa_protected": [], "errors": [], "user_id": user})
    response = client.post("/api/sick-leave-imports/commit", json={"token": token, "month": month})
    assert response.status_code == 200, response.text
    assert response.json()["replaced_record_count"] == 2 and response.json()["absent_employee_count"] == 1
    with SessionLocal() as db:
        assert all(db.get(SickLeaveRecord, record).status == "covered" for record in ids)
        assert db.query(AttendanceMonthlyScore).filter_by(employee_id=absent, attendance_month=month).one().actual_sick_days == 0
        assert db.query(AttendanceMonthlyScore).filter_by(employee_id=target, attendance_month=month).one().actual_sick_days == 1


def test_missing_fields_have_readable_validation_messages(client):
    login(client)
    response = client.post("/api/sick-leave-imports/single", data={"employee_id": str(employee_id())})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "SICK_LEAVE_VALIDATION_ERROR"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_backup_form_reuses_global_picker_and_has_no_proof_field():
    source = (Path(__file__).parents[1] / "app/static/js/app.js").read_text(encoding="utf-8")
    picker = source[source.index("function employeePicker("):source.index("function circleTransferEmployeePicker(")]
    markup = source[source.index("function singleSickLeaveForm(){"):source.index("function bindSingleSickLeave(")]
    checks = """
const state = {options:{attractions:[]}, me:{role_code:'HR_CIRCLE'}};
const esc = value => String(value);
const opt = () => '';
const today = () => '2026-10-02';
const html = singleSickLeaveForm();
if (!html.includes('data-global-search="1"') || !html.includes('/api/sick-leave-imports/employee-targets')) throw Error('global picker missing');
for (const field of ['leave_type','leave_start_date','leave_end_date','leave_days','note']) {
  if (!html.includes('name="'+field+'"')) throw Error('missing field '+field);
}
if (html.includes('type="file"') || html.includes('name="proof"')) throw Error('unexpected proof upload');
if (!html.includes('step="0.5"') || !html.includes('form-sticky-actions')) throw Error('form contract changed');
"""
    subprocess.run([shutil.which("node"), "-e", picker + markup + checks], check=True, capture_output=True, text=True)
