from __future__ import annotations

import asyncio
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import BytesIO
from pathlib import Path
from threading import Barrier

from PIL import Image
import pytest


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-supervisor-performance-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.v2_database import SessionLocal  # noqa: E402
from app.v2_models import Attraction, Employee, RecognitionRecord  # noqa: E402


def login(client: TestClient, account: str) -> None:
    client.post("/api/logout")
    response = client.post("/api/login", json={"employee_no": account, "password": "1234"})
    assert response.status_code == 200, response.text


def employee_id(employee_no: str) -> int:
    with SessionLocal() as db:
        return db.query(Employee).filter_by(employee_no=employee_no).one().id


def png_bytes() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (4, 4), "white").save(stream, format="PNG")
    return stream.getvalue()


def pdf_bytes() -> bytes:
    image = Image.new("RGB", (300, 400), "white")
    stream = BytesIO()
    image.save(stream, "PDF")
    return stream.getvalue()


def recognize(client: TestClient, *, recognizer_no: str, key: str, target_no: str | None = None, self_image: bool = False):
    options = client.get("/api/options").json()
    venue_id = next(row["id"] for row in options["recognition_venues"] if row["name"] == "热力追踪")
    circle_id = next(row["id"] for row in options["employee_circles"] if row["name"] == "热力追踪")
    type_id = next(row["id"] for row in options["recognition_types"] if row["code"] == "SAFETY")
    today = date.today().isoformat()
    recognizers = client.get("/api/recognizers", params={"attraction_id": circle_id, "recognition_date": today}).json()
    recognizer = next(row for row in recognizers if row.get("employee_no") == recognizer_no)
    data = {
        "recognition_date": today,
        "occurred_attraction_id": str(venue_id),
        "recognition_type_id": str(type_id),
        "recognizer_employee_id": str(recognizer["id"]),
        "content": "主管绩效测试",
        "idempotency_key": key,
    }
    if target_no:
        data["employee_id"] = str(employee_id(target_no))
        data["same_day_duplicate_confirmed"] = "true"
    files = {"image": ("proof.png", png_bytes(), "image/png")} if self_image else None
    response = client.post("/api/recognitions", data=data, files=files)
    if response.status_code == 409:
        detail = response.json()["detail"]
        if isinstance(detail, dict) and detail.get("code") == "SAME_DAY_RECOGNITION_DUPLICATE":
            data.update(same_day_duplicate_confirmed="true", same_day_duplicate_token=detail["same_day_duplicate_token"])
            response = client.post("/api/recognitions", data=data, files=files)
    return response


def deduct(client: TestClient, target_no: str, level_code: str, key: str):
    options = client.get("/api/options").json()
    level = next(row for row in options["deduction_levels"] if row["code"] == level_code)
    return client.post(
        "/api/deductions",
        data={
            "employee_id": str(employee_id(target_no)),
            "deduction_type_id": str(options["deduction_types"][0]["id"]),
            "deduction_level_id": str(level["id"]),
            "occurred_on": date.today().isoformat(),
            "description": "主管扣分测试",
            "idempotency_key": key,
        },
        files={"document": ("statement.pdf", pdf_bytes(), "application/pdf")},
    )


def test_supervisor_statements_with_pdf_stay_ordinary_with_same_type_history(monkeypatch):
    from app import deduction_materials, main
    from app.routers import deductions
    from app.score_queries import employee_month_scores
    from app.v2_models import DeductionMaterialJob, DeductionRecord, DeductionUpgradeRequest, SubmissionRequest

    monkeypatch.setattr(main, "start_deduction_material_worker", lambda: None)
    month = "2062-03"
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        target_id = employee_id("SUPTEST01")
        options = client.get("/api/options").json()
        kind = next(row for row in options["deduction_types"] if row["code"] == "SICK_LEAVE_VIOLATION")
        level = next(row for row in options["deduction_levels"] if row["code"] == "STATEMENT")
        form = {"employee_id": target_id, "deduction_type_id": kind["id"],
                "deduction_level_id": level["id"], "description": "正式主管普通声明"}
        pdf = {"document": ("statement.pdf", pdf_bytes(), "application/pdf")}

        def submit(day, files=None):
            response = client.post("/api/deductions", data={**form, "occurred_on": f"{month}-{day}"}, files=files)
            assert response.status_code == 200, response.text
            return response.json()["record"]

        def post_concurrently(path, data=None):
            barrier = Barrier(2, timeout=10)
            stage_pdf = deductions.stage_pdf_material

            async def stage_after_both_requests_read_state(*args, **kwargs):
                await asyncio.to_thread(barrier.wait)
                return await stage_pdf(*args, **kwargs)

            with monkeypatch.context() as race, ThreadPoolExecutor(max_workers=2) as pool:
                race.setattr(deductions, "stage_pdf_material", stage_after_both_requests_read_state)
                requests = [pool.submit(client.post, path, data=data, files=pdf) for _ in range(2)]
                return [request.result(timeout=30) for request in requests]

        preview = client.get("/api/deduction-upgrades/preview", params={
            "employee_id": target_id, "deduction_type_id": kind["id"], "occurred_on": month + "-01",
        })
        assert preview.status_code == 400
        first = submit("01", pdf)
        request_key = "supervisor-concurrent-pdf-registration"
        replies = post_concurrently("/api/deductions", {
            **form, "occurred_on": month + "-02", "idempotency_key": request_key,
        })
        assert [reply.status_code for reply in replies] == [200, 200], [reply.text for reply in replies]
        results = [reply.json() for reply in replies]
        assert sum(bool(result.get("duplicate")) for result in results) == 1
        second = results[0]["record"]
        assert second["id"] == results[1]["record"]["id"]
        assert first["actual_points"] == second["actual_points"] == level["points"]

        with monkeypatch.context() as queued:
            queued.setattr(deductions, "PDF_HIGH_QUALITY_OPTIMIZATION_THRESHOLD", 0)
            third = submit("03", pdf)
        assert third["material_status"] == "processing"
        assert deduction_materials.process_next_deduction_material_job()

        pending = submit("04")
        assert pending["status"] == "pending_material"
        path = f"/api/deductions/{pending['id']}/material"
        preview = client.get(path + "-upgrade-preview")
        assert preview.status_code == 200 and preview.json()["eligible"] is False
        uploads = sorted(post_concurrently(path), key=lambda reply: reply.status_code)
        assert [reply.status_code for reply in uploads] == [200, 409], [reply.text for reply in uploads]
        assert uploads[0].json()["record"]["id"] == pending["id"]
        with SessionLocal() as db:
            job = db.query(DeductionMaterialJob).filter_by(deduction_id=pending["id"]).one()
            assert (job.status, job.mode) == ("queued", "pdf_compress")
            assert employee_month_scores(db, month, [target_id])[0]["deduction_score"] == 3 * level["points"]
        assert deduction_materials.process_next_deduction_material_job()
        assert not deduction_materials.process_next_deduction_material_job()

        with SessionLocal() as db:
            receipt = db.query(SubmissionRequest).filter_by(operation="deduction", request_key=request_key).one()
            assert receipt.entity_id == second["id"]
            assert db.query(DeductionMaterialJob).filter_by(deduction_id=pending["id"]).one().status == "succeeded"
            rows = db.query(DeductionRecord).filter_by(employee_id=target_id, deduction_month=month).all()
            assert {row.id for row in rows} == {first["id"], second["id"], third["id"], pending["id"]}
            assert all(row.status == "active" and row.material_status == "ready" and row.upgrade_role is None for row in rows)
            assert not db.query(DeductionUpgradeRequest).filter_by(employee_id=target_id).count()
            assert employee_month_scores(db, month, [target_id])[0]["deduction_score"] == 4 * level["points"]


def test_supervisor_self_recognition_goes_to_the_formal_gsm_queue() -> None:
    with TestClient(app) as client:
        login(client, "SUPTEST01")
        me = client.get("/api/me").json()
        assert "SELF_RECOGNITION" in me["permissions"]
        assert recognize(client, recognizer_no="TATEST01", key="sup-self-ta", self_image=True).status_code == 400
        created = recognize(client, recognizer_no="GSMTEST01", key="sup-self-gsm", self_image=True)
        assert created.status_code == 200, created.text
        record = created.json()["record"]
        assert record["status"] == "pending"
        assert client.get("/api/supervisor-reviews").status_code == 403

        # Only a formal GSM reviews it; AM sees only acting TA GSM records, OM none.
        login(client, "AMTEST01")
        assert record["id"] not in {row["id"] for row in client.get("/api/supervisor-reviews").json()["items"]}
        login(client, "OMTEST01")
        assert client.get("/api/supervisor-reviews").status_code == 403
        login(client, "GSMTEST01")
        queued = next(row for row in client.get("/api/supervisor-reviews").json()["items"] if row["id"] == record["id"])
        assert queued["recognition_date"] == record["recognition_date"] == date.today().isoformat()
        assert queued["submitted_at"] == record["submitted_at"]
        actions = client.get("/api/action-center").json()["items"]
        assert any(item["type"] == "supervisor_review" for item in actions)
        confirmed = client.post(f"/api/supervisor-reviews/{record['id']}", json={"action": "confirm"})
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["record"]["status"] == "confirmed"
        history = next(row for row in client.get("/api/supervisor-reviews?view=history").json()["items"] if row["id"] == record["id"])
        assert history["recognition_date"] == queued["recognition_date"]
        assert history["submitted_at"] == queued["submitted_at"]

        login(client, "TAGSMTEST01")
        assert client.get("/api/supervisor-reviews").status_code == 403


def test_acting_ta_gsm_is_reviewed_by_am_or_formal_gsm_across_circles() -> None:
    with TestClient(app) as client:
        login(client, "TAGSMTEST01")
        # Recognizers: formal GSM, AM or OM; not another TA GSM.
        assert recognize(client, recognizer_no="TAGSMTEST01", key="tagsm-self-self", self_image=True).status_code == 400
        created = recognize(client, recognizer_no="GSMTEST01", key="tagsm-self-gsm", self_image=True)
        assert created.status_code == 200, created.text
        record_id = created.json()["record"]["id"]
        with SessionLocal() as db:
            row = db.get(RecognitionRecord, record_id)
            assert row.employee_acting_duty_code == "TA_GSM"
            gsm = db.query(Employee).filter_by(employee_no="GSMTEST01").one()
            row.home_attraction_id = db.query(Attraction).filter(Attraction.id != gsm.attraction_id).first().id
            db.commit()

        # Neither the applicant acting as TA GSM nor OM may review.
        assert client.get("/api/supervisor-reviews").status_code == 403
        assert client.post(f"/api/supervisor-reviews/{record_id}", json={"action": "confirm"}).status_code == 403
        login(client, "OMTEST01")
        assert client.get("/api/supervisor-reviews").status_code == 403
        assert client.post(f"/api/supervisor-reviews/{record_id}", json={"action": "confirm"}).status_code == 403

        login(client, "GSMTEST01")
        assert record_id in {row["id"] for row in client.get("/api/supervisor-reviews").json()["items"]}
        actions = client.get("/api/action-center").json()["items"]
        assert any(item["type"] == "supervisor_review" for item in actions)
        assert client.post(f"/api/supervisor-reviews/{record_id}", json={"action": "confirm"}).status_code == 200
        assert record_id in {row["id"] for row in client.get("/api/supervisor-reviews", params={"view": "history"}).json()["items"]}
        assert client.post(f"/api/supervisor-reviews/{record_id}", json={"action": "restore"}).status_code == 200
        login(client, "AMTEST01")
        assert record_id in {row["id"] for row in client.get("/api/supervisor-reviews").json()["items"]}
        assert client.post(f"/api/supervisor-reviews/{record_id}", json={"action": "confirm"}).status_code == 200


def test_who_may_credit_supervisors() -> None:
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        # The recognizer differs from the earlier self-registration so the
        # same-day duplicate reminder does not apply.
        credited = recognize(client, recognizer_no="OMTEST01", key="gsm-sup", target_no="SUPTEST01")
        assert credited.status_code == 200, credited.text
        assert credited.json()["record"]["status"] == "confirmed"
        assert recognize(client, recognizer_no="GSMTEST01", key="gsm-tagsm", target_no="TAGSMTEST01").status_code == 403
        found = client.get("/api/employee-targets", params={"usage": "recognition", "scope": "supervisor", "keyword": "测试"}).json()["items"]
        assert {row["employee_no"] for row in found} == {"SUPTEST01"}

        login(client, "TAGSMTEST01")
        assert recognize(client, recognizer_no="TAGSMTEST01", key="tagsm-sup", target_no="SUPTEST01").status_code == 200

        login(client, "SUPTEST01")
        assert recognize(client, recognizer_no="GSMTEST01", key="sup-tagsm", target_no="TAGSMTEST01").status_code == 403

        # Nobody credits a supervisor acting as TA GSM, AM included; AM has no supervisor entry.
        login(client, "AMTEST01")
        assert recognize(client, recognizer_no="GSMTEST01", key="am-sup", target_no="SUPTEST01").status_code == 403
        assert recognize(client, recognizer_no="AMTEST01", key="am-tagsm", target_no="TAGSMTEST01").status_code == 403
        assert client.get("/api/employee-targets", params={"usage": "deduction", "scope": "supervisor", "keyword": "测试"}).status_code == 403
        with SessionLocal() as db:
            tagsm_id = db.query(Employee).filter_by(employee_no="TAGSMTEST01").one().id
        poc = client.post(
            "/api/recognitions/poc",
            data={"recognition_date": date.today().isoformat(), "employee_id": str(tagsm_id), "points": "2", "poc_period_type": "month", "poc_reason": "代理期间"},
        )
        assert poc.status_code == 403, poc.text


def test_who_may_deduct_supervisors() -> None:
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        memo = deduct(client, "SUPTEST01", "MEMO", "gsm-deduct-sup")
        assert memo.status_code == 200, memo.text
        assert deduct(client, "TAGSMTEST01", "STATEMENT", "gsm-deduct-tagsm").status_code == 403

        login(client, "SUPTEST01")
        assert deduct(client, "TAGSMTEST01", "STATEMENT", "sup-deduct-tagsm").status_code == 403

        # Nobody deducts a supervisor acting as TA GSM, AM included.
        login(client, "AMTEST01")
        assert deduct(client, "TAGSMTEST01", "WARNING_1", "am-deduct-tagsm").status_code == 403
        assert deduct(client, "SUPTEST01", "STATEMENT", "am-deduct-sup").status_code == 403


@pytest.mark.parametrize('target_no,recognizer_no,expected',[
    ('SUPTEST01','GSMTEST01',0.5),('SUPTEST01','AMTEST01',1.0),
    ('TATEST01','GSMTEST01',1.0),('TATEST01','AMTEST01',1.5),
    ('TRTEST01','GSMTEST01',1.0),('TRTEST01','AMTEST01',1.5),
])
def test_recognition_rate_uses_recipient_base_identity(target_no,recognizer_no,expected):
    from app.v2_models import EmployeeActingDuty, Role
    from app.services.identity import base_role_at, duty_code_at
    with TestClient(app) as client:
        if target_no=='TRTEST01':
            with SessionLocal() as db:
                eid=db.query(Employee).filter_by(employee_no=target_no).one().id
                role_id=db.query(Role).filter_by(code='TA_SUPERVISOR').one().id
                if not db.query(EmployeeActingDuty).filter_by(employee_id=eid,role_id=role_id).first():
                    db.add(EmployeeActingDuty(employee_id=eid,role_id=role_id,starts_on=date.today().isoformat(),status='active'))
                    db.commit()
        login(client,target_no)
        if target_no!='SUPTEST01':
            with SessionLocal() as db:
                assert base_role_at(db,employee_id(target_no)).code in ('CM','TR')
                assert duty_code_at(db,employee_id(target_no))=='TA_SUPERVISOR'
        response=recognize(client,recognizer_no=recognizer_no,key=f'base-rate-{target_no}-{recognizer_no}',self_image=True)
        assert response.status_code==200,response.text
        record=response.json()['record'];assert record['fraction']==expected
        with SessionLocal() as db:
            saved=db.get(RecognitionRecord,record['id']);assert float(saved.fraction)==expected
        options=client.get('/api/options').json()
        circle_id=next(row['id'] for row in options['employee_circles'] if row['name']=='热力追踪')
        choices=client.get('/api/recognizers',params={'attraction_id':circle_id}).json()
        signer=next(row for row in choices if row.get('employee_no')==recognizer_no)
        assert signer['supervisor_score']==(0.5 if recognizer_no=='GSMTEST01' else 1)
        assert signer['score']==(1 if recognizer_no=='GSMTEST01' else 1.5)
