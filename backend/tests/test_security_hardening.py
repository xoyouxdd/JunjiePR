from __future__ import annotations

import os
from pathlib import Path
import tempfile
from datetime import datetime, timedelta

import pytest


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-security-test-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.security import (  # noqa: E402
    PRIVILEGED_PASSWORD_ROLE_CODES,
    password_policy_error,
)
from app.version import APP_VERSION, STATIC_CACHE_VERSION  # noqa: E402
from app.v2_crypto import hash_password, verify_password  # noqa: E402
from app.v2_database import ROLE_PERMISSION_CODES, SessionLocal, disable_test_accounts, ensure_highest_admin_account  # noqa: E402
from app.v2_models import Employee, UserAccount, UserSession  # noqa: E402


def login(client: TestClient, login_account: str, password: str = "1234"):
    return client.post(
        "/api/login",
        json={"employee_no": login_account, "password": password},
    )


def restore_account(login_account: str, password: str) -> None:
    with SessionLocal() as db:
        account = db.query(UserAccount).filter_by(login_account=login_account).one()
        account.password_hash = hash_password(password)
        account.must_change_password = False
        account.credential_initialized = True
        account.enabled = True
        account.failed_attempts = 0
        account.locked_until = None
        db.query(UserSession).filter_by(account_id=account.id).delete(synchronize_session=False)
        db.commit()


@pytest.mark.parametrize("role_code", ["CM", "GSM", "HR_CIRCLE", "SYSTEM_ADMIN"])
def test_all_roles_share_four_character_minimum_password_policy(role_code: str) -> None:
    assert password_policy_error("abc", role_code)
    assert password_policy_error("abcd", role_code) is None
    assert password_policy_error("1234", role_code) is None
    assert password_policy_error("Ab12Cd", role_code) is None
    assert password_policy_error("ab 12", role_code) is None
    assert password_policy_error("Test1!", role_code) is None
    assert password_policy_error("中文密码", role_code) is None


def test_security_headers_and_secure_cookie_follow_verified_https(monkeypatch) -> None:
    with TestClient(app) as client:
        login_page = client.get("/login")
        assert login_page.status_code == 200
        assert login_page.headers["x-content-type-options"] == "nosniff"
        assert login_page.headers["x-frame-options"] == "DENY"
        assert login_page.headers["referrer-policy"] == "same-origin"
        assert "frame-ancestors 'none'" in login_page.headers["content-security-policy"]
        assert "script-src 'self'" in login_page.headers["content-security-policy"]
        assert "unsafe-inline" not in login_page.headers["content-security-policy"]
        assert "strict-transport-security" not in login_page.headers
        assert "<script>" not in login_page.text
        assert f'src="static/js/login.js?v={STATIC_CACHE_VERSION}"' in login_page.text

        untrusted = login(client, "CMTEST01")
        assert untrusted.status_code == 200
        assert "; secure" not in untrusted.headers["set-cookie"].lower()

        spoofed_forward = client.post(
            "/api/login",
            headers={"X-Forwarded-Proto": "https"},
            json={"employee_no": "CMTEST01", "password": "1234"},
        )
        assert spoofed_forward.status_code == 200
        assert "; secure" not in spoofed_forward.headers["set-cookie"].lower()
        assert "strict-transport-security" not in spoofed_forward.headers

    monkeypatch.setenv("RECOGNITION_TRUSTED_PROXY_IPS", "testclient")
    with TestClient(app) as proxied_client:
        # TestClient sends the forwarded header per request so an untrusted caller
        # cannot force Secure cookies or HSTS on a local HTTP deployment.
        proxied = proxied_client.post(
            "/api/login",
            headers={"X-Forwarded-Proto": "https"},
            json={"employee_no": "CMTEST01", "password": "1234"},
        )
        assert proxied.status_code == 200
        assert "; secure" in proxied.headers["set-cookie"].lower()
        assert proxied.headers["strict-transport-security"] == "max-age=31536000"

    with TestClient(app, base_url="https://testserver") as https_client:
        direct_https = login(https_client, "CMTEST01")
        assert direct_https.status_code == 200
        assert "; secure" in direct_https.headers["set-cookie"].lower()
        assert direct_https.headers["strict-transport-security"] == "max-age=31536000"


def test_password_change_revokes_other_sessions_but_keeps_current() -> None:
    login_account = "GSMTEST01"
    original_password = "1234"
    strong_password = "GsmSecure12"
    restore_account(login_account, original_password)
    try:
        with TestClient(app) as current_client, TestClient(app) as other_client:
            assert login(current_client, login_account, original_password).status_code == 200
            assert login(other_client, login_account, original_password).status_code == 200

            changed = current_client.post(
                "/api/password",
                json={
                    "current_password": original_password,
                    "new_password": strong_password,
                    "confirm_password": strong_password,
                },
            )
            assert changed.status_code == 200
            assert current_client.get("/api/me").status_code == 200
            assert other_client.get("/api/me").status_code == 401

            with TestClient(app) as login_check:
                assert login(login_check, login_account, original_password).status_code == 401
                assert login(login_check, login_account, strong_password).status_code == 200
    finally:
        restore_account(login_account, original_password)


def test_frontline_password_change_keeps_existing_four_digit_rule() -> None:
    login_account = "CMTEST02"
    original_password = "1234"
    replacement = "5678"
    restore_account(login_account, original_password)
    try:
        with TestClient(app) as client:
            assert login(client, login_account, original_password).status_code == 200
            changed = client.post(
                "/api/password",
                json={
                    "current_password": original_password,
                    "new_password": replacement,
                    "confirm_password": replacement,
                },
            )
            assert changed.status_code == 200
            assert client.get("/api/me").status_code == 200
    finally:
        restore_account(login_account, original_password)


def test_employee_reset_uses_account_suffix_revokes_sessions_and_forces_change() -> None:
    target_account = "CMTEST02"
    resetter_account = "GSMTEST01"
    restore_account(target_account, "1234")
    restore_account(resetter_account, "1234")
    try:
        with TestClient(app) as target_client, TestClient(app) as resetter:
            assert login(target_client, target_account, "1234").status_code == 200
            assert login(resetter, resetter_account, "1234").status_code == 200
            reset = resetter.post(
                "/api/accounts/reset-password",
                json={"employee_no": target_account, "name": "测试CM乙"},
            )
            assert reset.status_code == 200, reset.text
            reset_password = reset.json()["temporary_password"]
            assert reset_password == target_account[-4:]
            assert target_client.get("/api/me").status_code == 401

        with TestClient(app) as first_login:
            assert login(first_login, target_account, "1234").status_code == 401
            assert login(first_login, target_account, reset_password).status_code == 200
            assert first_login.get("/api/me").json()["must_change_password"] is True
            blocked = first_login.get("/api/options")
            assert blocked.status_code == 403
            assert blocked.json()["detail"]["code"] == "PASSWORD_CHANGE_REQUIRED"
            changed = first_login.post(
                "/api/password",
                json={
                    "current_password": reset_password,
                    "new_password": "6789",
                    "confirm_password": "6789",
                },
            )
            assert changed.status_code == 200, changed.text
            assert first_login.get("/api/options").status_code == 200
    finally:
        restore_account(target_account, "1234")
        restore_account(resetter_account, "1234")


def test_am_and_om_keep_normal_password_reset_permission() -> None:
    assert "PASSWORD_RESET" in ROLE_PERMISSION_CODES["AM"]
    assert "PASSWORD_RESET" in ROLE_PERMISSION_CODES["OM"]


def test_account_name_correction_scopes_and_audits_changes() -> None:
    original_name = "测试CM乙"
    corrected_name = "测试CM乙修正"
    with SessionLocal() as db:
        target = db.query(Employee).filter_by(employee_no="CMTEST02").one()
        target.name = original_name
        db.commit()
    try:
        with TestClient(app) as admin:
            assert login(admin, "HR01", "HR123").status_code == 200
            found = admin.get("/api/accounts/name-targets", params={"keyword": "CMTEST02"})
            assert found.status_code == 200, found.text
            row = next(item for item in found.json()["items"] if item["employee_no"] == "CMTEST02")
            changed = admin.post("/api/accounts/update-name", json={"employee_id": row["id"], "name": corrected_name})
            assert changed.status_code == 200, changed.text
            assert changed.json()["name"] == corrected_name
            with SessionLocal() as db:
                assert db.query(Employee).filter_by(employee_no="CMTEST02").one().name == corrected_name
                assert db.query(Employee).filter_by(employee_no="HR01").one().name == "最高管理员"
        with TestClient(app) as frontline:
            assert login(frontline, "CMTEST01", "1234").status_code == 200
            assert frontline.get("/api/accounts/name-targets", params={"keyword": "CMTEST02"}).status_code == 403
    finally:
        with SessionLocal() as db:
            db.query(Employee).filter_by(employee_no="CMTEST02").one().name = original_name
            db.commit()


def test_admin_legacy_rotation_is_persisted_and_not_repeated(monkeypatch) -> None:
    login_account = "HR01"
    bootstrap_password = "Bootstrap1!Secure"
    monkeypatch.delenv("RECOGNITION_ENABLE_TEST_ACCOUNTS", raising=False)
    monkeypatch.setenv("RECOGNITION_BOOTSTRAP_ADMIN_PASSWORD", bootstrap_password)
    with SessionLocal() as db:
        account = db.query(UserAccount).filter_by(login_account=login_account).one()
        account.password_hash = hash_password("legacy-placeholder")
        account.must_change_password = False
        account.credential_initialized = False
        db.commit()
        ensure_highest_admin_account(db)
        db.refresh(account)
        first_hash = account.password_hash
        assert account.credential_initialized is True
        assert account.must_change_password is True
        assert verify_password(bootstrap_password, first_hash)
        ensure_highest_admin_account(db)
        db.refresh(account)
        assert account.password_hash == first_hash
    restore_account(login_account, "HR123")


def test_existing_test_accounts_are_disabled_outside_test_mode(monkeypatch) -> None:
    login_account = "CMTEST01"
    restore_account(login_account, "1234")
    try:
        monkeypatch.delenv("RECOGNITION_ENABLE_TEST_ACCOUNTS", raising=False)
        with SessionLocal() as db:
            account = db.query(UserAccount).filter_by(login_account=login_account).one()
            db.add(UserSession(account_id=account.id, token_hash="f" * 64, expires_at=datetime.now() + timedelta(hours=1)))
            db.commit()
            disable_test_accounts(db)
            db.refresh(account)
            assert account.enabled is False
            assert db.query(UserSession).filter_by(account_id=account.id).count() == 0
    finally:
        with SessionLocal() as db:
            test_accounts = (
                db.query(UserAccount)
                .join(Employee, Employee.id == UserAccount.employee_id)
                .filter((Employee.employee_no.like("%TEST%")) | (Employee.name.like("%测试%")))
                .all()
            )
            for account in test_accounts:
                account.enabled = True
            db.commit()
        restore_account(login_account, "1234")


def test_cross_site_state_changes_are_rejected() -> None:
    with TestClient(app) as client:
        body = {"employee_no": "CMTEST01", "password": "1234"}

        cross_site = client.post(
            "/api/login",
            json=body,
            headers={"Origin": "https://attacker.example"},
        )
        assert cross_site.status_code == 403

        cross_site_referer = client.post(
            "/api/login",
            json=body,
            headers={"Referer": "https://attacker.example/page"},
        )
        assert cross_site_referer.status_code == 403

        same_origin = client.post("/api/login", json=body, headers={"Origin": "http://testserver"})
        assert same_origin.status_code == 200, same_origin.text

        no_header = client.post("/api/login", json=body)
        assert no_header.status_code == 200, no_header.text

        cross_site_get = client.get("/api/options", headers={"Origin": "https://attacker.example"})
        assert cross_site_get.status_code == 200  # GET is not CSRF-guarded; session from the login above still works


def test_versions_come_from_a_single_source() -> None:
    with TestClient(app) as client:
        assert client.get("/health").json()["version"] == APP_VERSION

        login_page = client.get("/login").text
        app_page = client.get("/").text
        assert login_page.count(f"v={STATIC_CACHE_VERSION}") == 2
        assert app_page.count(f"v={STATIC_CACHE_VERSION}") == 2
        assert "__STATIC_CACHE_VERSION__" not in login_page
        assert "__STATIC_CACHE_VERSION__" not in app_page
