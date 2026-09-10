from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
import os
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("RECOGNITION_V2_DATA_DIR", str(BASE_DIR / "data_v2"))).resolve()
FILE_DIR = DATA_DIR / "files"
EXPORT_DIR = DATA_DIR / "exports"
BACKUP_DIR = DATA_DIR / "backups"
DB_PATH = DATA_DIR / "recognition_v2.db"

Base = declarative_base()
engine = create_engine(
    f"sqlite:///{DB_PATH.as_posix()}",
    connect_args={"check_same_thread": False, "timeout": 30},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@event.listens_for(engine, "connect")
def sqlite_pragmas(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def ensure_directories() -> None:
    for path in (DATA_DIR, FILE_DIR, EXPORT_DIR, BACKUP_DIR):
        path.mkdir(parents=True, exist_ok=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


ROLE_DEFINITIONS = (
    ("CM", "CM", 10, False, True),
    ("TR", "TR", 10, False, True),
    ("TA_SUPERVISOR", "TA主管", 20, True, False),
    ("SUPERVISOR", "主管", 20, True, False),
    ("TA_GSM", "TA GSM", 30, False, False),
    ("GSM", "GSM", 30, False, False),
    ("AM", "AM", 40, False, False),
    ("OM", "OM", 50, False, False),
    ("HR_ADMIN", "HR管理员", 90, False, False),
    ("HR_CIRCLE", "景点圈HR", 90, False, False),
    ("SYSTEM_ADMIN", "最高管理员", 100, False, False),
)

PERMISSION_DEFINITIONS = {
    "SELF_RECOGNITION": "本人快速登记",
    "EMPLOYEE_ADD": "为CM/TR代录加分",
    "SICK_REGISTER": "病假登记",
    "DEDUCTION_DIRECT": "所有CM/TR声明扣分",
    "DEDUCTION_ALL": "所有CM/TR全部等级扣分",
    "REVIEW_DIRECT": "直属组员复核",
    "MEMBER_RECORDS": "直属组员记录",
    "DATA_VIEW": "查看三个景点圈统计数据",
    "DATA_EXPORT": "导出三个景点圈数据",
    "HR_MANAGE": "HR人员和组织管理",
    "PASSWORD_RESET": "重置员工账号密码",
    "POC_ISSUE": "开具POC特别贡献认可",
    "SYSTEM_ADMIN": "系统紧急纠错",
}

ROLE_PERMISSION_CODES = {
    "CM": ("SELF_RECOGNITION",),
    "TR": ("SELF_RECOGNITION",),
    "TA_SUPERVISOR": ("EMPLOYEE_ADD", "SICK_REGISTER", "DEDUCTION_DIRECT", "REVIEW_DIRECT", "MEMBER_RECORDS"),
    "SUPERVISOR": ("EMPLOYEE_ADD", "SICK_REGISTER", "DEDUCTION_DIRECT", "REVIEW_DIRECT", "MEMBER_RECORDS"),
    "TA_GSM": ("EMPLOYEE_ADD", "DEDUCTION_ALL", "DATA_VIEW", "POC_ISSUE"),
    "GSM": ("EMPLOYEE_ADD", "DEDUCTION_ALL", "DATA_VIEW", "DATA_EXPORT", "PASSWORD_RESET", "POC_ISSUE"),
    "AM": ("DATA_VIEW", "DATA_EXPORT", "PASSWORD_RESET", "POC_ISSUE"),
    "OM": ("DATA_VIEW", "DATA_EXPORT", "PASSWORD_RESET"),
    "HR_ADMIN": ("HR_MANAGE",),
    "HR_CIRCLE": (
        "DATA_VIEW",
        "DATA_EXPORT",
        "HR_MANAGE",
        "PASSWORD_RESET",
    ),
    "SYSTEM_ADMIN": ("SYSTEM_ADMIN", "HR_MANAGE", "DATA_VIEW", "DATA_EXPORT", "PASSWORD_RESET"),
}

# Seed only creates these when a role has no score rules at all. Later edits
# go through the admin API; startup must not insert a new "today" default.
INITIAL_SCORE_RULE_EFFECTIVE_DATE = "2020-01-01"
DEFAULT_RECOGNIZER_SCORES = {
    "TA_SUPERVISOR": Decimal("0.50"),
    "SUPERVISOR": Decimal("0.50"),
    "TA_GSM": Decimal("1.00"),
    "GSM": Decimal("1.00"),
    "AM": Decimal("1.50"),
    "OM": Decimal("1.50"),
}

EMPLOYEE_CIRCLES = ("热力追踪", "矮人迷宫", "小熊罐子")
RECOGNITION_VENUES = (
    "热力追踪",
    "七个小矮人矿山车",
    "爱丽丝梦游仙境迷宫",
    "小熊维尼历险记",
    "旋转疯蜜罐",
)
LEGACY_CIRCLE_BY_VENUE = {
    "热力追踪": "热力追踪",
    "七个小矮人矿山车": "矮人迷宫",
    "爱丽丝梦游仙境迷宫": "矮人迷宫",
    "小熊维尼历险记": "小熊罐子",
    "旋转蜂蜜罐": "小熊罐子",
    "旋转疯蜜罐": "小熊罐子",
}


def init_db() -> None:
    ensure_directories()
    from app import v2_models  # noqa: F401

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
    finally:
        db.close()


# One-shot schema patches. Each runs at most once per database and is recorded
# in schema_migration_steps; add new steps here instead of extending init_db.
SCHEMA_MIGRATION_STEPS: list[tuple[str, object]] = [
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
]


def run_schema_migration_steps(db) -> None:
    db.execute(
        text(
            "CREATE TABLE IF NOT EXISTS schema_migration_steps ("
            "step VARCHAR(100) PRIMARY KEY, "
            "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    )
    db.commit()
    applied = {row[0] for row in db.execute(text("SELECT step FROM schema_migration_steps"))}
    for step_name, runner_name in SCHEMA_MIGRATION_STEPS:
        if step_name in applied:
            continue
        runner = globals()[runner_name]
        runner(db)
        db.execute(text("INSERT INTO schema_migration_steps (step) VALUES (:step)"), {"step": step_name})
        db.commit()


def ensure_void_audit_columns(db) -> None:
    required_columns = {
        "recognition_records": {
            "voided_by": "INTEGER REFERENCES employees(id)",
            "voided_by_name": "VARCHAR(100)",
            "voided_at": "DATETIME",
            "void_reason": "TEXT",
            "voided_by_role_code": "VARCHAR(30)",
            "voided_by_role_name": "VARCHAR(30)",
            "void_permission_scope_snapshot": "VARCHAR(255)",
            "voided_from_status": "VARCHAR(20)",
        },
        "deduction_records": {
            "voided_by_role_code": "VARCHAR(30)",
            "voided_by_role_name": "VARCHAR(30)",
            "void_permission_scope_snapshot": "VARCHAR(255)",
            "voided_from_status": "VARCHAR(20)",
        },
        "sick_leave_records": {
            "voided_by_name": "VARCHAR(100)",
            "employee_no_snapshot": "VARCHAR(50)",
            "employee_name_snapshot": "VARCHAR(100)",
            "employee_role_snapshot": "VARCHAR(30)",
            "attraction_id_snapshot": "INTEGER",
            "voided_by_role_code": "VARCHAR(30)",
            "voided_by_role_name": "VARCHAR(30)",
            "void_permission_scope_snapshot": "VARCHAR(255)",
            "voided_from_status": "VARCHAR(20)",
        },
    }
    for table_name, columns in required_columns.items():
        existing = {row[1] for row in db.execute(text(f"PRAGMA table_info({table_name})"))}
        for column_name, definition in columns.items():
            if column_name not in existing:
                db.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"))
    db.execute(
        text(
            "UPDATE sick_leave_records SET voided_by_name=(SELECT name FROM employees WHERE employees.id=sick_leave_records.voided_by) "
            "WHERE voided_by IS NOT NULL AND (voided_by_name IS NULL OR voided_by_name='')"
        )
    )
    db.execute(
        text(
            "UPDATE sick_leave_records SET "
            "employee_no_snapshot=(SELECT employee_no FROM employees WHERE employees.id=sick_leave_records.employee_id), "
            "employee_name_snapshot=(SELECT name FROM employees WHERE employees.id=sick_leave_records.employee_id), "
            "attraction_id_snapshot=(SELECT attraction_id FROM employees WHERE employees.id=sick_leave_records.employee_id) "
            "WHERE employee_no_snapshot IS NULL OR employee_name_snapshot IS NULL OR attraction_id_snapshot IS NULL"
        )
    )
    db.commit()


def ensure_credential_columns(db) -> None:
    columns = {row[1] for row in db.execute(text("PRAGMA table_info(user_accounts)"))}
    if "credential_initialized" not in columns:
        db.execute(text("ALTER TABLE user_accounts ADD COLUMN credential_initialized BOOLEAN NOT NULL DEFAULT 0"))
        db.commit()


def ensure_account_status_password_timestamp(db) -> None:
    """Add non-sensitive password-change evidence for the account-status view.

    Historical rows are only backfilled when there is an existing self-service
    password-change audit event.  We deliberately leave all other legacy
    accounts as unknown instead of guessing that their password was changed.
    """
    columns = {row[1] for row in db.execute(text("PRAGMA table_info(user_accounts)"))}
    if "password_changed_at" not in columns:
        db.execute(text("ALTER TABLE user_accounts ADD COLUMN password_changed_at DATETIME"))
    db.execute(
        text(
            "UPDATE user_accounts SET password_changed_at=("
            "SELECT MAX(created_at) FROM audit_logs "
            "WHERE audit_logs.entity_type='user_account' "
            "AND audit_logs.entity_id=CAST(user_accounts.id AS TEXT) "
            "AND audit_logs.action='修改密码'"
            ") WHERE password_changed_at IS NULL AND EXISTS ("
            "SELECT 1 FROM audit_logs WHERE audit_logs.entity_type='user_account' "
            "AND audit_logs.entity_id=CAST(user_accounts.id AS TEXT) "
            "AND audit_logs.action='修改密码'"
            ")"
        )
    )
    db.commit()


def ensure_submission_payload_digest(db) -> None:
    columns = {row[1] for row in db.execute(text("PRAGMA table_info(submission_requests)"))}
    if "payload_digest" not in columns:
        db.execute(text("ALTER TABLE submission_requests ADD COLUMN payload_digest VARCHAR(64)"))
    db.commit()


def ensure_login_account_archive_columns(db) -> None:
    """Add account-removal timestamps without touching employee history.

    Existing disabled accounts receive the employee row's latest update time
    as a conservative lower-bound for the seven-day countdown.  We never
    infer an earlier date, so legacy accounts can only become eligible later,
    not sooner, than their last recorded HR change.
    """
    employee_columns = {row[1] for row in db.execute(text("PRAGMA table_info(employees)"))}
    for column, definition in {
        "account_deleted_at": "DATETIME",
        "account_deleted_by_name": "VARCHAR(100)",
    }.items():
        if column not in employee_columns:
            db.execute(text(f"ALTER TABLE employees ADD COLUMN {column} {definition}"))
    account_columns = {row[1] for row in db.execute(text("PRAGMA table_info(user_accounts)"))}
    if "disabled_at" not in account_columns:
        db.execute(text("ALTER TABLE user_accounts ADD COLUMN disabled_at DATETIME"))
    db.execute(
        text(
            "UPDATE user_accounts SET disabled_at=(SELECT updated_at FROM employees WHERE employees.id=user_accounts.employee_id) "
            "WHERE enabled=0 AND disabled_at IS NULL"
        )
    )
    db.commit()


def ensure_recognition_same_day_duplicate_columns(db) -> None:
    columns = {row[1] for row in db.execute(text("PRAGMA table_info(recognition_records)"))}
    if "same_day_duplicate_group" not in columns:
        db.execute(text("ALTER TABLE recognition_records ADD COLUMN same_day_duplicate_group VARCHAR(160)"))
    if "same_day_duplicate_sequence" not in columns:
        db.execute(text("ALTER TABLE recognition_records ADD COLUMN same_day_duplicate_sequence INTEGER"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_recognition_same_day_duplicate_lookup ON recognition_records (employee_id, recognition_date, recognition_type_id, recognizer_employee_id, status, submitted_at)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_recognition_same_day_duplicate_group ON recognition_records (same_day_duplicate_group, same_day_duplicate_sequence)"))
    db.commit()


def ensure_deduction_statement_upgrade_columns(db) -> None:
    columns = {row[1] for row in db.execute(text("PRAGMA table_info(deduction_records)"))}
    required = {
        "upgrade_request_id": "INTEGER",
        "upgrade_role": "VARCHAR(30)",
        "upgrade_state": "VARCHAR(30)",
    }
    for name, definition in required.items():
        if name not in columns:
            db.execute(text(f"ALTER TABLE deduction_records ADD COLUMN {name} {definition}"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_deduction_statement_upgrade_lookup ON deduction_records (employee_id, deduction_type_id, status, deduction_level_id, occurred_on, upgrade_state, id)"))
    db.execute(text("CREATE TABLE IF NOT EXISTS deduction_upgrade_requests (id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id), deduction_type_id INTEGER NOT NULL REFERENCES deduction_types(id), first_deduction_id INTEGER NOT NULL UNIQUE REFERENCES deduction_records(id), second_deduction_id INTEGER NOT NULL UNIQUE REFERENCES deduction_records(id), reviewer_id INTEGER NOT NULL REFERENCES employees(id), reviewer_name VARCHAR(100) NOT NULL, status VARCHAR(30) NOT NULL DEFAULT 'pending', result_level_id INTEGER REFERENCES deduction_levels(id), result_deduction_id INTEGER UNIQUE REFERENCES deduction_records(id), handling_note TEXT, issued_confirmed BOOLEAN NOT NULL DEFAULT 0, submitted_by INTEGER NOT NULL REFERENCES employees(id), submitted_by_name VARCHAR(100) NOT NULL, resolved_by INTEGER REFERENCES employees(id), resolved_by_name VARCHAR(100), resolved_at DATETIME, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_deduction_upgrade_assignee_status ON deduction_upgrade_requests (reviewer_id, status, created_at)"))
    db.execute(text("CREATE TABLE IF NOT EXISTS deduction_upgrade_transfers (id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES deduction_upgrade_requests(id) ON DELETE CASCADE, from_reviewer_id INTEGER NOT NULL REFERENCES employees(id), from_reviewer_name VARCHAR(100) NOT NULL, to_reviewer_id INTEGER NOT NULL REFERENCES employees(id), to_reviewer_name VARCHAR(100) NOT NULL, reason TEXT NOT NULL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_deduction_upgrade_transfer_request ON deduction_upgrade_transfers (request_id, created_at)"))
    db.commit()


def ensure_recognition_credit_cap_and_poc_columns(db) -> None:
    """Add credit snapshots without rewriting any historic recognition score."""
    columns = {row[1] for row in db.execute(text("PRAGMA table_info(recognition_records)"))}
    required = {
        "employee_role_code_snapshot": "VARCHAR(30)",
        "recognizer_role_code_snapshot": "VARCHAR(30)",
        "operator_role_snapshot": "VARCHAR(30)",
        "operator_role_code_snapshot": "VARCHAR(30)",
        "credited_fraction": "NUMERIC(10,2) NOT NULL DEFAULT 0",
        "monthly_cap_rule_code": "VARCHAR(50)",
        "monthly_cap_status": "VARCHAR(30) NOT NULL DEFAULT 'not_applicable'",
        "monthly_cap_limit": "NUMERIC(10,2)",
        "monthly_cap_confirmed_before": "NUMERIC(10,2)",
        "monthly_cap_reason": "TEXT",
        "monthly_cap_evaluated_at": "DATETIME",
        "poc_period_type": "VARCHAR(20)",
        "poc_period_key": "VARCHAR(20)",
        "poc_reason": "TEXT",
    }
    for name, definition in required.items():
        if name not in columns:
            db.execute(text(f"ALTER TABLE recognition_records ADD COLUMN {name} {definition}"))
    # Historic rows keep their original, already-confirmed result. The new cap
    # starts on 2026-09-01 and is deliberately never applied backwards.
    db.execute(text("UPDATE recognition_records SET credited_fraction=CASE WHEN status='confirmed' THEN fraction ELSE 0 END WHERE credited_fraction IS NULL OR (credited_fraction=0 AND status='confirmed' AND recognition_date < '2026-09-01')"))
    db.execute(text("UPDATE recognition_records SET monthly_cap_status='legacy_not_limited' WHERE recognition_date < '2026-09-01' AND (monthly_cap_status IS NULL OR monthly_cap_status='not_applicable')"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_recognition_monthly_cap_lookup ON recognition_records (employee_id, recognition_month, recognition_type_id, status, reviewed_at, id)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_recognition_poc_ranking_lookup ON recognition_records (recognition_date, status, recognizer_role_code_snapshot, operator_role_code_snapshot)"))
    db.commit()


def ensure_employee_number_history(db) -> None:
    """Keep employee-number changes traceable without duplicating employees."""
    db.execute(
        text(
            "CREATE TABLE IF NOT EXISTS employee_number_history ("
            "id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE, "
            "old_employee_no VARCHAR(50) NOT NULL, new_employee_no VARCHAR(50) NOT NULL, "
            "effective_on VARCHAR(10) NOT NULL, reason TEXT NOT NULL, "
            "changed_by INTEGER NOT NULL REFERENCES employees(id), changed_by_name VARCHAR(100) NOT NULL, "
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    )
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_employee_number_history_employee_date ON employee_number_history (employee_id, effective_on, id)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_employee_number_history_old_number ON employee_number_history (old_employee_no)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_employee_number_history_new_number ON employee_number_history (new_employee_no)"))
    db.commit()


def ensure_deduction_photo_pdf_materials(db) -> None:
    """Add durable material-conversion state without rewriting old deductions."""
    columns = {row[1] for row in db.execute(text("PRAGMA table_info(deduction_records)"))}
    required = {
        "material_status": "VARCHAR(30) NOT NULL DEFAULT 'ready'",
        "material_error": "TEXT",
        "material_source_type": "VARCHAR(20) NOT NULL DEFAULT 'pdf'",
        "material_job_id": "INTEGER",
    }
    for name, definition in required.items():
        if name not in columns:
            db.execute(text(f"ALTER TABLE deduction_records ADD COLUMN {name} {definition}"))
    db.execute(text("UPDATE deduction_records SET material_status='ready' WHERE material_status IS NULL OR material_status=''"))
    db.execute(text("UPDATE deduction_records SET material_source_type='pdf' WHERE material_source_type IS NULL OR material_source_type=''"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_deduction_material_state ON deduction_records (material_status, status, submitted_at)"))
    db.execute(
        text(
            "CREATE TABLE IF NOT EXISTS deduction_material_jobs ("
            "id INTEGER PRIMARY KEY, deduction_id INTEGER NOT NULL REFERENCES deduction_records(id) ON DELETE CASCADE, "
            "output_file_id INTEGER NOT NULL REFERENCES stored_files(id), source_file_ids_json TEXT NOT NULL, "
            "mode VARCHAR(30) NOT NULL DEFAULT 'deduction', reviewer_id INTEGER REFERENCES employees(id), "
            "first_deduction_id INTEGER REFERENCES deduction_records(id), status VARCHAR(30) NOT NULL DEFAULT 'queued', "
            "error_code VARCHAR(50), error_message TEXT, attempts INTEGER NOT NULL DEFAULT 0, "
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, started_at DATETIME, completed_at DATETIME)"
        )
    )
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_deduction_material_job_status_created ON deduction_material_jobs (status, created_at)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_deduction_material_job_deduction ON deduction_material_jobs (deduction_id, id)"))
    db.commit()


def ensure_collaborative_material_columns(db) -> None:
    """Support auditable later material completion without rewriting scores."""
    deduction_columns = {row[1] for row in db.execute(text("PRAGMA table_info(deduction_records)"))}
    deduction_required = {
        "material_uploaded_by": "INTEGER REFERENCES employees(id)",
        "material_uploaded_by_name": "VARCHAR(100)",
        "material_uploaded_at": "DATETIME",
        "material_revision": "INTEGER NOT NULL DEFAULT 1",
        "legacy_upgrade_excluded": "BOOLEAN NOT NULL DEFAULT 0",
        "legacy_upgrade_note": "TEXT",
    }
    for name, definition in deduction_required.items():
        if name not in deduction_columns:
            db.execute(text(f"ALTER TABLE deduction_records ADD COLUMN {name} {definition}"))
    sick_columns = {row[1] for row in db.execute(text("PRAGMA table_info(sick_leave_records)"))}
    sick_required = {
        "is_violation": "BOOLEAN NOT NULL DEFAULT 0",
        "violation_deduction_id": "INTEGER REFERENCES deduction_records(id)",
    }
    for name, definition in sick_required.items():
        if name not in sick_columns:
            db.execute(text(f"ALTER TABLE sick_leave_records ADD COLUMN {name} {definition}"))
    db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_sick_leave_violation_deduction ON sick_leave_records (violation_deduction_id) WHERE violation_deduction_id IS NOT NULL"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_deduction_pending_material_lookup ON deduction_records (status, material_status, employee_id, deduction_type_id, deduction_level_id, occurred_on)"))
    # Before the 2026-08-29 upgrade-workorder release, historic violation-sick-leave
    # declarations had no review path. Keep their score/history, but do not let them
    # become automatic sources for a newly introduced escalation chain.
    db.execute(text("UPDATE deduction_records SET legacy_upgrade_excluded=1, legacy_upgrade_note='升级工单上线前历史声明，保留原扣分，不参与后续自动升级' WHERE deduction_type_name='违规病假' AND occurred_on<'2026-08-29' AND status='active' AND upgrade_request_id IS NULL"))
    db.commit()


def disable_test_accounts(db) -> None:
    """Quarantine historical demo identities unless an isolated run opts in."""
    if os.environ.get("RECOGNITION_ENABLE_TEST_ACCOUNTS", "").strip() == "1":
        return
    from app.v2_models import Employee, UserAccount, UserSession

    rows = (
        db.query(UserAccount)
        .join(Employee, Employee.id == UserAccount.employee_id)
        .filter((Employee.employee_no.like("%TEST%")) | (Employee.name.like("%测试%")))
        .all()
    )
    for account in rows:
        account.enabled = False
        db.query(UserSession).filter(UserSession.account_id == account.id).delete(synchronize_session=False)
    if rows:
        db.commit()


def ensure_attraction_dimensions(db) -> None:
    columns = {row[1] for row in db.execute(text("PRAGMA table_info(attractions)"))}
    if "employee_circle" not in columns:
        db.execute(text("ALTER TABLE attractions ADD COLUMN employee_circle BOOLEAN NOT NULL DEFAULT 0"))
    if "recognition_venue" not in columns:
        db.execute(text("ALTER TABLE attractions ADD COLUMN recognition_venue BOOLEAN NOT NULL DEFAULT 0"))
    db.commit()


def ensure_attraction_catalog(db) -> None:
    from app.v2_models import Attraction, DeductionRecord, Employee, ManagementScope, RecognitionRecord, WorkGroup, GroupTransfer

    rows = {row.name: row for row in db.query(Attraction).all()}
    for name in (*EMPLOYEE_CIRCLES, *RECOGNITION_VENUES):
        if name not in rows:
            row = Attraction(name=name, active=True)
            db.add(row)
            rows[name] = row
    db.flush()

    for row in rows.values():
        row.employee_circle = row.name in EMPLOYEE_CIRCLES
        row.recognition_venue = row.name in RECOGNITION_VENUES
        if row.employee_circle or row.recognition_venue:
            row.active = True

    circles = {name: rows[name] for name in EMPLOYEE_CIRCLES}
    for legacy_name, circle_name in LEGACY_CIRCLE_BY_VENUE.items():
        legacy = rows.get(legacy_name)
        circle = circles[circle_name]
        if not legacy or legacy.id == circle.id:
            continue
        db.query(Employee).filter(Employee.attraction_id == legacy.id).update(
            {Employee.attraction_id: circle.id}, synchronize_session=False
        )
        for scope in db.query(ManagementScope).filter(ManagementScope.attraction_id == legacy.id).all():
            params = {
                "employee_id": scope.employee_id,
                "circle_id": circle.id,
                "starts_on": scope.starts_on,
                "ends_on": scope.ends_on,
                "created_at": scope.created_at,
            }
            db.execute(
                text(
                    "INSERT OR IGNORE INTO management_scopes "
                    "(employee_id, attraction_id, starts_on, ends_on, created_at) "
                    "VALUES (:employee_id, :circle_id, :starts_on, :ends_on, :created_at)"
                ),
                params,
            )
            db.execute(
                text(
                    "UPDATE management_scopes SET ends_on = CASE "
                    "WHEN ends_on IS NULL OR :ends_on IS NULL THEN NULL "
                    "WHEN ends_on < :ends_on THEN :ends_on ELSE ends_on END "
                    "WHERE employee_id=:employee_id AND attraction_id=:circle_id AND starts_on=:starts_on"
                ),
                params,
            )
            db.delete(scope)
        db.query(WorkGroup).filter(WorkGroup.attraction_id == legacy.id).update(
            {WorkGroup.attraction_id: circle.id}, synchronize_session=False
        )
        db.query(GroupTransfer).filter(GroupTransfer.attraction_id == legacy.id).update(
            {GroupTransfer.attraction_id: circle.id}, synchronize_session=False
        )
        db.query(DeductionRecord).filter(DeductionRecord.attraction_id_snapshot == legacy.id).update(
            {DeductionRecord.attraction_id_snapshot: circle.id}, synchronize_session=False
        )
        db.query(RecognitionRecord).filter(RecognitionRecord.home_attraction_id == legacy.id).update(
            {
                RecognitionRecord.home_attraction_id: circle.id,
                RecognitionRecord.home_attraction_name: circle.name,
            },
            synchronize_session=False,
        )
    db.commit()


def synchronize_gsm_management_scope(db, employee, role_code: str | None, on_date: str | None = None) -> bool:
    """Reconcile one employee's live GSM/TA GSM tree scope.

    A management scope is only valid while the employee is active, currently a
    GSM/TA GSM, and belongs to an active employee circle.  Reconciliation must
    first end obsolete scopes, rather than returning early for an ineligible
    employee: otherwise a former GSM can remain in the organization tree after
    their role or circle changes.
    """
    from app.v2_models import Attraction, ManagementScope

    value = on_date or date.today().isoformat()
    if not employee:
        return False
    circle = db.get(Attraction, employee.attraction_id) if employee.attraction_id else None
    eligible = bool(
        employee
        and employee.is_active
        and role_code in {"GSM", "TA_GSM"}
        and circle
        and circle.active
        and circle.employee_circle
    )

    active_scopes = (
        db.query(ManagementScope)
        .filter(
            ManagementScope.employee_id == employee.id,
            ManagementScope.starts_on <= value,
            (ManagementScope.ends_on.is_(None) | (ManagementScope.ends_on >= value)),
        )
        .all()
    )
    changed = False
    target_scope = None
    if eligible:
        matching_scopes = [scope for scope in active_scopes if scope.attraction_id == circle.id]
        if matching_scopes:
            # Keep one current target scope.  The latest row is the one that
            # best represents an intentional same-day correction; all other
            # overlapping rows are stale duplicates and must be closed.
            target_scope = max(matching_scopes, key=lambda scope: (scope.starts_on, scope.id))
    for scope in active_scopes:
        if target_scope is scope:
            if scope.ends_on is not None:
                scope.ends_on = None
                changed = True
            continue

        # This includes all invalid cases: stale circle, removed circle,
        # inactive employee, changed role, and duplicate target rows.  A scope
        # created today has no historical interval to preserve, so delete it;
        # otherwise close it before today's effective organization state.
        if scope.starts_on >= value:
            db.delete(scope)
        else:
            scope.ends_on = (date.fromisoformat(value) - timedelta(days=1)).isoformat()
        changed = True
    if eligible and target_scope is None:
        db.add(ManagementScope(employee_id=employee.id, attraction_id=circle.id, starts_on=value))
        changed = True
    return changed


def ensure_gsm_management_scopes(db) -> None:
    """Reconcile every employee's GSM scope on startup, idempotently.

    Querying all employees (rather than only current GSM/TA GSM employees) is
    intentional: it removes scopes that became orphaned after a role, circle or
    employment-status change before this synchronization was available.
    """
    from app.v2_models import Employee
    from app.v2_services import roles_at

    today = date.today().isoformat()
    employees = db.query(Employee).all()
    current_roles = roles_at(db, [employee.id for employee in employees], today)
    changed = False
    for employee in employees:
        role = current_roles.get(employee.id)
        changed = synchronize_gsm_management_scope(db, employee, role.code if role else None, today) or changed
    if changed:
        db.commit()


def ensure_performance_indexes(db) -> None:
    statements = (
        "CREATE INDEX IF NOT EXISTS ix_employee_targets_status_attraction ON employees (is_active, attraction_id, name, employee_no)",
        "CREATE INDEX IF NOT EXISTS ix_role_assignments_current_lookup ON employee_role_assignments (employee_id, status, starts_on, ends_on, id)",
        "CREATE INDEX IF NOT EXISTS ix_group_memberships_current_lookup ON group_memberships (employee_id, status, starts_on, ends_on, id)",
        "CREATE INDEX IF NOT EXISTS ix_group_leaders_current_lookup ON group_leader_assignments (group_id, status, starts_on, ends_on, id)",
        "CREATE INDEX IF NOT EXISTS ix_recognition_month_employee_status ON recognition_records (recognition_month, employee_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_recognition_pr_ranking ON recognition_records (status, recognition_date, recognition_type_id, employee_id)",
        "CREATE INDEX IF NOT EXISTS ix_recognition_leader_ranking ON recognition_records (home_attraction_id, source, status, recognition_date, operator_employee_id)",
        "CREATE INDEX IF NOT EXISTS ix_recognition_leader_participant_ranking ON recognition_records (home_attraction_id, status, recognition_date, recognition_type_id)",
        "CREATE INDEX IF NOT EXISTS ix_deduction_month_employee_status ON deduction_records (deduction_month, employee_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_deduction_pr_ranking ON deduction_records (status, occurred_on, deduction_type_id, employee_id)",
        "CREATE INDEX IF NOT EXISTS ix_sick_leave_month_employee_status ON sick_leave_records (attendance_month, employee_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_sick_leave_pr_ranking ON sick_leave_records (status, leave_start_date, leave_end_date, employee_id)",
        "CREATE INDEX IF NOT EXISTS ix_attendance_month_employee ON attendance_monthly_scores (attendance_month, employee_id)",
        "CREATE INDEX IF NOT EXISTS ix_user_accounts_enabled_employee ON user_accounts (enabled, employee_id)",
    )
    for statement in statements:
        db.execute(text(statement))
    db.commit()


def ensure_second_audit_query_indexes(db) -> None:
    """Add measured query indexes and remove exact duplicate history indexes."""
    statements = (
        "CREATE INDEX IF NOT EXISTS ix_audit_operator_action_recent ON audit_logs (operator_id, action, created_at DESC, id DESC)",
        "CREATE INDEX IF NOT EXISTS ix_recognition_month_close_scope ON recognition_records (home_attraction_id, recognition_month, status)",
        "CREATE INDEX IF NOT EXISTS ix_deduction_month_close_scope ON deduction_records (attraction_id_snapshot, deduction_month, status)",
        "DROP INDEX IF EXISTS ix_employee_number_history_old_employee_no",
        "DROP INDEX IF EXISTS ix_employee_number_history_new_employee_no",
    )
    for statement in statements:
        db.execute(text(statement))
    db.commit()


def ensure_governance_case_indexes(db) -> None:
    """Create the indexes after metadata creates the new governance table."""
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_governance_case_status_scope ON governance_cases (case_type, status, attraction_id, submitted_at)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_governance_case_subject ON governance_cases (record_type, record_id, status)"))
    db.commit()


def legacy_attendance_cleanup_preview(db) -> dict:
    """Describe leftover ATTENDANCE catalog rows without deleting them."""
    from app.v2_models import AuditLog, DeductionRecord, DeductionType, StoredFile

    legacy_attendance = db.query(DeductionType).filter(DeductionType.code == "ATTENDANCE").first()
    if not legacy_attendance:
        return {"found": False, "type_id": None, "record_ids": [], "file_keys": [], "audit_count": 0}
    legacy_records = db.query(DeductionRecord).filter(DeductionRecord.deduction_type_id == legacy_attendance.id).all()
    legacy_ids = [str(row.id) for row in legacy_records]
    legacy_files = [db.get(StoredFile, row.document_file_id) for row in legacy_records]
    audit_count = 0
    if legacy_ids:
        audit_count = (
            db.query(AuditLog)
            .filter(AuditLog.entity_type == "deduction", AuditLog.entity_id.in_(legacy_ids))
            .count()
        )
    return {
        "found": True,
        "type_id": legacy_attendance.id,
        "record_ids": [row.id for row in legacy_records],
        "file_keys": [file_row.storage_key for file_row in legacy_files if file_row],
        "audit_count": audit_count,
    }


def purge_legacy_attendance(db, *, apply: bool = False) -> dict:
    """One-off ATTENDANCE cleanup. Default is dry-run; startup must not call this."""
    from app.v2_models import AuditLog, DeductionRecord, DeductionType, StoredFile

    preview = legacy_attendance_cleanup_preview(db)
    preview["mode"] = "apply" if apply else "dry-run"
    preview["applied"] = False
    if not apply or not preview["found"]:
        return preview
    legacy_attendance = db.query(DeductionType).filter(DeductionType.code == "ATTENDANCE").first()
    legacy_records = db.query(DeductionRecord).filter(DeductionRecord.deduction_type_id == legacy_attendance.id).all()
    legacy_ids = [str(row.id) for row in legacy_records]
    legacy_files = [db.get(StoredFile, row.document_file_id) for row in legacy_records]
    if legacy_ids:
        db.query(AuditLog).filter(AuditLog.entity_type == "deduction", AuditLog.entity_id.in_(legacy_ids)).delete(synchronize_session=False)
    for row in legacy_records:
        db.delete(row)
    db.flush()
    for file_row in legacy_files:
        if file_row:
            db.delete(file_row)
    db.delete(legacy_attendance)
    db.commit()
    for storage_key in preview["file_keys"]:
        path = (FILE_DIR / storage_key).resolve()
        if FILE_DIR.resolve() in path.parents:
            path.unlink(missing_ok=True)
    preview["applied"] = True
    return preview


def seed_reference_data(db) -> None:
    from app.v2_models import (
        AttendanceRule,
        DeductionLevel,
        DeductionType,
        Permission,
        RecognitionScoreRule,
        RecognitionType,
        Role,
        RolePermission,
    )

    ensure_attraction_catalog(db)
    for code, name, rank, can_lead, attendance_eligible in ROLE_DEFINITIONS:
        role = db.query(Role).filter(Role.code == code).first()
        if not role:
            role = Role(code=code, name=name, rank=rank)
            db.add(role)
        role.name = name
        role.rank = rank
        role.can_lead_group = can_lead
        role.attendance_eligible = attendance_eligible
        role.active = True
    db.flush()

    for code, name in PERMISSION_DEFINITIONS.items():
        permission = db.query(Permission).filter(Permission.code == code).first()
        if not permission:
            permission = Permission(code=code, name=name)
            db.add(permission)
        else:
            permission.name = name
    db.flush()
    for role_code, permission_codes in ROLE_PERMISSION_CODES.items():
        role = db.query(Role).filter(Role.code == role_code).one()
        configured = set(permission_codes)
        existing = db.query(RolePermission).filter(RolePermission.role_id == role.id).all()
        for assignment in existing:
            permission = db.get(Permission, assignment.permission_id)
            if permission and permission.code not in configured:
                db.delete(assignment)
        for permission_code in permission_codes:
            permission = db.query(Permission).filter(Permission.code == permission_code).one()
            if not db.query(RolePermission).filter_by(role_id=role.id, permission_id=permission.id).first():
                db.add(RolePermission(role_id=role.id, permission_id=permission.id))

    for code, name in (
        ("SAFETY", "安全"),
        ("COURTESY", "礼仪"),
        ("INCLUSION", "包容"),
        ("EFFICIENCY", "效率"),
        ("SHOW", "演出"),
        ("MSP", "MSP"),
        ("COMMENDATION_LETTER", "表扬信"),
        ("POC", "POC特别贡献"),
        ("OTHER", "其他"),
    ):
        if not db.query(RecognitionType).filter(RecognitionType.code == code).first():
            db.add(RecognitionType(code=code, name=name, active=True))

    for role_code, score in DEFAULT_RECOGNIZER_SCORES.items():
        role = db.query(Role).filter(Role.code == role_code).one()
        if not db.query(RecognitionScoreRule).filter_by(role_id=role.id).first():
            db.add(
                RecognitionScoreRule(
                    role_id=role.id,
                    score=score,
                    effective_date=INITIAL_SCORE_RULE_EFFECTIVE_DATE,
                    active=True,
                )
            )

    if not db.query(AttendanceRule).first():
        db.add(
            AttendanceRule(
                base_score=Decimal("10.00"),
                perfect_bonus=Decimal("2.00"),
                sick_day_deduction=Decimal("0.45"),
                zero_threshold=Decimal("0.10"),
                effective_date=INITIAL_SCORE_RULE_EFFECTIVE_DATE,
                active=True,
            )
        )

    desired_deduction_types = (
        ("SAFETY", "安全"),
        ("AUDIT", "审计"),
        ("COURTESY", "礼仪"),
        ("INCLUSION", "包容"),
        ("SHOW", "演出"),
        ("EFFICIENCY", "效率"),
        ("MSP", "MSP"),
        ("COMPLAINT", "客诉"),
        ("SICK_LEAVE_VIOLATION", "违规病假"),
        ("OTHER", "其他"),
        ("ATT_EARLY_CLOCK", "考勤-早打卡"),
        ("ATT_LATE_CLOCK", "考勤-晚打卡"),
        ("ATT_LATE_WITHIN_30", "考勤-迟到30分钟内"),
        ("ATT_LATE_OVER_30", "考勤-迟到30分钟以上"),
        ("ATT_EARLY_LEAVE_WITHIN_30", "考勤-早退30分钟内"),
        ("ATT_EARLY_LEAVE_OVER_30", "考勤-早退30分钟以上"),
        ("ATT_MISSING_CLOCK", "考勤-未打卡"),
        ("ATT_REMOTE_CLOCK", "考勤-异地打卡"),
    )
    desired_deduction_codes = {code for code, _ in desired_deduction_types}
    db.query(DeductionType).filter(DeductionType.code.notin_(desired_deduction_codes)).update(
        {DeductionType.active: False}, synchronize_session=False
    )
    for code, name in desired_deduction_types:
        row = db.query(DeductionType).filter(DeductionType.code == code).first()
        if not row:
            db.add(DeductionType(code=code, name=name, active=True))
        else:
            row.name = name
            row.active = True
    for code, name, points in (
        ("STATEMENT", "声明", Decimal("1.00")),
        ("MEMO", "备忘录", Decimal("3.00")),
        ("WARNING_1", "一级警告", Decimal("5.00")),
        ("WARNING_2", "二级警告", Decimal("10.00")),
    ):
        if not db.query(DeductionLevel).filter(DeductionLevel.code == code).first():
            db.add(DeductionLevel(code=code, name=name, points=points, active=True))
    db.commit()


def seed_test_accounts(db) -> None:
    # Predictable test identities are allowed only in explicitly isolated runs.
    if os.environ.get("RECOGNITION_ENABLE_TEST_ACCOUNTS", "").strip() != "1":
        return
    test_password = os.environ.get("RECOGNITION_TEST_DEFAULT_PASSWORD", "").strip()
    test_admin_password = os.environ.get("RECOGNITION_TEST_ADMIN_PASSWORD", "").strip()
    if not test_password or not test_admin_password:
        raise RuntimeError("启用测试账号时必须显式配置测试账号和测试管理员密码")
    from app.v2_crypto import hash_password
    from app.v2_models import (
        Attraction,
        Employee,
        EmployeeRoleAssignment,
        GroupLeaderAssignment,
        GroupMembership,
        ManagementScope,
        Role,
        UserAccount,
        WorkGroup,
    )

    if db.query(Employee).count():
        return
    today = date.today()
    attraction_a = db.query(Attraction).filter(Attraction.name == "热力追踪").one()
    attraction_b = db.query(Attraction).filter(Attraction.name == "矮人迷宫").one()
    roles = {role.code: role for role in db.query(Role).all()}
    employees = {}

    def add_employee(employee_no, name, role_code, attraction, password=None, *, temp_end=None, return_role=None):
        employee = Employee(
            employee_no=employee_no,
            name=name,
            attraction_id=attraction.id if attraction else None,
            is_active=True,
            hired_on=today.isoformat(),
        )
        db.add(employee)
        db.flush()
        db.add(
            EmployeeRoleAssignment(
                employee_id=employee.id,
                role_id=roles[role_code].id,
                starts_on=today.isoformat(),
                ends_on=temp_end,
                assignment_type="temporary" if temp_end else "permanent",
                return_role_id=roles[return_role].id if return_role else None,
                status="active",
                reason="V2测试账号初始化",
            )
        )
        db.add(
            UserAccount(
                employee_id=employee.id,
                login_account=employee_no,
                password_hash=hash_password(password or test_password),
                enabled=True,
                must_change_password=False,
            )
        )
        employees[employee_no] = employee
        return employee

    add_employee("CMTEST01", "测试CM甲", "CM", attraction_a)
    add_employee("CMTEST02", "测试CM乙", "CM", attraction_a)
    add_employee("TRTEST01", "测试TR甲", "TR", attraction_a)
    add_employee(
        "TATEST01",
        "测试TA主管",
        "TA_SUPERVISOR",
        attraction_a,
        temp_end=(today + timedelta(days=90)).isoformat(),
        return_role="CM",
    )
    add_employee("SUPTEST01", "测试主管", "SUPERVISOR", attraction_a)
    add_employee("TAGSMTEST01", "测试TA GSM", "TA_GSM", attraction_a)
    add_employee("GSMTEST01", "测试GSM", "GSM", attraction_a)
    add_employee("AMTEST01", "测试AM", "AM", attraction_a)
    add_employee("OMTEST01", "测试OM", "OM", None)
    add_employee("HR01", "最高管理员", "SYSTEM_ADMIN", None, test_admin_password)
    db.flush()

    group_ta = WorkGroup(name="V2-A-TA组", attraction_id=attraction_a.id, status="active")
    group_supervisor = WorkGroup(name="V2-A-主管组", attraction_id=attraction_a.id, status="active")
    db.add_all([group_ta, group_supervisor])
    db.flush()
    db.add_all(
        [
            GroupLeaderAssignment(group_id=group_ta.id, leader_employee_id=employees["TATEST01"].id, starts_on=today.isoformat(), status="active"),
            GroupLeaderAssignment(group_id=group_supervisor.id, leader_employee_id=employees["SUPTEST01"].id, starts_on=today.isoformat(), status="active"),
            GroupMembership(group_id=group_ta.id, employee_id=employees["CMTEST01"].id, starts_on=today.isoformat(), status="active"),
            GroupMembership(group_id=group_ta.id, employee_id=employees["TRTEST01"].id, starts_on=today.isoformat(), status="active"),
            GroupMembership(group_id=group_supervisor.id, employee_id=employees["CMTEST02"].id, starts_on=today.isoformat(), status="active"),
        ]
    )
    for employee_no in ("TAGSMTEST01", "GSMTEST01", "AMTEST01", "OMTEST01"):
        for attraction in (attraction_a, attraction_b):
            db.add(
                ManagementScope(
                    employee_id=employees[employee_no].id,
                    attraction_id=attraction.id,
                    starts_on=today.isoformat(),
                )
            )
    db.commit()


CIRCLE_HR_ACCOUNTS = (
    ("HR-HEAT", "热力追踪专属HR", "热力追踪"),
    ("HR-DWARF", "矮人迷宫专属HR", "矮人迷宫"),
    ("HR-BEAR", "小熊罐子专属HR", "小熊罐子"),
)


def ensure_highest_admin_account(db) -> None:
    """Promote HR01 and quarantine a legacy bootstrap credential once."""
    from app.v2_crypto import hash_password, new_temporary_password
    from app.v2_models import Employee, EmployeeRoleAssignment, Role, UserAccount, UserSession

    employee = db.query(Employee).filter(Employee.employee_no == "HR01").first()
    if not employee:
        bootstrap = os.environ.get("RECOGNITION_BOOTSTRAP_ADMIN_PASSWORD", "").strip()
        if not bootstrap or os.environ.get("RECOGNITION_ENABLE_TEST_ACCOUNTS", "").strip() == "1":
            return
        role = db.query(Role).filter(Role.code == "SYSTEM_ADMIN").one()
        today = date.today().isoformat()
        employee = Employee(employee_no="HR01", name="最高管理员", is_active=True, hired_on=today)
        db.add(employee)
        db.flush()
        db.add(
            EmployeeRoleAssignment(
                employee_id=employee.id,
                role_id=role.id,
                starts_on=today,
                assignment_type="permanent",
                status="active",
                reason="安全引导创建最高管理员",
            )
        )
        db.add(
            UserAccount(
                employee_id=employee.id,
                login_account="HR01",
                password_hash=hash_password(bootstrap),
                enabled=True,
                must_change_password=True,
                credential_initialized=True,
            )
        )
        db.commit()
        return
    role = db.query(Role).filter(Role.code == "SYSTEM_ADMIN").one()
    today = date.today().isoformat()
    assignment = (
        db.query(EmployeeRoleAssignment)
        .filter(
            EmployeeRoleAssignment.employee_id == employee.id,
            EmployeeRoleAssignment.status != "cancelled",
            EmployeeRoleAssignment.starts_on <= today,
        )
        .order_by(EmployeeRoleAssignment.starts_on.desc(), EmployeeRoleAssignment.id.desc())
        .first()
    )
    if assignment:
        assignment.role_id = role.id
        assignment.ends_on = None
        assignment.return_role_id = None
        assignment.assignment_type = "permanent"
        assignment.status = "active"
        assignment.reason = "原管理员升级为最高管理员"
    else:
        db.add(
            EmployeeRoleAssignment(
                employee_id=employee.id,
                role_id=role.id,
                starts_on=today,
                assignment_type="permanent",
                status="active",
                reason="原管理员升级为最高管理员",
            )
        )
    employee.name = "最高管理员"
    employee.is_active = True
    account = db.query(UserAccount).filter(UserAccount.employee_id == employee.id).first()
    if account:
        account.enabled = True
        if os.environ.get("RECOGNITION_ENABLE_TEST_ACCOUNTS", "").strip() != "1" and not account.credential_initialized:
            bootstrap = os.environ.get("RECOGNITION_BOOTSTRAP_ADMIN_PASSWORD", "").strip()
            account.password_hash = hash_password(bootstrap or new_temporary_password())
            account.must_change_password = True
            account.credential_initialized = True
            account.password_changed_at = None
            account.failed_attempts = 0
            account.locked_until = None
            db.query(UserSession).filter(UserSession.account_id == account.id).delete(synchronize_session=False)
    db.commit()


def ensure_circle_hr_accounts(db) -> None:
    """Create the three scoped HR accounts once, without resetting changed passwords."""
    from app.v2_crypto import hash_password, new_temporary_password
    from app.v2_models import Attraction, Employee, EmployeeRoleAssignment, Role, UserAccount

    role = db.query(Role).filter(Role.code == "HR_CIRCLE").one()
    today = date.today().isoformat()
    test_mode = os.environ.get("RECOGNITION_ENABLE_TEST_ACCOUNTS", "").strip() == "1"
    for login_account, name, circle_name in CIRCLE_HR_ACCOUNTS:
        circle = db.query(Attraction).filter(Attraction.name == circle_name, Attraction.employee_circle.is_(True)).one()
        employee = db.query(Employee).filter(Employee.employee_no == login_account).first()
        if not employee:
            employee = Employee(
                employee_no=login_account,
                name=name,
                attraction_id=circle.id,
                is_active=True,
                hired_on=today,
            )
            db.add(employee)
            db.flush()
        else:
            employee.name = name
            employee.attraction_id = circle.id
            employee.is_active = True

        assignment = (
            db.query(EmployeeRoleAssignment)
            .filter(
                EmployeeRoleAssignment.employee_id == employee.id,
                EmployeeRoleAssignment.status == "active",
                EmployeeRoleAssignment.starts_on <= today,
            )
            .order_by(EmployeeRoleAssignment.starts_on.desc(), EmployeeRoleAssignment.id.desc())
            .first()
        )
        if not assignment or assignment.role_id != role.id:
            if assignment and assignment.ends_on is None:
                assignment.ends_on = today
            db.add(
                EmployeeRoleAssignment(
                    employee_id=employee.id,
                    role_id=role.id,
                    starts_on=today,
                    assignment_type="permanent",
                    status="active",
                    reason="初始化景点圈HR账号",
                )
            )

        account = db.query(UserAccount).filter(UserAccount.employee_id == employee.id).first()
        if not account:
            db.add(
                UserAccount(
                    employee_id=employee.id,
                    login_account=login_account,
                    password_hash=hash_password(new_temporary_password()),
                    enabled=test_mode,
                    must_change_password=True,
                    credential_initialized=True,
                )
            )
        else:
            account.login_account = login_account
            if test_mode:
                account.enabled = True
            elif not account.credential_initialized:
                account.password_hash = hash_password(new_temporary_password())
                account.must_change_password = True
                account.credential_initialized = True
                account.password_changed_at = None
                account.enabled = False
    db.commit()


def create_score_view(db) -> None:
    db.execute(text("DROP VIEW IF EXISTS v_employee_month_scores"))
    db.execute(
        text(
            """
            CREATE VIEW v_employee_month_scores AS
            WITH months AS (
                SELECT employee_id, recognition_month AS score_month FROM recognition_records
                UNION SELECT employee_id, attendance_month FROM attendance_monthly_scores
                UNION SELECT employee_id, deduction_month FROM deduction_records
            ), recognition_totals AS (
                SELECT employee_id, recognition_month AS score_month,
                       ROUND(SUM(CASE WHEN status = 'confirmed' THEN CASE WHEN recognition_date < '2026-09-01' THEN fraction ELSE credited_fraction END ELSE 0 END), 2) AS recognition_score
                FROM recognition_records GROUP BY employee_id, recognition_month
            ), attendance_totals AS (
                SELECT employee_id, attendance_month AS score_month,
                       ROUND(CASE WHEN eligible = 1 THEN final_score ELSE 0 END, 2) AS attendance_score
                FROM attendance_monthly_scores
            ), deduction_totals AS (
                SELECT employee_id, deduction_month AS score_month,
                       ROUND(SUM(CASE WHEN status = 'active' THEN points ELSE 0 END), 2) AS deduction_score
                FROM deduction_records GROUP BY employee_id, deduction_month
            )
            SELECT e.id AS employee_id, e.employee_no, e.name AS employee_name, m.score_month,
                   COALESCE(r.recognition_score, 0) AS recognition_score,
                   COALESCE(a.attendance_score, 0) AS attendance_score,
                   COALESCE(d.deduction_score, 0) AS deduction_score,
                   ROUND(COALESCE(r.recognition_score, 0) + COALESCE(a.attendance_score, 0) - COALESCE(d.deduction_score, 0), 2) AS total_score
            FROM months m
            JOIN employees e ON e.id = m.employee_id
            LEFT JOIN recognition_totals r ON r.employee_id = m.employee_id AND r.score_month = m.score_month
            LEFT JOIN attendance_totals a ON a.employee_id = m.employee_id AND a.score_month = m.score_month
            LEFT JOIN deduction_totals d ON d.employee_id = m.employee_id AND d.score_month = m.score_month
            """
        )
    )
    db.commit()
