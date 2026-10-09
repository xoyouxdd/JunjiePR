"""Database module boundaries and startup behavior, without using a deployed DB."""
from __future__ import annotations

import ast
import importlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Event, Lock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app.database import bootstrap
from app.database.connection import Base
from app.database.migrations import registry


BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
EXPECTED_STEPS = (
    ("2026-08-void-audit-columns", "ensure_void_audit_columns"),
    ("2026-08-credential-columns", "ensure_credential_columns"),
    ("2026-08-attraction-dimensions", "ensure_attraction_dimensions"),
    ("2026-08-performance-indexes", "ensure_performance_indexes"),
    ("2026-08-governance-case-indexes", "ensure_governance_case_indexes"),
    ("2026-08-recognition-same-day-duplicate", "ensure_recognition_same_day_duplicate_columns"),
    ("2026-08-deduction-statement-upgrade", "ensure_deduction_statement_upgrade_columns"),
    ("2026-09-recognition-credit-cap-and-poc", "ensure_recognition_credit_cap_and_poc_columns"),
    ("2026-08-employee-number-history", "ensure_employee_number_history"),
    ("2026-09-deduction-photo-pdf-materials", "ensure_deduction_photo_pdf_materials"),
    ("2026-09-collaborative-materials-and-violation-absence", "ensure_collaborative_material_columns"),
    ("2026-09-account-status-password-timestamp", "ensure_account_status_password_timestamp"),
    ("2026-09-login-account-archive", "ensure_login_account_archive_columns"),
    ("2026-09-submission-payload-digest", "ensure_submission_payload_digest"),
    ("2026-09-second-audit-query-indexes", "ensure_second_audit_query_indexes"),
    ("2026-09-material-job-claim-generation", "ensure_material_job_claim_generation"),
    ("2026-09-sick-leave-import", "ensure_sick_leave_import_columns"),
    ("2026-10-acting-duties", "ensure_acting_duty_columns_and_migrate"),
    ("2026-10-recognition-shared-evidence", "ensure_recognition_shared_evidence"),
    ("2026-10-audit-scope-and-appeal-removal", "backfill_audit_scope_and_remove_appeals"),
    ("2026-10-group-leader-types", "ensure_group_leader_types"),
    ("2026-10-group-codes", "ensure_group_codes"),
    ("2026-10-group-codes-resequence", "resequence_all_group_codes"),
    ("2026-09-sick-leave-index-repair", "ensure_sick_leave_record_indexes"),
)


@pytest.fixture
def isolated_dir():
    # Windows restricted runners cannot reopen pytest's owner-only temp ACLs.
    # Use a regular UUID directory under this project's test output instead.
    base = (BACKEND.parent / "output/refactor-validation/phase3/database-storage").resolve()
    directory = base / uuid4().hex
    directory.mkdir(parents=True)
    try:
        yield directory
    finally:
        assert base.is_relative_to(BACKEND.parent.resolve())
        assert directory.resolve().parent == base
        shutil.rmtree(directory)


def isolated_python(tmp_path: Path, code: str, **settings: str) -> None:
    env = os.environ.copy()
    for key in (
        "RECOGNITION_ENABLE_TEST_ACCOUNTS", "RECOGNITION_TEST_DEFAULT_PASSWORD",
        "RECOGNITION_TEST_ADMIN_PASSWORD", "RECOGNITION_BOOTSTRAP_ADMIN_PASSWORD",
    ):
        env.pop(key, None)
    env["RECOGNITION_V2_DATA_DIR"] = str(tmp_path / "isolated-data")
    env.update(settings)
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND, env=env,
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("first_module", ["app.v2_models", "app.rotation.models", "app.v2_database"])
def test_fresh_imports_share_base_and_never_create_data_dirs(isolated_dir: Path, first_module: str) -> None:
    isolated_python(isolated_dir, f"""
import importlib
from pathlib import Path
importlib.import_module({first_module!r})
from app import v2_database, v2_models
from app.rotation import models
from app.database import connection
assert connection.BASE_DIR == Path.cwd()
assert v2_database.Base is connection.Base
assert v2_models.Base is models.Base is connection.Base
assert v2_database.engine is connection.engine
assert v2_database.SessionLocal is connection.SessionLocal
assert not connection.DATA_DIR.exists()
assert not connection.DB_PATH.exists()
""")


def test_models_and_live_scope_service_do_not_depend_on_database_facade() -> None:
    for relative in ("v2_models.py", "rotation/models.py", "services/management_scopes.py"):
        tree = ast.parse((APP / relative).read_text(encoding="utf-8"))
        imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert "app.v2_database" not in imports
        if "models.py" in relative:
            assert "app.database.connection" in imports
        else:
            assert "app.services.identity" in imports
            assert "app.v2_services" not in imports
    imports = ast.parse((APP / "database/connection.py").read_text(encoding="utf-8"))
    assert all(
        not (node.module or "").startswith("app.")
        for node in ast.walk(imports) if isinstance(node, ast.ImportFrom)
    )


def test_explicit_facade_exports_reference_their_current_owners() -> None:
    facade = importlib.import_module("app.v2_database")
    tree = ast.parse((APP / "v2_database.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.module == "__future__":
            continue
        owner = importlib.import_module(node.module)
        for alias in node.names:
            assert alias.name != "*"
            assert getattr(facade, alias.asname or alias.name) is getattr(owner, alias.name)


def test_stable_migration_keys_order_and_explicit_callables() -> None:
    actual = tuple((key, runner.__name__) for key, runner in registry.MIGRATION_RUNNERS)
    assert actual == EXPECTED_STEPS
    assert tuple(registry.SCHEMA_MIGRATION_STEPS) == EXPECTED_STEPS
    assert len({key for key, _ in actual}) == len(actual)
    assert all(callable(runner) for _, runner in registry.MIGRATION_RUNNERS)


def test_migrations_commit_each_completed_step_and_skip_applied_steps(monkeypatch) -> None:
    calls = []
    commits = []
    local_engine = create_engine("sqlite://")
    try:
        with Session(local_engine) as db:
            event.listen(db, "after_commit", lambda session: commits.append(True))

            def first(session):
                calls.append("first")
                session.execute(text("CREATE TABLE migrated (value INTEGER)"))

            def second(session):
                calls.append("second")
                session.execute(text("INSERT INTO migrated VALUES (42)"))

            monkeypatch.setattr(registry, "MIGRATION_RUNNERS", (("first", first), ("second", second)))
            registry.run_schema_migration_steps(db)
            assert len(commits) == 3  # registry creation and each successful step
            registry.run_schema_migration_steps(db)
            assert len(commits) == 4
            assert calls == ["first", "second"]
            assert db.execute(text("SELECT value FROM migrated")).scalars().all() == [42]
            assert db.execute(text("SELECT step FROM schema_migration_steps ORDER BY rowid")).scalars().all() == ["first", "second"]
    finally:
        local_engine.dispose()


def test_failed_migration_remains_unrecorded_and_completed_step_is_preserved(monkeypatch) -> None:
    local_engine = create_engine("sqlite://")
    try:
        with Session(local_engine) as db:
            def first(session):
                session.execute(text("CREATE TABLE migrated (value INTEGER)"))

            def failed(session):
                session.execute(text("INSERT INTO migrated VALUES (1)"))
                raise RuntimeError("isolated migration failure")

            monkeypatch.setattr(registry, "MIGRATION_RUNNERS", (("first", first), ("failed", failed)))
            with pytest.raises(RuntimeError, match="isolated migration failure"):
                registry.run_schema_migration_steps(db)
            db.rollback()
            assert db.execute(text("SELECT step FROM schema_migration_steps")).scalars().all() == ["first"]
            assert db.execute(text("SELECT value FROM migrated")).scalars().all() == []
    finally:
        local_engine.dispose()


def mock_bootstrap(monkeypatch):
    calls = []

    class FakeSession:
        def close(self):
            calls.append("close")

    def record(name):
        def call(*args, **kwargs):
            calls.append(name)
        return call

    monkeypatch.setattr(bootstrap, "ensure_directories", record("directories"))
    monkeypatch.setattr(Base.metadata, "create_all", record("create_all"))
    monkeypatch.setattr(bootstrap, "SessionLocal", FakeSession)
    for name in (
        "run_schema_migration_steps", "seed_reference_data", "seed_test_accounts",
        "ensure_gsm_management_scopes", "disable_test_accounts", "ensure_highest_admin_account",
        "ensure_circle_hr_accounts", "create_score_view",
    ):
        monkeypatch.setattr(bootstrap, name, record(name))
    rotation_access = importlib.import_module("app.rotation.access")
    monkeypatch.setattr(rotation_access, "ensure_rotation_accounts", record("ensure_rotation_accounts"))
    return calls


def test_each_start_reapplies_guarantees_in_original_order_and_closes_session(monkeypatch) -> None:
    calls = mock_bootstrap(monkeypatch)
    expected = [
        "directories", "create_all", "run_schema_migration_steps", "seed_reference_data",
        "seed_test_accounts", "ensure_gsm_management_scopes", "disable_test_accounts",
        "ensure_highest_admin_account", "ensure_circle_hr_accounts", "create_score_view",
        "ensure_rotation_accounts", "close",
    ]
    bootstrap.init_db()
    bootstrap.init_db()
    assert calls == expected * 2


def test_startup_failure_closes_session_and_releases_the_init_lock(monkeypatch) -> None:
    calls = mock_bootstrap(monkeypatch)

    def fail(session):
        raise RuntimeError("isolated startup failure")

    monkeypatch.setattr(bootstrap, "run_schema_migration_steps", fail)
    with pytest.raises(RuntimeError, match="isolated startup failure"):
        bootstrap.init_db()
    assert calls == ["directories", "create_all", "close"]
    monkeypatch.setattr(bootstrap, "run_schema_migration_steps", lambda db: None)
    bootstrap.init_db()
    assert calls[-1] == "close"


def test_startup_lock_serializes_overlapping_calls(monkeypatch) -> None:
    first_entered, second_entered, release_first = Event(), Event(), Event()
    sequence_lock = Lock()
    calls = []

    def unlocked():
        with sequence_lock:
            number = len(calls)
            calls.append(number)
        if number == 0:
            first_entered.set()
            assert release_first.wait(3)
        else:
            second_entered.set()

    monkeypatch.setattr(bootstrap, "_init_db_unlocked", unlocked)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(bootstrap.init_db)
        assert first_entered.wait(3)
        second = workers.submit(bootstrap.init_db)
        try:
            assert not second_entered.wait(0.1)
        finally:
            release_first.set()
        first.result(timeout=3)
        second.result(timeout=3)
    assert second_entered.is_set()
    assert calls == [0, 1]


def test_isolated_bootstrap_is_idempotent_preserves_passwords_and_never_runs_cleanup(isolated_dir: Path) -> None:
    isolated_python(isolated_dir, """
from decimal import Decimal
from sqlalchemy import text
from app.v2_database import init_db, SessionLocal, SCHEMA_MIGRATION_STEPS
from app.v2_models import (
    DeductionType, Employee, Permission, RecognitionScoreRule, Role, RolePermission, UserAccount,
)
from app.database.reference_data import ROLE_PERMISSION_CODES
init_db()
with SessionLocal() as db:
    assert db.query(Employee).filter(Employee.employee_no.like('%TEST%')).count() == 0
    snapshots = {row.login_account: row.password_hash for row in db.query(UserAccount)}
    assert snapshots.keys() == {'HR01', 'HR-HEAT', 'HR-DWARF', 'HR-BEAR'}
    assert all(not row.enabled for row in db.query(UserAccount).filter(UserAccount.login_account != 'HR01'))
    rule = db.query(RecognitionScoreRule).first()
    rule_id = rule.id
    rule.score = Decimal('8.00')
    db.add(DeductionType(code='ATTENDANCE', name='legacy', active=False))
    db.commit()
init_db()
with SessionLocal() as db:
    assert {row.login_account: row.password_hash for row in db.query(UserAccount)} == snapshots
    assert db.get(RecognitionScoreRule, rule_id).score == Decimal('8.00')
    assert db.query(DeductionType).filter(DeductionType.code == 'ATTENDANCE').count() == 1
    assert db.execute(text('SELECT count(*) FROM schema_migration_steps')).scalar_one() == len(SCHEMA_MIGRATION_STEPS)
    assert db.execute(text("SELECT count(*) FROM sqlite_master WHERE type='view' AND name='v_employee_month_scores'")).scalar_one() == 1
    for code, desired in ROLE_PERMISSION_CODES.items():
        actual = {value for (value,) in db.query(Permission.code).join(RolePermission).join(Role).filter(Role.code == code)}
        assert actual == set(desired)
""", RECOGNITION_BOOTSTRAP_ADMIN_PASSWORD="isolated-database-contract")
