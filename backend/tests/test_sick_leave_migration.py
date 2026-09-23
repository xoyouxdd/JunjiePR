from __future__ import annotations

import os
import tempfile
from pathlib import Path


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-sick-migration-test-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)

from sqlalchemy import create_engine, event, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.v2_database import (  # noqa: E402
    Base,
    SCHEMA_MIGRATION_STEPS,
    ensure_sick_leave_import_columns,
    ensure_sick_leave_record_indexes,
)
from app import v2_models  # noqa: E402,F401


# Pre-2026.09.23.1 shape: proof_file_id NOT NULL, no leave_type/import_source.
LEGACY_TABLE_SQL = (
    "CREATE TABLE sick_leave_records ("
    "id INTEGER NOT NULL PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id), "
    "employee_no_snapshot VARCHAR(50), employee_name_snapshot VARCHAR(100), employee_role_snapshot VARCHAR(30), "
    "attraction_id_snapshot INTEGER, attendance_month VARCHAR(7) NOT NULL, leave_start_date VARCHAR(10) NOT NULL, "
    "leave_end_date VARCHAR(10) NOT NULL, leave_days NUMERIC(6, 1) NOT NULL, charged_days NUMERIC(6, 1) NOT NULL, "
    "proof_file_id INTEGER NOT NULL REFERENCES stored_files(id), is_violation BOOLEAN NOT NULL DEFAULT 0, "
    "violation_deduction_id INTEGER REFERENCES deduction_records(id), note TEXT, status VARCHAR(20) NOT NULL, "
    "submitted_by INTEGER NOT NULL REFERENCES employees(id), submitted_by_name VARCHAR(100) NOT NULL, submitted_at DATETIME NOT NULL, "
    "voided_by INTEGER REFERENCES employees(id), voided_by_name VARCHAR(100), voided_by_role_code VARCHAR(30), "
    "voided_by_role_name VARCHAR(30), void_permission_scope_snapshot VARCHAR(255), voided_from_status VARCHAR(20), "
    "voided_at DATETIME, void_reason TEXT)"
)

LEGACY_INDEX_SQL = (
    "CREATE INDEX ix_sick_leave_records_employee_id ON sick_leave_records (employee_id)",
    "CREATE INDEX ix_sick_leave_records_employee_no_snapshot ON sick_leave_records (employee_no_snapshot)",
    "CREATE INDEX ix_sick_leave_records_employee_name_snapshot ON sick_leave_records (employee_name_snapshot)",
    "CREATE INDEX ix_sick_leave_records_attraction_id_snapshot ON sick_leave_records (attraction_id_snapshot)",
    "CREATE INDEX ix_sick_leave_records_attendance_month ON sick_leave_records (attendance_month)",
    "CREATE INDEX ix_sick_leave_records_is_violation ON sick_leave_records (is_violation)",
    "CREATE UNIQUE INDEX ix_sick_leave_records_violation_deduction_id ON sick_leave_records (violation_deduction_id)",
    "CREATE INDEX ix_sick_leave_records_status ON sick_leave_records (status)",
    "CREATE INDEX ix_sick_leave_records_submitted_by ON sick_leave_records (submitted_by)",
    "CREATE INDEX ix_sick_leave_month_employee_status ON sick_leave_records (attendance_month, employee_id, status)",
    "CREATE INDEX ix_sick_leave_pr_ranking ON sick_leave_records (status, leave_start_date, leave_end_date, employee_id)",
    "CREATE UNIQUE INDEX ix_sick_leave_violation_deduction ON sick_leave_records (violation_deduction_id) WHERE violation_deduction_id IS NOT NULL",
)

# Table produced by the first (buggy) 2026.09.23.1 rebuild, before indexes.
BUGGY_REBUILT_TABLE_SQL = (
    "CREATE TABLE sick_leave_records ("
    "id INTEGER NOT NULL PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id), "
    "employee_no_snapshot VARCHAR(50), employee_name_snapshot VARCHAR(100), employee_role_snapshot VARCHAR(30), "
    "attraction_id_snapshot INTEGER, attendance_month VARCHAR(7) NOT NULL, leave_start_date VARCHAR(10) NOT NULL, "
    "leave_end_date VARCHAR(10) NOT NULL, leave_days NUMERIC(6,1) NOT NULL, charged_days NUMERIC(6,1) NOT NULL, "
    "proof_file_id INTEGER REFERENCES stored_files(id), leave_type VARCHAR(30) NOT NULL DEFAULT '病假', "
    "import_source VARCHAR(30) NOT NULL DEFAULT 'manual', is_violation BOOLEAN NOT NULL DEFAULT 0, "
    "violation_deduction_id INTEGER UNIQUE REFERENCES deduction_records(id), note TEXT, status VARCHAR(20) NOT NULL DEFAULT 'active', "
    "submitted_by INTEGER NOT NULL REFERENCES employees(id), submitted_by_name VARCHAR(100) NOT NULL, submitted_at DATETIME NOT NULL, "
    "voided_by INTEGER REFERENCES employees(id), voided_by_name VARCHAR(100), voided_by_role_code VARCHAR(30), "
    "voided_by_role_name VARCHAR(30), void_permission_scope_snapshot VARCHAR(255), voided_from_status VARCHAR(20), "
    "voided_at DATETIME, void_reason TEXT)"
)
BUGGY_REBUILT_INDEX_SQL = (
    "CREATE INDEX ix_sick_leave_month_employee_status ON sick_leave_records (attendance_month, employee_id, status)",
    "CREATE INDEX ix_sick_leave_pr_ranking ON sick_leave_records (status, leave_start_date, leave_end_date, employee_id)",
    "CREATE INDEX ix_sick_leave_type ON sick_leave_records (leave_type, attendance_month, employee_id, status)",
    "CREATE UNIQUE INDEX ix_sick_leave_violation_deduction ON sick_leave_records (violation_deduction_id) WHERE violation_deduction_id IS NOT NULL",
)

LEGACY_ROWS = (
    # id, employee_id, month, start, end, days, proof, is_violation, deduction, status, voided_by
    (1, 1, "2026-07", "2026-07-03", "2026-07-04", "2.0", 10, 0, None, "active", None),
    (2, 2, "2026-08", "2026-08-10", "2026-08-10", "1.0", 11, 1, 100, "active", None),
    (3, 1, "2026-08", "2026-08-20", "2026-08-21", "1.5", 10, 0, None, "voided", 2),
)


def make_session(path: Path):
    engine = create_engine(f"sqlite:///{path.as_posix()}", future=True)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine, sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()


def build_database(tmp_path: Path, name: str, table_sql: str | None, index_sql: tuple[str, ...] = ()):
    """Create every model table; optionally replace sick_leave_records with a historical shape."""
    engine, db = make_session(tmp_path / name)
    Base.metadata.create_all(bind=engine)
    if table_sql is not None:
        db.execute(text("DROP TABLE sick_leave_records"))
        db.execute(text(table_sql))
        for statement in index_sql:
            db.execute(text(statement))
    db.commit()
    return engine, db


def insert_legacy_rows(db) -> None:
    # Parent tables have many NOT NULL columns; the rows only need to exist
    # as foreign key targets, so seed them with FK checks off.
    db.commit()
    db.execute(text("PRAGMA foreign_keys=OFF"))
    for table, ids in (("employees", (1, 2)), ("stored_files", (10, 11)), ("deduction_records", (100,))):
        columns = [row for row in db.execute(text(f"PRAGMA table_info({table})")) if row[3] and not row[5]]
        names = ["id"] + [row[1] for row in columns]
        for row_id in ids:
            values = {"id": row_id}
            for row in columns:
                values[row[1]] = _dummy_value(row[2], row_id)
            db.execute(
                text(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join(':' + n for n in names)})"),
                values,
            )
    for row in LEGACY_ROWS:
        db.execute(
            text(
                "INSERT INTO sick_leave_records (id, employee_id, employee_no_snapshot, employee_name_snapshot, "
                "employee_role_snapshot, attraction_id_snapshot, attendance_month, leave_start_date, leave_end_date, "
                "leave_days, charged_days, proof_file_id, is_violation, violation_deduction_id, note, status, "
                "submitted_by, submitted_by_name, submitted_at, voided_by, voided_by_name, void_reason) VALUES "
                "(:id, :employee_id, :no, :name, 'CM', 3, :month, :start, :end, :days, :days, :proof, :violation, "
                ":deduction, :note, :status, 2, '主管', '2026-08-31 10:00:00', :voided_by, :voided_by_name, :void_reason)"
            ),
            {
                "id": row[0],
                "employee_id": row[1],
                "no": f"000000{row[1]}",
                "name": f"员工{row[1]}",
                "month": row[2],
                "start": row[3],
                "end": row[4],
                "days": row[5],
                "proof": row[6],
                "violation": row[7],
                "deduction": row[8],
                "note": f"note-{row[0]}",
                "status": row[9],
                "voided_by": row[10],
                "voided_by_name": "主管" if row[10] else None,
                "void_reason": "重复登记" if row[10] else None,
            },
        )
    db.commit()
    db.execute(text("PRAGMA foreign_keys=ON"))
    db.commit()


def _dummy_value(declared_type: str, row_id: int):
    kind = declared_type.upper()
    if kind.startswith(("INTEGER", "BOOLEAN", "NUMERIC", "FLOAT", "DECIMAL")):
        return 0
    if kind.startswith(("DATETIME", "DATE")):
        return "2026-01-01 00:00:00"
    return f"seed-{row_id}"


def index_signature(db) -> set[tuple]:
    signature = set()
    for row in db.execute(text("PRAGMA index_list(sick_leave_records)")).all():
        name, unique, partial = str(row[1]), int(row[2]), int(row[4])
        columns = tuple(str(col[2]) for col in db.execute(text(f"PRAGMA index_info({name})")).all())
        signature.add((name, unique, partial, columns))
    return signature


def table_columns(db) -> list[tuple]:
    return [(row[1], row[2], row[3], row[5]) for row in db.execute(text("PRAGMA table_info(sick_leave_records)")).all()]


def sick_rows(db) -> list[tuple]:
    return [
        tuple(row)
        for row in db.execute(
            text(
                "SELECT id, employee_id, employee_no_snapshot, employee_name_snapshot, employee_role_snapshot, "
                "attraction_id_snapshot, attendance_month, leave_start_date, leave_end_date, leave_days, charged_days, "
                "proof_file_id, is_violation, violation_deduction_id, note, status, submitted_by, submitted_by_name, "
                "submitted_at, voided_by, voided_by_name, void_reason FROM sick_leave_records ORDER BY id"
            )
        ).all()
    ]


def fresh_reference(tmp_path: Path):
    engine, db = build_database(tmp_path, "fresh.db", None)
    try:
        ensure_sick_leave_import_columns(db)
        return index_signature(db), table_columns(db)
    finally:
        db.close()
        engine.dispose()


def test_index_repair_step_is_registered_last() -> None:
    assert SCHEMA_MIGRATION_STEPS[-1] == ("2026-09-sick-leave-index-repair", "ensure_sick_leave_record_indexes")


def test_fresh_database_has_all_model_and_composite_indexes(tmp_path: Path) -> None:
    expected_indexes, _ = fresh_reference(tmp_path)
    names = {item[0] for item in expected_indexes}
    for column in (
        "employee_id", "employee_no_snapshot", "employee_name_snapshot", "attraction_id_snapshot", "attendance_month",
        "leave_type", "import_source", "is_violation", "violation_deduction_id", "status", "submitted_by",
    ):
        assert f"ix_sick_leave_records_{column}" in names
    assert {
        "ix_sick_leave_month_employee_status",
        "ix_sick_leave_pr_ranking",
        "ix_sick_leave_type",
        "ix_sick_leave_violation_deduction",
    } <= names
    assert len(names) == 15


def test_legacy_rebuild_preserves_rows_and_matches_fresh_schema(tmp_path: Path) -> None:
    expected_indexes, expected_columns = fresh_reference(tmp_path)
    engine, db = build_database(tmp_path, "legacy.db", LEGACY_TABLE_SQL, LEGACY_INDEX_SQL)
    try:
        insert_legacy_rows(db)
        before = sick_rows(db)
        assert len(index_signature(db)) == 12

        ensure_sick_leave_import_columns(db)

        assert sick_rows(db) == before
        columns = {row[0]: row for row in table_columns(db)}
        assert columns["proof_file_id"][2] == 0
        assert table_columns(db) == expected_columns
        assert [tuple(row) for row in db.execute(text("SELECT leave_type, import_source FROM sick_leave_records ORDER BY id"))] == [
            ("病假", "manual")
        ] * len(LEGACY_ROWS)
        assert index_signature(db) == expected_indexes
        assert db.execute(text("PRAGMA foreign_key_check(sick_leave_records)")).all() == []
        assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1
        tables = {row[0] for row in db.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        assert "sick_leave_records_rebuild" not in tables
        assert "sick_leave_records_legacy_import" not in tables

        # A nullable proof is now accepted.
        db.execute(
            text(
                "INSERT INTO sick_leave_records (employee_id, attendance_month, leave_start_date, leave_end_date, leave_days, "
                "charged_days, proof_file_id, leave_type, import_source, is_violation, status, submitted_by, submitted_by_name, "
                "submitted_at) VALUES (1, '2026-09', '2026-09-01', '2026-09-01', 1, 1, NULL, '病假', 'import', 0, 'active', 2, "
                "'主管', '2026-09-23 09:00:00')"
            )
        )
        db.commit()

        # Re-running both steps is a no-op for schema and rows.
        snapshot = (sick_rows(db), index_signature(db), table_columns(db))
        ensure_sick_leave_import_columns(db)
        ensure_sick_leave_record_indexes(db)
        assert (sick_rows(db), index_signature(db), table_columns(db)) == snapshot
    finally:
        db.close()
        engine.dispose()


def test_repair_step_restores_indexes_lost_by_the_first_rebuild(tmp_path: Path) -> None:
    expected_indexes, _ = fresh_reference(tmp_path)
    engine, db = build_database(tmp_path, "buggy.db", BUGGY_REBUILT_TABLE_SQL, BUGGY_REBUILT_INDEX_SQL)
    try:
        insert_legacy_rows(db)
        before = sick_rows(db)
        assert len(index_signature(db)) == 5

        ensure_sick_leave_record_indexes(db)

        repaired = index_signature(db)
        assert expected_indexes <= repaired
        # Only the harmless inline-UNIQUE autoindex of the buggy table remains extra.
        assert {item[0] for item in repaired - expected_indexes} == {"sqlite_autoindex_sick_leave_records_1"}
        assert sick_rows(db) == before

        ensure_sick_leave_record_indexes(db)
        assert index_signature(db) == repaired
    finally:
        db.close()
        engine.dispose()


def test_fresh_database_migration_is_idempotent(tmp_path: Path) -> None:
    engine, db = build_database(tmp_path, "fresh-idempotent.db", None)
    try:
        ensure_sick_leave_import_columns(db)
        first = (index_signature(db), table_columns(db))
        ensure_sick_leave_import_columns(db)
        ensure_sick_leave_record_indexes(db)
        assert (index_signature(db), table_columns(db)) == first
    finally:
        db.close()
        engine.dispose()
