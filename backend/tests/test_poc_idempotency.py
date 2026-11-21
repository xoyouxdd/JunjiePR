from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import BytesIO
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient
from openpyxl import load_workbook


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-poc-idempotency-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from app.main import app  # noqa: E402
from app.database.connection import SessionLocal  # noqa: E402
from app.v2_models import RecognitionInboxItem, RecognitionRecord  # noqa: E402


def login(client: TestClient, account: str, password: str = "1234") -> None:
    response = client.post("/api/login", json={"employee_no": account, "password": password})
    assert response.status_code == 200, response.text


def test_poc_replay_returns_same_record_and_conflict_on_different_payload() -> None:
    today = date.today().isoformat()
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        target = next(
            row
            for row in client.get("/api/employee-targets", params={"usage": "poc", "keyword": "CMTEST01"}).json()["items"]
            if row["employee_no"] == "CMTEST01"
        )
        payload = {
            "recognition_date": today,
            "employee_id": str(target["id"]),
            "points": "2",
            "poc_period_type": "month",
            "poc_reason": "幂等测试特别贡献",
            "idempotency_key": "poc-idempotency-001",
        }
        first = client.post("/api/recognitions/poc", data=payload)
        assert first.status_code == 200, first.text
        first_id = first.json()["record"]["id"]
        replay = client.post("/api/recognitions/poc", data=payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()["record"]["id"] == first_id
        assert replay.json().get("duplicate") is True
        conflict = client.post("/api/recognitions/poc", data={**payload, "poc_reason": "另一项内容"})
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_PAYLOAD_CONFLICT"


def test_poc_batch_template_preview_confirm_and_deliver_once() -> None:
    base = "/api/recognitions/poc-batch"
    day = date.today()
    settings = {"recognition_date": day.isoformat()}
    reasons = ["协助带教新同事", "季度改善服务流程"]
    recipients = ["CMTEST01", "CMTEST02"]
    scores = dict(zip(recipients, [1, 5]))

    with TestClient(app) as manager:
        login(manager, "GSMTEST01")
        template = manager.get(base + "/template")
        assert template.status_code == 200, template.text
        assert template.headers["content-disposition"].startswith("attachment")
        book = load_workbook(BytesIO(template.content))
        sheet = book["特别贡献认可"]
        assert [cell.value for cell in sheet[1]] == ["姓名", "员工号", "认可原因", "认可周期", "POC分值"]
        assert sheet["B2"].number_format == "@"
        validation, score_validation = sheet.data_validations.dataValidation
        assert validation.formula1 == '"月度,季度"' and "D2" in validation and "D101" in validation
        assert score_validation.formula1 == '"1,2,3,4,5"' and "E2" in score_validation and "E101" in score_validation

        def upload():
            output = BytesIO()
            book.save(output)
            return {"workbook": ("poc.xlsx", output.getvalue(), template.headers["content-type"])}

        for line, (employee_no, reason, period) in enumerate(zip(recipients, reasons, ["月度", "季度"]), start=2):
            results = manager.get("/api/employee-targets", params={"usage": "poc", "keyword": employee_no}).json()["items"]
            employee = next(row for row in results if row["employee_no"] == employee_no)
            for column, value in enumerate((employee["name"], employee_no, reason, period, scores[employee_no]), start=1):
                sheet.cell(line, column).value = value

        name = sheet["A3"].value
        sheet["A3"] = "姓名不匹配"
        bad = manager.post(base + "/preview", data=settings, files=upload())
        assert bad.status_code == 400 and bad.json()["detail"]["rows"][0]["row"] == 3
        sheet["A3"] = name
        for invalid_score in [None, 6, 1.5]:
            sheet["E3"].value = invalid_score
            bad = manager.post(base + "/preview", data=settings, files=upload())
            assert bad.status_code == 400
            assert bad.json()["detail"]["rows"] == [{"row": 3, "message": "请选择 1–5 分的 POC 分值。"}]
        sheet["E3"] = 5
        original = upload()

        preview = manager.post(base + "/preview", data=settings, files=original)
        assert preview.status_code == 200, preview.text
        plan = preview.json()
        assert plan["recipient_count"] == 2
        assert [row["employee_no"] for row in plan["rows"]] == recipients
        assert [row["points"] for row in plan["rows"]] == [1, 5]
        assert [row["period_key"] for row in plan["rows"]] == [
            day.strftime("%Y-%m"), f"{day.year}-Q{(day.month - 1) // 3 + 1}",
        ]
        payload = {**settings, "confirmation": plan["confirmation"]}
        sheet["E3"] = 4
        changed = manager.post(base + "/send", data=payload, files=upload())
        assert changed.status_code == 409
        with SessionLocal() as db:
            assert db.query(RecognitionRecord).filter(RecognitionRecord.poc_reason.in_(reasons)).count() == 0

        with ThreadPoolExecutor(max_workers=2) as workers:
            attempts = list(workers.map(lambda _: manager.post(base + "/send", data=payload, files=original), range(2)))
        assert all(r.status_code == 200 for r in attempts), [r.text for r in attempts]
        assert sorted(r.json().get("duplicate", False) for r in attempts) == [False, True]
        assert all(r.json()["sent_count"] == 2 for r in attempts)
        assert manager.post(base + "/preview", data=settings, files=original).json()["duplicate"] is True

        with SessionLocal() as db:
            rows = db.query(RecognitionRecord).filter(RecognitionRecord.poc_reason.in_(reasons)).all()
            assert len(rows) == 2
            delivered = {row.employee_no: row.id for row in rows}
            for row in rows:
                assert row.status == "confirmed" and row.credited_fraction == scores[row.employee_no]
                assert db.get(RecognitionInboxItem, row.id).read_at is None

        for employee_no, reason in zip(recipients, reasons):
            manager.post("/api/logout")
            login(manager, employee_no)
            assert manager.get(base + "/template").status_code == 403
            assert manager.post(base + "/preview", data=settings, files=original).status_code == 403
            inbox = manager.get("/api/recognition-inbox").json()["items"]
            batch_mail = [item for item in inbox if item["id"] in delivered.values()]
            assert [item["id"] for item in batch_mail] == [delivered[employee_no]]
            assert batch_mail[0]["unread"] is True
            mail = manager.post(f"/api/recognition-inbox/{delivered[employee_no]}/read")
            assert mail.status_code == 200, mail.text
            assert mail.json()["content"] == reason
            assert mail.json()["credited_score"] == scores[employee_no]
