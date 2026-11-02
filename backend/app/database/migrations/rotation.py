"""Extend standard rotation templates without rewriting scheduled or historical days."""
import json

from sqlalchemy import text


def ensure_rotation_five_posts(db) -> None:
    from app.rotation.engine import DEFAULT_LINES

    standard_ids = {line['id'] for line in DEFAULT_LINES}
    for table in ('rotation_configs', 'formal_rotation_configs'):
        columns = {row[1] for row in db.execute(text(f'PRAGMA table_info({table})'))}
        if not {'id', 'lines_json'}.issubset(columns):
            continue
        for row_id, raw in db.execute(text(f'SELECT id, lines_json FROM {table}')).all():
            lines = json.loads(raw)
            changed = False
            for line in lines:
                if line['id'] in standard_ids and len(line['posts']) < 5:
                    line['posts'].extend({'name': '备用'} for _ in range(5 - len(line['posts'])))
                    changed = True
            if changed:
                db.execute(text(f'UPDATE {table} SET lines_json=:lines WHERE id=:id'),
                           {'id': row_id, 'lines': json.dumps(lines, ensure_ascii=False, separators=(',', ':'))})
