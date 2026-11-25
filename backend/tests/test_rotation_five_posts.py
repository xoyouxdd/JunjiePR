"""Five-slot templates and a narrowly scoped, idempotent legacy config upgrade."""
from copy import deepcopy
import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.database.migrations.rotation import (
    ensure_heat_pursuit_post_template_1126,
    ensure_rotation_five_posts,
    ensure_rotation_six_posts,
    ensure_rotation_standby_e_line,
)
from app.rotation.engine import DEFAULT_E_LINE, DEFAULT_LINES, append_standard_spares


def test_default_lines_have_only_configured_posts():
    lines = {line['id']: line for line in DEFAULT_LINES}
    assert list(lines) == ['A1', 'A2', 'B1', 'B2', 'C1', 'C2', 'D1', 'D2', 'E']
    assert [len(line['posts']) for line in DEFAULT_LINES] == [5, 5, 5, 5, 5, 5, 5, 4, 2]
    assert lines['E']['standby'] and [post['name'] for post in lines['E']['posts']] == ['队末', '城内巡视']
    assert not any(post['name'] == '备用' for line in DEFAULT_LINES for post in line['posts'])
    assert [post['name'] for post in lines['D1']['posts']] == ['铁马C', '铁马B', '铁马A', '铁马出口', '迎宾协助']
    assert [post['name'] for post in lines['D2']['posts']] == ['河马池', '单人截流', '下客1', '下客2']
    assert not lines['D2'].get('standby')
    assert {post['name']: post['openAt'] for line in DEFAULT_LINES for post in line['posts'] if post.get('openAt')} == {
        '铁马C': '08:00', '单人迎宾': '08:15', '大出口商店': '08:30', '快迎2': '08:30', '快速汇合': '08:30',
    }


@pytest.mark.parametrize('table', ['rotation_configs', 'formal_rotation_configs'])
@pytest.mark.parametrize('upgrade', [ensure_rotation_five_posts, ensure_rotation_six_posts])
def test_existing_templates_append_without_changing_days_or_settings(table, upgrade):
    engine = create_engine('sqlite://')
    old = deepcopy([line for line in DEFAULT_LINES if line['id'] != 'E'])
    old.append({'id': 'E', 'group': 'E', 'standby': True, 'posts': [{'name': '城内巡视'}, {'name': '队末'}]})
    append_standard_spares(old)
    for line in old:
        line['posts'].pop()
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
            upgrade(db)
            migrated, settings = db.execute(text(f'SELECT lines_json, settings_json FROM {table}')).one()
            expected = deepcopy(old)
            expected[-4]['posts'].extend([{'name': '备用'}, {'name': '备用'}])
            expected[-3]['posts'].extend([{'name': '备用'} for _ in range(3)])
            if upgrade is ensure_rotation_six_posts:
                for line in expected[:9]:
                    if line['id'] != 'A2':
                        line['posts'].append({'name': '备用', 'draftClosed': True})
            assert json.loads(migrated) == expected
            assert settings == 'unchanged-settings'
            for day_table in ('rotation_days', 'formal_rotation_days'):
                assert db.execute(text(f'SELECT state_json FROM {day_table}')).scalar_one() == raw
            upgrade(db)
            assert db.execute(text(f'SELECT lines_json FROM {table}')).scalar_one() == migrated
    finally:
        engine.dispose()


def test_missing_config_tables_are_safe():
    engine = create_engine('sqlite://')
    try:
        with Session(engine) as db:
            ensure_rotation_five_posts(db)
            ensure_rotation_six_posts(db)
            ensure_rotation_standby_e_line(db)
            ensure_heat_pursuit_post_template_1126(db)
    finally:
        engine.dispose()


@pytest.mark.parametrize('prefix', ['rotation', 'formal_rotation'])
def test_heat_pursuit_default_template_keeps_walk_times_and_other_data(prefix):
    engine = create_engine('sqlite://')
    configs, days = f'{prefix}_configs', f'{prefix}_days'
    old_lines = json.dumps([{'id': 'A1', 'walk': 7, 'posts': [{'name': '原岗位'}]}], ensure_ascii=False)
    expected = deepcopy(DEFAULT_LINES)
    expected[0]['walk'] = 7
    try:
        with Session(engine) as db:
            db.execute(text('CREATE TABLE attractions (id INTEGER PRIMARY KEY, name TEXT)'))
            db.execute(text("INSERT INTO attractions VALUES (1,'热力追踪'), (2,'另一景点圈')"))
            db.execute(text(f'CREATE TABLE {configs} (id INTEGER PRIMARY KEY, attraction_id INTEGER, lines_json TEXT, settings_json TEXT)'))
            db.execute(text(f'INSERT INTO {configs} VALUES (1,1,:lines,:settings), (2,2,:lines,:settings)'),
                       {'lines': old_lines, 'settings': '{"unchanged":true}'})
            db.execute(text(f'CREATE TABLE {days} (id INTEGER PRIMARY KEY, state_json TEXT, version INTEGER)'))
            db.execute(text(f'INSERT INTO {days} VALUES (1,:state,5)'), {'state': old_lines})
            ensure_heat_pursuit_post_template_1126(db)
            rows = db.execute(text(f'SELECT lines_json, settings_json FROM {configs} ORDER BY id')).all()
            assert json.loads(rows[0][0]) == expected and rows[0][1] == '{"unchanged":true}'
            assert rows[1] == (old_lines, '{"unchanged":true}')
            assert db.execute(text(f'SELECT state_json, version FROM {days}')).one() == (old_lines, 5)
            ensure_heat_pursuit_post_template_1126(db)
            assert db.execute(text(f'SELECT lines_json, settings_json FROM {configs} ORDER BY id')).all() == rows
    finally:
        engine.dispose()


@pytest.mark.parametrize('prefix', ['rotation', 'formal_rotation'])
def test_standby_e_migrates_configs_and_unfinished_plans_only(prefix):
    engine = create_engine('sqlite://')
    existing = deepcopy([line for line in DEFAULT_LINES if line['id'] != 'E'])
    existing[0]['posts'][0]['name'] = '原来改过的塔台'
    previous = {'lines': existing, 'ruleVersion': 2, 'lastTick': 555,
                'plan': {'crew': {'A1#0': 'resident'}}, 'persons': {'resident': {'state': 'onpost'}}}
    state = {**previous, 'initialDraft': previous, 'dailyOpening': {'state': previous}}
    custom = [{'id': 'A', 'posts': [{'name': '自定义岗位'}]}]
    config, days = f'{prefix}_configs', f'{prefix}_days'
    try:
        with Session(engine) as db:
            db.execute(text(f'CREATE TABLE {config} (id INTEGER PRIMARY KEY, lines_json TEXT)'))
            db.execute(text(f'CREATE TABLE {days} (id INTEGER PRIMARY KEY, status TEXT, state_json TEXT, version INTEGER)'))
            db.execute(text(f'INSERT INTO {config} VALUES (:id,:lines)'), [
                {'id': 1, 'lines': json.dumps(existing)}, {'id': 2, 'lines': json.dumps(custom)},
            ])
            db.execute(text(f'INSERT INTO {days} VALUES (:id,:status,:state,7)'), [
                {'id': i, 'status': status, 'state': json.dumps(state)}
                for i, status in enumerate(('live', 'draft', 'ended'), 1)
            ])
            ensure_rotation_standby_e_line(db)
            configs = [json.loads(raw) for raw, in db.execute(text(f'SELECT lines_json FROM {config} ORDER BY id'))]
            assert configs == [existing + [DEFAULT_E_LINE], custom]
            rows = db.execute(text(f'SELECT state_json, version FROM {days} ORDER BY id')).all()
            for raw, version in rows[:2]:
                plan = json.loads(raw)
                assert version == 8 and plan['dailyOpening'] == state['dailyOpening']
                for patched in (plan, plan['initialDraft']):
                    assert patched['plan'] == previous['plan'] and patched['persons'] == previous['persons']
                    assert patched['lastTick'] == previous['lastTick'] and patched['lines'][:-1] == existing
                    e = patched['lines'][-1]
                    assert e['id'] == 'E' and e['standby'] and not e['active'] and e['sortAt'] == 0
                    assert [post['name'] for post in e['posts']] == ['队末', '城内巡视']
                    assert all(post['key'] and not post['open'] and post['occ'] is None for post in e['posts'])
            assert json.loads(rows[-1][0]) == state and rows[-1][1] == 7
            ensure_rotation_standby_e_line(db)
            assert db.execute(text(f'SELECT state_json, version FROM {days} ORDER BY id')).all() == rows
    finally:
        engine.dispose()
