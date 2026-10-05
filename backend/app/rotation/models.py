"""轮岗模块的表。时间字段 *_min 一律是“当天零点起的分钟数”。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.v2_database import Base


class RotationScreenAccount(Base):
    """休息室大屏账号。不对应员工，不进入任何员工列表、统计或待办。"""

    __tablename__ = "rotation_screen_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    login_account: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    attraction_id: Mapped[int] = mapped_column(ForeignKey("attractions.id"), index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class RotationScreenSession(Base):
    __tablename__ = "rotation_screen_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("rotation_screen_accounts.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class RotationConfig(Base):
    """每个景点圈一份线和岗位配置、参数。"""

    __tablename__ = "rotation_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attraction_id: Mapped[int] = mapped_column(ForeignKey("attractions.id"), unique=True)
    lines_json: Mapped[str] = mapped_column(Text)
    settings_json: Mapped[str] = mapped_column(Text)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class RotationRosterUpload(Base):
    """一次名单上传。scope 为 week 或 day；同一天按天上传的覆盖按周上传的。"""

    __tablename__ = "rotation_roster_uploads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attraction_id: Mapped[int] = mapped_column(ForeignKey("attractions.id"), index=True)
    scope: Mapped[str] = mapped_column(String(10))
    start_date: Mapped[str] = mapped_column(String(10))
    end_date: Mapped[str] = mapped_column(String(10))
    file_name: Mapped[str] = mapped_column(String(255))
    hours_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    entry_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class RotationRosterEntry(Base):
    __tablename__ = "rotation_roster_entries"
    __table_args__ = (
        Index("ix_rotation_roster_entry_day", "attraction_id", "work_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("rotation_roster_uploads.id", ondelete="CASCADE"), index=True)
    attraction_id: Mapped[int] = mapped_column(ForeignKey("attractions.id"))
    work_date: Mapped[str] = mapped_column(String(10))
    employee_no: Mapped[str] = mapped_column(String(50), index=True)
    name: Mapped[str] = mapped_column(String(100))
    person_type: Mapped[str] = mapped_column(String(30), default="")
    mark: Mapped[str] = mapped_column(String(30), default="")
    cell_raw: Mapped[str] = mapped_column(String(255), default="")
    # 名单里的工号在 PR 系统中找不到时为空：照常轮岗，但收不到待办。
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)


class RotationDay(Base):
    """某个景点圈某一天的轮岗。state_json 是引擎的当天完整状态。"""

    __tablename__ = "rotation_days"
    __table_args__ = (UniqueConstraint("attraction_id", "work_date", name="uq_rotation_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attraction_id: Mapped[int] = mapped_column(ForeignKey("attractions.id"))
    work_date: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10), default="draft", index=True)  # draft / live / ended
    state_json: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class RotationEvent(Base):
    """轮岗操作日志。actor_type 为 employee / screen / system。"""

    __tablename__ = "rotation_events"
    __table_args__ = (
        Index("ix_rotation_event_day", "attraction_id", "work_date", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attraction_id: Mapped[int] = mapped_column(ForeignKey("attractions.id"))
    work_date: Mapped[str] = mapped_column(String(10))
    minute: Mapped[float] = mapped_column(Float)
    actor_type: Mapped[str] = mapped_column(String(10))
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_name: Mapped[str] = mapped_column(String(100))
    event_type: Mapped[str] = mapped_column(String(40))
    employee_no: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    line: Mapped[str | None] = mapped_column(String(20), nullable=True)
    post: Mapped[str | None] = mapped_column(String(50), nullable=True)
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class RotationSegment(Base):
    """每段在线记录，用于本周各线累计。"""

    __tablename__ = "rotation_segments"
    __table_args__ = (
        Index("ix_rotation_segment_week", "attraction_id", "work_date", "employee_no"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attraction_id: Mapped[int] = mapped_column(ForeignKey("attractions.id"))
    work_date: Mapped[str] = mapped_column(String(10))
    employee_no: Mapped[str] = mapped_column(String(50))
    line: Mapped[str] = mapped_column(String(20))
    start_min: Mapped[float] = mapped_column(Float)
    end_min: Mapped[float] = mapped_column(Float)
    minutes: Mapped[float] = mapped_column(Float)


class RotationDuty(Base):
    """每天的推 7 点和送失物人选，用于本周公平轮换。"""

    __tablename__ = "rotation_duties"
    __table_args__ = (
        UniqueConstraint("attraction_id", "work_date", "employee_no", "kind", name="uq_rotation_duty"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attraction_id: Mapped[int] = mapped_column(ForeignKey("attractions.id"))
    work_date: Mapped[str] = mapped_column(String(10), index=True)
    employee_no: Mapped[str] = mapped_column(String(50))
    kind: Mapped[str] = mapped_column(String(10))  # push7 / lost


class RotationNotice(Base):
    """CM/TR 的轮岗待办。由当前状态推导：状态变了就更新或关闭，每人每个 slot 最多一条进行中。

    主管的轮岗提醒不落表，待办中心按运行中的状态现算。
    """

    __tablename__ = "rotation_notices"
    __table_args__ = (
        Index("ix_rotation_notice_open", "employee_id", "status"),
        Index("ix_rotation_notice_day", "attraction_id", "work_date", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attraction_id: Mapped[int] = mapped_column(ForeignKey("attractions.id"))
    work_date: Mapped[str] = mapped_column(String(10))
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    # 去重键：同一人同一天同一 slot 只有一条进行中，例如 "step"、"duty:lost"。
    slot: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="open")  # open / done
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
