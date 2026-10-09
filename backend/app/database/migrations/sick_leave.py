"""Historical sick-leave table rebuild and index repair; preserve first-error checks."""
from __future__ import annotations

from sqlalchemy import text


SICK_LEAVE_EXTRA_INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS ix_sick_leave_month_employee_status ON sick_leave_records (attendance_month, employee_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_sick_leave_pr_ranking ON sick_leave_records (status, leave_start_date, leave_end_date, employee_id)",
    "CREATE INDEX IF NOT EXISTS ix_sick_leave_type ON sick_leave_records (leave_type, attendance_month, employee_id, status)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_sick_leave_violation_deduction ON sick_leave_records (violation_deduction_id) WHERE violation_deduction_id IS NOT NULL",
)


def ensure_sick_leave_import_columns(db) -> None:
    """Make sick leave rows importable without an employee proof attachment."""
    columns = {row[1]: row for row in db.execute(text("PRAGMA table_info(sick_leave_records)"))}
    for name, definition in {
        "leave_type": "VARCHAR(30) NOT NULL DEFAULT '病假'",
        "import_source": "VARCHAR(30) NOT NULL DEFAULT 'manual'",
    }.items():
        if name not in columns:
            db.execute(text(f"ALTER TABLE sick_leave_records ADD COLUMN {name} {definition}"))
    db.execute(text("UPDATE sick_leave_records SET leave_type='病假' WHERE leave_type IS NULL OR leave_type=''"))
    db.execute(text("UPDATE sick_leave_records SET import_source='manual' WHERE import_source IS NULL OR import_source=''"))
    db.commit()

    # SQLite cannot relax a NOT NULL constraint in place. Rebuild only when an
    # existing database still requires proof_file_id, preserving every row.
    proof_column = columns.get("proof_file_id")
    if proof_column and proof_column[3]:
        _rebuild_sick_leave_records(db)
    ensure_sick_leave_record_indexes(db)


def ensure_sick_leave_record_indexes(db) -> None:
    """Idempotently create every model and hand-written sick leave index.

    Also repairs databases where the first 2026.09.23.1 rebuild dropped the
    SQLAlchemy column-level indexes.
    """
    from app.v2_models import SickLeaveRecord

    connection = db.connection()
    for index in SickLeaveRecord.__table__.indexes:
        index.create(bind=connection, checkfirst=True)
    for statement in SICK_LEAVE_EXTRA_INDEX_STATEMENTS:
        db.execute(text(statement))
    db.commit()


def _foreign_key_violations(db, table_name: str) -> set[tuple]:
    return {(row[1], row[2]) for row in db.execute(text(f"PRAGMA foreign_key_check({table_name})"))}


def _rebuild_sick_leave_records(db) -> None:
    """Rebuild sick_leave_records from the model definition in one transaction.

    Follows the SQLite "other kinds of table schema changes" procedure: create
    the new table under a temporary name, copy, drop the old table, then rename.
    Indexes are recreated afterwards by ensure_sick_leave_record_indexes.
    """
    from sqlalchemy.schema import CreateTable

    from app.v2_models import SickLeaveRecord

    table = SickLeaveRecord.__table__
    temp_name = "sick_leave_records_rebuild"
    create_sql = str(CreateTable(table).compile(dialect=db.get_bind().dialect)).strip()
    prefix = f"CREATE TABLE {table.name} ("
    if not create_sql.startswith(prefix):
        raise RuntimeError(f"Unexpected DDL for {table.name}: {create_sql[:80]}")
    create_sql = f"CREATE TABLE {temp_name} (" + create_sql[len(prefix):]

    existing = {row[1] for row in db.execute(text("PRAGMA table_info(sick_leave_records)"))}
    copy_columns = ", ".join(column.name for column in table.columns if column.name in existing)

    # PRAGMA foreign_keys is a no-op inside a transaction, so switch it off
    # before BEGIN and back on only after COMMIT/ROLLBACK.
    db.commit()
    db.execute(text("PRAGMA foreign_keys=OFF"))
    try:
        dbapi_connection = db.connection().connection.dbapi_connection
        if not dbapi_connection.in_transaction:
            db.execute(text("BEGIN"))
        preexisting_violations = _foreign_key_violations(db, "sick_leave_records")
        db.execute(text(f"DROP TABLE IF EXISTS {temp_name}"))
        db.execute(text(create_sql))
        db.execute(text(f"INSERT INTO {temp_name} ({copy_columns}) SELECT {copy_columns} FROM sick_leave_records"))
        db.execute(text("DROP TABLE sick_leave_records"))
        db.execute(text(f"ALTER TABLE {temp_name} RENAME TO sick_leave_records"))
        # Rows are copied verbatim, so only violations the rebuild itself
        # introduced (e.g. a column mapping error) abort the migration.
        new_violations = _foreign_key_violations(db, "sick_leave_records") - preexisting_violations
        if new_violations:
            raise RuntimeError(f"sick_leave_records rebuild broke foreign keys: {sorted(new_violations)[:10]}")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.execute(text("PRAGMA foreign_keys=ON"))
        db.commit()
