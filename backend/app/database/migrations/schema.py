"""Historical schema, material and audit patches; columns and SQL stay compatible."""
from __future__ import annotations

from sqlalchemy import text


def ensure_recognition_shared_evidence(db) -> None:
    """Preserve legacy attachment IDs/data while allowing one file many links."""
    unique_file = False
    for index in db.execute(text("PRAGMA index_list(recognition_attachments)")):
        if index[2]:
            name = str(index[1]).replace('"', '""')
            columns = [row[2] for row in db.execute(text(f'PRAGMA index_info("{name}")'))]
            unique_file = unique_file or columns == ["file_id"]
    if not unique_file:
        return
    db.execute(text("""CREATE TABLE recognition_attachments_shared (
        id INTEGER PRIMARY KEY, recognition_id INTEGER NOT NULL
        REFERENCES recognition_records(id) ON DELETE CASCADE,
        file_id INTEGER NOT NULL REFERENCES stored_files(id),
        attachment_type VARCHAR(30) NOT NULL, sort_order INTEGER NOT NULL,
        created_at DATETIME NOT NULL,
        CONSTRAINT uq_recognition_attachment_type UNIQUE(recognition_id, attachment_type)
    )"""))
    db.execute(text("""INSERT INTO recognition_attachments_shared
        (id, recognition_id, file_id, attachment_type, sort_order, created_at)
        SELECT id, recognition_id, file_id, attachment_type, sort_order, created_at
        FROM recognition_attachments"""))
    db.execute(text("DROP TABLE recognition_attachments"))
    db.execute(text("ALTER TABLE recognition_attachments_shared RENAME TO recognition_attachments"))
    db.execute(text("CREATE INDEX ix_recognition_attachments_recognition_id ON recognition_attachments(recognition_id)"))
    db.execute(text("CREATE INDEX ix_recognition_attachments_file_id ON recognition_attachments(file_id)"))


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


def ensure_material_job_claim_generation(db) -> None:
    """Add claim generation and deferred source-cleanup columns to existing jobs.

    Old rows keep generation 0 and idle cleanup. New claims increment generation
    and write a token; rollback does not drop the new columns.
    """
    columns = {row[1] for row in db.execute(text("PRAGMA table_info(deduction_material_jobs)"))}
    required = {
        "claim_generation": "INTEGER NOT NULL DEFAULT 0",
        "claim_token": "VARCHAR(64)",
        "source_cleanup_status": "VARCHAR(20) NOT NULL DEFAULT 'idle'",
        "source_cleanup_attempts": "INTEGER NOT NULL DEFAULT 0",
    }
    for name, definition in required.items():
        if name not in columns:
            db.execute(text(f"ALTER TABLE deduction_material_jobs ADD COLUMN {name} {definition}"))
    db.execute(text("UPDATE deduction_material_jobs SET claim_generation=0 WHERE claim_generation IS NULL"))
    db.execute(text("UPDATE deduction_material_jobs SET source_cleanup_status='idle' WHERE source_cleanup_status IS NULL OR source_cleanup_status=''"))
    db.execute(text("UPDATE deduction_material_jobs SET source_cleanup_attempts=0 WHERE source_cleanup_attempts IS NULL"))
    db.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_deduction_material_job_cleanup "
            "ON deduction_material_jobs (source_cleanup_status, status, id)"
        )
    )
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


def ensure_attraction_dimensions(db) -> None:
    columns = {row[1] for row in db.execute(text("PRAGMA table_info(attractions)"))}
    if "employee_circle" not in columns:
        db.execute(text("ALTER TABLE attractions ADD COLUMN employee_circle BOOLEAN NOT NULL DEFAULT 0"))
    if "recognition_venue" not in columns:
        db.execute(text("ALTER TABLE attractions ADD COLUMN recognition_venue BOOLEAN NOT NULL DEFAULT 0"))
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


def backfill_audit_scope_and_remove_appeals(db) -> None:
    """Give old audit rows their employee circle; drop the retired online appeals.

    Online appeals were replaced by an offline channel.  The governance_cases
    table stays for month-close correction records; only appeal rows go.  The
    audit trail of past appeals is kept.
    """
    employee_circle = "(SELECT attraction_id FROM employees e WHERE CAST(e.id AS TEXT) = audit_logs.entity_id)"
    via_employee = lambda table: (  # noqa: E731
        f"(SELECT e.attraction_id FROM {table} t JOIN employees e ON e.id = t.employee_id "
        "WHERE CAST(t.id AS TEXT) = audit_logs.entity_id)"
    )
    sources = {
        "recognition": "(SELECT home_attraction_id FROM recognition_records t WHERE CAST(t.id AS TEXT) = audit_logs.entity_id)",
        "deduction": "(SELECT attraction_id_snapshot FROM deduction_records t WHERE CAST(t.id AS TEXT) = audit_logs.entity_id)",
        "sick_leave": "(SELECT attraction_id_snapshot FROM sick_leave_records t WHERE CAST(t.id AS TEXT) = audit_logs.entity_id)",
        "employee": employee_circle,
        "employee_group": employee_circle,
        "employee_loa": employee_circle,
        "employee_login_archive": employee_circle,
        "employee_number_change": employee_circle,
        "user_account": via_employee("user_accounts"),
        "employee_loa_period": via_employee("employee_loa_periods"),
        "deduction_upgrade": via_employee("deduction_upgrade_requests"),
        "deduction_follow_up": via_employee("deduction_follow_ups"),
        "work_group": "(SELECT attraction_id FROM work_groups t WHERE CAST(t.id AS TEXT) = audit_logs.entity_id)",
        "month_close": "(SELECT attraction_id FROM month_closures t WHERE CAST(t.id AS TEXT) = audit_logs.entity_id)",
        "circle_transfer": "(SELECT source_attraction_id FROM circle_transfer_requests t WHERE CAST(t.id AS TEXT) = audit_logs.entity_id)",
    }
    for entity_type, source in sources.items():
        db.execute(
            text(f"UPDATE audit_logs SET attraction_id = {source} WHERE entity_type = :entity_type AND attraction_id IS NULL"),
            {"entity_type": entity_type},
        )
    db.execute(text("DELETE FROM governance_cases WHERE case_type = 'appeal'"))
    db.commit()
