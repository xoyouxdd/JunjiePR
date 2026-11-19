"""SQLite connection, model base and configured paths; importing never creates files."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from app.runtime_environment import data_directory, validate_runtime_paths


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = data_directory()
FILE_DIR = DATA_DIR / "files"
EXPORT_DIR = DATA_DIR / "exports"
BACKUP_DIR = DATA_DIR / "backups"
DB_PATH = DATA_DIR / "recognition_v2.db"

Base = declarative_base()
engine = create_engine(
    f"sqlite:///{DB_PATH.as_posix()}",
    connect_args={"check_same_thread": False, "timeout": 30},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@event.listens_for(engine, "connect")
def sqlite_pragmas(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def ensure_directories() -> None:
    validate_runtime_paths(data_dir=DATA_DIR)
    for path in (DATA_DIR, FILE_DIR, EXPORT_DIR, BACKUP_DIR):
        path.mkdir(parents=True, exist_ok=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def reserve_writer(db: Session) -> None:
    """Drop any read snapshot (authentication may have opened one) and take SQLite's sole writer."""
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))


@contextmanager
def write_transaction(db: Session):
    """Hold the writer for the block and release it as soon as the block fails.

    Rolling back here, not at request teardown, keeps a failed write from
    holding SQLite's lock while the error response is produced.
    """
    reserve_writer(db)
    try:
        yield db
    except Exception:
        db.rollback()
        raise
