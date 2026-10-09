"""SQLite connection, model base and configured paths; importing never creates files."""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("RECOGNITION_V2_DATA_DIR", str(BASE_DIR / "data_v2"))).resolve()
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
    for path in (DATA_DIR, FILE_DIR, EXPORT_DIR, BACKUP_DIR):
        path.mkdir(parents=True, exist_ok=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
