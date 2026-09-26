from __future__ import annotations
import os
from pathlib import Path
import tempfile
from io import BytesIO
import json
import zipfile
import xml.etree.ElementTree as ET

os.environ["RECOGNITION_V2_DATA_DIR"] = tempfile.mkdtemp(prefix="hr-report-test-")
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from fastapi.testclient import TestClient
from app.main import app
from app.hr_monthly_pptx import NS, ROOT
from app.hr_monthly_report import REPORT_ROLES
from app.v2_database import ROLE_PERMISSION_CODES
from app.hr_monthly_pptx import build_pptx


def login(client, name="GSMTEST01", password="1234"):
    response = client.post("/api/login", json={"employee_no": name, "password": password})
    assert response.status_code == 200, response.text


def test_permission_mapping_explicit():
    assert {r for r, p in ROLE_PERMISSION_CODES.items() if "HR_MONTHLY_REPORT" in p} == REPORT_ROLES


def test_preview_and_export_all_templates():
    with TestClient(app) as client:
        login(client)
        options = client.get("/api/hr-monthly-reports/options")
        assert options.status_code == 200, options.text
        assert len(options.json()["templates"]) == 3
        response = client.get("/api/hr-monthly-reports/preview?month=2026-09")
        assert response.status_code == 200, response.text
        preview = response.json()
        data = preview["report"]
        assert data["summary"]["employee_count"] == sum(r["employee_count"] for r in data["circles"])
        for template in ("forest", "minimal", "warm"):
            result = client.post("/api/hr-monthly-reports/export", data={"config": json.dumps({"month": "2026-09", "template": template, "preview_digest": preview["digest"]})})
            assert result.status_code == 200, result.text[:300] if result.status_code != 200 else ""
            with zipfile.ZipFile(BytesIO(result.content)) as archive:
                slide_names = [n for n in archive.namelist() if n.startswith("ppt/slides/report") and n.endswith(".xml")]
                assert slide_names
                text = "".join(archive.read(n).decode() for n in slide_names)
                assert "示例" not in text and "9000001" not in text
                assert "960 条" not in text
                assert "CONFIDENTIAL" in text
                assert not any("notesSlide" in n for n in archive.namelist())
                assert not any(n.startswith("ppt/slides/slide") and n.endswith(".xml") for n in archive.namelist())
                assert str(data["summary"]["employee_count"]) + " 人" in text
                for n in archive.namelist():
                    if n.endswith(".xml") or n.endswith(".rels"):
                        ET.fromstring(archive.read(n))


def test_scope_validation_and_stale_preview():
    with TestClient(app) as client:
        login(client)
        assert client.get("/api/hr-monthly-reports/preview?month=2026-13").status_code == 400
        assert client.get("/api/hr-monthly-reports/preview?month=2026-09&attraction_id=99999").status_code == 400
        assert client.post("/api/hr-monthly-reports/export", data={"config": json.dumps({"month": "2026-09", "preview_digest": "stale"})}).status_code == 409
        assert client.post("/api/hr-monthly-reports/export", content=b"", headers={"Content-Length": str(33 * 1024 * 1024)}).status_code == 413
        for circle in client.get("/api/hr-monthly-reports/options").json()["attractions"]:
            response = client.get("/api/hr-monthly-reports/preview", params={"month": "2026-07", "attraction_id": circle["id"]})
            assert response.status_code == 200, response.text
            assert len(response.json()["report"]["circles"]) == 1


def test_unauthorized_endpoints():
    with TestClient(app) as client:
        assert client.get("/api/hr-monthly-reports/options").status_code == 401
        login(client, "CMTEST01")
        assert client.get("/api/hr-monthly-reports/options").status_code == 403
        assert client.get("/api/hr-monthly-reports/preview?month=2026-09").status_code == 403
        assert client.post("/api/hr-monthly-reports/export", data={"config": '{"month":"2026-09"}'}).status_code == 403


def test_all_role_guards():
    with TestClient(app) as client:
        for account, password, allowed in (("AMTEST01", "1234", True), ("OMTEST01", "1234", True), ("HR01", "HR123", True), ("TAGSMTEST01", "1234", False), ("SUPTEST01", "1234", False)):
            login(client, account, password)
            assert client.get("/api/hr-monthly-reports/options").status_code == (200 if allowed else 403)


def test_native_charts_tables_pagination_and_photos(tmp_path):
    from PIL import Image
    from openpyxl import load_workbook
    from app.routers.hr_monthly_reports import prepare_photo
    with TestClient(app) as client:
        login(client)
        data = client.get("/api/hr-monthly-reports/preview?month=2026-09").json()["report"]
    data["deductions"] = [{"attraction_name": data["circles"][0]["name"], "category": "CM", "type": "安全", "count": 11}]
    data["recognitions"] = [{"attraction_name": data["circles"][0]["name"], "category": "TR", "type": "服务", "count": 29}]
    data["trend"] = [{"month": "2026-07", "rate": 150}, {"month": "2026-08", "rate": None}, {"month": "2026-09", "rate": 240}]
    row = {"employee_name": "测试员工", "employee_no": "1234567", "recognition_score": 1, "deduction_score": 2, "attendance_score": 12, "total_score": 11}
    data["rankings"] = [{"category": "CM", "attraction_name": "测试景点圈", "rows": [{**row, "rank": i + 1} for i in range(19)]}]
    photo = BytesIO()
    Image.new("RGB", (800, 400), (80, 140, 80)).save(photo, "PNG")
    for theme in ("forest", "minimal", "warm"):
        content = build_pptx(data, theme, {"overview", "deductions", "recognitions", "recognition_stats", "rankings", "photos", "notes", "birthdays", "excellent", "closing"}, {data["candidates"][0]["employee_id"]}, "这是测试工作说明。\n" * 12, "这是测试生日祝福。\n" * 10, [prepare_photo(photo.getvalue())] * 5, "测试活动", "CONFIDENTIAL TEST")
        target = tmp_path / f"{theme}.pptx"
        target.write_bytes(content)
        with zipfile.ZipFile(BytesIO(content)) as archive:
            slides = [ET.fromstring(archive.read(n)) for n in archive.namelist() if n.startswith("ppt/slides/report") and n.endswith(".xml")]
            ranks = [s for s in slides if "PR排名：CM" in ''.join(t.text or '' for t in s.findall('.//a:t', NS))]
            assert all("CONFIDENTIAL TEST" in ''.join(t.text or '' for t in s.findall('.//a:t', NS)) for s in slides)
            assert "示例" not in ''.join(t.text or '' for s in slides for t in s.findall('.//a:t', NS))
            assert len(ranks) == 3
            assert sum(len(s.findall('.//a:tbl/a:tr', NS)) - 1 for s in ranks) == 19
            pics = [p for s in slides for p in s.findall('.//p:pic', NS) if p.find('p:nvPicPr/p:cNvPr', NS).get('name') == '活动照片']
            assert len(pics) == 5
            for pic in pics:
                ext = pic.find('p:spPr/a:xfrm/a:ext', NS)
                assert int(ext.get('cx')) == int(ext.get('cy')) * 2
                assert int(ext.get('cy')) > 1000000
            books = [n for n in archive.namelist() if n.endswith('.xlsx')]
            assert len(books) == 3
            for name in books:
                workbook = load_workbook(BytesIO(archive.read(name)))
                assert not any('示例' in str(c.value) for row in workbook.active for c in row)
    # Optionally retain an isolated synthetic report for visual QA, never real staff.
    if os.environ.get("HR_REPORT_QA_DIR"):
        destination = Path(os.environ["HR_REPORT_QA_DIR"])
        destination.mkdir(parents=True, exist_ok=True)
        for theme in ("forest", "minimal", "warm"):
            (destination / f"{theme}.pptx").write_bytes((tmp_path / f"{theme}.pptx").read_bytes())


def test_history_loa_credited_scores_and_read_only():
    from app.v2_database import SessionLocal
    from app.v2_models import Employee, Attraction, RecognitionRecord, EmployeeMonthOrganizationSnapshot, EmployeeLOAPeriod, AttendanceMonthlyScore, GroupMembership
    from app.hr_monthly_report import report_data
    with TestClient(app):
        with SessionLocal() as db:
            employee = db.query(Employee).filter_by(employee_no="CMTEST01").one()
            actor = db.query(Employee).filter_by(employee_no="GSMTEST01").one()
            circles = db.query(Attraction).filter(Attraction.employee_circle.is_(True)).order_by(Attraction.id).all()
            original, historical = circles[:2]
            member = db.query(GroupMembership).filter_by(employee_id=employee.id).first()
            db.add(EmployeeMonthOrganizationSnapshot(employee_id=employee.id, score_month="2026-09", attraction_id=historical.id, attraction_name=historical.name, group_id=member.group_id, group_name="封存小组", leader_name="历史组长"))
            db.add(RecognitionRecord(employee_id=employee.id, employee_no=employee.employee_no, employee_name=employee.name, employee_role_snapshot="CM", home_attraction_id=original.id, occurred_attraction_id=original.id, recognition_date="2026-09-12", recognition_month="2026-09", recognition_type_id=1, recognition_type_name="安全", content="测试", recognizer_employee_id=actor.id, recognizer_name=actor.name, recognizer_role_snapshot="GSM", operator_employee_id=actor.id, operator_name=actor.name, source="manager", fraction=7, credited_fraction=2, status="confirmed"))
            db.commit()
            attendance_before = db.query(AttendanceMonthlyScore).count()
            report = report_data(db, "2026-09", historical.id)
            assert report["summary"]["recognition_score"] == 2  # Not raw seven points.
            assert any(r["name"] == "封存小组" for r in report["groups"])
            assert report["summary"]["recognition_count"] == 0  # Event snapshot remains original circle.
            assert report_data(db, "2026-09", original.id)["summary"]["recognition_count"] == 1
            assert db.query(AttendanceMonthlyScore).count() == attendance_before
            db.add(EmployeeLOAPeriod(employee_id=employee.id, starts_on="2026-09-05", ends_on=None, created_by=actor.id, created_by_name=actor.name))
            db.commit()
            report = report_data(db, "2026-09", historical.id)
            assert report["summary"]["recognition_score"] == 0
            assert report["loa_count"] == 1
            assert not any(r["employee_id"] == employee.id for group in report["rankings"] for r in group["rows"])
            assert report_data(db, "2026-09", original.id)["summary"]["recognition_count"] == 1
            assert db.query(AttendanceMonthlyScore).count() == attendance_before
