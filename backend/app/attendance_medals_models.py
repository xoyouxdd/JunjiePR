"""Attendance honors are independent of all scoring tables."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, event, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Mapped, mapped_column

from app.database.connection import Base


class MedalAccountIdentity(Base):
    __tablename__ = "medal_account_identities"
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), primary_key=True)
    first_created_at: Mapped[datetime] = mapped_column(DateTime)


class MedalLeaveMonth(Base):
    __tablename__ = "medal_leave_months"
    month: Mapped[str] = mapped_column(String(7), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    roster: Mapped[str | None] = mapped_column(Text, nullable=True)


class MedalWageCode(Base):
    __tablename__ = "medal_wage_codes"
    __table_args__ = (CheckConstraint("kind IN ('leave', 'normal')"),)
    code: Mapped[str] = mapped_column(String(100), primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MedalLeaveBatch(Base):
    __tablename__ = "medal_leave_batches"
    __table_args__ = (UniqueConstraint("month", "revision"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    month: Mapped[str] = mapped_column(String(7), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(255))
    digest: Mapped[str] = mapped_column(String(64))
    leaves: Mapped[str] = mapped_column(Text)
    issues: Mapped[str] = mapped_column(Text)
    ignored_codes: Mapped[str] = mapped_column(Text)
    imported_by: Mapped[str] = mapped_column(String(100))
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MedalCircleRun(Base):
    __tablename__ = "medal_circle_runs"
    __table_args__ = (UniqueConstraint("month", "attraction_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    month: Mapped[str] = mapped_column(String(7), index=True)
    attraction_id: Mapped[int] = mapped_column(ForeignKey("attractions.id"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    results: Mapped[str] = mapped_column(Text)
    confirmed_by: Mapped[str] = mapped_column(String(100))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AttendanceMedal(Base):
    __tablename__ = "attendance_medals"
    __table_args__ = (UniqueConstraint("employee_id", "month"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    month: Mapped[str] = mapped_column(String(7), index=True)
    attraction_id: Mapped[int] = mapped_column(ForeignKey("attractions.id"))
    status: Mapped[str] = mapped_column(String(20), default="awarded")
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MedalNotice(Base):
    __tablename__ = "medal_notices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    month: Mapped[str] = mapped_column(String(7))
    message: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, default="")
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


def install_account_identity_events(account_model):
    def remember(mapper, connection, account):
        connection.execute(insert(MedalAccountIdentity).values(
            employee_id=account.employee_id, first_created_at=account.created_at or datetime.now()
        ).on_conflict_do_nothing(index_elements=["employee_id"]))
    event.listen(account_model, "after_insert", remember)
    event.listen(account_model, "before_delete", remember)


def backfill_account_identities(db):
    from app.v2_models import UserAccount
    db.execute(insert(MedalAccountIdentity).from_select(
        ["employee_id", "first_created_at"],
        select(UserAccount.employee_id, UserAccount.created_at).where(UserAccount.id > 0)
    ).on_conflict_do_nothing(index_elements=["employee_id"]))
