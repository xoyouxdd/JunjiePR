from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import BytesIO
from threading import Event

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from test_supervisor_performance import SessionLocal, app, employee_id, login, png_bytes
from app.idempotency import submission_payload_digest
from app.routers import recognitions
from app.v2_crypto import hash_password
from app.v2_models import RecognitionRecord, SubmissionRequest, UserAccount
from app.v2_services import current_leader_for_employee


def inbox(client):
    response = client.get("/api/recognition-inbox")
    assert response.status_code == 200, response.text
    return response.json()


def active_form(client, target, kind, key):
    options = client.get("/api/options").json()
    category = next(row for row in options["recognition_types"] if row["code"] == kind)
    signer = f"special:{kind}" if kind in {"MSP", "COMMENDATION_LETTER"} else client.get("/api/me").json()["id"]
    return {
        "employee_id": str(employee_id(target)),
        "recognition_date": date.today().isoformat(),
        "recognition_type_id": str(category["id"]),
        "occurred_attraction_id": str(next(row["id"] for row in options["recognition_venues"] if row["name"] == "热力追踪")),
        "recognizer_employee_id": str(signer),
        "content": "主动认可流程验证",
        "active_recognition": "true",
        "same_day_duplicate_confirmed": "true",
        "idempotency_key": key,
    }


def send(client, form):
    response = client.post("/api/recognitions", data=form, files={"image": ("proof.png", png_bytes(), "image/png")})
    assert response.status_code == 200, response.text
    return response.json()["record"]


def alternate_photo():
    output = BytesIO()
    Image.new("RGB", (4, 4), "blue").save(output, format="PNG")
    return ("proof.png", output.getvalue(), "image/png")


@pytest.mark.parametrize(("sender", "recipient"), [
    ("SUPTEST01", "CMTEST01"), ("SUPTEST01", "TRTEST01"), ("GSMTEST01", "SUPTEST01"),
])
def test_active_recognition_credits_before_read_and_is_private(sender, recipient):
    with TestClient(app) as client:
        login(client, recipient)
        before = client.get("/api/dashboard").json()["recognition_score"]
        prior = inbox(client)["unread_count"]
        login(client, sender)
        first_form = active_form(client, recipient, "SAFETY", f"inbox-{recipient}-safety")
        missing_image = client.post("/api/recognitions", data=first_form)
        assert missing_image.status_code == 400
        records = [send(client, first_form)]
        for kind in ("COURTESY", "INCLUSION"):
            records.append(send(client, active_form(client, recipient, kind, f"inbox-{recipient}-{kind}")))
        assert send(client, first_form)["id"] == records[0]["id"]
        assert all(row["status"] == "confirmed" and row["has_image"] for row in records)
        read_url = f"/api/recognition-inbox/{records[1]['id']}/read"
        assert client.post(read_url).status_code == 404
        login(client, recipient)
        assert inbox(client)["unread_count"] == prior + 3
        credited = sum(row["credited_fraction"] for row in records)
        assert credited > 0
        assert client.get("/api/dashboard").json()["recognition_score"] == pytest.approx(before + credited)

        detail = client.post(read_url)
        assert detail.status_code == 200, detail.text
        assert detail.json()["content"] == records[1]["content"]
        assert detail.json()["credited_score"] == records[1]["credited_fraction"]
        assert client.get(detail.json()["image_url"] + "?preview=1").headers["content-type"].startswith("image/")
        assert inbox(client)["unread_count"] == prior + 2
        assert client.post(read_url).status_code == 200
        items = inbox(client)["items"]
        assert not next(item for item in items if item["id"] == records[1]["id"])["unread"]
        assert items[-1]["id"] == records[1]["id"]
        assert client.get("/api/dashboard").json()["recognition_score"] == pytest.approx(before + credited)


@pytest.mark.parametrize("sender", ["GSMTEST01", "AMTEST01", "OMTEST01"])
def test_poc_inbox_uses_the_reason_and_does_not_require_a_photo(sender):
    with TestClient(app) as client:
        login(client, sender)
        form = {
            "employee_id": employee_id("SUPTEST01"), "recognition_date": date.today().isoformat(),
            "points": 2, "poc_period_type": "month", "poc_reason": "验证专项来信原文",
            "idempotency_key": "inbox-poc-supervisor",
        }
        response = client.post("/api/recognitions/poc", data=form)
        assert response.status_code == 200, response.text
        record_id = response.json()["record"]["id"]
        login(client, "SUPTEST01")
        response = client.post(f"/api/recognition-inbox/{record_id}/read")
        assert response.status_code == 200, response.text
        detail = response.json()
        assert detail["content"] == "验证专项来信原文"
        assert detail["credited_score"] == 2 and detail["poc_period"] and not detail["image_url"]
        login(client, sender)
        assert client.delete(f"/api/recognitions/{record_id}").status_code == 200
        entries = client.get("/api/my-entries").json()["items"]
        assert "reissue" in next(row for row in entries if row["id"] == record_id)["available_actions"]
        if sender in {"AMTEST01", "OMTEST01"}:
            assert client.get("/api/my-entries?record_type=deduction").status_code == 403
            assert all(row["recognition_type"] == "POC特别贡献" for row in entries)
        replacement = client.post("/api/recognitions/poc", data={
            **form, "points": 1, "poc_reason": "更正专项理由", "reissue_of_id": record_id,
            "idempotency_key": "inbox-poc-supervisor-corrected",
        })
        assert replacement.status_code == 200, replacement.text
        login(client, "SUPTEST01")
        detail = client.post(f"/api/recognition-inbox/{replacement.json()['record']['id']}/read").json()
        assert detail["reissued"] and detail["credited_score"] == 1 and detail["content"] == "更正专项理由"


def test_other_ordinary_categories_and_withdrawn_letters():
    with TestClient(app) as client:
        login(client, "SUPTEST01")
        legacy = active_form(client, "CMTEST01", "SHOW", "inbox-legacy")
        del legacy["active_recognition"]
        old_entry = client.post("/api/recognitions", data=legacy)
        assert old_entry.status_code == 200, old_entry.text
        records = [
            send(client, active_form(client, "CMTEST01", kind, f"inbox-other-{kind}"))
            for kind in ("EFFICIENCY", "SHOW", "MSP", "COMMENDATION_LETTER")
        ]
        login(client, "CMTEST01")
        items = {item["id"]: item for item in inbox(client)["items"]}
        assert old_entry.json()["record"]["id"] not in items
        assert all(items[row["id"]]["unread"] for row in records)
        assert {items[row["id"]]["recognition_type"] for row in records} == {"效率", "演出", "MSP", "表扬信"}

        withdrawn_id = records[0]["id"]
        login(client, "SUPTEST01")
        response = client.delete(f"/api/recognitions/{withdrawn_id}")
        assert response.status_code == 200, response.text
        msp_id = records[2]["id"]
        assert client.delete(f"/api/recognitions/{msp_id}").status_code == 200
        assert client.get(f"/api/recognitions/{msp_id}/reissue").json()["recognizer_employee_id"] == "special:MSP"
        login(client, "CMTEST01")
        assert next(item for item in inbox(client)["items"] if item["id"] == withdrawn_id)["cancelled"]
        assert client.post(f"/api/recognition-inbox/{withdrawn_id}/read").json()["cancelled"]


def test_group_review_read_only_and_read_letter_can_be_corrected():
    with TestClient(app) as client:
        target = "CMTEST01"
        with SessionLocal() as db:
            leader_account = current_leader_for_employee(db, employee_id(target)).employee_no
        login(client, target)
        before = client.get("/api/dashboard").json()["recognition_score"]
        login(client, "GSMTEST01")
        original_form = active_form(client, target, "SAFETY", "inbox-correction-original")
        original_form["recognizer_employee_id"] = str(employee_id("AMTEST01"))
        original = send(client, original_form)
        rid = original["id"]
        assert client.get(f"/api/recognitions/{rid}/reissue").status_code == 409
        legacy_form = active_form(client, target, "SHOW", "inbox-correction-legacy")
        del legacy_form["active_recognition"]
        legacy = client.post("/api/recognitions", data=legacy_form)
        assert legacy.status_code == 200, legacy.text
        legacy_id = legacy.json()["record"]["id"]
        login(client, leader_account)
        history = {row["id"]: row for row in client.get("/api/reviews?view=history").json()["items"]}
        assert history[rid]["review_read_only"] and not history[legacy_id]["review_read_only"]
        assert client.post(f"/api/reviews/{rid}", json={"action": "restore"}).status_code == 403
        restored = client.post(f"/api/reviews/{legacy_id}", json={"action": "restore"})
        assert restored.status_code == 200 and restored.json()["record"]["status"] == "pending"
        login(client, target)
        original_detail = client.post(f"/api/recognition-inbox/{rid}/read").json()
        assert original_detail["credited_score"] == original["credited_fraction"]
        photo_url = original_detail["image_url"]
        assert client.get(photo_url + "?preview=1").status_code == 200
        assert client.get("/api/recognitions/"+str(rid)+"/reissue").status_code == 404
        login(client, "GSMTEST01")
        response = client.request("DELETE", f"/api/recognitions/{rid}", json={"reason": "认可内容有误"})
        assert response.status_code == 200, response.text
        draft = client.get(f"/api/recognitions/{rid}/reissue").json()
        assert draft["content"] == original["content"]
        assert draft["recognizer_employee_id"] == employee_id("AMTEST01")
        assert client.get(photo_url).status_code == 200
        assert client.get(photo_url + "?preview=1").status_code == 200
        client.post("/api/logout")
        assert client.post("/api/login", json={"employee_no": "HR01", "password": "HR123"}).status_code == 200
        assert client.get(photo_url + "?preview=1").status_code == 200
        for account, expected_status in (("HR-HEAT", 200), ("HR-DWARF", 403)):
            with SessionLocal() as db:
                user_account = db.query(UserAccount).filter_by(employee_id=employee_id(account)).one()
                user_account.password_hash = hash_password("1234")
                user_account.enabled = True
                user_account.must_change_password = False
                db.commit()
            login(client, account)
            assert client.get(photo_url + "?preview=1").status_code == expected_status
        login(client, target)
        assert client.get(photo_url).status_code == 403
        assert client.get(photo_url + "?preview=1").status_code == 403
        assert client.get("/api/dashboard").json()["recognition_score"] == pytest.approx(before)
        cancelled = next(item for item in inbox(client)["items"] if item["id"] == rid)
        assert cancelled["unread"] and cancelled["cancelled"]
        notice = client.post(f"/api/recognition-inbox/{rid}/read").json()
        assert notice["cancel_reason"] == "认可内容有误" and "image_url" not in notice
        login(client, "GSMTEST01")
        corrected_form = {**original_form, "reissue_of_id": str(rid), "content": "更正后的认可内容",
                          "idempotency_key": "inbox-correction-reissued"}
        corrected = send(client, corrected_form)
        assert corrected["recognizer_name"] == original["recognizer_name"]
        assert corrected["credited_fraction"] == original["credited_fraction"]
        assert send(client, corrected_form)["id"] == corrected["id"]
        retry = {key: value for key, value in corrected_form.items() if key != "same_day_duplicate_confirmed"}
        assert send(client, retry)["id"] == corrected["id"]
        for changed, photo in (
            (retry, alternate_photo()),
            ({**retry, "content": "不同的更正文字"}, ("proof.png", png_bytes(), "image/png")),
        ):
            response = client.post("/api/recognitions", data=changed, files={"image": photo})
            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "IDEMPOTENCY_PAYLOAD_CONFLICT"
        entries = {row["id"]: row for row in client.get("/api/my-entries?record_type=recognition").json()["items"]}
        assert entries[rid]["replacement_id"] == corrected["id"] and entries[corrected["id"]]["reissued"]
        duplicate = client.post("/api/recognitions", data={**corrected_form, "idempotency_key": "inbox-reissue-second"},
                                files={"image": ("proof.png", png_bytes(), "image/png")})
        assert duplicate.status_code == 409
        login(client, target)
        detail = client.post(f"/api/recognition-inbox/{corrected['id']}/read").json()
        assert detail["reissued"] and detail["content"] == "更正后的认可内容"
        assert client.get("/api/dashboard").json()["recognition_score"] == pytest.approx(before + corrected["credited_fraction"])


def test_legacy_single_receipt_verifies_stored_photo_before_replaying():
    with TestClient(app) as client:
        login(client, "SUPTEST01")
        form = active_form(client, "TRTEST01", "SHOW", "inbox-legacy-photo-retry")
        original = send(client, form)
        fields = {key: value for key, value in form.items() if key != "idempotency_key"}
        for key in ("employee_id", "recognition_type_id", "occurred_attraction_id"):
            fields[key] = int(fields[key])
        fields["active_recognition"] = fields["same_day_duplicate_confirmed"] = True
        with SessionLocal() as db:
            receipt = db.query(SubmissionRequest).filter_by(request_key=form["idempotency_key"]).one()
            receipt.payload_digest = submission_payload_digest(fields)
            db.commit()
        del form["same_day_duplicate_confirmed"]
        assert send(client, form)["id"] == original["id"]
        for files in ({"image": alternate_photo()}, None):
            rejected = client.post("/api/recognitions", data=form, files=files)
            assert rejected.status_code == 409
            assert rejected.json()["detail"]["code"] == "IDEMPOTENCY_PAYLOAD_CONFLICT"


@pytest.mark.parametrize("change", ["content", "photo", "invalid-photo", "none", "confirmed", "key"])
def test_same_day_concurrent_reissue_checks_the_entire_payload(monkeypatch, change):
    with TestClient(app) as first, TestClient(app) as retry:
        login(first, "TRTEST01")
        before = first.get("/api/dashboard").json()["recognition_score"]
        for client in (first, retry):
            login(client, "SUPTEST01")
        key = f"inbox-race-{change}"
        kind = {
            "content": "EFFICIENCY", "photo": "INCLUSION", "invalid-photo": "SHOW",
            "none": "COURTESY", "confirmed": "SAFETY", "key": "SAFETY",
        }[change]
        form = active_form(first, "TRTEST01", kind, key+"-original")
        signer = "AMTEST01" if change in {"invalid-photo", "key"} else "GSMTEST01"
        form["recognizer_employee_id"] = str(employee_id(signer))
        original = send(first, form)
        assert first.delete(f"/api/recognitions/{original['id']}").status_code == 200
        corrected = {**form, "idempotency_key": key, "reissue_of_id": original["id"],
                     "content": "更正后的认可内容", "same_day_duplicate_confirmed": str(change == "confirmed").lower()}
        other = corrected.copy()
        if change == "content":
            other["content"] = "不同的更正文字"
        if change == "confirmed":
            del other["same_day_duplicate_confirmed"]
        if change == "key":
            other["idempotency_key"] += "-other"
        waiting, release = Event(), Event()
        lookup = recognitions.existing_submission

        def pause_after_first_miss(db, actor, operation, request_key, model, digest=None, **kwargs):
            row = lookup(db, actor, operation, request_key, model, digest, **kwargs)
            if request_key == other["idempotency_key"] and row is None and not waiting.is_set():
                waiting.set()
                assert release.wait(10)
            return row

        monkeypatch.setattr(recognitions, "existing_submission", pause_after_first_miss)
        photo = alternate_photo() if change == "photo" else ("proof.png", png_bytes(), "image/png")
        if change == "invalid-photo":
            photo = ("proof.png", b"invalid image", "image/png")
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(retry.post, "/api/recognitions", data=other, files={"image": photo})
            try:
                assert waiting.wait(10)
                accepted = send(first, corrected)
            finally:
                release.set()
            response = pending.result(timeout=10)
        if change in {"none", "confirmed"}:
            assert response.status_code == 200
            assert response.json()["duplicate"] and response.json()["record"]["id"] == accepted["id"]
        else:
            assert response.status_code == 409
            if change == "key":
                assert response.json()["detail"] == "原签卡已重新发卡"
            else:
                assert response.json()["detail"]["code"] == "IDEMPOTENCY_PAYLOAD_CONFLICT"
        login(first, "TRTEST01")
        after = first.get("/api/dashboard").json()["recognition_score"]
        assert after == pytest.approx(before + accepted["credited_fraction"])


@pytest.mark.parametrize("mode", ["active", "legacy", "self"])
def test_parallel_same_day_submissions_need_confirmation(monkeypatch, mode):
    recipient = "TRTEST01"
    sender = recipient if mode == "self" else "SUPTEST01"
    kind = {"active": "EFFICIENCY", "legacy": "INCLUSION", "self": "SHOW"}[mode]
    with TestClient(app) as first, TestClient(app) as second:
        login(first, recipient)
        before = first.get("/api/dashboard").json()["recognition_score"]
        for client in (first, second):
            login(client, sender)
        key = f"inbox-parallel-{mode}"
        form = active_form(first, recipient, kind, key)
        del form["same_day_duplicate_confirmed"]
        if mode != "active":
            del form["active_recognition"]
        if mode == "legacy":
            form["recognizer_employee_id"] = str(employee_id("AMTEST01"))
        if mode == "self":
            del form["employee_id"]
            form["recognizer_employee_id"] = str(employee_id("GSMTEST01"))
        second_form = {**form, "idempotency_key": key + "-other", "content": "另一项独立表现"}
        files = None if mode == "legacy" else {"image": ("proof.png", png_bytes(), "image/png")}
        waiting, release = Event(), Event()
        flush = Session.flush

        def pause_before_insert(db, *args, **kwargs):
            if not waiting.is_set() and any(
                isinstance(row, RecognitionRecord) and row.content == second_form["content"] for row in db.new
            ):
                waiting.set()
                assert release.wait(10)
            return flush(db, *args, **kwargs)

        monkeypatch.setattr(Session, "flush", pause_before_insert)
        url = "/api/recognitions"
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(second.post, url, data=second_form, files=files)
            try:
                assert waiting.wait(10)
                accepted = first.post(url, data=form, files=files)
            finally:
                release.set()
            response = pending.result(timeout=10)
        assert accepted.status_code == 200, accepted.text
        first_record = accepted.json()["record"]
        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "SAME_DAY_RECOGNITION_DUPLICATE"
        assert [row["id"] for row in detail["previous_records"]] == [first_record["id"]]
        with SessionLocal() as db:
            assert db.query(SubmissionRequest).filter_by(request_key=second_form["idempotency_key"]).first() is None

        login(first, recipient)
        expected_credit = 0 if mode == "self" else first_record["credited_fraction"]
        assert first.get("/api/dashboard").json()["recognition_score"] == pytest.approx(before + expected_credit)
        confirmed = second.post(url, data={**second_form, "same_day_duplicate_confirmed": "true"}, files=files)
        assert confirmed.status_code == 200, confirmed.text
        record = confirmed.json()["record"]
        assert record["same_day_duplicate_sequence"] == 2
        assert record["status"] == ("pending" if mode == "self" else "confirmed")
        expected_credit += 0 if mode == "self" else record["credited_fraction"]
        assert first.get("/api/dashboard").json()["recognition_score"] == pytest.approx(before + expected_credit)


@pytest.mark.parametrize("change", ["none", "reason", "key"])
def test_parallel_poc_reissue_replays_only_same_submission(monkeypatch, change):
    with TestClient(app) as first, TestClient(app) as retry:
        login(first, "SUPTEST01")
        before = first.get("/api/dashboard").json()["recognition_score"]
        for client in (first, retry):
            login(client, "GSMTEST01")
        key = f"inbox-poc-race-{change}"
        url = "/api/recognitions/poc"
        form = {
            "employee_id": employee_id("SUPTEST01"), "recognition_date": date.today().isoformat(),
            "points": 2, "poc_period_type": "month", "poc_reason": "专项贡献验证",
            "idempotency_key": key + "-original",
        }
        original = first.post(url, data=form)
        assert original.status_code == 200, original.text
        original_id = original.json()["record"]["id"]
        assert first.delete(f"/api/recognitions/{original_id}").status_code == 200
        corrected = {**form, "idempotency_key": key, "reissue_of_id": original_id}
        other = corrected.copy()
        if change == "reason":
            other["poc_reason"] = "不同专项贡献内容"
        if change == "key":
            other["idempotency_key"] += "-other"
        waiting, release = Event(), Event()
        lookup = recognitions.existing_submission

        def pause_after_first_miss(db, actor, operation, request_key, model, digest=None, **kwargs):
            row = lookup(db, actor, operation, request_key, model, digest, **kwargs)
            if operation == "poc" and request_key == other["idempotency_key"] and row is None and not waiting.is_set():
                waiting.set()
                assert release.wait(10)
            return row

        monkeypatch.setattr(recognitions, "existing_submission", pause_after_first_miss)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(retry.post, url, data=other)
            try:
                assert waiting.wait(10)
                accepted = first.post(url, data=corrected)
            finally:
                release.set()
            response = pending.result(timeout=10)
        assert accepted.status_code == 200, accepted.text
        accepted_record = accepted.json()["record"]
        if change == "none":
            assert response.status_code == 200, response.text
            assert response.json()["duplicate"]
            assert response.json()["record"]["id"] == accepted_record["id"]
        else:
            assert response.status_code == 409, response.text
            detail = response.json()["detail"]
            if change == "reason":
                assert detail["code"] == "IDEMPOTENCY_PAYLOAD_CONFLICT"
            else:
                assert detail == "原签卡已重新发卡"
        login(first, "SUPTEST01")
        assert first.get("/api/dashboard").json()["recognition_score"] == pytest.approx(
            before + accepted_record["credited_fraction"]
        )


@pytest.mark.parametrize("operation", ["recognition", "poc"])
def test_parallel_reissue_replays_after_month_close(monkeypatch, operation):
    class Clock(date):
        @classmethod
        def today(cls):
            return cls(2100, 1, 10)

    monkeypatch.setattr("app.month_closure.date", Clock)
    poc = operation == "poc"
    sender, recipient = ("GSMTEST01", "SUPTEST01") if poc else ("SUPTEST01", "TRTEST01")
    month = "2099-12" if poc else "2099-11"
    key = f"inbox-month-close-{operation}"
    url = "/api/recognitions/poc" if poc else "/api/recognitions"
    files = None if poc else {"image": ("proof.png", png_bytes(), "image/png")}
    with TestClient(app) as first, TestClient(app) as retry, TestClient(app) as hr:
        for client in (first, retry):
            login(client, sender)
        heat = next(row["id"] for row in first.get("/api/options").json()["employee_circles"] if row["name"] == "热力追踪")
        hr_id = employee_id("HR-HEAT")
        with SessionLocal() as db:
            account = db.query(UserAccount).filter_by(employee_id=hr_id).one()
            account.password_hash = hash_password("1234")
            account.enabled = True
            account.must_change_password = False
            db.commit()
        login(hr, "HR-HEAT")
        form = (
            {"employee_id": employee_id(recipient), "points": 2, "poc_period_type": "month", "poc_reason": "月结重发测试"}
            if poc else active_form(first, recipient, "SHOW", key)
        )
        form.update(recognition_date=f"{month}-20", idempotency_key=key + "-original")
        original = first.post(url, data=form, files=files)
        assert original.status_code == 200, original.text
        original_id = original.json()["record"]["id"]
        assert first.delete(f"/api/recognitions/{original_id}").status_code == 200
        replacement = {**form, "idempotency_key": key, "reissue_of_id": original_id}
        waiting, release = Event(), Event()
        lookup = recognitions.existing_submission

        def pause_after_first_miss(db, actor, receipt_operation, request_key, model, digest=None, **kwargs):
            row = lookup(db, actor, receipt_operation, request_key, model, digest, **kwargs)
            if receipt_operation == operation and request_key == key and row is None and not waiting.is_set():
                waiting.set()
                assert release.wait(10)
            return row

        monkeypatch.setattr(recognitions, "existing_submission", pause_after_first_miss)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(retry.post, url, data=replacement, files=files)
            try:
                assert waiting.wait(10)
                accepted = first.post(url, data=replacement, files=files)
                assert accepted.status_code == 200, accepted.text
                close = hr.post(f"/api/month-closes/{month}/close", json={"attraction_id": heat, "reason": "并发重发回归"})
                assert close.status_code == 200, close.text
            finally:
                release.set()
            response = pending.result(timeout=10)
        assert response.status_code == 200, response.text
        assert response.json()["duplicate"]
        assert response.json()["record"]["id"] == accepted.json()["record"]["id"]
        closed = retry.post(url, data={**form, "idempotency_key": key + "-new"}, files=files)
        assert closed.status_code == 423, closed.text
        assert closed.json()["detail"]["code"] == "MONTH_CLOSED"
        with SessionLocal() as db:
            records = db.query(RecognitionRecord).filter_by(employee_id=employee_id(recipient), recognition_month=month).all()
            assert sorted(record.status for record in records) == ["confirmed", "void"]
