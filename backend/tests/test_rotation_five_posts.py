"""Five-slot templates and a narrowly scoped, idempotent legacy config upgrade."""
from copy import deepcopy
import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.database.migrations.rotation import ensure_rotation_five_posts
from app.rotation.engine import DEFAULT_LINES


def test_default_lines_keep_original_posts_and_fill_only_d2_e():
    assert len(DEFAULT_LINES) == 9
    assert all(len(line['posts']) == 5 for line in DEFAULT_LINES)
    lines = {line['id']: line for line in DEFAULT_LINES}
    assert [post['name'] for post in lines['D2']['posts']] == ['铁马A', '铁马B', '铁马C', '备用', '备用']
    assert [post['name'] for post in lines['E']['posts']] == ['城内巡视', '队末', '备用', '备用', '备用']
    assert lines['D2']['standby'] and lines['E']['standby']
    assert lines['A1']['posts'][-1] == {'name': '大出口商店', 'openAt': '09:00'}
    assert [post['name'] for line in DEFAULT_LINES for post in line['posts']].count('备用') == 5


@pytest.mark.parametrize('table', ['rotation_configs', 'formal_rotation_configs'])
def test_existing_templates_append_without_changing_days_or_settings(table):
    engine = create_engine('sqlite://')
    old = deepcopy(DEFAULT_LINES)
    old[0]['posts'][0]['name'] = '已自定义塔台'
    old[0]['posts'][1]['closeAt'] = '18:30'
    old[-2]['posts'] = old[-2]['posts'][:3]
    old[-2]['walk'] = 7
    old[-1]['posts'] = old[-1]['posts'][:2]
    old[-1]['posts'][0]['name'] = '自定义巡视'
    old.append({'id': 'F', 'posts': [{'name': '独立配置'}]})
    old.append({'id': 'A3', 'posts': [{'name': '其他自定义线'}]})
    # A configured standard line already larger than five must not lose positions.
    old[1]['posts'].append({'name': '已存在第六岗'})
    raw = json.dumps(old, ensure_ascii=False)
    try:
        with Session(engine) as db:
            db.execute(text(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY, lines_json TEXT, settings_json TEXT)'))
            db.execute(text(f'INSERT INTO {table} VALUES (1,:lines,:settings)'), {'lines': raw, 'settings': 'unchanged-settings'})
            for day_table in ('rotation_days', 'formal_rotation_days'):
                db.execute(text(f'CREATE TABLE {day_table} (id INTEGER PRIMARY KEY, state_json TEXT)'))
                db.execute(text(f'INSERT INTO {day_table} VALUES (1,:state)'), {'state': raw})
            ensure_rotation_five_posts(db)
            migrated, settings = db.execute(text(f'SELECT lines_json, settings_json FROM {table}')).one()
            expected = deepcopy(old)
            expected[-4]['posts'].extend([{'name': '备用'}, {'name': '备用'}])
            expected[-3]['posts'].extend([{'name': '备用'} for _ in range(3)])
            assert json.loads(migrated) == expected
            assert settings == 'unchanged-settings'
            for day_table in ('rotation_days', 'formal_rotation_days'):
                assert db.execute(text(f'SELECT state_json FROM {day_table}')).scalar_one() == raw
            ensure_rotation_five_posts(db)
            assert db.execute(text(f'SELECT lines_json FROM {table}')).scalar_one() == migrated
    finally:
        engine.dispose()


def test_missing_config_tables_are_safe():
    engine = create_engine('sqlite://')
    try:
        with Session(engine) as db:
            ensure_rotation_five_posts(db)
    finally:
        engine.dispose()
