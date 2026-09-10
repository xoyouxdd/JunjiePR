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


def index_columns(db, name: str) -> list[str]:
    return [str(row[2]) for row in db.execute(text(f"PRAGMA index_xinfo({name})")).all() if row[2]]


def explain_detail(db, sql: str, params: dict) -> str:
    rows = db.execute(text("EXPLAIN QUERY PLAN " + sql), params).all()
    return " | ".join(str(row[-1]) for row in rows)


def test_month_close_index_columns_and_query_plans_avoid_full_scans() -> None:
    init_db()
    with SessionLocal() as db:
        assert index_columns(db, "ix_recognition_month_close_scope") == ["home_attraction_id", "recognition_month", "status"]
        assert index_columns(db, "ix_deduction_month_close_scope") == ["attraction_id_snapshot", "deduction_month", "status"]
        circle_id = db.execute(text("SELECT id FROM attractions WHERE employee_circle=1 LIMIT 1")).scalar()
        employee_id = db.execute(text("SELECT id FROM employees LIMIT 1")).scalar()
        type_id = db.execute(text("SELECT id FROM recognition_types LIMIT 1")).scalar()
        assert circle_id and employee_id and type_id
        db.execute(
            text(
                "INSERT INTO recognition_records ("
                "employee_id, employee_no, employee_name, employee_role_snapshot, home_attraction_id, home_attraction_name, "
                "occurred_attraction_id, recognition_date, recognition_month, recognition_type_id, recognition_type_name, "
                "content, recognizer_employee_id, recognizer_name, recognizer_role_snapshot, operator_employee_id, operator_name, "
                "source, fraction, credited_fraction, monthly_cap_status, status, submitted_at) "
                "VALUES (:employee, '0000001', '测', 'CM', :circle, '热力追踪', :circle, '2026-09-02', '2026-09', :type_id, '安全', "
                "'计划', :employee, '测', 'TA主管', :employee, '测', 'manager', 0.5, 0.5, 'not_applicable', 'pending', CURRENT_TIMESTAMP)"
            ),
            {"circle": circle_id, "employee": employee_id, "type_id": type_id},
        )
        db.commit()
        circle_plan = explain_detail(
            db,
            "SELECT COUNT(*) FROM recognition_records WHERE home_attraction_id=:id AND recognition_month=:month AND status='pending'",
            {"id": circle_id, "month": "2026-09"},
        )
        global_plan = explain_detail(
            db,
            "SELECT COUNT(*) FROM recognition_records WHERE recognition_month=:month AND status='pending'",
            {"month": "2026-09"},
        )
        deduction_circle = explain_detail(
            db,
            "SELECT COUNT(*) FROM deduction_records WHERE attraction_id_snapshot=:id AND deduction_month=:month AND status='pending_material'",
            {"id": circle_id, "month": "2026-09"},
        )
        deduction_global = explain_detail(
            db,
            "SELECT COUNT(*) FROM deduction_records WHERE deduction_month=:month AND status='pending_material'",
            {"month": "2026-09"},
        )

    assert "ix_recognition_month_close_scope" in circle_plan
    assert "SCAN recognition_records" not in global_plan
    assert "SCAN deduction_records" not in deduction_circle
    assert "SCAN deduction_records" not in deduction_global
