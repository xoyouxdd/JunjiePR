"""Video learning is separate from performance, announcements and scoring."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.connection import Base


class LearningVideo(Base):
    __tablename__ = "learning_videos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    attraction_id: Mapped[int] = mapped_column(ForeignKey("attractions.id"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    cover_title: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text, default="")
    replaces_id: Mapped[int | None] = mapped_column(ForeignKey("learning_videos.id"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="processing", index=True)
    original_bytes: Mapped[int] = mapped_column(Integer)
    duration: Mapped[int] = mapped_column(Integer, default=0)
    profiles: Mapped[str] = mapped_column(Text, default="{}")
    cover_second: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class VideoUpload(Base):
    __tablename__ = "learning_video_uploads"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    config: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(Integer)
    offset: Mapped[int] = mapped_column(Integer, default=0)
    video_id: Mapped[int | None] = mapped_column(ForeignKey("learning_videos.id"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class VideoCircle(Base):
    """Explicit audience snapshot; no rows means a legacy single-circle video."""
    __tablename__ = "learning_video_circles"
    video_id: Mapped[int] = mapped_column(ForeignKey("learning_videos.id"), primary_key=True)
    attraction_id: Mapped[int] = mapped_column(ForeignKey("attractions.id"), primary_key=True, index=True)


class VideoAudiencePolicy(Base):
    __tablename__ = "learning_video_audiences"
    video_id: Mapped[int] = mapped_column(ForeignKey("learning_videos.id"), primary_key=True)
    local_attraction_id: Mapped[int] = mapped_column(ForeignKey("attractions.id"), index=True)
    codes: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    replaces_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)


class VideoPlayback(Base):
    __tablename__ = "learning_video_playbacks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("learning_videos.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    session_hash: Mapped[str] = mapped_column(String(64))
    employee_no: Mapped[str] = mapped_column(String(50))
    notice_version: Mapped[str] = mapped_column(String(30))
    accepted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class VideoProgress(Base):
    __tablename__ = "learning_video_progress"
    __table_args__ = (UniqueConstraint("employee_id", "video_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("learning_videos.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class VideoJob(Base):
    __tablename__ = "learning_video_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("learning_videos.id"), index=True)
    # Unique render key makes replays share an identical account/version/quality.
    key: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[str] = mapped_column(String(20))
    payload: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    error: Mapped[str] = mapped_column(String(200), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
