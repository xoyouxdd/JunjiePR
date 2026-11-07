"""Activity contracts, entries and auction bids; point movements use the shared ledger."""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column
from app.database.connection import Base


class Activity(Base):
    __tablename__ = 'activities'
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), default='draft')
    circles_json: Mapped[str] = mapped_column(Text, default='[]')
    helpers_json: Mapped[str] = mapped_column(Text, default='[]')
    starts_at: Mapped[datetime] = mapped_column(DateTime)
    ends_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    claim_until: Mapped[datetime] = mapped_column(DateTime)
    location: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ActivityLot(Base):
    __tablename__ = 'activity_lots'
    id: Mapped[int] = mapped_column(primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey('activities.id'), index=True)
    name: Mapped[str] = mapped_column(String(120))
    price: Mapped[int] = mapped_column(Integer)
    per_person: Mapped[int] = mapped_column(Integer, default=1)
    step: Mapped[int] = mapped_column(Integer, default=1)
    extend_seconds: Mapped[int] = mapped_column(Integer, default=60)
    ends_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    state: Mapped[str] = mapped_column(String(20), default='open')
    highest_employee_id: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
    highest_price: Mapped[int] = mapped_column(Integer, default=0)


class ActivityPrize(Base):
    __tablename__ = 'activity_prizes'
    id: Mapped[int] = mapped_column(primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey('activity_lots.id'), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default='')
    quantity: Mapped[int] = mapped_column(Integer)


class ActivityPrizePhoto(Base):
    """Bound photos are immutable; abandoned uploads expire without affecting prizes."""
    __tablename__ = 'activity_prize_photos'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    prize_id: Mapped[int | None] = mapped_column(ForeignKey('activity_prizes.id'), nullable=True, unique=True)
    jpeg: Mapped[bytes] = mapped_column(LargeBinary)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)


class ActivityAward(Base):
    __tablename__ = 'activity_awards'
    id: Mapped[int] = mapped_column(primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey('activity_lots.id'), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    cost: Mapped[int] = mapped_column(Integer)
    prize_id: Mapped[int | None] = mapped_column(ForeignKey('activity_prizes.id'), nullable=True)
    state: Mapped[str] = mapped_column(String(20), default='pending')
    reason: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ActivityBid(Base):
    __tablename__ = 'activity_bids'
    id: Mapped[int] = mapped_column(primary_key=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey('activity_lots.id'), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'))
    amount: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ActivityOperation(Base):
    __tablename__ = 'activity_operations'
    __table_args__ = (UniqueConstraint('employee_id', 'request_key'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'))
    request_key: Mapped[str] = mapped_column(String(80))
    fingerprint: Mapped[str] = mapped_column(String(64))
    result_json: Mapped[str] = mapped_column(Text)


class ActivityDispatch(Base):
    __tablename__ = 'activity_dispatches'
    award_id: Mapped[int] = mapped_column(ForeignKey('activity_awards.id'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    issued_by: Mapped[int] = mapped_column(ForeignKey('employees.id'))
    issued_name: Mapped[str] = mapped_column(String(100))
    issued_at: Mapped[datetime] = mapped_column(DateTime)
    snapshot_json: Mapped[str] = mapped_column(Text)


class ActivityReceipt(Base):
    """Append-only evidence. Returning/refunding a prize never deletes its signature."""
    __tablename__ = 'activity_receipts'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    award_id: Mapped[int] = mapped_column(ForeignKey('activity_awards.id'), unique=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    account_id: Mapped[int] = mapped_column(Integer)
    dispatch_revision: Mapped[int] = mapped_column(Integer)
    snapshot_json: Mapped[str] = mapped_column(Text)
    confirmation: Mapped[str] = mapped_column(Text)
    signature_png: Mapped[bytes] = mapped_column(LargeBinary)
    evidence_hash: Mapped[str] = mapped_column(String(64))
    signed_at: Mapped[datetime] = mapped_column(DateTime)
