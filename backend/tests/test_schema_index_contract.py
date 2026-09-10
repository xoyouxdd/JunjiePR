from __future__ import annotations

import os
import tempfile
from pathlib import Path


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-schema-contract-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)

from app.v2_database import SessionLocal, ensure_second_audit_query_indexes, init_db  # noqa: E402
from sqlalchemy import text  # noqa: E402


def index_names(db, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(text(f"PRAGMA index_list({table})")).all()}


def test_new_database_has_the_measured_indexes_without_exact_history_duplicates() -> None:
    init_db()
    with SessionLocal() as db:
        assert "ix_audit_operator_action_recent" in index_names(db, "audit_logs")
        assert "ix_recognition_month_close_scope" in index_names(db, "recognition_records")
        assert "ix_deduction_month_close_scope" in index_names(db, "deduction_records")
        history = index_names(db, "employee_number_history")
        assert "ix_employee_number_history_old_number" in history
        assert "ix_employee_number_history_new_number" in history
        assert "ix_employee_number_history_old_employee_no" not in history
        assert "ix_employee_number_history_new_employee_no" not in history


def test_upgrade_step_removes_only_the_two_proven_duplicate_indexes() -> None:
    init_db()
    with SessionLocal() as db:
        db.execute(text("CREATE INDEX ix_employee_number_history_old_employee_no ON employee_number_history (old_employee_no)"))
        db.execute(text("CREATE INDEX ix_employee_number_history_new_employee_no ON employee_number_history (new_employee_no)"))
        db.commit()
        ensure_second_audit_query_indexes(db)
        history = index_names(db, "employee_number_history")

    assert "ix_employee_number_history_old_number" in history
    assert "ix_employee_number_history_new_number" in history
    assert "ix_employee_number_history_old_employee_no" not in history
    assert "ix_employee_number_history_new_employee_no" not in history


def test_schema_initialization_is_idempotent() -> None:
    init_db()
    with SessionLocal() as db:
        before = {
            table: index_names(db, table)
            for table in ("audit_logs", "recognition_records", "deduction_records", "employee_number_history")
        }
    init_db()
    with SessionLocal() as db:
        after = {
            table: index_names(db, table)
            for table in ("audit_logs", "recognition_records", "deduction_records", "employee_number_history")
        }

    assert after == before
