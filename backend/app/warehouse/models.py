from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, JSON, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.database.connection import Base


WAREHOUSE_TZ = timezone(timedelta(hours=8))
UNKNOWN_PHOTO_TIME = datetime.min
GROUND_SHELF = "G"
SHELF_ZONES = {"A": "左侧", "B": "左侧", "C": "中间", "D": "中间", "E": "右侧", "F": "右侧", GROUND_SHELF: "地面"}
# IDs 1–9 predate the expanded layout and are referenced by balances, orders and photos.
WAREHOUSE_LAYOUT = (
    *((index * 3 + level, shelf, level) for index, shelf in enumerate("ABC") for level in range(1, 4)),
    *((10 + index, shelf, 4) for index, shelf in enumerate("ABC")),
    *((13 + index * 4 + level - 1, shelf, level) for index, shelf in enumerate("DEF") for level in range(1, 5)),
    (25, GROUND_SHELF, 0),
)


def warehouse_now() -> datetime:
    return datetime.now(WAREHOUSE_TZ).replace(tzinfo=None)


class WarehouseOwner(Base):
    __tablename__ = "warehouse_owners"

    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True)
    assigned_by_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=warehouse_now)


class WarehouseItem(Base):
    __tablename__ = "warehouse_items"
    __table_args__ = (UniqueConstraint("name_key", "spec_key", name="uq_warehouse_item_name_spec"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    name_key: Mapped[str] = mapped_column(String(120))
    spec: Mapped[str] = mapped_column(String(100), default="")
    spec_key: Mapped[str] = mapped_column(String(120), default="")
    category: Mapped[str] = mapped_column(String(50))
    unit: Mapped[str] = mapped_column(String(20))
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=warehouse_now)


class WarehousePhoto(Base):
    __tablename__ = "warehouse_photos"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    owner_name: Mapped[str] = mapped_column(String(100))
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=warehouse_now, index=True)
    attached_at: Mapped[datetime | None] = mapped_column(DateTime)


class WarehousePosition(Base):
    __tablename__ = "warehouse_positions"
    __table_args__ = (UniqueConstraint("shelf", "level", name="uq_warehouse_position_shelf_level"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shelf: Mapped[str] = mapped_column(String(1))
    level: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(50))
    version: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    latest_photo_id: Mapped[str | None] = mapped_column(ForeignKey("warehouse_photos.id"))
    latest_photo_version: Mapped[int | None] = mapped_column(Integer)
    latest_photo_set_at: Mapped[datetime | None] = mapped_column(DateTime)
    latest_photo_transaction_id: Mapped[int | None] = mapped_column(ForeignKey("warehouse_transactions.id"))


class WarehouseBalance(Base):
    __tablename__ = "warehouse_balances"
    __table_args__ = (CheckConstraint("quantity >= 0", name="warehouse_balance_nonnegative"),)

    item_id: Mapped[int] = mapped_column(ForeignKey("warehouse_items.id"), primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("warehouse_positions.id"), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)


class WarehouseTransaction(Base):
    __tablename__ = "warehouse_transactions"
    __table_args__ = (UniqueConstraint("actor_id", "client_id", name="uq_warehouse_transaction_client"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(15), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    actor_name: Mapped[str] = mapped_column(String(100))
    client_id: Mapped[str] = mapped_column(String(80))
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    related_id: Mapped[int | None] = mapped_column(ForeignKey("warehouse_transactions.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=warehouse_now)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    posted_by_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"))


class WarehouseTransactionPhoto(Base):
    __tablename__ = "warehouse_transaction_photos"

    transaction_id: Mapped[int] = mapped_column(ForeignKey("warehouse_transactions.id"), primary_key=True)
    photo_id: Mapped[str] = mapped_column(ForeignKey("warehouse_photos.id"), primary_key=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("warehouse_positions.id"))
    item_id: Mapped[int | None] = mapped_column(ForeignKey("warehouse_items.id"))


class WarehousePositionPhotoRevision(Base):
    __tablename__ = "warehouse_position_photo_revisions"

    position_id: Mapped[int] = mapped_column(ForeignKey("warehouse_positions.id"), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    photo_id: Mapped[str] = mapped_column(ForeignKey("warehouse_photos.id"), unique=True)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime)


class WarehousePositionPhotoReuse(Base):
    __tablename__ = "warehouse_position_photo_reuses"

    # Retain the upload identifier after its redundant image data is removed.
    photo_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("warehouse_positions.id"))
    owner_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))


class WarehousePositionPhotoTask(Base):
    __tablename__ = "warehouse_position_photo_tasks"

    position_id: Mapped[int] = mapped_column(ForeignKey("warehouse_positions.id"), primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"))
    target_version: Mapped[int] = mapped_column(Integer)
    opened_at: Mapped[datetime] = mapped_column(DateTime)
    requested_at: Mapped[datetime] = mapped_column(DateTime)
    source_transaction_id: Mapped[int | None] = mapped_column(ForeignKey("warehouse_transactions.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_by_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"))
    photo_id: Mapped[str | None] = mapped_column(ForeignKey("warehouse_photos.id"))


class WarehouseMovement(Base):
    __tablename__ = "warehouse_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("warehouse_transactions.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("warehouse_items.id"), index=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("warehouse_positions.id"))
    kind: Mapped[str] = mapped_column(String(20))
    delta: Mapped[int] = mapped_column(Integer, default=0)
    quantity_before: Mapped[int] = mapped_column(Integer, default=0)
    quantity_after: Mapped[int] = mapped_column(Integer, default=0)
    usage_delta: Mapped[int] = mapped_column(Integer, default=0)
    direct_quantity: Mapped[int] = mapped_column(Integer, default=0)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    usage_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)


class WarehouseIssue(Base):
    __tablename__ = "warehouse_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    reporter_name: Mapped[str] = mapped_column(String(100))
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), index=True)
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("warehouse_transactions.id"), index=True)
    photo_id: Mapped[str | None] = mapped_column(ForeignKey("warehouse_photos.id"))
    reason: Mapped[str] = mapped_column(String(20))
    note: Mapped[str] = mapped_column(Text, default="")
    resolution: Mapped[str] = mapped_column(Text, default="")
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=warehouse_now, index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_by_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"))


class WarehouseIssueLine(Base):
    __tablename__ = "warehouse_issue_lines"
    __table_args__ = (UniqueConstraint("issue_id", "item_id", "position_id", name="uq_warehouse_issue_line"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("warehouse_issues.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("warehouse_items.id"), index=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("warehouse_positions.id"))
    book_quantity: Mapped[int] = mapped_column(Integer)
    expected_quantity: Mapped[int] = mapped_column(Integer)
    observed_quantity: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(Text, default="")


def seed_positions(db: Session) -> None:
    existing = {key for (key,) in db.query(WarehousePosition.id).all()}
    for key, shelf, level in WAREHOUSE_LAYOUT:
        if key not in existing:
            label = "地面临时储存区" if shelf == GROUND_SHELF else f"{shelf} 架第 {level} 层"
            db.add(WarehousePosition(id=key, shelf=shelf, level=level, label=label))
