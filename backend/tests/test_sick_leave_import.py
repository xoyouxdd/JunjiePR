from datetime import datetime, timedelta
from decimal import Decimal
from io import BytesIO
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-sick-import-test-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import load_workbook  # noqa: E402
import pytest  # noqa: E402
import xlrd  # noqa: E402

from app.main import app  # noqa: E402
from app.routers import sick_leave_import  # noqa: E402
from app.routers.sick_leave_import import _read_workbook, _reconcile, _system_number  # noqa: E402
from app.v2_database import ROLE_PERMISSION_CODES, SessionLocal  # noqa: E402
from app.v2_models import AttendanceMonthlyScore, Employee, SickLeaveRecord  # noqa: E402


def _preview_metadata(month):
    with SessionLocal() as db:
        from types import SimpleNamespace
        user=SimpleNamespace(role=SimpleNamespace(code='GSM'))
        return dict(scope=None,month_revision=sick_leave_import._month_revision(sick_leave_import._active_month_rows(db,user,month)))


def _preview_identity(employee_id):
    with SessionLocal() as db:
        employee=db.get(Employee,employee_id)
        return dict(employee_no=employee.employee_no,name=employee.name,system_employee_no=employee.employee_no,system_name=employee.name,system_attraction_id=employee.attraction_id)


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
            "month": "2099-08", **_preview_metadata("2099-08"), "matched": [{
                "employee_id": target["id"], **_preview_identity(target["id"]), "date": "2099-08-20", "days": Decimal("1"), "leave_type": "法定病假",
            }],
            "unmatched": [], "loa_protected": [], "errors": [], "user_id": user_id,
        })
        created = client.post("/api/loa-periods", json={
            "employee_id": target["id"], "starts_on": "2099-08-20", "ends_on": "2099-08-20",
        })
        assert created.status_code == 200, created.text
        committed = client.post("/api/sick-leave-imports/commit", json={"token": token, "month": "2099-08"})
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


def test_monthly_file_covers_prior_absence_for_matched_and_absent_employees():
    month = "2099-07"
    with TestClient(app) as client:
        with SessionLocal() as db:
            matched = db.query(Employee).filter_by(employee_no="CMTEST01").one()
            untouched = db.query(Employee).filter_by(employee_no="CMTEST02").one()
            submitter = db.query(Employee).filter_by(employee_no="GSMTEST01").one()

            def old_record(employee: Employee, day: str, source: str, *, violation: bool = False) -> SickLeaveRecord:
                row = SickLeaveRecord(
                    employee_id=employee.id, employee_no_snapshot=employee.employee_no,
                    employee_name_snapshot=employee.name, attendance_month=day[:7],
                    leave_start_date=day, leave_end_date=day, leave_days=Decimal("1"),
                    charged_days=Decimal("1"), import_source=source, is_violation=violation,
                    status="active", submitted_by=submitter.id, submitted_by_name=submitter.name,
                )
                db.add(row)
                db.flush()
                return row

            manual_id = old_record(matched, "2099-07-01", "manual").id
            violation_id = old_record(matched, "2099-07-02", "manual", violation=True).id
            imported_id = old_record(matched, "2099-07-03", "monthly_transaction_import").id
            untouched_id = old_record(untouched, "2099-07-04", "manual").id
            other_month_id = old_record(matched, "2099-06-01", "manual").id
            db.commit()
            matched_id, absent_id, submitter_id = matched.id, untouched.id, submitter.id

        login = client.post("/api/login", json={"employee_no": "GSMTEST01", "password": "1234"})
        assert login.status_code == 200, login.text
        token = "file-covers-manual-review"
        sick_leave_import._store_preview(token, {
            "created_at": datetime.now(), "filename": "review.xls", "content": b"",
            "month": month, **_preview_metadata(month), "matched": [
                {"employee_id": matched_id, **_preview_identity(matched_id), "date": "2099-07-05", "days": Decimal("0.5"), "leave_type": "法定病假"},
                {"employee_id": matched_id, **_preview_identity(matched_id), "date": "2099-07-06", "days": Decimal("1"), "leave_type": "全薪病假"},
            ], "unmatched": [], "loa_protected": [], "errors": [], "user_id": submitter_id,
        })
        response = client.post("/api/sick-leave-imports/commit", json={"token": token, "month": month})
        assert response.status_code == 200, response.text
        assert response.json()["covered_record_count"] == 2
        assert response.json()["replaced_record_count"] == 4
        assert response.json()["replaced_manual_count"] == 3
        assert response.json()["absent_employee_count"] == 1
        history = client.get("/api/my-entries", params={"month": month, "record_type": "sick_leave", "status": "covered"})
        assert history.status_code == 200, history.text
        assert {row["id"] for row in history.json()["items"]} == {manual_id, violation_id, untouched_id}
        assert {row["status_name"] for row in history.json()["items"]} == {"已覆盖"}
        with SessionLocal() as db:
            for record_id in (manual_id, violation_id, imported_id):
                row = db.get(SickLeaveRecord, record_id)
                assert row.status == "covered"
                assert row.voided_from_status == "active"
                assert row.voided_by == submitter_id
            assert db.get(SickLeaveRecord, untouched_id).status == "covered"
            assert db.get(SickLeaveRecord, other_month_id).status == "active"
            active = db.query(SickLeaveRecord).filter_by(employee_id=matched_id, attendance_month=month, status="active").all()
            assert {row.leave_start_date for row in active} == {"2099-07-05", "2099-07-06"}
            assert all(row.import_source == "monthly_transaction_import" for row in active)
            attendance = db.query(AttendanceMonthlyScore).filter_by(employee_id=matched_id, attendance_month=month).one()
            assert attendance.actual_sick_days == Decimal("1.5")
            absent_attendance = db.query(AttendanceMonthlyScore).filter_by(employee_id=absent_id, attendance_month=month).one()
            assert absent_attendance.actual_sick_days == Decimal("0")


def test_preview_rejects_workbooks_above_size_limit():
    with TestClient(app) as client:
        login = client.post("/api/login", json={"employee_no": "GSMTEST01", "password": "1234"})
        assert login.status_code == 200, login.text
        response = client.post("/api/sick-leave-imports/preview", files={
            "workbook": ("too-large.xls", b"x" * (sick_leave_import._MAX_WORKBOOK_BYTES + 1)),
        }, data={"month": "2099-08"})
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


class _FakeSheet:
    """Minimal stand-in for an xlrd sheet; rows are padded to a common width."""

    def __init__(self, rows: list[list], name: str = "员工事务"):
        self.name = name
        self.ncols = max(len(row) for row in rows)
        self.nrows = len(rows)
        self._rows = [list(row) + [""] * (self.ncols - len(row)) for row in rows]

    def row_values(self, index: int) -> list:
        return list(self._rows[index])


class _FakeBook:
    datemode = 0

    def __init__(self, sheet: _FakeSheet):
        self._sheet = sheet

    def sheet_by_index(self, index: int) -> _FakeSheet:
        assert index == 0
        return self._sheet

    def sheets(self) -> list[_FakeSheet]:
        return [self._sheet]


def _serial(year: int, month: int, day: int) -> float:
    return xlrd.xldate.xldate_from_date_tuple((year, month, day), 0)


def _workbook_rows(transactions: list[list], summaries: list[list], *, colon: str = ":") -> list[list]:
    """Build the HR layout: header two rows below "事务:", three rows below "总数:"."""
    return [
        ["员工事务报表"],
        [f"事务{colon}"],
        [],
        ["员工", "ID", "日期", "工资代码", "时数"],
        *transactions,
        [f"总数{colon}"],
        [],
        [],
        ["员工", "ID", "工资代码", "时数"],
        *summaries,
    ]


def _install_book(monkeypatch, rows: list[list]) -> None:
    book = _FakeBook(_FakeSheet(rows))
    monkeypatch.setattr(sick_leave_import.xlrd, "open_workbook", lambda *args, **kwargs: book)


@pytest.mark.parametrize("transaction_gap,summary_gap", [(0, 0), (1, 1), (5, 4)])
def test_layout_accepts_variable_blank_rows_and_reordered_columns(monkeypatch, transaction_gap, summary_gap):
    rows = [
        ["事务 ："], *([[]] * transaction_gap),
        ["时\n数", "员工：", "工资 代码", "日期", "ID:"],
        [8, "ZHANG, 张三", "法定病假", "2099-08-20", "01727264"],
        ["总 数:"], *([[]] * summary_gap),
        ["ID", "时数", "员工", "工资代码"],
        ["01727264", 8, "ZHANG, 张三", "病假时间总计"],
    ]
    _install_book(monkeypatch, rows)
    records, summaries, errors = _read_workbook(b"ignored")
    assert not errors and _reconcile(records, summaries) == []
    assert records[0]["row"] == transaction_gap + 3
    assert sick_leave_import._transaction_months(b"ignored") == {"2099-08"}


def test_layout_selects_report_after_cover_sheet_and_marks_correct_sheet(monkeypatch):
    report = _FakeSheet(_workbook_rows(
        [["NOBODY, 查无此人", "09999999", "2099-08-20", "法定病假", 8]],
        [["NOBODY, 查无此人", "09999999", "病假时间总计", 8]],
    ), "缺勤报表")
    # The same row number on a cover sheet must not be highlighted.
    cover = _FakeSheet([["说明"] for _ in range(report.nrows)], "封面")
    book = SimpleNamespace(datemode=0, sheets=lambda: [cover, report])
    monkeypatch.setattr(sick_leave_import.xlrd, "open_workbook", lambda *args, **kwargs: book)
    with TestClient(app) as client:
        assert client.post("/api/login", json={"employee_no": "GSMTEST01", "password": "1234"}).status_code == 200
        response = client.post("/api/sick-leave-imports/preview", files={"workbook": ("report.xls", b"fake")}, data={"month": "2099-08"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["can_commit"] and body["unmatched"][0]["sheet_name"] == "缺勤报表"
        token = body["token"]
        try:
            exported = client.get(f"/api/sick-leave-imports/{token}/unmatched-file")
            assert exported.status_code == 200, exported.text
            workbook = load_workbook(BytesIO(exported.content))
            row = body["unmatched"][0]["row"]
            assert workbook["缺勤报表"].cell(row, 1).fill.fgColor.rgb == "00FFC7CE"
            assert workbook["封面"].cell(row, 1).fill.fgColor.rgb != "00FFC7CE"
        finally:
            sick_leave_import._drop_preview(token)


def test_layout_rejects_multiple_complete_worksheets(monkeypatch):
    rows = _workbook_rows([], [])
    book = SimpleNamespace(datemode=0, sheets=lambda: [_FakeSheet(rows, "报表甲"), _FakeSheet(rows, "报表乙")])
    monkeypatch.setattr(sick_leave_import.xlrd, "open_workbook", lambda *args, **kwargs: book)
    with pytest.raises(HTTPException) as error:
        _read_workbook(b"ignored")
    assert error.value.status_code == 400
    assert all(part in error.value.detail for part in ("多个工作表", "报表甲", "报表乙", "第 4、8 行"))


@pytest.mark.parametrize("rows,expected", [
    ([["事务:"], ["员工", "ID", "日期", "时数"], ["总数:"], ["员工", "ID", "工资代码", "时数"]], ["第 2 行", "缺少列", "工资代码"]),
    ([["事务:"], ["员工", "ID", "日期", "工资代码", "时数", "ID"], ["总数:"], ["员工", "ID", "工资代码", "时数"]], ["第 2 行", "重复列", "ID"]),
    ([["事务:"], ["员工", "ID", "日期", "工资代码", "时数"], ["员工", "ID", "日期", "工资代码", "时数"], ["总数:"], ["员工", "ID", "工资代码", "时数"]], ["第 2、3 行", "多个表头"]),
    ([["事务:"], ["事务:"], ["总数:"]], ["第 1、2 行", "多个", "事务"]),
    ([["总数:"], ["事务:"]], ["第 2、1 行", "之前"]),
    ([["事务:"], ["总数:"]], ["第 1 行", "未找到完整表头"]),
])
def test_layout_failures_include_sheet_row_and_reason(monkeypatch, rows, expected):
    _install_book(monkeypatch, rows)
    with pytest.raises(HTTPException) as error:
        _read_workbook(b"ignored")
    assert error.value.status_code == 400
    assert "员工事务" in error.value.detail
    assert all(part in error.value.detail for part in expected)


def test_preview_returns_readable_location_error_without_creating_token(monkeypatch):
    _install_book(monkeypatch, [["事务:"], ["员工", "ID"], ["总数:"]])
    with TestClient(app) as client:
        assert client.post("/api/login", json={"employee_no": "GSMTEST01", "password": "1234"}).status_code == 200
        response = client.post("/api/sick-leave-imports/preview", files={"workbook": ("bad.xls", b"fake")}, data={"month": "2099-08"})
        assert response.status_code == 400
        assert "第 2 行" in response.json()["detail"] and "缺少列" in response.json()["detail"]
        assert "token" not in response.json()


def test_reconciliation_error_points_to_summary_row(monkeypatch):
    rows = _workbook_rows(
        [["ZHANG, 张三", "01727264", "2099-08-20", "法定病假", 8]],
        [["ZHANG, 张三", "01727264", "病假时间总计", 4]],
    )
    _install_book(monkeypatch, rows)
    records, summaries, errors = _read_workbook(b"ignored")
    assert not errors
    mismatch = _reconcile(records, summaries)
    assert len(mismatch) == 1
    assert f"总数区第 {len(rows)} 行" in mismatch[0]
    assert "员工事务" in mismatch[0]


def test_header_beyond_scan_range_is_not_guessed(monkeypatch):
    rows = [["事务:"], *([[]] * sick_leave_import._HEADER_SCAN_ROWS),
            ["员工", "ID", "日期", "工资代码", "时数"],
            ["总数:"], ["员工", "ID", "工资代码", "时数"]]
    _install_book(monkeypatch, rows)
    with pytest.raises(HTTPException) as error:
        _read_workbook(b"ignored")
    assert "第 2 至 21 行" in error.value.detail
    assert "未找到完整表头" in error.value.detail


def test_read_workbook_normalizes_text_and_numeric_ids(monkeypatch):
    _install_book(monkeypatch, _workbook_rows(
        [
            ["ZHANG, 张三", "01727264", _serial(2099, 8, 20), "法定病假", 8.0],
            ["LI, 李四", 1278490.0, "2099/08/21", "全薪病假", "4"],
            ["LI, 李四", 1278490.0, _serial(2099, 8, 22), "无薪病假", 8.0],
            ["LI, 李四", 1278490.0, _serial(2099, 8, 23), "年假", 8.0],
            ["WANG, 王五", "1727265", _serial(2099, 8, 24), "法定病假", 8.0],
        ],
        [
            ["ZHANG, 张三", "01727264", "病假时间总计", 8.0],
            ["WANG, 王五", "01727265", "病假时间总计", 8.0],
            ["LI, 李四", 1278490.0, "病假时间总计", 4.0],
            ["LI, 李四", 1278490.0, "无薪病假", 8.0],
        ],
    ))

    records, summaries, errors = _read_workbook(b"ignored")

    assert errors == []
    assert [(row["source_id"], row["employee_no"], row["name"], row["date"], row["hours"]) for row in records] == [
        ("01727264", "1727264", "张三", "2099-08-20", Decimal("8.00")),
        ("01278490", "1278490", "李四", "2099-08-21", Decimal("4.00")),
        ("01278490", "1278490", "李四", "2099-08-22", Decimal("8.00")),
        ("01727265", "1727265", "王五", "2099-08-24", Decimal("8.00")),
    ]
    assert records[0]["days"] == Decimal("1")
    assert summaries == {
        ("01727264", "病假时间总计"): Decimal("8.00"),
        ("01727265", "病假时间总计"): Decimal("8.00"),
        ("01278490", "病假时间总计"): Decimal("4.00"),
        ("01278490", "无薪病假"): Decimal("8.00"),
    }
    assert _reconcile(records, summaries) == []


def test_read_workbook_reports_row_errors_and_reconcile_mismatch(monkeypatch):
    _install_book(monkeypatch, _workbook_rows(
        [
            ["ZHANG, 张三", "01727264", _serial(2099, 8, 20), "法定病假", 8.0],
            ["ZHANG, 张三", "01727264", _serial(2099, 8, 21), "法定病假", 6.0],
            ["WANG, 王五", "117272650", _serial(2099, 8, 20), "法定病假", 8.0],
            ["ZHAO, 赵六", 1727266.5, _serial(2099, 8, 20), "法定病假", 8.0],
            ["QIAN, 钱七", "01727267", "not-a-date", "法定病假", 8.0],
        ],
        [["ZHANG, 张三", "01727264", "病假时间总计", 16.0]],
    ))

    records, summaries, errors = _read_workbook(b"ignored")

    assert [row["row"] for row in records] == [5]
    assert len(errors) == 4
    assert errors[0].startswith("事务区第 6 行") and "4 小时倍数" in errors[0]
    assert errors[1].startswith("事务区第 7 行") and "8 位数字" in errors[1]
    assert errors[2].startswith("事务区第 8 行") and "8 位数字" in errors[2]
    assert errors[3].startswith("事务区第 9 行") and "事务日期无效" in errors[3]
    mismatch = _reconcile(records, summaries)
    assert len(mismatch) == 1
    assert "总数区对账不一致" in mismatch[0] and "01727264" in mismatch[0]


def test_read_workbook_accepts_full_width_colon_markers(monkeypatch):
    _install_book(monkeypatch, _workbook_rows(
        [["ZHANG, 张三", 1727264.0, _serial(2099, 8, 20), "法定病假", 8.0]],
        [["ZHANG, 张三", "01727264", "病假时间总计", 8.0]],
        colon="：",
    ))

    records, summaries, errors = _read_workbook(b"ignored")

    assert errors == []
    assert [row["source_id"] for row in records] == ["01727264"]
    assert _reconcile(records, summaries) == []


def test_read_workbook_requires_block_markers(monkeypatch):
    _install_book(monkeypatch, [["员工", "ID", "日期", "工资代码", "时数"]])

    with pytest.raises(HTTPException) as excinfo:
        _read_workbook(b"ignored")

    assert excinfo.value.status_code == 400
    assert "事务:" in excinfo.value.detail


def test_preview_end_to_end_reports_unmatched_and_serves_marked_file(monkeypatch):
    _install_book(monkeypatch, _workbook_rows(
        [["NOBODY, 查无此人", 9999999.0, _serial(2099, 8, 20), "法定病假", 8.0]],
        [["NOBODY, 查无此人", "09999999", "病假时间总计", 8.0]],
    ))
    with TestClient(app) as client:
        login = client.post("/api/login", json={"employee_no": "GSMTEST01", "password": "1234"})
        assert login.status_code == 200, login.text
        response = client.post("/api/sick-leave-imports/preview", files={"workbook": ("transactions.xls", b"fake-xls")}, data={"month": "2099-08"})
        assert response.status_code == 200, response.text
        body = response.json()
        token = body["token"]
        try:
            assert body["month"] == "2099-08"
            assert body["blocking_errors"] == []
            assert body["matched_record_count"] == 0
            assert body["skipped_unmatched_count"] == 1
            assert body["can_commit"] is True
            assert [(row["source_id"], row["employee_no"], row["reason"]) for row in body["unmatched"]] == [
                ("09999999", "9999999", "系统中不存在该员工号"),
            ]

            download = client.get(f"/api/sick-leave-imports/{token}/unmatched-file")
            assert download.status_code == 200, download.text
            assert download.headers["content-type"].startswith(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            exported = load_workbook(BytesIO(download.content))
            assert exported.sheetnames == ["未覆盖说明", "员工事务"]
            notes = list(exported["未覆盖说明"].iter_rows(min_row=2, values_only=True))
            assert [(row[0], row[2], row[3], row[8]) for row in notes] == [(5, "09999999", "9999999", "系统中不存在该员工号")]
            assert exported["员工事务"].cell(row=5, column=1).fill.fgColor.rgb.endswith("FFC7CE")
            assert exported["员工事务"].cell(row=10, column=1).fill.fgColor.rgb.endswith("FFC7CE")

            committed = client.post("/api/sick-leave-imports/commit", json={"token": token, "month": "2099-08"})
            assert committed.status_code == 400, committed.text
            assert "复核未匹配员工" in committed.json()["detail"]
            committed = client.post("/api/sick-leave-imports/commit", json={"token": token, "month": "2099-08", "reviewed_unmatched": True})
            assert committed.status_code == 200, committed.text
            assert committed.json()["unmatched_count"] == 1
        finally:
            sick_leave_import._drop_preview(token)


def test_name_mismatch_is_skipped_after_registrant_review_and_old_sick_leave_is_covered(monkeypatch):
    month = "2099-11"
    with TestClient(app) as client:
        with SessionLocal() as db:
            employee = db.query(Employee).filter_by(employee_no="CMTEST01").one()
            submitter = db.query(Employee).filter_by(employee_no="GSMTEST01").one()
            old = SickLeaveRecord(
                employee_id=employee.id, employee_no_snapshot=employee.employee_no,
                employee_name_snapshot=employee.name, attendance_month=month,
                leave_start_date="2099-11-03", leave_end_date="2099-11-03",
                leave_days=Decimal("1"), charged_days=Decimal("1"),
                import_source="manual", status="active",
                submitted_by=submitter.id, submitted_by_name=submitter.name,
            )
            db.add(old)
            db.commit()
            old_id, employee_id = old.id, employee.id
        monkeypatch.setattr(sick_leave_import, "_read_workbook", lambda _: ([{
            "row": 5, "source_id": "01727264", "employee_no": "CMTEST01",
            "source_name": "WRONG, 姓名有误", "name": "姓名有误", "date": "2099-11-10",
            "leave_type": "法定病假", "hours": Decimal("8"), "days": Decimal("1"),
        }], {("01727264", "病假时间总计"): Decimal("8")}, []))
        monkeypatch.setattr(sick_leave_import, "_transaction_months", lambda _: {month})
        # This fixture replaces the BIFF parser with synthetic rows; provide the
        # independent honor reader the same unmatched identity evidence.
        from app.services import attendance_medals
        monkeypatch.setattr(attendance_medals, "read_all_leaves", lambda *args: ([], [
            {"employee_id": employee_id, "circle_id": employee.attraction_id, "reason": "姓名不匹配"}
        ], []))
        assert client.post("/api/login", json={"employee_no": "GSMTEST01", "password": "1234"}).status_code == 200
        preview = client.post("/api/sick-leave-imports/preview", files={"workbook": ("transactions.xls", b"fake-xls")}, data={"month": month})
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["can_commit"] is True
        assert body["unmatched"][0]["reason"].startswith("姓名不匹配")
        assert body["skipped_unmatched_count"] == 1
        committed = client.post("/api/sick-leave-imports/commit", json={"token": body["token"], "month": month, "reviewed_unmatched": True})
        assert committed.status_code == 200, committed.text
        assert committed.json()["unmatched_count"] == 1
        with SessionLocal() as db:
            assert db.get(SickLeaveRecord, old_id).status == "covered"
            assert db.query(SickLeaveRecord).filter_by(employee_id=employee_id, attendance_month=month, status="active").count() == 0


def test_preview_rejects_selected_month_mismatch_without_allowing_commit(monkeypatch):
    _install_book(monkeypatch, _workbook_rows(
        [["TEST, 测试CM甲", "0CMTEST01", _serial(2099, 8, 20), "法定病假", 8.0]],
        [["TEST, 测试CM甲", "0CMTEST01", "病假时间总计", 8.0]],
    ))
    with TestClient(app) as client:
        assert client.post("/api/login", json={"employee_no": "GSMTEST01", "password": "1234"}).status_code == 200
        response = client.post("/api/sick-leave-imports/preview", files={"workbook": ("transactions.xls", b"fake-xls")}, data={"month": "2099-07"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["can_commit"] is False
        assert any("月份不一致" in error for error in body["blocking_errors"])
        sick_leave_import._drop_preview(body["token"])


def test_empty_sick_leave_month_can_clear_old_records_when_other_transactions_prove_month(monkeypatch):
    month = "2099-09"
    _install_book(monkeypatch, _workbook_rows(
        [["TEST, 测试CM甲", "00000000", _serial(2099, 9, 10), "年假", 8.0]],
        [],
    ))
    with TestClient(app) as client:
        with SessionLocal() as db:
            employee = db.query(Employee).filter_by(employee_no="CMTEST01").one()
            submitter = db.query(Employee).filter_by(employee_no="GSMTEST01").one()
            old = SickLeaveRecord(
                employee_id=employee.id, employee_no_snapshot=employee.employee_no,
                employee_name_snapshot=employee.name, attendance_month=month,
                leave_start_date="2099-09-05", leave_end_date="2099-09-05",
                leave_days=Decimal("1"), charged_days=Decimal("1"),
                import_source="monthly_transaction_import", status="active",
                submitted_by=submitter.id, submitted_by_name=submitter.name,
            )
            db.add(old)
            db.commit()
            old_id, employee_id = old.id, employee.id
        assert client.post("/api/login", json={"employee_no": "GSMTEST01", "password": "1234"}).status_code == 200
        preview = client.post("/api/sick-leave-imports/preview", files={"workbook": ("transactions.xls", b"fake-xls")}, data={"month": month})
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["can_commit"] is True
        assert body["absent_employee_count"] >= 1
        committed = client.post("/api/sick-leave-imports/commit", json={"token": body["token"], "month": month})
        assert committed.status_code == 200, committed.text
        assert committed.json()["covered_record_count"] == 0
        with SessionLocal() as db:
            assert db.get(SickLeaveRecord, old_id).status == "covered"
            attendance = db.query(AttendanceMonthlyScore).filter_by(employee_id=employee_id, attendance_month=month).one()
            assert attendance.actual_sick_days == Decimal("0")


def test_monthly_replacement_rows_are_limited_to_circle_hr_scope():
    month = "2099-10"
    with TestClient(app):
        with SessionLocal() as db:
            own = db.query(Employee).filter_by(employee_no="HR-HEAT").one()
            other = db.query(Employee).filter_by(employee_no="HR-DWARF").one()
            assert own.attraction_id != other.attraction_id
            for employee in (own, other):
                db.add(SickLeaveRecord(
                    employee_id=employee.id, employee_no_snapshot=employee.employee_no,
                    employee_name_snapshot=employee.name, attendance_month=month,
                    leave_start_date="2099-10-05", leave_end_date="2099-10-05",
                    leave_days=Decimal("1"), charged_days=Decimal("1"),
                    import_source="manual", status="active",
                    submitted_by=own.id, submitted_by_name=own.name,
                ))
            db.flush()
            scoped = SimpleNamespace(role=SimpleNamespace(code="HR_CIRCLE"), employee=own)
            rows = sick_leave_import._active_month_rows(db, scoped, month)
            assert {row.employee_id for row in rows} == {own.id}
            db.rollback()


def test_expired_preview_token_explains_how_to_recover():
    with TestClient(app) as client:
        login = client.post("/api/login", json={"employee_no": "GSMTEST01", "password": "1234"})
        assert login.status_code == 200, login.text
        committed = client.post("/api/sick-leave-imports/commit", json={"token": "missing-token"})
        assert committed.status_code == 400, committed.text
        assert "服务重启" in committed.json()["detail"]
        assert "重新上传文件预检" in committed.json()["detail"]
        download = client.get("/api/sick-leave-imports/missing-token/unmatched-file")
        assert download.status_code == 404, download.text
        assert "重新上传文件预检" in download.json()["detail"]


@pytest.mark.parametrize('system,file,expected',[
 ('张森 Jason','张森',True),('张森 Jason','jAsOn',True),('张 森（Jason）','张森',True),
 ('张森Jason','张森 Jason',True),('张森','Jason',False),('张森 Jason','李森',False),
 ('张森 Jason','张',False),('张森 Jason','J',False),('张森 Jason','',False),
 ('Joanne 张森','Ann',False),('张森 Jason Smith','Jason Smith',True),
 ('张森 Jason','张森 Jason Smith',False),
])
def test_import_names_use_complete_contained_parts(system,file,expected):
    assert sick_leave_import._import_name_matches(system,file) is expected


@pytest.mark.parametrize('value,expected',[('01836527','1836527'),('1836527','1836527'),('00123456','0123456'),('0123456','0123456')])
def test_import_number_aliases_preserve_seven_digit_leading_zero(value,expected):
    assert _system_number(value)==expected


@pytest.mark.parametrize('value',['11836527','001836527','183652','ADMIN',''])
def test_import_does_not_drop_arbitrary_first_digit(value):
    with pytest.raises(ValueError):_system_number(value)


def _numeric_employee(name='张森 Jason',number=None):
    from uuid import uuid4
    from app.v2_models import EmployeeRoleAssignment,Role
    number=number or str(1000000+int(uuid4().hex[:8],16)%8999999)
    with SessionLocal() as db:
        reference=db.query(Employee).filter_by(employee_no='GSMTEST01').one()
        employee=Employee(employee_no=number,name=name,attraction_id=reference.attraction_id,is_active=True)
        db.add(employee);db.flush()
        db.add(EmployeeRoleAssignment(employee_id=employee.id,role_id=db.query(Role).filter_by(code='CM').one().id,starts_on='2020-01-01',status='active',assignment_type='permanent'))
        db.commit();return employee.id,number


def _numeric_preview(client,monkeypatch,number,file_name='张森',source=None):
    raw=source if source is not None else '0'+number
    _install_book(monkeypatch,_workbook_rows([[file_name,raw,'2098-06-20','法定病假',8]],[[file_name,raw,'病假时间总计',8]]))
    response=client.post('/api/sick-leave-imports/preview',files={'workbook':('numeric.xls',b'fake')},data={'month':'2098-06'})
    assert response.status_code==200,response.text
    return response.json()


@pytest.mark.parametrize('prefixed',[False,True])
@pytest.mark.parametrize('file_name',['张森','jason'])
def test_preview_and_commit_accept_aliases_and_contained_names(monkeypatch,prefixed,file_name):
    with TestClient(app) as client:
        assert client.post('/api/login',json={'employee_no':'GSMTEST01','password':'1234'}).status_code==200
        employee_id,number=_numeric_employee()
        preview=_numeric_preview(client,monkeypatch,number,file_name,source=('0'+number) if prefixed else number)
        assert preview['can_commit'] and preview['matched_employee_count']==1,preview
        row=preview['matched'][0]
        assert row['employee_id']==employee_id and row['system_employee_no']==number
        assert row['system_name']=='张森 Jason' and row['match_method']=='工号匹配、姓名包含匹配'
        result=client.post('/api/sick-leave-imports/commit',json={'token':preview['token'],'month':'2098-06'})
        assert result.status_code==200,result.text
        with SessionLocal() as db:
            row=db.query(SickLeaveRecord).filter_by(employee_id=employee_id,status='active').one()
            assert row.employee_name_snapshot=='张森 Jason' and row.employee_no_snapshot==number


def test_preview_resolves_eight_digit_stored_number_and_rejects_ambiguous_alias(monkeypatch):
    with TestClient(app) as client:
        client.post('/api/login',json={'employee_no':'GSMTEST01','password':'1234'})
        employee_id,number=_numeric_employee()
        with SessionLocal() as db:
            db.get(Employee,employee_id).employee_no='0'+number;db.commit()
        preview=_numeric_preview(client,monkeypatch,number)
        assert preview['matched'][0]['employee_id']==employee_id
        _numeric_employee(number=number)
        preview=_numeric_preview(client,monkeypatch,number)
        assert not preview['can_commit'] and not preview['matched']
        assert '多个档案' in preview['unmatched'][0]['reason']
        assert client.post('/api/sick-leave-imports/commit',json={'token':preview['token'],'month':'2098-06','reviewed_unmatched':True}).status_code==400


@pytest.mark.parametrize('change',['name','number','ambiguous','inactive','circle'])
def test_commit_revalidates_identity_and_scope_before_covering_old_rows(monkeypatch,change):
    from app.v2_models import Attraction
    from app.v2_crypto import hash_password
    from app.v2_models import UserAccount
    with TestClient(app) as client:
        employee_id,number=_numeric_employee()
        with SessionLocal() as db:
            account=db.query(UserAccount).filter_by(login_account='HR-HEAT').one()
            account.enabled=True;account.must_change_password=False;account.password_hash=hash_password('1234')
            own=db.get(Employee,account.employee_id).attraction_id
            db.get(Employee,employee_id).attraction_id=own;db.commit()
        assert client.post('/api/login',json={'employee_no':'HR-HEAT','password':'1234'}).status_code==200
        preview=_numeric_preview(client,monkeypatch,number)
        assert len(preview['matched'])==1,preview
        with SessionLocal() as db:
            employee=db.get(Employee,employee_id)
            if change=='name':employee.name='完全不同的人'
            if change=='number':employee.employee_no=str(int(number)+1)
            if change=='inactive':employee.is_active=False
            if change=='circle':employee.attraction_id=db.query(Attraction).filter(Attraction.employee_circle.is_(True),Attraction.id!=own).first().id
            db.commit()
        if change=='ambiguous':_numeric_employee(number='0'+number)
        with SessionLocal() as db:
            before=[(r.id,r.status) for r in db.query(SickLeaveRecord).filter_by(attendance_month='2098-06').all()]
        response=client.post('/api/sick-leave-imports/commit',json={'token':preview['token'],'month':'2098-06'})
        assert response.status_code in (403,409),response.text
        with SessionLocal() as db:
            assert [(r.id,r.status) for r in db.query(SickLeaveRecord).filter_by(attendance_month='2098-06').all()]==before


def test_preview_never_uses_a_name_to_find_another_employee(monkeypatch):
    with TestClient(app) as client:
        client.post('/api/login',json={'employee_no':'GSMTEST01','password':'1234'})
        employee_id,number=_numeric_employee()
        preview=_numeric_preview(client,monkeypatch,number,'完全不同')
        assert not preview['matched'] and '姓名不匹配' in preview['unmatched'][0]['reason']
        preview=_numeric_preview(client,monkeypatch,'9999998','张森')
        assert not preview['matched'] and '不存在' in preview['unmatched'][0]['reason']


def _commit_preview(client,preview):
    return client.post('/api/sick-leave-imports/commit',json={'token':preview['token'],'month':preview['month'],'reviewed_unmatched':True})


def _month_snapshot(month='2098-06'):
    with SessionLocal() as db:
        return [(r.id,r.status,str(r.leave_days),r.employee_name_snapshot,r.employee_no_snapshot) for r in db.query(SickLeaveRecord).filter_by(attendance_month=month).order_by(SickLeaveRecord.id)]


def test_real_biff_workbook_numeric_aliases_names_reconciliation_and_marked_download():
    content=(Path(__file__).parent/'fixtures/sick_leave_identity.xls').read_bytes()
    records,summaries,errors=_read_workbook(content)
    assert not errors and not _reconcile(records,summaries)
    assert [r['employee_no'] for r in records]==['1836527','1836527','0123456','1234567']
    with TestClient(app) as client:
        client.post('/api/login',json={'employee_no':'GSMTEST01','password':'1234'})
        for number,name in [('1836527','张森 Jason'),('0123456','李森 Jason'),('1234567','Joanne')]:
            _numeric_employee(name,number)
        response=client.post('/api/sick-leave-imports/preview',files={'workbook':('real.xls',content)},data={'month':'2096-06'})
        assert response.status_code==200,response.text
        preview=response.json();assert preview['matched_record_count']==3 and len(preview['unmatched'])==1
        assert preview['unmatched'][0]['name']=='Ann'
        downloaded=client.get(f"/api/sick-leave-imports/{preview['token']}/unmatched-file")
        assert downloaded.status_code==200,downloaded.text
        marked=load_workbook(BytesIO(downloaded.content));ws=marked['测试事务']
        assert ws.cell(8,1).fill.fgColor.rgb.endswith('FFC7CE')
        assert ws.cell(15,1).fill.fgColor.rgb.endswith('FFC7CE')
        assert not ws.cell(5,1).fill.fgColor.rgb.endswith('FFC7CE')
        result=_commit_preview(client,preview);assert result.status_code==200,result.text
        assert result.json()['covered_record_count']==3


@pytest.mark.parametrize('account',['CMTEST01','TRTEST01','SUPTEST01','TATEST01','TAGSMTEST01','AMTEST01','OMTEST01'])
def test_import_api_roles_cannot_bypass_entrance(account):
    with TestClient(app) as client:
        assert client.post('/api/login',json={'employee_no':account,'password':'1234'}).status_code==200
        assert client.post('/api/sick-leave-imports/preview',files={'workbook':('fake.xls',b'fake')},data={'month':'2098-06'}).status_code==403
        assert client.post('/api/sick-leave-imports/commit',json={'token':'fake','month':'2098-06'}).status_code==403
        assert client.get('/api/sick-leave-imports/fake/unmatched-file').status_code==403


@pytest.mark.parametrize('invalid',['other_user','expired','evicted'])
def test_preview_commit_and_file_are_bound_to_owner_and_lifetime(monkeypatch,invalid):
    with TestClient(app) as client:
        client.post('/api/login',json={'employee_no':'GSMTEST01','password':'1234'})
        _,number=_numeric_employee();preview=_numeric_preview(client,monkeypatch,number)
        before=_month_snapshot()
        if invalid=='other_user':
            client.post('/api/logout');assert client.post('/api/login',json={'employee_no':'HR01','password':'HR123'}).status_code==200
        elif invalid=='expired':
            with sick_leave_import._PREVIEWS_LOCK:sick_leave_import._PREVIEWS[preview['token']]['created_at']=datetime.now()-timedelta(minutes=21)
        else:sick_leave_import._drop_preview(preview['token'])
        assert _commit_preview(client,preview).status_code==400
        assert client.get(f"/api/sick-leave-imports/{preview['token']}/unmatched-file").status_code==404
        assert _month_snapshot()==before


def _login_circle(client):
    from app.v2_models import UserAccount
    from app.v2_crypto import hash_password
    with SessionLocal() as db:
        account=db.query(UserAccount).filter_by(login_account='HR-HEAT').one()
        account.enabled=True;account.must_change_password=False;account.password_hash=hash_password('1234')
        own=db.get(Employee,account.employee_id).attraction_id;actor=account.employee_id;db.commit()
    assert client.post('/api/login',json={'employee_no':'HR-HEAT','password':'1234'}).status_code==200
    return own,actor


def test_circle_preview_cannot_match_other_circle_or_reuse_scope_changed_token(monkeypatch):
    from app.v2_models import Attraction
    with TestClient(app) as client:
        own,actor=_login_circle(client);employee_id,number=_numeric_employee()
        with SessionLocal() as db:
            other=db.query(Attraction).filter(Attraction.employee_circle.is_(True),Attraction.id!=own).first().id
            db.get(Employee,employee_id).attraction_id=other;db.commit()
        preview=_numeric_preview(client,monkeypatch,number)
        assert not preview['matched'] and '景点圈范围' in preview['unmatched'][0]['reason']
        assert 'system_name' not in preview['unmatched'][0]
        with SessionLocal() as db:db.get(Employee,employee_id).attraction_id=own;db.commit()
        preview=_numeric_preview(client,monkeypatch,number);assert len(preview['matched'])==1
        before=_month_snapshot()
        with SessionLocal() as db:db.get(Employee,actor).attraction_id=other;db.commit()
        try:
            assert _commit_preview(client,preview).status_code==409
            assert client.get(f"/api/sick-leave-imports/{preview['token']}/unmatched-file").status_code==409
            assert _month_snapshot()==before
        finally:
            with SessionLocal() as db:db.get(Employee,actor).attraction_id=own;db.commit()


@pytest.mark.parametrize('change',['cancel','date','name'])
def test_loa_protected_rows_are_revalidated_before_covering(monkeypatch,change):
    from app.v2_models import EmployeeLOAPeriod
    with TestClient(app) as client:
        client.post('/api/login',json={'employee_no':'GSMTEST01','password':'1234'})
        actor=client.get('/api/me').json()['id'];employee_id,number=_numeric_employee()
        with SessionLocal() as db:
            period=EmployeeLOAPeriod(employee_id=employee_id,starts_on='2098-06-20',ends_on='2098-06-21',status='active',created_by=actor,created_by_name='测试')
            db.add(period);db.commit();period_id=period.id
        preview=_numeric_preview(client,monkeypatch,number)
        assert not preview['matched'] and len(preview['loa_protected'])==1
        before=_month_snapshot()
        with SessionLocal() as db:
            period=db.get(EmployeeLOAPeriod,period_id)
            if change=='cancel':period.status='cancelled'
            elif change=='date':period.starts_on='2098-06-21'
            else:db.get(Employee,employee_id).name='完全不同'
            db.commit()
        assert _commit_preview(client,preview).status_code==409
        assert _month_snapshot()==before


@pytest.mark.parametrize('same_token',[True,False])
def test_concurrent_month_cover_commits_once_and_rejects_old_preview(monkeypatch,same_token):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.v2_models import AuditLog
    with TestClient(app) as client:
        client.post('/api/login',json={'employee_no':'GSMTEST01','password':'1234'})
        _,number=_numeric_employee();first=_numeric_preview(client,monkeypatch,number)
        second=first if same_token else _numeric_preview(client,monkeypatch,number)
        cookies=dict(client.cookies);barrier=Barrier(2)
        with SessionLocal() as db:audits=db.query(AuditLog).filter_by(action='覆盖月度病假事务').count()
        def submit(preview):
            with TestClient(app) as c:
                c.cookies.update(cookies);barrier.wait(timeout=10)
                return _commit_preview(c,preview).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(submit,[first,second]))
        assert results.count(200)==1 and all(s in (200,400,409) for s in results),results
        with SessionLocal() as db:
            assert db.query(AuditLog).filter_by(action='覆盖月度病假事务').count()==audits+1
        before=_month_snapshot();assert _commit_preview(client,first).status_code in (400,409)
        assert _month_snapshot()==before


def test_failed_audit_rolls_back_cover_and_releases_preview_for_retry(monkeypatch):
    with TestClient(app) as client:
        client.post('/api/login',json={'employee_no':'GSMTEST01','password':'1234'})
        _,number=_numeric_employee();preview=_numeric_preview(client,monkeypatch,number);before=_month_snapshot()
        original=sick_leave_import.write_audit
        def fail(*args,**kwargs):raise RuntimeError('test audit failure')
        monkeypatch.setattr(sick_leave_import,'write_audit',fail)
        with pytest.raises(RuntimeError,match='audit failure'):_commit_preview(client,preview)
        assert _month_snapshot()==before
        monkeypatch.setattr(sick_leave_import,'write_audit',original)
        result=_commit_preview(client,preview);assert result.status_code==200,result.text


def test_month_close_added_after_preview_blocks_cover_without_writes(monkeypatch):
    from app.v2_models import MonthClosure
    with TestClient(app) as client:
        client.post('/api/login',json={'employee_no':'GSMTEST01','password':'1234'})
        _,number=_numeric_employee();preview=_numeric_preview(client,monkeypatch,number);before=_month_snapshot()
        with SessionLocal() as db:
            closed=MonthClosure(closure_month='2098-06',attraction_id=None,status='closed');db.add(closed);db.commit();closure_id=closed.id
        try:
            assert _commit_preview(client,preview).status_code==423
            assert _month_snapshot()==before
        finally:
            with SessionLocal() as db:db.delete(db.get(MonthClosure,closure_id));db.commit()


def test_marked_download_keeps_formula_like_source_text_inert(monkeypatch):
    with TestClient(app) as client:
        client.post('/api/login',json={'employee_no':'GSMTEST01','password':'1234'})
        preview=_numeric_preview(client,monkeypatch,'9999997','=1+1')
        response=client.get(f"/api/sick-leave-imports/{preview['token']}/unmatched-file")
        assert response.status_code==200,response.text
        book=load_workbook(BytesIO(response.content))
        assert book['员工事务'].cell(5,1).data_type!='f'
        assert book['未覆盖说明'].cell(2,2).data_type!='f'


@pytest.mark.parametrize('change',['name_still_contains','alias_spelling','global_circle'])
def test_global_operator_must_repreview_if_displayed_identity_changes(monkeypatch,change):
    from app.v2_models import Attraction
    with TestClient(app) as client:
        client.post('/api/login',json={'employee_no':'GSMTEST01','password':'1234'})
        employee_id,number=_numeric_employee();preview=_numeric_preview(client,monkeypatch,number);before=_month_snapshot()
        with SessionLocal() as db:
            employee=db.get(Employee,employee_id)
            if change=='name_still_contains':employee.name='张森 Jason 新名称'
            elif change=='alias_spelling':employee.employee_no='0'+number
            else:employee.attraction_id=db.query(Attraction).filter(Attraction.employee_circle.is_(True),Attraction.id!=employee.attraction_id).first().id
            db.commit()
        assert _commit_preview(client,preview).status_code==409
        assert _month_snapshot()==before


def test_backup_written_after_preview_cannot_be_silently_overwritten(monkeypatch):
    with TestClient(app) as client:
        client.post('/api/login',json={'employee_no':'GSMTEST01','password':'1234'})
        employee_id,number=_numeric_employee();preview=_numeric_preview(client,monkeypatch,number)
        backup=client.post('/api/sick-leave-imports/single',data={'employee_id':employee_id,'leave_start_date':'2098-06-19','leave_end_date':'2098-06-19','leave_days':'1','leave_type':'法定病假'})
        assert backup.status_code==200,backup.text
        before=_month_snapshot();assert _commit_preview(client,preview).status_code==409
        assert _month_snapshot()==before


def test_commit_waiting_for_write_lock_rechecks_expired_preview(monkeypatch):
    from sqlalchemy.orm import Session
    with TestClient(app) as client:
        client.post('/api/login',json={'employee_no':'GSMTEST01','password':'1234'})
        _,number=_numeric_employee();preview=_numeric_preview(client,monkeypatch,number);before=_month_snapshot()
        original=Session.execute
        def execute(db,statement,*args,**kwargs):
            result=original(db,statement,*args,**kwargs)
            if getattr(statement,'is_update',False) and getattr(getattr(statement,'table',None),'name',None)=='employees':
                with sick_leave_import._PREVIEWS_LOCK:
                    cached=sick_leave_import._PREVIEWS.get(preview['token'])
                    if cached:cached['created_at']=datetime.now()-timedelta(minutes=21)
            return result
        monkeypatch.setattr(Session,'execute',execute)
        assert _commit_preview(client,preview).status_code==400
        assert _month_snapshot()==before


def test_circle_month_cover_ignores_injected_other_circle_targets(monkeypatch):
    from app.v2_models import Attraction
    with TestClient(app) as client:
        own,actor=_login_circle(client)
        own_id,number=_numeric_employee();other_id,_=_numeric_employee()
        with SessionLocal() as db:
            db.get(Employee,own_id).attraction_id=own
            other=db.query(Attraction).filter(Attraction.employee_circle.is_(True),Attraction.id!=own).first().id
            db.get(Employee,other_id).attraction_id=other;db.commit()
        client.post('/api/logout');client.post('/api/login',json={'employee_no':'GSMTEST01','password':'1234'})
        result=client.post('/api/sick-leave-imports/single',data={'employee_id':other_id,'leave_start_date':'2098-06-18','leave_end_date':'2098-06-18','leave_days':'1','leave_type':'法定病假'})
        assert result.status_code==200,result.text
        other_record=result.json()['record']['id']
        client.post('/api/logout');_login_circle(client)
        preview=_numeric_preview(client,monkeypatch,number)
        result=client.post('/api/sick-leave-imports/commit',json={'token':preview['token'],'month':preview['month'],
            'attraction_id':other,'scope':[other],'employee_id':other_id,'matched':[{'employee_id':other_id}]})
        assert result.status_code==200,result.text
        assert result.json()['covered_employee_count']==1
        with SessionLocal() as db:
            assert db.get(SickLeaveRecord,other_record).status=='active'
            assert db.query(SickLeaveRecord).filter_by(employee_id=own_id,status='active').count()==1
            assert db.query(SickLeaveRecord).filter_by(employee_id=other_id,status='active').count()==1


def test_second_request_cannot_release_another_inflight_preview(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    entered,released=Event(),Event()
    original=sick_leave_import.write_audit
    def hold(db,*args,**kwargs):
        result=original(db,*args,**kwargs)
        if len(args)>1 and args[1]=='覆盖月度病假事务':
            entered.set();assert released.wait(10),'test did not release the first writer'
        return result
    with TestClient(app) as first,TestClient(app) as second:
        first.post('/api/login',json={'employee_no':'GSMTEST01','password':'1234'})
        _,number=_numeric_employee();preview=_numeric_preview(first,monkeypatch,number)
        second.cookies.update(dict(first.cookies));monkeypatch.setattr(sick_leave_import,'write_audit',hold)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(_commit_preview,first,preview)
            try:
                assert entered.wait(10)
                response=_commit_preview(second,preview)
                assert response.status_code==409,response.text
                with sick_leave_import._PREVIEWS_LOCK:
                    assert sick_leave_import._PREVIEWS[preview['token']]['committing'] is True
            finally:released.set()
            assert future.result(timeout=10).status_code==200
        before=_month_snapshot();assert _commit_preview(second,preview).status_code==400
        assert _month_snapshot()==before


def test_database_commit_failure_preserves_history_and_allows_safe_retry(monkeypatch):
    from sqlalchemy.orm import Session
    from app.v2_models import AuditLog
    audit=sick_leave_import.write_audit;commit=Session.commit;fail=[True]
    def mark(db,*args,**kwargs):
        result=audit(db,*args,**kwargs)
        if len(args)>1 and args[1]=='覆盖月度病假事务':db.info['test_cover_commit']=True
        return result
    def commit_once(db):
        if db.info.get('test_cover_commit') and fail[0]:
            fail[0]=False;raise RuntimeError('test database commit failure')
        return commit(db)
    with TestClient(app) as client:
        client.post('/api/login',json={'employee_no':'GSMTEST01','password':'1234'})
        _,number=_numeric_employee();preview=_numeric_preview(client,monkeypatch,number);before=_month_snapshot()
        with SessionLocal() as db:before_audit=db.query(AuditLog).filter_by(action='覆盖月度病假事务').count()
        monkeypatch.setattr(sick_leave_import,'write_audit',mark);monkeypatch.setattr(Session,'commit',commit_once)
        with pytest.raises(RuntimeError,match='database commit failure'):_commit_preview(client,preview)
        assert _month_snapshot()==before
        with SessionLocal() as db:assert db.query(AuditLog).filter_by(action='覆盖月度病假事务').count()==before_audit
        cached=sick_leave_import._get_preview(preview['token'],client.get('/api/me').json()['id'])
        assert cached and not cached.get('committing')
        assert _commit_preview(client,preview).status_code==200
        after=_month_snapshot();assert _commit_preview(client,preview).status_code==400
        assert _month_snapshot()==after


@pytest.mark.parametrize('invalid',['month','unreviewed'])
def test_rejected_request_does_not_consume_or_claim_valid_preview(monkeypatch,invalid):
    with TestClient(app) as client:
        client.post('/api/login',json={'employee_no':'GSMTEST01','password':'1234'})
        _,number=_numeric_employee()
        preview=_numeric_preview(client,monkeypatch,number,'完全不同' if invalid=='unreviewed' else '张森')
        before=_month_snapshot()
        response=client.post('/api/sick-leave-imports/commit',json={'token':preview['token'],'month':'2098-07' if invalid=='month' else preview['month']})
        assert response.status_code==400,response.text
        cached=sick_leave_import._get_preview(preview['token'],client.get('/api/me').json()['id'])
        assert cached and not cached.get('committing') and _month_snapshot()==before
        result=_commit_preview(client,preview);assert result.status_code==200,result.text


@pytest.mark.parametrize('change',['role_revoked','account_disabled'])
def test_existing_preview_cannot_bypass_lost_role_or_disabled_account(monkeypatch,change):
    from app.v2_models import UserAccount,EmployeeRoleAssignment,Role
    from app.v2_crypto import hash_password
    with TestClient(app) as client:
        actor,login_no=_numeric_employee('测试导入人')
        with SessionLocal() as db:
            assignment=db.query(EmployeeRoleAssignment).filter_by(employee_id=actor,status='active').one()
            assignment.role_id=db.query(Role).filter_by(code='GSM').one().id
            db.add(UserAccount(employee_id=actor,login_account=login_no,password_hash=hash_password('1234'),enabled=True,must_change_password=False,credential_initialized=True));db.commit()
        assert client.post('/api/login',json={'employee_no':login_no,'password':'1234'}).status_code==200
        _,number=_numeric_employee();preview=_numeric_preview(client,monkeypatch,number);before=_month_snapshot()
        with SessionLocal() as db:
            if change=='role_revoked':
                db.query(EmployeeRoleAssignment).filter_by(employee_id=actor,status='active').one().role_id=db.query(Role).filter_by(code='CM').one().id
            else:db.query(UserAccount).filter_by(employee_id=actor).one().enabled=False
            db.commit()
        expected=403 if change=='role_revoked' else 401
        assert _commit_preview(client,preview).status_code==expected
        assert client.get(f"/api/sick-leave-imports/{preview['token']}/unmatched-file").status_code==expected
        assert _month_snapshot()==before


def test_concurrent_distinct_circle_imports_do_not_block_or_cover_each_other(monkeypatch):
    from app.v2_models import UserAccount
    from app.v2_crypto import hash_password
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    with TestClient(app) as heat,TestClient(app) as dwarf:
        own,actor=_login_circle(heat)
        with SessionLocal() as db:
            account=db.query(UserAccount).filter_by(login_account='HR-DWARF').one()
            account.enabled=True;account.must_change_password=False;account.password_hash=hash_password('1234')
            other=db.get(Employee,account.employee_id).attraction_id;db.commit()
        assert dwarf.post('/api/login',json={'employee_no':'HR-DWARF','password':'1234'}).status_code==200
        own_id,number=_numeric_employee();other_id,other_no=_numeric_employee()
        with SessionLocal() as db:
            db.get(Employee,own_id).attraction_id=own;db.get(Employee,other_id).attraction_id=other;db.commit()
        a=_numeric_preview(heat,monkeypatch,number);b=_numeric_preview(dwarf,monkeypatch,other_no)
        assert a['matched_employee_count']==b['matched_employee_count']==1
        barrier=Barrier(2)
        def submit(args):
            client,preview=args;barrier.wait(timeout=10);return _commit_preview(client,preview)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(submit,[(heat,a),(dwarf,b)]))
        assert all(r.status_code==200 for r in results),[r.text for r in results]
        with SessionLocal() as db:
            assert db.query(SickLeaveRecord).filter_by(employee_id=own_id,status='active').count()==1
            assert db.query(SickLeaveRecord).filter_by(employee_id=other_id,status='active').count()==1
