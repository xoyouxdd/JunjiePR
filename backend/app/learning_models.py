"""Learning shares are independent of announcement receipts and performance records."""
from datetime import datetime
from sqlalchemy import ForeignKey, Integer, String, Text, DateTime, Boolean, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database.connection import Base


class LearningShare(Base):
    __tablename__ = 'learning_shares'
    id: Mapped[int] = mapped_column(primary_key=True)
    circle_id: Mapped[int] = mapped_column(ForeignKey('attractions.id'), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    publisher_id: Mapped[int] = mapped_column(ForeignKey('employees.id'))
    title: Mapped[str] = mapped_column(String(100))
    body: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(30), default='未分类', server_default='未分类')
    assets: Mapped[str] = mapped_column(Text, default='[]')
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default='published', index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class LearningRevision(Base):
    __tablename__ = 'learning_revisions'
    id: Mapped[int] = mapped_column(primary_key=True)
    share_id: Mapped[int] = mapped_column(ForeignKey('learning_shares.id'), index=True)
    editor_id: Mapped[int] = mapped_column(ForeignKey('employees.id'))
    base_version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(100))
    body: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(30), default='未分类', server_default='未分类')
    assets: Mapped[str] = mapped_column(Text, default='[]')
    summary: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default='pending', index=True)
    reason: Mapped[str] = mapped_column(String(500), default='')
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LearningDraft(Base):
    __tablename__ = 'learning_drafts'
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    circle_id: Mapped[int] = mapped_column(ForeignKey('attractions.id'))
    share_id: Mapped[int | None] = mapped_column(ForeignKey('learning_shares.id'), nullable=True)
    base_version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(100), default='')
    body: Mapped[str] = mapped_column(Text, default='')
    category: Mapped[str] = mapped_column(String(30), default='未分类')
    assets: Mapped[str] = mapped_column(Text, default='[]')
    summary: Mapped[str] = mapped_column(String(500), default='')
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class LearningFeedback(Base):
    __tablename__ = 'learning_feedback'
    id: Mapped[int] = mapped_column(primary_key=True)
    share_id: Mapped[int] = mapped_column(ForeignKey('learning_shares.id'), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    share_version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default='pending')
    response: Mapped[str] = mapped_column(String(500), default='')
    handled_by: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    version: Mapped[int] = mapped_column(Integer, default=1)


class LearningReaction(Base):
    __tablename__ = 'learning_reactions'
    __table_args__ = (UniqueConstraint('share_id', 'employee_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    share_id: Mapped[int] = mapped_column(ForeignKey('learning_shares.id'), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    liked: Mapped[bool] = mapped_column(Boolean, default=False)
    starred: Mapped[bool] = mapped_column(Boolean, default=False)


class LearningAccess(Base):
    __tablename__ = 'learning_access'
    __table_args__ = (UniqueConstraint('employee_id', 'circle_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    circle_id: Mapped[int] = mapped_column(ForeignKey('attractions.id'), index=True)
    home_circle_id: Mapped[int] = mapped_column(ForeignKey('attractions.id'))
    reason: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default='pending', index=True)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey('employees.id'), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    review_note: Mapped[str] = mapped_column(String(500), default='')


class LearningAsset(Base):
    __tablename__ = 'learning_assets'
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('employees.id'), index=True)
    circle_id: Mapped[int] = mapped_column(ForeignKey('attractions.id'), index=True)
    filename: Mapped[str] = mapped_column(String(200))
    key: Mapped[str] = mapped_column(String(100), unique=True)
    media_type: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
