"""Upgrade rotation settings and roster storage."""
from copy import deepcopy
import json
from uuid import uuid4

from sqlalchemy import text


def ensure_rotation_roster_name_remarks(db) -> None:
    for table in ('rotation_roster_entries', 'formal_rotation_roster_entries'):
        columns = {row[1] for row in db.execute(text(f'PRAGMA table_info({table})'))}
        if columns and 'name_remark' not in columns:
            db.execute(text(f"ALTER TABLE {table} ADD COLUMN name_remark TEXT NOT NULL DEFAULT ''"))


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


def _append_standby_e_line(lines, *, saved_plan=False):
    from app.rotation.engine import DEFAULT_E_LINE

    ids = {line['id'] for line in lines}
    if 'E' in ids or not {'A1', 'A2', 'B1', 'B2', 'C1', 'C2', 'D1', 'D2'} <= ids:
        return False
    line = deepcopy(DEFAULT_E_LINE)
    if saved_plan:
        line.update(active=False, sortAt=0.0)
        for post in line['posts']:
            post.update(key=uuid4().hex, open=False, opened=True, openAt=None, closeAt=None,
                        seven=None, manual=False, occ=None, since=None)
    lines.append(line)
    return True


def ensure_rotation_standby_e_line(db) -> None:
    for prefix in ('rotation', 'formal_rotation'):
        config_table, day_table = f'{prefix}_configs', f'{prefix}_days'
        config_columns = {row[1] for row in db.execute(text(f'PRAGMA table_info({config_table})'))}
        if {'id', 'lines_json'} <= config_columns:
            for row_id, raw in db.execute(text(f'SELECT id, lines_json FROM {config_table}')).all():
                lines = json.loads(raw)
                if _append_standby_e_line(lines):
                    db.execute(text(f'UPDATE {config_table} SET lines_json=:lines WHERE id=:id'),
                               {'id': row_id, 'lines': json.dumps(lines, ensure_ascii=False)})

        day_columns = {row[1] for row in db.execute(text(f'PRAGMA table_info({day_table})'))}
        if not {'id', 'status', 'state_json', 'version'} <= day_columns:
            continue
        days = db.execute(text(f"SELECT id, state_json FROM {day_table} WHERE status IN ('draft', 'live')")).all()
        for row_id, raw in days:
            state = json.loads(raw)
            if state.get('ruleVersion') != 2:
                continue
            changed = _append_standby_e_line(state['lines'], saved_plan=True)
            initial = state.get('initialDraft')
            if initial:
                changed = _append_standby_e_line(initial['lines'], saved_plan=True) or changed
            if changed:
                db.execute(text(f'UPDATE {day_table} SET state_json=:state, version=version+1 WHERE id=:id'),
                           {'id': row_id, 'state': json.dumps(state, ensure_ascii=False)})


def ensure_heat_pursuit_post_template_1126(db) -> None:
    """Set the template used by subsequent Heat Pursuit rosters."""
    from app.rotation.access import ROTATION_ATTRACTION_NAME
    from app.rotation.engine import DEFAULT_LINES

    if not db.execute(text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='attractions'")).scalar():
        return
    for table in ('formal_rotation_configs', 'rotation_configs'):
        columns = {row[1] for row in db.execute(text(f'PRAGMA table_info({table})'))}
        if not {'id', 'attraction_id', 'lines_json'} <= columns:
            continue
        rows = db.execute(text(f'SELECT cfg.id, cfg.lines_json FROM {table} AS cfg '
                               'JOIN attractions AS a ON a.id = cfg.attraction_id WHERE a.name = :name'),
                          {'name': ROTATION_ATTRACTION_NAME}).all()
        for row_id, raw in rows:
            old_lines = json.loads(raw)
            walks = {line['id']: line['walk'] for line in old_lines if line.get('walk') is not None}
            lines = deepcopy(DEFAULT_LINES)
            for line in lines:
                if line['id'] in walks:
                    line['walk'] = walks[line['id']]
            if old_lines != lines:
                db.execute(text(f'UPDATE {table} SET lines_json=:lines WHERE id=:id'),
                           {'id': row_id, 'lines': json.dumps(lines, ensure_ascii=False)})
