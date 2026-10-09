"""Compatibility imports for existing callers.

Connection setup, migrations, startup seeds, organization synchronization and
explicit maintenance have dedicated owners. Keep this facade for public imports;
models use database.connection directly so their Base never depends on bootstrap.
"""
from __future__ import annotations

from app.database.connection import (
    BASE_DIR,
    DATA_DIR,
    FILE_DIR,
    EXPORT_DIR,
    BACKUP_DIR,
    DB_PATH,
    Base,
    engine,
    SessionLocal,
    sqlite_pragmas,
    ensure_directories,
    get_db,
)

from app.database.bootstrap import (
    _init_db_lock,
    init_db,
    _init_db_unlocked,
)

from app.database.reference_data import (
    ROLE_DEFINITIONS,
    PERMISSION_DEFINITIONS,
    ROLE_PERMISSION_CODES,
    INITIAL_SCORE_RULE_EFFECTIVE_DATE,
    DEFAULT_RECOGNIZER_SCORES,
    EMPLOYEE_CIRCLES,
    RECOGNITION_VENUES,
    LEGACY_CIRCLE_BY_VENUE,
    ensure_attraction_catalog,
    seed_reference_data,
)

from app.database.accounts import (
    CIRCLE_HR_ACCOUNTS,
    disable_test_accounts,
    seed_test_accounts,
    ensure_highest_admin_account,
    ensure_circle_hr_accounts,
)

from app.database.migrations import (
    SCHEMA_MIGRATION_STEPS,
    SICK_LEAVE_EXTRA_INDEX_STATEMENTS,
    ACTING_DUTY_BASE_CODES,
    run_schema_migration_steps,
    ensure_recognition_shared_evidence,
    ensure_void_audit_columns,
    ensure_credential_columns,
    ensure_account_status_password_timestamp,
    ensure_submission_payload_digest,
    ensure_login_account_archive_columns,
    ensure_recognition_same_day_duplicate_columns,
    ensure_deduction_statement_upgrade_columns,
    ensure_recognition_credit_cap_and_poc_columns,
    ensure_employee_number_history,
    ensure_deduction_photo_pdf_materials,
    ensure_material_job_claim_generation,
    ensure_collaborative_material_columns,
    ensure_attraction_dimensions,
    ensure_performance_indexes,
    ensure_second_audit_query_indexes,
    ensure_governance_case_indexes,
    backfill_audit_scope_and_remove_appeals,
    ensure_sick_leave_import_columns,
    ensure_sick_leave_record_indexes,
    _foreign_key_violations,
    _rebuild_sick_leave_records,
    ensure_acting_duty_columns_and_migrate,
    ensure_group_leader_types,
    legacy_group_display_name,
    group_name_sort_key,
    ensure_group_codes,
    resequence_all_group_codes,
    migrate_legacy_duty_assignments,
)

from app.database.score_view import (
    create_score_view,
)

from app.services.management_scopes import (
    synchronize_gsm_management_scope,
    ensure_gsm_management_scopes,
)

from app.maintenance.legacy_attendance import (
    legacy_attendance_cleanup_preview,
    purge_legacy_attendance,
)
