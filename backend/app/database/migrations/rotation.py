"""Extend standard rotation templates without rewriting scheduled or historical days."""
import json

from sqlalchemy import text


def ensure_rotation_five_posts(db) -> None:
    _ensure_standard_posts(db, 5)


def ensure_rotation_six_posts(db) -> None:
    from app.rotation.engine import STANDARD_POST_COUNT

    _ensure_standard_posts(db, STANDARD_POST_COUNT)


def _ensure_standard_posts(db, count) -> None:
    from app.rotation.engine import append_standard_spares

    for table in ('rotation_configs', 'formal_rotation_configs'):
        columns = {row[1] for row in db.execute(text(f'PRAGMA table_info({table})'))}
        if not {'id', 'lines_json'}.issubset(columns):
            continue
        for row_id, raw in db.execute(text(f'SELECT id, lines_json FROM {table}')).all():
            lines = json.loads(raw)
            if append_standard_spares(lines, count):
                db.execute(text(f'UPDATE {table} SET lines_json=:lines WHERE id=:id'),
                           {'id': row_id, 'lines': json.dumps(lines, ensure_ascii=False, separators=(',', ':'))})
