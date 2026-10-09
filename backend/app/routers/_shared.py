"""Compatibility exports for helpers owned by explicit application modules.

New services import the owning modules directly. Legacy routes and tools
may keep this import boundary while moving to domain-specific imports.
"""
from __future__ import annotations

import hashlib
import json
import os
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Lock
from fastapi import HTTPException, Request
from sqlalchemy import and_, or_, text
from sqlalchemy.orm import Session
from app.v2_auth import V2User
from app.v2_models import Attraction, CircleTransferRequest, DeductionLevel, DeductionFollowUp, DeductionUpgradeRequest, DeductionRecord, DeductionType, Employee, GroupLeaderAssignment, MonthClosure, RecognitionRecord, Role, SickLeaveRecord, StoredFile, SubmissionRequest, SystemAlert, UserAccount, WorkGroup
from app.services.organization import ACTING_LEADER_FIRST
from app.role_constants import FRONTLINE_CODES
from app.role_constants import GSM_CODES
from app.role_constants import LEADER_CODES
from app.role_constants import RECOGNIZER_CODES
from app.role_constants import SCORED_BASE_CODES
from app.services.identity import base_role_at
from app.services.identity import base_roles_at
from app.services.identity import duties_at_bulk
from app.services.identity import duty_code_at
from app.services.organization import group_leader_label
from app.services.organization import groups_assigned_to
from app.services.identity import identity_labels
from app.services.organization import managed_attraction_ids
from app.services.identity import role_at
from app.v2_models import EmployeeActingDuty, EmployeeLOAPeriod, GroupMembership
from app.services.identity import roles_at

from app.data_cache import (
    STATISTICS_CACHE_SECONDS,
    _statistics_cache_lock,
    _statistics_response_cache,
    PR_RANKING_CACHE_SECONDS,
    _pr_ranking_cache_lock,
    _pr_ranking_response_cache,
    invalidate_data_caches,
)
from app.file_types import (
    PREVIEW_IMAGE_EXTENSIONS,
    PREVIEW_PDF_EXTENSIONS,
    is_previewable_image,
    preview_kind,
)
from app.date_utils import (
    parse_iso_date,
    parse_score_month,
    subtract_calendar_months,
    add_calendar_months,
    months_between,
)
from app.search_utils import (
    like_escaped_pattern,
)
from app.idempotency import (
    normalize_request_key,
    submission_payload_digest,
    existing_submission,
    remember_submission,
)
from app.access_policy import (
    CIRCLE_HR_MANAGED_ROLE_CODES,
    REGULAR_ACCOUNT_ROLE_CODES,
    SCOPED_HR_ROLE_CODE,
    CIRCLE_HR_SCORE_RULE_ROLE_CODES,
    MATERIAL_COLLABORATOR_CODES,
    EMPLOYEE_TARGET_PERMISSIONS,
    SUPERVISOR_SCORER_CODES,
    scoped_hr_attraction_ids,
    ensure_scoped_hr_attraction,
    ensure_scoped_hr_employee,
    ensure_operational_target_scope,
    visible_system_alerts,
    ensure_enabled_scored_target,
    ensure_supervisor_target_allowed,
    ensure_enabled_frontline_target,
    void_operator_snapshot,
)
from app.recognition_policy import (
    SPECIAL_RECOGNITION_TYPES,
    DEDICATED_RECOGNITION_TYPE_CODES,
    MONTHLY_CATEGORY_CAP_CODES,
    MONTHLY_CATEGORY_CAP_EFFECTIVE_DATE,
    MONTHLY_CATEGORY_CAP_LIMIT,
    effective_recognition_credit,
)
from app.deduction_policy import (
    ATTENDANCE_DEDUCTION_CODES,
    REPEAT_CONTROLLED_DEDUCTION_CODES,
    ATTENDANCE_UPGRADE_GROUPS,
    DIRECT_HIDDEN_DEDUCTION_CODES,
    DEDUCTION_TYPE_ORDER,
    DEDUCTION_LEVEL_ORDER,
    NEXT_DEDUCTION_LEVEL,
    UPGRADE_REVIEWER_CODES,
    upgrade_type_codes,
    deduction_counts_for_score,
    direct_only_deduction_user,
    ensure_deduction_type_allowed,
    statement_upgrade_candidate,
    statement_upgrade_reviewer_options,
)
from app.material_policy import (
    pending_material_conflict,
    raise_pending_material_conflict,
)
from app.record_payloads import (
    RECOGNITION_FILTER_STATUSES,
    ATTENDANCE_FILTER_STATUSES,
    DEDUCTION_FILTER_STATUSES,
    recognition_payload,
    deduction_payload,
    deduction_follow_up_payload,
    sick_leave_payload,
    sick_leave_payloads,
)
from app.organization_queries import (
    group_display_metadata_bulk,
    search_employee_targets,
    login_account_archive_state,
    employee_payloads,
)
from app.month_closure import (
    MONTH_CLOSE_ROLE_CODES,
    MONTH_CLOSE_EFFECTIVE_DATE,
    month_closure_scope,
    ensure_month_open,
    month_close_checklist,
    month_closure_payload,
)
from app.operations_health import (
    BACKUP_HEALTH_STATUS_PATH,
    backup_health_payload,
)
from app.http_utils import (
    client_ip,
)
