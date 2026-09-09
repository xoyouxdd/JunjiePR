from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.v2_database import Base


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(30), unique=True)
    rank: Mapped[int] = mapped_column(Integer, index=True)
    can_lead_group: Mapped[bool] = mapped_column(Boolean, default=False)
    attendance_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), index=True)
    permission_id: Mapped[int] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"), index=True)


class Attraction(Base):
    __tablename__ = "attractions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    employee_circle: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    recognition_venue: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_no: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    attraction_id: Mapped[int | None] = mapped_column(ForeignKey("attractions.id"), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    hired_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    terminated_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # A removed login never removes the employee's performance, attachment or
    # audit archive.  These fields distinguish that retained archive from an
    # employee who has simply never been provisioned with an account.
    account_deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    account_deleted_by_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    attraction = relationship("Attraction")


class EmployeeNumberHistory(Base):
    """Immutable employee-number change trail; all business rows keep employee_id."""

    __tablename__ = "employee_number_history"
    __table_args__ = (
        Index("ix_employee_number_history_employee_date", "employee_id", "effective_on", "id"),
        Index("ix_employee_number_history_old_number", "old_employee_no"),
        Index("ix_employee_number_history_new_number", "new_employee_no"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    old_employee_no: Mapped[str] = mapped_column(String(50), index=True)
    new_employee_no: Mapped[str] = mapped_column(String(50), index=True)
    effective_on: Mapped[str] = mapped_column(String(10), index=True)
    reason: Mapped[str] = mapped_column(Text)
    changed_by: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    changed_by_name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


class EmployeeLOAPeriod(Base):
    __tablename__ = "employee_loa_periods"
    __table_args__ = (Index("ix_employee_loa_lookup", "employee_id", "starts_on", "ends_on", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    starts_on: Mapped[str] = mapped_column(String(10), index=True)
    ends_on: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    created_by_name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    ended_by: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    ended_by_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class EmployeeRoleAssignment(Base):
    __tablename__ = "employee_role_assignments"
    __table_args__ = (
        CheckConstraint("ends_on IS NULL OR ends_on >= starts_on", name="ck_role_assignment_dates"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), index=True)
    starts_on: Mapped[str] = mapped_column(String(10), index=True)
    ends_on: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    assignment_type: Mapped[str] = mapped_column(String(20), default="permanent")
    return_role_id: Mapped[int | None] = mapped_column(ForeignKey("roles.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    employee = relationship("Employee", foreign_keys=[employee_id])
    role = relationship("Role", foreign_keys=[role_id])
    return_role = relationship("Role", foreign_keys=[return_role_id])


class UserAccount(Base):
    __tablename__ = "user_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), unique=True, index=True)
    login_account: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    credential_initialized: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Never store a readable password.  This timestamp is only used for the
    # HR/admin account-status view to distinguish an initial/reset password
    # from one the account holder has subsequently changed.
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Set whenever HR disables an account.  It provides a verifiable seven-day
    # waiting period before the credential can be removed while the employee
    # archive stays intact.
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    employee = relationship("Employee")


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class SubmissionRequest(Base):
    __tablename__ = "submission_requests"
    __table_args__ = (
        UniqueConstraint("actor_id", "operation", "request_key", name="uq_submission_request"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    operation: Mapped[str] = mapped_column(String(30), index=True)
    request_key: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


class ManagementScope(Base):
    __tablename__ = "management_scopes"
    __table_args__ = (UniqueConstraint("employee_id", "attraction_id", "starts_on", name="uq_management_scope"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    attraction_id: Mapped[int] = mapped_column(ForeignKey("attractions.id", ondelete="CASCADE"), index=True)
    starts_on: Mapped[str] = mapped_column(String(10), index=True)
    ends_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class WorkGroup(Base):
    __tablename__ = "work_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    attraction_id: Mapped[int] = mapped_column(ForeignKey("attractions.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    attraction = relationship("Attraction")


class GroupLeaderAssignment(Base):
    __tablename__ = "group_leader_assignments"
    __table_args__ = (CheckConstraint("ends_on IS NULL OR ends_on >= starts_on", name="ck_group_leader_dates"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("work_groups.id", ondelete="CASCADE"), index=True)
    leader_employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    starts_on: Mapped[str] = mapped_column(String(10), index=True)
    ends_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    transfer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    group = relationship("WorkGroup")
    leader = relationship("Employee")


class GroupMembership(Base):
    __tablename__ = "group_memberships"
    __table_args__ = (CheckConstraint("ends_on IS NULL OR ends_on >= starts_on", name="ck_group_membership_dates"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("work_groups.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    starts_on: Mapped[str] = mapped_column(String(10), index=True)
    ends_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    group = relationship("WorkGroup")
    employee = relationship("Employee")


class GroupTransfer(Base):
    __tablename__ = "group_transfers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("work_groups.id"), index=True)
    old_leader_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    new_leader_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    attraction_id: Mapped[int] = mapped_column(ForeignKey("attractions.id"))
    effective_date: Mapped[str] = mapped_column(String(10), index=True)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    pending_review_count: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(Text)
    operator_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class GroupTransferMember(Base):
    __tablename__ = "group_transfer_members"
    __table_args__ = (UniqueConstraint("transfer_id", "employee_id", name="uq_transfer_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transfer_id: Mapped[int] = mapped_column(ForeignKey("group_transfers.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    employee_no: Mapped[str] = mapped_column(String(50))
    employee_name: Mapped[str] = mapped_column(String(100))


class CircleTransferRequest(Base):
    __tablename__ = "circle_transfer_requests"
    __table_args__ = (Index("ix_circle_transfer_status_scope", "status", "source_attraction_id", "target_attraction_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    employee_no: Mapped[str] = mapped_column(String(50), index=True)
    employee_name: Mapped[str] = mapped_column(String(100), index=True)
    source_attraction_id: Mapped[int] = mapped_column(ForeignKey("attractions.id"), index=True)
    source_attraction_name: Mapped[str] = mapped_column(String(100))
    target_attraction_id: Mapped[int] = mapped_column(ForeignKey("attractions.id"), index=True)
    target_attraction_name: Mapped[str] = mapped_column(String(100))
    source_group_id: Mapped[int | None] = mapped_column(ForeignKey("work_groups.id"), nullable=True)
    source_leader_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    target_group_id: Mapped[int | None] = mapped_column(ForeignKey("work_groups.id"), nullable=True)
    target_leader_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    requested_by: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    requested_by_name: Mapped[str] = mapped_column(String(100))
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    reviewed_by_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    migrated_record_counts_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class RecognitionType(Base):
    __tablename__ = "recognition_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(30), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class RecognitionScoreRule(Base):
    __tablename__ = "recognition_score_rules"
    __table_args__ = (UniqueConstraint("role_id", "effective_date", name="uq_recognition_score_rule"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), index=True)
    score: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    effective_date: Mapped[str] = mapped_column(String(10), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class RecognitionRecord(Base):
    __tablename__ = "recognition_records"
    __table_args__ = (
        # Used when a confirmation must atomically calculate the remaining
        # per-employee/month/type recognition allowance.
        Index(
            "ix_recognition_monthly_cap_lookup",
            "employee_id",
            "recognition_month",
            "recognition_type_id",
            "status",
            "reviewed_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    employee_no: Mapped[str] = mapped_column(String(50), index=True)
    employee_name: Mapped[str] = mapped_column(String(100), index=True)
    employee_role_snapshot: Mapped[str] = mapped_column(String(30))
    employee_role_code_snapshot: Mapped[str | None] = mapped_column(String(30), nullable=True)
    home_attraction_id: Mapped[int | None] = mapped_column(ForeignKey("attractions.id"), nullable=True, index=True)
    home_attraction_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    occurred_attraction_id: Mapped[int] = mapped_column(ForeignKey("attractions.id"), index=True)
    recognition_date: Mapped[str] = mapped_column(String(10), index=True)
    recognition_month: Mapped[str] = mapped_column(String(7), index=True)
    recognition_type_id: Mapped[int] = mapped_column(ForeignKey("recognition_types.id"), index=True)
    recognition_type_name: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(String(20))
    recognizer_employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    recognizer_name: Mapped[str] = mapped_column(String(100))
    recognizer_role_snapshot: Mapped[str] = mapped_column(String(30))
    recognizer_role_code_snapshot: Mapped[str | None] = mapped_column(String(30), nullable=True)
    operator_employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    operator_name: Mapped[str] = mapped_column(String(100))
    operator_role_snapshot: Mapped[str | None] = mapped_column(String(30), nullable=True)
    operator_role_code_snapshot: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source: Mapped[str] = mapped_column(String(20), index=True)
    # fraction is the original score frozen at registration.  credited_fraction
    # is the score actually included in monthly performance after any cap.
    fraction: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    credited_fraction: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    monthly_cap_rule_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    monthly_cap_status: Mapped[str] = mapped_column(String(30), default="not_applicable", index=True)
    monthly_cap_limit: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    monthly_cap_confirmed_before: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    monthly_cap_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    monthly_cap_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # POC is an independent special-contribution recognition.  It keeps a
    # separate period and explanation instead of overloading the 20-char
    # ordinary-recognition content field.
    poc_period_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    poc_period_key: Mapped[str | None] = mapped_column(String(20), nullable=True)
    poc_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    same_day_duplicate_group: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    same_day_duplicate_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assigned_reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True, index=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    reviewed_by_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    voided_by: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    voided_by_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    voided_by_role_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    voided_by_role_name: Mapped[str | None] = mapped_column(String(30), nullable=True)
    void_permission_scope_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    voided_from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    void_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    employee = relationship("Employee", foreign_keys=[employee_id])
    attachments = relationship("RecognitionAttachment", cascade="all, delete-orphan", passive_deletes=True)


class RecognitionAttachment(Base):
    __tablename__ = "recognition_attachments"
    __table_args__ = (
        UniqueConstraint("recognition_id", "attachment_type", name="uq_recognition_attachment_type"),
        UniqueConstraint("file_id", name="uq_recognition_attachment_file"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recognition_id: Mapped[int] = mapped_column(ForeignKey("recognition_records.id", ondelete="CASCADE"), index=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("stored_files.id"), index=True)
    attachment_type: Mapped[str] = mapped_column(String(30), default="evidence")
    sort_order: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    file = relationship("StoredFile")


class RecognitionMonthlyQuota(Base):
    __tablename__ = "recognition_monthly_quotas"
    __table_args__ = (
        UniqueConstraint("employee_id", "quota_code", "quota_month", name="uq_recognition_monthly_quota"),
        UniqueConstraint("recognition_id", name="uq_recognition_monthly_quota_record"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    quota_code: Mapped[str] = mapped_column(String(30), index=True)
    quota_month: Mapped[str] = mapped_column(String(7), index=True)
    recognition_id: Mapped[int] = mapped_column(ForeignKey("recognition_records.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class RecognitionReview(Base):
    __tablename__ = "recognition_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recognition_id: Mapped[int] = mapped_column(ForeignKey("recognition_records.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(20))
    before_status: Mapped[str] = mapped_column(String(20))
    after_status: Mapped[str] = mapped_column(String(20))
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    reviewer_name: Mapped[str] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AppNotification(Base):
    """In-app notices.  Push delivery is optional and never replaces this record."""

    __tablename__ = "app_notifications"
    __table_args__ = (Index("ix_app_notification_employee_created", "employee_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    notification_type: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(String(500))
    target_path: Mapped[str] = mapped_column(String(255), default="/")
    record_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    record_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class PushSubscription(Base):
    """A browser/device subscription owned by the currently signed-in employee."""

    __tablename__ = "push_subscriptions"
    __table_args__ = (UniqueConstraint("endpoint", name="uq_push_subscription_endpoint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(Text)
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class StoredFile(Base):
    __tablename__ = "stored_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    storage_key: Mapped[str] = mapped_column(String(255), unique=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    extension: Mapped[str] = mapped_column(String(20))
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    status: Mapped[str] = mapped_column(String(20), default="active")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AttendanceRule(Base):
    __tablename__ = "attendance_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    base_score: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("10.00"))
    perfect_bonus: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("2.00"))
    sick_day_deduction: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.45"))
    zero_threshold: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.10"))
    effective_date: Mapped[str] = mapped_column(String(10), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class MonthClosure(Base):
    """Current close state for a score month and optional employee circle.

    The audit log retains every close/reopen transition; this table is the
    fast authoritative gate used by all write endpoints.
    """

    __tablename__ = "month_closures"
    __table_args__ = (
        UniqueConstraint("closure_month", "attraction_id", name="uq_month_closure_scope"),
        Index(
            "uq_month_closure_global",
            "closure_month",
            unique=True,
            sqlite_where=text("attraction_id IS NULL"),
        ),
        Index("ix_month_closure_lookup", "closure_month", "attraction_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    closure_month: Mapped[str] = mapped_column(String(7), index=True)
    attraction_id: Mapped[int | None] = mapped_column(ForeignKey("attractions.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    closed_by_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    close_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reopened_by: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    reopened_by_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reopen_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    attraction = relationship("Attraction")


class GovernanceCase(Base):
    """A traceable appeal or month-close correction request.

    Cases deliberately do not mutate score records themselves.  Any approved
    correction remains subject to the existing void/reopen write gates, while
    this table preserves the independent review decision and its rationale.
    """

    __tablename__ = "governance_cases"
    __table_args__ = (
        Index("ix_governance_case_status_scope", "case_type", "status", "attraction_id", "submitted_at"),
        Index("ix_governance_case_subject", "record_type", "record_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_type: Mapped[str] = mapped_column(String(30), index=True)
    record_type: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    record_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    attraction_id: Mapped[int | None] = mapped_column(ForeignKey("attractions.id"), nullable=True, index=True)
    score_month: Mapped[str | None] = mapped_column(String(7), nullable=True, index=True)
    subject_employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True, index=True)
    submitted_by: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    submitted_by_name: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    decision: Mapped[str | None] = mapped_column(String(30), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    resolved_by_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime, index=True)

    attraction = relationship("Attraction")


class EmployeeMonthOrganizationSnapshot(Base):
    """The first approved close locks the organization context for that month."""

    __tablename__ = "employee_month_organization_snapshots"
    __table_args__ = (
        UniqueConstraint("employee_id", "score_month", name="uq_employee_month_organization_snapshot"),
        Index("ix_employee_month_organization_scope", "score_month", "attraction_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    score_month: Mapped[str] = mapped_column(String(7), index=True)
    attraction_id: Mapped[int | None] = mapped_column(ForeignKey("attractions.id"), nullable=True, index=True)
    attraction_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("work_groups.id"), nullable=True)
    group_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    leader_employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    leader_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class SickLeaveRecord(Base):
    __tablename__ = "sick_leave_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    employee_no_snapshot: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    employee_name_snapshot: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    employee_role_snapshot: Mapped[str | None] = mapped_column(String(30), nullable=True)
    attraction_id_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    attendance_month: Mapped[str] = mapped_column(String(7), index=True)
    leave_start_date: Mapped[str] = mapped_column(String(10))
    leave_end_date: Mapped[str] = mapped_column(String(10))
    leave_days: Mapped[Decimal] = mapped_column(Numeric(6, 1))
    charged_days: Mapped[Decimal] = mapped_column(Numeric(6, 1))
    proof_file_id: Mapped[int] = mapped_column(ForeignKey("stored_files.id"))
    is_violation: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    violation_deduction_id: Mapped[int | None] = mapped_column(ForeignKey("deduction_records.id"), nullable=True, unique=True, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    submitted_by: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    submitted_by_name: Mapped[str] = mapped_column(String(100))
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    voided_by: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    voided_by_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    voided_by_role_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    voided_by_role_name: Mapped[str | None] = mapped_column(String(30), nullable=True)
    void_permission_scope_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    voided_from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    void_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class AttendanceMonthlyScore(Base):
    __tablename__ = "attendance_monthly_scores"
    __table_args__ = (UniqueConstraint("employee_id", "attendance_month", name="uq_attendance_employee_month"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    attendance_month: Mapped[str] = mapped_column(String(7), index=True)
    month_end_role_id: Mapped[int | None] = mapped_column(ForeignKey("roles.id"), nullable=True)
    eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    actual_sick_days: Mapped[Decimal] = mapped_column(Numeric(8, 1), default=Decimal("0.0"))
    charged_sick_days: Mapped[Decimal] = mapped_column(Numeric(8, 1), default=Decimal("0.0"))
    base_score: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    perfect_bonus: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    sick_deduction: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    final_score: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("attendance_rules.id"), nullable=True)
    calculation_version: Mapped[int] = mapped_column(Integer, default=1)
    calculated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class DeductionType(Base):
    __tablename__ = "deduction_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(30), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class DeductionLevel(Base):
    __tablename__ = "deduction_levels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(30), unique=True)
    points: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class DeductionRecord(Base):
    __tablename__ = "deduction_records"
    __table_args__ = (Index("ix_deduction_repeat_lookup", "employee_id", "deduction_type_id", "status", "occurred_on"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    employee_no: Mapped[str] = mapped_column(String(50), index=True)
    employee_name: Mapped[str] = mapped_column(String(100), index=True)
    employee_role_snapshot: Mapped[str] = mapped_column(String(30))
    employee_group_id_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attraction_id_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deduction_type_id: Mapped[int] = mapped_column(ForeignKey("deduction_types.id"))
    deduction_type_name: Mapped[str] = mapped_column(String(30))
    deduction_level_id: Mapped[int] = mapped_column(ForeignKey("deduction_levels.id"))
    deduction_level_name: Mapped[str] = mapped_column(String(30))
    points: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    occurred_on: Mapped[str] = mapped_column(String(10), index=True)
    deduction_month: Mapped[str] = mapped_column(String(7), index=True)
    description: Mapped[str] = mapped_column(Text)
    document_file_id: Mapped[int] = mapped_column(ForeignKey("stored_files.id"))
    submitter_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    submitter_name: Mapped[str] = mapped_column(String(100))
    submitter_role_snapshot: Mapped[str] = mapped_column(String(30))
    permission_scope_snapshot: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    # A photo submission is persisted immediately, but only becomes active
    # after its official PDF material is generated by the durable worker.
    material_status: Mapped[str] = mapped_column(String(30), default="ready", index=True)
    material_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    material_source_type: Mapped[str] = mapped_column(String(20), default="pdf")
    material_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    material_uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    material_uploaded_by_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    material_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    material_revision: Mapped[int] = mapped_column(Integer, default=1)
    legacy_upgrade_excluded: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    legacy_upgrade_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    voided_by: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    voided_by_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    voided_by_role_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    voided_by_role_name: Mapped[str | None] = mapped_column(String(30), nullable=True)
    void_permission_scope_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    voided_from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    void_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    upgrade_request_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    upgrade_role: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    upgrade_state: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)


class DeductionMaterialJob(Base):
    """Durable image-to-PDF conversion job for a deduction record.

    Source images are private temporary files.  The public deduction document
    remains one generated PDF, so existing review, preview and export paths do
    not expose raw mobile photos.
    """

    __tablename__ = "deduction_material_jobs"
    __table_args__ = (
        Index("ix_deduction_material_job_status_created", "status", "created_at"),
        Index("ix_deduction_material_job_deduction", "deduction_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deduction_id: Mapped[int] = mapped_column(ForeignKey("deduction_records.id", ondelete="CASCADE"), index=True)
    output_file_id: Mapped[int] = mapped_column(ForeignKey("stored_files.id"), index=True)
    source_file_ids_json: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(30), default="deduction")
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    first_deduction_id: Mapped[int | None] = mapped_column(ForeignKey("deduction_records.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DeductionUpgradeRequest(Base):
    __tablename__ = "deduction_upgrade_requests"
    __table_args__ = (Index("ix_deduction_upgrade_assignee_status", "reviewer_id", "status", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    deduction_type_id: Mapped[int] = mapped_column(ForeignKey("deduction_types.id"), index=True)
    first_deduction_id: Mapped[int] = mapped_column(ForeignKey("deduction_records.id"), unique=True, index=True)
    second_deduction_id: Mapped[int] = mapped_column(ForeignKey("deduction_records.id"), unique=True, index=True)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    reviewer_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    result_level_id: Mapped[int | None] = mapped_column(ForeignKey("deduction_levels.id"), nullable=True)
    result_deduction_id: Mapped[int | None] = mapped_column(ForeignKey("deduction_records.id"), nullable=True, unique=True)
    handling_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    issued_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    submitted_by: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    submitted_by_name: Mapped[str] = mapped_column(String(100))
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    resolved_by_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


class DeductionUpgradeTransfer(Base):
    __tablename__ = "deduction_upgrade_transfers"
    __table_args__ = (Index("ix_deduction_upgrade_transfer_request", "request_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("deduction_upgrade_requests.id", ondelete="CASCADE"), index=True)
    from_reviewer_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    from_reviewer_name: Mapped[str] = mapped_column(String(100))
    to_reviewer_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    to_reviewer_name: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


class DeductionFollowUp(Base):
    __tablename__ = "deduction_follow_ups"
    __table_args__ = (
        UniqueConstraint(
            "supervisor_id",
            "employee_id",
            "deduction_type_id",
            "occurred_on",
            name="uq_deduction_follow_up_event",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supervisor_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    supervisor_name: Mapped[str] = mapped_column(String(100))
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    employee_no: Mapped[str] = mapped_column(String(50), index=True)
    employee_name: Mapped[str] = mapped_column(String(100), index=True)
    deduction_type_id: Mapped[int] = mapped_column(ForeignKey("deduction_types.id"), index=True)
    deduction_type_name: Mapped[str] = mapped_column(String(30))
    occurred_on: Mapped[str] = mapped_column(String(10), index=True)
    previous_record_ids: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    issued_deduction_id: Mapped[int | None] = mapped_column(ForeignKey("deduction_records.id"), nullable=True)
    issued_by: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    issued_by_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    operator_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(50), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    before_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


class SystemAlert(Base):
    __tablename__ = "system_alerts"
    __table_args__ = (UniqueConstraint("alert_type", "dedupe_key", name="uq_system_alert"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_type: Mapped[str] = mapped_column(String(50), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(100))
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("work_groups.id"), nullable=True)
    due_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    handled_by: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class SystemJobRun(Base):
    __tablename__ = "system_job_runs"
    __table_args__ = (UniqueConstraint("job_type", "idempotency_key", name="uq_system_job_run"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(50), index=True)
    business_date: Mapped[str] = mapped_column(String(10))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
