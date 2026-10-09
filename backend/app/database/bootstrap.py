"""Serialized database startup: schema patches, per-start guarantees and score view."""
from __future__ import annotations

from threading import Lock

from app.database.accounts import (
    disable_test_accounts, ensure_circle_hr_accounts,
    ensure_highest_admin_account, seed_test_accounts,
)
from app.database.connection import Base, SessionLocal, engine, ensure_directories
from app.database.migrations import run_schema_migration_steps
from app.database.reference_data import seed_reference_data
from app.database.score_view import create_score_view
from app.services.management_scopes import ensure_gsm_management_scopes


_init_db_lock = Lock()


def init_db() -> None:
    # Multiple lifespan starts can overlap in one process (for example, two
    # concurrent TestClient instances). Schema and view setup must be serial.
    with _init_db_lock:
        _init_db_unlocked()


def _init_db_unlocked() -> None:
    ensure_directories()
    from app import v2_models  # noqa: F401
    from app.rotation import models as rotation_models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        run_schema_migration_steps(db)
        # Idempotent per-start guarantees below: reference seeds, account
        # safeguards and GSM scope sync must run on every boot, not once.
        seed_reference_data(db)
        seed_test_accounts(db)
        ensure_gsm_management_scopes(db)
        disable_test_accounts(db)
        ensure_highest_admin_account(db)
        ensure_circle_hr_accounts(db)
        create_score_view(db)
        from app.rotation.access import ensure_rotation_accounts

        ensure_rotation_accounts(db)
    finally:
        db.close()
