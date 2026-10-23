"""Date normalization and real Excel import transactions, using fictional employees."""
from datetime import date, datetime
from io import BytesIO
import os
from pathlib import Path
import tempfile

import pytest
from openpyxl import Workbook
from fastapi.testclient import TestClient

os.environ['RECOGNITION_V2_DATA_DIR'] = str(Path(tempfile.mkdtemp(prefix='fz-import-dates-')))
os.environ['RECOGNITION_ENABLE_TEST_ACCOUNTS'] = '1'
os.environ['RECOGNITION_TEST_DEFAULT_PASSWORD'] = 'import-dates-fixture'
os.environ['RECOGNITION_TEST_ADMIN_PASSWORD'] = 'import-dates-admin'

from app.services.employee_import_dates import normalize_import_date
from app.main import app
from app.v2_database import SessionLocal
from app.v2_models import Employee, EmployeeRoleAssignment


@pytest.mark.parametrize('value,expected', [
    (None, ''), ('  ', ''), (date(2026, 12, 4), '2026-12-04'),
    (datetime(2026, 12, 18, 13, 30), '2026-12-18'),
    ('12/4/2026 到期', '2026-12-04'), ('12/18/2026 到期', '2026-12-18'),
    ('11/21/2026 到\n期', '2026-11-21'), ('11/21/2026\r\n到期', '2026-11-21'),
    (' 12/18/2026到期 ', '2026-12-18'), ('2026-12-04', '2026-12-04'),
    ('2026/12/4', '2026-12-04'), ('2026.12.4', '2026-12-04'),
    ('2026年12月4日 到期', '2026-12-04'), ('4/12/2026', '2026-04-12'),
    ('2028-2-29', '2028-02-29')])
def test_normalization(value, expected):
    assert normalize_import_date(value) == expected


@pytest.mark.parametrize('value', ['2026-02-29', '2026-13-1', '18/12/2026', '12/4/26',
    '12/4/2026 junk', '到期', 46360, '2026/12-4', '2026-12-04 12:00:00'])
def test_invalid_dates_are_not_guessed(value):
    with pytest.raises(ValueError): normalize_import_date(value)


def workbook(rows):
    wb = Workbook(); ws = wb.active
    ws.append(['员工号','姓名','角色代码','景点圈','Type','实习结束日期'])
    for row in rows: ws.append(row)
    out = BytesIO(); wb.save(out)
    return {'workbook': ('dates.xlsx', out.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}


def row(no, end='', kind='Intern'):
    return [no, '日期导入模拟员工'+no[-1], 'TR', '热力追踪', kind, end]


def test_real_import_normalizes_mixed_excel_dates_and_rejects_whole_invalid_batch():
    with TestClient(app) as client:
        login = client.post('/api/login', json={'employee_no': 'HR01', 'password': 'import-dates-admin'})
        assert login.status_code == 200, login.text
        response = client.post('/api/hr/import-employees', files=workbook([
            row('9876101', date(2099,12,4)), row('9876102','12/18/2099 到期'),
            row('9876103',''), row('9876104',datetime(2099,12,18))]))
        assert response.status_code == 200, response.text
        assert response.json()['created'] == 4
        with SessionLocal() as db:
            employee = db.query(Employee).filter_by(employee_no='9876101').one()
            assignment = db.query(EmployeeRoleAssignment).filter_by(employee_id=employee.id).one()
            assert employee.internship_ends_on == '2099-12-04'
            assert assignment.starts_on == date.today().isoformat() and assignment.ends_on is None
            assert assignment.assignment_type == 'permanent'
            employee = db.query(Employee).filter_by(employee_no='9876103').one()
            assert employee.hired_on == date.today().isoformat() and employee.internship_ends_on is None
        for bad_end, kind in [('2026-02-30','Intern'),('12/4/2099 到期','Comp28'),('12/4/2099 到期','')]:
            response = client.post('/api/hr/import-employees', files=workbook([
                row('9876190'), row('9876191',bad_end,kind)]))
            assert response.status_code == 400 and '第3行' in response.text, response.text
            with SessionLocal() as db:
                assert not db.query(Employee).filter(Employee.employee_no.in_(['9876190','9876191'])).count()
