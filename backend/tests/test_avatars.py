"""Personal avatar writes and bounded directory reads on disposable databases."""
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import event
from test_monthly_self_ranks import system  # noqa: F401
from app.services.avatars import avatar_catalog, avatar_keys
from app.v2_models import EmployeeAvatarPreference, AuditLog, AttendanceMonthlyScore, EmployeeMonthRankSnapshot


def test_avatar_preferences_are_self_only_and_do_not_change_scores(system):
    db, client, _, _, make, switch, _, _ = system
    first, other = make('avatar-a', points=12), make('avatar-b', points=3)
    before = [(row.employee_id, row.final_score) for row in db.query(AttendanceMonthlyScore).order_by(AttendanceMonthlyScore.employee_id)]
    key = next(iter(avatar_catalog()))
    switch(first)
    saved = client.put('/api/self-avatar', json={'avatar_key': key, 'frame': 'ocean'})
    assert saved.status_code == 200, saved.text
    assert saved.headers['cache-control'] == 'private, no-store'
    assert saved.json()['avatar_key'] == key
    assert client.get('/api/self-avatar').json()['avatar_frame'] == 'ocean'
    assert client.put('/api/self-avatar', json={'avatar_key': key, 'employee_id': other.id}).status_code == 422
    switch(other)
    assert client.get('/api/self-avatar', params={'employee_id': first.id}).json()['avatar_key'] == ''
    assert db.query(EmployeeAvatarPreference).count() == 1
    assert db.query(AuditLog).filter_by(action='avatar_preference').count() == 1
    assert avatar_keys(db, [first.id, other.id]) == {first.id: key}
    assert before == [(row.employee_id, row.final_score) for row in db.query(AttendanceMonthlyScore).order_by(AttendanceMonthlyScore.employee_id)]
    assert db.query(EmployeeMonthRankSnapshot).count() == 0
    from app.organization_queries import employee_payloads
    public = employee_payloads(db, [first, other])
    assert public[first.id]['avatar_key'] == key
    assert all('avatar_frame' not in row for row in public.values())


@pytest.mark.parametrize('body', [
    {}, {'avatar_key': '../escape'}, {'avatar_key': 'https://example.com/image'},
    {'avatar_key': None}, {'avatar_key': 1}, {'avatar_key': '', 'frame': 'medal:2026-09:90'},
    {'avatar_key': '', 'frame': 'admin'}, {'avatar_key': '', 'employee_id': 1},
])
def test_invalid_or_unearned_cosmetics_never_write(system, body):
    db, client, _, _, make, switch, _, _ = system
    switch(make('invalid-avatar'))
    assert client.put('/api/self-avatar', json=body).status_code == 422
    assert db.query(EmployeeAvatarPreference).count() == 0


def test_400_person_avatar_lookup_uses_one_select(system):
    db, _, _, _, make, _, _, _ = system
    person = make('batch-avatar')
    db.add(EmployeeAvatarPreference(employee_id=person.id, avatar_key=next(iter(avatar_catalog())), frame='autumn'))
    db.commit()
    statements=[]
    def collect(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(db.bind, 'before_cursor_execute', collect)
    try:
        result = avatar_keys(db, range(person.id, person.id+400))
    finally:
        event.remove(db.bind, 'before_cursor_execute', collect)
    assert len(statements) == 1 and result[person.id]


def test_temporary_rotation_identity_cannot_write_employee_avatar(system):
    db, client, _, _, make, switch, holder, _ = system
    switch(make('temporary-avatar'))
    holder['user'].rotation_temporary = True
    assert client.get('/api/self-avatar').status_code == 403
    assert client.put('/api/self-avatar', json={'avatar_key': '', 'frame': 'plain'}).status_code == 403
    assert db.query(EmployeeAvatarPreference).count() == 0


@pytest.mark.parametrize('role', ['CM','TR','SUPERVISOR','GSM','AM','OM','SYSTEM_ADMIN'])
def test_regular_non_hr_accounts_can_choose_avatar(system, role):
    _, client, _, _, make, switch, _, _ = system
    person=make('avatar-role-'+role, role=role)
    switch(person, role)
    assert client.get('/api/self-avatar').status_code == 200
    assert client.put('/api/self-avatar', json={'avatar_key': next(iter(avatar_catalog())), 'frame': 'forest'}).status_code == 200
    if role in {'GSM','AM','OM'}:
        assert client.get('/api/self-month-rank').status_code == 403


@pytest.mark.parametrize('role', ['HR_CIRCLE','HR_ADMIN'])
def test_hr_accounts_do_not_use_avatar_preferences(system, role):
    db, client, _, _, make, switch, _, _ = system
    person=make('avatar-hr-'+role, role=role)
    switch(person, role)
    assert client.get('/api/self-avatar').status_code == 403
    assert client.put('/api/self-avatar', json={'avatar_key': '', 'frame': 'plain'}).status_code == 403
    assert db.query(EmployeeAvatarPreference).count() == 0


def test_historical_frame_requires_own_current_seal(system, monkeypatch):
    db, client, _, _, make, switch, _, _ = system
    switch(make('history-avatar'))
    def ranked(db, month, employee):
        assert month == '2026-09'
        return {'status': 'ranked', 'month_closed': True, 'tier': 50}
    monkeypatch.setattr('app.routers.statistics.member_month_rank', ranked)
    assert client.put('/api/self-avatar', json={'avatar_key': '', 'frame': 'medal:2026-09:50'}).status_code == 200
    assert client.put('/api/self-avatar', json={'avatar_key': '', 'frame': 'medal:2026-09:90'}).status_code == 422
    monkeypatch.setattr('app.routers.statistics.member_month_rank', lambda *a: {'status': 'reopened'})
    assert client.put('/api/self-avatar', json={'avatar_key': '', 'frame': 'medal:2026-09:50'}).status_code == 422
    monkeypatch.setattr('app.routers.statistics.member_month_rank', lambda *a: pytest.fail('Ordinary frames must not load ranks'))
    assert client.put('/api/self-avatar', json={'avatar_key': '', 'frame': 'forest'}).status_code == 200


def test_catalog_assets_are_local_small_and_complete():
    root = Path(__file__).resolve().parents[1] / 'app/static/images/avatars'
    records = json.loads((root/'catalog.json').read_text(encoding='utf-8'))['avatars']
    assert len(records) == len({a['key'] for a in records})
    assert len(records) >= 65
    for record in records:
        for size, limit in [('64', 4000), ('160', 16000)]:
            meta=record['images'][size]
            data=(root/meta['file']).read_bytes()
            assert len(data) == meta['bytes'] < limit
            assert hashlib.sha256(data).hexdigest() == meta['sha256']
            assert data[:4] == b'RIFF' and data[8:12] == b'WEBP'


def test_wardrobe_is_account_scoped_and_validated(system):
    _, client, _, _, make, switch, _, _ = system
    first, second = make('wardrobe-first'), make('wardrobe-second')
    key = next(iter(avatar_catalog()))
    switch(first)
    data = {'favorites': [key], 'recent': [key], 'presets': [{'avatar_key': key, 'frame': 'thumb', 'name': '今日营业'}, None, None]}
    assert client.put('/api/self-avatar-wardrobe', json=data).status_code == 200
    assert client.get('/api/self-avatar-wardrobe').json() == data
    for name in [' ' * 3, '长' * 13]:
        invalid = {**data, 'presets': [{'avatar_key': key, 'frame': 'thumb', 'name': name}, None, None]}
        assert client.put('/api/self-avatar-wardrobe', json=invalid).status_code == 422
    assert client.put('/api/self-avatar', json={'avatar_key': key, 'frame': 'thumb'}).status_code == 200
    assert client.put('/api/self-avatar-wardrobe', json={**data, 'favorites': ['invalid']}).status_code == 422
    assert client.get('/api/self-avatar-wardrobe').json() == data
    switch(second)
    assert client.get('/api/self-avatar-wardrobe').json() == {'favorites': [], 'recent': [], 'presets': [None, None, None]}


@pytest.mark.parametrize('role', ['HR_CIRCLE', 'HR_ADMIN'])
def test_hr_cannot_access_wardrobe(system, role):
    _, client, _, _, make, switch, _, _ = system
    switch(make('wardrobe-hr-'+role, role=role), role)
    assert client.get('/api/self-avatar-wardrobe').status_code == 403
    assert client.put('/api/self-avatar-wardrobe', json={'favorites': [], 'recent': [], 'presets': [None, None, None]}).status_code == 403
