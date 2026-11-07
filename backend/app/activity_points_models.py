"""Permanent recognition points, separate from PR scores and future redemption."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.connection import Base


class RecognitionPointsState(Base):
    __tablename__ = "recognition_points_states"
    recognition_id: Mapped[int] = mapped_column(ForeignKey("recognition_records.id"), primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    applied_points: Mapped[int] = mapped_column(Integer, default=0)
    entitlement: Mapped[int] = mapped_column(Integer, default=0)
    source_key: Mapped[str] = mapped_column(String(64))
    signer_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    recipient_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reason: Mapped[str] = mapped_column(String(60))


class ActivityPointsEntry(Base):
    __tablename__ = "activity_points_entries"
    __table_args__ = (UniqueConstraint("recognition_id", "revision", "employee_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    recognition_id: Mapped[int | None] = mapped_column(ForeignKey("recognition_records.id"), nullable=True, index=True)
    revision: Mapped[int] = mapped_column(Integer)
    delta: Mapped[int] = mapped_column(Integer)
    # Reserved: future redemption/refund entries must use their own audited workflow.
    kind: Mapped[str] = mapped_column(String(30), default="recognition")
    origin: Mapped[str] = mapped_column(String(30))
    business_date: Mapped[str] = mapped_column(String(10))
    detail_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class LegacyPointsBalance(Base):
    """One historical opening balance per person, shared across circle transfers."""
    __tablename__ = "legacy_points_balances"
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), primary_key=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_by: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class LegacyPointsExport(Base):
    __tablename__ = "legacy_points_exports"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    circle_id: Mapped[int] = mapped_column(ForeignKey("attractions.id"), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    roster_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class LegacyPointsPreview(Base):
    __tablename__ = "legacy_points_previews"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    export_id: Mapped[str] = mapped_column(ForeignKey("legacy_points_exports.id"))
    created_by: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
